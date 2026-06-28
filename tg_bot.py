#!/usr/bin/env python3
"""Claude TG-funnel bot v2: full assistant via headless `claude -p`.

Behaviour:
- Each text message → резидентный (долгоживущий) процесс claude в рабочем каталоге агента (AGENT_CWD).
- Прерывание: новое сообщение во время хода прерывает текущий ответ и склеивается с ним.
- First message creates a fresh session per user; session id persisted.
- /new resets session for the sender.
- Reply is sent back to TG, chunked at 4000 chars.
- After every claude run we git-sync the vault (claude may have written files).
- Media files still saved to attachments and path passed to claude in the prompt.
"""

import hashlib
import html
import http.server
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time

# Резидентный процесс claude вместо Popen-на-каждый-ход.
# Холодный старт (~6с) платится один раз при создании, второй и
# последующие ходы — без оверхеда Node.js + загрузки claude-binary + MCP.
# Модуль resident_claude.py — самодостаточный, без зависимостей бота.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from resident_claude import ResidentClaude

# ── Конфигурация. Все пути/секреты приходят из окружения с плейсхолдер-дефолтами.
HOME_DIR = os.environ.get("BOT_HOME", os.path.expanduser("~"))
# Каталог с секретами бота (токен, allowlist, ключи API, TLS-сертификаты).
SECRETS_DIR = os.environ.get("BOT_SECRETS_DIR", "/etc/tg-bot")
CACHE_DIR = Path(os.environ.get("BOT_CACHE_DIR", os.path.join(HOME_DIR, ".cache/claude-tg")))

TOKEN_FILE = os.environ.get("TELEGRAM_BOT_TOKEN_FILE", os.path.join(SECRETS_DIR, "token"))
ALLOW_FILE = os.environ.get("TELEGRAM_ALLOWLIST_FILE", os.path.join(SECRETS_DIR, "allowlist"))
OFFSET_FILE = CACHE_DIR / "offset"
SESSIONS_FILE = CACHE_DIR / "sessions.json"
VAULT = Path(os.environ.get("AGENT_CWD", os.path.join(HOME_DIR, "vault")))
ATTACH = VAULT / "Resources" / "attachments"
INBOX = VAULT / "inbox.md"
DO_QUEUE = VAULT / "Tasks" / "do-queue.md"
# Browser-automation commands routed to do-queue (executed elsewhere via CDP).
# Match: action-verb + marketplace keyword (in either order, within ~40 chars).
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
NATIVE_MIN_SEC = 20  # ≤ это → Whisper (бережём квоту родной расшифровки Telegram)
ASSEMBLYAI_KEY_FILE = os.environ.get(
    "ASSEMBLYAI_KEY_FILE", os.path.join(SECRETS_DIR, "assemblyai-key")
)
PENDING_TTL_SEC = 3600  # hard expiry of pending entry (1h)
PENDING_NAG_AFTER_SEC = (
    300  # remind about un-resolved pending after 5min when new attachment arrives
)
PENDING_CAL_FILE = CACHE_DIR / "pending-calendar.json"
PENDING_CAL_TTL_SEC = 600  # pending calendar proposal expires after 10min
# Жёсткий рубеж №2: отправку в Telegram делает ТОЛЬКО этот код после подтверждения
# хозяина. claude к отправке доступа не имеет (telegram-mcp в режиме read-only).
PENDING_SEND_FILE = CACHE_DIR / "pending-tgsend.json"
PENDING_SEND_TTL_SEC = 600  # предложение отправки ждёт подтверждения 10 мин
TG_SEND_ONESHOT = os.environ.get(
    "TG_SEND_ONESHOT", os.path.join(HOME_DIR, ".local/bin/tg-send-oneshot.py")
)
TG_SEND_PYTHON = os.environ.get("TG_SEND_PYTHON", "python3")
TASKS_FILE = VAULT / "Tasks" / "tasks.md"
ALBUM_BUFFER_SEC = 2.0  # buffer media_group_id messages for this many seconds before flushing
BURST_BUFFER_SEC = (
    0.8  # debounce: glue messages from same (uid,chat) arriving within this window into one prompt
)
MSK = timezone(timedelta(hours=3))
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
CLAUDE_TIMEOUT = 3600  # seconds — no hard cap for long tasks; status msg shows elapsed time
TG_CHUNK = 4000
SESSION_TTL_HOURS = 6
WHISPER_MODEL_SIZE = "small"
VOICE_PORT = 8444
SHORTCUT_TOKEN_FILE = os.environ.get(
    "SHORTCUT_TOKEN_FILE", os.path.join(SECRETS_DIR, "shortcut-token")
)
VOICE_CERT = os.environ.get("VOICE_CERT", os.path.join(SECRETS_DIR, "voice-cert.pem"))
VOICE_KEY = os.environ.get("VOICE_KEY", os.path.join(SECRETS_DIR, "voice-key.pem"))

# --- Voice-reply (edge-tts default, zvukogram for meme voices) ---
EDGE_TTS_BIN = os.environ.get("EDGE_TTS_BIN", "edge-tts")
TTS_VOICE_DEFAULT = "ru-RU-DmitryNeural"
VOICE_REPLY_MAX_CHARS = 3500

# Zvukogram: REST TTS API
ZVUKOGRAM_API = "https://zvukogram.com/index.php?r=api/text"
ZVUKOGRAM_KEY_PATH = os.environ.get("ZVUKOGRAM_KEY_PATH", os.path.join(HOME_DIR, ".zvukogram-key"))
ZVUKOGRAM_EMAIL_PATH = os.environ.get(
    "ZVUKOGRAM_EMAIL_PATH", os.path.join(HOME_DIR, ".zvukogram-email")
)

# __TTS__ в первой строке Claude-ответа определяет озвучку и голос.
TTS_MARKER_RE = re.compile(r"^__TTS__\s+voice=(\S+)\s*$", re.IGNORECASE | re.MULTILINE)
VOICE_TAG_MAP = {
    "максим": ("zvukogram", "Бот Максим"),
    "maksim": ("zvukogram", "Бот Максим"),
    "maxim": ("zvukogram", "Бот Максим"),
    "захар": ("zvukogram", "Захар new"),
    "zahar": ("zvukogram", "Захар new"),
    "дед мороз": ("zvukogram", "Дед Мороз"),
    "дедмороз": ("zvukogram", "Дед Мороз"),
    "moroz": ("zvukogram", "Дед Мороз"),
    "default": ("edge", TTS_VOICE_DEFAULT),
}

# ---------------------------------------------------------------------------
# Whisper — load once at startup
# ---------------------------------------------------------------------------
_whisper = None


def _load_whisper():
    global _whisper
    try:
        from faster_whisper import WhisperModel

        log(f"Loading Whisper model '{WHISPER_MODEL_SIZE}' (first run downloads ~500MB)…")
        _whisper = WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")
        log("Whisper ready")
    except Exception as e:
        log(f"Whisper unavailable: {e}")


def _archive_voice(src_path, file_id):
    """Move src .oga into voice-archive/YYYY-MM-DD/<ts>-<file_id>.oga (Fix-F).
    Best-effort: any failure → fall back to original delete."""
    try:
        day = datetime.now(MSK).strftime("%Y-%m-%d")
        ts = datetime.now(MSK).strftime("%H%M%S")
        dest_dir = VOICE_ARCHIVE_DIR / day
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{ts}-{file_id[:32]}.oga"
        shutil.move(src_path, str(dest))
        log(f"voice-archive: stored {dest}")
        return True
    except Exception as e:
        log(f"voice-archive: failed err={type(e).__name__}: {e}")
        return False


def cleanup_voice_archive():
    """Drop voice-archive dirs older than VOICE_ARCHIVE_TTL_DAYS (Fix-F)."""
    try:
        if not VOICE_ARCHIVE_DIR.exists():
            return
        cutoff = datetime.now(MSK) - timedelta(days=VOICE_ARCHIVE_TTL_DAYS)
        for d in VOICE_ARCHIVE_DIR.iterdir():
            if not d.is_dir():
                continue
            try:
                day = datetime.strptime(d.name, "%Y-%m-%d").replace(tzinfo=MSK)
            except ValueError:
                continue
            if day < cutoff:
                for f in d.iterdir():
                    try:
                        f.unlink()
                    except Exception:
                        pass
                try:
                    d.rmdir()
                except Exception:
                    pass
                log(f"voice-archive cleanup: dropped {d}")
    except Exception as e:
        log(f"voice-archive cleanup err: {e}")


def _read_assemblyai_key():
    try:
        with open(ASSEMBLYAI_KEY_FILE) as f:
            return f.read().strip() or None
    except Exception:
        return None


