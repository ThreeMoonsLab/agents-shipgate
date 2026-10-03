from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from test_current_control import _live
from test_current_control import repo as repo  # noqa: F401
from test_openshell_composition import files as composed_files
from test_openshell_inputs import POLICY, REGISTRATION, register
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.current_control import CurrentControlUnavailable, read_current_control
from agents_shipgate.core.openshell_native import (
    digest,
    observe_native,
    validate_envelope,
    validate_external_currency,
)
from agents_shipgate.schemas.openshell_native import DOMAINS


def trusted(tmp_path, *, result="within_boundary", required=True, extra="", mutate=None):
    trusted_root = tmp_path / "trusted"
    trusted_root.mkdir(exist_ok=True)
    executable = trusted_root / "prover"
    envelope = {"schema_version": 1, "prover_version": "0.1.2", "check": "boundary",
        "coverage": {"domains": DOMAINS}, "result": result, "exit_code": 0,
        "inputs": {"candidate": "candidate.yaml", "boundary": "boundary.yaml"},
        "counterexample": None, "reason_code": None, "reason": None}
    if result == "exceeds_boundary":
        envelope.update(exit_code=1, counterexample={"domain": "filesystem", "access": "write", "path": "/outside"})
    elif result in {"unsupported", "inconclusive", "error"}:
        envelope.update(exit_code=2 if result == "error" else 3, reason_code="invalid_input" if result == "error" else "unsupported_policy_shape", reason="Unsupported protocol fixture")
        if result == "error":
            envelope["coverage"] = None
    if mutate:
        mutate(envelope)
    program = f"#!{sys.executable}\nimport json,sys,time\n{extra}\nprint({json.dumps(json.dumps(envelope))})\nsys.exit({envelope['exit_code']})\n"
    executable.write_text(program)
    executable.chmod(0o700)
    boundary = trusted_root / "maximum.yaml"
    boundary.write_text(POLICY)
    config = {"version": 1, "required": required, "runtime_version": "0.1.2", "prover_version": "0.1.2",
        "executable": {"path": str(executable), "sha256": digest(executable.read_bytes())},
        "boundary": {"path": str(boundary), "sha256": digest(boundary.read_bytes())},
        "candidate": {"registration": REGISTRATION, "path": "arbitrary.rules"},
        "required_domains": DOMAINS, "timeout_seconds": 1}
    path = trusted_root / "trust.json"
    path.write_text(json.dumps(config))
    return path, config


def observe(root, config_path, required=False):
    return observe_native(config_path=config_path, required=required, workspace=root, input_root=root)


@pytest.mark.parametrize("outcome", ["within_boundary", "exceeds_boundary", "unsupported", "inconclusive", "error"])
def test_native_outcomes_remain_distinct(tmp_path, outcome):
    root = tmp_path / "repo"
    root.mkdir()
    register(root)
    path, _ = trusted(tmp_path, result=outcome)
    observed = observe(root, path)
    assert observed.status == outcome, observed
    assert observed.raw_result and observed.raw_result_sha256 == digest(observed.raw_result.encode())
    assert observed.grants_merge_authority is False
    assert len(observed.external_inputs) == 3


@pytest.mark.parametrize("mutation", [
    lambda env: env.update(schema_version=2),
    lambda env: env.update(prover_version="0.2.0"),
    lambda env: env["coverage"].update(domains=["filesystem"]),
    lambda env: env.update(exit_code=1),
    lambda env: env["inputs"].update(candidate="other.yaml"),
    lambda env: env.update(counterexample={"domain": "filesystem"}),
])
def test_mismatched_native_envelope_cannot_pass(tmp_path, mutation):
    root = tmp_path / "repo"
    root.mkdir()
    register(root)
    path, _ = trusted(tmp_path, mutate=mutation)
    observed = observe(root, path)
    assert observed.status == "invalid" and not observed.raw_result


@pytest.mark.parametrize("failure", ["timeout", "output", "malformed", "cancelled"])
def test_execution_failures_are_not_passing_evidence(tmp_path, failure):
    root = tmp_path / "repo"
    root.mkdir()
    register(root)
    extra = {"timeout": "time.sleep(10)", "output": "print('x' * (300 * 1024))",
             "malformed": "print('not JSON');sys.exit(0)", "cancelled": "sys.exit(130)"}[failure]
    path, _ = trusted(tmp_path, extra=extra)
    observed = observe(root, path)
    assert observed.status in {"timeout", "error", "invalid"}
    assert observed.actual_exit_code is not None


