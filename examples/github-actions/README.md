# GitHub Actions examples

Copy-paste-ready workflows. Each one is a complete file — drop it into `.github/workflows/`. Recipes 01–05, 07–11 and 13 expect `shipgate.yaml` at the repository root; recipe 12 needs a committed host-grant baseline instead; recipe 14 needs neither.

**No `shipgate.yaml`?** If the repository's pull requests change coding-agent host configuration — `.claude/settings.json`, `.mcp.json`, Codex, Cursor or VS Code MCP configuration — start with [`14-host-only-advisory-pr.yml`](14-host-only-advisory-pr.yml). It is the team version of a local `agents-shipgate diff`; see [Host-only advisory PR review](#host-only-advisory-pr-review).

| File | When to use |
|---|---|
| [`01-advisory-pr-comment.yml`](01-advisory-pr-comment.yml) | First time you're adding the gate. Comments on PRs but never blocks. **Recommended starting point.** |
| [`02-strict-on-critical.yml`](02-strict-on-critical.yml) | After your team has tuned suppressions and is ready to fail PRs on new criticals. |
| [`03-strict-with-baseline.yml`](03-strict-with-baseline.yml) | When you have existing findings and want to fail only on net-new ones. |
| [`04-multi-config-workspace.yml`](04-multi-config-workspace.yml) | Monorepo with several agents (each with its own `shipgate.yaml`). |
| [`05-sarif-to-code-scanning.yml`](05-sarif-to-code-scanning.yml) | Surface findings in GitHub's Security tab and as PR annotations. |
| [`07-block-on-blocked-verdict.yml`](07-block-on-blocked-verdict.yml) | Intermediate verifier policy: allow human-review PRs, but fail blocked verdicts. |
| [`08-require-mergeable.yml`](08-require-mergeable.yml) | Strict verifier policy: fail unless no human authority gap remains. |
| [`09-risk-labels-and-reviewers.yml`](09-risk-labels-and-reviewers.yml) | Label PRs by risk signal (`agent-capability-change`, `trust-root-touched`, `shipgate-blocked`) and request boundary owners as reviewers. |
| [`10-check-run-annotations.yml`](10-check-run-annotations.yml) | Native Check Run with merge-relevant line annotations; branch protection can require the "Agents Shipgate" check directly. Needs `checks: write`. |
| [`11-fail-on-insufficient-evidence.yml`](11-fail-on-insufficient-evidence.yml) | Evidence policy: fail when static evidence is too weak to gate confidently. |
| [`12-host-grant-drift.yml`](12-host-grant-drift.yml) | Scheduled drift gate: fail when current coding-agent host grants (MCP servers, permission rules, hooks, workflow scopes) no longer match the acknowledged `.agents-shipgate/host-grants.json` baseline. Catches authority changes that land outside PR review. |
| [`13-org-governance.yml`](13-org-governance.yml) | Scheduled organization governance gate: exception hygiene, policy-pack pinning, and host-grant drift. Does not create a second release verdict. |
| [`14-host-only-advisory-pr.yml`](14-host-only-advisory-pr.yml) | No `shipgate.yaml` and no saved baseline. Compares each PR's coding-agent host configuration with its base branch, comments, and never blocks. **Recommended starting point for host-only repositories**, after a local `agents-shipgate diff` was useful. |

> **Retired:** the `on-tool-source-changes` recipe was removed. A change-prefilter cannot gate Shipgate safely. `TRIGGER-EXISTING-MANIFEST-PRESENT` is `force_run`, so an adopted repo (one with `shipgate.yaml`) is contracted to run on **every** PR — the prefilter was not saving the scan it claimed to save. Worse, every prefilter language here matches paths case-sensitively while the trigger catalog does not, so an allowlist silently drops governance edits such as `services/foo/Policies/refund.yaml` — with no job, no check, and no signal. Run the advisory recipe on every PR and let the in-job trigger evaluator decide.

## Host-only advisory PR review

[`14-host-only-advisory-pr.yml`](14-host-only-advisory-pr.yml) runs the comparison a local `agents-shipgate diff` makes — the PR against its base branch in Git history — on every pull request, with no `shipgate.yaml` and no saved baseline. It needs no inputs beyond the advisory recipe's: with no manifest present, the Action's `verify` takes its manifest-free host route.

Each run leaves one comment, which later pushes update in place rather than adding new ones. Its human summary opens with one of:

