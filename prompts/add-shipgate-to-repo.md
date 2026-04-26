# Prompt · Add Agents Shipgate to a repo

You are working in a repo that contains an AI agent — likely one of: an MCP server tool list, a Python file with `@function_tool` decorators (OpenAI Agents SDK), a Google ADK agent in `agent.py`, or an OpenAPI spec the agent calls.

## Your task

1. **Install the tool:**
   ```bash
   pipx install agents-shipgate
   ```
   If `pipx` is unavailable, use `python -m pip install agents-shipgate` and verify with `agents-shipgate --version`.

2. **Sanity-check the install** before touching the user's code:
   ```bash
   agents-shipgate self-check --json
   ```
   Confirm `"ready": true`. If not, surface the failure to the user.

3. **Generate a starter manifest:**
   ```bash
   agents-shipgate init --workspace . --write --json
   ```
   The `--json` form returns a structured response including a `placeholders` array. Each placeholder has a `path` (a YAML pointer-ish location) and `current` (the literal value, typically `CHANGE_ME`).

4. **Replace placeholders.** Read the agent's prompt or main file, then edit `shipgate.yaml`:
   - `agent.name`: the actual agent name from the codebase
   - `agent.declared_purpose`: 1-2 short bullet points describing what the agent is allowed to do (read the system prompt or main agent definition for this)

5. **Run the scan:**
   ```bash
   agents-shipgate scan -c shipgate.yaml --ci-mode advisory
   ```

6. **Read the JSON report** at `agents-shipgate-reports/report.json` (not the markdown — the JSON is the stable contract). Stable fields:
   - `summary.{critical_count, high_count, medium_count, status}`
   - `findings[].{check_id, severity, tool_name, recommendation}`

7. **Add `agents-shipgate-reports/` to `.gitignore`** if it isn't already. The reports are scan artifacts, not source.

8. **Report back to the user**:
   - The status (`release_blockers_detected`, `warnings_detected`, etc.)
   - The top 3 findings by severity (use the JSON, not stdout)
   - Any check IDs the user should investigate first (use `agents-shipgate explain <CHECK_ID> --json` for details)

## What to do if the scan errors out

Set `AGENTS_SHIPGATE_AGENT_MODE=1` and re-run. The CLI will append a JSON line to stderr with `{error, message, next_action}`. Follow the `next_action`.

Common errors and fixes:

| Error | Fix |
|---|---|
| `Config file not found: shipgate.yaml` | Run `agents-shipgate init --workspace . --write` first |
| `Input path '...' resolves outside manifest directory` | The declared `tool_sources[].path` is outside the manifest dir. Move the spec inside the tree, symlink it, or copy it |
| `Invalid shipgate.yaml: ... Did you mean X?` | A field is at the wrong nesting level; move it as suggested |

## What NOT to do

- Do **not** commit `agents-shipgate-reports/` — it's regenerated each run.
- Do **not** run `agents-shipgate baseline save` until the user has reviewed the initial findings. Baselining ratchets in noise that strict CI will silently ignore. The right time to baseline is **after** the user has decided which findings they accept.
- Do **not** suppress findings without a real `reason` — the manifest validator rejects empty reasons, and the `reason` field is the audit trail when someone asks "why is this OK?"
- Do **not** use `risk_overrides.tools.{tool}.remove_tags` to silence a finding without checking whether the heuristic is actually wrong. Prefer `checks.ignore` with a reason.

## Verification before reporting success

- `agents-shipgate-reports/report.json` exists
- `agents-shipgate-reports/report.json` parses as JSON
- `shipgate.yaml` has no `CHANGE_ME` strings
- `.gitignore` contains `agents-shipgate-reports/` (or equivalent)
- The user knows the top 3 findings and at least one suggested next step