def test_repository_config_or_prover_boundary_cannot_self_authorize(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    register(root)
    external, config = trusted(tmp_path)
    local = root / "trust.json"
    local.write_text(external.read_text())
    assert observe(root, local, required=True).status == "invalid"
    for key in ("boundary", "executable"):
        original = dict(config[key])
        inside = root / key
        inside.write_bytes(Path(original["path"]).read_bytes())
        config[key]["path"] = str(inside)
        external.write_text(json.dumps(config))
        assert observe(root, external).status == "invalid"
        config[key] = original


@pytest.mark.parametrize("changed", ["config", "executable", "boundary"])
def test_every_external_identity_invalidates_currency(tmp_path, changed):
    root = tmp_path / "repo"
    root.mkdir()
    register(root)
    path, config = trusted(tmp_path)
    observed = observe(root, path)
    assert observed.status == "within_boundary"
    target = path if changed == "config" else Path(config[changed]["path"])
    target.write_bytes(target.read_bytes() + b"\n")
    with pytest.raises(ValueError):
        validate_external_currency(observed.model_dump(mode="json"), root)


def test_forged_repository_result_cannot_be_imported_as_configuration(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    register(root)
    path, _ = trusted(tmp_path)
    path.write_text(json.dumps({"version": 1, "result": "within_boundary", "candidate_sha256": digest(POLICY.encode())}))
    assert observe(root, path, required=True).status == "invalid"
    with pytest.raises(ValueError):
        validate_envelope(b'{"schema_version":1,"schema_version":1}', 0)


def test_composed_candidate_binds_provenance_without_reading_credentials(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    for name, value in composed_files().items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value if isinstance(value, str) else json.dumps(value))
    path, config = trusted(tmp_path)
    config["candidate"] = {"registration": REGISTRATION, "composition": "worker"}
    path.write_text(json.dumps(config))
    observed = observe(root, path)
    assert observed.status == "within_boundary", observed
    assert observed.candidate["role"] == "composed"
    assert any(row["role"] == "profile" for row in observed.candidate["composition"]["contributors"])


@pytest.mark.parametrize("protocol", ["rest", "l4"])
def test_composed_native_input_preserves_authored_omissions(tmp_path, protocol):
    root = tmp_path / "repo"
    root.mkdir()
    contents = composed_files()
    if protocol == "l4":
        contents["profiles/github.profile"]["endpoints"] = [{"host": "api.provider.example", "port": 443}]
    for name, value in contents.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value if isinstance(value, str) else json.dumps(value))
    assertions = (
        "from pathlib import Path\n"
        "value=json.loads(Path('candidate.yaml').read_text())\n"
        "endpoint=value['network_policies']['_provider_work_github']['endpoints'][0]\n"
        "assert 'persisted_queries' not in endpoint and 'graphql_max_body_bytes' not in endpoint\n"
        "assert 'credential_signing' not in endpoint and 'mcp' not in endpoint\n"
    )
    if protocol == "l4":
        assertions += "assert 'enforcement' not in endpoint and 'protocol' not in endpoint\n"
    path, config = trusted(tmp_path, extra=assertions)
    config["candidate"] = {"registration": REGISTRATION, "composition": "worker"}
    path.write_text(json.dumps(config))
    assert observe(root, path).status == "within_boundary"


def verify(root, *flags):
    result = CliRunner().invoke(app, ["verify", "--workspace", str(root), "--base", "main", "--json", *flags])
    assert result.exit_code in {0, 10, 20}, result.output
    return json.loads(result.output)


def test_required_absent_proof_routes_through_existing_insufficient_evidence(repo):
    result = verify(repo, "--openshell-proof-required")
    assert result["release_decision"]["decision"] == "insufficient_evidence"
    assert result["control"]["permissions"]["report_complete"] is False
    assert "--openshell-proof-required" in json.dumps(result["control"])


def test_default_verifier_never_executes_a_prover(repo, monkeypatch):
    monkeypatch.setattr("agents_shipgate.core.openshell_native._execute", lambda *args: pytest.fail("unexpected native execution"))
    verify(repo)
    plan = json.loads((repo / "agents-shipgate-reports/verification-plan.json").read_text())
    assert "openshell_native" not in plan["inputs"]["options"]


def test_invalid_selected_trust_configuration_cannot_complete_clean_verification(repo, tmp_path):
    path, _ = trusted(tmp_path)
    path.write_text('{"version":1,"required":true}')
    result = verify(repo, "--openshell-proof-config", str(path))
    assert result["control"]["permissions"]["report_complete"] is False
    assert result["release_decision"]["decision"] != "passed"


def test_optional_absent_proof_is_not_success_even_when_other_checks_complete(repo, tmp_path):
    missing = tmp_path / "not-supplied.json"
    result = verify(repo, "--openshell-proof-config", str(missing))
    evidence = json.loads((repo / "agents-shipgate-reports/openshell-native.json").read_text())
    assert evidence["observation"]["status"] == "absent"
    assert evidence["observation"]["provenance"] == "not_executed"
    assert result["release_decision"]["decision"] == "passed"
    missing.write_text("{}")
    with pytest.raises(CurrentControlUnavailable):
        read_current_control(repo / "agents-shipgate-reports", live=lambda: _live(repo))


def test_sensitive_trust_config_path_is_never_published_in_rerun_commands(repo, tmp_path):
    secret = "sk-live-0123456789abcdef0123456789abcdef"
    path = tmp_path / secret
    path.write_text("{}")
    result = CliRunner().invoke(app, ["verify", "--workspace", str(repo), "--base", "main", "--openshell-proof-config", str(path), "--json"])
    assert result.exit_code == 2
    assert secret not in result.output
    for artifact in (repo / "agents-shipgate-reports").glob("*"):
        if artifact.is_file():
            assert secret not in artifact.read_text()


@pytest.mark.parametrize("alias", ["dotdot", "symlink", "relative"])
def test_cli_rejects_trust_path_aliases_before_writing_any_artifact(repo, tmp_path, alias):
    path, _ = trusted(tmp_path)
    if alias == "dotdot":
        selected = path.parent / "unused" / ".." / path.name
    elif alias == "symlink":
        selected = path.parent / "alias.json"
        selected.symlink_to(path)
    else:
        selected = Path("relative-trust.json")
    result = CliRunner().invoke(app, ["verify", "--workspace", str(repo), "--base", "main", "--openshell-proof-config", str(selected), "--json"])
    assert result.exit_code == 2
    assert not (repo / "agents-shipgate-reports").exists()


@pytest.mark.parametrize("raw", ["[" * 1100 + "0" + "]" * 1100, "[" * 40 + "0" + "]" * 40, '{"version":NaN}'])
def test_malformed_native_json_is_a_bounded_invalid_observation(tmp_path, raw):
    root = tmp_path / "repo"
    root.mkdir()
    register(root)
    path, _ = trusted(tmp_path)
    path.write_text(raw)
    assert observe(root, path).status == "invalid"
    with pytest.raises(ValueError):
        validate_envelope(raw.encode(), 0)


def test_native_receipt_binds_inputs_and_external_executable_currency(repo, tmp_path):
    register(repo)
    path, config = trusted(tmp_path)
    result = verify(repo, "--openshell-proof-config", str(path))
    out = repo / "agents-shipgate-reports"
    evidence = json.loads((out / "openshell-native.json").read_text())
    assert evidence["observation"]["status"] == "within_boundary"
    assert evidence["subject"]["git"]["head_commit_sha"]
    manifest = json.loads((out / "verification-artifacts.json").read_text())
    assert "openshell_native_json" in manifest["artifacts"]
    read_current_control(out, live=lambda: _live(repo))
    Path(config["executable"]["path"]).write_text("changed executable")
    with pytest.raises(CurrentControlUnavailable):
        read_current_control(out, live=lambda: _live(repo))
    assert result["control"]["permissions"]["merge"] is False


def test_native_artifact_supports_unignored_output_reruns_and_is_cleared_by_default_verify(repo, tmp_path):
    from agents_shipgate.cli.current_workspace import live_workspace

    register(repo)
    path, _ = trusted(tmp_path)
    out = repo / "proof-output"
    for _ in range(2):
        verify(repo, "--openshell-proof-config", str(path), "--out", str(out))
        assert json.loads((out / "openshell-native.json").read_text())["observation"]["status"] == "within_boundary"
        read_current_control(out, live=lambda: live_workspace(repo, out))
    verify(repo, "--out", str(out))
    assert not (out / "openshell-native.json").exists()
    assert "openshell_native_json" not in json.loads((out / "verification-artifacts.json").read_text())["artifacts"]


def test_native_counterexample_words_do_not_change_finding_identity(repo, tmp_path):
    register(repo)
    path, _ = trusted(tmp_path, result="exceeds_boundary")
    first = verify(repo, "--openshell-proof-config", str(path))
    assert first["release_decision"]["decision"] == "blocked"
    report = json.loads((repo / "agents-shipgate-reports/report.json").read_text())
    old, = [row for row in report["findings"] if row["check_id"] == "SHIP-VERIFY-OPENSHELL-BOUNDARY-EXCEEDED"]
    path, _ = trusted(tmp_path, result="exceeds_boundary", mutate=lambda env: env["counterexample"].update(path="/different-wording"))
    verify(repo, "--openshell-proof-config", str(path))
    report = json.loads((repo / "agents-shipgate-reports/report.json").read_text())
    new, = [row for row in report["findings"] if row["check_id"] == old["check_id"]]
    assert old["fingerprint"] == new["fingerprint"]


def test_optional_missing_proof_is_absent_and_appearing_input_is_stale(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    missing = tmp_path / "missing.json"
    observation = observe(root, missing)
    assert observation.status == "absent" and observation.provenance == "not_executed"
    validate_external_currency(observation.model_dump(mode="json"), root)
    missing.write_text("{}")
    with pytest.raises(ValueError):
        validate_external_currency(observation.model_dump(mode="json"), root)


def test_valid_native_cancellation_is_distinct(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    register(root)
    def cancel(envelope):
        envelope.update(exit_code=130, reason_code="cancelled")
    path, _ = trusted(tmp_path, result="inconclusive", mutate=cancel)
    observed = observe(root, path)
    assert observed.status == "cancelled" and observed.actual_exit_code == 130


def test_pin_mismatch_never_executes_prover(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    register(root)
    path, config = trusted(tmp_path)
    Path(config["boundary"]["path"]).write_text(POLICY + "\n")
    monkeypatch.setattr("agents_shipgate.core.openshell_native._execute", lambda *args: pytest.fail("untrusted execution"))
    assert observe(root, path).status == "invalid"


def test_prover_gets_captured_inputs_and_clean_environment(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    register(root)
    monkeypatch.setenv("GITHUB_TOKEN", "fixture-secret")
    monkeypatch.setenv("PYTHONPATH", str(root))
    path, _ = trusted(tmp_path, extra="import os\nassert 'GITHUB_TOKEN' not in os.environ\nassert 'PYTHONPATH' not in os.environ\nassert open('candidate.yaml').read() == open('boundary.yaml').read()")
    assert observe(root, path).status == "within_boundary"


@pytest.mark.parametrize("alias", ["link", "hardlink", "writable"])
def test_trust_files_cannot_be_aliases_or_group_writable(tmp_path, alias):
    root = tmp_path / "repo"
    root.mkdir()
    register(root)
    path, config = trusted(tmp_path)
    boundary = Path(config["boundary"]["path"])
    if alias == "writable":
        boundary.chmod(0o666)
    else:
        replacement = boundary.with_name("alias")
        if alias == "link":
            replacement.symlink_to(boundary)
        else:
            replacement.hardlink_to(boundary)
        config["boundary"]["path"] = str(replacement)
        path.write_text(json.dumps(config))
    assert observe(root, path).status == "invalid"


def test_output_directory_never_overwrites_native_trust_inputs(repo, tmp_path):
    register(repo)
    path, config = trusted(tmp_path)
    boundary = Path(config["boundary"]["path"])
    original = boundary.read_bytes()
    result = CliRunner().invoke(app, ["verify", "--workspace", str(repo), "--base", "main",
        "--openshell-proof-config", str(path), "--out", str(boundary.parent)])
    assert result.exit_code == 2 and "overlap" in result.output.lower()
    assert boundary.read_bytes() == original


def test_worker_cannot_import_native_proof_plan(repo, tmp_path):
    register(repo)
    path, _ = trusted(tmp_path)
    verify(repo, "--openshell-proof-config", str(path))
    plan = repo / "agents-shipgate-reports/verification-plan.json"
    result = CliRunner().invoke(app, ["verification", "worker", "--workspace", str(repo), "--plan", str(plan)])
    assert result.exit_code != 0 and "cannot be imported" in str(result.exception)
