---
name: scout
description: Entry point into the niche research chain for solo income — source-scout → niche-scout → freelance-pulse. Launches the first phase (source-scout) and proposes the next step at each checkpoint. The owner decides whether to continue. Use for "/scout <niche>", "research niche X from scratch", "run the scout chain on topic Y".
---

# /scout — niche research chain dispatcher

## Purpose
One entry point into a three-phase solo income research chain. The chain is intentionally split — between phases the owner makes a decision; automation would kill that. The skill holds checkpoints and doesn't proceed without an explicit "yes".

## Chain
```
/scout <niche or anchor-product URL>
   │
   ▼
[Phase 0] product description  ── conditional, by input rawness (see rules)
   │     - URL/ready product → WebFetch + JTBD summary, no questions
   │     - hypothesis by name  → 1 clarifying: "give URL/anchor product name"
   │     - raw idea            → 3-5 questions by JTBD/ICP/risk assumptions
   │
   ▼
[Phase 1] source-scout  ──► platform map by types
   │
   ▼ checkpoint: "does the map work?"  ← owner decides
   │
   ▼
[Phase 2] niche-scout   ──► findings map + gap-tasks (numbered)
   │
   ▼ checkpoint: "which gaps to close?"  ← owner selects numbers
   │
   ▼
[Phase 3] freelance-pulse gap-N  ──► live URL or honest "none"
   (repeats per each selected gap in a separate run)
   │
   ▼
[Final] go/no-go verdict  ──► "dig into localization / don't dig" with numerical basis
```

## Dispatcher algorithm

1. **Accept input** from `/scout <niche or URL>`. Determine input state (see "Phase 0 — triggers" below) and choose the branch.

2. **Phase 0 — product description (conditional)**. Only if trigger fires. Details — in "Phase 0 — triggers and algorithm" section below. Phase 0 result — JTBD summary (1 paragraph + 3-5 points: ICP, client job, value, risky assumptions), which **feeds into source-scout** as a refined niche formulation.

3. **Run Phase 1 — source-scout** per the rules of [`source-scout/SKILL.md`](../source-scout/SKILL.md). Output the platform map in its format.

4. **Checkpoint 1** — at the end of the source-scout output, add a block:
   ```
   ─── chain checkpoint ───
   Map assembled. Next — niche-scout: will dig into WHAT people say in these platforms.

   Options:
   1) "continue" / "niche-scout" → I launch phase 2
   2) "stop" / "map is off" → I stop, niche is thin or platforms are wrong
   3) "refine X" → refine niche/platforms, relaunch source-scout
   ```
   **Don't launch phase 2 without explicit "yes".**

5. **Run Phase 2 — niche-scout** per the rules of [`niche-scout/SKILL.md`](../niche-scout/SKILL.md), using the map from phase 1 as input ("only dig in these platforms, don't cover others"). Output the findings map with numbered gap-tasks.

6. **Checkpoint 2** — at the end of the niche-scout output, add a block:
   ```
   ─── chain checkpoint ───
   Findings map assembled, N gaps total (closeable via pulse) + M uncloseable.

   Next — freelance-pulse, per ONE gap per run (one run = one live URL or honest "none").

   Options:
   1) "pulse gap-X.Y" → I launch phase 3 for this gap
   2) "pulse gap-X.Y, gap-Z.W" → sequentially for each (NOT in parallel, pulse design)
   3) "stop" / "material is enough" → I stop, chain closed
   4) "save the map" → propose saving to `Projects/<solo-project>/journal/YYYY-MM-DD <niche>.md` without launching pulse
   ```
   **Don't launch phase 3 without explicit gap number selection.**

7. **Run Phase 3 — freelance-pulse** per the rules of [`freelance-pulse/SKILL.md`](../freelance-pulse/SKILL.md) for **each** selected gap number in a **separate run**. One gap = one output = one anchor URL.

