---
name: claude-relogin
description: Update expired Claude Code subscription on VPS bots (owner, user-b, user-c, user-d, user-e). N independent OAuth logins from one Max account — each bot lives with its own refresh token for months. Triggers: "update token on bots", "login link", "subscription expired on bot", "anthropic authorization", bot on VPS fails with 401/auth error in `claude -p`.
---

# claude-relogin — update Claude Code token on VPS bots

## When it fires

Bots on VPS run `claude -p` under users `owner`, `user-b`, `user-c`, `user-d`, `user-e`. Each has its own `~/.claude/.credentials.json`. When token expires (bot sends error or is silent):

- "update token on bots"
- "login link / authorization link"
- "subscription expired on bot"
- "anthropic authorization" (= Claude Code OAuth, not Anthropic API)
- "token from browser on mac"

This is **not** about user authorization to the bot via deep link, **not** about Telegram. It's about `claude` CLI on VPS stopping to work and needing re-login via owner's browser on Mac.

## Main rule of the scheme

**N independent OAuth logins** (currently N=5: owner, user-b, user-c, user-d, user-e), one per bot, from **one** Max account. Each has its own refresh token.

**DON'T**: one login → copy `.credentials.json` to other users. This was the old (wrong) scheme. Anthropic OAuth uses **rotating single-use refresh tokens** — the first bot to refresh revokes refresh for others → they get 401 after ~8 hours. Sources: [#43392](https://github.com/anthropics/claude-code/issues/43392), [#21765](https://github.com/anthropics/claude-code/issues/21765).

**Symlink to shared file doesn't work**: CLI writes credentials via atomic replace (unlink+create), symlink breaks on first refresh.

With N independent OAuth tokens, refresh tokens don't interfere → each bot self-refreshes → re-login needed only when refresh actually expires (months with regular use).

## Two launch modes

**A. From Mac (standard)** — via `ssh lab-root`, steps below ("Mac algorithm").

**B. From inside VPS bot** (e.g. owner-bot needs to re-login user-e) — `ssh lab-root` is unavailable from there. Go via `docker-sudo` workaround + long-lived container. "Inside VPS algorithm" section below.

## Mac algorithm (N parallel OAuth sessions)

### 1. Start login for all users in parallel

```bash
ssh lab-root '
for u in owner user-b user-c user-d user-e; do
  sudo -u $u -H bash -lc "rm -f /tmp/claude-auth-$u.txt; tmux kill-session -t claude-auth-$u 2>/dev/null; tmux new-session -d -s claude-auth-$u \"claude auth login > /tmp/claude-auth-$u.txt 2>&1\""
done
sleep 8
for u in owner user-b user-c user-d user-e; do
  echo "=== $u ==="
  sudo -u $u cat /tmp/claude-auth-$u.txt | grep -oE "https://claude.com/cai[^ ]+"
done'
```

### 2. Give links to owner

Extract URLs and put in chat with note of which belongs to whom. Owner opens them **one by one** in one browser (where Max account is logged in), sends codes (format: `<token>#<state>`).

### 3. Send codes back to tmux sessions

```bash
ssh lab-root '
sudo -u owner  -H bash -lc "tmux send-keys -t claude-auth-owner  \"<CODE_OWNER>\" Enter"
sudo -u user-b -H bash -lc "tmux send-keys -t claude-auth-user-b \"<CODE_B>\" Enter"
sudo -u user-c -H bash -lc "tmux send-keys -t claude-auth-user-c \"<CODE_C>\" Enter"
sudo -u user-d -H bash -lc "tmux send-keys -t claude-auth-user-d \"<CODE_D>\" Enter"
sudo -u user-e -H bash -lc "tmux send-keys -t claude-auth-user-e \"<CODE_E>\" Enter"
sleep 12
for u in owner user-b user-c user-d user-e; do
  echo "=== $u ==="
  sudo -u $u cat /tmp/claude-auth-$u.txt | tail -2
done'
```

Wait for `Login successful.` for each.

### 4. Check: different refresh tokens + smoke-test

```bash
ssh lab-root '
echo "=== unique refresh tokens (should be N different) ==="
for u in owner user-b user-c user-d user-e; do
  r=$(sudo -u $u cat /home/$u/.claude/.credentials.json | python3 -c "import json,sys; print(json.load(sys.stdin)[\"claudeAiOauth\"][\"refreshToken\"][:30])")
  echo "$u: $r"
done
echo "--- smoke ---"
for u in owner user-b user-c user-d user-e; do
  echo "=== $u ==="
  sudo -u $u -H bash -lc "claude -p \"one\" --model claude-sonnet-4-6 2>&1 | head -1"
done'
```

### 5. Restart bot services

