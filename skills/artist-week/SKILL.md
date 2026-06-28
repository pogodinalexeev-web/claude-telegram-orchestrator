---
name: artist-week
description: "Procedural skill for working through a weekly chapter of Julia Cameron's 'The Artist's Way'. Adapts exercises to the owner's current live context from vault (anchor formulas, morning pages, witnesses, creative thread), asks 3-4 pointed questions per week, records answers in journal. Not a role prompt — a working algorithm. Use with `/artist-week`, 'let's do Cameron', 'questions for week X', 'adapt exercises for the week', 'continuing the artist's path'. Experiment started with a friend who is doing the course together."
---

# /artist-week — weekly work with Cameron

## Purpose

A working algorithm (not "role-playing"). Each call — one pass through the current week's Cameron chapter with adaptation to the owner's live vault context.

Main principle: **feeling is primary over thought** (anchor formulas). On "doomed obscurity / nobody cares again" — don't go to logic ("this is a thought, not a fact"), go to acknowledgment and promise to the feeling. Slipping back to logic = harm, not help.

## When called

- Slash command `/artist-week` (with week number or without — takes current from state).
- Owner's words: "let's do Cameron", "questions for the week", "adapt exercises", "continuing the artist's path".
- During `/weekly-review` step — if owner says "continuing" and next week needs to be prepared.

## Required reading at start of each call

In order:

1. **`Projects/LIFE/Personal/artist-path.md`** — current week, focus, arrangement with study partner, 12-week map.
2. **`Projects/LIFE/Personal/anchor-formulas.md` pts. 27-29** — working tools for the thread (gesture + "I'm not leaving", promise to feeling "I won't give up", principle "feeling is primary over thought").
3. **`Projects/LIFE/Personal/witnesses.md`** — pocket for the small: what has already been.
4. **Last file in `Projects/LIFE/Personal/morning-pages/`** — live material from last page.
5. **`Projects/LIFE/Personal/log.md`** — last 50 lines of the `#artist-path` branch for chronicle context.
6. **The chapter of the current week in Cameron's book** — `Resources/attachments/<cameron-book-filename>.fb2`.

Book in windows-1251 and XML wrapper. Extracting needed chapter:

```bash
python3 << 'EOF'
import re
with open('$VAULT/Resources/attachments/<cameron-book-filename>.fb2', 'rb') as f:
    raw = f.read()
text = raw.decode('windows-1251', errors='replace')
text = re.sub(r'<[^>]+>', ' ', text)
text = re.sub(r'\s+', ' ', text)
i = text.find('WEEK N')  # substitute needed week
print(text[i:i+18000])
EOF
```

## Working algorithm (4 steps)

### Step 1. Determine current week

From `artist-path.md` "Week" field. If owner explicitly specified in command (`/artist-week 2`) — use specified, otherwise from file.

### Step 2. Read chapter + identify 3-4 most pointed exercises

Read the week's chapter in Cameron. Identify **3-4 most pointed** exercises / concepts / tasks — not all, not "questions from the book". Criterion for "pointed-ness" — what hooks the **already-recorded nodes in owner's vault** (anchor formulas, morning pages, thread log, current open loops).

### Step 3. Adapt questions to owner's live context

This is **the main step**. Not "questions from the book", but **hook into specific owner's words** in vault:

