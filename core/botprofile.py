#!/usr/bin/env python3
"""Профиль бота — всё, что отличает одного хозяина от другого.

Ядро (tg-bot.py) одно на всех, профиль свой у каждого. Шаг 4 пересборки
(19.08.2026): значения переезжают из литералов в шапке кода в JSON-файл
`/etc/claude-tg-<пользователь>/profile.json`.

Три железных правила:

1. Имя пользователя берётся ТОЛЬКО из системы (`pwd.getpwuid(os.geteuid())`).
   Не из аргумента командной строки, не из сообщения, не из переменной. Иначе
   бот Beta сможет прочитать профиль Хозяина — а это чужой токен, чужой vault и
   чужой хозяин в групповом чате.
2. Лишнее поле в json → падение с именем поля (опечатка не должна тихо
   игнорироваться). Нет обязательного поля → падение с понятным текстом.
3. Всё остальное считается от четырёх корней: home, vault, cache_dir,
   secrets_dir. У нового человека профиль — полтора десятка строк.

Зависимостей нет вообще — только стандартная библиотека, потому что боты
крутятся на системном /usr/bin/python3 без своего окружения.
"""
import json
import os
import pwd
import shutil
import socket
import subprocess
import sys
from dataclasses import MISSING, dataclass, field, fields
from pathlib import Path

__all__ = ["Profile", "ProfileError", "load_profile", "current_user", "profile_path"]


class ProfileError(Exception):
    """Профиль не читается / не проходит проверку. Бот с таким не стартует."""


# Выключатели функций. Значение по умолчанию — «выключено» для всего, что
# требует отдельной настройки у хозяина (ключи, чужой Mac, права). Новый бот
# должен работать голым, а не падать на отсутствующем движке.
DEFAULT_FEATURES = {
    "git_sync": False,          # нет vault.git → pull/push молчат
    "rag": False,               # смысловой поиск по хранилищу заметок
    "voice_groq": False,
    "voice_assemblyai": False,
    "voice_native": False,
    "tts_edge": False,
    "tts_zvukogram": False,
    "calendar": False,
    "tg_send_userbot": False,   # отправка от лица хозяина через user-сессию
    "playwright": False,
    "mac_router": False,        # исполнитель на ноуте (очередь Авито/Озон)
    "chatlog": False,
    "self_edit": False,         # право править свой код и жать systemctl
}


