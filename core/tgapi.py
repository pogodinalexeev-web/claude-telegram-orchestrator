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
    PROFILE, API, TG_CHUNK, TG_SEND_ONESHOT, TG_SEND_PYTHON, log,
    _CONFIRM_KEYBOARD, _MENU_ROW, _NO_KEYBOARD, _STOP_KEYBOARD,
    _brief_active_msg, carry, ckey, topic_of,
)


# Ответы Telegram, которые ничего не значат: правка сообщения тем же текстом.
# Гасим без шума — иначе журнал засыпан 400-ми там, где всё в порядке.
_BENIGN_API_ERRORS = ("message is not modified",)


# Методы, которые принимают message_thread_id. Правки и удаления (edit*, delete*)
# адресуют сообщение по номеру, тема им не нужна и не принимается.
TOPIC_METHODS = frozenset({
    "sendMessage", "sendRichMessage", "sendPhoto", "sendDocument", "sendVoice",
    "sendAudio", "sendVideo", "sendAnimation", "sendVideoNote", "sendSticker",
    "sendMediaGroup", "sendChatAction", "sendMessageDraft", "sendLocation",
    "sendVenue", "sendContact", "sendPoll", "sendDice", "copyMessage",
    "forwardMessage",
})


def is_topic_error(desc):
    """Отказ Telegram, который значит «в эту тему нельзя»: тема удалена, закрыта
    или «мёртвая» (жалоба tdlib/telegram-bot-api #905). Тогда шлём в общий вид."""
    d = (desc or "").lower()
    return ("thread not found" in d or "topic_closed" in d or "topic_deleted" in d
            or "topic closed" in d or "topic deleted" in d or "topic_id_invalid" in d)


def api(method, **params):
    """Вызов Bot API. В ходе из темы (botctx.pin_topic) отправка в этот же чат
    сама получает message_thread_id. Явный message_thread_id=None = общий вид.
    Тема отвергла отправку — повтор без темы, молча: ответ важнее места."""
    if method in TOPIC_METHODS and "message_thread_id" not in params:
        tid = topic_of(params.get("chat_id"))
        if tid:
            params["message_thread_id"] = tid
    res = _api_call(method, params)
    if (not res.get("ok") and params.get("message_thread_id")
            and method in TOPIC_METHODS and is_topic_error(res.get("description"))):
        log(f"api {method}: тема {params['message_thread_id']} не принимает, шлю в общий вид")
        retry = {k: v for k, v in params.items() if k != "message_thread_id"}
        res = _api_call(method, retry)
    return res


