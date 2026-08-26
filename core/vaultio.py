#!/usr/bin/env python3
"""Диск: хранилище заметок, git, входящие, задачи, манифест, сессии.

Всё, что бот кладёт на диск и берёт с диска. Две группы.

Первая — хранилище заметок хозяина: страж записи (не дать записать мимо
хранилища), отпечаток состояния git, синхронизация, дописывание во входящие
и в список задач, манифест сохранённых вложений. Синхронизация обвешана
защитами по следам прошлых сбоев — каждая ветка там это чей-то ночной инцидент.

Вторая — служебное состояние бота на диске: кому разрешено писать боту, на
чём остановился опрос обновлений, какая у кого сессия. Оно живёт рядом,
потому что отметка сессии хранит отпечаток хранилища: правила поменялись —
живой сессии доносится разница, а не перезапуск.
"""
import json
import os
import socket
import subprocess
import time
from datetime import datetime
from pathlib import Path

from botctx import (
    ALLOW_FILE, DO_QUEUE, INBOX, MANIFEST_DIR, MSK, OFFSET_FILE, PROFILE,
    SESSIONS_FILE, TASKS_FILE, VAULT, log,
)


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
    """Return (session_id, invalidation_reason, saved_head).
    reason ∈ {None, 'vault_changed'}. На vault_changed sid НЕ обнуляем —
    сессию держим, правила доносим diff'ом (см. process_user_text)."""
    s = load_sessions()
    rec = s.get(str(uid))
    if not rec:
        return None, None, None
    saved_head = rec.get("vault_head")
    current_head = vault_head()
    if saved_head and current_head and saved_head != current_head:
        return rec.get("session_id"), "vault_changed", saved_head
    return rec.get("session_id"), None, None


def significant_diff(old_head, new_head):
    """Текст git-diff по файлам-правилам между old_head и new_head. Для доношения
    изменившихся правил в живую сессию без перезапуска. Обрезается по размеру."""
    if not old_head or not new_head:
        return ""
    try:
        r = subprocess.run(
            ["git", "-C", str(VAULT), "diff", f"{old_head}..{new_head}", "--"] + SIGNIFICANT_PATHS,
            capture_output=True, timeout=10,
            env={**os.environ, "HOME": PROFILE["home"]},
        )
        if r.returncode != 0:
            return ""
        d = r.stdout.decode("utf-8", "ignore").strip()
        return d[:6000] if d else ""
    except Exception as e:
        log(f"significant_diff error: {e}")
        return ""


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


COMPACT_FIRST_AT = 15  # first compact reminder at this msg count
COMPACT_REPEAT = 5     # then every N messages

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


def _persona_dir():
    """Папка с файлом личности — в относительном виде, с косой чертой на конце.
    У каждого хозяина она своя (поле profile.persona_path), раньше хозяйская
    лежала здесь литералом. Файл вне хранилища — папки в списке просто не будет."""
    try:
        rel = Path(PROFILE["persona_path"]).parent.relative_to(VAULT)
    except ValueError:
        return None
    return f"{rel}/" if str(rel) != "." else None


# Файлы, правка которых меняет ПОВЕДЕНИЕ бота (а не копит данные). Список
# общий для всех, личное в нём одно — папка личности, она приходит из профиля.
SIGNIFICANT_PATHS = [p for p in [
    "CLAUDE.md",
    _persona_dir(),
    "Resources/_templates/",
    ".claude/commands/",
    ".claude/agents/",
    ".claude/skills/",
] if p]