@dataclass
class Profile:
    """Типизированный профиль. Обязательные поля — сверху, у них нет значения
    по умолчанию: не указал в json — процесс не стартует."""

    # --- идентичность (обязательные) ---
    owner_name: str
    owner_uid: int
    bot_username: str
    tz_offset_hours: int
    tz_label: str

    # --- корни (обязательные) ---
    home: str
    vault: str

    # --- как звать хозяина в тексте промпта ---
    # Ядро одно на всех, а обращается оно к конкретному человеку: «Owner тап —
    # тебе придёт подпись», «чтобы Owner увидел картинку». Раньше имя было
    # вписано в текст ядра латиницей и новому хозяину бот говорил чужое имя.
    # Три поля, потому что русский язык склоняется: именительный, родительный,
    # дательный. Не указаны — берётся owner_name во всех трёх (грамматически
    # шершаво, зато никогда не чужое имя).
    owner_alias: str = ""       # ⊘ owner_name  — «Owner», «Beta»
    owner_alias_gen: str = ""   # ⊘ owner_alias — «Owner'а», «Beta»
    owner_alias_dat: str = ""   # ⊘ owner_alias — «Owner'у», «Beta»

    # --- часовой пояс, необязательные украшения фразы про «завтра в 14» ---
    tz_short: str = ""          # «MSK»
    tz_city: str = ""           # «Москвы» (родительный падеж)
    # Пояс в том виде, в каком его понимает Google Calendar: «Europe/Moscow».
    # Не указан — считаем от сдвига («Etc/GMT-3» = UTC+3, знак у Etc/GMT
    # перевёрнут, это не опечатка). Дальше уезжает в блок промпта про календарь.
    tz_iana: str = ""

    # --- примеры «напиши такому-то» в блоке отправки сообщений ---
    # Ядро объясняет модели, как выглядит просьба отправить сообщение, и делает
    # это на живых примерах («напиши Alpha привет»). Имена друзей — личные
    # данные хозяина, поэтому список приходит из профиля. Пусто — в промпте
    # останется нейтральное «<имя>», никто чужой не назван.
    contact_examples: list = field(default_factory=list)

    # --- корни с производными значениями ---
    cache_dir: str = ""         # ⊘ home/.cache/claude-tg
    secrets_dir: str = ""       # ⊘ /etc/claude-tg-<пользователь>

    # --- производные пути ---
    claude_bin: str = ""        # ⊘ which("claude") или nvm внутри home
    resident_module_dir: str = ""   # ⊘ home (там лежит resident_claude.py)
    tg_send_oneshot: str = ""
    tg_send_python: str = ""
    edge_tts_bin: str = ""
    zvukogram_key_path: str = ""
    zvukogram_email_path: str = ""
    native_helper: str = ""
    settings_json: str = ""
    rag_search: str = ""
    rag_sock: str = ""
    chatlog_script: str = ""
    persona_path: str = ""      # ⊘ vault/Self/tg-persona.md
    self_code_path: str = "/opt/claude-tg/tg-bot.py"   # чем бот отвечает «где мой код»
    # Карта стенда для нового Клода: слои, четыре корня, розетки, синхронизация.
    # Промпт даёт на неё ССЫЛКУ, а не содержимое — читать по нужде, а не тащить
    # семь килобайт в каждый ход. Файл лежит рядом с ядром и правится вместе с
    # ним: устаревшая карта хуже отсутствующей, по ней уверенно идут не туда.
    architecture_doc: str = "/opt/claude-tg/ARCHITECTURE.md"
    # Папка своих обработчиков хозяина (см. botext.py). Пусто — считается от
    # хранилища: <vault>/.claude/bot-ext. Ядро зовёт оттуда on_prompt/on_reply.
    # Включателя нет сознательно: папка лежит в хранилище хозяина, её наличие
    # и есть его воля. Профиль в /etc ему на запись недоступен — гейт там
    # означал бы поход к root за каждой мелочью.
    ext_dir: str = ""           # ⊘ vault/.claude/bot-ext

    # Как этот стенд добывает права root — и добывает ли вообще. Уезжает в блок
    # промпта про права. До 26.08.2026 там намертво стояло обещание «полный
    # sudo NOPASSWD», которое перестало быть правдой 25.08: юнит поднят с
    # NoNewPrivileges=yes, это запрет уровня ядра ОС, пароль ни при чём.
    # Промпт врал каждому боту с включённой правкой своего кода.
    #   "docker" — через контейнер с --privileged (наш случай)
    #   "sudo"   — там, где sudoers действительно настроен
    #   "none"   — никак, и не надо обещать
    root_method: str = "docker"
    unit_name: str = ""         # ⊘ claude-tg-bot@<пользователь>

    # --- инфраструктура ---
    api_base: str = "http://127.0.0.1:8081"
    # Контейнер локального Bot API server: из него забираются файлы больше 20 МБ
    # (`docker exec <имя> cat <путь>`) и о нём же говорит блок промпта про лимиты.
    # На этом сервере он общий для всех ботов, поэтому значение по умолчанию есть.
    local_api_container: str = "tg-local-api"
    voice_port: object = None       # int или None (не поднимать сервер голоса)
    # Сколько секунд простоя терпит резидентный `claude` в чате, прежде чем жнец
    # (claude_run._reap_idle_residents) его закроет. Следующее сообщение поднимет
    # процесс заново с --resume: sid лежит в sessions.json, история цела.
    # 0 = не жать вовсе (процесс живёт до перезапуска юнита — так было до 27.08.2026,
    # и это стоило памяти: сессия сама не истекает, а память по ходу растёт).
    resident_idle_sec: int = 600
    router_unit: object = None      # имя юнита для метрики, None → не собирать
    xdg_runtime_dir: str = ""       # ⊘ /run/user/<uid системного пользователя>
    # Куда синхронизируется vault. Пусто = определить по самому хранилищу
    # (_git_remote_of / _git_branch_of). Заполнено в json = явная воля хозяина,
    # она старше. Раньше здесь стояло «origin/main» намертво в шести местах
    # кода, и бот с веткой master каждый ход писал в журнал
    # «origin/main - not something we can merge» (26.08.2026).
    git_remote: str = ""        # ⊘ lab или origin — что найдётся в хранилище
    git_branch: str = ""        # ⊘ текущая ветка хранилища

    # --- личные данные системного промпта ---
    calendar_id: object = None
    owner_second_account: object = None
    neighbor_bots: list = field(default_factory=list)   # [{"username","note"}]

    # --- выключатели ---
    features: dict = field(default_factory=lambda: dict(DEFAULT_FEATURES))

    # --- меню ---
    menu_projects: list = field(default_factory=list)       # [(slug, name, dir)]
    menu_categories: list = field(default_factory=list)     # [(slug, name, [kids])]
    menu_subprojects: dict = field(default_factory=dict)
    menu_hidden_at_top: set = field(default_factory=set)

    # --- служебное, в json не пишется ---
    user: str = ""
    source: str = ""

    # ------------------------------------------------------------------
    # Ядро обращается к профилю как к словарю (PROFILE["vault"]) — так было
    # на шаге 3, и менять 70 обращений ради синтаксиса точки смысла нет.
    def __getitem__(self, key):
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key) from None

    def get(self, key, default=None):
        return getattr(self, key, default)

    def feature(self, name):
        return bool(self.features.get(name))

    def as_dict(self):
        return {f.name: getattr(self, f.name) for f in fields(self)}

    # ------------------------------------------------------------------
    def __post_init__(self):
        home = self.home.rstrip("/")
        self.home = home
        self.vault = self.vault.rstrip("/")
        d = self._derive
        d("owner_alias", self.owner_name)
        d("owner_alias_gen", self.owner_alias)
        d("owner_alias_dat", self.owner_alias)
        d("cache_dir", f"{home}/.cache/claude-tg")
        d("secrets_dir", f"/etc/claude-tg-{self.user}" if self.user else "/etc/claude-tg")
        d("resident_module_dir", home)
        d("claude_bin", _find_claude(home))
        d("tg_send_oneshot", f"{home}/.local/bin/tg-send-oneshot.py")
        d("tg_send_python", f"{home}/telegram-mcp/.venv/bin/python")
        d("edge_tts_bin", f"{home}/edge-tts/.venv/bin/edge-tts")
        d("zvukogram_key_path", f"{home}/.zvukogram-key")
        d("zvukogram_email_path", f"{home}/.zvukogram-email")
        d("native_helper", f"{home}/transcribe_native.py")
        d("settings_json", f"{home}/.claude/settings.json")
        d("rag_search", f"{home}/vault-rag/search")
        d("rag_sock", f"{home}/vault-rag/daemon.sock")
        d("chatlog_script", f"{home}/vault-rag/chatlog.py")
        d("persona_path", f"{self.vault}/Self/tg-persona.md")
        d("unit_name", f"claude-tg-bot@{self.user}" if self.user else "claude-tg-bot")
        d("tz_iana", _iana_from_offset(self.tz_offset_hours))
        d("xdg_runtime_dir", f"/run/user/{_uid_of(self.user)}")
        # выключатели: неизвестный ключ — почти всегда опечатка в json
        merged = dict(DEFAULT_FEATURES)
        for key, value in (self.features or {}).items():
            if key not in DEFAULT_FEATURES:
                raise ProfileError(
                    f"неизвестный выключатель features.{key!r}; известные: "
                    + ", ".join(sorted(DEFAULT_FEATURES))
                )
            merged[key] = bool(value)
        self.features = merged
        # адрес синхронизации спрашиваем у самого хранилища — но только если
        # синхронизация вообще включена: незачем дёргать git на каждом старте
        # у бота, которому синхронизировать нечего.
        if merged.get("git_sync"):
            d("git_remote", _git_remote_of(self.vault))
            d("git_branch", _git_branch_of(self.vault))
        # меню: в json это списки объектов, коду удобнее кортежи
        self.menu_projects = [_menu_project(p) for p in self.menu_projects]
        self.menu_categories = [_menu_category(c) for c in self.menu_categories]
        self.menu_hidden_at_top = set(self.menu_hidden_at_top)
        self.menu_subprojects = dict(self.menu_subprojects)
        self.neighbor_bots = [_neighbor(n) for n in self.neighbor_bots]

    def _derive(self, name, value):
        if not getattr(self, name):
            setattr(self, name, value)

    # ------------------------------------------------------------------
    @property
    def token_file(self):
        return f"{self.secrets_dir}/token"

    @property
    def neighbor_usernames(self):
        return [n["username"] for n in self.neighbor_bots]

    @property
    def api_hostport(self):
        """«127.0.0.1:8081» — как это пишется прозой в системном промпте."""
        return self.api_base.split("://", 1)[-1].rstrip("/")

    # ------------------------------------------------------------------
    def validate(self, log=print):
        """Жёсткие провалы → исключение, бот не стартует. Мягкие → предупреждение
        в журнал и автоматическое выключение соответствующей функции.

        Падать на старте лучше, чем полдня писать в чужой vault."""
        hard = []
        if not Path(self.vault).is_dir():
            hard.append(f"vault не найден: {self.vault}")
        try:
            if not Path(self.token_file).read_text().strip():
                hard.append(f"токен пустой: {self.token_file}")
        except OSError as e:
            hard.append(f"токен не читается: {self.token_file} ({e.strerror})")
        if not (Path(self.claude_bin).is_file() and os.access(self.claude_bin, os.X_OK)):
            hard.append(f"claude не запускается: {self.claude_bin}")
        if hard:
            raise ProfileError("профиль не прошёл проверку:\n  - " + "\n  - ".join(hard))

        soft = []
        if self.voice_port is not None and _port_busy(int(self.voice_port)):
            soft.append(f"порт голоса {self.voice_port} занят — сервер голоса не поднимаем")
            self.voice_port = None
        for feat, path in (
            ("voice_assemblyai", f"{self.secrets_dir}/assemblyai-key"),
            ("voice_groq", f"{self.secrets_dir}/groq-key"),
            ("tts_edge", self.edge_tts_bin),
            ("tts_zvukogram", self.zvukogram_key_path),
            ("voice_native", self.native_helper),
            ("rag", self.rag_search),
            ("chatlog", self.chatlog_script),
        ):
            if self.features.get(feat) and not Path(path).exists():
                soft.append(f"{feat}: нет {path} — функцию выключаю")
                self.features[feat] = False
        if self.features.get("git_sync") and not Path(self.vault, ".git").exists():
            soft.append("git_sync: в vault нет .git — синхронизацию выключаю")
            self.features["git_sync"] = False
        if self.features.get("git_sync") and not (self.git_remote and self.git_branch):
            soft.append("git_sync: не нашёл склад или ветку — синхронизацию выключаю "
                        "(пропишите git_remote/git_branch в профиле)")
            self.features["git_sync"] = False
        for line in soft:
            log(f"profile warn: {line}")
        log(f"profile ok: {self.user or '?'} ({self.source})")
        return soft


