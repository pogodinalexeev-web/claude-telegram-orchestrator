---
name: deep-research
description: Launch Deep Research in claude.ai — enable Research mode, paste prompt, click start. Doesn't wait for result — owner picks it up later. Two paths: from Mac via live Chrome (Claude_in_Chrome MCP), from VPS bot via stealth-browser (patchright + saved session). Use with "/deep-research <topic>", "launch research", "deep research on X", "let Claude dig in".
---

# /deep-research — launch Deep Research in claude.ai

> Two launch paths. **From Mac** — section "Path A (Mac)". **From VPS bot** — section "Path B (VPS)" below. Self-selection: if running in bot on server — Path B; on laptop — Path A.

> **⚠️ REPORT MUST BE IN RUSSIAN (rule 18.06.2026).** Deep Research on a Russian prompt still tends to output the final document in English. To prevent this — **always append to the end of the prompt**: `Финальный отчёт пиши на русском языке.` Put the same requirement in `CLARIFY_ANSWER` (answer to the clarifying question). If the report still came out in English — send to the same chat via `answer.py` with `ANSWER='Переведи документ на русский, сохрани структуру, цифры и источники'`.

## Path A (Mac, Claude_in_Chrome)

### What to do

1. Take topic from arguments (everything after `/deep-research `). If empty — ask one line: "What topic to research?".
2. If topic is short (one or two phrases) — expand into a proper Research prompt: what exactly to look for, what timeframe, what outcome is expected. If owner gave a ready long prompt — don't touch.
3. Via `Claude_in_Chrome` MCP:
   - `tabs_create_mcp` → `https://claude.ai/new`
   - `read_page` to see current state (login alive, input field available).
   - Enable **Research** mode (button/toggle near the input field). If button not found — `take_screenshot` and ask owner where it is now (claude.ai UI changes).
   - **Paste the prompt IN ONE SHOT.** Field is ProseMirror (`<div contenteditable>`), `form_input` on it fails with "Element type DIV is not supported". Prompt contains `\n` — newline character in `computer.type` is interpreted as Enter and submits a fragment (bug 14.06.2026, hit twice). So:
     - **DON'T** use `\n` inside `computer.type` — replace newlines with ` // ` or another inline delimiter, or compose prompt without newlines at all (numbering `1) ... 2) ...` reads fine).
     - Alternative: `type` in short blocks without `\n` via multiple calls, each block without newline at end.
     - After `type` click the **send button** (`find` "Send message button"), NOT Enter — Enter may pick up buffered newlines.
4. Wait for chat to start. **If claude.ai asked clarifying questions before starting research** (typical with long prompts) — read them via `read_page` / screenshot, answer from prompt context (or from vault if not in prompt), submit using same method (no `\n`), wait for Research to actually start (indicator "Research complete" / "N sources" appears later; at answer time there should be a "searching / thinking" indicator).
5. Grab tab URL and return to owner one line:
   ```
   ✓ launched: <url>
   ```
6. **Don't wait for research to complete.** Owner will go get the result when ready.

## What NOT to do

- Don't open via `playwright` / `chrome-devtools` — no logged-in claude.ai session there. Only `Claude_in_Chrome` (owner's live Chrome).
- Don't repeat the prompt back to owner before launching. If expanded — launch right away; if owner is unhappy, they'll redo it.
- Don't auto-save result to vault. That's a separate step on owner's request.
- Don't comment on research topic, don't evaluate "good question". Just launch.

## If something broke (Path A)

- Login expired / claude.ai asks to sign in → stop, tell owner one line: "claude.ai not logged in, sign in manually".
- Research toggle not found → screenshot + ask "where is Research button now?".
- Network error / page doesn't load → stop, don't retry silently.

---

## Path B (VPS, stealth-browser) — bot on server

Bot on VPS has no live Chrome. Launch goes through a real browser on server (`stealth-browser`: patchright + Xvfb + saved session `~/browser/claude/auth.json`). Tested live — works end-to-end.

### Scripts — `~/browser/claude/`

| Script | What it does | Env |
|---|---|---|
| `research.py` | `claude.ai/new` → "+" → **Research** → prompt → Send → URL. Then waits for FIRST Claude response (phase 2, see below) | `PROMPT` (required), optional `CLARIFY_ANSWER` / `AUTO_BROAD=1` |
| `answer.py` | Send a response to an ALREADY open chat (to a clarifying question). Verifies editor cleared = sent | `CHAT_URL`, `ANSWER` |
| `read_chat.py` | Dump chat state (texts `.font-claude-message`, screenshot `/tmp/read-chat.png`) | `CHAT_URL` |

All three must run **via `xvfb-run`** (without it — `Missing X server`, crash). Launching research:
```bash
cd ~/browser && source venv/bin/activate
PROMPT='<expanded prompt in ONE line>' xvfb-run -a -s "-screen 0 1400x900x24" python3 claude/research.py
```
Chat URL is printed as `RESULT_URL <url>` and written to `/tmp/claude-research-url.txt`. Log — `/tmp/claude-research.log`, screenshots `/tmp/research-*.png`.

### ⚠️ Deep Research FIRST asks a clarifying question (phase 2)

**Key lesson:** on a substantive prompt Deep Research almost always **first asks a clarifying question** (text turn) and starts digging ONLY after the answer. **Orange spinner ≠ research started** — Claude is generating the clarifying question. Don't report "launched" based on the spinner.

