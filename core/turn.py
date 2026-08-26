#!/usr/bin/env python3
"""Ход разговора: от готового текста человека до отправленного ответа.

Один проход сверху вниз, порядок важен и держится годами инцидентов:
подтянуть свежее хранилище → донести изменившиеся правила в живую сессию →
позвать модель → разобрать маркеры её ответа → отправить текст, картинки и
документы → дописать задачи и манифест → синхронизировать хранилище →
дособрать поисковый индекс.

Здесь же два хода, которые начал не человек: фоновая задача со сторожем
(модель попросила сделать долгую работу отдельно) и «ничейный ход» — резидент
проснулся сам, например по напоминалке.
"""
import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from botctx import (
    MSK, PENDING_CAL_TTL_SEC, PENDING_SEND_TTL_SEC, PROFILE,
    VAULT, log,
    _INTERRUPTED_SENTINEL, _RESIDENT, _RESIDENT_LOCK, _STOP_FLAGS, _STOP_POSTED,
)
import botext
from claude_run import call_claude
from markers import (
    auto_dig, clear_pending_cal, clear_pending_send, detect_choices,
    get_pending_cal, get_pending_send, needs_confirm, parse_affirm_marker,
    parse_bg_task_marker, parse_cal_cancel_marker, parse_cal_event_marker,
    parse_cal_propose_marker, parse_choices_marker, parse_dig_marker,
    parse_task_markers, parse_tg_send_cancel_marker, parse_tg_send_confirm_marker,
    parse_tg_send_propose_marker, parse_tts_marker, parse_wrote_markers,
    set_pending_cal, set_pending_send,
)
from media import (
    extract_and_send_documents, extract_and_send_images,
    process_attachment_markers,
)
from menu import _nav_state_line
from tgapi import (
    TypingPulser, api, send_chunked, strip_html, tg_resolve_peer, tg_send_now,
    _oneshot,
)
from vaultio import (
    append_chatlog, append_tasks_from_markers, append_to_inbox_raw, get_msg_count,
    get_session, get_total_tokens, git_sync, rag_reindex,
    set_session, significant_diff, update_vault_head, vault_head,
    write_manifest_entry, COMPACT_FIRST_AT, COMPACT_REPEAT,
)
from voice import send_voice_reply


def _dispatch_stray_turn(uid, chat_id, events):
    """2026-07-02: ход, начатый самим harness'ом (будильник фонового агента),
    раньше копился в трубе и вываливался при следующем сообщении хозяина.
    Теперь читатель резидента отдаёт его сюда СРАЗУ по завершении — шлём в чат
    немедленно, тем же трактом: картинки, __BG_TASK__, текст."""
    text = ""
    for ev in reversed(events):
        if ev.get("type") == "result":
            text = ev.get("result") or ""
            break
    if not (text or "").strip():
        return
    log(f"stray-turn flush → chat={chat_id}, {len(text)} chars")
    try:
        cleaned, bg_spec = parse_bg_task_marker(text)
        cleaned = extract_and_send_images(chat_id, cleaned)
        # кнопочные/служебные маркеры вне обычного тракта не сработают — глушим строки
        _svc = ("__WROTE__", "__TASK__", "__CHOICES__", "__AFFIRM__", "__DIG__", "__TTS__")
        cleaned = "\n".join(
            l for l in (cleaned or "").splitlines()
            if not l.strip().startswith(_svc)
        ).strip()
        if cleaned:
            send_chunked(chat_id, cleaned)
        if bg_spec:
            log(f"  __BG_TASK__ принят из stray-хода: {bg_spec['label']!r}")
            threading.Thread(target=_run_bg_task, args=(uid, chat_id, bg_spec), daemon=True).start()
    except Exception as e:
        log(f"stray-turn dispatch err: {e}")


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
            cmd, shell=True, executable="/bin/bash", cwd=str(VAULT),
            env={**os.environ, "HOME": PROFILE["home"]},
            capture_output=True, text=True, timeout=timeout,
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
        process_user_text(uid, chat_id, synthetic, source="bg",
                           summary_text=f"bg: {label}", _bg=True)
    except Exception as e:
        log(f"bg-task follow-up turn error: {e}")
        try:
            api("sendMessage", chat_id=chat_id,
                text=f"⚠️ Фоновая задача «{label}» закрылась, но я не смог оформить ответ: {e}")
        except Exception:
            pass


