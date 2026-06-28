#!/usr/bin/env bash
# double-simple-check.sh — Stop hook.
# Double-check simple-language: after Claude's response, parses the last
# assistant message and looks for English terms from glossary dictionaries
# Resources/glossaries/{tech-jargon.md,marketing-terms.md} WITHOUT a native
# translation nearby (format "native (english)" within a ±80 char window).
# If found — blocks, Claude rewrites.
# Added after owner observation: "the gate fires in the hook name but not in practice".

set -u
INPUT=$(cat)

TRANSCRIPT=$(echo "$INPUT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('transcript_path',''))" 2>/dev/null)
[ -z "$TRANSCRIPT" ] && exit 0
[ ! -f "$TRANSCRIPT" ] && exit 0

VAULT="${CLAUDE_PROJECT_DIR:-$HOME/vault}"
GLOSS1="$VAULT/Resources/glossaries/tech-jargon.md"
GLOSS2="$VAULT/Resources/glossaries/marketing-terms.md"

python3 - "$TRANSCRIPT" "$GLOSS1" "$GLOSS2" <<'PY'
import json, sys, re

transcript_path, gloss1, gloss2 = sys.argv[1], sys.argv[2], sys.argv[3]

# --- Load terms from glossaries (format | **term** |) ---
TERM_RE = re.compile(r'\|\s*\*\*([^*]+)\*\*\s*\|')
terms = set()
for path in (gloss1, gloss2):
    try:
        for line in open(path, encoding='utf-8', errors='replace'):
            for m in TERM_RE.finditer(line):
                t = m.group(1).strip()
                # Only English terms (contain Latin characters)
                if re.search(r'[A-Za-z]', t):
                    terms.add(t.lower())
    except Exception:
        pass

# Whitelist exceptions (models/products/widely known terms) — don't flag
WHITELIST = {
    'claude', 'opus', 'sonnet', 'haiku', 'telegram', 'anthropic',
    'max', 'vk', 'docker', 'linux', 'mac', 'iphone', 'android',
    'http', 'https', 'json', 'ssh', 'git', 'github', 'wifi',
    'youtube', 'instagram', 'whatsapp', 'pdf', 'url',
}

# Dynamically add hook and slash-command names (and their dash sub-tokens)
import os, glob
VAULT_ROOT = os.environ.get("CLAUDE_PROJECT_DIR", os.path.expanduser("~/vault"))
for pattern in (f'{VAULT_ROOT}/.claude/hooks/*.sh', f'{VAULT_ROOT}/.claude/commands/*.md'):
    for path in glob.glob(pattern):
        name = os.path.splitext(os.path.basename(path))[0].lower()
        WHITELIST.add(name)
        for sub in name.split('-'):
            if len(sub) >= 2:
                WHITELIST.add(sub)
terms = {t for t in terms if t not in WHITELIST and len(t) >= 2}

if not terms:
    sys.exit(0)

# --- Get the last assistant message ---
try:
    lines = open(transcript_path, encoding='utf-8', errors='replace').readlines()
except Exception:
    sys.exit(0)

last_text = ""
for line in reversed(lines):
    try:
        m = json.loads(line)
    except Exception:
        continue
    if m.get("type") != "assistant":
        continue
    content = m.get("message", {}).get("content", [])
    if isinstance(content, list):
        text_parts = [c.get("text","") for c in content if isinstance(c,dict) and c.get("type")=="text"]
        text = "\n".join(text_parts).strip()
    else:
        text = str(content).strip()
    if text:
        last_text = text
        break

if not last_text:
    sys.exit(0)

# --- Clean: remove code blocks, inline code, md links ---
clean = re.sub(r'```.*?```', '', last_text, flags=re.DOTALL)
clean = re.sub(r'`[^`]*`', '', clean)
clean = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', clean)  # links → text
# Remove filenames (contain / or .extension)
clean = re.sub(r'\S+\.(md|sh|py|json|txt|js|ts|yaml|yml)', '', clean)
clean = re.sub(r'[A-Za-z0-9_./-]+/[A-Za-z0-9_./-]+', '', clean)
# Remove hyphenated English identifiers (like hook names) — these are identifiers, not prose
clean = re.sub(r'\b[a-z]+(?:-[a-z0-9]+)+\b', '', clean, flags=re.IGNORECASE)

