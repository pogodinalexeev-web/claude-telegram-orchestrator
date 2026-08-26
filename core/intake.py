#!/usr/bin/env python3
"""Приём сообщения: кому отвечать, что склеить, что вытащить из пересланного.

Между «Telegram отдал обновление» и «начался ход разговора» стоит несколько
фильтров, и каждый появился из живой боли.

Групповой фильтр: в общем чате бот отвечает только своему хозяину или когда
его позвали по имени — иначе четыре бота в одной комнате начинают говорить
хором. Кто хозяин и как зовут бота — из профиля, не из констант.

Склейка альбомов: Telegram присылает альбом из пяти картинок пятью разными
сообщениями за доли секунды. Их надо собрать обратно в одно.

Склейка очереди: человек часто пишет мысль тремя сообщениями подряд. Ждём
меньше секунды и объединяем — один ход вместо трёх.

Плюс разбор пересланного (кто автор, откуда) и вытаскивание ссылок.
"""
import json
import re
import time
import threading
import collections
from pathlib import Path

from botctx import (
    ALBUM_BUFFER_SEC, BURST_BUFFER_SEC, COMMAND_RE, PROFILE, VOICE_DIARIZE_SEC, log,
    _album_buffers, _album_lock, _brief_level, _burst_buffers, _burst_lock,
    _RESIDENT, _RESIDENT_LOCK,
)
from markers import parse_tts_marker
from media import (
    _extract_attachment_tuple, auto_save_attachment, clear_pending,
    download_file_to, get_pending, get_pending_list, set_pending,
)
from menu import (
    BOT_MENU_COMMANDS, SKILL_ALIASES, _kb_root, _rewrite_skill_alias, _send_brief,
    _text_root,
)
from tgapi import api, send_chunked
from turn import do_compact, process_user_text
from vaultio import (
    add_to_allowlist, append_to_inbox_raw, load_allowlist, reset_session,
    route_to_do_queue,
)
from voice import transcribe_voice


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
        api("sendMessage", chat_id=chat_id,
            text=f"⏳ висит {len(stale_warnings)} pending старше 5 мин — разрешить сначала?")
    links_note = ("\n\n" + "\n".join(link_blocks)) if link_blocks else ""
    full = (text + attachment_note + links_note).strip()
    if not full:
        api("sendMessage", chat_id=chat_id, text="(пустой альбом)")
        return
    process_user_text(uid, chat_id, full, msg_id=msg_id, source="tg-album", summary_text=text or "[album]")

def _handle_single_message(msg):
    """Identical to handle_message but skips album-buffer check (used by flush)."""
    handle_message(msg, _from_album=True)

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
            anchor = text[off:off + ln] if text else ""
            url = e["url"]
            key = (anchor, url)
            if key in seen:
                continue
            seen.add(key)
            lines.append(f'- «{anchor}» → {url}')
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
            lines.append(f'- [кнопка] «{label}» → {url}')
    if not lines:
        return ""
    return "[ССЫЛКИ В СООБЩЕНИИ]\n" + "\n".join(lines)


_GroupGate = collections.namedtuple("_GroupGate", "allowed mentioned is_reply_to_bot reason")


def _group_gate(msg, chat_type):
    """Кто в групповом чате имеет право говорить с ботом.

    Хозяин — всегда. Остальные — только через @упоминание бота или ответ на его
    сообщение. Обращение, начатое с @другого-бота, пропускаем мимо ушей (в группах
    сидят боты друзей, у всех выключен privacy mode — иначе перебиваем друг друга).
    В личке фильтра нет.
    """
    if chat_type not in ("group", "supergroup"):
        return _GroupGate(True, False, False, "")

    bot_username = PROFILE["bot_username"]
    at_bot = "@" + bot_username
    uid = msg["from"]["id"]
    text_check = msg.get("text") or msg.get("caption") or ""
    ents = (msg.get("entities") or []) + (msg.get("caption_entities") or [])
    mentioned = any(
        e.get("type") == "mention" and at_bot in text_check[e.get("offset", 0):e.get("offset", 0) + e.get("length", 0)]
        for e in ents
    )
    # Запасной путь: сообщения от других ботов приходят без разметки упоминаний.
    if not mentioned and at_bot.lower() in text_check.lower():
        mentioned = True
    reply_to = msg.get("reply_to_message") or {}
    is_reply_to_bot = (reply_to.get("from") or {}).get("username") == bot_username

    stripped = text_check.lstrip()
    if stripped.startswith("@") and not stripped.lower().startswith(at_bot.lower()):
        return _GroupGate(False, mentioned, is_reply_to_bot,
                          f"addressed to other @-mention: {stripped[:40]}")
    if uid != PROFILE["owner_uid"] and not mentioned and not is_reply_to_bot:
        return _GroupGate(False, mentioned, is_reply_to_bot, "no mention/reply")
    return _GroupGate(True, mentioned, is_reply_to_bot, "")


