# Slide 8 — three options to compare

This folder holds three drafts of Slide 8 (Phase 1 — Static Release-Readiness Scanner).
All three use the same real artifact: `samples/support_refund_agent/expected/report.md`,
which is the CI-baseline output of `agents-shipgate scan` on the canonical
support-refund-agent fixture (Stripe refund + Shopify cancel + Gmail send + Zendesk + KB +
wildcard MCP exposure). Findings shown are the actual check IDs the scanner emits.

Pick one. Delete the others.

## V1 — Raw report rendering · `v1-raw-report.png`

The actual `report.md` rendered as a PNG, framed by the slide chrome. Brand cream
background, navy text, monospace report card with a soft fade at the bottom signaling
"more findings below."

**Tone:** honest, OSS-scanner, "this is the artifact, not a mockup."
**Strength:** maximum credibility — every finding ID, evidence string, recommendation
is exactly what the scanner outputs. No design liberties.
**Weakness:** dense; the audience has to read monospace text from slide distance.
**Use when:** the viewer is technical and you want to ground the deck in real output
without product polish.

## V2 — Designed product-UI summary · `v2-product-ui.png`

A clean dashboard tile mock with verdict banner, severity counts grid, and 4 top
findings as styled rows. Real check IDs preserved. Tool-surface summary as pill chips.

**Tone:** "this could be a real product surface."
**Strength:** scannable from across a room; the verdict + counts read first, the
findings second, the inventory third — clear visual hierarchy. Strong slide energy.
**Weakness:** it's a designed mockup, not the actual output. Some viewers may discount
that you "designed it to look good."
**Use when:** the viewer is a VC/operator who wants to see a product story, not a
terminal capture; or when you want to set up Phase 2/3 visually consistent with this.

## V3 — Animated terminal GIF · `v3-terminal-scan.gif`

A ~9-second animated terminal showing the command being typed, then the actual
output streaming in: status, severity counts, top findings table, reports written,
exit code 20. Brand-matched cream slide chrome around a dark terminal panel.

**Tone:** live demo without the latency.
**Strength:** the only option that *moves*; the eye locks onto the BLOCKED moment
and the cascade of CRITICAL findings. Memorable in a way static slides aren't.
**Weakness:** depends on the deck format — must be a context that plays GIFs
(Keynote/PowerPoint export, web, Loom, Figma); does not work in static PDF.
**Use when:** the deck is being shared digitally or screen-shared, and the viewer
will sit through 9 seconds of motion.

## My read

If forced to pick one for tomorrow's Prateek meeting, I'd lead with **V2** for the
slide-projected version (it reads fastest and feels most "company") and keep **V3**
in the back pocket as a Loom/follow-up artifact. **V1** has the strongest
intellectual-honesty signal but loses to V2 on legibility at slide distance.

But you said "ship all three and decide later," so all three are here.

## Files

- `v1-raw-report.png` (1920×1080 @2x)
- `v2-product-ui.png` (1920×1080 @2x)
- `v3-terminal-scan.gif` (1280×720, ~9s, 53 frames, ~2.6 MB)
- `build_v1.py`, `build_v2.py`, `build_v3.py` — generators (re-runnable)
- `v1-raw-report.html`, `v2-product-ui.html` — generated intermediate HTML

The `_v3_*_check.png` files are sandbox-side preview stills used to debug the GIF
before the final render; they're sandbox-owned and didn't delete cleanly. Safe to
ignore or remove manually.

## Re-run

```bash
cd docs/decks/vc-thesis/slide-08-options
python3 build_v1.py
python3 build_v2.py
python3 build_v3.py
```

Requires Python 3, `playwright` + `chromium`, `markdown`, and `Pillow`.
