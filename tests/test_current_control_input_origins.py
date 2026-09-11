"""Recorded blob currency across prepare, replay, imports and live origins."""

from __future__ import annotations

import pytest
from test_current_control import _git, _live, _verify
from test_current_control import repo as repo  # noqa: F401
from test_current_control_auxiliary_inputs import auxiliary, current
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.cli.verification import assemble, worker
from agents_shipgate.core import verification_input_currency as currency
from agents_shipgate.core.current_control import CurrentControlUnavailable, read_current_control
from agents_shipgate.schemas.verification_identity import VerificationPlan


def plan_at(reports):
    return VerificationPlan.model_validate_json((reports / "verification-plan.json").read_bytes())


@pytest.mark.parametrize("kind", ["policy", "baseline", "diff_from"])
@pytest.mark.parametrize("committed", [False, True])
def test_prepare_exports_auxiliary_bytes_without_losing_their_roles(repo, kind, committed):
    original, _ = auxiliary(repo, repo / "inputs", kind)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "Synthetic preparation input")
    reports = repo / "agents-shipgate-reports"
    result = CliRunner().invoke(app, [
        "verification", "prepare", "--workspace", str(repo), "--no-plugins",
        {"policy": "--policy-pack", "baseline": "--baseline", "diff_from": "--diff-from"}[kind],
        str(original), "--out", str(reports / "verification-plan.json"),
        *(["--base", "HEAD", "--head", "HEAD"] if committed else []),
    ])
    assert result.exit_code == 0, result.output
    plan = plan_at(reports)
    blob = plan.inputs.policy_packs[0] if kind == "policy" else getattr(plan.inputs, kind)
    assert blob is not None
    assert (reports / blob.path).read_bytes() == original.read_bytes()
    assert original.relative_to(repo).as_posix() not in {b.path for b in plan.inputs.tool_sources}
    (origin,) = plan.inputs.options["input_origins"]["external"]
    assert origin["path"] == original.relative_to(repo).as_posix()
    assert origin["kind"] == ("git_blob" if committed and kind != "diff_from" else "worktree")
    currency.validate_current_plan_inputs(plan, root=repo, artifacts_root=reports)
    worker(plan_path=reports / "verification-plan.json", workspace=repo,
           diff_path=reports / "verification-input.diff", out=reports / "replayed-unit.json")
    _git(repo, "update-index", "--assume-unchanged", original.relative_to(repo).as_posix())
    original.write_bytes(original.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="changed"):
        currency.validate_current_plan_inputs(plan, root=repo, artifacts_root=reports)
    # Worker replay validates the captured request, not its current local
    # authority. The frozen portable copy still supplies exactly those bytes.
    worker(plan_path=reports / "verification-plan.json", workspace=repo,
           diff_path=reports / "verification-input.diff", out=reports / "replayed-unit.json")


@pytest.mark.parametrize("kind", ["policy", "baseline", "diff_from"])
def test_assembled_control_retains_auxiliary_origin_currency(repo, kind):
    original, options = auxiliary(repo, repo / "inputs", kind)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "Synthetic assembly input")
    _verify(repo, archive_head=True, **options)
    reports = repo / "agents-shipgate-reports"
    unit = reports / "replayed-unit.json"
    worker(plan_path=reports / "verification-plan.json", workspace=repo,
           diff_path=reports / "verification-input.diff", out=unit)
    assemble(plan_path=reports / "verification-plan.json", unit_paths=[unit],
             verifier_path=reports / "verifier.json", artifacts_root=reports,
             out=reports / "verification-receipt.json")
    current(repo)
    _git(repo, "update-index", "--assume-unchanged", original.relative_to(repo).as_posix())
    original.write_bytes(original.read_bytes() + b"\n")
    with pytest.raises(CurrentControlUnavailable):
        current(repo)


