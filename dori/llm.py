"""Talking to Nemotron.

Every call goes to Nemotron 3 Ultra first. If Ultra errors, rate-limits or
takes too long, the call is retried once on Nemotron 3.5 Lightning. The model
is asked for JSON, and what comes back is parsed leniently.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from dori import config, turn_log

log = logging.getLogger("dori.llm")

_client: httpx.AsyncClient | None = None


class LLMError(RuntimeError):
    """The model answered, but with nothing usable."""


@dataclass(frozen=True, slots=True)
class Completion:
    """What a model call returned, plus what the log wants to know about it."""

    text: str
    model: str
    latency_s: float
    fallback: bool
    fallback_reason: str | None = None


_FENCE = re.compile(r"```(?:json)?", re.IGNORECASE)
_THINKING = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_PAIR = re.compile(r'"(\w+)"\s*:\s*(null|"(?:[^"\\]|\\.)*")', re.DOTALL)


def parse_json(raw: str) -> dict[str, Any] | None:
    """Pull a JSON object out of model output, or return None.

    Models wrap JSON in code fences, add a sentence before or after it, and put
    real line breaks inside strings. This strips the fences, takes everything
    from the first "{" to the last "}" and parses that leniently. If the object
    is cut off, it keeps whichever string and null fields are complete.
    """
    text = _FENCE.sub("", _THINKING.sub("", raw))
    start = text.find("{")
    if start == -1:
        return None
    end = text.rfind("}")
    if end > start:
        try:
            data = json.loads(text[start : end + 1], strict=False)
        except ValueError:
            data = None
        if isinstance(data, dict):
            return data
    return _salvage(text[start:])


def _salvage(text: str) -> dict[str, Any] | None:
    """Recover the complete "key": "value" and "key": null pairs from broken JSON."""
    found: dict[str, Any] = {}
    for key, value in _PAIR.findall(text):
        try:
            found[key] = json.loads(value, strict=False)
        except ValueError:
            continue
    return found or None


def _http() -> httpx.AsyncClient:
    """One shared client, so connections to NVIDIA are reused."""
    global _client
    if _client is None:
        # asyncio.timeout enforces the real limit; this is only a backstop.
        _client = httpx.AsyncClient(timeout=config.LLM_TIMEOUT_S + 5)
    return _client


async def aclose() -> None:
    """Close the shared client (called at server shutdown)."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def _call(model: str, system: str, messages: list[dict[str, str]]) -> str:
    """One chat completion request. Returns the text the model wrote."""
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, *messages],
        "temperature": config.LLM_TEMPERATURE,
        "max_tokens": config.LLM_MAX_TOKENS,
        # Nemotron reasons out loud by default, which costs seconds per text.
        "chat_template_kwargs": {"enable_thinking": False},
    }
    response = await _http().post(
        config.NVIDIA_CHAT_URL,
        json=body,
        headers={"Authorization": f"Bearer {config.NVIDIA_API_KEY}"},
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"].get("content")
    if not content or not content.strip():
        raise LLMError(f"{model} returned an empty message")
    return content


def _describe(exc: Exception) -> str:
    """A short reason for the log when the primary model fails."""
    if isinstance(exc, TimeoutError):
        return f"timeout after {config.LLM_TIMEOUT_S:g}s"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return repr(exc)[:200]


async def complete(system: str, messages: list[dict[str, str]]) -> Completion:
    """Ask Ultra. On any error, a 429 or a timeout, ask Lightning once instead.

    If Lightning fails too, its exception propagates to the caller.
    """
    started = time.perf_counter()
    model = config.PRIMARY_MODEL
    reason: str | None = None
    try:
        async with asyncio.timeout(config.LLM_TIMEOUT_S):
            text = await _call(model, system, messages)
    except Exception as exc:
        reason = _describe(exc)
        model = config.FALLBACK_MODEL
        log.warning("FALLBACK to %s: %s", model, reason)
        async with asyncio.timeout(config.LLM_TIMEOUT_S):
            text = await _call(model, system, messages)

    completion = Completion(
        text=text,
        model=model,
        latency_s=time.perf_counter() - started,
        fallback=reason is not None,
        fallback_reason=reason,
    )
    turn_log.note_llm(
        model_used=completion.model,
        fallback=completion.fallback,
        fallback_reason=completion.fallback_reason,
        latency_s=completion.latency_s,
        raw_model_output=completion.text,
    )
    return completion


async def chat_json(system: str, messages: list[dict[str, str]]) -> dict[str, Any]:
    """Ask the model for a JSON object and return it as a dict.

    If the model answers in plain prose instead, that text comes back as an
    ordinary in-character reply. Broken JSON that cannot be recovered raises
    ``LLMError`` rather than being shown to the user.
    """
    completion = await complete(system, messages)
    data = parse_json(completion.text)
    if data is not None:
        return data

    raw = completion.text.strip()
    if raw.startswith(("{", "```")):
        raise LLMError("the model returned JSON that could not be read")
    log.warning("the model did not return JSON; using its text as the reply")
    return {"intent": "chat", "correction": None, "explanation": None, "reply": raw}
