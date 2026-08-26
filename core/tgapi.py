#!/usr/bin/env python3
"""Разговор с Telegram: вызов API, разметка, нарезка, кнопки, живая панель.

Всё, что знает про формат сообщений мессенджера и ничего не знает про смысл
разговора. Сюда же — отправка от лица хозяина через его пользовательскую
сессию (это тоже Telegram, только другим ключом).

Модуль зависит только от botctx, поэтому его может звать кто угодно.
"""
import html
import json
import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from botctx import (
    API, TG_CHUNK, TG_SEND_ONESHOT, TG_SEND_PYTHON, log,
    _CONFIRM_KEYBOARD, _MENU_ROW, _NO_KEYBOARD, _STOP_KEYBOARD,
    _brief_active_msg,
)


# Ответы Telegram, которые ничего не значат: правка сообщения тем же текстом.
# Гасим без шума — иначе журнал засыпан 400-ми там, где всё в порядке.
_BENIGN_API_ERRORS = ("message is not modified",)


def api(method, **params):
    data = urllib.parse.urlencode(
        {k: v for k, v in params.items() if v is not None}
    ).encode()
    req = urllib.request.Request(f"{API}/{method}", data=data)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        # Причину Telegram кладёт в ТЕЛО ответа. Без чтения тела в журнал уходит
        # безлицее «HTTP Error 400: Bad Request», по которому не понять ничего —
        # ровно это и было в первом прогоне канарейки 19.08.
        body = {}
        try:
            body = json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            pass
        desc = body.get("description") or f"HTTP {e.code}"
        if not any(x in desc for x in _BENIGN_API_ERRORS):
            log(f"api {method} error: {desc}")
        return {"ok": False, "description": desc,
                "error_code": body.get("error_code", e.code)}
    except Exception as e:
        log(f"api {method} error: {e}")
        return {"ok": False, "description": str(e)}

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

STATUS_PANEL_LIMIT = 3800   # запас под лимит сообщения Telegram (4096) с тегами
STATUS_PANEL_BODY_MAX = 2500  # сколько знаков хвоста показываем в одном блоке