def _extract_forward_meta(msg):
    fo = msg.get("forward_origin") or {}
    if fo:
        otype = fo.get("type", "?")
        if otype == "channel":
            ch = fo.get("chat", {})
            return f"[FORWARD_META] Переслано из канала: @{ch.get('username') or '?'} ({ch.get('title') or '?'}), msg_id={fo.get('message_id')}, дата={fo.get('date')}"
        if otype == "user":
            u = fo.get("sender_user", {})
            return f"[FORWARD_META] Переслано от пользователя: @{u.get('username') or '?'} (id={u.get('id')}, имя={u.get('first_name','?')})"
        if otype == "hidden_user":
            return f"[FORWARD_META] Переслано от скрытого пользователя: {fo.get('sender_user_name','?')}"
        if otype == "chat":
            ch = fo.get("sender_chat", {})
            return f"[FORWARD_META] Переслано из чата: @{ch.get('username') or '?'} ({ch.get('title') or '?'})"
        return f"[FORWARD_META] type={otype} raw={json.dumps(fo, ensure_ascii=False)[:300]}"
    if msg.get("forward_from"):
        u = msg["forward_from"]
        return f"[FORWARD_META] Переслано от @{u.get('username') or '?'} ({u.get('first_name','?')})"
    if msg.get("forward_from_chat"):
        ch = msg["forward_from_chat"]
        return f"[FORWARD_META] Переслано из @{ch.get('username') or '?'} ({ch.get('title') or '?'}, type={ch.get('type')})"
    return ""


# --- Заслон перед AssemblyAI на длинных записях (25.08.2026, по просьбе хозяина) ---
# Записи > VOICE_DIARIZE_SEC уходят в платный AssemblyAI с разбором по голосам.
# Молча это делать нельзя: сначала кнопка «✅ отправить», и только по ней — расшифровка.
# Живёт здесь, а не в voice.py: гейт после подтверждения продолжает ход разговора
# (process_user_text), а voice.py про ход ничего не знает и знать не должен.
_PENDING_AAI = {}            # uid -> {file_id, duration, chat_id, date, text, ts}
_PENDING_AAI_LOCK = threading.Lock()
_AAI_KEYBOARD = json.dumps({"inline_keyboard": [[
    {"text": "✅ отправить", "callback_data": "aai:ok"},
    {"text": "✖ отмена", "callback_data": "aai:no"},
]]}, ensure_ascii=False)


def _aai_gate(uid, chat_id, voice, msg_date, prefix_text=""):
    """True = запись длинная, ход перехвачен, ждём подтверждения кнопкой."""
    dur = voice.get("duration", 0) or 0
    if dur <= VOICE_DIARIZE_SEC:
        return False
    with _PENDING_AAI_LOCK:
        _PENDING_AAI[uid] = {
            "file_id": voice["file_id"], "duration": dur, "chat_id": chat_id,
            "date": msg_date, "text": (prefix_text or "").strip(), "ts": time.time(),
        }
    log(f"AAI-gate: voice {dur}s от uid={uid} ждёт подтверждения")
    api("sendMessage", chat_id=chat_id,
        text=(f"🎙 Запись {dur // 60} мин {dur % 60} сек.\n\n"
              f"Отправляю в AssemblyAI с разбором по голосам (диаризация) — платно. Подтверди."),
        reply_markup=_AAI_KEYBOARD)
    return True


def drop_pending_aai(uid):
    """Нажали ✖ — забыть запрос. Зовётся из обработчика кнопок."""
    with _PENDING_AAI_LOCK:
        _PENDING_AAI.pop(uid, None)


def run_pending_aai(uid, chat_id):
    """Нажали ✅ — качаем, расшифровываем, отдаём ход как обычное голосовое."""
    with _PENDING_AAI_LOCK:
        p = _PENDING_AAI.pop(uid, None)
    if not p:
        api("sendMessage", chat_id=chat_id, text="Нечего расшифровывать — запрос уже протух.")
        return
    dur = p["duration"]
    api("sendMessage", chat_id=chat_id,
        text=f"🎧 Расшифровываю {dur // 60} мин через AssemblyAI, это займёт несколько минут…")
    transcript = transcribe_voice(p["file_id"], dur, p.get("date"))
    if transcript:
        note = f"[VOICE_TRANSCRIPT] {dur}сек: «{transcript}»"
    else:
        note = f"[VOICE_FAIL] голосовое {dur}сек — транскрипция не удалась"
    prefix = p.get("text") or ""
    full = (prefix + "\n" + note).strip() if prefix else note
    process_user_text(uid, chat_id, full, source="aai", summary_text=f"[🎙 {dur}с]")


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
            # Длинная запись → в AssemblyAI только после кнопки. Ход обрываем целиком,
            # текст остальных сообщений пачки сохраняем и приклеим после подтверждения.
            if _aai_gate(uid, chat_id, v, m.get("date"), "\n".join(summary_parts)):
                return
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
        chunk = (text + attachment_note + (("\n\n" + fwd_meta) if fwd_meta else "")
                 + (("\n\n" + links_block) if links_block else "")).strip()
        if chunk:
            parts.append(chunk)
    if not parts:
        return
    full = "\n\n---\n\n".join(parts)
    summary_text = " | ".join(summary_parts) if summary_parts else "[burst]"
    process_user_text(uid, chat_id, full, msg_id=msg_id, source="tg-burst", summary_text=summary_text)

