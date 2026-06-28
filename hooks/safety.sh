#!/usr/bin/env bash
# PreToolUse (Bash): dangerous command blocker.
# exit 2 = block the call. Protects the vault from accidental rm -rf, force push, sudo.

INPUT=$(cat 2>/dev/null || echo "{}")

# Dangerous patterns (extensible)
PATTERNS=(
  'rm -rf /'
  'rm -rf ~'
  'rm -rf \*'
  'rm -rf \.\.'
  'git push.*--force'
  'git push.*-f '
  'git reset --hard origin'
  'git clean -fd'
  ':(){ :|:&'      # fork bomb
  'sudo rm'
  'mkfs\.'
  'dd if=.*of=/dev/'
  'curl.*\|.*sh'   # curl pipe shell
  'wget.*\|.*sh'
)

for pat in "${PATTERNS[@]}"; do
  if echo "$INPUT" | grep -qE "$pat"; then
    echo "BLOCKED by safety hook: matched pattern '$pat'." >&2
    echo "   If this is intentional — disable the hook or explicitly confirm in chat." >&2
    exit 2
  fi
done

exit 0
