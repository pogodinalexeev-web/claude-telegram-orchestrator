#!/usr/bin/env python3
"""
run.py — оркестратор скилла /cinema.

Шаги:
  1) запустить сборщики (cinema1, cinema2) → объединённый JSON.
  2) прочитать Projects/кино.md — hard-NO жанры, soft, журнал просмотров.
  3) отфильтровать кандидатов / отброшенных.
  4) перезаписать разделы «Текущие кандидаты» и «Отброшено в последнем прогоне» в Projects/кино.md.
  5) git add + commit + push (если не --no-push).

Запускается на VPS (где живут сборщики и vault).
"""
import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

VPS_VAULT = Path("$VAULT")
VPS_SCRAPERS = Path("$HOME/browser/cinema/scrapers")
PYTHON = "$HOME/browser/venv/bin/python"
SCRAPERS = ["cinema1", "cinema2"]


def run_scraper(name: str) -> list[dict]:
    proc = subprocess.run(
        [PYTHON, str(VPS_SCRAPERS / f"{name}.py")],
        capture_output=True, text=True, timeout=1500,
    )
    if proc.returncode != 0:
        print(f"[err] {name}: rc={proc.returncode}\n{proc.stderr[:500]}", file=sys.stderr)
        return []
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        print(f"[err] {name}: bad json: {e}", file=sys.stderr)
        return []


def parse_section_items(content: str, header_regex: str) -> list[str]:
    """Возвращает строки-bulletы из секции, начинающейся с заголовка."""
    m = re.search(rf"{header_regex}\s*\n(.*?)(?=\n##\s)", content, re.S)
    if not m:
        return []
    items = []
    for line in m.group(1).splitlines():
        bm = re.match(r"^\s*-\s+\*\*([^*]+)\*\*", line)
        if bm:
            items.append(bm.group(1).strip())
    return items


def parse_seen_films(content: str) -> set[str]:
    """Из раздела «Журнал просмотров» забирает названия как нормализованные ключи."""
    m = re.search(r"## Журнал просмотров.*?\n(.*?)(?=\n##\s)", content, re.S)
    if not m:
        return set()
    seen = set()
    for line in m.group(1).splitlines():
        bm = re.match(r"^-\s+\d{4}-\d{2}-\d{2}\s+—\s+(.+?)\s+\(", line)
        if bm:
            seen.add(bm.group(1).strip().lower())
    return seen


def filter_films(films: list[dict], hard_no: list[str], soft: list[str], seen: set[str]):
    """
    Возвращает (кандидаты_основные, кандидаты_soft, отброшенные[(film, reason)]).
    Дедуп по title — если фильм в <cinema-1>+<cinema-2>, объединяем сеансы.
    """
    by_title: dict[str, dict] = {}
    for f in films:
        t = f["title"].strip()
        if t not in by_title:
            by_title[t] = dict(f)
            by_title[t]["cinemas"] = [(f["cinema"], f["url"], f["sessions"])]
        else:
            agg = by_title[t]
            agg["cinemas"].append((f["cinema"], f["url"], f["sessions"]))
            if not agg.get("genres"):
                agg["genres"] = f["genres"]
            if not agg.get("year"):
                agg["year"] = f["year"]
            if not agg.get("country"):
                agg["country"] = f["country"]

    main, soft_list, rejected = [], [], []
    hard_no_lc = [h.lower() for h in hard_no]
    soft_lc = [s.lower() for s in soft]

    for t, f in by_title.items():
        gen_lc = [g.lower() for g in f.get("genres", [])]
        if t.lower() in seen:
            rejected.append((f, "уже смотрели"))
            continue
        # hard-NO: ужастики
        if any(any(hn in g for g in gen_lc) for hn in hard_no_lc):
            rejected.append((f, f"hard-NO жанр ({', '.join(f['genres'])})"))
            continue
        is_soft = any(any(s in g for g in gen_lc) for s in soft_lc)
        if is_soft:
            soft_list.append(f)
        else:
            main.append(f)

    # сортировка по ближайшей дате сеанса
    def first_date(f):
        all_dates = []
        for _cinema, _url, sessions in f["cinemas"]:
            for s in sessions:
                d = s.get("date")
                if d and re.match(r"\d{4}-\d{2}-\d{2}", d):
                    all_dates.append(d)
        return min(all_dates) if all_dates else "9999-99-99"

    main.sort(key=first_date)
    soft_list.sort(key=first_date)
    return main, soft_list, rejected


