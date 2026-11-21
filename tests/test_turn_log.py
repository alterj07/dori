"""The JSON-lines debug log."""

import json

import pytest

from dori import config, turn_log

PHONE = "+15555550100"


def log_lines():
    text = config.LOG_PATH.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


def note_call(model="primary", output="{}"):
    turn_log.note_llm(
        model_used=model,
        fallback=False,
        fallback_reason=None,
        latency_s=1.2345,
        raw_model_output=output,
    )


def test_write_appends_one_timestamped_line_per_record():
    turn_log.write({"event_type": "first"})
    turn_log.write({"event_type": "second"})

    lines = log_lines()
    assert [line["event_type"] for line in lines] == ["first", "second"]
    assert all(line["ts"] for line in lines)


def test_korean_is_written_as_readable_text():
    turn_log.write({"incoming_text": "안녕"})

    assert "안녕" in config.LOG_PATH.read_text(encoding="utf-8")


def test_webhook_line_keeps_the_raw_payload():
    payload = {"event": "message_inbound", "contact": PHONE, "text": "hi", "message_id": "in-1"}

    turn_log.webhook(payload, "buffered")

    (line,) = log_lines()
    assert line["phone"] == PHONE
    assert line["event_type"] == "message_inbound"
    assert line["incoming_text"] == "hi"
    assert line["note"] == "buffered"
    assert line["payload"] == payload


def test_turn_collects_model_and_outgoing_details():
    with turn_log.turn(PHONE, "turn:message", "hi"):
        note_call(model="primary", output='{"reply": "안녕"}')
        turn_log.note_outgoing("안녕")

    (line,) = log_lines()
    assert line["phone"] == PHONE
    assert line["event_type"] == "turn:message"
    assert line["incoming_text"] == "hi"
    assert line["model_used"] == "primary"
    assert line["fallback"] is False
    assert line["latency_s"] == 1.23
    assert line["raw_model_output"] == '{"reply": "안녕"}'
    assert line["outgoing_texts"] == ["안녕"]
    assert line["error"] is None


def test_a_second_model_call_keeps_the_first_one():
    with turn_log.turn(PHONE, "turn:message", "hi"):
        note_call(model="first")
        note_call(model="second")

    (line,) = log_lines()
    assert line["model_used"] == "second"
    assert [call["model_used"] for call in line["earlier_llm_calls"]] == ["first"]


def test_notes_outside_a_turn_are_ignored():
    note_call()
    turn_log.note_outgoing("hello")

    assert not config.LOG_PATH.exists()


def test_turn_is_logged_even_when_it_raises():
    with pytest.raises(RuntimeError):
        with turn_log.turn(PHONE, "turn:message", "hi"):
            raise RuntimeError("boom")

    (line,) = log_lines()
    assert "boom" in line["error"]
