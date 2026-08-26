#!/usr/bin/env python3
"""Меню бота и хождение по хранилищу заметок кнопками.

Два разных меню, оба здесь. Первое — список команд, который Telegram
показывает по слэшу; он собирается из встроенных команд плюс скиллы,
найденные в хранилище хозяина. Второе — кнопочная навигация: корень,
категории, проекты, файлы внутри проекта, чтение файла с чисткой служебных
разделов.

Состав меню целиком приходит из профиля. У нового хозяина список пуст —
и это рабочее состояние, а не поломка: корень показывает, что проектов пока
нет, и не падает.
"""
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

from botctx import (
    API, PROFILE, VAULT, log, _MENU_ROW, _brief_active_msg, _brief_level,
)
from tgapi import api, md_to_html, send_chunked, _strip_active_keyboard

# Кнопка «📅 daily» в меню запускает обычный ход разговора. Сам ход живёт в
# turn.py, а turn.py уже зависит от меню (ему нужна строка [NAV_STATE]) —
# импортировать друг друга насмерть нельзя. Поэтому вход не импортируется, а
# ставится снаружи одной строкой при старте: явная стрелка вместо кольца.
_run_turn = None


def set_turn_runner(fn):
    """Кто выполняет ход разговора. Ставит tg-bot.py при сборке."""
    global _run_turn
    _run_turn = fn


# TG menu commands. Internal commands always shown; skill commands discovered
# dynamically from <VAULT>/.claude/commands/*.md.
# Names must match [a-z][a-z0-9_]{0,31}; skills with hyphens are registered with
# underscores and rewritten back before forwarding to Claude.
_BOT_INTERNAL_COMMANDS = [
    ("menu", "🏠 Меню: проекты / задачи / идеи / inbox"),
    ("new", "Новая сессия (сбросить контекст)"),
    ("compact", "Сжать текущую сессию"),
]


def _discover_skill_commands():
    """Read VAULT/.claude/commands/*.md, return ([(cmd,desc),...], aliases_dict).
    If VAULT/.claude/tg-menu.txt exists, filter to commands listed there
    (one per line; # comments and blanks ignored)."""
    cmds = []
    aliases = {}
    cmd_dir = VAULT / ".claude" / "commands"
    if not cmd_dir.is_dir():
        return cmds, aliases
    allow_file = VAULT / ".claude" / "tg-menu.txt"
    allow = None
    if allow_file.is_file():
        allow = set()
        for line in allow_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                allow.add(line.lstrip("/").replace("-", "_"))
    name_re = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
    for f in sorted(cmd_dir.glob("*.md")):
        name = f.stem
        tg_name = name.replace("-", "_")
        if not name_re.match(tg_name):
            continue
        if allow is not None and tg_name not in allow:
            continue
        desc = ""
        try:
            text = f.read_text(encoding="utf-8")
            m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
            if m:
                for line in m.group(1).splitlines():
                    if line.lower().startswith("description:"):
                        desc = line.split(":", 1)[1].strip().strip("\"'")
                        break
                body = text[m.end():]
            else:
                body = text
            if not desc:
                for line in body.splitlines():
                    s = line.strip()
                    if s and not s.startswith("#") and not s.startswith(">"):
                        desc = s
                        break
        except Exception as e:
            log(f"discover cmd {f}: {e}")
        if not desc:
            desc = f"/{name}"
        cmds.append((tg_name, desc[:256]))
        if tg_name != name:
            aliases[f"/{tg_name}"] = f"/{name}"
    return cmds, aliases


_skill_cmds, SKILL_ALIASES = _discover_skill_commands()
BOT_MENU_COMMANDS = _BOT_INTERNAL_COMMANDS + _skill_cmds


def _rewrite_skill_alias(text: str) -> str:
    """Translate /underscored aliases back to /hyphenated skill names."""
    if not text:
        return text
    head, sep, tail = text.partition(" ")
    head_no_at = head.split("@", 1)[0]  # strip /cmd@botname suffix
    canonical = SKILL_ALIASES.get(head_no_at)
    if canonical:
        return canonical + sep + tail
    return text

# slug → (display_name_in_status, relative_dir). Order = order in the projects list.
# Шаг 4: меню целиком приходит из профиля (у нового хозяина оно пустое —
# рендер обязан это пережить, см. тест T7).
_BRIEF_PROJECTS = PROFILE["menu_projects"]
_BRIEF_PROJ_BY_SLUG = {slug: (name, d) for slug, name, d in _BRIEF_PROJECTS}

