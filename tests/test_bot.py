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


async def test_a_message_with_no_korean_in_it_is_never_corrected(sent, model, chatting):
    model.replies = [
        chat_reply("오 운동했구나!", correction="오늘 헬스장 갔어", explanation="In Korean.")
    ]

    await bot.handle_turn(PHONE, message("I went to the gym today", "in-5"))

    assert [m["text"] for m in sent] == ["오 운동했구나!"]


async def test_an_english_word_inside_korean_can_still_be_corrected(sent, model, chatting):
    model.replies = [
        chat_reply("오 헬스장 갔구나", correction="나 오늘 헬스장 갔어", explanation="gym is 헬스장.")
    ]

    await bot.handle_turn(PHONE, message("나 오늘 gym 갔어", "in-5"))

    assert sent[0] == {"text": "✏️ 나 오늘 헬스장 갔어\n💬 gym is 헬스장.", "reply_to": "in-5"}


async def test_each_line_of_a_reply_is_its_own_text(sent, model, chatting):
    model.replies = [chat_reply("오, 무슨 영화 봤어?\n나도 영화 볼까 고민 중이거든")]

    await bot.handle_turn(PHONE, message("영화 봤어", "in-5"))

    assert [m["text"] for m in sent] == ["오, 무슨 영화 봤어?", "나도 영화 볼까 고민 중이거든"]
    assert store.find_by_loop_id(PHONE, "out-2")["text"] == "나도 영화 볼까 고민 중이거든"


def test_a_long_reply_is_capped_at_three_texts():
    assert bot.split_bubbles("하나\n\n둘\n셋\n넷\n다섯") == ["하나", "둘", "셋 넷 다섯"]


async def test_long_dashes_from_the_model_become_commas(sent, model, chatting):
    model.replies = [
        {"intent": "question", "explanation": "헐 is slang for surprise — like whoa.", "reply": ""}
    ]

    await bot.handle_turn(PHONE, message("what does 헐 mean?", "in-5"))

    assert [m["text"] for m in sent] == ["헐 is slang for surprise, like whoa."]


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


# --- ❓ reactions ---


def question_reaction(message_id, text=""):
    return Event(kind="reaction", text=text, message_id=message_id, reaction="question")


async def test_a_question_reaction_explains_that_message_inline(sent, model, chatting):
    model.replies = [{"explanation": "It means: haha, what did you do this weekend?"}]

    await bot.handle_turn(PHONE, question_reaction("out-0"))

    assert sent == [
        {"text": "It means: haha, what did you do this weekend?", "reply_to": "out-0"}
    ]
    assert store.recent(PHONE)[-1]["kind"] == "explanation"
    assert store.get_user(PHONE)["mode"] == "explain"
    assert model.calls[0]["user"].endswith("<<<\nㅋㅋ 주말에 뭐 했어?\n>>>")


async def test_a_reaction_with_an_unknown_id_is_matched_by_its_text(sent, model, chatting):
    store.add_message(PHONE, "bot", "오 대박!", loop_id="out-1")
    store.add_message(PHONE, "bot", "나도 가고 싶다", loop_id="out-2")
    model.replies = [{"explanation": "대박 means awesome."}]

    await bot.handle_turn(PHONE, question_reaction("some-other-id", text="오 대박!"))

    assert sent == [{"text": "대박 means awesome.", "reply_to": "out-1"}]


async def test_a_reaction_that_matches_nothing_explains_doris_latest_message(sent, model, chatting):
    store.add_message(PHONE, "user", "영화 봤어", loop_id="in-1")
    store.add_message(PHONE, "bot", "오 무슨 영화?", loop_id="out-1")
    model.replies = [{"explanation": "It asks which movie you saw."}]

    await bot.handle_turn(PHONE, question_reaction("some-other-id"))

    assert sent == [{"text": "It asks which movie you saw.", "reply_to": "out-1"}]


async def test_a_reaction_on_the_users_own_message_is_ignored(sent, model, chatting):
    store.add_message(PHONE, "user", "영화 봤어", loop_id="in-1")

    await bot.handle_turn(PHONE, question_reaction("in-1"))

    assert sent == []
    assert model.calls == []