`research.py` after sending the prompt waits for the first response (`FIRST_RESPONSE: ...`) and decides:
- Text looks like a question (ends with `?` / "clarify" / "before" / etc.) AND `CLARIFY_ANSWER` is set (or `AUTO_BROAD=1` → auto-answer "as broadly as possible") → types answer, sends, prints `CLARIFY_ANSWERED`.
- Looks like a question but no answer provided → prints `PENDING_CLARIFY` + URL. Then **send answer from prompt context** via `answer.py`:
  ```bash
  CHAT_URL='<url>' ANSWER='<answer in one line>' xvfb-run -a python3 claude/answer.py
  ```
  `answer.py` reports `ANSWER_SENT` (editor cleared = sent) or `ANSWER_STUCK` (not sent, text stuck — retry). **`ANSWER_SENT` is the only honest confirmation of sending; don't rely on spinner.**
- Answer to clarifying question should be composed **from the prompt itself** (owner provided it): for each point — broadest capture, true to original goal. No data in prompt — check vault.

After answering the question — **re-read chat** (`read_chat.py` + check `/tmp/read-chat.png`), confirm REAL research appeared (progress/sources), not another question. Only then report "✓ launched" to owner.

### claude.ai UI map (as of 18.06.2026)

- Input field — `div.ProseMirror`.
- "+" button — `button[aria-label="Add files, connectors, and more"]`.
- **Research lives INSIDE the "+" menu** (not on input panel!), item with magnifier icon, next to "Web search". Clicking "Research" enables mode — blue magnifier lights up at bottom of composer.
- Send button — `button[aria-label="Send message"]` (appears after text is entered).

### Important

- **Prompt must be substantive.** On a trivial/"test" request Claude responds briefly WITHOUT a full research. Expand a short topic into a proper research prompt — as in Path A.
- **No `\n` in prompt** — newline in ProseMirror sends a fragment (same bug 14.06). `research.py` collapses newlines to spaces; keep numbering `1) ... 2) ...` in one line.
- Session expired (claude.ai asks to sign in) → re-login per `stealth-browser` Recipe B1 (magic link from owner). Session lifetime ~30 days.
- Don't wait for research to complete — return URL and done. Owner picks up result themselves.

### How to extract the READY report to a file (export, Path B)

Deep Research report is in the chat as a **separate document artifact** (right panel). The page body only shows a preview snippet — `read_chat.py` does NOT return full text. Two tools (both in `~/browser/claude/`, run via `xvfb-run`):

| Script | When | Env |
|---|---|---|
| `download_artifact.py` | Document has a **Download** button (only when Claude explicitly "Created a file", e.g. after "translate" request — it recreates document as downloadable `.md`). Returns CLEAN markdown | `CHAT_URL`, `NEEDLE` (fragment of card title), `OUT` |
| `dump_report.py` | Document has NO Download (regular artifact). Opens card and gathers text by SCROLLING the panel. Returns text (markup sometimes simplified, but content is complete) | `CHAT_URL`, `NEEDLE`, `OUT` |

```bash
cd ~/browser && source venv/bin/activate
CHAT_URL='<url>' NEEDLE='<fragment of document title>' OUT='/tmp/report.md' \
  xvfb-run -a -s "-screen 0 1500x1000x24" python3 claude/dump_report.py
```

**Lessons learned:**
- **Opening the card: `dump_report.py` uses Playwright click** (`get_by_text(NEEDLE).last` + `scroll_into_view` + `click(force=True)`). JS click on text does NOT open the panel (React handler is on the container, not text) — don't go back to it.
- **`download_artifact.py` grabs Download from the first artifact** — if chat has MULTIPLE documents and Download is only on one, it will always export THAT one, ignoring `NEEDLE`. For others — only `dump_report.py` (scroll).
- **`NEEDLE` — unique fragment of the CARD title** (e.g. `Client Acquisition and Sales Playbook`), not the research process title ("Research complete").
- One chat can have **multiple reports** (owner asks follow-up questions → new research rounds). Each — its own card, extract per separate `NEEDLE`.
- If chat has orange spinner — **owner may be working in it manually right now** (one claude.ai account). Don't automate in parallel, ask/wait.
- Export to vault — **separate step on owner's request**, not automatic. Store in `Projects/<X>/journal/` + pointer in `ideas.md`.

### Auto-delivery of result (self-notification, Path B)

If owner wants "send me the result when ready" (not go get the report manually) — no separate skill needed, use the standard `__BG_TASK__` mechanism (bot self-notification). After launching research (have `RESULT_URL`), set a background task with watcher `wait_and_dump.py`:

| Script | What it does | Env |
|---|---|---|
| `wait_and_dump.py` | Keeps browser open, polls chat every `POLL` seconds (default 75), waits for disappearance of "N sources and counting" counter (two consecutive checks = done), then exports report by scrolling to `OUT`. Prints `STATUS=DONE/TIMEOUT` + `WROTE=<path>` | `CHAT_URL` (required), `OUT`, optional `MAX_MIN` (35), `POLL` (75) |

Marker in response (one line), bot runs command and starts separate turn with result:
```
__BG_TASK__: {"cmd":"cd ~/browser && source venv/bin/activate && CHAT_URL='<url>' OUT='/tmp/research-report.md' xvfb-run -a -s \"-screen 0 1500x1000x24\" python3 claude/wait_and_dump.py","then":"read /tmp/research-report.md; if DONE — export to Projects/<X>/journal/ + pointer in ideas.md + live summary to owner; if empty/timeout — be honest, give chat link","label":"deep-research <topic>","timeout":2400}
```
Completion detection is fragile (claude.ai UI changes) — if watcher returned TIMEOUT with non-empty chat, grab report manually via `dump_report.py` with correct `NEEDLE`.

### If something broke (Path B)

- `logged_in=False` / redirect to login → session expired, re-login (see above).
- "Research" item not found in "+" menu → claude.ai UI changed, `take_screenshot` (`/tmp/research-*.png` → show owner) and ask where Research is now.
- Cloudflare blocks >45s → bad config anti-pattern, see `stealth-browser` SKILL.md.
