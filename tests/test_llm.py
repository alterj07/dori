"""Nemotron client: lenient JSON parsing, the fallback policy and JSON replies."""

import asyncio
import json

import httpx
import pytest

from dori import config, llm, turn_log

SYSTEM = "You are Dori."
MESSAGES = [{"role": "user", "content": "안녕"}]


def use_handler(monkeypatch, handler):
    """Send the client's requests to ``handler`` instead of the network."""
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(llm, "_client", client)


def answer(text):
    """A chat completion response whose message is ``text``."""
    message = {"role": "assistant", "content": text}
    return httpx.Response(200, json={"choices": [{"message": message}]})


def ultra_fails_with(make_response):
    """A handler where Ultra gets ``make_response()`` and Lightning answers normally."""
    models = []

    def handler(request):
        model = json.loads(request.content)["model"]
        models.append(model)
        if model == config.PRIMARY_MODEL:
            return make_response()
        return answer('{"reply": "from lightning"}')

    return models, handler


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


async def test_asks_ultra_first_with_thinking_turned_off(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return answer('{"reply": "안녕!"}')

    use_handler(monkeypatch, handler)

    completion = await llm.complete(SYSTEM, MESSAGES)

    assert completion.text == '{"reply": "안녕!"}'
    assert completion.model == config.PRIMARY_MODEL
    assert completion.fallback is False
    assert completion.fallback_reason is None
    assert seen["url"] == config.NVIDIA_CHAT_URL
    assert seen["authorization"] == "Bearer test-nvidia-key"
    assert seen["body"]["model"] == config.PRIMARY_MODEL
    assert seen["body"]["messages"] == [{"role": "system", "content": SYSTEM}, *MESSAGES]
    assert seen["body"]["chat_template_kwargs"] == {"enable_thinking": False}


@pytest.mark.parametrize("status", [429, 500, 410])
async def test_an_error_from_ultra_falls_back_to_lightning_once(monkeypatch, status):
    models, handler = ultra_fails_with(lambda: httpx.Response(status, json={"detail": "no"}))
    use_handler(monkeypatch, handler)

    completion = await llm.complete(SYSTEM, MESSAGES)

    assert models == [config.PRIMARY_MODEL, config.FALLBACK_MODEL]
    assert completion.text == '{"reply": "from lightning"}'
    assert completion.model == config.FALLBACK_MODEL
    assert completion.fallback is True
    assert completion.fallback_reason == f"HTTP {status}"


async def test_a_rate_limited_ultra_is_asked_again_before_falling_back(monkeypatch):
    monkeypatch.setattr(config, "RATE_LIMIT_WAITS_S", (0.01, 0.01))
    models = []

    def handler(request):
        models.append(json.loads(request.content)["model"])
        if len(models) < 3:
            return httpx.Response(429, json={"detail": "slow down"})
        return answer('{"reply": "from ultra"}')

    use_handler(monkeypatch, handler)

    completion = await llm.complete(SYSTEM, MESSAGES)

    assert models == [config.PRIMARY_MODEL] * 3
    assert completion.model == config.PRIMARY_MODEL
    assert completion.fallback is False


async def test_an_ultra_that_stays_rate_limited_falls_back_and_says_so(monkeypatch):
    monkeypatch.setattr(config, "RATE_LIMIT_WAITS_S", (0.01, 0.01))
    models, handler = ultra_fails_with(lambda: httpx.Response(429, json={}))
    use_handler(monkeypatch, handler)

    completion = await llm.complete(SYSTEM, MESSAGES)

    assert models == [config.PRIMARY_MODEL] * 3 + [config.FALLBACK_MODEL]
    assert completion.fallback_reason == "HTTP 429 after 2 rate-limit waits"


async def test_other_errors_from_ultra_are_not_retried(monkeypatch):
    monkeypatch.setattr(config, "RATE_LIMIT_WAITS_S", (0.01, 0.01))
    models, handler = ultra_fails_with(lambda: httpx.Response(500, json={}))
    use_handler(monkeypatch, handler)

    await llm.complete(SYSTEM, MESSAGES)

    assert models == [config.PRIMARY_MODEL, config.FALLBACK_MODEL]


async def test_chat_json_says_when_the_fallback_model_answered(monkeypatch):
    models, handler = ultra_fails_with(lambda: httpx.Response(500, json={}))
    use_handler(monkeypatch, handler)

    data = await llm.chat_json(SYSTEM, MESSAGES)

    assert data == {"reply": "from lightning", "_fallback": True}


async def test_an_empty_answer_from_ultra_falls_back(monkeypatch):
    models, handler = ultra_fails_with(lambda: answer("   "))
    use_handler(monkeypatch, handler)

    completion = await llm.complete(SYSTEM, MESSAGES)

    assert models == [config.PRIMARY_MODEL, config.FALLBACK_MODEL]
    assert completion.fallback is True


async def test_a_slow_ultra_falls_back(monkeypatch):
    monkeypatch.setattr(config, "LLM_TIMEOUT_S", 0.05)
    models = []

    async def handler(request):
        model = json.loads(request.content)["model"]
        models.append(model)
        if model == config.PRIMARY_MODEL:
            await asyncio.sleep(1)
        return answer('{"reply": "from lightning"}')

    use_handler(monkeypatch, handler)

    completion = await llm.complete(SYSTEM, MESSAGES)

    assert models == [config.PRIMARY_MODEL, config.FALLBACK_MODEL]
    assert completion.model == config.FALLBACK_MODEL
    assert completion.fallback_reason == "timeout after 0.05s"


async def test_raises_when_lightning_fails_too(monkeypatch):
    models = []

    def handler(request):
        models.append(json.loads(request.content)["model"])
        return httpx.Response(429, json={"detail": "rate limited"})

    use_handler(monkeypatch, handler)

    with pytest.raises(httpx.HTTPStatusError):
        await llm.complete(SYSTEM, MESSAGES)
    assert models == [config.PRIMARY_MODEL, config.FALLBACK_MODEL]


async def test_a_call_is_added_to_the_current_turn_log(monkeypatch):
    models, handler = ultra_fails_with(lambda: httpx.Response(429, json={}))
    use_handler(monkeypatch, handler)

    with turn_log.turn("+15555550100", "turn:message", "안녕") as record:
        await llm.complete(SYSTEM, MESSAGES)

    assert record["model_used"] == config.FALLBACK_MODEL
    assert record["fallback"] is True
    assert record["fallback_reason"] == "HTTP 429"
    assert record["raw_model_output"] == '{"reply": "from lightning"}'
    assert record["latency_s"] >= 0


async def test_chat_json_returns_the_parsed_object(monkeypatch):
    raw = '```json\n{"intent": "chat", "correction": null, "explanation": null, "reply": "안녕!"}\n```'
    use_handler(monkeypatch, lambda request: answer(raw))

    data = await llm.chat_json(SYSTEM, MESSAGES)

    assert data == {"intent": "chat", "correction": None, "explanation": None, "reply": "안녕!"}


async def test_chat_json_uses_plain_prose_as_the_reply(monkeypatch):
    use_handler(monkeypatch, lambda request: answer("  오, 진짜? 무슨 영화 봤어?  "))

    data = await llm.chat_json(SYSTEM, MESSAGES)

    assert data == {
        "intent": "chat",
        "correction": None,
        "explanation": None,
        "reply": "오, 진짜? 무슨 영화 봤어?",
    }


async def test_chat_json_refuses_to_pass_on_broken_json(monkeypatch):
    use_handler(monkeypatch, lambda request: answer("{oops"))

    with pytest.raises(llm.LLMError):
        await llm.chat_json(SYSTEM, MESSAGES)
