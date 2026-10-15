"""pult.py — пульт бота: движок, модель и усилие кнопками (08.10.2026).

Слово хозяина стенда 08.10.2026 17:23: «нужен пульт с выбором модели… Это будет общее
ядро… У троих есть выбор, у остальных только Опус. Но можно выбрать модель, усилия.
Делай пульт».

Как устроено:
- Команда `/model` (она же `/pult`) присылает сообщение с кнопками. Тап правит выбор
  и перерисовывает то же сообщение, нового хода модели не заводит.
- Движок: строка «Клод / Кодекс» есть только там, где лежит переходник Кодекса
  (`engine.codex_available()`). У остальных ботов её нет, движок всегда Клод.
- Модель и усилие пишутся в тот же файл, откуда ядро их и так читает:
  у Клода `settings_json` профиля, у Кодекса `bot-codex/model.json`
  (`engine.bin_and_settings`). Остальные поля файла (крюки, followup) не трогаются.
- Применяется со следующего сообщения: `claude_run` видит, что у живого резидента
  модель или усилие не те, гасит его в начале хода и поднимает новый с той же беседой.
"""
import hashlib
import hmac
import json
import os
import time
import urllib.parse
from pathlib import Path

import engine
import meters
from botctx import log

CLAUDE_MODELS = [("claude-opus-5-5", "Opus 5.5"), ("claude-sonnet-5-5", "Sonnet 5.5")]
CODEX_MODELS = [("gpt-6-astra", "GPT-6 Astra")]
EFFORTS = {
    "claude": ["low", "medium", "high", "xhigh", "max"],
    "codex": ["low", "medium", "high"],
}
EFFORT_RU = {"low": "низкое", "medium": "среднее", "high": "высокое",
             "xhigh": "очень высокое", "max": "максимум"}
ENGINE_RU = {"claude": "Клод", "codex": "Кодекс"}
COMMANDS = ("/model", "/pult")


def models(eng):
    return CODEX_MODELS if eng == "codex" else CLAUDE_MODELS


def _path(eng):
    return Path(engine.bin_and_settings(eng)[1])


def _read(path):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return {}