def transcribe_assemblyai(file_path):
    """Upload .oga to AssemblyAI, poll until done, return text or None.
    Длинные голосовые (>VOICE_LONG_THRESHOLD_SEC) — облако вместо локального Whisper."""
    key = _read_assemblyai_key()
    if not key:
        log("AssemblyAI: key file missing")
        return None
    headers = {"authorization": key}
    try:
        with open(file_path, "rb") as f:
            data = f.read()
        req = urllib.request.Request(
            "https://api.assemblyai.com/v2/upload",
            data=data,
            headers={**headers, "content-type": "application/octet-stream"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            upload_url = json.loads(r.read())["upload_url"]
        body = json.dumps(
            {"audio_url": upload_url, "language_code": "ru", "speech_models": ["universal-2"]}
        ).encode()
        req = urllib.request.Request(
            "https://api.assemblyai.com/v2/transcript",
            data=body,
            headers={**headers, "content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            tid = json.loads(r.read())["id"]
        poll_url = f"https://api.assemblyai.com/v2/transcript/{tid}"
        deadline = time.time() + 600  # 10 минут максимум
        while time.time() < deadline:
            time.sleep(3)
            req = urllib.request.Request(poll_url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as r:
                j = json.loads(r.read())
            st = j.get("status")
            if st == "completed":
                return (j.get("text") or "").strip() or None
            if st == "error":
                log(f"AssemblyAI error: {j.get('error')}")
                return None
        log("AssemblyAI: timeout")
        return None
    except Exception as e:
        log(f"AssemblyAI exception: {type(e).__name__}: {e}")
        return None


NATIVE_VENV_PY = os.environ.get("NATIVE_TRANSCRIBE_PYTHON", "python3")
NATIVE_HELPER = os.environ.get(
    "NATIVE_TRANSCRIBE_HELPER", os.path.join(HOME_DIR, "transcribe_native.py")
)


def transcribe_voice_native(msg_date, duration):
    """Родная расшифровка Telegram через пользовательскую сессию владельца (Premium).
    Ищет голосовое по времени отправки + длительности. Возвращает текст или None."""
    if not msg_date:
        return None
    try:
        r = subprocess.run(
            [NATIVE_VENV_PY, NATIVE_HELPER, str(int(msg_date)), str(int(duration or 0))],
            capture_output=True,
            text=True,
            timeout=25,
        )
        if r.returncode == 0:
            txt = (r.stdout or "").strip()
            if txt:
                log(f"voice native ok date={msg_date} chars={len(txt)}")
                return txt
        log(f"voice native miss date={msg_date} rc={r.returncode} err={(r.stderr or '')[:120]}")
    except Exception as e:
        log(f"voice native error: {e}")
    return None


def transcribe_voice(file_id, duration, msg_date=None):
    """Download voice file, transcribe, return text or None.
    Routing: ≤VOICE_LONG_THRESHOLD_SEC → локальный Whisper; иначе → AssemblyAI.
    Fix-F: on success, original .oga is moved to voice-archive instead of deleted.
    Native-first: пробуем родную расшифровку Telegram (сессия владельца), иначе Whisper."""
    # Родная расшифровка Telegram length-independent (~6.5с на любой длине), но у Telegram
    # есть квота. Короткие (≤NATIVE_MIN_SEC) бережём на Whisper, длинные (>порог) — AssemblyAI.
    if NATIVE_MIN_SEC < (duration or 0) <= VOICE_LONG_THRESHOLD_SEC:
        native = transcribe_voice_native(msg_date, duration)
        if native:
            return native
    with tempfile.NamedTemporaryFile(suffix=".oga", delete=False) as tmp:
        tmp_path = tmp.name
    archived = False
    try:
        ok = download_file_to(file_id, tmp_path)
        if not ok:
            return None
        if (duration or 0) > NATIVE_MIN_SEC:
            # длинные ИЛИ средние где родная упёрлась в квоту → облако AssemblyAI
            log(f"voice {duration}s → AssemblyAI (fallback/long)")
            text = transcribe_assemblyai(tmp_path)
        else:
            # короткие ≤NATIVE_MIN_SEC → локальный Whisper (бережём квоту Telegram)
            if _whisper is None:
                return None
            segments, _ = _whisper.transcribe(tmp_path, language="ru", beam_size=1)
            text = " ".join(s.text.strip() for s in segments).strip()
        archived = _archive_voice(tmp_path, file_id)
        return text or None
    except Exception as e:
        log(f"transcribe error: {e}")
        return None
    finally:
        if not archived:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


# Системный промпт читается из файла (см. SYSTEM_PROMPT_FILE). В исходном боте
# здесь была огромная захардкоженная константа с личными правилами владельца —
# в этой портфолио-копии она вынесена наружу, образец в prompts/system_prompt.example.md.
SYSTEM_PROMPT = open(
    os.environ.get("SYSTEM_PROMPT_FILE", "prompts/system_prompt.example.md"),
    encoding="utf-8",
).read()

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
API = f"http://127.0.0.1:8081/bot{TOKEN}"
FILE_API = f"http://127.0.0.1:8081/file/bot{TOKEN}"

# TG menu commands. Internal commands always shown; skill commands discovered
# dynamically from <VAULT>/.claude/commands/*.md.
# Names must match [a-z][a-z0-9_]{0,31}; skills with hyphens are registered with
# underscores and rewritten back before forwarding to Claude.
_BOT_INTERNAL_COMMANDS = [
    ("menu", "🏠 Меню: проекты / задачи / идеи / inbox"),
    ("new", "Новая сессия (сбросить контекст)"),
    ("compact", "Сжать текущую сессию"),
]


def _discover_skill_commands():
    """Read VAULT/.claude/commands/*.md, return ([(cmd,desc),...], aliases_dict).
    If VAULT/.claude/tg-menu.txt exists, filter to commands listed there
    (one per line; # comments and blanks ignored)."""
    cmds = []
    aliases = {}
    cmd_dir = VAULT / ".claude" / "commands"
    if not cmd_dir.is_dir():
        return cmds, aliases
    allow_file = VAULT / ".claude" / "tg-menu.txt"
    allow = None
    if allow_file.is_file():
        allow = set()
        for line in allow_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                allow.add(line.lstrip("/").replace("-", "_"))
    name_re = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
    for f in sorted(cmd_dir.glob("*.md")):
        name = f.stem
        tg_name = name.replace("-", "_")
        if not name_re.match(tg_name):
            continue
        if allow is not None and tg_name not in allow:
            continue
        desc = ""
        try:
            text = f.read_text(encoding="utf-8")
            m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
            if m:
                for line in m.group(1).splitlines():
                    if line.lower().startswith("description:"):
                        desc = line.split(":", 1)[1].strip().strip("\"'")
                        break
                body = text[m.end() :]
            else:
                body = text
            if not desc:
                for line in body.splitlines():
                    s = line.strip()
                    if s and not s.startswith("#") and not s.startswith(">"):
                        desc = s
                        break
        except Exception as e:
            log(f"discover cmd {f}: {e}")
        if not desc:
            desc = f"/{name}"
        cmds.append((tg_name, desc[:256]))
        if tg_name != name:
            aliases[f"/{tg_name}"] = f"/{name}"
    return cmds, aliases


_skill_cmds, SKILL_ALIASES = _discover_skill_commands()
BOT_MENU_COMMANDS = _BOT_INTERNAL_COMMANDS + _skill_cmds


def _rewrite_skill_alias(text: str) -> str:
    """Translate /underscored aliases back to /hyphenated skill names."""
    if not text:
        return text
    head, sep, tail = text.partition(" ")
    head_no_at = head.split("@", 1)[0]  # strip /cmd@botname suffix
    canonical = SKILL_ALIASES.get(head_no_at)
    if canonical:
        return canonical + sep + tail
    return text


def api(method, **params):
    data = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}).encode()
    req = urllib.request.Request(f"{API}/{method}", data=data)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except Exception as e:
        log(f"api {method} error: {e}")
        return {"ok": False}


# ── inline images from Claude replies (2026-05-15) ────────────────────────
# Когда reply содержит markdown-картинку или путь Resources/...{jpg,png,...}
# на отдельной строке — шлём sendPhoto, путь вырезаем из текста.

_INLINE_IMG_EXTS = ("jpg", "jpeg", "png", "webp", "gif")
_INLINE_IMG_MD_RE = re.compile(
    r"!\[[^\]]*\]\(([^)\s]+\.(?:jpg|jpeg|png|webp|gif))(?:\s+\"[^\"]*\")?\)",
    re.IGNORECASE,
)
_INLINE_IMG_BARE_RE = re.compile(
    r"^[\s\-*•]*((?:Resources|/tmp)/[^\s\)\]\"']+?\.(?:jpg|jpeg|png|webp|gif))\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# Telegram sendPhoto file size limit. (10 MB documented for Bot API photo uploads.)
_TG_PHOTO_MAX_BYTES = 10 * 1024 * 1024


def _resolve_inline_image(rel_or_abs):
    """Resolve a path candidate against vault. Returns absolute Path or None."""
    p = rel_or_abs.strip().strip("'\"")
    if not p:
        return None
    cand = Path(p)
    if not cand.is_absolute():
        cand = VAULT / cand
    try:
        cand = cand.resolve(strict=False)
    except Exception:
        return None
    # confine to vault or /tmp
    s = str(cand)
    if not (s.startswith(str(VAULT)) or s.startswith("/tmp/")):
        return None
    if not cand.is_file():
        return None
    return cand


def api_send_photo(chat_id, path, caption=None):
    """Send a local image as a Telegram photo via multipart/form-data."""
    boundary = "----telegrambot" + uuid.uuid4().hex
    try:
        with open(path, "rb") as f:
            file_bytes = f.read()
    except Exception as e:
        log(f"send_photo open {path}: {e}")
        return {"ok": False}
    body = []
    body.append(f"--{boundary}\r\n".encode())
    body.append(f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'.encode())
    if caption:
        body.append(f"--{boundary}\r\n".encode())
        body.append(b'Content-Disposition: form-data; name="caption"\r\n\r\n')
        body.append(caption.encode("utf-8"))
        body.append(b"\r\n")
    body.append(f"--{boundary}\r\n".encode())
    fname = Path(path).name
    body.append(
        f'Content-Disposition: form-data; name="photo"; filename="{fname}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n".encode()
    )
    body.append(file_bytes)
    body.append(b"\r\n")
    body.append(f"--{boundary}--\r\n".encode())
    data = b"".join(body)
    req = urllib.request.Request(f"{API}/sendPhoto", data=data)
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)
    except Exception as e:
        log(f"sendPhoto error: {e}")
        return {"ok": False}


def extract_and_send_images(chat_id, reply):
    log(f"SEND-TRACE extract_and_send_images entry, reply_len={len(reply or '')}")
    """Pull image paths out of reply, send each via sendPhoto, return cleaned reply.

    Markdown ![](path) and bare-line Resources/... paths are recognized.
    Path must resolve inside VAULT (or /tmp). Files >10 MB are skipped (left in text).
    """
    if not reply:
        return reply
    sent = set()
    cleaned = reply

    def _try_send(raw_path, caption=None):
        if raw_path in sent:
            return True
        resolved = _resolve_inline_image(raw_path)
        if not resolved:
            return False
        if resolved.stat().st_size > _TG_PHOTO_MAX_BYTES:
            log(f"inline-img skip (>10MB): {resolved}")
            return False
        res = api_send_photo(chat_id, str(resolved), caption=caption)
        if res.get("ok"):
            sent.add(raw_path)
            log(f"inline-img sent: {resolved.name}")
            return True
        return False

    # Markdown form first — preserves alt text as caption.
    def _md_sub(m):
        path = m.group(1)
        if _try_send(path):
            return ""
        return m.group(0)

    cleaned = _INLINE_IMG_MD_RE.sub(_md_sub, cleaned)

    # Bare-line paths.
    def _bare_sub(m):
        path = m.group(1)
        if _try_send(path):
            return ""
        return m.group(0)

    cleaned = _INLINE_IMG_BARE_RE.sub(_bare_sub, cleaned)

    # Collapse triple+ blank lines produced by stripping.
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


# ── inline documents from Claude replies (2026-05-18) ─────────────────────
# Когда reply содержит путь к файлу с документным расширением на отдельной строке —
# шлём sendDocument, путь вырезаем из текста.

_INLINE_DOC_EXT_RE = (
    r"(?:pdf|docx?|xlsx?|pptx?|csv|zip|txt|json|md|mp3|ogg|m4a|wav|flac|mp4|mov|epub|html?)"
)
_INLINE_DOC_BARE_RE = re.compile(
    r"^[\s\-*•]*((?:Resources|Projects|/tmp)/[^\s\)\]\"\']+?\.(?:" + _INLINE_DOC_EXT_RE + r"))\s*$",
    re.IGNORECASE | re.MULTILINE,
)

_TG_DOC_MAX_BYTES = 50 * 1024 * 1024


def api_send_document(chat_id, path, caption=None):
    """Send a local file as a Telegram document via multipart/form-data."""
    boundary = "----telegrambot" + uuid.uuid4().hex
    try:
        with open(path, "rb") as f:
            file_bytes = f.read()
    except Exception as e:
        log(f"send_document open {path}: {e}")
        return {"ok": False}
    body = []
    body.append(f"--{boundary}\r\n".encode())
    body.append(f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'.encode())
    if caption:
        body.append(f"--{boundary}\r\n".encode())
        body.append(b'Content-Disposition: form-data; name="caption"\r\n\r\n')
        body.append(caption.encode("utf-8"))
        body.append(b"\r\n")
    body.append(f"--{boundary}\r\n".encode())
    fname = Path(path).name
    body.append(
        f'Content-Disposition: form-data; name="document"; filename="{fname}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n".encode()
    )
    body.append(file_bytes)
    body.append(b"\r\n")
    body.append(f"--{boundary}--\r\n".encode())
    data = b"".join(body)
    req = urllib.request.Request(f"{API}/sendDocument", data=data)
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)
    except Exception as e:
        log(f"sendDocument error: {e}")
        return {"ok": False}


def extract_and_send_documents(chat_id, reply):
    """Pull document paths out of reply, send each via sendDocument, return cleaned reply."""
    if not reply:
        return reply
    sent = set()
    cleaned = reply

    def _try_send_doc(raw_path):
        if raw_path in sent:
            return True
        p = raw_path.strip().strip("'\"")
        cand = Path(p)
        if not cand.is_absolute():
            cand = VAULT / cand
        try:
            cand = cand.resolve(strict=False)
        except Exception:
            return False
        s = str(cand)
        if not (s.startswith(str(VAULT)) or s.startswith("/tmp/")):
            return False
        if not cand.is_file():
            return False
        if cand.stat().st_size > _TG_DOC_MAX_BYTES:
            log(f"inline-doc skip (>50MB): {cand}")
            return False
        res = api_send_document(chat_id, str(cand))
        if res.get("ok"):
            sent.add(raw_path)
            log(f"inline-doc sent: {cand.name}")
            return True
        return False

    def _bare_sub(m):
        path = m.group(1)
        if _try_send_doc(path):
            return ""
        return m.group(0)

    cleaned = _INLINE_DOC_BARE_RE.sub(_bare_sub, cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


class TypingPulser:
    """Keep TG 'typing…' indicator alive while a long task runs.

    Telegram drops the indicator ~5s after sendChatAction; we re-send every 4s
    in a background thread until the context exits.
    """

    def __init__(self, chat_id, interval=4):
        self.chat_id = chat_id
        self.interval = interval
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)

    def _run(self):
        while True:
            api("sendChatAction", chat_id=self.chat_id, action="typing")
            if self._stop.wait(self.interval):
                return


def load_allowlist():
    try:
        with open(ALLOW_FILE) as f:
            return set(int(line.strip()) for line in f if line.strip().isdigit())
    except FileNotFoundError:
        return set()


def allowlist_owner():
    """Owner = FIRST line of the allowlist file (insertion order = хозяин бота).
    load_allowlist() returns a set, so next(iter(...)) picks an arbitrary member
    by hash order — NOT the owner. Scheduled briefs (/inject) must target the
    owner's private chat, so read the file directly preserving order."""
    try:
        with open(ALLOW_FILE) as f:
            for line in f:
                s = line.strip()
                if s.isdigit():
                    return int(s)
    except FileNotFoundError:
        pass
    return None


def add_to_allowlist(uid):
    os.system(f"echo {uid} | tee -a {ALLOW_FILE} >/dev/null")
    log(f"TOFU: added user {uid} to allowlist")


def get_offset():
    try:
        return int(OFFSET_FILE.read_text().strip())
    except FileNotFoundError:
        return 0


def save_offset(o):
    OFFSET_FILE.write_text(str(o))


def load_sessions():
    try:
        return json.loads(SESSIONS_FILE.read_text())
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def save_sessions(s):
    SESSIONS_FILE.write_text(json.dumps(s, indent=2))


def get_session(uid):
    """Return (session_id, invalidation_reason).
    reason ∈ {None, 'ttl', 'vault_changed'}. None+None means no prior session.
    """
    s = load_sessions()
    rec = s.get(str(uid))
    if not rec:
        return None, None
    saved_head = rec.get("vault_head")
    current_head = vault_head()
    if saved_head and current_head and saved_head != current_head:
        return None, "vault_changed"
    return rec.get("session_id"), None


def update_vault_head(uid):
    """Stamp current vault HEAD onto user's session. Call after git_sync settles."""
    head = vault_head()
    if not head:
        return
    s = load_sessions()
    rec = s.get(str(uid))
    if not rec:
        return
    rec["vault_head"] = head
    s[str(uid)] = rec
    save_sessions(s)


def set_session(uid, sid, msg_count=None, add_tokens=0):
    s = load_sessions()
    rec = s.get(str(uid), {})
    if msg_count is None:
        if sid == rec.get("session_id"):
            msg_count = rec.get("msg_count", 0) + 1
        else:
            msg_count = 1
    rec["session_id"] = sid
    rec["last_ts"] = time.time()
    rec["msg_count"] = msg_count
    if msg_count == 1:
        rec.pop("warned_high", None)
        rec.pop("warned_urgent", None)
        rec["total_tokens"] = 0  # новая сессия — счётчик с нуля
    rec["total_tokens"] = rec.get("total_tokens", 0) + (add_tokens or 0)
    s[str(uid)] = rec
    save_sessions(s)


def get_msg_count(uid):
    s = load_sessions()
    return s.get(str(uid), {}).get("msg_count", 0)


def get_total_tokens(uid):
    s = load_sessions()
    return s.get(str(uid), {}).get("total_tokens", 0)


def reset_session(uid):
    s = load_sessions()
    s.pop(str(uid), None)
    save_sessions(s)


CONTEXT_WINDOW = 1_000_000  # claude-opus-4-8[1m] — для отображения в префиксе ответа

COMPACT_FIRST_AT = 15  # first compact reminder at this msg count
COMPACT_REPEAT = 5  # then every N messages


def download_file_to(file_id, dest_abs):
    """Download a TG file by file_id to absolute dest path. Returns True/False.
    Logs every failure mode (getFile rc, urlretrieve error, post-write size)."""
    res = api("getFile", file_id=file_id)
    if not res.get("ok"):
        log(
            f"download_file_to: getFile failed file_id={file_id[:20]}… resp={json.dumps(res)[:200]}"
        )
        return False
    path = res["result"]["file_path"]
    Path(dest_abs).parent.mkdir(parents=True, exist_ok=True)
    if path.startswith("/var/lib/telegram-bot-api/"):
        # local Bot API server: getFile отдаёт локальный путь внутри контейнера
        # (файл уже скачан сервером), а не URL. Тащим через docker exec cat —
        # путь содержит ':' (токен), поэтому cat, а не docker cp.
        try:
            with open(dest_abs, "wb") as out:
                r = subprocess.run(
                    ["docker", "exec", "tg-local-api", "cat", path],
                    stdout=out,
                    stderr=subprocess.PIPE,
                    timeout=600,
                )
            if r.returncode != 0:
                log(f"download_file_to: docker cat failed dest={dest_abs} err={r.stderr[:200]}")
                return False
        except Exception as e:
            log(f"download_file_to: docker cat exc dest={dest_abs} err={type(e).__name__}: {e}")
            return False
    else:
        try:
            urllib.request.urlretrieve(f"{FILE_API}/{path}", dest_abs)
        except Exception as e:
            log(f"download_file_to: urlretrieve failed dest={dest_abs} err={type(e).__name__}: {e}")
            return False
    try:
        sz = Path(dest_abs).stat().st_size
    except Exception as e:
        log(f"download_file_to: post-write stat failed dest={dest_abs} err={e}")
        return False
    if sz == 0:
        log(f"download_file_to: zero-byte file written, treating as failure dest={dest_abs}")
        try:
            Path(dest_abs).unlink()
        except Exception:
            pass
        return False
    log(f"download_file_to: ok dest={dest_abs} size={sz}")
    return True


_ATTACH_NAME_RE = re.compile(r"name=([^,]+)")
_ISO_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _make_attach_path(kind, ext, summary):
    """Determine vault path for an auto-saved attachment.
    Documents: preserve original file_name from summary, prefix with date if missing.
    Photo/video/audio/voice: YYYY-MM-DD-HHMMSS-<kind>-<short_hash><ext>.
    Dedup: append -2/-3 suffix on collision (preserve extension).
    """
    today = time.strftime("%Y-%m-%d")
    name = None
    if kind == "document":
        m = _ATTACH_NAME_RE.search(summary or "")
        if m:
            raw = m.group(1).strip()
            if raw and raw != "?":
                name = raw if _ISO_PREFIX_RE.match(raw) else f"{today} {raw}"
    if not name:
        ts = time.strftime("%H%M%S")
        h = hashlib.sha1(f"{summary}{time.time()}".encode()).hexdigest()[:6]
        name = f"{today}-{ts}-{kind}-{h}{ext or ''}"
    dest = ATTACH / name
    if dest.exists():
        base = dest.stem
        suffix = dest.suffix
        i = 2
        while (ATTACH / f"{base}-{i}{suffix}").exists():
            i += 1
        dest = ATTACH / f"{base}-{i}{suffix}"
    return str(dest)


def auto_save_attachment(chat_id, kind, file_id, ext, summary):
    """Try to download attachment immediately to Resources/attachments/.
    Returns (status, info) where status ∈ {"saved", "pending"}:
      saved   → info = relative vault path (string)
      pending → info = (pid, stale_list) — bot kept old PENDING fallback for large files / API fails.
    """
    dest_abs = _make_attach_path(kind, ext, summary)
    if download_file_to(file_id, dest_abs):
        try:
            rel = str(Path(dest_abs).relative_to(VAULT))
        except ValueError:
            rel = dest_abs
        return ("saved", rel)
    pid, stale = set_pending(chat_id, file_id, kind, ext, summary)
    return ("pending", (pid, stale))


def load_pending():
    """Pending as {chat_id: [list of pending dicts]}. Old single-dict format auto-migrated.
    Fix-C: queue not single slot."""
    try:
        raw = json.loads(PENDING_FILE.read_text())
    except Exception:
        return {}
    out = {}
    for k, v in raw.items():
        if isinstance(v, list):
            out[k] = v
        elif isinstance(v, dict) and v.get("file_id"):
            v.setdefault("pid", uuid.uuid4().hex[:8])
            out[k] = [v]
        else:
            out[k] = []
    return out


def save_pending(p):
    PENDING_FILE.write_text(json.dumps(p, indent=2, ensure_ascii=False))


def _prune_pending_list(lst):
    """Drop entries older than PENDING_TTL_SEC. Returns kept list."""
    now = time.time()
    return [r for r in lst if (now - r.get("ts", 0)) <= PENDING_TTL_SEC]


def set_pending(chat_id, file_id, kind, ext, summary):
    """Append a new pending entry to the chat's queue (Fix-C).
    Returns (pid, stale_list) where stale_list = entries already older than
    PENDING_NAG_AFTER_SEC at the moment we appended (Fix-I — bot nags about them)."""
    p = load_pending()
    key = str(chat_id)
    lst = _prune_pending_list(p.get(key) or [])
    now = time.time()
    stale = [r for r in lst if (now - r.get("ts", 0)) > PENDING_NAG_AFTER_SEC]
    pid = uuid.uuid4().hex[:8]
    lst.append(
        {
            "pid": pid,
            "file_id": file_id,
            "kind": kind,
            "ext": ext,
            "summary": summary,
            "ts": now,
        }
    )
    p[key] = lst
    save_pending(p)
    return pid, stale


def get_pending_list(chat_id):
    """Return list of un-expired pending entries for chat (newest last). Fix-C."""
    p = load_pending()
    key = str(chat_id)
    lst = _prune_pending_list(p.get(key) or [])
    if lst != (p.get(key) or []):
        p[key] = lst
        save_pending(p)
    return lst


def get_pending(chat_id):
    """Back-compat single-pending getter: returns the OLDEST un-expired entry, or None.
    New code should use get_pending_list / pop_pending_by_pid."""
    lst = get_pending_list(chat_id)
    return lst[0] if lst else None


def pop_pending_by_pid(chat_id, pid):
    """Remove a specific pending by pid, return the dict (or None). Fix-C."""
    p = load_pending()
    key = str(chat_id)
    lst = p.get(key) or []
    for i, r in enumerate(lst):
        if r.get("pid") == pid:
            removed = lst.pop(i)
            p[key] = lst
            save_pending(p)
            return removed
    return None


def pop_pending_oldest(chat_id):
    """Remove and return the oldest pending (used when Claude doesn't specify pid). Fix-C."""
    p = load_pending()
    key = str(chat_id)
    lst = _prune_pending_list(p.get(key) or [])
    if not lst:
        p[key] = []
        save_pending(p)
        return None
    removed = lst.pop(0)
    p[key] = lst
    save_pending(p)
    return removed


def clear_pending(chat_id):
    """Drop ALL pending for chat (used by __DROP_ALL_ATTACHMENTS__ or admin reset)."""
    p = load_pending()
    p.pop(str(chat_id), None)
    save_pending(p)


def safe_vault_path(rel_path):
    """Resolve rel_path inside VAULT, reject traversal/absolute. Returns abs path or None."""
    rel_path = rel_path.strip().strip("/")
    if not rel_path:
        return None
    candidate = (VAULT / rel_path).resolve()
    try:
        candidate.relative_to(VAULT.resolve())
    except ValueError:
        return None
    # Don't allow overwriting outside Resources/ or other safe roots
    parts = candidate.relative_to(VAULT.resolve()).parts
    if not parts or parts[0] not in {"Resources", "Projects", "Journal", "Tasks", "Archives"}:
        return None
    return candidate


SIGNIFICANT_PATHS = [
    "CLAUDE.md",
    "Projects/Example/Self/",
    "Resources/_templates/",
    ".claude/commands/",
    ".claude/agents/",
    ".claude/skills/",
]


def vault_head():
    """SHA of last commit that touched files which actually change Claude's behavior
    (system prompt, principles, skill templates). Captures in inbox.md / Tasks/ / log.md /
    status.md / Journal/ DON'T trigger session reset — they're append-only data, not behavior changes."""
    try:
        r = subprocess.run(
            ["git", "-C", str(VAULT), "log", "-1", "--format=%H", "--"] + SIGNIFICANT_PATHS,
            capture_output=True,
            timeout=5,
            env={**os.environ, "HOME": HOME_DIR},
        )
        if r.returncode == 0:
            sha = r.stdout.decode().strip()
            return sha or None
    except Exception as e:
        log(f"vault_head error: {e}")
    return None


def route_to_do_queue(text):
    """Append a browser-command to Tasks/do-queue.md and git-sync."""
    DO_QUEUE.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(MSK).strftime("%Y-%m-%d %H:%M")
    line = f"\n---\n{ts} (TG)\n{text.strip()}\n"
    with DO_QUEUE.open("a", encoding="utf-8") as f:
        f.write(line)
    git_sync(f"do-queue: {text.strip()[:50]}")


def git_sync(summary):
    """Stage, commit, sync, push. Robust to auto-commit hooks pre-committing."""
    try:
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "HOME": HOME_DIR}
        # 1. Stage + commit (no-op if hooks already committed).
        subprocess.run(
            ["git", "-C", str(VAULT), "add", "-A"],
            check=False,
            capture_output=True,
            env=env,
            timeout=15,
        )
        r = subprocess.run(
            ["git", "-C", str(VAULT), "commit", "-m", f"tg: {summary}"],
            capture_output=True,
            env=env,
            timeout=15,
        )
        # Tolerate "nothing to commit" — fall through to sync/push.
        if r.returncode != 0 and b"nothing to commit" not in r.stdout:
            log(f"git commit: {r.stdout.decode()[:200]}")
        # 2. Symmetric sync: fetch + ff-only, fall back to merge --no-edit.
        subprocess.run(
            ["git", "-C", str(VAULT), "fetch", "origin", "main"],
            check=False,
            capture_output=True,
            env=env,
            timeout=20,
        )
        ff = subprocess.run(
            ["git", "-C", str(VAULT), "merge", "--ff-only", "origin/main"],
            capture_output=True,
            env=env,
            timeout=15,
        )
        if ff.returncode != 0:
            mr = subprocess.run(
                [
                    "git",
                    "-C",
                    str(VAULT),
                    "merge",
                    "--no-edit",
                    "-m",
                    f"auto-merge tg-bot ({summary})",
                    "origin/main",
                ],
                capture_output=True,
                env=env,
                timeout=20,
            )
            if mr.returncode != 0:
                subprocess.run(
                    ["git", "-C", str(VAULT), "merge", "--abort"],
                    check=False,
                    capture_output=True,
                    env=env,
                    timeout=10,
                )
                log(f"git_sync merge failed: {mr.stderr.decode()[:200]}")
                return
        # 3. Push only if ahead.
        ahead_proc = subprocess.run(
            ["git", "-C", str(VAULT), "rev-list", "--count", "origin/main..HEAD"],
            capture_output=True,
            env=env,
            timeout=10,
        )
        try:
            ahead = int(ahead_proc.stdout.decode().strip() or "0")
        except ValueError:
            ahead = 0
        if ahead > 0:
            p = subprocess.run(
                ["git", "-C", str(VAULT), "push", "origin", "main"],
                capture_output=True,
                env=env,
                timeout=30,
            )
            if p.returncode != 0:
                log(f"git push failed: {p.stderr.decode()[:200]}")
    except Exception as e:
        log(f"git_sync error: {e}")


def _humanize_action(name, inp):
    """Короткая человеческая фраза «что делаю сейчас» для живой строки статуса.
    Без сырых команд и аргументов — только понятное действие."""
    inp = inp if isinstance(inp, dict) else {}
    base = ""
    fp = inp.get("file_path") or inp.get("path") or ""
    if fp:
        base = fp.rsplit("/", 1)[-1]
    if name == "Read":
        return f"📖 читаю {base}" if base else "📖 читаю файл"
    if name in ("Edit", "Write", "NotebookEdit"):
        return f"✏️ правлю {base}" if base else "✏️ правлю файл"
    if name == "Bash":
        return "⚙️ работаю в терминале"
    if name in ("Grep", "Glob"):
        return "🔍 ищу по файлам"
    if name == "WebSearch":
        return "🌐 ищу в сети"
    if name == "WebFetch":
        return "🌐 читаю страницу"
    if name == "Task":
        return "🤖 запускаю агента"
    if name == "TodoWrite":
        return "📝 обновляю план"
    if name == "ToolSearch":
        return "🔧 ищу инструмент"
    if name.startswith("mcp__playwright"):
        return "🌐 браузер"
    if name.startswith("mcp__telegram"):
        return "✈️ Telegram"
    if name.startswith("mcp__google-calendar"):
        return "📅 календарь"
    return f"🔧 {name}"


def _summarize_tool_input(name, inp):
    """Compact one-line summary of a tool_use input for the journal log."""
    if not isinstance(inp, dict):
        return ""
    if name in ("Read", "Write", "Edit", "NotebookEdit"):
        path = inp.get("file_path") or inp.get("path") or ""
        extras = []
        if "offset" in inp:
            extras.append(f"off={inp['offset']}")
        if "limit" in inp:
            extras.append(f"lim={inp['limit']}")
        suffix = (" " + " ".join(extras)) if extras else ""
        return f"{path}{suffix}"
    if name == "Bash":
        cmd = (inp.get("command") or "").replace("\n", " ")
        return cmd[:160]
    if name in ("Grep", "Glob"):
        return f"pat={inp.get('pattern', '')!r} path={inp.get('path', '')}"[:160]
    if name == "Agent" or name == "Task":
        return f"sub={inp.get('subagent_type', '?')} desc={inp.get('description', '')[:80]}"
    if name == "WebFetch":
        return inp.get("url", "")[:160]
    if name == "TodoWrite":
        todos = inp.get("todos", [])
        return f"todos={len(todos)}"
    # fallback: first stringy field
    for k, v in inp.items():
        if isinstance(v, str):
            return f"{k}={v[:120]}"
    return ""


_EFFORT_HIGH_TRIGGERS = re.compile(
    r"\b(подумай|обдумай|обстоятельн|подробн|детальн|глубок|разбер[иёе]|"
    r"архитектур|спроектир|стратеги|почему|как лучше|посоветуй|"
    r"что выбрать|оцени|критик|челлендж|аудит|план)",
    re.IGNORECASE,
)


def pick_effort(prompt_text):
    """Fixed high reasoning effort (configurable).
    Old hybrid logic kept inline in case we revert: triggered high on think-hard
    keywords or long prompts (>600 chars)."""
    return "high"


def append_to_inbox_raw(text, source="TG", note=None):
    """Канонический формат записи в inbox.md (см. Tasks/manual.md §inbox).
    note — опциональный маркер источника."""
    try:
        ts = datetime.now(MSK).strftime("%Y-%m-%d %H:%M")
        src = f"{source}, {note}" if note else source
        block = f"\n---\n{ts} ({src})\n{text}\n"
        with open(INBOX, "a", encoding="utf-8") as f:
            f.write(block)
        return True
    except Exception as e:
        log(f"append_to_inbox_raw error: {e}")
        return False


def call_claude(prompt_text, session_id=None, status_chat_id=None, bg_wait=False):
    """Run headless claude with stream-json output. Logs each tool_use as it streams.
    If status_chat_id is given, posts a live status message in TG and edits it as
    tool_use events arrive (throttled to 1 edit/sec). Returns (reply_text, new_session_id, used_tokens).
    """
    # 2026-06-05: переезд с Popen-per-turn на ResidentClaude (живой процесс на чат).
    # Холодный старт ~6с платится один раз при создании, на 2-м и след. ходах ~0.
    env_extra = {
        "HOME": HOME_DIR,
        "PATH": os.environ.get(
            "CLAUDE_PATH",
            os.path.dirname(CLAUDE_BIN)
            + ":"
            + os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        ),
    }
    DISALLOWED = "Bash(rm:-rf /) Bash(rm:-rf /*) Bash(reboot:*) Bash(shutdown:*) Edit(/etc/**) Write(/etc/**)"

    reply = ""
    result_is_error = False
    new_sid = session_id
    used_tokens = 0
    ctx_tokens = 0
    cache_creation = 0
    cache_read = 0
    tool_count = 0
    text_chars = 0
    first_event_at = None
    stderr_buf = []
    status_msg_id = [None]
    status_lines = []
    streamed_text = []
    thinking_text = []
    current_action = [None]  # человеческая фраза «что делаю сейчас» для живой строки
    last_edit = [0.0]
    # 2026-06-04: флаг — пришли ли дельты текста через stream_event.content_block_delta.text_delta.
    # Если да, финальный assistant.text игнорируем (его контент уже накопился из дельт).
    # Если нет (фоллбэк — флаг --include-partial-messages не сработал) — берём из assistant.text.
    stream_text_received = [False]
    # 2026-06-27: ставим пустую строку между текстовыми блоками, разорванными
    # инструментом (text → tool → text). Флаг взводится на tool_use, гасится при
    # следующем тексте — тогда вставляем "\n\n" перед ним. Между дельтами одного
    # блока разделитель НЕ вставляется (иначе слова разорвало бы).
    pending_sep = [False]
    # Публикуем «принял, думаю…» со ⏹ СТОП сразу, ещё до старта claude -p,
    # чтобы владелец мог нажать СТОП прямо в момент когда понял «не туда пошёл».
    if status_chat_id:
        try:
            r = api(
                "sendMessage",
                chat_id=status_chat_id,
                text="⏳ принял, думаю…",
                parse_mode="HTML",
                reply_markup=_STOP_KEYBOARD,
            )
            if r.get("ok"):
                status_msg_id[0] = r["result"]["message_id"]
        except Exception as e:
            log(f"early-status post failed: {e}")

    def update_status(force=False):
        if not status_chat_id or (not status_lines and not streamed_text):
            return
        elapsed = int(time.time() - t_start)
        m, s = divmod(elapsed, 60)
        elapsed_str = f"{m}м {s}с" if m else f"{s}с"

        # 2026-05-18: tool-строки (терминал) — под expandable blockquote.
        # "думаю…" и поток текста модели — снаружи, видны сразу.
        def _esc(s):
            return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        hourglass = "⏳" if elapsed % 2 == 0 else "⌛"
        parts = [f"{hourglass} думаю… ({elapsed_str})"]
        if current_action[0]:
            parts.append(_esc(current_action[0]))
        if thinking_text:
            think_body = "".join(thinking_text)
            if len(think_body) > 2500:
                think_body = "…" + think_body[-2500:]
            parts.append(f"🧠 <blockquote expandable>{_esc(think_body)}</blockquote>")
        if status_lines:
            tools_html = "\n".join(_esc(x) for x in status_lines[-8:])
            parts.append(f"<blockquote expandable>{tools_html}</blockquote>")
        if streamed_text:
            body = "".join(streamed_text)
            if len(body) > 2500:
                body = "…" + body[-2500:]
            parts.append(_esc(body))
        text = "\n\n".join(parts)
        if len(text) > 3800:
            text = text[:3800] + "…"
        now = time.time()
        # 2026-06-17: throttle 5.0 → 1.5с. На 5.0 текст и рассуждение прыгали раз в 5с —
        # выглядело как зависание. Telegram editMessageText держит ~1/сек/чат на один
        # message_id без 429; 1.5с — безопасный запас, при этом видно «слова текут».
        # 4.0с между обновлениями статуса — иначе читать на ходу не успеваешь.
        if not force and now - last_edit[0] < 4.0:
            return
        last_edit[0] = now
        if status_msg_id[0] is None:
            r = api(
                "sendMessage",
                chat_id=status_chat_id,
                text=text,
                parse_mode="HTML",
                reply_markup=_STOP_KEYBOARD,
            )
            if r.get("ok"):
                status_msg_id[0] = r["result"]["message_id"]
        else:
            api(
                "editMessageText",
                chat_id=status_chat_id,
                message_id=status_msg_id[0],
                text=text,
                parse_mode="HTML",
                reply_markup=_STOP_KEYBOARD,
            )

    # 2026-06-17: догонка. Один ход на чат. Если замок занят (идёт прошлый ход) —
    # прерываем тот ход protocol-interrupt'ом (процесс жив), ждём его чистого
    # завершения, забираем замок. Резидента для interrupt берём из _RESIDENT
    # напрямую; свежий rc фетчим уже под замком (ниже) — это переживает и случай,
    # когда прошлый ход умер по СТОП и резидента надо пересоздать.
    turn_lock = None
    did_interrupt = False
    if status_chat_id is not None:
        turn_lock = _get_turn_lock(status_chat_id)
        if not turn_lock.acquire(blocking=False):
            if bg_wait:
                # 2026-06-19: фоновый ход (сторож за процессом) — НЕ прерывает живой
                # диалог хозяина, а вежливо ждёт замок. Хозяин главнее сторожа.
                log(f"  bg-ход ждёт замок на чате {status_chat_id} (не прерываю)")
                turn_lock.acquire()
            else:
                _INTERRUPT_FLAGS[status_chat_id] = True
                with _RESIDENT_LOCK:
                    running = _RESIDENT.get(status_chat_id)
                if running is not None:
                    log(f"  догонка: прерываю текущий ход на чате {status_chat_id}")
                    try:
                        running.interrupt()
                    except Exception as e:
                        log(f"  interrupt() failed: {e}")
                turn_lock.acquire()  # ждём, пока прерванный ход освободит замок
                did_interrupt = True
    if did_interrupt:
        prompt_text = (
            "[Хозяин прервал твой предыдущий ответ, не дождавшись конца, и сразу "
            "дослал это сообщение. Прерванный ответ отброшен и в чат не ушёл — "
            "учти оба сообщения вместе и ответь заново, одним сообщением.]\n\n" + prompt_text
        )

    # Получаем живой ResidentClaude для этого чата или создаём.
    rc = None
    if status_chat_id is not None:
        with _RESIDENT_LOCK:
            existing = _RESIDENT.get(status_chat_id)
            if existing is not None and existing.is_alive():
                rc = existing
    if rc is None:
        try:
            rc = ResidentClaude(
                claude_bin=CLAUDE_BIN,
                system_prompt=SYSTEM_PROMPT,
                model="claude-opus-4-8[1m]",
                effort="medium",
                cwd=str(VAULT),
                resume_sid=session_id,
                env_extra=env_extra,
                disallowed_tools=DISALLOWED,
                permission_mode="bypassPermissions",
            )
            rc.start()
        except Exception as e:
            if turn_lock is not None:
                try:
                    turn_lock.release()
                except RuntimeError:
                    pass
            return (f"⚠️ Ошибка запуска claude: {e}", session_id, 0, 0, status_msg_id[0])
        if status_chat_id is not None:
            with _RESIDENT_LOCK:
                _RESIDENT[status_chat_id] = rc

    # Регистрируем underlying Popen для кнопки ⏹ СТОП (handle_callback делает proc.kill()).
    proc = rc.proc
    if status_chat_id:
        with _RUNNING_PROCS_LOCK:
            _RUNNING_PROCS[status_chat_id] = proc

    t_start = time.time()
    timed_out = False
    died_mid_turn = False
    # 2026-05-18: тикер риалтайм-счётчика. update_status() сам по себе зовётся
    # только на события от claude — между ними может быть тишина 10+ сек,
    # и время "думаю…" замирает. Отдельный поток дёргает update_status каждые 1.2с.
    _ticker_stop = threading.Event()

    def _ticker():
        while not _ticker_stop.wait(10.0):
            try:
                update_status(force=True)
            except Exception:
                pass

    _ticker_thread = threading.Thread(target=_ticker, daemon=True)
    _ticker_thread.start()
    try:
        for ev in rc.send_and_collect(prompt_text, turn_timeout=CLAUDE_TIMEOUT):
            if time.time() - t_start > CLAUDE_TIMEOUT:
                timed_out = True
                rc.kill()
                break
            etype = ev.get("type")
            # 2026-06-04: дельты текста — приоритетная ветка. С --include-partial-messages
            # CLI шлёт content_block_delta каждые ~300-500мс, по 30-100 символов.
            # Это и есть «слова текут», как в Claude Code десктопе.
            if etype == "stream_event":
                event_inner = ev.get("event") or {}
                ev_type = event_inner.get("type")
                if ev_type == "content_block_delta":
                    delta = event_inner.get("delta") or {}
                    if delta.get("type") == "text_delta":
                        t = delta.get("text") or ""
                        if t:
                            if first_event_at is None:
                                first_event_at = time.time() - t_start
                                log(f"  first-event after {first_event_at:.1f}s (stream-delta)")
                            stream_text_received[0] = True
                            if pending_sep[0] and streamed_text:
                                streamed_text.append("\n\n")
                            pending_sep[0] = False
                            streamed_text.append(t)
                            text_chars += len(t)
                            update_status()
                    elif delta.get("type") == "thinking_delta":
                        th = delta.get("thinking") or ""
                        if th:
                            if first_event_at is None:
                                first_event_at = time.time() - t_start
                                log(f"  first-event after {first_event_at:.1f}s (thinking)")
                            thinking_text.append(th)
                            update_status()
                # message_start / content_block_start / content_block_stop / message_stop /
                # signature_delta — UI не нужны, игнорируем.
                continue
            if etype == "assistant" and first_event_at is None:
                first_event_at = time.time() - t_start
                log(f"  first-event after {first_event_at:.1f}s (")
            if etype == "assistant":
                content = (ev.get("message") or {}).get("content") or []
                for blk in content:
                    btype = blk.get("type")
                    if btype == "tool_use":
                        tool_count += 1
                        pending_sep[0] = True
                        nm = blk.get("name", "?")
                        summary = _summarize_tool_input(nm, blk.get("input") or {})
                        log(f"  tool[{tool_count}] {nm}: {summary}")
                        status_lines.append(f"🔧 {nm}: {summary[:80]}")
                        current_action[0] = _humanize_action(nm, blk.get("input") or {})
                        update_status()
                    elif btype == "text":
                        t = blk.get("text") or ""
                        # Если дельты из stream_event уже накопили этот текст — пропускаем,
                        # чтобы не дублировать. Если стрима не было (фоллбэк) — кладём полностью.
                        if t and not stream_text_received[0]:
                            text_chars += len(t)
                            if pending_sep[0] and streamed_text:
                                streamed_text.append("\n\n")
                            pending_sep[0] = False
                            streamed_text.append(t)
                            update_status()
            elif etype == "result":
                reply = ev.get("result") or ""
                result_is_error = bool(ev.get("is_error"))
                new_sid = ev.get("session_id", session_id)
                u = ev.get("usage") or {}
                # iterations[-1] = последняя итерация = реальный активный контекст,
                # который модель видела в финальном API-вызове этого turn'а.
                # result.usage (без iterations) — это СУММА по всем iterations,
                # она множится количеством tool_use в turn'е и врёт в 5-8x.
                its = u.get("iterations") or [u]
                # turn_total = реальный API-расход за весь turn (сумма по итерациям).
                # input + cache_creation + cache_read + output — все токены, которых
                # коснулась модель в этом ответе. Это число копится в session-счётчик.
                used_tokens = sum(
                    (it.get("input_tokens") or 0)
                    + (it.get("cache_creation_input_tokens") or 0)
                    + (it.get("cache_read_input_tokens") or 0)
                    + (it.get("output_tokens") or 0)
                    for it in its
                )
                # ctx_tokens = размер контекста в финальной итерации (что модель видела
                # в финальном API-вызове). Аналог Mac context-window счётчика.
                last_it = its[-1] if its else {}
                ctx_tokens = (
                    (last_it.get("cache_creation_input_tokens") or 0)
                    + (last_it.get("cache_read_input_tokens") or 0)
                    + (last_it.get("input_tokens") or 0)
                )
                cache_creation = sum((it.get("cache_creation_input_tokens") or 0) for it in its)
                cache_read = sum((it.get("cache_read_input_tokens") or 0) for it in its)
    except TimeoutError:
        timed_out = True
        rc.kill()
    except RuntimeError as e:
        # ResidentClaude.send_and_collect → "claude died mid-turn" или "not alive".
        died_mid_turn = True
        log(f"resident died mid-turn: {e}")
        try:
            stderr_buf.append(rc.proc.stderr.read() or "")
        except Exception:
            pass
    finally:
        _ticker_stop.set()
        if status_chat_id:
            with _RUNNING_PROCS_LOCK:
                if _RUNNING_PROCS.get(status_chat_id) is proc:
                    _RUNNING_PROCS.pop(status_chat_id, None)
        # Если процесс мёртв (СТОП, timeout, краш) — выкинем из _RESIDENT,
        # следующий ход поднимет новый с --resume последнего sid.
        if not rc.is_alive() and status_chat_id is not None:
            with _RESIDENT_LOCK:
                if _RESIDENT.get(status_chat_id) is rc:
                    _RESIDENT.pop(status_chat_id, None)
        if turn_lock is not None:
            try:
                turn_lock.release()
            except RuntimeError:
                pass

    # 2026-06-17: этот ход прервали догонкой. Его error-result пустой — не шлём
    # «(пусто)» в чат, удаляем его «думаю…» (у догонки своё), отдаём sentinel.
    # pop() в условии всегда снимает флаг; подавляем ТОЛЬКО при is_error
    # (если ход успел доехать до настоящего ответа за миг до interrupt — отдаём его).
    if status_chat_id and _INTERRUPT_FLAGS.pop(status_chat_id, False) and result_is_error:
        log(f"  ход прерван догонкой — пустой результат подавлен (chat {status_chat_id})")
        if status_msg_id[0] is not None:
            try:
                api("deleteMessage", chat_id=status_chat_id, message_id=status_msg_id[0])
            except Exception:
                pass
        return (_INTERRUPTED_SENTINEL, new_sid or session_id, 0, 0, status_msg_id[0])

    # Если юзер нажал ⏹ СТОП — proc убит. Возвращаем то, что успело накопиться.
    if status_chat_id and _STOP_FLAGS.pop(status_chat_id, False):
        partial = "".join(streamed_text).strip()
        if partial:
            stopped_reply = f"{partial}\n\n🛑 остановлено по кнопке."
        else:
            stopped_reply = "🛑 остановлено по кнопке (ответ ещё не начался)."
        log(f"  stopped-by-user: text_chars={text_chars}, tools={tool_count}")
        return (stopped_reply, new_sid or session_id, 0, 0, status_msg_id[0])

    if timed_out:
        log(f"  timeout after {CLAUDE_TIMEOUT}s, tools={tool_count}, text_chars={text_chars}")
        return (
            "⏱ Клод думал слишком долго (>5 мин). Попробуй ещё раз или /new.",
            session_id,
            0,
            0,
            status_msg_id[0],
        )

    if died_mid_turn and not reply:
        err = ("".join(stderr_buf))[:500]
        if session_id and "session" in err.lower():
            return (
                f"⚠️ Сессия слетела. Сбрось через /new и повтори.\n\n{err}",
                None,
                0,
                0,
                status_msg_id[0],
            )
        return (f"⚠️ Ошибка claude:\n{err}", session_id, 0, 0, status_msg_id[0])

    fe = f"{first_event_at:.1f}s" if first_event_at is not None else "n/a"
    log(
        f"  turn-summary: first_event={fe}, tools={tool_count}, text_chars={text_chars}, ctx={used_tokens}, cache_create={cache_creation}, cache_read={cache_read}"
    )
    # Не удаляем статусное сообщение — вернём его id, чтобы send_chunked
    # отредактировал его в финальный ответ (Fix Bug2: начало больше не пропадает).
    # 2026-06-27 Fix Bug3: при tool_use в ходе CLI-поле result содержит ТОЛЬКО текст
    # последнего assistant-блока (после инструментов). Текст ДО инструментов
    # (основной ответ) терялся. streamed_text накопил ВЕСЬ текст хода (стрим-дельты
    # или фоллбэк из assistant.text) — берём его, он полнее. Когда инструментов не
    # было, streamed_text == reply, поведение не меняется.
    full_text = "".join(streamed_text).strip()
    return (full_text or reply or "(пусто)", new_sid, used_tokens, ctx_tokens, status_msg_id[0])


_CB_TOKEN = "\x00CB{}\x00"
_IC_TOKEN = "\x00IC{}\x00"


def md_to_html(text):
    """Convert lightweight markdown to TG-flavoured HTML.

    Supports: **bold**, __bold__, *italic*, _italic_, ~~strike~~, `code`,
    ```fenced```, [text](url), # headings (→ bold), - / * bullets (→ •).
    Code blocks are escaped first and pulled out so their inner *_~ don't get
    re-interpreted as markdown.
    """
    if not text:
        return ""

    code_blocks = []
    inline_codes = []

    def _stash_block(m):
        body = m.group(1).rstrip("\n")
        code_blocks.append(html.escape(body))
        return _CB_TOKEN.format(len(code_blocks) - 1)

    text = re.sub(r"```(?:[^\n`]*)\n?(.*?)```", _stash_block, text, flags=re.DOTALL)

    def _stash_inline(m):
        inline_codes.append(html.escape(m.group(1)))
        return _IC_TOKEN.format(len(inline_codes) - 1)

    text = re.sub(r"`([^`\n]+)`", _stash_inline, text)

    # Escape the rest, then re-apply markdown as HTML tags.
    text = html.escape(text)

    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.DOTALL)
    text = re.sub(r"(?<!\w)__(.+?)__(?!\w)", r"<b>\1</b>", text, flags=re.DOTALL)
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text, flags=re.DOTALL)
    text = re.sub(r"(?<![\*\w])\*([^\*\n]+?)\*(?![\*\w])", r"<i>\1</i>", text)
    text = re.sub(r"(?<![_\w])_([^_\n]+?)_(?![_\w])", r"<i>\1</i>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', text)
    text = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)
    text = re.sub(r"^[ \t]*[\*\-\+][ \t]+", "• ", text, flags=re.MULTILINE)

    for i, code in enumerate(inline_codes):
        text = text.replace(_IC_TOKEN.format(i), f"<code>{code}</code>")
    for i, code in enumerate(code_blocks):
        text = text.replace(_CB_TOKEN.format(i), f"<pre>{code}</pre>")

    return text


def strip_html(text):
    """Best-effort plain-text fallback when TG rejects HTML."""
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text)