def vault_head():
    """SHA of last commit that touched files which actually change Claude's behavior
    (system prompt, principles, skill templates). Captures in inbox.md / Tasks/ / log.md /
    status.md / Journal/ DON'T trigger session reset — they're append-only data, not behavior changes."""
    try:
        r = subprocess.run(
            ["git", "-C", str(VAULT), "log", "-1", "--format=%H", "--"] + SIGNIFICANT_PATHS,
            capture_output=True, timeout=5,
            env={**os.environ, "HOME": PROFILE["home"]},
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
    # выключатель профиля: у бота без vault.git синхронизировать нечего, и каждый
    # ход в журнал сыпалось «not a git repository» (поймано канарейкой 19.08)
    if not PROFILE.feature("git_sync"):
        return
    try:
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "HOME": PROFILE["home"]}
        # куда синхронизируемся — из профиля, не «origin/main» намертво
        remote, branch = PROFILE["git_remote"], PROFILE["git_branch"]
        upstream = f"{remote}/{branch}"
        # git_sync guard: stuck merge — недорешённое конфликтное слияние откатываем,
        # а не коммитим add -A (инцидент 26.07: маркеры <<<<<<< запеклись в status.md)
        if (VAULT / ".git" / "MERGE_HEAD").exists():
            u = subprocess.run(["git", "-C", str(VAULT), "ls-files", "-u"],
                               capture_output=True, env=env, timeout=10)
            if u.stdout.strip():
                subprocess.run(["git", "-C", str(VAULT), "merge", "--abort"],
                               check=False, capture_output=True, env=env, timeout=10)
                log("git_sync: aborted stuck conflicted merge")
        # 1. Stage + commit (no-op if hooks already committed).
        subprocess.run(
            ["git", "-C", str(VAULT), "add", "-A"],
            check=False, capture_output=True, env=env, timeout=15,
        )
        r = subprocess.run(
            ["git", "-C", str(VAULT), "commit", "-m", f"tg: {summary}"],
            capture_output=True, env=env, timeout=15,
        )
        # Tolerate "nothing to commit" — fall through to sync/push.
        if r.returncode != 0 and b"nothing to commit" not in r.stdout:
            log(f"git commit: {r.stdout.decode()[:200]}")
        # 2. Symmetric sync: fetch + ff-only, fall back to merge --no-edit.
        subprocess.run(
            ["git", "-C", str(VAULT), "fetch", remote, branch],
            check=False, capture_output=True, env=env, timeout=20,
        )
        ff = subprocess.run(
            ["git", "-C", str(VAULT), "merge", "--ff-only", upstream],
            capture_output=True, env=env, timeout=15,
        )
        if ff.returncode != 0:
            mr = subprocess.run(
                ["git", "-C", str(VAULT), "merge", "--no-edit",
                 "-m", f"auto-merge tg-bot ({summary})", upstream],
                capture_output=True, env=env, timeout=20,
            )
            if mr.returncode != 0:
                subprocess.run(["git", "-C", str(VAULT), "merge", "--abort"],
                               check=False, capture_output=True, env=env, timeout=10)
                log(f"git_sync merge failed: {mr.stderr.decode()[:200]}")
                return
        # 3. Push only if ahead.
        ahead_proc = subprocess.run(
            ["git", "-C", str(VAULT), "rev-list", "--count", f"{upstream}..HEAD"],
            capture_output=True, env=env, timeout=10,
        )
        try:
            ahead = int(ahead_proc.stdout.decode().strip() or "0")
        except ValueError:
            ahead = 0
        if ahead > 0:
            p = subprocess.run(
                ["git", "-C", str(VAULT), "push", remote, branch],
                capture_output=True, env=env, timeout=30,
            )
            if p.returncode != 0:
                log(f"git push failed: {p.stderr.decode()[:200]}")
    except Exception as e:
        log(f"git_sync error: {e}")

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


def append_chatlog():
    """Дописать разговор этого хода в лог хранилища.

    Зовётся ДО синхронизации git и синхронно (не отдельным процессом в фоне):
    иначе свежий файл не успеет попасть в тот же коммит. В безголовом режиме
    модели крюк завершения не вызывается, поэтому пишем из кода бота."""
    if not PROFILE.feature("chatlog"):
        return
    try:
        subprocess.run(["/usr/bin/python3", PROFILE["chatlog_script"]],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    except Exception as e:
        log(f"  chatlog append err: {e}")


def rag_reindex():
    """Досборка поискового индекса сразу после синхронизации.

    К этому моменту на диске уже и свои правки, и подтянутое с ноута — поиск
    будет мгновенным, а не станет ждать пересборку пачкой."""
    if not PROFILE.feature("rag"):
        return
    try:
        rs = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        rs.settimeout(120)
        rs.connect(PROFILE["rag_sock"])
        rs.sendall(json.dumps({"cmd": "reindex"}).encode())
        rs.recv(4096)
        rs.close()
    except Exception as e:
        log(f"  reindex ping err: {e}")
