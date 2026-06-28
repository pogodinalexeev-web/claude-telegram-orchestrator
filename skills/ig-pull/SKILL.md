---
name: ig-pull
description: Pull an Instagram post/reel/carousel via Apify instagram-scraper and save JSON+media to Resources/IG/Reels/. Use when owner gives an instagram.com/reel/, instagram.com/p/, instagram.com/<username>/ link, or says "/ig-pull <url>", "parse reel", "pull this post", "what's in this IG".
---

# /ig-pull — Apify Instagram parser

## Triggers

- Message contains `instagram.com/reel/...`, `instagram.com/p/...`, `instagram.com/<user>/`.
- Explicit `/ig-pull <url1> [url2 ...]`.
- "parse reel", "pull this post", "what's in this IG".

If no links — ask one line "Give me the URL".

## Where the token is

- **VPS:** `~/.config/apify/token` (chmod 600).
- **Mac:** `Projects/secrets.md` line after `apify`.

Don't print the token in chat and don't commit it.

## Prerequisite (fresh install)

If this skill is deployed to a new machine or Apify token is regenerated:
1. Get token from Apify console.
2. On VPS: `ssh vps 'mkdir -p ~/.config/apify && echo "<TOKEN>" > ~/.config/apify/token && chmod 600 ~/.config/apify/token'`.
3. Check: `ssh vps 'cat ~/.config/apify/token | head -c 10'` (should start with `apify_api_`).

This pattern (`~/.config/<service>/token` chmod 600) is canonical for any new service with an API key on VPS.

## What to do

1. Extract all IG links from message (regex `https?://(?:www\.)?instagram\.com/(?:reel|p|tv|[A-Za-z0-9_.]+)/[A-Za-z0-9_-]*/?`).
2. Run in one request:

```bash
TOKEN=$(cat ~/.config/apify/token)   # on VPS
curl -s -X POST "https://api.apify.com/v2/acts/apify~instagram-scraper/run-sync-get-dataset-items?token=$TOKEN&timeout=300" \
  -H "Content-Type: application/json" \
  -d '{
    "directUrls": ["<url1>", "<url2>"],
    "resultsType": "posts",
    "resultsLimit": 1,
    "addParentData": false
  }' > /tmp/ig-results.json
```

3. For each post:
   - Read `caption` (full, not first 200), `firstComment`, `latestComments[0..2]`, `ownerUsername`, `hashtags`, `type`.
   - Download `displayUrl` → `Resources/IG/Reels/media/<shortCode>.jpg`.
   - If `type: Sidecar` — all `childPosts[].displayUrl` → `<shortCode>-s<i>.jpg`.
   - If `type: Video` and thumbnail doesn't explain content — download `videoUrl` and extract 4 frames via `ffmpeg` (10/35/65/90%).
4. Save analysis as one file `Resources/IG/Reels/<shortCode> <short phrase>.md` with links to media.
5. Apply **classification checklist**: full caption, frames, comments, rule "monetization > everything".

## What NOT to do

- Don't classify by thumbnail + 200 characters of caption (anti-pattern).
- Don't use `apidojo/instagram-scraper` (paid per-result, breaks on reels).
- Don't leak token in logs/chat/commits.

## Pricing

$0.50 per 1000 results, free $5/month (~10,000 posts). 1 link ≈ $0.0005.
