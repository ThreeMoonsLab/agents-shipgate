from pathlib import Path

import yaml

WORKFLOW_DIR = Path(".github/workflows")


def _workflow_paths() -> list[Path]:
    return sorted([*WORKFLOW_DIR.glob("*.yml"), *WORKFLOW_DIR.glob("*.yaml")])


def _workflow_steps(workflow: dict) -> list[dict]:
    steps: list[dict] = []
    jobs = workflow.get("jobs") or {}
    for job in jobs.values():
        if not isinstance(job, dict):
            continue
        for step in job.get("steps") or []:
            if isinstance(step, dict):
                steps.append(step)
    return steps


def _local_action_metadata_path(uses: str) -> Path | None:
    if not uses.startswith("./"):
        return None
    action_path = Path(uses.split("@", 1)[0])
    candidates = [action_path / "action.yml", action_path / "action.yaml"]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise AssertionError(f"Local action {uses!r} has no action.yml or action.yaml")


def test_github_script_reads_output_dir_from_env():
    text = Path("action.yml").read_text(encoding="utf-8")

    assert "OUTPUT_DIR: ${{ inputs.output_dir }}" in text
    assert "process.env.OUTPUT_DIR" in text
    assert 'path.join("${{ inputs.output_dir }}", "report.json")' not in text


def test_action_installs_from_source_when_no_pypi_version_is_set():
    text = Path("action.yml").read_text(encoding="utf-8")

    assert 'default: ""' in text
    assert 'python -m pip install "${GITHUB_ACTION_PATH}"' in text
    assert "agents-shipgate==${SHIPGATE_VERSION}" in text


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
        "check_annotations_json",
        "capability_lock_json",
        "base_capability_lock_json",
        "capability_lock_diff_json",
        "attestation_json",
        "org_evidence_bundle_json",
        "host_grants_json",
        "org_status_json",
        "exit_code",
        "should_run",
        "trigger_action",
        "trigger_rule_ids",
        "verifier_verdict",
        "merge_verdict",
        "can_merge_without_human",
        "agent_control_state",
        "agent_control_reason",
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
    assert "fail_on_decisions" not in data["inputs"]
    assert data["inputs"]["check_annotations"]["default"] == "true"
    assert data["inputs"]["check_annotation_limit"]["default"] == "50"
    assert data["inputs"]["check_run"]["default"] == "false"
    assert data["inputs"]["check_run_policy"]["default"] == "advisory"
    assert "require-mergeable" in data["inputs"]["check_run_policy"]["description"]
    assert data["inputs"]["check_run_name"]["default"] == "Agents Shipgate"
    assert data["inputs"]["pr_comment_style"]["default"] == "capability-review"
    assert "legacy v1 findings comment" in data["inputs"]["pr_comment_style"]["description"]
    assert data["inputs"]["attestation"]["default"] == "false"
    assert data["inputs"]["registry_repo_label"]["default"] == ""
    assert data["inputs"]["org_bundle"]["default"] == "false"
    assert data["inputs"]["host_audit"]["default"] == "false"
    assert data["inputs"]["org_status"]["default"] == "false"
    assert data["inputs"]["registry_path"]["default"] == ""


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
        "agent_control_state",
        "agent_control_reason",
        "trust_root_touched",
        "policy_weakened",
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
    assert "CHECK_ANNOTATION_LIMIT: ${{ inputs.check_annotation_limit }}" in text
    assert "scripts/github_action_annotations.py" in text
    assert "fail_on_merge_verdicts" in text
    assert "fail_on_decisions" not in text
    assert "Apply Agents Shipgate merge verdict policy" in text
    assert "merge_verdict_policy_exit_code" in text
    assert "if: ${{ always() && inputs.fail_on_merge_verdicts != '' }}" in text
    assert "verifier.json did not expose a merge verdict" in text
    assert "scripts/github_check_run.py" in text
    assert "check-run-payload.json" in text
    assert "Build Agents Shipgate attestation" in text
    assert "inputs.attestation == 'true'" in text
    assert "--ci-context github-actions" in text
    assert "Build Agents Shipgate host audit" in text
    assert "inputs.host_audit == 'true'" in text
    assert "Build Agents Shipgate org status" in text
    assert "inputs.org_status == 'true'" in text
    assert "Build Agents Shipgate org evidence bundle" in text
    assert "inputs.org_bundle == 'true'" in text
    assert "REGISTRY_PATH: ${{ inputs.registry_path }}" in text
    assert "REGISTRY_REPO_LABEL: ${{ inputs.registry_repo_label }}" in text
    assert "CHECK_RUN_POLICY: ${{ inputs.check_run_policy }}" in text
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


