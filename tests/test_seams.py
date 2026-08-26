#!/usr/bin/env python3
"""Тесты точек разреза — шаг 1 спецификации пересборки (19.08.2026).

Отличие от test_pure.py: тот пиннит поведение чистых функций «вообще», этот —
ровно те места, по которым пойдёт нож на шагах 2-5. Каждый класс тут = один
будущий разрез. Задача: не дать правке изменить поведение молча.

T1 — золотой слепок системного промпта (разрез на шаге 5).
T2 — снапшот значений будущего профиля (разрезы 3 и 4).
T3 — производные пути: всё считается от четырёх корней (разрез 3).
T4 — групповой фильтр: отложенный, ждёт функцию _group_gate (разрез 3).
T5 — форварды: функция и её инлайн-двойник дают одно и то же (разрез 2).

Запуск: cd /home/owner/bot-src/tests
        TGBOT_PATH=/home/owner/bot-src/tg-bot.py python3 -m unittest test_pure test_seams
"""
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import harness

tg = harness.load("tgbot_seams")

_TARGET = str(harness.TARGET)
_SRC = harness.SRC
_GOLDEN = Path(__file__).parent / "golden"
_SANDBOX = harness.TARGET.parent


class T1SystemPrompt(unittest.TestCase):
    """Шаг 5 режет константу промпта на блоки ядра + личность из vault + подстановки
    профиля. Условие приёмки — собранный текст байт-в-байт равен нынешнему."""

    def test_golden_snapshot_matches(self):
        golden = _GOLDEN / "system_prompt_vlad.txt"
        self.assertTrue(golden.exists(), "нет слепка — снять до правки промпта")
        self.assertEqual(golden.read_text(encoding="utf-8"), tg.SYSTEM_PROMPT)

    def test_persona_is_read_from_vault(self):
        """Личность живёт в заметках хозяина, а не в коде: слоты вынуты дословно."""
        persona = tg.load_persona(tg.PROFILE)
        self.assertEqual(set(persona), {"identity", "capture", "longform"})
        self.assertIn("Ты — ассистент Хозяина в Telegram", persona["identity"])
        self.assertIn("ЕДИНАЯ ТОЧКА ВХОДА", persona["capture"])
        for slot in persona.values():
            self.assertIn(slot, tg.SYSTEM_PROMPT, "текст личности попал в промпт не дословно")

    def test_bare_profile_drops_gated_blocks(self):
        """Профиль новичка: всё выключено, соседей нет, второго аккаунта нет.
        В промпте не должно остаться ни следа чужой инфраструктуры."""
        import botprofile
        bare = botprofile.build_profile({
            "owner_name": "Beta", "owner_uid": 1, "bot_username": "nastyabot",
            "tz_offset_hours": 3, "tz_label": "Москва (Europe/Moscow, UTC+3)",
            "home": "/home/beta", "vault": "/home/beta/vault",
        }, user="beta", source="<тест>")
        template = _SANDBOX / "templates" / "tg-persona-default.md"
        persona = tg.load_persona({"persona_path": str(template)}) if template.exists() else {}
        text = tg.build_system_prompt(bare, persona)
        for forbidden in ("календар", "Календар", "Звукограм", "Playwright", "playwright",
                          "@Pogodinalexeev", "sudo", "__TTS__", "__CAL_PROPOSE__",
                          "__TG_SEND_PROPOSE__", "otzhogbot", "alldanceallsing",
                          "100000001", "/home/owner", "owner_assistant_bot",
                          "Owner", "@@owner"):
            with self.subTest(forbidden):
                self.assertNotIn(forbidden, text)
        # а контракт, общий для всех, обязан остаться
        for kept in ("__WROTE__", "__TASK__", "__BG_TASK__", "__CHOICES__", "__AFFIRM__"):
            with self.subTest(kept):
                self.assertIn(kept, text)
        # бот обращается к СВОЕМУ хозяину по имени из профиля, а не к чужому
        self.assertIn("Beta", text)
        # папки .claude у выдуманного хозяина нет — обещать её нельзя (см. T1bSelfConf)
        self.assertNotIn("/home/beta/vault/.claude/**", text)
        # без файла личности ядро всё равно должно представить бота хозяину
        self.assertIn("Ты — ассистент Beta в Telegram.",
                      tg.build_system_prompt(bare, {}))

    def test_owner_alias_comes_from_profile(self):
        """Имя хозяина внутри текста ядра — подстановка, а не литерал.
        У Хозяина она обязана давать ровно «Owner» (иначе слепок разойдётся),
        у нового хозяина — его имя во всех трёх падежах."""
        import botprofile
        self.assertEqual(tg.PROFILE["owner_alias"], "Owner")
        self.assertEqual(tg.PROFILE["owner_alias_gen"], "Owner'а")
        self.assertEqual(tg.PROFILE["owner_alias_dat"], "Owner'у")
        self.assertIn("Owner тап", tg.SYSTEM_PROMPT)
        bare = botprofile.build_profile({
            "owner_name": "Beta", "owner_uid": 1, "bot_username": "nastyabot",
            "tz_offset_hours": 3, "tz_label": "Москва",
            "home": "/tmp/beta", "vault": "/tmp/beta/vault",
        }, user="beta", source="<тест>")
        # не указала падежи — берётся имя, но НИКОГДА чужое
        self.assertEqual(bare["owner_alias_gen"], "Beta")
        text = tg.build_system_prompt(bare, {})
        self.assertIn("Beta тап", text)
        self.assertNotIn("Owner", text)

    def test_prompt_carries_marker_contract(self):
        """Маркеры — контракт с парсерами. Пропал из промпта — я перестану их выдавать,
        а парсер останется ждать. Регрессия молчаливая, ловим здесь."""
        for marker in ("__WROTE__", "__TASK__", "__CAL_PROPOSE__", "__CAL_EVENT__",
                       "__CAL_CANCEL__", "__TG_SEND_PROPOSE__", "__BG_TASK__",
                       "__CHOICES__", "__AFFIRM__", "__DIG__", "__TTS__",
                       "__SAVE_ATTACHMENT__", "__DROP_ATTACHMENT__"):
            self.assertIn(marker, tg.SYSTEM_PROMPT, f"маркер {marker} исчез из промпта")