def thread_form_part(boundary, chat_id, thread_id):
    """Кусок multipart-формы с message_thread_id для отправок файлов (фото, документ,
    голос идут мимо api()). Пусто, если темы нет."""
    if not thread_id:
        return b""
    return (f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="message_thread_id"\r\n\r\n{int(thread_id)}\r\n').encode()


def http_error_desc(e):
    """Причина отказа из тела HTTPError Telegram (тело читается один раз)."""
    try:
        return json.loads(e.read().decode("utf-8", "replace")).get("description") or f"HTTP {e.code}"
    except Exception:
        return f"HTTP {getattr(e, 'code', '?')}"


def _api_call(method, params):
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
        self._thread = threading.Thread(target=carry(self._run), daemon=True)
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


# 09.10.2026 (слово хозяина: «как в клод-коде, percolating… бибопинг»): слова панели
# взяты как есть из Claude Code 2.1.288, все 189 (вытащены из его бинарника).
# Слово меняется раз в 8 секунд по часам, поэтому каждый ход начинается с нового.
THINK_WORDS = [
    "Accomplishing", "Actioning", "Actualizing", "Architecting", "Baking", "Beaming",
    "Beboppin'", "Befuddling", "Billowing", "Blanching", "Bloviating", "Boogieing",
    "Boondoggling", "Booping", "Bootstrapping", "Brewing", "Bunning", "Burrowing",
    "Calculating", "Canoodling", "Caramelizing", "Cascading", "Catapulting", "Cerebrating",
    "Channeling", "Choreographing", "Churning", "Clauding", "Coalescing", "Cogitating",
    "Combobulating", "Composing", "Computing", "Concocting", "Considering", "Contemplating",
    "Cooking", "Crafting", "Creating", "Crunching", "Crystallizing", "Cultivating",
    "Deciphering", "Deliberating", "Determining", "Dilly-dallying", "Discombobulating",
    "Doing", "Doodling", "Drizzling", "Ebbing", "Effecting", "Elucidating", "Embellishing",
    "Enchanting", "Envisioning", "Fermenting", "Fiddle-faddling", "Finagling", "Flambéing",
    "Flibbertigibbeting", "Flowing", "Flummoxing", "Fluttering", "Forging", "Forming",
    "Frolicking", "Frosting", "Gallivanting", "Galloping", "Garnishing", "Generating",
    "Gesticulating", "Germinating", "Gitifying", "Grooving", "Gusting", "Harmonizing",
    "Hashing", "Hatching", "Herding", "Honking", "Hullaballooing", "Hyperspacing", "Ideating",
    "Imagining", "Improvising", "Incubating", "Inferring", "Infusing", "Ionizing",
    "Jitterbugging", "Julienning", "Kerfuffling", "Kneading", "Leavening", "Levitating",
    "Lollygagging", "Manifesting", "Marinating", "Meandering", "Metamorphosing", "Misting",
    "Moonwalking", "Moseying", "Mulling", "Mustering", "Musing", "Nebulizing", "Nesting",
    "Newspapering", "Noodling", "Nucleating", "Onioning", "Orbiting", "Orchestrating",
    "Osmosing", "Perambulating", "Percolating", "Perusing", "Philosophizing",
    "Photosynthesizing", "Polishing", "Pollinating", "Pondering", "Pontificating", "Pouncing",
    "Precipitating", "Prestidigitating", "Processing", "Proofing", "Propagating", "Puttering",
    "Puzzling", "Quantumizing", "Razzle-dazzling", "Razzmatazzing", "Recombobulating",
    "Reticulating", "Roosting", "Ruminating", "Sautéing", "Scampering", "Schlepping",
    "Scurrying", "Seasoning", "Shenaniganing", "Shimmying", "Simmering", "Skedaddling",
    "Sketching", "Slithering", "Smooshing", "Sock-hopping", "Spelunking", "Spinning",
    "Sprouting", "Stewing", "Sublimating", "Swirling", "Swooping", "Symbioting",
    "Synthesizing", "Tempering", "Thinking", "Thundering", "Tinkering", "Tomfoolering",
    "Topsy-turvying", "Transfiguring", "Transmogrifying", "Transmuting", "Twisting",
    "Undulating", "Unfurling", "Unraveling", "Vibing", "Waddling", "Wandering", "Warping",
    "Whatchamacalliting", "Whirlpooling", "Whirring", "Whisking", "Wibbling", "Working",
    "Wrangling", "Zesting", "Zigzagging"
]
# Ступени по длине хода, тоже из Claude Code (пороги 10, 20, 30, 45 секунд).
THINK_STAGES = [(45, "deep in thought"), (30, "thinking some more"),
                (20, "thinking more"), (10, "still thinking")]


def _elapsed_sec(elapsed_str):
    m = re.match(r"(?:(\d+)м )?(\d+)с$", elapsed_str or "")
    return int(m.group(1) or 0) * 60 + int(m.group(2)) if m else 0


# Значок пульсирует, как звёздочка Claude Code: цвет в Telegram тексту не задать,
# поэтому оранжевые эмодзи по кругу, кадр сменяется раз в 4 секунды (как правка панели).
THINK_SPIN = ["🔸", "🔶", "✴️", "🔶"]


def think_spin(elapsed_str):
    """Кадр пульса. Если в профиле есть spin_emoji_id, кадр становится запасным
    текстом живого эмодзи: владельцу с Premium Telegram покажет анимацию,
    остальным эти же оранжевые эмодзи. В spin_emoji_id можно перечислить
    несколько номеров через запятую: первый звёздочка, дальше крабы Клод
    (качается, танцует, саксофон, ноутбук)."""
    frame = THINK_SPIN[_elapsed_sec(elapsed_str) // 4 % len(THINK_SPIN)]
    ids = [x.strip() for x in str(PROFILE.get("spin_emoji_id") or "").split(",")
           if x.strip().isdigit()]
    if not ids:
        return frame
    # 09.10.2026 (хозяин: «по кругу переключалась с рандомом», «звёздочка наравне со всеми»):
    # раз в 30 с жребий, все значки равны. Жребий от номера получаса, поэтому
    # внутри 30 секунд значок не дёргается на каждой правке панели.
    h = (int(time.time()) // 30 * 0x9E3779B1) % 2**32
    eid = ids[(h >> 3) % len(ids)]
    return f'<tg-emoji emoji-id="{eid}">{frame}</tg-emoji>'


def think_word(elapsed_str):
    return THINK_WORDS[int(time.time()) // 8 * 7919 % len(THINK_WORDS)]


def think_stage(elapsed_str):
    sec = _elapsed_sec(elapsed_str)
    return next((name for lim, name in THINK_STAGES if sec >= lim), "thinking")


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
        parts = [f"{think_spin(elapsed_str)} {think_word(elapsed_str)}… ({elapsed_str} · {think_stage(elapsed_str)})"]
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
    parts = [f"{think_spin(elapsed_str)} {think_word(elapsed_str)}… ({elapsed_str} · {think_stage(elapsed_str)})"]
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

    # 24.09.2026: подряд идущие строки «> …» → один раскрывающийся блок Telegram
    # (как вывод инструментов). После html.escape знак «>» уже стал «&gt;».
    def _quote_block(m):
        lines = [re.sub(r"^&gt;[ \t]?", "", ln) for ln in m.group(0).rstrip("\n").split("\n")]
        return "<blockquote expandable>" + "\n".join(lines).strip("\n") + "</blockquote>\n"

    text = re.sub(r"(?:^&gt;[^\n]*(?:\n|$))+", _quote_block, text, flags=re.MULTILINE)
    text = text.replace("</blockquote>\n\n", "</blockquote>\n")

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
_URLISH_RE = re.compile(r"https?://|www\.|\b[\w-]+(\.[\w-]+)*\.(ru|com|org|net|io|me|app|dev|ai|org|рф|su|co|xyz|site|online)\b", re.I)
_RICH_RES = (_TABLE_SEP_RE, _NESTED_LI_RE, _HEADING_RE,
             _ORDERED_RE, _CHECKBOX_RE, _DIVIDER_RE)


# Подвал сохранения (24.09.2026): строки «> 💾 …» в самом конце ответа. На HTML-пути
# сворачиваются в раскрывающийся блок, на rich-пути идут простой цитатой.
SAVE_FOOTER_HEAD = "💾 сохранено"


def _split_footer(text):
    i = text.rfind("\n> " + SAVE_FOOTER_HEAD)
    if i < 0 and text.startswith("> " + SAVE_FOOTER_HEAD):
        i = 0
    return (text, "") if i < 0 else (text[:i], text[i:].lstrip("\n"))


def _wants_rich(text):
    """True если в тексте есть markdown-элемент, который красиво рендерит только
    sendRichMessage: таблица, вложенный/нумерованный список, галочки, заголовок,
    разделитель. Обычная проза остаётся на HTML-пути."""
    if not text:
        return False
    # подвал сохранения rich-путь не отнимает: смотрим только тело ответа
    text = _split_footer(text)[0]
    # цитаты «> » нужны раскрывающимся блоком, а его умеет только HTML-путь
    if re.search(r"^>[ \t]", text, flags=re.MULTILINE):
        return False
    # 14.10.2026: клиент Telegram падает при нажатии на ссылку в rich-сообщении,
    # если текст ссылки похож на адрес (голый URL, [url](url), <url>). Проверено
    # пробами А/Б/В: [слово](url) живёт, остальное роняет клиент. Такие ответы
    # идут HTML-путём. Ссылки вида [слово](url) вырезаем перед проверкой.
    if _URLISH_RE.search(re.sub(r"\[[^\]]*\]\([^)\s]+\)",
                                lambda m: "" if not _URLISH_RE.search(m.group(0).split("](")[0]) else m.group(0),
                                text)):
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
    old = _brief_active_msg.get(ckey(chat_id))
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
    # Кнопка меню отключается переключателем menu_enabled в profile.json.
    if with_menu and not PROFILE.get("menu_enabled", True):
        with_menu = False
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
        body, foot = _split_footer(text)
        if foot:
            # rich склеивает строки цитаты в одну: разводим их пустой строкой цитаты
            foot = "\n>\n".join(foot.split("\n"))
        rm = json.dumps({"markdown": (body.rstrip() + "\n\n" + foot) if foot else text}, ensure_ascii=False)
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
                _brief_active_msg[ckey(chat_id)] = mid
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
        _brief_active_msg[ckey(chat_id)] = last_msg_id

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
