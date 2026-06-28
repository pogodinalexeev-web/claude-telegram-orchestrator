---
name: challenge
description: Opponent agent. Argues AGAINST the owner's idea, drawing on their own history from status.md, Journal/log.md, ideas.md. Use when the owner is at a fork, about to make an irreversible or costly move — "/challenge <idea>", "challenge this", "argue with me", "what's wrong with this idea".
---

# /challenge — opponent agent on your own data

## Model

If /challenge is called **as a subagent** (from /audit, /end-day, /weekly-review, or another skill) — use `model: sonnet`, not opus.

> **Dialogue mode:** the challenger can work not in a single shot, but in several rounds. Challenger attacks → the owner/main thread responds → challenger attacks again based on the response. Two different models in dialogue = the challenger doesn't see its own past reasoning, only the new argument → no confirmation bias. Apply for complex hypotheses — one round of challenge often only surfaces surface-level holes.

## Purpose
Force the owner to cross-check with the past before making an impulsive move. Not just "playing devil's advocate", but extracting **concrete** arguments from their history of rollbacks, closed loops, notes on "what we're NOT doing".

## What to do

1. **Take the idea** from command arguments (everything after `/challenge `). If empty — ask in one line: "What to challenge?" and stop.

2. **Read (in this order, don't skip):**
   - `status.md` — "Open loops" section (including closed `~~struck through~~`) and "What we DON'T do and why".
   - `Journal/log.md` — last ~80 lines (tail). Goal — find rollbacks, recurrences, disappointments.
   - `Tasks/ideas.md` — perhaps the same or similar idea was already dropped.
   - Project principles/personality files — for tone calibration (honesty, no flattery).

3. **Formulate 3-5 arguments AGAINST.** Each argument:
   - Starts with a concrete fact from the vault: "In status.md loop #X", "In Journal/log.md from 2026-04-XX", "In ideas.md from XX.XX there was already idea Y, which…".
   - Firm, no filler, no "however possibly".
   - No invented facts. If the vault has no argument — don't make one up. Better 3 real ones than 5 stretched.

4. **Summary** at the end (1-2 lines): not "the idea is good/bad", but "aligns with X, contradicts Y". Example: "Aligns with your course toward stack simplification (#16). Contradicts closed loop #5 — you already decided not to touch this until the assistant stabilizes."

5. **If there are no arguments** (vault is silent): say directly — "The vault has no facts against this idea. Either this is a first attempt, or the context isn't recorded in the vault. Give arguments from general considerations or stop?"

## What NOT to do
- Don't edit files. Read-only and output to chat.
- Don't propose alternatives. This is a challenge, not a brainstorm. Alternatives — next step, separate.
- Don't flatter. "Honesty and rigor, no flattery" (core personality principle). If an idea contradicts many loops — say so directly.
- Don't apologize for bluntness. This is the skill's purpose.
- Don't invent facts "from history". If citing — must be a real line in the vault.
- Don't comment on the challenge request itself ("great idea to challenge!"). Straight to the point.

## Output format

```
Challenging: <short one-line reformulation of the idea>

1. **<short thesis>**
   <1-2 lines. Reference: [file, loop/date]>

2. **<thesis>**
   <reference>

…

**Summary:** <aligns with X, contradicts Y>
```

## Example

Owner: `/challenge migrate the whole system to the new stack right now`

Response:
```
Challenging: migrating to the new stack now, without a rollback option.

1. **You set the decision for 1-2 weeks ahead.**
   status.md, loop #10: "In 1-2 weeks — decision: final migration or rollback". Not "1-2 weeks from now", that's around May 13-20.

2. **The stack is still unstable — closed loops are fresh.**
   Journal/log.md May 6 22:00: loops #14, #15, #16, #17 closed today. Sync, hooks, commands↔skills. By the "better a week without recurrences" rule — observe, don't poke.

3. **The last major phase was closed an hour ago.**
   status.md, loop #4 closed at 22:00. The first user hasn't even been set up in the new environment yet. Migrating from a stack that hasn't been used for its intended purpose is premature.

**Summary:** aligns with your course toward stabilization. Contradicts your own deadline in loop #10.
```
