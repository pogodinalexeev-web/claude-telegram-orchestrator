---
name: people
description: Notes on people with tier-enrichment. Creates/updates Projects/LIFE/People/<name>.md, counts mentions across vault, upgrades tier when frequency grows. Use for "/people <name>", "record about <name>", "update note on <name>", or before/after contact with a person.
---

# /people — lightweight CRM with tier-enrichment

## Purpose
People (contacts, collaborators, clients, friends) are scattered across projects and loops. This skill gathers them in `Projects/LIFE/People/<name>.md` and grows the detail level as mention frequency increases — without over-engineering a full CRM.

## Tiers (source: huytieu/COG-second-brain)
- **Tier 3 (stub)** — 1+ mention. Name + one context line ("who, where, why relevant to me").
- **Tier 2 (moderate)** — 3+ mentions. Expand: work style, strengths, what was promised / what we're waiting for.
- **Tier 1 (full)** — 8+ mentions or direct contact (call/meeting). Full profile: interaction history, triggers, debts.

## What to do

1. **Take the name** from argument (`/people Alex`). If empty — ask "About whom?".

2. **Find an existing note** by exact match `Projects/LIFE/People/<name>.md` or a close variant (Alex/Alexander/Sasha). If a variant with different spelling found — ask "Is this the same person?".

3. **If no note:**
   - Create `Projects/LIFE/People/<name>.md` from `Resources/_templates/person.md` (Tier-3, stub).
   - Count mentions: `grep -ri "<name>" <vault_path>/ --include="*.md" -l | wc -l` (number of files, not lines).
   - Record `mentions: <N>` in frontmatter.
   - Fill "Context" in one line based on grep results (where mentioned, in what context).
   - Ask the owner: "What to add about <name>? (one line, or enter to skip)"

4. **If note exists:**
   - Recount `mentions` (grep across vault).
   - Update frontmatter (`mentions`, `last_contact: YYYY-MM-DD` if owner mentioned contact today).
   - **Check tier upgrade:**
     - At `mentions ≥ 3` and current `tier: 3` → propose upgrade to Tier-2 ("Expand sections: work style / strengths / promises?").
     - At `mentions ≥ 8` or explicit "had a call/meeting" and current `tier: 2` → propose upgrade to Tier-1.
   - Ask: "What to add or update?" — depending on tier.

5. **Confirmation:**
   ```
   ✓ Projects/LIFE/People/<name>.md updated (Tier <N>, mentions: <N>).
   ```

## What NOT to do
- Don't parse mentions and extract facts automatically. Context is asked from the owner.
- Don't upgrade tier without confirmation. That's their choice, when they need a deep profile.
- Don't touch `linked_projects` without an explicit request.
- Don't edit other vault files (mentions in projects stay as-is).
- Don't do massive grep across vault if volume is very large — limit with `-l` (file names only, not lines); it's cheaper.

## Example

Owner: `/people Alex`