# Dori (돌이)

Dori is a Korean practice buddy that lives in iMessage. You text it the way you would text a friend, and it texts back in Korean. It was built at tidalBYTE '26 for older learners and immigrants who find apps like Duolingo overwhelming but already know how to send a text.

Status: onboarding, chat with inline corrections and answers to language questions all work. Explanations for a ❓ reaction, explain mode and the `help` and `/reset` commands come next.

## What it does

You text first. Dori answers with a welcome that asks for your name, your native language, the language you want to practice, your level in your own words, and a style: casual, friendly, romantic or business. You can answer in one free-form message. The model pulls the fields out of it, and the code decides what is still missing and asks only for that.

After that Dori chats in Korean, in character for the style you picked. Casual, friendly and romantic use 반말. Business uses 합니다체 or 해요체.

When your message has a real mistake, Dori replies inline to that message with the corrected sentence and a short explanation in English, then carries on the conversation in a separate text:

```
You:   나 어제 친구 만났어요 그리고 우리 영화 봤다 진짜 재밌었어
Dori:  ✏️ 나 어제 친구 만났어 그리고 우리 영화 봤어 진짜 재밌었어
       💬 Mixed speech levels: 만났어요 (polite) + 봤다/재밌었어 (casual).
       Changed 만났어요 → 만났어 and 봤다 → 봤어 for consistent 반말.
Dori:  오 누구랑? ㅋㅋ 무슨 영화 봤어?
```

Dori fixes at most two errors per message. Slang and shortenings that fit the style (ㅋㅋ, 대박, dropped particles in casual speech) are left alone. Mixing speech levels in one message counts as a mistake in every style, and 반말 counts as one only in business style.

If you ask about the language, for example "what does 대박 mean?", Dori answers in English and leaves the story where it was.

## How it works

```
iPhone -> iMessage -> LoopMessage -> POST /webhook -> ngrok -> FastAPI on your laptop
                                                                  |
                                          bot -> Nemotron -> LoopMessage -> iPhone
```

### Webhooks

LoopMessage delivers each text and tapback as a webhook, and it retries up to 30 times unless it gets a 200 within 15 seconds. So the server answers first and does the work afterwards:

1. It compares the `Authorization` header with `WEBHOOK_SECRET` and returns 401 on a mismatch.
2. It drops any `webhook_id` it has already stored in SQLite.
3. It returns 200.
4. It adds the text to that sender's buffer and waits for 3 quiet seconds, so three short texts in a row become one turn.
5. It runs the turn. Turns for the same person run one at a time, and different people don't wait for each other.

If a turn crashes, the user gets "Sorry, can you say that again? 🙏" and the traceback goes to the log.

### The model

Each chat turn is a single call to NVIDIA Nemotron 3 Ultra. The prompt carries the user's profile and the last 20 messages, and the model returns one JSON object with four fields: `intent` (chat or question), `correction`, `explanation` and `reply`.

Ultra is the only Nemotron model that lists Korean as supported. In a side-by-side run of `scripts/bench_models.py`, the faster Nemotron 3.5 Lightning returned valid JSON but missed the speech-level mistake both times.

Lightning is still the fallback. If Ultra errors, returns a 429 or takes longer than 10 seconds, the call is retried once on Lightning and the log records why. The JSON is parsed leniently: code fences, stray text around the object and a cut-off object are all handled. If the model answers in plain prose, that text goes out as an ordinary reply.

## Requirements

- Python 3.14
- An NVIDIA API key from build.nvidia.com
- A LoopMessage account with the sandbox turned on, and your iPhone added as a sandbox contact
- An ngrok account with a fixed domain, and the `ngrok` CLI logged in
- `make` (optional, since the commands it runs are short)

## Setup

```bash
make install
cp .env.example .env
```

`make install` creates `.venv` and installs the pinned packages. Then fill in `.env`:

| Variable | Where it comes from |
|---|---|
| `NVIDIA_API_KEY` | build.nvidia.com (it starts with `nvapi-`) |
| `LOOP_API_KEY` | LoopMessage dashboard, under API and then Settings |
| `WEBHOOK_SECRET` | Any long random string you make up |
| `NGROK_DOMAIN` | Your fixed ngrok domain, without `https://` |

## Usage

### On your phone

Run the server and the tunnel in two terminals:

```bash
make run      # uvicorn on http://localhost:8000
make tunnel   # ngrok from your fixed domain to port 8000
```

Check that the tunnel reaches the server:

```bash
curl https://<your-domain>/health    # {"ok":true}
```

On the LoopMessage sandbox page, set Sandbox Webhook URL to `https://<your-domain>/webhook` and Sandbox Webhook Header to the exact value of `WEBHOOK_SECRET`, with no `Bearer` prefix. Save.

Now text the sandbox number from your iPhone. The sandbox can't start a conversation, so the first text has to come from you. Each text you send reopens a 24-hour window in which Dori can reply.

To see what is happening, watch the debug log. It gets one JSON line per webhook and one per turn, including the model used, its raw output and how long it took:

```bash
tail -f logs/dori.jsonl
```

ngrok also has a request inspector with a replay button at http://localhost:4040.

### In the terminal

You can talk to the same bot without a phone. This runs the real prompts against Nemotron and prints Dori's texts instead of sending them:

```bash
.venv/bin/python scripts/chat_cli.py --verbose
```

`--verbose` shows the raw model output after each turn, which helps when you are tuning a prompt. The chat uses a throwaway database unless you pass `--db some-file.db`.

## Project structure

```
dori/
  config.py        settings from .env, plus constants such as the debounce time
  events.py        the Event the server hands to the bot
  server.py        FastAPI app: auth, dedupe, debounce, per-user lock, fallback
  bot.py           what Dori does with each turn: onboarding, corrections, replies
  prompts.py       fixed messages and the prompts sent to the model
  llm.py           Nemotron calls, the Lightning fallback, lenient JSON parsing
  loop_client.py   sends a message or a typing indicator through LoopMessage
  store.py         SQLite: users, messages, seen webhook ids
  turn_log.py      the JSON-lines debug log
tests/             pytest suite, one file per module
scripts/
  chat_cli.py      chat with Dori in the terminal
  bench_models.py  times Nemotron models on one Korean correction
```

`dori.db` and `logs/` are created at runtime and are not committed.

## Tests

```bash
make test
```

The tests use a temporary database, fake keys and a scripted model. They never reach LoopMessage or NVIDIA: a fixture fails any test that tries to open a real network connection.
