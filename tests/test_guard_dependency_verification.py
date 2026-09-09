from __future__ import annotations

import json
import subprocess

import pytest
import yaml
from test_current_control import _live, _verify
from test_sdk_guard_dependencies import AGENT, read_workspace, write_workspace

from agents_shipgate.cli.scan.orchestrator import run_scan
from agents_shipgate.core.current_control import CurrentControlUnavailable, read_current_control
from agents_shipgate.core.static_inputs import (
    StaticInputSnapshot,
    activate_static_input_snapshot,
    reset_static_input_snapshot,
)
from agents_shipgate.core.verification_identity import (
    build_verification_plan,
    validate_dependency_inputs,
)


def _git(root, *args):
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, check=True, text=True
    ).stdout.strip()


def _workspace(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    manifest = write_workspace(root)
    (root / "shipgate.yaml").write_text(
        yaml.safe_dump(manifest.model_dump(mode="json", exclude_none=True))
    )
    (root / ".gitignore").write_text(
        "agents-shipgate-reports/\nrefund_agent/guards/\n/__init__.py\n"
    )
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.name", "Test User")
    _git(root, "config", "user.email", "test@example.test")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "base")
    return root, manifest


@pytest.mark.parametrize(
    "predicate,direction",
    [
        ("approved", "predicate_widened"),
        ("False", "predicate_narrowed"),
        ("within_limit and approved", "predicate_unchanged"),
    ],
)
def test_real_paired_repository_preserves_findings_and_guard_provenance(
    tmp_path, predicate, direction
):
    root, manifest = _workspace(tmp_path)
    old, _ = run_scan(
        config_path=root / "shipgate.yaml",
        output_dir=tmp_path / "base-report",
        plugins_enabled=False,
    )
    write_workspace(root, predicate)
    new, _ = run_scan(
        config_path=root / "shipgate.yaml",
        output_dir=tmp_path / "head-report",
        diff_from_path=tmp_path / "base-report/report.json",
        plugins_enabled=False,
    )
    (row,) = new.tool_surface_diff.guard_comparisons
    assert row.direction == direction
    assert row.before.guard_path == row.after.guard_path == "refund_agent/guards.py"
    assert row.before.inputs != row.after.inputs
    assert {f.fingerprint for f in old.findings} == {f.fingerprint for f in new.findings}
    assert old.release_decision.decision == new.release_decision.decision
    assert new.tool_surface_diff.policy_drift == []
    assert new.tool_surface_diff.finding_deltas.new_findings == []
    markdown = (tmp_path / "head-report/report.md").read_text()
    assert direction.replace("_", " ") in markdown and "no finding is excluded" in markdown


def test_actual_committed_verify_reads_guard_from_each_archived_tree(tmp_path):
    root, _ = _workspace(tmp_path)
    base = _git(root, "rev-parse", "HEAD")
    write_workspace(root, "approved")
    _git(root, "add", "refund_agent/guards.py")
    _git(root, "commit", "-qm", "weaken shared predicate")
    _verify(root, base=base)
    report = json.loads((root / "agents-shipgate-reports/report.json").read_text())
    (row,) = report["tool_surface_diff"]["guard_comparisons"]
    assert row["direction"] == "predicate_widened"
    assert row["before"]["allowed_inputs"] != row["after"]["allowed_inputs"]
    plan = json.loads((root / "agents-shipgate-reports/verification-plan.json").read_text())
    dependency_inputs = plan["inputs"]["options"]["dependency_inputs"]
    assert "refund_agent/guards.py" in {r["path"] for r in dependency_inputs["files"]}
    assert "refund_agent/guards" in dependency_inputs["absent_paths"]
    assert "__init__.py" in dependency_inputs["absent_paths"]


