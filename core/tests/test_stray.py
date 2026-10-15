"""25.09.2026: служебное событие между ходами не должно превращать следующий ход
в «ничейный» (инцидент 00:45: ответ пропал, бот висел до ⏹СТОП)."""
import json, os, subprocess, sys, tempfile, threading, time, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import resident_claude as R


def _ev(**k):
    return json.dumps(k, ensure_ascii=False)


def _delta(t):
    return _ev(type="stream_event", event={"type": "content_block_delta",
                                            "delta": {"type": "text_delta", "text": t}})


class StrayNoise(unittest.TestCase):
    def test_noise_does_not_steal_next_turn(self):
        ms = _ev(type="stream_event", event={"type": "message_start"})
        turn = [ms, _delta("ответ"), ms, _delta("💾"), _ev(type="result", result="💾", session_id="s")]
        lines = ["import sys,time",
                 f"print({_ev(type='system', subtype='status')!r},flush=True)",
                 "sys.stdin.readline()",
                 *[f"print({l!r},flush=True)" for l in turn],
                 "time.sleep(2)"]
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write("\n".join(lines))
        rc = R.ResidentClaude("x", "", "m", "e", "/tmp")
        rc.proc = subprocess.Popen([sys.executable, f.name], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   text=True, bufsize=1)
        strays = []
        rc.on_stray = strays.append
        threading.Thread(target=rc._read_loop, daemon=True).start()
        time.sleep(0.5)
        self.assertFalse(rc._turn_open)
        got = list(rc.send_and_collect("hi", turn_timeout=10, silence_timeout=10))
        self.assertEqual(got[-1].get("type"), "result")
        self.assertEqual(strays, [])
        rc.proc.kill()
        os.unlink(f.name)


if __name__ == "__main__":
    unittest.main()
