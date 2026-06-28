---
name: weekly-review
description: Weekly PARA revision — go through the week's daily notes, projects, inbox, parking lot. Use for "/weekly-review", "week review", usually on Sunday.
---

# /weekly-review — weekly revision

## When
- Every Sunday evening (or the nearest day with a slot available).
- On explicit command `/weekly-review`.

## Algorithm
1. **Create** `Journal/<YYYY>-W<NN>.md` from `Resources/_templates/weekly-review.md`.
2. **Read yourself** (without delegating) `Journal/<7 dates>.md` for the last 7 days. Extract: wins / Top-3 tails / recurring themes / fatigue signals.

2a. **Run through `## Reflections` for 7 days**. In each `Journal/<date>.md` there's a `## Reflections` section — thoughts/doubts/reflection without actionable seed. Without a return ritual they sink.
   - Collect everything from 7 days.
   - For each entry, decide: (a) **ripened into action** → to `Tasks/ideas.md` or project `ideas.md` with date; (b) **no longer relevant** → delete from the journal; (c) **still needs thinking** → leave for another week.
   - If an entry has been sitting for 3 weeks without action — forced decision "extract or delete", no fourth "let me think".
3. **Also read**:
   - `status.md` — what was planned for the week.
   - Stuck projects via grep: `for p in Projects/*/; do grep -rq "#next" "$p" || echo "STUCK: $p"; done`.
   - `inbox.md`, `Tasks/ideas.md` — process via `/process-inbox`.
   - **All `Projects/<X>/ideas.md`** — everything that exists. This is the "far layer" (project ideas, maybe do or not), revision here is the main cycle (see step 4a).

3b. **Run through `Tasks/tasks.md` section "Open"**. `/daily-prep` picks only dated (step 3a) and project 🟢-tasks. Open ones without date and without project (lifestyle #life, one-off actions without an anchor) — nobody picks them up. Symmetry of step 4a for ideas.
   - Go through each line, assign pass counter `R1 / R2 / R3` (or date of last revision in brackets).
   - On the **third pass without action** — forced decision: (a) **take now** (set a date or do <2 min and delete); (b) **date it** (move to "Date triggers" with YYYY-MM-DD); (c) **delete** as expired.
   - `#waiting` and `#waiting-self` — skip, handled by step 4.

4. **Dashboard state reassessment block** — go through the active project table in `status.md` (modes 🟢🟡🔵🔴⏸🔔).
   - **⏸ Paused** — for each project ask "is the blocker still valid?". If **no** (blocker gone) → forced reassessment: take (🟢) / kill (Archives) / reformulate. Don't auto-leave in ⏸.
   - **🔴 Stuck** — requires a decision each time: take / kill / reformulate. No moralizing, just diagnose.
   - **🔔 On watch** — run: "still relevant / trigger appeared / dead". Trigger appeared → move to active projects or `tasks.md`.
   - **Self-deception check ⏸:** if a project has been in ⏸ for 3 reviews in a row (≈6 weeks) with the same blocker — ask a second question "are you really waiting for the blocker to leave, or is this an excuse?". If "excuse" → 🔴.
   - **First 2 weeks** after setting ⏸ — don't touch (allowed window).
   - **🔴 #waiting-self in `tasks.md`** (doctor, vet, etc) — gentle pass without pressure. If hanging >3 weeks — ask once "remove entirely?", and if left — another 3 weeks without questions.

4a. **Idea revision block (near↔far layer)**. Ideas don't mix into the morning brief (that's noise) — revision happens here, on the weekly cycle.

   Go through two levels:
   - **`Tasks/ideas.md`** — general ideas without a project. Rule: 3 review passes in a row → decision "extract to `Tasks/tasks.md` with a concrete next step / reformulate / delete". Don't leave "let me think" for a fourth time.
   - **All `Projects/<X>/ideas.md`** — project ideas. Same rule: 3 passes → decision. Special attention: **pointers to documents from `Projects/<X>/journal/`** — this is the return point to slow-burn materials. Don't review the journals themselves (forbidden by rule), but review the pointers in `ideas.md` — they pull the document back to consideration if the trigger fired.

   Marking format for passes: each idea line gets a revision prefix: `R1 / R2 / R3` (pass counter). After the third pass without action — forced decision. Alternative to counter — date of last revision in brackets, then count by date difference.

   **If idea extracted to `tasks.md`** (general or project) — line from `ideas.md` is deleted (rule "single source of truth").