def test_repo_workflows_use_declared_local_action_inputs():
    for workflow_path in _workflow_paths():
        workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
        assert isinstance(workflow, dict), f"{workflow_path} must parse as a mapping"
        for step in _workflow_steps(workflow):
            uses = step.get("uses")
            if not isinstance(uses, str):
                continue
            metadata_path = _local_action_metadata_path(uses)
            if metadata_path is None:
                continue
            metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
            declared = set((metadata.get("inputs") or {}).keys())
            supplied = set((step.get("with") or {}).keys())
            unknown = supplied - declared
            assert not unknown, (
                f"{workflow_path}: step uses {uses!r} with undeclared local "
                f"action inputs {sorted(unknown)}; update {metadata_path}"
            )


def test_agents_shipgate_workflow_uses_merge_verdict_policy_input():
    text = (WORKFLOW_DIR / "agents-shipgate.yml").read_text(encoding="utf-8")

    assert "fail_on_decisions" not in text
    # Advisory workflows fail only on blocked/unknown; review verdicts route
    # to the human PR reviewer instead of an uncleareable red CI state.
    assert 'fail_on_merge_verdicts: "blocked,unknown"' in text


def test_action_step_summary_leads_with_verifier_merge_state():
    text = Path("action.yml").read_text(encoding="utf-8")
    script = Path("scripts/github_action_outputs.py").read_text(encoding="utf-8")

    assert "scripts/github_action_outputs.py" in text
    assert "GITHUB_STEP_SUMMARY" in script
    assert "## Agents Shipgate" in script
    assert "Merge verdict:" in script
    assert "Can merge without human:" in script
    assert "First next action:" in script
    assert "Release gate:" in script
    assert "Run ID:" in script
    assert "Agent control:" in script
    assert "Blockers:" in script
    assert "Review items:" in script
    assert "would_fail_ci=" in script


def test_action_pr_comment_truncates_user_controlled_text():
    text = Path("action.yml").read_text(encoding="utf-8")

    assert "pr-comment.md" in text
    assert "fs.readFileSync(commentPath" in text
    assert ".slice(0, 6000)" in text
    assert "Workflow artifacts:" in text
    assert "body.length + artifactLine.length <= 6000" in text
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
    import yaml

    text = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

    assert "uv publish --trusted-publishing always" in text
    assert "sigstore sign" in text

    # Checked against the commands the workflows actually run, not their text:
    # both files discuss the superseded `cyclonedx-py environment` scan in
    # comments explaining why it was replaced.
    commands = []
    for name in ("release.yml", "release-verify.yml"):
        parsed = yaml.safe_load(Path(".github/workflows", name).read_text(encoding="utf-8"))
        for job in parsed["jobs"].values():
            commands.extend(step["run"] for step in (job.get("steps") or []) if "run" in step)
    joined = "\n".join(commands)

    # The SBOM describes an isolated runtime-only install of the shipped
    # wheel. Scanning the CI environment inventoried pytest, ruff, twine and
    # Sigstore instead — a signed attestation about software the user never
    # receives.
    assert "scripts/release_sbom.py build" in joined
    assert "cyclonedx-py environment" not in joined


# --- Checkout contract for jobs that run the whole test suite (#497) ---------

#: pytest options that consume the token after them. Anything else starting
#: with `-` is a flag, and `--ignore=tests/x` carries its value inline — so a
#: path there is not a positional and does not scope the run.
_PYTEST_VALUE_OPTIONS = frozenset(
    {
        "-n",
        "-m",
        "-k",
        "-p",
        "-c",
        "-o",
        "-W",
        "--ignore",
        "--deselect",
        "--cov",
        "--cov-report",
        "--cov-fail-under",
        "--maxfail",
        "--rootdir",
    }
)


