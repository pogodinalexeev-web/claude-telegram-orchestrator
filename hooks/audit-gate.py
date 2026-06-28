#!/usr/bin/env python3
"""audit-gate — UserPromptSubmit. Counts structural/hypothesis triggers in last
N user replies. >=3 hits without /audit in the window → injects pulse + threshold note.
Pulse fires on every architectural turn (no refire-suppression — owner wants the counter visible).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _lib

TRIGGERS = [
    "hypothesis", "architecture", "refactor",
    "new component", "new category", "new folder", "new module",
    "new file", "which approach", "how to do", "what to use",
    "sub-agent", "subagent", "multi-agent", "multiagent",
    "let's break it down", "challenge this", "verify idea", "help me decide",
    "strategic", "choice of approach", "dilemma",
    "buy", "subscription", "switch model",
    "migrate", "merge two", "split",
    "how to solve", "choose between", "either", "or this or that",
    "not sure how", "is it needed", "should I", "is it correct",
    "take it or not", "keep it", "abandon", "cancel",
    "different approach", "approach differently",
    "restructure", "reorganize",
]

AUDIT_MARKERS = [
    "/audit",
    "run audit", "launch audit", "do an audit",
    "three-agent", "triagent",
]

WINDOW = 30
THRESHOLD = 3


def main():
    inp = _lib.read_hook_input()
    session_id = inp.get("session_id", "")
    if not session_id:
        return
    current_prompt = inp.get("prompt", "")

    jsonl = _lib.get_session_jsonl(session_id)
    entries = _lib.parse_jsonl(jsonl) if jsonl else []

    hits = 0
    for text in _lib.iter_user_texts(entries, current_prompt=current_prompt, limit=WINDOW):
        low = text.lower()
        if any(m in low for m in AUDIT_MARKERS):
            return  # /audit found in window → silent
        if any(tr in low for tr in TRIGGERS):
            hits += 1

    if hits == 0:
        return

    pulse = (
        "AUDIT GATE PULSE. In the hook-names block (before main response, see simple-language-gate) "
        f"add [A#{hits}]. Number = counter of architectural/hypothesis turns "
        "in the thread without /audit. Current turn only."
    )

    if hits >= THRESHOLD:
        pulse += (
            f"\n\nAUDIT GATE THRESHOLD. {hits} architectural/hypothesis turns accumulated without /audit. "
            "Before the next substantive response choose one: (a) run /audit on the last "
            "architectural turn; (b) explicitly write one line 'audit not needed because <reason>' "
            "(acceptable: implementing a previously decided approach / minor edit in accepted architecture / "
            "simple lookup question / owner said 'no audit'); (c) close the thread via /close-session. "
            "Silently continuing is not allowed."
        )

    _lib.emit_context(pulse)

    marker = os.path.join(_lib.state_dir(), "audit-gate.last-fired")
    try:
        import json
        json.dump({"session_id": session_id, "hits": hits}, open(marker, "w"))
    except Exception:
        pass


if __name__ == "__main__":
    main()
