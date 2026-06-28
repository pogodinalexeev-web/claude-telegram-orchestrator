---
name: capture
description: Capture any thought/note/link into inbox.md without sorting. Use when the owner says "remember this", "/capture ...", "drop into inbox" — anything that's not an idea and not a task.
---

# /capture — capture into the shared Inbox

## What to do
1. Take text from the command arguments (everything after `/capture `).
2. If no text — ask in one line: "What to record?"
3. Append to `inbox.md` a block **in the canonical funnel format** (see Tasks/manual.md → "Canonical inbox.md entry format"):
   ```
   ---
   YYYY-MM-DD HH:MM (capture)
   <text>
   ```
   The `---` separator is mandatory. Source is `(capture)` (distinguishes manual capture from `(TG)` bot, `(voice)` voice, `(forward)` forwards).
4. If the text contains tags `#idea`, `#task`, `#read`, `#job`, `#ai`, `#music` — keep them as-is; they'll guide `/process-inbox` on where to route.
5. Confirm: `✓ recorded in inbox.md`.

## What NOT to do
- Don't sort (that's what `/process-inbox` is for).
- Don't ask clarifying questions.
- Don't comment.
- Don't use `## YYYY-MM-DD HH:MM` — this is the old format; `/process-inbox` splits on `---`.

## Example
Owner: `/capture a contact mentioned an internal GitLab for pet projects #ai`

In `inbox.md`:
```
---
2026-05-07 12:30 (capture)
a contact mentioned an internal GitLab for pet projects #ai
```

Response: `✓ recorded in inbox.md`
