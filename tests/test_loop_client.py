"""LoopMessage client: request shape, error handling and the typing indicator."""

import json

import httpx
import pytest

from dori import config, loop_client, turn_log

PHONE = "+15555550100"


def use_handler(monkeypatch, handler):
    """Send the client's requests to ``handler`` instead of the network."""
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(loop_client, "_client", client)


def accept(request):
    return httpx.Response(200, json={"success": True, "message_id": "out-1"})


async def test_send_posts_contact_and_text_with_the_raw_api_key(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return accept(request)

    use_handler(monkeypatch, handler)

    assert await loop_client.send(PHONE, "안녕!") == "out-1"
    assert seen == {
        "url": config.LOOP_SEND_URL,
        "authorization": "test-loop-key",
        "body": {"contact": PHONE, "text": "안녕!"},
    }


async def test_send_with_reply_to_makes_an_inline_reply(monkeypatch):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return accept(request)

    use_handler(monkeypatch, handler)

    await loop_client.send(PHONE, "✏️ 만났어", reply_to="in-7")

    assert bodies == [{"contact": PHONE, "text": "✏️ 만났어", "reply_to_id": "in-7"}]


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(400, json={"success": False, "code": 160, "message": "Invalid contact"}),
        httpx.Response(200, json={"success": False, "code": 200, "message": "No conversation"}),
        httpx.Response(200, json={"success": True}),
        httpx.Response(502, text="bad gateway"),
    ],
    ids=["http-400", "success-false", "no-message-id", "not-json"],
)
async def test_send_raises_when_loopmessage_refuses(monkeypatch, response):
    use_handler(monkeypatch, lambda request: response)

    with pytest.raises(loop_client.LoopError):
        await loop_client.send(PHONE, "hi")


async def test_send_raises_when_loopmessage_is_unreachable(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("no route to host")

    use_handler(monkeypatch, handler)

    with pytest.raises(loop_client.LoopError):
        await loop_client.send(PHONE, "hi")


async def test_send_adds_the_text_to_the_current_turn_log(monkeypatch):
    use_handler(monkeypatch, accept)

    with turn_log.turn(PHONE, "turn:message", "hi") as record:
        await loop_client.send(PHONE, "hello")

    assert record["outgoing_texts"] == ["hello"]


async def test_show_typing_posts_the_message_id_and_marks_it_read(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"success": True})

    use_handler(monkeypatch, handler)

    assert await loop_client.show_typing("in-1", seconds=8) is True
    assert seen == {
        "url": config.LOOP_TYPING_URL,
        "authorization": "test-loop-key",
        "body": {"message_id": "in-1", "typing": 8, "read": True},
    }


async def test_show_typing_never_raises(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("no route to host")

    use_handler(monkeypatch, handler)

    assert await loop_client.show_typing("in-1", seconds=8) is False


async def test_show_typing_returns_false_when_refused(monkeypatch):
    use_handler(monkeypatch, lambda request: httpx.Response(400, json={"success": False}))

    assert await loop_client.show_typing("in-1", seconds=8) is False


async def test_show_typing_sends_nothing_when_turned_off(monkeypatch):
    monkeypatch.setattr(config, "TYPING_SECONDS", 0)

    # No handler is installed: the network guard would fail this test if a
    # request went out.
    assert await loop_client.show_typing("in-1") is False
