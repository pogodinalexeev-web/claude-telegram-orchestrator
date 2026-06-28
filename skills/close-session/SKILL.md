---
name: close-session
description: "[ARCHIVE, DO NOT USE] Absorbed into /end-day — decision sedimentation, skill harvest, glossary check, and plain-language block are now there, covering yesterday+today. File kept for history only. Any closing (day/thread/'wrap up'/'final') → use /end-day. DO NOT trigger this skill."
---

> ⚠️ **ARCHIVE.** This ritual was absorbed into `/end-day` (decision sedimentation + skill harvest + glossary + plain-language block moved there, coverage extended to yesterday+today). File preserved for history only. Don't use — for any closing use `/end-day`.

# /close-session — thread closing ritual (ARCHIVE)

Closes a **thread of decisions**, not a calendar day. May coincide with `/end-day`, may not. Goal — the self-improvement loop is **visible**: the ritual surfaces it, the owner doesn't catch bugs manually.

## Architecture — one phase via subagent

Subagent `model: sonnet` — fix for "one LLM worker and judge". Main thread keeps only the final report.

After return — main thread compiles the report and **waits for the owner's reaction to flags**.

## When NOT to run

- Session was a single question / fact-check — nothing to close.
- Session ended without decisions (just chatted / reviewed material).
- `/end-day` already ran and covered all session decisions — don't duplicate.

---

## Part 1 — operational check: sedimentation-revision

**Goal:** ensure session decisions physically settled in a file and that file sits in a place with a revision cycle that picks it up.

**Subagent prompt (Sonnet — different model, not opus of the main thread, fixes "one LLM worker and judge"):**

> Given: list of decisions from the current session (pass in the prompt — what was discussed and where it was decided to place it).
>
> For each decision:
> 1. Read the target file, verify the line/block is physically there.
> 2. Determine the revision cycle of this file:
>    - `Tasks/tasks.md` #next → `/daily-prep` (near layer) / `/process-inbox`
>    - `Tasks/ideas.md` → `/weekly-review` step 4a (far layer, 3 passes → decision)
>    - `Tasks/manual.md` → selectively when vault rules are edited
>    - `Projects/<X>/tasks.md` → `/daily-prep` step 3a (for 🟢-projects) / weekly-review
>    - `Projects/<X>/ideas.md` → `/weekly-review` step 4a (far project layer)
>    - `Projects/<X>/manual.md` → touch when working with project + weekly-review
>    - `Projects/<X>/log.md` → touch + weekly-review (chronicle, by dates)
>    - `Projects/<X>/journal/` → **not ritually revised**. Return — via pointer in `Projects/<X>/ideas.md`, which falls under idea revision in weekly.
>    - `Journal/log.md` → each new session (startup hook appends the tail)
>    - `status.md` dashboard → each new session (startup hook)
>    - trigger in a loop ("when X — return")
> 3. If the place **has no revision cycle** — flag.
> 4. Decisions that were discussed but not recorded — separate flag.
> 5. **Operational check by affected projects.** For **each project** mentioned/touched in the thread — run through v3.6 checklist:
>    - [ ] `Projects/<X>/manual.md` — **does NOT contain** a state block ("## Where we are", "*relevant as of DD.MM*", dated state headers). State lives **only** in `status.md`, chronicle — in `log.md`. If manual has a dated state block — flag, propose to remove and replace with a one-line reference `> State — in [status.md](...). Chronicle — in [log.md](log.md). Tasks — in [tasks.md](tasks.md).`
>    - [ ] `Projects/<X>/log.md` — thread events (loop closures, decisions, forks) recorded new at top with date
>    - [ ] `Projects/<X>/tasks.md` — closed loops **moved to project log.md and deleted from tasks.md** (rule "closed = moved"); new ones added; `#next`/`#waiting` statuses correct; `#waiting` has a trigger
>    - [ ] `Projects/<X>/ideas.md` — new project ideas added with date; pointers to fresh documents from `Projects/<X>/journal/` (if created in the thread) are in place
>    - [ ] Root `status.md` — this project's line in current form (mode + State + Waiting)
>
>    Don't audit projects NOT touched in the thread — that's `/weekly-review` work.
> 6. **Root operational files check.** Run through v3.6 checklist:
>    - [ ] `Tasks/tasks.md` — what moved to project is **deleted from source**; what closed in this session — **deleted** (fact is in Journal/log.md); no zombie links
>    - [ ] `Tasks/ideas.md` — graduated ones removed, new ones with date
>    - [ ] `Tasks/manual.md` — if vault rules changed (formulations, template, new gates) — reflected; `last_*` header updated on structural edits
>    - [ ] `index.md` — if vault structure changed (renamed folders/files, changed template, added/removed categories), reflected correctly; `last_rebuilt:` updated
>    - [ ] `Resources/glossaries/tech-jargon.md` — new anglicisms from the session added (language gate)
>
>    Day journal (`Journal/YYYY-MM-DD.md`) and `Journal/log.md` — **`/end-day` zone**, don't duplicate.
>
> 7. **Skill harvest.** If a working pattern/technique/rule appeared in the thread that's not in `.claude/skills/` or `audit-mode.md` / `Tasks/manual.md` — flag in a line: `💡 Skill/rule candidate: <essence>`. Don't force doing it now — just don't let it sink in the logs. If obvious duplicate already exists — stay silent.
>
> Return ≤700 words in format:
> ```
> ✅ <decision> → <file:line> → <cycle>
> ⚠️ <decision> → <file> → NO cycle, suggesting: <fix>
> ❌ <decision> — discussed but not recorded
> 🔄 status.md loop "<name>" — description outdated: was <X>, now <Y>. Proposed text: <…>
> 📂 Projects/<X>/<file> — out of sync: <what's not reflected>. Suggesting: <…>
> 🧹 Tasks/<file> — zombie/stale: <what>. Suggesting: <…>
> ```

---

## Report format to owner

After subagent returns — compile the report. Strictly to the point.

```
**Sedimentation-revision:**
<subagent output — flags ✅⚠️❌📂🧹>

**Plain language (required block):**
<3-5 short paragraphs — what was done in the session, what's not closed, action for tomorrow>
```

After the report — **wait for the owner's reaction to flags, don't silently close**.

*Agent behavior revision and debt delta — steps 8 and 8a in `/end-day`.*

**Final ritual line.** When all flags are addressed (owner said "yes/do it" on proposed edits OR explicitly "defer/rest") — the last message is strictly **`All done`** and nothing else. Not "sleep", not "done, rest up", not a summary. Exactly `All done`.

**PROHIBITION on "sleep" in finale + wise wish rule:**

In the "Plain language" block — and in any final part of `/close-session` — **strictly forbidden** to end with the word "sleep" (or "sleep well", "rest", "go to sleep", "rest up" — any wind-down phrasing). This is a flat close of an exhausting thread.

Instead — **one short wise and positively charged wish based on what was closed in the session**. Not generic "everything will work out", but a reference to the specific thing the person went through/saw/decided today. 1-2 sentences, no pomposity, no moralizing, no "well done".

Examples (for tone calibration, not templates):
- *After a personal breakthrough:* "What you saw today isn't going anywhere. Tomorrow it'll be there, even if it feels cooler."
- *After closing a big technical loop:* "Built a tool that will run tomorrow while you have your coffee. Good day."
- *After a pivot / deciding to change direction:* "The decision is made — that's already half the journey. In the morning you'll see where the first step is."

The wish goes **after** the "Plain language" block and **before** the `All done` line — sequence: plain-language block (no "sleep") → wish (1-2 sentences from the session) → `All done`.