def fmt_film(f: dict) -> str:
    lines = []
    year = f.get("year") or "—"
    country = f.get("country") or "—"
    age = f.get("age") or "—"
    genres = ", ".join(f.get("genres") or []) or "—"
    head = f"### {f['title']} ({year}, {country})"
    lines.append(head)
    lines.append(f"- Жанр: {genres} · {age}")
    for cinema, url, sessions in f["cinemas"]:
        if not sessions:
            lines.append(f"- 📍 [{cinema}]({url}) — сеансы не найдены")
            continue
        # первые 3 даты, по 4 времени
        date_lines = []
        for s in sessions[:3]:
            slots = ", ".join(x["time"] for x in s["slots"][:5])
            date_lines.append(f"{s['date']}: {slots}")
        more = "" if len(sessions) <= 3 else f" (+ещё {len(sessions)-3} дн.)"
        lines.append(f"- 📍 [{cinema}]({url}){more}")
        for dl in date_lines:
            lines.append(f"   - {dl}")
    return "\n".join(lines)


def fmt_rejected(f: dict, reason: str) -> str:
    age = f.get("age") or "—"
    return f"- ❌ **{f['title']}** ({age}) — {reason}"


def update_vault_file(path: Path, main: list, soft: list, rejected: list):
    content = path.read_text(encoding="utf-8")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    cand_body = [f"**Последний прогон:** {now}", ""]
    if main:
        cand_body.append("### ✅ Кандидаты")
        cand_body.append("")
        cand_body.extend(fmt_film(f) + "\n" for f in main)
    if soft:
        cand_body.append("### ⚠️ Soft-категория (детское/военное — обычно мимо, проверь)")
        cand_body.append("")
        cand_body.extend(fmt_film(f) + "\n" for f in soft)
    if not main and not soft:
        cand_body.append("*Кандидатов нет.*")

    rej_body = ""
    if rejected:
        rej_body = "\n".join(fmt_rejected(f, r) for f, r in rejected)
    else:
        rej_body = "*Ничего не отбраковано.*"

    content = re.sub(
        r"(## Текущие кандидаты \(последний прогон скилла\)\s*\n)(.*?)(?=\n## Отброшено в последнем прогоне)",
        lambda m: m.group(1) + "\n" + "\n".join(cand_body) + "\n",
        content, flags=re.S, count=1,
    )
    content = re.sub(
        r"(## Отброшено в последнем прогоне\s*\n)(.*?)(?=\n## Заметки)",
        lambda m: m.group(1) + "\n*(перезаписывается скиллом)*\n\n" + rej_body + "\n\n",
        content, flags=re.S, count=1,
    )
    path.write_text(content, encoding="utf-8")


def git_push(vault: Path):
    try:
        subprocess.run(["git", "-C", str(vault), "add", "Projects/кино.md"], check=True)
        subprocess.run(
            ["git", "-C", str(vault), "commit", "-m",
             f"cinema: weekly run {datetime.now().strftime('%Y-%m-%d %H:%M')}"],
            check=False,
        )
        subprocess.run(["git", "-C", str(vault), "push"], check=False)
    except Exception as e:
        print(f"[warn] git push: {e}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-push", action="store_true", help="не делать git commit+push")
    ap.add_argument("--scrapers", default=",".join(SCRAPERS), help="comma-list")
    args = ap.parse_args()

    all_films = []
    for name in args.scrapers.split(","):
        name = name.strip()
        if not name:
            continue
        films = run_scraper(name)
        print(f"[ok] {name}: {len(films)} films", file=sys.stderr)
        all_films.extend(films)

    base = VPS_VAULT / "Projects/кино.md"
    content = base.read_text(encoding="utf-8")
    hard_no = parse_section_items(content, r"## Hard-NO")
    soft = parse_section_items(content, r"## Soft")
    seen = parse_seen_films(content)
    print(f"[rules] hard_no={hard_no} soft={soft} seen={len(seen)}", file=sys.stderr)

    main_, soft_list, rejected = filter_films(all_films, hard_no, soft, seen)
    print(f"[result] main={len(main_)} soft={len(soft_list)} rejected={len(rejected)}", file=sys.stderr)

    update_vault_file(base, main_, soft_list, rejected)
    print(f"[ok] {base} updated", file=sys.stderr)

    if not args.no_push:
        git_push(VPS_VAULT)
        print("[ok] git push", file=sys.stderr)


if __name__ == "__main__":
    main()