# Remove English quotes if ≥70% Latin — direct citation from an English source, not prose.
def _strip_eng_quote(m):
    inner = m.group(0)
    letters = [c for c in inner if c.isalpha()]
    if not letters:
        return inner
    latin_ratio = sum(1 for c in letters if c.isascii()) / len(letters)
    return '' if latin_ratio >= 0.7 else inner
clean = re.sub(r'«[^»]*»', _strip_eng_quote, clean)
clean = re.sub(r'"[^"]*"', _strip_eng_quote, clean)
clean = re.sub(r'"[^"]*"', _strip_eng_quote, clean)
clean = re.sub(r''[^']*'', _strip_eng_quote, clean)
clean = re.sub(r"'[^']*'", _strip_eng_quote, clean)

low = clean.lower()
orig = last_text

# --- For each term: present in text, and is there a translation in the ±80 window? ---
WINDOW = 80
SNIPPET = 60
violations = []

for term in terms:
    pattern = r'(?<![A-Za-z0-9])' + re.escape(term) + r'(?![A-Za-z0-9])'
    matches = list(re.finditer(pattern, low))
    if not matches:
        continue
    # If at least ONE occurrence has correct surrounding context — term is "already introduced"
    any_ok = False
    first_violation_match = None
    for m in matches:
        start, end = m.start(), m.end()
        window_start = max(0, start - WINDOW)
        window_end = min(len(low), end + WINDOW)
        window = low[window_start:window_end]

        has_paren_with_term = bool(re.search(r'\(\s*' + re.escape(term) + r'[^)]*\)', window))
        has_term_then_cyr_paren = bool(re.search(re.escape(term) + r'\s*\([^)]*[а-яё]', window))
        has_dash_cyr = bool(re.search(re.escape(term) + r'\s*[—–-]\s*[а-яё]', window))
        has_cyr_then_term = bool(re.search(r'[а-яё]+\s+' + re.escape(term) + r'\b', window))

        if has_paren_with_term or has_term_then_cyr_paren or has_dash_cyr or has_cyr_then_term:
            any_ok = True
            break
        if first_violation_match is None:
            first_violation_match = m

    if not any_ok and first_violation_match is not None:
        snippet = ""
        orig_search_start = first_violation_match.start()
        orig_match = None
        for om in re.finditer(pattern, orig, flags=re.IGNORECASE):
            if om.start() >= orig_search_start - 20:
                orig_match = om
                break
        if orig_match is None:
            orig_match = re.search(pattern, orig, flags=re.IGNORECASE)
        if orig_match:
            s = max(0, orig_match.start() - SNIPPET)
            e = min(len(orig), orig_match.end() + SNIPPET)
            raw = orig[s:e].replace('\n', ' ').strip()
            ts, te = orig_match.start() - s, orig_match.end() - s
            snippet = raw[:ts] + '>>>' + raw[ts:te] + '<<<' + raw[te:]
            snippet = re.sub(r'\s+', ' ', snippet)
            if s > 0:
                snippet = '...' + snippet
            if e < len(orig):
                snippet = snippet + '...'
        violations.append((term, snippet))

if not violations:
    sys.exit(0)

violation_lines = []
for term, snippet in sorted(set(violations), key=lambda x: x[0]):
    if snippet:
        violation_lines.append(f"  • '{term}' in context: {snippet}")
    else:
        violation_lines.append(f"  • '{term}' (context not extracted)")

reason = (
    "BLOCK double-simple-check: the response uses English terms "
    "from the glossary WITHOUT a native-language translation nearby "
    "(format 'native translation (english)' or 'english (native explanation)' "
    "within a ±80 character window).\n\n"
    "Specific locations:\n" + "\n".join(violation_lines) + "\n\n"
    "Rewrite EXACTLY these fragments: either translate the term (with English in parentheses), "
    "replace with a native equivalent, or remove/rephrase the citation. "
    "Don't do a global search-replace — fix each item in the list precisely. "
    "Glossaries: Resources/glossaries/tech-jargon.md, marketing-terms.md."
)
print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
sys.exit(0)
PY
