#!/usr/bin/env bash
# tired-mode-gate.sh — UserPromptSubmit hook.
# Trigger: "tired", "simpler", "keep it simple", "plain language", "no jargon", "tired mode".
# Goal: switch to "tired mode" — shorter, no jargon, one change at a time.
# Origin: owner said "simpler — make it a hook".

set -u
INPUT=$(cat)

PROMPT=$(echo "$INPUT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('prompt',''))" 2>/dev/null)
[ -z "$PROMPT" ] && exit 0

export PROMPT
HIT=$(python3 -c "
import re, os
p = os.environ.get('PROMPT','').lower()
triggers = ['tired', 'keep it simple', 'simpler', 'plain language', 'no jargon', 'tired mode', 'dumb it down', 'eli5']
print('HIT' if any(t in p for t in triggers) else '')
")
[ -z "$HIT" ] && exit 0

msg="TIRED-MODE GATE. Trigger: owner is tired / asked for simpler. Add 'tired-mode' to the hook-names block.
Mode rules:
— One change at a time. No batches.
— Shorter — 1-2 sentences, no lists unless asked.
— No jargon. English terms — in parentheses with translation.
— Confirm each change before the next one.
— If owner replies briefly or seems confused — don't push, ask 'continue or pause?'.
Don't deactivate until owner explicitly says 'back to normal' / 'normal mode' / 'without tired mode'."

printf '%s' "$msg" | python3 -c "import json,sys; print(json.dumps({'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit', 'additionalContext': sys.stdin.read()}}, ensure_ascii=False))"

exit 0