- **Changes** — `Repository-declared host capability changes:`, then one entry per changed grant with before → after and why it matters. After `1.0.0` (not in the pinned release yet), a widening entry is marked `⚠`, a permission rule names its disposition, a replaced or moved rule is one `widened`, `narrowed` or `moved` entry, an MCP server names its published command name (not its path) or redacted URL and key names, and the entries end with `Review question: Does the team intend …?`, a `Compared:` line naming the base and head commits and the version, and a `Reproduce:` line with the `agents-shipgate diff --base <sha>` to run after checking out the head. Both lines also end a comment with no change. A comment whose comparison was unavailable ends with the same two lines, the first labelled `Inputs:` rather than `Compared:`, since that run compared nothing — but only where that run named a base commit: `shallow_history` and an unfetched base name none, so those comments carry neither line, as they carry no block. Neither asks a question. On `pull_request` that head is the commit the Action compared, `github.sha`: GitHub's merge commit for the PR, not the branch tip. No branch holds it, so fetch it first with `git fetch origin refs/pull/<number>/merge`, which serves it only until a later push to the PR or its base branch replaces it.
- **No change** — `Repository-declared host capability changes:`, then `No static host-grant changes detected in the covered comparison. No verdict is implied.`
- Either of those can add **Not compared** — `Not compared: unchanged in this change and not read, so no claim is made about them:` and the sources it skipped, such as an unparseable `.cursor/mcp.json` the PR did not touch. Nothing is claimed about those.
- **Cannot compare** — `Host capability comparison unavailable:` with the reason, such as `head_inventory_incomplete` for a configuration file the PR leaves unreadable, or `shallow_history` for a clone without the base branch's history. That is an input limit, not a finding and not a pass.
- After `1.0.0` (not in the pinned release yet), an answer from a comparison that read both sides — every one above but `shallow_history` — is followed by **What this run established**: the sources compared and how many rows each gave, a source that changed with no compared grant changing (so an `env` or `apiKeyHelper` edit to `.claude/settings.json`, key or value, no longer looks like no change; a source is listed as unchanged only when its bytes are proven identical), any other changed source no row is attributed to, such as a plugin manifest's `hooks` reference, a source only the base or the head read or published, such as a deleted file or an untracked `.claude/settings.local.json`, and for an unavailable comparison each source that left an inventory incomplete, with its kind. It lists only sources the comparison already read or tried to read, and its first line says so, so it is never read as the whole account of the change; a changed file this entry does not read is absent from it. Blocking limits come first, most actionable kind first, so a cap never hides an unreadable source behind routine ones, and what it leaves out is counted as ranking below what it lists. In the comment it lists only what fits in the room the entries and the lines after it leave, at most 2000 characters, and counts the rest, so it never pushes those lines out.

Next comes `Advisory: no application release policy configured. This comparison grants no merge authority.`, then — when the comparison was unavailable — the next actor, action and command, and an `Evidence:` line naming `verifier.json`. An agent instruction block follows the summary. `verifier.json`, `agent-handoff.json` and `pr-comment.md` are in the `agents-shipgate-report` artifact, and `verifier.json`'s `host_comparison` carries the same `rows`, `comparison_status`, `unchanged_limits`, `incomparable_reasons` and, after `1.0.0`, `coverage` as `agents-shipgate diff --json` on the same refs. One `coverage` status can differ on a local checkout whose Git conversion writes a host file with `CRLF` line endings (`eol=crlf`, or `core.autocrlf=true` as Git for Windows sets it): `diff` against that working tree cannot prove the unchanged file's bytes identical and reads `unchanged_not_proven`, while the Action's `verify --head <sha>` reads both sides from commits and reads `compared` (see [Read the answer](../../docs/quickstart.md#3-read-the-answer)).

**What fails the job.** Nothing the comparison finds, and not missing history: a shallow clone or an unfetched base branch shows in the comment as `Host capability comparison unavailable`. The job fails on setup and execution errors, which are not review results. For example:

- a setup step fails — `actions/setup-python`, the install step (for example when PyPI cannot be reached), or the artifact upload;
- the CLI exits non-zero, which the Action applies as the job's exit code — for example when a PR adds an invalid `shipgate.yaml` at the root, which switches off the host-only route, and `verify` exits 2;
- posting the comment hits a GitHub API error other than a missing permission (403 or 404), including rate limiting;
- the job exceeds `timeout-minutes: 10`, or a newer push cancels it (see *One run per PR*).

The `merge_verdict` and `agent_control_state` outputs are not a pass/fail signal for this recipe: with no application release policy, a docs-only PR still reports `unknown` and `agent_action_required`.

