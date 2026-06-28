# rollout-patch — roll out a single point fix to tg-bot.py fleet

Applies ONE point fix (replace by anchor / insert by anchor) to `/home/<user>/tg-bot.py` for selected bots. Root — via `docker` (sudo on VPS broken). Each bot has its own customized `tg-bot.py` of different size → fix must be **point-specific, not whole-file rewrite**.

## Fleet map

| Bot | UID | Unit | Code |
|---|---|---|---|
| **owner** (self) | 1000 | `claude-tg-bot.service` | `/home/owner/tg-bot.py` |
| user-b | 1001 | `claude-tg-bot-user-b.service` | `/home/user-b/tg-bot.py` |
| user-c | 1002 | `claude-tg-bot-user-c.service` | `/home/user-c/tg-bot.py` |
| user-d | 1003 | `claude-tg-bot-user-d.service` | `/home/user-d/tg-bot.py` |
| user-e | 1004 | `claude-tg-bot-user-e.service` | `/home/user-e/tg-bot.py` |

> Context: "at some point we'll replace all bots with the current version adjusted for each user's specifics, and then these patches are needed to keep versions current". Patch ≠ file replacement — each has its own specifics, fix only the needed piece.

## Main rule: point fix, SELF — last and delayed

- **Whole-file overwrite is forbidden.** Files differ. Only "find X → replace with Y" or "after anchor Z insert block".
- **Own unit** (`claude-tg-bot.service`, owner) restarts **last and delayed** — otherwise kills current turn before sending response (see `fleet-restart`).

## Algorithm — for EACH target bot

### 1. Backup (MSK time)
```bash
TS=$(TZ=Europe/Moscow date +%Y%m%d-%H%M%S)
docker run --rm -v /home/<user>:/dst alpine \
  cp /dst/tg-bot.py /dst/tg-bot.py.bak-$TS
```

### 2. Apply point fix
Replace by anchor or insert. Example (anchor replace via python inside docker — more reliable than sed for multiline):
```bash
docker run --rm -v /home/<user>:/dst alpine sh -c '
python3 - <<"PY"
p="/dst/tg-bot.py"; s=open(p).read()
anchor="<ANCHOR_LINE>"
assert anchor in s, "anchor not found"   # anchor missing → crash, file untouched
s=s.replace(anchor, "<NEW_BLOCK>", 1)
open(p,"w").write(s)
PY'
```
Anchor not found → script crashes on `assert`, file untouched. Normal: this bot doesn't have the code/has different version, handle manually, don't force it.

### 3. Syntax check
```bash
docker run --rm -v /home/<user>:/dst alpine python3 -m py_compile /dst/tg-bot.py && echo OK
```
**Fails → RESTORE from backup, DON'T restart bot, report, stop for this bot:**
```bash
docker run --rm -v /home/<user>:/dst alpine cp /dst/tg-bot.py.bak-$TS /dst/tg-bot.py
```

### 4. Return owner (docker creates root-owned files)
```bash
docker run --rm -v /home/<user>:/dst alpine chown <uid>:<uid> /dst/tg-bot.py
```

### 5. Restart unit
**Other bot — immediately:**
```bash
docker run --rm --privileged --pid=host alpine \
  nsenter -t 1 -m -u -i -n -p -- systemctl restart claude-tg-bot-<user>.service
```
**Self (owner) — last, delayed** (container lives in its own cgroup, survives bot restart):
```bash
docker run -d --rm --privileged --pid=host alpine sh -c \
  'sleep 5; nsenter -t 1 -m -u -i -n -p -- systemctl restart claude-tg-bot.service'
```

### 6. Liveness check
```bash
docker run --rm --privileged --pid=host alpine \
  nsenter -t 1 -m -u -i -n -p -- systemctl is-active claude-tg-bot-<user>.service
```
`failed` → pull log:
```bash
docker run --rm --privileged --pid=host alpine \
  nsenter -t 1 -m -u -i -n -p -- journalctl -u claude-tg-bot-<user>.service -n 20 --no-pager
```

## Safety / when NOT to roll

- **First on one bot** (usually self — owner), confirm it works, then roll to others. Don't deploy to all at once without testing.
- **py_compile is required** before every restart — broken syntax will kill bot hard.
- **restore-on-fail** — on compile failure: revert and DON'T restart. Better the old working version than a dead bot.
- **Other bot in mid-conversation** — restart cuts their turn. By default warn owner before mass rollout (affects other users).
- **Don't keep old backups forever** — clean up old `.bak-*` after successful rollout (VPS disk is tight, ~96% used).

## Origin

Extracted from a week of manual fleet management. Root mechanism — `docker-sudo`. Delayed self-restart — `fleet-restart`. Trigger: series of manual patches to other users' `tg-bot.py` without any safeguards (backup/compile/rollback).
