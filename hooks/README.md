# .claude/hooks/ — UserPromptSubmit and other hooks

Reserve rules and format spec. When writing a new hook — start here.

## Substring lists — live, trained on logs

Substantive hooks (`audit-gate`, `verify-plan-gate`, `ground-truth-gate`) filter by a list of trigger phrases — this is a **subset of the owner's conversational speech**, not a final closed set. Language coverage expands on owner complaints ("the hook should have fired, it didn't") + an offline pass over session JSONL logs.

New miss = look at the phrase, add a substring, run `/hook-smoke-test`. Don't invent "how the owner usually talks" — only from logs.

## Output format for UserPromptSubmit hooks

**Correct** — JSON with `hookSpecificOutput.additionalContext`:

```json
{"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "<text>"}}
```

Claude Code injects this field into the model's window as a `<system-reminder>` before the response — the agent physically sees it.

**Incorrect** — JSON with `systemMessage`:

```json
{"systemMessage": "<text>"}
```

`systemMessage` is shown **only to the user** in the Claude Code transcript. **The agent doesn't see it.** Silent failure: the hook prints `systemMessage`, owner believes hooks are working, agent never receives the instructions.

## Reading the current prompt

**Correct** — parse `prompt` from stdin INPUT JSON:

```bash
INPUT=$(cat)
CURRENT_PROMPT=$(echo "$INPUT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('prompt',''))" 2>/dev/null)
export CURRENT_PROMPT
```

Then the Python block reads `os.environ.get('CURRENT_PROMPT','')` as the freshest turn.

**Incorrect** — parsing the session jsonl history. At the moment of `UserPromptSubmit`, the current prompt **is not yet written to jsonl** — the hook sees only the previous user-prompt, fires one turn later than needed.

Fallback to jsonl is acceptable as a safety net (if `prompt` is empty for some reason), but the primary candidate is always `current`.

## Pulse markers (owner sees which hooks fired)

Each hook injects into `additionalContext` an instruction like:

> "On the first line of the response add the line 'hook-name' (one line, no other markers). Current turn only."

Active hooks:
- `simple-language` — every turn (no triggers)
- `audit #N` — on hits ≥1 in a 30 user-prompt window, N = counter
- `verify-plan` — on implementation triggers
- `ground-truth` — on state-fact questions
- `pull-lab` — when vault is synced with remote

Owner sees the hook-names block at the very start of the response — at a glance understands what fired, without digging through logs.

## Where the hook rules live

- **This spec** — `.claude/hooks/README.md`.
- **Discipline rules** (when which hook applies) — `Self/principles.md` and `Self/audit-mode.md`.

## Smoke-test after editing

Any `Edit` to `.claude/hooks/*.sh` must be accompanied by a smoke-test:

```bash
bash -n .claude/hooks/<file>.sh    # syntax check
echo '{"session_id":"test","prompt":"test input"}' | bash .claude/hooks/<file>.sh   # dry-run
# exit should be either empty (hook decided not to fire), or valid JSON with additionalContext
```

The skill `.claude/skills/hook-smoke-test/` automates this after Edit.
