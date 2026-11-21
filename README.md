# Dori (돌이)

Dori is a Korean practice buddy that lives in iMessage. You text it the way you would text a friend, and it texts back. It was built at tidalBYTE '26 for older learners and immigrants who find apps like Duolingo overwhelming but already know how to send a text.

Status: the webhook server, the storage layer and the LoopMessage client work. The bot itself is still a stub that echoes whatever you send. Onboarding, corrections and explanations come next.

## How it works

```
iPhone -> iMessage -> LoopMessage -> POST /webhook -> ngrok -> FastAPI on your laptop
                                                                  |
                                                        bot -> LoopMessage -> iPhone
```

LoopMessage delivers each text and tapback as a webhook, and it retries up to 30 times unless it gets a 200 within 15 seconds. So the server answers first and does the work afterwards:

1. It compares the `Authorization` header with `WEBHOOK_SECRET` and returns 401 on a mismatch.
2. It drops any `webhook_id` it has already stored in SQLite.
3. It returns 200.
4. It adds the text to that sender's buffer and waits for 3 quiet seconds, so three short texts in a row become one turn.
5. It runs the turn. Turns for the same person run one at a time, and different people don't wait for each other.

A ❓ tapback skips step 4 and runs immediately. If a turn crashes, the user gets "Sorry, can you say that again? 🙏" and the traceback goes to the log.

## Requirements

- Python 3.14
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
| `LOOP_API_KEY` | LoopMessage dashboard, under API and then Settings |
| `WEBHOOK_SECRET` | Any long random string you make up |
| `NGROK_DOMAIN` | Your fixed ngrok domain, without `https://` |

## Usage

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

To see what is happening, watch the debug log. It gets one JSON line per webhook and one per turn:

```bash
tail -f logs/dori.jsonl
```

ngrok also has a request inspector with a replay button at http://localhost:4040.

## Project structure

```
dori/
  config.py        settings from .env, plus constants such as the debounce time
  events.py        the Event the server hands to the bot
  server.py        FastAPI app: auth, dedupe, debounce, per-user lock, fallback
  loop_client.py   sends a message or a typing indicator through LoopMessage
  store.py         SQLite: users, messages, seen webhook ids
  turn_log.py      the JSON-lines debug log
  bot.py           what Dori says (an echo stub for now)
tests/             pytest suite, one file per module
scripts/
  bench_models.py  times Nemotron models on one Korean correction
```

`dori.db` and `logs/` are created at runtime and are not committed.

## Tests

```bash
make test
```

The tests use a temporary database and fake keys. They never reach LoopMessage: a fixture fails any test that tries to open a real network connection.