def chunk_text(text, limit=TG_CHUNK):
    """Split text into <=limit chunks, preferring paragraph/line boundaries.
    Doesn't try to balance HTML tags across chunks — md_to_html keeps tags
    on a single line in practice; if a chunk lands mid-tag, the HTML send
    will fail and we fall back to plain text.
    """
    if len(text) <= limit:
        return [text]
    chunks = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n\n", 0, limit)
        if cut < limit // 2:
            cut = remaining.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = remaining.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


_CONFIRM_KEYBOARD = json.dumps({"inline_keyboard": [[{"text": "✅", "callback_data": "ok"}]]})
_MENU_ROW = [{"text": "🏠 МЕНЮ", "callback_data": "nav:root"}]
_STOP_KEYBOARD = json.dumps({"inline_keyboard": [[{"text": "⏹ СТОП", "callback_data": "stop"}]]})

# chat_id → live subprocess.Popen ещё не завершившегося `claude -p`.
# Жмёт ⏹ СТОП в TG → handle_callback убивает proc по chat_id.
_RUNNING_PROCS = {}
_RUNNING_PROCS_LOCK = threading.Lock()
# 2026-06-05: chat_id → живой ResidentClaude. Один процесс на чат, живёт
# между ходами. При СТОП — kill, при следующем ходе поднимаем с --resume.
_RESIDENT = {}
_RESIDENT_LOCK = threading.Lock()
# chat_id → True если последний proc был убит юзером через ⏹ СТОП.
# call_claude читает флаг и помечает финальный ответ как «остановлено», а не
# как ошибку claude.
_STOP_FLAGS = {}

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


