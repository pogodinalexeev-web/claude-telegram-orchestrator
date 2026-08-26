#!/usr/bin/env python3
"""Пересобрать дневной файл логов разговоров за СЕГОДНЯ из всех jsonl-сессий.
Идемпотентно: каждый вызов перезаписывает Resources/chat-logs/processed/<today>.md.
Без состояния, без дублей, переживает прерванные ходы.
Вызывается Stop-крюком после каждого ответа. Аргумент: дата YYYY-MM-DD (опц., по умолч. сегодня MSK)."""
import json, glob, re, os, sys, datetime

# Машинная специфика — в localcfg.py рядом с кодом (НЕ в git): где сессии, где vault,
# суффикс файла (-mac на Mac, пусто у бота), ярлык не-хозяйской реплики (Claude / бот).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import localcfg
PROJ = localcfg.SESSIONS_PROJ
OUT = os.path.join(localcfg.VAULT, "Resources/chat-logs/processed")
SUFFIX = localcfg.CHATLOG_SUFFIX
WHO = localcfg.CHATLOG_WHO
# Имя хозяина хранилища — для ярлыка его реплик. Дефолт Owner (каждый ставит своё).
OWNER = getattr(localcfg, "CHATLOG_OWNER", "Owner")


MSK = datetime.timezone(datetime.timedelta(hours=3))


def to_msk(ts):
    """UTC-timestamp из лога (…Z) → строка времени по Москве 'YYYY-MM-DDTHH:MM'."""
    try:
        dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.astimezone(MSK).strftime("%Y-%m-%dT%H:%M")
    except Exception:
        return ts[:16]


def clean(o):
    m = o.get("message"); role = m.get("role") if isinstance(m, dict) else o.get("type")
    txt = ""
    if isinstance(m, dict):
        c = m.get("content")
        if isinstance(c, str):
            txt = c
        elif isinstance(c, list):
            txt = " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    if not txt and isinstance(o.get("content"), str):
        txt = o["content"]
    if not txt:
        return None
    if "system-reminder" in txt and "VOICE_TRANSCRIPT" not in txt and "[ATTACHMENT" not in txt:
        m2 = re.search(r'VOICE_TRANSCRIPT.*?»', txt, re.S)
        txt = m2.group(0) if m2 else None
        if not txt:
            return None
    txt = re.sub(r'^\[[SHTGDAVP\]\[]+\]\s*', '', txt.strip())
    if len(txt) < 6:
        return None
    return (o.get("timestamp", ""), role, txt)


def main():
    day = sys.argv[1] if len(sys.argv) > 1 else \
        datetime.datetime.now(MSK).strftime("%Y-%m-%d")
    # MSK-день пересекает два UTC-дня → пред-фильтр по day и day-1 (UTC)
    yest = (datetime.date.fromisoformat(day) - datetime.timedelta(days=1)).isoformat()
    items = []
    for fp in glob.glob(f"{PROJ}/*.jsonl") + glob.glob(f"{PROJ}/*/subagents/*.jsonl"):
        for ln in open(fp, encoding="utf-8", errors="ignore"):
            if f'"{day}' not in ln and f'"{yest}' not in ln:  # быстрый пред-фильтр (UTC)
                continue
            try:
                o = json.loads(ln)
            except Exception:
                continue
            r = clean(o)
            if not r:
                continue
            ts_msk = to_msk(r[0])
            if ts_msk[:10] == day:  # московская дата
                items.append((ts_msk, r[1], r[2]))
    if not items:
        return
    items.sort(key=lambda x: x[0])
    _tag = " (Mac)" if SUFFIX else ""
    lines = [f"# Лог разговоров{_tag} {day}\n"]
    prev, lasthour = None, None
    for ts, role, txt in items:
        if txt == prev:
            continue
        prev = txt
        hh = ts[11:16] if len(ts) >= 16 else ""
        if hh[:2] != lasthour:
            lines.append(f"\n## {hh}\n"); lasthour = hh[:2]
        is_vlad = (role in ("user", "voice", "queue/voice", "queue-operation")
                   or txt.startswith("[VOICE_TRANSCRIPT") or txt.startswith("[FORWARD")
                   or txt.startswith("[ATTACHMENT"))
        who = OWNER if is_vlad else WHO
        lines.append(f"**{who}:** {txt}\n")
    os.makedirs(OUT, exist_ok=True)
    tmp = os.path.join(OUT, f".{day}{SUFFIX}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    os.replace(tmp, os.path.join(OUT, f"{day}{SUFFIX}.md"))  # атомарная замена


if __name__ == "__main__":
    main()