# Categories shown at top of "projects" menu. Slug → (display, [project_slugs]).
# Projects NOT listed in any category render as flat top-level entries.
# multi is intentionally hidden at top level — appears only inside course's menu.
_BRIEF_CATEGORIES = PROFILE["menu_categories"]
_BRIEF_CAT_BY_SLUG = {slug: (name, kids) for slug, name, kids in _BRIEF_CATEGORIES}
_HIDDEN_AT_TOP = PROFILE["menu_hidden_at_top"]
# Subprojects shown inside a parent project's menu. parent_slug → [child_slugs].
_BRIEF_SUBPROJECTS = PROFILE["menu_subprojects"]


def _parse_status_dashboard():
    """Read vault/status.md and return {display_name: (mod, state)}."""
    try:
        text = open(os.path.join(VAULT, "status.md"), encoding="utf-8").read()
    except OSError:
        return {}
    out = {}
    in_table = False
    for line in text.splitlines():
        if line.startswith("| Мод"):
            in_table = True
            continue
        if in_table:
            if not line.startswith("|"):
                break
            if line.startswith("| ---"):
                continue
            cells = [c.strip() for c in line.split("|")[1:-1]]
            if len(cells) < 3:
                continue
            mod = cells[0]
            name = re.sub(r"\*\*", "", cells[1]).strip()
            name = re.sub(r"\*([^*]+)\*", r"\1", name).strip()
            name = re.sub(r"\s*\([^)]+\)\s*$", "", name).strip()
            state = cells[2]
            out[name] = (mod, state)
    return out


def _shorten(s, limit=140):
    s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s or "")
    s = re.sub(r"`([^`]+)`", r"\1", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) <= limit:
        return s
    cut = s[:limit - 1]
    sp = cut.rfind(" ")
    if sp > limit * 0.6:
        cut = cut[:sp]
    return cut + "…"


def _kb_root():
    # у нового хозяина проектов в профиле нет — кнопку «проекты» не показываем,
    # иначе она ведёт в пустоту (канарейка 19.08)
    rows = []
    if PROFILE["menu_projects"]:
        rows.append([{"text": "📂 проекты", "callback_data": "nav:projects"}])
    return {"inline_keyboard": rows + [
        [{"text": "📋 задачи",  "callback_data": "f:gen_tasks"},
         {"text": "💡 идеи",    "callback_data": "f:gen_ideas"}],
        [{"text": "📥 inbox",   "callback_data": "f:inbox"},
         {"text": "📅 daily",   "callback_data": "f:daily"}],
    ]}


def _text_root():
    if not PROFILE["menu_projects"]:
        return ("*Главное меню*\n\nПроектов пока нет — их список задаётся полем "
                "`menu_projects` в профиле. Пока доступны задачи, идеи, inbox и дневник.")
    return "*Главное меню моей жизни*\n\nкуда копаем?"


_MOD_RANK = {"🟢": 0, "🔴": 1, "🔵": 2, "🟡": 3, "🔔": 4, "⏸": 5, "·": 6}

def _project_mod(slug, dash):
    entry = _BRIEF_PROJ_BY_SLUG.get(slug)
    if not entry:
        return "·"
    return dash.get(entry[0], ("·", ""))[0]


def _category_mod(cat_slug, dash):
    _, kids = _BRIEF_CAT_BY_SLUG[cat_slug]
    ranks = [_MOD_RANK.get(_project_mod(k, dash), 99) for k in kids]
    if not ranks:
        return "·"
    best = min(ranks)
    for mod, r in _MOD_RANK.items():
        if r == best:
            return mod
    return "·"


def _kb_projects():
    dash = _parse_status_dashboard()
    items = []
    in_cat = {k for _, _, kids in _BRIEF_CATEGORIES for k in kids}
    for idx, (cat_slug, cat_name, _kids) in enumerate(_BRIEF_CATEGORIES):
        mod = _category_mod(cat_slug, dash)
        items.append((_MOD_RANK.get(mod, 99), idx, "cat", cat_slug, cat_name, mod))
    for idx, (slug, name, _) in enumerate(_BRIEF_PROJECTS):
        if slug in in_cat or slug in _HIDDEN_AT_TOP:
            continue
        mod = dash.get(name, ("·", ""))[0]
        items.append((_MOD_RANK.get(mod, 99), 1000 + idx, "proj", slug, name, mod))
    items.sort()
    rows = []
    for _, _, kind, slug, name, mod in items:
        if kind == "cat":
            rows.append([{"text": f"{mod} {name} ▸", "callback_data": f"nav:cat:{slug}"}])
        else:
            rows.append([{"text": f"{mod} {name}", "callback_data": f"nav:proj:{slug}"}])
    rows.append([{"text": "⬅ назад", "callback_data": "nav:root"}])
    return {"inline_keyboard": rows}