@pytest.mark.parametrize("mutation", ["guard_bytes", "package", "root_initializer"])
@pytest.mark.parametrize("committed", [False, True])
def test_current_control_rejects_changed_dependency_including_ignored_candidates(
    tmp_path, mutation, committed
):
    root, _ = _workspace(tmp_path)
    _verify(root, archive_head=committed)
    out = root / "agents-shipgate-reports"
    read_current_control(out, live=_live(root))
    if mutation == "guard_bytes":
        # An ignored dependency is still an input. The ordinary Git overlay
        # cannot serve as a substitute for comparing the captured bytes.
        _git(root, "update-index", "--assume-unchanged", "refund_agent/guards.py")
        (root / "refund_agent/guards.py").write_text(
            "def permitted(a: bool, b: bool):\n    return True\n"
        )
    elif mutation == "package":
        (root / "refund_agent/guards").mkdir()
    else:
        (root / "__init__.py").write_text("raise RuntimeError('never execute')\n")
    assert _git(root, "status", "--porcelain") == ""
    with pytest.raises(CurrentControlUnavailable, match="dependency"):
        read_current_control(out, live=_live(root))


def test_dependency_bytes_and_absence_are_in_same_input_identity(tmp_path):
    root, manifest = _workspace(tmp_path)
    plans = []
    for predicate in ("approved", "approved and within_limit"):
        write_workspace(root, predicate)
        snapshot = StaticInputSnapshot(root)
        token = activate_static_input_snapshot(snapshot)
        try:
            read_workspace(root, manifest)
            plans.append(
                build_verification_plan(
                    git_root=root,
                    input_root=root,
                    config_path=root / "shipgate.yaml",
                    config_logical_path="shipgate.yaml",
                    base_ref=None,
                    head_ref="HEAD",
                    archived_head=True,
                    repository_id="https://example.test/fixture.git",
                    base_commit_sha=None,
                    base_tree_sha=None,
                    head_commit_sha="a" * 40,
                    head_tree_sha="b" * 40,
                    merge_base_sha=None,
                    changed_files=[],
                    diff_text="",
                    baseline_path=None,
                    diff_from_path=None,
                    policy_pack_paths=[],
                    evaluation_date="2026-09-08",
                    options={},
                    plugins_enabled=False,
                    captured_input_paths=snapshot.paths(),
                )
            )
            snapshot.finish()
        finally:
            reset_static_input_snapshot(token)
    assert plans[0].inputs.input_set_id != plans[1].inputs.input_set_id
    validate_dependency_inputs(plans[1], root=root)
    with pytest.raises(ValueError, match="dependency input changed"):
        validate_dependency_inputs(plans[0], root=root)
    (root / "refund_agent/guards").mkdir()
    with pytest.raises(ValueError, match="lookup candidate appeared"):
        validate_dependency_inputs(plans[1], root=root)


def test_source_secrets_are_not_exposed_as_guard_evidence(tmp_path):
    root, _ = _workspace(tmp_path)
    sentinel = "sk-proj-" + "A1b2C3d4E5f6" * 5
    path = root / "refund_agent/agent.py"
    path.write_text(
        AGENT.replace("Synthetic refund guard fixture; no tool executes during a scan.", sentinel)
    )
    report, _ = run_scan(
        config_path=root / "shipgate.yaml", output_dir=tmp_path / "reports", plugins_enabled=False
    )
    for path in (tmp_path / "reports").iterdir():
        if path.is_file():
            assert sentinel not in path.read_text(errors="replace")
    assert "ast" not in report.tool_surface_facts.guard_dependencies[0].model_dump()


@pytest.mark.parametrize("missing", ["guards.py", "__init__.py"])
@pytest.mark.parametrize("committed", [False, True])
def test_recovery_of_missing_ignored_dependency_invalidates_control(tmp_path, missing, committed):
    root, _ = _workspace(tmp_path)
    path = root / "refund_agent" / missing
    data = path.read_bytes()
    _git(root, "rm", str(path.relative_to(root)))
    with (root / ".gitignore").open("a") as handle:
        handle.write(f"/refund_agent/{missing}\n")
    _git(root, "add", ".gitignore")
    _git(root, "commit", "-qm", "missing dependency")
    _verify(root, archive_head=committed)
    out = root / "agents-shipgate-reports"
    read_current_control(out, live=_live(root))
    path.write_bytes(data)
    assert _git(root, "status", "--porcelain") == ""
    with pytest.raises(CurrentControlUnavailable, match="dependency"):
        read_current_control(out, live=_live(root))


