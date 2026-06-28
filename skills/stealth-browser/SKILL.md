---
name: stealth-browser
description: Open a site behind Cloudflare protection ("Just a moment / security check") with a real browser on VPS — patchright (stealth fork of playwright) headed via Xvfb. Bypasses bot checks that headless and regular playwright fail. Use when you need to open/scrape a Cloudflare-protected site from server: claude.ai, TGStat, and any site showing "Just a moment". Triggers: "can't scrape X, Cloudflare blocks", "log VPS into claude.ai", "bypass cloudflare", "open <site> on server".
---

# stealth-browser — bypass Cloudflare with real browser on VPS

## Main lesson

Cloudflare on claude.ai/TGStat doesn't hit on obvious robot signals (patchright hides those) — Cloudflare still lets patchright through **if you don't interfere with it**. Past failures were not about GPU, but about **bad configuration**.

**✅ Working configuration (bypasses Cloudflare):**
```python
ctx = await p.chromium.launch_persistent_context(
    USER_DATA_DIR, headless=False,
    executable_path=CHROME, no_viewport=True,
    args=["--no-sandbox"])
```

**❌ Anti-pattern (Cloudflare blocks — verified):**
- DON'T set your own `user_agent=` — patchright sets the right one.
- DON'T add `--disable-blink-features=AutomationControlled` and other "stealth flags" manually — patchright patches this itself, manual override exposes the bot.
- DON'T set `viewport=` — use `no_viewport=True`.
- DON'T `headless=True` — only `headless=False` under Xvfb.

Difference before/after: with manual UA+flags → `TITLE: One moment…` (blocked). Clean config → `TITLE: Sign in - Claude` (broke through).

## Stack (on VPS, under bot user)

- **venv:** `~/browser/venv` — contains `patchright` (not bare playwright).
- **Full Chrome:** `~/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome` (NOT `chromium_headless_shell`).
- **Xvfb:** `/usr/bin/xvfb-run` — virtual display, needed to run headed without monitor.
- **Profile:** `~/browser/<service>/userdata` — persistent profile folder. Stores Cloudflare pass (`cf_clearance`) between runs → second visit is faster.
- **Saved session:** `~/browser/<service>/auth.json` — login cookies (`storage_state`).

## How to run (always via Xvfb)

```bash
cd ~/browser; source venv/bin/activate
xvfb-run -a -s "-screen 0 1280x800x24" python3 <script>.py
```
Long/interactive login (waiting for email code) — run in background via `nohup ... &` so process lives between turns without losing Cloudflare pass.

## Recipe A — just open a Cloudflare-protected site and grab content

```python
import asyncio
from patchright.async_api import async_playwright
CHROME="~/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome"
async def main():
    async with async_playwright() as p:
        ctx=await p.chromium.launch_persistent_context(
            "~/browser/<svc>/userdata", headless=False,
            executable_path=CHROME, no_viewport=True, args=["--no-sandbox"])
        page=ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(URL, wait_until="domcontentloaded", timeout=45000)
        for _ in range(10):                 # wait for Cloudflare to solve challenge
            await page.wait_for_timeout(4000)
            t=await page.title()
            if "момент" not in t.lower() and "moment" not in t.lower(): break
        # now on real page — do your work
        await ctx.close()
asyncio.run(main())
```
Sign of passing: title stopped being "One moment… / Just a moment…".

## Recipe B — log into claude.ai, session persists on VPS

**Two paths. Magic link is simpler — try it first.**

### B1 (preferred) — magic link directly in VPS browser

Working script — `~/browser/claude/magic.py`. Logic:
1. Owner initiates login at claude.ai/login via email → email arrives with link `https://claude.ai/magic-link#<hash>:<base64email>`.
2. Owner **sends the link in TG** (not the code).
3. `MAGIC_LINK='<link>' xvfb-run ... python3 magic.py` opens it **in the VPS browser itself** → claude.ai logs in this browser, redirects to `/new`.
4. `storage_state` → `~/browser/claude/auth.json`.

**Important:** the old lesson "link doesn't work, need a code" was about clicking the link on the owner's PHONE (logs in a different browser). If the link is opened in the patchright browser on VPS — it works on the first try, faster than the code method. Link is **short-lived and one-time** — use fresh (older than ~10 min is "This link has expired") and don't open it in regular playwright beforehand (will burn it).

### B2 (fallback) — code from email

Working script — `~/browser/claude/login.py`. Logic:
1. Open `https://claude.ai/login`, wait for Cloudflare pass.
2. Fill `input[type=email]`, click "Continue with email".
3. claude.ai sends a 6-digit code to email.
4. Script waits for code in file `/tmp/claude-code.txt` (up to ~10 min). Owner sends code in TG → write it to file.
5. Enter code → `storage_state` saved to `~/browser/claude/auth.json`.

Session lifetime on claude.ai — ~30 days. Re-login with either scheme.

**Launch gotcha:** don't write `pkill -f 'userdata2'` in the same command where `userdata2` appears in paths — pkill will kill the shell command itself (exit 144, script doesn't start). Kill by script name (`pkill -f login.py`), clean profile in a separate line.

## Recipe C — reuse saved session

```python
ctx = await p.chromium.launch_persistent_context(USER_DATA_DIR, headless=False,
    executable_path=CHROME, no_viewport=True, args=["--no-sandbox"])
# profile userdata already holds cookies; or separate context with storage_state=auth.json
```

## TGStat and other scraping

TGStat blocks Cloudflare. Solution — Recipe A: open the needed TGStat page in this browser, wait for pass, grab HTML/text via `page.content()` / `page.evaluate("document.body.innerText")`. If TGStat requires login — Recipe B under their account, session in `~/browser/tgstat/`.

## Deploying to another bot

Each bot — its own user, its own `~/browser`. Needed under their home:
1. `python3 -m venv ~/browser/venv && source ~/browser/venv/bin/activate`
2. `pip install patchright && patchright install chromium` (pulls full Chrome).
3. `apt` package `xvfb` — system-wide, already installed.
4. Copy this skill to `~/vault/.claude/skills/stealth-browser/`.

## Debugging

- `TITLE: One moment… / Just a moment…` doesn't go away >45s → check that you have NOT set manual UA/flags/viewport (main anti-pattern above).
- Empty/timeout on `goto` → site is down or timeout too small, increase to 60000.
- Session not logging in after restore → cookies expired, re-login (Recipe B).
- Process hanging in background — log at `/tmp/claude-login.log`, screenshots at `/tmp/*.png`.

## History

- Created when trying to scrape a Cloudflare-protected site and hitting the wall with headless and manual "stealth" config. Clean patchright headed via Xvfb broke through on the first try. Skill captures the working recipe to avoid repeating the mistake.