- Quote their own formulations from morning pages / anchor formulas / log.md.
- Highlight contradictions between their words and Cameron's exercise.
- Refer to witnesses (if there's a counter-fact to a negative belief — mention it).
- Don't supply answers — leave space.

Question format: 1-2 sentences with explicit vault quote + question with specific hook. Not a lecture.

3-4 questions. Optional 5th if there's a particularly hot node — mark as optional.

### Step 4. After owner's answers — record

1. **Verbatim text of answers** → `Projects/LIFE/Personal/journal/YYYY-MM-DD answers to week N Cameron questions.md`.
2. **Live marker** in the same file — one or two phrases of what was noticed (main nerve, counter-fact, connection to another thread).
3. **Chronicle** → one entry at top of `Projects/LIFE/Personal/log.md` tagged `#artist-path`.
4. **Witnesses**: if in answers there was a response from someone to owner's creative work or their own moment of hitting home — **propose** adding a line to `Projects/LIFE/Personal/witnesses.md`. Not silently, not automatically — propose, wait for confirmation.
5. **New anchor formula** (if one was born) → item in `Projects/LIFE/Personal/anchor-formulas.md` + pointer in map "when to return".

## What this skill does NOT do

- **Does not imitate Cameron's facilitator.** "You're an experienced facilitator with 20 years experience" — placebo, doesn't help, wastes space. I stay myself per SOUL.md, just execute the algorithm in this file.
- **Does not lecture about Cameron.** Book is a source of exercises, not material to retell owner. Owner reads it themselves.
- **Does not go to logic "this is a thought, not a fact"** on a live feeling. See anchor formula pt. 29. That's harm, not help.
- **Does not ask more than 4-5 questions per call.** Overload kills the live quality.
- **Does not file answers to other files without owner's explicit agreement.** Only proposes.

## Evening pull-mode

**Push→pull shift.** Earlier a timer at 18:00 itself pushed 4 blocks (questions + micro-exercise + provocation + check-in). This was push — owner didn't call it, content fell by schedule. Feedback: "second week went badly because you pushed the same briefing every day".

Now at 18:00 goes **only a quiet invitation** (`~/bin/evening-invite.sh` — curl with reply button "Let's talk about the artist's path", without Claude). The conversation itself — **on request**: owner presses the button, writes "Let's talk about the artist's path" / "let's do the evening path work", or just starts the topic.

**When owner accepted the invitation — this conversation:**

1. **Lift the WHOLE Cameron week, not one day's question.** Not "here's today's question from the table". Give the whole current week in digestible form — its exercises/tasks entirely (from chapter + from week question map in `artist-path.md`), so owner sees the week as a unified field, not daily slices. Direct fix for the "same briefing" complaint.
2. **Anchor something from the chapter TEXT** (not just exercises). Pull from the week's chapter one live thought/quote/Cameron image — and connect it to what owner has now. The textual part, not tasks. (Direct owner request.)
3. **Frame — fuel gauge** (anchor formula — chest as live-level sensor). Enter via chest state: "purring cat" (full tank) vs "overcast day" (gloom/sadness/grayness = empty tank → feed **now**, not later). Week questions layered on this, not separately.
4. **Checklist — INTERNAL, owner doesn't see it.** 4-block structure (questions / micro-exercise / provocation / check-in "purring cat") stays as MY internal scaffold — I track that conversation touches these facets, but **don't print** it to owner as a list-template. Live conversation, not a form.

Recording answers — as in Step 4 above (journal + log.md + anchor formulas on growth of new one).

## Connection to other rituals

- **`/weekly-review` step** — weekly fork "continue week N+1 or stop experiment". If "continue" — update `artist-path.md` "Week" field.
- **Evening timer 18:00** — now sends only quiet invitation (pull, see "Evening pull-mode" above), not 4 blocks. Conversation unfolds by this skill on owner's request.
- **`/end-day`** — recording answers in log.md and touched files should make it into session/day review.

## Collaborative work with a friend

Experiment is joint. A friend does the same week on their side. After their own pass — owner can tell friend, friend shares on next call.

**Not automated in the skill.** Owner decides what and when to pass. I just remember (recorded in `artist-path.md` "Agreement with friend" field).

## History

- Experiment started, joint agreement with a friend for the week.
- First test of the format "4 adapted questions for week 1". Owner answered by voice. Main nerve: "doomed obscurity". Breakthrough: formulas 27-29 into anchors. Owner: "format is interesting, could be polished".
- Procedural skill vs role card decision. This file.