# ----------------------------------------------------------------------
def _iana_from_offset(hours):
    """Сдвиг в часах → название пояса, понятное календарю. У зоны «Etc/GMT»
    знак перевёрнут по стандарту: UTC+3 это «Etc/GMT-3». Грубо, зато честно —
    красивое «Europe/Moscow» хозяин пишет в профиль руками."""
    hours = int(hours)
    if hours == 0:
        return "UTC"
    return f"Etc/GMT{-hours:+d}"


def _uid_of(user):
    try:
        return pwd.getpwnam(user).pw_uid
    except (KeyError, TypeError):
        return os.geteuid()


def _find_claude(home):
    """Claude Code ставится через nvm, версия у каждого своя. Берём старшую.
    Чужой /home закрыт правами — это нормально, тогда просто идём дальше."""
    try:
        nvm = sorted(Path(f"{home}/.nvm/versions/node").glob("*/bin/claude")) if home else []
    except OSError:
        nvm = []
    if nvm:
        return str(nvm[-1])
    return shutil.which("claude") or f"{home}/.local/bin/claude"


def _git_remote_of(vault):
    """Как называется склад, куда синхронизируется хранилище.

    Имена исторически разные: `lab` — голый склад на сервере, `origin` —
    всё остальное (внешний хостинг, локальный склад у новичка). Спрашиваем
    сам git, а не гадаем: зашитое имя три месяца молча роняло синхронизацию
    на чужой машине (найдено 26.08.2026, 792 записи в журнале ошибок)."""
    for candidate in ("lab", "origin"):
        r = subprocess.run(["git", "-C", str(vault), "remote", "get-url", candidate],
                           capture_output=True, timeout=5)
        if r.returncode == 0:
            return candidate
    return ""


