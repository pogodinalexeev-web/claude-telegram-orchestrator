"""Download a generated artifact (file) from an assistant chat in the web app.

Opens the chat in the stealth browser, finds the artifact card by a snippet of its
title (NEEDLE), walks up the DOM to the nearest clickable ancestor to open it, then
clicks the panel's Download button and saves the freshest downloaded file to OUT.

Configuration via environment:
    RESEARCH_USER_DATA   persistent browser profile dir
    RESEARCH_CHROME_BIN  path to the Chromium/Chrome binary
    CHAT_URL             URL of the chat
    NEEDLE               text snippet identifying the artifact card title
    OUT                  destination path for the saved artifact
    RESEARCH_DL_DIR      browser downloads dir (default /tmp/research-dl)
"""

import asyncio
import glob
import os
import shutil

from patchright.async_api import async_playwright

UD = os.environ["RESEARCH_USER_DATA"]
CHROME = os.environ["RESEARCH_CHROME_BIN"]
URL = os.environ.get("CHAT_URL", "").strip()
NEEDLE = os.environ.get("NEEDLE", "Report title")
OUT = os.environ.get("OUT", "/tmp/artifact.md")
DLDIR = os.environ.get("RESEARCH_DL_DIR", "/tmp/research-dl")


async def cf(page):
    """Wait out the anti-bot "just a moment" interstitial."""
    for _i in range(12):
        await page.wait_for_timeout(3500)
        t = await page.title()
        if "момент" not in t.lower() and "moment" not in t.lower():
            return t
    return await page.title()


async def main():
    os.makedirs(DLDIR, exist_ok=True)
    for f in glob.glob(DLDIR + "/*"):
        try:
            os.remove(f)
        except Exception:
            pass
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            UD,
            headless=False,
            executable_path=CHROME,
            no_viewport=True,
            accept_downloads=True,
            downloads_path=DLDIR,
            args=["--no-sandbox", "--window-size=1500,1000"],
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(URL, wait_until="domcontentloaded", timeout=45000)
        await cf(page)
        await page.wait_for_timeout(4000)

        # клик по карточке артефакта: находим текст needle, лезем к кликабельному предку
        opened = await page.evaluate(
            """(needle) => {
          const all=[...document.querySelectorAll('*')];
          // последний элемент в DOM с этим текстом и в теле чата (x>350) = карточка, не сайдбар
          let target=null;
          for(const e of all){
            if((e.childElementCount===0||e.tagName==='DIV') && (e.innerText||'').includes(needle)){
              const r=e.getBoundingClientRect();
              if(r.x>350 && r.width>80) target=e;
            }
          }
          if(!target) return 'NO_TARGET';
          // подняться к кликабельному предку
          let n=target, hops=0;
          while(n && hops<8){
            const role=n.getAttribute&&n.getAttribute('role');
            if(n.tagName==='BUTTON'||n.tagName==='A'||role==='button'||n.onclick){ n.click(); return 'CLICKED:'+n.tagName; }
            n=n.parentElement; hops++;
          }
          target.click(); return 'CLICKED_LEAF';
        }""",
            NEEDLE,
        )
        print("OPEN=" + str(opened))
        await page.wait_for_timeout(4500)
        await page.screenshot(path="/tmp/dl-step1.png")

        # ищем кнопку Download в открытой панели
        dl_ok = False
        for sel in [
            'button:has-text("Download")',
            '[aria-label*="Download"]',
            'a:has-text("Download")',
            "text=Download",
        ]:
            try:
                b = page.locator(sel)
                if await b.count():
                    async with page.expect_download(timeout=15000) as di:
                        await b.first.click(timeout=5000)
                    d = await di.value
                    dest = os.path.join(DLDIR, d.suggested_filename or "artifact.md")
                    await d.save_as(dest)
                    print("DOWNLOADED=" + dest)
                    dl_ok = True
                    break
            except Exception as e:
                print("DL_TRY(" + sel + ")=" + str(e)[:90])
        await page.screenshot(path="/tmp/dl-step2.png")

        # перенести самый свежий файл в OUT
        files = sorted(glob.glob(DLDIR + "/*"), key=os.path.getmtime)
        if files:
            shutil.copy(files[-1], OUT)
            print("SAVED=" + OUT + " (" + str(os.path.getsize(OUT)) + " bytes) from " + files[-1])
        else:
            print("NO_FILE opened=" + str(opened) + " dl_ok=" + str(dl_ok))
        await ctx.close()


asyncio.run(main())
