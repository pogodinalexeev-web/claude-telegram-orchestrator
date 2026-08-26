#!/usr/bin/env bash
# sync-pull.sh — крюк UserPromptSubmit. Часть эталона /opt/claude-tg/deploy/sync-hooks/.
#
# Взять свежее до того, как человек заговорил. Порядок:
#   спасательный коммит несохранённого → fetch → быстрая перемотка →
#   слияние без правки → откат при конфликте.
#
# Спасательный коммит стоит ПЕРЕД fetch сознательно: если прошлый ход умер
# посередине, его правки висят в рабочей папке, и первое же слияние их затопчет.
# Коммит дёшев и обратим, потеря файла — нет.
set -u

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_sync-env.sh
source "$HOOK_DIR/_sync-env.sh" || exit 0

# Правило 4: чужое слияние не трогаем.
sync_busy && exit 0

emit_context() {
  # UserPromptSubmit умеет вернуть строку в контекст хода. Пользуемся ровно
  # дважды: личная отметка хозяина (если он её завёл) и крик про шумный журнал.
  local msg="$1"
  [ -n "$msg" ] || return 0
  command -v python3 >/dev/null 2>&1 || return 0
  MSG="$msg" python3 - <<'PYEOF' 2>/dev/null || true
import json, os
print(json.dumps({"hookSpecificOutput": {
    "hookEventName": "UserPromptSubmit",
    "additionalContext": os.environ["MSG"],
}}, ensure_ascii=False))
PYEOF
}

# --- 1. Спасательный коммит -------------------------------------------------
git add -A 2>/dev/null || true
if ! git diff --cached --quiet 2>/dev/null; then
  FILES=$(git diff --cached --name-only 2>/dev/null | wc -l | tr -d ' ')
  git commit --quiet -m "rescue: $SYNC_STAMP (${FILES} files)" 2>/dev/null || true
fi

# --- 2. Забрать свежее ------------------------------------------------------
if ! git fetch "$SYNC_REMOTE" "$SYNC_BRANCH" >/dev/null 2>&1; then
  sync_note_err "fetch $SYNC_REMOTE $SYNC_BRANCH failed"
  exit 0
fi

LOCAL=$(git rev-parse HEAD 2>/dev/null || echo "")
REMOTE=$(git rev-parse "$SYNC_REMOTE/$SYNC_BRANCH" 2>/dev/null || echo "")
[ -n "$LOCAL" ] && [ -n "$REMOTE" ] || exit 0

# --- 3. Свести, не перебазируя (правило 2) ----------------------------------
if [ "$LOCAL" != "$REMOTE" ]; then
  if ! git merge --ff-only "$SYNC_REMOTE/$SYNC_BRANCH" >/dev/null 2>&1; then
    if ! git merge --no-edit -m "auto-merge $SYNC_REMOTE/$SYNC_BRANCH (sync-pull)" "$SYNC_REMOTE/$SYNC_BRANCH" >/dev/null 2>&1; then
      # Правило 3: полуслияние не оставляем.
      git merge --abort >/dev/null 2>&1 || true
      sync_note_err "diverged from $SYNC_REMOTE/$SYNC_BRANCH, manual merge needed"
      exit 0
    fi
  fi
fi

# --- 4. Отдать своё ---------------------------------------------------------
AHEAD=$(git rev-list --count "$SYNC_REMOTE/$SYNC_BRANCH..HEAD" 2>/dev/null || echo 0)
if [ "${AHEAD:-0}" -gt 0 ]; then
  git push "$SYNC_REMOTE" "$SYNC_BRANCH" >/dev/null 2>&1 || sync_note_err "push to $SYNC_REMOTE/$SYNC_BRANCH failed"
fi

# --- 5. Сказать вслух -------------------------------------------------------
NOTE=""
PULSE_FILE="$SYNC_VAULT/.claude/hooks/sync-pulse.txt"
[ -r "$PULSE_FILE" ] && NOTE=$(cat "$PULSE_FILE" 2>/dev/null)
if ALARM=$(sync_daily_alarm); then
  if [ -n "$NOTE" ]; then NOTE="$NOTE"$'\n'"$ALARM"; else NOTE="$ALARM"; fi
fi
emit_context "$NOTE"

exit 0
