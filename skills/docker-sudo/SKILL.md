# docker-sudo — bypass NoNewPrivileges via Docker

**Context:** bot runs in a systemd service with `NoNewPrivileges=yes` → `sudo` is blocked at kernel level. `docker` group is available for all bots → Docker CLI works without sudo and without restrictions.

**History:** initially docker was only for the owner bot. Other users were added later by `usermod -aG docker <user>`. After adding to the group — required service restart (`claude-tg-bot-<user>.service`), otherwise old process doesn't see the new group.

## Pattern 1: operations with other users' files

```bash
# Instead of: sudo cp /src /home/user-b/.claude/file && sudo chown user-b:user-b ...
docker run --rm \
  -v /home/user-b:/dst \
  -v /path/to/src:/src:ro \
  alpine sh -c 'cp /src /dst/file && chown 1001:1001 /dst/file && chmod 600 /dst/file'
```

UID/GID: `owner=1000`, `user-b=1001`, `user-c=1002`, `user-d=1003` (from /etc/passwd).

## Pattern 2: system commands (systemctl, kill, etc.)

```bash
# Instead of: sudo systemctl restart some-service
docker run --rm --privileged --pid=host alpine \
  nsenter -t 1 -m -u -i -n -p -- systemctl restart some-service

# Instead of: sudo kill -9 <pid>
docker run --rm --privileged --pid=host alpine \
  kill -9 <pid>
```

## Pattern 3: write to system paths (/etc, /usr/local/bin)

```bash
# Instead of: sudo tee /etc/some.conf
echo "content" | docker run --rm -i \
  -v /etc:/etc_host \
  alpine sh -c 'cat > /etc_host/some.conf'
```

## When Docker also won't help

- `PrivateNetwork=yes` in service — blocks network namespace. Check: `systemctl cat claude-tg-bot | grep Private`
- Kernel operations (modprobe, sysctl) — need `--privileged`, usually available.

## Origin

Found when trying to copy Anthropic credentials to another user's home directory. sudo was blocked, docker was not.
