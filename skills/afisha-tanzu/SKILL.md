---
name: afisha-tanzu
description: "Build a vertical event poster in the graphic style of Andrei Tanzu — one cutout figure + ornamental framing + large text. NOT photorealism, NOT AI-generated scene: clean human figure (with recognizable face) on procedural SVG ornament. Use with `/afisha-tanzu`, words \"afisha\", \"concert poster\", \"event poster\", \"in Tanzu style\", when a vertical poster 1080×1920 with ornament is needed."
---

# Event Poster in Tanzu Style

## Style essence (what this is)

A graphic poster, not a photo collage and not an "AI-generated scene". Three pillars:

1. **One figure.** A person cut out by contour (dancer/musician) with a **recognizable real face**. Stands to the side or bottom-center, often "cropped by the frame" (hem/hand goes beyond the edge — intentional, not a bug).
2. **Ornamental framing.** Procedural pattern: mandala-suns, lotus petals, zodiac rings, arches, torana garlands, paisley, corner medallions, border frame. Drawn in code (SVG), not an image.
3. **Large text.** Serif display font for name (Playfair Display), narrow grotesque for captions and date (Oswald). Large date numerals.

Palette — 2-3 colors. Dark background + gold (indigo/burgundy + #e8b93f) OR light parchment + red-ochre.

## MAIN PRINCIPLE: multiplicity, not density of one object

> Calibration note: "pattern and their quantity — that's exactly it". Past mistake was densifying ONE mandala (120 rays instead of 60). Saturation comes from **quantity of separate objects across the canvas**, not density of one center.

Correct "×7 saturation" = not one mandala, but **3-5 mandalas** (center + side pockets + lower corners); not one corner, but **a cluster in each of 4 corners**; **row of rosette-border** along entire perimeter; **scattered** small suns/stars in open spaces.

Calibration:
- Mandalas — can feel tacky. Keep restrained: thinner line, less gold fill, more air in the mandala itself. Better several elegant ones than one fat one.
- Light concepts (parchment) on cream background muffle thin ornament — raise ornament opacity (0.3-0.6, not 0.1) or darken background.

### Density scale

Owner's preferred level is **×10** (parchment): tiled rosette grid ~92px step + dense zodiac + star scatter. Reference: `c3.html` (block "EXTREME ×10") and `render_c3_x10.png`. At this level individual rosettes still **read as objects** — that's the "multiplicity".

**Ceiling ×100** (grid step 38px, 600 stars, 360 ticks) — beyond useful: objects shrink to "spider web" and at low opacity on cream background **disappear in preview** (in full resolution visible as thin lace texture, in thumbnail — almost empty fields). Rule: saturation grows by count, but when object size < ~7px and opacity < 0.25 on light background — it's noise, not ornament. Working range — ×5…×10, not higher.

## Technical pipeline (proven)

Layout — HTML + procedural SVG, render via Playwright to PNG 1080×1920 (vertical for Stories/Telegram).

1. **Working folder:** keep html + `fig_cut.png` + renders together. Example: `Resources/attachments/_afisha_build/`.
2. **Local server is required** — `file://` in Playwright is blocked. Start in folder: `python3 -m http.server 8741`, then open `http://127.0.0.1:8741/c1.html`.
3. **Image cache** — Chromium caches `fig_cut.png`. Change version in `src="fig_cut.png?v5"` or append `?r2` to page URL on re-render.
4. Render: `browser_resize 1080×1920` → `browser_navigate` → `browser_take_screenshot fullPage`.

### Gotchas

- **Playwright writes screenshot to ROOT of vault** (`$VAULT/render_*.png`), NOT to working folder. After render — `cp` to working folder and read from there. Otherwise you'll be looking at a stale file thinking the code didn't run.
- **Reduced preview eats thin lines.** Read PNG shows image ~3x smaller — 1px gold lines on dark background disappear. Judge density by **crop in full resolution** (PIL `im.crop(...).save(...)`), not by full-poster thumbnail.
- **Figure image box overlaps background.** `.figwrap` has `filter: drop-shadow(...)` — creates an overlapping layer across the entire rectangular image area (z-index 3), even where PNG is transparent. So ornament on the BACK SVG layer in side pockets disappears under the figure. **Fix:** draw side/bottom mandalas on the FRONT SVG layer (`#ornFg`, z-index 4 — above figure). They'll sit in dark pockets beside arms/skirt and won't dirty the dancer (PNG is transparent there). Keep main mandala-nimbus on back layer behind the head.
- **Giant skirt eats center-bottom.** Visible space for ornament: dark pockets beside raised arms (above skirt), narrow strips at canvas edges, top corners, bottom triangles at hem. Place objects there, not in the center under the figure.

## Figure cutout (rembg)

Clean contour with recognizable face — model `isnet-general-use` + alpha matting. Full recipe and the "cropped hem" nuance — in `cutout.md` next to this SKILL.md. Briefly: environment `~/cloak/bin/python` (has rembg/onnxruntime), `alpha_matting=True`, foreground=240, background=15, erode_size=12, then crop by alpha bbox and feathering along side columns.

## Ready start

`template.html` alongside — working scaffold (dark "cosmos"): HTML text markup + SVG ornament library (`mandala`, `rosette`, `petalRing`, `frame`, `cornerCluster`, `smallSun`) + placement across 7 depth planes. Copy, change text/date/palette/`fig_cut.png`, render.

Three proven concepts (dark cosmos / burgundy palace / cream parchment) live as `c1/c2/c3.html` in `Resources/attachments/_afisha_build/` — use as palette and composition reference.
