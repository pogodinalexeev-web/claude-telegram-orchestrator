---
name: hook-smoke-test
description: Run smoke-test after editing hooks `.claude/hooks/*.sh` — syntax check + dry-run JSON + verify `additionalContext` format. Use ALWAYS after Edit/Write on any `.claude/hooks/*.sh`. Also on triggers "check hooks", "smoke-test hooks", "/hook-smoke-test".
---

# hook-smoke-test

## Why

Multiple hooks were silently broken for a day: output format was `systemMessage` instead of `hookSpecificOutput.additionalContext`. The bot believed they were working because `exit 0` was returned. Failure was only discovered when owner noticed no visual pulse — could have dragged on for weeks.

Rule: after **any** edit to `.claude/hooks/*.sh` — `bash -n` + dry-run + grep on output.

## When to call

**Automatically** (without explicit request from owner):
- Right after `Edit` / `Write` on any `.claude/hooks/*.sh`.
- When creating a new hook.
- When editing `.claude/settings.json` if `hooks.*.hooks.*.command` paths changed.

**On request:** `/hook-smoke-test`, "check hooks", "smoke-test hooks", "are hooks working".

**Don't call:** for documentation edits (`.md`), for skill edits, for non-UserPromptSubmit hooks without output (e.g. bare `git pull`).

## Algorithm

For each `.sh` in `.claude/hooks/` (or specific one if passed as argument):

### 1. Syntax check

```bash
bash -n .claude/hooks/<file>.sh
```

If exit is not 0 — hook is broken, **immediately** tell owner with the specific error line. Don't continue.

### 2. Dry-run with dummy prompt

```bash
echo '{"session_id":"smoke-test","prompt":"test architectural hypothesis let us do it better"}' | bash .claude/hooks/<file>.sh
```

Prompt intentionally contains triggers for various gates: "architectural" (audit), "let us do" (verify-plan), "better" (audit). Each semantic hook should fire on it.

Anti-hooks (no triggers — `simple-language-gate`, `pull-lab`) — fire always / don't fire on condition, separately.

### 3. Check output format

If dry-run output is not empty:
- Must be valid JSON: `python3 -c "import json,sys; json.load(sys.stdin)" <<< "$OUTPUT"` — exit 0.
- Must contain key `hookSpecificOutput.additionalContext`: `python3 -c "import json,sys; d=json.load(sys.stdin); assert 'hookSpecificOutput' in d and 'additionalContext' in d['hookSpecificOutput']" <<< "$OUTPUT"`.
- **Anti-pattern**: key `systemMessage` at top level. If present — hook is broken. This is the exact bug from before.

If output is empty — hook decided not to fire. Valid if hook has a firing condition (triggers, threshold, anti-list). If hook should always fire (like `simple-language-gate`) — empty output = failure.

### 4. Report

Format — table:

```
| Hook | Syntax | Dry-run | Format | Conclusion |
|---|---|---|---|---|
| audit-gate | ✅ | ✅ (output 412 bytes) | ✅ additionalContext | OK |
| simple-language-gate | ✅ | ✅ (always fires) | ✅ additionalContext | OK |
| ground-truth-gate | ✅ | ⚠️ empty output on test prompt | — | check triggers |
| ... | | | | |
```

If all ✅ — one line "5/5 OK". If ⚠️ or ❌ — specifics.

## Model for dry-run

Smoke-test runs as subagent. If main thread is on Opus — subagent on Sonnet. If main is on Sonnet — on Opus. **Never Haiku.** One model can't be both executor and judge (single-judge hole).

## Anti-pattern

Don't use as substitute for real eval (live metric over 2 weeks). Smoke-test catches **technical integrity** (syntax, output format), not **semantic accuracy** of triggers (whether it correctly catches owner's phrases).

## Origin

Created after hooks were silently non-working and owner tested hooks manually after every edit — that's skill work, not owner's job.
