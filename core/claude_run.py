#!/usr/bin/env python3
"""Запуск модели: резидентный процесс, живая панель, СТОП, догонка.

Главный и самый нервный кусок бота. Что здесь происходит по шагам: берётся
замок на чат (один ход в один момент), поднимается или переиспользуется
резидент — долгоживущий процесс модели, чтобы не платить холодный старт
каждый раз, — читается поток событий, и на каждое событие перерисовывается
живая панель «думаю…».

Две механики, из-за которых код такой плотный. Первая — кнопка СТОП: она
должна срабатывать мгновенно, посреди потока. Вторая — догонка: новое
сообщение во время ответа не ждёт в очереди, а прерывает текущий ход
по-хорошему (процесс жив, история цела) и склеивается с ним в один ответ.
Обе живут на общих словарях состояния из botctx и на замках.

Переписывать это при распиле не стали ни строчки: код перенесён дословно.
"""
import json
import os
import re
import threading
import time
from pathlib import Path

from botctx import (
    CLAUDE_BIN, CLAUDE_TIMEOUT, PROFILE, ResidentClaude,
    STATUS_PANEL_MIN_INTERVAL_SEC, VAULT, log,
    _INTERRUPTED_SENTINEL, _INTERRUPT_FLAGS, _RESIDENT, _RESIDENT_LOCK,
    _RESIDENT_SEEN,
    _RUNNING_PROCS, _RUNNING_PROCS_LOCK, _STOP_FLAGS, _STOP_POSTED,
    _STOP_KEYBOARD, _STREAM_PARTIAL, _get_turn_lock, _FOLLOWUP_GATES,
    carry, ckey, ckey_topic, in_topic, topic_session_name,
)
import engine
import meters
from prompt import SYSTEM_PROMPT, build_system_prompt, load_persona
from tgapi import api, build_status_panel, strip_html, think_spin, _BENIGN_API_ERRORS

# «Ничейный ход» — резидент проснулся сам (напоминалка, фоновая задача) и
# выдал текст, которого никто не просил. Обработка такого хода живёт в turn.py,
# а turn.py зависит от этого модуля — импортировать назад значило бы кольцо.
# Поэтому обработчик ставится снаружи одной строкой при сборке.
_stray_handler = None


def set_stray_handler(fn):
    """Кто разбирает ничейный ход. Ставит tg-bot.py при сборке."""
    global _stray_handler
    _stray_handler = fn


def _stray_in_topic(chat_id, thread_id, events):
    """Ничейный ход резидента темы: отвечаем в ту же тему."""
    with in_topic(chat_id, thread_id):
        _stray_handler(chat_id, chat_id, events)


# 25.09.2026 (слово хозяина 04:29 «надо режим досылания делать»): что делать с сообщением,
# пришедшим посреди хода. «interrupt» — старая догонка: ход рвётся, обрывок «✂️ прервано»,
# новое сообщение отдельным ходом. «soft» — ход не рвётся, сообщение дописывается внутрь
# него, в чат метка «➕ поправка». Выключатель — поле "followup" в settings_json хозяина
# (у хозяина это ~/.claude/bot-model.json), по умолчанию interrupt: соседи не меняются.
_SOFT_PREFIX = (
    "[Хозяин дослал это по ходу твоего ответа, не дожидаясь конца. Ответ НЕ прерван: "
    "учти досланное и продолжай с того места, где был. Уже написанное не повторяй, "
    "дописывай только новое.]\n\n"
)


def _followup_mode():
    try:
        _settings = engine.bin_and_settings(engine.stored())[1]
        return json.loads(Path(_settings).read_text()).get("followup") or "interrupt"
    except Exception:
        return "interrupt"


