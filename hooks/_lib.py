"""Shared helpers for UserPromptSubmit / Stop hooks.

Each hook file imports from here. Behaviour preserved 1:1 with pre-refactor
hand-rolled JSONL parsing; only deduplication.
"""
import json
import os
import sys


def read_hook_input():
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def get_session_jsonl(session_id, project_slug=None):
    if not session_id:
        return None
    # project_slug encodes the vault path; override via CLAUDE_PROJECT_SLUG env var
    if project_slug is None:
        project_slug = os.environ.get("CLAUDE_PROJECT_SLUG", "-home-owner-vault")
    path = os.path.expanduser(
        f"~/.claude/projects/{project_slug}/{session_id}.jsonl"
    )
    return path if os.path.exists(path) else None


def parse_jsonl(path):
    if not path or not os.path.exists(path):
        return []
    out = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for ln in f:
                try:
                    out.append(json.loads(ln))
                except Exception:
                    continue
    except Exception:
        return []
    return out


def is_real_user(entry):
    """A real owner prompt: type=user, no isMeta, no sourceToolUseID,
    and content is not a tool_result wrapper."""
    if entry.get("type") != "user":
        return False
    if entry.get("isMeta") or entry.get("sourceToolUseID"):
        return False
    content = entry.get("message", {}).get("content", "")
    if isinstance(content, list):
        if any(
            isinstance(c, dict) and c.get("type") == "tool_result"
            for c in content
        ):
            return False
    return True


def extract_text(entry):
    content = entry.get("message", {}).get("content", "")
    if isinstance(content, list):
        parts = [
            c.get("text", "")
            for c in content
            if isinstance(c, dict) and c.get("type") == "text"
        ]
        return "\n".join(parts).strip()
    return str(content).strip()


def iter_user_texts(entries, current_prompt=None, limit=None):
    """Yield user texts newest→oldest. current_prompt yielded first if non-empty."""
    yielded = 0
    if current_prompt:
        s = current_prompt.strip()
        if s:
            yield s
            yielded += 1
            if limit and yielded >= limit:
                return
    for e in reversed(entries):
        if not is_real_user(e):
            continue
        t = extract_text(e)
        if not t:
            continue
        yield t
        yielded += 1
        if limit and yielded >= limit:
            return


def last_user_text(entries, current_prompt=None):
    """Return the newest real user text (current_prompt if present, else jsonl tail)."""
    for t in iter_user_texts(entries, current_prompt=current_prompt, limit=1):
        return t
    return ""


def count_user_turns(entries):
    return sum(1 for e in entries if is_real_user(e))


def refire_ok(marker_path, session_id, user_count, min_gap=2):
    """True if enough user-turns passed since last fire. Writes marker on True."""
    prev = {}
    if os.path.exists(marker_path):
        try:
            prev = json.load(open(marker_path))
        except Exception:
            prev = {}
    if (
        prev.get("session_id") == session_id
        and (user_count - int(prev.get("turn", 0))) < min_gap
    ):
        return False
    try:
        json.dump(
            {"session_id": session_id, "turn": user_count},
            open(marker_path, "w"),
        )
    except Exception:
        pass
    return True


def state_dir():
    base = os.environ.get("CLAUDE_PROJECT_DIR", os.path.expanduser("~/vault"))
    d = os.path.join(base, ".claude", "scheduled", "state")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def emit_context(text):
    """UserPromptSubmit additionalContext payload."""
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": text,
                }
            },
            ensure_ascii=False,
        )
    )


def emit_block(reason):
    """Stop hook block decision."""
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