def _kb_category(cat_slug):
    name, kids = _BRIEF_CAT_BY_SLUG[cat_slug]
    dash = _parse_status_dashboard()
    items = []
    for idx, child in enumerate(kids):
        entry = _BRIEF_PROJ_BY_SLUG.get(child)
        if not entry:
            continue
        cname, _ = entry
        mod = dash.get(cname, ("·", ""))[0]
        items.append((_MOD_RANK.get(mod, 99), idx, child, cname, mod))
    items.sort()
    rows = [[{"text": f"{mod} {cname}", "callback_data": f"nav:proj:{slug}"}] for _, _, slug, cname, mod in items]
    rows.append([{"text": "⬅ назад", "callback_data": "nav:projects"}])
    return {"inline_keyboard": rows}


def _text_category(cat_slug):
    name, _ = _BRIEF_CAT_BY_SLUG[cat_slug]
    return f"📂 *{name}* — выбери"


def _text_projects():
    return "📂 *проекты* — выбери"


def _parent_of(slug):
    """Return ('cat', cat_slug) or ('proj', parent_slug) or ('projects', None)."""
    for parent, kids in _BRIEF_SUBPROJECTS.items():
        if slug in kids:
            return ("proj", parent)
    for cat_slug, _, kids in _BRIEF_CATEGORIES:
        if slug in kids:
            return ("cat", cat_slug)
    return ("projects", None)


def _kb_project(slug):
    _, rel = _BRIEF_PROJ_BY_SLUG[slug]
    rows = []
    row = []
    if os.path.exists(os.path.join(VAULT, rel, "tasks.md")):
        row.append({"text": "📋 задачи", "callback_data": f"f:t:{slug}"})
    if os.path.exists(os.path.join(VAULT, rel, "ideas.md")):
        row.append({"text": "💡 идеи",   "callback_data": f"f:i:{slug}"})
    if row:
        rows.append(row)
    if os.path.exists(os.path.join(VAULT, rel, "manual.md")):
        rows.append([{"text": "📂 manual", "callback_data": f"f:m:{slug}"}])
    # Drill-in to subprojects (e.g. Мульти-Агент under Курс Курс).
    dash = _parse_status_dashboard()
    for child in _BRIEF_SUBPROJECTS.get(slug, []):
        centry = _BRIEF_PROJ_BY_SLUG.get(child)
        if not centry:
            continue
        cname, _ = centry
        cmod = dash.get(cname, ("·", ""))[0]
        rows.append([{"text": f"↳ {cmod} {cname}", "callback_data": f"nav:proj:{child}"}])
    kind, ptarget = _parent_of(slug)
    if kind == "cat":
        rows.append([{"text": "⬅ назад", "callback_data": f"nav:cat:{ptarget}"}])
    elif kind == "proj":
        pname, _ = _BRIEF_PROJ_BY_SLUG[ptarget]
        rows.append([{"text": f"⬅ {pname}", "callback_data": f"nav:proj:{ptarget}"}])
    else:
        rows.append([{"text": "⬅ назад", "callback_data": "nav:projects"}])
    return {"inline_keyboard": rows}


def _text_project(slug):
    name, _ = _BRIEF_PROJ_BY_SLUG[slug]
    dash = _parse_status_dashboard()
    mod, state = dash.get(name, ("·", "(нет в status.md)"))
    return f"{mod} *{name}*\n\n{_shorten(state, 300)}"

def _describe_level(level):
    if level == "root":
        return "главное меню (кнопки: проекты / задачи / идеи / inbox)"
    if level == "projects":
        return "список проектов (выбор категории/проекта)"
    if isinstance(level, tuple):
        kind = level[0]
        if kind == "cat":
            entry = _BRIEF_CAT_BY_SLUG.get(level[1])
            if entry:
                return f"категория «{entry[0]}» (выбор проекта)"
            return f"категория {level[1]}"
        if kind == "proj":
            slug = level[1]
            entry = _BRIEF_PROJ_BY_SLUG.get(slug)
            if entry:
                return f"меню проекта «{entry[0]}» (кнопки: manual / задачи / идеи)"
            return f"меню проекта {slug}"
        if kind == "file":
            rel_path = level[1]
            return f"просматривает файл {rel_path}"
    return None


