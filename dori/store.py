"""SQLite persistence: user profiles, message history and webhook dedupe.

Everything lives in one file (``dori.db``). Calls are synchronous; each one
takes well under a millisecond, so they are fine to make from async code.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from dori import config

MODES = ("onboarding", "chat", "explain")
ROLES = ("user", "bot")
KINDS = ("chat", "correction", "explanation", "system")
PROFILE_FIELDS = ("name", "native", "target", "level_desc", "style")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    phone      TEXT PRIMARY KEY,
    mode       TEXT NOT NULL DEFAULT 'onboarding',  -- onboarding | chat | explain
    name       TEXT,
    native     TEXT,
    target     TEXT,
    level_desc TEXT,
    style      TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    phone   TEXT NOT NULL,
    role    TEXT NOT NULL,                 -- user | bot
    text    TEXT NOT NULL,
    kind    TEXT NOT NULL DEFAULT 'chat',  -- chat | correction | explanation | system
    loop_id TEXT,                          -- LoopMessage message_id
    ts      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_by_phone ON messages (phone, id);
CREATE INDEX IF NOT EXISTS messages_by_loop_id ON messages (loop_id);
CREATE TABLE IF NOT EXISTS seen_webhooks (
    webhook_id TEXT PRIMARY KEY,
    ts         TEXT NOT NULL
);
"""

_conn: sqlite3.Connection | None = None


def _db() -> sqlite3.Connection:
    """Open the database on first use and create the tables if needed."""
    global _conn
    if _conn is None:
        conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        _conn = conn
    return _conn


def close() -> None:
    """Close the connection. The next call reopens it."""
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _check_mode(mode: str) -> None:
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; expected one of {MODES}")


# --- Users ---


def get_user(phone: str) -> dict[str, Any] | None:
    """Return the user's row as a dict, or None if we have never seen them."""
    row = _db().execute("SELECT * FROM users WHERE phone = ?", (phone,)).fetchone()
    return dict(row) if row is not None else None


def save_user(phone: str, **fields: Any) -> dict[str, Any]:
    """Create the user if needed (in onboarding mode), then set the given columns.

    Accepts ``mode`` and the profile fields (name, native, target, level_desc,
    style). Returns the updated row.
    """
    unknown = set(fields) - {"mode", *PROFILE_FIELDS}
    if unknown:
        raise ValueError(f"unknown user fields: {sorted(unknown)}")
    if "mode" in fields:
        _check_mode(fields["mode"])
    with _db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (phone, mode, created_at) VALUES (?, 'onboarding', ?)",
            (phone, _now()),
        )
        if fields:
            # Column names were checked against the whitelist above.
            assignments = ", ".join(f"{column} = ?" for column in fields)
            conn.execute(
                f"UPDATE users SET {assignments} WHERE phone = ?",
                (*fields.values(), phone),
            )
    user = get_user(phone)
    assert user is not None
    return user


def set_mode(phone: str, mode: str) -> None:
    """Switch the user between onboarding, chat and explain mode."""
    _check_mode(mode)
    with _db() as conn:
        conn.execute("UPDATE users SET mode = ? WHERE phone = ?", (mode, phone))


def reset_user(phone: str) -> None:
    """Forget the user: delete their profile and their whole message history."""
    with _db() as conn:
        conn.execute("DELETE FROM messages WHERE phone = ?", (phone,))
        conn.execute("DELETE FROM users WHERE phone = ?", (phone,))


# --- Messages ---


def add_message(
    phone: str,
    role: str,
    text: str,
    kind: str = "chat",
    loop_id: str | None = None,
) -> int:
    """Store one message and return its row id.

    ``role`` is "user" or "bot". ``loop_id`` is the LoopMessage message_id,
    which is how a ❓ reaction is matched back to the message it landed on.
    """
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}; expected one of {ROLES}")
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}; expected one of {KINDS}")
    with _db() as conn:
        cursor = conn.execute(
            "INSERT INTO messages (phone, role, text, kind, loop_id, ts) VALUES (?, ?, ?, ?, ?, ?)",
            (phone, role, text, kind, loop_id, _now()),
        )
    return int(cursor.lastrowid)


def recent(phone: str, n: int = 20) -> list[dict[str, Any]]:
    """The user's last ``n`` messages, oldest first."""
    rows = _db().execute(
        "SELECT * FROM (SELECT * FROM messages WHERE phone = ? ORDER BY id DESC LIMIT ?) "
        "ORDER BY id ASC",
        (phone, n),
    ).fetchall()
    return [dict(row) for row in rows]


def find_by_loop_id(phone: str, loop_id: str) -> dict[str, Any] | None:
    """Look up one of this user's messages by its LoopMessage message_id."""
    row = _db().execute(
        "SELECT * FROM messages WHERE phone = ? AND loop_id = ? ORDER BY id DESC LIMIT 1",
        (phone, loop_id),
    ).fetchone()
    return dict(row) if row is not None else None


# --- Webhook dedupe ---


def claim_webhook(webhook_id: str) -> bool:
    """Record a webhook id. True the first time it is seen, False for a repeat."""
    with _db() as conn:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO seen_webhooks (webhook_id, ts) VALUES (?, ?)",
            (webhook_id, _now()),
        )
    return cursor.rowcount == 1