- **Permissions.** `contents: read` checks out the repository; `pull-requests: write` is only for the comment. Without it — including on every PR from a fork, which `pull_request` gives a read-only token — the comment step writes the same review to the job summary and says publication was unavailable. Do not switch to `pull_request_target` to reach forks: it runs with a write token against untrusted PR contents.
- **History.** Keep `fetch-depth: 0`. With `diff_base: target` the Action compares against `origin/<the PR's base branch>`, so a PR into `develop` is compared with `develop`. The Action never fetches.
- **One run per PR.** The `concurrency` group runs one job per pull request and cancels the older run when a new push arrives; the newer run waits until the cancelled one has finished, so two quick pushes cannot both create a comment and the newer push's result is written last. The Action's reporting steps run with `if: always()`, so a cancelled run may still upload an artifact or update the comment from a partial or missing report before the newer run replaces it. Manually re-running an older run writes that older result again.
- **What runs.** `shipgate_version: '1.0.0'` installs `agents-shipgate==1.0.0` from PyPI; pip resolves that package's dependencies within their declared ranges when the job runs, so they are not frozen. The Action's steps come from the `v1.0.0` tag, which can be moved. For an immutable ref, replace `v1.0.0` in the `uses:` line with the commit it names, `bace7c1871834e0b3eb98e6f60c0627725c53a59`, and keep `# v1.0.0` as a trailing comment; that commit is what `agents-shipgate init --ci` from the 1.0.0 release writes. No agent, tool or MCP server is started.
- **Whose job this is.** On `pull_request`, GitHub runs the workflow file from the PR's merge commit, for fork PRs too (by default, a first-time contributor's run waits for approval). The PR can therefore change this workflow, and the job's output is always advisory output of a job the PR controls — never an independent check on the PR.
- **Known issue in the `v1.0.0` Action.** Its install and merge-verdict steps start Python with the checkout on `sys.path` (`python -m pip`, `python -`), so a PR that adds a `pip/` or `agents_shipgate/` package runs its own code even when the workflow file is left unchanged. The next release's Action starts them with `python -P`, which closes that import route — it matters most where the workflow itself is trusted, such as `pull_request_target`, `workflow_run` or a required workflow — but does not make a `pull_request` job's result independent of the PR.
- **Not a gate.** It adds no required check, failure policy or branch protection. Making a result blocking is a separate, explicit choice (recipes 07, 08 and 10).

