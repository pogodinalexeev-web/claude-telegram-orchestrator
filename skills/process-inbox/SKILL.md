---
name: process-inbox
description: Distribute the contents of inbox.md across the PARA structure through an active batch with multiple hypotheses per block. Use when the owner says "/process-inbox", "sort the inbox", "process the captures".
---

# /process-inbox — active batch with hypotheses

## When to invoke
- On explicit command `/process-inbox`.
- During `/end-day`, if `inbox.md` has >5 entries.
- During `/weekly-review` — always.

## Principle

Not "deterministic silently" and not "checkbox yes/no". **Active batch with multiple hypotheses**: for each block the assistant proposes **1-3 orthogonal hypotheses**, the owner confirms / corrects / rejects.

**Why multiple:** one block can be two-part. Example: "Call a contact from Company X" = (a) task-call + (b) person-mention. One hypothesis doesn't cover it.

**Hypothesis axes:** task / person / atom / clipping / research / project material / idea / thought.

## Algorithm

1. **Read** `inbox.md`, `Tasks/ideas.md`, `Tasks/tasks.md`.
   - Canonical block separator in `inbox.md` — line `---`. Each block = `---` + line `YYYY-MM-DD HH:MM (source)` + text.
   - Transition period: old blocks with header `## YYYY-MM-DD HH:MM` without `---` may exist — process them the same way, don't rewrite format retroactively.
2. If files are large — read in targeted slices, **don't delegate to a weaker model** (Opus only).

**Closed platforms (IG / TikTok / X with login wall):**
When a block contains a URL with `instagram.com` / `tiktok.com` / `x.com` / other login-walled platforms — `WebFetch` redirects to `/accounts/login/` or Cloudflare, doesn't return content. Route:
- First try `WebFetch` — sometimes returns meta-description or first carousel slide.
- If blocked — use Playwright/browser MCP tools. Take a fullPage screenshot, read it visually, classify the content.
- IG carousels with `?img_index=N` — playwright shows first slide; to see slide N — ask the owner to send a screenshot of that slide.
- Profiles behind IG login wall — playwright also redirects. Alternative — ask the owner to copy the bio manually, or WebSearch by handle.
- Close browser in the end.

3. **For each block — compile hypotheses.** For each block — 1-3 orthogonal hypotheses on different axes. Don't try to "guess the one right one". Example output for one block:
   ```
   inbox 10:09 "Call a contact from Company X"

   Hypothesis 1 (task): Tasks/tasks.md → ## Not urgent "call with contact from X"
   Hypothesis 2 (person): create Projects/LIFE/People/ContactName.md (tier-1 — mentioned for the first time)

   Confirm both / only one / correct.
   ```
4. **Clarifications by hypothesis type:**
   - **<2 min action** → execute immediately (if safe), mark as done.
   - **#task in project** → move to `Projects/<project>/tasks.md` as `- [ ] #next <text>`. **Don't duplicate in `Tasks/tasks.md`** — project file is the single source of truth. Ongoing obligations (`Projects/LIFE/Home/`, `Projects/LIFE/Car/`, `Projects/LIFE/People/<name>/`) — valid targets same as regular projects.
   - **Date trigger** (action in N days/months, or hard deadline with time) → section `## Date triggers` in `Tasks/tasks.md` (if no project) or `Projects/<X>/tasks.md` (with project). Format: `- [ ] YYYY-MM-DD — <action>`. Picked up by daily-prep step 3a with filter `date ≤ today+7d`.
   - **Event with specific time** (meeting, call) → `Journal/<YYYY-MM-DD>.md` section `## Events` + if action needed before the event → a line in `tasks.md` with back-reference `(event Journal/<date>.md)`, and in `## Events` — link `→ tasks.md`. The link is mandatory.
   - **#task without project** → line `- [ ] #next <text>` in `Tasks/tasks.md` → section `## Now` (#next), `## Not urgent` (#soon), or `## Research` (research / Spike).
   - **#idea without project** → `Tasks/ideas.md` (if not already there).
   - **#idea for project** → `Projects/<project>/ideas.md` — far layer, revised in `/weekly-review` step 4a.
   - **project material (research, forward, mini-doc)** → file in `Projects/<project>/journal/<YYYY-MM-DD phrase in words>.md`. **Required step:** add a pointer line to `Projects/<project>/ideas.md`: `<topic in one phrase> — [journal/<name>.md](journal/<name>.md)`. Without the pointer the journal becomes a silo — the pointer falls under the weekly idea revision.
   - **knowledge/fact** (short, meaningful) → `Resources/atoms/<slug>.md` via template `Resources/_templates/atom.md`. Large raw material → `Resources/chat-logs/raw/`.
   - **about a job/hiring** → `Projects/IT/Brain-dumps/YYYY-MM-DD.md` (or relevant project journal).
   - **decision** (project decision) → a line in `Projects/<project>/log.md` (new at top, date + what was decided + why).
   - **person mention** (name, not from `Projects/LIFE/People/`) → separate hypothesis "create `Projects/LIFE/People/<name>.md` tier-1". Don't do silently.
   - **doubt / thought / reflection** (no actionable seed) → `Journal/<YYYY-MM-DD>.md` → section `## Reflections`.
   - **block with marker `hypothesis:`** at the start of the block text → **don't** route to regular PARA. Create a skeleton entry in `decisions-log.md` as a new section at the top:
     ```
     ### YYYY-MM-DD — <name from block text> *(incoming, requires /audit)*

     - **Niche / what:** <capture text>
     - **Signals (at the time):** — (not assessed)
     - **Client channel:** not found.
     - **Expert:** none.
     - **My confidence:** **L0** (not verified). Entry from inbox, needs `/audit`.
     - **Date:** YYYY-MM-DD.
     - **Outcome:** **under review.**
     ```
     After recording — one line to the owner: "📌 Hypothesis `<name>` → decisions-log.md as L0 skeleton. Run `/audit` now?". Don't auto-run audit.
   - **block with marker `**DEFERRED ...**`** at the start — skip (owner said "later").
   - **block with marker `**ROUTED ...**`** — leftover from previous session, didn't finish cleanup. Move to `Journal/<date-of-original-block>.md` and delete from `inbox.md`.
