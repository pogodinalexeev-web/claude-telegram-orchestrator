#!/usr/bin/env python3
"""Розетка для своего кода: обработчики хозяина, которых ядро не знает.

Ядро одно на всех. Всё, что «только у меня» — тон, гейты, привычки — живёт в
хранилище хозяина: крюки, скиллы, файл личности. Но крюк Claude Code видит
только разговор с моделью, а не ход бота. Счётчик сообщений за день, своя
подсказка раз в сутки, своя пометка на входящем — крюком этого не сделать,
и человек шёл править общее ядро. Один такой обработчик нашёлся в форке
26.08.2026 (педагогическая подсказка после тридцатого сообщения за день) —
он и был причиной завести эту розетку.

Как устроено: папка `<хранилище>/.claude/bot-ext/`, в ней питоновские файлы
хозяина. Ядро зовёт из них две точки, обе необязательные:

    def on_prompt(text, ctx) -> str    перед отправкой модели
    def on_reply(text, ctx) -> str     перед отправкой человеку

Вернул не строку или упал — ядро берёт исходный текст и пишет строку в
журнал. Расширение не может уронить ход: это правило важнее любой его пользы.

`ctx` — словарь: uid, chat_id, source, profile, state_dir (папка под своё
состояние, создаётся сама), log (функция записи в журнал).

Про доверие: код из этой папки исполняется правами хозяина. Новой дыры тут
нет — крюки в соседней `.claude/hooks/` исполняются ровно так же, а папка
лежит в собственном хранилище хозяина, куда чужой не пишет по правам файлов.
Поэтому включателя в профиле нет: наличие папки и есть воля хозяина. Профиль
лежит в /etc и хозяину недоступен на запись — гейт там сделал бы розетку
бессмысленной, за каждой мелочью пришлось бы идти к root.
"""
import importlib.util
import sys
import traceback
from pathlib import Path

__all__ = ["load_extensions", "apply_point", "POINTS"]

POINTS = ("on_prompt", "on_reply")

_CACHE = None          # список загруженных модулей, грузим один раз за жизнь процесса


def _ext_dir(profile):
    raw = (profile.get("ext_dir") or "").strip()
    if raw:
        return Path(raw)
    return Path(profile["vault"]) / ".claude" / "bot-ext"


def load_extensions(profile, log=None, force=False):
    """Загружает *.py из папки расширений. Порядок — по имени файла, чтобы
    он был предсказуем, а не зависел от того, как файлы легли на диск."""
    global _CACHE
    if _CACHE is not None and not force:
        return _CACHE
    say = log or (lambda _m: None)
    mods = []
    d = _ext_dir(profile)
    try:
        files = sorted(p for p in d.glob("*.py") if not p.name.startswith("_"))
    except Exception:
        files = []
    for path in files:
        name = f"botext_{path.stem}"
        try:
            spec = importlib.util.spec_from_file_location(name, path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            spec.loader.exec_module(mod)
        except Exception:
            sys.modules.pop(name, None)
            say(f"  расширение {path.name}: не загрузилось — {traceback.format_exc(limit=1).strip()}")
            continue
        points = [p for p in POINTS if callable(getattr(mod, p, None))]
        if not points:
            say(f"  расширение {path.name}: ни одной известной точки, пропускаю")
            continue
        mods.append(mod)
        say(f"  расширение {path.name}: {', '.join(points)}")
    _CACHE = mods
    return mods


def apply_point(point, text, ctx, log=None):
    """Прогоняет текст через все расширения по цепочке. Любой сбой —
    возвращаем то, что было на входе в упавшее расширение, и идём дальше."""
    if point not in POINTS:
        raise ValueError(f"неизвестная точка расширения: {point!r}")
    say = log or (lambda _m: None)
    profile = ctx.get("profile")
    if profile is None:
        return text
    state = _ext_dir(profile) and ctx.get("state_dir")
    if state:
        try:
            Path(state).mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
    for mod in load_extensions(profile, log=say):
        fn = getattr(mod, point, None)
        if not callable(fn):
            continue
        try:
            out = fn(text, ctx)
        except Exception:
            say(f"  расширение {getattr(mod, '__name__', '?')}.{point} упало — беру текст как был")
            continue
        if isinstance(out, str):
            text = out
        elif out is not None:
            say(f"  расширение {getattr(mod, '__name__', '?')}.{point} вернуло не строку — игнорирую")
    return text
