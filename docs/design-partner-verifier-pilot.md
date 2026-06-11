# Design Partner Verifier Pilot

Use this runbook to get three design partners through the v0.13.0 verifier
loop on one real or sanitized AI-generated agent PR each.

## Goal

Three design partner repos provide one AI-generated agent-capability PR or
sanitized diff, run Agents Shipgate in advisory verifier mode, and share a
redacted feedback artifact.

The pilot is about the merge-verdict loop, not a generic first scan:

```text
AI-generated agent PR
  -> agents-shipgate verify
  -> verifier.json / pr-comment.md / report.json
  -> coding agent fixes only safe mechanical work
  -> human reviewer handles authority gaps
  -> feedback export becomes product and benchmark input
```

## Definition Of Running

A partner counts as running the verifier pilot when all of these are true:

- The partner supplied a real PR, sanitized patch, or representative diff from
  Codex, Claude Code, Cursor, or similar tooling.
- The PR changes an agent capability: tools, prompts, MCP/OpenAPI surfaces,
  permissions, policy, CI, `shipgate.yaml`, or another trust root.
- `shipgate.yaml` has been reviewed and has no unresolved `CHANGE_ME` values.
- The repo has advisory Shipgate CI or an equivalent local verifier run that
  produced `verifier.json`, `pr-comment.md`, and `report.json`.
- A reviewer read `verifier.json` first and used
  `report.json.release_decision.decision` as the release gate.
- `agents-shipgate-reports/` is ignored and not committed.
- The partner exported a redacted feedback artifact or provided equivalent
  structured notes.

## Lower-friction first touch

If a partner hesitates at "bring a PR," start with the zero-config host
audit instead — one read-only command, no manifest, no CI:

```bash
agents-shipgate audit --host
```

It prints the repo's current coding-agent grants (MCP servers, permission
rules with wildcard flags, hooks, workflow write scopes). Reviewing that
one page together usually surfaces the first governance question and
motivates the verifier loop.

## Partner Fit

Use the general fit criteria in [`design-partners.md`](design-partners.md).
Prioritize teams that can share actionability feedback within one week.

Good first partners usually have:

- At least one refund, email, cancellation, deployment, record-modifying,
  sensitive-read, or other authority-bearing tool.
- A coding agent already used for PR work.
- Permission to run a non-blocking GitHub Action or equivalent local verifier
  command during the pilot.
- A named reviewer who can judge whether the merge verdict and next action are
  useful.

Avoid first-wave partners that need hosted dashboards, runtime enforcement,
private-data upload, compliance certification, or non-GitHub CI as the primary
success path.

## Pilot Commands

Run these from the target repo root. The `verify` and `feedback` commands
require agents-shipgate >=0.13.0, so the block leads with `pipx install`
then `pipx upgrade`: a plain `pipx install` is a no-op when an older build
is already installed, and the follow-up `pipx upgrade` brings a stale copy
current. If `pipx` is unavailable, use
`python -m pip install -U "agents-shipgate>=0.13"` and verify with
`agents-shipgate --version`. For committed PR/CI refs, make `origin/main`
and `HEAD` available before the final verify command.

```bash
pipx install agents-shipgate
pipx upgrade agents-shipgate
agents-shipgate verify --preview --json
agents-shipgate init --workspace . --write --ci --agent-instructions=all
agents-shipgate verify --workspace . --config shipgate.yaml \
  --base origin/main --head HEAD --ci-mode advisory --format json
agents-shipgate feedback export \
  --from agents-shipgate-reports/verifier.json \
  --redact \
  --out shipgate-feedback.json
```

If the repo is not yet committed, omit `--base` and `--head` for the local
pre-commit verifier run, then rerun with base/head refs after opening the PR.

## Read Order

Read `agents-shipgate-reports/verifier.json` first:

1. `merge_verdict`
2. `can_merge_without_human`
3. `first_next_action`
4. `fix_task`
5. `capability_review.top_changes`

Then read `agents-shipgate-reports/report.json.release_decision.decision`.
`merge_verdict` is the reviewer-facing projection; `release_decision.decision`
remains the release gate.

Do not self-resolve authority gaps. If `first_next_action.actor` or
`fix_task.actor` is `human`, the coding agent must surface that item for a
person rather than inventing approval, confirmation, idempotency,
broad-scope, prohibited-action, waiver, baseline, suppression, or
policy-weakening evidence.

## Partner Agent Prompt

