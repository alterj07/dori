"""Settings for Dori, read from the environment and the project's .env file.

Other modules read these as ``config.NAME`` at call time (never
``from dori.config import NAME``), so a test can patch one value and every
module sees the change.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

# python-dotenv accepts spaces around "=" and trailing "# comments". Variables
# already set in the real environment win over the file.
load_dotenv(ROOT / ".env")


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


# --- Secrets and deployment values (.env) ---
NVIDIA_API_KEY = _env("NVIDIA_API_KEY")
LOOP_API_KEY = _env("LOOP_API_KEY")
WEBHOOK_SECRET = _env("WEBHOOK_SECRET")
NGROK_DOMAIN = _env("NGROK_DOMAIN")

REQUIRED = ("NVIDIA_API_KEY", "LOOP_API_KEY", "WEBHOOK_SECRET")

# --- LoopMessage ---
LOOP_SEND_URL = "https://a.loopmessage.com/api/v1/message/send/"
LOOP_TYPING_URL = "https://a.loopmessage.com/api/v1/message/show-typing/"
LOOP_TIMEOUT_S = 15.0
TYPING_SECONDS = 12  # typing bubble shown while a reply is prepared; 0 turns it off

# --- NVIDIA Nemotron ---
NVIDIA_CHAT_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
PRIMARY_MODEL = "nvidia/nemotron-3-ultra-550b-a55b"  # the only Nemotron that lists Korean
FALLBACK_MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"  # fast, but misses corrections
LLM_TIMEOUT_S = 10.0  # per attempt; past this the primary gives way to the fallback
LLM_TEMPERATURE = 0.6
LLM_MAX_TOKENS = 500

# --- Bot behaviour ---
DEBOUNCE_S = 3.0  # quiet time before a burst of texts becomes one turn

# --- Local files ---
DB_PATH = ROOT / "dori.db"
LOG_PATH = ROOT / "logs" / "dori.jsonl"


def missing() -> list[str]:
    """Names of required settings that are empty."""
    return [name for name in REQUIRED if not globals()[name]]
