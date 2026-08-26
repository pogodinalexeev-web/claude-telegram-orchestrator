#!/usr/bin/env bash
# rollout-sync.sh — раскатка пакета синхронизации по стендам.
#
#   rollout-sync.sh <имя> [<имя>...]     сухой прогон (по умолчанию!)
#   rollout-sync.sh --apply <имя>...     сделать
#   rollout-sync.sh --all                все стенды, у кого есть /etc/claude-tg-<имя>
#
# Кладёт четыре файла эталона в <хранилище>/.claude/hooks/, заводит переходники
# со старых имён и точечно дописывает недостающие вызовы в settings.json.
#
# Чего НЕ делает никогда: не трогает файлы вне списка пакета, не переписывает
# settings.json целиком, не трогает профиль, не перезапускает юниты, не копирует
# чужие хранилища.
#
# Запускать от root. Соседние машины (ноут хозяина, его домашний компьютер)
# раскатывать не нужно: пакет лежит внутри хранилища и доедет туда обычной
# синхронизацией, тем самым git, который он и чинит.
set -u

ETALON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/sync-hooks"
PACKAGE=(_sync-env.sh sync-pull.sh sync-commit.sh sync-flush.sh)
STAMP=$(date '+%Y-%m-%d-%H%M%S')
APPLY=0
NAMES=()

# старое имя → новое; переходник заводится только если старое имя прописано
# в settings.json (иначе плодить нечего)
OLD_NEW=(
  "pull-lab.sh:sync-pull.sh"
  "auto-commit.sh:sync-commit.sh"
  "auto-commit-flush.sh:sync-flush.sh"
)

# новый файл → событие и отбор инструментов для settings.json
HOOK_EVENT_sync_pull="UserPromptSubmit:"
HOOK_EVENT_sync_commit="PostToolUse:Write|Edit|MultiEdit"
HOOK_EVENT_sync_flush="Stop:"

usage() { sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --apply)   APPLY=1 ;;
    --dry-run) APPLY=0 ;;
    --all)
      for d in /etc/claude-tg-*; do
        [ -d "$d" ] || continue
        NAMES+=("${d#/etc/claude-tg-}")
      done ;;
    -h|--help) usage ;;
    -*) echo "неизвестный ключ: $1" >&2; usage ;;
    *) NAMES+=("$1") ;;
  esac
  shift
