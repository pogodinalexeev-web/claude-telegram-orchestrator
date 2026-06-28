#!/usr/bin/env bash
# Reverse-prompt nudge — reminder to run a reverse-prompt after editing own infrastructure.
# Fires on Write/Edit to: `.claude/skills/*/SKILL.md`, Self/{audit-mode,principles,autonomy,SOUL}.md.
# Doesn't block — only outputs a system-message.
# Origin: "reverse-prompt applies to editing one's own skills too".

INPUT=$(cat)
TOOL=$(echo "$INPUT" | python3 -c "import json,sys;print(json.load(sys.stdin).get('tool_name',''))" 2>/dev/null)
FILE_PATH=$(echo "$INPUT" | python3 -c "import json,sys;d=json.load(sys.stdin).get('tool_input',{});print(d.get('file_path',''))" 2>/dev/null)

case "$TOOL" in
  Write|Edit|MultiEdit) ;;
  *) exit 0 ;;
esac

MATCH=0
case "$FILE_PATH" in
  */.claude/skills/*/SKILL.md) MATCH=1 ;;
  */Self/audit-mode.md|*/Self/principles.md|*/Self/autonomy.md|*/Self/SOUL.md) MATCH=1 ;;
esac

[ "$MATCH" -eq 0 ] && exit 0

cat <<'EOF'
{"systemMessage":"Reverse-prompt: editing a skill/rule = architecture-by-process. Run a reverse-prompt after a successful smoke-test? Spec goes into the changed file itself, not a separate one."}
EOF

exit 0
