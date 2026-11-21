"""Settings: required values and the .env format we rely on."""

from dotenv import dotenv_values

from dori import config


def test_env_file_may_have_spaces_around_equals_and_trailing_comments(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "LOOP_API_KEY = abc123\n"
        "WEBHOOK_SECRET=s3cret\n"
        "NGROK_DOMAIN = demo.ngrok-free.app  # fixed domain\n",
        encoding="utf-8",
    )

    assert dotenv_values(env) == {
        "LOOP_API_KEY": "abc123",
        "WEBHOOK_SECRET": "s3cret",
        "NGROK_DOMAIN": "demo.ngrok-free.app",
    }


def test_missing_is_empty_when_everything_is_set():
    assert config.missing() == []


def test_missing_names_the_empty_required_settings(monkeypatch):
    monkeypatch.setattr(config, "LOOP_API_KEY", "")

    assert config.missing() == ["LOOP_API_KEY"]
