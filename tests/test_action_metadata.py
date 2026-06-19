from pathlib import Path

import yaml


def test_github_script_reads_output_dir_from_env():
    text = Path("action.yml").read_text(encoding="utf-8")

    assert "OUTPUT_DIR: ${{ inputs.output_dir }}" in text
    assert "process.env.OUTPUT_DIR" in text
    assert 'path.join("${{ inputs.output_dir }}", "report.json")' not in text


def test_action_installs_from_source_when_no_pypi_version_is_set():
    text = Path("action.yml").read_text(encoding="utf-8")

    assert 'default: ""' in text
    assert 'python -m pip install "${GITHUB_ACTION_PATH}"' in text
    assert 'agents-shipgate==${SHIPGATE_VERSION}' in text


def test_action_has_marketplace_metadata_and_outputs():
    data = yaml.safe_load(Path("action.yml").read_text(encoding="utf-8"))

    assert data["name"] == "Agents Shipgate"
    assert data["author"] == "ThreeMoonsLab"
    assert data["branding"] == {"icon": "shield", "color": "blue"}
    assert {
        "decision",
        "blocker_count",
        "review_item_count",
        "ci_would_fail",
        "diff_enabled",
        "status",
        "critical_count",
        "high_count",
        "baseline_new_count",
        "report_json",
        "verifier_json",
        "verify_run_json",
        "run_id",
        "pr_comment_markdown",
        "exit_code",
        "should_run",
        "trigger_action",
        "trigger_rule_ids",
        "verifier_verdict",
        "merge_verdict",
        "can_merge_without_human",
        "agent_controller_must_stop",
        "agent_controller_stop_reason",
        "agent_controller_completion_allowed",
        "trust_root_touched",
        "policy_weakened",
        "capability_changes_added",
        "capability_changes_modified",
        "capability_changes_removed",
    } <= set(data["outputs"])
    assert data["outputs"]["verifier_verdict"]["description"].startswith(
        "Verifier convenience verdict. Prefer `decision`"
    )
    assert data["inputs"]["verify_mode"]["default"] == "verify"
    assert data["inputs"]["fail_on_merge_verdicts"]["default"] == ""
    assert data["inputs"]["pr_comment_style"]["default"] == "capability-review"
    assert "legacy v1 findings comment" in data["inputs"]["pr_comment_style"]["description"]


def test_action_exposes_verifier_merge_outputs():
    """v0.22: the merge-decision projection is surfaced as Action outputs,
    sourced from verifier.json in the report_outputs step."""
    text = Path("action.yml").read_text(encoding="utf-8")
    script = Path("scripts/github_action_outputs.py").read_text(encoding="utf-8")
    data = yaml.safe_load(text)

    assert {
        "should_run",
        "trigger_rule_ids",
        "merge_verdict",
        "can_merge_without_human",
        "trust_root_touched",
        "policy_weakened",
        "verify_run_json",
        "run_id",
        "agent_controller_must_stop",
        "agent_controller_stop_reason",
        "agent_controller_completion_allowed",
        "capability_changes_added",
        "capability_changes_modified",
        "capability_changes_removed",
    } <= set(data["outputs"])
    # The new outputs read verifier.json (not just report.json).
    assert "verifier_payload = _load_json(verifier_json)" in script
    assert '"merge_verdict": merge_verdict' in script
    assert '"can_merge_without_human"' in script
    # Existing gating output stays stable (not renamed to merge_verdict).
    assert "decision" in data["outputs"]


def test_action_preserves_reports_before_applying_exit_code():
    text = Path("action.yml").read_text(encoding="utf-8")

    assert "id: scan" in text
    assert "exit 0" in text
    assert "Apply Agents Shipgate exit code" in text
    assert "steps.scan.outputs.exit_code" in text
    assert "FAIL_ON: ${{ inputs.fail_on }}" in text
    assert "BASELINE: ${{ inputs.baseline }}" in text
    assert "DIFF_FROM: ${{ inputs.diff_from }}" in text
    assert "DIFF_BASE: ${{ inputs.diff_base }}" in text
    assert "VERIFY_MODE: ${{ inputs.verify_mode }}" in text
    assert "INPUT_HEAD_REF: ${{ inputs.head_ref }}" in text
    assert "PR_COMMENT_STYLE: ${{ inputs.pr_comment_style }}" in text
    assert "fail_on_merge_verdicts" in text
    assert "Apply Agents Shipgate merge verdict policy" in text
    assert "merge_verdict_policy_exit_code" in text
    assert "verify" in text
    assert "scan" in text
    assert "--workspace" in text
    assert "--pr-comment-style" in text
    assert "args+=(--diff-from" in text
    assert "args+=(--base" in text
    assert "args+=(--head" in text
    assert "git fetch" not in text
    assert "git checkout --detach" not in text
    assert "git worktree" not in text
    assert "POLICY_PACKS: ${{ inputs.policy_packs }}" in text
    assert "args+=(--policy-pack" in text
    assert "NO_PLUGINS: ${{ inputs.no_plugins }}" in text
    assert "args+=(--no-plugins)" in text