5. **Debt-sweep block — full system debt revision.** Canonical GTD (Get Current) / PARA (operating system) place. Run as a separate subagent **`model: sonnet`** (different model, not opus of the main thread — fixes "one LLM worker and judge"). Main thread receives only the summary. Scan vault for passive debt:
   - **Old loops in status.md** — no movement >14 days (by `Journal/log.md` mentions). List with age in days.
   - **Stale loop descriptions in status.md** — loop written, but reality moved forward (from `Journal/log.md` over the week there's movement, but the loop description is old). Cross-check: for each open loop in status.md → grep its topic in Journal/log.md for 7 days → if fresh mentions not reflected in the loop text → flag with proposed new text.
   - **#waiting without trigger** — `#waiting` marks without "when X — return". Silent hangs.
   - **Places without revision cycle** — files outside `Tasks/`, `Journal/`, `Projects/<X>/manual.md`, `status.md`, where live decisions sit.
   - **Fat files** — `find . -name '*.md' -size +50k` (or wc -l >5000). Split candidates.
   - **Old ideas** — lines in `Tasks/ideas.md` older than 30 days. Graduate / let go / reformulate?
   - **Stuck projects** — `Projects/<X>/manual.md` without edits >30 days and without loop in status.md. Archive?
   - **Duplicates in memory** — two feedback files about the same thing.
   - **Old loops in project `log.md`** — event sits in the log, but didn't close the loop in `tasks.md` (out of sync between fact and task state).
   - **Return to journals — via `ideas.md`, not ritual pass.** Don't ritually review `Projects/<X>/journal/` (rule: slow burn by Forte/Matuschak). If a journal document is needed — a pointer to it must live in `Projects/<X>/ideas.md`, and then it falls under the idea revision of step 4a.

   Top-10 findings by "rotting" with a concrete proposal (graduate / archive / split). If nothing critical — "healthy week".

6. **Skill-harvest block — skill candidates from the week**. Run as a separate subagent **`model: sonnet`** (not opus — "one LLM worker and judge" fix) **in parallel with debt-sweep** (main thread receives only the summary list). Goal — catch "I'm doing this manually again" and propose wrapping it in a skill/command.

   What the subagent does:
   - Scans Claude Code jsonl sessions for 7 days.
   - Also — bot sessions on the server (optional, if ssh available).
   - Looks for **repeating patterns ≥3 times with similar output**: same sequences of Edit/Write/Bash, repeating question topics, manual command stitching.
   - Candidate format: `name — what it does — N repeats in the week — example session`.
   - Filter: discards one-offs (1-2 repeats), episodic (repeat in 1 day doesn't count), and already existing as a skill (cross-check with `.claude/skills/` and `.claude/commands/`).
   - **Does not propose** skills for what's already automated by hooks/crons.

   Main thread receives: top-5 candidates + 1-line justification. Owner decides per each: (a) create skill now / (b) put in `Tasks/skill-candidates.md` to ripen / (c) discard.

   If nothing strong — "this week without clear skill patterns". This is a valid outcome.

7. **Fill** `Journal/<YYYY>-W<NN>.md`:
   - Week's wins.
   - Tails.
   - Decisions made during the week (from project `log.md` for 7 days).
   - What's in focus next week.
   - Energy / health / mood (trend).
   - **Debt-sweep result** — top-3 findings and what to do with them.
   - **Skill-harvest result** — top-3 skill candidates (or "no patterns") and what to do with them.
8. **Update `status.md`** for the new week.
9. **Append to `Journal/log.md`** a weekly-review block with a link to the file.
10. **Respond** with a 5-7 line summary + link to `Journal/<YYYY>-W<NN>.md`.

## Rules
- This is reflection, not a report. "Don't know / fell short / didn't understand" are allowed.
- No flattery ("great week"). If the week was weak — call it as it is.
- Target time — 15-20 minutes (debt-sweep + skill-harvest subagents work in parallel, don't eat the main slot).
