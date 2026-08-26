#!/usr/bin/env python3
"""Голос в обе стороны: расшифровка входящих и озвучка ответов.

Расшифровка идёт цепочкой движков: быстрый облачный, потом родная расшифровка
самого Telegram, потом точный облачный для длинных записей. Падение любого
звена молча уводит на следующее — человек не должен видеть «сервис недоступен»,
он должен получить текст. Выключенный в профиле движок просто пропускается.

Озвучка — тот же принцип: мемные голоса через сторонний сервис, обычный голос
через локальный синтезатор. Нет ключа или бинарника — функция выключается на
старте проверкой профиля, а не падает посреди разговора.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from botctx import (
    API, ASSEMBLYAI_KEY_FILE, EDGE_TTS_BIN, GROQ_KEY_FILE, GROQ_MODEL, MSK,
    NATIVE_MIN_SEC, PROFILE, TTS_VOICE_DEFAULT, VOICE_ARCHIVE_DIR,
    VOICE_ARCHIVE_TTL_DAYS, VOICE_DIARIZE_SEC, VOICE_LONG_THRESHOLD_SEC, VOICE_REPLY_MAX_CHARS,
    ZVUKOGRAM_API, ZVUKOGRAM_EMAIL_PATH, ZVUKOGRAM_KEY_PATH, log,
)
from tgapi import md_to_html, strip_html
from media import download_file_to


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
                    try: f.unlink()
                    except Exception: pass
                try: d.rmdir()
                except Exception: pass
                log(f"voice-archive cleanup: dropped {d}")
    except Exception as e:
        log(f"voice-archive cleanup err: {e}")


def _read_assemblyai_key():
    try:
        with open(ASSEMBLYAI_KEY_FILE, "r") as f:
            return f.read().strip() or None
    except Exception:
        return None


def transcribe_assemblyai(file_path, diarize=False):
    """Upload .oga to AssemblyAI, poll until done, return text or None.
    Запасной путь расшифровки, когда Groq и родная расшифровка Telegram не сработали.
    diarize=True → просим разбор по голосам, текст возвращается репликами
    вида `[MM:SS] [Speaker A] ...` (созвоны на несколько человек)."""
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
        _payload = {"audio_url": upload_url, "language_code": "ru", "speech_models": ["universal-2"]}
        if diarize:
            _payload["speaker_labels"] = True
        body = json.dumps(_payload).encode()
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
                if diarize and j.get("utterances"):
                    lines = []
                    for u in j["utterances"]:
                        sec = int((u.get("start") or 0) / 1000)
                        lines.append(
                            f"[{sec // 60:02d}:{sec % 60:02d}] [Speaker {u.get('speaker', '?')}] "
                            f"{(u.get('text') or '').strip()}"
                        )
                    if lines:
                        log(f"AssemblyAI: диаризация, реплик {len(lines)}")
                        return "\n".join(lines)
                return (j.get("text") or "").strip() or None
            if st == "error":
                log(f"AssemblyAI error: {j.get('error')}")
                return None
        log("AssemblyAI: timeout")
        return None
    except Exception as e:
        log(f"AssemblyAI exception: {type(e).__name__}: {e}")
        return None


NATIVE_VENV_PY = PROFILE["tg_send_python"]
NATIVE_HELPER = PROFILE["native_helper"]


def transcribe_voice_native(msg_date, duration):
    """Родная расшифровка Telegram через пользовательскую сессию хозяина (Premium).
    Ищет голосовое по времени отправки + длительности. Возвращает текст или None."""
    if not msg_date:
        return None
    try:
        r = subprocess.run([NATIVE_VENV_PY, NATIVE_HELPER, str(int(msg_date)), str(int(duration or 0))],
                           capture_output=True, text=True, timeout=25)
        if r.returncode == 0:
            txt = (r.stdout or "").strip()
            if txt:
                log(f"voice native ok date={msg_date} chars={len(txt)}")
                return txt
        log(f"voice native miss date={msg_date} rc={r.returncode} err={(r.stderr or '')[:120]}")
    except Exception as e:
        log(f"voice native error: {e}")
    return None


def _read_groq_key():
    try:
        with open(GROQ_KEY_FILE) as f:
            return f.read().strip()
    except Exception:
        return None


def transcribe_groq(file_path):
    """Быстрая облачная расшифровка через Groq (whisper-large-v3). Текст или None.
    Через urllib (бот не использует requests); multipart собираем вручную.
    Имя файла шлём voice.ogg — Groq фильтрует по расширению и .oga не принимает."""
    key = _read_groq_key()
    if not key:
        return None
    try:
        with open(file_path, "rb") as fh:
            audio = fh.read()
        b = "----groq" + os.urandom(16).hex()
        def _field(name, value):
            return (f'--{b}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n').encode()
        body = _field("model", GROQ_MODEL) + _field("language", "ru") + _field("response_format", "text")
        body += (f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="voice.ogg"\r\n'
                 f'Content-Type: audio/ogg\r\n\r\n').encode() + audio + b"\r\n"
        body += f"--{b}--\r\n".encode()
        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            data=body, method="POST",
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": f"multipart/form-data; boundary={b}",
                     "User-Agent": "claude-tg-bot/1.0"},  # без UA край Groq отдаёт 403
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read().decode("utf-8", "ignore").strip() or None
    except Exception as e:
        log(f"Groq exception: {type(e).__name__}: {e}")
        return None


def transcribe_voice(file_id, duration, msg_date=None):
    """Download voice file, transcribe, return text or None.
    Цепочка целиком облачная: Groq (whisper-large-v3) → родная расшифровка Telegram → AssemblyAI.
    Fix-F: on success, original .oga is moved to voice-archive instead of deleted."""
    # Длинные записи (созвоны) минуют Groq: он отдаёт сплошной текст без разделения
    # на голоса и стирает оригинал. Сразу AssemblyAI с разбором по говорящим.
    _long = (duration or 0) > VOICE_DIARIZE_SEC
    if _long:
        log(f"voice {duration}s > {VOICE_DIARIZE_SEC}s → AssemblyAI с разбором по голосам")

    # Groq-first: быстрый облачный Whisper. Нет ключа/ошибка/лимит → прежняя цепочка ниже.
    try:
        if _long:
            raise RuntimeError("skip-groq-long")
        with tempfile.NamedTemporaryFile(suffix=".oga", delete=True) as _gt:
            if download_file_to(file_id, _gt.name):
                _gtext = transcribe_groq(_gt.name)
                if _gtext:
                    log(f"voice {duration}s → Groq ok ({len(_gtext)} chars)")
                    return _gtext
    except Exception as _ge:
        log(f"groq-first error: {type(_ge).__name__}: {_ge}")

    # Родная расшифровка Telegram length-independent (~6.5с на любой длине), но у Telegram
    # есть квота: короткие её не тратят, длинные (>порог) уходят в AssemblyAI.
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
        # Groq не сработал (или пропущен на длинной записи) → облачный AssemblyAI
        log(f"voice {duration}s → AssemblyAI ({'диаризация' if _long else 'fallback'})")
        text = transcribe_assemblyai(tmp_path, diarize=_long)
        archived = _archive_voice(tmp_path, file_id)
        return text or None
    except Exception as e:
        log(f"transcribe error: {e}")
        return None
    finally:
        if not archived:
            try: os.unlink(tmp_path)
            except Exception: pass

def _strip_for_tts(text):
    """Plain text for TTS: drop ctx-header, markdown, urls, hook badges, service markers."""
    if not text:
        return ""
    text = re.sub(r"^\(ctx [^)]+\)\s*\n+", "", text)
    # Drop leading hook-names block (simple-language / terse / honesty / audit #N / ground-truth / verify-plan / pull-lab / do-it-yourself).
    text = re.sub(r"^(?:(?:simple-language|terse|honesty|do-it-yourself|ground-truth|verify-plan|pull-lab|audit\s*#\d+)\s*\n)+\s*\n*", "", text, flags=re.IGNORECASE)
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
    import subprocess, tempfile, os, urllib.parse
    tok, email = _zvukogram_creds()
    if not tok or not email:
        log("zvukogram: creds missing")
        return False
    data = urllib.parse.urlencode({
        "token": tok, "email": email, "voice": voice, "text": text,
    }).encode()
    req = urllib.request.Request(ZVUKOGRAM_API, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
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
            capture_output=True, timeout=60
        )
        if r2.returncode != 0 or not os.path.exists(out_ogg):
            log(f"ffmpeg(zvukogram) failed rc={r2.returncode} err={r2.stderr[:200]!r}")
            return False
        return True
    except Exception as e:
        log(f"_synth_voice_zvukogram err: {type(e).__name__}: {e}")
        return False
    finally:
        try: os.unlink(mp3)
        except Exception: pass


def _synth_voice_ogg(text, out_ogg, voice=None):
    """edge-tts -> mp3 -> ffmpeg opus/ogg. True on success."""
    import subprocess, tempfile, os
    if voice is None:
        voice = TTS_VOICE_DEFAULT
    mp3 = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False).name
    try:
        r = subprocess.run(
            [EDGE_TTS_BIN, "-v", voice, "-t", text, "--write-media", mp3],
            capture_output=True, timeout=180
        )
        if r.returncode != 0 or not os.path.exists(mp3) or os.path.getsize(mp3) < 200:
            log(f"edge-tts failed rc={r.returncode} err={r.stderr[:200]!r}")
            return False
        r2 = subprocess.run(
            ["ffmpeg", "-y", "-i", mp3, "-c:a", "libopus", "-b:a", "32k", out_ogg],
            capture_output=True, timeout=60
        )
        if r2.returncode != 0 or not os.path.exists(out_ogg):
            log(f"ffmpeg failed rc={r2.returncode} err={r2.stderr[:200]!r}")
            return False
        return True
    except Exception as e:
        log(f"_synth_voice_ogg err: {type(e).__name__}: {e}")
        return False
    finally:
        try: os.unlink(mp3)
        except Exception: pass


def api_send_voice(chat_id, ogg_path):
    """Multipart sendVoice."""
    import uuid
    boundary = uuid.uuid4().hex
    with open(ogg_path, "rb") as f:
        audio = f.read()
    head = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat_id}\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"voice\"; filename=\"reply.ogg\"\r\n"
        f"Content-Type: audio/ogg\r\n\r\n"
    ).encode()
    tail = f"\r\n--{boundary}--\r\n".encode()
    body = head + audio + tail
    req = urllib.request.Request(
        f"{API}/sendVoice",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}
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
        chunks.append(t[:cut+1].strip())
        t = t[cut+1:].strip()
    if t:
        chunks.append(t)
    return chunks

def send_voice_reply(chat_id, reply_text, voice=None, engine="edge"):
    """True if at least one voice chunk delivered.
    engine ∈ {'edge','zvukogram'}. voice: edge — ru-RU-* name; zvukogram — голос из каталога."""
    import tempfile, os
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
                    log(f"zvukogram failed — falling back to edge-tts")
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
            try: os.unlink(ogg)
            except Exception: pass
    return ok_any
