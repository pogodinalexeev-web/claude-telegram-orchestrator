---
name: chat-with-bot
description: Show the history of the owner's conversation with the TG-bot (Claude Code headless on VPS) as a chat view. Source — jsonl sessions in /home/owner/.claude/projects/. Use when owner says "what did I write to bot about X", "find in TG", "/chat-with-bot ...", "chat history", "what did we discuss in the bot", or when ground truth is needed about a message sent via TG-bot (link, password, decision fragment, forward).
---

# /chat-with-bot — TG-bot chat history

## Why
Vault records **results** (inbox, tasks, notes), but not the **process** of dialogue with the bot. Links, passwords, forwards, voice transcriptions, own responses — live in Claude Code jsonl sessions on VPS. This skill extracts them in human-readable form.

Canonical case: owner says "I sent you a link/password/idea in the bot" — need to find it in history, not make it up or ask to resend.

## How to run
Script on VPS: `~/extract_tg_chat.py`. Run via ssh:

```bash
ssh owner@<vps-host> "python3 ~/extract_tg_chat.py [args]"
```

**Arguments:**
- `<query>` — substring filter (case-insensitive). Multiple words with spaces (taken as one phrase).
- `--last 24h` / `--last 7d` / `--last 30m` — for last period.
- `--since 2026-05-08` — from specific date.
- `--limit 100` — increase limit (default 50).
- `--raw` — without text truncation (by default lines cut at 400 chars).

**Output:** chronologically, by sessions, format `[YYYY-MM-DD HH:MM] USER/BOT: <text>`.

## Scenarios

### 1. Find a specific fragment
Owner: "find in chat the link to the zoom recording"
```bash
ssh owner@<vps-host> "python3 ~/extract_tg_chat.py 'zoom' --last 30d --raw"
```
Then visually scan result — extract URL/password/context. If result is large — add narrower query.

### 2. What was discussed for a period
Owner: "what did I write to bot yesterday"
```bash
ssh owner@<vps-host> "python3 ~/extract_tg_chat.py --last 24h --limit 100"
```

### 3. Topic search without date
Owner: "find what I said about <project>"
```bash
ssh owner@<vps-host> "python3 ~/extract_tg_chat.py '<project>'"
```

## What to do after running
1. Run command, read output.
2. If needed fragment found — **quote in chat** (link, password, phrase) and continue the task.
3. If nothing found — widen window (`--last 30d` instead of `7d`) or try different query.
4. If search found too much — narrow query or add `--last`.

## What NOT to do
- Don't invent chat content. If script returned `(no messages matching filter)` — say so.
- Don't quote passwords/secrets in `log.md` or `status.md` without explicit owner request — sensitive data.
- Don't go into `/home/owner/.claude/projects/` manually with `cat` — 27MB+ jsonl will crash the session. Use the script.
- Don't `rsync` all jsonl to Mac — that's 27MB+ and a copy of sensitive content. Parse on VPS, fetch only result.

## Origin
Owner twice asked "find the link/password in TG" — response was "MCP can't read chat history" and asking to resend. Owner: "you have logs I know exactly, find a way". Logs do exist — every TG session is written as Claude Code jsonl. This skill permanently closes that gap.

## Script source
- `~/extract_tg_chat.py` — on VPS (run from here).
- After edits: sync to VPS via scp.
