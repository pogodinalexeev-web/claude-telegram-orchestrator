#!/usr/bin/env python3
"""check-before-claim — Stop hook. If user said "check" (or similar) and the
assistant made zero tool calls this turn → block. "A claim must be grounded in a fact, not a form."
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _lib

TRIGGERS = [
    "check", "verify", "look at", "find", "search for",
    "look up", "look into", "have a look", "show me",
]

ANTI = [
    "don't check", "from memory", "no read", "just tell me", "don't look",
]


def main():
    inp = _lib.read_hook_input()
    session_id = inp.get("session_id", "")
    transcript = inp.get("transcript_path", "")
    if not session_id or not transcript or not os.path.exists(transcript):
        return

    entries = _lib.parse_jsonl(transcript)
    text = _lib.last_user_text(entries)
    if not text:
        return

    low = text.lower()
    if any(a in low for a in ANTI):
        return
    if not any(t in low for t in TRIGGERS):
        return

    # Index of last real user msg
    last_idx = -1
    for i, e in enumerate(entries):
        if _lib.is_real_user(e) and _lib.extract_text(e):
            last_idx = i

    # Count tool_use in assistant messages after that index
    tool_count = 0
    for m in entries[last_idx + 1:]:
        if m.get("type") != "assistant":
            continue
        content = m.get("message", {}).get("content", [])
        if isinstance(content, list):
            tool_count += sum(
                1 for c in content if isinstance(c, dict) and c.get("type") == "tool_use"
            )

    if tool_count > 0:
        return

    _lib.emit_block(
        "BLOCK check-before-claim: the prompt contains a 'check/verify/find' trigger, "
        "but this turn had ZERO tool calls (Read/Bash/Grep/search/etc). "
        "You answered from memory. "
        "Pick the right tool for the question type and rewrite the answer with facts from source:\n"
        "• Vague/semantic question ('what was said about X', names, project status) → "
        "semantic (hybrid) RAG search first. On broad topics run 2-4 reformulations. "
        "Found a chunk → read the source via Read by file:line.\n"
        "• Exact string (password, URL, exact filename, recent edit) → Grep/Read on the raw file."
    )


if __name__ == "__main__":
    main()
