# Design-Partner Re-engagement: the IE Fix Follow-up

Use this when going back to a pilot that stalled on
`insufficient_evidence` (the 2026-06-01 pilot's exact failure mode), and
as the template for new outreach. Personalize the bracketed parts; keep
it under 150 words.

## The message (email / Slack)

> Subject: The thing that blocked your Shipgate pilot is fixed
>
> Hi [name] — quick follow-up on the verifier run we did on [PR /
> repo]. It returned `insufficient_evidence` because your dynamic
> toolkit factory hid the tool surface from static analysis, and the
> verdict gave you nowhere to go. That was our bug, not yours.
>
> v0.12.0 changes this in two ways:
>
> 1. Every evidence gap now comes with a concrete next action
>    (`evidence_gaps[]` names the tool, why confidence is low, and the
>    exact manifest key to fix it), and the scan writes a ready-to-edit
>    `suggested-inventory.json` skeleton next to the report.
> 2. If a PR ever *removes or broadens* a config-bound toolkit's
>    allowlist, that's now a blocking check, not an evidence shrug.
>
> Same PR, one command, ~5 minutes: `pipx upgrade agents-shipgate &&
> agents-shipgate verify --base origin/main --head HEAD --format json`.
> Could we re-run it this week? I'll be on the call if you want.

## For new prospects: lead with the zero-config audit

No manifest, no CI change, read-only, one command:

```bash
pipx install agents-shipgate
agents-shipgate audit --host
```

One page: every MCP server, permission rule (wildcards flagged), hook,
and workflow write scope their coding agents currently hold. Reviewing
it together surfaces the first governance question; the verifier pilot
([design-partner-verifier-pilot.md](design-partner-verifier-pilot.md))
is the natural next step.

## What to capture from the re-run

Per the pilot runbook: the verifier artifacts, a redacted
`feedback export`, and specifically for re-engaged IE pilots —

- Did `evidence_gaps[].next_action` lead to a working inventory without
  human research? (time-to-green is the metric)
- Was `suggested-inventory.json` usable as-written, or did entries need
  manual rework? (skeleton quality)
- Did the final verdict change category (IE → review_required/blocked)?

These three answers feed directly into the v0.13 priorities.