A GitHub-hosted run of this recipe on a real pull request is still outstanding; see [#780](https://github.com/ThreeMoonsLab/agents-shipgate/issues/780) and [#570](https://github.com/ThreeMoonsLab/agents-shipgate/issues/570).

## Permissions

Most examples need:

```yaml
permissions:
  contents: read
  pull-requests: write       # for pr_comment
  security-events: write     # for SARIF upload
  checks: write              # for check_run
```

Configure per-job, never repo-wide.

## Pinning versions

For reproducible CI, pin both the action and the underlying CLI:

```yaml
- uses: ThreeMoonsLab/agents-shipgate@v1.0.0
  with:
    shipgate_version: "1.0.0"
```

When `shipgate_version` is empty the action installs the CLI from the action source — convenient for local action development, less reproducible for CI.

`shipgate_version` pins the `agents-shipgate` package only; pip resolves its dependencies when the job runs. A tag such as `v1.0.0` can be moved, so the hardened form replaces it with the commit it names and keeps the tag as a comment — see *What runs* under [Host-only advisory PR review](#host-only-advisory-pr-review).

## Action outputs

**Prefer for new release gates (v0.8+):**

| Output | Purpose |
|---|---|
| `decision` | `blocked` / `review_required` / `insufficient_evidence` / `passed`. Baseline-aware; this is the gating signal. `insufficient_evidence` (added v0.14) fires when evidence coverage is degraded past threshold. |
| `blocker_count` | Number of items in `release_decision.blockers`. |
| `review_item_count` | Number of items in `release_decision.review_items`. |
| `ci_would_fail` | `true`/`false`. Whether the active fail policy would fail CI. |

```yaml
- id: shipgate
  uses: ThreeMoonsLab/agents-shipgate@v1.0.0

- if: steps.shipgate.outputs.decision == 'blocked'
  run: echo "Release blocked by Agents Shipgate"
```

**Diagnostic (informational by itself):** `diff_enabled` — `true`/`false`.
Whether the action performed a base-branch tool-surface comparison
(`diff_base: target` or `diff_from: <ref>` was set and the scan succeeded).

Action Surface Diff outputs:

| Output | Purpose |
|---|---|
| `action_diff_enabled` | `true`/`false`. Whether `action_surface_diff` was enabled. |
| `actions_added` | Count of newly added actions. |
| `actions_modified` | Count of modified actions. |
| `actions_removed` | Count of removed actions. |

Release decisions still come from `decision` / `ci_would_fail`; action policy
findings can feed those fields through `findings[].blocks_release`.

**Legacy (kept for v0.7 callers, baseline-blind):** `status`, `critical_count`, `high_count`, `medium_count`, `baseline_new_count`, `baseline_matched_count`, `baseline_resolved_count`, `report_json`, `report_markdown`, `report_sarif`, `exit_code`. New gates should use `decision` and `ci_would_fail` instead — `summary.status` flips to `release_blockers_detected` even on baseline-matched-only criticals, while `decision` correctly classifies them as `review_required`.

Verifier artifacts: `verifier_json` points at `verifier.json`, and
`pr_comment_markdown` points at the Markdown body the action posts to PRs.
The default PR comment style is `capability-review`: it leads with
two sections: a human summary with `merge_verdict`, capability delta, next
actor, and artifact links; then a fenced JSON agent instruction block with
`control.next_action`, `fix_task`, and `control`. The underlying release
gate remains `report.json.release_decision.decision`. For one minor release
cycle, existing adopters can set `pr_comment_style: findings` to keep the v1
findings-oriented comment while updating downstream automation.

The Action also emits GitHub Actions job annotations by default for
source-backed blockers and review items. Disable with `check_annotations:
'false'`, or tune the cap with `check_annotation_limit`.

When `check_run: 'true'` is enabled, the Check Run uses the same PR projection
as job annotations. `check_run_policy: advisory` preserves the default
mergeable/success, blocked/failure, human-routed/neutral behavior.
`check_run_policy: blocked-fails` keeps human-routed verdicts neutral but fails
`blocked` and `unknown` so setup failures do not look successful. For direct
branch protection, use `check_run_policy: require-mergeable`; only
`can_merge_without_human == true` succeeds. `check_run_policy` first shipped in
v1.0.0, which the Check Run policy example pins.

`verify` writes static capability artifacts to the workflow artifact when
available: `capabilities.lock.json`, `base.capabilities.lock.json`, and
`capability-lock-diff.json`. These are review artifacts only; they do not
create a second gate.

For PR review diffs, set `diff_base: target`. The action delegates to
`agents-shipgate verify`, which never fetches. Use `fetch-depth: 0` on
`actions/checkout` or fetch the base ref in an earlier step; otherwise verify
records `merge_verdict: "unknown"`, skips the head-only scan, and exits 2.
`base_ref` and `head_ref` may be set explicitly for clearer PR wiring. When
`head_ref` is set, verify scans an isolated archive of that ref; otherwise it
scans the checked-out workspace.
Existing `diff_base` / `diff_from` workflows keep working.

Rollout note for the verifier-cycle minor: the Action defaults are
`verify_mode: verify` and `pr_comment_style: capability-review`. New outputs
are additive and old outputs remain stable; keep using `decision` /
`ci_would_fail` as CI gating outputs, and use `merge_verdict` /
`can_merge_without_human` for PR-controller routing. The additive verifier
outputs are:
`should_run`, `trigger_action`, `trigger_rule_ids`, `verifier_verdict`,
`merge_verdict`, `can_merge_without_human`, `trust_root_touched`,
`policy_weakened`, `capability_changes_added`,
`capability_changes_modified`, and `capability_changes_removed`.
The verifier flags mirror `verifier_summary`; the capability counts mirror
`capability_change` (`modified` is `broadened + narrowed`).

## Verifier Rollout Policies

Use one of these policies after the advisory comment is understood. These
policies consume verifier projections for workflow routing; the source gate is
still `report.json.release_decision.decision`.

```yaml
- name: Fail blocked capability changes
  if: steps.shipgate.outputs.merge_verdict == 'blocked'
  run: exit 1
```

This blocks obvious release blockers while still allowing
`human_review_required` PRs to proceed after the team performs the review.

```yaml
- name: Require mergeable verifier verdict
  if: steps.shipgate.outputs.can_merge_without_human != 'true'
  run: exit 1
```

This is the strict authority mode: only PRs with no blocker, no insufficient
evidence, and no human-review requirement can merge automatically.

```yaml
- name: Fail insufficient static evidence
  if: steps.shipgate.outputs.merge_verdict == 'insufficient_evidence'
  run: exit 1
```

This blocks only evidence-degraded PRs while leaving `blocked` and
`human_review_required` to separate policies.
