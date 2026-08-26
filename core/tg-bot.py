#!/usr/bin/env python3
"""Вход бота: опрос Telegram, тапы кнопок, эндпоинт голоса, сборка модулей.

Тонкий вход. Всё, что раньше лежало в одном файле на пять тысяч строк, теперь
разложено по модулям рядом, и этот файл только собирает их вместе:

    botprofile.py  профиль хозяина и его проверка на старте
    botctx.py      пути, лимиты, журнал, всё живое состояние чатов
    tgapi.py       вызовы Telegram, разметка, нарезка, кнопки, живая панель
    prompt.py      блоки системного промпта, личность из заметок, сборка
    markers.py     разбор служебных маркеров ответа модели
    vaultio.py     хранилище, git, входящие, задачи, манифест, сессии
    media.py       вложения: приём файлов, картинки и документы
    voice.py       расшифровка голосовых и озвучка ответов
    menu.py        команды бота и навигация по хранилищу кнопками
    claude_run.py  запуск модели: резидент, СТОП, догонка
    turn.py        ход разговора целиком, фоновые и ничейные ходы
    intake.py      приём сообщения: групповой фильтр, склейки, пересланное

Импорты ниже — сквозные: имена ре-экспортируются, чтобы остаток кода и тесты
видели их там же, где раньше. Две стрелки поставлены руками (set_turn_runner,
set_stray_handler) — там, где модули иначе замкнулись бы в кольцо.

Поведение бота:
- Каждое сообщение → резидентный процесс claude в хранилище хозяина.
- Новое сообщение во время ответа прерывает текущий ход и склеивается с ним.
- Первое сообщение заводит сессию, /new её сбрасывает.
- Ответ уходит в TG кусками по 4000 знаков.
- После каждого хода хранилище синхронизируется (модель могла писать файлы).
- Файлы сохраняются во вложения, путь передаётся модели в запросе.
"""
import http.server
import json
import socket
import ssl
import sys
import threading
import time
# 2026-06-05: резидентный процесс claude вместо Popen-на-каждый-ход.
# Холодный старт (~6с на VPS) платится один раз при создании, второй и
# последующие ходы — без оверхеда Node.js + загрузки claude-binary + MCP.
# Модуль resident_claude.py (рядом с ядром или в home хозяина) — самодостаточный.
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# Модули ядра лежат рядом с этим файлом. Явная строка нужна, когда ядро
# загружают не как скрипт, а по пути (так делают тесты через harness.py).
sys.path.insert(0, str(Path(__file__).resolve().parent))

from botctx import (  # noqa: F401  (сквозной ре-экспорт для остатка ядра)
    ProfileError, load_profile, ResidentClaude,
    PROFILE, _SECRETS, TOKEN_FILE, ALLOW_FILE, CACHE_DIR, OFFSET_FILE,
    SESSIONS_FILE, VAULT, ATTACH, INBOX, DO_QUEUE, COMMAND_RE, PENDING_FILE,
    MANIFEST_DIR, VOICE_ARCHIVE_DIR, VOICE_ARCHIVE_TTL_DAYS,
    VOICE_LONG_THRESHOLD_SEC, NATIVE_MIN_SEC, ASSEMBLYAI_KEY_FILE,
    GROQ_KEY_FILE, GROQ_MODEL, PENDING_TTL_SEC, PENDING_NAG_AFTER_SEC,
    PENDING_CAL_FILE, PENDING_CAL_TTL_SEC, PENDING_SEND_FILE,
    PENDING_SEND_TTL_SEC, TG_SEND_ONESHOT, TG_SEND_PYTHON, TASKS_FILE,
    ALBUM_BUFFER_SEC, BURST_BUFFER_SEC, MSK, CLAUDE_BIN, CLAUDE_TIMEOUT,
    TG_CHUNK, SESSION_TTL_HOURS, VOICE_PORT, SHORTCUT_TOKEN_FILE, VOICE_CERT,
    VOICE_KEY, EDGE_TTS_BIN, TTS_VOICE_DEFAULT, VOICE_REPLY_MAX_CHARS,
    ZVUKOGRAM_API, ZVUKOGRAM_KEY_PATH, ZVUKOGRAM_EMAIL_PATH, TTS_MARKER_RE,
    VOICE_TAG_MAP, log, read_token, read_shortcut_token, TOKEN, SHORTCUT_TOKEN,
    API, FILE_API, _CONFIRM_KEYBOARD, _MENU_ROW, _STOP_KEYBOARD, _NO_KEYBOARD,
    _RUNNING_PROCS, _RUNNING_PROCS_LOCK, _RESIDENT, _RESIDENT_LOCK,
    _STOP_FLAGS, _STOP_POSTED, _STREAM_PARTIAL, _TURN_LOCKS, _TURN_LOCKS_GUARD,
    _INTERRUPT_FLAGS, _INTERRUPTED_SENTINEL, _get_turn_lock, _album_lock,
    _album_buffers, _burst_lock, _burst_buffers, _brief_active_msg,
    _brief_level
)

