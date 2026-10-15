#!/usr/bin/env python3
"""Общий контекст бота: профиль, пути, лимиты, журнал и живое состояние чатов.

Это фундамент всей раскладки — модуль, который не зависит ни от чего своего,
кроме загрузчика профиля. Все остальные модули берут пути и настройки отсюда,
а не друг у друга: так не возникает кольцевых импортов (модуль A ждёт B, B ждёт A).

Здесь же собраны ВСЕ изменяемые словари состояния (их тринадцать). Раньше они
были рассыпаны по монолиту — резидент рядом с потоком, буфер альбомов рядом с
разбором вложений, состояние меню в конце файла. Ключ у всех один и тот же —
chat_id, живут они ровно столько, сколько живёт процесс, и чинить гонку удобнее,
когда весь этот слой лежит в одном месте и виден целиком.

С 08.10.2026 ключ словарей — беседа (ckey): без темы Telegram это тот же
chat_id, с темой пара (chat_id, тема). Устройство в блоке «ТЕМЫ» ниже.
"""
import json
import re
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path


# ============================================================================
# ПРОФИЛЬ — всё, что отличает одного хозяина от другого.
# Шаг 4 пересборки (19.08.2026): значения уехали в файл
# /etc/claude-tg-<пользователь>/profile.json, здесь остался только вызов
# загрузчика. Имя пользователя берётся из системы, не из аргументов.
# Правило: ниже по файлу не должно появляться ни одного «/home/<имя>»,
# ни одного uid и ни одного @username — только чтение из PROFILE.
# ============================================================================
sys.path.insert(0, str(Path(__file__).resolve().parent))
from botprofile import ProfileError, load_profile  # noqa: E402  (модуль лежит рядом с ядром)

PROFILE = load_profile()

sys.path.insert(0, PROFILE["resident_module_dir"])
from resident_claude import ResidentClaude

_SECRETS = PROFILE["secrets_dir"]
TOKEN_FILE = f"{_SECRETS}/token"
ALLOW_FILE = f"{_SECRETS}/allowlist"
CACHE_DIR = Path(PROFILE["cache_dir"])
OFFSET_FILE = CACHE_DIR / "offset"
SESSIONS_FILE = CACHE_DIR / "sessions.json"
VAULT = Path(PROFILE["vault"])
ATTACH = VAULT / "Resources" / "attachments"
INBOX = VAULT / "inbox.md"
DO_QUEUE = VAULT / "Tasks" / "do-queue.md"
# Browser-automation commands routed to do-queue (Mac executes via CDP).
# Match: action-verb + Avito/Ozon (in either order, within ~40 chars).
COMMAND_RE = re.compile(
    r"\b(найди|найти|ищи|искать|купи|купить|положи|закажи|заказать|открой|открыть|добавь|добавить|свяжись|напиши|напишу)\b.{0,60}\b(авито|озон|ozon|avito)\b"
    r"|\b(авито|озон|ozon|avito)\b.{0,60}\b(найди|найти|ищи|искать|купи|купить|положи|закажи|заказать|открой|открыть|добавь|добавить|свяжись|напиши)\b",
    re.IGNORECASE,
)
PENDING_FILE = CACHE_DIR / "pending-attachments.json"
MANIFEST_DIR = CACHE_DIR / "manifests"
VOICE_ARCHIVE_DIR = CACHE_DIR / "voice-archive"
VOICE_ARCHIVE_TTL_DAYS = 30
VOICE_LONG_THRESHOLD_SEC = 300  # > этой длительности → AssemblyAI
VOICE_DIARIZE_SEC = 900  # > 15 мин → минуем Groq, сразу AssemblyAI с разбором по голосам
NATIVE_MIN_SEC = 20  # ≤ это → сразу облако (бережём квоту родной расшифровки Telegram)
ASSEMBLYAI_KEY_FILE = f"{_SECRETS}/assemblyai-key"
GROQ_KEY_FILE = f"{_SECRETS}/groq-key"   # быстрый первый движок расшифровки
GROQ_MODEL = "whisper-large-v3"
PENDING_TTL_SEC = 3600          # hard expiry of pending entry (1h)
PENDING_NAG_AFTER_SEC = 300     # remind about un-resolved pending after 5min when new attachment arrives
PENDING_CAL_FILE = CACHE_DIR / "pending-calendar.json"
PENDING_CAL_TTL_SEC = 600       # pending calendar proposal expires after 10min
# Жёсткий рубеж №2: отправку в Telegram делает ТОЛЬКО этот код после подтверждения
# хозяина. claude к отправке доступа не имеет (telegram-mcp в режиме read-only).
PENDING_SEND_FILE = CACHE_DIR / "pending-tgsend.json"
PENDING_SEND_TTL_SEC = 1800     # предложение отправки ждёт подтверждения 30 мин.
                                # Было 10 — не хватало: 27.08 хозяин ответил через
                                # 44 минуты, предложение к тому часу уже истлело.
