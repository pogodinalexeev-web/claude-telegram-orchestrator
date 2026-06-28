"""Telegram orchestrator for a Claude Code agent.

The bot is a thin transport layer: it long-polls Telegram, keeps one resident
`claude -p` process per chat (see resident_claude.ResidentClaude), streams the
agent's events back as a single reply, and supports "catch-up" — if the user sends
a new message before the previous answer is done, the running turn is interrupted
and a fresh one starts on the same live process (history preserved).

Claude Code is the engine; this code orchestrates it. Configuration is read from
the environment — see .env.example.
"""

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from resident_claude import ResidentClaude

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
API = f"https://api.telegram.org/bot{TOKEN}"

CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-4-7")
AGENT_CWD = os.environ.get("AGENT_CWD", ".")
SYSTEM_PROMPT_FILE = os.environ.get("SYSTEM_PROMPT_FILE", "prompts/system_prompt.example.md")

# Only these user ids may talk to the bot. Comma-separated in the env.
ALLOWLIST = {
    int(x) for x in os.environ.get("TELEGRAM_ALLOWLIST", "").replace(" ", "").split(",") if x
}

# Hard guardrails handed to every resident process.
DISALLOWED_TOOLS = (
    "Bash(rm:-rf /) Bash(rm:-rf /*) Bash(reboot:*) Bash(shutdown:*) Edit(/etc/**) Write(/etc/**)"
)


def load_system_prompt() -> str:
    with open(SYSTEM_PROMPT_FILE, encoding="utf-8") as f:
        return f.read()


def api_call(method: str, **params) -> dict:
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(f"{API}/{method}", data=data)
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.load(r)


def send_message(chat_id: int, text: str) -> None:
    # Telegram caps a message at 4096 chars; chunk on paragraph boundaries.
    for chunk in _chunk(text, 4000):
        api_call("sendMessage", chat_id=chat_id, text=chunk)


def _chunk(text: str, limit: int):
    if len(text) <= limit:
        yield text
        return
    buf = ""
    for para in text.split("\n\n"):
        if len(buf) + len(para) + 2 > limit and buf:
            yield buf
            buf = ""
        buf += para + "\n\n"
    if buf.strip():
        yield buf.strip()


class ChatPool:
    """One resident Claude process per chat, with a per-chat turn lock for catch-up."""

    def __init__(self, system_prompt: str):
        self.system_prompt = system_prompt
        self._procs: dict[int, ResidentClaude] = {}
        self._locks: dict[int, threading.Lock] = {}

    def _lock_for(self, chat_id: int) -> threading.Lock:
        return self._locks.setdefault(chat_id, threading.Lock())

    def _resident_for(self, chat_id: int) -> ResidentClaude:
        rc = self._procs.get(chat_id)
        if rc is None or not rc.is_alive():
            rc = ResidentClaude(
                claude_bin=CLAUDE_BIN,
                system_prompt=self.system_prompt,
                model=MODEL,
                cwd=AGENT_CWD,
                resume_sid=rc.session_id if rc else None,
                disallowed_tools=DISALLOWED_TOOLS,
            )
            rc.start()
            self._procs[chat_id] = rc
        return rc

    def handle(self, chat_id: int, text: str) -> str:
        # Catch-up: if a turn is already running for this chat, interrupt it so this
        # new message starts a fresh turn on the same process.
        rc = self._procs.get(chat_id)
        if rc and rc.is_alive():
            rc.interrupt()
        with self._lock_for(chat_id):
            rc = self._resident_for(chat_id)
            reply = ""
            for ev in rc.send_and_collect(text):
                if ev.get("type") == "result":
                    reply = ev.get("result", "") or reply
            return reply or "(empty reply)"


def _handle_in_thread(pool: ChatPool, chat_id: int, text: str) -> None:
    try:
        send_message(chat_id, pool.handle(chat_id, text))
    except Exception as e:  # noqa: BLE001 — surface any turn error to the user
        send_message(chat_id, f"internal error: {e}")


def main() -> None:
    pool = ChatPool(load_system_prompt())
    offset = 0
    print(f"bot started, allowlist={sorted(ALLOWLIST)}", flush=True)
    while True:
        try:
            with urllib.request.urlopen(
                f"{API}/getUpdates?timeout=30&offset={offset}", timeout=40
            ) as r:
                data = json.load(r)
            if not data.get("ok"):
                time.sleep(5)
                continue
            for upd in data["result"]:
                offset = upd["update_id"] + 1
                msg = upd.get("message")
                if not msg or "text" not in msg:
                    continue
                uid = msg["from"]["id"]
                chat_id = msg["chat"]["id"]
                if ALLOWLIST and uid not in ALLOWLIST:
                    continue
                # Run each turn off the poll loop so a new message can arrive mid-answer
                # and trigger catch-up (interrupt). The per-chat lock serializes turns.
                threading.Thread(
                    target=_handle_in_thread, args=(pool, chat_id, msg["text"]), daemon=True
                ).start()
        except urllib.error.URLError:
            time.sleep(10)
        except KeyboardInterrupt:
            raise
        except Exception:  # noqa: BLE001 — keep the poll loop alive
            time.sleep(5)


if __name__ == "__main__":
    main()
