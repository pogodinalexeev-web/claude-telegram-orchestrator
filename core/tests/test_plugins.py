"""08.10.2026: общее ядро скиллов, флаг --plugin-dir в запуске резидента."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import resident_claude as rc  # noqa: E402


def _rc():
    r = rc.ResidentClaude.__new__(rc.ResidentClaude)
    r.claude_bin, r.model, r.effort = "claude", "m", "high"
    r.system_prompt, r.permission_mode = "sp", "bypassPermissions"
    r.disallowed_tools, r.resume_sid = "", None
    return r


class PluginDirs(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("TGBOT_PLUGIN_DIRS", None)

    def test_dir_with_manifest_is_passed(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / ".claude-plugin").mkdir()
            (Path(d) / ".claude-plugin" / "plugin.json").write_text("{}")
            os.environ["TGBOT_PLUGIN_DIRS"] = d
            cmd = _rc()._build_cmd()
            self.assertEqual(cmd[-2:], ["--plugin-dir", d])

    def test_missing_dir_adds_nothing(self):
        os.environ["TGBOT_PLUGIN_DIRS"] = "/nonexistent/lab-skills"
        self.assertNotIn("--plugin-dir", _rc()._build_cmd())

    def test_dir_without_manifest_adds_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["TGBOT_PLUGIN_DIRS"] = d
            self.assertNotIn("--plugin-dir", _rc()._build_cmd())


if __name__ == "__main__":
    unittest.main()
