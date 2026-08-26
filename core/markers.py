#!/usr/bin/env python3
"""Разбор маркеров ответа модели.

Модель ставит в свой ответ служебные строки — «я записал файл», «поставь
задачу», «предложи событие в календарь», «покажи кнопки выбора». Здесь они
вылавливаются и вырезаются из текста, который увидит человек.

Каждый разборщик устроен одинаково: получает ответ целиком, возвращает пару
«очищенный текст, добытые данные». Побочных действий нет ни у одного — писать
в файлы и слать в чат будут другие модули. Формулировки маркеров описаны в
prompt.py; менять их можно только парой: текст промпта и регулярка здесь.

Тут же — короткая память двухстадийных подтверждений (календарь и отправка от
лица хозяина): предложение лежит в файле, пока человек не нажмёт «да».
"""
import json
import re
import threading
import time

from botctx import (
    PENDING_CAL_FILE, PENDING_CAL_TTL_SEC, PENDING_SEND_FILE,
    PENDING_SEND_TTL_SEC, TTS_MARKER_RE, TTS_VOICE_DEFAULT, VOICE_TAG_MAP, log,
)
from tgapi import strip_html


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
    See SYSTEM_PROMPT item 6b for the dispatch rule between __CHOICES__ and __AFFIRM__.
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
    See SYSTEM_PROMPT item 6c.
    """
    if not reply:
        return reply, False
    if _DIG_RE.search(reply):
        cleaned = _DIG_RE.sub("", reply, count=1).rstrip()
        return cleaned, True
    return reply, False


# 2026-08-01: кнопки почти исчезли из ответов. Причина оказалась не в коде —
# модель просто перестала ставить маркеры (хозяин: «зелёные галочки практически
# на нуле»). Дисциплина модели ненадёжна, поэтому лупа ставится кодом:
# содержательный ответ без своей кнопки получает 🔎 по умолчанию.
_AUTO_DIG_MIN_CHARS = 400


def auto_dig(reply, want_choices, want_affirm, want_dig):
    """True если надо подставить 🔎 самим, без маркера от модели.

    Не трогаем: ответы со своей кнопкой (конкурируют за слот), короткие
    статусные реплики, и ответы-вопросы (там сработает needs_confirm → ✅).
    """
    if want_choices or want_affirm or want_dig:
        return False
    if not reply or len(reply) < _AUTO_DIG_MIN_CHARS:
        return False
    if needs_confirm(strip_html(reply)):
        return False
    return True


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
    cleaned = (reply[:m.start()] + reply[m.end():]).strip()
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
    cleaned = (reply[:m.start()] + reply[m.end():]).strip()
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
    cleaned = (reply[:m.start()] + reply[m.end():]).strip()
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
