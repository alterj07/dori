"""The unit of work the server hands to the bot."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class Event:
    """One turn for the bot to handle.

    kind "message": ``text`` is everything the user sent during the debounce
    window, joined with newlines. ``message_id`` is the id of their last text,
    which is the target for an inline correction.

    kind "reaction": ``message_id`` is the id of the message the user reacted
    to and ``reaction`` is the tapback name. Only "question" reaches the bot.
    ``text`` is whatever text LoopMessage sent along with the reaction.
    """

    kind: Literal["message", "reaction"]
    text: str = ""
    message_id: str | None = None
    reaction: str | None = None
