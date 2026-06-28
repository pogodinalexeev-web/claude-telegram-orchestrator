---
name: megafon-calls
description: Pull call recordings from a mobile operator's personal cabinet via stealth browser and transcribe them. Login via SMS code, capture recordings by number or all, download mp3, transcribe via AssemblyAI. Use when owner says "pull call recordings", "extract conversations from carrier", "record calls with number X", "/megafon-calls", or for building an evidence base for a dispute.
---

# megafon-calls — call recordings from operator cabinet

Pull audio recordings of conversations from the mobile operator's personal cabinet and transcribe them. Production use case: building an evidence base for a dispute with a service provider.

## Key facts

- **Carrier archive — rolling window ~1 month.** Old recordings are deleted permanently. Pull in time.
- **Browser — stealth stack only** (patchright + Xvfb), as in the `stealth-browser` skill. Built-in Playwright MCP does not work on VPS.
- **Session persists** in profile `~/browser/<operator>/userdata`. Log in once via SMS — reuse until session expires.
- Transcription — via skill `batch-aai-transcribe` (AssemblyAI, key at `/etc/claude-tg/assemblyai-key`).

## Stack

- venv `~/browser/venv` (patchright), Chrome `~/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome`.
- Working scripts — `~/browser/<operator>/`:
  - `login.py` — login via SMS code (needed on first run / expired session).
  - `pull.py` — **main**: `list` (call list) and `download` (download by callId).

## Algorithm

### 1. Check/refresh login

Try `list` (step 2) first. If it fails with "session expired" → re-login:

```bash
cd ~/browser && source venv/bin/activate
# start login; script fills number, clicks "Get code", waits for code in /tmp/mf-code.txt
nohup xvfb-run -a -s "-screen 0 1280x900x24" python3 <operator>/login.py >/tmp/mf-login.log 2>&1 &
```

Owner sends 6-digit code → write it to file:

```bash
echo "123456" > /tmp/mf-code.txt
```

Script enters the code and saves the session (`auth.json` + profile `userdata`).

### 2. List calls

```bash
cd ~/browser && source venv/bin/activate
xvfb-run -a -s "-screen 0 1280x900x24" python3 <operator>/pull.py list
# or by specific number (digits without +):
xvfb-run -a -s "-screen 0 1280x900x24" python3 <operator>/pull.py list --number <phone>
```

Output: `TOTAL`, `NEWEST/OLDEST` (archive boundaries) and rows `date | duration | number | callId`. Copy needed `callId` values.

### 3. Download recordings

```bash
xvfb-run -a -s "-screen 0 1280x900x24" python3 <operator>/pull.py download \
  --ids <callId1>,<callId2>,<callId3> \
  --out $VAULT/Resources/attachments \
  --prefix <topic-slug>
```

Files saved as `<prefix>-1.mp3`, `<prefix>-2.mp3`… (MP3, ~160 kbps). Rename by date/topic and move to project.

### 4. Transcribe

Call skill `batch-aai-transcribe` on the folder with downloaded mp3 — get text with speaker labels. For evidence — store transcripts in `Projects/<X>/journal/` alongside audio.

## How it works under the hood (for debugging)

- Operator cabinet — internal API `api.<operator>/mlk/api/eve/...`. Requires headers `x-cabinet-authorization: Bearer <JWT>` (~20 min lifetime) + `x-app-type`, `x-cabinet-*` + cookies.
- Trick: **don't fake the token** — intercept the actual XHR of the page (`callHistory`) via `page.on("request")`, grab its headers and replay via `ctx.request.get(...)`. Token is fresh because the page just generated it.
- List: `GET /mlk/api/eve/v2/callHistory?screen=callRecords&page=N&count=100` → `calls[]` (`cgpn` number, `duration` ms, `lastReplyDate` UTC, `callId`).
- Audio: `GET /mlk/api/eve/callRecords/call?recordId=<callId>` with `accept: audio/mpeg` → mp3.

## Dangers

- 🔴 In the UI next to "Download" (`aria-label="Download"`) there is "Delete" (`aria-label="Delete"`). The API path (`pull.py`) doesn't touch them — read-only. No need to click recordings manually.
- Bearer is short-lived — if >20 min passed between `list` and `download` and 401/500 errors appear, just restart (script captures fresh headers each run).
- `429 Too Many Requests` from neighboring reference services — this is about third-party site parsing, not the operator itself.

## History

- Created for pulling call recordings and transcribing them for a dispute evidence base. Multiple exploratory scripts consolidated into one `pull.py` (list/download).
