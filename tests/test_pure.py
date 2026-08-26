#!/usr/bin/env python3
"""Сеть безопасности перед пересборкой tg-bot.py (18.08.2026).

Пинним ТЕКУЩЕЕ поведение чистых функций — тех, что не лезут в сеть и не держат
состояние. Это не «правильное» поведение, а «то, что есть сейчас»: любой рефактор,
меняющий их молча, роняет тесты.

Запуск:  cd /home/owner/bot-src/tests
         TGBOT_PATH=/home/owner/bot-src/tg-bot.py python3 -m unittest test_pure test_seams
"""
import unittest

# По умолчанию — живой бот. TGBOT_PATH позволяет натравить тесты на копию
# (проверка «сеть ловит поломку», прогон правки до выкатки). Заготовку профиля
# для песочницы подставляет harness — см. его docstring.
import harness

tg = harness.load("tgbot")


class Markers(unittest.TestCase):
    """Контракт между моим ответом и парсерами кода. Ломается — бот перестаёт
    понимать собственные маркеры: не пишет в inbox, не ставит календарь, не шлёт кнопки."""

    def test_wrote(self):
        self.assertEqual(
            tg.parse_wrote_markers("текст\n__WROTE__: 2 entries to inbox.md"),
            ("текст", [(2, "inbox.md")]),
        )

    def test_wrote_multiple(self):
        cleaned, items = tg.parse_wrote_markers(
            "a\n__WROTE__: 1 entries to inbox.md\n__WROTE__: 3 entries to Tasks/tasks.md"
        )
        self.assertEqual(cleaned, "a")
        self.assertEqual(items, [(1, "inbox.md"), (3, "Tasks/tasks.md")])

    def test_wrote_absent(self):
        self.assertEqual(tg.parse_wrote_markers("просто текст"), ("просто текст", []))

    def test_task(self):
        self.assertEqual(
            tg.parse_task_markers("a\n__TASK__: сделай X"), ("a", ["сделай X"])
        )

    def test_choices(self):
        self.assertEqual(
            tg.parse_choices_marker("a\n__CHOICES__: Раз | Два | Три"),
            ("a", ["Раз", "Два", "Три"]),
        )

    def test_affirm(self):
        self.assertEqual(tg.parse_affirm_marker("a\n__AFFIRM__"), ("a", True))
        self.assertEqual(tg.parse_affirm_marker("a"), ("a", False))

    def test_dig(self):
        self.assertEqual(tg.parse_dig_marker("a\n__DIG__"), ("a", True))

    def test_cal_propose_allday(self):
        cleaned, data = tg.parse_cal_propose_marker(
            'a\n__CAL_PROPOSE__: {"title":"T","start":"2026-08-19",'
            '"end":"2026-08-19","all_day":true}'
        )
        self.assertEqual(cleaned, "a")
        self.assertEqual(data["title"], "T")
        self.assertTrue(data["all_day"])

    def test_bg_task_defaults(self):
        cleaned, data = tg.parse_bg_task_marker('a\n__BG_TASK__: {"cmd":"ls","label":"L"}')
        self.assertEqual(cleaned, "a")
        self.assertEqual(data["cmd"], "ls")
        self.assertEqual(data["timeout"], 3600)  # дефолт, если не указан
        self.assertEqual(data["then"], "")

    def test_tts(self):
        text, on, engine, voice = tg.parse_tts_marker("__TTS__ voice=максим\nтекст")
        self.assertEqual(text, "текст")
        self.assertTrue(on)
        self.assertEqual(engine, "zvukogram")


class Rendering(unittest.TestCase):
    """Разметка и нарезка. Ломается — сообщения уходят кусками или со звёздочками."""

    def test_md_bold_italic(self):
        self.assertEqual(tg.md_to_html("**жир** и *кур*"), "<b>жир</b> и <i>кур</i>")

    def test_chunk_splits_at_limit(self):
        self.assertEqual([len(c) for c in tg.chunk_text("a" * 9000)], [4000, 4000, 1000])

    def test_chunk_short_untouched(self):
        self.assertEqual(tg.chunk_text("коротко"), ["коротко"])

    def test_tg_chunk_limit_is_4000(self):
        self.assertEqual(tg.TG_CHUNK, 4000)


class Buttons(unittest.TestCase):
    """Кнопки под ответом."""

    def test_no_choices_by_default(self):
        self.assertEqual(tg.detect_choices("Что дальше?"), [])

    def test_question_needs_confirm(self):
        self.assertTrue(tg.needs_confirm("Делать?"))

    def test_statement_needs_no_confirm(self):
        self.assertFalse(tg.needs_confirm("Сделал."))


class VaultSafety(unittest.TestCase):
    """Страж записи. Ломается — бот пишет за пределы vault. Самый важный тест файла."""

    def test_normal_path_resolves(self):
        # возвращает Path, не строку — пинним как есть
        self.assertEqual(str(tg.safe_vault_path("Tasks/tasks.md")), "/home/owner/vault/Tasks/tasks.md")

    def test_escape_denied(self):
        self.assertIsNone(tg.safe_vault_path("../../etc/passwd"))

    def test_absolute_escape_denied(self):
        self.assertIsNone(tg.safe_vault_path("/etc/passwd"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
