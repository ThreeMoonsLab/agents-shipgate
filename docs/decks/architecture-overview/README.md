# Agents Shipgate Architecture Deck

Editable presentation for explaining how Agents Shipgate works internally.

The deck is grounded in:

- `docs/architecture.md`
- `docs/trust-model.md`
- `STABILITY.md`
- `src/agents_shipgate/cli/scan.py`

## Files

- `output/output.pptx` - editable PowerPoint deck.
- `src/build.mjs` - source used to generate the deck.
- `scratch/previews/contact-sheet.png` - rendered preview contact sheet.
- `scratch/quality-report.json` - PPTX package QA report.

## Rebuild

Create or refresh the presentation workspace with the Presentations runtime, then
run the builder:

```bash
node /path/to/presentations/scripts/create_presentation_workspace.js \
  --deck-id architecture-overview \
  --workspace docs/decks/architecture-overview \
  --force
cd docs/decks/architecture-overview
node src/build.mjs
```