def _soft_followup(chat_id, prompt_text):
    """Дописать сообщение в идущий ход. False — резидента нет или запись не прошла,
    тогда зовущий откатывается на старую догонку. chat_id здесь — ключ беседы (ckey)."""
    with _RESIDENT_LOCK:
        running = _RESIDENT.get(chat_id)
    if running is None or not running.is_alive():
        return False
    # ворота до инъекции: если досланное встанет отдельным ходом, его ничейный ответ
    # ждёт, пока в чат уйдёт ответ идущего хода, иначе продолжение обгонит начало
    _FOLLOWUP_GATES[chat_id] = threading.Event()
    if not running.inject(_SOFT_PREFIX + prompt_text):
        _FOLLOWUP_GATES.pop(chat_id, None)
        return False
    log(f"  мягкий досыл: сообщение дописано в идущий ход на чате {chat_id}")
    # Acknowledgement belongs to the existing streamed answer, never a new message.
    partial = _STREAM_PARTIAL.get(chat_id)
    if partial is not None:
        partial.append("➕")
    return True


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
        if "offset" in inp: extras.append(f"off={inp['offset']}")
        if "limit" in inp: extras.append(f"lim={inp['limit']}")
        suffix = (" " + " ".join(extras)) if extras else ""
        return f"{path}{suffix}"
    if name == "Bash":
        cmd = (inp.get("command") or "").replace("\n", " ")
        return cmd[:160]
    if name in ("Grep", "Glob"):
        return f"pat={inp.get('pattern','')!r} path={inp.get('path','')}"[:160]
    if name == "Agent" or name == "Task":
        return f"sub={inp.get('subagent_type','?')} desc={inp.get('description','')[:80]}"
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
    re.IGNORECASE
)

def pick_effort(prompt_text):
    """Fixed high по решению хозяина 2026-06-16 ~01:00 (overrides earlier fixed medium from 2026-05-08).
    Old hybrid logic kept inline in case we revert: triggered high on think-hard
    keywords or long prompts (>600 chars)."""
    return "high"

