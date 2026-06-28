---
name: freelance-pulse
description: Targeted closure of a specific gap from the niche-scout map via a live MCP browser. One run = one gap number = one platform + one goal. Returns either a live URL with specifics, or an honest "not found". Use for "/freelance-pulse gap-1.2", "check gap-2.1", "pulse on exchange X".
---

# /freelance-pulse — closing a gap via a live tool

## Purpose
Not "market overview", not "full niche run". Takes **one specific gap** (usually from `niche-scout` output) and closes it via MCP browser: live platform, live account, live feed. Output — a fact anchor or an honest "none".

## Precondition

- **MCP browser must be connected** (Chrome DevTools MCP or equivalent). In the TG bot on VPS it's NOT available — the skill applies only in Claude Code on the owner's machine.
- If launched in an environment without MCP browser — **stop immediately**, write "no live tool, not running pulse", suggest an alternative (e.g., defer until Claude Code on the machine).

## Algorithm

1. **Accept the gap number** (`gap-1.2`, `gap-2.1`, etc.) and confirm understanding: what exactly I'm checking, on what platform, what answer counts as closed.
2. **First move — live tool, not WebSearch**. Open the platform via MCP browser.
3. **Account state first**, if the gap is about a specific exchange: activated/not, KYC, limits, what's accessible. Without this, looking at the feed is pointless — there might be orders but the owner physically can't respond.
4. **Data collection on the gap**: feed, cards, statuses, prices in cards, responses (if visible), deadlines. All numbers — with screenshots/quotes/URLs.
5. **Confidence marker on every claim**:
   - `[fact: saw in feed, URL: ...]` — what was physically opened and read.
   - `[hypothesis: 2 sources confirm, but not self-verified]` — derived.
   - `[extrapolation: 1 case]` — weak signal.
   Without marker — number not included.
6. **Rate-shift stop rule**: if within the session a key figure (ticket, deadline, volume, estimate) changes a second time — **stop**, summary "what changed and why", don't issue a new rate in the same turn.

## Output format

```
🎯 Pulse gap-<N>: <short gap name>

Platform: <name + URL>
Account state: <line>

What I saw:
- [fact: ...] — URL
- [fact: ...] — URL
- [hypothesis: ...] — basis

Anchor:
→ <ONE live URL that the owner opens in 30 sec and sees the same picture>

Conclusion:
- gap closed: <yes/no>
- answer: <in one phrase>
- next move: <one concrete action, or "nothing, niche doesn't work">
```

## Strict rules

- **One run = one gap number.** Don't spread to neighboring gaps from the same niche, even if tempting. Want a second gap — next run.
- **WebSearch on the first move is forbidden.** Only MCP browser. WebSearch allowed on the second-third move as follow-up, not as the base.
- **Skill output — either a live URL with specifics, or an honest "not found, platform doesn't work because X".** Not a framework, not "here are directions for further study".
- **Rate-shift stop rule** — second time changing a key figure in the session → summary, not a new rate.

## Link with niche-scout

- Pulse reads gap-tasks from the latest `niche-scout` output (owner gives the number). If working on a "free" gap without a scout map — formulate the gap in the first output line in the same format.
- After closing a gap — propose to the owner either to record the fact in `Projects/<solo-project>/journal/YYYY-MM-DD <niche>.md`, or to `Tasks/ideas.md` if it's a raw insight.

## Origin

Key errors from an early run:
- Web-research = averaged signal from blogs, doesn't see platform blockers → fix: first move is a live tool.
- No confidence scale → fix: marker on every quantitative claim.
- Rate shifted three times in one day under "does this work?" pressure → fix: stop rule.
- Enthusiasm over the request (framework on 8+7 cases for "I want money") → fix: output = one URL or an honest "none".
