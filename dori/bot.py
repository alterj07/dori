"""Bot behaviour. For now this is a stub that echoes, to prove the plumbing works."""

from __future__ import annotations

from dori import loop_client, store
from dori.events import Event


async def say(phone: str, text: str, kind: str = "chat", reply_to: str | None = None) -> str:
    """Send a message and store it, so a later ❓ reaction can find it."""
    message_id = await loop_client.send(phone, text, reply_to=reply_to)
    store.add_message(phone, "bot", text, kind=kind, loop_id=message_id)
    return message_id


async def handle_turn(phone: str, event: Event) -> None:
    """Echo a text back as an inline reply; name the message a ❓ landed on."""
    if event.kind == "reaction":
        target = store.find_by_loop_id(phone, event.message_id) if event.message_id else None
        if target is None:
            await say(phone, f"You reacted ❓, but I can't match it to a message (id {event.message_id}).")
            return
        await say(phone, f"You reacted ❓ to: {target['text']}", reply_to=event.message_id)
        return

    store.add_message(phone, "user", event.text, loop_id=event.message_id)
    await say(phone, f"Echo: {event.text}", reply_to=event.message_id)
