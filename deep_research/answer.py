# Дослать ответ в УЖЕ открытый чат web-app (например, на уточняющий вопрос Research).
# CHAT_URL=<url> ANSWER='<текст в одну строку>' python3 answer.py
"""Send a follow-up message into an already-open chat (e.g. answer a clarifying
question from a Deep Research run). Drives the stealth browser, types into the
ProseMirror rich-text editor, and confirms delivery by checking the editor cleared.

Configuration via environment:
    RESEARCH_USER_DATA   persistent browser profile dir
    RESEARCH_CHROME_BIN  path to the Chromium/Chrome binary
    CHAT_URL             URL of the chat to answer in
    ANSWER               the answer text (collapsed to one line)
"""

import asyncio
import os
import sys

from patchright.async_api import async_playwright

UD = os.environ["RESEARCH_USER_DATA"]
CHROME = os.environ["RESEARCH_CHROME_BIN"]
URL = os.environ.get("CHAT_URL", "").strip()
ANSWER = " ".join(os.environ.get("ANSWER", "").split("\n")).strip()


async def cf(page):
    """Wait out the anti-bot "just a moment" interstitial."""
    for _i in range(12):
        await page.wait_for_timeout(3500)
        t = await page.title()
        if "момент" not in t.lower() and "moment" not in t.lower():
            return t
    return await page.title()


async def main():
    if not URL or not ANSWER:
        print("NEED CHAT_URL and ANSWER")
        sys.exit(2)
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            UD,
            headless=False,
            executable_path=CHROME,
            no_viewport=True,
            args=["--no-sandbox", "--window-size=1400,900"],
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(URL, wait_until="domcontentloaded", timeout=45000)
        await cf(page)
        await page.wait_for_timeout(3500)
        ed = page.locator("div.ProseMirror").first
        await ed.click()
        await page.keyboard.type(ANSWER, delay=8)
        await page.wait_for_timeout(800)

        # отправка с проверкой: редактор должен очиститься = улетело
        async def editor_text():
            try:
                return (await ed.inner_text()).strip()
            except Exception:
                return "?"

        delivered = False
        for _attempt in range(4):
            send = page.locator('button[aria-label="Send message"]').first
            clicked = False
            if await send.count():
                try:
                    await send.click(timeout=5000)
                    clicked = True
                except Exception:
                    pass
            if not clicked:
                await ed.click()
                await page.keyboard.press("Enter")
            await page.wait_for_timeout(2500)
            if len(await editor_text()) < 3:
                delivered = True
                break
            # текст застрял в редакторе — пробуем ещё раз
        await page.wait_for_timeout(4000)
        await page.screenshot(path="/tmp/answer-sent.png")
        print(
            ("ANSWER_SENT" if delivered else "ANSWER_STUCK")
            + " len="
            + str(len(ANSWER))
            + " | "
            + page.url
        )
        await ctx.close()


asyncio.run(main())
