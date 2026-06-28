#!/usr/bin/env python3
"""verify-plan-gate — UserPromptSubmit. Catches "let's do X" without verify-plan.
Injects 3-question verify reminder (Plan-first L1). Refire suppression: min_gap=2.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _lib

TRIGGERS = [
    "let's do", "let's build", "let's write", "let's implement",
    "let's add", "add a hook", "add a rule", "add a skill", "add a command",
    "build a hook", "build a skill", "new hook", "new skill", "new command",
    "let's fix", "let's extend", "extend the rule", "extend skill",
    "let's deploy", "let's ship", "let's wire",
    "implement", "build it", "write the hook", "write the skill",
    "we need to", "we need a", "we need to add",
    "need to implement", "need to do", "need to build", "need to fix",
    "need to add", "need to write", "need to wire",
    "let's go ahead", "let's proceed",
    "fixing the hook", "fixing the skill", "fixing the rule",
    "patch the hook", "patch the format",
    "let's run through", "go ahead and run",
    "roll out", "hard-code", "bake in",
    "change this", "we're changing",
    "show the new", "show the fixed",
]

ANTI = [
    "no verification", "no eval", "no test",
    "just do it", "trust me", "don't verify",
    "minor edit", "per the agreed architecture",
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

    marker = os.path.join(_lib.state_dir(), "verify-plan-gate.last-fired")
    if not _lib.refire_ok(marker, session_id, _lib.count_user_turns(entries), min_gap=2):
        return

    shown = ", ".join(matched[:2])
    msg = (
        "VERIFY-PLAN GATE. In the hook-names block (before main response, see simple-language-gate) "
        "add [V]. Current turn only. "
        f"Implementation trigger: '{shown}'. "
        "Before the first Write/Edit/new file you must:\n"
        "(A) Write out a plan in plain text (Plan-first L1 — plan mode as the first control point). "
        "If owner opened Plan mode via Shift+Tab — use ExitPlanMode when plan is ready. "
        "If not — EnterPlanMode is not available to the agent, so plan in text + discipline: don't write before approve.\n"
        "(B) In the plan, answer three verify questions (Plan-first L1, 'how to verify' — most important part):\n"
        "   1) How will we know it worked? (metric / test / run / example case)\n"
        "   2) What sample? (1 case / 5 / 30 / live observation N days)\n"
        "   3) What counts as failure? (if X — revert / redo)\n"
        "(C) Wait for explicit 'approve' before the first Write/Edit. Not self-authorized.\n"
        "Not 'build it, test later'. Plan + verify questions — BEFORE first implementation. "
        "If a plan isn't needed (minor edit per accepted architecture / owner said 'no plan' / 'no verification') — "
        "one line at the start: 'plan not needed because <reason>'."
    )
    _lib.emit_context(msg)


if __name__ == "__main__":
    main()
