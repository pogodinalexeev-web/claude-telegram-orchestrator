---
name: Brainstorming Ideas Into Designs
description: Interactive idea refinement using Socratic method to develop fully-formed designs
when_to_use: when partner describes any feature or project idea, before writing code or implementation plans
version: 2.2.0
---

# Brainstorming Ideas Into Designs

## Overview

Transform rough ideas into fully-formed designs through structured questioning and alternative exploration.

**Core principle:** Ask questions to understand, explore alternatives, present design incrementally for validation.

**Announce at start:** "I'm using the Brainstorming skill to refine your idea into a design."

## The Process

### Phase 1: Understanding
- Check current project state in working directory
- Ask ONE question at a time to refine the idea
- Prefer multiple choice when possible
- Gather: Purpose, constraints, success criteria

### Phase 2: Exploration
- Propose 2-3 different approaches
- For each: Core architecture, trade-offs, complexity assessment
- Ask your human partner which approach resonates

### Phase 3: Design Presentation
- Present in 200-300 word sections
- Cover: Architecture, components, data flow, error handling, testing
- Ask after each section: "Does this look right so far?"

### Phase 4: Worktree Setup (for implementation)
When design is approved and implementation will follow:
- Announce: "I'm using the Using Git Worktrees skill to set up an isolated workspace."
- Switch to skills/collaboration/using-git-worktrees
- Follow that skill's process for directory selection, safety verification, and setup
- Return here when worktree ready

### Phase 5: Planning Handoff
Ask: "Ready to create the implementation plan?"

When your human partner confirms (any affirmative response):
- Announce: "I'm using the Writing Plans skill to create the implementation plan."
- Switch to skills/collaboration/writing-plans skill
- Create detailed plan in the worktree

## When to Revisit Earlier Phases

**You can and should go backward when:**
- Partner reveals new constraint during Phase 2 or 3 → Return to Phase 1 to understand it
- Validation shows fundamental gap in requirements → Return to Phase 1
- Partner questions approach during Phase 3 → Return to Phase 2 to explore alternatives
- Something doesn't make sense → Go back and clarify

**Don't force forward linearly** when going backward would give better results.

## Related Skills

**During exploration:**
- When approaches have genuine trade-offs: skills/architecture/preserving-productive-tensions

**Before proposing changes to existing code:**
- Understand why it exists: skills/research/tracing-knowledge-lineages

## Audit-mode integration

In brainstorming mode the executor-turns counter is disabled — it's easy to slide into a pure "idea generator" without an auditor's perspective. The fix:

**Every 2 rounds of dialogue — one auditor observation, not just generation.**

Format — a short line "📋 Audit note: <observation>" at the end of the response after the main phase. Content:
- Where I may be inflating positivity toward the current design direction
- Whether I missed context from the vault that changes the picture (name in the thread, an asset in project files, a recent decision)
- Whether I steered the conversation toward my own position on the first move, missing the opposite option

This doesn't break the methodology (questions/alternatives/design proceed as usual), but preserves the auditor role alongside the generator role.

**Rule trigger:** During Phase 2 of a "publishing and recovering" formula, the first move gave three variants of "close social media / switch attention / separate channels", forgetting that the owner is a musician for whom social media = an organic growth tool. The owner firmly corrected: "I can't stop posting." If the audit-note rule had been in place — on the second round I would have been required to record "I'm generating from a position of fear for the owner, not from their profession" and self-correct, before the correction.

## Remember
- One question per message during Phase 1
- Apply YAGNI ruthlessly
- Explore 2-3 alternatives before settling
- Present incrementally, validate as you go
- Go backward when needed - flexibility > rigid progression
- Announce skill usage at start
- **Audit-mode integration:** every 2 rounds — 📋 audit-note (see section above)
