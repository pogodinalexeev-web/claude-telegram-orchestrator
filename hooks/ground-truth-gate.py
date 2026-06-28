#!/usr/bin/env python3
"""ground-truth-gate — UserPromptSubmit. Soft reminder to Read source before
asserting code/architecture/status/external-fixations/numbers/external facts.
Refire suppression: min_gap=2 user turns.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _lib

TRIGGERS = [
    # 1. code / config
    "what's in the code", "how is it structured", "how does it work", "what does the function do",
    "what's in settings", "what's in the hook", "what's in the config", "what's the logic",
    "does it scan", "why does it work that way", "why does the bot", "why does the script",
    "what's in the .py", "what's in the .sh",
    # 2. architecture / folder structure
    "what's in the folder", "which files", "how is the project structured", "what lives in",
    "where do I have", "where does it live", "where is it stored", "where did it move",
    "what are the connections", "how is it connected",
    # 3. status / loop state
    "what's the status of", "what's closed", "what's open", "what phase", "what's now",
    "what's in the roadmap", "what did we decide", "what was decided",
    "what's recorded", "what's in the loop",
    "what do we have with", "how are we doing with",
    "on schedule", "per our plan",
    "when will we", "when is this",
    "why didn't it fire", "why did it fire", "why didn't I see",
    "what's in the list now", "what's in the list",
    "what tasks", "what's on for tomorrow",
    "full text", "show the file", "show the hook",
    # 4. external fixations (what someone else said/wrote)
    "what I wrote", "what we discussed", "what's in the log", "what's in the inbox",
    "what's in memory", "what's in the chat", "what the bot wrote", "what I said",
    # explicit discipline commands
    "check", "find", "look at", "have a look",
    # 5. numbers / dates / names
    "when did I", "what date", "how many times", "how many lines", "how many files",
    "what's the limit", "what's the filename", "what's the exact name",
    # 6. external facts
    "how much does it cost", "what's the price", "latest version", "new model",
    "what was released", "release",
]

ANTI = [
    "don't check", "from memory", "no read", "no audit", "on the fly",
    "don't look in the file", "just tell me",
]


def main():
    inp = _lib.read_hook_input()
    session_id = inp.get("session_id", "")
    if not session_id:
        return
    current_prompt = inp.get("prompt", "")

    jsonl = _lib.get_session_jsonl(session_id)
    entries = _lib.parse_jsonl(jsonl) if jsonl else []

    text = _lib.last_user_text(entries, current_prompt=current_prompt)
    if not text:
        return

    low = text.lower()
    if any(a in low for a in ANTI):
        return

    matched = [t for t in TRIGGERS if t in low]
    if not matched:
        return

    marker = os.path.join(_lib.state_dir(), "ground-truth-gate.last-fired")
    if not _lib.refire_ok(marker, session_id, _lib.count_user_turns(entries), min_gap=2):
        return

    shown = ", ".join(matched[:3])
    msg = (
        "GROUND-TRUTH GATE. In the hook-names block (before main response, see simple-language-gate) "
        "add [G]. Current turn only. "
        f"Prompt contains a state-fact trigger class: '{shown}'. "
        "Before asserting anything about code/architecture/status/external fixations/numbers/external facts — "
        "**use an appropriate tool** before answering (not from memory). "
        "Choose what fits — file/command/search/external request. "
        "If answering from memory — mark it explicitly with 'from memory, unverified' at the start of the response. "
        "Don't restate another agent's words as fact — go to the primary source. "
    )
    _lib.emit_context(msg)


if __name__ == "__main__":
    main()
