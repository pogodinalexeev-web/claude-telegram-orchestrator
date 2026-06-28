#!/usr/bin/env bash
# PreCompact: backup transcript before Claude Code compresses the context.
# Compression loses detail; this hook saves the full transcript in case of "go back and look".

VAULT="${CLAUDE_PROJECT_DIR:-$(pwd)}"
BACKUP_DIR="$VAULT/.claude/backups"
mkdir -p "$BACKUP_DIR"

if [ -n "$CLAUDE_TRANSCRIPT_PATH" ] && [ -f "$CLAUDE_TRANSCRIPT_PATH" ]; then
  cp "$CLAUDE_TRANSCRIPT_PATH" "$BACKUP_DIR/transcript-$(date +%s).jsonl" 2>/dev/null || true
fi

# Clean up old backups (>14 days)
find "$BACKUP_DIR" -type f -mtime +14 -delete 2>/dev/null || true

exit 0
