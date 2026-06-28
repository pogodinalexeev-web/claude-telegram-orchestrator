---
name: graduate
description: Turn a line from Tasks/ideas.md into a full project — create Projects/<slug>/manual.md from a template, delete the line from ideas.md. Use for "/graduate <idea fragment>", "idea is ready", "expand idea into project".
---

# /graduate — idea → project

## Purpose
An idea in `Tasks/ideas.md` keeps recurring. This is a signal: time to expand it into an active project under `Projects/`. The skill does this in one move — no manual template copying or line deletion.

## What to do

1. **Find the idea:**
   - If there's an argument after `/graduate ` — search `Tasks/ideas.md` by substring (case-insensitive, by line text).
   - If 0 matches — say "Not found. Show the list?", wait.
   - If >1 matches — show a numbered list and ask to choose a number.
   - If no argument — show all lines from recent `## 20XX-XX` sections with numbers and ask to choose.

2. **Ask the owner 3 questions** (in one message, wait for response):
   - **Goal** (deliverable + date): "What will count as done and by what date?"
   - **Folder slug**: "Folder name in `Projects/`? Suggesting `<auto-slug>`" — auto-slug from the idea text, kebab-case, up to 30 characters. No spaces or non-ASCII.
   - **First step (`#next`)**: "What's the first concrete action this week?"

3. **Create `Projects/<slug>/manual.md`** based on `Resources/_templates/project.md`:
   - frontmatter: `goal: "<goal>"`, `deadline: "<date>"`, `linked_area: ""` if relevant tags. `tags: [project, <tags from idea without #>]`.
   - `# <Title>` — take the first 5-7 words of the idea or ask for manual input if the phrase is long.
   - Goal — fill from the owner's response.
   - `#next` — fill from the response.
   - Journal — add the first line: `- YYYY-MM-DD — graduated from ideas.md, original formulation: "<idea text>"`.

4. **Delete the line from `Tasks/ideas.md`** (exactly the one used).

5. **Confirmation** in one line: `✓ Projects/<slug>/manual.md created, line deleted from ideas.md. Open the project and fill in milestones manually.`

6. **Don't edit status.md.** If the owner wants to add the project to active — that's their decision, not mine.

## What NOT to do
- Don't invent milestones (M1/M2/M3) — the owner will fill them in; we don't have enough context.
- Don't touch `linked_area` unless there's an explicit binding in the idea's tags.
- Don't create nested folders (`Tasks/`, `Resources/` inside the project) — let them appear as needed.
- Don't run git commands (auto-commit hook picks it up).
- Don't ask more than 3 questions. If the owner wants finer control — they'll open manual.md and add it.

## Example

Owner: `/graduate voice bot for self-capture`

Assistant finds the line:
```
- 2026-05-06 22:50 — Voice bot for self-capture (dictate → parse → sort) as portfolio — why: half is already assembled in the TG-bot + voice pipeline, can be polished into a public demo in a week. tags: #job #portfolio #assistant-2.0
```

Asks (in one message):
```
Found: "Voice bot for self-capture (... demo)"
1. Goal + date? (e.g.: "public TG demo, ready by May 20")
2. Folder slug? Suggesting `voice-self-capture-bot`
3. #next for this week?
```

Owner responds → assistant creates `Projects/voice-self-capture-bot/manual.md`, deletes the line from ideas.md, confirms.