_CHOICE_LINE_RE = re.compile(r"^\s*(?:\d+[\.\)]|[-•*])\s+(.{2,120})\s*$")


def detect_choices(plain_text):
    """If the reply contains a numbered/bulleted list (2-6 items) AND ends with '?',
    return [label, …] for buttons. Else []."""
    if not plain_text or "?" not in plain_text:
        return []
    choices = []
    for line in plain_text.splitlines():
        m = _CHOICE_LINE_RE.match(line)
        if m:
            label = m.group(1).strip()
            label = re.sub(r"\*\*|__|`", "", label)
            label = label.rstrip(" .,;:?")
            if label:
                choices.append(label)
    if 2 <= len(choices) <= 6:
        return choices
    return []


def needs_confirm(plain_text):
    """True if the reply is a single yes/no-style question (ends with '?' and no choice list)."""
    if not plain_text:
        return False
    if detect_choices(plain_text):
        return False
    return plain_text.rstrip().endswith("?")


def _build_choices_markup(choices):
    rows = []
    for label in choices:
        short = label if len(label) <= 32 else label[:30] + "…"
        cb = f"pick:{label}"
        if len(cb.encode("utf-8")) > 64:
            cb = cb.encode("utf-8")[:64].decode("utf-8", "ignore")
        rows.append([{"text": short, "callback_data": cb}])
    return json.dumps({"inline_keyboard": rows})


def _strip_for_tts(text):
    """Plain text for TTS: drop ctx-header, markdown, urls, hook badges, service markers."""
    if not text:
        return ""
    text = re.sub(r"^\(ctx [^)]+\)\s*\n+", "", text)
    # Drop leading hook-names block (simple-language / terse / honesty / audit #N / ground-truth / verify-plan / pull-lab / do-it-yourself).
    text = re.sub(
        r"^(?:(?:simple-language|terse|honesty|do-it-yourself|ground-truth|verify-plan|pull-lab|audit\s*#\d+)\s*\n)+\s*\n*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    # Drop leading single-letter hook badges: [S][H][D], [A#1], [G][V][P] etc. (one or more in a row, then blank line).
    text = re.sub(r"^(?:\[[A-Z](?:#\d+)?\])+\s*\n+", "", text)
    # Drop any service marker line: __TTS__, __CHOICES__, __WROTE__, __TASK__, __CAL_*__, __SAVE_ATTACHMENT__, __DROP_*__ etc.
    text = re.sub(r"^__[A-Z_]+__\b[^\n]*$\n?", "", text, flags=re.MULTILINE)
    try:
        text = strip_html(md_to_html(text))
    except Exception:
        pass
    text = re.sub(r"https?://\S+", "ссылка", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _zvukogram_creds():
    """Return (token, email) from local files, or (None, None)."""
    try:
        with open(ZVUKOGRAM_KEY_PATH) as f:
            tok = f.read().strip()
        with open(ZVUKOGRAM_EMAIL_PATH) as f:
            email = f.read().strip()
        if tok and email:
            return tok, email
    except Exception as e:
        log(f"zvukogram creds err: {type(e).__name__}: {e}")
    return None, None


def _synth_voice_zvukogram(text, out_ogg, voice):
    """zvukogram API -> mp3 URL -> download -> ffmpeg opus/ogg. True on success."""
    import os
    import subprocess
    import tempfile
    import urllib.parse

    tok, email = _zvukogram_creds()
    if not tok or not email:
        log("zvukogram: creds missing")
        return False
    data = urllib.parse.urlencode(
        {
            "token": tok,
            "email": email,
            "voice": voice,
            "text": text,
        }
    ).encode()
    req = urllib.request.Request(
        ZVUKOGRAM_API, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.load(r)
    except Exception as e:
        log(f"zvukogram api err: {type(e).__name__}: {e}")
        return False
    if str(resp.get("status")) != "1" or not resp.get("file"):
        log(f"zvukogram bad resp: {str(resp)[:200]}")
        return False
    mp3_url = resp["file"]
    mp3 = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False).name
    try:
        urllib.request.urlretrieve(mp3_url, mp3)
        if os.path.getsize(mp3) < 200:
            log("zvukogram: empty mp3")
            return False
        r2 = subprocess.run(
            ["ffmpeg", "-y", "-i", mp3, "-c:a", "libopus", "-b:a", "32k", out_ogg],
            capture_output=True,
            timeout=60,
        )
        if r2.returncode != 0 or not os.path.exists(out_ogg):
            log(f"ffmpeg(zvukogram) failed rc={r2.returncode} err={r2.stderr[:200]!r}")
            return False
        return True
    except Exception as e:
        log(f"_synth_voice_zvukogram err: {type(e).__name__}: {e}")
        return False
    finally:
        try:
            os.unlink(mp3)
        except Exception:
            pass


def _synth_voice_ogg(text, out_ogg, voice=None):
    """edge-tts -> mp3 -> ffmpeg opus/ogg. True on success."""
    import os
    import subprocess
    import tempfile

    if voice is None:
        voice = TTS_VOICE_DEFAULT
    mp3 = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False).name
    try:
        r = subprocess.run(
            [EDGE_TTS_BIN, "-v", voice, "-t", text, "--write-media", mp3],
            capture_output=True,
            timeout=180,
        )
        if r.returncode != 0 or not os.path.exists(mp3) or os.path.getsize(mp3) < 200:
            log(f"edge-tts failed rc={r.returncode} err={r.stderr[:200]!r}")
            return False
        r2 = subprocess.run(
            ["ffmpeg", "-y", "-i", mp3, "-c:a", "libopus", "-b:a", "32k", out_ogg],
            capture_output=True,
            timeout=60,
        )
        if r2.returncode != 0 or not os.path.exists(out_ogg):
            log(f"ffmpeg failed rc={r2.returncode} err={r2.stderr[:200]!r}")
            return False
        return True
    except Exception as e:
        log(f"_synth_voice_ogg err: {type(e).__name__}: {e}")
        return False
    finally:
        try:
            os.unlink(mp3)
        except Exception:
            pass


def api_send_voice(chat_id, ogg_path):
    """Multipart sendVoice."""
    import uuid

    boundary = uuid.uuid4().hex
    with open(ogg_path, "rb") as f:
        audio = f.read()
    head = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="voice"; filename="reply.ogg"\r\n'
        f"Content-Type: audio/ogg\r\n\r\n"
    ).encode()
    tail = f"\r\n--{boundary}--\r\n".encode()
    body = head + audio + tail
    req = urllib.request.Request(
        f"{API}/sendVoice",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)
    except Exception as e:
        log(f"sendVoice error: {e}")
        return {"ok": False}


def _split_for_voice(text, cap=None):
    if cap is None:
        cap = VOICE_REPLY_MAX_CHARS
    chunks = []
    t = text
    while len(t) > cap:
        cut = t.rfind(". ", 0, cap)
        if cut < cap // 2:
            cut = t.rfind(" ", 0, cap)
        if cut < cap // 3:
            cut = cap
        chunks.append(t[: cut + 1].strip())
        t = t[cut + 1 :].strip()
    if t:
        chunks.append(t)
    return chunks


def parse_tts_marker(reply):
    """Strip __TTS__ marker if present. Returns (reply, voice_reply, engine, voice_name)."""
    if not reply:
        return reply, False, "edge", None
    m = TTS_MARKER_RE.search(reply)
    if not m:
        return reply, False, "edge", None
    tag = m.group(1).lower().strip(",.")
    engine, voice_name = VOICE_TAG_MAP.get(tag, ("edge", TTS_VOICE_DEFAULT))
    return TTS_MARKER_RE.sub("", reply, count=1).lstrip(), True, engine, voice_name


def send_voice_reply(chat_id, reply_text, voice=None, engine="edge"):
    """True if at least one voice chunk delivered.
    engine ∈ {'edge','zvukogram'}. voice: edge — ru-RU-* name; zvukogram — голос из каталога."""
    import os
    import tempfile

    if voice is None:
        voice = TTS_VOICE_DEFAULT
    text = _strip_for_tts(reply_text)
    if not text:
        return False
    ok_any = False
    for ch in _split_for_voice(text):
        ogg = tempfile.NamedTemporaryFile(suffix=".ogg", delete=False).name
        try:
            if engine == "zvukogram":
                ok_synth = _synth_voice_zvukogram(ch, ogg, voice=voice)
                if not ok_synth:
                    log("zvukogram failed — falling back to edge-tts")
                    ok_synth = _synth_voice_ogg(ch, ogg, voice=TTS_VOICE_DEFAULT)
            else:
                ok_synth = _synth_voice_ogg(ch, ogg, voice=voice)
            if ok_synth:
                r = api_send_voice(chat_id, ogg)
                if r.get("ok"):
                    ok_any = True
                else:
                    log(f"sendVoice not ok: {(r.get('description') or '?')[:160]}")
        finally:
            try:
                os.unlink(ogg)
            except Exception:
                pass
    return ok_any


def send_chunked(
    chat_id,
    text,
    confirm=False,
    choices=None,
    dig=False,
    with_menu=True,
    menu_rows=None,
    edit_first_msg_id=None,
):
    """Send text. The last chunk gets a navigation keyboard.
    - with_menu=False: no nav keyboard.
    - menu_rows=None: default = [[🏠 МЕНЮ]].
    - menu_rows=<list of rows>: use these rows verbatim (e.g. back + menu).
    - edit_first_msg_id: if set, edits this existing message for the first chunk
      instead of sending a new one (Fix Bug2: status message becomes final reply).
    The previously active nav keyboard in this chat is stripped first."""
    if not text:
        text = "(пусто)"
    html_text = md_to_html(text)
    chunks = list(chunk_text(html_text, TG_CHUNK))
    # Build keyboard rows for the LAST chunk.
    last_rows = []
    if choices:
        last_rows = json.loads(_build_choices_markup(choices))["inline_keyboard"]
    elif dig:
        last_rows = [[{"text": "🔎", "callback_data": "dig"}]]
    elif confirm:
        last_rows = [[{"text": "✅", "callback_data": "ok"}]]
    if with_menu:
        if menu_rows is None:
            last_rows.append(_MENU_ROW)
        else:
            last_rows.extend(menu_rows)
    last_markup = (
        json.dumps({"inline_keyboard": last_rows}, ensure_ascii=False) if last_rows else None
    )
    # Strip prior active nav keyboard before posting new messages below.
    if with_menu:
        try:
            _strip_active_keyboard(chat_id)
        except NameError:
            pass  # not yet defined at module load — safe at call time
    last_msg_id = None
    import traceback as _tb

    log(
        f"SEND-TRACE send_chunked chat={chat_id} chunks={len(chunks)} total_chars={sum(len(c) for c in chunks)} caller={_tb.extract_stack()[-2].name}"
    )
    for i, chunk in enumerate(chunks):
        is_last = i == len(chunks) - 1
        markup = last_markup if is_last else None
        log(f"SEND-TRACE  chunk[{i}/{len(chunks)}] {len(chunk)} chars, markup={bool(markup)}")
        # Fix Bug2: edit status message into first chunk instead of sending new.
        if i == 0 and edit_first_msg_id is not None:
            res = api(
                "editMessageText",
                chat_id=chat_id,
                message_id=edit_first_msg_id,
                text=chunk,
                parse_mode="HTML",
                reply_markup=markup,
                disable_web_page_preview="true",
            )
            if res.get("ok"):
                if is_last:
                    last_msg_id = edit_first_msg_id
                continue
            # Edit failed (deleted/rate-limited) → fall through to sendMessage
            log(
                f"editMessageText status→final failed: {res.get('description', '?')[:120]}, falling back to send"
            )
        res = api(
            "sendMessage",
            chat_id=chat_id,
            text=chunk,
            parse_mode="HTML",
            reply_markup=markup,
            disable_web_page_preview="true",
        )
        if not res.get("ok"):
            log(f"HTML send failed, falling back to plain: {res.get('description', '?')[:120]}")
            res = api(
                "sendMessage",
                chat_id=chat_id,
                text=strip_html(chunk),
                reply_markup=markup,
                disable_web_page_preview="true",
            )
        if is_last and res.get("ok"):
            last_msg_id = res.get("result", {}).get("message_id")
    if with_menu and last_msg_id is not None:
        _brief_active_msg[chat_id] = last_msg_id


# Fix-D/E/G: post-reply markers. Lines anywhere in reply that match — extracted, removed.
_WROTE_RE = re.compile(
    r"^\s*__WROTE__\s*:\s*(\d+)\s+entries?\s+to\s+(\S.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_TASK_RE = re.compile(
    r"^\s*__TASK__\s*:\s*(.+?)\s*$",
    re.MULTILINE,
)
# 2026-06-19: __BG_TASK__: {json} — модель запускает долгий процесс в фоне и просит
# позвать её по завершении. Бот гоняет cmd в потоке-стороже, потом заводит
# отдельный ход с результатом — модель сама пишет хозяину первым сообщением.
_BG_TASK_RE = re.compile(
    r"^\s*__BG_TASK__\s*:\s*(\{.*\})\s*$",
    re.MULTILINE,
)


def parse_wrote_markers(reply):
    """Pull all __WROTE__: <count> entries to <path> lines. Returns (cleaned, [(count, path), ...])."""
    if not reply:
        return reply, []
    out = []

    def _take(m):
        try:
            cnt = int(m.group(1))
        except Exception:
            cnt = 0
        path = m.group(2).strip().strip("`").strip("'\"")
        out.append((cnt, path))
        return ""

    cleaned = _WROTE_RE.sub(_take, reply)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, out


def parse_task_markers(reply):
    """Pull all __TASK__: <text> lines. Returns (cleaned, [text, ...])."""
    if not reply:
        return reply, []
    out = []

    def _take(m):
        t = m.group(1).strip()
        if t:
            out.append(t[:200])
        return ""

    cleaned = _TASK_RE.sub(_take, reply)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, out


def parse_bg_task_marker(reply):
    """Pull first __BG_TASK__: {json} line. Returns (cleaned, spec_or_None).

    spec keys: cmd (required shell command to run & wait on), then (optional
    instruction for the follow-up turn), label (optional human name), timeout
    (optional seconds, default 3600). Invalid json / missing cmd → ignored.
    """
    if not reply:
        return reply, None
    m = _BG_TASK_RE.search(reply)
    if not m:
        return reply, None
    spec = None
    try:
        obj = json.loads(m.group(1))
        cmd = (obj.get("cmd") or "").strip()
        if cmd:
            spec = {
                "cmd": cmd,
                "then": (obj.get("then") or "").strip(),
                "label": (obj.get("label") or "").strip() or cmd[:60],
                "timeout": int(obj.get("timeout") or 3600),
            }
    except Exception as e:
        log(f"  __BG_TASK__ bad json: {e}")
    cleaned = _BG_TASK_RE.sub("", reply, count=1)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, spec


_CHOICES_RE = re.compile(r"^\s*(?:__)?CHOICES(?:__)?\s*[: ]\s*(.+?)\s*$", re.MULTILINE)


def parse_choices_marker(reply):
    """Extract __CHOICES__ marker. Returns (cleaned_reply, [label, …])."""
    if not reply:
        return reply, []
    m = _CHOICES_RE.search(reply)
    if not m:
        return reply, []
    raw = m.group(1)
    cleaned = _CHOICES_RE.sub("", reply, count=1).rstrip()
    labels = []
    for part in raw.split("|"):
        label = part.strip().strip("`").strip("'\"")
        if not label:
            continue
        if len(label.encode("utf-8")) > 58:
            label = label[:28] + "…"
        labels.append(label)
    if not (2 <= len(labels) <= 6):
        return cleaned, []
    return cleaned, labels


_AFFIRM_RE = re.compile(r"^\s*(?:__)?AFFIRM(?:__)?\s*$", re.MULTILINE)


def parse_affirm_marker(reply):
    """Extract __AFFIRM__ marker. Returns (cleaned_reply, want_affirm_bool).

    If marker present — bot shows a single ✅ button; tap delivers 'да' to the user.
    See the system prompt for the dispatch rule between __CHOICES__ and __AFFIRM__.
    """
    if not reply:
        return reply, False
    if _AFFIRM_RE.search(reply):
        cleaned = _AFFIRM_RE.sub("", reply, count=1).rstrip()
        return cleaned, True
    return reply, False


_DIG_RE = re.compile(r"^\s*(?:__)?DIG(?:__)?\s*$", re.MULTILINE)