Paste this into the partner's coding agent from the target repo root:

```text
Add Agents Shipgate as an advisory verifier for this AI-generated
agent-capability PR.

Use the v0.13.0 verifier-first path:
1. Install or upgrade agents-shipgate (the pilot needs >=0.13.0):
   pipx install agents-shipgate
   pipx upgrade agents-shipgate
   A plain pipx install is a no-op when an older build is already installed,
   so the follow-up pipx upgrade brings a stale copy current. If pipx is
   unavailable, use python -m pip install -U "agents-shipgate>=0.13" and
   verify with agents-shipgate --version.
2. Run:
   agents-shipgate verify --preview --json
   agents-shipgate init --workspace . --write --ci --agent-instructions=all
3. Replace every CHANGE_ME value in shipgate.yaml using the agent's system
   prompt, README, main agent module, or owner-provided context.
4. Open or update the PR, make origin/main and HEAD available, then run:
   agents-shipgate verify --workspace . --config shipgate.yaml \
     --base origin/main --head HEAD --ci-mode advisory --format json
5. Read agents-shipgate-reports/verifier.json first. Lead with merge_verdict,
   can_merge_without_human, first_next_action, fix_task, and
   capability_review.top_changes. Then read
   agents-shipgate-reports/report.json.release_decision.decision.
6. Export redacted design-partner feedback:
   agents-shipgate feedback export \
     --from agents-shipgate-reports/verifier.json \
     --redact \
     --out shipgate-feedback.json
7. Ensure agents-shipgate-reports/ is ignored and not committed.

Do not enable strict CI, save a baseline, suppress findings, weaken Shipgate
policy, remove Shipgate CI, or auto-assert approval, confirmation,
idempotency, broad-scope, prohibited-action, waiver, baseline, suppression, or
runtime-trace evidence.
```

If the partner wants the smallest Codex-only install, use
`--agent-instructions=agents-md,codex-skill` instead of `all`. For Claude Code,
use `--agent-instructions=agents-md,claude-md,claude-code-skill`.

## First Call Agenda

- Confirm the PR/diff source, changed capability, framework, and tool-source
  boundary.
- Confirm GitHub Actions and PR comments are acceptable for the advisory pass.
- Run or supervise the partner-agent prompt.
- Review `shipgate.yaml`, the advisory workflow, and the first verifier
  artifacts.
- Decide which findings are mechanical fixes and which require human authority.
- Agree which redacted feedback can be shared back with Three Moons Lab.

## Success Tracker

Keep private notes outside the public repo.

Template: copy into a private tracker:

| Field | Value |
| --- | --- |
| Partner |  |
| Repo / agent type |  |
| PR or sanitized diff |  |
| Capability changed |  |
| Tool source type |  |
| Coding agent |  |
| Advisory workflow run |  |
| `verifier.json` / `pr-comment.md` / `report.json` |  |
| `merge_verdict` |  |
| `can_merge_without_human` |  |
| `first_next_action.actor` |  |
| `fix_task.actor` |  |
| `trust_root_touched` |  |
| `policy_weakened` |  |
| Top capability changes |  |
| Finding IDs |  |
| Feedback artifact |  |
| False positive or friction |  |
| Benchmark candidate decision |  |
| Follow-up date |  |

## Follow-Up Questions

Ask these after the first verifier artifact lands:

- Did the coding agent discover and run the verifier without
  command-by-command coaching?
- Did `merge_verdict` match what the human reviewer would do before merge?
- Was `first_next_action` clear enough to route work to the right actor?
- Did `fix_task` draw the right boundary between mechanical fixes and human
  authority?
- Did `capability_review.top_changes` describe the actual capability delta?
- Which finding was useful, noisy, confusing, or missing context?
- Should this PR become a benchmark scenario?

## Outreach Snippet

```text
We are looking for three design partners to try Agents Shipgate on one
AI-generated agent PR. The pilot is local-first: your coding agent installs
shipgate.yaml and advisory CI, then Agents Shipgate produces verifier.json,
pr-comment.md, report.json, and a redacted feedback artifact. No agent
execution, LLM calls, MCP connections, hosted dashboard, or telemetry are
required. In return, we ask whether the merge verdict, capability changes, and
next action are useful for your platform, security, and release reviewers.
```

## Exit Criteria

The goal is met when three tracker rows satisfy the definition of running,
each has a first-run feedback note, and at least one concrete product, docs, or
benchmark follow-up has been captured from each partner.
