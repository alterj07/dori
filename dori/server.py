"""FastAPI app that receives LoopMessage webhooks and hands turns to the bot.

The endpoint checks the shared secret, drops duplicates and answers 200 right
away: LoopMessage retries anything that does not return 200 within 15 seconds.
The actual work runs afterwards in background tasks, one turn per user at a
time.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import traceback
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from dori import bot, config, llm, loop_client, store, turn_log
from dori.events import Event

log = logging.getLogger("dori.server")

FALLBACK_TEXT = "Sorry, can you say that again? 🙏"

# A reaction containing any of these counts as "I don't understand this".
_QUESTION_MARKS = "?？❓❔⁉"


def is_question_reaction(payload: dict[str, Any]) -> bool:
    """True when a reaction webhook is the user putting a question mark on a message.

    LoopMessage sends the classic "?" tapback as "question" and an emoji
    reaction as the emoji itself. Real payloads carry it in reaction_type,
    while the docs call the field reaction, so both are read.
    """
    reaction = payload.get("reaction_type") or payload.get("reaction")
    if not isinstance(reaction, str):
        return False
    if payload.get("reaction_event", "placed") != "placed":
        return False  # the user took the reaction back
    if payload.get("reaction_direction", "inbound") != "inbound":
        return False  # a reaction sent from our side
    name = reaction.strip().lower()
    if name.startswith("-"):
        return False  # the older way of marking a removed reaction
    return "question" in name or any(mark in name for mark in _QUESTION_MARKS)


async def run_turn(phone: str, event: Event) -> None:
    """Run one bot turn. If it fails, log the traceback and apologise to the user."""
    with turn_log.turn(phone, f"turn:{event.kind}", event.text) as record:
        try:
            await bot.handle_turn(phone, event)
        except Exception:
            record["error"] = traceback.format_exc()
            log.exception("turn failed")
            try:
                await loop_client.send(phone, FALLBACK_TEXT)
            except Exception:
                log.exception("could not send the fallback message")


class Dispatcher:
    """Groups each user's texts into turns and runs those turns one at a time.

    Texts are buffered per phone number. Every new text restarts that user's
    debounce timer, and when the timer fires the buffered texts become one
    turn. A per-user lock keeps one user's turns strictly sequential while
    different users run in parallel.
    """

    def __init__(self) -> None:
        # Per phone: (text, message_id, id of the message it replies to inline).
        self._buffers: dict[str, list[tuple[str, str | None, str | None]]] = {}
        self._timers: dict[str, asyncio.TimerHandle] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task[Any]] = set()

    def add_message(
        self,
        phone: str,
        text: str,
        message_id: str | None,
        in_reply_to: str | None = None,
        delay: float | None = None,
    ) -> None:
        """Buffer one inbound text and (re)start the user's debounce timer.

        ``in_reply_to`` is the id of the message this text replies to inline.
        ``delay`` is how long to wait for more texts; the default is
        ``config.DEBOUNCE_S``.
        """
        self._buffers.setdefault(phone, []).append((text, message_id, in_reply_to))
        timer = self._timers.pop(phone, None)
        if timer is not None:
            timer.cancel()
        loop = asyncio.get_running_loop()
        wait = config.DEBOUNCE_S if delay is None else delay
        self._timers[phone] = loop.call_later(wait, self._on_timer, phone)

    def add_reaction(
        self, phone: str, message_id: str | None, reaction: str, text: str = ""
    ) -> None:
        """Start a reaction turn right away, skipping the debounce."""
        event = Event(kind="reaction", text=text, message_id=message_id, reaction=reaction)
        self.spawn(self._run_event(phone, event))

    def spawn(self, coro: Coroutine[Any, Any, Any]) -> None:
        """Run a coroutine in the background."""
        task = asyncio.create_task(coro)
        self._tasks.add(task)  # hold a reference so the task is not garbage collected
        task.add_done_callback(self._tasks.discard)

    def _on_timer(self, phone: str) -> None:
        self._timers.pop(phone, None)
        self.spawn(self._run_buffered(phone))

    def _lock(self, phone: str) -> asyncio.Lock:
        return self._locks.setdefault(phone, asyncio.Lock())

    async def _run_buffered(self, phone: str) -> None:
        async with self._lock(phone):
            # Read the buffer only once the lock is held, so texts that arrived
            # while an earlier turn was still running join this turn.
            items = self._buffers.pop(phone, [])
            if not items:
                return
            event = Event(
                kind="message",
                text="\n".join(item[0] for item in items),
                message_id=items[-1][1],
                in_reply_to=next((item[2] for item in reversed(items) if item[2]), None),
            )
            await run_turn(phone, event)

    async def _run_event(self, phone: str, event: Event) -> None:
        async with self._lock(phone):
            await run_turn(phone, event)

    async def idle(self) -> None:
        """Wait until every pending timer has fired and every turn has finished."""
        while self._timers or self._tasks:
            await asyncio.sleep(0.01)

    def cancel(self) -> None:
        """Drop pending timers and running turns (used at shutdown)."""
        for timer in self._timers.values():
            timer.cancel()
        self._timers.clear()
        self._buffers.clear()
        for task in self._tasks:
            task.cancel()


dispatcher = Dispatcher()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    missing = config.missing()
    if missing:
        raise RuntimeError(f"missing settings in .env: {', '.join(missing)}")
    yield
    dispatcher.cancel()
    await loop_client.aclose()
    await llm.aclose()
    store.close()


# The interactive API docs are off: this server sits behind a public tunnel.
app = FastAPI(title="Dori", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, bool]:
    """Lets you check that the server and the tunnel are up."""
    return {"ok": True}


def _authorized(request: Request) -> bool:
    secret = config.WEBHOOK_SECRET
    given = request.headers.get("authorization", "").strip()
    return bool(secret) and hmac.compare_digest(given.encode(), secret.encode())


def _debounce_for(phone: str) -> float:
    """How long to wait for more texts from this person.

    Someone who is still signing up tends to send name, language and level as
    separate texts with a pause to type each one, so they get a longer wait.
    """
    user = store.get_user(phone)
    if user is not None and user["mode"] == "onboarding":
        return config.ONBOARDING_DEBOUNCE_S
    return config.DEBOUNCE_S


def route(payload: dict[str, Any]) -> str:
    """Hand one webhook to the dispatcher. Returns a short note for the log."""
    # Older LoopMessage payloads call these two fields alert_type and recipient.
    event = payload.get("event") or payload.get("alert_type")
    phone = payload.get("contact") or payload.get("recipient")
    message_id = payload.get("message_id")

    if not phone or payload.get("group"):
        return "logged"

    if event == "message_inbound":
        text = (payload.get("text") or "").strip()
        if not text:
            return "ignored: no text"
        # An inline reply names the message it answers in a "thread" object.
        thread = payload.get("thread")
        in_reply_to = thread.get("initial_message_id") if isinstance(thread, dict) else None
        dispatcher.add_message(phone, text, message_id, in_reply_to, _debounce_for(phone))
        if message_id and config.TYPING_SECONDS > 0:
            dispatcher.spawn(loop_client.show_typing(message_id))
        return "buffered"

    if event == "message_reaction":
        if is_question_reaction(payload):
            text = (payload.get("text") or "").strip()
            dispatcher.add_reaction(phone, message_id, "question", text)
            return "dispatched"
        reaction = payload.get("reaction_type") or payload.get("reaction")
        return f"logged: reaction {reaction}"

    return "logged"


@app.post("/webhook")
async def webhook(request: Request) -> JSONResponse:
    """Receive one LoopMessage event: authenticate, dedupe, acknowledge."""
    if not _authorized(request):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)

    try:
        payload = await request.json()
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        # A retry would not fix a malformed body, so acknowledge it anyway.
        log.warning("ignored a webhook whose body was not a JSON object")
        return JSONResponse({"ok": True})

    webhook_id = payload.get("webhook_id")
    if webhook_id and not store.claim_webhook(str(webhook_id)):
        turn_log.webhook(payload, "duplicate")
        return JSONResponse({"ok": True})

    note = route(payload)
    turn_log.webhook(payload, note)
    log.info("webhook %s: %s", payload.get("event"), note)
    return JSONResponse({"ok": True})