def _nav_state_line(chat_id):
    """Return '[NAV_STATE] ...' prefix line or empty string."""
    level = _brief_level.get(chat_id)
    desc = _describe_level(level) if level else None
    if not desc:
        return ""
    return f"[NAV_STATE] Пользователь сейчас в TG-меню: {desc}.\n"

def _send_brief(chat_id, text, kb):
    """Send a new brief message at the bottom of chat, transfer the active keyboard."""
    _strip_active_keyboard(chat_id)
    try:
        res = api("sendMessage", chat_id=chat_id,
                  text=md_to_html(text), parse_mode="HTML",
                  reply_markup=json.dumps(kb, ensure_ascii=False),
                  disable_web_page_preview="true")
    except Exception as e:
        log(f"_send_brief: {e}")
        return
    if res.get("ok"):
        _brief_active_msg[chat_id] = res["result"]["message_id"]


def _level_text_kb(level):
    """level: 'root' | 'projects' | ('proj', slug)"""
    if level == "root":
        return _text_root(), _kb_root()
    if level == "projects":
        return _text_projects(), _kb_projects()
    if isinstance(level, tuple) and level[0] == "proj":
        return _text_project(level[1]), _kb_project(level[1])
    if isinstance(level, tuple) and level[0] == "cat":
        return _text_category(level[1]), _kb_category(level[1])
    return _text_root(), _kb_root()


def handle_brief_nav_callback(chat_id, msg_id, data):
    """Navigation between morning-brief menus. Sends new message at bottom, strips old."""
    parts = data.split(":")
    if len(parts) == 2 and parts[1] == "root":
        level = "root"
    elif len(parts) == 2 and parts[1] == "projects":
        level = "projects"
    elif len(parts) == 3 and parts[1] == "proj":
        slug = parts[2]
        if slug not in _BRIEF_PROJ_BY_SLUG:
            return
        level = ("proj", slug)
    elif len(parts) == 3 and parts[1] == "cat":
        cat_slug = parts[2]
        if cat_slug not in _BRIEF_CAT_BY_SLUG:
            return
        level = ("cat", cat_slug)
    else:
        return
    # If the clicked button was on the currently-active message, also treat it
    # as the active one (sync state in case external script sent the first menu).
    if msg_id and chat_id not in _brief_active_msg:
        _brief_active_msg[chat_id] = msg_id
    _brief_level[chat_id] = level
    text, kb = _level_text_kb(level)
    _send_brief(chat_id, text, kb)


def handle_brief_file_callback(chat_id, uid, data):
    """callback_data formats:
    - f:t:<slug>   → Projects/<dir>/tasks.md
    - f:i:<slug>   → Projects/<dir>/ideas.md
    - f:m:<slug>   → Projects/<dir>/manual.md
    - f:gen_tasks  → Tasks/tasks.md
    - f:gen_ideas  → Tasks/ideas.md
    - f:inbox      → inbox.md
    - f:daily      → запустить /daily-prep как user-сообщение
    """
    parts = data.split(":")
    rel_path = None
    label = None
    # After dumping a file: which "back" target the post-file keyboard points to.
    back_to_slug = None  # set for project files only
    if len(parts) == 2:
        key = parts[1]
        if key == "gen_tasks":
            rel_path, label = "Tasks/tasks.md", "Tasks/tasks.md"
        elif key == "gen_ideas":
            rel_path, label = "Tasks/ideas.md", "Tasks/ideas.md"
        elif key == "inbox":
            rel_path, label = "inbox.md", "inbox.md"
        elif key == "daily":
            _run_turn(uid, chat_id, "/daily-prep",
                      source="callback", summary_text="[📅 daily]")
            return
    elif len(parts) == 3:
        kind, slug = parts[1], parts[2]
        entry = _BRIEF_PROJ_BY_SLUG.get(slug)
        if not entry:
            return
        _, proj_dir = entry
        fname = {"t": "tasks.md", "i": "ideas.md", "m": "manual.md"}.get(kind)
        if not fname:
            return
        rel_path = f"{proj_dir}/{fname}"
        label = rel_path
        back_to_slug = slug
    if not rel_path:
        return
    # Build nav row: project files → [⬅ <project>] [🏠 МЕНЮ]; general → [⬅ назад] [🏠 МЕНЮ].
    if back_to_slug:
        name, _ = _BRIEF_PROJ_BY_SLUG[back_to_slug]
        back_btn = {"text": f"⬅ {name}", "callback_data": f"nav:proj:{back_to_slug}"}
    else:
        back_btn = {"text": "⬅ назад", "callback_data": "nav:root"}
    # Track that the user is now LOOKING AT the file, not at a menu level.
    _brief_level[chat_id] = ("file", rel_path)
    nav_rows = [[back_btn, _MENU_ROW[0]]]
    full = os.path.join(VAULT, rel_path)
    try:
        with open(full, encoding="utf-8") as f:
            body = f.read()
    except FileNotFoundError:
        send_chunked(chat_id, f"⚠️ нет файла: {rel_path}", menu_rows=nav_rows)
        return
    body = _clean_file_for_view(body)
    if not body.strip():
        send_chunked(chat_id, f"📄 {rel_path}\n\n(пусто)", menu_rows=nav_rows)
        return
    send_chunked(chat_id, f"📄 *{rel_path}*\n\n{body}", menu_rows=nav_rows)


