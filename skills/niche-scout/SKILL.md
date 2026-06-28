---
name: niche-scout
description: Income niche scout with neural networks. Collects a findings map from open sources (exchanges, blogs, forums) with source quality markers and formulates numbered gap-tasks for the freelance-pulse skill. Does NOT rank, does NOT set potential scores. Use for "/niche-scout <query>", "research niche X", "what's there for earning on Y".
---

# /niche-scout — niche scout from open sources

## Purpose
Raw material for deciding "what to dig into", not the decision itself. Findings map + gap-tasks for pulse. The decision "dig into X" is made by the owner, not the skill.

## Algorithm

1. **Clarify the query** (if vague): what vertical, what income format is interesting (freelance/service/product), are there exclusions.
2. **Run reconnaissance** via web (WebSearch + WebFetch) across 3-5 sources of different types:
   - Freelance exchanges (Kwork, Profi.ru, FL.ru, Upwork) — open order feeds.
   - First-person blog cases (Habr, VC.ru, Telegram channels, Substack).
   - Review articles / summaries.
   - Forums and discussions (Reddit, niche chats in the public part).
3. **Formulate the map** in the format below.

## Output format

```
🗺️ Niche map: <name>

Found N directions:

▸ Direction 1: <short name>
  - [live exchange: <N orders per period>, average ticket <range>] — source: <URL/name>
  - [blog case: "<result quote>", author @<name>] — source: <URL>
  - [review: <one phrase>] — source: <URL>

  gap-1.1: <specific question> → close via pulse: <how exactly, what tool>
  gap-1.2: <specific question> → close via pulse: <how exactly>

▸ Direction 2: ...

—

🚫 Uncloseable gaps (record separately, don't send to pulse):
  - <question>: <why it can't be answered via live browser / API> (e.g.: "demand stability over 6 months" — that's a forecast, can't be grepped)

—

What I physically CANNOT see from this session:
  - state of the owner's accounts on specific exchanges
  - real rates in private messages (only showcase)
  - hidden stages (KYC, moderation, limits for new users)
  - niche saturation in the moment

→ This is material for freelance-pulse. The gap list above — tasks for it.
```

## Strict rules

- **No ranking**, no potential scores, no "top-1" picks. Map — equal presentation of all directions.
- **Every item — with a source quality marker** in square brackets: `[live exchange]`, `[blog case]`, `[review]`, `[forum]`. Without marker — item not included.
- **Sources → names.** Not "from the internet", but "blog X, case Y, exchange Z" with URL. Without name — item not included.
- **Gap-tasks are numbered** by scheme `gap-<N direction>.<M in direction>` — pulse will reference them by numbers.
- **Uncloseable gaps — separate block.** Don't mask them as gap-tasks for pulse, otherwise pulse barges in where there's no data in principle.
- **Don't propose action for today.** This is reconnaissance, not a plan. Decision — with the owner.

## Stop signals

- If a direction has at least no `[live exchange]` or `[blog case]` source — direction **not included** in the map. Pure review articles without a primary source = averaged signal, exactly the error that happened on Day 10.
- If the query returns <2 directions with a real signal — write honestly "niche is thin, found only X", don't inflate to 5 items artificially.

## Origin

Error on Day 10 (see solo project log): two web agents output 8+7 cases with hypotheses presented in the same tone as facts. The owner couldn't distinguish "200-300K in 60 days" (extrapolation from 2 blogs) from "barbershops — gap from 15 listing summary" (real summary). This skill — a structural fix: markers are mandatory, ranking is forbidden, gap-tasks are numbered for passing to pulse.
