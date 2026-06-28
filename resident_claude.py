"""ResidentClaude — a long-lived `claude -p --input-format stream-json` process.

The idea: instead of spawning a fresh `subprocess.Popen` on every turn, keep one
process alive per conversation. The cold start (~6s) is paid once at creation;
the second and later turns skip the Node.js + claude-binary + MCP-server load.

Lifecycle:
    rc = ResidentClaude(claude_bin=..., system_prompt=..., model=..., cwd=...)
    rc.start()                              # spawn Popen
    for ev in rc.send_and_collect("hi"):    # generator of JSON events until result
        ...
    rc.is_alive() -> bool
    rc.interrupt()                          # protocol interrupt, process stays alive
    rc.close()                              # close stdin, wait for exit
    rc.kill()                               # SIGKILL (for a hard STOP button)

After kill() the instance is dead — create a new ResidentClaude (with --resume of
the old session id if you want to keep the history).
"""

import json
import os
import subprocess
import threading
import time
from collections.abc import Iterator


class ResidentClaude:
    def __init__(
        self,
        claude_bin: str,
        system_prompt: str,
        model: str = "claude-opus-4-7",
        cwd: str = ".",
        resume_sid: str | None = None,
        env_extra: dict | None = None,
        disallowed_tools: str = "",
        permission_mode: str = "bypassPermissions",
        effort: str = "medium",
    ):
        self.claude_bin = claude_bin
        self.system_prompt = system_prompt
        self.model = model
        self.effort = effort
        self.cwd = cwd
        self.resume_sid = resume_sid
        self.env_extra = env_extra or {}
        self.disallowed_tools = disallowed_tools
        self.permission_mode = permission_mode

        self.proc: subprocess.Popen | None = None
        self.session_id: str | None = resume_sid
        self.started_at: float | None = None
        self._lock = threading.Lock()
        self._interrupt_seq = 0

    def _build_cmd(self) -> list:
        cmd = [
            self.claude_bin,
            "-p",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--model",
            self.model,
            "--effort",
            self.effort,
            "--verbose",
            "--include-partial-messages",
            "--append-system-prompt",
            self.system_prompt,
            "--permission-mode",
            self.permission_mode,
        ]
        if self.disallowed_tools:
            cmd += ["--disallowedTools", self.disallowed_tools]
        if self.resume_sid:
            cmd += ["--resume", self.resume_sid]
        return cmd

    def start(self) -> None:
        """Spawn the Popen. Does not block on an init event — claude stays silent on
        stdout until it receives the first user message on stdin. The init event and
        session_id arrive in the stream during the first send_and_collect()."""
        env = {**os.environ, **self.env_extra}
        self.proc = subprocess.Popen(
            self._build_cmd(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=self.cwd,
            bufsize=1,
            text=True,
        )
        self.started_at = time.time()

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def send_and_collect(
        self,
        text: str,
        turn_timeout: float = 3600.0,
        silence_timeout: float = 600.0,
    ) -> Iterator[dict]:
        """Send a user message and yield JSON events until a type=result line.
        Not thread-safe: one turn at a time.

        A watchdog thread kills the process if a turn runs longer than turn_timeout
        OR goes silent (no bytes on stdout) for longer than silence_timeout. Without
        the watchdog the timeout never fired: a blocking readline() sits idle while
        claude is silent (stuck in a hung tool call), so the loop never reaches the
        time check — the bot would stay mute until the full hour limit elapsed."""
        if not self.is_alive():
            raise RuntimeError("claude process not alive")
        msg = {"type": "user", "message": {"role": "user", "content": text}}
        self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

        t0 = time.time()
        last_output = [t0]  # mutable cell shared with the watchdog
        stop_watchdog = threading.Event()

        def _watchdog():
            while not stop_watchdog.wait(5.0):
                now = time.time()
                if now - t0 > turn_timeout or now - last_output[0] > silence_timeout:
                    # kill the process: stdout closes, readline returns '' (EOF)
                    self.kill()
                    return

        wd = threading.Thread(target=_watchdog, daemon=True)
        wd.start()

        try:
            while True:
                line = self.proc.stdout.readline()
                if not line:
                    if self.proc.poll() is not None:
                        # watchdog killed it on timeout/silence OR claude died on its own
                        if time.time() - t0 > turn_timeout:
                            raise TimeoutError(f"turn exceeded {turn_timeout}s")
                        if time.time() - last_output[0] > silence_timeout:
                            raise TimeoutError(f"no output for {silence_timeout}s (stuck)")
                        raise RuntimeError("claude died mid-turn")
                    continue
                last_output[0] = time.time()
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") == "system" and ev.get("subtype") == "init":
                    self.session_id = ev.get("session_id") or self.session_id
                yield ev
                if ev.get("type") == "result":
                    if ev.get("session_id"):
                        self.session_id = ev["session_id"]
                    return
        finally:
            stop_watchdog.set()

    def interrupt(self) -> None:
        """Send a protocol interrupt (control_request) to the current turn. The
        process stays alive (unlike kill): the interrupted turn ends with a result of
        subtype=error_during_execution / is_error=True and empty text. Used for
        "catch-up" — when the user sends another message before the answer finishes:
        interrupt the current turn to start a new one on the same live process (the
        session history is preserved)."""
        if not self.is_alive():
            return
        self._interrupt_seq += 1
        req = {
            "type": "control_request",
            "request_id": f"int-{self._interrupt_seq}",
            "request": {"subtype": "interrupt"},
        }
        with self._lock:
            try:
                self.proc.stdin.write(json.dumps(req) + "\n")
                self.proc.stdin.flush()
            except Exception:
                pass

    def kill(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.kill()
            except Exception:
                pass

    def close(self, wait: float = 5.0) -> None:
        if not self.proc:
            return
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=wait)
        except Exception:
            self.kill()


if __name__ == "__main__":
    # Minimal smoke test. Configure via environment, no hardcoded paths.
    claude_bin = os.environ.get("CLAUDE_BIN", "claude")
    model = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

    rc = ResidentClaude(
        claude_bin=claude_bin,
        system_prompt="You are a test assistant. Answer very briefly, one word if possible.",
        model=model,
        cwd=os.environ.get("AGENT_CWD", "."),
        disallowed_tools="Bash(rm:-rf /) Bash(rm:-rf /*) Bash(reboot:*) Bash(shutdown:*) Edit(/etc/**) Write(/etc/**)",
    )

    t0 = time.time()
    rc.start()
    print(f"[{time.time() - t0:.1f}s] started")

    for i, prompt in enumerate(["say one", "say two", "say three"], 1):
        t1 = time.time()
        reply = ""
        for ev in rc.send_and_collect(prompt):
            if ev.get("type") == "result":
                reply = ev.get("result", "")
                break
        print(f"[turn {i}] {time.time() - t1:.1f}s reply={reply!r} alive={rc.is_alive()}")

    rc.close()
    print(f"total {time.time() - t0:.1f}s, closed.")
