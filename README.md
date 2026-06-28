# claude-telegram-orchestrator

A personal assistant that runs in Telegram, orchestrating a **Claude Code** agent as
its engine. The bot is the transport and control layer; all reasoning and tool use
happen inside a long-lived `claude -p` process.

> Claude Code is a dependency (the engine), not part of this repository. This code is
> the orchestration around it. This is a portfolio extract of a working personal bot,
> with all private data removed; paths and secrets are read from the environment.

## Layout

| File | What it is |
| --- | --- |
| `resident_claude.py` | The engine wrapper: one long-lived `claude -p --input-format stream-json` process, with a watchdog and a protocol-interrupt "catch-up". |
| `bot.py` | A **minimal** orchestrator (~150 lines) — the core loop in its clearest form: long-poll, one resident per chat, stream the reply back. Good place to start reading. |
| `tg_bot.py` | The **full** bot — everything the real assistant does: attachments, reply post-processing markers, photo/document/voice sending, long-message chunking, an inline navigation menu, voice transcription, error handling. |
| `deep_research/` | A browser bridge: drives a stealth browser on the server to use a "Deep Research" feature that only exists in the web UI, and reads the result back. |
| `prompts/system_prompt.example.md` | A sanitized example system prompt — shows the *shape* of the rules without any private content. |
| `transcribe_native.py` | Voice transcription via a Premium Telegram user session (fast, no server CPU). |

## Why it's interesting

- **Resident process per chat.** Instead of spawning a fresh agent on every message,
  one `claude -p` process is kept alive per conversation. The ~6s cold start is paid
  once; later turns skip the Node + binary + MCP load. See `resident_claude.py`.
- **Watchdog timeouts.** A blocking `readline()` on the agent's stream can't time out
  on its own when the agent goes silent mid-tool-call. A watchdog thread kills the
  process on a turn or silence timeout so the bot never hangs.
- **Catch-up via protocol interrupt.** If the user sends another message before the
  answer is done, the current turn is interrupted (not killed) and a new turn starts
  on the same process, preserving session history.
- **Markers move behavior out of the model.** The reply is post-processed for
  lightweight control markers (e.g. confirm a file write, render buttons, propose a
  calendar event), keeping deterministic behavior in code rather than hoping the model
  complies.
- **Browser bridge for a UI-only feature.** `deep_research/` exposes a browser-only
  capability to the bot by driving a real, stealth browser on the server.

## Architecture

```
Telegram  ──getUpdates──►  bot.py / tg_bot.py  ──stream-json──►  resident_claude.py  ──►  claude -p
   ▲                              │                                  (one per chat)        (engine)
   └──────────sendMessage─────────┘
```

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