5. **Confirmation from owner** — three response forms for a block:
   - "yes" / "✓" / hypothesis-numbers — apply the selected.
   - "not right, <correction>" — apply the correction.
   - "later" — leave block in `inbox.md` unchanged, mark `**DEFERRED <date>**` at the start of the block.
6. **Optional mini-audit** at the end of the batch — "did I understand correctly what to do with each?". Can be skipped.
7. **Archive and clean** `inbox.md`:
   - Each routed block is **moved** to `Journal/<date-of-original-block>.md` (by date from the block header, not routing date) under section `## Inbox routing`. If the journal file doesn't exist — create it. If section already exists — append to the end.
   - In the journal, put the block whole (original text + marker `**ROUTED <date> →** ...` where it went). This is a situational trace: "what I captured that day and where it ended up."
   - From `inbox.md` the block is **fully deleted** — buffer stays thin. Technical trace preserved in git history.
   - File header, template comment, and blocks with marker `**DEFERRED ...**` (owner said "later") — leave.
8. **Report** in one block:
   ```
   ✓ Inbox processed:
   - N tasks → Projects/... + tasks.md
   - N ideas → ideas.md
   - N people → Projects/LIFE/People/...
   - N atoms → Resources/atoms/
   - N reflections → Journal/<date>.md
   - N executed immediately
   - N deleted
   - N deferred
   ```

9. **Zombie-check against rules** *(catch loops made obsolete by a rule, between weekly-reviews):*
   - Read `Tasks/manual.md` fully, especially recent sections with trigger dates and phrases "abolished / deleted / no longer doing / rule cancels / X not needed".
   - For each such rule go through `status.md` (open loops) and project `tasks.md` — are there loops whose description contradicts the rule?
   - Found zombies — flag to the owner in one line per loop: `⚠ Loop "<name>" in <file> contradicts Tasks/manual.md rule "<rule>". Close?`.
   - Don't close silently — owner confirms "yes" / "no, leave it, different context".
   - Check time — 30-60 seconds, no deeper. If more than 5 rules — only go through rules added since the last `/process-inbox`.

## Rules

- **Don't invent bindings.** If it's unclear which project — hypothesis "leave in `Tasks/ideas.md` with note `#triage`".
- **Don't rewrite the owner's wording** without reason. Preserve the owner's voice.
- **With multiple hypotheses** — formulate **orthogonally**, not as variations of one. "Task / person" — orthogonal. "Task in Project-A / task in Project-B" — variations of one axis, extra noise.
- **Forbidden to write in `Journal/log.md` and `status.md`.** The skill is only responsible for routing inbox content. No retro blocks, git hashes, "next steps" in the log — that's the job of `/end-day` and the human.
- **No git commands inside the skill.** Push/pull/rebase is done by the sync script or the human. The skill edits files; the auto-edit hook commits.
