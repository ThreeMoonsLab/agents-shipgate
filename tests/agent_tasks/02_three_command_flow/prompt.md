# Task 02 — adopt Agents Shipgate in a single tool-using turn

You are looking at a LangChain agent project. Adopt Agents Shipgate end
to end:

1. Detect what kind of agent project this is.
2. Generate a `shipgate.yaml` and a GitHub Actions workflow.
3. Run a scan with patch suggestions.
4. Apply the safe trivial patches.

You have one turn. Use the canonical 4-call flow:

```bash
agents-shipgate detect --json
agents-shipgate init --write --ci --json
agents-shipgate scan -c shipgate.yaml --suggest-patches --format json
agents-shipgate apply-patches \
    --from agents-shipgate-reports/report.json --confidence high --apply
```

Steps 1 and 2 require no further editing — `init` produces a valid
manifest with the framework's tool sources auto-populated. Step 3
attaches patches to every active finding. Step 4 mutates only the
high-confidence patches (today: stale-manifest removals); the rest
remain as `ManualPatch` for human review.

After the flow, replace any remaining `CHANGE_ME` placeholders in
`shipgate.yaml`. For this LangChain starter `init` emits two:

- `agent.name: CHANGE_ME` — there is no `Agent(name="…")` literal in
  the source, so set this to the agent's role (e.g. `support-case-reader`).
- `agent.declared_purpose: [- CHANGE_ME]` — replace with a one-line
  description of what the agent should do.
