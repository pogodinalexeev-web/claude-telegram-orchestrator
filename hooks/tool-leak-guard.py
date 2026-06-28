#!/usr/bin/env python3
"""tool-leak-guard — Stop hook.

Catches a leaked tool call: the model printed tool-call markup
(<invoke name="...">...</invoke>) as PLAIN TEXT instead of making a real call.
The engine doesn't see a real tool → closes the turn → the bot returns a truncated response.

Reference incident: session where the model output `<invoke name="Read">...</invoke>`
as text after a Bash call; the turn was cut off at stop_hook_summary and the owner
received a truncated response and had to manually ping.

Signal (narrow, to avoid catching legitimate markup citations in explanations):
the final text block of the last assistant message, after strip,
ENDS WITH `</invoke>` (or `</parameter>`). A real leak is always terminal —
the model was about to call and got stuck. A bug explanation doesn't end that way.

Loop protection: if stop_hook_active=True (we already nudged once, and the model
is still leaking) — skip, let it through.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _lib

# Terminal tails of a leaked call.
TAIL_RE = re.compile(r"(</invoke>|</parameter>|</function_calls>)\s*$")
# Confirming marker that this is actually a call, not a stray </tag>.
OPEN_RE = re.compile(r"<invoke\s+name=|<parameter\s+name=|<function_calls>")


def last_assistant_final_text(entries):
    """Text of the last assistant message (concatenation of its text blocks)."""
    for e in reversed(entries):
        if e.get("type") != "assistant":
            continue
        content = e.get("message", {}).get("content", [])
        if not isinstance(content, list):
            return ""
        parts = [
            c.get("text", "")
            for c in content
            if isinstance(c, dict) and c.get("type") == "text"
        ]
        # If this same message had a real tool_use — it's NOT a leak,
        # the model is working normally (may cite markup alongside real calls).
        has_real_tool = any(
            isinstance(c, dict) and c.get("type") == "tool_use" for c in content
        )
        if has_real_tool:
            return ""
        return "\n".join(parts).strip()
    return ""


def main():
    inp = _lib.read_hook_input()
    # Already nudged this cycle — don't loop.
    if inp.get("stop_hook_active"):
        return
    transcript = inp.get("transcript_path", "")
    if not transcript or not os.path.exists(transcript):
        return

    entries = _lib.parse_jsonl(transcript)
    text = last_assistant_final_text(entries)
    if not text:
        return

    if TAIL_RE.search(text) and OPEN_RE.search(text):
        _lib.emit_block(
            "BLOCK tool-leak-guard: you printed a TOOL CALL as raw text "
            "(<invoke name=\"...\">...</invoke>) instead of making a real call — "
            "so the turn was cut off, the tool did NOT execute, and the owner got a truncated response. "
            "Do NOT print tool-call markup in response text. Make a REAL tool call "
            "(Read/Bash/Grep/Edit/…) — it will execute — then finish the response with normal prose."
        )


if __name__ == "__main__":
    main()
