#!/usr/bin/env bash
# sync-commit.sh — крюк PostToolUse (Write|Edit|MultiEdit). Часть эталона.
#
# Только откладывает правки в корзину (git add). Коммит — один на весь ход,
# его делает sync-flush.sh. Иначе один ответ даёт пять коммитов и журнал
# превращается в кашу.
set -u

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_sync-env.sh
source "$HOOK_DIR/_sync-env.sh" || exit 0

sync_busy && exit 0

git add -A 2>/dev/null || true

# Метка «есть несохранённое» — на случай, если крюк конца хода не сработает.
# Тогда её подберёт спасательный коммит следующего sync-pull.sh.
if ! git diff --cached --quiet 2>/dev/null; then
  touch "$SYNC_VAULT/.git/sync-pending" 2>/dev/null || true
fi

exit 0
