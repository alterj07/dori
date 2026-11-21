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


_LONG_DASH = re.compile(r"\s*[—–]\s*")


def _text(value: Any) -> str:
    """A model field as clean text. Missing, null and "null" all become "".

    Long dashes become commas: nobody types them in a text message.
    """
    if not isinstance(value, str):
        return ""
    value = _LONG_DASH.sub(", ", value).strip()
    return "" if value.lower() in ("null", "none") else value


async def handle_turn(phone: str, event: Event) -> None:
    """Handle one turn. The first matching case wins."""
    text = event.text.strip()
    command = text.lower().strip(" .!?")

    if event.kind == "message" and command == "/reset":
        # Nothing is stored here: the user and their history are gone.
        store.reset_user(phone)
        await loop_client.send(phone, prompts.RESET_DONE)
        return

    user = store.get_user(phone)

    if event.kind == "reaction":
        # A ❓ only means something once there is a conversation to explain.
        if user is not None and user["mode"] != "onboarding":
            await _explain(phone, user, event)
        return

    if user is None:
        store.save_user(phone, mode="onboarding")
        store.add_message(phone, "user", text, kind="system", loop_id=event.message_id)
        await say(phone, prompts.WELCOME, kind="system")
        return

    if user["mode"] == "onboarding":
        await _onboard(phone, user, text, event.message_id)
        return

    if command == "help":
        store.add_message(phone, "user", text, kind="system", loop_id=event.message_id)
        await say(phone, prompts.TIPS, kind="system")
        return

    await _chat(phone, user, text, event.message_id, event.in_reply_to)


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
    _keep_asked_order(text, updates)
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


_SAYS_WHICH_LANGUAGE = re.compile(
    r"\b(native|mother|first|speak|speaks|learn|learning|practice|practise|practicing|"
    r"study|studying|want|wants|target|improve)\b",
    re.IGNORECASE,
)


def _keep_asked_order(text: str, updates: dict[str, str]) -> None:
    """Undo a native/target mix-up when both languages came as a bare list.

    The welcome asks for the native language first, so "english korean" means
    an English speaker practicing Korean. The model sometimes reads it the
    other way round. If the message says which is which, it is left alone.
    """
    native, target = updates.get("native"), updates.get("target")
    if not native or not target or _SAYS_WHICH_LANGUAGE.search(text):
        return
    lowered = text.lower()
    native_at, target_at = lowered.find(native.lower()), lowered.find(target.lower())
    if native_at == -1 or target_at == -1 or native_at < target_at:
        return
    updates["native"], updates["target"] = target, native


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


_HANGUL = re.compile(r"[가-힣ㄱ-ㅎㅏ-ㅣ]")


def _same(first: str, second: str) -> bool:
    """True when two texts differ only in spacing and punctuation."""
    return _SPACING_AND_PUNCTUATION.sub("", first) == _SPACING_AND_PUNCTUATION.sub("", second)


_LATIN = re.compile(r"[A-Za-z]")


def _in_wrong_language(user: dict[str, Any], answer: str) -> bool:
    """True when an out-of-character answer is mostly Korean for a non-Korean speaker.

    Explanations quote Korean words, so some Hangul is expected. More Hangul
    than Latin letters means the model answered in the wrong language.
    """
    if not prompts.is_korean(user["target"]) or prompts.is_korean(user["native"]):
        return False
    return len(_HANGUL.findall(answer)) > len(_LATIN.findall(answer))


_PUNCTUATION = re.compile(r"[.,!?~…]+")
_PARTICLES = "을를이가은는"


def _only_drops_particles(text: str, correction: str) -> bool:
    """True when a "fix" does nothing but remove particles the user wrote.

    Dropping 을/를 and the like is common in casual texts, but keeping them is
    never wrong, so this is not a correction.
    """
    original = _PUNCTUATION.sub("", text).split()
    fixed = _PUNCTUATION.sub("", correction).split()
    if len(original) != len(fixed):
        return False
    dropped = False
    for before, after in zip(original, fixed):
        if before == after:
            continue
        if len(before) > 1 and before[-1] in _PARTICLES and before[:-1] == after:
            dropped = True
            continue
        return False
    return dropped