def test_ignored_live_manifest_overlaid_on_an_archived_head_is_not_a_git_blob(repo):
    _git(repo, "rm", "--cached", "shipgate.yaml")
    with (repo / ".gitignore").open("a") as handle:
        handle.write("shipgate.yaml\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "Synthetic local manifest")
    _verify(repo, archive_head=True)
    current(repo)
    assert plan_at(repo / "agents-shipgate-reports").inputs.config.source == "worktree"
    path = repo / "shipgate.yaml"
    path.write_bytes(path.read_bytes() + b"\n")
    assert _live(repo).changed_paths == ()
    with pytest.raises(CurrentControlUnavailable):
        current(repo)


def test_historical_head_refuses_before_reading_checkout_as_its_inputs(repo, monkeypatch):
    _verify(repo, archive_head=True)
    _git(repo, "commit", "--allow-empty", "-m", "Another live head")

    def forbidden(*args, **kwargs):
        pytest.fail("historical request must not compare inputs with another live HEAD")

    monkeypatch.setattr("agents_shipgate.core.current_control.validate_current_plan_inputs", forbidden)
    with pytest.raises(CurrentControlUnavailable) as raised:
        current(repo)
    assert raised.value.reason == "workspace_changed"


@pytest.mark.parametrize("outside", [False, True])
def test_portable_input_parent_alias_refuses_even_outside_workspace(repo, tmp_path, outside):
    original, options = auxiliary(repo, repo / "inputs", "baseline")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "Synthetic baseline")
    reports = tmp_path / "reports" if outside else repo / "agents-shipgate-reports"
    _verify(repo, archive_head=True, out=reports, **options)
    before = read_current_control(reports, live=lambda: _live(repo)).pointer
    assert before.control.permissions.update_pr
    tree = reports / "verification-inputs"
    moved = tmp_path / "relocated-inputs"
    tree.rename(moved)
    tree.symlink_to(moved, target_is_directory=True)
    with pytest.raises(CurrentControlUnavailable):
        read_current_control(reports, live=lambda: _live(repo))


@pytest.mark.parametrize("mutation", ["missing", "version", "unknown", "absolute", "duplicate", "unmatched"])
def test_ambiguous_or_malformed_import_origin_refuses_before_reading(repo, monkeypatch, mutation):
    _, options = auxiliary(repo, repo / "inputs", "baseline")
    _verify(repo, archive_head=False, **options)
    reports = repo / "agents-shipgate-reports"
    plan = plan_at(reports)
    # Exercise validation of engine-owned provenance independently from the
    # outer plan digest checks (which already reject un-rehashed mutation).
    origins = plan.inputs.options["input_origins"]
    if mutation == "missing":
        del plan.inputs.options["input_origins"]
    elif mutation == "version":
        origins["version"] = True
    elif mutation == "unknown":
        origins["external"][0]["kind"] = "trusted"
    elif mutation == "absolute":
        origins["external"][0]["path"] = str(repo / "inputs/baseline.json")
    elif mutation == "duplicate":
        origins["external"].append(dict(origins["external"][0]))
    else:
        origins["external"][0]["input_path"] = "another-file.json"

    def forbidden(*args, **kwargs):
        pytest.fail("invalid provenance must refuse before input I/O")

    monkeypatch.setattr(currency, "StaticInputSnapshot", forbidden)
    with pytest.raises(ValueError):
        currency.validate_current_plan_inputs(plan, root=repo, artifacts_root=reports)


@pytest.mark.parametrize("limit", ["file", "aggregate"])
def test_currency_input_reads_have_per_file_and_shared_aggregate_limits(repo, monkeypatch, limit):
    _, options = auxiliary(repo, repo / "inputs", "baseline")
    _verify(repo, archive_head=False, **options)
    current(repo)
    plan = plan_at(repo / "agents-shipgate-reports")
    if limit == "file":
        monkeypatch.setattr(currency, "MAX_CURRENCY_INPUT_BYTES", plan.inputs.config.size_bytes - 1)
    else:
        # Each input is small enough; workspace and portable copies together
        # exhaust the shared budget, without allocating a large fixture.
        largest = max(b.size_bytes for b in [plan.inputs.config, *plan.inputs.tool_sources])
        monkeypatch.setattr(currency, "MAX_CURRENCY_TOTAL_BYTES", largest + 1)
    with pytest.raises(CurrentControlUnavailable):
        current(repo)


