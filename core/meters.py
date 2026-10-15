"""meters.py — расход подписки и заполнение беседы для пульта (08.10.2026).

Слово хозяина стенда 08.10.2026 17:40: «можем видеть, сколько осталось токенов… полоску создать,
в этот пульт её добавить… в кодексе, в коде, везде»; 17:43: «контекст… если нельзя
отдельной кнопкой, то в пульт».

Откуда цифры:
- Клод: в потоке ответа на каждый ход приходит событие `rate_limit_event`, внутри
  `unifiedWindows.five_hour` и `seven_day` с `utilization` (0..1) и `resetsAt`.
  Заполнение беседы считает `claude_run` (ctx), размер окна приходит в
  `result.modelUsage.<модель>.contextWindow`.
- Кодекс: переходник лимитов не отдаёт, зато Кодекс сам пишет их в журнал беседы
  `~/.codex/sessions/<гггг>/<мм>/<дд>/*.jsonl` (`rate_limits.primary`) вместе
  с `model_context_window`. Читаем самый свежий журнал при открытии пульта.
Всё последнее известное лежит в `<cache_dir>/meters.json`.
"""
import json
import re
import time
from pathlib import Path

from botctx import CACHE_DIR, log

METERS_FILE = CACHE_DIR / "meters.json"
CODEX_SESSIONS = Path.home() / ".codex" / "sessions"


def _load():
    try:
        return json.loads(METERS_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _save(data):
    tmp = METERS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False))
    tmp.replace(METERS_FILE)


def note_claude_limits(info):
    """Событие rate_limit_event из потока Клода."""
    try:
        wins = (info or {}).get("unifiedWindows") or {}
        if not wins:
            return
        data = _load()
        data["claude"] = {k: {"used": float(v.get("utilization") or 0),
                              "resets": int(v.get("resetsAt") or 0)}
                          for k, v in wins.items() if isinstance(v, dict)}
        data["claude_at"] = int(time.time())
        _save(data)
    except Exception as e:  # счётчик не должен ронять ход
        log(f"meters claude error: {e}")


def note_context(chat_id, eng, tokens, window=0):
    """Заполнение беседы после хода."""
    if not chat_id or not tokens:
        return
    try:
        data = _load()
        data.setdefault("ctx", {})[str(chat_id)] = {
            "engine": eng, "tokens": int(tokens), "window": int(window or 0),
            "at": int(time.time())}
        _save(data)
    except Exception as e:
        log(f"meters ctx error: {e}")


def _codex_latest(root=None):
    """(лимиты, окно) из самого свежего журнала Кодекса или (None, 0)."""
    root = root or CODEX_SESSIONS
    try:
        files = sorted(root.glob("*/*/*/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return None, 0
    window = 0
    for f in files[:5]:
        try:
            lines = f.read_text(errors="replace").splitlines()
        except OSError:
            continue
        if not window:
            wins = re.findall(r'"model_context_window":\s*(\d+)', "\n".join(lines))
            window = int(wins[-1]) if wins else 0
        for ln in reversed(lines):
            if '"rate_limits"' not in ln:
                continue
            try:
                rl = _find_key(json.loads(ln), "rate_limits")
            except ValueError:
                continue
            prim = (rl or {}).get("primary") or {}
            if prim:
                return {"week": {"used": float(prim.get("used_percent") or 0) / 100,
                                 "resets": int(prim.get("resets_at") or 0),
                                 "minutes": int(prim.get("window_minutes") or 0)},
                        "plan": rl.get("plan_type") or "",
                        "at": int(f.stat().st_mtime)}, window
    return None, window


def _find_key(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = _find_key(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_key(v, key)
            if r is not None:
                return r
    return None


def snapshot(chat_id, codex=False):
    """Всё для пульта: {'claude': {...}, 'codex': {...}|None, 'ctx': {...}|None}."""
    data = _load()
    out = {"claude": data.get("claude") or {}, "claude_at": data.get("claude_at") or 0,
           "codex": None, "ctx": (data.get("ctx") or {}).get(str(chat_id))}
    if codex:
        lim, window = _codex_latest()
        out["codex"] = lim
        ctx = out["ctx"]
        if ctx and ctx.get("engine") == "codex" and not ctx.get("window"):
            ctx["window"] = window
    return out