class T1bSelfConf(unittest.TestCase):
    """Блок САМО-КОНФИГ обещает боту его скиллы и крюки в `<vault>/.claude/`.
    Нет папки — нет обещания (баг Б1 первого прогона канарейки, 19.08)."""

    def _cfg(self, vault, git=False):
        import botprofile
        return botprofile.build_profile({
            "owner_name": "Тест", "owner_uid": 1, "bot_username": "testbot",
            "tz_offset_hours": 3, "tz_label": "Москва",
            "home": str(vault), "vault": str(vault),
            "features": {"git_sync": git},
        }, user="test", source="<тест>")

    def test_block_promises_config_when_folder_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".claude" / "hooks").mkdir(parents=True)
            text = tg._selfconf_block(self._cfg(Path(tmp)))
            self.assertIn("@@vault@@/.claude/**", text)
            self.assertIn("hook-smoke-test", text)

    def test_block_stays_silent_when_folder_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = tg._selfconf_block(self._cfg(Path(tmp)))
            self.assertNotIn(".claude/**", text)
            self.assertNotIn("hook-smoke-test", text)
            # шапка и остальные пункты на месте — блок не выкинут целиком
            self.assertIn("САМО-КОНФИГ", text)
            self.assertIn("@@settings_json@@", text)
            self.assertIn("__WROTE__", text)

    def test_no_git_no_rollback_promise(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".claude").mkdir()
            off = tg._selfconf_block(self._cfg(Path(tmp), git=False))
            on = tg._selfconf_block(self._cfg(Path(tmp), git=True))
            self.assertNotIn("откат через git", off)
            self.assertIn("откат через git", on)

    def test_unreadable_vault_is_treated_as_missing(self):
        """Чужой /home закрыт правами — сборка промпта не имеет права падать."""
        self.assertFalse(tg._dir_exists("/proc/1/root/root/nonexistent"))


class T1cStatusPanel(unittest.TestCase):
    """Живая панель «думаю…». Прежняя версия резала собранный HTML по 3800 —
    обрезка рвала теги, Telegram отвечал 400 (баг Б2 канарейки, 19.08)."""

    def test_fits_limit_and_closes_tags(self):
        panel = tg.build_status_panel(
            "⏳", "1м 5с", "читаю файл",
            ["раз-два-три " * 400],                 # рассуждение
            [f"строка инструмента {i} & <тег>" for i in range(20)],
            ["ответ модели " * 400])
        self.assertLessEqual(len(panel), tg.STATUS_PANEL_LIMIT)
        self.assertEqual(panel.count("<blockquote expandable>"),
                         panel.count("</blockquote>"), "разорванный blockquote")
        self.assertNotIn("&am\n", panel)
        self.assertFalse(panel.rstrip().endswith("<"), "обрезка попала внутрь тега")

    def test_escapes_user_content(self):
        panel = tg.build_status_panel("⏳", "3с", "<b>шалость</b>", [], ["a & b"], [])
        self.assertIn("&lt;b&gt;", panel)
        self.assertIn("a &amp; b", panel)

    def test_short_panel_is_not_padded(self):
        panel = tg.build_status_panel("⏳", "3с", None, [], [], ["привет"])
        self.assertEqual(panel, "⏳ думаю… (3с)\n\nпривет")

    def test_giant_tool_lines_still_fit(self):
        panel = tg.build_status_panel("⏳", "9с", None, [], ["x" * 5000], [])
        self.assertLessEqual(len(panel), tg.STATUS_PANEL_LIMIT)


class T2ProfileSnapshot(unittest.TestCase):
    """Шаг 3 собирает эти значения в объект PROFILE, шаг 4 — грузит их из json.
    После обоих шагов значения обязаны совпасть с этим слепком поле в поле."""

    EXPECTED = {
        # идентичность
        "VOICE_PORT": 8444,
        "TG_CHUNK": 4000,
        "SESSION_TTL_HOURS": 6,
        "CLAUDE_TIMEOUT": 3600,
        # секреты и кэш
        "TOKEN_FILE": "/etc/claude-tg/token",
        "ALLOW_FILE": "/etc/claude-tg/allowlist",
        "ASSEMBLYAI_KEY_FILE": "/etc/claude-tg/assemblyai-key",
        "GROQ_KEY_FILE": "/etc/claude-tg/groq-key",
        "SHORTCUT_TOKEN_FILE": "/etc/claude-tg/shortcut-token",
        "VOICE_CERT": "/etc/claude-tg/voice-cert.pem",
        "VOICE_KEY": "/etc/claude-tg/voice-key.pem",
        # исполняемые файлы и хелперы
        "CLAUDE_BIN": "/home/owner/.nvm/versions/node/v20.20.2/bin/claude",
        "TG_SEND_ONESHOT": "/home/owner/.local/bin/tg-send-oneshot.py",
        "TG_SEND_PYTHON": "/home/owner/telegram-mcp/.venv/bin/python",
        "EDGE_TTS_BIN": "/home/owner/edge-tts/.venv/bin/edge-tts",
        "ZVUKOGRAM_KEY_PATH": "/home/owner/.zvukogram-key",
        "ZVUKOGRAM_EMAIL_PATH": "/home/owner/.zvukogram-email",
        # пороги
        "GROQ_MODEL": "whisper-large-v3",
        "TTS_VOICE_DEFAULT": "ru-RU-DmitryNeural",
        "VOICE_REPLY_MAX_CHARS": 1800,
        "VOICE_LONG_THRESHOLD_SEC": 300,
        "NATIVE_MIN_SEC": 20,
        "VOICE_ARCHIVE_TTL_DAYS": 30,
        "PENDING_TTL_SEC": 3600,
        "PENDING_NAG_AFTER_SEC": 300,
        "PENDING_CAL_TTL_SEC": 600,
        "PENDING_SEND_TTL_SEC": 600,
        "ALBUM_BUFFER_SEC": 2.0,
        "BURST_BUFFER_SEC": 0.8,
    }

    EXPECTED_PATHS = {
        "CACHE_DIR": "/home/owner/.cache/claude-tg",
        "OFFSET_FILE": "/home/owner/.cache/claude-tg/offset",
        "SESSIONS_FILE": "/home/owner/.cache/claude-tg/sessions.json",
        "PENDING_FILE": "/home/owner/.cache/claude-tg/pending-attachments.json",
        "MANIFEST_DIR": "/home/owner/.cache/claude-tg/manifests",
        "VOICE_ARCHIVE_DIR": "/home/owner/.cache/claude-tg/voice-archive",
        "PENDING_CAL_FILE": "/home/owner/.cache/claude-tg/pending-calendar.json",
        "PENDING_SEND_FILE": "/home/owner/.cache/claude-tg/pending-tgsend.json",
        "VAULT": "/home/owner/vault",
        "ATTACH": "/home/owner/vault/Resources/attachments",
        "INBOX": "/home/owner/vault/inbox.md",
        "DO_QUEUE": "/home/owner/vault/Tasks/do-queue.md",
        "TASKS_FILE": "/home/owner/vault/Tasks/tasks.md",
    }

    def test_scalar_values(self):
        for name, value in self.EXPECTED.items():
            with self.subTest(name):
                self.assertEqual(getattr(tg, name), value)

    def test_path_values(self):
        for name, value in self.EXPECTED_PATHS.items():
            with self.subTest(name):
                self.assertEqual(str(getattr(tg, name)), value)

    def test_identity_values(self):
        """Кто хозяин и как зовут бота. С шага 3 — поля профиля, а не литералы в теле."""
        self.assertEqual(tg.PROFILE["owner_uid"], 100000001)
        self.assertEqual(tg.PROFILE["bot_username"], "owner_assistant_bot")
        self.assertEqual(tg.PROFILE["owner_name"], "Хозяин")
        self.assertEqual(tg.PROFILE["tz_offset_hours"], 3)
        self.assertEqual(str(tg.MSK), "UTC+03:00")
        self.assertEqual(tg.PROFILE["calendar_id"], "owner.calendar@example.com")
        self.assertEqual(tg.PROFILE["owner_second_account"], "@Pogodinalexeev")
        self.assertEqual(tg.PROFILE.neighbor_usernames, ["@alpha_assistant_bot", "@delta_assistant_bot"])

    def test_no_identity_literals_outside_profile(self):
        """Правило шага 3: uid, @username и /home/<имя> живут только в файле профиля.
        В коде — лишь чтение из PROFILE, иначе новый хозяин унаследует чужое.
        Здесь смотрим модуль контекста (он один знает про профиль), сплошная
        проверка по всем модулям ядра — в T8."""
        ctx = (_SANDBOX / "botctx.py").read_text(encoding="utf-8")
        _, _, body = ctx.partition("PROFILE = load_profile()")
        self.assertTrue(body, "блок профиля не найден — разметка botctx.py изменилась")
        for needle in ("100000001", "owner_assistant_bot", '"/home/owner'):
            with self.subTest(needle):
                self.assertNotIn(needle, body, f"{needle} осталось в теле кода вне профиля")

    def test_api_base_is_local_server(self):
        """Локальный Bot API — обход лимита 20 МБ. Съедет на api.telegram.org —
        большие файлы молча перестанут приниматься."""
        self.assertTrue(tg.API.startswith("http://127.0.0.1:8081/bot"))
        self.assertTrue(tg.FILE_API.startswith("http://127.0.0.1:8081/file/bot"))

    def test_menu_shape(self):
        self.assertEqual(len(tg._BRIEF_PROJECTS), 9)
        self.assertEqual([s for s, _, _ in tg._BRIEF_PROJECTS][:3], ["course", "ind", "ass"])
        self.assertEqual([s for s, _, _ in tg._BRIEF_CATEGORIES], ["it", "music"])
        self.assertEqual(tg._HIDDEN_AT_TOP, {"multi"})
        self.assertEqual(tg._BRIEF_SUBPROJECTS, {"course": ["multi"]})
        # производные словари обязаны сходиться со списками
        self.assertEqual(set(tg._BRIEF_PROJ_BY_SLUG), {s for s, _, _ in tg._BRIEF_PROJECTS})
        self.assertEqual(set(tg._BRIEF_CAT_BY_SLUG), {s for s, _, _ in tg._BRIEF_CATEGORIES})
        # каждый ребёнок категории существует как проект
        for _, _, kids in tg._BRIEF_CATEGORIES:
            for kid in kids:
                self.assertIn(kid, tg._BRIEF_PROJ_BY_SLUG)


