---
name: job-search
description: Targeted search for live job openings matching the owner's profile (AI/LLM Engineer) via deep research. CORE — running deep research that (1) finds fresh open listings from the last 1-2 weeks and (2) EXPANDS the source map (new channels/boards/chats). Verifies link liveness, populates the channel list and shortlist. Use for "/job-search", "find openings", "what's out there for jobs", "find vacancies".
---

# /job-search — job search via deep research

> Source of profile and channels — `Projects/IT/Industry/Resources/vacancy-channels.md`. **Read it first** — candidate profile, filter, current source list.

## Principle (important — don't violate)

**Core of the skill = deep research, NOT reading the same two channels.** Reading known channels by hand — auxiliary, for a quick check. The main thing — each run:
1. **finds fresh live openings** (last 1-2 weeks);
2. **expands the source map** — brings NEW channels/boards/chats not yet in `vacancy-channels.md`.

The source list must **grow** from run to run, not freeze at two channels.

## Algorithm

1. **Read `Projects/IT/Industry/Resources/vacancy-channels.md`** — profile, filter, what's already in the list (so deep research looks for NEW things beyond this).

2. **Run deep research** with a prompt in two parts:
   - **(A) Fresh openings:** open listings from the last 1-2 weeks matching the profile (LLM integration, RAG, agents, prompts, AI Creator, automation; remote priority; WITHOUT requiring classical ML/PyTorch/degree). With direct application links.
   - **(B) Source expansion:** collect MAXIMUM diverse places where such openings are published — TG channels, boards, aggregators, community chats, career portals — beyond the already known ones (list known from the resource, ask for NEW ones beyond them).
   - At the end of the prompt: `Write the final report in Russian.` (or target language as appropriate)

3. **Set up background delivery** — so the result arrives on its own, without hanging the turn.

4. **When report is ready**:
   - **Verify liveness** of each opening with a direct link via `WebFetch` (open/archived/404). Lesson: links go stale in days-weeks, date ≠ open.
   - **Populate `vacancy-channels.md`** with new sources from part (B) — append to channel/board tables, don't overwrite old ones.
   - **Update the shortlist** in the relevant tasks file with live openings and their statuses.
   - Export the full report to `Projects/IT/Industry/journal/YYYY-MM-DD ...md` + pointer in `ideas.md`.

5. **Deliver to owner** compactly: live openings (role, link, why it fits) + how many NEW sources were added to the list.

## Rules

- **Only verified live links** as "apply now". Unverified — honestly "not verified".
- Positioning — "AI/LLM Engineer", not "prompt engineer" (salary ceiling too low).
- **Source list must grow** — if a run brought no new channels/boards, that's a weak run, dig wider.
- Manual reading of known channels — only for a quick check in addition to deep research, not instead of it.

## Connections

- Profile/channels — `Projects/IT/Industry/Resources/vacancy-channels.md` (growing list).
- Deep research mechanism + background delivery — `.claude/skills/deep-research/SKILL.md`.
- Resume — `Projects/IT/resume.md` + `/resume-builder`.
- Track status (not search) — `/job-pulse`.