# Локальный Whisper убран 10.08.2026 — расшифровка целиком облачная:
# Groq (whisper-large-v3) → родная расшифровка Telegram → AssemblyAI.

from voice import (  # noqa: F401  (сквозной ре-экспорт для остатка ядра)
    _archive_voice, cleanup_voice_archive, _read_assemblyai_key,
    transcribe_assemblyai, NATIVE_VENV_PY, NATIVE_HELPER,
    transcribe_voice_native, _read_groq_key, transcribe_groq, transcribe_voice,
    _strip_for_tts, _zvukogram_creds, _synth_voice_zvukogram, _synth_voice_ogg,
    api_send_voice, _split_for_voice, send_voice_reply
)

from prompt import (  # noqa: F401  (сквозной ре-экспорт для остатка ядра)
    _CORE, _PERSONA_SLOTS, _PERSONA_HEADING_RE, load_persona, _fill,
    _drop_lines, _time_block, _neighbors_block, _tts_block, _dir_exists,
    _selfconf_block, build_system_prompt, SYSTEM_PROMPT
)




from menu import set_turn_runner
from menu import (  # noqa: F401  (сквозной ре-экспорт для остатка ядра)
    _BOT_INTERNAL_COMMANDS, _discover_skill_commands, _skill_cmds,
    SKILL_ALIASES, BOT_MENU_COMMANDS, _rewrite_skill_alias, _BRIEF_PROJECTS,
    _BRIEF_PROJ_BY_SLUG, _BRIEF_CATEGORIES, _BRIEF_CAT_BY_SLUG, _HIDDEN_AT_TOP,
    _BRIEF_SUBPROJECTS, _parse_status_dashboard, _shorten, _kb_root,
    _text_root, _MOD_RANK, _project_mod, _category_mod, _kb_projects,
    _kb_category, _text_category, _text_projects, _parent_of, _kb_project,
    _text_project, _describe_level, _nav_state_line, _send_brief,
    _level_text_kb, handle_brief_nav_callback, handle_brief_file_callback,
    _clean_file_for_view, _CONTENT_MARKER_RE, _drop_empty_sections,
    setup_bot_menu
)


from tgapi import (  # noqa: F401  (сквозной ре-экспорт для остатка ядра)
    _BENIGN_API_ERRORS, api, TypingPulser, STATUS_PANEL_LIMIT,
    STATUS_PANEL_BODY_MAX, esc_html, build_status_panel, _CB_TOKEN, _IC_TOKEN,
    md_to_html, strip_html, _TABLE_SEP_RE, _NESTED_LI_RE, _HEADING_RE,
    _ORDERED_RE, _CHECKBOX_RE, _DIVIDER_RE, _RICH_RES, _wants_rich, chunk_text,
    _build_choices_markup, send_chunked, _oneshot, tg_resolve_peer,
    tg_send_now, _strip_active_keyboard
)


# ── inline images from Claude replies (2026-05-15) ────────────────────────
# Когда reply содержит markdown-картинку или путь Resources/...{jpg,png,...}
# на отдельной строке — шлём sendPhoto, путь вырезаем из текста.

