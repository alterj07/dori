# Dori (돌이)

Dori is a Korean practice buddy that lives in iMessage. You text it the way you would text a friend, and it texts back in Korean. It was built at tidalBYTE '26 for older learners and immigrants who find apps like Duolingo overwhelming but already know how to send a text.

## What it does

You text first. Dori answers with a welcome that asks for your name, your native language, the language you want to practice, your level in your own words, and a style: casual, friendly, romantic or business. You can answer in one free-form message. The model pulls the fields out of it, and the code decides what is still missing and asks only for that. If you send something that is not an answer, such as a question or a joke, Dori reacts to it in one line and then lists what it still needs. While you are signing up it waits 6 quiet seconds instead of 3 before answering, because people send their details in pieces.

If either language is not English or Korean, Dori says so before the chat starts: "Just a warning, I'm only tuned for English and Korean so far".

After that Dori chats in Korean, in character for the style you picked. Casual, friendly and romantic use 반말. Business uses 합니다체 or 해요체, and in a business chat slang counts as a mistake too: Dori rewrites the message politely and says which words were too casual.

When your message has a real mistake, Dori replies inline to that message with the corrected sentence and a short explanation in English, then carries on the conversation in a separate text:

```
You:   나 어제 친구 만났어요 그리고 우리 영화 봤다 진짜 재밌었어
Dori:  ✏️ 나 어제 친구 만났어 그리고 우리 영화 봤어 진짜 재밌었어
       💬 Mixed speech levels: 만났어요 (polite) + 봤다/재밌었어 (casual).
       Changed 만났어요 → 만났어 and 봤다 → 봤어 for consistent 반말.
Dori:  오 누구랑? ㅋㅋ 무슨 영화 봤어?
```

Dori fixes at most two errors per message. Slang and shortenings that fit the style (ㅋㅋ, 대박, dropped particles in casual speech) are left alone. Mixing speech levels in one message counts as a mistake in every style, and 반말 counts as one only in business style.

A fix that only changes punctuation, capital letters or spacing is dropped before it reaches you. So is one that only removes a particle you wrote. A message with no Korean in it is never corrected. If you drop an English word into a Korean sentence, Dori will often give you the Korean word.

If you don't understand one of Dori's messages, react to it with the question mark tapback. A ❓ emoji reaction works too. Dori replies inline to that message and explains it in English: what it means, the slang in it and any cultural context.

```
Dori:  오 대박 ㅋㅋ 어디 갔는데? 메뉴 뭐 시켰어?
You:   ❓
Dori:  Translation: Oh wow lol Where did you go? What did you order?
       대박 = slang for wow, awesome, or jackpot
       ㅋㅋ = text laughter like haha or lol
       어디 갔는데? = casual Where did you go? with a friendly nudge
       메뉴 뭐 시켰어? = casual What did you order on the menu?
       Tone: upbeat and curious, typical close-friend texting style
```

You can also just ask, in English or in Korean: "what does 대박 mean?" Dori answers in English and leaves the story where it was. A vague question works too. Reply inline to any message with "what's that?" and Dori explains that message. Without an inline reply, it explains the latest Korean one. Dori treats you as fluent in your own language, so it never explains or corrects your English. It then stays out of character, so you can ask follow-up questions without the chat cutting in. When you send a normal message again, such as a reply in Korean or "ok got it", Dori picks the conversation back up.

Dori's English is meant to read like a text from a person. Its explanations are short plain lines, and the period that would end a line of English is removed before sending.

There are three commands. `help` resends the quick tips that Dori sends at the end of onboarding. `/style business` changes the style without starting over, and so does asking in your own words, for example "can you talk to me more formally?". `/reset` deletes your profile and your history, and your next text starts onboarding again.

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

A ❓ tapback skips the wait in step 4. Every message is stored with its LoopMessage id, which is how a tapback or an inline reply is matched to the message it points at.

When a text arrives, the server also asks LoopMessage to mark it as read and show a typing bubble while the reply is prepared. If a turn crashes, the user gets "Sorry, can you say that again? 🙏" and the traceback goes to the log.

### The model

Each chat turn is a single call to NVIDIA Nemotron 3 Ultra. The prompt carries the user's profile and the last 20 messages, and the model returns one JSON object with four fields: `intent` (chat or question), `correction`, `explanation` and `reply`.

