---
name: end-day
description: Close the day across everything done yesterday+today (both sessions — Telegram bot and desktop — + all operational logs). Processes inbox, verifies decision sedimentation via subagent, checks duplicates across layers, updates Journal/log.md + status.md + index.md, agent review, push. Absorbed /close-session. Use for "/end-day", "close the day", "see you tomorrow", "wrapping up", "closing the session", "final", "finalizing", "closing the thread".
---

# /end-day — closing the day (absorbed /close-session)

> Closes **the calendar day across everything done yesterday+today** — both sessions (Telegram bot `<date>.md` + desktop `<date>-mac.md`) and all operational logs. Decision sedimentation happens here (formerly in `/close-session`, now archived).

## Algorithm

### Step 0: process inbox
If `inbox.md` has at least one entry — run `/process-inbox` **before** everything else. The skill runs a full batch with hypotheses, clears the buffer. Without this, end-day closes the day with unprocessed captures.

### Step 0a: operational file cross-check
**⛔ BLOCKING. Step 1 (day review) does not start until "Report 0a" (see below) is output.**

**Read strictly in order — first the whole layer, then the next:**

**Layer 1 — tasks:**
1. `Tasks/tasks.md`
2. `Projects/<X>/tasks.md` for **each project in `Projects/`** (if the file exists)

**Layer 2 — ideas:**
3. `Tasks/ideas.md`
4. `Projects/<X>/ideas.md` for each project in `Projects/` (if the file exists)

**Layer 3 — manual (project descriptions):**
5. `Projects/<X>/manual.md` for each project in `Projects/`

**Layer 4 — journals:**
6. **Read the content** of files in `Projects/<X>/journal/` with mtime today for each project in `Projects/` (not just a list — read, otherwise duplicate check is impossible)
7. `Journal/<date>.md` if created

**Layer 5 — project log.md:**
8. Check for existence of `Projects/<X>/log.md` for each project where `tasks.md` changed today. If a [x]-task is found in tasks.md but log.md **doesn't exist** — create it. If it exists — ensure the closure fact is there.

**Check per layer:**
- Task simultaneously in `Tasks/tasks.md` AND in `Projects/<X>/tasks.md`? → duplicate tasks
- Idea simultaneously in two `ideas.md` in full text (not a pointer)? → duplicate ideas; a pointer-link is acceptable
- `[x]`-task not moved to `log.md`? → violation of "closed = moved/deleted"
- Loop in `status.md` AND in `Projects/<X>/tasks.md`? → duplicate layers
- Journal file content duplicates another journal file (not a pointer)? → duplicate journal

**Mandatory output after reading — step not counted without it:**
```
### Report 0a
- Read tasks (layer 1): Tasks/tasks.md + [list of project tasks.md by all modes]
- Read ideas (layer 2): Tasks/ideas.md + [list of project ideas.md]
- Read manuals (layer 3): [list of projects — manual.md]
- Read journals for today (layer 4): [list of files or NONE]
- Checked project log.md (layer 5): [list of projects with changed tasks.md or NONE]
- Duplicates: [list or NONE]
- Incorrectly placed tasks: [list or NONE]
- [x]-tasks not moved: [list or NONE]
```
Found duplicate → flag in one line: `⚠ Duplicate: <what> in <file A> and <file B>. Delete from A / B?`
Wait for confirmation. Don't silently clean up.

1. **Read** today's `Journal/<date>.md` (if any).
2. **Compile a factual review for yesterday+today** (to avoid asking the owner from scratch). Coverage — **two calendar days** (yesterday and today), not just the current session. Sources:
   - `Journal/log.md` blocks with yesterday and today dates;
   - `git log --since="yesterday 00:00" --oneline` in the main vault;
   - `Journal/<yesterday>.md` and `Journal/<today>.md` (if any);
   - **both conversation log streams for both days**: `Resources/chat-logs/processed/<yesterday>.md`, `<yesterday>-mac.md`, `<today>.md`, `<today>-mac.md`;
   - current session history.

   Output in one block of 3-5 lines format "factually today was: <topic>". No assessments, no filler:
   ```
   ### Day in review
   - <topic 1 in one line>
   - <topic 2>
   - <topic 3>
   ```
