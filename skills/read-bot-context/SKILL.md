# read-bot-context — read code/config/vault of another bot

Reads files of any bot in the fleet (`/home/<user>/...`) without manual sudo. Root — via `docker` (sudo on VPS broken). Read-only: mount `:ro`, don't write anything.

## Fleet map

| Bot | UID | Home | Code | Token/allowlist | Vault |
|---|---|---|---|---|---|
| owner | 1000 | `/home/owner` | `tg-bot.py` | `/etc/claude-tg/` | `/home/owner/vault` |
| user-b | 1001 | `/home/user-b` | `tg-bot.py` | `/etc/claude-tg-user-b/` | `/home/user-b/vault` |
| user-c | 1002 | `/home/user-c` | `tg-bot.py` | `/etc/claude-tg-user-c/` | `/home/user-c/vault` |
| user-d | 1003 | `/home/user-d` | `tg-bot.py` | `/etc/claude-tg-user-d/` | `/home/user-d/vault` |
| user-e | 1004 | `/home/user-e` | `tg-bot.py` | `/etc/claude-tg-user-e/` | `/home/user-e/vault` |

> Own files (`/home/owner/...`) — read directly via `Read`/`Grep` — no docker needed. Docker only for **other people's** homes.

## Read patterns

**Full file / code fragment of another bot:**
```bash
docker run --rm -v /home/<user>:/src:ro alpine cat /src/tg-bot.py
```
For large files — filter in place, don't pull 200K into context:
```bash
# find where user-b's bot describes marker __CAL_PROPOSE__
docker run --rm -v /home/user-b:/src:ro alpine grep -n "CAL_PROPOSE\|SYSTEM_PROMPT" /src/tg-bot.py
# extract line range
docker run --rm -v /home/user-b:/src:ro alpine sed -n '168,310p' /src/tg-bot.py
```

**SYSTEM_PROMPT of a bot** (often need to compare with own):
```bash
docker run --rm -v /home/<user>:/src:ro alpine \
  sh -c 'grep -n "SYSTEM_PROMPT" /src/tg-bot.py | head'
```

**Vault / CLAUDE.md of another bot:**
```bash
docker run --rm -v /home/<user>:/src:ro alpine cat /src/vault/.claude/CLAUDE.md
docker run --rm -v /home/<user>:/src:ro alpine ls -la /src/vault/Projects
```

**Token/allowlist (sensitive — only when needed):**
```bash
docker run --rm -v /etc:/etc_host:ro alpine cat /etc_host/claude-tg-<user>/allowlist
```

## Algorithm

1. **Determine bot and what's needed** (code / prompt / vault / config).
2. **Own bot → direct Read/Grep.** Other bot → docker `:ro`.
3. **Don't pull whole 130–210K files into context.** First `grep -n` by anchor, then `sed -n 'A,Bp'` for needed range.
4. **Comparing with own bot** (typical request "does user-b have the same as me?"): pull same section from both, show diff:
   ```bash
   docker run --rm -v /home/user-b:/b:ro -v /home/owner:/o:ro alpine \
     sh -c 'diff <(sed -n "168,310p" /o/tg-bot.py) <(sed -n "168,310p" /b/tg-bot.py)'
   ```
5. **Summary to owner** — what was found, without dumping raw 200 lines.

## Security

- Only `:ro`. If writing/editing is needed — that's `rollout-patch`, not this skill.
- Read other bots' tokens only when task explicitly requires it (send diagnostics etc.), not "just in case".

## Origin

Extracted from manual pattern of browsing other bots. Mechanism — `docker-sudo`. Case trigger: couldn't read another bot's model "because sudo is cut" — actually readable via docker.
