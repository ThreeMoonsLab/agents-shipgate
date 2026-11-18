"""Tests for ``shipgate apply-patches``.

Per the v0.6 plan §4:
- File-grouped, single SHA per file.
- Containment-checked against report.manifest_dir (per C13), and against the
  working directory and its git toplevel, which the report cannot name (#1031).
- Dry-run by default; ``--apply`` required to mutate.
- ``--confidence high`` (default) skips medium-confidence patches like
  scope-coverage appends.
- ManualPatch is never applied.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.cli.scan import run_scan
from agents_shipgate.schemas.report import ReadinessReport

REPORT_SCHEMA_VERSION = ReadinessReport.model_fields["report_schema_version"].default

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
runner = CliRunner()


@pytest.fixture(autouse=True)
def _run_from_the_test_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """apply-patches writes only under the directory it runs from (#1031).

    An operator runs it from the repository the report was produced for; the
    tests do the same, from the temporary directory that holds their workspace.
    """

    monkeypatch.chdir(tmp_path)


def _agent_mode_payload(result) -> dict:
    lines = [line for line in (result.output or "").splitlines() if line.startswith("{")]
    assert lines, result.output
    return json.loads(lines[-1])


def _seed_with_stale_suppression(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    shutil.copytree(SAMPLES / "support_refund_agent", workspace)
    manifest_path = workspace / "shipgate.yaml"
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    data.setdefault("checks", {})["ignore"] = [
        {
            "check_id": "SHIP-DOC-MISSING-DESCRIPTION",
            "tool": "nonexistent_tool",
            "reason": "stale",
        }
    ]
    manifest_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return workspace


def _scan_with_patches(workspace: Path) -> Path:
    """Run scan against a workspace, return the report.json path."""
    out_dir = workspace / "agents-shipgate-reports"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    run_scan(
        config_path=workspace / "shipgate.yaml",
        output_dir=out_dir,
        formats=["json"],
        ci_mode="advisory",
        suggest_patches=True,
    )
    return out_dir / "report.json"


# --- Happy path ------------------------------------------------------------


def test_apply_removes_stale_suppression_round_trip(tmp_path: Path) -> None:
    workspace = _seed_with_stale_suppression(tmp_path)
    report_path = _scan_with_patches(workspace)
    manifest_path = workspace / "shipgate.yaml"

    # Sanity: stale suppression present pre-apply.
    pre = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert any(
        s["tool"] == "nonexistent_tool" for s in pre["checks"]["ignore"]
    )

    result = runner.invoke(
        app,
        [
            "apply-patches",
            "--from",
            str(report_path),
            "--confidence",
            "high",
            "--apply",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["applied"] is True
    file_outcome = payload["files"][str(manifest_path.resolve())]
    assert file_outcome["status"] == "applied"

    post = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert post["checks"]["ignore"] == []


def test_dry_run_default_does_not_mutate(tmp_path: Path) -> None:
    workspace = _seed_with_stale_suppression(tmp_path)
    report_path = _scan_with_patches(workspace)
    manifest_path = workspace / "shipgate.yaml"
    pre_text = manifest_path.read_text(encoding="utf-8")

    result = runner.invoke(
        app,
        ["apply-patches", "--from", str(report_path), "--confidence", "high", "--json"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["applied"] is False
    # File untouched.
    assert manifest_path.read_text(encoding="utf-8") == pre_text


# --- Confidence filter -----------------------------------------------------


def test_confidence_high_skips_medium_scope_coverage_patches(tmp_path: Path) -> None:
    """Default --confidence high means medium scope-coverage appends are
    not applied. Per A5: adding scopes can encode policy choices."""
    workspace = _seed_with_stale_suppression(tmp_path)
    report_path = _scan_with_patches(workspace)
    report = json.loads(report_path.read_text(encoding="utf-8"))

    # The fixture should have at least one scope-coverage finding (medium
    # patch). Skip the test if the fixture happens not to surface one.
    medium_patches = [
        p
        for f in report["findings"]
        for p in (f.get("patches") or [])
        if p.get("kind") == "append_pointer"
    ]
    if not medium_patches:
        return  # fixture-dependent; nothing to assert

    manifest_path = workspace / "shipgate.yaml"
    pre = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    pre_scopes = list(pre.get("permissions", {}).get("scopes", []))

    result = runner.invoke(
        app,
        [
            "apply-patches",
            "--from",
            str(report_path),
            "--confidence",
            "high",
            "--apply",
        ],
    )
    assert result.exit_code == 0

    post = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    post_scopes = list(post.get("permissions", {}).get("scopes", []))
    # Default --confidence high must NOT have appended scopes.
    assert post_scopes == pre_scopes


def test_confidence_medium_includes_scope_appends(tmp_path: Path) -> None:
    workspace = _seed_with_stale_suppression(tmp_path)
    report_path = _scan_with_patches(workspace)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    medium_patches = [
        p
        for f in report["findings"]
        for p in (f.get("patches") or [])
        if p.get("kind") == "append_pointer"
    ]
    if not medium_patches:
        return

    manifest_path = workspace / "shipgate.yaml"
    result = runner.invoke(
        app,
        [
            "apply-patches",
            "--from",
            str(report_path),
            "--confidence",
            "medium",
            "--apply",
        ],
    )
    assert result.exit_code == 0
    post = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert post.get("permissions", {}).get("scopes")  # something appended


# --- Containment ----------------------------------------------------------


def test_containment_violation_refused(tmp_path: Path) -> None:
    """A patch whose target_file is outside report.manifest_dir aborts
    with exit code 5. Critical safety net per C13."""
    workspace = _seed_with_stale_suppression(tmp_path)
    report_path = _scan_with_patches(workspace)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    # Forge a patch targeting /etc/passwd; rewrite the report.
    for finding in report["findings"]:
        for patch in finding.get("patches") or []:
            if patch.get("kind") == "remove_pointer":
                patch["target_file"] = "/etc/passwd"
                break
    report_path.write_text(json.dumps(report), encoding="utf-8")

    result = runner.invoke(
        app,
        ["apply-patches", "--from", str(report_path), "--apply"],
    )
    assert result.exit_code == 5
    assert "Containment violation" in result.output or "not under" in result.output


def test_containment_violation_emits_agent_mode_error_json(tmp_path: Path) -> None:
    workspace = _seed_with_stale_suppression(tmp_path)
    report_path = _scan_with_patches(workspace)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    for finding in report["findings"]:
        for patch in finding.get("patches") or []:
            if patch.get("kind") == "remove_pointer":
                patch["target_file"] = "/etc/passwd"
                break
    report_path.write_text(json.dumps(report), encoding="utf-8")

    result = runner.invoke(
        app,
        ["apply-patches", "--from", str(report_path), "--apply"],
        env={"AGENTS_SHIPGATE_AGENT_MODE": "1"},
    )

    assert result.exit_code == 5
    payload = _agent_mode_payload(result)
    assert payload["error"] == "other_error"
    assert payload["next_action"]
    assert payload["next_actions"][0]["kind"] == "review"
    assert payload["next_actions"][0]["path"] == str(report_path)


def test_missing_manifest_dir_refuses(tmp_path: Path) -> None:
    """A current-schema report without its mutation boundary must be refused."""
    payload = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "findings": [
            {
                "patches": [
                    {
                        "kind": "remove_pointer",
                        "target_file": str(tmp_path / "anything.yaml"),
                        "pointer": "/x",
                        "target_format": "yaml",
                        "confidence": "high",
                        "rationale": "x",
                        "target_sha256": "0" * 64,
                    }
                ]
            }
        ],
    }
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(payload), encoding="utf-8")
    result = runner.invoke(
        app,
        ["apply-patches", "--from", str(report_path), "--apply"],
    )
    assert result.exit_code == 5


def test_missing_manifest_dir_emits_agent_mode_error_json(tmp_path: Path) -> None:
    payload = {"report_schema_version": REPORT_SCHEMA_VERSION, "findings": []}
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(payload), encoding="utf-8")

    result = runner.invoke(
        app,
        ["apply-patches", "--from", str(report_path), "--apply"],
        env={"AGENTS_SHIPGATE_AGENT_MODE": "1"},
    )

    assert result.exit_code == 5
    error = _agent_mode_payload(result)
    assert error["error"] == "other_error"
    assert error["next_actions"][0]["kind"] == "command"
    assert "scan -c shipgate.yaml" in error["next_actions"][0]["command"]


# --- SHA drift ------------------------------------------------------------


def test_drift_skips_file_with_clear_message(tmp_path: Path) -> None:
    workspace = _seed_with_stale_suppression(tmp_path)
    report_path = _scan_with_patches(workspace)
    manifest_path = workspace / "shipgate.yaml"

    # Mutate the manifest so its SHA diverges from the patch envelope.
    text = manifest_path.read_text(encoding="utf-8")
    manifest_path.write_text(text + "\n# user edit\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "apply-patches",
            "--from",
            str(report_path),
            "--confidence",
            "high",
            "--apply",
            "--json",
        ],
    )
    assert result.exit_code == 0  # not an error; per-file skip
    payload = json.loads(result.output)
    file_outcome = payload["files"][str(manifest_path.resolve())]
    assert file_outcome["status"] == "skipped_drift"
    # File still has the user edit.
    assert "# user edit" in manifest_path.read_text(encoding="utf-8")


# --- Manual filter --------------------------------------------------------


def test_manual_patches_never_applied(tmp_path: Path) -> None:
    """Even with --kinds=manual, ManualPatch is filtered out — it carries
    no machine-applicable data. (Defense-in-depth.)"""
    workspace = _seed_with_stale_suppression(tmp_path)
    report_path = _scan_with_patches(workspace)
    result = runner.invoke(
        app,
        [
            "apply-patches",
            "--from",
            str(report_path),
            "--kinds",
            "manual",
            "--apply",
            "--json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    # No files touched (only manual filtered through, then dropped).
    assert payload["files"] == {}


# --- Multi-remove ordering (regression: list index shifts) ----------------


def test_multi_remove_against_same_list_does_not_corrupt(tmp_path: Path) -> None:
    """Two RemovePointerPatch ops against the same YAML list (e.g.
    /policies/.../0 and /policies/.../1) must NOT raise IndexError or
    silently delete the wrong entry.

    Regression for the v0.6 reviewer's reproduction: applying in report
    order was buggy because the first delete shifts subsequent indexes.
    Fix: removes are now sorted so higher list indexes fire first.
    """
    from agents_shipgate.cli.apply_patches import _apply_yaml
    from agents_shipgate.schemas.patches import RemovePointerPatch

    text = (
        "policies:\n"
        "  require_approval_for_tools:\n"
        "    - tool: a\n"
        "      reason: x\n"
        "    - tool: b\n"
        "      reason: y\n"
        "    - tool: c\n"
        "      reason: z\n"
    )
    patches = [
        RemovePointerPatch(
            target_file="x",
            pointer="/policies/require_approval_for_tools/0",
            target_format="yaml",
            confidence="high",
            rationale="x",
            target_sha256="0",
        ),
        RemovePointerPatch(
            target_file="x",
            pointer="/policies/require_approval_for_tools/1",
            target_format="yaml",
            confidence="high",
            rationale="x",
            target_sha256="0",
        ),
    ]
    result = _apply_yaml(text, patches)
    # Removed indexes 0 and 1 of the original list → only `c` remains.
    assert "tool: c" in result
    assert "tool: a" not in result
    assert "tool: b" not in result


def test_malformed_patch_payload_exits_2(tmp_path: Path) -> None:
    """A patch with missing required fields must exit 2 (documented
    contract for malformed --from input), not raise a Pydantic
    traceback exiting 1.

    Regression for v0.6 reviewer feedback.
    """
    report = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "manifest_dir": str(tmp_path),
        "findings": [
            {
                "patches": [
                    # Missing target_file, pointer, target_format,
                    # rationale, target_sha256.
                    {"kind": "remove_pointer", "confidence": "high"}
                ]
            }
        ],
    }
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    result = runner.invoke(app, ["apply-patches", "--from", str(path)])
    assert result.exit_code == 2, result.output
    assert "Malformed patch" in (result.output or "")


def test_unreadable_report_emits_malformed_patch_agent_mode_error(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "missing-report.json"
    result = runner.invoke(
        app,
        ["apply-patches", "--from", str(report_path)],
        env={"AGENTS_SHIPGATE_AGENT_MODE": "1"},
    )

    assert result.exit_code == 2
    payload = _agent_mode_payload(result)
    assert payload["error"] == "malformed_patch"
    assert payload["next_actions"][0]["kind"] == "review"
    assert payload["next_actions"][0]["path"] == str(report_path)


def test_multi_remove_index_overflow_does_not_crash(tmp_path: Path) -> None:
    """2-element list, remove indexes 0 and 1 in report order would
    crash with IndexError after fix=False. Sorted highest-first, both
    succeed and the list is empty."""
    from agents_shipgate.cli.apply_patches import _apply_yaml
    from agents_shipgate.schemas.patches import RemovePointerPatch

    text = (
        "policies:\n"
        "  require_approval_for_tools:\n"
        "    - tool: a\n"
        "      reason: x\n"
        "    - tool: b\n"
        "      reason: y\n"
    )
    patches = [
        RemovePointerPatch(
            target_file="x",
            pointer="/policies/require_approval_for_tools/0",
            target_format="yaml",
            confidence="high",
            rationale="x",
            target_sha256="0",
        ),
        RemovePointerPatch(
            target_file="x",
            pointer="/policies/require_approval_for_tools/1",
            target_format="yaml",
            confidence="high",
            rationale="x",
            target_sha256="0",
        ),
    ]
    result = _apply_yaml(text, patches)
    # Both entries removed; list should be empty.
    assert "tool: a" not in result
    assert "tool: b" not in result
    assert "require_approval_for_tools: []" in result


def test_containment_violation_symlink_escape_refused(tmp_path: Path) -> None:
    """A target_file that is a symlink inside manifest_dir pointing outside
    must be refused: containment compares resolved real paths, and the
    symlink's final destination escapes the boundary."""
    workspace = _seed_with_stale_suppression(tmp_path)
    report_path = _scan_with_patches(workspace)
    report = json.loads(report_path.read_text(encoding="utf-8"))

    outside = tmp_path / "outside-target.yaml"
    outside.write_text("version: '0.1'\n", encoding="utf-8")
    manifest_dir = Path(report["manifest_dir"])
    evil_link = manifest_dir / "evil-link.yaml"
    evil_link.symlink_to(outside)

    forged = False
    for finding in report["findings"]:
        for patch in finding.get("patches") or []:
            if patch.get("kind") == "remove_pointer":
                patch["target_file"] = str(evil_link)
                forged = True
                break
        if forged:
            break
    assert forged, "expected a remove_pointer patch to forge"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    result = runner.invoke(
        app,
        ["apply-patches", "--from", str(report_path), "--apply"],
    )
    assert result.exit_code == 5
    assert "Containment violation" in result.output or "not under" in result.output
    # The file outside the boundary must be untouched.
    assert outside.read_text(encoding="utf-8") == "version: '0.1'\n"


@pytest.mark.parametrize("version,reason", [
    (None, "report_schema_missing"), ("0.43", "report_schema_pre_freeze"),
    ("1.99", "report_schema_newer_than_engine"), ("2.0", "report_schema_future_major"),
    ("garbage", "report_schema_malformed"),
])
def test_unsupported_report_schema_cannot_mutate_manifest(tmp_path: Path, version, reason) -> None:
    workspace = _seed_with_stale_suppression(tmp_path)
    report_path = _scan_with_patches(workspace)
    payload = json.loads(report_path.read_text())
    if version is None:
        payload.pop("report_schema_version")
    else:
        payload["report_schema_version"] = version
    report_path.write_text(json.dumps(payload))
    manifest = workspace / "shipgate.yaml"
    before = manifest.read_bytes()
    result = runner.invoke(app, ["apply-patches", "--from", str(report_path), "--apply", "--json"], env={"AGENTS_SHIPGATE_AGENT_MODE": "1"})
    assert result.exit_code == 2, result.output
    assert manifest.read_bytes() == before
    assert _agent_mode_payload(result)["reason_code"] == reason
    assert _agent_mode_payload(result)["next_actions"][0]["kind"] == "review"


# --- Trusted roots and targets (#1031) -------------------------------------
#
# ``manifest_dir`` and every ``target_file`` arrive in the same report as the
# patches, so neither can be what bounds them. These reports are written by
# hand, as anyone handing over a report could.

_MANIFEST = (
    'version: "0.1"\n'
    "project:\n"
    "  name: p\n"
    "agent:\n"
    "  name: a\n"
    "  declared_purpose:\n"
    "    - do one thing\n"
    "environment:\n"
    "  target: local\n"
    "tool_sources:\n"
    "  - id: s\n"
    "    type: mcp\n"
    "    path: tools.json\n"
)


def _write_manifest(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / "shipgate.yaml"
    manifest.write_text(_MANIFEST, encoding="utf-8")
    return manifest


def _pointer_report(
    path: Path,
    *,
    manifest_dir: Path,
    patches: list[dict],
) -> Path:
    path.write_text(
        json.dumps(
            {
                "report_schema_version": REPORT_SCHEMA_VERSION,
                "manifest_dir": str(manifest_dir),
                "findings": [{"id": "F1", "patches": patches}],
            }
        ),
        encoding="utf-8",
    )
    return path


def _pointer_patch(kind: str, target: Path, pointer: str, **extra) -> dict:
    return {
        "kind": kind,
        "target_file": str(target),
        "pointer": pointer,
        "target_format": "json" if target.suffix == ".json" else "yaml",
        "confidence": "high",
        "rationale": "test",
        "target_sha256": (
            hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else "0" * 64
        ),
        **extra,
    }


def test_apply_patches_refuses_target_outside_trusted_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A report naming another directory as its manifest_dir writes nothing there."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    other = tmp_path / "other-repo"
    target = _write_manifest(other)
    before = target.read_bytes()
    report = _pointer_report(
        workspace / "report.json",
        manifest_dir=other,
        patches=[_pointer_patch("set_pointer", target, "/project/name", value="changed")],
    )
    monkeypatch.chdir(workspace)

    result = runner.invoke(
        app,
        ["apply-patches", "--from", str(report), "--apply"],
        env={"AGENTS_SHIPGATE_AGENT_MODE": "1"},
    )

    assert result.exit_code == 5, result.output
    assert target.read_bytes() == before
    assert "Containment violation" in result.output
    payload = _agent_mode_payload(result)
    assert payload["error"] == "other_error"
    assert payload["next_actions"][0]["kind"] == "review"
    assert payload["next_actions"][0]["path"] == str(report)
    assert "untrusted" in payload["next_actions"][0]["why"]


@pytest.mark.parametrize(
    ("relative", "content", "pointer"),
    [
        (".claude/settings.json", '{"permissions": {"allow": []}}\n', "/permissions/allow"),
        (".github/workflows/ci.yml", "on: push\njobs: {}\n", "/jobs"),
        ("config/settings.json", '{"allow": []}\n', "/allow"),
    ],
    ids=["host-settings", "workflow", "other-json"],
)
def test_apply_patches_refuses_a_target_that_is_not_a_shipgate_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative: str,
    content: str,
    pointer: str,
) -> None:
    """Inside the repository too: a generated patch only ever edits the manifest."""

    repository = tmp_path / "repository"
    (repository / ".git").mkdir(parents=True)
    _write_manifest(repository)
    target = repository / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    before = target.read_bytes()
    report = _pointer_report(
        repository / "report.json",
        manifest_dir=repository,
        patches=[_pointer_patch("append_pointer", target, pointer, value="Bash(example:*)")],
    )
    monkeypatch.chdir(repository)

    result = runner.invoke(
        app,
        ["apply-patches", "--from", str(report), "--apply"],
        env={"AGENTS_SHIPGATE_AGENT_MODE": "1"},
    )

    assert result.exit_code == 5, result.output
    assert target.read_bytes() == before
    assert "not a Shipgate manifest" in result.output or "protected" in result.output
    assert _agent_mode_payload(result)["error"] == "other_error"


def test_apply_patches_refuses_a_declaration_placed_by_a_foreign_manifest_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``declare_action`` targets resolve against manifest_dir, so they are bounded too."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    other = tmp_path / "other-repo"
    manifest = _write_manifest(other)
    before = manifest.read_bytes()
    report = workspace / "report.json"
    report.write_text(
        json.dumps(
            {
                "report_schema_version": REPORT_SCHEMA_VERSION,
                "manifest_dir": str(other),
                "findings": [],
                "release_decision": {
                    "evidence_coverage": {
                        "evidence_gaps": [
                            {
                                "next_action": {
                                    "patch": {
                                        "kind": "declare_action",
                                        "target_path": "shipgate.yaml",
                                        "selector": {"tool": "send_email"},
                                        "declaration": {"effect": "write"},
                                        "confidence": "high",
                                        "rationale": "test",
                                        "target_sha256": hashlib.sha256(before).hexdigest(),
                                    }
                                }
                            }
                        ]
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(workspace)

    result = runner.invoke(
        app,
        ["apply-patches", "--from", str(report), "--kinds", "declare_action", "--apply"],
    )

    assert result.exit_code == 5, result.output
    assert manifest.read_bytes() == before
    # The refusal names the file the relative target_path resolves to.
    assert f"shipgate.yaml -> {manifest.resolve()}" in result.output


def test_apply_patches_writes_anywhere_in_the_repository_it_runs_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The git toplevel of the working directory is trusted: run from a subdirectory."""

    repository = tmp_path / "repository"
    (repository / ".git").mkdir(parents=True)
    (repository / "docs").mkdir()
    target = _write_manifest(repository / "services" / "agent")
    report = _pointer_report(
        repository / "report.json",
        manifest_dir=target.parent,
        patches=[_pointer_patch("set_pointer", target, "/project/name", value="q")],
    )
    monkeypatch.chdir(repository / "docs")

    result = runner.invoke(app, ["apply-patches", "--from", str(report), "--apply"])

    assert result.exit_code == 0, result.output
    assert "name: q" in target.read_text(encoding="utf-8")


def test_apply_patches_writes_under_the_workspace_it_is_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--workspace`` is typed by the operator, so it is trusted wherever the command runs."""

    repository = tmp_path / "repository"
    target = _write_manifest(repository)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    report = _pointer_report(
        repository / "report.json",
        manifest_dir=repository,
        patches=[_pointer_patch("set_pointer", target, "/project/name", value="q")],
    )
    monkeypatch.chdir(elsewhere)

    refused = runner.invoke(app, ["apply-patches", "--from", str(report), "--apply"])
    assert refused.exit_code == 5, refused.output

    result = runner.invoke(
        app,
        ["apply-patches", "--workspace", str(repository), "--from", str(report), "--apply"],
    )

    assert result.exit_code == 0, result.output
    assert "name: q" in target.read_text(encoding="utf-8")


def test_apply_patches_ignores_a_git_entry_that_is_not_a_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stray ``.git`` file in an ancestor does not make that ancestor trusted."""

    outer = tmp_path / "outer"
    target = _write_manifest(outer)
    (outer / ".git").write_text("not a repository\n", encoding="utf-8")
    inner = outer / "inner"
    inner.mkdir()
    before = target.read_bytes()
    report = _pointer_report(
        inner / "report.json",
        manifest_dir=outer,
        patches=[_pointer_patch("set_pointer", target, "/project/name", value="q")],
    )
    monkeypatch.chdir(inner)

    result = runner.invoke(app, ["apply-patches", "--from", str(report), "--apply"])

    assert result.exit_code == 5, result.output
    assert target.read_bytes() == before


def test_owned_git_root_accepts_only_a_repository_this_user_owns(tmp_path: Path) -> None:
    from agents_shipgate.core.agent_controls import owned_git_root_for

    directory_repo = tmp_path / "directory-repo"
    (directory_repo / ".git").mkdir(parents=True)
    assert owned_git_root_for(directory_repo / "a" / "b") == directory_repo.resolve()

    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: /somewhere/.git/worktrees/w\n", encoding="utf-8")
    assert owned_git_root_for(worktree) == worktree.resolve()

    # The nearest entry decides: a stray file below a real repository answers
    # None rather than widening to the repository above it.
    stray = directory_repo / "nested"
    stray.mkdir()
    (stray / ".git").write_text("junk\n", encoding="utf-8")
    assert owned_git_root_for(stray / "deeper") is None

    if os.name == "posix":
        linked = tmp_path / "linked"
        linked.mkdir()
        (linked / ".git").symlink_to(directory_repo / ".git", target_is_directory=True)
        assert owned_git_root_for(linked) is None


def test_apply_patches_reports_a_directory_target_without_a_traceback(
    tmp_path: Path,
) -> None:
    """A target that is a directory is refused as a structured error, exit 5."""

    _write_manifest(tmp_path)
    report = _pointer_report(
        tmp_path / "report.json",
        manifest_dir=tmp_path,
        patches=[_pointer_patch("set_pointer", tmp_path, "/project/name", value="q")],
    )

    result = runner.invoke(
        app,
        ["apply-patches", "--from", str(report), "--apply"],
        env={"AGENTS_SHIPGATE_AGENT_MODE": "1"},
    )

    assert result.exit_code == 5, result.output
    assert isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.output
    assert "not a regular file" in result.output
    assert _agent_mode_payload(result)["error"] == "other_error"


def test_a_directory_target_is_an_error_row_when_applied_directly(tmp_path: Path) -> None:
    from agents_shipgate.cli.apply_patches import _apply_one_file
    from agents_shipgate.schemas.patches import SetPointerPatch

    patch = SetPointerPatch(
        target_file=str(tmp_path),
        pointer="/x",
        value=1,
        target_format="yaml",
        confidence="high",
        rationale="r",
        target_sha256="0" * 64,
    )

    outcome = _apply_one_file(tmp_path, [patch], apply=True)

    assert outcome.status == "error"
    assert "not a regular file" in (outcome.error or "")


# --- Pointer validation (#1031) ---------------------------------------------


@pytest.mark.parametrize(
    ("kind", "pointer", "extra"),
    [
        ("set_pointer", "/tool_sources/-", {"value": "x"}),
        ("set_pointer", "/tool_sources/01", {"value": "x"}),
        ("set_pointer", "/tool_sources/7", {"value": "x"}),
        ("set_pointer", "/tool_sources/-1", {"value": "x"}),
        ("set_pointer", "/agent/declared_purpose/0/key", {"value": "x"}),
        ("set_pointer", "/version/key", {"value": "x"}),
        ("set_pointer", "/a~2b", {"value": "x"}),
        ("set_pointer", "project", {"value": "x"}),
        ("remove_pointer", "/tool_sources/-", {}),
        ("remove_pointer", "/tool_sources/first", {}),
        ("remove_pointer", "/missing/key", {}),
        ("append_pointer", "/tool_sources/7", {"value": "x"}),
        ("append_pointer", "/tool_sources/0", {"value": "x"}),
        ("append_pointer", "/version", {"value": "x"}),
    ],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_apply_patches_reports_a_pointer_that_does_not_fit_as_malformed(
    tmp_path: Path, kind: str, pointer: str, extra: dict
) -> None:
    """Exit 2 and the malformed_patch envelope, never a traceback, nothing written."""

    target = _write_manifest(tmp_path)
    before = target.read_bytes()
    report = _pointer_report(
        tmp_path / "report.json",
        manifest_dir=tmp_path,
        patches=[_pointer_patch(kind, target, pointer, **extra)],
    )

    result = runner.invoke(
        app,
        ["apply-patches", "--from", str(report), "--apply"],
        env={"AGENTS_SHIPGATE_AGENT_MODE": "1"},
    )

    assert result.exit_code == 2, result.output
    assert isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.output
    payload = _agent_mode_payload(result)
    assert payload["error"] == "malformed_patch"
    assert payload["next_actions"][0]["path"] == str(report)
    assert target.read_bytes() == before


def test_a_malformed_pointer_in_one_file_leaves_every_file_unwritten(tmp_path: Path) -> None:
    """Files are planned before any is written, so no group is applied half-way."""

    first = _write_manifest(tmp_path / "a")
    second = _write_manifest(tmp_path / "b")
    before = (first.read_bytes(), second.read_bytes())
    report = _pointer_report(
        tmp_path / "report.json",
        manifest_dir=tmp_path,
        patches=[
            _pointer_patch("set_pointer", first, "/project/name", value="ok"),
            _pointer_patch("set_pointer", second, "/tool_sources/-", value="x"),
        ],
    )

    result = runner.invoke(app, ["apply-patches", "--from", str(report), "--apply"])

    assert result.exit_code == 2, result.output
    assert (first.read_bytes(), second.read_bytes()) == before


def test_an_append_pointer_ending_in_dash_appends_to_that_list(tmp_path: Path) -> None:
    target = _write_manifest(tmp_path)
    report = _pointer_report(
        tmp_path / "report.json",
        manifest_dir=tmp_path,
        patches=[
            _pointer_patch(
                "append_pointer", target, "/agent/declared_purpose/-", value="do another thing"
            )
        ],
    )

    result = runner.invoke(app, ["apply-patches", "--from", str(report), "--apply"])

    assert result.exit_code == 0, result.output
    purposes = yaml.safe_load(target.read_text(encoding="utf-8"))["agent"]["declared_purpose"]
    assert purposes == ["do one thing", "do another thing"]


def test_apply_patches_refuses_a_patch_that_leaves_the_manifest_unloadable(
    tmp_path: Path,
) -> None:
    """The target is checked after the patch too: the result must still be a manifest."""

    target = _write_manifest(tmp_path / "docs" / "example")
    before = target.read_bytes()
    report = _pointer_report(
        tmp_path / "report.json",
        manifest_dir=tmp_path,
        patches=[
            _pointer_patch(
                "set_pointer",
                target,
                "/ci",
                value={"mode": "advisory", "extra_key_for_other_tool": True},
            )
        ],
    )

    result = runner.invoke(
        app,
        ["apply-patches", "--from", str(report), "--apply"],
        env={"AGENTS_SHIPGATE_AGENT_MODE": "1"},
    )

    assert result.exit_code == 5, result.output
    assert "would no longer load as a Shipgate manifest" in result.output
    assert target.read_bytes() == before


def test_apply_patches_applies_a_patch_that_keeps_the_manifest_loadable(
    tmp_path: Path,
) -> None:
    target = _write_manifest(tmp_path)
    report = _pointer_report(
        tmp_path / "report.json",
        manifest_dir=tmp_path,
        patches=[_pointer_patch("set_pointer", target, "/ci", value={"mode": "advisory"})],
    )

    result = runner.invoke(app, ["apply-patches", "--from", str(report), "--apply"])

    assert result.exit_code == 0, result.output
    assert yaml.safe_load(target.read_text(encoding="utf-8"))["ci"] == {"mode": "advisory"}
