"""Talking to Nemotron: lenient JSON parsing of what the model sends back."""

from __future__ import annotations

import json
import re
from typing import Any

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