from media import (  # noqa: F401  (сквозной ре-экспорт для остатка ядра)
    _INLINE_IMG_EXTS, _INLINE_IMG_MD_RE, _INLINE_IMG_BARE_RE,
    _TG_PHOTO_MAX_BYTES, _resolve_inline_image, api_send_photo,
    extract_and_send_images, _INLINE_DOC_EXT_RE, _INLINE_DOC_BARE_RE,
    _TG_DOC_MAX_BYTES, api_send_document, extract_and_send_documents,
    download_file_to, _ATTACH_NAME_RE, _ISO_PREFIX_RE, _make_attach_path,
    auto_save_attachment, load_pending, save_pending, _prune_pending_list,
    set_pending, get_pending_list, get_pending, pop_pending_by_pid,
    pop_pending_oldest, clear_pending, _extract_attachment_tuple,
    _ATTACH_LINE_RE, _DROP_ALL_RE, process_attachment_markers
)


# ── inline documents from Claude replies (2026-05-18) ─────────────────────
# Когда reply содержит путь к файлу с документным расширением на отдельной строке —
# шлём sendDocument, путь вырезаем из текста.





from vaultio import (  # noqa: F401  (сквозной ре-экспорт для остатка ядра)
    load_allowlist, allowlist_owner, add_to_allowlist, get_offset, save_offset,
    load_sessions, save_sessions, get_session, significant_diff,
    update_vault_head, set_session, get_msg_count, get_total_tokens,
    reset_session, COMPACT_FIRST_AT, COMPACT_REPEAT, safe_vault_path,
    SIGNIFICANT_PATHS, vault_head, route_to_do_queue, git_sync,
    append_to_inbox_raw, append_tasks_from_markers, write_manifest_entry,
    append_chatlog, rag_reindex
)






from claude_run import set_stray_handler, start_resident_reaper
from claude_run import (  # noqa: F401  (сквозной ре-экспорт для остатка ядра)
    _humanize_action, _summarize_tool_input, _EFFORT_HIGH_TRIGGERS,
    pick_effort, call_claude
)













from markers import (  # noqa: F401  (сквозной ре-экспорт для остатка ядра)
    _CHOICE_LINE_RE, detect_choices, needs_confirm, parse_tts_marker,
    _WROTE_RE, _TASK_RE, _BG_TASK_RE, parse_wrote_markers, parse_task_markers,
    parse_bg_task_marker, _CHOICES_RE, parse_choices_marker, _AFFIRM_RE,
    parse_affirm_marker, _DIG_RE, parse_dig_marker, _AUTO_DIG_MIN_CHARS,
    auto_dig, _CAL_PROPOSE_RE, _CAL_EVENT_RE, _CAL_CANCEL_RE,
    _parse_cal_json_marker, parse_cal_propose_marker, parse_cal_event_marker,
    parse_cal_cancel_marker, _pending_cal_lock, _load_pending_cal_all,
    _save_pending_cal_all, get_pending_cal, set_pending_cal, clear_pending_cal,
    _TG_SEND_PROPOSE_RE, _TG_SEND_CONFIRM_RE, _TG_SEND_CANCEL_RE,
    parse_tg_send_propose_marker, _parse_flag_marker,
    parse_tg_send_confirm_marker, parse_tg_send_cancel_marker,
    _pending_send_lock, _load_pending_send_all, _save_pending_send_all,
    get_pending_send, set_pending_send, clear_pending_send
)























from turn import (  # noqa: F401  (сквозной ре-экспорт для остатка ядра)
    _dispatch_stray_turn, _run_bg_task, process_user_text, do_compact
)


# Меню умеет запускать ход по кнопке «📅 daily» — отдаём ему вход явной строкой,
# чтобы модули не импортировали друг друга по кольцу.
set_turn_runner(process_user_text)
set_stray_handler(_dispatch_stray_turn)


# ---------------------------------------------------------------------------
# Fix-J: media_group_id album buffering
# ---------------------------------------------------------------------------


from intake import (  # noqa: F401  (сквозной ре-экспорт для остатка ядра)
    _flush_album, _buffer_album_message, _handle_album, _handle_single_message,
    _flush_burst, _buffer_burst_message, _extract_links, _GroupGate,
    _group_gate, _extract_forward_meta, _handle_burst, handle_message,
    drop_pending_aai, run_pending_aai
)






