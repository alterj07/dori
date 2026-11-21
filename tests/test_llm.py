"""Nemotron client: lenient JSON parsing."""

from dori import llm


def test_parses_a_plain_json_object():
    raw = '{"intent": "chat", "correction": null, "reply": "안녕"}'

    assert llm.parse_json(raw) == {"intent": "chat", "correction": None, "reply": "안녕"}


def test_strips_code_fences():
    raw = '```json\n{"reply": "안녕"}\n```'

    assert llm.parse_json(raw) == {"reply": "안녕"}


def test_ignores_text_around_the_object():
    raw = 'Sure! Here it is:\n{"reply": "안녕"}\nHope that helps.'

    assert llm.parse_json(raw) == {"reply": "안녕"}


def test_allows_real_line_breaks_inside_strings():
    raw = '{"reply": "오, 무슨 영화 봤어?\n나도 보고 싶다"}'

    assert llm.parse_json(raw) == {"reply": "오, 무슨 영화 봤어?\n나도 보고 싶다"}


def test_drops_a_thinking_block():
    raw = '<think>{"draft": "not this"}</think>{"reply": "안녕"}'

    assert llm.parse_json(raw) == {"reply": "안녕"}


def test_returns_none_when_there_is_no_object():
    assert llm.parse_json("그냥 평범한 답장이야") is None
    assert llm.parse_json("[1, 2]") is None
    assert llm.parse_json("{oops") is None


def test_recovers_the_complete_fields_of_a_cut_off_object():
    raw = '{"intent": "chat", "correction": null, "reply": "오 진짜?", "explanation": "Use 반말 cons'

    assert llm.parse_json(raw) == {"intent": "chat", "correction": None, "reply": "오 진짜?"}
