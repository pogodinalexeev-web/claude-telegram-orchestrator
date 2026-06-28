#!/usr/bin/env bash
# do-it-yourself-gate.sh — UserPromptSubmit hook.
# Trigger: imperative "do it" / "make it" / "build it" in the owner's prompt.
# Goal: don't reply "here are the commands, run them yourself". Check vault first,
# then community solutions, then propose an implementation.
# Origin: owner said "DO IT means do it yourself, not give me steps".

set -u
INPUT=$(cat)

PROMPT=$(echo "$INPUT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('prompt',''))" 2>/dev/null)
[ -z "$PROMPT" ] && exit 0

export PROMPT
HIT=$(python3 -c "
import re, os
p = os.environ.get('PROMPT','')
# Matches common imperative forms: 'do it', 'make it', 'build it', 'implement', 'create'
print('HIT' if re.search(r'(?i)\b(do it|make it|build it|implement it|create it|just do|go ahead and (do|make|build|implement))\b', p) else '')
")
[ -z "$HIT" ] && exit 0

msg="DO-IT-YOURSELF GATE. Owner said 'do it' — that means doing it programmatically, not offloading manual steps.
1) Add [D] to the hook-names line.
2) Action order:
   a) Project knowledge first — grep the vault: is there an existing skill/hook/script for this task (.claude/skills/, .claude/hooks/, .claude/commands/)?
   b) If not — check web/github: how does the community solve this (relevant repos, README, SKILL.md)? WebFetch specific files — not a generic search.
   c) Propose an implementation: new skill, edit to existing one, or new hook. With concrete filenames and line numbers.
3) Forbidden:
   — Responding 'here are the commands, run them yourself' without attempting a programmatic solution;
   — Disguising manual steps as advice ('you could do it like this');
   — Listing UI clicks when an API/CLI exists.
4) If the task genuinely requires out-of-scope actions (physical world, another account without auth) — be honest: 'cannot do this programmatically, reason: <X>'. Don't dress up a dead end as a plan."

printf '%s' "$msg" | python3 -c "import json,sys; print(json.dumps({'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit', 'additionalContext': sys.stdin.read()}}, ensure_ascii=False))"

exit 0
