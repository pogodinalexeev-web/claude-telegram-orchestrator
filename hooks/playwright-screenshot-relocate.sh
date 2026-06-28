#!/bin/bash
# PostToolUse hook for mcp__playwright__browser_take_screenshot.
# Moves .png files created by playwright in the vault root
# to Resources/attachments/playwright/YYYY-MM-DD-<original>.png.
# Keeps the vault root clean after browser screenshot sessions.

set -euo pipefail

VAULT_ROOT="${CLAUDE_PROJECT_DIR:-$HOME/vault}"
ATTACH_DIR="$VAULT_ROOT/Resources/attachments/playwright"
DATE="$(date +%Y-%m-%d)"

[[ -d "$VAULT_ROOT" ]] || exit 0
mkdir -p "$ATTACH_DIR"

# Find .png files created in the last 60 seconds directly in vault root (not subdirs).
shopt -s nullglob
moved=0
for f in "$VAULT_ROOT"/*.png; do
  mtime_age=$(( $(date +%s) - $(stat -f %m "$f" 2>/dev/null || stat -c %Y "$f" 2>/dev/null || echo 0) ))
  if (( mtime_age <= 60 )); then
    base="$(basename "$f")"
    target="$ATTACH_DIR/${DATE}-${base}"
    mv "$f" "$target"
    moved=$((moved+1))
  fi
done

if (( moved > 0 )); then
  echo "[playwright-screenshot-relocate] moved $moved → $ATTACH_DIR" >&2
fi

exit 0
