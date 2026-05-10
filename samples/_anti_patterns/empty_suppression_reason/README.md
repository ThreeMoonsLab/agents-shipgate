# Anti-pattern · empty suppression reason

This manifest declares a `checks.ignore` entry with `reason: ""`. Agents Shipgate rejects empty reasons because the audit trail is the entire point of the suppression mechanism.

## Expected behavior

```bash
$ agents-shipgate scan -c shipgate.yaml
Config error: Invalid shipgate.yaml:
- checks.ignore.0.reason: Value error, suppression reason is required
```

Exit code: `2` (manifest config error).

## Why this is an anti-pattern

`checks.ignore` accepts a finding as known-acceptable risk. Without a reason, a future reviewer can't tell whether the suppression was intentional, what tradeoff was accepted, or when it should be revisited. The validator forces every entry to carry a non-empty `reason` so `git blame` always lands on a sentence the team can audit.

The fix: replace `reason: ""` with something concrete that names the audit trail. Good shapes:

- `reason: "tool deprecated 2026-Q2; deletion tracked in JIRA-1234"`
- `reason: "false positive on GET endpoint; 'destroy' appears in operationId only"`
- `reason: "reviewed by platform-eng 2026-04-10; see ADR-007"`

[`prompts/triage-false-positive.md`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/prompts/triage-false-positive.md) walks the override-vs-suppress decision and includes a reason-quality bar.
