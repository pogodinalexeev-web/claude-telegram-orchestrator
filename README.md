# claude-telegram-orchestrator

A **personal AI assistant that lives in Telegram**. The bot is the transport and
control layer; a long-lived **Claude Code** agent (`claude -p`) is the engine — all
reasoning and tool use happen inside it. Built and hardened over ~2 months of daily
use, it runs on a VPS and a Mac in sync, and powers several separate users from one
shared skill pool.

The hard parts aren't "send a message to Telegram" — they're keeping a long-running
agent **fast** (a resident process per chat), **honest and on-task** (a layer of hooks
that fix model drift), **grounded** (semantic search over a personal knowledge base),
**durable** (a file-based long-term memory and two-way Mac↔VPS sync), and **capable**
(its own Telegram infrastructure, a browser bridge, voice, calendar, background jobs).

> Claude Code is a dependency (the engine), not part of this repository. This repo is
> a **portfolio extract** of a working personal assistant, with all private data
> removed — names, paths, cabinets and secrets are stubbed or read from the
> environment. It includes the orchestrator, the search engine, the discipline hooks,
> the skill pool, infrastructure examples and a vault skeleton.

🇷🇺 Russian version: [README.ru.md](README.ru.md)

## What's in this repository

| File | What it is |
| --- | --- |
| `resident_claude.py` | The engine wrapper: one long-lived `claude -p --input-format stream-json` process, with a watchdog and a protocol-interrupt "catch-up". |
| `bot.py` | A **minimal** orchestrator (~150 lines) — the core loop in its clearest form: long-poll, one resident per chat, stream the reply back. Good place to start reading. |
| `tg_bot.py` | The **full** bot — attachments, reply markers, media/voice sending, long-message chunking, inline menu, voice transcription, calendar, background jobs. |
| `deep_research/` | A browser bridge: drives a stealth browser on the server to use a "Deep Research" feature that only exists in the web UI, and reads the result back. |
| `prompts/system_prompt.example.md` | A sanitized example system prompt — the *shape* of the rules without private content. |
| `transcribe_native.py` | Voice transcription via a Premium Telegram user session (fast, no server CPU). |
| `vault_rag/` | The semantic search engine over the knowledge base — hybrid (vector + full-text), local, GPU-free, with a warm daemon. See its own README. |
| `hooks/` | ~30 Claude Code hooks — the discipline layer that fixes model drift (honesty, ground-truth, terse, plan-verify, audit) plus safety and sync hooks. |
| `skills/` | 53 skills (slash-commands) — the assistant's capability pool, de-personalized. |
| `infra/` | Deployment examples: own Telegram Bot API server (Docker), systemd units, the faster-whisper transcription shim, the Mac↔server sync script. |
| `vault_example/` | A de-personalized skeleton of the knowledge base the assistant runs on (structure + rules, no content). |

## Architecture

```
Telegram  ──getUpdates──►  bot.py / tg_bot.py  ──stream-json──►  resident_claude.py  ──►  claude -p
   ▲                              │                                  (one per chat)        (engine)
   └──────────sendMessage─────────┘                                        │
                                                                           ▼
                            vault (knowledge base)  ◄──RAG / memory / git-sync──┘
```

## Full feature set

### Engine & sessions
- **Resident process per chat.** One `claude -p` process is kept alive per
  conversation; the ~6s cold start is paid once, later turns skip the Node + binary +
  MCP load. (`resident_claude.py`)
- **Catch-up via protocol interrupt.** A new message mid-answer interrupts the current
  turn (not kills it) and starts a new turn on the same process, preserving history.
- **Watchdog timeouts.** A watchdog thread kills the process on a turn or silence
  timeout so a stalled tool-call can never hang the bot.
- **Inline "⏹ Stop" button** that appears before the turn starts and kills the process
  cleanly, keeping the partial text.
- **Streaming output.** The reply appears token-by-token in a status message
  (throttled), not as one final blob.
- **Background jobs (`__BG_TASK__`).** Long jobs run in a watchdog thread; when done,
  the bot opens a *separate* turn with the result and messages the user first — without
  blocking the live chat.
- **Session rotation & token accounting.** Per-chat session store; auto-suggests
  `/compact` over time; a 3-horizon token counter (session / turn / msg).
- **Quota-limit TTL mode.** On a Claude usage limit the bot silently files the incoming
  message to the inbox with a 👀 reaction instead of failing.

### Memory & rules (fixing model drift)
- **File-based long-term memory.** A `memory/` folder of `feedback_*` / `project_*`
  notes with an index the agent reads and writes — knowledge that survives across
  sessions, separate per instance.
- **A "constitution" of behaviour files** (`SOUL.md`, `principles.md`, `autonomy.md`,
  `audit-mode.md`) that define voice, rules, permissions and the audit stance.