class T2bProfileLoader(unittest.TestCase):
    """Шаг 4: значения приехали из файла. Тут проверяем сам загрузчик —
    что он читает файл, что падает на опечатке и на нехватке обязательного,
    и что подмена через TGBOT_PROFILE работает."""

    FILE = _SANDBOX / "deploy" / "profile.example.json"

    def setUp(self):
        if not self.FILE.exists():
            self.skipTest("нет заготовки deploy/profile.example.json (тест только для песочницы)")
        import botprofile
        self.mod = botprofile
        self.raw = json.loads(self.FILE.read_text(encoding="utf-8"))

    def build(self, raw):
        return self.mod.build_profile(raw, user="owner", source=str(self.FILE))

    def test_file_matches_step3_literals(self):
        """Профиль из файла == словарь-литерал шага 3, поле в поле."""
        p = self.build(self.raw)
        for name, value in {
            "owner_name": "Хозяин", "owner_uid": 100000001,
            "bot_username": "owner_assistant_bot", "tz_offset_hours": 3,
            "tz_label": "Москва (Europe/Moscow, UTC+3)",
            "home": "/home/owner", "vault": "/home/owner/vault",
            "cache_dir": "/home/owner/.cache/claude-tg", "secrets_dir": "/etc/claude-tg",
            "claude_bin": "/home/owner/.nvm/versions/node/v20.20.2/bin/claude",
            # 26.08.2026, переезд на общее ядро: было "/home/owner" — верно, пока
            # код жил в домашней папке. После переключения резидент берётся из
            # выкаченного ядра, иначе получаются две копии одного файла, которые
            # разъезжаются молча (предупреждение из deploy/cutover.md, шаг 3a).
            "resident_module_dir": "/opt/claude-tg",
            "tg_send_oneshot": "/home/owner/.local/bin/tg-send-oneshot.py",
            "tg_send_python": "/home/owner/telegram-mcp/.venv/bin/python",
            "edge_tts_bin": "/home/owner/edge-tts/.venv/bin/edge-tts",
            "zvukogram_key_path": "/home/owner/.zvukogram-key",
            "zvukogram_email_path": "/home/owner/.zvukogram-email",
            "native_helper": "/home/owner/transcribe_native.py",
            "settings_json": "/home/owner/.claude/settings.json",
            "rag_search": "/home/owner/vault-rag/search",
            "rag_sock": "/home/owner/vault-rag/daemon.sock",
            "chatlog_script": "/home/owner/vault-rag/chatlog.py",
            "api_base": "http://127.0.0.1:8081", "voice_port": 8444,
            "router_unit": "9router", "xdg_runtime_dir": "/run/user/1000",
            "calendar_id": "owner.calendar@example.com",
            "owner_second_account": "@Pogodinalexeev",
        }.items():
            with self.subTest(name):
                self.assertEqual(p[name], value)
        self.assertEqual(p.neighbor_usernames, ["@alpha_assistant_bot", "@delta_assistant_bot"])
        self.assertEqual(len(p.menu_projects), 9)
        self.assertEqual(p.menu_hidden_at_top, {"multi"})
        self.assertTrue(all(p.features.values()), "у Хозяина включено всё")

    def test_loaded_module_profile_equals_file(self):
        """То, с чем реально работает ядро, совпадает с файлом."""
        p = self.build(self.raw)
        for name in ("owner_uid", "vault", "cache_dir", "claude_bin", "voice_port"):
            with self.subTest(name):
                self.assertEqual(tg.PROFILE[name], p[name])

    def test_unknown_field_is_fatal(self):
        raw = dict(self.raw, vaultt="/home/owner/vault")
        with self.assertRaises(self.mod.ProfileError) as ctx:
            self.build(raw)
        self.assertIn("vaultt", str(ctx.exception))

    def test_unknown_feature_is_fatal(self):
        raw = dict(self.raw, features=dict(self.raw["features"], telepathy=True))
        with self.assertRaises(self.mod.ProfileError) as ctx:
            self.build(raw)
        self.assertIn("telepathy", str(ctx.exception))

    def test_missing_required_is_fatal(self):
        raw = {k: v for k, v in self.raw.items() if k != "owner_uid"}
        with self.assertRaises(self.mod.ProfileError) as ctx:
            self.build(raw)
        self.assertIn("owner_uid", str(ctx.exception))

    def test_wrong_type_is_fatal(self):
        raw = dict(self.raw, owner_uid="100000001")
        with self.assertRaises(self.mod.ProfileError) as ctx:
            self.build(raw)
        self.assertIn("owner_uid", str(ctx.exception))

    def test_missing_file_is_fatal(self):
        env = dict(os.environ, TGBOT_PROFILE="/tmp/нет-такого-профиля.json")
        code = "import botprofile; botprofile.load_profile()"
        r = subprocess.run([sys.executable, "-c", code], cwd=str(_SANDBOX),
                           env=env, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("нет файла профиля", r.stderr)

    def test_override_announces_itself(self):
        """Подмена профиля обязана быть видна в журнале — иначе однажды бот
        поедет в бою на тестовом файле, и никто не заметит."""
        lines = []
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as fh:
            json.dump(self.raw, fh, ensure_ascii=False)
            tmp = fh.name
        try:
            os.environ["TGBOT_PROFILE"] = tmp
            p = self.mod.load_profile(log=lines.append)
            self.assertTrue(lines and lines[0].startswith("PROFILE OVERRIDE "))
            self.assertEqual(p.owner_uid, 100000001)
        finally:
            os.environ["TGBOT_PROFILE"] = str(self.FILE)
            os.unlink(tmp)

    def test_defaults_for_a_new_person(self):
        """Профиль новичка — полтора десятка строк, остальное считается от корней."""
        p = self.mod.build_profile({
            "owner_name": "Beta", "owner_uid": 1, "bot_username": "nastyabot",
            "tz_offset_hours": 3, "tz_label": "Москва",
            "home": "/home/beta", "vault": "/home/beta/vault",
        }, user="beta", source="<тест>")
        self.assertEqual(p.cache_dir, "/home/beta/.cache/claude-tg")
        self.assertEqual(p.secrets_dir, "/etc/claude-tg-beta")
        self.assertEqual(p.persona_path, "/home/beta/vault/Self/tg-persona.md")
        self.assertEqual(p.unit_name, "claude-tg-bot@beta")
        self.assertEqual(p.settings_json, "/home/beta/.claude/settings.json")
        self.assertIsNone(p.voice_port)
        self.assertEqual(p.menu_projects, [])
        self.assertFalse(any(p.features.values()), "у новичка по умолчанию всё выключено")


class T3DerivedPaths(unittest.TestCase):
    """Шаг 3 требует: всё считается от четырёх корней (home, vault, cache, secrets).
    Если тут вылезет путь, не выводимый из корня, — у нового человека он останется
    указывать в чужой дом."""

    ROOTS = {
        "home": "/home/owner",
        "vault": "/home/owner/vault",
        "cache": "/home/owner/.cache/claude-tg",
        "secrets": "/etc/claude-tg",
    }

    def test_every_path_constant_lives_under_a_root(self):
        names = list(T2ProfileSnapshot.EXPECTED_PATHS) + [
            "TOKEN_FILE", "ALLOW_FILE", "ASSEMBLYAI_KEY_FILE", "GROQ_KEY_FILE",
            "SHORTCUT_TOKEN_FILE", "VOICE_CERT", "VOICE_KEY", "CLAUDE_BIN",
            "TG_SEND_ONESHOT", "TG_SEND_PYTHON", "EDGE_TTS_BIN",
            "ZVUKOGRAM_KEY_PATH", "ZVUKOGRAM_EMAIL_PATH",
        ]
        for name in names:
            with self.subTest(name):
                value = str(getattr(tg, name))
                self.assertTrue(
                    any(value.startswith(root) for root in self.ROOTS.values()),
                    f"{name}={value} не выводится ни из одного корня",
                )

    def test_vault_dir_duplicate_still_present(self):
        """_VAULT_DIR — второй экземпляр того же корня (шаг 2 его схлопывает в VAULT).
        Пока дубль жив — он обязан совпадать, иначе меню и запись разъедутся."""
        if hasattr(tg, "_VAULT_DIR"):
            self.assertEqual(tg._VAULT_DIR, str(tg.VAULT))


class T4GroupGate(unittest.TestCase):
    """Кто в групповом чате имеет право говорить с ботом. Сейчас логика зашита
    внутрь handle_message (нельзя вызвать отдельно) — тест ждёт росток _group_gate
    с шага 3. До того проверяем правила по исходнику."""

    RULES = [
        ("хозяин проходит всегда", "uid != VLAD_UID and not mentioned and not is_reply_to_bot"),
        ("обращение к другому боту — мимо", 'stripped.startswith("@") and not stripped.lower().startswith'),
        ("упоминание без разметки тоже считается", 'if not mentioned and ("@" + BOT_USERNAME).lower() in text_check.lower()'),
        ("ответ на сообщение бота считается", 'is_reply_to_bot = (reply_to.get("from") or {}).get("username") == BOT_USERNAME'),
    ]

    @unittest.skipIf(hasattr(tg, "_group_gate"), "росток уже вырос — переписать на прямые вызовы")
    def test_rules_present_in_source(self):
        for label, needle in self.RULES:
            with self.subTest(label):
                self.assertIn(needle, _SRC)

    @unittest.skipUnless(hasattr(tg, "_group_gate"), "ждёт функцию _group_gate (шаг 3)")
    def test_gate_decisions(self):
        gate = tg._group_gate
        owner, stranger = 100000001, 111
        msg = lambda t, **kw: dict({"text": t, "from": {"id": kw.pop("uid", stranger)}}, **kw)
        cases = [
            ("хозяин проходит всегда", msg("привет", uid=owner), "group", True),
            ("чужой без обращения — мимо", msg("привет"), "group", False),
            ("чужой с @упоминанием — проходит", msg("@owner_assistant_bot привет"), "group", True),
            ("хозяин пишет другому боту — мимо", msg("@alpha_assistant_bot привет", uid=owner), "group", False),
            ("ответ на сообщение бота — проходит",
             msg("ага", reply_to_message={"from": {"username": "owner_assistant_bot"}}), "group", True),
            ("в личке фильтра нет", msg("привет"), "private", True),
        ]
        for label, m, ctype, expected in cases:
            with self.subTest(label):
                self.assertEqual(gate(m, ctype).allowed, expected)

    @unittest.skipUnless(hasattr(tg, "_group_gate"), "ждёт функцию _group_gate (шаг 3)")
    def test_gate_reports_flags(self):
        """mentioned/is_reply_to_bot нужны ниже по коду — для допуска не-хозяина
        мимо allowlist. Потеряются — чужие в группе перестанут проходить вовсе."""
        g = tg._group_gate({"text": "@owner_assistant_bot ку", "from": {"id": 111}}, "group")
        self.assertTrue(g.mentioned)
        g2 = tg._group_gate({"text": "ку", "from": {"id": 111},
                             "reply_to_message": {"from": {"username": "owner_assistant_bot"}}}, "group")
        self.assertTrue(g2.is_reply_to_bot)


class T5Forwards(unittest.TestCase):
    """Шаг 2 схлопывает инлайн-разбор форвардов в handle_message в вызов
    _extract_forward_meta. Тест пиннит вывод функции до схлопывания и следит,
    чтобы у двойника были ровно те же шаблоны текста."""

    CASES = [
        ("канал",
         {"forward_origin": {"type": "channel", "chat": {"username": "ai_news", "title": "AI News"},
                             "message_id": 42, "date": 1755550000}},
         "[FORWARD_META] Переслано из канала: @ai_news (AI News), msg_id=42, дата=1755550000"),
        ("канал без username",
         {"forward_origin": {"type": "channel", "chat": {"title": "Закрытый"}, "message_id": 7, "date": 1}},
         "[FORWARD_META] Переслано из канала: @? (Закрытый), msg_id=7, дата=1"),
        ("пользователь",
         {"forward_origin": {"type": "user", "sender_user": {"username": "Pogodinalexeev", "id": 100000001,
                                                             "first_name": "Хозяин"}}},
         "[FORWARD_META] Переслано от пользователя: @Pogodinalexeev (id=100000001, имя=Хозяин)"),
        ("скрытый пользователь",
         {"forward_origin": {"type": "hidden_user", "sender_user_name": "Аноним"}},
         "[FORWARD_META] Переслано от скрытого пользователя: Аноним"),
        ("чат",
         {"forward_origin": {"type": "chat", "sender_chat": {"username": "devchat", "title": "Разработка"}}},
         "[FORWARD_META] Переслано из чата: @devchat (Разработка)"),
        ("старое поле forward_from",
         {"forward_from": {"username": "alpha", "first_name": "Alpha"}},
         "[FORWARD_META] Переслано от @alpha (Alpha)"),
        ("старое поле forward_from_chat",
         {"forward_from_chat": {"username": "ch", "title": "Канал", "type": "channel"}},
         "[FORWARD_META] Переслано из @ch (Канал, type=channel)"),
        ("обычное сообщение", {"text": "привет"}, ""),
    ]

    def test_function_output_pinned(self):
        for label, msg, expected in self.CASES:
            with self.subTest(label):
                self.assertEqual(tg._extract_forward_meta(msg), expected)

    def test_unknown_type_falls_back_to_raw(self):
        out = tg._extract_forward_meta({"forward_origin": {"type": "story", "chat": {"id": 1}}})
        self.assertTrue(out.startswith("[FORWARD_META] type=story raw="))

    def test_inline_twin_uses_same_templates(self):
        """Пока двойник жив — его шаблоны обязаны совпадать с функцией. После шага 2
        двойник исчезает и проверка становится пустой (это и есть цель шага)."""
        templates = re.findall(r'\[FORWARD_META\][^"]*', harness.ALL_SRC)
        in_func = inspect.getsource(tg._extract_forward_meta)
        func_templates = set(re.findall(r'\[FORWARD_META\][^"]*', in_func))
        # шаблоны из промпта в счёт не идут — там маркер упоминается прозой
        code_templates = {t for t in templates if "Переслано" in t or t.startswith("[FORWARD_META] type=")}
        self.assertTrue(func_templates)
        self.assertEqual(code_templates, func_templates,
                         "инлайн-двойник разошёлся с функцией — схлопывание изменит поведение")


class T8Isolation(unittest.TestCase):
    """Главный тест приватности. Ядро одно на всех — значит ни в одном его
    модуле не должно остаться следа конкретного человека. След в ядре = бот
    Beta однажды прочитает токен Хозяина и ответит в его чат от его имени.

    С распила 19.08 проверка идёт по ВСЕМ файлам ядра, а не по одному:
    иначе достаточно было переложить строку в соседний модуль, чтобы тест
    замолчал. Исключения ровно три — botprofile.py (там дефолты и живёт схема
    профиля), deploy/ (заготовки выката) и tests/golden/ (слепок хозяйского
    промпта, он обязан быть хозяйским)."""

    # Классы вшитых значений. Не список конкретных строк, а правила: новый
    # чужой uid или новый @ник должны падать так же, как известные.
    RULES = {
        "домашняя папка человека": r"/home/[a-z][a-z0-9._-]*",
        "числовой id человека": r"(?<![\d.])\d{9,10}(?![\d.])",
        "@ник бота или человека": r"(?<![@\w])@[A-Za-z][A-Za-z0-9_]{4,31}\b",
        "почтовый адрес": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        "юнит конкретного человека": r"claude-tg-bot@(?!%i|<)[a-z]+",
        "секреты конкретного человека": r"/etc/claude-tg-(?!<|\{|%)[a-z]+",
        # добавлено 19.08 (часть 3): классы, которых прежняя версия не видела
        "имя живого человека": (
            r"\bOwner\b|Хозяин|\bКост[яеию]\b|\bНаст[яеию]\b|\bДин[аеы]\b|\bЛ[её]в[аеу]?\b"
            r"|Pogodin|alldanceallsing"
        ),
        "часовой пояс конкретного города": r"\b(?:Europe|Asia|America|Africa|Australia)/[A-Za-z_]+",
        "сетевой адрес": r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d{2,5})?\b",
        "чужой сайт": r"https?://(?!api\.telegram\.org|api\.assemblyai\.com|api\.groq\.com"
                      r"|zvukogram\.com|bugs\.telegram\.org|0\.0\.0\.0)[A-Za-z0-9.-]+",
    }
    # Слова, которые выглядят как @ник, но им не являются: декораторы питона и
    # места, где «@username» написано как пример формата, а не чей-то ник.
    NOT_A_NICK = {"@dataclass", "@property", "@staticmethod", "@classmethod",
                  "@username", "@mentioned", "@имя", "@ник", "@bot", "@owner"}
    # Адреса, которые не принадлежат человеку: «слушать на всех интерфейсах» и
    # петля на себя. Их вписывать в профиль незачем.
    NOT_AN_ADDRESS = {"0.0.0.0"}
    EXEMPT_FILES = {"botprofile.py"}

    def test_no_hardcoded_identity(self):
        found = []
        for name, src in harness.CORE_SRC.items():
            if name in self.EXEMPT_FILES:
                continue
            for label, pattern in self.RULES.items():
                for m in re.finditer(pattern, src):
                    hit = m.group(0)
                    if hit in self.NOT_A_NICK or hit.split(":")[0] in self.NOT_AN_ADDRESS:
                        continue
                    line_no = src[:m.start()].count("\n") + 1
                    line = src.splitlines()[line_no - 1]
                    if line.lstrip().startswith("@"):      # декоратор
                        continue
                    found.append(f"{name}:{line_no}: {label}: {hit!r}")
        self.assertEqual(found, [], "вшитые личные значения в ядре:\n" + "\n".join(found))

    def test_git_target_comes_from_profile(self):
        """Ветка и склад — поля профиля, а не «origin/main» в коде.

        До 26.08.2026 обе величины стояли намертво в шести местах, и бот с
        веткой master каждый ход писал в журнал «origin/main - not something
        we can merge». Ловим возврат хардкода: в аргументах git его быть не
        должно (в комментариях — можно, там это объяснение).
        """
        bad = []
        for name, src in harness.CORE_SRC.items():
            for line_no, line in enumerate(src.splitlines(), 1):
                code = line.split("#", 1)[0]
                if re.search(r'"origin/main"|"origin",\s*"main"', code):
                    bad.append(f"{name}:{line_no}: {line.strip()}")
        self.assertEqual(bad, [], "ветка/склад вшиты в код вместо профиля:\n" + "\n".join(bad))

    # ------------------------------------------------------------------
    # Автоопределение адреса синхронизации (спека 26.08.2026, §2).
    # Профиль — явная воля, она старше. Пусто — спрашиваем сам git.
    def _make_repo(self, branch, remote_name):
        """Одноразовое хранилище с заданной веткой и складом."""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        run = lambda *a: subprocess.run(["git", "-C", d, *a], check=True, capture_output=True)
        run("init", "-q", "-b", branch)
        run("remote", "add", remote_name, "https://example.invalid/x.git")
        Path(d, "f.md").write_text("x", encoding="utf-8")
        run("add", "-A")
        run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
        return d

    def test_sync_target_detected_when_profile_silent(self):
        """Ветка master и склад origin определяются сами."""
        import botprofile
        d = self._make_repo("master", "origin")
        self.assertEqual(botprofile._git_remote_of(d), "origin")
        self.assertEqual(botprofile._git_branch_of(d), "master")

    def test_sync_target_prefers_lab_over_origin(self):
        """Где есть исторический `lab` — берём его, а не origin."""
        import botprofile
        d = self._make_repo("main", "lab")
        subprocess.run(["git", "-C", d, "remote", "add", "origin",
                        "https://example.invalid/y.git"], check=True, capture_output=True)
        self.assertEqual(botprofile._git_remote_of(d), "lab")

    def test_sync_target_empty_when_nothing_to_sync(self):
        """Не репозиторий вовсе — пустые значения, а не выдумка."""
        import botprofile
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        self.assertEqual(botprofile._git_remote_of(d), "")
        self.assertEqual(botprofile._git_branch_of(d), "")

    def test_profile_value_beats_detection(self):
        """Прописанное в json старше того, что нашлось в хранилище."""
        import botprofile
        d = self._make_repo("main", "origin")
        p = botprofile.build_profile({
            "owner_name": "Тест", "owner_uid": 1, "bot_username": "testbot",
            "tz_offset_hours": 3, "tz_label": "Москва",
            "home": d, "vault": d,
            "features": {"git_sync": True},
            "git_remote": "codeberg", "git_branch": "master",
        }, user="test", source="<тест>")
        self.assertEqual((p["git_remote"], p["git_branch"]), ("codeberg", "master"))

    def test_pre_turn_pull_guards_and_rescues(self):
        """Подтягивание перед ходом: откат застрявшего слияния и спасательный
        коммит — оба до fetch, иначе несохранённое затопчет слиянием."""
        src = harness.CORE_SRC["turn.py"]
        head = src.split('"fetch", _rem, _br')[0]
        self.assertIn("MERGE_HEAD", head, "нет проверки застрявшего слияния до fetch")
        self.assertIn("rescue:", head, "нет спасательного коммита до fetch")

    def test_built_prompt_never_names_a_stranger(self):
        """Поведенческая проверка вдобавок к грепу: собранный промпт нового
        хозяина не должен называть ни одного человека из жизни Хозяина."""
        import botprofile
        bare = botprofile.build_profile({
            "owner_name": "Beta", "owner_uid": 1, "bot_username": "nastyabot",
            "tz_offset_hours": 3, "tz_label": "Москва",
            "home": "/tmp/beta", "vault": "/tmp/beta/vault",
        }, user="beta", source="<тест>")
        text = tg.build_system_prompt(bare, {})
        for name in ("Owner", "Хозяин", "Alpha", "Alpha", "Лёве", "Beta",
                     "Gamma", "Pogodinalexeev", "alldanceallsing"):
            with self.subTest(name):
                self.assertNotIn(name, text)

    def test_all_blocks_on_still_name_nobody(self):
        """Прошлая проверка щадящая: у голого профиля выключено почти всё, и
        блоки про календарь и отправку сообщений в промпт даже не попадают —
        а именно в них до 19.08 сидели `Europe/Moscow` и «напиши Alpha».
        Здесь включаем ВСЁ и смотрим, что чужих имён по-прежнему нет, пояс
        посчитан от сдвига, а вместо друзей стоит нейтральное «<имя>»."""
        import botprofile
        bare = botprofile.build_profile({
            "owner_name": "Beta", "owner_uid": 1, "bot_username": "nastyabot",
            "tz_offset_hours": 3, "tz_label": "Москва",
            "home": "/tmp/beta", "vault": "/tmp/beta/vault",
            "features": {k: True for k in botprofile.DEFAULT_FEATURES},
        }, user="beta", source="<тест>")
        self.assertEqual(bare["tz_iana"], "Etc/GMT-3")
        text = tg.build_system_prompt(bare, {})
        for name in ("Alpha", "Лёве", "Beta", "Europe/Moscow", "Owner", "Хозяин"):
            with self.subTest(name):
                self.assertNotIn(name, text)
        self.assertIn("напиши <имя> привет", text)
        self.assertIn("timeZone=\"Etc/GMT-3\"", text)

    def test_owner_keeps_his_own_words(self):
        """Обратная сторона: у Хозяина подстановки обязаны давать ровно прежний
        текст. Слепок это ловит целиком, но при разборе поломки полезно видеть
        именно эти три строки."""
        text = tg.SYSTEM_PROMPT
        self.assertIn("напиши Alpha привет", text)
        self.assertIn("timezone `Europe/Moscow`", text)
        self.assertIn("контейнер tg-local-api", text)

    @unittest.skipUnless((_SANDBOX / ".git").exists(), "тест для песочницы")
    def test_only_profile_and_deploy_carry_identity(self):
        """То же самое, но по всему репозиторию: где ещё лежат хозяйские
        строки. Разрешены только заготовки выката, тесты и слепки."""
        r = subprocess.run(
            ["git", "grep", "-l", "-e", "/home/owner", "-e", "100000001",
             "-e", "owner_assistant_bot", "-e", "alldanceallsing"],
            cwd=str(_SANDBOX), capture_output=True, text=True)
        hits = {p for p in r.stdout.split() if p}
        allowed_prefixes = ("deploy/", "inventory/", "tests/golden/", "tests/",
                            "build/")
        stray = {p for p in hits if not p.startswith(allowed_prefixes)}
        self.assertEqual(stray, set(), f"личные данные в файлах ядра: {stray}")