def process_user_text(uid, chat_id, full, *, msg_id=None, source="tg", summary_text=None, _bg=False):
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
        api("setMessageReaction", chat_id=chat_id, message_id=msg_id,
            reaction=json.dumps([{"type": "emoji", "emoji": "👀"}]))


    pending_cal = get_pending_cal(uid)
    full_with_pending = full
    if pending_cal:
        full_with_pending = (
            f"[PENDING_CAL_EVENT] {json.dumps(pending_cal, ensure_ascii=False)}\n\n"
            + full
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

    sid, invalidation, _saved_head = get_session(session_key)
    if invalidation == "vault_changed":
        _diff = significant_diff(_saved_head, vault_head())
        log(f"rules changed for uid={uid}: diff={len(_diff)} chars, session kept (no restart)")
        if _diff:
            api("sendMessage", chat_id=chat_id,
                text="🔄 Правила обновились — донёс изменения без перезапуска сессии.")
            full_with_pending = (
                "[СИСТЕМА: твои файлы-правила (CLAUDE.md / Self/ / навыки / команды) изменились "
                "с прошлого хода. Сессию НЕ перезапускаем — вот точный diff. Применяй новую версию "
                "с этого момента; где старый текст из начала сессии противоречит diff'у — он устарел]:\n\n"
                "```diff\n" + _diff + "\n```\n\n— — —\n" + full_with_pending
            )
    prompt_for_claude = full_with_pending

    # забрать свежее с общего репозитория ПЕРЕД ответом — увидеть правки с Mac/другого инстанса
    # (push остаётся в git_sync в конце хода; здесь только pull, без коммита/пуша)
    try:
        _penv = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "HOME": PROFILE["home"]}
        # ветка и склад — из профиля: у иного бота ветка master и внешний склад
        _rem, _br = PROFILE["git_remote"], PROFILE["git_branch"]
        _up = f"{_rem}/{_br}"
        # застрявшее конфликтное слияние: в git_sync такая проверка есть, здесь
        # её не было — и подтягивание лезло сливать поверх недорешённого
        # конфликта (найдено на разборе 26.08.2026).
        if (VAULT / ".git" / "MERGE_HEAD").exists():
            _u = subprocess.run(["git", "-C", str(VAULT), "ls-files", "-u"],
                                capture_output=True, env=_penv, timeout=10)
            if _u.stdout.strip():
                subprocess.run(["git", "-C", str(VAULT), "merge", "--abort"],
                               check=False, capture_output=True, env=_penv, timeout=10)
                log("  pre-turn pull: откатил застрявшее слияние")
        # спасательный коммит: бот мог умереть посреди прошлого хода, и
        # несохранённое висит в рабочей папке. Слияние такое затопчет, поэтому
        # сначала фиксируем как есть — без отправки в склад.
        _dirty = subprocess.run(["git", "-C", str(VAULT), "status", "--porcelain"],
                                capture_output=True, env=_penv, timeout=15)
        if _dirty.stdout.strip():
            subprocess.run(["git", "-C", str(VAULT), "add", "-A"],
                           check=False, capture_output=True, env=_penv, timeout=15)
            _rc = subprocess.run(
                ["git", "-C", str(VAULT), "commit", "-m",
                 "rescue: несохранённое перед ходом "
                 + datetime.now(MSK).strftime("%Y-%m-%d %H:%M")],
                capture_output=True, env=_penv, timeout=15)
            if _rc.returncode == 0:
                log("  pre-turn pull: спасательный коммит")
        subprocess.run(["git", "-C", str(VAULT), "fetch", _rem, _br],
                       check=False, capture_output=True, env=_penv, timeout=20)
        _ff = subprocess.run(["git", "-C", str(VAULT), "merge", "--ff-only", _up],
                             capture_output=True, env=_penv, timeout=15)
        if _ff.returncode != 0:
            _mr = subprocess.run(["git", "-C", str(VAULT), "merge", "--no-edit",
                                  "-m", "auto-merge pre-turn pull", _up],
                                 capture_output=True, env=_penv, timeout=20)
            if _mr.returncode != 0:
                subprocess.run(["git", "-C", str(VAULT), "merge", "--abort"],
                               check=False, capture_output=True, env=_penv, timeout=10)
    except Exception as _pe:
        log(f"  pre-turn pull err: {_pe}")

    # розетка хозяина: его обработчики из <хранилище>/.claude/bot-ext могут
    # дописать в промпт своё. Упало расширение — берём промпт как был.
    _ectx = {"uid": uid, "chat_id": chat_id, "source": source, "profile": PROFILE,
             "state_dir": str(Path(PROFILE["cache_dir"]) / "ext")}
    prompt_for_claude = botext.apply_point("on_prompt", prompt_for_claude, _ectx, log)

    log(f"→ claude (uid={uid}, src={source}, session={sid or 'new'}, {len(full)} chars)")

    t0 = time.time()
    with TypingPulser(chat_id):
        # In group chats placeholder leaks to other bots before deletion → duplicate replies. Suppress.
        _status_chat = chat_id if not (isinstance(chat_id, int) and chat_id < 0) else None
        reply, new_sid, used_tokens, ctx_tokens, _status_mid = call_claude(prompt_for_claude, sid, status_chat_id=_status_chat, bg_wait=_bg)
    dt = time.time() - t0
    # 2026-06-17: ход был прерван догонкой — его пустой ответ подавлен в call_claude.
    # Ничего не постим, сессию/коммит не трогаем (догонка делает свой полный цикл).
    if reply == _INTERRUPTED_SENTINEL:
        log(f"← claude ({dt:.1f}s) прерван догонкой (uid={uid}) — подавлено")
        return ""
    log(f"← claude ({dt:.1f}s, {len(reply)} chars, session={new_sid}, ctx={used_tokens})")
    reply = botext.apply_point("on_reply", reply, _ectx, log)

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
        log(f"  __CAL_PROPOSE__ stored for uid={uid}: {cal_propose.get('title','?')} @ {cal_propose.get('start','?')}")
        wrote_status_lines.append(f"⏳ календарь: ждёт подтверждения ({PENDING_CAL_TTL_SEC // 60}мин)")
    if cal_event:
        clear_pending_cal(uid)
        link = cal_event.get("link") or ""
        title = cal_event.get("title", "событие")
        start = cal_event.get("start", "")
        log(f"  __CAL_EVENT__ created for uid={uid}: id={cal_event.get('id','?')} {title} @ {start}")
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
                entry = {"to": to_q, "text": text,
                         "peer_id": res.get("peer_id"),
                         "display": res.get("display") or to_q,
                         "username": res.get("username")}
                set_pending_send(uid, entry)
                uname = f" (@{entry['username']})" if entry.get("username") else ""
                log(f"  __TG_SEND_PROPOSE__ stored uid={uid}: → {entry['display']} [{entry['peer_id']}]")
                wrote_status_lines.append(
                    f"⏳ отправить → {entry['display']}{uname}: «{text[:60]}»? "
                    f"скажи «да» ({PENDING_SEND_TTL_SEC // 60}мин)")
            elif res.get("error") == "ambiguous":
                cands = res.get("candidates", [])
                lines = "\n".join(
                    f"  • {c.get('display','?')}" + (f" (@{c['username']})" if c.get("username") else "")
                    for c in cands)
                wrote_status_lines.append(
                    f"⚠️ «{to_q}» — нашёл несколько, уточни кого:\n{lines}")
            elif res.get("error") == "not_found":
                wrote_status_lines.append(f"⚠️ «{to_q}» — не нашёл в твоих чатах. Дай @ник или номер.")
            else:
                wrote_status_lines.append(f"⚠️ отправка: ошибка поиска ({res.get('error','?')})")
    if tg_confirm:
        # Подтверждаем ТОЛЬКО если pending существовал ДО этого turn'а
        # (snapshot pending_send снят перед вызовом claude) — иначе claude мог бы
        # в одном ходе и предложить, и подтвердить, обойдя хозяина.
        if pending_send:
            # fix 01.08: слать по @username, если он известен. Числовой peer_id
            # для НЕЗНАКОМОГО собеседника (нет общего диалога) Telethon отвергает —
            # "Could not find the input entity for PeerUser(...)", т.к. в кэше нет
            # access_hash. По username клиент добирает его сам. Номер — запасной путь.
            _uname = (pending_send.get("username") or "").lstrip("@").strip()
            _target = _uname or pending_send.get("peer_id")
            res = tg_send_now(_target, pending_send.get("text", ""))
            if not res.get("ok") and _uname:
                # username не сработал — пробуем прежним путём, по номеру
                res = tg_send_now(pending_send.get("peer_id"), pending_send.get("text", ""))
            clear_pending_send(uid)
            if res.get("ok"):
                log(f"  TG SENT uid={uid} → {res.get('display','?')} [{pending_send.get('peer_id')}]")
                wrote_status_lines.append(f"📤 отправлено → {res.get('display') or pending_send.get('display','?')}")
            else:
                log(f"  TG SEND FAIL uid={uid}: {res.get('error')}")
                wrote_status_lines.append(f"⚠️ не отправилось: {res.get('error','?')}")
        else:
            log(f"  __TG_SEND_CONFIRM__ без pending (uid={uid}) — игнор")
            wrote_status_lines.append("⚠️ нечего отправлять — предложения в очереди нет")
    if tg_cancel:
        clear_pending_send(uid)
        log(f"  __TG_SEND_CANCEL__ for uid={uid}")
        wrote_status_lines.append("❌ отправка отменена")

    if wrote_entries or task_texts:
        write_manifest_entry({
            "ts": datetime.now(MSK).isoformat(),
            "uid": uid,
            "chat_id": chat_id,
            "incoming": (full or "")[:120],
            "wrote": [{"count": c, "path": p} for c, p in wrote_entries],
            "tasks_added": tasks_appended,
            "task_summaries": task_texts[:5],
            "session": new_sid,
            "src": source,
        })

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
        api("sendMessage", chat_id=chat_id,
            text=f"⚠️ {count} сообщений в сессии — контекст растёт. /compact чтобы сжать или /new чтобы начать заново.")
    session_total = get_total_tokens(session_key)

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
                return f"{n/1_000_000:.2f}M"
            if n >= 1_000:
                return f"{n/1_000:.1f}k"
            return str(n)
        rtk_str = ""
        try:
            import re as _re
            _jr = subprocess.run(
                ["journalctl", "--user", "-u", PROFILE["router_unit"], "-n", "100", "--no-pager"],
                capture_output=True, text=True, timeout=2,
                env={**os.environ, "XDG_RUNTIME_DIR": PROFILE["xdg_runtime_dir"]},
            )
            _m = _re.findall(r'\[RTK\] saved \d+B / \d+B \((\d+(?:\.\d+)?)%\)', _jr.stdout)
            if _m:
                rtk_str = f" ({float(_m[-1]):.0f}%)"
        except Exception:
            pass
        header = f"ctx {_fmt(ctx_tokens)}{rtk_str}"
        reply = f"({header})\n\n{reply}"

    if not skip_send:
        _choices = choices_labels
        # дефолтная лупа, если модель не поставила ни одной своей кнопки
        want_dig = want_dig or auto_dig(reply, _choices, want_affirm, want_dig)
        if voice_reply:
            ok = send_voice_reply(chat_id, reply, voice=voice_name, engine=voice_engine)
            if not ok:
                send_chunked(chat_id, "⚠️ Голос не синтезировался, отдаю текстом:\n\n" + reply,
                             confirm=want_affirm or needs_confirm(reply), choices=_choices, dig=want_dig)
        else:
            send_chunked(chat_id, reply, confirm=want_affirm or needs_confirm(reply), choices=_choices, dig=want_dig,
                         edit_first_msg_id=_status_mid)

    # 2026-06-19: модель попросила фоновую задачу — поднимаем поток-сторож. Он
    # дождётся конца команды и заведёт отдельный ход с результатом (Design B).
    if bg_spec:
        log(f"  __BG_TASK__ принят (uid={uid}): {bg_spec['label']!r} timeout={bg_spec['timeout']}s")
        threading.Thread(
            target=_run_bg_task, args=(uid, chat_id, bg_spec), daemon=True
        ).start()

    s_summary = ((summary_text if summary_text is not None else full)[:50] or "msg").replace("\n", " ")

    # автозапись лога разговора ДО git_sync — чтобы лог текущего сообщения уехал тем же ходом
    # (синхронно, не Popen: иначе git_sync ниже не подхватит свежий файл)
    # (Stop-крюк в headless claude -p не вызывается, поэтому делаем тут, в коде бота)
    append_chatlog()

    git_sync(f"{datetime.now(MSK).strftime('%Y-%m-%d %H:%M')} {s_summary}")

    # инкрементальная досборка индекса СЕЙЧАС (после git_sync — локальные правки + подтянутое
    # с Mac уже на диске) — чтобы смысловой поиск был мгновенным, а не ждал пересборку пачкой
    rag_reindex()

    if new_sid:
        update_vault_head(session_key)

    return reply

def do_compact(uid, chat_id):
    """Summarise current session via Claude, start fresh session with that summary as context."""
    sid, _, _ = get_session(uid)
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
        # 2026-07-01 fix: конспект пишет ЖИВОЙ резидент (status_chat_id=chat_id),
        # иначе поднимался второй --resume процесс на тот же sid → коллизия с
        # резидентом → пустой конспект (turn-summary text_chars=0). Резидент убьём
        # ниже перед handoff — как и было.
        summary, _, _, _, _ = call_claude(compact_prompt, sid, status_chat_id=chat_id)

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
        try: rc_kill.kill()
        except Exception as e: log(f"  /compact: resident kill err: {e}")
    with TypingPulser(chat_id):
        _, new_sid, _, _, _ = call_claude(handoff, None)

    if not new_sid:
        api("sendMessage", chat_id=chat_id, text="⚠️ Не удалось начать новую сессию после компакта.")
        return

    set_session(uid, new_sid, msg_count=1)
    update_vault_head(uid)
    api("sendMessage", chat_id=chat_id,
        text=f"✅ Контекст сжат, новая сессия начата.\n\nКонспект:\n{summary}")