def _git_branch_of(vault):
    """Текущая ветка хранилища. У кого-то main, у кого-то master —
    это свойство стенда, а не кода. Отсоединённая голова = синхронизировать
    нечего, отдаём пустую строку и функция выключится сама."""
    r = subprocess.run(["git", "-C", str(vault), "rev-parse", "--abbrev-ref", "HEAD"],
                       capture_output=True, timeout=5)
    branch = r.stdout.decode(errors="replace").strip() if r.returncode == 0 else ""
    return "" if branch in ("", "HEAD") else branch


def _port_busy(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", port))
        except OSError:
            return True
    return False


def _menu_project(p):
    if isinstance(p, dict):
        return (p["slug"], p["name"], p["dir"])
    return tuple(p)


def _menu_category(c):
    if isinstance(c, dict):
        return (c["slug"], c["name"], list(c["kids"]))
    slug, name, kids = c
    return (slug, name, list(kids))


def _neighbor(n):
    """Сосед по групповым чатам: @ник, пояснение кто это и пример обращения."""
    if isinstance(n, dict):
        return {"username": n["username"], "note": n.get("note", ""),
                "example": n.get("example", "")}
    return {"username": str(n), "note": "", "example": ""}


def current_user():
    """Кто мы для системы. Единственный источник имени — ядро ОС."""
    return pwd.getpwuid(os.geteuid()).pw_name


def profile_path(user=None):
    return Path(f"/etc/claude-tg-{user or current_user()}/profile.json")


def load_profile(log=print):
    """Читает профиль текущего пользователя. Нет файла — исключение: молча
    работать на чужих значениях по умолчанию нельзя.

    TGBOT_PROFILE=/путь — подмена файла для тестов и обкатки."""
    user = current_user()
    override = os.environ.get("TGBOT_PROFILE")
    if override:
        path = Path(override)
        log(f"PROFILE OVERRIDE {path}")
    else:
        path = profile_path(user)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ProfileError(
            f"нет файла профиля {path}. Положи его туда (образец — deploy/profile.example.json) "
            "или укажи TGBOT_PROFILE=/путь"
        ) from None
    except json.JSONDecodeError as e:
        raise ProfileError(f"профиль {path} — сломанный json: {e}") from None
    return build_profile(raw, user=user, source=str(path))


def build_profile(raw, user="", source="<dict>"):
    """Словарь → Profile, с проверкой имён и типов полей."""
    if not isinstance(raw, dict):
        raise ProfileError("профиль должен быть объектом json, а не " + type(raw).__name__)
    raw = {k: v for k, v in raw.items() if not k.startswith("_")}   # _комментарий
    known = {f.name for f in fields(Profile)} - {"user", "source"}
    for key in raw:
        if key not in known:
            raise ProfileError(
                f"неизвестное поле профиля {key!r} (опечатка?); известные: "
                + ", ".join(sorted(known))
            )
    required = [f.name for f in fields(Profile)
                if f.default is MISSING and f.default_factory is MISSING]
    missing = [name for name in required if name not in raw]
    if missing:
        raise ProfileError("в профиле нет обязательных полей: " + ", ".join(missing))
    _typecheck(raw)
    return Profile(user=user, source=source, **raw)


_TYPES = {
    "owner_uid": int, "tz_offset_hours": int,
    "owner_name": str, "owner_alias": str, "owner_alias_gen": str,
    "owner_alias_dat": str, "bot_username": str, "tz_label": str, "home": str, "vault": str,
    "tz_iana": str, "local_api_container": str,
    "git_remote": str, "git_branch": str,
    "architecture_doc": str,
    "ext_dir": str,
    "root_method": str,
    "features": dict, "menu_subprojects": dict,
    "neighbor_bots": list, "menu_projects": list, "menu_categories": list,
    "menu_hidden_at_top": list, "contact_examples": list,
}


def _typecheck(raw):
    for key, want in _TYPES.items():
        if key in raw and not isinstance(raw[key], want):
            raise ProfileError(
                f"поле {key!r}: ожидался {want.__name__}, пришёл {type(raw[key]).__name__}"
            )
    if "voice_port" in raw and raw["voice_port"] is not None and not isinstance(raw["voice_port"], int):
        raise ProfileError("поле 'voice_port': ожидалось число или null")


if __name__ == "__main__":   # ручная проверка: python3 botprofile.py
    try:
        p = load_profile()
        p.validate()
        print(json.dumps({k: (sorted(v) if isinstance(v, set) else v)
                          for k, v in p.as_dict().items()},
                         ensure_ascii=False, indent=2, default=str))
    except ProfileError as e:
        print(f"ПРОФИЛЬ НЕ ГОДЕН: {e}", file=sys.stderr)
        sys.exit(1)
