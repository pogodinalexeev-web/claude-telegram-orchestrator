---
name: atomize
description: Break down raw material — conversation logs (ChatGPT/Claude/podcast), articles, playbooks, audit reports, or any md file — into atomic notes in Resources/atoms/. Sources: Resources/chat-logs/raw/, Projects/<X>/journal/, or any specified path. Trigger on "/atomize <path>", "atomize this", "break into atoms".
---

# /atomize — atomize raw material into zettelkasten

## When
- The user provided a path to an md file (or PDF/other text from the vault).
- Default sources: `Resources/chat-logs/raw/`, `Projects/<X>/journal/`. Any md path is valid.
- Command `/atomize <path>` or `/atomize` with no argument (then take the freshest unprocessed file from `chat-logs/raw/`).

## What counts as an "atom"
**One atom = one thought** (Zettelkasten style):
- Self-contained (understandable without the source context).
- Has its own name (slug) in kebab-case.
- 3–15 lines of text.
- Links via `[[atom-name]]` in the `## Links` section.
- Keep `source_quote` — original phrase/quote for verification.

## What to atomize by source type
- **chat-logs/raw/** (dialogues, podcast transcripts) — individual **thoughts/insights from the user or interlocutor**. A 30-minute dialogue usually yields 3–7 atoms.
- **reports/** (articles, playbooks, forwards, reports, security audits) — **author's concepts, frameworks, theses, conclusions, risks, patterns**. Long playbooks (300+ lines) may yield 8–15 atoms. An audit typically gives 3–8 atoms per report. Per the "no default atomization of forwards/PDFs" rule — only when the owner explicitly requests it.
- **Any other source** — follow the general principle: one thought = one atom.

## Algorithm
1. **Check for duplicates**: `ls Resources/atoms/` + grep for relevant slugs/topics. If an atom with a similar idea already exists — merge manually (show diff) or skip.
2. **Read the file yourself** (do not delegate to a weaker model). If >5000 lines — read in chunks of 1000.
3. **Extract atoms**: each self-contained thought → one atom with slug, title, topic, body, source_quote. Do NOT generate atoms from unanswered questions. Do NOT duplicate common knowledge.
4. **Mode B (plan → confirm → write)** — required for clippings/audits and long sources (>20 atoms): first **show the plan** (list of slugs with a one-line description of each), wait for "yes/ok", then write. For short chat-logs you can proceed directly.
5. **For each atom**:
   - Create `Resources/atoms/<slug>.md` from `Resources/_templates/atom.md`, fill in frontmatter and body.
   - If a file with that slug already exists — add a suffix (`-v2`, date) or merge.
6. **Update `Resources/atoms/_MOC.md`**: add links to new atoms under appropriate topics. If no suitable section — add a new one.
7. **Mark as "processed"**:
   - For `chat-logs/raw/`: create a flag `Resources/chat-logs/processed/<name>.processed.md` with frontmatter `processed_at: ...`, `atoms_created: N`.
   - For `clippings/`/`audits/`/others: add `atomized: 'YYYY-MM-DD HH:MM (N atoms)'` to the source file's frontmatter. If no frontmatter — create a minimal one.
8. **Report** in chat:
   ```
   ✓ atomized <file>: N atoms in Resources/atoms/
   Topics: <top-3 topics>
   MOC updated.
   ```

## Rules
- Files >5000 lines — process in chunks of 1000.
- **No duplicates.** Before creating — grep `Resources/atoms/`. If similar exists — merge, don't duplicate.
- `source_quote` is mandatory for verification.
- Atoms are **extracted knowledge**, not a transcript. 30-min dialogue = 3–7 atoms, not 30. 300-line playbook = 8–15 atoms, not 50.
- For clippings/audits **Mode B is required** — the owner confirms the plan before writing.
