"""Webhook endpoint and dispatcher: auth, dedupe, debounce, locking, fallback."""

import asyncio
import itertools
import json

import httpx
import pytest

from dori import bot, config, loop_client, server
from dori.events import Event

PHONE = "+15555550100"
OTHER = "+15555550101"
AUTH = {"Authorization": "test-webhook-secret"}

_ids = itertools.count(1)


def inbound(text, message_id="in-1", phone=PHONE, webhook_id=None):
    return {
        "event": "message_inbound",
        "contact": phone,
        "text": text,
        "message_id": message_id,
        "webhook_id": webhook_id or f"wh-{next(_ids)}",
    }


def reaction(name, message_id="out-1", phone=PHONE, event="placed", direction="inbound"):
    """A reaction webhook shaped like the ones LoopMessage really sends."""
    return {
        "event": "message_reaction",
        "contact": phone,
        "reaction_type": name,
        "reaction_event": event,
        "reaction_direction": direction,
        "message_id": message_id,
        "webhook_id": f"wh-{next(_ids)}",
    }


def log_lines():
    if not config.LOG_PATH.exists():
        return []
    text = config.LOG_PATH.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


@pytest.fixture(autouse=True)
def fast_dispatcher(monkeypatch):
    """A fresh dispatcher per test, with a short debounce and no typing calls."""
    monkeypatch.setattr(config, "DEBOUNCE_S", 0.05)
    monkeypatch.setattr(config, "TYPING_SECONDS", 0)
    monkeypatch.setattr(server, "dispatcher", server.Dispatcher())


@pytest.fixture
def turns(monkeypatch):
    """Replace the bot with a recorder and return the turns it was given."""
    seen = []

    async def record(phone, event):
        seen.append((phone, event))

    monkeypatch.setattr(bot, "handle_turn", record)
    return seen


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


async def test_health(client):
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


@pytest.mark.parametrize("headers", [{}, {"Authorization": "wrong"}, {"Authorization": ""}])
async def test_rejects_a_missing_or_wrong_authorization_header(client, turns, headers):
    response = await client.post("/webhook", json=inbound("hi"), headers=headers)
    await server.dispatcher.idle()

    assert response.status_code == 401
    assert turns == []


async def test_rejects_everything_when_no_secret_is_configured(client, turns, monkeypatch):
    monkeypatch.setattr(config, "WEBHOOK_SECRET", "")

    response = await client.post("/webhook", json=inbound("hi"), headers={"Authorization": ""})
    await server.dispatcher.idle()

    assert response.status_code == 401
    assert turns == []


async def test_acknowledges_before_the_turn_runs(client, turns):
    response = await client.post("/webhook", json=inbound("hi"), headers=AUTH)

    assert response.status_code == 200
    assert turns == []  # still inside the debounce window

    await server.dispatcher.idle()
    assert turns == [(PHONE, Event(kind="message", text="hi", message_id="in-1"))]


async def test_a_body_that_is_not_json_is_acknowledged_and_ignored(client, turns):
    response = await client.post("/webhook", content=b"not json", headers=AUTH)
    await server.dispatcher.idle()

    assert response.status_code == 200
    assert turns == []


async def test_a_repeated_webhook_id_is_processed_once(client, turns):
    payload = inbound("hi", webhook_id="wh-repeat")

    for _ in range(3):
        response = await client.post("/webhook", json=payload, headers=AUTH)
        assert response.status_code == 200
    await server.dispatcher.idle()

    assert [event.text for _, event in turns] == ["hi"]
    webhook_notes = [line["note"] for line in log_lines() if "note" in line]
    assert webhook_notes == ["buffered", "duplicate", "duplicate"]


async def test_a_burst_of_texts_becomes_one_turn(client, turns):
    for number, text in enumerate(["안녕", "뭐해?", "나 심심해"], start=1):
        await client.post("/webhook", json=inbound(text, message_id=f"in-{number}"), headers=AUTH)
    await server.dispatcher.idle()

    assert turns == [(PHONE, Event(kind="message", text="안녕\n뭐해?\n나 심심해", message_id="in-3"))]


