"""Score the chat prompt against Nemotron on a fixed set of learner messages.

Each case names what a good tutor would do with the message: "fix" it, leave
it alone ("none"), or treat it as a "question". The script runs every case
through the real prompt and model, prints what came back and counts how many
matched.

Usage:
    .venv/bin/python scripts/eval_prompts.py [--model MODEL] [--only WORD]

Needs NVIDIA_API_KEY in .env. Nothing is sent to LoopMessage.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dori import bot, config, llm, prompts  # noqa: E402

LEVEL = (
    "I can understand Korean phonetically at a conversational level but often "
    "don't understand words that aren't used every day."
)


@dataclass(frozen=True)
class Case:
    style: str
    dori: str  # Dori's previous message
    text: str  # the learner's new message
    expect: str  # "fix", "none" or "question"
    mode: str = "chat"


CASES = [
    # Casual: real errors.
    Case("casual", "ㅋㅋ 주말에 뭐 했어?", "나 어제 친구 만났어요 그리고 우리 영화 봤다 진짜 재밌었어", "fix"),
    Case("casual", "어제 뭐 했어?", "어제 나는 집에서 영화를 봐", "fix"),
    Case("casual", "과일 뭐 좋아해?", "나 사과를 좋아", "fix"),
    Case("casual", "오늘 어땠어?", "오늘 날씨가 너무 덥어서 집에 있었어", "fix"),
    Case("casual", "어제 뭐 했어?", "나 어제 학교 가요 그리고 친구를 만나요 그리고 밥을 먹다", "fix"),
    # Casual: slang, shortenings and dropped particles that are fine.
    Case("casual", "ㅋㅋ 주말에 뭐 했어?", "ㅋㅋㅋ 그냥 집에서 쉬었어 넘 피곤했어", "none"),
    Case("casual", "밥 먹었어?", "ㅇㅇ 방금 먹었어 너는?", "none"),
    Case("casual", "나 오늘 시험 봤어", "헐 대박 진짜? 어땠어", "none"),
    Case("casual", "주말에 뭐 했어?", "나는 집에 있었어 너는 주말에 뭐 했어?", "none"),
    # Casual: correct, even though another phrasing is more common.
    Case("casual", "오늘 뭐 했어?", "나 오늘 시험 봤어 근데 망했어 ㅠㅠ", "none"),
    Case("casual", "오늘 뭐 해?", "나 오늘 친구 만나 같이 밥 먹을 거야", "none"),
    Case("casual", "뭐 먹었어?", "나는 김치찌개를 먹었어 진짜 맛있었어", "none"),
    # Casual: an English word where the Korean one was not known.
    Case("casual", "오늘 뭐 했어?", "나 오늘 gym 갔어", "fix"),
    # Casual: not Korean at all.
    Case("casual", "오늘 뭐 했어?", "I went to the gym today", "none"),
    Case("casual", "ㅋㅋ 그래서 무슨 영화 봤어?", "ok got it", "none", mode="explain"),
    # Questions about the language.
    Case("casual", "헐 대박!", "what does 대박 mean?", "question"),
    Case("casual", "헐 대박!", "대박이 무슨 뜻이야?", "question"),
    Case("casual", "배고파?", "how do I say I'm hungry?", "question"),
    Case("casual", "대박 means awesome.", "and 헐?", "question", mode="explain"),
    # Business: polite speech is expected.
    Case("business", "오늘 하루 어떠셨어요?", "오늘 회사에서 일 많이 했어", "fix"),
    Case("business", "오늘 하루 어떠셨어요?", "오늘은 회의가 많아서 조금 바빴어요", "none"),
    Case("business", "주말 잘 보내셨어요?", "네, 주말에 친구를 만났습니다 그리고 영화를 봤어", "fix"),
    Case("business", "처음 뵙겠습니다.", "안녕하새요, 만나서 반갑습니다", "fix"),
    # Romantic and friendly.
    Case("romantic", "자기야 오늘 뭐 했어?", "오늘 계속 너 생각했어 ㅎㅎ", "none"),
    Case("romantic", "보고 싶어 ㅠㅠ", "나도 너를 보고 싶어요 그리고 사랑한다", "fix"),
    Case("friendly", "오늘 기분 어때?", "오늘 기분 좋아! 날씨가 좋아서 산책했어", "none"),
]


def user_for(case: Case) -> dict[str, str]:
    return {
        "name": "Jay",
        "native": "English",
        "target": "Korean",
        "level_desc": LEVEL,
        "style": case.style,
        "mode": case.mode,
    }


def verdict(case: Case, data: dict) -> str:
    """What the bot would actually do with this model output."""
    if data.get("intent") == "question":
        return "question"
    correction = bot._text(data.get("correction"))
    return "fix" if bot.usable_correction(user_for(case), case.text, correction) else "none"


async def run_case(case: Case, gate: asyncio.Semaphore) -> bool:
    user = user_for(case)
    kind = "explanation" if case.mode == "explain" else "chat"
    history = [{"role": "bot", "kind": kind, "text": case.dori}]
    async with gate:
        completion = await llm.complete(
            prompts.chat_system(user),
            [{"role": "user", "content": prompts.chat_turn(user, history, case.text)}],
        )
    data = llm.parse_json(completion.text) or {}
    got = verdict(case, data)
    passed = got == case.expect

    fallback = " FALLBACK" if completion.fallback else ""
    print(f"[{'PASS' if passed else 'FAIL'}] {case.style:<9} want {case.expect:<8} got {got:<8} "
          f"{completion.latency_s:4.1f}s{fallback}")
    print(f"    Dori: {case.dori}")
    print(f"    Jay:  {case.text}")
    if not data:
        print(f"    (not JSON) {completion.text[:300]}")
    for label, key in (("✏️", "correction"), ("💬", "explanation"), ("↩ ", "reply")):
        value = bot._text(data.get(key))
        if value:
            print(f"    {label} {value}")
    return passed


async def main() -> None:
    parser = argparse.ArgumentParser(description="Score the chat prompt on fixed cases.")
    parser.add_argument("--model", help="model to test instead of the primary one")
    parser.add_argument("--only", help="run only the cases whose message contains this text")
    args = parser.parse_args()
    if args.model:
        config.PRIMARY_MODEL = args.model

    cases = [case for case in CASES if not args.only or args.only in case.text]
    gate = asyncio.Semaphore(3)
    results = await asyncio.gather(*(run_case(case, gate) for case in cases))
    await llm.aclose()
    print(f"\n{sum(results)}/{len(results)} matched on {config.PRIMARY_MODEL}")


if __name__ == "__main__":
    asyncio.run(main())
