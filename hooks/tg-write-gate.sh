#!/usr/bin/env bash
# PreToolUse (mcp__telegram-mcp__*): confirmation/block for writing to Telegram chats.
#
# Goal: no MUTATING Telegram action (send, reply, forward, edit, delete,
# create groups/channels, etc.) goes through without explicit confirmation.
# Read-only actions (get_*, list_*, search_*, *_info) pass freely.
#
# Behavior by mode:
#   - Interactive session: permissionDecision=ask → native yes/no prompt.
#   - Silent bot (claude -p on server, no approver): ask → auto-block, nothing sent.
#   - Emergency one-time bypass: file ~/.config/telegram-mcp/allow-send
#     (touch it → next write call is allowed, file is immediately deleted).
#
# Input: JSON on stdin (tool_name, tool_input). Output: JSON decision on stdout, exit 0.

INPUT=$(cat 2>/dev/null || echo "{}")

python3 - "$INPUT" <<'PY'
import json, os, sys

raw = sys.argv[1] if len(sys.argv) > 1 else "{}"
try:
    data = json.loads(raw)
except Exception:
    data = {}

tool = data.get("tool_name", "") or ""
# short tool name after the mcp__<server>__ prefix
short = tool.split("__")[-1] if "__" in tool else tool

# Mutating (write) telegram-mcp tools — gated.
WRITE = {
    "send_message", "send_scheduled_message", "delete_scheduled_message",
    "reply_to_message", "forward_message", "forward_messages",
    "edit_message", "delete_message", "delete_chat_history",
    "delete_messages_bulk", "create_poll", "send_reaction", "remove_reaction",
    "pin_message", "send_file", "send_album", "send_voice", "send_sticker",
    "send_gif", "upload_file", "send_contact", "add_contact", "delete_contact",
    "create_group", "create_channel", "invite_to_group", "leave_chat",
    "edit_chat_title", "edit_chat_photo", "edit_chat_about", "delete_chat_photo",
    "set_profile_photo", "delete_profile_photo", "set_privacy_settings",
    "set_bot_commands", "create_folder", "delete_folder",
    "add_chat_to_folder", "remove_chat_from_folder",
}

# Not our tool or read-only — pass silently.
if not tool.startswith("mcp__telegram-mcp__") or short not in WRITE:
    sys.exit(0)

# Emergency one-time bypass.
allow = os.path.expanduser("~/.config/telegram-mcp/allow-send")
if os.path.exists(allow):
    try:
        os.remove(allow)
    except OSError:
        pass
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow",
        "permissionDecisionReason": "One-time allow-send bypass — allowed, flag cleared.",
    }}))
    sys.exit(0)

# What and where — for readable confirmation text.
ti = data.get("tool_input", {}) or {}
chat = ti.get("chat_id", ti.get("entity", "?"))
text = ti.get("message", ti.get("text", ti.get("file", "")))
snippet = (str(text)[:80] + "...") if text and len(str(text)) > 80 else str(text)
reason = f"Telegram write: {short} → chat {chat}."
if snippet:
    reason += f" Text: '{snippet}'."
reason += " Confirm send (in silent bot mode — blocked)."

print(json.dumps({"hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "ask",
    "permissionDecisionReason": reason,
}}, ensure_ascii=False))
sys.exit(0)
PY