TG_SEND_ONESHOT = PROFILE["tg_send_oneshot"]
TG_SEND_PYTHON = PROFILE["tg_send_python"]
TASKS_FILE = VAULT / "Tasks" / "tasks.md"
ALBUM_BUFFER_SEC = 2.0          # buffer media_group_id messages for this many seconds before flushing
BURST_BUFFER_SEC = 0.8          # debounce: glue messages from same (uid,chat) arriving within this window into one prompt
MSK = timezone(timedelta(hours=PROFILE["tz_offset_hours"]))
CLAUDE_BIN = PROFILE["claude_bin"]
CLAUDE_TIMEOUT = 3600  # seconds — no hard cap for long tasks; status msg shows elapsed time
TG_CHUNK = 4000
STATUS_PANEL_MIN_INTERVAL_SEC = 2.5  # реже — «зависло», чаще — Telegram шлёт 429
SESSION_TTL_HOURS = 6
VOICE_PORT = PROFILE["voice_port"]
SHORTCUT_TOKEN_FILE = f"{_SECRETS}/shortcut-token"
VOICE_CERT = f"{_SECRETS}/voice-cert.pem"
INJECT_PORT = PROFILE["inject_port"]
INJECT_TOKEN_FILE = f"{_SECRETS}/inject-token"
PULT_PORT = PROFILE["pult_port"]
PULT_HOST = PROFILE["pult_host"]
PULT_URL = PROFILE["pult_url"]
VOICE_KEY = f"{_SECRETS}/voice-key.pem"

# --- Voice-reply (edge-tts default, zvukogram for meme voices) ---
EDGE_TTS_BIN = PROFILE["edge_tts_bin"]
TTS_VOICE_DEFAULT = "ru-RU-DmitryNeural"
VOICE_REPLY_MAX_CHARS = 1800  # Звукограм режет всё длиннее 2000 символов (19.08)

# Zvukogram: REST TTS API
ZVUKOGRAM_API = "https://zvukogram.com/index.php?r=api/text"
ZVUKOGRAM_KEY_PATH = PROFILE["zvukogram_key_path"]
ZVUKOGRAM_EMAIL_PATH = PROFILE["zvukogram_email_path"]

# __TTS__ в первой строке Claude-ответа определяет озвучку и голос.
TTS_MARKER_RE = re.compile(r"^__TTS__\s+voice=(\S+)\s*$", re.IGNORECASE | re.MULTILINE)
VOICE_TAG_MAP = {
    "максим":  ("zvukogram", "Бот Максим"),
    "maksim":  ("zvukogram", "Бот Максим"),
    "maxim":   ("zvukogram", "Бот Максим"),
    "захар":   ("zvukogram", "Захар new"),
    "zahar":   ("zvukogram", "Захар new"),
    "дед мороз": ("zvukogram", "Дед Мороз"),
    "дедмороз":  ("zvukogram", "Дед Мороз"),
    "moroz":     ("zvukogram", "Дед Мороз"),
    "default": ("edge", TTS_VOICE_DEFAULT),
}

CACHE_DIR.mkdir(parents=True, exist_ok=True)
ATTACH.mkdir(parents=True, exist_ok=True)
INBOX.parent.mkdir(parents=True, exist_ok=True)
MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
VOICE_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
TASKS_FILE.parent.mkdir(parents=True, exist_ok=True)

def log(msg):
    print(f"[{datetime.now(MSK).strftime('%H:%M:%S')}] {msg}", flush=True)


def read_token():
    with open(TOKEN_FILE) as f:
        return f.read().strip()


def read_shortcut_token():
    try:
        return Path(SHORTCUT_TOKEN_FILE).read_text().strip() or None
    except Exception:
        return None


TOKEN = read_token()
SHORTCUT_TOKEN = read_shortcut_token()


def read_inject_token():
    try:
        return Path(INJECT_TOKEN_FILE).read_text().strip() or None
    except Exception:
        return None


INJECT_TOKEN = read_inject_token()
API = f"{PROFILE['api_base']}/bot{TOKEN}"
FILE_API = f"{PROFILE['api_base']}/file/bot{TOKEN}"

_CONFIRM_KEYBOARD = json.dumps({"inline_keyboard": [[{"text": "✅", "callback_data": "ok"}]]})
_MENU_ROW = [{"text": "🏠 МЕНЮ", "callback_data": "nav:root"}]
_STOP_KEYBOARD = json.dumps({"inline_keyboard": [[{"text": "⏹ СТОП", "callback_data": "stop"}]]})
_NO_KEYBOARD = json.dumps({"inline_keyboard": []})

