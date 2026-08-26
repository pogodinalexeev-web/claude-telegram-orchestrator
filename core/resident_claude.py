"""ResidentClaude — долгоживущий процесс `claude -p --input-format stream-json`.

Идея: вместо `subprocess.Popen` на каждый ход бота держать один процесс на chat_id.
Холодный старт (~6с на VPS) платится один раз при создании, второй и последующие
ходы — без оверхеда Node.js + загрузки claude-binary + MCP-серверов.

Жизненный цикл:
  rc = ResidentClaude(system_prompt=..., model="claude-opus-4-7", cwd=...)
  rc.start()                       # spawn Popen, дождаться init-события
  events = rc.send_and_collect("привет")  # генератор JSON-событий до result
  rc.is_alive() -> bool
  rc.close()                       # закрыть stdin, дождаться выхода
  rc.kill()                        # SIGKILL (для кнопки СТОП)

После kill() инстанс мёртв — нужен новый ResidentClaude (с --resume старого sid если
надо сохранить историю).
"""

import json
import os
import queue
import subprocess
import threading
import time
from typing import Iterator, Optional


class ResidentClaude:
    def __init__(
        self,
        claude_bin: str,
        system_prompt: str,
        cwd: str,          # без значения по умолчанию: забыть = писать в чужую папку
        model: str = "claude-opus-4-7",
        resume_sid: Optional[str] = None,
        env_extra: Optional[dict] = None,
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

        self.proc: Optional[subprocess.Popen] = None
        self.session_id: Optional[str] = resume_sid
        self.started_at: Optional[float] = None
        self._lock = threading.Lock()
        self._interrupt_seq = 0

        # 2026-07-02: единый читатель stdout. Раньше stdout читал только
        # send_and_collect — ходы, начатые самим harness'ом (будильники
        # фоновых агентов), копились в трубе и вываливались с отставанием
        # при следующем сообщении хозяина. Теперь читает ОДИН поток всегда;
        # «ничейные» ходы отдаются сразу через self.on_stray.
        self._q: Optional[queue.Queue] = None
        self._consumer_active = False
        self._turn_open = False          # читатель сейчас внутри ничейного хода
        self._stray_buf: list = []
        self._last_line_at = time.time()
        self.on_stray = None             # callback(list_of_events) — ставит бот

    def _build_cmd(self) -> list:
        cmd = [
            self.claude_bin, "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--model", self.model,
            "--effort", self.effort,
            "--verbose",
            "--include-partial-messages",
            "--append-system-prompt", self.system_prompt,
            "--permission-mode", self.permission_mode,
        ]
        if self.disallowed_tools:
            cmd += ["--disallowedTools", self.disallowed_tools]
        if self.resume_sid:
            cmd += ["--resume", self.resume_sid]
        return cmd

    def start(self) -> None:
        """Spawn the Popen. Не блокируется на init-событие — claude молчит на stdout,
        пока не получит первый user-message в stdin. init/session_id придут потоком
        в первый send_and_collect()."""
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
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        """Единственный читатель stdout. Активный ход → события в очередь
        консюмеру; ничейный ход (никто не ждёт) → копим и на result отдаём
        через on_stray НЕМЕДЛЕННО. Ничего не копится в трубе."""
        while True:
            try:
                line = self.proc.stdout.readline()
            except Exception:
                break
            if not line:
                if self.proc.poll() is not None:
                    q = self._q
                    if q is not None:
                        q.put(None)   # разбудить консюмера: процесс умер
                    break
                continue
            self._last_line_at = time.time()
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if ev.get("type") == "system" and ev.get("subtype") == "init":
                self.session_id = ev.get("session_id") or self.session_id
            if self._consumer_active and self._q is not None:
                self._q.put(ev)
            else:
                self._turn_open = True
                self._stray_buf.append(ev)
                if ev.get("type") == "result":
                    self._turn_open = False
                    if ev.get("session_id"):
                        self.session_id = ev["session_id"]
                    buf, self._stray_buf = self._stray_buf, []
                    cb = self.on_stray
                    if cb:
                        try:
                            cb(buf)
                        except Exception:
                            pass

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def send_and_collect(
        self,
        text: str,
        turn_timeout: float = 3600.0,
        silence_timeout: float = 600.0,
    ) -> Iterator[dict]:
        """Шлёт user-message и yield-ит JSON-события до строки type=result.
        Не thread-safe: один турн в один момент времени.

        Сторож (watchdog) в отдельном потоке убивает процесс, если ход висит
        дольше turn_timeout ИЛИ молчит (ни байта на stdout) дольше silence_timeout.
        Без сторожа таймаут не срабатывал: голый readline() блокирующий, и пока
        claude молчит (застрял в зависшем вызове инструмента), цикл не доходит до
        проверки времени — бот сидел немой до самого конца часового лимита."""
        if not self.is_alive():
            raise RuntimeError("claude process not alive")

        # Хвост ничейного хода, идущего прямо сейчас: его события придут в нашу
        # очередь первыми — их нельзя выдать как ответ на НАШ prompt. Помечаем и
        # доотдаём через on_stray, ответ хозяину начинается со СЛЕДУЮЩЕГО result.
        self._q = queue.Queue()
        pending_stray = 1 if self._turn_open else 0
        stray_prefix: list = []
        if pending_stray:
            stray_prefix, self._stray_buf = self._stray_buf, []
        self._consumer_active = True

        msg = {"type": "user", "message": {"role": "user", "content": text}}
        self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

        t0 = time.time()
        # Сброс отметки тишины (26.08.2026, инцидент «дневной брифинг умер за 3 минуты»):
        # _last_line_at общая на процесс, а не на ход. Резидент, простоявший ночь, приходил
        # в новый ход с отметкой десятичасовой давности — сторож на первой же проверке
        # видел «молчит дольше silence_timeout» и убивал процесс ДО начала работы.
        # Считаем тишину от момента отправки запроса, а не от прошлой активности.
        self._last_line_at = t0
        stop_watchdog = threading.Event()

        def _watchdog():
            while not stop_watchdog.wait(5.0):
                now = time.time()
                if now - t0 > turn_timeout or now - self._last_line_at > silence_timeout:
                    # убиваем процесс: stdout закроется, читатель положит None
                    self.kill()
                    return

        wd = threading.Thread(target=_watchdog, daemon=True)
        wd.start()

        try:
            while True:
                try:
                    ev = self._q.get(timeout=1.0)
                except queue.Empty:
                    if self.proc.poll() is not None:
                        if time.time() - t0 > turn_timeout:
                            raise TimeoutError(f"turn exceeded {turn_timeout}s")
                        if time.time() - self._last_line_at > silence_timeout:
                            raise TimeoutError(f"no output for {silence_timeout}s (stuck)")
                        raise RuntimeError("claude died mid-turn")
                    continue
                if ev is None:
                    # читатель сообщил: процесс умер
                    if time.time() - t0 > turn_timeout:
                        raise TimeoutError(f"turn exceeded {turn_timeout}s")
                    if time.time() - self._last_line_at > silence_timeout:
                        raise TimeoutError(f"no output for {silence_timeout}s (stuck)")
                    raise RuntimeError("claude died mid-turn")
                if pending_stray:
                    stray_prefix.append(ev)
                    if ev.get("type") == "result":
                        pending_stray -= 1
                        cb = self.on_stray
                        if cb:
                            try:
                                cb(stray_prefix)
                            except Exception:
                                pass
                        stray_prefix = []
                    continue
                yield ev
                if ev.get("type") == "result":
                    if ev.get("session_id"):
                        self.session_id = ev["session_id"]
                    return
        finally:
            stop_watchdog.set()
            self._consumer_active = False
            self._q = None

    def interrupt(self) -> None:
        """Послать protocol-interrupt (control_request) текущему ходу.
        Процесс ОСТАЁТСЯ жив (в отличие от kill): прерванный ход завершается
        result'ом с subtype=error_during_execution / is_error=True и пустым text.
        Используется для «догонки» — когда хозяин дослал сообщение, не дождавшись
        конца ответа: прерываем текущий ход, чтобы запустить новый на том же
        живом процессе (история сессии сохраняется)."""
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


if __name__ == "__main__":   # ручная проверка: python3 resident_claude.py [папка]
    import os
    import shutil
    import sys
    HOME = os.path.expanduser("~")
    CLAUDE = shutil.which("claude") or f"{HOME}/.local/bin/claude"
    WORKDIR = sys.argv[1] if len(sys.argv) > 1 else HOME
    SYSTEM_PROMPT = "Ты — тестовый ассистент. Отвечай очень коротко, одним словом если возможно."
    rc = ResidentClaude(
        claude_bin=CLAUDE,
        system_prompt=SYSTEM_PROMPT,
        model="claude-haiku-4-5-20251001",
        cwd=WORKDIR,
        env_extra={
            "HOME": HOME,
            "PATH": f"{os.path.dirname(CLAUDE)}:{os.environ.get('PATH', '')}",
        },
        disallowed_tools="Bash(rm:-rf /) Bash(rm:-rf /*) Bash(reboot:*) Bash(shutdown:*) Edit(/etc/**) Write(/etc/**)",
    )

    print(f"[{0:.1f}s] starting...")
    t0 = time.time()
    rc.start()
    print(f"[{time.time()-t0:.1f}s] init done, session_id={rc.session_id}")

    for i, prompt in enumerate(["скажи раз", "скажи два", "скажи три"], 1):
        print(f"\n[turn {i}] send: {prompt}")
        t1 = time.time()
        reply = ""
        for ev in rc.send_and_collect(prompt):
            if ev.get("type") == "result":
                reply = ev.get("result", "")
                break
        dt = time.time() - t1
        print(f"[turn {i}] done in {dt:.1f}s, reply={reply!r}, alive={rc.is_alive()}")

    print(f"\ntotal: {time.time()-t0:.1f}s")
    rc.close()
    print("closed.")
