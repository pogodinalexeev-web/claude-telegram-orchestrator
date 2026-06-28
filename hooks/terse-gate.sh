#!/usr/bin/env bash
# terse-gate.sh — UserPromptSubmit hook on every message.
# One instruction: answer briefly, verbosely only on explicit request.
# Origin: owner said "make a hook before any message — answer briefly unless told otherwise".

set -u
cat >/dev/null  # input not needed — fires unconditionally

msg="TERSE GATE. Answer briefly.
— No preambles, no wrap-up summaries at the end.
— Direct answer to the question, no padding.
— Lists only if explicitly expected. Otherwise — one or two sentences.
— Verbose (elaborate, give options, explain the chain) — only if owner said 'in detail' / 'elaborate' / 'expand' / 'full' / 'detailed'.
— When in doubt whether to be short or long — choose short.
Add [T] to the hook-names line."

printf '%s' "$msg" | python3 -c "import json,sys; print(json.dumps({'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit', 'additionalContext': sys.stdin.read()}}, ensure_ascii=False))"

exit 0
