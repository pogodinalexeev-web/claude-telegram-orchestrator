---
name: daily-prep
description: Create/open the daily note and give 3 priorities for the day based on status.md, Journal/log.md, and open #next items in projects. Use for "/daily-prep", "good morning", "what's on today".
---

# /daily-prep — morning brief

## Algorithm
1. **Date:** take today's date in YYYY-MM-DD format.
2. **Create** `Journal/<date>.md` from `Resources/_templates/daily.md`, if it doesn't exist yet. If it exists — open the existing one, don't overwrite.
3. **Read context** (should already be loaded by the session, but refresh if not):
   - `status.md` — main track, blockers, project states (modes 🟢🟡🔵🔴⏸🔔).
   - `Journal/log.md` — last 20 lines (what happened yesterday, open loops).
   - Open `#next` via grep: `grep -rn "#next" Projects/ | grep -v "^\s*<!--"`.
   - Current month roadmap/star document — north star + current week.
3a. **Run through `Projects/<X>/tasks.md` in 🟢-mode + root `Tasks/tasks.md` + date triggers**. Sources for picking live work:
   - **Root `Tasks/tasks.md`** — all open `#next` without a project, especially with `#soon` or 🔴.
   - **Project `Projects/<X>/tasks.md` in 🟢-mode** (from `status.md` dashboard). "Now in queue" section or first block of open loops.
   - **`## Date triggers` sections** in `Tasks/tasks.md` and in all projects' `Tasks.md`. Line format: `- [ ] YYYY-MM-DD — <action>`. Filter: `date ≤ today+7d`. What falls in the window — candidate for top-3 or at least a reminder. Distant triggers (>7 days) — don't show, don't add noise.
   - **Events today** — `## Events` section in `Journal/<today>.md`. If any — mention separately as "📅 Events today".
   
   Pick 1-2 live loops as candidates for top-3 today. **Don't read project `journal/` or project `log.md`** — that's slow burn, not picking.
4. **Formulate 3 priorities for the day**, based on:
   - North star of the month (from `status.md`).
   - **At least one of the three must explicitly advance the north star.** If no connection — say so honestly in one line.
   - Open loops from `Journal/log.md`.
   - Live loops from `Projects/<X>/tasks.md` in 🟢-mode (step 3a).
   - Nearest #next from the main track.
5. **Write to Journal/<date>.md** in the "Top 3 for today" section.
6. **Respond** in chat briefly:
   ```
   ☀ <date>, <weekday>
   Top 3:
   1. <first priority>
   2. <second>
   3. <third>
   
   Open loops from Journal/log.md: <if any>
   Energy / mood / sleep — fill in the note.
   ```

## Rules
- Don't invent priorities. If nothing clear from status and log — honestly say "I don't see anything urgent from the vault, what's in focus?"
- No more than 3 priorities. If you want more — pick the 3 heaviest.
- Account for context: Friday/weekends/sick yesterday — adjust the tone.
