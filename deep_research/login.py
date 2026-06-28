"""Log a persistent stealth-browser profile into the assistant web app via email OTP.

Drives a real, stealth browser (patchright + Chromium under Xvfb) through the
email -> one-time-code login flow and saves the authenticated session into a
persistent profile so later runs (research.py, read_chat.py, ...) start logged in.

The OTP code arrives out-of-band (you paste it into RESEARCH_CODE_FILE while the
script polls), so no inbox access is required.

Configuration via environment (no hardcoded paths):
    RESEARCH_USER_DATA   persistent browser profile dir
    RESEARCH_CHROME_BIN  path to the Chromium/Chrome binary
    RESEARCH_LOGIN_URL   login URL of the assistant web app
    RESEARCH_NEW_URL     new-chat URL (used to verify the session)
    RESEARCH_AUTH        path to write the storage_state JSON
    RESEARCH_CODE_FILE   file polled for the one-time login code
    RESEARCH_LOG         log file (default /tmp/research-login.log)
    ASSISTANT_EMAIL      login email
"""

import asyncio
import os
import sys
import time

from patchright.async_api import async_playwright

UD = os.environ["RESEARCH_USER_DATA"]
CHROME = os.environ["RESEARCH_CHROME_BIN"]
LOGIN_URL = os.environ.get("RESEARCH_LOGIN_URL", "https://example.com/login")
NEW_URL = os.environ.get("RESEARCH_NEW_URL", "https://example.com/new")
EMAIL = os.environ.get("ASSISTANT_EMAIL", "").strip()
CODEFILE = os.environ.get("RESEARCH_CODE_FILE", "/tmp/assistant-code.txt")
AUTH = os.environ.get("RESEARCH_AUTH", "/tmp/auth.json")
LOG = os.environ.get("RESEARCH_LOG", "/tmp/research-login.log")


def log(m):
    line = f"[{time.strftime('%H:%M:%S')}] {m}"
    print(line, flush=True)
    open(LOG, "a").write(line + "\n")


async def main():
    if os.path.exists(CODEFILE):
        os.remove(CODEFILE)
    open(LOG, "w").write("")
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            UD, headless=False, executable_path=CHROME, no_viewport=True, args=["--no-sandbox"]
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        log("goto login")
        await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=45000)
        # Wait out the anti-bot "just a moment" interstitial.
        for i in range(10):
            await page.wait_for_timeout(4000)
            t = await page.title()
            if "момент" not in t.lower() and "moment" not in t.lower():
                break
            log(f"interstitial wait {(i + 1) * 4}s: {t}")
        log(f"page: {await page.title()}")
        # email
        em = page.locator("input[type=email],input[name=email]").first
        await em.wait_for(timeout=15000)
        await em.fill(EMAIL)
        log("email filled")
        await page.wait_for_timeout(500)
        # click continue-with-email
        for sel in [
            'button:has-text("Continue with email")',
            'button:has-text("Continue")',
            "button[type=submit]",
        ]:
            try:
                b = page.locator(sel).first
                if await b.count():
                    await b.click(timeout=4000)
                    log(f"clicked {sel}")
                    break
            except Exception as e:
                log(f"click {sel} fail: {e}")
        await page.wait_for_timeout(4000)
        await page.screenshot(path="/tmp/after-email.png")
        log(f"after email -> {await page.title()} | {page.url}")
        body = await page.evaluate("document.body.innerText.slice(0,300)")
        log(f"body: {body!r}")
        # wait for code (paste it into CODEFILE out-of-band)
        log(f"WAITING FOR CODE in {CODEFILE} ...")
        code = None
        for _ in range(200):  # ~10 min
            if os.path.exists(CODEFILE):
                code = "".join(ch for ch in open(CODEFILE).read() if ch.isdigit())
                if len(code) >= 6:
                    break
            await asyncio.sleep(3)
        if not code:
            log("NO CODE — timeout")
            await ctx.close()
            sys.exit(2)
        log("got code")
        # fill code: try single input or OTP boxes
        inputs = page.locator("input")
        n = await inputs.count()
        log(f"{n} inputs on code page")
        filled = False
        # try single text/tel input
        single = page.locator("input[type=text],input[type=tel],input[inputmode=numeric]").first
        if await single.count():
            try:
                await single.click()
                await page.keyboard.type(code, delay=120)
                filled = True
                log("typed code into single/otp via keyboard")
            except Exception as e:
                log(f"single fill fail: {e}")
        if not filled:
            log("could not fill code")
            await page.screenshot(path="/tmp/code-page.png")
        await page.wait_for_timeout(1500)
        for sel in [
            'button:has-text("Continue")',
            'button:has-text("Verify")',
            "button[type=submit]",
        ]:
            try:
                b = page.locator(sel).first
                if await b.count():
                    await b.click(timeout=4000)
                    log(f"submit {sel}")
                    break
            except Exception as e:
                log(f"submit {sel} fail: {e}")
        await page.wait_for_timeout(7000)
        log(f"after code -> {await page.title()} | {page.url}")
        await page.screenshot(path="/tmp/after-code.png")
        # save state
        await ctx.storage_state(path=AUTH)
        log(f"storage_state saved -> {AUTH}")
        # verify logged in
        await page.goto(NEW_URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(6000)
        b2 = await page.evaluate("document.body.innerText.slice(0,200)")
        loginish = any(m in b2 for m in ["Sign in", "Войти", "Continue with email"])
        log(f"VERIFY /new logged_in={not loginish} | head={b2!r}")
        await ctx.storage_state(path=AUTH)
        await page.screenshot(path="/tmp/verify-new.png")
        await ctx.close()
        log("DONE")


asyncio.run(main())
