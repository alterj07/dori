"""Thin async client for the LoopMessage iMessage API."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from dori import config, turn_log

log = logging.getLogger("dori.loop")

_client: httpx.AsyncClient | None = None


class LoopError(RuntimeError):
    """LoopMessage could not be reached or refused the request."""


def _http() -> httpx.AsyncClient:
    """One shared client, so connections to LoopMessage are reused."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=config.LOOP_TIMEOUT_S)
    return _client


async def aclose() -> None:
    """Close the shared client (called at server shutdown)."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _headers() -> dict[str, str]:
    # LoopMessage wants the raw key here, with no "Bearer" prefix.
    return {"Authorization": config.LOOP_API_KEY}


def _body(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


async def send(phone: str, text: str, reply_to: str | None = None) -> str:
    """Send one iMessage and return LoopMessage's ``message_id`` for it.

    ``reply_to`` is the message_id of a message to reply to inline. A 200 only
    means LoopMessage queued the message, not that it was delivered. Raises
    ``LoopError`` if the request fails or is refused.
    """
    body: dict[str, str] = {"contact": phone, "text": text}
    if reply_to:
        body["reply_to_id"] = reply_to
    try:
        response = await _http().post(config.LOOP_SEND_URL, json=body, headers=_headers())
    except httpx.HTTPError as exc:
        raise LoopError(f"could not reach LoopMessage: {exc!r}") from exc

    data = _body(response)
    message_id = data.get("message_id")
    if response.status_code != 200 or data.get("success") is False or not message_id:
        raise LoopError(
            f"send refused: HTTP {response.status_code}, "
            f"code={data.get('code')}, message={data.get('message')!r}"
        )

    turn_log.note_outgoing(text)
    log.info("sent %s%s", message_id, " as an inline reply" if reply_to else "")
    return str(message_id)


async def show_typing(message_id: str, seconds: int | None = None) -> bool:
    """Mark the user's message as read and show a typing bubble.

    Purely cosmetic, so it never raises: it returns False if LoopMessage says
    no or cannot be reached. ``seconds`` defaults to ``config.TYPING_SECONDS``.
    """
    seconds = config.TYPING_SECONDS if seconds is None else seconds
    if not message_id or seconds <= 0:
        return False
    body = {"message_id": message_id, "typing": min(seconds, 60), "read": True}
    try:
        response = await _http().post(config.LOOP_TYPING_URL, json=body, headers=_headers())
    except httpx.HTTPError as exc:
        log.warning("typing indicator failed: %r", exc)
        return False
    if response.status_code != 200 or _body(response).get("success") is False:
        log.warning("typing indicator refused: HTTP %s %s", response.status_code, response.text[:200])
        return False
    return True
