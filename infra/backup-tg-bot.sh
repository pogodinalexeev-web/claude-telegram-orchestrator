#!/usr/bin/env bash
# backup-tg-bot.sh — mirror tg-bot.py into the vault git repo.
#
# Run hourly via systemd user timer (tg-bot-backup.timer).
# Source of truth is the live file on the server; vault is a read-only mirror.
# Skips the commit if the file has not changed (sha256 compare).
#
# Usage: this script takes no arguments.
# Configure SRC and VAULT/DST_REL below, or override via environment.

set -euo pipefail

SRC="${BOT_SRC:-<install-dir>/tg-bot.py}"
VAULT="${VAULT_DIR:-<install-dir>/vault}"
DST_REL="${BOT_DST_REL:-Projects/<project>/bot/tg-bot.py}"   # path inside vault
DST="$VAULT/$DST_REL"

GIT_AUTHOR_NAME="${GIT_AUTHOR_NAME:-backup-bot}"
GIT_AUTHOR_EMAIL="${GIT_AUTHOR_EMAIL:-backup@localhost}"

[ -f "$SRC" ]              || { echo "no source: $SRC"; exit 1; }
[ -d "$(dirname "$DST")" ] || { echo "no vault dir: $(dirname "$DST")"; exit 1; }

# Skip if files are byte-for-byte identical.
if cmp -s "$SRC" "$DST"; then
    exit 0
fi

cp "$SRC" "$DST"
cd "$VAULT"
git add "$DST_REL"

# Nothing staged after add = only whitespace/metadata changed; skip commit.
git diff --cached --quiet && exit 0

HASH=$(sha256sum "$DST" | cut -c1-8)
git -c "user.email=${GIT_AUTHOR_EMAIL}" \
    -c "user.name=${GIT_AUTHOR_NAME}" \
    commit -m "backup: tg-bot.py snapshot $(date +%Y-%m-%d-%H%M) ($HASH)"

git push origin main
