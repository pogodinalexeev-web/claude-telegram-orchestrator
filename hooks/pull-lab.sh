#!/usr/bin/env bash
# pull-lab.sh — UserPromptSubmit hook: sync vault between machines before each user prompt.
#
# On every message: commit (if anything unsaved) + fetch + ff/merge + push (if ahead).
# Goal: don't depend on the Stop hook. Any uncommitted edits from the last session
# get picked up by a "rescue" commit at the start of the next message.
#
# - Never blocks the prompt; all errors are silent.
# - Doesn't interfere during merge/rebase in progress.
# - No auto-rebase; resolves via merge --ff-only → merge --no-edit → abort+marker.
set -u
VAULT_ROOT="${CLAUDE_PROJECT_DIR:-$HOME/vault}"
cd "$VAULT_ROOT" || exit 0

emit_pulse() {
  python3 -c "import json; print(json.dumps({'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit', 'additionalContext': 'PULL-LAB PULSE. In the hook-names block (before main response, see simple-language-gate) add [P]. Vault synced with remote. Current turn only.'}}, ensure_ascii=False))"
}

git remote get-url lab >/dev/null 2>&1 || exit 0

# Don't interfere if merge/rebase is in progress
if [ -d .git/rebase-merge ] || [ -d .git/rebase-apply ] || [ -f .git/MERGE_HEAD ]; then
  exit 0
fi

ERR_FILE=".claude/scheduled/logs/vault-pull.err"
STAMP=$(date '+%Y-%m-%d %H:%M')

# 1) Rescue commit: if working tree is dirty — commit everything in one line.
#    Removes dependency on whether the Stop hook fired in the last session.
git add -A 2>/dev/null
if ! git diff --cached --quiet; then
  FILES=$(git diff --cached --name-only | wc -l | tr -d ' ')
  git commit --quiet -m "auto: rescue at $STAMP (${FILES} files)" 2>/dev/null || true
fi

export GIT_SSH_COMMAND="ssh -o ConnectTimeout=5 -o ServerAliveInterval=3 -o ServerAliveCountMax=2 -o BatchMode=yes"
git fetch lab main >/dev/null 2>&1 || exit 0

LOCAL=$(git rev-parse HEAD 2>/dev/null)
REMOTE=$(git rev-parse lab/main 2>/dev/null)
[ -z "$LOCAL" ] || [ -z "$REMOTE" ] && exit 0

# 2) Merge with remote if diverged.
if [ "$LOCAL" != "$REMOTE" ]; then
  if ! git merge --ff-only lab/main >/dev/null 2>&1; then
    if ! git merge --no-edit -m "auto-merge lab/main (pull-lab hook)" lab/main >/dev/null 2>&1; then
      git merge --abort >/dev/null 2>&1 || true
      echo "$STAMP" > "$ERR_FILE" 2>/dev/null || true
      echo "diverged from lab/main, manual merge needed" >> "$ERR_FILE" 2>/dev/null || true
      exit 0
    fi
  fi
fi

# 3) Push to remote if there are local commits ahead.
AHEAD=$(git rev-list --count lab/main..HEAD 2>/dev/null || echo 0)
if [ "${AHEAD:-0}" -gt 0 ]; then
  git push lab main >/dev/null 2>&1 || true
fi

# Success — clear the error marker.
: > "$ERR_FILE" 2>/dev/null || true
emit_pulse
exit 0