def usable_correction(user: dict[str, Any], text: str, correction: str) -> bool:
    """Whether a correction from the model is worth sending.

    The model sometimes "fixes" punctuation, removes a particle that was fine,
    or translates a message that was written in the user's own language. None
    of those is a mistake in their Korean.
    """
    if not correction or _same(correction, text) or _only_drops_particles(text, correction):
        return False
    if prompts.is_korean(user["target"]) and not _HANGUL.search(text):
        return False
    return True


def _history(phone: str) -> list[dict[str, Any]]:
    """Recent conversation for the model, without onboarding and help messages."""
    recent = store.recent(phone, config.HISTORY_LIMIT)
    return [message for message in recent if message["kind"] != "system"]


async def _chat(
    phone: str,
    user: dict[str, Any],
    text: str,
    message_id: str | None,
    in_reply_to: str | None = None,
) -> None:
    """One chat turn, from a single model call.

    If the user's message has a real error, Dori first replies inline to that
    message with the fix, then carries on the conversation. If the user asked
    a question about the language, Dori answers it instead.

    ``in_reply_to`` is the id of the message the user replied to inline. The
    model is shown that message, so "what's that?" has something to point at.
    """
    history = _history(phone)
    replied_to = store.find_by_loop_id(phone, in_reply_to) if in_reply_to else None
    store.add_message(phone, "user", text, loop_id=message_id)

    system = prompts.chat_system(user)
    content = prompts.chat_turn(user, history, text, replied_to)
    data = await llm.chat_json(system, [{"role": "user", "content": content}])
    correction = _text(data.get("correction"))
    explanation = _text(data.get("explanation"))
    reply = _text(data.get("reply"))

    if data.get("intent") == "question":
        answer = explanation or reply
        if answer and _in_wrong_language(user, answer):
            log.warning("the answer came back in the wrong language; asking again")
            retry = content + prompts.wrong_language_note(user)
            again = await llm.chat_json(system, [{"role": "user", "content": retry}])
            answer = _text(again.get("explanation")) or _text(again.get("reply")) or answer
        if not answer:
            raise BotError("the model gave no answer to the question")
        await say(phone, answer, kind="explanation")
        # Stay out of character, so follow-up questions are not interrupted.
        store.set_mode(phone, "explain")
        return

    corrected = usable_correction(user, text, correction)
    if corrected:
        fix = f"✏️ {correction}\n💬 {explanation}" if explanation else f"✏️ {correction}"
        await say(phone, fix, kind="correction", reply_to=message_id)
    if not reply and not corrected:
        raise BotError("the model gave no reply")
    await say_bubbles(phone, reply)
    store.set_mode(phone, "chat")


# --- ❓ reactions ---


def _reaction_target(phone: str, event: Event) -> dict[str, Any] | None:
    """Work out which of Dori's messages a ❓ landed on.

    Normally the reaction carries that message's id. If the id is not one we
    stored, fall back to matching the text that came with the reaction, and
    then to Dori's most recent message, which is the one people usually mean.
    """
    if event.message_id:
        found = store.find_by_loop_id(phone, event.message_id)
        if found is not None:
            return found if found["role"] == "bot" else None

    from_dori = [message for message in _history(phone) if message["role"] == "bot"]
    quoted = event.text.strip()
    if quoted:
        for message in reversed(from_dori):
            if message["text"].strip() == quoted:
                return message
    return from_dori[-1] if from_dori else None


async def _explanation(system: str, content: str) -> str:
    """One model call that returns an explanation, or "" if it gave none."""
    data = await llm.chat_json(system, [{"role": "user", "content": content}])
    return _text(data.get("explanation")) or _text(data.get("reply"))


async def _explain(phone: str, user: dict[str, Any], event: Event) -> None:
    """Explain the message a ❓ landed on, as an inline reply to it."""
    target = _reaction_target(phone, event)
    if target is None:
        log.info("ignored a question reaction that points at none of Dori's messages")
        return

    system = prompts.explain_system(user)
    content = prompts.explain_turn(user, _history(phone), target["text"], target["kind"])
    explanation = await _explanation(system, content)
    if explanation and _in_wrong_language(user, explanation):
        log.warning("the explanation came back in the wrong language; asking again")
        retry = content + prompts.wrong_language_note(user)
        explanation = await _explanation(system, retry) or explanation
    if not explanation:
        raise BotError("the model gave no explanation")
    await say(phone, explanation, kind="explanation", reply_to=target["loop_id"])
    store.set_mode(phone, "explain")
