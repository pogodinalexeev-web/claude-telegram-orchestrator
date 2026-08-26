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
PENDING_SEND_TTL_SEC = 600      # предложение отправки ждёт подтверждения 10 мин
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
    with _TURN_LOCKS_GUARD:
        lk = _TURN_LOCKS.get(chat_id)
        if lk is None:
            lk = threading.Lock()
            _TURN_LOCKS[chat_id] = lk
        return lk

_album_lock = threading.Lock()
_album_buffers = {}   # media_group_id -> {"msgs": [msg, ...], "timer": Timer, "first_chat": int}

_burst_lock = threading.Lock()
_burst_buffers = {}  # (uid, chat_id) -> {"msgs": [...], "timer": Timer}

# Per-chat: id of the message currently holding the brief keyboard.
_brief_active_msg = {}
# Per-chat: which menu level the user is currently "in". Updated on every nav/file
# click and /menu. Injected as [NAV_STATE] into Claude prompts so the headless
# instance knows what the user is looking at.
# Values: "root" | "projects" | ("proj", slug) | None
_brief_level = {}