def test_action_step_summary_leads_with_release_decision():
    text = Path("action.yml").read_text(encoding="utf-8")
    script = Path("scripts/github_action_outputs.py").read_text(encoding="utf-8")

    assert "scripts/github_action_outputs.py" in text
    assert "GITHUB_STEP_SUMMARY" in script
    assert "## Agents Shipgate" in script
    assert "Merge verdict:" in script
    assert "Can merge without human:" in script
    assert "Run ID:" in script
    assert "Blockers:" in script
    assert "Review items:" in script
    assert "would_fail_ci=" in script


def test_action_pr_comment_truncates_user_controlled_text():
    text = Path("action.yml").read_text(encoding="utf-8")

    assert "pr-comment.md" in text
    assert "fs.readFileSync(commentPath" in text
    assert ".slice(0, 6000)" in text
    assert "preferredDiff" not in text


def test_action_diff_inputs_describe_current_schema_versions():
    data = yaml.safe_load(Path("action.yml").read_text(encoding="utf-8"))

    assert "v0.4 baseline" in data["inputs"]["diff_from"]["description"]
    assert "head_ref" in data["inputs"]
    assert "never fetches" in data["inputs"]["base_ref"]["description"]
    assert "diff and scan" in data["inputs"]["head_ref"]["description"]


def test_action_pr_comment_upserts_via_sticky_marker():
    """Re-running the action on the same PR must update the existing
    Shipgate comment in place rather than appending a new one — that's
    PR spam and erodes trust. The marker also lets external tooling
    find the comment programmatically."""
    text = Path("action.yml").read_text(encoding="utf-8")

    assert "<!-- agents-shipgate-pr-comment -->" in text
    assert "github.rest.issues.listComments" in text
    assert "github.rest.issues.updateComment" in text
    # createComment is still present (the create-on-first-run branch),
    # but it must be guarded by the sticky-marker lookup.
    assert "github.rest.issues.createComment" in text


def test_action_pr_comment_paginates_listcomments_lookup():
    """Single-page listComments (per_page=100, no pagination) silently
    regresses to append-on-rerun once a PR has >100 earlier comments
    before Shipgate's first scan — the sticky marker lookup misses,
    and a fresh comment posts every time. Use github.paginate."""
    text = Path("action.yml").read_text(encoding="utf-8")

    assert "github.paginate(github.rest.issues.listComments" in text, (
        "PR-comment upsert must paginate the listComments lookup so it "
        "can find the sticky marker on PRs with many prior comments."
    )


def test_action_pr_comment_includes_packet_artifact_pointer():
    """The action must post the verifier-rendered PR comment artifact."""
    text = Path("action.yml").read_text(encoding="utf-8")

    assert "pr-comment.md" in text
    assert "verifier_json" in text


def test_action_pr_comment_surfaces_ci_mode():
    """Teams adopting Shipgate need to see at a glance whether the gate
    is advisory (won't block their PR) or strict (will). Surfacing
    fail_policy.ci_mode in the comment removes that ambiguity."""
    text = Path("action.yml").read_text(encoding="utf-8")
    script = Path("scripts/github_action_outputs.py").read_text(encoding="utf-8")

    assert "pr-comment.md" in text
    assert "would_fail_ci" in script


def test_marketplace_action_repo_has_ci_and_release_workflows():
    workflow_dir = Path(".github/workflows")

    assert workflow_dir.exists()
    # The marketplace action ships {ci.yml, release.yml}; additional
    # operational workflows (e.g. adoption-harness.yml — workflow_dispatch
    # only) are allowed and do not affect marketplace publish.
    present = {path.name for path in workflow_dir.glob("*")}
    assert {"ci.yml", "release.yml"} <= present


def test_release_workflow_uses_release_security_steps():
    text = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

    assert "uv publish --trusted-publishing always" in text
    assert "sigstore sign" in text
    assert "cyclonedx-py environment" in text
