"""Bridge a chat assistant to a browser-only "Deep Research" feature.

Some assistant features (long multi-source research runs) only exist in the web UI,
not in the headless API. This module drives a real, stealth browser on a server so a
Telegram bot can trigger a research run and read back the first response — turning a
browser-only capability into something a bot can call.

Stack: patchright (a stealth fork of Playwright) + a persistent Chromium profile that
is already logged in. Running headed under a virtual display (Xvfb) clears the
anti-bot interstitial that headless browsers trip.

Configuration via environment (no hardcoded paths):
    RESEARCH_USER_DATA   persistent browser profile dir (logged-in session)
    RESEARCH_CHROME_BIN  path to the Chromium/Chrome binary
    RESEARCH_BASE_URL    new-chat URL of the assistant web app
    PROMPT               the research prompt
    CLARIFY_ANSWER       optional answer to the assistant's clarifying question
    AUTO_BROAD=1         auto-answer the clarifying question with "go as broad as possible"
"""

import asyncio
import os
import sys
import time

from patchright.async_api import async_playwright

USER_DATA = os.environ["RESEARCH_USER_DATA"]
CHROME = os.environ["RESEARCH_CHROME_BIN"]
BASE_URL = os.environ.get("RESEARCH_BASE_URL", "https://example.com/new")
PROMPT = os.environ.get("PROMPT", "").strip()
LOG = os.environ.get("RESEARCH_LOG", "/tmp/research.log")


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def one_line(s: str) -> str:
    return " ".join(s.split())


async def clear_interstitial(page) -> str:
    """Wait out the anti-bot "just a moment" interstitial, return the final title."""
    for _ in range(12):
        await page.wait_for_timeout(4000)
        title = await page.title()
        if "moment" not in title.lower():
            return title
    return await page.title()


async def send_or_enter(page, editor) -> None:
    """Click the send button; fall back to Enter (the prompt is one line, no fragment)."""
    send = page.locator('button[aria-label="Send message"]').first
    if await send.count():
        try:
            await send.click(timeout=6000)
            return
        except Exception as e:
            log("send click intercepted: " + str(e)[:80])
    await editor.click()
    await page.keyboard.press("Enter")


async def main() -> None:
    if not PROMPT:
        log("NO PROMPT")
        sys.exit(2)
    # Keep the prompt on one line: a newline into the rich-text editor sends a fragment.
    text = one_line(PROMPT)
    with open(LOG, "w"):
        pass

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            USER_DATA,
            headless=False,
            executable_path=CHROME,
            no_viewport=True,
            args=["--no-sandbox", "--window-size=1400,900"],
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=45000)
        log("title: " + await clear_interstitial(page))
        await page.wait_for_timeout(3500)

        # Dismiss the cookie banner — it intercepts clicks on the send button.
        for sel in (
            'button:has-text("Reject All Cookies")',
            'button:has-text("Accept All Cookies")',
        ):
            try:
                btn = page.locator(sel).first
                if await btn.count():
                    await btn.click(timeout=3000)
                    log("dismissed cookie banner: " + sel)
                    break
            except Exception as e:
                log("cookie dismiss fail: " + str(e))
        await page.wait_for_timeout(800)

        # Open the "+" menu and enable Research mode.
        await page.locator('button[aria-label="Add files, connectors, and more"]').first.click(
            timeout=6000
        )
        log("clicked +")
        await page.wait_for_timeout(1500)
        await page.get_by_text("Research", exact=True).first.click(timeout=6000)
        log("clicked Research")
        await page.wait_for_timeout(1200)

        # Type the prompt into the rich-text editor.
        editor = page.locator("div.ProseMirror").first
        await editor.click()
        await page.keyboard.type(text, delay=10)
        log(f"typed prompt ({len(text)} chars)")
        await page.wait_for_timeout(1000)

        await send_or_enter(page, editor)
        log("sent prompt")

        await page.wait_for_timeout(8000)
        url = page.url
        log(f"after send -> {await page.title()} | {url}")
        print("RESULT_URL " + url, flush=True)

        # Deep Research almost always asks a clarifying question first, then digs.
        # Wait for the first response to settle and hand it back.
        async def settle() -> str:
            last = ""
            stable = 0
            for _ in range(20):  # up to ~60s
                await page.wait_for_timeout(3000)
                cur = await page.evaluate(
                    "() => {const e=[...document.querySelectorAll('.font-claude-message')];"
                    "return e.length ? e[e.length-1].innerText.trim() : '';}"
                )
                if cur and cur == last:
                    stable += 1
                else:
                    stable = 0
                last = cur
                if stable >= 2 and cur:
                    break
            return last

        first = await settle()
        log("FIRST_RESPONSE len=" + str(len(first)))
        print("FIRST_RESPONSE: " + " ".join(first.split("\n"))[:1500], flush=True)

        # Answer the clarifying question, if there is one.
        clarify = os.environ.get("CLARIFY_ANSWER", "").strip()
        if not clarify and os.environ.get("AUTO_BROAD", "") == "1":
            clarify = "Go as broad as possible on every point, no hard limits and no further questions — start the deep search with the widest scope."
        looks_like_question = (
            first.endswith("?") or "before i" in first.lower() or "clarif" in first.lower()
        )
        if clarify and looks_like_question:
            answer = one_line(clarify)
            editor2 = page.locator("div.ProseMirror").first
            await editor2.click()
            await page.keyboard.type(answer, delay=8)
            await page.wait_for_timeout(600)
            await send_or_enter(page, editor2)
            log("sent clarify answer (" + str(len(answer)) + " chars)")
            await page.wait_for_timeout(6000)
            print("CLARIFY_ANSWERED", flush=True)
        elif looks_like_question:
            print("PENDING_CLARIFY (answer via answer.py: CHAT_URL=<url> ANSWER='...')", flush=True)

        await ctx.close()
        log("DONE")


if __name__ == "__main__":
    asyncio.run(main())
