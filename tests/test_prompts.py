"""Prompt builders: the right profile, rules and transcript end up in the prompt."""

import re

import pytest

from dori import prompts

USER = {
    "name": "Jay",
    "native": "English",
    "target": "Korean",
    "level_desc": "I understand a lot but can't write well",
    "style": "casual",
}


def placeholders(text):
    """Any {placeholder} that was left unfilled."""
    return re.findall(r"\{[a-z_]+\}", text)


def test_tips_have_the_five_plan_bullets():
    assert prompts.TIPS.startswith("Quick tips:")
    assert prompts.TIPS.count("•") == 5
    assert 'Text "help" anytime' in prompts.TIPS


def test_ask_missing_lists_only_the_missing_fields():
    text = prompts.ask_missing(["level_desc", "style"])

    assert "your level" in text
    assert "the style you want" in text
    assert "your name" not in text


def test_onboarding_prompt_says_what_is_known_and_what_is_needed():
    system = prompts.onboarding_system({"name": "Jay", "native": "English"}, ["style"])

    assert 'Already known: name = "Jay", native = "English"' in system
    assert "Still needed: style" in system
    assert placeholders(system) == []


def test_onboarding_prompt_for_an_empty_profile():
    system = prompts.onboarding_system({}, ["name", "style"])

    assert "Already known: nothing yet" in system
    assert "Still needed: name, style" in system


def test_chat_prompt_uses_the_profile_word_for_word():
    system = prompts.chat_system(USER)

    assert "helps Jay practice Korean" in system
    assert "native language is English" in system
    assert '"I understand a lot but can\'t write well"' in system
    assert "Style they chose: casual" in system
    assert placeholders(system) == []


@pytest.mark.parametrize("style", ["casual", "friendly", "romantic"])
def test_informal_styles_accept_banmal(style):
    system = prompts.chat_system({**USER, "style": style})

    assert "This chat is in 반말" in system
    assert "Use 반말 in every message" in system


def test_business_style_treats_banmal_as_an_error():
    system = prompts.chat_system({**USER, "style": "business"})

    assert "반말 from them is an error here" in system
    assert "합니다체 or 해요체" in system


@pytest.mark.parametrize("style", prompts.STYLES)
def test_every_style_flags_mixed_speech_levels(style):
    system = prompts.chat_system({**USER, "style": style})

    assert "Mixing speech levels inside one message is an error" in system


def test_chat_prompt_caps_corrections_at_two_and_spares_slang():
    system = prompts.chat_system(USER)

    assert "Fix at most 2 errors" in system
    assert "slang that fit this style are not errors" in system


def test_other_target_languages_skip_the_korean_speech_rules():
    system = prompts.chat_system({**USER, "target": "Spanish"})

    assert "practice Spanish" in system
    assert "This chat is in" not in system
    assert "Mixing speech levels" not in system


def test_chat_turn_shows_the_transcript_and_then_the_new_message():
    history = [
        {"role": "bot", "kind": "chat", "text": "ㅋㅋ 주말에 뭐 했어?"},
        {"role": "user", "kind": "chat", "text": "친구 만났어요\n영화 봤다"},
        {"role": "bot", "kind": "correction", "text": "✏️ 친구 만났어\n💬 Keep one speech level."},
        {"role": "bot", "kind": "explanation", "text": "대박 means awesome."},
    ]

    turn = prompts.chat_turn(USER, history, "재밌었어")

    assert turn.splitlines()[:5] == [
        "Conversation so far:",
        "Dori: ㅋㅋ 주말에 뭐 했어?",
        "Jay: 친구 만났어요 / 영화 봤다",
        "Dori (correction, out of character): ✏️ 친구 만났어 / 💬 Keep one speech level.",
        "Dori (explanation, out of character): 대박 means awesome.",
    ]
    assert turn.endswith("Jay's new message:\n<<<\n재밌었어\n>>>")


def test_chat_turn_without_history():
    turn = prompts.chat_turn(USER, [], "안녕")

    assert "Conversation so far:\n(nothing yet)" in turn


def test_opening_turn_asks_for_a_greeting_by_name():
    turn = prompts.opening_turn(USER)

    assert "greet Jay by name" in turn


def test_explain_prompt_asks_for_an_explanation_in_the_native_language():
    system = prompts.explain_system(USER)

    assert "explain that message in English" in system
    assert '{"explanation": string}' in system
    assert placeholders(system) == []


def test_explain_turn_ends_with_the_message_to_explain():
    history = [{"role": "bot", "kind": "chat", "text": "ㅋㅋ 주말에 뭐 했어?"}]

    turn = prompts.explain_turn(USER, history, "ㅋㅋ 주말에 뭐 했어?")

    assert "Dori: ㅋㅋ 주말에 뭐 했어?" in turn
    assert turn.endswith("The message Jay reacted to:\n<<<\nㅋㅋ 주말에 뭐 했어?\n>>>")
