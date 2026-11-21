"""Bot behaviour, with a scripted model and a captured outbox."""

import pytest

from dori import bot, config, llm, loop_client, prompts, store
from dori.events import Event

PHONE = "+15555550100"

PROFILE = {
    "name": "Jay",
    "native": "English",
    "target": "Korean",
    "level_desc": "I understand a lot but can't write well",
    "style": "casual",
}


def message(text, message_id="in-1"):
    return Event(kind="message", text=text, message_id=message_id)


def chat_reply(reply, correction=None, explanation=None):
    return {"intent": "chat", "correction": correction, "explanation": explanation, "reply": reply}


class FakeModel:
    """Stands in for llm.chat_json: hands out scripted replies and records prompts."""

    def __init__(self):
        self.replies = []
        self.calls = []

    async def chat_json(self, system, messages):
        self.calls.append({"system": system, "user": messages[-1]["content"]})
        if not self.replies:
            raise AssertionError("the bot called the model more often than the test expected")
        return self.replies.pop(0)


@pytest.fixture(autouse=True)
def no_bubble_gap(monkeypatch):
    monkeypatch.setattr(config, "BUBBLE_GAP_S", 0)


@pytest.fixture
def sent(monkeypatch):
    """Capture outgoing messages instead of calling LoopMessage."""
    outbox = []

    async def fake_send(phone, text, reply_to=None):
        outbox.append({"text": text, "reply_to": reply_to})
        return f"out-{len(outbox)}"

    monkeypatch.setattr(loop_client, "send", fake_send)
    return outbox


@pytest.fixture
def model(monkeypatch):
    fake = FakeModel()
    monkeypatch.setattr(llm, "chat_json", fake.chat_json)
    return fake


# --- Onboarding ---


async def test_the_first_text_from_a_new_number_gets_the_welcome(sent, model):
    await bot.handle_turn(PHONE, message("hi"))

    assert sent == [{"text": prompts.WELCOME, "reply_to": None}]
    assert store.get_user(PHONE)["mode"] == "onboarding"
    assert model.calls == []


async def test_a_complete_answer_finishes_onboarding(sent, model):
    store.save_user(PHONE)
    model.replies = [dict(PROFILE), chat_reply("Jay, 안녕! 오늘 뭐 했어?")]

    await bot.handle_turn(PHONE, message("I'm Jay, I speak English and want to learn Korean"))

    user = store.get_user(PHONE)
    assert {field: user[field] for field in PROFILE} == PROFILE
    assert user["mode"] == "chat"
    assert [m["text"] for m in sent] == [prompts.TIPS, "Jay, 안녕! 오늘 뭐 했어?"]


async def test_a_partial_answer_is_followed_up_for_the_missing_fields_only(sent, model):
    store.save_user(PHONE)
    model.replies = [
        {"name": "Jay", "native": "English", "target": "Korean", "level_desc": None, "style": None}
    ]

    await bot.handle_turn(PHONE, message("Jay, English, Korean"))

    assert store.get_user(PHONE)["mode"] == "onboarding"
    assert [m["text"] for m in sent] == [prompts.ask_missing(["level_desc", "style"])]


async def test_the_follow_up_answer_completes_the_profile(sent, model):
    store.save_user(PHONE, name="Jay", native="English", target="Korean")
    model.replies = [
        {"level_desc": "total beginner", "style": "Business"},
        chat_reply("안녕하세요, Jay 님!"),
    ]

    await bot.handle_turn(PHONE, message("total beginner, business please"))

    user = store.get_user(PHONE)
    assert user["level_desc"] == "total beginner"
    assert user["style"] == "business"
    assert user["mode"] == "chat"
    assert 'Already known: name = "Jay"' in model.calls[0]["system"]
    assert "Still needed: level_desc, style" in model.calls[0]["system"]


async def test_an_unknown_style_is_asked_for_again(sent, model):
    store.save_user(PHONE, name="Jay", native="English", target="Korean", level_desc="beginner")
    model.replies = [{"style": "pirate"}]

    await bot.handle_turn(PHONE, message("talk like a pirate"))

    assert store.get_user(PHONE)["style"] is None
    assert [m["text"] for m in sent] == [prompts.ask_missing(["style"])]


async def test_fields_that_are_already_known_are_not_overwritten(sent, model):
    store.save_user(PHONE, name="Jay", native="English", target="Korean", level_desc="beginner")
    model.replies = [{"name": "Casual", "style": "casual"}, chat_reply("Jay, 안녕!")]

    await bot.handle_turn(PHONE, message("casual"))

    user = store.get_user(PHONE)
    assert user["name"] == "Jay"
    assert user["style"] == "casual"


async def test_an_answer_the_model_cannot_read_asks_for_everything_again(sent, model):
    store.save_user(PHONE)
    model.replies = [chat_reply("I am not sure what you mean")]

    await bot.handle_turn(PHONE, message("asdf"))

    assert store.get_user(PHONE)["mode"] == "onboarding"
    assert [m["text"] for m in sent] == [prompts.ask_missing(list(store.PROFILE_FIELDS))]


async def test_onboarding_messages_are_stored_as_system_messages(sent, model):
    store.save_user(PHONE)
    model.replies = [dict(PROFILE), chat_reply("Jay, 안녕!")]

    await bot.handle_turn(PHONE, message("I'm Jay"))

    stored = [(m["role"], m["kind"]) for m in store.recent(PHONE)]
    assert stored == [("user", "system"), ("bot", "system"), ("bot", "chat")]


async def test_onboarding_fails_loudly_when_there_is_no_opening_message(sent, model):
    store.save_user(PHONE)
    model.replies = [dict(PROFILE), chat_reply("")]

    with pytest.raises(bot.BotError):
        await bot.handle_turn(PHONE, message("I'm Jay"))