Ultra is the only Nemotron model that lists Korean as supported. `scripts/eval_prompts.py` runs the chat prompt over a fixed set of learner messages and checks whether each one gets fixed, left alone, treated as a question or read as a request to change style. In the last full run, on 37 cases, Ultra matched all of them. The much faster Nemotron 3.5 Lightning matched 21 of 24 on an earlier version of the set, but several of its fixes were wrong. It changed 만났어요 to 만났고, for example, where the right fix is 만났어.

Lightning is still the fallback. NVIDIA often answers 429 for Ultra and accepts the same call a moment later, so a 429 is retried after 1, 2 and 3 seconds first. If Ultra errors, stays rate limited or takes longer than 10 seconds in total, the call goes to Lightning once and the log records why. A correction that comes from Lightning is not sent, because its fixes are wrong too often. Its reply still goes out. The JSON is parsed leniently: code fences, stray text around the object and a cut-off object are all handled. If the model answers in plain prose, that text goes out as an ordinary reply. If an explanation comes back mostly in Korean, the bot asks once more for it in the user's language.

## Limits

- Dori has mostly been tested with English speakers learning Korean. Korean speakers practicing English have eight scoring cases and one real conversation behind them. There, texting habits such as a lowercase i, no final period and "im" are left alone, while real errors such as "she dont like" are fixed. Other pairs may work, but nobody has tried.
- NVIDIA rate-limits Ultra tightly. In one real chat it refused 10 of 35 calls at only a few calls a minute. Dori retries before falling back, but a turn that still ends up on Lightning gets a reply and no correction.
- LoopMessage sends nothing when you edit or unsend a message, so Dori cannot react to that. Send the corrected text as a new message.
- The LoopMessage sandbox allows five contacts, and Dori can only reply within 24 hours of a contact's last text.
- Dori never texts first, so there are no reminders. Voice messages, group chats and a progress dashboard are not built.

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

To start over, for example before a demo, text `/reset` and then anything else.

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

### Scoring the prompts

`scripts/eval_prompts.py` is how the prompts were tuned. Each case names what a good tutor would do with a message, and the script prints what the model did next to it:

```bash
.venv/bin/python scripts/eval_prompts.py
.venv/bin/python scripts/eval_prompts.py --model nvidia/nemotron-3.5-lightning-30b-a3b
```

Run it after any change to `dori/prompts.py`. The model is sampled at temperature 0.6, so one or two cases can flip between runs.

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
  mass_test.py     runs the whole app through 21 scripted conversations
  chat_cli.py      chat with Dori in the terminal
  eval_prompts.py  scores the chat prompt on a fixed set of learner messages
  bench_models.py  times Nemotron models on one Korean correction
```

`dori.db` and `logs/` are created at runtime and are not committed.

## Tests

```bash
make test
```

The tests use a temporary database, fake keys and a scripted model. They never reach LoopMessage or NVIDIA: a fixture fails any test that tries to open a real network connection.

### End-to-end run

`scripts/mass_test.py` drives the whole app through 21 scripted conversations. The FastAPI app, the debounce, the bot and the Nemotron calls are real. LoopMessage is replaced by a fake that records what Dori would send, and every inbound message is a webhook shaped like the ones LoopMessage delivers. It uses fictional phone numbers and a throwaway database.

```bash
.venv/bin/python scripts/mass_test.py
.venv/bin/python scripts/mass_test.py --only "reactions,commands"
```

It covers onboarding in several shapes, corrections in each style, questions and explain mode, reactions, inline replies, the commands, webhook auth and duplicates, two people at once, a 16-turn conversation, odd inputs, and injected failures of LoopMessage and of both models. Every message goes to `logs/mass_test_transcript.txt`.

A full run makes about 130 model calls. It spaces them 4 seconds apart and waits whenever NVIDIA answers 429, so allow ten minutes or more.

The first full run passed 140 of 150 checks, but NVIDIA rate-limited Ultra and 54 of its 122 turns were answered by the fallback model. A slower re-run of the six affected scenarios passed 41 of 42 with Ultra answering every turn. The one miss was an English line inside a Korean reply, which the bot now removes.
