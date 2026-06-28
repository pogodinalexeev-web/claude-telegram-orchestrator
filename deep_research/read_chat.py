"""Read back all assistant messages from an existing chat in the web app.

Opens a chat URL in the stealth browser (patchright + Chromium under Xvfb), clears
the anti-bot interstitial, and dumps every assistant message plus whether the input
editor is present (i.e. the run is waiting on you) and any choice buttons on screen.

Configuration via environment:
    RESEARCH_USER_DATA   persistent browser profile dir
    RESEARCH_CHROME_BIN  path to the Chromium/Chrome binary
    CHAT_URL             URL of the chat to read
"""

import asyncio
import os

from patchright.async_api import async_playwright

UD = os.environ["RESEARCH_USER_DATA"]
CHROME = os.environ["RESEARCH_CHROME_BIN"]
URL = os.environ.get("CHAT_URL", "").strip()


async def cf(page):
    """Wait out the anti-bot "just a moment" interstitial."""
    for _i in range(12):
        await page.wait_for_timeout(3500)
        t = await page.title()
        if "момент" not in t.lower() and "moment" not in t.lower():
            return t
    return await page.title()


async def main():
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
        await page.wait_for_timeout(4000)
        # текст всех сообщений ассистента
        msgs = await page.evaluate("""() => {
          const out=[];
          document.querySelectorAll('[data-testid="chat-turn"], .font-claude-message, [data-is-streaming]').forEach(e=>{
            const t=e.innerText.trim(); if(t) out.push(t.slice(0,1200));
          });
          return out;
        }""")
        if not msgs:
            body = await page.evaluate("document.body.innerText")
            print("RAW_BODY:\n" + body[-2500:])
        else:
            print("=== MESSAGES (" + str(len(msgs)) + ") ===")
            for i, m in enumerate(msgs):
                print(f"--- msg {i} ---\n{m}\n")
        # есть ли поле ввода (значит ждёт ответа) / кнопки выбора
        ed = await page.locator("div.ProseMirror").count()
        btns = await page.evaluate(
            """() => [...document.querySelectorAll('button')].map(b=>b.innerText.trim()).filter(x=>x&&x.length<40).slice(0,25)"""
        )
        print("EDITOR_PRESENT=" + str(ed))
        print("BUTTONS=" + str(btns))
        await page.screenshot(path="/tmp/read-chat.png")
        await ctx.close()


asyncio.run(main())