def call_claude(prompt_text, session_id=None, status_chat_id=None, bg_wait=False, engine_name=None, chat_id=None):
    """Run headless claude with stream-json output. Logs each tool_use as it streams.
    If status_chat_id is given, posts a live status message in TG and edits it as
    tool_use events arrive (throttled to 1 edit/sec). Returns (reply_text, new_session_id, used_tokens).
    """
    # 2026-06-05: переезд с Popen-per-turn на ResidentClaude (живой процесс на чат).
    # Холодный старт ~6с платится один раз при создании, на 2-м и след. ходах ~0.
    env_extra = {
        "HOME": PROFILE["home"],
        "PATH": os.path.dirname(CLAUDE_BIN) + ":/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
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
    last_panel = [""]        # что уже висит в панели — не переотправляем то же самое
    # 2026-06-04: флаг — пришли ли дельты текста через stream_event.content_block_delta.text_delta.
    # Если да, финальный assistant.text игнорируем (его контент уже накопился из дельт).
    # Если нет (фоллбэк — флаг --include-partial-messages не сработал) — берём из assistant.text.
    stream_text_received = [False]
    # 2026-06-27: ставим пустую строку между текстовыми блоками, разорванными
    # инструментом (text → tool → text). Флаг взводится на tool_use, гасится при
    # следующем тексте — тогда вставляем "\n\n" перед ним. Между дельтами одного
    # блока разделитель НЕ вставляется (иначе слова разорвало бы).
    pending_sep = [False]
    # Нативный черновик Telegram (sendMessageDraft) похоронен 18.08.2026 по решению
    # хозяина. История: включён 05.07, выключен 06.07 из-за трёх клиентских глюков
    # Android (блок поля ввода, ghost-пузырь на ⏹Стоп, переанимация при переоткрытии
    # чата — bugs.telegram.org/c/62189, закрыт как «intended»). С 06.07 по 18.08 весь
    # механизм лежал недостижимым балластом. Поток идёт через editMessageText в панель
    # (ветка `streamed_text` в update_status), прерывание держит кнопка ⏹СТОП.
    # Разбор — журнал/2026-07-06 стрим черновика — блокировка доставки на Android.md
    # chunk_flushed остаётся [0] навсегда — ветки заморозки/стопа/финала ниже читают
    # [chunk_flushed[0]:] = весь накопленный текст.
    chunk_flushed = [0]
    # Публикуем «принял, думаю…» со ⏹ СТОП сразу, ещё до старта claude -p,
    # чтобы хозяин мог нажать СТОП прямо в момент когда понял «не туда пошёл».
    # 24.09.2026: status_panel=False (бот отдела) — панели нет совсем: ни «думаю…», ни ⏹СТОП,
    # ни потока правками. status_chat_id при этом живёт: замок хода, резидент и догонка работают.
    # 24.09.2026 16:15: status_panel="compact" (бот отдела, слово хозяина) — панель и ⏹СТОП есть,
    # но в ней только «думаю… (время)», текущий шаг и свёрнутый список вызовов; поток текста и
    # рассуждение не показываются, ответ приходит одним куском правкой той же панели.
    _panel_mode = PROFILE.get("status_panel", True)
    _show_panel = bool(_panel_mode)
    _compact = _panel_mode == "compact"
    def update_status(force=False):
        if not status_chat_id or not _show_panel or (not status_lines and (_compact or not streamed_text)):
            return
        elapsed = int(time.time() - t_start)
        m, s = divmod(elapsed, 60)
        elapsed_str = f"{m}м {s}с" if m else f"{s}с"
        # 2026-05-18: tool-строки (терминал) — под expandable blockquote.
        # "думаю…" и поток текста модели — снаружи, видны сразу.
        hourglass = "⏳" if elapsed % 2 == 0 else "⌛"
        text = build_status_panel(hourglass, elapsed_str, current_action[0],
                                  [] if _compact else thinking_text, status_lines,
                                  [] if _compact else streamed_text)
        now = time.time()
        # 2026-06-17: throttle 5.0 → 1.5с. На 5.0 текст и рассуждение прыгали раз в 5с —
        # выглядело как зависание. Telegram editMessageText держит ~1/сек/чат на один
        # message_id без 429; 1.5с — безопасный запас, при этом видно «слова текут».
        # 2026-06-27: 1.5 → 4.0с — хозяин не успевал читать на ходу, просил реже.
        if not force and now - last_edit[0] < STATUS_PANEL_MIN_INTERVAL_SEC:
            return
        # правка тем же текстом — это 400 «message is not modified» и ничего больше
        if text == last_panel[0]:
            return
        last_edit[0] = now
        last_panel[0] = text
        # ⏹СТОП живёт на панели всегда — это единственный способ прервать ход.
        panel_kb = _STOP_KEYBOARD
        if status_msg_id[0] is None:
            r = api("sendMessage", chat_id=status_chat_id, text=text,
                    parse_mode="HTML", reply_markup=panel_kb)
            if r.get("ok"):
                status_msg_id[0] = r["result"]["message_id"]
        else:
            r = api("editMessageText", chat_id=status_chat_id,
                    message_id=status_msg_id[0], text=text,
                    parse_mode="HTML", reply_markup=panel_kb)
            if not r.get("ok"):
                desc = r.get("description", "")
                if any(x in desc for x in _BENIGN_API_ERRORS):
                    return          # тот же текст — не о чем говорить
                # разметку Telegram не принял (или сообщение уже удалили) —
                # показываем то же самое голым текстом, панель важнее красоты
                api("editMessageText", chat_id=status_chat_id,
                    message_id=status_msg_id[0], text=strip_html(text),
                    reply_markup=panel_kb)

    # 2026-06-17: догонка. Один ход на чат. Если замок занят (идёт прошлый ход) —
    # прерываем тот ход protocol-interrupt'ом (процесс жив), ждём его чистого
    # завершения, забираем замок. Резидента для interrupt берём из _RESIDENT
    # напрямую; свежий rc фетчим уже под замком (ниже) — это переживает и случай,
    # когда прошлый ход умер по СТОП и резидента надо пересоздать.
    turn_lock = None
    did_interrupt = False
    # 08.10.2026: ключ беседы. Без темы это status_chat_id, как было; в теме
    # (chat_id, тема): у каждой темы свой замок, резидент, СТОП и досыл.
    # 09.10.2026 (слово хозяина 12:40: «не должно быть бесхозных бесед, всё к чему-то
    # привязано»): резидент привязан к беседе, даже если панели нет. Раньше в группе
    # без панели (status_chat_id=None) ключа не было: каждый ход поднимал новый
    # `claude` и бросал его живым. Штурм с соседним ботом в общей группе оставил 17
    # процессов по ~230 МБ, подкачка встала. chat_id даёт ключ без панели.
    _key_chat = status_chat_id if status_chat_id is not None else chat_id
    ck = ckey(_key_chat) if _key_chat is not None else None
    if ck is not None:
        turn_lock = _get_turn_lock(ck)
        if not turn_lock.acquire(blocking=False):
            if bg_wait or status_chat_id is None:
                # Беседа без панели (группа): прерывать нечем и некому показать
                # обрывок, ход ждёт свою очередь, как ждал бы фон.
                # 2026-06-19: фоновый ход (сторож за процессом) — НЕ прерывает живой
                # диалог хозяина, а вежливо ждёт замок. Хозяин главнее сторожа.
                log(f"  bg-ход ждёт замок на чате {status_chat_id} (не прерываю)")
                turn_lock.acquire()
            elif _followup_mode() == "soft" and _soft_followup(ck, prompt_text):
                # 25.09.2026 (слово хозяина 04:29): мягкий досыл. Идущий ход не прерываем,
                # досланное уже внутри него; своего хода у этого сообщения нет.
                return (_INTERRUPTED_SENTINEL, session_id, 0, 0, None)
            else:
                _INTERRUPT_FLAGS[ck] = True
                with _RESIDENT_LOCK:
                    running = _RESIDENT.get(ck)
                _dogonka_freed = threading.Event()
                if running is not None:
                    log(f"  догонка: прерываю текущий ход на чате {status_chat_id}")
                    try:
                        running.interrupt()
                    except Exception as e:
                        log(f"  interrupt() failed: {e}")
                    # 2026-07-06 (по просьбе хозяина): страховка как у кнопки STOP — если мягкий
                    # interrupt не отпустил замок за 4с (ход застрял в долгом вызове
                    # инструмента/субагенте), добиваем kill'ом. Иначе досыл ждёт
                    # замок бесконечно (инцидент 06.07: субагент в Reddit не прервать).
                    def _dogonka_escalate(rc=running, cid=status_chat_id, freed=_dogonka_freed):
                        if freed.wait(4.0):
                            return
                        try:
                            rc.kill()
                            log(f"  догонка escalate — interrupt не отпустил за 4с, killed чат {cid}")
                        except Exception as e:
                            log(f"  догонка escalate kill failed: {e}")
                    threading.Thread(target=_dogonka_escalate, daemon=True).start()
                turn_lock.acquire()  # ждём, пока прерванный ход освободит замок
                _dogonka_freed.set()
                did_interrupt = True
    # Only the owner of the turn may publish a panel or replace the STOP buffer.
    if status_chat_id:
        _STREAM_PARTIAL[ck] = streamed_text  # для callback ⏹СТОП
    if status_chat_id and _show_panel:
        try:
            r = api("sendMessage", chat_id=status_chat_id,
                    text=f"{think_spin('0с')} принял, думаю…",
                    parse_mode="HTML", reply_markup=_STOP_KEYBOARD)
            if r.get("ok"):
                status_msg_id[0] = r["result"]["message_id"]
        except Exception as e:
            log(f"early-status post failed: {e}")

    if did_interrupt:
        prompt_text = (
            "[Хозяин прервал твой предыдущий ответ и сразу дослал это сообщение. "
            "То, что ты успел написать, заморожено в чате как есть (обрывком) — "
            "повторять его не нужно. Ответь на это новое сообщение.]\n\n"
            + prompt_text
        )

    def _settings_model():
        """Единая точка выбора модели/усилия — settings.json хозяина (поле профиля).
        2026-07-02 (по просьбе хозяина): не хардкодить модель в коде — менять в одном месте."""
        try:
            cfg = json.loads(Path(run_settings).read_text())
            return (cfg.get("model") or "claude-opus-4-8[1m]",
                    cfg.get("effort") or "medium")
        except Exception:
            return ("claude-opus-4-8[1m]", "medium")

    # 08.10.2026: движок хода (Клод или Кодекс через переходник), см. engine.py
    engine_name = engine_name or engine.current()
    run_bin, run_settings = engine.bin_and_settings(engine_name)
    # Получаем живой ResidentClaude для этого чата или создаём.
    rc = None
    if ck is not None:
        with _RESIDENT_LOCK:
            existing = _RESIDENT.get(ck)
            # 08.10.2026: движок сменился — живой процесс другого движка гасим,
            # новый поднимется ниже со своей беседой (sid из своего файла бесед).
            if existing is not None and getattr(existing, "engine", "claude") != engine_name:
                _RESIDENT.pop(ck, None)
                try:
                    existing.kill()
                except Exception as e:
                    log(f"  engine switch: resident kill err: {e}")
                existing = None
            # 08.10.2026: пульт (pult.py) сменил модель или усилие — живой резидент
            # поднят со старыми флагами. Ход ещё не начат, гасим его, новый поднимется
            # ниже с той же беседой (resume) и новыми флагами.
            if existing is not None and existing.is_alive() and \
                    (getattr(existing, "model", None), getattr(existing, "effort", None)) != _settings_model():
                _RESIDENT.pop(ck, None)
                log(f"  pult: модель/усилие сменились ({existing.model}/{existing.effort} → "
                    f"{'/'.join(_settings_model())}), резидент поднимается заново")
                try:
                    existing.kill()
                except Exception as e:
                    log(f"  pult: resident kill err: {e}")
                existing = None
            if existing is not None and existing.is_alive():
                rc = existing
    if rc is None:
        try:
            _model, _effort = _settings_model()
            # Личность перечитывается при КАЖДОМ создании резидента: хозяин
            # поправил Self/tg-persona.md, сказал /new — получил новый тон,
            # рестарт юнита не нужен.
            # 08.10.2026: резидент темы подписан: в списке бесед Claude Code он
            # «TG: <название темы>», а таймер, поставленный из темы, знает, куда
            # вернуться (BOT_CHAT_ID/BOT_THREAD_ID читает tools/bot-timer.py).
            _topic_kw = {}
            _tid = ckey_topic(ck) if ck is not None else None
            if _tid:
                env_extra = {**env_extra, "BOT_CHAT_ID": str(_key_chat), "BOT_THREAD_ID": str(_tid)}
                _topic_kw["name"] = topic_session_name(_key_chat, _tid)
            rc = ResidentClaude(
                claude_bin=run_bin,
                system_prompt=build_system_prompt(PROFILE, load_persona(PROFILE, log=log)),
                model=_model,
                effort=_effort,
                cwd=str(VAULT),
                resume_sid=session_id,
                env_extra=env_extra,
                disallowed_tools=DISALLOWED,
                permission_mode="bypassPermissions",
                **_topic_kw,
            )
            rc.engine = engine_name
            rc.start()
        except Exception as e:
            if turn_lock is not None:
                try:
                    turn_lock.release()
                except RuntimeError:
                    pass
            return (f"⚠️ Ошибка запуска claude: {e}", session_id, 0, 0, status_msg_id[0])
        if ck is not None:
            with _RESIDENT_LOCK:
                _RESIDENT[ck] = rc
            _RESIDENT_SEEN[ck] = time.time()
            _enforce_resident_cap(keep=ck)

    # 2026-07-02: ничейные ходы (будильники агентов) — в чат сразу, не в трубу.
    if ck is not None:
        _sc = _key_chat
        # читатель резидента — чужой поток: тему беседы закрепляем явно
        rc.on_stray = lambda events, _c=_sc, _t=ckey_topic(ck): _stray_in_topic(_c, _t, events)

    # Регистрируем underlying Popen для кнопки ⏹ СТОП (handle_callback делает proc.kill()).
    proc = rc.proc
    if status_chat_id:
        with _RUNNING_PROCS_LOCK:
            _RUNNING_PROCS[ck] = proc

    t_start = time.time()
    timed_out = False
    died_mid_turn = False
    # 2026-05-18: тикер риалтайм-счётчика. update_status() сам по себе зовётся
    # только на события от claude — между ними может быть тишина 10+ сек,
    # и время "думаю…" замирает. Отдельный поток дёргает update_status каждые 1.2с.
    _ticker_stop = threading.Event()
    def _ticker():
        while not _ticker_stop.wait(6.0):
            try:
                update_status(force=True)
            except Exception:
                pass
    _ticker_thread = threading.Thread(target=carry(_ticker), daemon=True)
    _ticker_thread.start()
    try:
        for ev in rc.send_and_collect(prompt_text, turn_timeout=CLAUDE_TIMEOUT):
            if time.time() - t_start > CLAUDE_TIMEOUT:
                timed_out = True
                rc.kill()
                break
            etype = ev.get("type")
            if etype == "rate_limit_event":
                # 08.10.2026: остаток подписки для полоски в пульте (meters.py)
                meters.note_claude_limits(ev.get("rate_limit_info"))
                continue
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
                # 2026-09-23: новое сообщение модели в том же ходе (ответ после крюка
                # сохранения) прежде приклеивалось к предыдущему без разрыва, и маркер
                # вида __BG_TASK__: {...} переставал распознаваться: разборщик ждёт }
                # в конце строки, а получал "}·". Ставим разрыв между сообщениями.
                if ev_type == "message_start":
                    pending_sep[0] = True
                # content_block_start / content_block_stop / message_stop /
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
                ctx_tokens = (last_it.get("cache_creation_input_tokens") or 0) + (last_it.get("cache_read_input_tokens") or 0) + (last_it.get("input_tokens") or 0)
                cache_creation = sum((it.get("cache_creation_input_tokens") or 0) for it in its)
                cache_read = sum((it.get("cache_read_input_tokens") or 0) for it in its)
                # 08.10.2026: заполнение беседы для пульта; окно модели отдаёт сам Клод
                _win = max([int((mu or {}).get("contextWindow") or 0)
                            for mu in (ev.get("modelUsage") or {}).values()] or [0])
                meters.note_context(status_chat_id, engine_name,
                                    ctx_tokens or (u.get("input_tokens") or 0), _win)
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
                if _RUNNING_PROCS.get(ck) is proc:
                    _RUNNING_PROCS.pop(ck, None)
        # Если процесс мёртв (СТОП, timeout, краш) — выкинем из _RESIDENT,
        # следующий ход поднимет новый с --resume последнего sid.
        # 2026-08-27: тут же отметка времени для жнеца — «в этом чате был ход
        # вот когда». Мёртвого убираем и из отметок, чтобы словарь не копил
        # чаты, которых давно нет.
        if ck is None:
            # Ход без беседы (разовый вызов, напр. передача при /compact):
            # процесс одноразовый, закрываем сразу, бесхозным он не остаётся.
            threading.Thread(target=rc.close, daemon=True).start()
        else:
            if rc.is_alive():
                _RESIDENT_SEEN[ck] = time.time()
            else:
                with _RESIDENT_LOCK:
                    if _RESIDENT.get(ck) is rc:
                        _RESIDENT.pop(ck, None)
                _RESIDENT_SEEN.pop(ck, None)
        if turn_lock is not None:
            try:
                turn_lock.release()
            except RuntimeError:
                pass

    # 2026-06-17: этот ход прервали догонкой. Его error-result пустой — не шлём
    # «(пусто)» в чат, удаляем его «думаю…» (у догонки своё), отдаём sentinel.
    # pop() в условии всегда снимает флаг; подавляем ТОЛЬКО при is_error
    # (если ход успел доехать до настоящего ответа за миг до interrupt — отдаём его).
    if status_chat_id and _INTERRUPT_FLAGS.pop(ck, False) and result_is_error:
        # 2026-07-05 (по просьбе хозяина): прерванный ответ БОЛЬШЕ НЕ выбрасываем. Замораживаем
        # на месте, где прерывание настигло, и оставляем в чате обрывком.
        # Новое (догоняющее) сообщение отвечается отдельным ходом.
        partial = "".join(streamed_text)[chunk_flushed[0]:].strip()
        if partial:
            frozen = f"{partial}\n\n✂️ прервано"
            # 2026-07-06 (по просьбе хозяина): НЕ пишем frozen в черновик — send_chunked ниже постит
            # его настоящим сообщением, а двойная запись (черновик + реальное) давала
            # задвоение (стрим-черновик живёт 30с и висел рядом с постом). Стрим-черновик
            # сам гаснет, когда придёт реальное сообщение — как на обычном финале.
            log(f"  ход прерван догонкой — заморожен partial ({len(partial)} симв, chat {status_chat_id})")
            return (frozen, new_sid or session_id, 0, 0, status_msg_id[0])
        # Текста ещё не было (прервали до первых слов) — тихо подавляем, как раньше.
        log(f"  ход прерван догонкой — пустой результат подавлен (chat {status_chat_id})")
        if status_msg_id[0] is not None:
            try:
                api("deleteMessage", chat_id=status_chat_id, message_id=status_msg_id[0])
            except Exception:
                pass
        return (_INTERRUPTED_SENTINEL, new_sid or session_id, 0, 0, status_msg_id[0])

    # Если юзер нажал ⏹ СТОП — proc убит. Возвращаем то, что успело накопиться.
    if status_chat_id and _STOP_FLAGS.pop(ck, False):
        _STREAM_PARTIAL.pop(ck, None)
        # 2026-07-06 (по просьбе хозяина): финал по СТОП уже отправлен реальным сообщением из
        # handle_callback → не дублируем. Убираем панель «думаю…⏹» и отдаём sentinel.
        if _STOP_POSTED.pop(ck, False):
            if status_msg_id[0] is not None:
                try:
                    api("deleteMessage", chat_id=status_chat_id, message_id=status_msg_id[0])
                except Exception:
                    pass
            log(f"  stopped-by-user: финал отправлен из callback, панель убрана")
            return (_INTERRUPTED_SENTINEL, new_sid or session_id, 0, 0, status_msg_id[0])
        partial = "".join(streamed_text)[chunk_flushed[0]:].strip()
        if partial:
            stopped_reply = f"{partial}\n\n🛑 остановлено по кнопке."
        else:
            stopped_reply = "🛑 остановлено по кнопке (ответ ещё не начался)."
        # 2026-07-06 (по просьбе хозяина): НЕ пишем stopped_reply в черновик — send_chunked ниже
        # постит его настоящим сообщением. Двойная запись давала задвоение (см. ветку
        # прерывания-догонки выше). Стрим-черновик гаснет сам при реальном сообщении.
        log(f"  stopped-by-user: text_chars={text_chars}, tools={tool_count}")
        return (stopped_reply, new_sid or session_id, 0, 0, status_msg_id[0])

    if timed_out:
        log(f"  timeout after {CLAUDE_TIMEOUT}s, tools={tool_count}, text_chars={text_chars}")
        return (f"⏱ Ход прерван сторожем: либо шёл дольше {CLAUDE_TIMEOUT // 60} мин, либо молчал дольше 10 мин. Попробуй ещё раз или /new.", session_id, 0, 0, status_msg_id[0])

    if died_mid_turn and not reply:
        err = ("".join(stderr_buf))[:500]
        if session_id and "session" in err.lower():
            return (f"⚠️ Сессия слетела. Сбрось через /new и повтори.\n\n{err}", None, 0, 0, status_msg_id[0])
        return (f"⚠️ Ошибка claude:\n{err}", session_id, 0, 0, status_msg_id[0])

    fe = f"{first_event_at:.1f}s" if first_event_at is not None else "n/a"
    log(f"  turn-summary: first_event={fe}, tools={tool_count}, text_chars={text_chars}, ctx={used_tokens}, cache_create={cache_creation}, cache_read={cache_read}")
    # Не удаляем статусное сообщение — вернём его id, чтобы send_chunked
    # отредактировал его в финальный ответ (Fix Bug2: начало больше не пропадает).
    # 2026-06-27 Fix Bug3: при tool_use в ходе CLI-поле result содержит ТОЛЬКО текст
    # последнего assistant-блока (после инструментов). Текст ДО инструментов
    # (основной ответ) терялся. streamed_text накопил ВЕСЬ текст хода (стрим-дельты
    # или фоллбэк из assistant.text) — берём его, он полнее. Когда инструментов не
    # было, streamed_text == reply, поведение не меняется.
    full_text = "".join(streamed_text)[chunk_flushed[0]:].strip()
    # Если чанки уже вынесены реальными сообщениями, а хвост пуст — НЕ падаем в reply
    # (это был бы дубль всего ответа). Отдаём sentinel → send_chunked ничего не постит.
    if chunk_flushed[0] > 0:
        answer_out = full_text or _INTERRUPTED_SENTINEL
    else:
        answer_out = full_text or reply or "(пусто)"
    # Здесь до 18.08.2026 стоял блок сворачивания панели под нативный черновик —
    # недостижимый с 06.07 (draft_active всегда False). Похоронен вместе с черновиком.
    return (answer_out, new_sid, used_tokens, ctx_tokens, status_msg_id[0])


# ---------------------------------------------------------------------------
# Жнец резидентов (27.08.2026)
#
# Резидентный `claude` живёт в чате между ходами: холодный старт (~6с) платится
# один раз. Обратная сторона — процесс не умирает никогда. Сессия сама не
# истекает, а память по ходу растёт (у Anthropic это прямо записано в известных
# ограничениях Agent SDK: «Memory growth over long sessions — cap session length
# or recycle subprocesses periodically»). Шесть чатов, каждый по разу за день, —
# и к вечеру шесть процессов держат память просто так.
#
# Жнец раз в минуту обходит живых резидентов и закрывает простоявших дольше
# порога (профиль, resident_idle_sec). Следующее сообщение поднимет процесс
# заново с --resume: sid лежит в sessions.json, история цела. Отсчёт идёт от
# последнего ХОДА, а не от рождения процесса, — пока разговор в темпе, резидент
# остаётся прогретым.

RESIDENT_REAP_PERIOD_SEC = 60


def _reap_idle_residents(idle_sec, now=None):
    """Один проход жнеца. Возвращает список выселенных chat_id.

    Чат, в котором прямо сейчас идёт ход, держит свой замок — такой пропускаем
    и вернёмся через минуту. Порядок захвата (сначала замок хода, потом замок
    словаря) тот же, что в call_claude: обратный порядок рано или поздно сводит
    два потока лбами насмерть.
    """
    now = time.time() if now is None else now
    reaped = []
    with _RESIDENT_LOCK:
        chat_ids = list(_RESIDENT)
    for chat_id in chat_ids:
        seen = _RESIDENT_SEEN.get(chat_id)
        if seen is None:
            # Резидент есть, отметки нет: либо ход ещё идёт (штамп ставится в его
            # finally), либо бот только поднялся. Заводим отсчёт с этой минуты,
            # а не рубим вслепую.
            _RESIDENT_SEEN[chat_id] = now
            continue
        if now - seen < idle_sec:
            continue
        lock = _get_turn_lock(chat_id)
        if not lock.acquire(blocking=False):
            continue
        try:
            with _RESIDENT_LOCK:
                rc = _RESIDENT.get(chat_id)
                if rc is not None:
                    _RESIDENT.pop(chat_id, None)
            _RESIDENT_SEEN.pop(chat_id, None)
            if rc is None:
                continue
            try:
                rc.close()
            except Exception as e:
                log(f"жнец: закрыть резидента чата {chat_id} не вышло: {e}")
            reaped.append(chat_id)
            log(f"жнец: резидент чата {chat_id} закрыт после {int(now - seen)}с простоя")
        finally:
            lock.release()
    return reaped


# ---------------------------------------------------------------------------
# Потолок живых резидентов (08.10.2026, темы). С темами живых бесед у бота может
# быть сколько угодно, а каждая держит свой `claude` на 270–350 МБ. Потолок из
# профиля (max_residents, по умолчанию 4): новый резидент сверх него выселяет
# самого давно молчащего. Занятых (идёт ход) не трогаем. Беседа не теряется:
# sid лежит в sessions.json, следующий ход в той теме поднимет --resume.

def _max_residents():
    try:
        return int(PROFILE.get("max_residents", 4) or 0)
    except (TypeError, ValueError):
        return 4


def _enforce_resident_cap(keep=None, cap=None):
    """Выселить лишних живых резидентов по давности. Возвращает список ключей."""
    cap = _max_residents() if cap is None else cap
    if cap <= 0:
        return []
    with _RESIDENT_LOCK:
        alive = [k for k, rc in _RESIDENT.items() if rc.is_alive()]
    excess = len(alive) - cap
    if excess <= 0:
        return []
    order = sorted((k for k in alive if k != keep), key=lambda k: _RESIDENT_SEEN.get(k, 0))
    evicted = []
    for k in order:
        if len(evicted) >= excess:
            break
        lock = _get_turn_lock(k)
        if not lock.acquire(blocking=False):
            continue            # там идёт ход — не трогаем
        try:
            with _RESIDENT_LOCK:
                rc = _RESIDENT.pop(k, None)
            _RESIDENT_SEEN.pop(k, None)
        finally:
            lock.release()
        if rc is None:
            continue
        threading.Thread(target=rc.close, daemon=True).start()
        evicted.append(k)
        log(f"потолок резидентов ({cap}): закрыт резидент беседы {k}, следующий ход поднимет его с --resume")
    return evicted


def start_resident_reaper(idle_sec=None, period_sec=RESIDENT_REAP_PERIOD_SEC):
    """Поднимает фоновый поток жнеца. Порог ≤ 0 — жнеца нет вовсе."""
    if idle_sec is None:
        try:
            idle_sec = int(PROFILE.get("resident_idle_sec", 600) or 0)
        except (TypeError, ValueError):
            idle_sec = 600
    if idle_sec <= 0:
        log("жнец резидентов выключен (resident_idle_sec ≤ 0)")
        return None

    def _loop():
        while True:
            time.sleep(period_sec)
            try:
                _reap_idle_residents(idle_sec)
            except Exception as e:
                log(f"жнец: проход упал: {e}")

    t = threading.Thread(target=_loop, daemon=True, name="resident-reaper")
    t.start()
    log(f"жнец резидентов: порог {idle_sec}с, обход раз в {period_sec}с")
    return t
