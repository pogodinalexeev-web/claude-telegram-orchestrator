#!/usr/bin/env bash
# _sync-env.sh — общая среда пакета синхронизации. Часть эталона /opt/claude-tg/deploy/sync-hooks/.
#
# Сам ничего не делает: подключается через `source` из трёх крюков-соседей.
# Возвращает 1, если синхронизировать не с чем — вызывающий крюк тогда тихо выходит.
#
# Определение адреса — три источника в строгом порядке (спека 26.08.2026, §2):
#   1. переменные среды SYNC_REMOTE / SYNC_BRANCH        — ручное вмешательство, старше всего
#   2. профиль /etc/claude-tg-<кто>/profile.json          — явная воля хозяина
#   3. опрос самого git (перебор lab → origin, текущая ветка)
#
# Зашитого имени склада здесь нет и быть не может: адрес — свойство стенда, а не кода.
# Ровно на этом три месяца молча падала синхронизация у бота Alpha (792 записи в
# журнале ошибок с 26.05 по 26.08.2026, никто в журнал не смотрел).

set -u

# --- 1. Где хранилище -------------------------------------------------------
SYNC_VAULT="${CLAUDE_PROJECT_DIR:-$PWD}"
cd "$SYNC_VAULT" 2>/dev/null || return 1
[ -e .git ] || return 1

# --- 2. Среда git -----------------------------------------------------------
# HOME не зашиваем: берём из среды, подставляем только если пусто (правило 6).
[ -n "${HOME:-}" ] || HOME=$(eval echo "~$(id -un)")
export HOME
export GIT_TERMINAL_PROMPT=0
export GIT_SSH_COMMAND="${GIT_SSH_COMMAND:-ssh -o ConnectTimeout=5 -o ServerAliveInterval=3 -o ServerAliveCountMax=2 -o BatchMode=yes}"

# --- 3. Куда синхронизировать ----------------------------------------------
_sync_from_profile() {
  # Профиль лежит рядом с ключами хозяина. На Mac его нет — это норма.
  local pf="${TGBOT_PROFILE:-/etc/claude-tg-$(id -un)/profile.json}"
  [ -r "$pf" ] || return 1
  command -v python3 >/dev/null 2>&1 || return 1
  python3 - "$pf" <<'PYEOF' 2>/dev/null
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
print((d.get("git_remote") or "").strip())
print((d.get("git_branch") or "").strip())
PYEOF
}

SYNC_REMOTE="${SYNC_REMOTE:-}"
SYNC_BRANCH="${SYNC_BRANCH:-}"

if [ -z "$SYNC_REMOTE" ] || [ -z "$SYNC_BRANCH" ]; then
  _pf_out=$(_sync_from_profile) || _pf_out=""
  if [ -n "$_pf_out" ]; then
    _pf_remote=$(printf '%s\n' "$_pf_out" | sed -n 1p)
    _pf_branch=$(printf '%s\n' "$_pf_out" | sed -n 2p)
    [ -z "$SYNC_REMOTE" ] && SYNC_REMOTE="${_pf_remote:-}"
    [ -z "$SYNC_BRANCH" ] && SYNC_BRANCH="${_pf_branch:-}"
  fi
fi

# Автоопределение — только там, где воля не выражена.
if [ -z "$SYNC_REMOTE" ]; then
  for _cand in lab origin; do
    if git remote get-url "$_cand" >/dev/null 2>&1; then
      SYNC_REMOTE="$_cand"
      break
    fi
  done
fi
if [ -z "$SYNC_BRANCH" ]; then
  _cur=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
  # HEAD означает отсоединённое состояние — синхронизировать нечего.
  [ "$_cur" = "HEAD" ] && _cur=""
  SYNC_BRANCH="$_cur"
fi

[ -n "$SYNC_REMOTE" ] && [ -n "$SYNC_BRANCH" ] || return 1
git remote get-url "$SYNC_REMOTE" >/dev/null 2>&1 || return 1
export SYNC_VAULT SYNC_REMOTE SYNC_BRANCH

# --- 4. Журнал ошибок -------------------------------------------------------
SYNC_LOG_DIR="$SYNC_VAULT/.claude/scheduled/logs"
SYNC_ERR="$SYNC_LOG_DIR/vault-sync.err"
mkdir -p "$SYNC_LOG_DIR" 2>/dev/null || true
export SYNC_LOG_DIR SYNC_ERR

SYNC_STAMP=$(date '+%Y-%m-%d %H:%M')
SYNC_TODAY=$(date '+%Y-%m-%d')
export SYNC_STAMP SYNC_TODAY

# --- 5. Служебное -----------------------------------------------------------
# Правило 4: идёт слияние или перебазирование — не лезем, пусть доразрулится.
sync_busy() {
  [ -d "$SYNC_VAULT/.git/rebase-merge" ] && return 0
  [ -d "$SYNC_VAULT/.git/rebase-apply" ] && return 0
  [ -f "$SYNC_VAULT/.git/MERGE_HEAD" ] && return 0
  [ -f "$SYNC_VAULT/.git/REBASE_HEAD" ] && return 0
  return 1
}

sync_note_err() {
  # $1 — строка причины. Журнал растёт, но не бесконечно: держим последние 500 строк.
  {
    echo "$SYNC_STAMP $1"
  } >> "$SYNC_ERR" 2>/dev/null || true
  if [ -f "$SYNC_ERR" ]; then
    _lines=$(wc -l < "$SYNC_ERR" 2>/dev/null | tr -d ' ')
    if [ "${_lines:-0}" -gt 500 ]; then
      tail -n 400 "$SYNC_ERR" > "$SYNC_ERR.tmp" 2>/dev/null && mv "$SYNC_ERR.tmp" "$SYNC_ERR" 2>/dev/null || true
    fi
  fi
}

# Правило 7: журнал, в который никто не смотрит, — это тишина, а не отчёт.
# Больше 50 записей за сутки — один раз в сутки говорим хозяину вслух.
sync_daily_alarm() {
  [ -f "$SYNC_ERR" ] || return 1
  local today_count seen_file
  today_count=$(grep -c "^$SYNC_TODAY " "$SYNC_ERR" 2>/dev/null || echo 0)
  [ "${today_count:-0}" -gt 50 ] || return 1
  seen_file="$SYNC_LOG_DIR/.sync-alarm-seen"
  [ "$(cat "$seen_file" 2>/dev/null)" = "$SYNC_TODAY" ] && return 1
  echo "$SYNC_TODAY" > "$seen_file" 2>/dev/null || true
  printf 'синхронизация хранилища сыплет ошибками: %s записей за сегодня в %s — посмотри, что там' \
    "$today_count" "$SYNC_ERR"
  return 0
}
