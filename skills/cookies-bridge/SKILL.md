---
name: cookies-bridge
description: Transfer a logged-in session from Chrome on Mac to headless Playwright on VPS without manual steps, without VNC, without re-logging in. Programmatic cookie extraction from Chrome SQLite + Keychain ACL → Playwright storage_state JSON → scp to VPS → headless test. Use when owner says "give VPS access to <site>", "log server into X", "transfer authorization", "cookies from chrome to vps", or when automation of a logged-in site on VPS is needed without a browser window.
---

# cookies-bridge — Chrome (Mac) → headless Playwright (VPS)

## Why

GUI Chromium via VNC doesn't work well on low-RAM VPS (clicks lag, windows crash). The program path: Chrome cookies on Mac are accessible without user prompts **if you know the Chrome Safe Storage password** (via `security` from shell — `find-generic-password -s "Chrome Safe Storage" -a "Chrome" -w`). From shell this call passes without Keychain GUI prompt.

Then — SQLite copy of `~/Library/Application Support/Google/Chrome/Default/Cookies` + AES-128-CBC v10 decryption → Playwright storage_state JSON → scp to VPS → headless Playwright loads cookies and works with the logged-in site.

## When to call

**On request:**
- "give VPS access to <site>", "log server into X"
- "transfer authorization from chrome", "cookies from chrome to vps"
- "headless session for bot on VPS", "bot needs to access <site> automatically"

**Automatically (without explicit request):**
- Owner is building VPS automation for a site they're logged into on Mac. Suggest the pattern, don't default to VNC.

**Don't call:**
- If the site has an API — go through API, not cookies.
- If the task is one-off and faster to do manually on Mac — don't bother copying to VPS.
- If there's an auth-stays-on-mac rule for this account (bank, main email) — **stop, ask for explicit exception**.

## Artifacts

Scripts in `Projects/IT/Production/<assistant>/Resources/cookies-bridge/`:
- `extract.py` — extraction and decryption of cookies on Mac (CLI: domains + `-o <path>`).
- `test-auth.py` — headless check on VPS (CLI: `--auth <path> --url <site>`).

## Algorithm

### 1. Domain discovery
Before extracting — find the exact cookie domain for the site. Without this, `extract.py` returns 0.

```bash
cp "$HOME/Library/Application Support/Google/Chrome/Default/Cookies" /tmp/chk.db
sqlite3 /tmp/chk.db "SELECT DISTINCT host_key FROM cookies WHERE host_key LIKE '%<keyword>%' LIMIT 10"
```

Example: owner said "Kling" → searched `kling` → found `.kling.ai`, `id.kling.ai`, `kling.ai`. Site domain **is not always** the obvious `klingai.com`.

### 2. Get Chrome Safe Storage password

```bash
SAFE_PW=$(security find-generic-password -s "Chrome Safe Storage" -a "Chrome" -w)
```

If run from subprocess Python — hangs on Keychain ACL (Apple TCC). Only from shell, then pass to Python via ENV.

### 3. Extract cookies

```bash
CHROME_SAFE_STORAGE="$SAFE_PW" python3 \
  Projects/IT/Production/<assistant>/Resources/cookies-bridge/extract.py \
  <site>.ai -o /tmp/<service>-auth.json
```

Supports multiple domains in one call — `extract.py <domain1> .<domain2> -o ...`.

### 4. Upload to VPS

```bash
ssh vps "mkdir -p ~/browser/<service>"
scp /tmp/<service>-auth.json vps:~/browser/<service>/auth.json
```

### 5. Headless test

```bash
scp Projects/.../Resources/cookies-bridge/test-auth.py vps:~/browser/<service>/
ssh vps "cd ~/browser && source venv/bin/activate && \
  python3 ~/browser/<service>/test-auth.py \
    --auth ~/browser/<service>/auth.json \
    --url https://<site>/app/"
```

Success indicator: output shows `Login button on page: False (logged)`. If `True (NOT logged)` — cookies didn't work (see "Debug").

### 6. Record in vault
- If task was under auth-stays-on-mac and owner gave an exception — short line in `Journal/log.md` with justification.
- In `Projects/<assistant>/Resources/browser-automation.md` add service to "VPS headless sessions" list.

## Debug

**0 cookies on output:**
- Wrong domain — see step 1.
- Wrong Chrome profile — may have `Default`, `Profile 1`, `Profile 2`. Default is `Default`. If not there — `--db "$HOME/Library/Application Support/Google/Chrome/Profile 1/Cookies"`.
- Not Chrome but Brave/Yandex — they have their own Safe Storage, different path.

**Cookies exist but values have garbage (`?` chars):**
- Script already trims SHA256(host_key) prefix from decrypted value — this is a known Chrome 80+ quirk.
- If still garbage — check the prefix of encrypted values in SQLite: `SELECT substr(hex(encrypted_value),1,6) FROM cookies LIMIT 5`. Should be `763130` (= `v10`). If `763230` (= `v20`) — Chrome switched to App-Bound Encryption, need a different scheme. On macOS as of early 2026 still v10.

**Cookies decrypted, but `test-auth.py` shows Sign-in:**
- Site tied session to IP/UA. Mac→VPS = country change. Try:
  1. Set `user_agent` in new_context = same as Chrome on Mac.
  2. Connect VPN on VPS to same country as Mac.
- Session expired on site. Re-login in Chrome on Mac → re-run.

**`extract.py` hangs without output:**
- Likely forgot `CHROME_SAFE_STORAGE=...` — script will fail with a clear error, but if run in background via harness — output not visible. Run foreground.

## Extension pattern

Idea for later: wrap into one command `cookies-bridge <site-keyword> <service-name>`:
1. Domain discovery in Chrome SQLite by keyword.
2. Extraction.
3. Upload to VPS to `~/browser/<service-name>/`.
4. Headless check.
5. Record in `browser-automation.md`.