def esc_html(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_status_panel(hourglass, elapsed_str, action, thinking, tool_lines,
                       streamed, limit=STATUS_PANEL_LIMIT):
    """Собирает HTML живой панели «думаю…» так, чтобы она гарантированно влезала
    в лимит Telegram и ни один тег не остался разорванным.

    Прежний код резал уже собранный HTML (`text[:3800]`). Обрезка попадала внутрь
    `<blockquote expandable>` или внутрь `&amp;` — Telegram такое не разбирает и
    отвечает `400 Bad Request`. Ровно эти 400-е и сыпались в журнал канарейки
    19.08 на длинных ходах. Теперь режется СЫРОЙ текст до экранирования, а бюджет
    раздаётся по важности: сперва хвост ответа модели, потом её рассуждение."""
    def _tail(chunks, n):
        s = "".join(chunks)
        if n <= 0:
            return ""
        return s if len(s) <= n else "…" + s[-n:]

    body_max = min(STATUS_PANEL_BODY_MAX, limit)
    think_max = body_max
    for _ in range(24):     # ужимаем, пока не влезет; шаг заведомо сходится
        parts = [f"{hourglass} думаю… ({elapsed_str})"]
        if action:
            parts.append(esc_html(action))
        think = _tail(thinking, think_max)
        if think:
            parts.append(f"🧠 <blockquote expandable>{esc_html(think)}</blockquote>")
        if tool_lines:
            tools_html = "\n".join(esc_html(x) for x in tool_lines[-8:])
            parts.append(f"<blockquote expandable>{tools_html}</blockquote>")
        body = _tail(streamed, body_max)
        if body:
            parts.append(esc_html(body))
        text = "\n\n".join(parts)
        if len(text) <= limit:
            return text
        if think_max > 0:                       # рассуждение — первый кандидат на нож
            think_max = int(think_max * 0.7) if think_max > 40 else 0
        elif body_max > 0:
            body_max = int(body_max * 0.7) if body_max > 40 else 0
        else:
            break
    # остались одни строки инструментов и они не влезли — режем их по границе строк
    kept = list(tool_lines[-8:])
    while kept and len("\n".join(esc_html(x) for x in kept)) > limit - 200:
        kept.pop(0)
    parts = [f"{hourglass} думаю… ({elapsed_str})"]
    if kept:
        parts.append("<blockquote expandable>"
                     + "\n".join(esc_html(x) for x in kept) + "</blockquote>")
    return "\n\n".join(parts)

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
    text = re.sub(
        r"(?<![\*\w])\*([^\*\n]+?)\*(?![\*\w])", r"<i>\1</i>", text
    )
    text = re.sub(
        r"(?<![_\w])_([^_\n]+?)_(?![_\w])", r"<i>\1</i>", text
    )
    text = re.sub(
        r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', text
    )
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


# 2026-07-15/16: богатая разметка рендерится нативно через sendRichMessage
# (Bot API 10.1+, локальный сервер поддерживает). Классический parse_mode=HTML
# не умеет таблицы/заголовки/нумерацию/галочки/разделители (выходят палками или
# срезаются). Роутим в rich сообщения, где есть любой такой элемент; обычная
# проза (bold/italic/code/цитата) идёт прежним HTML-путём — он проверен.
_TABLE_SEP_RE = re.compile(r"^\s*\|?[ :]*-{3,}[ :]*(\|[ :]*-{0,}[ :]*)+\|?\s*$", re.M)
_NESTED_LI_RE = re.compile(r"^[ \t]{2,}[\*\-\+][ \t]+\S", re.M)
_HEADING_RE = re.compile(r"^#{1,6}\s+\S", re.M)          # # Заголовок
_ORDERED_RE = re.compile(r"^\s*\d+\.\s+\S", re.M)         # 1. пункт
_CHECKBOX_RE = re.compile(r"^\s*[\*\-\+]\s+\[[ xX]\]\s", re.M)  # - [ ] / - [x]
_DIVIDER_RE = re.compile(r"^\s*---+\s*$", re.M)            # --- разделитель
_RICH_RES = (_TABLE_SEP_RE, _NESTED_LI_RE, _HEADING_RE,
             _ORDERED_RE, _CHECKBOX_RE, _DIVIDER_RE)


def _wants_rich(text):
    """True если в тексте есть markdown-элемент, который красиво рендерит только
    sendRichMessage: таблица, вложенный/нумерованный список, галочки, заголовок,
    разделитель. Обычная проза остаётся на HTML-пути."""
    if not text:
        return False
    return any(r.search(text) for r in _RICH_RES)


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

def _build_choices_markup(choices):
    rows = []
    for label in choices:
        short = label if len(label) <= 32 else label[:30] + "…"
        cb = f"pick:{label}"
        if len(cb.encode("utf-8")) > 64:
            cb = cb.encode("utf-8")[:64].decode("utf-8", "ignore")
        rows.append([{"text": short, "callback_data": cb}])
    return json.dumps({"inline_keyboard": rows})

def _strip_active_keyboard(chat_id):
    """Remove keyboard from the previously active brief message in this chat."""
    old = _brief_active_msg.get(chat_id)
    if not old:
        return
    try:
        api("editMessageReplyMarkup", chat_id=chat_id, message_id=old,
            reply_markup=json.dumps({"inline_keyboard": []}))
    except Exception as e:
        log(f"strip_active_keyboard: {e}")


def send_chunked(chat_id, text, confirm=False, choices=None, dig=False, with_menu=True, menu_rows=None, edit_first_msg_id=None):
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
    last_markup = json.dumps({"inline_keyboard": last_rows}, ensure_ascii=False) if last_rows else None
    # Strip prior active nav keyboard before posting new messages below.
    if with_menu:
        try:
            _strip_active_keyboard(chat_id)
        except NameError:
            pass  # not yet defined at module load — safe at call time
    last_msg_id = None
    import traceback as _tb
    log(f"SEND-TRACE send_chunked chat={chat_id} chunks={len(chunks)} total_chars={sum(len(c) for c in chunks)} caller={_tb.extract_stack()[-2].name}")
    # Rich path (2026-07-15): если есть таблица/вложенный список и всё влезает в
    # одно сообщение — шлём сырой markdown через sendRichMessage (нативный рендер
    # таблиц/списков/заголовков). sendRichMessage не умеет редактировать, поэтому
    # если есть статус-сообщение «думаю» — удаляем его и шлём rich свежим.
    # Сбой rich → откат на обычный HTML-путь ниже.
    if _wants_rich(text) and len(text) <= TG_CHUNK:
        rm = json.dumps({"markdown": text}, ensure_ascii=False)
        res = api("sendRichMessage", chat_id=chat_id, rich_message=rm,
                  reply_markup=last_markup)
        if res.get("ok"):
            if edit_first_msg_id is not None:
                # статус-сообщение больше не станет финалом — убираем его
                try:
                    api("deleteMessage", chat_id=chat_id, message_id=edit_first_msg_id)
                except Exception:
                    pass
            mid = res.get("result", {}).get("message_id")
            if with_menu and mid is not None:
                _brief_active_msg[chat_id] = mid
            log("SEND-TRACE  rich message sent (table/heading/list)")
            return
        log(f"rich send failed, fallback to HTML: {res.get('description','?')[:120]}")
    for i, chunk in enumerate(chunks):
        is_last = (i == len(chunks) - 1)
        markup = last_markup if is_last else None
        log(f"SEND-TRACE  chunk[{i}/{len(chunks)}] {len(chunk)} chars, markup={bool(markup)}")
        # Fix Bug2: edit status message into first chunk instead of sending new.
        if i == 0 and edit_first_msg_id is not None:
            res = api("editMessageText", chat_id=chat_id, message_id=edit_first_msg_id,
                      text=chunk, parse_mode="HTML", reply_markup=markup,
                      disable_web_page_preview="true")
            if res.get("ok"):
                if is_last:
                    last_msg_id = edit_first_msg_id
                continue
            # Edit failed (deleted/rate-limited) → fall through to sendMessage
            log(f"editMessageText status→final failed: {res.get('description','?')[:120]}, falling back to send")
        res = api("sendMessage", chat_id=chat_id, text=chunk,
                  parse_mode="HTML", reply_markup=markup,
                  disable_web_page_preview="true")
        if not res.get("ok"):
            log(f"HTML send failed, falling back to plain: {res.get('description','?')[:120]}")
            res = api("sendMessage", chat_id=chat_id, text=strip_html(chunk),
                      reply_markup=markup, disable_web_page_preview="true")
        if is_last and res.get("ok"):
            last_msg_id = res.get("result", {}).get("message_id")
    if with_menu and last_msg_id is not None:
        _brief_active_msg[chat_id] = last_msg_id

def _oneshot(args, timeout=60):
    """Запустить tg-send-oneshot.py, вернуть распарсенный JSON (или dict с ok=False)."""
    try:
        r = subprocess.run(
            [TG_SEND_PYTHON, TG_SEND_ONESHOT] + list(args),
            capture_output=True, text=True, timeout=timeout)
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