def parse_dig_marker(reply):
    """Extract __DIG__ marker. Returns (cleaned_reply, want_dig_bool).

    If marker present — bot shows a single 🔎 button; tap delivers 'копай' to the user,
    which signals the brainstorming/Socratic mode for the next turn.
    See the system prompt.
    """
    if not reply:
        return reply, False
    if _DIG_RE.search(reply):
        cleaned = _DIG_RE.sub("", reply, count=1).rstrip()
        return cleaned, True
    return reply, False


# Calendar markers (Google Calendar via MCP)
_CAL_PROPOSE_RE = re.compile(r"^\s*__CAL_PROPOSE__\s*:\s*(\{.+?\})\s*$", re.MULTILINE)
_CAL_EVENT_RE = re.compile(r"^\s*__CAL_EVENT__\s*:\s*(\{.+?\})\s*$", re.MULTILINE)
_CAL_CANCEL_RE = re.compile(r"^\s*__CAL_CANCEL__\s*:\s*\S+\s*$", re.MULTILINE)


def _parse_cal_json_marker(reply, regex):
    """Extract single JSON marker. Returns (cleaned_reply, parsed_dict_or_None)."""
    if not reply:
        return reply, None
    m = regex.search(reply)
    if not m:
        return reply, None
    try:
        data = json.loads(m.group(1))
    except Exception as e:
        log(f"cal marker json parse err: {e}; raw={m.group(1)[:200]}")
        data = None
    cleaned = (reply[: m.start()] + reply[m.end() :]).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned, data


def parse_cal_propose_marker(reply):
    return _parse_cal_json_marker(reply, _CAL_PROPOSE_RE)


def parse_cal_event_marker(reply):
    return _parse_cal_json_marker(reply, _CAL_EVENT_RE)


def parse_cal_cancel_marker(reply):
    """Returns (cleaned, True_if_cancel_marker_present)."""
    if not reply:
        return reply, False
    m = _CAL_CANCEL_RE.search(reply)
    if not m:
        return reply, False
    cleaned = (reply[: m.start()] + reply[m.end() :]).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned, True


# Pending calendar proposal state (file-backed, per-uid)
_pending_cal_lock = threading.Lock()


