#!/usr/bin/env bash
# sync-flush.sh — крюк Stop. Часть эталона /opt/claude-tg/deploy/sync-hooks/.
#
# Один коммит на весь ход + отправка в склад. Сводим даже когда коммитить
# нечего: рабочая копия могла отстать, пока шёл длинный ответ.
set -u

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_sync-env.sh
source "$HOOK_DIR/_sync-env.sh" || exit 0

PENDING="$SYNC_VAULT/.git/sync-pending"
drop_pending() { rm "$PENDING" 2>/dev/null || true; }

sync_busy && exit 0

# --- 1. Коммит хода ---------------------------------------------------------
git add -A 2>/dev/null || true
COMMITTED=0
if ! git diff --cached --quiet 2>/dev/null; then
  FILES=$(git diff --cached --name-only 2>/dev/null | wc -l | tr -d ' ')
  if git commit --quiet -m "turn: $SYNC_STAMP (${FILES} files)" 2>/dev/null; then
    COMMITTED=1
  fi
fi

# --- 2. Свести со складом ---------------------------------------------------
if ! git fetch "$SYNC_REMOTE" "$SYNC_BRANCH" >/dev/null 2>&1; then
  sync_note_err "fetch $SYNC_REMOTE $SYNC_BRANCH failed (committed=$COMMITTED)"
  drop_pending
  exit 0
fi

if ! git merge --ff-only "$SYNC_REMOTE/$SYNC_BRANCH" >/dev/null 2>&1; then
  if ! git merge --no-edit -m "auto-merge $SYNC_REMOTE/$SYNC_BRANCH (sync-flush)" "$SYNC_REMOTE/$SYNC_BRANCH" >/dev/null 2>&1; then
    git merge --abort >/dev/null 2>&1 || true
    sync_note_err "merge with $SYNC_REMOTE/$SYNC_BRANCH failed (committed=$COMMITTED)"
    drop_pending
    exit 0
  fi
fi

# --- 3. Отдать --------------------------------------------------------------
git push "$SYNC_REMOTE" "$SYNC_BRANCH" >/dev/null 2>&1 || sync_note_err "push to $SYNC_REMOTE/$SYNC_BRANCH failed (committed=$COMMITTED)"

drop_pending
exit 0
