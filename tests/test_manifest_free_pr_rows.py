"""#684: the first PR route names changes without a manifest."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def write(repo: Path, name: str, value) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) if not isinstance(value, str) else value)


@pytest.fixture
def s1(tmp_path):
    repo = tmp_path / "host"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    write(repo, ".gitignore", "agents-shipgate-reports/\n")
    write(repo, ".claude/settings.json", {"permissions": {"allow": [], "deny": ["Bash(rm *)"]}})
    write(repo, ".mcp.json", {"mcpServers": {}})
    workflow = "on: [push]\npermissions:\n  contents: read\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo fixture\n"
    write(repo, ".github/workflows/ci.yml", workflow)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    git(repo, "checkout", "-qb", "change")
    write(
        repo,
        ".claude/settings.json",
        {"permissions": {"allow": ["Bash(*)", "WebFetch(*)"], "deny": []}},
    )
    write(repo, ".mcp.json", {"mcpServers": {"postgres": {"command": "fixture-postgres"}}})
    write(repo, ".github/workflows/ci.yml", workflow.replace("contents: read", "contents: write"))
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "widen")
    return repo


NAMES = ["Bash(*)", "WebFetch(*)", "postgres", "contents: write", "Bash(rm *)"]


@pytest.mark.parametrize("preview", [True, False])
def test_first_pr_route_names_s1_rows_before_advisory_footer(s1, preview):
    args = [
        "verify",
        "--workspace",
        str(s1),
        "--base",
        "main",
        "--head",
        "HEAD",
        "--format",
        "text",
    ]
    if preview:
        args.append("--preview")
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    footer = result.output.index("advisory: no application release policy configured")
    for name in NAMES:
        assert name in result.output[:footer]
    comment = (s1 / "agents-shipgate-reports/pr-comment.md").read_text()
    for name in NAMES:
        assert name in comment
    assert "init --write" not in result.output
    verifier = json.loads((s1 / "agents-shipgate-reports/verifier.json").read_text())
    comparison = verifier["host_comparison"]
    assert comparison["comparison_status"] == "comparable"
    assert len(comparison["rows"]) == 5
    assert verifier["release_decision"] is None
    assert not verifier["can_merge_without_human"]
    assert not verifier["control"]["permissions"]["merge"]


def test_check_names_same_five_changes_in_explicit_text_and_json(s1):
    args = ["check", "--workspace", str(s1), "--base", "main", "--head", "HEAD"]
    rendered = CliRunner().invoke(app, [*args, "--format", "text"])
    assert rendered.exit_code == 0, rendered.output
    for name in [*NAMES[:-1], "Bash(<redacted-arguments>)"]:
        assert name in rendered.output
    machine = CliRunner().invoke(app, [*args, "--format", "agent-boundary-json"])
    assert machine.exit_code == 0, machine.output
    payload = json.loads(machine.output)
    assert len(payload["rows"]) == 5
    assert payload["comparison_status"] == "comparable"
    assert payload["control"]["state"] != "complete"


def test_verify_explicit_head_ignores_dirty_worktree(s1):
    write(s1, ".claude/settings.json", {"permissions": {"allow": ["Bash(DIRTY_CANARY)"]}})
    args = [
        "verify",
        "--preview",
        "--workspace",
        str(s1),
        "--base",
        "main",
        "--head",
        "HEAD",
        "--format",
        "json",
    ]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    rows = payload["host_comparison"]["rows"]
    assert len(rows) == 5
    assert "DIRTY_CANARY" not in json.dumps(rows)
    assert "Bash(*)" in json.dumps(rows)


def test_advisory_verify_does_not_bind_old_scan_artifacts(s1):
    reports = s1 / "agents-shipgate-reports"
    reports.mkdir()
    for name in ("report.json", "report.md", "report.sarif", "packet.json", "packet.html"):
        (reports / name).write_text("OLD_SCAN_CANARY")
    result = CliRunner().invoke(
        app, ["verify", "--workspace", str(s1), "--base", "main", "--head", "HEAD", "--json"]
    )
    assert result.exit_code == 0, result.output
    pointer = json.loads((reports / "current-control.json").read_text())
    assert "report" not in pointer["artifacts"]
    assert "packet" not in pointer["artifacts"]


@pytest.mark.parametrize("side", ["base", "head"])
def test_provided_diff_matches_rows_on_either_side(s1, side, tmp_path):
    patch = tmp_path / "change.patch"
    patch.write_text(git(s1, "diff", "main", "HEAD") + "\n")
    if side == "base":
        git(s1, "checkout", "-q", "main")
    result = CliRunner().invoke(
        app,
        ["check", "--workspace", str(s1), "--diff", str(patch), "--format", "agent-boundary-json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["comparison_status"] == "comparable"
    assert len(payload["rows"]) == 5
    assert payload["comparison_scope"] == "changed_host_files"
    for name in [*NAMES[:-1], "Bash(<redacted-arguments>)"]:
        assert name in json.dumps(payload["rows"])


def test_covered_no_change_is_explicitly_zero_rows(s1):
    result = CliRunner().invoke(
        app,
        [
            "verify",
            "--preview",
            "--workspace",
            str(s1),
            "--base",
            "HEAD",
            "--head",
            "HEAD",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["host_comparison"]["comparison_status"] == "comparable"
    assert payload["host_comparison"]["rows"] == []
    assert not payload["can_merge_without_human"]


def test_malformed_host_input_is_not_a_successful_zero(s1):
    write(s1, ".mcp.json", "{broken")
    result = CliRunner().invoke(
        app, ["verify", "--preview", "--workspace", str(s1), "--base", "main", "--json"]
    )
    assert result.exit_code == 0, result.output
    comparison = json.loads(result.output)["host_comparison"]
    assert comparison["comparison_status"] == "incomparable"
    assert comparison["rows"] == []
    assert comparison["incomparable_reasons"]


def test_historical_manifest_cannot_be_replaced_with_advisory(s1):
    # The requested historical tree has a gate even though this checkout does
    # not. It must not acquire a host-only advisory bypass.
    write(s1, "shipgate.yaml", 'version: "0.1"\n')
    git(s1, "add", "shipgate.yaml")
    git(s1, "commit", "-qm", "historical manifest")
    historical = git(s1, "rev-parse", "HEAD")
    git(s1, "checkout", "-q", "HEAD~1")
    result = CliRunner().invoke(
        app, ["verify", "--workspace", str(s1), "--base", "main", "--head", historical, "--json"]
    )
    assert result.exit_code != 0
    assert json.loads(result.output)["host_comparison"] is None


def test_host_comparison_pointer_rejects_moved_base(s1):
    result = CliRunner().invoke(
        app,
        [
            "verify",
            "--preview",
            "--workspace",
            str(s1),
            "--base",
            "main",
            "--head",
            "HEAD",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    git(s1, "update-ref", "refs/heads/main", "HEAD")
    control = CliRunner().invoke(
        app,
        [
            "agent",
            "control",
            "--workspace",
            str(s1),
            "--reports-dir",
            str(s1 / "agents-shipgate-reports"),
        ],
    )
    assert control.exit_code != 0, control.output


def test_shallow_comparison_retains_recovery_instead_of_init(s1, tmp_path):
    clone = tmp_path / "shallow"
    subprocess.run(["git", "clone", "-q", "--depth=1", s1.as_uri(), str(clone)], check=True)
    result = CliRunner().invoke(
        app,
        [
            "verify",
            "--preview",
            "--workspace",
            str(clone),
            "--base",
            "HEAD",
            "--head",
            "HEAD",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["host_comparison"]["comparison_status"] == "incomparable"
    assert "shallow_history" in payload["host_comparison"]["incomparable_reasons"]
    assert payload["control"]["next_action"]["kind"] == "fetch_base"
    assert "init --write" not in result.output


@pytest.mark.parametrize("agent_mode", ["0", "1"])
def test_check_default_matches_the_reader(s1, agent_mode):
    result = CliRunner().invoke(
        app,
        ["check", "--workspace", str(s1), "--base", "main", "--head", "HEAD"],
        env={"AGENTS_SHIPGATE_AGENT_MODE": agent_mode},
    )
    assert result.exit_code == 0, result.output
    if agent_mode == "1":
        assert len(json.loads(result.output)["rows"]) == 5
    else:
        assert result.output.startswith("Repository-declared host capability changes:")
        assert result.output.index("Bash(*)") < result.output.index("Control:")


def test_strict_application_gate_is_not_silently_replaced_by_advisory(s1):
    result = CliRunner().invoke(
        app,
        [
            "verify",
            "--workspace",
            str(s1),
            "--base",
            "main",
            "--head",
            "HEAD",
            "--ci-mode",
            "strict",
            "--json",
        ],
    )
    assert result.exit_code != 0
    assert json.loads(result.output)["host_comparison"] is None


@pytest.mark.parametrize("legacy_version", ["0.1", "0.7", "0.15", "0.16", "0.17"])
def test_current_host_result_validates_and_legacy_does_not_gain_evidence(s1, legacy_version):
    from jsonschema import Draft202012Validator

    from agents_shipgate.schemas.verifier import VerifierArtifact

    result = CliRunner().invoke(
        app,
        [
            "verify",
            "--preview",
            "--workspace",
            str(s1),
            "--base",
            "main",
            "--head",
            "HEAD",
            "--json",
        ],
    )
    payload = json.loads(result.output)
    schema = json.loads((Path(__file__).parents[1] / "docs/verifier-schema.v0.18.json").read_text())
    Draft202012Validator(schema).validate(payload)
    payload["verifier_schema_version"] = legacy_version
    with pytest.raises(ValueError, match="Legacy verifier"):
        VerifierArtifact.model_validate(payload)
    if legacy_version != "0.17":
        return  # Only the immediately previous shape is this test fixture.
    payload.pop("host_comparison")
    previous = VerifierArtifact.model_validate(payload)
    assert previous.host_comparison is None
    assert previous.release_decision is None
    assert not previous.control.permissions.merge


@pytest.mark.parametrize("moved_ref", ["main", "change"])
def test_ref_move_during_initial_capture_cannot_rebind_old_rows(s1, monkeypatch, moved_ref):
    from agents_shipgate.cli.verify import host_comparison as subject

    original = subject.commit_sha
    moved = False

    def resolve(root, ref):
        nonlocal moved
        answer = original(root, ref)
        if ref == moved_ref and not moved:
            moved = True
            git(
                s1,
                "update-ref",
                "refs/heads/" + moved_ref,
                "change" if moved_ref == "main" else "main",
            )
        return answer

    monkeypatch.setattr(subject, "commit_sha", resolve)
    with pytest.raises(ValueError, match="moved"):
        subject.compare_host_refs(
            workspace=s1,
            base="main",
            head="change",
            auto_base=False,
            config_relative=Path("shipgate.yaml"),
        )


@pytest.mark.parametrize("style", ["capability-review", "findings"])
def test_both_pr_styles_show_each_change_once(s1, style):
    result = CliRunner().invoke(
        app,
        [
            "verify",
            "--workspace",
            str(s1),
            "--base",
            "main",
            "--head",
            "HEAD",
            "--pr-comment-style",
            style,
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    comment = (s1 / "agents-shipgate-reports/pr-comment.md").read_text()
    assert "Head scan did not produce a report" not in comment
    assert "Merge verdict:" not in comment
    for name in NAMES:
        assert comment.count(name) == 1, comment


def test_untracked_host_change_remains_in_default_check(s1):
    write(s1, ".cursor/mcp.json", {"mcpServers": {"untracked_server": {"command": "fixture"}}})
    result = CliRunner().invoke(
        app, ["check", "--workspace", str(s1), "--base", "HEAD", "--format", "agent-boundary-json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["comparison_status"] == "comparable"
    assert "untracked_server" in json.dumps(payload["rows"])


@pytest.mark.parametrize("format_", ["agent-boundary-json", "text"])
def test_multiline_permission_arguments_stay_private(s1, format_):
    write(
        s1,
        ".claude/settings.json",
        {"permissions": {"allow": ["Bash(echo PRIVATE_ARGUMENT_CANARY\nsecond_line)"]}},
    )
    result = CliRunner().invoke(
        app, ["check", "--workspace", str(s1), "--base", "HEAD", "--format", format_]
    )
    assert result.exit_code == 0, result.output
    assert "PRIVATE_ARGUMENT_CANARY" not in result.output
    assert "redacted-arguments" in result.output


def test_misspelled_explicit_config_does_not_become_advisory(s1):
    result = CliRunner().invoke(
        app,
        [
            "verify",
            "--workspace",
            str(s1),
            "--config",
            "custom-missing.yaml",
            "--base",
            "main",
            "--head",
            "HEAD",
            "--json",
        ],
    )
    assert result.exit_code != 0
    assert json.loads(result.output)["host_comparison"] is None


def test_advisory_first_value_does_not_interrupt_with_release_permissions(s1):
    result = CliRunner().invoke(
        app,
        [
            "verify",
            "--preview",
            "--workspace",
            str(s1),
            "--base",
            "main",
            "--head",
            "HEAD",
            "--format",
            "text",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Bash(*)" in result.output
    assert "advisory: no application release policy configured" in result.output
    assert "You may not:" not in result.output
    assert "merge authority is granted" in result.output


def test_binary_host_diff_never_claims_comparable_zero(s1, tmp_path):
    from agents_shipgate.cli.agent_result import build_agent_boundary_result

    patch_text = (
        "diff --git a/.claude/settings.json b/.claude/settings.json\n"
        "index 1111111..2222222 100644\n"
        "Binary files a/.claude/settings.json and b/.claude/settings.json differ\n"
    )
    patch = tmp_path / "binary.diff"
    patch.write_text(patch_text)
    direct = build_agent_boundary_result(
        workspace=s1,
        diff_text=patch_text,
        config=Path("shipgate.yaml"),
        policy=None,
    )
    result = CliRunner().invoke(
        app,
        [
            "check",
            "--workspace",
            str(s1),
            "--diff",
            str(patch),
            "--format",
            "agent-boundary-json",
        ],
    )
    assert result.exit_code == 0, result.output
    for payload in [direct.model_dump(mode="json"), json.loads(result.output)]:
        assert payload["input_coverage"] == "partial"
        assert payload["comparison_status"] == "incomparable"
        assert payload["incomparable_reasons"] == ["diff_input_coverage_incomplete"]
        assert payload["rows"] == []
        assert not payload["control"]["permissions"]["merge"]


def test_previous_boundary_reader_does_not_invent_comparison(s1):
    from jsonschema import Draft202012Validator

    from agents_shipgate.schemas.agent_boundary import AgentBoundaryResultV1

    result = CliRunner().invoke(app, [
        "check", "--workspace", str(s1), "--base", "main", "--head", "HEAD",
        "--format", "agent-boundary-json",
    ])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    schemas = Path(__file__).parents[1] / "docs"
    Draft202012Validator(json.loads(
        (schemas / "agent-boundary-result-schema.v3.json").read_text()
    )).validate(payload)
    payload["schema_version"] = "shipgate.agent_boundary_result/v2"
    with pytest.raises(ValueError, match="Legacy boundary"):
        AgentBoundaryResultV1.model_validate(payload)
    for field in ("rows", "comparison_status", "incomparable_reasons", "comparison_scope"):
        payload.pop(field)
    Draft202012Validator(json.loads(
        (schemas / "agent-boundary-result-schema.v2.json").read_text()
    )).validate(payload)
    previous = AgentBoundaryResultV1.model_validate(payload)
    assert previous.rows == []
    assert previous.comparison_status == "not_attempted"
    assert previous.control.model_dump(mode="json") == payload["control"]
