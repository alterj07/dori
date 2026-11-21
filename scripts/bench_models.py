"""Time Nemotron models on one realistic correction turn and print what they say.

The test message mixes two Korean speech levels (만났어요 / 봤다), which a good
tutor should catch.

Usage: python3 scripts/bench_models.py .env
Needs NVIDIA_API_KEY in that file. Standard library only. Never prints the key.
"""

import json
import sys
import time
import urllib.error
import urllib.request

MODELS = [
    "nvidia/nemotron-3-ultra-550b-a55b",
    "nvidia/nemotron-3.5-lightning-30b-a3b",
]
RUNS = 2
URL = "https://integrate.api.nvidia.com/v1/chat/completions"

SYSTEM = """You are a Korean texting buddy for an English speaker learning Korean.
Style: casual (use 반말). Learner level: understands spoken Korean at a conversational level, weak on less common words.
Casual shortenings and slang that fit the style are NOT mistakes. Only flag real grammar/spelling/meaning errors, max 2.
Respond ONLY with JSON: {"intent": "chat"|"question", "correction": string|null, "explanation": string|null (in English, 1-2 lines), "reply": string (in Korean, in character, 1-2 short texts)}"""

HISTORY = [
    {"role": "assistant", "content": "ㅋㅋ 주말에 뭐 했어?"},
    {"role": "user", "content": "나 어제 친구 만났어요 그리고 우리 영화 봤다 진짜 재밌었어"},
]


def load_key(env_path):
    for line in open(env_path, encoding="utf-8"):
        name, sep, value = line.strip().partition("=")
        if sep and name.strip() == "NVIDIA_API_KEY":
            return value.strip().strip('"').strip("'")
    sys.exit(f"NVIDIA_API_KEY not found in {env_path}")


def call(key, model):
    body = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM}, *HISTORY],
        "temperature": 0.6,
        "max_tokens": 400,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    request = urllib.request.Request(
        URL,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        return time.perf_counter() - started, f"HTTP {exc.code}: {exc.read()[:300]!r}"
    except Exception as exc:  # timeout or network trouble
        return time.perf_counter() - started, f"ERROR: {exc}"
    return time.perf_counter() - started, data["choices"][0]["message"]["content"]


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    key = load_key(sys.argv[1])
    for model in MODELS:
        print(f"\n===== {model} =====")
        for run in range(RUNS):
            seconds, output = call(key, model)
            print(f"--- run {run + 1}: {seconds:.1f}s ---\n{output}")


if __name__ == "__main__":
    main()
