---
name: meeting-debrief
description: Synthesize a summary of a work/scoping call to highlight the RIGHT accents, not a retelling. Use for "summarize the call", "break down the meeting", "debrief of the meeting", "what came out of the client call". The reference for accents — reference-example.md in the same folder (a scoping call summary, included as an example of right emphasis).
---

# /meeting-debrief — call summary with correct accents

## When

- There's a transcript/recording of a scoping call (client, customer, sales, project assessment).
- The owner asks to "summarize the results", "break down the call", "what was the outcome".
- NOT a retelling of lines in order — but extracting what is actually at stake.

## Why a reference example

A reference debrief is included as `reference-example.md` to calibrate the right accents. Before writing your own summary — reread it to maintain the bar: not a retelling, but analysis of the hidden.

## Summary skeleton (as in the reference)

1. **Participants + client** — with one vivid characterization of the client (what kind of person, what they're about), not a dry list.
2. **Breakdown into independent tasks** — don't lump together. If N tasks surfaced in the call — N blocks, explicitly "independent".
3. **Under each task** discuss:
   - the essence (in your own words, "translator from jargon"),
   - scale of pain + numbers (how many people, documents, %),
   - **key volume uncertainty** — what directly hits the estimate,
   - data / locality (any personal data → on-prem needed?),
   - interface (web/TG/...),
   - timeline + budget,
   - **next concrete step** (who sends what, by what day).

## Accent checklist "What stands out" — the HEART of the debrief

A separate section at the end. Not a retelling — but what **wasn't said aloud** or was said more softly than warranted. Run the call through the filters:

1. **Main contradiction.** What physically doesn't add up? (Reference: start July 1, but people with pain/answers are on vacation until September → can't estimate until you get a sample.)
2. **Lure vs plan.** "We'll sell to everyone / scale" — that's a dream, not the near-term value. Record firmly: the nearest measurable value = the pain of ONE client. Scalability — into architecture, not budget justification.
3. **Feature/upsell vs requirement.** What sounds nice but no one on the client side asked for? (Reference: on-prem on their GPU — an upsell layer, not a baseline scenario; don't burden the first estimate with it.)
4. **Honest scope trade-off.** Where does the "right for the client" recommendation cut your own revenue? Say it out loud. And watch that "the project" doesn't quietly collapse into "a small chatbot" — otherwise the economics of entry won't work.
5. **What wasn't discussed at all.** Structural gap: is this one contract or three? Who pays? Is there a procurement procedure? Extract BEFORE counting numbers.

## Discipline

- Don't invent numbers. No sample/data — write "estimating blind, need X".
- Tone — demanding auditor, not a reteller. Surface the risk before it fires.
- Save the finished summary to `Projects/<X>/journal/YYYY-MM-DD <topic> — summary.md`.
