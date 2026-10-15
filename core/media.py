#!/usr/bin/env python3
"""Вложения: приём файлов, картинки и документы в обе стороны.

Три сюжета в одном модуле, потому что все про файлы.

Приём — скачать файл по идентификатору Telegram и положить в хранилище с
осмысленным именем (дата плюс краткое описание). Очередь ожидающих вложений
нужна, когда файл пришёл, а куда его класть — ещё не решено: он ждёт слова
хозяина, но не вечно, у записи есть срок.

Отдача — если модель упомянула в ответе путь к картинке или документу, файл
уезжает в чат отдельным сообщением, а путь из текста вырезается.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import urllib.request
import uuid
import urllib.error
import threading
from datetime import datetime
from pathlib import Path

from botctx import (
    API, ATTACH, FILE_API, MSK, PENDING_FILE, PENDING_NAG_AFTER_SEC, PENDING_TTL_SEC,
    PROFILE, VAULT, log, _PINNED, ckey, tkey, topic_of,
)
from tgapi import api, http_error_desc, is_topic_error, thread_form_part
from vaultio import safe_vault_path


_INLINE_IMG_EXTS = ("jpg", "jpeg", "png", "webp", "gif")
_INLINE_IMG_MD_RE = re.compile(
    r"!\[[^\]]*\]\(([^)\s]+\.(?:jpg|jpeg|png|webp|gif))(?:\s+\"[^\"]*\")?\)",
    re.IGNORECASE,
)
# Перед путём в строке может стоять что угодно, кроме букв, цифр, «/» и «.»: пробелы, маркер списка,
# двоеточие и ЛЮБОЕ эмодзи («📎 /tmp/отчёт.xlsx», «🖼 Resources/…png»). До 05.10.2026 пускали только
# пробел, «-», «*» и «•», и бот контент-отдела дважды писал «📎 путь», а файл не уходил (02.10).
_PATH_LINE_PREFIX = r"^[^\w/.\n]*"

_INLINE_IMG_BARE_RE = re.compile(
    _PATH_LINE_PREFIX + r"((?:Resources|Projects|Journal|Tasks|" + re.escape(str(VAULT)) + r"|/tmp)/[^\s\)\]\"']+?\.(?:jpg|jpeg|png|webp|gif))\s*$",
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


def _unseen_reason(cand):
    """14.10.2026: файл из ответа не найден. Раньше молчали, хозяин видел голый путь
    или «не отправился» без причины. У юнита PrivateTmp=yes: /tmp модели и /tmp ядра разные."""
    if str(cand).startswith("/tmp/"):
        return "ядро его не видит: у бота своя /tmp (PrivateTmp), клади файл в Resources/attachments/"
    return "файла нет по этому пути"


def api_send_photo(chat_id, path, caption=None, method="sendPhoto", field="photo", thread_id=_PINNED):
    """Send a local image as a Telegram photo via multipart/form-data.

    24.09.2026: .gif идёт через sendAnimation (поле animation), иначе Telegram показывает
    её неподвижной картинкой. Бот отдела отвечает гифками (слово хозяина 16:05).
    08.10.2026: в ходе из темы картинка уходит в эту тему; тема отвергла — в общий вид."""
    tid = topic_of(chat_id) if thread_id is _PINNED else thread_id
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
    body.append(thread_form_part(boundary, chat_id, tid))
    if caption:
        body.append(f"--{boundary}\r\n".encode())
        body.append('Content-Disposition: form-data; name="caption"\r\n\r\n'.encode())
        body.append(caption.encode("utf-8"))
        body.append(b"\r\n")
    body.append(f"--{boundary}\r\n".encode())
    fname = Path(path).name
    body.append(
        f'Content-Disposition: form-data; name="{field}"; filename="{fname}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n".encode()
    )
    body.append(file_bytes)
    body.append(b"\r\n")
    body.append(f"--{boundary}--\r\n".encode())
    data = b"".join(body)
    req = urllib.request.Request(f"{API}/{method}", data=data)
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        desc = http_error_desc(e)
        if tid and is_topic_error(desc):
            log(f"{method}: тема {tid} не принимает, шлю в общий вид")
            return api_send_photo(chat_id, path, caption, method, field, thread_id=None)
        log(f"sendPhoto error: {desc}")
        return {"ok": False, "description": desc}
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
            p0 = Path(raw_path.strip().strip("'\""))
            p0 = p0 if p0.is_absolute() else VAULT / p0
            if str(p0).startswith((str(VAULT), "/tmp/")) and not p0.is_file():
                log(f"inline-img not found: {p0}")
                _note_doc_failure(chat_id, p0, 0, _unseen_reason(p0))
            return False
        if resolved.stat().st_size > _TG_PHOTO_MAX_BYTES:
            log(f"inline-img skip (>10MB): {resolved}")
            return False
        if resolved.suffix.lower() == ".gif":
            res = api_send_photo(chat_id, str(resolved), caption=caption, method="sendAnimation", field="animation")
        else:
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

_INLINE_DOC_EXT_RE = r"(?:pdf|docx?|xlsx?|pptx?|csv|zip|txt|json|md|mp3|ogg|m4a|wav|flac|mp4|mov|epub|html?)"
_INLINE_DOC_BARE_RE = re.compile(
    _PATH_LINE_PREFIX + r"((?:Resources|Projects|" + re.escape(str(VAULT)) + r"|/tmp)/[^\n\)\]\"\']+?\.(?:"
    + _INLINE_DOC_EXT_RE + r"))\s*$",
    re.IGNORECASE | re.MULTILINE,
)

_TG_DOC_MAX_BYTES = 50 * 1024 * 1024


def api_send_document(chat_id, path, caption=None, thread_id=_PINNED):
    """Send a local file as a Telegram document via multipart/form-data."""
    tid = topic_of(chat_id) if thread_id is _PINNED else thread_id
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
    body.append(thread_form_part(boundary, chat_id, tid))
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
    # 24.09.2026 (слово хозяина): выгрузка на 37 МБ с соседней машины упала «write operation timed out»
    # на жёстких 120 с, ядро промолчало, в чате остался голый путь. Теперь таймаут растёт
    # с размером (60 с + 8 с на МБ, это ~125 КБ/с худшего канала) и есть вторая попытка.
    mb = len(data) / (1024 * 1024)
    tmo = int(60 + 8 * mb)
    last_err = ""
    for attempt in (1, 2):
        req = urllib.request.Request(f"{API}/sendDocument", data=data)
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=tmo) as r:
                res = json.load(r)
            if res.get("ok"):
                log(f"sendDocument ok: {fname} {mb:.1f}MB за {time.time() - t0:.0f}с, попытка {attempt}")
                return res
            last_err = str(res.get("description") or res)[:200]
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}: {http_error_desc(e)[:200]}"
            if tid and is_topic_error(last_err):
                log(f"sendDocument: тема {tid} не принимает, шлю в общий вид")
                return api_send_document(chat_id, path, caption, thread_id=None)
        except Exception as e:
            last_err = str(e)[:200]
        log(f"sendDocument error (попытка {attempt}, {mb:.1f}MB, таймаут {tmo}с): {last_err}")
        if last_err.startswith("HTTP 4"):
            break  # отказ Telegram (слишком большой, нет прав) повтором не лечится
        time.sleep(3)
    return {"ok": False, "error": last_err}


# Провалы отправки документов по чатам: ход забирает их через pop_doc_failures и
# заводит модели отдельный ход-пинок (24.09.2026, слово хозяина «пинает и говорит: не прикрепилось»).
_DOC_FAILS = {}
_DOC_FAILS_LOCK = threading.Lock()


def _note_doc_failure(chat_id, path, size_mb, reason):
    with _DOC_FAILS_LOCK:
        _DOC_FAILS.setdefault(ckey(chat_id), []).append(
            {"path": str(path), "name": Path(path).name, "mb": round(size_mb, 1), "reason": reason})


def pop_doc_failures(chat_id):
    with _DOC_FAILS_LOCK:
        return _DOC_FAILS.pop(ckey(chat_id), [])


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
            log(f"inline-doc not found: {cand}")
            _note_doc_failure(chat_id, cand, 0, _unseen_reason(cand))
            return False
        size_mb = cand.stat().st_size / (1024 * 1024)
        if cand.stat().st_size > _TG_DOC_MAX_BYTES:
            log(f"inline-doc skip (>50MB): {cand}")
            _note_doc_failure(chat_id, cand, size_mb, "больше 50 МБ, Telegram такой не пропустит")
            return False
        # гифки из Telegram приходят .mp4; из папки «гифки» шлём их анимацией, а не файлом (24.09.2026)
        if cand.suffix.lower() == ".mp4" and "гифк" in str(cand).lower():
            res = api_send_photo(chat_id, str(cand), method="sendAnimation", field="animation")
        else:
            res = api_send_document(chat_id, str(cand))
        if res.get("ok"):
            sent.add(raw_path)
            log(f"inline-doc sent: {cand.name}")
            return True
        _note_doc_failure(chat_id, cand, size_mb, res.get("error") or "неизвестная ошибка")
        return False

    def _bare_sub(m):
        path = m.group(1)
        if _try_send_doc(path):
            return ""
        return f"⚠️ файл не отправился, лежит тут: {path.strip()}"
    cleaned = _INLINE_DOC_BARE_RE.sub(_bare_sub, cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned

def download_file_to(file_id, dest_abs):
    """Download a TG file by file_id to absolute dest path. Returns True/False.
    Logs every failure mode (getFile rc, urlretrieve error, post-write size)."""
    res = api("getFile", file_id=file_id)
    if not res.get("ok"):
        log(f"download_file_to: getFile failed file_id={file_id[:20]}… resp={json.dumps(res)[:200]}")
        return False
    path = res["result"]["file_path"]
    Path(dest_abs).parent.mkdir(parents=True, exist_ok=True)
    if path.startswith("/var/lib/telegram-bot-api/"):
        # local Bot API server: getFile отдаёт локальный путь внутри контейнера
        # (файл уже скачан сервером), а не URL. Если том виден с хоста — просто
        # копируем; иначе тащим через docker exec cat (путь содержит ':' — токен,
        # поэтому cat, а не docker cp).
        try:
            if os.access(path, os.R_OK):
                shutil.copyfile(path, dest_abs)
            else:
                with open(dest_abs, "wb") as out:
                    r = subprocess.run(["docker", "exec", PROFILE["local_api_container"],
                                        "cat", path],
                                       stdout=out, stderr=subprocess.PIPE, timeout=600)
                if r.returncode != 0:
                    err = r.stderr.decode("utf-8", "replace").strip()
                    if "docker.sock" in err and "permission denied" in err.lower():
                        # Своей группы docker у бота нет — и это правильно: группа
                        # docker равна root, через неё бот одного человека читал
                        # токен другого (проверено живьём 27.08.2026). Взамен есть
                        # узкая обёртка: она сама смотрит, кто её позвал, и отдаёт
                        # файл только если путь содержит токен этого же бота.
                        with open(dest_abs, "wb") as out2:
                            r2 = subprocess.run(
                                ["sudo", "-n", "/usr/local/bin/tg-fetch-attachment", path],
                                stdout=out2, stderr=subprocess.PIPE, timeout=600)
                        if r2.returncode == 0:
                            log("download_file_to: вложение взято через tg-fetch-attachment")
                        else:
                            err2 = r2.stderr.decode("utf-8", "replace").strip()
                            log("download_file_to: обёртка вложений не сработала "
                                f"dest={dest_abs} err={err2[:200]}")
                            return False
                    else:
                        log(f"download_file_to: docker cat failed dest={dest_abs} err={err[:200]}")
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
        try: Path(dest_abs).unlink()
        except Exception: pass
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
    # 08.10.2026: атомарно — вложения в двух темах приходят одновременно
    tmp = PENDING_FILE.with_name(f".{PENDING_FILE.name}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(p, indent=2, ensure_ascii=False))
    tmp.replace(PENDING_FILE)


def _prune_pending_list(lst):
    """Drop entries older than PENDING_TTL_SEC. Returns kept list."""
    now = time.time()
    return [r for r in lst if (now - r.get("ts", 0)) <= PENDING_TTL_SEC]


def set_pending(chat_id, file_id, kind, ext, summary):
    """Append a new pending entry to the chat's queue (Fix-C).
    Returns (pid, stale_list) where stale_list = entries already older than
    PENDING_NAG_AFTER_SEC at the moment we appended (Fix-I — bot nags about them)."""
    p = load_pending()
    key = tkey(chat_id)   # 08.10.2026: у темы своя очередь вложений
    lst = _prune_pending_list(p.get(key) or [])
    now = time.time()
    stale = [r for r in lst if (now - r.get("ts", 0)) > PENDING_NAG_AFTER_SEC]
    pid = uuid.uuid4().hex[:8]
    lst.append({
        "pid": pid,
        "file_id": file_id,
        "kind": kind,
        "ext": ext,
        "summary": summary,
        "ts": now,
    })
    p[key] = lst
    save_pending(p)
    return pid, stale


def get_pending_list(chat_id):
    """Return list of un-expired pending entries for chat (newest last). Fix-C."""
    p = load_pending()
    key = tkey(chat_id)   # 08.10.2026: у темы своя очередь вложений
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
    key = tkey(chat_id)   # 08.10.2026: у темы своя очередь вложений
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
    key = tkey(chat_id)   # 08.10.2026: у темы своя очередь вложений
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
    p.pop(tkey(chat_id), None)
    save_pending(p)

def _extract_attachment_tuple(msg):
    """Return (kind, file_id, ext, summary) for the attachment in msg, or None.
    Voice handled separately (transcribed inline)."""
    if "audio" in msg:
        a = msg["audio"]
        ext = Path(a.get("file_name", "")).suffix or ".mp3"
        return ("audio", a["file_id"], ext,
                f"audio {a.get('duration','?')}s, {a.get('mime_type','?')}, name={a.get('file_name','?')}, {a.get('file_size','?')} bytes")
    if "photo" in msg:
        p = msg["photo"][-1]
        return ("photo", p["file_id"], ".jpg",
                f"photo {p.get('width','?')}x{p.get('height','?')}, {p.get('file_size','?')} bytes")
    if "document" in msg:
        d = msg["document"]
        ext = Path(d.get("file_name", "")).suffix or ""
        return ("document", d["file_id"], ext,
                f"document name={d.get('file_name','?')}, mime={d.get('mime_type','?')}, {d.get('file_size','?')} bytes")
    if "video" in msg:
        v = msg["video"]
        return ("video", v["file_id"], ".mp4",
                f"video {v.get('duration','?')}s, {v.get('width','?')}x{v.get('height','?')}, {v.get('file_size','?')} bytes")
    if "video_note" in msg:
        v = msg["video_note"]
        return ("video_note", v["file_id"], ".mp4",
                f"video_note {v.get('duration','?')}s, {v.get('file_size','?')} bytes")
    return None

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
                statuses.append(f"⚠️ SAVE без pid отклонён: в очереди {len(queue)} файлов — укажи pid")
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
            log(f"attach-marker: SAVE — download_file_to returned False")

    if statuses:
        # Tidy reply: collapse triple+ blank lines that the regex strip left behind.
        new_reply = re.sub(r"\n{3,}", "\n\n", new_reply).strip()
        return new_reply, "\n".join(statuses)
    return reply, None
