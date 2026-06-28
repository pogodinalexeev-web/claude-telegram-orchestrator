#!/usr/bin/env bash
# SessionStart: briefing when Claude Code session starts/resumes in the vault.
# Goal: eliminate the "what was I doing?" tax with a ~30-line summary.

set -e
VAULT="${CLAUDE_PROJECT_DIR:-$(pwd)}"
cd "$VAULT" || exit 0

DATE=$(date '+%Y-%m-%d %A, %H:%M')
TODAY=$(date '+%Y-%m-%d')

echo "---"
echo "Date: $DATE"
echo ""
echo "Reading order for this session:"
echo "   1) status.md   2) index.md   3) Tasks/manual.md"
echo "   4) Self/SOUL.md  5) Self/audit-mode.md  6) Self/principles.md  7) Self/autonomy.md"
echo "   8) Journal/log.md (last 30 lines)"
echo ""

# Daily note
DAILY="Journal/${TODAY}.md"
if [ -f "$DAILY" ]; then
  echo "Daily note for today already exists: $DAILY"
else
  echo "Daily note for today NOT created. Run /daily-prep."
fi

# Inbox status
if [ -f "inbox.md" ]; then
  RAW_LINES=$(wc -l < "inbox.md" | tr -d ' ')
  echo "inbox.md: $RAW_LINES lines$([ "$RAW_LINES" -gt 50 ] && echo " — time for /process-inbox")"
fi

# Files with #next across projects
NEXT_COUNT=$(grep -rl "#next" Projects/ 2>/dev/null | wc -l | tr -d ' ')
echo "Files with #next: $NEXT_COUNT"

# Truth point — status.md (current state, updated in /end-day)
if [ -f "status.md" ]; then
  STATUS_AGE_LINE=$(grep -m1 "Updated" status.md || echo "")
  echo ""
  echo "status.md — current state ($STATUS_AGE_LINE):"
  awk '/^## Open loops/{p=1} p' status.md | sed 's/^/   /'
fi

# Last block header in Journal/log.md (no body — body is in the log, not the brief)
if [ -f "Journal/log.md" ]; then
  LAST_HEADER=$(grep -E "^## " Journal/log.md | tail -n 1)
  echo ""
  echo "Last block in Journal/log.md: $LAST_HEADER"
  echo "   (full log in Journal/log.md; brief shows only the header)"
fi

echo "---"
exit 0