done
[ ${#NAMES[@]} -gt 0 ] || usage

for f in "${PACKAGE[@]}"; do
  [ -r "$ETALON_DIR/$f" ] || { echo "нет эталона: $ETALON_DIR/$f" >&2; exit 2; }
done

[ "$APPLY" = 1 ] && echo "=== РАСКАТКА (--apply) ===" || echo "=== СУХОЙ ПРОГОН, ничего не меняю (--apply чтобы сделать) ==="
echo "эталон: $ETALON_DIR"
echo

# уникализируем имена, сохраняя порядок
declare -A _seen=()
UNIQ=()
for n in "${NAMES[@]}"; do
  [ -n "${_seen[$n]:-}" ] && continue
  _seen[$n]=1
  UNIQ+=("$n")
done

RC=0
for NAME in "${UNIQ[@]}"; do
  ERRORS=0
  SETTINGS_ADDED=0
  SHIMS=0

  # --- 1. жив ли стенд ------------------------------------------------------
  if ! id "$NAME" >/dev/null 2>&1; then
    echo "$NAME: пользователя нет — пропускаю"; RC=1; continue
  fi
  HOME_DIR=$(getent passwd "$NAME" | cut -d: -f6)
  PROFILE="/etc/claude-tg-$NAME/profile.json"
  VAULT=""
  if [ -r "$PROFILE" ]; then
    VAULT=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("vault",""))' "$PROFILE" 2>/dev/null || echo "")
  fi
  [ -n "$VAULT" ] || VAULT="$HOME_DIR/vault"
  if [ ! -d "$VAULT" ] || [ ! -e "$VAULT/.git" ]; then
    echo "$NAME: нет хранилища с git ($VAULT) — пропускаю"; RC=1; continue
  fi

  # --- 2. куда он синхронизируется (его же логикой) -------------------------
  DETECT=$(sudo -u "$NAME" env CLAUDE_PROJECT_DIR="$VAULT" TGBOT_PROFILE="$PROFILE" \
             bash -c "source '$ETALON_DIR/_sync-env.sh' && echo \"\$SYNC_REMOTE \$SYNC_BRANCH\"" 2>/dev/null || echo "")
  REMOTE=$(echo "$DETECT" | awk '{print $1}')
  BRANCH=$(echo "$DETECT" | awk '{print $2}')
  if [ -z "$REMOTE" ] || [ -z "$BRANCH" ]; then
    echo "$NAME: адрес синхронизации не определился — дальше не иду"; RC=1; continue
  fi

  HOOKS="$VAULT/.claude/hooks"
  SETTINGS="$VAULT/.claude/settings.json"

  # --- 3-4. бэкап и раскладка ----------------------------------------------
  for f in "${PACKAGE[@]}"; do
    dst="$HOOKS/$f"
    if [ "$APPLY" = 1 ]; then
      install -d -o "$NAME" -g "$NAME" -m 755 "$HOOKS" || { ERRORS=$((ERRORS+1)); continue; }
      [ -e "$dst" ] && cp -p "$dst" "$dst.bak-$STAMP"
      install -o "$NAME" -g "$NAME" -m 755 "$ETALON_DIR/$f" "$dst" || ERRORS=$((ERRORS+1))
    else
      if [ -e "$dst" ]; then
        cmp -s "$ETALON_DIR/$f" "$dst" && echo "   = $f (уже совпадает)" || echo "   ~ $f (заменю, бэкап .bak-$STAMP)"
      else
        echo "   + $f (положу)"
      fi
    fi
  done

  # --- 5. переходники со старых имён ---------------------------------------
  for pair in "${OLD_NEW[@]}"; do
    old="${pair%%:*}"; new="${pair##*:}"
    grep -q "$old" "$SETTINGS" 2>/dev/null || continue
    SHIMS=$((SHIMS+1))
    if [ "$APPLY" = 1 ]; then
      [ -e "$HOOKS/$old" ] && cp -p "$HOOKS/$old" "$HOOKS/$old.bak-$STAMP"
      cat > "$HOOKS/$old" <<SHIM
#!/usr/bin/env bash
# Переходник со старого имени. Настоящий крюк — $new (пакет синхронизации).
# Заведён раскатчиком $STAMP. Убрать после недели живой работы: поправить
# settings.json на новое имя и удалить этот файл.
exec bash "\$(cd "\$(dirname "\${BASH_SOURCE[0]}")" && pwd)/$new" "\$@"
SHIM
      chown "$NAME:$NAME" "$HOOKS/$old"; chmod 755 "$HOOKS/$old"
    else
      echo "   → переходник $old → $new (старое имя прописано в settings.json)"
    fi
  done

  # --- 6. точечная дописка вызовов в settings.json --------------------------
  if [ -e "$SETTINGS" ] || [ "$APPLY" = 1 ]; then
    OUT=$(SETTINGS="$SETTINGS" APPLY="$APPLY" STAMP="$STAMP" NAME="$NAME" python3 - <<'PY'
import json, os, shutil, sys
path = os.environ["SETTINGS"]
apply_ = os.environ["APPLY"] == "1"
want = [
    ("UserPromptSubmit", "",                     "sync-pull.sh"),
    ("PostToolUse",      "Write|Edit|MultiEdit", "sync-commit.sh"),
    ("Stop",             "",                     "sync-flush.sh"),
]
# старое имя ещё живёт в настройках и теперь ведёт на новый файл через
# переходник — второй вызов не нужен, иначе синхронизация пойдёт дважды
alias = {"sync-pull.sh": "pull-lab.sh",
         "sync-commit.sh": "auto-commit.sh",
         "sync-flush.sh": "auto-commit-flush.sh"}
try:
    data = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
except Exception as e:
    print(f"ОШИБКА чтения settings.json: {e}", file=sys.stderr)
    sys.exit(3)
hooks = data.setdefault("hooks", {})
added = 0
for event, matcher, script in want:
    groups = hooks.setdefault(event, [])
    blob = json.dumps(groups, ensure_ascii=False)
    if script in blob or alias[script] in blob:
        continue
    cmd = 'bash "$CLAUDE_PROJECT_DIR/.claude/hooks/%s"' % script
    for g in groups:
        if (g.get("matcher") or "") == matcher:
            g.setdefault("hooks", []).append({"type": "command", "command": cmd})
            break
    else:
        groups.append({"matcher": matcher,
                       "hooks": [{"type": "command", "command": cmd}]})
    added += 1
    print(f"   + settings.json: {event} → {script}")
if apply_ and added:
    if os.path.exists(path):
        shutil.copy2(path, path + ".bak-" + os.environ["STAMP"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    shutil.chown(path, os.environ["NAME"], os.environ["NAME"])
print(f"ADDED={added}")
PY
    ) || ERRORS=$((ERRORS+1))
    echo "$OUT" | grep -v '^ADDED=' | sed '/^$/d'
    SETTINGS_ADDED=$(echo "$OUT" | sed -n 's/^ADDED=//p')
    SETTINGS_ADDED=${SETTINGS_ADDED:-0}
  fi

  # --- 7. проверка вхолостую ------------------------------------------------
  if [ "$APPLY" = 1 ]; then
    for f in "${PACKAGE[@]}"; do
      bash -n "$HOOKS/$f" 2>/dev/null || { echo "   !! синтаксис $f"; ERRORS=$((ERRORS+1)); }
    done
    CHECK=$(sudo -u "$NAME" env CLAUDE_PROJECT_DIR="$VAULT" TGBOT_PROFILE="$PROFILE" \
              bash -c "source '$HOOKS/_sync-env.sh' && echo \"\$SYNC_REMOTE \$SYNC_BRANCH\"" 2>/dev/null || echo "")
    [ "$CHECK" = "$REMOTE $BRANCH" ] || { echo "   !! после раскладки адрес читается иначе: '$CHECK'"; ERRORS=$((ERRORS+1)); }
  fi

  # --- 8. строка отчёта -----------------------------------------------------
  echo "$NAME: склад=$REMOTE ветка=$BRANCH, файлов ${#PACKAGE[@]}, переходников $SHIMS, settings +$SETTINGS_ADDED, ошибок $ERRORS"
  echo
  [ "$ERRORS" = 0 ] || RC=1
done

exit $RC