async def test_each_new_text_restarts_the_debounce_timer(client, turns, monkeypatch):
    monkeypatch.setattr(config, "DEBOUNCE_S", 0.3)

    await client.post("/webhook", json=inbound("one", message_id="in-1"), headers=AUTH)
    await asyncio.sleep(0.2)
    await client.post("/webhook", json=inbound("two", message_id="in-2"), headers=AUTH)
    await asyncio.sleep(0.2)

    # 0.4s after the first text, but only 0.2s after the second.
    assert turns == []

    await server.dispatcher.idle()
    assert [event.text for _, event in turns] == ["one\ntwo"]


async def test_a_question_reaction_skips_the_debounce(client, turns, monkeypatch):
    monkeypatch.setattr(config, "DEBOUNCE_S", 30)

    response = await client.post("/webhook", json=reaction("question", "out-9"), headers=AUTH)
    await server.dispatcher.idle()

    assert response.status_code == 200
    assert turns == [(PHONE, Event(kind="reaction", message_id="out-9", reaction="question"))]


async def test_older_payload_field_names_are_understood(client, turns):
    payload = {
        "alert_type": "message_inbound",
        "recipient": PHONE,
        "text": "hi",
        "message_id": "in-1",
        "webhook_id": "wh-old-style",
    }

    await client.post("/webhook", json=payload, headers=AUTH)
    await server.dispatcher.idle()

    assert turns == [(PHONE, Event(kind="message", text="hi", message_id="in-1"))]
    assert log_lines()[0]["event_type"] == "message_inbound"


@pytest.mark.parametrize("emoji", ["❓", "❔", "?", "⁉️"])
async def test_a_question_mark_emoji_counts_as_a_question_reaction(client, turns, emoji):
    await client.post("/webhook", json=reaction(emoji, "out-9"), headers=AUTH)
    await server.dispatcher.idle()

    assert turns == [(PHONE, Event(kind="reaction", message_id="out-9", reaction="question"))]


async def test_the_reaction_field_name_from_the_docs_is_understood_too(client, turns):
    payload = {
        "event": "message_reaction",
        "contact": PHONE,
        "reaction": "question",
        "message_id": "out-9",
        "webhook_id": "wh-docs-shape",
    }

    await client.post("/webhook", json=payload, headers=AUTH)
    await server.dispatcher.idle()

    assert turns == [(PHONE, Event(kind="reaction", message_id="out-9", reaction="question"))]


async def test_a_reaction_carries_the_text_sent_with_it(client, turns):
    payload = {**reaction("question", "out-9"), "text": "오 대박!"}

    await client.post("/webhook", json=payload, headers=AUTH)
    await server.dispatcher.idle()

    expected = Event(kind="reaction", text="오 대박!", message_id="out-9", reaction="question")
    assert turns == [(PHONE, expected)]


@pytest.mark.parametrize(
    "payload",
    [
        reaction("love"),
        reaction("-question"),
        reaction("question", event="removed"),
        reaction("❓", direction="outbound"),
        {"event": "message_delivered", "contact": PHONE, "message_id": "out-1"},
        {"event": "message_failed", "contact": PHONE, "message_id": "out-1"},
        {"event": "unknown"},
        inbound("   "),
        {"event": "message_inbound", "contact": PHONE, "text": "hi", "group": {"group_id": "g"}},
    ],
    ids=[
        "love",
        "old-style-removal",
        "taken-back",
        "outbound",
        "delivered",
        "failed",
        "unknown",
        "blank-text",
        "group",
    ],
)
async def test_other_events_are_logged_but_start_no_turn(client, turns, payload):
    response = await client.post("/webhook", json=payload, headers=AUTH)
    await server.dispatcher.idle()

    assert response.status_code == 200
    assert turns == []
    assert log_lines()[-1]["payload"] == payload


