---
name: resume-builder
description: Build/strengthen a resume in 4 stages through 4 sequential passes from a recruiter's perspective. Each stage is a separate model "mode", results shown between stages with a pause for "continue". Procedural skill.
---

# resume-builder — resume build/upgrade in 4 stages

Turns a resume into a strong document through 4 sequential passes from a recruiter's perspective. Each stage is a separate model "mode"; between stages I show the result and wait for "continue". Source of prompts — a viral thread on recruiter-mode prompting, formalized as a procedural skill.

## When to use

- The owner is building or rewriting a resume.
- Need to adapt an existing resume for a specific job opening.

## Start — where to get the resume

First ask in one line:
> "Is there resume text / path to file — or are we building from scratch from vault facts?"

- **Text/file available** — use it as input for stage 1.
- **From scratch** — pull facts from relevant project files: technical trajectory, key skills, recent achievements. First build a draft from facts, then run through stages. **Don't invent anything** — only what's in the vault or confirmed by the owner.

## Stages (strictly in order, pause between them with "continue")

### Stage 1 — Recruiter mode (honest breakdown, WITHOUT rewriting)

Play the role verbatim:
> "You are an experienced recruiter and hiring manager. Look at my resume not as the author, but as someone who: reviews dozens of resumes daily; quickly filters out weak formulations; looks for value, not 'duties'. Analyze the resume. Say: which formulations sound weak or vague; where I'm not conveying my real value; where the resume doesn't answer 'why should I invite this person further?'. No rewriting. Honest breakdown only."

This is a genuinely tough breakdown, not reassurance. Point out weak spots directly.

### Stage 2 — Translate to HR language (rewrite to results)

> "Rewrite the resume the way a recruiter wants to see it. Rules: focus on results, not processes; specifics instead of general words; each point should answer — what value do I bring to the company. Preserve real experience and skills. Don't invent anything. Change only formulations and emphasis."

### Stage 3 — "Do I want to invite this person to an interview" check

> "Read the updated resume again as a recruiter. Answer: what 3 strengths of the candidate are immediately visible; what role do they look most convincing for; does the resume make you want to invite them to an interview and why. If not — what exactly is preventing it and how to fix it."

### Stage 4 — Adapt for a specific vacancy

Requires the vacancy text. If absent — ask to provide one.
> "Act as a recruiter closing a specific vacancy. Here's the vacancy description: [text]. Here's the resume: [updated version]. Task: adapt to the requirements of this specific vacancy; position as a strong candidate; remove or weaken what doesn't strengthen the application for this role."

## Where to save results

Each stage — separate file in the project journal (local time):
`Projects/<relevant-project>/journal/YYYY-MM-DD resume-<stage>.md`
Final version for the vacancy — same location with the vacancy name in the filename.

## Rules

- Stages do NOT merge — one pass at a time, wait for "continue" from the owner.
- Stage 1 — honest breakdown, no flattery (see audit-mode).
- "Don't invent anything" — a through-rule for all stages. No fact in vault / not confirmed by owner → don't write.
- ATS-logic pass (stages 2-4) — simple formulations, keywords from the vacancy, no filler.
