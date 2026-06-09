from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.cli.verify.capability_review import build_capability_review
from agents_shipgate.cli.verify.git import read_file_at_ref
from agents_shipgate.cli.verify.orchestrator import _prune_base_scan_cache
from agents_shipgate.cli.verify.pr_comment import render_pr_comment
from agents_shipgate.core.errors import AgentsShipgateError, ConfigError, InputParseError
from agents_shipgate.report.json_report import report_json_payload
from agents_shipgate.schemas.capabilities import (
    CapabilityLockFileV1,
    CapabilityLockHashes,
    CapabilityLockSource,
    CapabilityLockSummary,
)
from agents_shipgate.schemas.capability_change import (
    CapabilityChangeBlock,
    CapabilityChangeMember,
    ProtectedSurfaceChange,
    VerifierCapabilityDeltaSummary,
    VerifierSummary,
)
from agents_shipgate.schemas.report import (
    BaselineDelta,
    EvidenceCoverageDecision,
    FailPolicy,
    Finding,
    ReadinessReport,
    ReleaseDecision,
    ReportSummary,
    ToolSurfaceSummary,
)
from agents_shipgate.schemas.surfaces import (
    ActionSurfaceChange,
    ActionSurfaceDiff,
    ActionSurfaceDiffSummary,
)
from agents_shipgate.schemas.verifier import VerifierArtifact, VerifierFixTask

runner = CliRunner()


