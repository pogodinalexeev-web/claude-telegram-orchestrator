---
name: idea
description: Capture an idea into Tasks/ideas.md in one line with date. Use when the owner says "idea:", "save this idea", "/idea ..." or explicitly asks to save a thought without discussion.
---

# /idea — quick idea capture

## What to do
1. Take the idea text from command arguments (everything after `/idea `).
2. If no text — ask in one line: "What to record?"
3. **Check for "why"**: without one line of "why it's interesting" — don't save the idea. If "why" is absent from the text — ask in exactly one line: "Why is it interesting?". If the owner says "just save it" / "no why needed" — save with note `[no-why]` for triage in weekly-review.
4. Collect tags from the text (if there are `#something` — keep them; they'll guide `/process-inbox` on where to route).
5. Append to `Tasks/ideas.md` a line in the format:
   ```
   - YYYY-MM-DD · <text> — why: <…> #idea <tags>
   ```
6. Don't comment, don't assess, don't propose development. Only confirm: `✓ recorded in Tasks/ideas.md`.

## What NOT to do
- Don't edit the owner's wording.
- Don't ask more than one question (only "why", if it's absent).
- Don't sort immediately into a project — that's what `/process-inbox` is for.
- Don't discuss the idea. If you want to discuss — that's no longer `/idea`.

## Example
Owner: `/idea could build an MOC for hiring through Obsidian Bases skills`
Action: Edit Tasks/ideas.md → add line `- 2026-05-05 · could build an MOC for hiring through Obsidian Bases skills #idea`
Response: `✓ recorded in Tasks/ideas.md`