def test_redacted_parameter_cannot_produce_an_equal_predicate_claim(tmp_path):
    root, _ = _workspace(tmp_path)
    secret = "AKIAABCDEFGHIJKLMNOP"
    for name in ("agent.py", "guards.py"):
        path = root / "refund_agent" / name
        path.write_text(path.read_text().replace("approved", secret))
    first, _ = run_scan(
        config_path=root / "shipgate.yaml", output_dir=tmp_path / "base", plugins_enabled=False
    )
    assert first.tool_surface_facts.guard_dependencies[0].status == "redacted"
    second, _ = run_scan(
        config_path=root / "shipgate.yaml",
        output_dir=tmp_path / "head",
        diff_from_path=tmp_path / "base/report.json",
        plugins_enabled=False,
    )
    assert second.tool_surface_diff.guard_comparisons[0].direction == "unresolved"
    for folder in ("base", "head"):
        for path in (tmp_path / folder).iterdir():
            if path.is_file():
                assert secret not in path.read_text(errors="replace")


@pytest.mark.parametrize("committed", [False, True])
def test_removing_ignored_conflicting_package_invalidates_control(tmp_path, committed):
    root, _ = _workspace(tmp_path)
    path = root / "refund_agent/guards"
    path.mkdir()
    # A committed scan does not see untracked files, so only the worktree run
    # records this conflict. In committed mode use a tracked package and hide
    # its removal from Git's ordinary working-tree status.
    if committed:
        marker = path / "__init__.py"
        marker.write_text("")
        _git(root, "add", "-f", "refund_agent/guards/__init__.py")
        _git(root, "commit", "-qm", "ambiguous import")
        _git(root, "update-index", "--assume-unchanged", "refund_agent/guards/__init__.py")
    _verify(root, archive_head=committed)
    out = root / "agents-shipgate-reports"
    read_current_control(out, live=_live(root))
    if committed:
        marker.unlink()
    path.rmdir()
    assert _git(root, "status", "--porcelain") == ""
    with pytest.raises(CurrentControlUnavailable, match="dependency"):
        read_current_control(out, live=_live(root))


@pytest.mark.parametrize("kind", ["symlink", "oversized", "directory", "symlink_loop"])
@pytest.mark.parametrize("relative", ["refund_agent/guards.py", "refund_agent/__init__.py"])
def test_uncaptured_helper_cannot_supply_current_authority(tmp_path, kind, relative):
    root, _ = _workspace(tmp_path)
    path = root / relative
    valid = path.read_bytes()
    _git(root, "rm", relative)
    with (root / ".gitignore").open("a") as handle:
        handle.write(f"/{relative}\n")
    _git(root, "add", ".gitignore")
    _git(root, "commit", "-qm", "external helper")
    if kind in {"symlink", "symlink_loop"}:
        external = tmp_path / "external.py"
        external.write_bytes(valid)
        try:
            path.symlink_to(path.name if kind == "symlink_loop" else external)
        except OSError:
            pytest.skip("symlink creation unavailable")
    elif kind == "oversized":
        path.write_bytes(b"#" * (256 * 1024 + 1))
    else:
        path.mkdir()
    _verify(root, archive_head=False)
    out = root / "agents-shipgate-reports"
    plan = json.loads((out / "verification-plan.json").read_text())
    assert plan["inputs"]["options"]["dependency_inputs"]["unconfirmable_paths"] == [
        relative
    ]
    with pytest.raises(CurrentControlUnavailable, match="dependency could not be captured"):
        read_current_control(out, live=_live(root))
    if kind == "directory":
        path.rmdir()
    else:
        path.unlink()
    path.write_bytes(valid)
    assert _git(root, "status", "--porcelain") == ""
    with pytest.raises(CurrentControlUnavailable, match="dependency could not be captured"):
        read_current_control(out, live=_live(root))
    _verify(root, archive_head=False)
    read_current_control(out, live=_live(root))
