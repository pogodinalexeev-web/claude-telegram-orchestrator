# fleet-restart — restart a group of bots on VPS

Restarts one, several, or all bot systemd units in the fleet. Root — via `docker` (sudo on VPS broken: `NoNewPrivileges` + broken audit plugin). Built on `docker-sudo` mechanism.

## Fleet map

| Bot | UID | Unit | Code |
|---|---|---|---|
| **owner** (self) | 1000 | `claude-tg-bot.service` | `/home/owner/tg-bot.py` |
| user-b | 1001 | `claude-tg-bot-user-b.service` | `/home/user-b/tg-bot.py` |
| user-c | 1002 | `claude-tg-bot-user-c.service` | `/home/user-c/tg-bot.py` |
| user-d | 1003 | `claude-tg-bot-user-d.service` | `/home/user-d/tg-bot.py` |
| user-e | 1004 | `claude-tg-bot-user-e.service` | `/home/user-e/tg-bot.py` |

## Main rule: restart SELF delayed

`claude-tg-bot.service` (owner) — this is **the process I'm currently running in**. Direct `systemctl restart` kills the current turn before the response goes out to chat. So self-restart is done **delayed from a docker container** — the container lives in its own cgroup, survives the bot restart, and triggers it after 5s when my response is already sent:

```bash
docker run -d --rm --privileged --pid=host alpine sh -c \
  'sleep 5; nsenter -t 1 -m -u -i -n -p -- systemctl restart claude-tg-bot.service'
```

Other bots — restart immediately (not my process):

```bash
docker run --rm --privileged --pid=host alpine \
  nsenter -t 1 -m -u -i -n -p -- systemctl restart claude-tg-bot-<user>.service
```

## Algorithm

1. **Determine targets** from owner's request: one bot / list / "all" / "all except me".
2. **Split into "self" and "others".** If target includes `owner` — goes through delayed pattern, always **last**.
3. **Restart others** — one by one, immediately, checking result:
   ```bash
   for u in user-b user-c user-d user-e; do
     echo "=== $u ==="
     docker run --rm --privileged --pid=host alpine \
       nsenter -t 1 -m -u -i -n -p -- systemctl restart claude-tg-bot-$u.service
     docker run --rm --privileged --pid=host alpine \
       nsenter -t 1 -m -u -i -n -p -- systemctl is-active claude-tg-bot-$u.service
   done
   ```
4. **Liveness check** after each restart — `is-active` should return `active`. If `failed` — pull last log lines:
   ```bash
   docker run --rm --privileged --pid=host alpine \
     nsenter -t 1 -m -u -i -n -p -- journalctl -u claude-tg-bot-<user>.service -n 20 --no-pager
   ```
5. **Self — last**, delayed pattern (see above). In response to owner honestly: "will restart myself 5s after this message".
6. **Report** in one table: who was restarted, who is `active`, who crashed.

## When NOT to trigger

- If a bot is in the middle of a user turn — restart will cut the other person's conversation. For other people's bots owner decides; by default warn "a user may have an active conversation — restart?".
- Mass restart of all five at once — owner confirmation (affects all users).

## Origin

Extracted from repeated fleet management pattern during weekly review. Root mechanism — `docker-sudo`. Delayed self-restart — `memory/feedback_bot_delayed_restart.md`.