async def test_a_reaction_before_onboarding_is_finished_is_ignored(sent, model):
    await bot.handle_turn(PHONE, question_reaction("out-1"))
    store.save_user(PHONE)
    await bot.handle_turn(PHONE, question_reaction("out-1"))

    assert sent == []
    assert model.calls == []


async def test_an_empty_explanation_raises(sent, model, chatting):
    model.replies = [{"explanation": ""}]

    with pytest.raises(bot.BotError):
        await bot.handle_turn(PHONE, question_reaction("out-0"))


# --- Explain mode ---

QUESTION = {
    "intent": "question",
    "correction": None,
    "explanation": "대박 means awesome.",
    "reply": "",
}


async def test_a_question_puts_the_user_in_explain_mode(sent, model, chatting):
    model.replies = [dict(QUESTION)]

    await bot.handle_turn(PHONE, message("what does 대박 mean?", "in-5"))

    assert store.get_user(PHONE)["mode"] == "explain"


async def test_follow_up_questions_stay_in_explain_mode(sent, model, chatting):
    model.replies = [dict(QUESTION), {**QUESTION, "explanation": "헐 means whoa."}]

    await bot.handle_turn(PHONE, message("what does 대박 mean?", "in-5"))
    await bot.handle_turn(PHONE, message("and 헐?", "in-6"))

    assert [m["text"] for m in sent] == ["대박 means awesome.", "헐 means whoa."]
    assert store.get_user(PHONE)["mode"] == "explain"
    assert "RIGHT NOW" not in model.calls[0]["system"]
    assert "RIGHT NOW" in model.calls[1]["system"]


async def test_a_normal_message_goes_back_in_character(sent, model, chatting):
    store.set_mode(PHONE, "explain")
    model.replies = [chat_reply("ㅋㅋ 그래서 무슨 영화 봤어?")]

    await bot.handle_turn(PHONE, message("ok got it", "in-5"))

    assert [m["text"] for m in sent] == ["ㅋㅋ 그래서 무슨 영화 봤어?"]
    assert store.get_user(PHONE)["mode"] == "chat"


# --- Commands ---


@pytest.mark.parametrize("command", ["/reset", "/Reset", " /RESET "])
async def test_reset_wipes_the_user_and_confirms(sent, model, chatting, command):
    await bot.handle_turn(PHONE, message(command))

    assert store.get_user(PHONE) is None
    assert store.recent(PHONE) == []
    assert sent == [{"text": prompts.RESET_DONE, "reply_to": None}]
    assert model.calls == []


async def test_the_next_text_after_a_reset_gets_the_welcome(sent, model, chatting):
    await bot.handle_turn(PHONE, message("/reset"))
    await bot.handle_turn(PHONE, message("hi"))

    assert [m["text"] for m in sent] == [prompts.RESET_DONE, prompts.WELCOME]
    assert store.get_user(PHONE)["mode"] == "onboarding"


async def test_reset_works_in_the_middle_of_onboarding(sent, model):
    store.save_user(PHONE, name="Jay")

    await bot.handle_turn(PHONE, message("/reset"))

    assert store.get_user(PHONE) is None
    assert model.calls == []
    assert [m["text"] for m in sent] == [prompts.RESET_DONE]


@pytest.mark.parametrize("command", ["help", "Help", "HELP!"])
async def test_help_resends_the_tips_without_calling_the_model(sent, model, chatting, command):
    await bot.handle_turn(PHONE, message(command))

    assert sent == [{"text": prompts.TIPS, "reply_to": None}]
    assert model.calls == []
    assert store.get_user(PHONE)["mode"] == "chat"


async def test_help_keeps_the_user_in_explain_mode(sent, model, chatting):
    store.set_mode(PHONE, "explain")

    await bot.handle_turn(PHONE, message("help"))

    assert store.get_user(PHONE)["mode"] == "explain"


async def test_help_during_onboarding_is_read_as_an_answer(sent, model):
    store.save_user(PHONE)
    model.replies = [{}]

    await bot.handle_turn(PHONE, message("help"))

    assert len(model.calls) == 1
    assert [m["text"] for m in sent] == [prompts.ask_missing(list(store.PROFILE_FIELDS))]