- **Hot rule reload — no restart.** The bot watches the rule/prompt files; on change it
  injects the diff at the start of the next turn and notes "rules updated", without
  breaking the Telegram thread.
- **Discipline hooks that fix model drift.** A layer of hooks keeps the agent honest
  and on-task, each firing on triggers and injecting guidance: `honesty-gate`
  (mark facts / don't fabricate), `ground-truth-gate` (check the source before
  claiming, don't answer from memory), `terse-gate` (be brief), `simple-language-gate`
  (plain language + glossary), `verify-plan-gate` (require a plan + approval before
  edits), `audit-gate` (nudge an audit before architectural decisions). Plus safety
  hooks (`safety.sh` blocks dangerous shell, `tg-write-gate` gates outbound messages),
  a `precompact-backup`, an `auto-commit-flush`, and more — ~30 hooks in total.

### Semantic search (RAG over the vault)
- **Hybrid search over the knowledge base.** Semantic (a multilingual MiniLM embedder
  via `fastembed`, no GPU, no external API) + full-text (`SQLite FTS5`), merged with
  reciprocal-rank fusion; results as `file:line`, under ~0.6s.
- **A warm daemon** that keeps the model loaded, unloads after idle, and answers
  `search` / `reindex` commands; autostarted via systemd (VPS) / launchd (Mac).
- **Re-index after every message**, not before each search — so search is always
  instant and never pays a batch-rebuild lag.
- **Chat logs are indexed too** — the bot's own conversations are appended to the index
  so "what did we discuss about X" is searchable.

### Mac ↔ VPS synchronisation
- **Two-way sync around every message.** A bare git repo is the transport; the VPS bot
  syncs at the start of each turn, the Mac mirrors it symmetrically via a hook — neither
  side loses work on an unclean exit.
- **A rescue commit** before every fetch/merge so an interrupted session can't drop
  files.
- **Hourly self-backup** of the bot's own source into the vault (read-only mirror).
- **Per-machine log files** so two instances never overwrite the same day's log; both
  are indexed for search.

### Telegram infrastructure (built, not off-the-shelf)
- **Own Telegram Bot API server** (local mode, `127.0.0.1`) — raises the file limit
  from 20 MB to **2 GB**; one server serves multiple bots, told apart by token.
- **Own Telegram MCP server** — a Telethon user session that can message *any* chat as
  the owner, send voice / files / reactions, edit and delete messages — beyond the Bot
  API's "only who wrote first" limit. Outbound writes are gated by a hook.
- **4-step voice transcription routing** — Groq Whisper first, then by length: a
  resident faster-whisper shim (≤20s), a native Premium session (20s–5min), AssemblyAI
  (>5min), with automatic fallback on quota.
- **Text-to-speech (`__TTS__`).** The model picks the voice; routed to zvukogram /
  edge-tts.
- **Google Calendar (MCP).** Two-stage event creation (`__CAL_PROPOSE__` → confirm →
  create), all-day events, a pending state with TTL.
- **Attachments.** Auto-save of photos/video/docs/audio to the vault, album
  aggregation, a pending confirm/drop queue, a 30-day voice archive.
- **Media replies.** File paths in the reply are sent back as photos or documents;
  long messages are chunked under Telegram's limit with Markdown→HTML rendering.
- **Inline navigation menu** for briefs/projects, with callback handling.
- **Scheduled agents** via systemd timers (Moscow TZ): daily brief, reflection, weekly
  review.
- **Deploy copies for other people** — a template clones the whole bot (new user +
  vault skeleton + clean rule files) onto the same shared skill pool.

### External integrations & tooling (a skill pool)
The assistant carries **~55 skills** (slash-commands). Highlights:
- **Browser automation on the server** — a stealth browser (patchright + Chrome under
  Xvfb) gets past Cloudflare for scraping and for UI-only features (`deep_research/`).
- **Call-record pull & transcription** — log into a phone-operator web cabinet, pull
  MP3 call records, transcribe with diarisation (AssemblyAI).
- **Cinema / listings scrapers** — parse cinema schedules, filter and report on a
  schedule.
- **Media pulls** — Instagram reels (via Apify) and YouTube (subtitles / frames /
  audio).
- **Content & marketplace generation** — product cards, photoshoots, poster/affiche
  generation, image/video generation.
- **The vault funnel** — inbox-first capture, `/process-inbox`, `/atomize`,
  `/weekly-review`, `/daily-prep`, and a 3-agent `/audit` (vault + web + challenger)
  run before architectural decisions.

## Run

```bash
cp .env.example .env      # fill in your values
pip install -r requirements.txt
set -a; source .env; set +a
python bot.py             # minimal version — or: python tg_bot.py for the full one
```

## Notes

- Portfolio extract — a faithful, de-personalized copy of a working bot, not a toy.
- All paths and secrets are read from the environment; see `.env.example`.
