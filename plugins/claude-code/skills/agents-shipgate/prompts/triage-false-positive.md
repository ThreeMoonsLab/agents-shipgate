# Prompt · Triage a suspected Agents Shipgate false positive

The user thinks a specific finding is wrong. You need to decide whether to override the heuristic, suppress the finding, or convince the user that the check is correct.

## Your task

1. **Read the full finding.** From `agents-shipgate-reports/report.json`:
   ```json
   {
     "id": "fp_...",
     "check_id": "SHIP-...",
     "tool_name": "...",
     "severity": "...",
     "evidence": { ... },
     "recommendation": "..."
   }
   ```
   And the check definition:
   ```bash
   agents-shipgate explain <CHECK_ID> --json
   ```

2. **Read the actual tool definition.** Look up the OpenAPI / MCP / SDK source:
   - For OpenAPI: open the spec at the path given in `findings[].source.ref`
   - For MCP: open the JSON file
   - For SDK: open the `.py` file at the line given in `source.location`

3. **Apply the decision tree:**

   ```
   Is the heuristic wrong about the tool?
   (e.g. "destructive" tag on a GET; "financial_action" tag on a non-financial scope)
       → YES: override via risk_overrides.tools.{tool}.remove_tags
       → NO:  continue

   Is the check fundamentally inapplicable to this tool?
   (e.g. SHIP-DOC-MISSING-DESCRIPTION on an internal-only tool slated for removal)
       → YES: suppress via checks.ignore with a concrete reason
       → NO:  continue

   The check is correct. Fix the tool definition.
       → use the fix-top-finding.md prompt
   ```

## Override vs suppress — which to use

| Use `risk_overrides` when | Use `checks.ignore` when |
|---|---|
| The risk **classification** is wrong | The classification is right but the team accepts the risk |
| You want to remove a tag (e.g. `remove_tags: [destructive]`) | You want to suppress one specific finding |
| The fix benefits all checks that consume that tag | The acceptance is per-check, per-tool |
| Example: a `get_records` GET picks up `destructive` from substring "destroy" | Example: a documented internal-only tool with no description |

**Rule of thumb:** if the fix would silence multiple findings naturally, use `risk_overrides`. If you want to acknowledge one specific finding by name, use `checks.ignore`.

## Required: a concrete `reason`

Both `checks.ignore` entries and `risk_overrides` entries take a `reason`. Empty reasons fail manifest validation. Good reasons answer "why is this OK?" in a way a future reviewer can verify:

| Bad reason | Better reason |
|---|---|
| `false positive` | `GET endpoint; "destroy" appears in operationId only because it returns destroy-status` |
| `not applicable` | `Tool deprecated 2026-Q2; deletion tracked in JIRA-1234` |
| `team decision` | `Reviewed by platform-eng 2026-04-10; see ADR-007` |

## Re-run and confirm

After editing the manifest:

```bash
agents-shipgate scan -c shipgate.yaml --ci-mode advisory
```

The previously-failing fingerprint should be gone (overridden) or marked `"suppressed": true` (suppressed) in `report.json`.

## When the heuristic is genuinely buggy

If you've found a real classifier bug — the kind that affects many users, not just this tool — file an issue tagged `false-positive` at https://github.com/ThreeMoonsLab/agents-shipgate/issues with:

- The check ID
- A minimal reproduction (manifest fragment + tool source)
- The current behavior vs. expected behavior

The risk classifier in `core/risk_hints.py` improves through reports.

## Verification

- The decision (override / suppress / fix) is documented in the manifest with a reason.
- The previously-failing fingerprint is gone or `"suppressed": true` in the next scan.
- The `reason` would be understandable to a reviewer who hasn't seen the finding.