def test_generated_base_report_is_an_artifact_not_a_live_source(repo):
    _git(repo, "branch", "base", "HEAD")
    (repo / "note.txt").write_text("Unrelated change.\n")
    _git(repo, "add", "note.txt")
    _git(repo, "commit", "-m", "Synthetic range")
    _verify(repo, archive_head=True, base="base")
    current(repo)
    reports = repo / "agents-shipgate-reports"
    plan = plan_at(reports)
    assert plan.inputs.diff_from is not None
    (origin,) = plan.inputs.options["input_origins"]["external"]
    assert origin == {
        "input_path": plan.inputs.diff_from.path, "kind": "generated", "path": None,
    }
    assert not (repo / plan.inputs.diff_from.path).exists()
    path = reports / plan.inputs.diff_from.path
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(CurrentControlUnavailable):
        current(repo)


def test_legacy_plan_without_external_inputs_needs_no_origin_guess(repo):
    _verify(repo, archive_head=False)
    reports = repo / "agents-shipgate-reports"
    plan = plan_at(reports)
    del plan.inputs.options["input_origins"]
    currency.validate_current_plan_inputs(plan, root=repo, artifacts_root=reports)


def test_drive_prefixed_blob_refuses_before_input_io(repo, monkeypatch):
    _verify(repo, archive_head=False)
    reports = repo / "agents-shipgate-reports"
    plan = plan_at(reports)
    plan.inputs.config.path = "C:shipgate.yaml"

    def forbidden(*args, **kwargs):
        pytest.fail("drive-prefixed blob must refuse before input I/O")

    monkeypatch.setattr(currency, "StaticInputSnapshot", forbidden)
    with pytest.raises(ValueError, match="portable"):
        currency.validate_current_plan_inputs(plan, root=repo, artifacts_root=reports)


@pytest.mark.parametrize("producer", ["verify", "prepare"])
@pytest.mark.parametrize("committed", [False, True])
def test_identical_policy_bytes_keep_both_live_origins(repo, producer, committed):
    first, _ = auxiliary(repo, repo / "a", "policy")
    second, _ = auxiliary(repo, repo / "b", "policy")
    assert first.read_bytes() == second.read_bytes()
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "Two distinct policy origins")
    reports = repo / "agents-shipgate-reports"
    if producer == "verify":
        _verify(repo, archive_head=committed, policy_packs=[first, second])
        current(repo)
    else:
        result = CliRunner().invoke(app, [
            "verification", "prepare", "--workspace", str(repo), "--no-plugins",
            "--policy-pack", str(first), "--policy-pack", str(second),
            "--out", str(reports / "verification-plan.json"),
            *(["--base", "HEAD", "--head", "HEAD"] if committed else []),
        ])
        assert result.exit_code == 0, result.output
    plan = plan_at(reports)
    assert len(plan.inputs.policy_packs) == 2
    origins = plan.inputs.options["input_origins"]["external"]
    assert {o["path"] for o in origins} == {"a/policy.yaml", "b/policy.yaml"}
    currency.validate_current_plan_inputs(plan, root=repo, artifacts_root=reports)
    _git(repo, "update-index", "--assume-unchanged", "a/policy.yaml")
    first.write_bytes(first.read_bytes() + b"\n")
    assert _live(repo).changed_paths == ()
    with pytest.raises(ValueError, match="changed"):
        currency.validate_current_plan_inputs(plan, root=repo, artifacts_root=reports)
    if producer == "verify":
        with pytest.raises(CurrentControlUnavailable):
            current(repo)
