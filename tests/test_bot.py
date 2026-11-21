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


# --- Chat ---


@pytest.fixture
def chatting():
    """A user who has finished onboarding and has Dori's first message."""
    store.save_user(PHONE, mode="chat", **PROFILE)
    store.add_message(PHONE, "bot", "ㅋㅋ 주말에 뭐 했어?", loop_id="out-0")


async def test_a_clean_message_gets_only_an_in_character_reply(sent, model, chatting):
    model.replies = [chat_reply("오 좋았겠다!")]

    await bot.handle_turn(PHONE, message("친구 만났어", "in-5"))

    assert sent == [{"text": "오 좋았겠다!", "reply_to": None}]


async def test_a_mistake_gets_an_inline_fix_and_then_the_reply(sent, model, chatting):
    model.replies = [
        chat_reply(
            "오, 무슨 영화 봤어?",
            correction="나 어제 친구 만났어. 그리고 영화 봤는데 진짜 재밌었어.",
            explanation="Keep one speech level: 만났어 and 봤어, not 만났어요 with 봤다.",
        )
    ]

    await bot.handle_turn(PHONE, message("나 어제 친구 만났어요 그리고 영화 봤다 진짜 재밌었어", "in-5"))

    assert sent == [
        {
            "text": "✏️ 나 어제 친구 만났어. 그리고 영화 봤는데 진짜 재밌었어.\n"
            "💬 Keep one speech level: 만났어 and 봤어, not 만났어요 with 봤다.",
            "reply_to": "in-5",
        },
        {"text": "오, 무슨 영화 봤어?", "reply_to": None},
    ]
    assert [m["kind"] for m in store.recent(PHONE)][-2:] == ["correction", "chat"]


async def test_a_fix_that_only_changes_spacing_or_punctuation_is_dropped(sent, model, chatting):
    model.replies = [chat_reply("오 좋았겠다!", correction="친구 만났어.", explanation="Add a period.")]

    await bot.handle_turn(PHONE, message("친구만났어", "in-5"))

    assert [m["text"] for m in sent] == ["오 좋았겠다!"]


async def test_each_line_of_a_reply_is_its_own_text(sent, model, chatting):
    model.replies = [chat_reply("오, 무슨 영화 봤어?\n나도 영화 볼까 고민 중이거든")]

    await bot.handle_turn(PHONE, message("영화 봤어", "in-5"))

    assert [m["text"] for m in sent] == ["오, 무슨 영화 봤어?", "나도 영화 볼까 고민 중이거든"]
    assert store.find_by_loop_id(PHONE, "out-2")["text"] == "나도 영화 볼까 고민 중이거든"


def test_a_long_reply_is_capped_at_three_texts():
    assert bot.split_bubbles("하나\n\n둘\n셋\n넷\n다섯") == ["하나", "둘", "셋 넷 다섯"]


async def test_a_language_question_gets_only_the_answer(sent, model, chatting):
    model.replies = [
        {
            "intent": "question",
            "correction": None,
            "explanation": "대박 means awesome or no way. It is casual slang.",
            "reply": "",
        }
    ]

    await bot.handle_turn(PHONE, message("what does 대박 mean?", "in-5"))

    assert sent == [{"text": "대박 means awesome or no way. It is casual slang.", "reply_to": None}]
    assert store.recent(PHONE)[-1]["kind"] == "explanation"


async def test_the_model_sees_the_profile_the_history_and_the_new_message(sent, model, chatting):
    store.add_message(PHONE, "bot", prompts.TIPS, kind="system")
    model.replies = [chat_reply("오 좋았겠다!")]

    await bot.handle_turn(PHONE, message("친구 만났어", "in-5"))

    call = model.calls[0]
    assert PROFILE["level_desc"] in call["system"]
    assert "Dori: ㅋㅋ 주말에 뭐 했어?" in call["user"]
    assert "Quick tips" not in call["user"]
    assert call["user"].endswith("<<<\n친구 만났어\n>>>")


async def test_only_the_last_twenty_messages_are_sent_as_context(sent, model, chatting):
    for number in range(1, 31):
        store.add_message(PHONE, "user", f"msg {number}")
    model.replies = [chat_reply("응!")]

    await bot.handle_turn(PHONE, message("안녕", "in-5"))

    lines = model.calls[0]["user"].splitlines()
    assert "Jay: msg 11" in lines
    assert "Jay: msg 10" not in lines


async def test_messages_are_stored_with_their_loopmessage_ids(sent, model, chatting):
    model.replies = [chat_reply("오 좋았겠다!")]

    await bot.handle_turn(PHONE, message("친구 만났어", "in-5"))

    assert store.find_by_loop_id(PHONE, "in-5")["role"] == "user"
    assert store.find_by_loop_id(PHONE, "out-1")["text"] == "오 좋았겠다!"


async def test_a_refused_inline_reply_goes_out_as_a_normal_message(model, chatting, monkeypatch):
    attempts = []

    async def picky_send(phone, text, reply_to=None):
        attempts.append(reply_to)
        if reply_to:
            raise loop_client.LoopError("reply_to_id refused")
        return f"out-{len(attempts)}"

    monkeypatch.setattr(loop_client, "send", picky_send)
    model.replies = [chat_reply("오!", correction="친구 만났어", explanation="Use 반말.")]

    await bot.handle_turn(PHONE, message("친구 만났다요", "in-5"))

    assert attempts == ["in-5", None, None]
    assert store.recent(PHONE)[-2]["kind"] == "correction"


async def test_an_empty_answer_from_the_model_raises(sent, model, chatting):
    model.replies = [chat_reply("")]

    with pytest.raises(bot.BotError):
        await bot.handle_turn(PHONE, message("친구 만났어", "in-5"))

    assert sent == []
