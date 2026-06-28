---
name: source-scout
description: Meta-scout for platforms — where live people talk about a niche/topic. Returns a map of places with a mandatory breakdown by source type (exchange / forum / TG / Discord / comments / aggregator / blog), not a top-10 list of links. Feeds niche-scout. Use for "/source-scout <niche>", "where do people talk about this", "find platforms for topic X".
---

# /source-scout — platform scout by niche

## Purpose
Find **where** people talk about a topic before niche-scout goes to find **what** they say there. Output — a platform map with a mandatory breakdown by type, not a top-10 links dump.

Chain: `source-scout <niche>` → platform map → `niche-scout <niche>` uses it as input → findings map with gap numbers → `freelance-pulse gap-N.M`.

## Algorithm

1. **Clarify the niche**: specific enough that platforms don't spread out (not "AI", but "AI voiceover for podcasters"; not "copywriting", but "copywriting prompts for entrepreneurs").
2. **Run WebSearch + WebFetch** with different formulations: "<niche> community", "<niche> discord", "<niche> forum", "<niche> telegram channel", "<niche> reddit", "<niche> kwork OR fl.ru OR profi.ru", "<niche> blog cases".
3. **Fill in the breakdown** by type (see below). Each type — a separate section. If a type is empty — write "not found", don't fill with junk.
4. Output the map in the format below.

## Output format — mandatory breakdown by type

```
🗺️ Platforms for niche: <name>

▸ Exchanges (open order feeds)
  - <name>, <URL>, type: <local/global>, activity: <freshness of recent orders>
  - ...
  (if empty: "no live exchanges found for this niche")

▸ Forum communities (permanent audience, not a feed)
  - <name>, <URL>, ~<N> members, activity: <posts/day or last post>
  - ...

▸ TG channels / chats
  - <link>, type: [cases | discussions | catalog | news], ~<N> subscribers
  - ...

▸ Discord / Slack servers
  - <name>, <public invite URL if open>, type
  - ...

▸ Comments (where exactly to dig for live opinions)
  - YouTube comments under reviews of tool <Y>
  - Reddit threads r/<sub> with tag <flair>
  - ProductHunt discussions of product <Z>
  - ...

▸ Blog aggregators / catalogs
  - <name>, <URL>, how to filter by niche: <specific filter/tag>
  - ...

▸ Personal first-person blogs (cases only, not reviews)
  - <author> @<handle>, <URL>, what they write: <one phrase>
  - ...

—

🚫 What I didn't find (types with no live signal):
  - <type>: <why — niche too young / closed / local segment empty / etc>

—

→ Pass to niche-scout: "only dig in these platforms, don't cover others".
```

## Strict rules

- **Mandatory breakdown by type.** Not "here are 10 links", but 7 type sections. If a section is empty — explicitly "not found", don't mask the gap.
- **Not top-10.** Goal — **diversity of types**, not density of one type. Better 1-2 in each type than 8 exchanges and nothing else.
- **Each platform with URL and at least one freshness marker** (post freshness, member count, activity). Without marker — don't include.
- **Local/English split explicit.** In TG channels and Discord indicate language — for a local-language niche an English Discord is often irrelevant.
- **Don't assess platform quality.** Not "best", not "most active". Facts only: URL, type, size, freshness.

## Stop signals

- If the niche can't get **≥3 types** with a live signal — write honestly "niche too thin / closed for open reconnaissance, source-scout is off", don't inflate.
- If WebSearch returns only review articles without pointing to platforms — this means **live people don't talk about the niche in the open**. A separate signal, not a reason to invent.

## Origin

niche-scout was written with a **hardcoded** list of platforms (exchanges/forums/blogs). For a niche like "AI voiceover for podcasters" that's the wrong direction: live discussions sit in niche Discord servers, in YouTube comments under tool reviews, in specialized TG chats. niche-scout didn't find them because it was looking in the wrong places. source-scout — the fix: **map of places first**, then reconnaissance inside.

The risk of duplication with WebSearch is removed by the strict type breakdown — without it, this would just be a WebSearch wrapper.
