"""engine.py — переключатель движка бота: Клод или Кодекс (08.10.2026).

Слово хозяина 08.10.2026 15:00–15:07: у него и у двух соседних хозяев должна быть возможность
уйти на Кодекс, когда у Клода кончились лимиты, руками и сама. Остальные боты живут
на Клоде, их это не касается.

Как устроено:
- Кодекс есть только у того, у кого в доме лежит `bot-codex/codex-bridge.py`
  (переходник, который притворяется `claude` и гонит ход в `codex app-server`).
  Нет переходника — движок всегда Клод, поведение ядра не меняется ни на байт.
- Выбор движка лежит в `<cache_dir>/engine.json`: {"engine", "until", "reason"}.
  `until` (unix-время) ставит только автоматика по лимиту: время прошло, и следующий
  ход снова идёт на Клоде. Ручной `/codex` без срока, держится до `/claude`.
- Беседы раздельные: Клод в `sessions.json`, Кодекс в `sessions.codex.json`.
  Вернулся на Клода, и он продолжает свою беседу с того места, где ушёл.
- Движок хода закрепляется на поток (`pin`) в начале хода, чтобы смена флажка
  посреди хода (чужой ход, истёкший срок) не увела sid в чужой файл бесед.
"""
import json
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from botctx import CACHE_DIR, CLAUDE_BIN, PROFILE, log

MSK = ZoneInfo(PROFILE.get("tz_iana") or "UTC")   # пояс хозяина из профиля (у исходного стенда Москва); имя оставлено ради старых вызовов
STATE_FILE = CACHE_DIR / "engine.json"
SESSIONS = {"claude": CACHE_DIR / "sessions.json",
            "codex": CACHE_DIR / "sessions.codex.json"}
# Запас после объявленного сброса: лимит снимается не секунда в секунду.
RESET_SLACK_SEC = 120
# Не разобрали время сброса — пробуем Клода снова через час.
UNKNOWN_RESET_SEC = 3600

_tls = threading.local()
_lock = threading.Lock()


def _bridge():
    return Path(PROFILE["home"]) / "bot-codex" / "codex-bridge.py"


def codex_available():
    try:
        return _bridge().is_file()
    except Exception:
        return False


def _read_state():
    try:
        st = json.loads(STATE_FILE.read_text())
        if st.get("engine") in SESSIONS:
            return st
    except Exception:
        pass
    return {"engine": "claude"}


def _write_state(st):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False))
    tmp.replace(STATE_FILE)


def stored():
    """Движок по флажку, без перевода срока. Без переходника всегда Клод."""
    if not codex_available():
        return "claude"
    return _read_state()["engine"]


def set_engine(name, until=None, reason="manual"):
    with _lock:
        st = {"engine": name, "reason": reason, "set_at": time.time()}
        if until:
            st["until"] = float(until)
        _write_state(st)
    log(f"engine → {name} ({reason}{', до ' + fmt_hhmm(until) if until else ''})")


def begin_turn():
    """Решить движок на этот ход. Возвращает (движок, заметка_хозяину, подсказка_модели).
    Срок автоматики истёк — переводим обратно на Клода и говорим об этом один раз."""
    if not codex_available():
        return "claude", "", ""
    with _lock:
        st = _read_state()
        until = st.get("until")
        if st["engine"] == "codex" and until and time.time() >= float(until):
            since = st.get("set_at")
            _write_state({"engine": "claude", "reason": "limit-reset", "set_at": time.time()})
            log("engine → claude (срок лимита истёк)")
            note = "↩️ Лимит Клода сброшен, снова отвечает Клод."
            hint = ("[Пока у Клода был лимит"
                    + (f" (с {fmt_hhmm(since)} до {fmt_hhmm(until)} по Москве)" if since else "")
                    + ", в этом чате отвечал Кодекс. Ты этих ходов не видел. Если по смыслу "
                    "нужно знать, что там было, загляни в сегодняшний лог переписки, "
                    "а хозяину не пересказывай это служебное.]\n\n")
            return "claude", note, hint
        return st["engine"], "", ""


CODEX_INTRO = ("[Это первый ход Кодекса в этом чате: до этого отвечал Клод, его разговор "
               "ты не видел. Если по смыслу нужен прежний контекст, загляни в сегодняшний "
               "лог переписки, а хозяину это служебное не пересказывай.]\n\n")


def pin(name):
    _tls.engine = name


def unpin():
    _tls.engine = None


def current():
    """Движок текущего хода (закреплённый на поток), иначе по флажку."""
    return getattr(_tls, "engine", None) or stored()


def sessions_file():
    return SESSIONS.get(current(), SESSIONS["claude"])


def bin_and_settings(name):
    """Чем запускать и откуда брать модель для движка."""
    if name == "codex" and codex_available():
        return str(_bridge()), str(_bridge().with_name("model.json"))
    return CLAUDE_BIN, PROFILE["settings_json"]


def fmt_hhmm(ts):
    try:
        return datetime.fromtimestamp(float(ts), MSK).strftime("%H:%M")
    except Exception:
        return "?"


# ---------------------------------------------------------------------------
# Отказ Клода по лимиту.
# Живые образцы (логи переписки, сентябрь–октябрь 2026):
#   You've hit your session limit · resets 5pm (<пояс аккаунта>)
#   You've hit your limit · resets 2:50am (<пояс аккаунта>)
#   You've hit your weekly limit · resets Oct 8, 12am (<пояс аккаунта>)
# Старый формат CLI: Claude AI usage limit reached|<unix-время>

_LIMIT_RE = re.compile(r"^\s*You['’]ve hit your (?:[\w-]+ )?limit\b", re.I)
_OLD_RE = re.compile(r"^\s*Claude AI usage limit reached\|(\d{9,})")
_RESET_RE = re.compile(
    r"resets\s+(?:(?P<mon>[A-Z][a-z]{2})\s+(?P<day>\d{1,2}),\s*)?"
    r"(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>am|pm)"
    r"(?:\s*\((?P<tz>[^)]+)\))?", re.I)
_MONTHS = {m: i for i, m in enumerate(
    "jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}


def limit_until(reply, now=None):
    """Отказ по лимиту → unix-время, когда пробовать Клода снова. Иначе None.
    Ловим только ответ, который целиком и есть отказ (короткий, с начала),
    чтобы цитата этой фразы в обычном ответе не переключала движок."""
    text = (reply or "").strip()
    if not text or len(text) > 300:
        return None
    now = now or time.time()
    m = _OLD_RE.match(text)
    if m:
        return float(m.group(1)) + RESET_SLACK_SEC
    if not _LIMIT_RE.match(text):
        return None
    r = _RESET_RE.search(text)
    if not r:
        return now + UNKNOWN_RESET_SEC
    try:
        tz = ZoneInfo(r.group("tz").strip()) if r.group("tz") else MSK
    except Exception:
        tz = MSK
    h = int(r.group("h")) % 12 + (12 if r.group("ap").lower() == "pm" else 0)
    mi = int(r.group("m") or 0)
    base = datetime.fromtimestamp(now, tz)
    if r.group("mon"):
        mon = _MONTHS.get(r.group("mon").lower())
        if not mon:
            return now + UNKNOWN_RESET_SEC
        at = base.replace(month=mon, day=int(r.group("day")), hour=h, minute=mi,
                          second=0, microsecond=0)
        if at.timestamp() < now - 86400:
            at = at.replace(year=at.year + 1)
    else:
        at = base.replace(hour=h, minute=mi, second=0, microsecond=0)
        if at.timestamp() <= now:
            at += timedelta(days=1)
    return at.timestamp() + RESET_SLACK_SEC
