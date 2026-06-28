#!/usr/bin/env bash
# Cyrillic filename gate — content file naming convention enforcer.
# Fires on Write/Edit to .md files inside Projects/<X>/ or Resources/.
# If the filename is Latin-slug (some-english-slug.md) and not a structural file
# (status.md, manual.md, ...) — warns on stderr.
# Rule origin: vault naming convention — content .md files use native-language
# names (Cyrillic for Russian vaults), not English slugs.

# Hook receives JSON via stdin with {tool_name, tool_input: {file_path, ...}}
INPUT=$(cat)
TOOL=$(echo "$INPUT" | python3 -c "import json,sys;print(json.load(sys.stdin).get('tool_name',''))" 2>/dev/null)
FILE_PATH=$(echo "$INPUT" | python3 -c "import json,sys;d=json.load(sys.stdin).get('tool_input',{});print(d.get('file_path',''))" 2>/dev/null)

# Only Write / Edit / NotebookEdit
case "$TOOL" in
  Write|Edit|NotebookEdit) ;;
  *) exit 0 ;;
esac

# Only .md in Projects/*/ or Resources/
case "$FILE_PATH" in
  */Projects/*/*.md|*/Resources/*.md|*/Resources/*/*.md) ;;
  *) exit 0 ;;
esac

BASENAME=$(basename "$FILE_PATH")

# Structural names — always allowed
case "$BASENAME" in
  status.md|manual.md|tasks.md|index.md|inbox.md|log.md|claude.md|SKILL.md|README.md|_MOC.md|secrets.md)
    exit 0 ;;
esac

# .claude/ — code, not content
case "$FILE_PATH" in
  */.claude/*) exit 0 ;;
esac

# Check: does the basename contain any native-language characters?
if echo "$BASENAME" | grep -qE '[А-Яа-яЁё]'; then
  exit 0
fi

# Latin slug in a content file — warning
echo "⚠️  Cyrillic-filename-gate: '$BASENAME' looks like a Latin slug in a content file." >&2
echo "    Per vault convention, content .md filenames should use native-language words," >&2
echo "    or 'YYYY-MM-DD <phrase in native language>.md'. Exceptions: structural files," >&2
echo "    '.claude/' (code), brand/product names embedded in an otherwise-native name." >&2
echo "    Not blocking — but rename if this is unintentional." >&2

exit 0