# ---------------------------------------------------------------------------
# Burst (per-user debounce): glue forward+caption / multi-message bursts
# from the same (uid, chat_id) into one logical turn so we make a single
# LLM call instead of N. Slash commands and CDP router run BEFORE this and
# skip buffering. Album (media_group_id) buffering runs BEFORE this too.
# ---------------------------------------------------------------------------




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
            api("editMessageReplyMarkup", chat_id=chat_id, message_id=msg_id,
                reply_markup=json.dumps({"inline_keyboard": []}))
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
            # 2026-07-06 (по просьбе хозяина): гасим стрим-черновик МГНОВЕННО, постя финальное
            # РЕАЛЬНОЕ сообщение. Пустой текст черновик НЕ удаляет — рисует плейсхолдер
            # «Thinking…» на ~10с и держит поле ввода (Deep Research, журнал 2026-07-06).
            # Реальное сообщение гасит черновик и освобождает ввод сразу. Партиал берём
            # из _STREAM_PARTIAL; двойной пост из хода гасим флагом _STOP_POSTED.
            try:
                _pl = _STREAM_PARTIAL.get(chat_id)
                _partial = ("".join(_pl)).strip() if _pl else ""
                _msg = (_partial + "\n\n🛑 остановлено по кнопке.") if _partial \
                    else "🛑 остановлено по кнопке (ответ ещё не начался)."
                api("sendMessage", chat_id=chat_id, text=_msg[:TG_CHUNK])
                _STOP_POSTED[chat_id] = True
            except Exception:
                pass
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
    if data == "aai:no":
        drop_pending_aai(uid)
        api("sendMessage", chat_id=chat_id, text="Отменил, в AssemblyAI не отправляю.")
        return
    if data == "aai:ok":
        # Расшифровка длинной записи идёт минутами — уводим в отдельный поток,
        # чтобы опрос обновлений Telegram не залипал.
        threading.Thread(target=run_pending_aai, args=(uid, chat_id), daemon=True).start()
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
        api("editMessageReplyMarkup", chat_id=chat_id, message_id=msg_id,
            reply_markup=json.dumps({"inline_keyboard": []}))
    process_user_text(uid, chat_id, reply_text, source="callback", summary_text=summary)


















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
            self._send_json(code,
                {"ok": True, "reply": payload} if ok else {"ok": False, "error": payload})

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
        tls = self._ssl_ctx.wrap_socket(sock, server_side=True,
                                        do_handshake_on_connect=False)
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




def main():
    # Проверка профиля — до первого сетевого вызова. Жёсткий провал (нет vault,
    # не читается токен, не запускается claude) обязан убить процесс здесь, а не
    # обернуться получасовой работой в чужой папке.
    try:
        PROFILE.validate(log=log)
    except ProfileError as e:
        log(f"ПРОФИЛЬ НЕ ГОДЕН — не стартую.\n{e}")
        sys.exit(1)
    log(f"bot v2 started, vault={VAULT}, allowlist={load_allowlist()}")
    setup_bot_menu()
    cleanup_voice_archive()
    start_voice_server()
    # 2026-08-27: гасит резидентов, простоявших дольше resident_idle_sec.
    # Без него процесс `claude` в чате живёт до перезапуска юнита и течёт.
    start_resident_reaper()
    offset = get_offset()
    while True:
        try:
            req = urllib.request.Request(
                f"{API}/getUpdates?timeout=30&offset={offset}"
            )
            with urllib.request.urlopen(req, timeout=40) as r:
                data = json.load(r)
            if not data.get("ok"):
                log(f"getUpdates not ok: {data}")
                time.sleep(5)
                continue
            for upd in data["result"]:
                offset = upd["update_id"] + 1
                if "message" in upd:
                    # 2026-07-05: dispatch each message in its own daemon thread so the
                    # getUpdates loop keeps polling while a turn runs. Без этого главный
                    # цикл блокировался на handle_message и не забирал догонку-сообщение
                    # → машинерия прерывания (call_claude «догонка») никогда не срабатывала.
                    def _dispatch(m=upd["message"]):
                        try:
                            handle_message(m)
                        except Exception as e:
                            log(f"handle_message error: {e}")
                            try:
                                api("sendMessage",
                                    chat_id=m["chat"]["id"],
                                    text=f"⚠️ Внутренняя ошибка: {e}")
                            except Exception:
                                pass
                    threading.Thread(target=_dispatch, daemon=True).start()
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
