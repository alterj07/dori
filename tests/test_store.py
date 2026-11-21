"""SQLite store: users, messages and webhook dedupe."""

import pytest

from dori import store

PHONE = "+15555550100"
OTHER = "+15555550101"


def test_unknown_user_is_none():
    assert store.get_user(PHONE) is None


def test_save_user_creates_the_user_in_onboarding_mode():
    user = store.save_user(PHONE)

    assert user["phone"] == PHONE
    assert user["mode"] == "onboarding"
    assert user["name"] is None
    assert user["created_at"]


def test_save_user_updates_only_the_given_fields():
    store.save_user(PHONE, name="Jay", style="casual")

    user = store.save_user(PHONE, level_desc="I know a few words", mode="chat")

    assert user["name"] == "Jay"
    assert user["style"] == "casual"
    assert user["level_desc"] == "I know a few words"
    assert user["mode"] == "chat"


def test_save_user_rejects_unknown_fields_and_modes():
    with pytest.raises(ValueError):
        store.save_user(PHONE, nickname="J")
    with pytest.raises(ValueError):
        store.save_user(PHONE, mode="sleeping")


def test_set_mode_changes_the_mode():
    store.save_user(PHONE)

    store.set_mode(PHONE, "explain")

    assert store.get_user(PHONE)["mode"] == "explain"
    with pytest.raises(ValueError):
        store.set_mode(PHONE, "sleeping")


def test_add_message_stores_role_kind_and_loop_id():
    store.add_message(PHONE, "bot", "✏️ 만났어", kind="correction", loop_id="out-1")

    (message,) = store.recent(PHONE)
    assert message["role"] == "bot"
    assert message["text"] == "✏️ 만났어"
    assert message["kind"] == "correction"
    assert message["loop_id"] == "out-1"
    assert message["ts"]


def test_add_message_rejects_unknown_roles_and_kinds():
    with pytest.raises(ValueError):
        store.add_message(PHONE, "robot", "hi")
    with pytest.raises(ValueError):
        store.add_message(PHONE, "bot", "hi", kind="joke")


def test_recent_returns_the_last_n_oldest_first():
    for number in range(1, 6):
        store.add_message(PHONE, "user", f"msg {number}")

    assert [m["text"] for m in store.recent(PHONE, 3)] == ["msg 3", "msg 4", "msg 5"]


def test_recent_only_returns_that_users_messages():
    store.add_message(PHONE, "user", "mine")
    store.add_message(OTHER, "user", "theirs")

    assert [m["text"] for m in store.recent(PHONE)] == ["mine"]


def test_find_by_loop_id_is_scoped_to_the_user():
    store.add_message(PHONE, "bot", "안녕!", loop_id="out-1")

    assert store.find_by_loop_id(PHONE, "out-1")["text"] == "안녕!"
    assert store.find_by_loop_id(OTHER, "out-1") is None
    assert store.find_by_loop_id(PHONE, "missing") is None


def test_reset_user_wipes_profile_and_history_for_that_user_only():
    store.save_user(PHONE, name="Jay")
    store.add_message(PHONE, "user", "hi")
    store.save_user(OTHER, name="Min")
    store.add_message(OTHER, "user", "hello")

    store.reset_user(PHONE)

    assert store.get_user(PHONE) is None
    assert store.recent(PHONE) == []
    assert store.get_user(OTHER)["name"] == "Min"
    assert [m["text"] for m in store.recent(OTHER)] == ["hello"]


def test_claim_webhook_is_true_once_then_false():
    assert store.claim_webhook("wh-1") is True
    assert store.claim_webhook("wh-1") is False
    assert store.claim_webhook("wh-2") is True


def test_seen_webhooks_survive_a_restart():
    assert store.claim_webhook("wh-1") is True

    store.close()

    assert store.claim_webhook("wh-1") is False
