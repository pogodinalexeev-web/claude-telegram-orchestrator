#!/usr/bin/env bash
# draw-trigger.sh — UserPromptSubmit hook.
# Trigger: "draw", "visualize", "diagram", "infographic", "poster", "slide(s)", "dashboard" in prompt.
# Goal: before starting to draw, ask which of the 4 installed skills to use.
# Origin: owner asked "make a hook on 'draw' / 'visualize' to offer a choice from 4 skills".

set -u
INPUT=$(cat)

PROMPT=$(echo "$INPUT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('prompt',''))" 2>/dev/null)
[ -z "$PROMPT" ] && exit 0

export PROMPT
HIT=$(python3 -c "
import re, os
p = os.environ.get('PROMPT','')
pat = r'(?i)\b(draw|visualize|visualise|diagram|infographic|poster|slide[s]?|dashboard|chart|schema|mockup|wireframe)\b'
print('HIT' if re.search(pat, p) else '')
")
[ -z "$HIT" ] && exit 0

msg="DRAW TRIGGER. Owner wants a visual. Before starting, ask in one sentence which of the 4 skills to use:

1) visualize — self-contained HTML: slides, dashboards, infographics, flowcharts, timelines, one-pager. General purpose.
2) diagram-design — technical/product diagrams in HTML+SVG (architecture, flowchart, sequence, ER, swimlane, venn, pyramid, timeline). Picks up brand colors.
3) excalidraw-diagram — hand-drawn wireframe style (.excalidraw JSON). Workflow, architecture, concept.
4) canvas-design — posters/art PNG+PDF via design philosophy. Not for business infographics, for visual art.

Add 'draw-trigger' to the hook-names block.
Format of the question to owner: one line with 4 options + brief hint. Don't start drawing without an answer."

printf '%s' "$msg" | python3 -c "import json,sys; print(json.dumps({'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit', 'additionalContext': sys.stdin.read()}}, ensure_ascii=False))"

exit 0