class T9StandMap(unittest.TestCase):
    """Карта стенда (ARCHITECTURE.md) и пакет синхронизации — спека 26.08.2026,
    §4 и §5. Промпт обязан ссылаться на карту, когда она есть, и молчать про
    неё, когда её нет: обещание несуществующего файла — та самая болезнь,
    которую этот блок и лечит."""

    def _profile(self, **over):
        import botprofile
        base = {
            "owner_name": "Тест", "owner_uid": 1, "bot_username": "testbot",
            "tz_offset_hours": 3, "tz_label": "Москва",
            "home": "/tmp/t", "vault": "/tmp/t/vault",
        }
        base.update(over)
        return botprofile.build_profile(base, user="test", source="<тест>")

    def test_selfconf_has_no_dangling_pointer(self):
        """«см. ниже» указывает на блок прав, а тот выходит только при
        включённой правке своего кода. Выключено — указатель висит в пустоту,
        и бот честно ищет ниже то, чего там нет. Поймал сам бот на аудите
        переезда 26.08.2026."""
        import botprofile
        base = {
            "owner_name": "Тест", "owner_uid": 1, "bot_username": "testbot",
            "tz_offset_hours": 3, "tz_label": "Москва",
            "home": "/tmp/t", "vault": "/tmp/t/vault",
        }
        off = botprofile.build_profile({**base, "features": {"self_edit": False}},
                                       user="test", source="<тест>")
        text = tg.build_system_prompt(off, {})
        self.assertNotIn("нельзя, см. ниже", text)
        self.assertIn("не твоими руками", text)
        on = botprofile.build_profile({**base, "features": {"self_edit": True}},
                                      user="test", source="<тест>")
        self.assertIn("нельзя, см. ниже", tg.build_system_prompt(on, {}))

    def test_root_promise_matches_reality(self):
        """Промпт не имеет права обещать sudo там, где его нет.

        До 26.08.2026 блок прав намертво говорил «полный sudo NOPASSWD» и
        «sudo systemctl restart» — а с 25.08 sudo из бота не работает вовсе
        (NoNewPrivileges, запрет уровня ядра ОС). Бот строил на этом планы и
        обещал хозяину то, чего не может.
        """
        import botprofile
        base = {
            "owner_name": "Тест", "owner_uid": 1, "bot_username": "testbot",
            "tz_offset_hours": 3, "tz_label": "Москва",
            "home": "/tmp/t", "vault": "/tmp/t/vault",
            "features": {"self_edit": True},
        }
        cases = {
            "docker": ("NoNewPrivileges", ("sudo systemctl restart", "NOPASSWD")),
            "none":   ("просьба к хозяину", ("sudo systemctl restart", "NOPASSWD")),
            "sudo":   ("sudo systemctl restart", ("NOPASSWD",)),
        }
        for method, (must, must_not) in cases.items():
            with self.subTest(method):
                p = botprofile.build_profile({**base, "root_method": method},
                                             user="test", source="<тест>")
                text = tg.build_system_prompt(p, {})
                self.assertIn(must, text)
                for bad in must_not:
                    self.assertNotIn(bad, text)

    def test_target_modules_are_not_shadowed(self):
        """Проверяем ту сборку, на которую целимся, — а не соседнюю.

        botctx подставляет в путь поиска `resident_module_dir` из профиля;
        после переезда ядра в /opt песочные тесты молча стали импортировать
        живой код. Зелёный тест при этом ничего не значил. Сторож ловит
        возврат: любой модуль ядра не из целевой папки — брак.
        """
        self.assertEqual(harness.stray_modules(), {})

    def test_doc_linked_when_present(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        doc = Path(d, "ARCHITECTURE.md")
        doc.write_text("# карта", encoding="utf-8")
        text = tg.build_system_prompt(self._profile(architecture_doc=str(doc)), {})
        self.assertIn(str(doc), text)
        self.assertIn("УСТРОЙСТВО СТЕНДА", text)

    def test_doc_silent_when_missing(self):
        text = tg.build_system_prompt(
            self._profile(architecture_doc="/nope/ARCHITECTURE.md"), {})
        self.assertNotIn("/nope/ARCHITECTURE.md", text)
        self.assertNotIn("УСТРОЙСТВО СТЕНДА", text)

    def test_doc_covers_required_sections(self):
        """Семь обязательных разделов карты. Раздел выпал — карта врёт молчанием."""
        doc = _SANDBOX / "ARCHITECTURE.md"
        self.assertTrue(doc.exists(), "нет карты стенда рядом с ядром")
        text = doc.read_text(encoding="utf-8")
        for need in ("Три слоя", "Четыре корня", "Розетки", "Синхронизация",
                     "Чего у тебя нет", "Как править себя", "Куда смотреть"):
            with self.subTest(need):
                self.assertIn(need, text)

    # ------------------------------------------------------------------
    def _hooks(self):
        return _SANDBOX / "deploy" / "sync-hooks"

    def test_sync_package_is_four_files(self):
        """Состав пакета задан явно: всё, что не в списке, — чужое имущество."""
        want = {"_sync-env.sh", "sync-pull.sh", "sync-commit.sh", "sync-flush.sh"}
        have = {p.name for p in self._hooks().glob("*")}
        self.assertEqual(have, want)

    def test_sync_package_syntax_is_valid(self):
        for f in sorted(self._hooks().glob("*.sh")):
            with self.subTest(f.name):
                r = subprocess.run(["bash", "-n", str(f)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)

    def test_sync_package_has_no_hardcoded_stand(self):
        """Правила 5 и 6 контракта: адрес только из _sync-env.sh, HOME не зашит.

        Имена складов допустимы ровно в одном месте — в переборе автоопределения
        внутри _sync-env.sh. В остальных трёх файлах их быть не должно.
        """
        bad = []
        for f in sorted(self._hooks().glob("sync-*.sh")):
            for line_no, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                code = line.split("#", 1)[0]
                if re.search(r'\b(lab|origin)/(main|master)\b|"(lab|origin)"', code):
                    bad.append(f"{f.name}:{line_no}: {line.strip()}")
                if re.search(r'HOME=/home/', code):
                    bad.append(f"{f.name}:{line_no}: зашитый HOME: {line.strip()}")
        self.assertEqual(bad, [], "стенд вшит в пакет синхронизации:\n" + "\n".join(bad))

    def test_sync_package_never_rebases(self):
        """Правило 2: перебазирования в пакете нет ни в каком виде."""
        for f in sorted(self._hooks().glob("*.sh")):
            code = "\n".join(l.split("#", 1)[0] for l in
                             f.read_text(encoding="utf-8").splitlines())
            with self.subTest(f.name):
                self.assertNotIn("git rebase", code)
                self.assertNotIn("--rebase", code)

    def test_sync_package_exits_quietly(self):
        """Правило 1: крюк не блокирует человека — ненулевого выхода в файлах нет."""
        for f in sorted(self._hooks().glob("sync-*.sh")):
            code = "\n".join(l.split("#", 1)[0] for l in
                             f.read_text(encoding="utf-8").splitlines())
            with self.subTest(f.name):
                self.assertNotIn("exit 1", code)


class T10Extensions(unittest.TestCase):
    """Розетка для своего кода (botext.py). Заведена 26.08.2026, когда сверка
    форка перед онбордингом нашла у хозяина свой обработчик, которого в ядре
    нет: переезд на общее ядро молча отнял бы у него функцию.

    Главное свойство — расширение не может уронить ход. Всё остальное вторично.
    """

    def setUp(self):
        import botext
        self.botext = botext
        botext._CACHE = None
        self.addCleanup(setattr, botext, "_CACHE", None)
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.ext = Path(self.d, ".claude", "bot-ext")
        self.ext.mkdir(parents=True)

    def _ctx(self, **over):
        import botprofile
        p = botprofile.build_profile({
            "owner_name": "Тест", "owner_uid": 1, "bot_username": "testbot",
            "tz_offset_hours": 3, "tz_label": "Москва",
            "home": self.d, "vault": self.d,
        }, user="test", source="<тест>")
        ctx = {"uid": 1, "chat_id": 1, "source": "tg", "profile": p,
               "state_dir": str(Path(self.d, "state"))}
        ctx.update(over)
        return ctx

    def _write(self, name, body):
        Path(self.ext, name).write_text(body, encoding="utf-8")

    def test_no_folder_no_change(self):
        shutil.rmtree(self.ext)
        self.assertEqual(
            self.botext.apply_point("on_prompt", "текст", self._ctx()), "текст")

    def test_extension_changes_prompt(self):
        self._write("a.py", "def on_prompt(text, ctx):\n    return '[метка] ' + text\n")
        self.assertEqual(
            self.botext.apply_point("on_prompt", "текст", self._ctx()), "[метка] текст")

    def test_chain_is_ordered_by_filename(self):
        self._write("1_first.py", "def on_prompt(t, c):\n    return t + 'A'\n")
        self._write("2_second.py", "def on_prompt(t, c):\n    return t + 'B'\n")
        self.assertEqual(
            self.botext.apply_point("on_prompt", "", self._ctx()), "AB")

    def test_broken_extension_never_breaks_the_turn(self):
        """Падение в чужом коде не имеет права стоить хозяину хода."""
        self._write("1_bad.py", "def on_prompt(t, c):\n    raise RuntimeError('бум')\n")
        self._write("2_good.py", "def on_prompt(t, c):\n    return t + '!'\n")
        self.assertEqual(
            self.botext.apply_point("on_prompt", "текст", self._ctx()), "текст!")

    def test_broken_import_is_skipped(self):
        self._write("bad.py", "это не питон вообще ((((\n")
        self._write("good.py", "def on_reply(t, c):\n    return t + '?'\n")
        self.assertEqual(
            self.botext.apply_point("on_reply", "ответ", self._ctx()), "ответ?")

    def test_non_string_return_is_ignored(self):
        self._write("a.py", "def on_prompt(t, c):\n    return 42\n")
        self.assertEqual(
            self.botext.apply_point("on_prompt", "текст", self._ctx()), "текст")

    def test_underscore_files_are_helpers_not_extensions(self):
        self._write("_lib.py", "def on_prompt(t, c):\n    return 'НЕ ДОЛЖНО'\n")
        self.assertEqual(
            self.botext.apply_point("on_prompt", "текст", self._ctx()), "текст")

    def test_unknown_point_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            self.botext.apply_point("on_whatever", "текст", self._ctx())

    def test_core_calls_both_points(self):
        """Обе точки обязаны быть подключены в ходе, а не лежать мёртвыми."""
        src = harness.CORE_SRC["turn.py"]
        for point in ("on_prompt", "on_reply"):
            with self.subTest(point):
                self.assertIn(f'apply_point("{point}"', src)



class T11ResidentReaper(unittest.TestCase):
    """Жнец резидентов (27.08.2026). До него процесс `claude` в чате жил до
    перезапуска юнита: сессия сама не истекает, а память по ходу растёт.

    Проверяем ровно четыре вещи: простоявшего дольше порога закрываем,
    свежего не трогаем, чат посреди хода не трогаем ВООБЩЕ, и порог ≤ 0
    выключает жнеца целиком.
    """

    class FakeResident:
        """Дублёр ResidentClaude: считает, сколько раз его закрыли."""

        def __init__(self):
            self.closed = 0

        def close(self, wait=5.0):
            self.closed += 1

        def is_alive(self):
            return self.closed == 0

    def setUp(self):
        import botctx
        import claude_run
        self.cr = claude_run
        self.ctx = botctx
        # Словари ядра общие на процесс — чистим за собой, иначе тесты
        # начнут видеть чужих резидентов.
        for d in (botctx._RESIDENT, botctx._RESIDENT_SEEN, botctx._TURN_LOCKS):
            self.addCleanup(d.clear)
            d.clear()

    def _put(self, chat_id, seen_ago=None):
        rc = self.FakeResident()
        self.ctx._RESIDENT[chat_id] = rc
        if seen_ago is not None:
            self.ctx._RESIDENT_SEEN[chat_id] = time.time() - seen_ago
        return rc

    def test_idle_longer_than_threshold_is_closed(self):
        rc = self._put(1, seen_ago=700)
        self.assertEqual(self.cr._reap_idle_residents(600), [1])
        self.assertEqual(rc.closed, 1)
        self.assertNotIn(1, self.ctx._RESIDENT)
        self.assertNotIn(1, self.ctx._RESIDENT_SEEN)

    def test_fresh_resident_survives(self):
        """Ответил девять минут назад при пороге десять — процесс прогрет."""
        rc = self._put(2, seen_ago=540)
        self.assertEqual(self.cr._reap_idle_residents(600), [])
        self.assertEqual(rc.closed, 0)
        self.assertIn(2, self.ctx._RESIDENT)

    def test_turn_in_progress_is_never_touched(self):
        """Замок хода занят — значит бот сейчас отвечает. Такого не трогаем
        даже при огромном простое: убить резидента посреди ответа хуже, чем
        подождать минуту до следующего обхода."""
        rc = self._put(3, seen_ago=99999)
        lock = self.ctx._get_turn_lock(3)
        self.assertTrue(lock.acquire(blocking=False))
        self.addCleanup(lock.release)
        self.assertEqual(self.cr._reap_idle_residents(600), [])
        self.assertEqual(rc.closed, 0)
        self.assertIn(3, self.ctx._RESIDENT)

    def test_unseen_resident_gets_a_clock_not_a_knife(self):
        """Резидент есть, отметки нет (ход ещё идёт или бот только поднялся):
        первый обход заводит отсчёт, а не рубит вслепую."""
        rc = self._put(4)
        self.assertEqual(self.cr._reap_idle_residents(600), [])
        self.assertEqual(rc.closed, 0)
        self.assertIn(4, self.ctx._RESIDENT_SEEN)

    def test_lock_is_released_after_reaping(self):
        """Жнец берёт замок хода и обязан его вернуть — иначе следующий ход
        в этом чате встанет намертво."""
        self._put(5, seen_ago=700)
        self.cr._reap_idle_residents(600)
        lock = self.ctx._get_turn_lock(5)
        self.assertTrue(lock.acquire(blocking=False), "жнец не вернул замок хода")
        lock.release()

    def test_zero_threshold_disables_the_reaper(self):
        self.assertIsNone(self.cr.start_resident_reaper(idle_sec=0))

    def test_threshold_comes_from_profile(self):
        """Порог живёт в профиле, а не в коде: у каждого стенда свой."""
        import botprofile
        prof = botprofile.build_profile({
            "owner_name": "Тест", "owner_uid": 1, "bot_username": "testbot",
            "tz_offset_hours": 3, "tz_label": "Москва",
            "home": "/tmp", "vault": "/tmp",
        }, user="test", source="<тест>")
        self.assertEqual(prof["resident_idle_sec"], 600)

    def test_reaper_is_wired_into_boot(self):
        """Функция, которую никто не зовёт, не существует."""
        self.assertIn("start_resident_reaper()", harness.CORE_SRC["tg-bot.py"])

if __name__ == "__main__":
    unittest.main()
