# Direct OpenAI API Use Case Deck

Shareable deck for future users who call the OpenAI API directly with function
tools instead of MCP, OpenAPI, or SDK metadata.

The story uses `samples/simple_openai_api_agent` as a realistic fixture, not a
customer case study. It follows four questions future users usually ask:

1. What did the original direct API prompt say?
2. What problems did Agents Shipgate find?
3. Why are those problems release-significant?
4. How do we connect Agents Shipgate to a direct OpenAI API app?

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
  --deck-id openai-api-use-case \
  --workspace docs/decks/openai-api-use-case \
  --force
cd docs/decks/openai-api-use-case
node src/build.mjs
```