async def test_one_users_turns_never_overlap(client, monkeypatch):
    running = 0
    peak = 0
    finished = []

    async def slow(phone, event):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.05)
        running -= 1
        finished.append(event.message_id)

    monkeypatch.setattr(bot, "handle_turn", slow)

    for message_id in ("out-1", "out-2", "out-3"):
        await client.post("/webhook", json=reaction("question", message_id), headers=AUTH)
    await server.dispatcher.idle()

    assert peak == 1
    assert finished == ["out-1", "out-2", "out-3"]


async def test_different_users_run_in_parallel(client, monkeypatch):
    running = 0
    peak = 0

    async def slow(phone, event):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.05)
        running -= 1

    monkeypatch.setattr(bot, "handle_turn", slow)

    await client.post("/webhook", json=reaction("question", phone=PHONE), headers=AUTH)
    await client.post("/webhook", json=reaction("question", phone=OTHER), headers=AUTH)
    await server.dispatcher.idle()

    assert peak == 2


async def test_texts_sent_during_a_running_turn_join_the_next_turn(client, monkeypatch):
    seen = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def gated(phone, event):
        seen.append(event.text)
        started.set()
        await release.wait()

    monkeypatch.setattr(bot, "handle_turn", gated)

    await client.post("/webhook", json=inbound("one", message_id="in-1"), headers=AUTH)
    await started.wait()  # the first turn is now running and holds the lock

    await client.post("/webhook", json=inbound("two", message_id="in-2"), headers=AUTH)
    await asyncio.sleep(0.1)  # its timer fires; the second turn waits for the lock
    await client.post("/webhook", json=inbound("three", message_id="in-3"), headers=AUTH)

    release.set()
    await server.dispatcher.idle()

    assert seen == ["one", "two\nthree"]


async def test_inbound_text_shows_the_typing_indicator(client, turns, monkeypatch):
    typed = []

    async def fake_typing(message_id, seconds=None):
        typed.append(message_id)
        return True

    monkeypatch.setattr(config, "TYPING_SECONDS", 12)
    monkeypatch.setattr(loop_client, "show_typing", fake_typing)

    await client.post("/webhook", json=inbound("hi", message_id="in-7"), headers=AUTH)
    await server.dispatcher.idle()

    assert typed == ["in-7"]


async def test_a_failed_turn_sends_the_fallback_and_logs_the_error(client, monkeypatch):
    sent = []

    async def explode(phone, event):
        raise RuntimeError("model exploded")

    async def fake_send(phone, text, reply_to=None):
        sent.append((phone, text))
        return "out-1"

    monkeypatch.setattr(bot, "handle_turn", explode)
    monkeypatch.setattr(loop_client, "send", fake_send)

    await client.post("/webhook", json=inbound("hi"), headers=AUTH)
    await server.dispatcher.idle()

    assert sent == [(PHONE, server.FALLBACK_TEXT)]
    turn_line = log_lines()[-1]
    assert turn_line["event_type"] == "turn:message"
    assert "RuntimeError: model exploded" in turn_line["error"]


async def test_a_failing_fallback_does_not_break_later_turns(client, monkeypatch):
    handled = []

    async def explode_once(phone, event):
        handled.append(event.text)
        if len(handled) == 1:
            raise RuntimeError("model exploded")

    async def broken_send(phone, text, reply_to=None):
        raise loop_client.LoopError("LoopMessage is down")

    monkeypatch.setattr(bot, "handle_turn", explode_once)
    monkeypatch.setattr(loop_client, "send", broken_send)

    await client.post("/webhook", json=inbound("first"), headers=AUTH)
    await server.dispatcher.idle()
    await client.post("/webhook", json=inbound("second"), headers=AUTH)
    await server.dispatcher.idle()

    assert handled == ["first", "second"]


async def test_startup_fails_when_a_required_setting_is_missing(monkeypatch):
    monkeypatch.setattr(config, "WEBHOOK_SECRET", "")

    with pytest.raises(RuntimeError, match="WEBHOOK_SECRET"):
        async with server.lifespan(server.app):
            pass
