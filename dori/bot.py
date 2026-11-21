"""Bot behaviour: decides what Dori says for each turn."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from dori import config, llm, loop_client, prompts, store
from dori.events import Event

log = logging.getLogger("dori.bot")

MAX_BUBBLES = 3  # an in-character reply goes out as at most this many texts


class BotError(RuntimeError):
    """The model's answer left Dori with nothing to say."""


async def say(phone: str, text: str, kind: str = "chat", reply_to: str | None = None) -> str:
    """Send a message and store it, so a later ❓ reaction can find it.

    If LoopMessage refuses an inline reply, the text goes out as a normal
    message instead.
    """
    try:
        message_id = await loop_client.send(phone, text, reply_to=reply_to)
    except loop_client.LoopError:
        if not reply_to:
            raise
        log.warning("inline reply refused; sending it as a normal message")
        message_id = await loop_client.send(phone, text)
    store.add_message(phone, "bot", text, kind=kind, loop_id=message_id)
    return message_id


def split_bubbles(reply: str) -> list[str]:
    """Split a reply into separate texts, one per line, at most MAX_BUBBLES."""
    lines = [line.strip() for line in reply.splitlines() if line.strip()]
    if len(lines) > MAX_BUBBLES:
        lines = [*lines[: MAX_BUBBLES - 1], " ".join(lines[MAX_BUBBLES - 1 :])]
    return lines


async def say_bubbles(phone: str, reply: str) -> None:
    """Send an in-character reply as one iMessage per line."""
    for index, bubble in enumerate(split_bubbles(reply)):
        if index:
            await asyncio.sleep(config.BUBBLE_GAP_S)
        await say(phone, bubble)


def _text(value: Any) -> str:
    """A model field as clean text. Missing, null and "null" all become ""."""
    if not isinstance(value, str):
        return ""
    value = value.strip()
    return "" if value.lower() in ("null", "none") else value


async def handle_turn(phone: str, event: Event) -> None:
    """Handle one turn. The first matching case wins."""
    if event.kind != "message":
        return  # reactions are not handled yet

    text = event.text.strip()
    user = store.get_user(phone)

    if user is None:
        store.save_user(phone, mode="onboarding")
        store.add_message(phone, "user", text, kind="system", loop_id=event.message_id)
        await say(phone, prompts.WELCOME, kind="system")
        return

    if user["mode"] == "onboarding":
        await _onboard(phone, user, text, event.message_id)
        return

    await _chat(phone, user, text, event.message_id)


# --- Onboarding ---


async def _onboard(phone: str, user: dict[str, Any], text: str, message_id: str | None) -> None:
    """Fill in the profile from the user's answer.

    The model only extracts fields. This code decides what is still missing
    and when onboarding is over, so it cannot get stuck or drift into chat.
    """
    store.add_message(phone, "user", text, kind="system", loop_id=message_id)
    known = {field: user[field] for field in store.PROFILE_FIELDS if user[field]}
    missing = [field for field in store.PROFILE_FIELDS if not user[field]]

    data = await llm.chat_json(
        prompts.onboarding_system(known, missing),
        [{"role": "user", "content": text}],
    )
    updates = _profile_updates(data, missing)
    if updates:
        user = store.save_user(phone, **updates)

    missing = [field for field in store.PROFILE_FIELDS if not user[field]]
    if missing:
        await say(phone, prompts.ask_missing(missing), kind="system")
        return

    store.set_mode(phone, "chat")
    await say(phone, prompts.TIPS, kind="system")
    await _open_conversation(phone, user)


def _profile_updates(data: dict[str, Any], missing: list[str]) -> dict[str, str]:
    """The extracted values that are usable and still needed."""
    updates: dict[str, str] = {}
    for field in missing:
        value = _text(data.get(field))
        if field == "style":
            value = value.lower()
            if value not in prompts.STYLES:
                continue
        if value:
            updates[field] = value
    return updates


async def _open_conversation(phone: str, user: dict[str, Any]) -> None:
    """Send Dori's first in-character message."""
    data = await llm.chat_json(
        prompts.chat_system(user),
        [{"role": "user", "content": prompts.opening_turn(user)}],
    )
    reply = _text(data.get("reply"))
    if not reply:
        raise BotError("the model gave no opening message")
    await say_bubbles(phone, reply)


# --- Chat ---

_SPACING_AND_PUNCTUATION = re.compile(r"[\s.,!?~…]+")


def _same(first: str, second: str) -> bool:
    """True when two texts differ only in spacing and punctuation."""
    return _SPACING_AND_PUNCTUATION.sub("", first) == _SPACING_AND_PUNCTUATION.sub("", second)


def _history(phone: str) -> list[dict[str, Any]]:
    """Recent conversation for the model, without onboarding and help messages."""
    recent = store.recent(phone, config.HISTORY_LIMIT)
    return [message for message in recent if message["kind"] != "system"]


async def _chat(phone: str, user: dict[str, Any], text: str, message_id: str | None) -> None:
    """One chat turn, from a single model call.

    If the user's message has a real error, Dori first replies inline to that
    message with the fix, then carries on the conversation. If the user asked
    a question about the language, Dori answers it instead.
    """
    history = _history(phone)
    store.add_message(phone, "user", text, loop_id=message_id)

    data = await llm.chat_json(
        prompts.chat_system(user),
        [{"role": "user", "content": prompts.chat_turn(user, history, text)}],
    )
    correction = _text(data.get("correction"))
    explanation = _text(data.get("explanation"))
    reply = _text(data.get("reply"))

    if data.get("intent") == "question":
        answer = explanation or reply
        if not answer:
            raise BotError("the model gave no answer to the question")
        await say(phone, answer, kind="explanation")
        return

    corrected = bool(correction) and not _same(correction, text)
    if corrected:
        fix = f"✏️ {correction}\n💬 {explanation}" if explanation else f"✏️ {correction}"
        await say(phone, fix, kind="correction", reply_to=message_id)
    if not reply and not corrected:
        raise BotError("the model gave no reply")
    await say_bubbles(phone, reply)
