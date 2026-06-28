---
name: cinema
description: Finds movies for the owner at 2 local cinemas — runs listings through headless browser on VPS, filters hard-NO genres and already-seen films, produces a report with links. Automatic run on Thursday 09:00 via cron. On demand — run the script manually. Source of truth — Projects/LIFE/кино.md.
---

# /cinema — cinema listings pulse + filter

## When triggered

1. **Schedule** — Thursday 09:00 via cron on VPS (`crontab -l` line `0 9 * * 4` with `CRON_TZ=Europe/Moscow`). Runs `~/browser/cinema/run.py`, gathers listings, filters, rewrites "Current candidates" / "Rejected" sections in `Projects/LIFE/кино.md`, does `git commit+push`.
2. **On demand** — "what's showing", "what's on this weekend", "/cinema". From session: run the script on VPS.
3. **Feedback processing** — when owner says "we watched X last night, grandma liked it" — record in journal in `Projects/LIFE/кино.md`.

## Source of truth

**`Projects/LIFE/кино.md`** — human file in vault.
- Hard-NO rules, soft categories, viewing journal, owner's notes — read from there **every run**.
- After run: overwrite "Current candidates" and "Rejected in last run" sections. Don't touch the rest.

## Run algorithm

1. Read `Projects/LIFE/кино.md` — grab hard-NO genres, soft categories, list of titles from "Viewing journal" (to avoid re-suggesting seen films).
2. Launch listing scrapers on VPS via `~/browser/fetch.py`. Scrapers live in `.claude/skills/cinema/scrapers/<site>.py` — each returns normalized JSON list `[{title, year, country, genres, age, cinema, hall, dates: [{date, times: [HH:MM], price_from}], url}]`.
3. Merge lists, deduplicate by `title+year` (one film at multiple cinemas — one card with multiple sessions).
4. Filter:
   - **hard-NO** → "Rejected", reason "hard-NO genre".
   - **already in journal** → "Rejected", reason "already seen YYYY-MM-DD".
   - **soft category** → "Current candidates", but tagged `[soft: <category>]`.
   - **other** → "Current candidates".
5. Sort candidates: nearest session date → higher; soft categories → below main.
6. Write `Projects/LIFE/кино.md` (steps 4-5 in one save).
7. If **running on schedule (Thursday)** — send TG summary: "New listings. N candidates, M rejected. See Projects/LIFE/кино.md". If **on demand** — reply to owner directly with full list.

## Format of "Current candidates" section

```
**Last run:** 2026-05-XX HH:MM

### ✅ Candidates

1. **<Title>** (<year>, <country>) — <genre>, <age rating>
   - 📍 <cinema-1>: <dates and times>
   - 🔗 <link to film page>

[soft: war] **Seven Miles to Dawn** ...
```

## Format of "Rejected" section

```
- ❌ <title> — hard-NO (horror)
- ❌ <title> — already watched 2026-05-XX, reaction: ...
```

## Feedback processing (owner said "we saw X last night")

1. Find `<X>` in "Current candidates" — confirms the suggestion worked.
2. Add to "Viewing journal": `- YYYY-MM-DD — <X> (<cinema>) — <reaction per owner>`.
3. Remove from "Current candidates", add to "Rejected" with reason "already seen".
4. If owner gave explicit rejection reason / dislike of genre — update "Owner's notes" or propose hard-NO/soft update.

## What NOT to do

- Never suggest horror. Any hint at "horror / mystical thriller / suspense" → hard-NO without discussion.
- Don't hard-reject soft categories (children's, war) — include in report tagged `[soft]`.
- Don't touch "Owner's notes" and "Viewing journal" during automatic run — owner's territory.
- Don't guess cinema site URLs — if no scraper for a site yet, write "cinema X — scraper not ready".
- Don't suggest films from "Viewing journal" — that's a repeat.

## Tech stack

- Headless browser on VPS: `~/browser/venv/bin/python ~/browser/fetch.py` (CLI) or direct `playwright.sync_api` for multi-step scenarios (modals, clicks).
- Per-site scrapers: `.claude/skills/cinema/scrapers/<cinema1>.py`, `<cinema2>.py` — each outputs normalized JSON to stdout.
- Runner: `.claude/skills/cinema/run.py` — orchestrator: runs scrapers (parallel), filters, writes `Projects/LIFE/кино.md`.

## History

- Stage 1: skill scaffold + `Projects/LIFE/кино.md`. Scrapers and runner — added in subsequent stages.
