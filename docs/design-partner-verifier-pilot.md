# Design Partner Verifier Pilot

Use this runbook to get three design partners through the verifier loop on one
real or sanitized AI-generated agent PR each. It teaches the **current**
verifier loop, which is ahead of the newest published release — settle
[which build a partner is on](#which-build-this-runbook-is-for) before you
quote a command at them.

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

## Which build this runbook is for

The read order, the tracker and the agent prompt below all name `control.state`
and `control.next_action.actor`. Those come from the agent-control envelope,
which landed **after** the newest published tag — so this runbook is not
runnable end to end on the released build, and saying so is part of the
instructions rather than a footnote:

| Channel | How a partner gets it | Runtime contract | Emits `control.*`? | Qualification |
| --- | --- | --- | --- | --- |
| Published release `v0.15.0` | `pipx install agents-shipgate` | 10 | **No.** `agent-handoff.json` is `shipgate.agent_handoff/v1`; its nearest field is `controller`, and there is no `control.state` | Qualified release |
| Unqualified preview | `gh release download preview-<version> --repo ThreeMoonsLab/agents-shipgate --pattern '*.whl'`, then `pip install ./<wheel>` | that of the source commit it was cut from | Yes | **None.** No adjudicated corpus, no qualification artifact, nothing signed — see [`release-evidence-policy-decision.md`](release-evidence-policy-decision.md) § Amendment 2 |
| Source checkout | `git clone`, then `./shipgate …` from the checkout | that of the checkout | Yes | Not a distributed build |

Pick one channel per partner and stay on it. The released build against this
runbook's read order produces a partner asking where `control.state` went;
a preview quoted without its qualification status sells an evaluation build as
a release. `agents-shipgate contract --json` prints the `contract_version` the
installed build actually implements — run it first and record it in the
tracker, rather than assuming the number this document was written against.

## Definition Of Running

A partner counts as running the verifier pilot when all of these are true:

- The partner supplied a real PR, sanitized patch, or representative diff from
  Codex, Claude Code, Cursor, or similar tooling.
- The PR changes an agent capability: tools, prompts, MCP/OpenAPI surfaces,
  permissions, policy, CI, `shipgate.yaml`, or another trust root.
- `shipgate.yaml` has been reviewed and has no unresolved `CHANGE_ME` values,
  and every human-owned one was filled by a person. `init` routes those itself
  — it returns `control.next_action.actor: "human"` and names the fields and
  lines — because a purpose, effect, authority or binding claim a coding agent
  supplied is a declaration nobody made, and the pilot is measuring exactly the
  gate that would have caught it.
- The repo has advisory Shipgate CI or an equivalent local verifier run that
  produced `agent-handoff.json`, `verifier.json`, `pr-comment.md`, and
  `report.json`.
- A reviewer read `agent-handoff.json` first and used
  `report.json.release_decision.decision` as the release gate.
- `agents-shipgate-reports/` is ignored and not committed.
- The partner exported a redacted feedback artifact or provided equivalent
  structured notes.

## Lower-friction first touch

If a partner hesitates at "bring a PR," start with the zero-config host
audit instead — one read-only command, no manifest, no CI:

```bash
shipgate audit --host
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

Run these from the target repo root, on the channel you settled above.

`verify` and `feedback export` are present in every channel, including the
released build. The runbook's **read order** is what needs the newer contract:
`control.state` requires the agent-control envelope, which the released
`v0.15.0` (contract 10) does not emit. So either take a partner down the
preview or source-checkout route, or run the released build and read
`controller` / `gate` instead — do not quote a contract floor the build you
just told them to install cannot reach.

On the released channel the block leads with `pipx install` then
`pipx upgrade`: a plain `pipx install` is a no-op when an older build is
already installed, and the follow-up `pipx upgrade` brings a stale copy
current. If `pipx` is unavailable, use
`python -m pip install -U "agents-shipgate>=0.15"` and confirm the build with
`agents-shipgate --version` and `agents-shipgate contract --json`. For
committed PR/CI refs, make `origin/main` and `HEAD` available before the final
verify command.

```bash
pipx install agents-shipgate
pipx upgrade agents-shipgate
agents-shipgate contract --json      # record contract_version in the tracker
agents-shipgate verify --preview --json
agents-shipgate init --workspace . --write --ci --agent-instructions=default --json
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

Read `agents-shipgate-reports/agent-handoff.json` first. This order needs a
build that emits the agent-control envelope (preview or source checkout); on
the released `v0.15.0` build start at step 2 and read `controller` in place of
`control`:

1. `control.state`
2. `gate.can_merge_without_human`
3. `gate.merge_verdict`
4. `next_action` / `fix_task`
5. `capability_review.top_changes`

Then read `agents-shipgate-reports/report.json.release_decision.decision`.
`merge_verdict` is the reviewer-facing projection; `release_decision.decision`
remains the release gate.

Do not self-resolve authority gaps. If `control.next_action.actor` or
`fix_task.actor` is `human`, the coding agent must surface that item for a
person rather than inventing action effect, action authority, approval,
confirmation, idempotency, broad-scope, prohibited-action, waiver, baseline,
suppression, or policy-weakening evidence.

## Partner Agent Prompt

Paste this into the partner's coding agent from the target repo root, with the
install line for the channel you settled above — the block below carries the
released one. Steps 3 and 5 name the agent-control envelope; on the released
build there is none, so the agent reads `controller` and `gate` instead. The
ownership boundary in step 3 is the rule either way: on a build with the
envelope the tool enforces it, and on one without it, nothing but the
instruction does.

```text
Add Agents Shipgate as an advisory verifier for this AI-generated
agent-capability PR.

Use the verifier-first path:
1. Install or upgrade agents-shipgate:
   pipx install agents-shipgate
   pipx upgrade agents-shipgate
   A plain pipx install is a no-op when an older build is already installed,
   so the follow-up pipx upgrade brings a stale copy current. If pipx is
   unavailable, use python -m pip install -U "agents-shipgate>=0.15".
   Then report what you actually installed, and stop if either command fails:
   agents-shipgate --version
   agents-shipgate contract --json
2. Run:
   agents-shipgate verify --preview --json
   agents-shipgate init --workspace . --write --ci --agent-instructions=default --json
3. Resolve only the placeholders you own. init lists each one in placeholders[]
   by manifest path and line. Fill the agent-owned ones (agent.name, tool source
   paths) from the repository. Do not fill agent.declared_purpose,
   prohibited_actions, permissions, action_surface, agent_bindings,
   tool_identity or any other declaration field from the system prompt, README
   or main module — those must be supplied by a human, and a value a coding
   agent supplied is a declaration nobody made. Report each one to the
   repository owner by manifest path and line, and stop there. On a build that
   emits the agent-control envelope, init says the same thing itself: while a
   human-owned placeholder is unresolved the payload comes back with
   control.next_action.actor "human", control_state "human_review_required" and
   permissions.edit false, and control.reason names the exact fields and lines.
4. Open or update the PR, make origin/main and HEAD available, then run:
   agents-shipgate verify --workspace . --config shipgate.yaml \
     --base origin/main --head HEAD --ci-mode advisory --format json
5. Read agents-shipgate-reports/agent-handoff.json first. Lead with
   control.state, gate.merge_verdict, gate.can_merge_without_human, next_action,
   fix_task, and capability_review.top_changes. If the handoff carries no
   control block, say so and lead with gate and controller instead — do not
   report a state the artifact does not contain. Then read
   agents-shipgate-reports/report.json.release_decision.decision.
6. Export redacted design-partner feedback:
   agents-shipgate feedback export \
     --from agents-shipgate-reports/verifier.json \
     --redact \
     --out shipgate-feedback.json
7. Ensure agents-shipgate-reports/ is ignored and not committed.

Do not enable strict CI, save a baseline, suppress findings, weaken Shipgate
policy, remove Shipgate CI, or auto-assert action effect, action authority,
approval, confirmation, idempotency, broad-scope, prohibited-action, waiver,
baseline, suppression, or runtime-trace evidence.
```

If the partner wants the Codex skill bundle, use
`--agent-instructions=agents-md,codex-skill`. For Claude Code skill bundles,
use `--agent-instructions=agents-md,claude-command,claude-code-skill`.

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
| `control.next_action.actor` |  |
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
- Was `control.next_action` clear enough to route work to the right actor?
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
