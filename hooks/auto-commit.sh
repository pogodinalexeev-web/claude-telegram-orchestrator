#!/usr/bin/env bash
# PostToolUse (Write|Edit|MultiEdit): only stages changes.
# The actual commit is made once in the Stop hook (auto-commit-flush.sh) — so that
# one Claude response produces one commit, not N commits.
# Reduces git log noise and avoids races with scheduled tasks.

VAULT="${CLAUDE_PROJECT_DIR:-$(pwd)}"
cd "$VAULT" || exit 0

[ -d ".git" ] || exit 0

# Don't interfere during merge/rebase
[ -f ".git/MERGE_HEAD" ] && exit 0
[ -f ".git/REBASE_HEAD" ] && exit 0

git add -A 2>/dev/null

# Mark that there are unstaged changes (in case Stop hook doesn't fire)
if ! git diff --cached --quiet; then
  touch ".git/auto-commit-pending"
fi

exit 0
