"""Scrape the full text of a research report artifact from an assistant chat.

When a report is too long to copy in one go, this opens the artifact panel (by a
title snippet, NEEDLE), finds the scrollable container, and scrolls it to the bottom
accumulating every line of text, then writes the de-duplicated result to OUT.

Configuration via environment:
    RESEARCH_USER_DATA   persistent browser profile dir
    RESEARCH_CHROME_BIN  path to the Chromium/Chrome binary
    CHAT_URL             URL of the chat
    NEEDLE               text snippet identifying the report card title
    OUT                  destination path for the scraped text (default /tmp/report.md)
"""

import asyncio
import os

from patchright.async_api import async_playwright

UD = os.environ["RESEARCH_USER_DATA"]
CHROME = os.environ["RESEARCH_CHROME_BIN"]
URL = os.environ.get("CHAT_URL", "").strip()
OUT = os.environ.get("OUT", "/tmp/report.md")


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
            args=["--no-sandbox", "--window-size=1500,1000"],
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(URL, wait_until="domcontentloaded", timeout=45000)
        await cf(page)
        await page.wait_for_timeout(4000)

        # NEEDLE — кусок заголовка карточки нужного отчёта (env)
        NEEDLE = os.environ.get("NEEDLE", "Report title")
        clicked = "NONE"
        try:
            loc = page.get_by_text(NEEDLE, exact=False).last
            await loc.scroll_into_view_if_needed(timeout=6000)
            await page.wait_for_timeout(800)
            await loc.click(force=True, timeout=6000)
            clicked = "PW_CLICK"
        except Exception as e:
            clicked = "ERR:" + str(e)[:80]
        await page.wait_for_timeout(5000)

        # найти прокручиваемый контейнер артефакта и прогнать его вниз, копя текст
        text = await page.evaluate("""async () => {
          const sleep=ms=>new Promise(r=>setTimeout(r,ms));
          // кандидат: элемент с большим scrollHeight, содержащий маркеры отчёта
          let panel=null, bestH=0;
          for(const e of document.querySelectorAll('div,section,article')){
            const sh=e.scrollHeight, ch=e.clientHeight;
            const t=e.innerText||'';
            if(sh>ch+200 && sh>bestH && (t.includes('TL;DR')||t.includes('##')||t.length>3000)){
              bestH=sh; panel=e;
            }
          }
          if(!panel){
            // fallback: самый длинный по тексту контейнер
            for(const e of document.querySelectorAll('div,section,article,main')){
              const t=e.innerText||''; if(t.length>bestH){bestH=t.length; panel=e;}
            }
            return panel? panel.innerText : document.body.innerText;
          }
          // прокрутка
          panel.scrollTop=0; await sleep(400);
          let acc=new Set();
          for(let i=0;i<60;i++){
            (panel.innerText||'').split('\\n').forEach(l=>acc.add(l));
            const before=panel.scrollTop;
            panel.scrollTop=panel.scrollTop+panel.clientHeight*0.85;
            await sleep(350);
            if(panel.scrollTop<=before+5) break;
          }
          (panel.innerText||'').split('\\n').forEach(l=>acc.add(l));
          return [...acc].join('\\n');
        }""")
        with open(OUT, "w") as f:
            f.write(text or "")
        print("CLICKED=" + str(clicked))
        print("TEXT_LEN=" + str(len(text or "")))
        print("WROTE=" + OUT)
        await page.screenshot(path="/tmp/dump-report.png")
        await ctx.close()


asyncio.run(main())