def handle_message(msg, _from_album=False, _from_burst=False):
    uid = msg["from"]["id"]
    uname = msg["from"].get("username") or msg["from"].get("first_name", "?")
    chat_id = msg["chat"]["id"]
    msg_id = msg["message_id"]
    chat_type = (msg.get("chat") or {}).get("type", "private")
    fname = msg["from"].get("first_name", "") or ""
    lname = msg["from"].get("last_name", "") or ""
    author_label = (fname + " " + lname).strip() or msg["from"].get("username") or str(uid)

    gate = _group_gate(msg, chat_type)
    mentioned, is_reply_to_bot = gate.mentioned, gate.is_reply_to_bot
    if not gate.allowed:
        log(f"GROUP-SKIP uid={uid} ({author_label}) {gate.reason}")
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

    # Forward metadata (единственный разбор — _extract_forward_meta в этом же модуле)
    fwd_meta = _extract_forward_meta(msg)

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
            try: rc_kill.kill()
            except Exception as e: log(f"  /new: resident kill err: {e}")
        api("sendMessage", chat_id=chat_id, text="✅ Новая сессия. Прошлый контекст сброшен.")
        return
    if text.strip() == "/menu":
        _brief_level[chat_id] = "root"
        _send_brief(chat_id, _text_root(), _kb_root())
        return
    if text.strip() == "/start":
        api("sendMessage", chat_id=chat_id,
            text="Привет. Я тот же ассистент, что у тебя в Claude Code. Пиши что угодно — мысль/идею/вопрос. Перед сохранением переспрошу. /new — новая сессия. Меню «/» — список скилов.")
        return

    # Skill aliases: TG menu allows only [a-z0-9_], skills use hyphens.
    # Rewrite "/process_inbox foo" → "/process-inbox foo" before forwarding to Claude.
    text = _rewrite_skill_alias(text)

    # Browser-automation router: Avito/Ozon commands -> do-queue.md (Mac/CDP executes)
    if text and COMMAND_RE.search(text):
        route_to_do_queue(text)
        api("sendMessage", chat_id=chat_id,
            text=f"✅ Команда в очередь:\n«{text[:160]}»\n\nИсполнится при заходе на Mac (CDP-Chrome).")
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
        if _aai_gate(uid, chat_id, v, msg.get("date"), text or ""):
            return
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
                stale_summary = ", ".join(f"{r['kind']}/{r.get('ext','?')}" for r in stale[:3])
                api("sendMessage", chat_id=chat_id,
                    text=f"⏳ висит {len(stale)} pending старше 5 мин ({stale_summary}) — разрешить сначала?")
            all_pending = get_pending_list(chat_id)
            if len(all_pending) <= 1:
                attachment_note = (
                    f"\n[ATTACHMENT_PENDING] pid={pid} kind={kind} ext={ext} meta={summary}. "
                    f"Auto-save не сработал (обычно >20MB Bot API). "
                    f"Если нужно — первая строка ответа `__SAVE_ATTACHMENT__ {pid} <путь>` "
                    f"или `__DROP_ATTACHMENT__ {pid}`."
                )
            else:
                descs = [f"  - pid={r['pid']} kind={r['kind']} ext={r['ext']} {r['summary']}" for r in all_pending]
                attachment_note = (
                    f"\n[ATTACHMENT_PENDING] в очереди {len(all_pending)} файлов "
                    f"(новый pid={pid}, auto-save fail). Команды: `__SAVE_ATTACHMENT__ <pid> <путь>` / "
                    f"`__DROP_ATTACHMENT__ <pid>` / `__DROP_ALL_ATTACHMENTS__`. "
                    f"Список:\n" + "\n".join(descs)
                )

    # voice decision moved to reply parser (parse_tts_marker)

    links_block = _extract_links(msg)
    full = (text + attachment_note + ("\n\n" + fwd_meta if fwd_meta else "")
            + ("\n\n" + links_block if links_block else "")).strip()
    if chat_type in ("group", "supergroup") and full:
        full = f"[от {author_label} (uid={uid})]: {full}"
    if not full:
        return

    process_user_text(uid, chat_id, full, msg_id=msg_id, source="tg", summary_text=text)