2a. **Decision sedimentation for yesterday+today — subagent `model: sonnet`** (different model, not the one that worked — fixes "one LLM worker and judge"):

   **Given to subagent:** list of decisions for both days (compile from step 2 review — what was decided and where it was placed) + list of affected projects.

   **Prompt to subagent:**
   > For **each decision** for yesterday+today:
   > 1. Read target file — verify the line/block is physically there.
   > 2. Determine the file's revision cycle: `tasks.md`→daily-prep; `ideas.md`→weekly-review; `manual.md`→when rules are edited; `log.md`/`status.md`→each session; `journal/`→through pointer in ideas; trigger in the loop. A place **without a cycle** → flag.
   > 3. Decisions that were discussed but **not recorded** → separate flag.
   > 4. **Skill harvest:** a working pattern/rule appeared that's not in `.claude/skills/` or project rules → line `💡 Skill/rule candidate: <essence>`. Obvious duplicate — stay silent.
   > 5. **Glossary:** new technical anglicisms from sessions not in `Resources/glossaries/tech-jargon.md` → flag `📖 for glossary: <term>`.
   >
   > Return ≤700 words:
   > ```
   > ✅ <decision> → <file:line> → <cycle>
   > ⚠️ <decision> → <file> → NO cycle, suggesting: <fix>
   > ❌ <decision> — discussed, not recorded
   > 💡 Skill/rule candidate: <essence>
   > 📖 for glossary: <term>
   > ```

   Show subagent output as "Sedimentation" block. For flags — **wait for the owner's reaction**, don't silently clean up.

3. **Ask the owner on top of the review** (not from scratch):
   ```
   Closing the day. Anything to add or correct?
   - Top 3 done?
   - What didn't work and why?
   - What's #1 for tomorrow?
   ```
   If already said in conversation — don't ask, use it.