def _pytest_positionals(line: str) -> list[str] | None:
    """Positional arguments of the pytest invocation in ``line``.

    ``None`` when the line does not invoke pytest. An empty list means pytest
    was invoked with no path, which collects the whole suite.
    """

    import shlex

    stripped = line.strip()
    if stripped.startswith("#") or "pytest" not in stripped:
        return None
    try:
        tokens = shlex.split(stripped)
    except ValueError:
        # Unparseable (a shell expression, say). Treat it as a whole-suite run:
        # requiring the fuller checkout is the safe direction.
        return []
    if "pytest" not in tokens:
        return None
    positionals: list[str] = []
    skip_next = False
    for token in tokens[tokens.index("pytest") + 1 :]:
        if skip_next:
            skip_next = False
            continue
        if token.startswith("-"):
            skip_next = token in _PYTEST_VALUE_OPTIONS
            continue
        positionals.append(token)
    return positionals


def _jobs_running_the_whole_suite() -> list[tuple[Path, str, dict]]:
    found: list[tuple[Path, str, dict]] = []
    for path in _workflow_paths():
        workflow = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for job_name, job in (workflow.get("jobs") or {}).items():
            if not isinstance(job, dict):
                continue
            for step in job.get("steps") or []:
                if not isinstance(step, dict) or "run" not in step:
                    continue
                for line in str(step["run"]).splitlines():
                    positionals = _pytest_positionals(line)
                    if positionals == []:
                        found.append((path, job_name, job))
                        break
                else:
                    continue
                break
    return found


def test_jobs_running_the_whole_suite_check_out_history_and_tags():
    """The suite reads a released build's contract out of its own tag.

    ``tests/test_distribution_surface_parity.py::test_published_build_table_matches_the_tag``
    corroborates the committed ``PUBLISHED_BUILDS`` table against the real
    ``v0.15.0``, and refuses to skip when ``CI`` is set — a check that skips in
    CI is not a check. The default ``actions/checkout`` fetch is shallow and
    tagless, so any job running the whole suite has to ask for more.

    This exists because updating only `ci.yml` left `release-verify.yml`
    checking out the candidate the default way and then running the full suite,
    which `release.yml` and `release-rehearsal.yml` both call. The PR's own CI
    was green; the release path would not have been.
    """

    jobs = _jobs_running_the_whole_suite()
    assert jobs, (
        "no job was detected as running the whole suite, so this contract is "
        "checking nothing. Either the pytest invocations changed shape or "
        "_pytest_positionals stopped recognising them."
    )
    for path, job_name, job in jobs:
        checkouts = [
            step
            for step in job.get("steps") or []
            if isinstance(step, dict)
            and str(step.get("uses", "")).startswith("actions/checkout@")
        ]
        assert checkouts, f"{path.name}:{job_name} runs the suite without a checkout"
        for step in checkouts:
            options = step.get("with") or {}
            assert options.get("fetch-depth") == 0, (
                f"{path.name}:{job_name} runs the whole suite but checks out "
                "with the default shallow fetch. Add `fetch-depth: 0`."
            )
            assert options.get("fetch-tags") is True, (
                f"{path.name}:{job_name} runs the whole suite but does not "
                "fetch tags. Add `fetch-tags: true`."
            )


def test_pytest_invocation_reader_separates_scoped_runs_from_the_suite():
    """Negative control: `--ignore=tests/x` is not a positional path."""

    assert _pytest_positionals("python -m pytest tests/test_cli.py -q") == [
        "tests/test_cli.py"
    ]
    assert (
        _pytest_positionals(
            'python -m pytest -n auto -m "not perf" '
            "--ignore=tests/test_adapter_static_only.py --cov=agents_shipgate"
        )
        == []
    )
    assert _pytest_positionals("python -m pytest -n auto tests") == ["tests"]
    assert _pytest_positionals("python -m pip install -e .") is None
    assert _pytest_positionals("# python -m pytest") is None
