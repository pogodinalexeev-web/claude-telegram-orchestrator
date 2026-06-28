#!/usr/bin/env bash
# simple-language-gate.sh — UserPromptSubmit hook on EVERY message.
# Gently reminds: answer in plain language + translate English terms on first use
# (format "native translation (english original)" — original in parentheses for
# glossary lookup in Resources/glossaries/tech-jargon.md).
# No triggers — fires on every turn. No refire-suppression — every turn.
# Origin: owner asked "make a hook on every message — answer in plain language,
# translate anglicisms and jargon".
# "hook = hook" (literal translation preferred over a loose synonym).

set -u
INPUT=$(cat)

# No heavy work — just inject. Don't read jsonl, don't count hits.
msg="SIMPLE-LANGUAGE GATE (every turn). Response discipline:
1) Before the main response, print one line with the letters of active hooks in format [X][Y][Z]. Key: S=simple-language, H=honesty, T=terse, G=ground-truth, D=do-it-yourself, A=audit (A#N with number), V=verify-plan, P=pull-lab. This hook adds [S]. If other hooks also gave instructions — add their letters to the same line. After the line — blank line, then main response. Only in the current turn; without this instruction in the next turn — don't print it.
2) Plain language — no industry jargon, no bare anglicisms.
3) English terms on first use in a turn: give as 'native translation (english original)'. Original in parentheses is required — needed for glossary lookup in Resources/glossaries/tech-jargon.md. Example: 'hook (hook)', 'eval (eval)', 'system reminder (system-reminder)'.
4) If a term isn't in the glossary — translate by meaning and add it to tech-jargon.md (as a separate edit, not in this response).
5) Don't apply to: code quotes, filenames/functions/commands, model/product names (Claude, Opus, Sonnet, Telegram), already-standard loanwords (bot, file, script)."

printf '%s' "$msg" | python3 -c "import json,sys; print(json.dumps({'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit', 'additionalContext': sys.stdin.read()}}, ensure_ascii=False))"

exit 0