# chat_id → live subprocess.Popen ещё не завершившегося `claude -p`.
# Жмёт ⏹ СТОП в TG → handle_callback убивает proc по chat_id.
_RUNNING_PROCS = {}
_RUNNING_PROCS_LOCK = threading.Lock()
# 2026-06-05: chat_id → живой ResidentClaude. Один процесс на чат, живёт
# между ходами. При СТОП — kill, при следующем ходе поднимаем с --resume.
_RESIDENT = {}
_RESIDENT_LOCK = threading.Lock()
# 2026-08-27: chat_id → время (time.time()) последнего завершённого хода.
# По нему жнец в claude_run решает, кто простаивает. Отдельный словарь, а не поле
# ResidentClaude: жнецу нужно видеть время и у тех чатов, чей процесс уже мёртв.
_RESIDENT_SEEN = {}
# chat_id → True если последний proc был убит юзером через ⏹ СТОП.
# call_claude читает флаг и помечает финальный ответ как «остановлено», а не
# как ошибку claude.
_STOP_FLAGS = {}
# 2026-07-06 (по просьбе хозяина): chat_id → True если финал по ⏹СТОП уже отправлен реальным
# сообщением прямо из handle_callback (гасит стрим-черновик и освобождает поле ввода
# мгновенно). Ход тогда не дублирует пост, отдаёт sentinel.
_STOP_POSTED = {}
# chat_id → ссылка на streamed_text текущего хода, чтобы callback ⏹СТОП собрал партиал.
_STREAM_PARTIAL = {}
# 25.09.2026, мягкий досыл: chat_id → Event «ответ идущего хода уже в чате». Ничейный ход
# с продолжением ждёт его, чтобы не обогнать начало ответа.
_FOLLOWUP_GATES = {}

# 2026-06-17: «догонка». Один ход на чат в момент времени — замок на chat_id.
# Если приходит новое сообщение пока идёт ход, оно НЕ ждёт молча (старый Popen-режим
# так делал и копил конфликт каналов), а прерывает текущий ход protocol-interrupt'ом
# (процесс жив, история цела), забирает замок и запускает новый ход с пометкой,
# что предыдущий ответ был прерван — модель сворачивает оба сообщения в один ответ.
_TURN_LOCKS = {}
_TURN_LOCKS_GUARD = threading.Lock()
# chat_id → True если идущий ход прерван догонкой. Прерванный ход видит флаг +
# error-result и ПОДАВЛЯЕТ свой пустой ответ (не шлёт «(пусто)» в чат).
_INTERRUPT_FLAGS = {}
# sentinel в reply: call_claude вернул подавленный прерванный ход — process_user_text
# не постит ничего и не трогает сессию.
_INTERRUPTED_SENTINEL = "\x00INTERRUPTED\x00"


def _get_turn_lock(chat_id):
    """Замок хода на беседу. Ключ — ckey(): без темы это голый chat_id, как было."""
    with _TURN_LOCKS_GUARD:
        lk = _TURN_LOCKS.get(chat_id)
        if lk is None:
            lk = threading.Lock()
            _TURN_LOCKS[chat_id] = lk
        return lk


# ============================================================================
# ТЕМЫ (08.10.2026, слово хозяина «давай делать»): параллельные беседы в личке с
# темами Telegram (Threaded Mode у BotFather) и в форум-группах. Беседа = чат +
# тема. Тема None (тем нет или общий вид) = ключ РОВНО прежний, словари и
# sessions.json те же, что до тем; у ботов без тем не меняется ничего.
#
# Тема хода закрепляется на поток (как engine.pin) на входе: сообщение, кнопка,
# /inject, фон. Дальше её видят ключи словарей (ckey/tkey) и tgapi.api, который
# сам подставляет message_thread_id во все отправки в этот чат. Потоки, рождённые
# внутри хода, закреплённое не наследуют: их цель оборачивается в carry().
# ============================================================================
_TOPIC_TLS = threading.local()
_PINNED = object()


def msg_topic(msg):
    """Номер темы из сообщения Telegram или None. В обычной супергруппе
    message_thread_id бывает и у ветки ответов, темой его считаем только с
    is_topic_message; в личке веток ответов нет, там номер и есть тема."""
    if not isinstance(msg, dict):
        return None
    tid = msg.get("message_thread_id")
    if not tid:
        return None
    chat_type = (msg.get("chat") or {}).get("type")
    if msg.get("is_topic_message") or chat_type == "private":
        try:
            return int(tid)
        except (TypeError, ValueError):
            return None
    return None