def test_verify_trigger_skip_writes_lightweight_artifacts(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _commit_all(repo, "base")
    _set_origin_main(repo)
    (repo / "README.md").write_text("docs only\n", encoding="utf-8")
    _commit_all(repo, "docs")

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            "shipgate.yaml",
            "--base",
            "origin/main",
            "--head",
            "HEAD",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["head_status"] == "skipped"
    assert payload["trigger"]["run_shipgate"] is False
    verifier_json = repo / "agents-shipgate-reports" / "verifier.json"
    pr_comment = repo / "agents-shipgate-reports" / "pr-comment.md"
    assert verifier_json.is_file()
    assert pr_comment.is_file()
    assert not (repo / "agents-shipgate-reports" / "report.json").exists()
    assert "report_json" not in payload["artifacts"]
    assert payload["base_status"] == "skipped"


def test_verify_missing_base_without_manifest_is_unknown_not_mergeable(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    (repo / "tools.json").write_text('{"tools":[]}\n', encoding="utf-8")
    _commit_all(repo, "base")
    (repo / "tools.json").write_text(
        '{"tools":[{"name":"delete_files","description":"Delete files."}]}\n',
        encoding="utf-8",
    )
    _commit_all(repo, "head")

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            "shipgate.yaml",
            "--base",
            "origin/main",
            "--head",
            "HEAD",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 2, result.output
    payload = json.loads(result.output)
    assert payload["base_status"] == "ref_missing"
    assert payload["head_status"] == "failed"
    assert payload["merge_verdict"] == "unknown"
    assert payload["can_merge_without_human"] is False
    assert not (repo / "agents-shipgate-reports" / "report.json").exists()


def test_verify_non_git_workspace_exits_config_error(tmp_path: Path) -> None:
    workspace = tmp_path / "not-git"
    workspace.mkdir()

    result = runner.invoke(
        app,
        ["verify", "--workspace", str(workspace), "--config", "shipgate.yaml"],
    )

    assert result.exit_code == 2
    assert "Workspace is not inside a git checkout" in result.output


def test_verify_explicit_head_scans_ref_archive_not_dirty_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo_with_manifest(tmp_path)
    _set_origin_main(repo)
    (repo / "tools.json").write_text(
        '{"tools":[{"name":"delete_files"}]}\n',
        encoding="utf-8",
    )
    calls: list[dict[str, Any]] = []

    def fake_run_scan(**kwargs: Any):
        calls.append(kwargs)
        scan_root = Path(kwargs["config_path"]).parent
        assert scan_root != repo
        assert "delete_files" not in (scan_root / "tools.json").read_text(
            encoding="utf-8"
        )
        report = _report(decision="passed", exit_code=0)
        out_dir = Path(kwargs["output_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "report.json").write_text(
            json.dumps(report_json_payload(report), indent=2),
            encoding="utf-8",
        )
        return report, 0

    monkeypatch.setattr("agents_shipgate.cli.verify.orchestrator.run_scan", fake_run_scan)

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            "shipgate.yaml",
            "--head",
            "origin/main",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["changed_files"] == []
    assert len(calls) == 1


def test_verify_real_base_scan_enables_head_diff(tmp_path: Path) -> None:
    repo = _repo_with_manifest(tmp_path)
    _set_origin_main(repo)
    (repo / "tools.json").write_text(
        """
{
  "tools": [
    {
      "name": "docs.lookup",
      "description": "Look up internal documentation metadata.",
      "annotations": {"readOnlyHint": true}
    }
  ]
}
""".lstrip(),
        encoding="utf-8",
    )
    _commit_all(repo, "add tool")

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            "shipgate.yaml",
            "--base",
            "origin/main",
            "--head",
            "HEAD",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["base_status"] == "succeeded"
    report_payload = json.loads(
        (repo / "agents-shipgate-reports" / "report.json").read_text(encoding="utf-8")
    )
    assert report_payload["tool_surface_diff"]["enabled"] is True
    assert report_payload["tool_surface_diff"]["summary"]["tools_added"] == 1


def test_pr_comment_keeps_code_span_values_unescaped() -> None:
    verifier = VerifierArtifact(
        workspace="/tmp/work",
        config="shipgate.yaml",
        base_ref="origin/main",
        head_ref="HEAD",
        trigger={"rationale": "docs-only"},
        base_status="cache_hit",
        head_status="skipped",
        artifacts={
            "report_markdown": "agents-shipgate-reports/report.md",
            "verifier_json": "agents-shipgate-reports/verifier.json",
        },
    )

    comment = render_pr_comment(verifier, report=None, style="findings")

    assert "`cache_hit`" in comment
    assert "cache\\_hit" not in comment
    assert "`agents-shipgate-reports/report.md`" in comment
    assert "agents\\-shipgate\\-reports" not in comment
    assert "workflow artifact" in comment


def test_capability_review_pr_comment_leads_with_top_changes_and_trust_root() -> None:
    report = _report(decision="blocked", exit_code=20)
    report.action_surface_diff = ActionSurfaceDiff(
        enabled=True,
        summary=ActionSurfaceDiffSummary(actions_added=1, blocking_findings=1),
        added=[
            ActionSurfaceChange(
                type="ACTION_ADDED",
                action_id="refund",
                tool_name="stripe.create_refund",
                operation="create_refund",
                severity="critical",
                reason="Action added: stripe.create_refund",
            )
        ],
    )
    report.findings = [
        Finding(
            id="F-action",
            check_id="SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING",
            title="refund action lacks controls",
            severity="critical",
            category="action_surface",
            tool_name="stripe.create_refund",
            evidence={"action_id": "refund"},
            recommendation="Declare approval and idempotency.",
            blocks_release=True,
        ),
        Finding(
            id="F-trust",
            check_id="SHIP-VERIFY-TRUST-ROOT-TOUCHED",
            title="Release trust root touched: shipgate.yaml",
            severity="medium",
            category="verify",
            evidence={
                "changed_file": "shipgate.yaml",
                "trust_root_class": "manifest",
            },
            recommendation="Human review required.",
        ),
    ]
    report.capability_change = CapabilityChangeBlock(
        enabled=True,
        added=[
            CapabilityChangeMember(
                id="cap-refund",
                direction="added",
                subject_kind="action",
                tool="stripe.create_refund",
                action="refund",
                release_impact="blocks_release",
                rationale="Action added: stripe.create_refund",
                related_finding_ids=["F-action"],
            )
        ],
    )
    report.protected_surface_changes = [
        ProtectedSurfaceChange(
            path="shipgate.yaml",
            kind="manifest",
            related_finding_ids=["F-trust"],
        )
    ]
    report.verifier_summary = VerifierSummary(
        verdict="blocked",
        capability_delta_summary=VerifierCapabilityDeltaSummary(added=1),
        protected_surface_touched=True,
        policy_weakened=False,
    )
    verifier = VerifierArtifact(
        workspace="/tmp/work",
        config="shipgate.yaml",
        trigger={"rationale": "1 run_shipgate rule(s) matched."},
        head_status="succeeded",
        release_decision={"decision": "blocked"},
        decision="blocked",
        merge_verdict="blocked",
        headline="This PR adds a refund action without approval evidence.",
        capability_review=build_capability_review(report),
        fix_task=VerifierFixTask(
            actor="human",
            safe_to_attempt=False,
            instructions=[
                "A human owner must confirm approval and idempotency evidence."
            ],
            forbidden_shortcuts=[],
            verification_command="agents-shipgate verify --base origin/main --head HEAD --json",
        ),
        artifacts={
            "report_json": "agents-shipgate-reports/report.json",
            "packet_json": "agents-shipgate-reports/packet.json",
            "verifier_json": "agents-shipgate-reports/verifier.json",
        },
    )

    comment = render_pr_comment(verifier, report=report)

    assert "## Agents Shipgate result: block" in comment
    assert "Decision: `block`" in comment
    assert "Risk: `" in comment
    assert "Audit ID: `sg_audit_" in comment
    assert "Headline: This PR adds a refund action without approval evidence" in comment
    assert "Release gate: `blocked`" in comment
    assert "Reason: test decision" in comment
    assert "Capability changes: +1, 0 modified, -0" in comment
    assert "### Capability changes" in comment
    assert "| blocks release | action added | `stripe.create_refund` |" in comment
    assert "### Required before merge" in comment
    assert "Actor: Human (human authority required" in comment
    assert "A human owner must confirm approval and idempotency evidence" in comment
    assert "### Trust-root warnings" in comment
    assert comment.index("### Required before merge") < comment.index(
        "### Trust-root warnings"
    )
    assert "`shipgate.yaml` (manifest): human review is required." in comment
    assert "Do not suppress findings, lower severity, or edit evidence" in comment
    assert "### Artifacts" in comment
    assert "[packet.json](agents-shipgate-reports/packet.json)" in comment


def test_capability_review_pr_comment_uses_merge_verdict_vocabulary() -> None:
    report = _report(decision="review_required", exit_code=0)
    verifier = VerifierArtifact(
        workspace="/tmp/work",
        config="shipgate.yaml",
        trigger={"rationale": "1 run_shipgate rule(s) matched."},
        head_status="succeeded",
        release_decision={"decision": "review_required"},
        decision="review_required",
        merge_verdict="human_review_required",
        capability_review=build_capability_review(report),
        artifacts={"verifier_json": "agents-shipgate-reports/verifier.json"},
    )

    comment = render_pr_comment(verifier, report=report)

    assert "## Agents Shipgate result: require_review" in comment
    assert "Decision: `require_review`" in comment
    assert "Release gate: `review_required`" in comment
    assert "Decision: `review_required`" not in comment
    assert "Reason: test decision" in comment


def test_capability_review_pr_comment_does_not_double_blank_without_headline() -> None:
    report = _report(decision="review_required", exit_code=0)
    verifier = VerifierArtifact(
        workspace="/tmp/work",
        config="shipgate.yaml",
        trigger={"rationale": "1 run_shipgate rule(s) matched."},
        head_status="succeeded",
        release_decision={"decision": "review_required"},
        decision="review_required",
        merge_verdict="human_review_required",
        headline="",
        capability_review=build_capability_review(report),
        artifacts={"verifier_json": "agents-shipgate-reports/verifier.json"},
    )

    comment = render_pr_comment(verifier, report=report)

    assert "\n\n\nDecision:" not in comment


def test_capability_review_pr_comment_unknown_when_head_scan_failed() -> None:
    verifier = VerifierArtifact(
        workspace="/tmp/work",
        config="shipgate.yaml",
        trigger={"rationale": "1 run_shipgate rule(s) matched."},
        head_status="failed",
        head_exit_code=2,
        merge_verdict="unknown",
        artifacts={"verifier_json": "agents-shipgate-reports/verifier.json"},
    )

    comment = render_pr_comment(verifier, report=None)

    assert "## Agents Shipgate result: require_review" in comment
    assert "Decision: `require_review`" in comment
    assert "## Agents Shipgate: mergeable" not in comment
    assert "Head scan did not produce a report" in comment


def test_verify_missing_base_ref_is_unknown_not_head_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo_with_manifest(tmp_path)
    calls: list[dict[str, Any]] = []
    _patch_run_scan(monkeypatch, calls, head_exit=0)

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            "shipgate.yaml",
            "--base",
            "origin/main",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 2, result.output
    payload = json.loads(result.output)
    assert payload["base_status"] == "ref_missing"
    assert payload["head_status"] == "failed"
    assert payload["merge_verdict"] == "unknown"
    assert payload["can_merge_without_human"] is False
    assert payload["release_decision"] is None
    assert calls == []


def test_verify_base_missing_manifest_disables_diff_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _commit_all(repo, "base")
    _set_origin_main(repo)
    _write_manifest(repo)
    _commit_all(repo, "head")
    calls: list[dict[str, Any]] = []
    _patch_run_scan(monkeypatch, calls, head_exit=0)

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            "shipgate.yaml",
            "--base",
            "origin/main",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["base_status"] == "missing_manifest"
    assert len(calls) == 1
    assert calls[0]["diff_from_path"] is None


def test_verify_successful_base_scan_feeds_head_diff_from(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo_with_manifest(tmp_path)
    _set_origin_main(repo)
    (repo / "tools.json").write_text('{"tools":[{"name":"new"}]}\n', encoding="utf-8")
    _commit_all(repo, "head")
    calls: list[dict[str, Any]] = []
    _patch_run_scan(monkeypatch, calls, head_exit=0)

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            "shipgate.yaml",
            "--base",
            "origin/main",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["base_status"] == "succeeded"
    assert len(calls) == 2
    assert calls[0]["ci_mode"] == "advisory"
    assert calls[0]["packet_enabled"] is False
    assert calls[1]["diff_from_path"] is not None
    assert Path(calls[1]["diff_from_path"]).is_file()


def test_verify_base_scan_cache_hit_skips_second_base_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo_with_manifest(tmp_path)
    _set_origin_main(repo)
    (repo / "tools.json").write_text('{"tools":[{"name":"new"}]}\n', encoding="utf-8")
    _commit_all(repo, "head")
    calls: list[dict[str, Any]] = []
    _patch_run_scan(monkeypatch, calls, head_exit=0)
    args = [
        "verify",
        "--workspace",
        str(repo),
        "--config",
        "shipgate.yaml",
        "--base",
        "origin/main",
        "--format",
        "json",
    ]

    first = runner.invoke(app, args)
    second = runner.invoke(app, args)

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert json.loads(second.output)["base_status"] == "cache_hit"
    cache_path = json.loads(second.output)["base_report_json"]
    assert "agents-shipgate-reports/.cache" not in cache_path
    assert not (repo / "agents-shipgate-reports" / ".cache").exists()
    assert len(calls) == 3
    assert calls[0]["config_path"] != calls[1]["config_path"]  # base then head
    assert calls[2]["config_path"] == calls[1]["config_path"]  # second run head only


def test_verify_head_overrides_manifest_packet_formats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo_with_manifest(tmp_path)
    with (repo / "shipgate.yaml").open("a", encoding="utf-8") as handle:
        handle.write(
            """
output:
  packet:
    formats: [md, html]
""",
        )
    _commit_all(repo, "packet formats")
    calls: list[dict[str, Any]] = []
    _patch_run_scan(monkeypatch, calls, head_exit=0)

    result = runner.invoke(
        app,
        ["verify", "--workspace", str(repo), "--config", "shipgate.yaml"],
    )

    assert result.exit_code == 0, result.output
    assert calls[0]["packet_enabled"] is True
    assert calls[0]["packet_formats"] == ["json"]


def test_verify_head_strict_gate_exit_is_authoritative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo_with_manifest(tmp_path)
    calls: list[dict[str, Any]] = []
    _patch_run_scan(monkeypatch, calls, head_exit=20, decision="blocked")

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            "shipgate.yaml",
            "--ci-mode",
            "strict",
        ],
    )

    assert result.exit_code == 20
    assert (repo / "agents-shipgate-reports" / "verifier.json").is_file()


@pytest.mark.parametrize(
    ("exc", "exit_code", "message"),
    [
        (ConfigError("bad config"), 2, "Config error: bad config"),
        (InputParseError("bad input"), 3, "Input parsing error: bad input"),
        (AgentsShipgateError("bad shipgate"), 4, "Agents Shipgate error: bad shipgate"),
    ],
)
def test_verify_head_errors_preserve_exit_codes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exc: Exception,
    exit_code: int,
    message: str,
) -> None:
    repo = _repo_with_manifest(tmp_path)

    def fake_run_scan(**_kwargs: Any):
        raise exc

    monkeypatch.setattr("agents_shipgate.cli.verify.orchestrator.run_scan", fake_run_scan)

    result = runner.invoke(
        app,
        ["verify", "--workspace", str(repo), "--config", "shipgate.yaml"],
    )

    assert result.exit_code == exit_code
    assert message in result.output


def test_verify_artifact_write_failure_does_not_mask_scan_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo_with_manifest(tmp_path)

    def fake_run_scan(**_kwargs: Any):
        raise ConfigError("bad config")

    def fake_write_artifacts(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("artifact failed")

    monkeypatch.setattr("agents_shipgate.cli.verify.orchestrator.run_scan", fake_run_scan)
    monkeypatch.setattr(
        "agents_shipgate.cli.verify.orchestrator._write_artifacts",
        fake_write_artifacts,
    )

    result = runner.invoke(
        app,
        ["verify", "--workspace", str(repo), "--config", "shipgate.yaml"],
    )

    assert result.exit_code == 2
    assert "Config error: bad config" in result.output
    assert "artifact failed" not in result.output


def test_verify_capability_review_artifact_failure_does_not_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo_with_manifest(tmp_path)

    def fake_run_scan(**kwargs: Any):
        callback = kwargs.get("capability_lock_callback")
        if callback is not None:
            callback(_empty_capability_lock())
        report = _report(decision="passed", exit_code=0)
        out_dir = Path(kwargs["output_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "report.json").write_text(
            json.dumps(report_json_payload(report), indent=2),
            encoding="utf-8",
        )
        return report, 0

    def fail_review_artifacts(**_kwargs: Any):
        raise RuntimeError("artifact boom")

    monkeypatch.setattr("agents_shipgate.cli.verify.orchestrator.run_scan", fake_run_scan)
    monkeypatch.setattr(
        "agents_shipgate.cli.verify.orchestrator._write_capability_review_artifacts",
        fail_review_artifacts,
    )

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            "shipgate.yaml",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["head_status"] == "succeeded"
    assert payload["head_exit_code"] == 0
    assert payload["merge_verdict"] == "mergeable"
    assert "capability_lock" not in payload["artifacts"]
    assert payload["base_notes"] == [
        "Capability review artifacts unavailable: artifact boom"
    ]


def test_verify_base_materialization_does_not_create_git_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo_with_manifest(tmp_path)
    _set_origin_main(repo)
    (repo / "tools.json").write_text('{"tools":[{"name":"new"}]}\n', encoding="utf-8")
    _commit_all(repo, "head")
    calls: list[dict[str, Any]] = []
    _patch_run_scan(monkeypatch, calls, head_exit=0)

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            "shipgate.yaml",
            "--base",
            "origin/main",
        ],
    )

    assert result.exit_code == 0, result.output
    worktrees = subprocess.run(
        ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert worktrees.count("worktree ") == 1


def test_read_file_at_ref_reads_single_blob(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    lock = repo / ".agents-shipgate" / "capabilities.lock.json"
    lock.parent.mkdir()
    lock.write_text('{"ok": true}\n', encoding="utf-8")
    _commit_all(repo, "lock")

    assert (
        read_file_at_ref(repo, "HEAD", Path(".agents-shipgate/capabilities.lock.json"))
        == '{"ok": true}\n'
    )
    assert read_file_at_ref(repo, "HEAD", Path("missing.json")) is None


def test_prune_base_scan_cache_keeps_newest_entries(tmp_path: Path) -> None:
    cache_root = tmp_path / "base-scans"
    cache_root.mkdir()
    old = cache_root / "old"
    new = cache_root / "new"
    newest = cache_root / "newest"
    for index, path in enumerate((old, new, newest), start=1):
        path.mkdir()
        os.utime(path, (index, index))

    _prune_base_scan_cache(cache_root, keep=2)

    assert not old.exists()
    assert new.exists()
    assert newest.exists()


def test_verify_preview_requires_no_manifest_and_exits_zero(tmp_path: Path) -> None:
    workspace = tmp_path / "fresh"
    workspace.mkdir()

    result = runner.invoke(
        app, ["verify", "--workspace", str(workspace), "--preview", "--format", "json"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mode"] == "preview"
    assert payload["merge_verdict"] == "unknown"
    assert "init" in payload["first_next_action"]["command"]
    assert (workspace / "agents-shipgate-reports" / "verifier.json").is_file()
    assert not (workspace / "shipgate.yaml").exists()


def test_verify_preview_docs_only_diff_does_not_recommend_init(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _commit_all(repo, "base")
    _set_origin_main(repo)
    (repo / "README.md").write_text("docs only\n", encoding="utf-8")
    _commit_all(repo, "docs")

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--preview",
            "--base",
            "origin/main",
            "--head",
            "HEAD",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mode"] == "preview"
    assert payload["trigger"]["should_run"] is False
    assert payload["first_next_action"]["kind"] == "none"
    assert payload["first_next_action"]["command"] is None


def test_verify_preview_missing_base_reports_uncertainty(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _commit_all(repo, "base")
    (repo / "README.md").write_text("docs only\n", encoding="utf-8")
    _commit_all(repo, "docs")

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--preview",
            "--base",
            "origin/main",
            "--head",
            "HEAD",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mode"] == "preview"
    assert payload["first_next_action"]["kind"] == "fetch_base"
    assert "could not inspect" in payload["first_next_action"]["why"]
    assert payload["base_notes"]


def test_verify_json_flag_is_shortcut_for_format_json(tmp_path: Path) -> None:
    workspace = tmp_path / "fresh"
    workspace.mkdir()
    result = runner.invoke(
        app, ["verify", "--workspace", str(workspace), "--preview", "--json"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mode"] == "preview"


def _patch_run_scan(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[dict[str, Any]],
    *,
    head_exit: int,
    decision: str = "passed",
) -> None:
    def fake_run_scan(**kwargs: Any):
        calls.append(kwargs)
        report = _report(decision=decision, exit_code=head_exit)
        out_dir = Path(kwargs["output_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "report.json").write_text(
            json.dumps(report_json_payload(report), indent=2),
            encoding="utf-8",
        )
        return report, head_exit

    monkeypatch.setattr("agents_shipgate.cli.verify.orchestrator.run_scan", fake_run_scan)


def _report(*, decision: str, exit_code: int) -> ReadinessReport:
    return ReadinessReport(
        run_id="r",
        project={"name": "p"},
        agent={"name": "a"},
        environment={"target": "local"},
        summary=ReportSummary(status="clean"),
        release_decision=ReleaseDecision(
            decision=decision,  # type: ignore[arg-type]
            reason="test decision",
            evidence_coverage=EvidenceCoverageDecision(
                level="static",
                human_review_recommended=False,
                source_warning_count=0,
                low_confidence_tool_count=0,
            ),
            baseline_delta=BaselineDelta(enabled=False),
            fail_policy=FailPolicy(
                ci_mode="strict" if exit_code == 20 else "advisory",
                fail_on=["critical"],
                new_findings_only=False,
                would_fail_ci=exit_code == 20,
                exit_code=exit_code,
            ),
        ),
        tool_surface=ToolSurfaceSummary(total_tools=0, high_risk_tools=0),
    )


def _empty_capability_lock() -> CapabilityLockFileV1:
    return CapabilityLockFileV1(
        cli_version="test",
        source=CapabilityLockSource(
            config_path="shipgate.yaml",
            manifest_dir=".",
            agent_id="agent:test",
            agent_name="test-agent",
        ),
        summary=CapabilityLockSummary(),
        hashes=CapabilityLockHashes(
            semantic_capability_set_hash="0" * 64,
            evidence_set_hash="1" * 64,
            source_set_hash="2" * 64,
        ),
        capabilities=[],
    )


def _repo_with_manifest(tmp_path: Path) -> Path:
    repo = _init_repo(tmp_path)
    _write_manifest(repo)
    (repo / "tools.json").write_text('{"tools":[]}\n', encoding="utf-8")
    _commit_all(repo, "base")
    return repo


def _write_manifest(repo: Path) -> None:
    (repo / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: test
agent:
  name: test-agent
  declared_purpose:
    - test
environment:
  target: local
tool_sources:
  - id: tools
    type: mcp
    path: tools.json
""".lstrip(),
        encoding="utf-8",
    )


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    return repo


def _commit_all(repo: Path, message: str) -> None:
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)


def _set_origin_main(repo: Path) -> None:
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", "HEAD"],
        cwd=repo,
        check=True,
    )
