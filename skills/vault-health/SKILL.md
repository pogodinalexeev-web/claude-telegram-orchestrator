---
name: vault-health
description: Vault hygiene — stuck projects, old #waiting items, oversized files, inbox duplicates. Use for "/vault-health", "check the vault", in weekly-review.
---

# /vault-health — vault hygiene

## Algorithm (do it yourself, without delegating to a weaker model)
1. **Stuck projects** — projects in `Projects/*` without open `#next`:
   ```bash
   for p in Projects/*/; do
     if ! grep -rq "#next" "$p"; then echo "STUCK: $p"; fi
   done
   ```
2. **Stale #waiting** — `#waiting` older than 7 days:
   ```bash
   grep -rn "#waiting" Projects/ | <filter by date>
   ```
3. **Large files** (>5000 lines):
   ```bash
   find . -name "*.md" -not -path "./.obsidian/*" -not -path "./.git/*" | xargs wc -l | awk '$1 > 5000'
   ```
4. **Inbox overload**: `wc -l inbox.md` — if >50 lines, flag.
5. **Old empty dailies** — empty `Journal/*.md`.
6. **Duplicates in `Resources/atoms/`** — slugs with similar names (lev-distance).
7. **Script sync across environments** — for scripts in `.claude/scripts/` with headers `# canonical: ... # deploy: ...` compare hashes:
   ```bash
   for f in .claude/scripts/<scriptname>.py .claude/scripts/<scriptname>.sh; do
     local_hash=$(md5sum "$f" | cut -d' ' -f1)
     remote_path=$(grep -m1 "^# deploy:" "$f" | awk '{print $3}')
     remote_hash=$(ssh remote-host "md5sum $remote_path 2>/dev/null | cut -d' ' -f1")
     [ "$local_hash" != "$remote_hash" ] && echo "DRIFT: $f vs $remote_path"
   done
   ```
   If there's drift — flag with a suggestion to sync.

8. **Eval after major manual.md edit** *(if 3+ files named `manual.md` were edited in the current session)* — run a check:
   - **Broken relative links** in edited files + related files (`status.md`, `index.md`):
     ```bash
     python3 -c "
     import os, re, urllib.parse
     link_re = re.compile(r'\]\(([^)]+)\)')
     for f in ['Tasks/manual.md', 'status.md', 'index.md'] + <edited project manuals>:
         with open(f, encoding='utf-8') as fp: content = fp.read()
         d = os.path.dirname(f) or '.'
         for m in link_re.finditer(content):
             link = m.group(1)
             if link.startswith(('http://','https://','#','mailto:')) or '<' in link: continue
             path = urllib.parse.unquote(link.split('#',1)[0])
             target = path if path.startswith('/') else os.path.normpath(os.path.join(d, path))
             if not os.path.exists(target): print(f'❌ {f}: {link}')
     "
     ```
   - **Out-of-sync between moved and remaining references** — for each project where `manual.md` was compressed and material moved to `Resources/old/`:
     ```bash
     # Find mentions of moved sections in project tasks.md / ideas.md / log.md
     grep -nE '(moved-section-name|old-hypothesis-name)' "Projects/<X>/tasks.md" "Projects/<X>/ideas.md"
     ```
     If tasks.md/ideas.md has a copy of something moved — flag: truth point is blurring, leave only a pointer.
   - **Record the compression in project `log.md`** — structural event of the project (moving history to old/, reformatting) deserves a line in `Projects/<X>/log.md` (append, new at top).

## Report
```
🩺 Vault health (YYYY-MM-DD)

Stuck projects (no #next):
- <list>

Stale #waiting (>7 days):
- <list with dates>

Files >5000 lines:
- <list>

inbox.md: N lines (need /process-inbox? Y/N)

Recommendations:
1. <concrete action>
2. <second>
```

## Rules
- This is diagnostics, not a work plan. Don't propose fixing everything at once.
- If everything is fine — say "vault is healthy" and don't invent problems.