def _load_pending_cal_all():
    if not PENDING_CAL_FILE.exists():
        return {}
    try:
        return json.loads(PENDING_CAL_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_pending_cal_all(data):
    PENDING_CAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PENDING_CAL_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(PENDING_CAL_FILE)


def get_pending_cal(uid):
    """Return pending event dict for uid (or None) — prunes if expired."""
    with _pending_cal_lock:
        data = _load_pending_cal_all()
        entry = data.get(str(uid))
        if not entry:
            return None
        if time.time() - entry.get("ts", 0) > PENDING_CAL_TTL_SEC:
            data.pop(str(uid), None)
            _save_pending_cal_all(data)
            return None
        return entry.get("event")


def set_pending_cal(uid, event_json):
    with _pending_cal_lock:
        data = _load_pending_cal_all()
        data[str(uid)] = {"event": event_json, "ts": time.time()}
        _save_pending_cal_all(data)


def clear_pending_cal(uid):
    with _pending_cal_lock:
        data = _load_pending_cal_all()
        if str(uid) in data:
            data.pop(str(uid), None)
            _save_pending_cal_all(data)


# --- Рубеж №2: отправка в Telegram через подтверждение ---------------------
# claude предлагает (__TG_SEND_PROPOSE__), хозяин подтверждает, ОТПРАВЛЯЕТ этот
# код через tg-send-oneshot.py. claude физически не может слать (read-only MCP).
_TG_SEND_PROPOSE_RE = re.compile(r"^\s*__TG_SEND_PROPOSE__\s*:\s*(\{.+?\})\s*$", re.MULTILINE)
_TG_SEND_CONFIRM_RE = re.compile(r"^\s*__TG_SEND_CONFIRM__\s*:\s*\S+\s*$", re.MULTILINE)
_TG_SEND_CANCEL_RE = re.compile(r"^\s*__TG_SEND_CANCEL__\s*:\s*\S+\s*$", re.MULTILINE)


def parse_tg_send_propose_marker(reply):
    return _parse_cal_json_marker(reply, _TG_SEND_PROPOSE_RE)


def _parse_flag_marker(reply, regex):
    if not reply:
        return reply, False
    m = regex.search(reply)
    if not m:
        return reply, False
    cleaned = (reply[: m.start()] + reply[m.end() :]).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned, True


def parse_tg_send_confirm_marker(reply):
    return _parse_flag_marker(reply, _TG_SEND_CONFIRM_RE)


def parse_tg_send_cancel_marker(reply):
    return _parse_flag_marker(reply, _TG_SEND_CANCEL_RE)


_pending_send_lock = threading.Lock()


def _load_pending_send_all():
    if not PENDING_SEND_FILE.exists():
        return {}
    try:
        return json.loads(PENDING_SEND_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_pending_send_all(data):
    PENDING_SEND_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PENDING_SEND_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(PENDING_SEND_FILE)


def get_pending_send(uid):
    with _pending_send_lock:
        data = _load_pending_send_all()
        entry = data.get(str(uid))
        if not entry:
            return None
        if time.time() - entry.get("ts", 0) > PENDING_SEND_TTL_SEC:
            data.pop(str(uid), None)
            _save_pending_send_all(data)
            return None
        return entry.get("send")


def set_pending_send(uid, send_json):
    with _pending_send_lock:
        data = _load_pending_send_all()
        data[str(uid)] = {"send": send_json, "ts": time.time()}
        _save_pending_send_all(data)


def clear_pending_send(uid):
    with _pending_send_lock:
        data = _load_pending_send_all()
        if str(uid) in data:
            data.pop(str(uid), None)
            _save_pending_send_all(data)


def _oneshot(args, timeout=60):
    """Запустить tg-send-oneshot.py, вернуть распарсенный JSON (или dict с ok=False)."""
    try:
        r = subprocess.run(
            [TG_SEND_PYTHON, TG_SEND_ONESHOT] + list(args),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        out = (r.stdout or "").strip().splitlines()
        for line in reversed(out):
            line = line.strip()
            if line.startswith("{"):
                return json.loads(line)
        return {"ok": False, "error": "no_json", "raw": (r.stdout or r.stderr or "")[:300]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tg_resolve_peer(query):
    return _oneshot(["resolve", query])


def tg_send_now(peer_id, text):
    return _oneshot(["send", str(peer_id), text])


def append_tasks_from_markers(task_texts):
    """Fix-G: append captured tasks into Tasks/tasks.md.
    Returns count actually written (after dedup)."""
    if not task_texts:
        return 0
    try:
        TASKS_FILE.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(MSK).strftime("%Y-%m-%d %H:%M")
        # Read existing to avoid trivial dupes within same minute
        existing = ""
        try:
            existing = TASKS_FILE.read_text(encoding="utf-8")
        except FileNotFoundError:
            pass
        lines_to_add = []
        for t in task_texts:
            t = t.replace("\n", " ").strip()
            if not t:
                continue
            line = f"- [ ] {ts} — {t} #from-tg"
            if line in existing:
                continue
            lines_to_add.append(line)
        if not lines_to_add:
            return 0
        with TASKS_FILE.open("a", encoding="utf-8") as f:
            if existing and not existing.endswith("\n"):
                f.write("\n")
            f.write("\n".join(lines_to_add) + "\n")
        log(f"Fix-G: appended {len(lines_to_add)} task(s) to tasks.md")
        return len(lines_to_add)
    except Exception as e:
        log(f"Fix-G tasks append err: {e}")
        return 0


def write_manifest_entry(entry):
    """Fix-E: append one jsonl line to manifests/YYYY-MM-DD.jsonl."""
    try:
        MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
        day = datetime.now(MSK).strftime("%Y-%m-%d")
        path = MANIFEST_DIR / f"{day}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        log(f"manifest write err: {e}")


def _run_bg_task(uid, chat_id, spec):
    """Поток-сторож (Design B). Гоняет spec['cmd'], ждёт завершения, затем заводит
    отдельный ход с результатом — модель сама пишет хозяину первым сообщением.
    Фоновый ход НЕ прерывает живой диалог (call_claude bg_wait=True ждёт замок)."""
    cmd = spec["cmd"]
    label = spec["label"]
    timeout = spec["timeout"]
    then = spec["then"]
    log(f"bg-task старт: label={label!r}, timeout={timeout}s")
    rc = None
    out = ""
    err = ""
    timed_out = False
    try:
        p = subprocess.run(
            cmd,
            shell=True,
            executable="/bin/bash",
            cwd=str(VAULT),
            env={**os.environ, "HOME": HOME_DIR},
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        rc = p.returncode
        out = (p.stdout or "")[-3000:]
        err = (p.stderr or "")[-1000:]
    except subprocess.TimeoutExpired:
        timed_out = True
        log(f"bg-task таймаут: {label!r} >{timeout}s")
    except Exception as e:
        err = str(e)
        log(f"bg-task ошибка запуска: {e}")

    if timed_out:
        status = f"НЕ ДОЖДАЛАСЬ: процесс не уложился в {timeout}с и был убит."
    else:
        status = f"Код выхода: {rc} ({'успех' if rc == 0 else 'ошибка'})."
    synthetic = (
        "[ФОНОВАЯ ЗАДАЧА ЗАВЕРШЕНА]\n"
        f"Что запускал: {label}\n"
        f"{status}\n"
        f"stdout (хвост):\n{out or '(пусто)'}\n"
        + (f"stderr (хвост):\n{err}\n" if err else "")
        + "\nРанее ты сам запустил это в фоне"
        + (f" и просил по завершении: {then}" if then else "")
        + ".\nПосмотри результат и напиши хозяину первым сообщением — коротко, по делу. "
        "Это не ответ на его реплику, а твоя инициативная весть, что фоновая задача закрылась."
    )
    try:
        process_user_text(
            uid, chat_id, synthetic, source="bg", summary_text=f"bg: {label}", _bg=True
        )
    except Exception as e:
        log(f"bg-task follow-up turn error: {e}")
        try:
            api(
                "sendMessage",
                chat_id=chat_id,
                text=f"⚠️ Фоновая задача «{label}» закрылась, но я не смог оформить ответ: {e}",
            )
        except Exception:
            pass


def process_user_text(
    uid, chat_id, full, *, msg_id=None, source="tg", summary_text=None, _bg=False
):
    """Common path: invalidation → call_claude → send → compact-prompt → git_sync.

    Used by both TG message handler and the /voice HTTP endpoint, so a single
    session, single compact-prompter counter and single git_sync per turn
    regardless of where the input came from.
    Returns the final reply text (after attachment markers stripped).
    """
    # Group chats (chat_id<0) share one session per chat — all members in common context.
    # Private chats: per-user session as before.
    session_key = chat_id if isinstance(chat_id, int) and chat_id < 0 else uid
    if msg_id is not None:
        api(
            "setMessageReaction",
            chat_id=chat_id,
            message_id=msg_id,
            reaction=json.dumps([{"type": "emoji", "emoji": "👀"}]),
        )

    pending_cal = get_pending_cal(uid)
    full_with_pending = full
    if pending_cal:
        full_with_pending = (
            f"[PENDING_CAL_EVENT] {json.dumps(pending_cal, ensure_ascii=False)}\n\n" + full
        )
        log(f"  pending calendar event for uid={uid} injected into prompt")
    pending_send = get_pending_send(uid)
    if pending_send:
        full_with_pending = (
            f"[PENDING_TG_SEND] {json.dumps(pending_send, ensure_ascii=False)}\n\n"
            + full_with_pending
        )
        log(f"  pending tg-send for uid={uid} injected into prompt")
    nav_prefix = _nav_state_line(chat_id)
    if nav_prefix:
        full_with_pending = nav_prefix + "\n" + full_with_pending

    sid, invalidation = get_session(session_key)
    if invalidation == "vault_changed":
        api("sendMessage", chat_id=chat_id, text="🔄 Vault обновился — начинаю чистую сессию.")
        log(f"session invalidated for uid={uid}: vault_changed")
        reset_session(session_key)
        sid = None
    prompt_for_claude = full_with_pending
    log(f"→ claude (uid={uid}, src={source}, session={sid or 'new'}, {len(full)} chars)")

    t0 = time.time()
    with TypingPulser(chat_id):
        # In group chats placeholder leaks to other bots before deletion → duplicate replies. Suppress.
        _status_chat = chat_id if not (isinstance(chat_id, int) and chat_id < 0) else None
        reply, new_sid, used_tokens, ctx_tokens, _status_mid = call_claude(
            prompt_for_claude, sid, status_chat_id=_status_chat, bg_wait=_bg
        )
    dt = time.time() - t0
    # 2026-06-17: ход был прерван догонкой — его пустой ответ подавлен в call_claude.
    # Ничего не постим, сессию/коммит не трогаем (догонка делает свой полный цикл).
    if reply == _INTERRUPTED_SENTINEL:
        log(f"← claude ({dt:.1f}s) прерван догонкой (uid={uid}) — подавлено")
        return ""
    log(f"← claude ({dt:.1f}s, {len(reply)} chars, session={new_sid}, ctx={used_tokens})")

    # Fix-D/E/G: extract __WROTE__ / __TASK__ markers, build status lines, write manifest.
    reply, wrote_entries = parse_wrote_markers(reply)
    reply, task_texts = parse_task_markers(reply)
    reply, bg_spec = parse_bg_task_marker(reply)
    tasks_appended = append_tasks_from_markers(task_texts)
    wrote_status_lines = []
    for cnt, path in wrote_entries:
        wrote_status_lines.append(f"✓ {path}" + (f" ({cnt} entries)" if cnt > 1 else ""))
    if tasks_appended:
        wrote_status_lines.append(f"✓ tasks.md ({tasks_appended} from-tg)")

    # Calendar markers — propose / confirm / cancel
    reply, cal_propose = parse_cal_propose_marker(reply)
    reply, cal_event = parse_cal_event_marker(reply)
    reply, cal_cancelled = parse_cal_cancel_marker(reply)
    if cal_propose:
        set_pending_cal(uid, cal_propose)
        log(
            f"  __CAL_PROPOSE__ stored for uid={uid}: {cal_propose.get('title', '?')} @ {cal_propose.get('start', '?')}"
        )
        wrote_status_lines.append(
            f"⏳ календарь: ждёт подтверждения ({PENDING_CAL_TTL_SEC // 60}мин)"
        )
    if cal_event:
        clear_pending_cal(uid)
        link = cal_event.get("link") or ""
        title = cal_event.get("title", "событие")
        start = cal_event.get("start", "")
        log(
            f"  __CAL_EVENT__ created for uid={uid}: id={cal_event.get('id', '?')} {title} @ {start}"
        )
        line = f"📅 {title} — {start}"
        if link:
            line += f"\n   {link}"
        wrote_status_lines.append(line)
    if cal_cancelled:
        clear_pending_cal(uid)
        log(f"  __CAL_CANCEL__ for uid={uid}")
        wrote_status_lines.append("❌ календарь: отменено")

    # Telegram-отправка — propose / confirm / cancel (рубеж №2)
    reply, tg_propose = parse_tg_send_propose_marker(reply)
    reply, tg_confirm = parse_tg_send_confirm_marker(reply)
    reply, tg_cancel = parse_tg_send_cancel_marker(reply)
    if tg_propose:
        # claude предложил отправку — резолвим адресата и кладём в pending.
        to_q = str(tg_propose.get("to", "")).strip()
        text = str(tg_propose.get("text", "")).strip()
        if not to_q or not text:
            wrote_status_lines.append("⚠️ отправка: пустой адресат или текст — не ставлю")
        else:
            res = tg_resolve_peer(to_q)
            if res.get("ok"):
                entry = {
                    "to": to_q,
                    "text": text,
                    "peer_id": res.get("peer_id"),
                    "display": res.get("display") or to_q,
                    "username": res.get("username"),
                }
                set_pending_send(uid, entry)
                uname = f" (@{entry['username']})" if entry.get("username") else ""
                log(
                    f"  __TG_SEND_PROPOSE__ stored uid={uid}: → {entry['display']} [{entry['peer_id']}]"
                )
                wrote_status_lines.append(
                    f"⏳ отправить → {entry['display']}{uname}: «{text[:60]}»? "
                    f"скажи «да» ({PENDING_SEND_TTL_SEC // 60}мин)"
                )
            elif res.get("error") == "ambiguous":
                cands = res.get("candidates", [])
                lines = "\n".join(
                    f"  • {c.get('display', '?')}"
                    + (f" (@{c['username']})" if c.get("username") else "")
                    for c in cands
                )
                wrote_status_lines.append(f"⚠️ «{to_q}» — нашёл несколько, уточни кого:\n{lines}")
            elif res.get("error") == "not_found":
                wrote_status_lines.append(
                    f"⚠️ «{to_q}» — не нашёл в твоих чатах. Дай @ник или номер."
                )
            else:
                wrote_status_lines.append(f"⚠️ отправка: ошибка поиска ({res.get('error', '?')})")
    if tg_confirm:
        # Подтверждаем ТОЛЬКО если pending существовал ДО этого turn'а
        # (snapshot pending_send снят перед вызовом claude) — иначе claude мог бы
        # в одном ходе и предложить, и подтвердить, обойдя хозяина.
        if pending_send:
            res = tg_send_now(pending_send.get("peer_id"), pending_send.get("text", ""))
            clear_pending_send(uid)
            if res.get("ok"):
                log(
                    f"  TG SENT uid={uid} → {res.get('display', '?')} [{pending_send.get('peer_id')}]"
                )
                wrote_status_lines.append(
                    f"📤 отправлено → {res.get('display') or pending_send.get('display', '?')}"
                )
            else:
                log(f"  TG SEND FAIL uid={uid}: {res.get('error')}")
                wrote_status_lines.append(f"⚠️ не отправилось: {res.get('error', '?')}")
        else:
            log(f"  __TG_SEND_CONFIRM__ без pending (uid={uid}) — игнор")
            wrote_status_lines.append("⚠️ нечего отправлять — предложения в очереди нет")
    if tg_cancel:
        clear_pending_send(uid)
        log(f"  __TG_SEND_CANCEL__ for uid={uid}")
        wrote_status_lines.append("❌ отправка отменена")

    if wrote_entries or task_texts:
        write_manifest_entry(
            {
                "ts": datetime.now(MSK).isoformat(),
                "uid": uid,
                "chat_id": chat_id,
                "incoming": (full or "")[:120],
                "wrote": [{"count": c, "path": p} for c, p in wrote_entries],
                "tasks_added": tasks_appended,
                "task_summaries": task_texts[:5],
                "session": new_sid,
                "src": source,
            }
        )

    # Failsafe: если claude недоступен (лимит/timeout/ошибка) — сохранить сырое
    # сообщение в inbox.md (корень vault), чтоб не потерять захват. При успехе claude
    # разносит сам через свои tools, дубля не будет.
    if reply.startswith("⚠️") or reply.startswith("⏱"):
        try:
            raw_path = VAULT / "inbox.md"
            ts = datetime.now(MSK).strftime("%Y-%m-%d %H:%M")
            block = f"\n---\n{ts} (TG, BOT_FAILSAFE — claude недоступен, сохранил сырое)\n{full}\n"
            with open(raw_path, "a", encoding="utf-8") as f:
                f.write(block)
            log(f"BOT_FAILSAFE: appended {len(full)} chars to inbox.md")
        except Exception as e:
            log(f"BOT_FAILSAFE write error: {e}")

    if new_sid:
        set_session(session_key, new_sid, add_tokens=used_tokens)

    count = get_msg_count(session_key)
    if count >= COMPACT_FIRST_AT and (count - COMPACT_FIRST_AT) % COMPACT_REPEAT == 0:
        api(
            "sendMessage",
            chat_id=chat_id,
            text=f"⚠️ {count} сообщений в сессии — контекст растёт. /compact чтобы сжать или /new чтобы начать заново.",
        )
    get_total_tokens(session_key)

    reply, choices_labels = parse_choices_marker(reply)
    reply, want_affirm = parse_affirm_marker(reply)
    reply, want_dig = parse_dig_marker(reply)
    reply, voice_reply, voice_engine, voice_name = parse_tts_marker(reply)
    reply, attachment_status = process_attachment_markers(chat_id, reply)

    # Inline images first — strip image paths BEFORE wrapping in status/ctx headers.
    # Иначе при ответе одними путями под фото оставались только заголовки. (2026-05-15)
    reply = extract_and_send_images(chat_id, reply)
    reply = extract_and_send_documents(chat_id, reply)

    status_block_parts = []
    if attachment_status:
        status_block_parts.append(attachment_status)
    if wrote_status_lines:
        status_block_parts.extend(wrote_status_lines)
    if status_block_parts:
        reply = ("\n".join(status_block_parts) + "\n\n" + reply).strip()

    # Флаг: если после извлечения картинок и статусов основной текст пуст —
    # не шлём «пустое» сообщение с одним ctx-заголовком под фото. (2026-05-15)
    skip_send = not reply.strip()

    if used_tokens and not skip_send:

        def _fmt(n):
            if n >= 1_000_000:
                return f"{n / 1_000_000:.2f}M"
            if n >= 1_000:
                return f"{n / 1_000:.1f}k"
            return str(n)

        rtk_str = ""
        try:
            import re as _re

            _jr = subprocess.run(
                ["journalctl", "--user", "-u", "9router", "-n", "100", "--no-pager"],
                capture_output=True,
                text=True,
                timeout=2,
                env={**os.environ, "XDG_RUNTIME_DIR": "/run/user/1000"},
            )
            _m = _re.findall(r"\[RTK\] saved \d+B / \d+B \((\d+(?:\.\d+)?)%\)", _jr.stdout)
            if _m:
                rtk_str = f" ({float(_m[-1]):.0f}%)"
        except Exception:
            pass
        header = f"ctx {_fmt(ctx_tokens)}{rtk_str}"
        reply = f"({header})\n\n{reply}"

    if not skip_send:
        _choices = choices_labels
        if voice_reply:
            ok = send_voice_reply(chat_id, reply, voice=voice_name, engine=voice_engine)
            if not ok:
                send_chunked(
                    chat_id,
                    "⚠️ Голос не синтезировался, отдаю текстом:\n\n" + reply,
                    confirm=want_affirm or needs_confirm(reply),
                    choices=_choices,
                    dig=want_dig,
                )
        else:
            send_chunked(
                chat_id,
                reply,
                confirm=want_affirm or needs_confirm(reply),
                choices=_choices,
                dig=want_dig,
                edit_first_msg_id=_status_mid,
            )

    # 2026-06-19: модель попросила фоновую задачу — поднимаем поток-сторож. Он
    # дождётся конца команды и заведёт отдельный ход с результатом (Design B).
    if bg_spec:
        log(f"  __BG_TASK__ принят (uid={uid}): {bg_spec['label']!r} timeout={bg_spec['timeout']}s")
        threading.Thread(target=_run_bg_task, args=(uid, chat_id, bg_spec), daemon=True).start()

    s_summary = ((summary_text if summary_text is not None else full)[:50] or "msg").replace(
        "\n", " "
    )
    git_sync(f"{datetime.now(MSK).strftime('%Y-%m-%d %H:%M')} {s_summary}")

    # автозапись лога разговора: пересобрать сегодняшний дневной файл для RAG-поиска
    # (Stop-крюк в headless claude -p не вызывается, поэтому делаем тут, в коде бота)
    try:
        subprocess.Popen(
            [
                sys.executable,
                os.environ.get("CHATLOG_SCRIPT", os.path.join(HOME_DIR, "vault-rag/chatlog.py")),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        log(f"  chatlog append err: {e}")

    if new_sid:
        update_vault_head(session_key)

    return reply


# ---------------------------------------------------------------------------
# Fix-J: media_group_id album buffering
# ---------------------------------------------------------------------------
_album_lock = threading.Lock()
_album_buffers = {}  # media_group_id -> {"msgs": [msg, ...], "timer": Timer, "first_chat": int}


def _flush_album(group_id):
    with _album_lock:
        rec = _album_buffers.pop(group_id, None)
    if not rec:
        return
    msgs = rec["msgs"]
    if not msgs:
        return
    log(f"album-flush: group_id={group_id} count={len(msgs)}")
    if len(msgs) == 1:
        # Edge: only one message arrived in 2s — process as normal.
        try:
            _handle_single_message(msgs[0])
        except Exception as e:
            log(f"album single fallthrough err: {e}")
        return
    # Synthesize a wrapper "album" message: pass first msg with all attachments accumulated.
    try:
        _handle_album(msgs)
    except Exception as e:
        log(f"album handler err: {e}")


def _buffer_album_message(group_id, msg):
    """Add msg to album buffer; (re-)arm flush timer for ALBUM_BUFFER_SEC.
    Returns True if the message was buffered (caller must NOT process it now)."""
    with _album_lock:
        rec = _album_buffers.get(group_id)
        if rec is None:
            rec = {"msgs": [msg], "timer": None, "first_chat": msg["chat"]["id"]}
            _album_buffers[group_id] = rec
        else:
            rec["msgs"].append(msg)
            if rec["timer"] is not None:
                rec["timer"].cancel()
        t = threading.Timer(ALBUM_BUFFER_SEC, _flush_album, args=(group_id,))
        t.daemon = True
        rec["timer"] = t
        t.start()
    return True


def _handle_album(msgs):
    """Process a batch of messages with same media_group_id as one logical turn.
    All caption text from the album is concatenated; every attachment gets its own pending."""
    first = msgs[0]
    uid = first["from"]["id"]
    uname = first["from"].get("username") or first["from"].get("first_name", "?")
    chat_id = first["chat"]["id"]
    msg_id = first["message_id"]
    allow = load_allowlist()
    if not allow:
        add_to_allowlist(uid)
        allow = {uid}
    if uid not in allow:
        log(f"BLOCKED uid={uid} ({uname}) [album]")
        return
    captions = []
    saved_paths = []
    failed_pendings = []
    stale_warnings = []
    link_blocks = []
    for m in msgs:
        cap = m.get("caption") or m.get("text") or ""
        if cap:
            captions.append(cap)
        lb = _extract_links(m)
        if lb:
            link_blocks.append(lb)
        att = _extract_attachment_tuple(m)
        if att:
            kind, file_id, ext, summary = att
            status, info = auto_save_attachment(chat_id, kind, file_id, ext, summary)
            if status == "saved":
                saved_paths.append(f"  - {info}  ({kind}, {summary})")
            else:
                pid, stale = info
                failed_pendings.append(f"  - pid={pid} kind={kind} ext={ext} {summary}")
                if stale:
                    stale_warnings.extend(stale)
    text = "\n".join(captions).strip()
    attachment_note_parts = []
    if saved_paths:
        attachment_note_parts.append(
            f"[ATTACHMENT_SAVED] альбом, {len(saved_paths)} файлов уже в vault:\n"
            + "\n".join(saved_paths)
        )
    if failed_pendings:
        attachment_note_parts.append(
            f"[ATTACHMENT_PENDING] {len(failed_pendings)} файлов не скачались "
            f"(auto-save fail, обычно >20MB). Команды: `__SAVE_ATTACHMENT__ <pid> <путь>` "
            f"или `__DROP_ATTACHMENT__ <pid>`. Список:\n" + "\n".join(failed_pendings)
        )
    attachment_note = ("\n" + "\n\n".join(attachment_note_parts)) if attachment_note_parts else ""
    if stale_warnings:
        api(
            "sendMessage",
            chat_id=chat_id,
            text=f"⏳ висит {len(stale_warnings)} pending старше 5 мин — разрешить сначала?",
        )
    links_note = ("\n\n" + "\n".join(link_blocks)) if link_blocks else ""
    full = (text + attachment_note + links_note).strip()
    if not full:
        api("sendMessage", chat_id=chat_id, text="(пустой альбом)")
        return
    process_user_text(
        uid, chat_id, full, msg_id=msg_id, source="tg-album", summary_text=text or "[album]"
    )


def _extract_attachment_tuple(msg):
    """Return (kind, file_id, ext, summary) for the attachment in msg, or None.
    Voice handled separately (transcribed inline)."""
    if "audio" in msg:
        a = msg["audio"]
        ext = Path(a.get("file_name", "")).suffix or ".mp3"
        return (
            "audio",
            a["file_id"],
            ext,
            f"audio {a.get('duration', '?')}s, {a.get('mime_type', '?')}, name={a.get('file_name', '?')}, {a.get('file_size', '?')} bytes",
        )
    if "photo" in msg:
        p = msg["photo"][-1]
        return (
            "photo",
            p["file_id"],
            ".jpg",
            f"photo {p.get('width', '?')}x{p.get('height', '?')}, {p.get('file_size', '?')} bytes",
        )
    if "document" in msg:
        d = msg["document"]
        ext = Path(d.get("file_name", "")).suffix or ""
        return (
            "document",
            d["file_id"],
            ext,
            f"document name={d.get('file_name', '?')}, mime={d.get('mime_type', '?')}, {d.get('file_size', '?')} bytes",
        )
    if "video" in msg:
        v = msg["video"]
        return (
            "video",
            v["file_id"],
            ".mp4",
            f"video {v.get('duration', '?')}s, {v.get('width', '?')}x{v.get('height', '?')}, {v.get('file_size', '?')} bytes",
        )
    if "video_note" in msg:
        v = msg["video_note"]
        return (
            "video_note",
            v["file_id"],
            ".mp4",
            f"video_note {v.get('duration', '?')}s, {v.get('file_size', '?')} bytes",
        )
    return None


def _handle_single_message(msg):
    """Identical to handle_message but skips album-buffer check (used by flush)."""
    handle_message(msg, _from_album=True)


# ---------------------------------------------------------------------------
# Burst (per-user debounce): glue forward+caption / multi-message bursts
# from the same (uid, chat_id) into one logical turn so we make a single
# LLM call instead of N. Slash commands and CDP router run BEFORE this and
# skip buffering. Album (media_group_id) buffering runs BEFORE this too.
# ---------------------------------------------------------------------------
_burst_lock = threading.Lock()
_burst_buffers = {}  # (uid, chat_id) -> {"msgs": [...], "timer": Timer}


def _flush_burst(key):
    with _burst_lock:
        rec = _burst_buffers.pop(key, None)
    if not rec or not rec["msgs"]:
        return
    msgs = rec["msgs"]
    log(f"burst-flush: key={key} count={len(msgs)}")
    try:
        if len(msgs) == 1:
            handle_message(msgs[0], _from_album=True, _from_burst=True)
        else:
            _handle_burst(msgs)
    except Exception as e:
        log(f"burst handler err: {e}")


def _buffer_burst_message(uid, chat_id, msg):
    key = (uid, chat_id)
    with _burst_lock:
        rec = _burst_buffers.get(key)
        if rec is None:
            rec = {"msgs": [msg], "timer": None}
            _burst_buffers[key] = rec
        else:
            rec["msgs"].append(msg)
            if rec["timer"] is not None:
                rec["timer"].cancel()
        t = threading.Timer(BURST_BUFFER_SEC, _flush_burst, args=(key,))
        t.daemon = True
        rec["timer"] = t
        t.start()


def _extract_links(msg):
    """Telegram прячет реальные URL в entities (text_link) и inline-кнопках
    (reply_markup), в msg['text'] их нет — видно только слово-якорь. Собираем
    их в явный блок, чтобы модель видела, куда ведут ссылки в форвардах."""
    text = msg.get("text") or msg.get("caption") or ""
    ents = (msg.get("entities") or []) + (msg.get("caption_entities") or [])
    lines = []
    seen = set()
    for e in ents:
        if e.get("type") == "text_link" and e.get("url"):
            off = e.get("offset", 0)
            ln = e.get("length", 0)
            anchor = text[off : off + ln] if text else ""
            url = e["url"]
            key = (anchor, url)
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"- «{anchor}» → {url}")
    kb = (msg.get("reply_markup") or {}).get("inline_keyboard") or []
    for row in kb:
        for btn in row:
            url = btn.get("url")
            if not url:
                continue
            label = btn.get("text", "?")
            key = (label, url)
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"- [кнопка] «{label}» → {url}")
    if not lines:
        return ""
    return "[ССЫЛКИ В СООБЩЕНИИ]\n" + "\n".join(lines)


def _extract_forward_meta(msg):
    fo = msg.get("forward_origin") or {}
    if fo:
        otype = fo.get("type", "?")
        if otype == "channel":
            ch = fo.get("chat", {})
            return f"[FORWARD_META] Переслано из канала: @{ch.get('username') or '?'} ({ch.get('title') or '?'}), msg_id={fo.get('message_id')}, дата={fo.get('date')}"
        if otype == "user":
            u = fo.get("sender_user", {})
            return f"[FORWARD_META] Переслано от пользователя: @{u.get('username') or '?'} (id={u.get('id')}, имя={u.get('first_name', '?')})"
        if otype == "hidden_user":
            return f"[FORWARD_META] Переслано от скрытого пользователя: {fo.get('sender_user_name', '?')}"
        if otype == "chat":
            ch = fo.get("sender_chat", {})
            return f"[FORWARD_META] Переслано из чата: @{ch.get('username') or '?'} ({ch.get('title') or '?'})"
        return f"[FORWARD_META] type={otype} raw={json.dumps(fo, ensure_ascii=False)[:300]}"
    if msg.get("forward_from"):
        u = msg["forward_from"]
        return (
            f"[FORWARD_META] Переслано от @{u.get('username') or '?'} ({u.get('first_name', '?')})"
        )
    if msg.get("forward_from_chat"):
        ch = msg["forward_from_chat"]
        return f"[FORWARD_META] Переслано из @{ch.get('username') or '?'} ({ch.get('title') or '?'}, type={ch.get('type')})"
    return ""


def _handle_burst(msgs):
    """Process a burst of messages from same (uid,chat) as one turn.
    Concatenates all text/captions, transcribes each voice, auto-saves each
    attachment, appends forward metadata for each forward — single LLM call."""
    first = msgs[0]
    uid = first["from"]["id"]
    chat_id = first["chat"]["id"]
    msg_id = first["message_id"]
    parts = []
    summary_parts = []
    for m in msgs:
        text = m.get("text") or m.get("caption") or ""
        if text:
            summary_parts.append(text)
        attachment_note = ""
        if "voice" in m:
            v = m["voice"]
            dur = v.get("duration", 0)
            transcript = transcribe_voice(v["file_id"], dur, m.get("date"))
            if transcript:
                attachment_note = f"\n[VOICE_TRANSCRIPT] {dur}сек: «{transcript}»"
            else:
                attachment_note = f"\n[VOICE_FAIL] голосовое {dur}сек — транскрипция не удалась"
        else:
            att = _extract_attachment_tuple(m)
            if att:
                kind, file_id, ext, summary = att
                status, info = auto_save_attachment(chat_id, kind, file_id, ext, summary)
                if status == "saved":
                    rel = info
                    attachment_note = (
                        f"\n[ATTACHMENT_SAVED] path={rel} kind={kind} ext={ext} meta={summary}."
                    )
                else:
                    pid, _stale = info
                    attachment_note = (
                        f"\n[ATTACHMENT_PENDING] pid={pid} kind={kind} ext={ext} meta={summary}. "
                        f"Auto-save не сработал (обычно >20MB Bot API)."
                    )
        fwd_meta = _extract_forward_meta(m)
        links_block = _extract_links(m)
        chunk = (
            text
            + attachment_note
            + (("\n\n" + fwd_meta) if fwd_meta else "")
            + (("\n\n" + links_block) if links_block else "")
        ).strip()
        if chunk:
            parts.append(chunk)
    if not parts:
        return
    full = "\n\n---\n\n".join(parts)
    summary_text = " | ".join(summary_parts) if summary_parts else "[burst]"
    process_user_text(
        uid, chat_id, full, msg_id=msg_id, source="tg-burst", summary_text=summary_text
    )


def handle_callback(cq):
    """Inline-button tap. Currently only the ✅ confirm button is wired (callback_data='ok').
    Acks the query, removes the keyboard, and replays 'да' into the user's session."""
    cq_id = cq.get("id")
    data = cq.get("data") or ""
    msg = cq.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    msg_id = msg.get("message_id")
    uid = (cq.get("from") or {}).get("id")
    api("answerCallbackQuery", callback_query_id=cq_id)
    if not (chat_id and uid):
        return
    allow = load_allowlist()
    if allow and uid not in allow:
        return
    # Universal: the message we clicked from must always lose its keyboard.
    # (Covers buttons posted by external scripts the bot doesn't track.)
    if msg_id is not None:
        try:
            api(
                "editMessageReplyMarkup",
                chat_id=chat_id,
                message_id=msg_id,
                reply_markup=json.dumps({"inline_keyboard": []}),
            )
        except Exception as e:
            log(f"strip clicked-msg keyboard: {e}")
        # Forget tracking for this message — it no longer has buttons.
        if _brief_active_msg.get(chat_id) == msg_id:
            _brief_active_msg.pop(chat_id, None)
    if data == "stop":
        proc = None
        with _RUNNING_PROCS_LOCK:
            proc = _RUNNING_PROCS.pop(chat_id, None)
        if proc is not None:
            _STOP_FLAGS[chat_id] = True
            rc_stop = None
            with _RESIDENT_LOCK:
                rc_stop = _RESIDENT.get(chat_id)
            # Мягкое прерывание (как в десктопе): процесс остаётся жив, без
            # холодного старта на следующем ходе. Страховка: если за 4с ход не
            # завершился (процесс реально завис, не читает ввод) — добиваем kill().
            try:
                if rc_stop is not None and rc_stop.is_alive():
                    rc_stop.interrupt()
                    log(f"⏹ STOP by uid={uid} chat={chat_id} — soft interrupt pid={proc.pid}")
                else:
                    proc.kill()
                    log(f"⏹ STOP by uid={uid} chat={chat_id} — no rc, killed pid={proc.pid}")
            except Exception as e:
                log(f"⏹ STOP interrupt error: {e}")
                try:
                    proc.kill()
                except Exception:
                    pass

            def _stop_escalate(p=proc, cid=chat_id):
                time.sleep(4.0)
                if _STOP_FLAGS.get(cid):
                    try:
                        p.kill()
                        log(f"⏹ STOP escalate — interrupt не отпустил за 4с, killed pid={p.pid}")
                    except Exception:
                        pass

            threading.Thread(target=_stop_escalate, daemon=True).start()
            # Текст не трогаем — call_claude доедет до конца и допишет
            # «🛑 остановлено» к тому, что уже успело накопиться.
        else:
            log(f"⏹ STOP by uid={uid} in chat={chat_id} — no running proc")
        return
    if data == "ok":
        reply_text = "да"
        summary = "[✅]"
    elif data == "dig":
        reply_text = "копай"
        summary = "[🔎]"
    elif data.startswith("pick:"):
        label = data.split(":", 1)[1].strip()
        if not label:
            return
        reply_text = label
        summary = f"[{label[:40]}]"
    elif data.startswith("f:"):
        # File-view buttons. Dumb output, no Claude turn.
        handle_brief_file_callback(chat_id, uid, data)
        return
    elif data.startswith("nav:"):
        handle_brief_nav_callback(chat_id, msg_id, data)
        return
    else:
        return
    if msg_id is not None:
        api(
            "editMessageReplyMarkup",
            chat_id=chat_id,
            message_id=msg_id,
            reply_markup=json.dumps({"inline_keyboard": []}),
        )
    process_user_text(uid, chat_id, reply_text, source="callback", summary_text=summary)


# slug → (display_name_in_status, relative_dir). Order = order in the projects list.
# Нейтральный пример. В реальном боте здесь статический список проектов владельца;
# при желании можно генерировать его из структуры каталога Projects/ в vault.
_BRIEF_PROJECTS = [
    ("alpha", "Project Alpha", "Projects/Work/Alpha"),
    ("beta", "Project Beta", "Projects/Work/Beta"),
    ("sub", "Subtask", "Projects/Work/Alpha/Subtask"),
    ("gamma", "Project Gamma", "Projects/Personal/Gamma"),
]
_BRIEF_PROJ_BY_SLUG = {slug: (name, d) for slug, name, d in _BRIEF_PROJECTS}

# Categories shown at top of "projects" menu. Slug → (display, [project_slugs]).
# Projects NOT listed in any category render as flat top-level entries.
# "sub" is intentionally hidden at top level — appears only inside alpha's menu.
_BRIEF_CATEGORIES = [
    ("work", "Work", ["alpha", "beta"]),
    ("personal", "Personal", ["gamma"]),
]
_BRIEF_CAT_BY_SLUG = {slug: (name, kids) for slug, name, kids in _BRIEF_CATEGORIES}
_HIDDEN_AT_TOP = {"sub"}
# Subprojects shown inside a parent project's menu. parent_slug → [child_slugs].
_BRIEF_SUBPROJECTS = {"alpha": ["sub"]}
_VAULT_DIR = str(VAULT)


def _parse_status_dashboard():
    """Read vault/status.md and return {display_name: (mod, state)}."""
    try:
        text = open(os.path.join(_VAULT_DIR, "status.md"), encoding="utf-8").read()
    except OSError:
        return {}
    out = {}
    in_table = False
    for line in text.splitlines():
        if line.startswith("| Мод"):
            in_table = True
            continue
        if in_table:
            if not line.startswith("|"):
                break
            if line.startswith("| ---"):
                continue
            cells = [c.strip() for c in line.split("|")[1:-1]]
            if len(cells) < 3:
                continue
            mod = cells[0]
            name = re.sub(r"\*\*", "", cells[1]).strip()
            name = re.sub(r"\*([^*]+)\*", r"\1", name).strip()
            name = re.sub(r"\s*\([^)]+\)\s*$", "", name).strip()
            state = cells[2]
            out[name] = (mod, state)
    return out


def _shorten(s, limit=140):
    s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s or "")
    s = re.sub(r"`([^`]+)`", r"\1", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) <= limit:
        return s
    cut = s[: limit - 1]
    sp = cut.rfind(" ")
    if sp > limit * 0.6:
        cut = cut[:sp]
    return cut + "…"


def _kb_root():
    return {
        "inline_keyboard": [
            [{"text": "📂 проекты", "callback_data": "nav:projects"}],
            [
                {"text": "📋 задачи", "callback_data": "f:gen_tasks"},
                {"text": "💡 идеи", "callback_data": "f:gen_ideas"},
            ],
            [
                {"text": "📥 inbox", "callback_data": "f:inbox"},
                {"text": "📅 daily", "callback_data": "f:daily"},
            ],
        ]
    }


def _text_root():
    return "*Главное меню моей жизни*\n\nкуда копаем?"


_MOD_RANK = {"🟢": 0, "🔴": 1, "🔵": 2, "🟡": 3, "🔔": 4, "⏸": 5, "·": 6}


def _project_mod(slug, dash):
    entry = _BRIEF_PROJ_BY_SLUG.get(slug)
    if not entry:
        return "·"
    return dash.get(entry[0], ("·", ""))[0]


def _category_mod(cat_slug, dash):
    _, kids = _BRIEF_CAT_BY_SLUG[cat_slug]
    ranks = [_MOD_RANK.get(_project_mod(k, dash), 99) for k in kids]
    if not ranks:
        return "·"
    best = min(ranks)
    for mod, r in _MOD_RANK.items():
        if r == best:
            return mod
    return "·"


def _kb_projects():
    dash = _parse_status_dashboard()
    items = []
    in_cat = {k for _, _, kids in _BRIEF_CATEGORIES for k in kids}
    for idx, (cat_slug, cat_name, _kids) in enumerate(_BRIEF_CATEGORIES):
        mod = _category_mod(cat_slug, dash)
        items.append((_MOD_RANK.get(mod, 99), idx, "cat", cat_slug, cat_name, mod))
    for idx, (slug, name, _) in enumerate(_BRIEF_PROJECTS):
        if slug in in_cat or slug in _HIDDEN_AT_TOP:
            continue
        mod = dash.get(name, ("·", ""))[0]
        items.append((_MOD_RANK.get(mod, 99), 1000 + idx, "proj", slug, name, mod))
    items.sort()
    rows = []
    for _, _, kind, slug, name, mod in items:
        if kind == "cat":
            rows.append([{"text": f"{mod} {name} ▸", "callback_data": f"nav:cat:{slug}"}])
        else:
            rows.append([{"text": f"{mod} {name}", "callback_data": f"nav:proj:{slug}"}])
    rows.append([{"text": "⬅ назад", "callback_data": "nav:root"}])
    return {"inline_keyboard": rows}


def _kb_category(cat_slug):
    name, kids = _BRIEF_CAT_BY_SLUG[cat_slug]
    dash = _parse_status_dashboard()
    items = []
    for idx, child in enumerate(kids):
        entry = _BRIEF_PROJ_BY_SLUG.get(child)
        if not entry:
            continue
        cname, _ = entry
        mod = dash.get(cname, ("·", ""))[0]
        items.append((_MOD_RANK.get(mod, 99), idx, child, cname, mod))
    items.sort()
    rows = [
        [{"text": f"{mod} {cname}", "callback_data": f"nav:proj:{slug}"}]
        for _, _, slug, cname, mod in items
    ]
    rows.append([{"text": "⬅ назад", "callback_data": "nav:projects"}])
    return {"inline_keyboard": rows}


def _text_category(cat_slug):
    name, _ = _BRIEF_CAT_BY_SLUG[cat_slug]
    return f"📂 *{name}* — выбери"


def _text_projects():
    return "📂 *проекты* — выбери"


def _parent_of(slug):
    """Return ('cat', cat_slug) or ('proj', parent_slug) or ('projects', None)."""
    for parent, kids in _BRIEF_SUBPROJECTS.items():
        if slug in kids:
            return ("proj", parent)
    for cat_slug, _, kids in _BRIEF_CATEGORIES:
        if slug in kids:
            return ("cat", cat_slug)
    return ("projects", None)


def _kb_project(slug):
    _, rel = _BRIEF_PROJ_BY_SLUG[slug]
    rows = []
    row = []
    if os.path.exists(os.path.join(_VAULT_DIR, rel, "tasks.md")):
        row.append({"text": "📋 задачи", "callback_data": f"f:t:{slug}"})
    if os.path.exists(os.path.join(_VAULT_DIR, rel, "ideas.md")):
        row.append({"text": "💡 идеи", "callback_data": f"f:i:{slug}"})
    if row:
        rows.append(row)
    if os.path.exists(os.path.join(_VAULT_DIR, rel, "manual.md")):
        rows.append([{"text": "📂 manual", "callback_data": f"f:m:{slug}"}])
    # Drill-in to subprojects (e.g. a child project under a parent).
    dash = _parse_status_dashboard()
    for child in _BRIEF_SUBPROJECTS.get(slug, []):
        centry = _BRIEF_PROJ_BY_SLUG.get(child)
        if not centry:
            continue
        cname, _ = centry
        cmod = dash.get(cname, ("·", ""))[0]
        rows.append([{"text": f"↳ {cmod} {cname}", "callback_data": f"nav:proj:{child}"}])
    kind, ptarget = _parent_of(slug)
    if kind == "cat":
        rows.append([{"text": "⬅ назад", "callback_data": f"nav:cat:{ptarget}"}])
    elif kind == "proj":
        pname, _ = _BRIEF_PROJ_BY_SLUG[ptarget]
        rows.append([{"text": f"⬅ {pname}", "callback_data": f"nav:proj:{ptarget}"}])
    else:
        rows.append([{"text": "⬅ назад", "callback_data": "nav:projects"}])
    return {"inline_keyboard": rows}


def _text_project(slug):
    name, _ = _BRIEF_PROJ_BY_SLUG[slug]
    dash = _parse_status_dashboard()
    mod, state = dash.get(name, ("·", "(нет в status.md)"))
    return f"{mod} *{name}*\n\n{_shorten(state, 300)}"


# Per-chat: id of the message currently holding the brief keyboard.
_brief_active_msg = {}
# Per-chat: which menu level the user is currently "in". Updated on every nav/file
# click and /menu. Injected as [NAV_STATE] into Claude prompts so the headless
# instance knows what the user is looking at.
# Values: "root" | "projects" | ("proj", slug) | None
_brief_level = {}


def _describe_level(level):
    if level == "root":
        return "главное меню (кнопки: проекты / задачи / идеи / inbox)"
    if level == "projects":
        return "список проектов (выбор категории/проекта)"
    if isinstance(level, tuple):
        kind = level[0]
        if kind == "cat":
            entry = _BRIEF_CAT_BY_SLUG.get(level[1])
            if entry:
                return f"категория «{entry[0]}» (выбор проекта)"
            return f"категория {level[1]}"
        if kind == "proj":
            slug = level[1]
            entry = _BRIEF_PROJ_BY_SLUG.get(slug)
            if entry:
                return f"меню проекта «{entry[0]}» (кнопки: manual / задачи / идеи)"
            return f"меню проекта {slug}"
        if kind == "file":
            rel_path = level[1]
            return f"просматривает файл {rel_path}"
    return None


def _nav_state_line(chat_id):
    """Return '[NAV_STATE] ...' prefix line or empty string."""
    level = _brief_level.get(chat_id)
    desc = _describe_level(level) if level else None
    if not desc:
        return ""
    return f"[NAV_STATE] Пользователь сейчас в TG-меню: {desc}.\n"


def _strip_active_keyboard(chat_id):
    """Remove keyboard from the previously active brief message in this chat."""
    old = _brief_active_msg.get(chat_id)
    if not old:
        return
    try:
        api(
            "editMessageReplyMarkup",
            chat_id=chat_id,
            message_id=old,
            reply_markup=json.dumps({"inline_keyboard": []}),
        )
    except Exception as e:
        log(f"strip_active_keyboard: {e}")


def _send_brief(chat_id, text, kb):
    """Send a new brief message at the bottom of chat, transfer the active keyboard."""
    _strip_active_keyboard(chat_id)
    try:
        res = api(
            "sendMessage",
            chat_id=chat_id,
            text=md_to_html(text),
            parse_mode="HTML",
            reply_markup=json.dumps(kb, ensure_ascii=False),
            disable_web_page_preview="true",
        )
    except Exception as e:
        log(f"_send_brief: {e}")
        return
    if res.get("ok"):
        _brief_active_msg[chat_id] = res["result"]["message_id"]


def _level_text_kb(level):
    """level: 'root' | 'projects' | ('proj', slug)"""
    if level == "root":
        return _text_root(), _kb_root()
    if level == "projects":
        return _text_projects(), _kb_projects()
    if isinstance(level, tuple) and level[0] == "proj":
        return _text_project(level[1]), _kb_project(level[1])
    if isinstance(level, tuple) and level[0] == "cat":
        return _text_category(level[1]), _kb_category(level[1])
    return _text_root(), _kb_root()


def handle_brief_nav_callback(chat_id, msg_id, data):
    """Navigation between morning-brief menus. Sends new message at bottom, strips old."""
    parts = data.split(":")
    if len(parts) == 2 and parts[1] == "root":
        level = "root"
    elif len(parts) == 2 and parts[1] == "projects":
        level = "projects"
    elif len(parts) == 3 and parts[1] == "proj":
        slug = parts[2]
        if slug not in _BRIEF_PROJ_BY_SLUG:
            return
        level = ("proj", slug)
    elif len(parts) == 3 and parts[1] == "cat":
        cat_slug = parts[2]
        if cat_slug not in _BRIEF_CAT_BY_SLUG:
            return
        level = ("cat", cat_slug)
    else:
        return
    # If the clicked button was on the currently-active message, also treat it
    # as the active one (sync state in case external script sent the first menu).
    if msg_id and chat_id not in _brief_active_msg:
        _brief_active_msg[chat_id] = msg_id
    _brief_level[chat_id] = level
    text, kb = _level_text_kb(level)
    _send_brief(chat_id, text, kb)


def handle_brief_file_callback(chat_id, uid, data):
    """callback_data formats:
    - f:t:<slug>   → Projects/<dir>/tasks.md
    - f:i:<slug>   → Projects/<dir>/ideas.md
    - f:m:<slug>   → Projects/<dir>/manual.md
    - f:gen_tasks  → Tasks/tasks.md
    - f:gen_ideas  → Tasks/ideas.md
    - f:inbox      → inbox.md
    - f:daily      → запустить /daily-prep как user-сообщение
    """
    parts = data.split(":")
    rel_path = None
    # After dumping a file: which "back" target the post-file keyboard points to.
    back_to_slug = None  # set for project files only
    if len(parts) == 2:
        key = parts[1]
        if key == "gen_tasks":
            rel_path, _label = "Tasks/tasks.md", "Tasks/tasks.md"
        elif key == "gen_ideas":
            rel_path, _label = "Tasks/ideas.md", "Tasks/ideas.md"
        elif key == "inbox":
            rel_path, _label = "inbox.md", "inbox.md"
        elif key == "daily":
            process_user_text(
                uid, chat_id, "/daily-prep", source="callback", summary_text="[📅 daily]"
            )
            return
    elif len(parts) == 3:
        kind, slug = parts[1], parts[2]
        entry = _BRIEF_PROJ_BY_SLUG.get(slug)
        if not entry:
            return
        _, proj_dir = entry
        fname = {"t": "tasks.md", "i": "ideas.md", "m": "manual.md"}.get(kind)
        if not fname:
            return
        rel_path = f"{proj_dir}/{fname}"
        back_to_slug = slug
    if not rel_path:
        return
    # Build nav row: project files → [⬅ <project>] [🏠 МЕНЮ]; general → [⬅ назад] [🏠 МЕНЮ].
    if back_to_slug:
        name, _ = _BRIEF_PROJ_BY_SLUG[back_to_slug]
        back_btn = {"text": f"⬅ {name}", "callback_data": f"nav:proj:{back_to_slug}"}
    else:
        back_btn = {"text": "⬅ назад", "callback_data": "nav:root"}
    # Track that the user is now LOOKING AT the file, not at a menu level.
    _brief_level[chat_id] = ("file", rel_path)
    nav_rows = [[back_btn, _MENU_ROW[0]]]
    full = os.path.join(_VAULT_DIR, rel_path)
    try:
        with open(full, encoding="utf-8") as f:
            body = f.read()
    except FileNotFoundError:
        send_chunked(chat_id, f"⚠️ нет файла: {rel_path}", menu_rows=nav_rows)
        return
    body = _clean_file_for_view(body)
    if not body.strip():
        send_chunked(chat_id, f"📄 {rel_path}\n\n(пусто)", menu_rows=nav_rows)
        return
    send_chunked(chat_id, f"📄 *{rel_path}*\n\n{body}", menu_rows=nav_rows)


def _clean_file_for_view(body):
    """Strip служебную информацию for TG file dumps:
    - YAML-ish frontmatter (---...--- at file head)
    - HTML comments <!-- ... -->
    - Leading blockquotes and italic-meta paragraphs above the first content line
    """
    # 1. Strip frontmatter.
    m = re.match(r"\A\s*---\s*\n.*?\n---\s*\n", body, flags=re.DOTALL)
    if m:
        body = body[m.end() :]
    # 2. Strip HTML comments.
    body = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
    # 3. Strip "header noise" between headings and the first real content line:
    # blockquotes (>), italic-only meta paragraphs (*...*), blank lines.
    # Skipping mode RESETS after each ## / ### heading, so the same filter
    # cleans the preamble of every section, not just the file top.
    lines = body.splitlines()
    out = []
    skipping = True
    for ln in lines:
        s = ln.strip()
        # Drop file-level heading (# X) — redundant with the breadcrumb header.
        if s.startswith("# ") and not s.startswith("## "):
            continue
        # Section heading: keep, then re-enter skipping for the section's preamble.
        if s.startswith("## "):
            out.append(ln)
            skipping = True
            continue
        if skipping:
            if not s:
                continue
            # Any blockquote line, including bare ">" (paragraph break inside quote).
            if s.startswith(">"):
                continue
            if s.startswith("*") and s.endswith("*") and not s.startswith("**"):
                continue
            skipping = False
        out.append(ln)
    cleaned = "\n".join(out)
    cleaned = _drop_empty_sections(cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


# Marker of "real content" inside a section: bullet, checkbox, numbered list,
# separator line (---), table row, or fenced code block.
_CONTENT_MARKER_RE = re.compile(r"(?m)^\s*(?:[-*+]\s|\d+[\.\)]\s|---\s*$|\|.+\||```)")


def _drop_empty_sections(text):
    """Drop any `## Section` whose body has no content marker (bullet/separator/etc).
    Such sections are documentation/explanatory and don't belong in a TG file view."""
    parts = re.split(r"(?m)^(?=## )", text)
    out_parts = []
    for i, part in enumerate(parts):
        if i == 0:
            out_parts.append(part)  # pre-section preamble (already filtered above)
            continue
        # Strip the heading line itself before testing the body.
        body = part.split("\n", 1)[1] if "\n" in part else ""
        if _CONTENT_MARKER_RE.search(body):
            out_parts.append(part)
    return "".join(out_parts)


def handle_message(msg, _from_album=False, _from_burst=False):
    uid = msg["from"]["id"]
    uname = msg["from"].get("username") or msg["from"].get("first_name", "?")
    chat_id = msg["chat"]["id"]
    msg_id = msg["message_id"]
    chat_type = (msg.get("chat") or {}).get("type", "private")
    fname = msg["from"].get("first_name", "") or ""
    lname = msg["from"].get("last_name", "") or ""
    author_label = (fname + " " + lname).strip() or msg["from"].get("username") or str(uid)

    # Group/supergroup: owner always; others — only if bot is @mentioned or replied-to.
    mentioned = False
    is_reply_to_bot = False
    if chat_type in ("group", "supergroup"):
        BOT_USERNAME = os.environ.get("BOT_USERNAME", "your_bot_username")
        OWNER_UID = allowlist_owner()
        text_check = msg.get("text") or msg.get("caption") or ""
        ents = (msg.get("entities") or []) + (msg.get("caption_entities") or [])
        mentioned = any(
            e.get("type") == "mention"
            and ("@" + BOT_USERNAME)
            in text_check[e.get("offset", 0) : e.get("offset", 0) + e.get("length", 0)]
            for e in ents
        )
        # Fallback: messages from other bots may arrive without parsed entities.
        if not mentioned and ("@" + BOT_USERNAME).lower() in text_check.lower():
            mentioned = True
        reply_to = msg.get("reply_to_message") or {}
        is_reply_to_bot = (reply_to.get("from") or {}).get("username") == BOT_USERNAME
        # NEW: if message starts with @<other-bot>, skip — it's addressed to someone else.
        stripped = text_check.lstrip()
        if stripped.startswith("@") and not stripped.lower().startswith("@" + BOT_USERNAME.lower()):
            log(
                f"GROUP-SKIP uid={uid} ({author_label}) addressed to other @-mention: {stripped[:40]}"
            )
            return
        if uid != OWNER_UID and not mentioned and not is_reply_to_bot:
            log(f"GROUP-SKIP uid={uid} ({author_label}) no mention/reply")
            return

    # Fix-J: Telegram albums arrive as multiple messages with the same media_group_id
    # within a few hundred ms. Buffer them and process the whole album as one turn.
    group_id = msg.get("media_group_id")
    if group_id and not _from_album:
        _buffer_album_message(str(group_id), msg)
        return

    allow = load_allowlist()
    if not allow:
        add_to_allowlist(uid)
        allow = {uid}
    if uid not in allow:
        # In groups: allow through if bot was explicitly @mentioned or replied-to.
        if chat_type in ("group", "supergroup") and (mentioned or is_reply_to_bot):
            log(f"GROUP-ALLOW uid={uid} ({uname}) via mention/reply (not in allowlist)")
        else:
            log(f"BLOCKED uid={uid} ({uname})")
            return

    text = msg.get("text") or msg.get("caption") or ""

    # Forward metadata
    fwd_meta = ""
    fo = msg.get("forward_origin") or {}
    if fo:
        otype = fo.get("type", "?")
        if otype == "channel":
            ch = fo.get("chat", {})
            fwd_meta = f"[FORWARD_META] Переслано из канала: @{ch.get('username') or '?'} ({ch.get('title') or '?'}), msg_id={fo.get('message_id')}, дата={fo.get('date')}"
        elif otype == "user":
            u = fo.get("sender_user", {})
            fwd_meta = f"[FORWARD_META] Переслано от пользователя: @{u.get('username') or '?'} (id={u.get('id')}, имя={u.get('first_name', '?')})"
        elif otype == "hidden_user":
            fwd_meta = f"[FORWARD_META] Переслано от скрытого пользователя: {fo.get('sender_user_name', '?')}"
        elif otype == "chat":
            ch = fo.get("sender_chat", {})
            fwd_meta = f"[FORWARD_META] Переслано из чата: @{ch.get('username') or '?'} ({ch.get('title') or '?'})"
        else:
            fwd_meta = f"[FORWARD_META] type={otype} raw={json.dumps(fo, ensure_ascii=False)[:300]}"
    elif msg.get("forward_from"):
        u = msg["forward_from"]
        fwd_meta = (
            f"[FORWARD_META] Переслано от @{u.get('username') or '?'} ({u.get('first_name', '?')})"
        )
    elif msg.get("forward_from_chat"):
        ch = msg["forward_from_chat"]
        fwd_meta = f"[FORWARD_META] Переслано из @{ch.get('username') or '?'} ({ch.get('title') or '?'}, type={ch.get('type')})"

    # Slash commands handled locally
    if text.strip() == "/compact":
        do_compact(uid, chat_id)
        return
    if text.strip() == "/new":
        reset_session(uid)
        # 2026-06-06 fix: убить резидентный claude — иначе он подхватит старую сессию
        # через свой resume_sid и /new будет фикцией (sessions.json чист, jsonl продолжается).
        with _RESIDENT_LOCK:
            rc_kill = _RESIDENT.pop(chat_id, None)
        if rc_kill:
            try:
                rc_kill.kill()
            except Exception as e:
                log(f"  /new: resident kill err: {e}")
        api("sendMessage", chat_id=chat_id, text="✅ Новая сессия. Прошлый контекст сброшен.")
        return
    if text.strip() == "/menu":
        _brief_level[chat_id] = "root"
        _send_brief(chat_id, _text_root(), _kb_root())
        return
    if text.strip() == "/start":
        api(
            "sendMessage",
            chat_id=chat_id,
            text="Привет. Я тот же ассистент, что у тебя в Claude Code. Пиши что угодно — мысль/идею/вопрос. Перед сохранением переспрошу. /new — новая сессия. Меню «/» — список скилов.",
        )
        return

    # Skill aliases: TG menu allows only [a-z0-9_], skills use hyphens.
    # Rewrite "/process_inbox foo" → "/process-inbox foo" before forwarding to Claude.
    text = _rewrite_skill_alias(text)

    # Browser-automation router: Avito/Ozon commands -> do-queue.md (Mac/CDP executes)
    if text and COMMAND_RE.search(text):
        route_to_do_queue(text)
        api(
            "sendMessage",
            chat_id=chat_id,
            text=f"✅ Команда в очередь:\n«{text[:160]}»\n\nИсполнится при заходе на Mac (CDP-Chrome).",
        )
        return

    # Burst debounce: glue forward+caption and multi-message bursts into one LLM call.
    if not _from_burst:
        _buffer_burst_message(uid, chat_id, msg)
        return

    # Attachments — DO NOT download yet. Stash metadata, ask Claude to confirm.
    attachment_note = ""
    att = None
    if "voice" in msg:
        v = msg["voice"]
        dur = v.get("duration", 0)
        transcript = transcribe_voice(v["file_id"], dur, msg.get("date"))
        if transcript:
            attachment_note = f"\n[VOICE_TRANSCRIPT] {dur}сек: «{transcript}»"
        else:
            attachment_note = f"\n[VOICE_FAIL] голосовое {dur}сек — транскрипция не удалась"
        att = None  # не ставим pending для голосовых
    else:
        att = _extract_attachment_tuple(msg)

    if att:
        kind, file_id, ext, summary = att
        status, info = auto_save_attachment(chat_id, kind, file_id, ext, summary)
        if status == "saved":
            rel = info
            attachment_note = (
                f"\n[ATTACHMENT_SAVED] path={rel} kind={kind} ext={ext} meta={summary}. "
                f"Файл уже в vault — читай/атомизируй/переноси как обычный файл, "
                f"спец-команды боту не нужны."
            )
        else:
            pid, stale = info
            if stale:
                stale_summary = ", ".join(f"{r['kind']}/{r.get('ext', '?')}" for r in stale[:3])
                api(
                    "sendMessage",
                    chat_id=chat_id,
                    text=f"⏳ висит {len(stale)} pending старше 5 мин ({stale_summary}) — разрешить сначала?",
                )
            all_pending = get_pending_list(chat_id)
            if len(all_pending) <= 1:
                attachment_note = (
                    f"\n[ATTACHMENT_PENDING] pid={pid} kind={kind} ext={ext} meta={summary}. "
                    f"Auto-save не сработал (обычно >20MB Bot API). "
                    f"Если нужно — первая строка ответа `__SAVE_ATTACHMENT__ {pid} <путь>` "
                    f"или `__DROP_ATTACHMENT__ {pid}`."
                )
            else:
                descs = [
                    f"  - pid={r['pid']} kind={r['kind']} ext={r['ext']} {r['summary']}"
                    for r in all_pending
                ]
                attachment_note = (
                    f"\n[ATTACHMENT_PENDING] в очереди {len(all_pending)} файлов "
                    f"(новый pid={pid}, auto-save fail). Команды: `__SAVE_ATTACHMENT__ <pid> <путь>` / "
                    f"`__DROP_ATTACHMENT__ <pid>` / `__DROP_ALL_ATTACHMENTS__`. "
                    f"Список:\n" + "\n".join(descs)
                )

    # voice decision moved to reply parser (parse_tts_marker)

    links_block = _extract_links(msg)
    full = (
        text
        + attachment_note
        + ("\n\n" + fwd_meta if fwd_meta else "")
        + ("\n\n" + links_block if links_block else "")
    ).strip()
    if chat_type in ("group", "supergroup") and full:
        full = f"[от {author_label} (uid={uid})]: {full}"
    if not full:
        return

    process_user_text(uid, chat_id, full, msg_id=msg_id, source="tg", summary_text=text)


def do_compact(uid, chat_id):
    """Summarise current session via Claude, start fresh session with that summary as context."""
    sid, _ = get_session(uid)
    if not sid:
        api("sendMessage", chat_id=chat_id, text="Нет активной сессии — нечего компактить.")
        return

    api("sendMessage", chat_id=chat_id, text="⏳ Сжимаю контекст...")

    compact_prompt = (
        "СИСТЕМНОЕ ДЕЙСТВИЕ: Составь конспект нашего разговора для переноса контекста. "
        "До 600 символов, без вступления:\n"
        "- Темы разговора\n"
        "- Что решили / сделали\n"
        "- Открытые задачи и вопросы\n"
        "- На чём остановились последним"
    )
    with TypingPulser(chat_id):
        summary, _, _, _, _ = call_claude(compact_prompt, sid)

    if summary.startswith("⚠️") or summary.startswith("⏱"):
        api("sendMessage", chat_id=chat_id, text=f"Не удалось получить конспект:\n{summary}")
        return

    handoff = (
        f"[COMPACT] Продолжаем разговор. Конспект предыдущей сессии:\n\n{summary}\n\n"
        "---\nПродолжай в том же стиле."
    )
    # 2026-06-06 fix: убить резидент перед новой сессией, иначе handoff уйдёт в старую.
    with _RESIDENT_LOCK:
        rc_kill = _RESIDENT.pop(chat_id, None)
    if rc_kill:
        try:
            rc_kill.kill()
        except Exception as e:
            log(f"  /compact: resident kill err: {e}")
    with TypingPulser(chat_id):
        _, new_sid, _, _, _ = call_claude(handoff, None)

    if not new_sid:
        api("sendMessage", chat_id=chat_id, text="⚠️ Не удалось начать новую сессию после компакта.")
        return

    set_session(uid, new_sid, msg_count=1)
    update_vault_head(uid)
    api(
        "sendMessage",
        chat_id=chat_id,
        text=f"✅ Контекст сжат, новая сессия начата.\n\nКонспект:\n{summary}",
    )


_ATTACH_LINE_RE = re.compile(
    r"^\s*__(SAVE|DROP)_ATTACHMENT__(?:\s+(\S+))?(?:\s+(\S.*?))?\s*$",
    re.MULTILINE,
)
_DROP_ALL_RE = re.compile(r"^\s*__DROP_ALL_ATTACHMENTS__\s*$", re.MULTILINE)


def process_attachment_markers(chat_id, reply):
    """Fix-C: parse one or more attachment-action lines anywhere in reply.

    Supported forms (each on its own line):
      __SAVE_ATTACHMENT__ <pid> <relative/path.ext>
      __SAVE_ATTACHMENT__ <relative/path.ext>     (back-compat: only valid when queue has 1 pending)
      __DROP_ATTACHMENT__ <pid>
      __DROP_ATTACHMENT__                         (back-compat: drops oldest pending)
      __DROP_ALL_ATTACHMENTS__

    Returns (cleaned_reply, joined_status_string_or_None). Logs every branch.
    """
    if not reply:
        return reply, None

    statuses = []

    # 1) __DROP_ALL_ATTACHMENTS__ first (gobble whole queue if requested)
    if _DROP_ALL_RE.search(reply):
        before = len(get_pending_list(chat_id))
        clear_pending(chat_id)
        log(f"attach-marker: DROP_ALL chat={chat_id} cleared={before}")
        if before:
            statuses.append(f"🗑 очистил очередь pending ({before} шт.)")
        else:
            statuses.append("🗑 очередь pending была пуста")
        reply = _DROP_ALL_RE.sub("", reply)

    # 2) Per-pending lines
    matches = list(_ATTACH_LINE_RE.finditer(reply))
    if matches:
        # Strip them from the reply text
        new_reply = _ATTACH_LINE_RE.sub("", reply)
    else:
        new_reply = reply

    for m in matches:
        action = m.group(1).upper()
        a1 = m.group(2)
        a2 = m.group(3)
        log(f"attach-marker: {action} a1={a1!r} a2={a2!r}")

        if action == "DROP":
            # __DROP_ATTACHMENT__ [pid]
            pid = a1
            if pid:
                rec = pop_pending_by_pid(chat_id, pid)
                if rec:
                    statuses.append(f"🗑 не сохраняю pid={pid}")
                else:
                    statuses.append(f"⚠️ DROP: pid={pid} не найден (уже выкинут или TTL)")
            else:
                rec = pop_pending_oldest(chat_id)
                if rec:
                    statuses.append(f"🗑 не сохраняю pid={rec['pid']}")
                else:
                    statuses.append("⚠️ DROP: очередь pending пуста")
            continue

        # action == "SAVE": __SAVE_ATTACHMENT__ <pid> <path>  OR  __SAVE_ATTACHMENT__ <path>
        # Heuristic: a1 is pid if it's exactly 8 hex chars; otherwise a1 is the path.
        pid = None
        rel = None
        if a1 and re.fullmatch(r"[0-9a-fA-F]{8}", a1):
            pid = a1
            rel = (a2 or "").strip()
        else:
            rel = ((a1 or "") + (" " + a2 if a2 else "")).strip()
        if not rel:
            statuses.append("⚠️ SAVE: путь не указан")
            continue

        # Locate the pending
        if pid:
            rec = pop_pending_by_pid(chat_id, pid)
            if not rec:
                statuses.append(f"⚠️ SAVE: pid={pid} не найден (TTL/уже сохранён)")
                continue
        else:
            queue = get_pending_list(chat_id)
            if not queue:
                statuses.append("⚠️ SAVE: нет pending-вложения (TTL 1ч истёк)")
                continue
            if len(queue) > 1:
                statuses.append(
                    f"⚠️ SAVE без pid отклонён: в очереди {len(queue)} файлов — укажи pid"
                )
                continue
            rec = pop_pending_oldest(chat_id)
            if not rec:
                statuses.append("⚠️ SAVE: pending исчез гонкой")
                continue

        dest = safe_vault_path(rel)
        if not dest:
            statuses.append(f"⚠️ путь `{rel}` отвергнут (вне vault или запрещённая зона)")
            log(f"attach-marker: SAVE rejected — unsafe path rel={rel!r}")
            continue

        log(f"attach-marker: SAVE pid={rec['pid']} file_id={rec['file_id'][:20]}… dest={dest}")
        ok = download_file_to(rec["file_id"], str(dest))
        if ok:
            statuses.append(f"✅ сохранил → {dest.relative_to(VAULT)}")
        else:
            statuses.append(f"⚠️ скачать не удалось (pid={rec['pid']})")
            log("attach-marker: SAVE — download_file_to returned False")

    if statuses:
        # Tidy reply: collapse triple+ blank lines that the regex strip left behind.
        new_reply = re.sub(r"\n{3,}", "\n\n", new_reply).strip()
        return new_reply, "\n".join(statuses)
    return reply, None


class VoiceHandler(http.server.BaseHTTPRequestHandler):
    """HTTP endpoint for iPhone Shortcut voice → Claude.

    POST /voice  — iPhone Shortcut voice: echoes "🎙 <text>" then the reply.
    POST /inject — scheduled brief (claude-task.sh): no echo, source="scheduled",
                   delivers the prompt into the resident session so the brief is a
                   real remembered turn instead of a one-shot headless claude.
      Headers: X-Auth-Token: <SHORTCUT_TOKEN>
      Body:    {"text": "..."}
    Response: {"ok": true, "reply": "..."} (200) or error (4xx/5xx).

    Side effects: the assistant reply is posted into the TG private chat by
    process_user_text; /voice additionally echoes the user text first.
    """

    def _send_json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, code, text):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _reply(self, fmt_text, code, ok, payload):
        """Unified reply: payload is reply text on success, error string on failure."""
        if fmt_text:
            self._send_text(code, payload if ok else f"ERROR: {payload}")
        else:
            self._send_json(
                code, {"ok": True, "reply": payload} if ok else {"ok": False, "error": payload}
            )

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        fmt_text = query.get("fmt", [""])[0] == "text"

        if parsed.path not in ("/voice", "/inject"):
            self._reply(fmt_text, 404, False, "not found")
            return
        token = self.headers.get("X-Auth-Token", "")
        if not SHORTCUT_TOKEN or token != SHORTCUT_TOKEN:
            self._reply(fmt_text, 401, False, "unauthorized")
            return
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
            body = self.rfile.read(n) if n else b""
            data = json.loads(body or b"{}")
        except Exception as e:
            self._reply(fmt_text, 400, False, f"bad json: {e}")
            return
        text = (data.get("text") or "").strip()
        if not text:
            self._reply(fmt_text, 400, False, "empty text")
            return

        uid = allowlist_owner()
        if uid is None:
            self._reply(fmt_text, 503, False, "no allowlist owner")
            return
        chat_id = uid  # private TG chat: chat_id == user_id (owner = первая строка allowlist)

        # /voice = spoken user message → echo "🎙 …" so chat mirrors a normal
        # exchange. /inject = scheduled brief prompt → deliver straight into the
        # resident session WITHOUT echoing the prompt, so the brief becomes a real
        # turn the live assistant remembers (was: one-shot headless claude in
        # claude-task.sh that posted via raw sendMessage and bypassed this process).
        if parsed.path == "/voice":
            api("sendMessage", chat_id=chat_id, text=f"🎙 {text}")
            src = "shortcut"
        else:
            src = "scheduled"

        try:
            reply = process_user_text(uid, chat_id, text, source=src)
        except Exception as e:
            log(f"{parsed.path} endpoint error: {e}")
            self._reply(fmt_text, 500, False, str(e))
            return

        self._reply(fmt_text, 200, True, reply)

    def log_message(self, fmt, *args):
        log(f"voice-http: {self.address_string()} {fmt % args}")


class _TLSThreadingHTTPServer(http.server.ThreadingHTTPServer):
    """TLS оборачивается per-connection: рукопожатие идёт в потоке-обработчике,
    а не в accept-цикле. Если wrap делать на listening-сокете (как было до
    2026-06-23), один медленный/зависший TLS-клиент морозит приём ВСЕХ
    соединений — из-за этого scheduled-брифинг отвалился с http=000 (curl: (28)
    Failed to connect after 133s), пока accept висел на чужом handshake."""

    daemon_threads = True

    def __init__(self, addr, handler, ssl_ctx):
        self._ssl_ctx = ssl_ctx
        super().__init__(addr, handler)

    def get_request(self):
        sock, addr = self.socket.accept()
        sock.settimeout(30)  # мёртвый клиент не держит поток вечно
        tls = self._ssl_ctx.wrap_socket(sock, server_side=True, do_handshake_on_connect=False)
        return tls, addr


def start_voice_server():
    if not SHORTCUT_TOKEN:
        log("voice endpoint disabled (no shortcut-token configured)")
        return
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=VOICE_CERT, keyfile=VOICE_KEY)
    except Exception as e:
        log(f"voice endpoint TLS failed: {e}; refusing to start without TLS")
        return
    try:
        httpd = _TLSThreadingHTTPServer(("", VOICE_PORT), VoiceHandler, ctx)
    except Exception as e:
        log(f"voice endpoint bind failed on :{VOICE_PORT}: {e}")
        return
    log(f"voice endpoint: https://0.0.0.0:{VOICE_PORT}/voice (TLS, per-conn handshake)")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()


def setup_bot_menu():
    """Register slash-command menu in TG client (the '/' button)."""
    cmds = [{"command": c, "description": d} for c, d in BOT_MENU_COMMANDS]
    payload = json.dumps({"commands": cmds}).encode()
    req = urllib.request.Request(
        f"{API}/setMyCommands",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            res = json.load(r)
        if res.get("ok"):
            log(f"setMyCommands ok: {len(cmds)} commands")
        else:
            log(f"setMyCommands not ok: {res}")
    except Exception as e:
        log(f"setMyCommands error: {e}")


def main():
    log(f"bot v2 started, vault={VAULT}, allowlist={load_allowlist()}")
    setup_bot_menu()
    cleanup_voice_archive()
    _load_whisper()
    start_voice_server()
    offset = get_offset()
    while True:
        try:
            req = urllib.request.Request(f"{API}/getUpdates?timeout=30&offset={offset}")
            with urllib.request.urlopen(req, timeout=40) as r:
                data = json.load(r)
            if not data.get("ok"):
                log(f"getUpdates not ok: {data}")
                time.sleep(5)
                continue
            for upd in data["result"]:
                offset = upd["update_id"] + 1
                if "message" in upd:
                    try:
                        handle_message(upd["message"])
                    except Exception as e:
                        log(f"handle_message error: {e}")
                        try:
                            api(
                                "sendMessage",
                                chat_id=upd["message"]["chat"]["id"],
                                text=f"⚠️ Внутренняя ошибка: {e}",
                            )
                        except Exception:
                            pass
                elif "callback_query" in upd:
                    try:
                        handle_callback(upd["callback_query"])
                    except Exception as e:
                        log(f"handle_callback error: {e}")
                save_offset(offset)
        except urllib.error.URLError as e:
            log(f"network: {e}; retry in 10s")
            time.sleep(10)
        except Exception as e:
            log(f"loop error: {e}; retry in 5s")
            time.sleep(5)


if __name__ == "__main__":
    main()
