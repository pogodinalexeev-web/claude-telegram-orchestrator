#!/usr/bin/env bash
# sudo-gate.sh — PreToolUse (Bash).
# If command contains sudo → remind about docker-sudo skill.
# Doesn't block (exit 0), only injects context.
# Origin: NoNewPrivileges blocks sudo in the bot, docker works instead.

INPUT=$(cat 2>/dev/null || echo "{}")

CMD=$(echo "$INPUT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('command',''))" 2>/dev/null)
[ -z "$CMD" ] && exit 0

echo "$CMD" | grep -qE '(^|\s)sudo(\s|$)' || exit 0

MSG="SUDO-GATE: command contains sudo, but the bot runs under NoNewPrivileges=yes — sudo is blocked by the kernel.
Use the docker-sudo pattern (.claude/skills/docker-sudo/SKILL.md):
• Other users' files → docker run -v /home/user:/mnt alpine sh -c '...'
• systemctl/kill → docker run --privileged --pid=host alpine nsenter -t 1 -m -u -i -n -p -- <command>
• Write to /etc → docker run -v /etc:/etc_host alpine sh -c 'cat > /etc_host/...'
Adjust UIDs to your user configuration."

printf '%s' "$MSG" | python3 -c "
import json, sys
print(json.dumps({
  'hookSpecificOutput': {
    'hookEventName': 'PreToolUse',
    'additionalContext': sys.stdin.read()
  }
}, ensure_ascii=False))
"

exit 0
