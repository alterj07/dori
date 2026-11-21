"""Shared fixtures: every test gets a throwaway database, log file and fake keys."""

import httpx
import pytest

from dori import config, loop_client, store


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Point the app at temp files and fake keys so tests never touch real state."""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "dori.db")
    monkeypatch.setattr(config, "LOG_PATH", tmp_path / "logs" / "dori.jsonl")
    monkeypatch.setattr(config, "NVIDIA_API_KEY", "test-nvidia-key")
    monkeypatch.setattr(config, "LOOP_API_KEY", "test-loop-key")
    monkeypatch.setattr(config, "WEBHOOK_SECRET", "test-webhook-secret")
    monkeypatch.setattr(loop_client, "_client", None)
    store.close()
    yield
    store.close()


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    """Fail loudly if a test would reach LoopMessage or any other real host."""

    async def refuse(self, request):
        raise AssertionError(f"test tried a real network call: {request.method} {request.url}")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse)
