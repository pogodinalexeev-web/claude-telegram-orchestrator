---
name: remind
description: Set a delayed reminder for the owner via systemd timer. Bot creates the unit itself, sends a message via its TG token at time X. Use when owner says "remind me in N minutes/hours", "remind me tomorrow at HH:MM", "remind me on date N at HH:MM <text>", "/remind ...". Same skill lives on all bots on one VPS — each isolated: sees and edits ONLY its own timers, sends ONLY to its own owner.
---

# Skill: remind — delayed reminders via systemd

## When to use

Owner asks to be reminded about something in the future. Trigger phrases:
- "remind me in 10 minutes …"
- "remind me tomorrow at 09:00 …"
- "remind me 20.05 at 14:30 …"
- "/remind <when> <text>"

Don't use for:
- Immediate tasks (just do it),
- Regular cycles once a day/week — use different mechanism (cron/schedule skills if available; otherwise create timer with `--on-calendar` and warn it survives reboot).

## Architecture (shared across all bots)

**One shared mechanism:** systemd `--user` timers. For each bot user on VPS, `linger` is enabled — user-systemd runs without session, survives reboot.

**Isolation (important for security):**
- Each bot can create / read / delete **only its own** user timers. `systemctl --user` physically can't see another user's units — this is kernel level, not trust.
- Sender `/usr/local/bin/tg-remind` determines current user (`id -un`) and reads token **only from own config** (`/etc/claude-tg/` for owner, `/etc/claude-tg-<user>/` for others). Permissions `640 group=<user>` prevent other bots from reading owner's token.
- Owner = **first line** of `allowlist` file (convention). tg-remind sends only there.

**What bot CAN:**
- Create new timers (`systemd-run --user ...`).
- List own timers (`systemctl --user list-timers`).
- Delete own timers (`systemctl --user stop <unit>.timer`).
- Edit own timers (recreate).

**What bot CANNOT (and shouldn't try):**
- Read/edit another bot's timers.
- Access `/home/<other>/`, `/etc/claude-tg-<other>/`.
- `sudo` for system timers — only user-level (sudo blocked by `NoNewPrivileges=yes`).

## Commands

### Set a reminder

**In N minutes/hours:**
```bash
systemd-run --user \
  --on-active=10min \
  --unit="remind-$(date +%s)" \
  /usr/local/bin/tg-remind "call a friend"
```

**At specific calendar time (systemd `OnCalendar=` format):**
```bash
systemd-run --user \
  --on-calendar="2026-05-17 09:00:00" \
  --unit="remind-$(date +%s)" \
  /usr/local/bin/tg-remind "morning standup"
```

Regular (every day at 09:00) — also works, but warn owner that timer will fire without explicit cancellation:
```bash
systemd-run --user --on-calendar="*-*-* 09:00:00" \
  --unit="remind-daily-morning" /usr/local/bin/tg-remind "morning briefing"
```

### List own reminders
```bash
systemctl --user list-timers --all | grep '^.* remind-'
```

### Cancel a reminder
```bash
systemctl --user stop remind-<id>.timer
systemctl --user reset-failed remind-<id>.timer 2>/dev/null || true
```

### Parsing natural language time

If owner writes "tomorrow at 14:30", "in 2 hours", "20.05 at 09:00" — convert to:
- relative → `--on-active=<N>min` or `<N>h`,
- absolute → `--on-calendar="YYYY-MM-DD HH:MM:SS"` (timezone from `Environment=TZ=...` in bot's service).

"Tomorrow" is calculated from **bot's local TZ**, not UTC. Don't calculate manually — systemd handles `OnCalendar` with date+time.

## Response etiquette

After setting a reminder:
1. Tell owner one phrase: "set for <time> in TZ <zone>, name `remind-<id>`".
2. Don't repeat the command that was run — that's noise.
3. If owner wants to cancel — find by text in `list-timers` (or by id if they remember it) and stop.

## Checks before firing

- Reminder text has no tokens / chat_id / passwords — this will go to TG as plain text.
- Time in the past? Decline and ask again.
- Unit with this name already exists? Add microseconds to name or ask "overwrite or new?".

## Known formatting bugs (fix history)

- **2026-05-20:** TG received `Reminder\n\nbuy detergent` literally (literal `\n`). Cause: in `/usr/local/bin/tg-remind` the line `BODY="⏰ Reminder\n\n${TEXT}"` — double quotes in bash do NOT interpret `\n`. Fix: `BODY=$'⏰ Reminder\n\n'"${TEXT}"` (ANSI-C quoting `$'...'` turns `\n` into real newline). If header breaks again in future — check exactly this line.

## If something broke

- `journalctl --user -u remind-<id>.service` — run log.
- `XDG_RUNTIME_DIR=/run/user/$(id -u)` is required for all `systemctl --user` commands if running from non-interactive session (e.g. via ssh non-login).
- If `--user` complains "Failed to connect to bus" — check `loginctl show-user <user> | grep Linger=yes`. Should be `yes`.
