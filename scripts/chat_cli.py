"""Chat with Dori in the terminal: the real bot and real Nemotron calls, no iMessage.

Usage:
    .venv/bin/python scripts/chat_cli.py [--db PATH] [--verbose]

Type a message and press Enter. Dori's messages are numbered, and:
    ?3       reacts ❓ to Dori's message number 3
    /quit    leaves

Nothing goes to LoopMessage. The chat uses a throwaway database unless you
pass --db, and --verbose prints the raw model output after every turn.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
import textwrap
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dori import bot, config, llm, loop_client, store, turn_log  # noqa: E402
from dori.events import Event  # noqa: E402

PHONE = "+15555550100"  # fictional; nothing is ever sent to it


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Chat with Dori in the terminal.")
    parser.add_argument("--db", help="keep the conversation in this SQLite file")
    parser.add_argument("--verbose", action="store_true", help="print raw model output")
    return parser.parse_args()


def to_event(line: str, number: int) -> Event:
    """Turn a typed line into the event the server would have built."""
    target = line[1:].strip()
    if line.startswith("?") and target.isdigit():
        return Event(kind="reaction", message_id=f"cli-{target}", reaction="question")
    return Event(kind="message", text=line, message_id=f"user-{number}")


def show_model_calls(record: dict) -> None:
    calls = [*record.get("earlier_llm_calls", []), record]
    for call in calls:
        if not call["model_used"]:
            continue
        fallback = f", fallback: {call['fallback_reason']}" if call["fallback"] else ""
        print(f"    [{call['model_used']}, {call['latency_s']}s{fallback}]")
        print(textwrap.indent(call["raw_model_output"].strip(), "    | "))


async def chat(verbose: bool) -> None:
    outbox: list[str] = []

    async def print_instead_of_sending(phone: str, text: str, reply_to: str | None = None) -> str:
        outbox.append(text)
        turn_log.note_outgoing(text)
        how = f" (inline reply to {reply_to})" if reply_to else ""
        print(f"Dori [{len(outbox)}]{how}:")
        print(textwrap.indent(text, "    "))
        return f"cli-{len(outbox)}"

    loop_client.send = print_instead_of_sending
    config.BUBBLE_GAP_S = 0

    number = 0
    while True:
        try:
            line = input("You: ").strip()
        except EOFError:
            break
        if not sys.stdin.isatty():
            print(line)  # echo piped input so the transcript reads properly
        if line == "/quit":
            break
        if not line:
            continue

        number += 1
        event = to_event(line, number)
        with turn_log.turn(PHONE, f"turn:{event.kind}", event.text) as record:
            try:
                await bot.handle_turn(PHONE, event)
            except Exception:
                record["error"] = traceback.format_exc()
                print(textwrap.indent(record["error"], "    ! "))
        if verbose:
            show_model_calls(record)

    await llm.aclose()


def main() -> None:
    args = parse_args()
    with tempfile.TemporaryDirectory() as scratch:
        config.DB_PATH = Path(args.db) if args.db else Path(scratch) / "dori.db"
        config.LOG_PATH = Path(scratch) / "dori.jsonl"
        try:
            asyncio.run(chat(args.verbose))
        finally:
            store.close()


if __name__ == "__main__":
    main()