4. **Review of captures today.** Go through sources (mtime today or lines with today's date):
   - `inbox.md` — raw captures;
   - `Tasks/ideas.md` — ideas (root, no project);
   - `Tasks/tasks.md` — tasks;
   - `Projects/<X>/ideas.md` — each project's ideas (if file exists);
   - `Projects/<X>/journal/` — new files today in project journals (new only, not full review);
   - `Journal/<date>.md` — daily journal (if created via `/daily-prep`). **Sub-step:** if the file has a `## Evening reflection` section — read it and for each insight propose routing: to project/personality files (values/pattern), to `Projects/<X>/log.md` (if it concerns a specific project), to `Resources/atoms/` (if it's a reusable idea). Format: `"<insight>" → <where> — route?`. Don't route automatically, only propose.
   - `Resources/atoms/` — new atoms.

   For each section with fresh content, a separate block:
   ```
   ### <source>
   - <entry> → <opinion: duplicate / parked / ripe / noise / merge with X> → <action: /graduate, /process-inbox, archive, leave>
   ```
   Compactness rules:
   - Empty sections — don't print at all, don't write "no entries".
   - Entries where there's nothing to say besides "noted" — skip.
   - If `inbox.md` has many fresh items and nothing to comment — one line: "inbox.md: N entries, route with `/process-inbox` now/morning?".
5. **Append to `Journal/log.md`** block:
   ```
   ## YYYY-MM-DD HH:MM
   - done: <short list>
   - open loops: <what's not closed>
   - tomorrow: <#1 priority>
   ```

5a. **Closed loops route — move to project log.md**. If in this session **loops were closed** in `Projects/<X>/tasks.md` — each moves to `Projects/<X>/log.md` in one line with date + one-phrase result. Then deleted from `tasks.md`.

   End-of-day checklist:
   - Go through `git diff --name-only --since=midnight Projects/*/tasks.md` — which project tasks.md changed today.
   - In each — find `[x]` / struck-through `~~text~~` lines added today.
   - For each: add an entry in the corresponding `Projects/<X>/log.md` (new at top, format `**<date> — <name>** — <result in one line>. Details — Journal/log.md <time>.`).
   - Delete this line from `tasks.md`.

   Root `Tasks/tasks.md` — closed items **are not moved, just deleted** (fact is already in Journal/log.md step 5).

5b. **REQUIRED: fill `Journal/<today>.md`** (without this the day is not closed):
   - "Evening → What was done" section — 3-7 fact lines from step 2 review.
   - "Wins / Lows" section — 2-3 wins, 1-2 lows honestly.
   - "Tomorrow — first `#next`" section — fill from step 3 response (#1 for tomorrow).
   - If "Morning → top-3" fields are empty (owner didn't open `/daily-prep` in the morning) — fill retrospectively from facts, what was the priority, and what was the status.
   - Daily note — entry point for tomorrow with `/daily-prep`. Empty template devalues the ritual.

5c. **Reconciliation gate (sanity-check status.md ↔ Journal/log.md)** — before editing `status.md`:
   - Read tail `Journal/log.md` for the day (last ~100 lines or since previous `/end-day`).
   - Read "Open loops" in `status.md`.
   - For each open loop — check: is it mentioned in the log as "✓ / closed / works / done / pushed / in production"?
   - If discrepancy found — flag to the owner **in one line per loop**:
     ```
     ⚠ Reconciliation: loop #N ("short name") — in log today <X, Y, Z>, in status.md still "<open description>". Close / update status / leave?
     ```
   - Wait for confirmation per each → edit `status.md` accordingly.
   - If no discrepancies — write: `✓ Reconciliation: all open loops match the log.`

6. **REQUIRED: update `status.md`** (without this the day is not closed):
   - Change `*Updated: ...*` to current date+time.
   - Move loops closed today from "Open loops" to "Closed <date>".
   - Add new loops if they appeared.
   - Reflect priority/track changes if any.

6b. **Dashboard and priority calibration with the owner** (gate before closing the day):
   - After steps 5-6 — show the owner a state summary:
     - current **dashboard** `status.md` (only Mode + Project + State columns, without "Waiting");
     - **main track** in one line (from `status.md` header);
     - **today's touched projects** — what's in the State column for each (one line per project).
   - Concise. Don't retell the whole project manual.
   - Ask literally: **"Here's the situation. All correct? Anything off?"**
   - If the owner says "correct" — close the day, move on.
   - If editing — record the edit in the appropriate file (`status.md` dashboard / `Projects/<X>/manual.md` / `Projects/<X>/tasks.md`), repeat step 6b until confirmed.

6c. **Sync `index.md` with the dashboard** (update vault map):
   - After dashboard calibration — go through the `index.md` sections for affected project categories.
   - For **each project line** in index.md: compare description (mode + one phrase about state) with the current project line in `status.md` dashboard. If discrepancy — fix index.md.
   - If the dashboard gained/lost a project — reflect in index.md.
   - If a project got a subproject not yet in the map — add it.
   - Update `last_rebuilt:` in index.md frontmatter to today's date with sync note.

6d. **Check for stale state blocks in project manuals**:
   ```bash
   grep -ln "relevant as of 20[0-9][0-9]-[0-9]\|## Where we are now" \
     Projects/*/manual.md Projects/*/*/manual.md 2>/dev/null
   ```
   For each found manual — flag in a separate line:
   ```
   ⚠ manual with stale state: <path>
     Remove the "Where we are now" / "*relevant as of DD.MM*" block and replace with:
     "> State — in [status.md](...). Chronicle — in [log.md](log.md). Tasks — in [tasks.md](tasks.md)."
     — yes/no?
   ```
   Wait for confirmation per each. Don't silently edit.

   If nothing found — `✓ State blocks in manuals: clean.`

7. **If the day followed the main track** — add 1-3 lines to the main project's manual.

8. **Agent review** (subagent `model: sonnet`). Reads the current session's **raw log** + personality/principles/autonomy files.

Three blocks:
- **(a) Reflection** — where a skill/check was missed (with reason); where it was well picked up (symmetry required). Reverse prompt: table `Turn | Was there a reverse? | Where it would have gone` for each significant closed turn.
- **(b) Voice drift** — did I slip into executor mode? Flattery? Anglicisms without translation?
- **(c) Role boundaries** — where the owner does manually what a skill/hook/cron could do.

≤500 words. For each point — a concrete proposal (memory edit, Self edit, new skill or hook). Not "I'll try to remember".

9. **Push to remote**: `git push origin main` — so the morning brief sees the fresh status.md. If push fails — report to the owner, don't stay silent.

9b. **"Plain language" block** (required, absorbed from `/close-session`). Before the wise wish — a short simple recap, no jargon:
   - 3-5 short paragraphs, each one thought.
   - Structure: **what was done today** (one phrase) → **what I noticed about myself** (main signal from the agent review step 8, in plain words) → **what's not closed** (main flag from sedimentation step 2a / reconciliation 5c) → **action for tomorrow**.
   - Synthesis, not a retelling of steps. Empty point (∅) — skip. **No "sleep"** in this block — the farewell goes separately in step 10.

10. **Final wise wish** *(to avoid flat closings of demanding days):*

   **STRICTLY FORBIDDEN** to end the day with the word "sleep" (or "sleep well", "rest", "go to sleep", "rest up" — any wind-down phrasing). This is a flat close of an exhausting day.

   Instead — **one short wise and positively charged wish based on what was closed during the day**. Not a generic "everything will work out", but a reference to the specific thing the person went through/saw/decided today. 1-2 sentences, no pomposity, no moralizing, no "well done".

   Examples (for tone calibration, not templates):
   - *After a breakthrough in a personal session:* "What you saw today isn't going anywhere. Tomorrow it'll be there, even if it feels cooler."
   - *After closing a big technical loop:* "Built a tool that will run tomorrow while you have your coffee. Good day."
   - *After a pivot / deciding to change direction:* "The decision is made — that's already half the journey. In the morning you'll see where the first step is."

11. **Finish** in one line: `✓ day closed. Journal/log.md and status.md updated, pushed.` — then empty line, then wish from step 10. No "See you tomorrow", "Sleep", "Rest" in the finale.

## Rules
- Don't turn this into a long reflection. Goal — record, not analyze.
- Don't give assessments ("well done/bad"). Facts only.
- If less than Top 3 was done — that's normal, no moralizing.
- In the finale — **never** "sleep"; always a wise wish based on the day (see step 10).
