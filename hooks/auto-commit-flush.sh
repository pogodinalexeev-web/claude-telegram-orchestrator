#!/usr/bin/env bash
# Stop hook: one commit per Claude response (if anything changed) + push to bare.
# Paired with auto-commit.sh — that one only does git add -A, this one does commit+sync.
# Goal: instead of 5 small "auto: edit at 21:42" commits, one commit per turn.
#
# Sync block: working tree commit also propagates to bare remote (lab) so that
# the next push from another machine can fast-forward cleanly.

VAULT="${CLAUDE_PROJECT_DIR:-$(pwd)}"
cd "$VAULT" || exit 0

[ -d ".git" ] || exit 0

# Don't interfere during merge/rebase
[ -f ".git/MERGE_HEAD" ] && exit 0
[ -f ".git/REBASE_HEAD" ] && exit 0

# Safety: if PostToolUse hook didn't fire, stage everything anyway
git add -A 2>/dev/null

COMMITTED=0
if ! git diff --cached --quiet; then
  STAMP=$(date '+%Y-%m-%d %H:%M')
  FILES_CHANGED=$(git diff --cached --name-only | wc -l | tr -d ' ')
  if git commit --quiet -m "auto: turn at $STAMP (${FILES_CHANGED} files)" 2>/dev/null; then
    COMMITTED=1
  fi
fi

# Sync with bare remote (lab = ~/vault.git): pull fresh from remote, then push our commit.
# We sync even when nothing was committed — keeps working tree aligned.
LOG="$VAULT/.claude/scheduled/logs/auto-commit-flush.err"
mkdir -p "$(dirname "$LOG")" 2>/dev/null || true

SYNC_ERR=""
GIT_TERMINAL_PROMPT=0 git fetch lab main >>"$LOG" 2>&1 || true

if ! GIT_TERMINAL_PROMPT=0 git merge --ff-only lab/main >>"$LOG" 2>&1; then
  if ! GIT_TERMINAL_PROMPT=0 git merge --no-edit -m "auto-merge from remote (auto-commit-flush)" lab/main >>"$LOG" 2>&1; then
    GIT_TERMINAL_PROMPT=0 git merge --abort >/dev/null 2>&1 || true
    SYNC_ERR="merge with lab/main failed (manual)"
  fi
fi

if [ -z "$SYNC_ERR" ]; then
  GIT_TERMINAL_PROMPT=0 git push lab main >>"$LOG" 2>&1 || SYNC_ERR="push to bare failed"
fi

if [ -n "$SYNC_ERR" ]; then
  date '+%Y-%m-%d %H:%M' >> "$LOG"
  echo "sync_err: $SYNC_ERR (committed=$COMMITTED)" >> "$LOG"
else
  # Truncate on success — log only holds unresolved divergences
  : > "$LOG"
fi

# Clean up marker
rm -f ".git/auto-commit-pending"

exit 0