def pin_topic(chat_id, thread_id):
    _TOPIC_TLS.pin = (chat_id, int(thread_id)) if thread_id else None


def unpin_topic():
    _TOPIC_TLS.pin = None


def pinned_topic():
    return getattr(_TOPIC_TLS, "pin", None)


def topic_of(chat_id):
    """Тема, закреплённая на поток, если она про этот чат. Иначе None:
    отправка в чужой чат (группу, другого человека) тему не получает."""
    p = pinned_topic()
    if p is None or chat_id is None:
        return None
    return p[1] if str(p[0]) == str(chat_id) else None


def ckey(chat_id, thread_id=_PINNED):
    """Ключ беседы для живых словарей: chat_id без темы, (chat_id, тема) с темой."""
    tid = topic_of(chat_id) if thread_id is _PINNED else thread_id
    return (chat_id, int(tid)) if tid else chat_id


def ckey_chat(key):
    return key[0] if isinstance(key, tuple) else key


def ckey_topic(key):
    return key[1] if isinstance(key, tuple) else None


def tkey(base, chat_id=None, thread_id=_PINNED):
    """Строковый ключ на диске (sessions.json, ожидания): "<base>" без темы,
    "<base>:t<тема>" с темой. Тема берётся по чату chat_id (по умолчанию base)."""
    if thread_id is _PINNED:
        thread_id = topic_of(base if chat_id is None else chat_id)
    return f"{base}:t{int(thread_id)}" if thread_id else str(base)


class in_topic:
    """with in_topic(chat_id, тема): ... — закрепить тему на время блока."""
    def __init__(self, chat_id, thread_id):
        self.pin = (chat_id, int(thread_id)) if thread_id else None

    def __enter__(self):
        self._prev = pinned_topic()
        _TOPIC_TLS.pin = self.pin
        return self

    def __exit__(self, *exc):
        _TOPIC_TLS.pin = self._prev
        return False


# Названия тем и их сессии: <cache>/topics.json, ключ "<chat_id>:t<тема>".
# Название приходит один раз, сервисным forum_topic_created (и forum_topic_edited
# при переименовании); номер сессии нужен логу переписки, чтобы подписать реплики
# темой. Нет названия — подписываем номером.
TOPICS_FILE = CACHE_DIR / "topics.json"
_TOPICS_LOCK = threading.Lock()


def _topics_load():
    try:
        d = json.loads(TOPICS_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _topics_save(d):
    tmp = TOPICS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(TOPICS_FILE)


def remember_topic_title(chat_id, thread_id, title):
    if not thread_id or not (title or "").strip():
        return
    with _TOPICS_LOCK:
        d = _topics_load()
        rec = d.setdefault(f"{chat_id}:t{int(thread_id)}", {})
        rec["title"] = title.strip()[:120]
        _topics_save(d)


def note_topic_session(chat_id, thread_id, sid):
    if not thread_id or not sid:
        return
    with _TOPICS_LOCK:
        d = _topics_load()
        rec = d.setdefault(f"{chat_id}:t{int(thread_id)}", {})
        sids = rec.setdefault("sessions", [])
        if sid not in sids:
            sids.append(sid)
            _topics_save(d)


def topic_title(chat_id, thread_id):
    if not thread_id:
        return None
    rec = _topics_load().get(f"{chat_id}:t{int(thread_id)}") or {}
    return rec.get("title")


def topic_label(chat_id, thread_id):
    """Название темы или «тема <номер>»."""
    return topic_title(chat_id, thread_id) or f"тема {int(thread_id)}"


def topic_session_name(chat_id, thread_id):
    """Имя беседы темы в списке Claude Code."""
    return f"TG: {topic_label(chat_id, thread_id)}"


def carry(fn):
    """Обернуть цель потока: новый поток получит тему того, кто его родил."""
    pin = pinned_topic()

    def run(*a, **kw):
        prev = pinned_topic()
        _TOPIC_TLS.pin = pin
        try:
            return fn(*a, **kw)
        finally:
            _TOPIC_TLS.pin = prev
    return run

_album_lock = threading.Lock()
_album_buffers = {}   # media_group_id -> {"msgs": [msg, ...], "timer": Timer, "first_chat": int}

_burst_lock = threading.Lock()
_burst_buffers = {}  # (uid, ckey) -> {"msgs": [...], "timer": Timer}

# Per-chat: id of the message currently holding the brief keyboard.
_brief_active_msg = {}
# Per-chat: which menu level the user is currently "in". Updated on every nav/file
# click and /menu. Injected as [NAV_STATE] into Claude prompts so the headless
# instance knows what the user is looking at.
# Values: "root" | "projects" | ("proj", slug) | None
_brief_level = {}