8. **Final — go/no-go verdict.** After the last pulse, not just "chain closed" but an **explicit verdict** with numerical basis:
   ```
   ─── scout final: verdict ───
   Niche: <name>
   Completed: <phases>, anchors: <URL list>

   Verdict: ⚪ go / 🔴 no-go / 🟡 conditional-go

   Basis (with numbers and URLs):
   - <basis 1, quality marker: [fact/hypothesis/extrapolation]>
   - <basis 2, ...>
   - <basis 3, ...>

   Conditions (only for conditional-go):
   - <what must be true for go to become unconditional>

   Next step:
   - go → <one concrete action within N days>
   - no-go → <what to defer, what trigger to reconsider>
   - conditional → <how to check the condition, in what timeframe>

   Suggest: save summary to `Projects/<solo-project>/journal/YYYY-MM-DD <niche>.md`?
   ```

   **Final strict rules:**
   - Minimum **3 bases**, each with a quality marker (per pulse model: `[fact]` / `[hypothesis]` / `[extrapolation]`) and a URL anchor.
   - **Don't issue go** if ≥1 basis is `[extrapolation]` or `[hypothesis without confirmation]`. In that case — `conditional-go` with an explicit condition.
   - **Don't issue no-go** based solely on "no analogues in local market" — that can be either a market gap (go) or a "doesn't fly here" signal (no-go). Phase 2 niche-scout distinguishes them via local failure cases. If no failure cases found and no analogues — `conditional-go` ("chance to be first, but verify why no one has succeeded").
   - **Labels without basis are forbidden.** "Promising", "hot niche", "all bad" — not verdicts. Only go/no-go/conditional-go with numerical justification.

## Phase 0 — triggers and algorithm

Trigger determined by the form of `/scout <X>`:

| Input state | Trigger | What phase 0 does |
|---|---|---|
| **Product URL** (`/scout https://...`) or explicit service name with public site | URL/name exists | **Automatically**, no questions: WebFetch description → JTBD summary (1 paragraph: what job for what ICP, what value, what risky assumptions). Feed directly into phase 1. |
| **Hypothesis by name** ("analogues of X in local market", "like Y, but localized") without URL | Name exists, anchor product not specified | One question: "give URL/name of the anchor product you're basing on". After response — move to top row. |
| **Raw idea** (`/scout AI for dentists`, `/scout earning with neural nets`) | Direction only, no product | 3-5 JTBD questions: (1) what client job are you closing, (2) who is ICP — companies / people / roles, (3) value hypothesis — what they're willing to pay for, (4) the single riskiest assumption, (5) is there a sample analogue anywhere in the world. After answers — JTBD summary, phase 1. |

**Phase 0 strict rules:**

- **If URL exists — questioning is forbidden.** WebFetch + summary, then move on. Don't add "clarifying" questions out of politeness.
- **If raw idea — maximum 5 questions at once.** Not "one question → answer → next question". Give as a list, owner answers in a block.
- **If owner refuses to answer a raw idea** ("just run it") — write honestly "without product description the scout will run on a vague formulation, risk of framework 05/10 error" and ask directly: "continue on raw input or refine first?". Decision — with the owner.
- **JTBD summary — no more than 6 lines.** Longer isn't needed; scout is a **niche formulation**, not marketing copy.

## Dispatcher strict rules

- **Language gate — strict.** Any anglicism in the scout output (`outbound`, `outreach`, `ICP`, `SDR`, `RevOps`, `B2B`, `pipeline`, `lead`, `follow-up`, etc.) — mandatory with native-language equivalent in brackets at **first encounter in the phase output**. If the term isn't in the glossary — **add it to the glossary in the same turn**, before the answer. Not "later". Otherwise the scout blocks its own output.

- **Checkpoints don't get skipped.** Between phases **always** wait for an explicit "yes" from the owner. Silence ≠ consent.
- **Don't run pulse "for all gaps at once".** Pulse design: one run = one gap. If the owner asks "all" — do sequentially, not in parallel, not in one output.
- **WebSearch inside source-scout / niche-scout — yes** (that's reconnaissance). Inside pulse — no (there it's live browser first, see freelance-pulse rules).
- **Don't substitute rules of nested skills.** Quality markers, ban on ranking, rate-shift stop rule — all stays as in the original SKILL.md.
- **If on any phase the nested skill's stop signal fires** (niche is thin, <3 platform types, <2 directions in the map) — stop the chain **at this phase**, don't drag forward, don't inflate.

## Chain stop signals

- Owner didn't select any gap number after niche-scout (silence / "don't know what to take") — **don't run pulse**, stop, propose either saving the map or rebuilding on another niche.
- Live browser not connected (bot / server / Claude Code without MCP) — **stop at phase 3** per freelance-pulse rules. Phases 1-2 work fine (they use WebSearch).
