"""Log a persistent stealth-browser profile in via a one-time "magic link".

Some web apps offer a passwordless magic-link login: you click a one-time URL and
the session is established. This script opens that link in the stealth browser
(patchright + Chromium under Xvfb), clears the anti-bot interstitial, and saves the
resulting authenticated session into the persistent profile.

The link is a one-time secret — pass the real one only via env, never hardcode it.

Configuration via environment (no hardcoded paths/secrets):
    RESEARCH_USER_DATA   persistent browser profile dir
    RESEARCH_CHROME_BIN  path to the Chromium/Chrome binary
    RESEARCH_NEW_URL     new-chat URL (used to verify the session)
    RESEARCH_AUTH        path to write the storage_state JSON
    RESEARCH_LOG         log file (default /tmp/research-magic.log)
    MAGIC_LINK           the one-time login URL
"""

import asyncio
import os
import sys
import time

from patchright.async_api import async_playwright

UD = os.environ["RESEARCH_USER_DATA"]
CHROME = os.environ["RESEARCH_CHROME_BIN"]
NEW_URL = os.environ.get("RESEARCH_NEW_URL", "https://example.com/new")
LINK = os.environ.get("MAGIC_LINK", "").strip()
AUTH = os.environ.get("RESEARCH_AUTH", "/tmp/auth.json")
LOG = os.environ.get("RESEARCH_LOG", "/tmp/research-magic.log")


def log(m):
    line = f"[{time.strftime('%H:%M:%S')}] {m}"
    print(line, flush=True)
    open(LOG, "a").write(line + "\n")


async def cf_wait(page, label):
    """Wait out the anti-bot "just a moment" interstitial."""
    for i in range(12):
        await page.wait_for_timeout(4000)
        t = await page.title()
        if "момент" not in t.lower() and "moment" not in t.lower():
            log(f"{label}: interstitial passed -> {t}")
            return True
        log(f"{label}: interstitial {(i + 1) * 4}s: {t}")
    log(f"{label}: interstitial STILL blocked")
    return False


async def main():
    if not LINK:
        log("NO LINK")
        sys.exit(2)
    open(LOG, "w").write("")
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            UD, headless=False, executable_path=CHROME, no_viewport=True, args=["--no-sandbox"]
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        log("goto magic link")
        await page.goto(LINK, wait_until="domcontentloaded", timeout=45000)
        await cf_wait(page, "magic")
        await page.wait_for_timeout(5000)
        log(f"after magic -> {await page.title()} | {page.url}")
        body = await page.evaluate("document.body.innerText.slice(0,300)")
        log(f"body: {body!r}")
        await page.screenshot(path="/tmp/magic-after.png")
        # save whatever session we got
        await ctx.storage_state(path=AUTH)
        log(f"storage_state saved -> {AUTH}")
        # verify
        await page.goto(NEW_URL, wait_until="domcontentloaded", timeout=30000)
        await cf_wait(page, "verify")
        await page.wait_for_timeout(5000)
        b2 = await page.evaluate("document.body.innerText.slice(0,200)")
        loginish = any(m in b2 for m in ["Sign in", "Войти", "Continue with email", "Log in"])
        log(f"VERIFY /new logged_in={not loginish} | head={b2!r}")
        await ctx.storage_state(path=AUTH)
        await page.screenshot(path="/tmp/magic-verify.png")
        await ctx.close()
        log("DONE")


asyncio.run(main())
