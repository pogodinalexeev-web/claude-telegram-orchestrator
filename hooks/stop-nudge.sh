#!/usr/bin/env bash
# Stop: hint at the end of Claude's response. Only fires if there's something to surface.

VAULT="${CLAUDE_PROJECT_DIR:-$(pwd)}"
cd "$VAULT" || exit 0

# Inbox grew large → remind about /process-inbox
if [ -f "inbox.md" ]; then
  LINES=$(wc -l < "inbox.md" | tr -d ' ')
  if [ "$LINES" -gt 50 ]; then
    echo "inbox.md is already $LINES lines. Run /process-inbox to sort into PARA."
  fi
fi

# No daily note today and it's working hours
HOUR=$(date '+%H')
TODAY=$(date '+%Y-%m-%d')
if [ ! -f "Journal/${TODAY}.md" ] && [ "$HOUR" -ge 8 ] && [ "$HOUR" -lt 22 ]; then
  echo "No daily note. Run /daily-prep to set top-3 for today."
fi

exit 0