def _clean_file_for_view(body):
    """Strip служебную информацию for TG file dumps:
    - YAML-ish frontmatter (---...--- at file head)
    - HTML comments <!-- ... -->
    - Leading blockquotes and italic-meta paragraphs above the first content line
    """
    # 1. Strip frontmatter.
    m = re.match(r"\A\s*---\s*\n.*?\n---\s*\n", body, flags=re.DOTALL)
    if m:
        body = body[m.end():]
    # 2. Strip HTML comments.
    body = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
    # 3. Strip "header noise" between headings and the first real content line:
    # blockquotes (>), italic-only meta paragraphs (*...*), blank lines.
    # Skipping mode RESETS after each ## / ### heading, so the same filter
    # cleans the preamble of every section, not just the file top.
    lines = body.splitlines()
    out = []
    skipping = True
    for ln in lines:
        s = ln.strip()
        # Drop file-level heading (# X) — redundant with the breadcrumb header.
        if s.startswith("# ") and not s.startswith("## "):
            continue
        # Section heading: keep, then re-enter skipping for the section's preamble.
        if s.startswith("## "):
            out.append(ln)
            skipping = True
            continue
        if skipping:
            if not s:
                continue
            # Any blockquote line, including bare ">" (paragraph break inside quote).
            if s.startswith(">"):
                continue
            if s.startswith("*") and s.endswith("*") and not s.startswith("**"):
                continue
            skipping = False
        out.append(ln)
    cleaned = "\n".join(out)
    cleaned = _drop_empty_sections(cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


# Marker of "real content" inside a section: bullet, checkbox, numbered list,
# separator line (---), table row, or fenced code block.
_CONTENT_MARKER_RE = re.compile(
    r"(?m)^\s*(?:[-*+]\s|\d+[\.\)]\s|---\s*$|\|.+\||```)"
)


def _drop_empty_sections(text):
    """Drop any `## Section` whose body has no content marker (bullet/separator/etc).
    Such sections are documentation/explanatory and don't belong in a TG file view."""
    parts = re.split(r"(?m)^(?=## )", text)
    out_parts = []
    for i, part in enumerate(parts):
        if i == 0:
            out_parts.append(part)  # pre-section preamble (already filtered above)
            continue
        # Strip the heading line itself before testing the body.
        body = part.split("\n", 1)[1] if "\n" in part else ""
        if _CONTENT_MARKER_RE.search(body):
            out_parts.append(part)
    return "".join(out_parts)

def setup_bot_menu():
    """Register slash-command menu in TG client (the '/' button)."""
    cmds = [{"command": c, "description": d} for c, d in BOT_MENU_COMMANDS]
    payload = json.dumps({"commands": cmds}).encode()
    req = urllib.request.Request(
        f"{API}/setMyCommands",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            res = json.load(r)
        if res.get("ok"):
            log(f"setMyCommands ok: {len(cmds)} commands")
        else:
            log(f"setMyCommands not ok: {res}")
    except Exception as e:
        log(f"setMyCommands error: {e}")
