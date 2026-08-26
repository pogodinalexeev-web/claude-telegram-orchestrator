#!/usr/bin/env python3
"""Общая обвязка тестов: где лежит цель и какой профиль ей подсунуть.

С шага 4 ядро при импорте читает профиль из /etc/claude-tg-<пользователь>/profile.json.
В песочнице такого файла нет (в /etc мы не лезем), поэтому тесты подставляют
заготовку `deploy/profile.example.json` рядом с кодом через TGBOT_PROFILE.

Запуск тестов:
    cd /home/owner/bot-src/tests
    TGBOT_PATH=/home/owner/bot-src/tg-bot.py python3 -m unittest test_pure test_seams
"""
import importlib.util
import os
import sys
from pathlib import Path

# По умолчанию целимся в ядро, рядом с которым лежат сами тесты. Раньше здесь
# стоял живой файл — и запуск из песочницы молча проверял ЖИВОЙ монолит: 17
# «падений», которых в песочнице нет (26.08.2026). Живой файл проверяем явно:
#   TGBOT_PATH=/home/owner/tg-bot.py python3 tests/test_seams.py
_SANDBOX_CORE = Path(__file__).resolve().parent.parent / "tg-bot.py"
TARGET = Path(os.environ.get("TGBOT_PATH", str(_SANDBOX_CORE))).resolve()
FALLBACK_PROFILE = TARGET.parent / "deploy" / "profile.example.json"

if "TGBOT_PROFILE" not in os.environ and FALLBACK_PROFILE.exists():
    os.environ["TGBOT_PROFILE"] = str(FALLBACK_PROFILE)

# С распила 19.08 ядро — не один файл, а дюжина модулей рядом. Чтобы `import
# botctx` из tg-bot.py сработал при загрузке по пути, папку ядра кладём в путь
# поиска сами (когда ядро запускают как скрипт, питон делает это за нас).
if str(TARGET.parent) not in sys.path:
    sys.path.insert(0, str(TARGET.parent))

SRC = TARGET.read_text(encoding="utf-8")

# Все файлы ядра. Тесты, которые ищут что-то грепом по исходнику (следы
# личных данных, шаблоны маркеров), обязаны смотреть их ВСЕ — иначе распил
# на модули превращает проверку в пустую: код уехал, грепать стало нечего.
CORE_FILES = sorted(p for p in TARGET.parent.glob("*.py"))
CORE_SRC = {p.name: p.read_text(encoding="utf-8") for p in CORE_FILES}
ALL_SRC = "\n".join(CORE_SRC.values())


def pin_target():
    """Загоняет в sys.modules модули ИМЕННО той сборки, на которую целятся тесты.

    Зачем: botctx на импорте вставляет в начало пути поиска `resident_module_dir`
    из профиля. После переезда 26.08.2026 это `/opt/claude-tg` — и всё, что
    импортировалось следом (prompt, turn, vaultio...), приезжало из ЖИВОГО ядра,
    а не из песочницы. Тесты при этом зеленели: они проверяли не тот код, который
    только что правили. Ловушка того же рода, что и в шапке файла, только злее —
    там целился один файл, здесь расходились двенадцать.

    Лечение: грузим модули по пути в порядке зависимостей и после каждого
    возвращаем свою папку в начало пути.
    """
    order = ["botprofile", "resident_claude", "botctx", "markers", "tgapi", "prompt", "vaultio",
             "media", "voice", "claude_run", "menu", "botext",
             "intake", "turn"]
    here = str(TARGET.parent)
    for name in order:
        path = TARGET.parent / f"{name}.py"
        if not path.exists() or name in sys.modules:
            continue
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        try:
            spec.loader.exec_module(mod)
        except Exception:
            del sys.modules[name]
            raise
        finally:
            # botctx (и не он один) мог переставить путь поиска под себя
            if sys.path[0] != here:
                sys.path.insert(0, here)


def stray_modules():
    """Модули ядра, приехавшие не из целевой сборки. Пусто — значит честно."""
    here = str(TARGET.parent)
    stray = {}
    for name in sorted(CORE_SRC):
        mod = sys.modules.get(name[:-3])
        f = getattr(mod, "__file__", None)
        if mod is not None and f and not f.startswith(here):
            stray[name[:-3]] = f
    return stray


def load(module_name):
    """Импортирует ядро по пути (а не по имени) — чтобы можно было целиться
    и в живой файл, и в песочницу."""
    spec = importlib.util.spec_from_file_location(module_name, TARGET)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pin_target()