def _write(path, cfg):
    path = Path(path)
    tmp = path.with_name(path.name + ".pult-tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
    try:
        os.chmod(tmp, path.stat().st_mode & 0o777)
    except FileNotFoundError:
        pass
    tmp.replace(path)


def state():
    """(движок, модель, усилие) как их сейчас увидит следующий ход."""
    eng = engine.stored()
    cfg = _read(_path(eng))
    return eng, cfg.get("model") or "", cfg.get("effort") or "medium"


def model_label(eng, model):
    base = (model or "").split("[", 1)[0]
    for mid, name in models(eng):
        if base == mid:
            return name
    return model or "по умолчанию"


def render():
    """Текст и клавиатура пульта."""
    eng, model, effort = state()
    text = (f"🎛 Пульт\n\n"
            f"Движок: {ENGINE_RU[eng]}\n"
            f"Модель: {model_label(eng, model)}\n"
            f"Усилие: {EFFORT_RU.get(effort, effort)}\n\n"
            f"Меняется со следующего сообщения, беседа сохраняется.")
    rows = []
    if engine.codex_available():
        rows.append([{"text": ("✓ " if eng == e else "") + ENGINE_RU[e],
                      "callback_data": f"pult:e:{e}"} for e in ("claude", "codex")])
    base = (model or "").split("[", 1)[0]
    ms = models(eng)
    if len(ms) > 1:
        rows.append([{"text": ("✓ " if base == mid else "") + name,
                      "callback_data": f"pult:m:{i}"} for i, (mid, name) in enumerate(ms)])
    effs = EFFORTS[eng]
    btns = [{"text": ("✓ " if effort == f else "") + EFFORT_RU[f],
             "callback_data": f"pult:f:{f}"} for f in effs]
    rows.extend(btns[i:i + 3] for i in range(0, len(btns), 3))
    rows.append([{"text": "✖ закрыть", "callback_data": "pult:x"}])
    return text, {"inline_keyboard": rows}


def apply(data):
    """Разобрать `pult:...` и записать выбор. Возвращает True, если пульт надо
    перерисовать, False, если его закрыли. Чужое и битое молча пропускает."""
    parts = (data or "").split(":")
    if len(parts) < 2 or parts[0] != "pult":
        return True
    kind, val = parts[1], (parts[2] if len(parts) > 2 else "")
    if kind == "x":
        return False
    if kind == "e":
        if val in ENGINE_RU and engine.codex_available() and engine.stored() != val:
            engine.set_engine(val, reason="pult")
        return True
    eng = engine.stored()
    path = _path(eng)
    cfg = _read(path)
    if kind == "m":
        ms = models(eng)
        if not val.isdigit() or int(val) >= len(ms):
            return True
        new = ms[int(val)][0]
        old = cfg.get("model") or ""
        # Хвост вроде «[1m]» (большое окно) при смене модели переносим как был.
        tail = old[len(old.split("[", 1)[0]):] if eng == "claude" else ""
        cfg["model"] = new + tail
    elif kind == "f":
        if val not in EFFORTS[eng]:
            return True
        cfg["effort"] = val
    else:
        return True
    _write(path, cfg)
    log(f"pult: {eng} {kind}={val} → {path}")
    return True


# --- Мини-приложение (08.10.2026, слово хозяина стенда 17:27: «супер отдельная кнопка, которая
# открывает мини-ап и никаких слэш-команд») ---
# Кнопка у поля ввода (setChatMenuButton, тип web_app) открывает PAGE_HTML. Страница шлёт
# на `api` строку initData, которую ей выдал сам Telegram, и действие в том же виде, что
# у кнопок сообщения («pult:f:high»). Подпись initData проверяется токеном бота, так что
# подделать вход без токена нельзя; дальше ядро пускает только тех, кто в allowlist.
INIT_DATA_MAX_AGE = 24 * 3600


def check_init_data(init_data, token, max_age=INIT_DATA_MAX_AGE, now=None):
    """Пользователь из initData мини-приложения, если подпись Telegram верна и не
    протухла. Иначе None. Алгоритм из документации Telegram (WebAppData)."""
    try:
        pairs = urllib.parse.parse_qsl(init_data or "", keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return None
    data = dict(pairs)
    got = data.pop("hash", "")
    if not got or not token:
        return None
    check = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    want = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(want, got):
        return None
    try:
        if (now or time.time()) - int(data.get("auth_date", "0")) > max_age:
            return None
        user = json.loads(data.get("user") or "{}")
    except (ValueError, TypeError):
        return None
    return user if isinstance(user, dict) and isinstance(user.get("id"), int) else None


def _bar(used):
    used = max(0.0, min(1.0, float(used or 0)))
    return round(used * 100)


def meters_view(chat_id):
    """Полоски для страницы: [{title, pct, note}], сначала расход, потом контекст."""
    snap = meters.snapshot(chat_id, codex=engine.codex_available())
    rows = []
    c = snap.get("claude") or {}
    for key, title in (("five_hour", "Клод, 5 часов"), ("seven_day", "Клод, неделя")):
        w = c.get(key)
        if w:
            rows.append({"group": "Расход", "title": title, "pct": _bar(w.get("used")),
                         "resets": w.get("resets") or 0})
    cx = snap.get("codex")
    if cx and cx.get("week"):
        w = cx["week"]
        rows.append({"group": "Расход", "title": "Кодекс, неделя", "pct": _bar(w.get("used")),
                     "resets": w.get("resets") or 0})
    ctx = snap.get("ctx")
    if ctx and ctx.get("tokens"):
        win = ctx.get("window") or 0
        rows.append({"group": "Контекст",
                     "title": f"Беседа, {ENGINE_RU.get(ctx.get('engine'), '')}".rstrip(", "),
                     "pct": _bar(ctx["tokens"] / win) if win else None,
                     "tokens": ctx["tokens"], "window": win})
    return rows


def webapp_state(chat_id=None):
    """Состояние для страницы: что выбрано и из чего выбирать."""
    eng, model, effort = state()
    base = (model or "").split("[", 1)[0]
    return {
        "meters": meters_view(chat_id),
        "engine": eng,
        "engines": ([[e, ENGINE_RU[e]] for e in ("claude", "codex")]
                    if engine.codex_available() else []),
        "model": base,
        "model_label": model_label(eng, model),
        "models": [[i, mid, name] for i, (mid, name) in enumerate(models(eng))],
        "effort": effort,
        "efforts": [[f, EFFORT_RU[f]] for f in EFFORTS[eng]],
    }


def webapp_api(body, token, allowed):
    """Обработка запроса страницы. body: dict {initData, action?}.
    Возвращает (код, ответ)."""
    user = check_init_data((body or {}).get("initData", ""), token)
    if user is None:
        return 401, {"ok": False, "error": "bad initData"}
    if user["id"] not in allowed:
        return 403, {"ok": False, "error": "not allowed"}
    action = (body or {}).get("action") or ""
    if action:
        if not isinstance(action, str) or not action.startswith("pult:") or action == "pult:x":
            return 400, {"ok": False, "error": "bad action"}
        apply(action)
        log(f"pult webapp: uid={user['id']} {action}")
    return 200, {"ok": True, "state": webapp_state(user["id"])}


PAGE_HTML = """<!doctype html>
<html lang="ru"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>Пульт бота: движок, модель, усилие</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
:root{--bg:var(--tg-theme-bg-color,#17212b);--fg:var(--tg-theme-text-color,#f5f5f5);
--hint:var(--tg-theme-hint-color,#8a9aa9);--sec:var(--tg-theme-secondary-bg-color,#232e3c);
--acc:var(--tg-theme-button-color,#5288c1);--accfg:var(--tg-theme-button-text-color,#fff)}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;padding:18px 16px 28px;background:var(--bg);color:var(--fg);
font:16px/1.35 -apple-system,system-ui,Segoe UI,Roboto,sans-serif}
h1{font-size:20px;margin:0 0 4px}.sub{color:var(--hint);font-size:14px;margin:0 0 18px}
.card{background:var(--sec);border-radius:14px;padding:14px;margin:0 0 14px}
.lbl{color:var(--hint);font-size:13px;text-transform:uppercase;letter-spacing:.04em;margin:0 0 10px}
.row{display:flex;flex-wrap:wrap;gap:8px}
button{flex:1 1 30%;min-height:46px;border:0;border-radius:11px;padding:10px 8px;
background:var(--bg);color:var(--fg);font:inherit;font-weight:500;cursor:pointer;transition:transform .08s}
button:active{transform:scale(.96)}button.on{background:var(--acc);color:var(--accfg)}
button:disabled{opacity:.5}
.note{color:var(--hint);font-size:13px;text-align:center;margin-top:6px}
.err{color:#e5534b;text-align:center;margin:20px 0}
.m{margin:0 0 12px}.m:last-child{margin:0}.mh{display:flex;justify-content:space-between;font-size:15px;margin:0 0 6px}
.mh b{font-weight:600}.mh span{color:var(--hint);font-size:13px}
.track{height:10px;border-radius:6px;background:var(--bg);overflow:hidden}
.fill{height:100%;border-radius:6px;background:var(--acc)}.fill.warn{background:#d9a03f}.fill.bad{background:#e5534b}
</style></head><body>
<h1>Пульт</h1><p class="sub" id="sum">загружаю…</p>
<div id="meters"></div>
<div id="ui"></div>
<p class="note">Меняется со следующего сообщения, беседа сохраняется.</p>
<script>
const tg = window.Telegram && Telegram.WebApp;
if (tg) { tg.ready(); tg.expand(); }
const ui = document.getElementById('ui'), sum = document.getElementById('sum');
let busy = false;
function card(title, items, cur, mk) {
  if (!items || items.length < 2) return '';
  return '<div class="card"><div class="lbl">' + title + '</div><div class="row">' +
    items.map(it => { const [act, val, name] = mk(it);
      return '<button data-a="' + act + '"' + (val === cur ? ' class="on"' : '') + '>' + name + '</button>';
    }).join('') + '</div></div>';
}
function fmtTok(n) { return n >= 1e6 ? (Math.round(n / 1e5) / 10) + 'M' : (n >= 1000 ? Math.round(n / 1000) + 'k' : String(n)); }
function fmtReset(t) {
  if (!t) return '';
  const d = new Date(t * 1000), now = new Date();
  const hm = d.toLocaleTimeString('ru-RU', {hour: '2-digit', minute: '2-digit'});
  const same = d.toDateString() === now.toDateString();
  return 'сброс ' + (same ? hm : d.toLocaleDateString('ru-RU', {day: '2-digit', month: '2-digit'}) + ' ' + hm);
}
function meters(rows) {
  const box = document.getElementById('meters');
  if (!rows || !rows.length) { box.innerHTML = ''; return; }
  const groups = {};
  rows.forEach(r => (groups[r.group] = groups[r.group] || []).push(r));
  box.innerHTML = Object.keys(groups).map(g => '<div class="card"><div class="lbl">' + g + '</div>' +
    groups[g].map(r => {
      const pct = r.pct;
      const right = r.tokens != null
        ? fmtTok(r.tokens) + (r.window ? ' из ' + fmtTok(r.window) : '')
        : (pct + '%' + (r.resets ? ' · ' + fmtReset(r.resets) : ''));
      const cls = pct == null ? '' : (pct >= 85 ? ' bad' : (pct >= 60 ? ' warn' : ''));
      return '<div class="m"><div class="mh"><b>' + r.title + '</b><span>' + right + '</span></div>' +
        (pct == null ? '' : '<div class="track"><div class="fill' + cls + '" style="width:' + Math.max(pct, 2) + '%"></div></div>') +
        '</div>';
    }).join('') + '</div>').join('');
}
function draw(s) {
  meters(s.meters);
  const eng = (s.engines.find(e => e[0] === s.engine) || [0, ''])[1];
  const eff = (s.efforts.find(e => e[0] === s.effort) || [0, s.effort])[1];
  sum.textContent = (eng ? eng + ', ' : '') + s.model_label + ', усилие ' + eff;
  ui.innerHTML =
    card('Движок', s.engines, s.engine, e => ['pult:e:' + e[0], e[0], e[1]]) +
    card('Модель', s.models, s.model, m => ['pult:m:' + m[0], m[1], m[2]]) +
    card('Усилие', s.efforts, s.effort, f => ['pult:f:' + f[0], f[0], f[1]]);
  ui.querySelectorAll('button').forEach(b => b.onclick = () => send(b.dataset.a));
}
async function send(action) {
  if (busy) return; busy = true;
  ui.querySelectorAll('button').forEach(b => b.disabled = true);
  try {
    const r = await fetch('api', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({initData: tg ? tg.initData : '', action: action || ''})});
    const j = await r.json();
    if (!j.ok) throw new Error(j.error || r.status);
    draw(j.state);
    if (action && tg && tg.HapticFeedback) tg.HapticFeedback.impactOccurred('light');
  } catch (e) {
    ui.innerHTML = '<p class="err">Не открылось: ' + e.message + '.<br>Открой пульт кнопкой в чате с ботом.</p>';
  } finally { busy = false; }
}
send('');
</script></body></html>
"""