```bash
ssh lab-root "systemctl restart claude-tg-bot.service claude-tg-bot-user-b.service claude-tg-bot-user-c.service claude-tg-bot-user-d.service claude-tg-bot-user-e.service && sleep 3 && systemctl is-active claude-tg-bot.service claude-tg-bot-user-b.service claude-tg-bot-user-c.service claude-tg-bot-user-d.service claude-tg-bot-user-e.service"
```

## Inside VPS algorithm (one user via docker workaround)

When `ssh lab-root` is unavailable (running from inside one of the VPS bots). Use `docker-sudo` for context, then — specifically one user (`$U` below = owner / user-b / user-c / user-d / user-e):

```bash
U=user-e   # example

# 1. Long-lived container (sleep 1800 = 30 min for code entry). SHORT docker --rm breaks tmux!
docker rm -f relog 2>/dev/null
docker run -d --name relog --privileged --pid=host alpine sh -c "nsenter -t 1 -m -u -i -n -p -- sleep 1800" >/dev/null

# 2. Start claude auth login (NOT setup-token!) under user via login shell (su -). Tmux survives container.
docker exec relog nsenter -t 1 -m -u -i -n -p -- bash -c "
  rm -f /tmp/claude-auth-$U.txt
  su - $U -c 'tmux kill-server 2>/dev/null; tmux new-session -d -s claude-auth-$U \"claude auth login > /tmp/claude-auth-$U.txt 2>&1\"'
  sleep 12
  sed 's/\x1b\[[0-9;]*[a-zA-Z]//g; s/\x1b\][^\x07]*\x07//g' /tmp/claude-auth-$U.txt | grep -oE 'https://claude\.com/cai/[^ ]+' | sort -u | head -1
"
# → URL give to owner, wait for their code <token>#<state>

# 3. Send code (state in code MUST match state in URL — otherwise session is old, restart)
CODE='<token>#<state>'
docker exec relog nsenter -t 1 -m -u -i -n -p -- bash -c "
  su - $U -c 'tmux send-keys -t claude-auth-$U \"$CODE\" Enter'
  sleep 15
  sed 's/\x1b\[[0-9;]*[a-zA-Z]//g; s/\x1b\][^\x07]*\x07//g' /tmp/claude-auth-$U.txt | tail -5
  python3 -c \"
import json, time
d = json.load(open('/home/$U/.claude/.credentials.json'))['claudeAiOauth']
print('refresh:', d['refreshToken'][:20] or '<EMPTY!>')
print('exp in h:', round((d['expiresAt']/1000 - time.time())/3600, 2))
\"
"

# 4. Restart bot + smoke
docker exec relog nsenter -t 1 -m -u -i -n -p -- bash -c "
  systemctl restart claude-tg-bot-$U.service
  sleep 3
  systemctl is-active claude-tg-bot-$U.service
  su - $U -c 'claude -p \"one\" --model claude-sonnet-4-6 2>&1' | tail -3"

# 5. Clean up container
docker rm -f relog
```

Note: for `owner` the service is named `claude-tg-bot.service` (without `-owner`), for others — `claude-tg-bot-<user>.service`.

## Anti-patterns

- ❌ **One OAuth → copy to other users**. Old scheme, breaks in a day. Refresh is one-time.
- ❌ **Symlink to shared file**. CLI does atomic replace, symlink breaks on first refresh.
- ❌ **`claude setup-token` instead of `claude auth login`**. `setup-token` creates a short-lived accessToken WITHOUT refresh token (`refreshToken: ""`), expires in ~12 hours. For bot need full OAuth with rotating refresh — only `claude auth login`. Check: after login `.credentials.json` must have BOTH `accessToken` AND non-empty `refreshToken`.
- ❌ **`runuser -u <user> -- tmux new-session -d` from short-lived `docker --rm` container**. Tmux-server attaches to docker container via process chain and dies when container exits. Send-keys then goes to void → code not delivered. Fix: `docker run -d --name ... sleep 1800` (long-lived) + `su - <user> -c 'tmux new-session -d -s ...'` (login shell).

## Context paths

- VPS users: `owner` (UID 1000), `user-b` (1001), `user-c` (1002), `user-d` (1003), `user-e` (1004)
- Claude Code credentials: `/home/<user>/.claude/.credentials.json` (chmod 600, owner `<user>:<user>`)
- Root access: `ssh lab-root` (no password) — **only from Mac side**. From inside VPS bot `lab-root` is not resolvable, use `docker-sudo` (see "Inside VPS algorithm").
- Services systemd: `claude-tg-bot.service` (owner), `claude-tg-bot-user-b.service`, `claude-tg-bot-user-c.service`, `claude-tg-bot-user-d.service`, `claude-tg-bot-user-e.service`
