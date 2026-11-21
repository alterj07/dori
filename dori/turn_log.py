"""JSON-lines debug log (``logs/dori.jsonl``).

Two kinds of line are written: one when a webhook arrives and one when the bot
finishes a turn. ``loop_client`` and ``llm`` add their details to the current
turn through ``note_outgoing`` and ``note_llm``, so the bot itself needs no
logging code.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from typing import Any

from dori import config

_LLM_FIELDS = ("model_used", "fallback", "fallback_reason", "latency_s", "raw_model_output")

# The record of the turn being handled by the current asyncio task, if any.
_current: ContextVar[dict[str, Any] | None] = ContextVar("dori_turn", default=None)


def write(record: dict[str, Any]) -> None:
    """Append one record to the log as a single JSON line."""
    path = config.LOG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    stamped = {"ts": datetime.now().astimezone().isoformat(timespec="seconds"), **record}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(stamped, ensure_ascii=False, default=str) + "\n")


def webhook(payload: dict[str, Any], note: str) -> None:
    """Log a webhook as it arrives. ``note`` says what the server did with it."""
    write(
        {
            "phone": payload.get("contact") or payload.get("recipient"),
            "event_type": payload.get("event") or payload.get("alert_type"),
            "incoming_text": payload.get("text"),
            "note": note,
            "payload": payload,
        }
    )


@contextmanager
def turn(phone: str, event_type: str, incoming_text: str) -> Iterator[dict[str, Any]]:
    """Collect everything that happens during one bot turn, then log it."""
    record: dict[str, Any] = {
        "phone": phone,
        "event_type": event_type,
        "incoming_text": incoming_text,
        "model_used": None,
        "fallback": False,
        "fallback_reason": None,
        "latency_s": None,
        "raw_model_output": None,
        "outgoing_texts": [],
        "error": None,
    }
    token = _current.set(record)
    started = time.perf_counter()
    try:
        yield record
    except BaseException as exc:
        record["error"] = record["error"] or repr(exc)
        raise
    finally:
        _current.reset(token)
        record["turn_s"] = round(time.perf_counter() - started, 2)
        write(record)


def note_llm(
    *,
    model_used: str,
    fallback: bool,
    fallback_reason: str | None,
    latency_s: float,
    raw_model_output: str,
) -> None:
    """Record a model call on the current turn.

    The top-level fields describe the latest call. If a turn makes more than
    one call, the earlier ones move to ``earlier_llm_calls``.
    """
    record = _current.get()
    if record is None:
        return
    if record["model_used"] is not None:
        record.setdefault("earlier_llm_calls", []).append({key: record[key] for key in _LLM_FIELDS})
    record.update(
        model_used=model_used,
        fallback=fallback,
        fallback_reason=fallback_reason,
        latency_s=round(latency_s, 2),
        raw_model_output=raw_model_output,
    )


def note_outgoing(text: str) -> None:
    """Record a message sent to the user on the current turn."""
    record = _current.get()
    if record is not None:
        record["outgoing_texts"].append(text)
