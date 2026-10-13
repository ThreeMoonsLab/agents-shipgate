"""The three receipt consumers, fed real ``verify`` output (#1022).

``attest``, ``org bundle`` and the GitHub Action's output step each gated
receipt validation on a version literal the engine had stopped emitting —
verifier ``0.5``–``0.7`` and ``shipgate.verify_run/v3`` — and every test fed
them synthetic dictionaries with no receipt beside them. So ``attest`` crashed
on every current verify output, ``org bundle`` published a forged receipt
identity, and the Action exported four empty identities and an unchecked
receipt path. These tests run ``verify`` on a git-initialised copy of
``samples/support_refund_agent`` and hand its directory to all three.
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import jsonschema
import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.schemas.verification_identity import (
    VERIFIER_SCHEMA_VERSIONS_BEFORE_RECEIPTS,
    requires_verification_receipt,
)
from agents_shipgate.schemas.verifier import VerifierArtifact
from agents_shipgate.schemas.verify_run import VERIFY_RUN_SCHEMA_VERSION
from scripts.github_action_outputs import extract_outputs

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE = REPO_ROOT / "samples" / "support_refund_agent"
ATTESTATION_SCHEMA = json.loads(
    (REPO_ROOT / "docs" / "attestation-schema.v0.5.json").read_text(encoding="utf-8")
)
CURRENT_VERIFIER = VerifierArtifact.model_fields["verifier_schema_version"].default
IDENTITY_FIELDS = ("request_id", "receipt_id", "decision_id", "artifact_set_id")
#: The identity fields verifier 0.5 added with receipts; no legacy verifier has one.
RECEIPT_ERA_FIELDS = (
    "request_id",
    "subject_id",
    "input_set_id",
    "engine_requirement_id",
    "executor_id",
    "decision_id",
)
RECEIPT = "verification-receipt.json"

runner = CliRunner()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def _repo(root: Path, *, sample: bool = True) -> Path:
    repo = root / "repo"
    if sample:
        shutil.copytree(SAMPLE, repo)
    else:
        repo.mkdir()
        (repo / ".mcp.json").write_text('{"mcpServers": {}}\n', encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    return repo


def _verify(repo: Path, out: Path, *, config: str = "shipgate.yaml") -> int:
    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            str(repo / config),
            "--ci-mode",
            "advisory",
            "--json",
            "--out",
            str(out),
        ],
    )
    assert (out / "verifier.json").is_file(), result.output
    return result.exit_code


@pytest.fixture(scope="module")
def verified(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """One real verify run on the sample, shared read-only by every test."""

    root = tmp_path_factory.mktemp("receipt-consumers")
    repo = _repo(root)
    out = root / "verify-out"
    assert _verify(repo, out) == 0
    return repo, out


@pytest.fixture
def run(verified: tuple[Path, Path], tmp_path: Path) -> tuple[Path, Path]:
    """A private copy of the verify output, free to tamper with."""

    repo, out = verified
    copy = tmp_path / "verify-out"
    shutil.copytree(out, copy)
    return repo, copy


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _attest(out: Path, target: Path) -> object:
    return runner.invoke(
        app, ["attest", "--from", str(out / "verifier.json"), "--out", str(target)]
    )


def _bundle(repo: Path, out: Path) -> object:
    return runner.invoke(
        app,
        [
            "org",
            "bundle",
            "--workspace",
            str(repo),
            "--config",
            str(repo / "shipgate.yaml"),
            "--from",
            str(out / "verifier.json"),
            "--json",
        ],
    )


def _tamper(out: Path, field: str) -> None:
    receipt = _json(out / RECEIPT)
    receipt[field] = "sha256:" + ("f" if field == "receipt_id" else "e") * 64
    (out / RECEIPT).write_text(json.dumps(receipt, indent=2, sort_keys=True), encoding="utf-8")


# --- the run itself is current output ---------------------------------------


def test_the_fixture_is_current_verify_output(verified: tuple[Path, Path]) -> None:
    _repo_path, out = verified
    verifier = _json(out / "verifier.json")

    assert verifier["verifier_schema_version"] == CURRENT_VERIFIER
    assert verifier["verifier_schema_version"] not in VERIFIER_SCHEMA_VERSIONS_BEFORE_RECEIPTS
    assert _json(out / "verify-run.json")["schema_version"] == VERIFY_RUN_SCHEMA_VERSION
    assert verifier["execution"] == "succeeded"
    assert requires_verification_receipt(verifier) is True
    assert (out / RECEIPT).is_file()


# --- attest -----------------------------------------------------------------


def test_attest_binds_the_receipt_of_a_real_verify_run(
    run: tuple[Path, Path], tmp_path: Path
) -> None:
    _repo_path, out = run
    target = tmp_path / "attestation.json"

    result = _attest(out, target)

    assert result.exit_code == 0, result.output
    attestation = _json(target)
    receipt = _json(out / RECEIPT)
    assert attestation["verification_receipt_sha256"] == (
        hashlib.sha256((out / RECEIPT).read_bytes()).hexdigest()
    )
    for field in IDENTITY_FIELDS:
        assert attestation[field] == receipt[field]
        assert attestation[field]
    jsonschema.validate(attestation, ATTESTATION_SCHEMA)


@pytest.mark.parametrize("field", ["receipt_id", "decision_id"])
def test_attest_refuses_a_tampered_receipt_in_one_line(
    run: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    _repo_path, out = run
    _tamper(out, field)
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    target = tmp_path / "attestation.json"

    result = _attest(out, target)

    assert result.exit_code == 3
    assert "Traceback" not in result.output
    assert not target.exists()
    message = next(
        line for line in result.output.splitlines() if line.startswith("Verification receipt")
    )
    assert message.startswith(f"Verification receipt error ({RECEIPT}): ")
    assert f"{field} must hash" in message
    agent_line = json.loads(
        [line for line in result.output.splitlines() if line.startswith("{")][-1]
    )
    assert agent_line["error"] == "input_parse_error"
    assert agent_line["exit_code"] == 3
    assert agent_line["message"] == message
    assert agent_line["next_action"]
    assert agent_line["next_actions"][0]["kind"] == "review"


# --- org bundle -------------------------------------------------------------


def test_org_bundle_publishes_the_validated_receipt_identity(run: tuple[Path, Path]) -> None:
    repo, out = run

    result = _bundle(repo, out)

    assert result.exit_code == 0, result.output
    bundle = json.loads(result.output)
    receipt = _json(out / RECEIPT)
    for field in IDENTITY_FIELDS:
        assert bundle[field] == receipt[field]
        assert bundle["attestation"][field] == receipt[field]
    assert bundle["attestation"]["verification_receipt_sha256"] == (
        hashlib.sha256((out / RECEIPT).read_bytes()).hexdigest()
    )
    assert bundle["artifacts"]["verification_receipt"]["sha256"]


def test_org_bundle_accepts_the_attestation_attest_wrote(run: tuple[Path, Path]) -> None:
    """The Action's order: ``attest`` beside the run, then ``org bundle``."""

    repo, out = run
    assert _attest(out, out / "attestation.json").exit_code == 0

    result = _bundle(repo, out)

    assert result.exit_code == 0, result.output
    bundle = json.loads(result.output)
    assert bundle["receipt_id"] == _json(out / RECEIPT)["receipt_id"]
    assert bundle["source_attestation"]


@pytest.mark.parametrize("field", ["receipt_id", "decision_id"])
def test_org_bundle_refuses_a_forged_receipt_identity(
    run: tuple[Path, Path], field: str
) -> None:
    repo, out = run
    _tamper(out, field)

    result = _bundle(repo, out)

    assert result.exit_code == 3
    assert "Traceback" not in result.output
    assert f"Verification receipt error ({RECEIPT}): " in result.output
    assert "sha256:" + "f" * 64 not in result.output
    assert "sha256:" + "e" * 64 not in result.output


def test_org_bundle_refuses_an_attestation_from_another_run(run: tuple[Path, Path]) -> None:
    """The identity cross-check that a version literal had left as dead code."""

    repo, out = run
    assert _attest(out, out / "attestation.json").exit_code == 0
    attestation = _json(out / "attestation.json")
    attestation["receipt_id"] = "sha256:" + "f" * 64
    (out / "attestation.json").write_text(json.dumps(attestation), encoding="utf-8")

    result = _bundle(repo, out)

    assert result.exit_code == 3
    assert "Attestation receipt_id does not match the terminal verification receipt" in (
        result.output
    )


# --- the GitHub Action's output step ----------------------------------------


def test_action_outputs_publish_the_validated_receipt_identity(run: tuple[Path, Path]) -> None:
    _repo_path, out = run

    outputs = extract_outputs(out)

    receipt = _json(out / RECEIPT)
    for field in IDENTITY_FIELDS:
        assert outputs[field] == receipt[field]
        assert outputs[field]
    assert outputs["verification_receipt_json"] == out / RECEIPT


@pytest.mark.parametrize("field", ["receipt_id", "decision_id"])
def test_action_outputs_fail_the_step_on_a_tampered_receipt(
    run: tuple[Path, Path], field: str
) -> None:
    _repo_path, out = run
    _tamper(out, field)

    with pytest.raises(ValueError, match=f"{field} must hash"):
        extract_outputs(out)


def test_action_outputs_fail_when_verifier_identity_disagrees_with_the_receipt(
    run: tuple[Path, Path],
) -> None:
    """A ``verifier.json`` that is not the one the receipt binds is caught."""

    _repo_path, out = run
    verifier = _json(out / "verifier.json")
    verifier["decision_id"] = "sha256:" + "e" * 64
    (out / "verifier.json").write_text(json.dumps(verifier, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="verifier_json"):
        extract_outputs(out)


# --- a missing receipt fails closed; a legacy verifier owes none ------------


def test_a_missing_receipt_fails_closed_in_all_three_consumers(
    run: tuple[Path, Path], tmp_path: Path
) -> None:
    repo, out = run
    (out / RECEIPT).unlink()

    attest = _attest(out, tmp_path / "attestation.json")
    assert attest.exit_code == 3
    assert f"{RECEIPT} is missing" in attest.output
    assert not (tmp_path / "attestation.json").exists()

    bundle = _bundle(repo, out)
    assert bundle.exit_code == 3
    assert f"{RECEIPT} is missing" in bundle.output

    with pytest.raises(ValueError, match=f"{RECEIPT} is missing"):
        extract_outputs(out)


@pytest.mark.parametrize("legacy", sorted(VERIFIER_SCHEMA_VERSIONS_BEFORE_RECEIPTS))
def test_a_verifier_from_before_receipts_owes_none(
    run: tuple[Path, Path], tmp_path: Path, legacy: str
) -> None:
    repo, out = run
    (out / RECEIPT).unlink()
    verifier = _json(out / "verifier.json")
    verifier["verifier_schema_version"] = legacy
    for field in RECEIPT_ERA_FIELDS:
        verifier.pop(field, None)
    (out / "verifier.json").write_text(json.dumps(verifier, indent=2), encoding="utf-8")

    if legacy == "0.4":
        # 0.1-0.3 are re-read through the verifier model, which this current
        # payload relabelled by hand does not claim to satisfy.
        attest = _attest(out, tmp_path / "attestation.json")
        assert attest.exit_code == 0, attest.output
        attestation = _json(tmp_path / "attestation.json")
        assert all(attestation[field] is None for field in IDENTITY_FIELDS)
        assert attestation["verification_receipt_sha256"] is None

        bundle = _bundle(repo, out)
        assert bundle.exit_code == 0, bundle.output
        assert json.loads(bundle.output)["receipt_id"] is None

    outputs = extract_outputs(out)
    assert all(outputs[field] == "" for field in IDENTITY_FIELDS)
    assert outputs["verification_receipt_json"] == ""


def test_a_relabelled_current_verifier_is_not_legacy(
    run: tuple[Path, Path], tmp_path: Path
) -> None:
    """A ``0.4`` label on a payload that carries receipt-era identities is not believed.

    Review C1-3: current output relabelled ``0.4``, claiming a pass, with its
    receipt deleted, was accepted by all three consumers.
    """

    repo, out = run
    (out / RECEIPT).unlink()
    verifier = _json(out / "verifier.json")
    verifier.update(
        {
            "verifier_schema_version": "0.4",
            "decision": "passed",
            "merge_verdict": "mergeable",
            "can_merge_without_human": True,
        }
    )
    (out / "verifier.json").write_text(json.dumps(verifier, indent=2), encoding="utf-8")

    attest = _attest(out, tmp_path / "attestation.json")
    assert attest.exit_code == 3
    assert f"{RECEIPT} is missing" in attest.output
    assert not (tmp_path / "attestation.json").exists()

    bundle = _bundle(repo, out)
    assert bundle.exit_code == 3
    assert f"{RECEIPT} is missing" in bundle.output

    with pytest.raises(ValueError, match=f"{RECEIPT} is missing"):
        extract_outputs(out)


def test_action_outputs_refuse_a_report_outcome_no_receipt_binds(
    run: tuple[Path, Path],
) -> None:
    """Review C1-1: the step publishes ``report.json``'s decision, so it owes the receipt.

    ``verifier.json`` stripped to a run that states nothing, the receipt
    deleted, and ``report.json`` saying ``passed``: the step published
    ``decision=passed`` and ``merge_verdict=mergeable`` with no receipt.
    """

    _repo_path, out = run
    verifier = _json(out / "verifier.json")
    verifier["execution"] = verifier["head_status"] = "not_run"
    for key in ("decision", "release_decision", "merge_verdict", "control"):
        verifier.pop(key, None)
    (out / "verifier.json").write_text(json.dumps(verifier, indent=2), encoding="utf-8")
    (out / RECEIPT).unlink()
    report = _json(out / "report.json")
    report["release_decision"]["decision"] = "passed"
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    assert requires_verification_receipt(verifier) is False

    with pytest.raises(ValueError, match=f"{RECEIPT} is missing"):
        extract_outputs(out)


# --- runs that state no outcome are not closed by a receipt -----------------


@pytest.mark.parametrize("route", ["host_only", "missing_manifest"])
def test_a_run_that_states_no_outcome_owes_no_receipt(tmp_path: Path, route: str) -> None:
    """The manifest-free host route and a failed run publish, they do not fail.

    The engine writes no receipt for either; failing closed on them would turn
    the host-only advisory workflow red on every pull request.
    """

    repo = _repo(tmp_path, sample=route == "missing_manifest")
    out = tmp_path / "verify-out"
    exit_code = _verify(
        repo, out, config="missing.yaml" if route == "missing_manifest" else "shipgate.yaml"
    )
    assert exit_code == (2 if route == "missing_manifest" else 0)
    verifier = _json(out / "verifier.json")
    assert verifier["verifier_schema_version"] == CURRENT_VERIFIER
    assert verifier["execution"] == ("failed" if route == "missing_manifest" else "not_run")
    assert not (out / RECEIPT).exists()
    assert not (out / "report.json").exists()
    assert requires_verification_receipt(verifier) is False

    outputs = extract_outputs(out)

    assert outputs["merge_verdict"] == "unknown"
    assert outputs["can_merge_without_human"] == "false"
    assert all(outputs[field] == "" for field in IDENTITY_FIELDS)
    assert outputs["verification_receipt_json"] == ""


def test_a_no_outcome_run_cannot_carry_a_forged_attestation_identity(
    tmp_path: Path,
) -> None:
    """With no receipt to check against, a bundle publishes no receipt identity."""

    repo = _repo(tmp_path)
    out = tmp_path / "verify-out"
    assert _verify(repo, out, config="missing.yaml") == 2
    assert _attest(out, out / "attestation.json").exit_code == 0
    attestation = _json(out / "attestation.json")
    assert all(attestation[field] is None for field in IDENTITY_FIELDS)
    # A complete forged identity: the attestation model alone accepts it.
    attestation.update({field: "sha256:" + "f" * 64 for field in IDENTITY_FIELDS})
    attestation["verification_receipt_sha256"] = "f" * 64
    (out / "attestation.json").write_text(json.dumps(attestation), encoding="utf-8")

    result = _bundle(repo, out)

    assert result.exit_code == 3
    assert "Attestation request_id names a verification receipt" in result.output


# --- the predicate ----------------------------------------------------------


_SUCCEEDED = {
    "execution": "succeeded",
    "head_status": "succeeded",
    "decision": "blocked",
    "merge_verdict": "blocked",
    "can_merge_without_human": False,
}


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("0.1", False),
        ("0.4", False),
        ("0.5", True),
        ("0.7", True),
        ("0.8", True),
        ("0.21", True),
        # A version newer than this build is never read as one that owes none.
        ("0.22", True),
        ("1.0", True),
        (None, True),
    ],
)
def test_the_predicate_names_only_the_versions_before_receipts(
    version: str | None, expected: bool
) -> None:
    payload = dict(_SUCCEEDED)
    if version is not None:
        payload["verifier_schema_version"] = version
    assert requires_verification_receipt(payload) is expected


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"execution": "not_run", "merge_verdict": "unknown"}, False),
        ({"execution": "failed", "merge_verdict": "unknown"}, False),
        ({"head_status": "failed", "merge_verdict": "unknown"}, False),
        ({"merge_verdict": "unknown", "can_merge_without_human": False}, False),
        ({}, False),
        # The engine closes these with a receipt.
        ({"execution": "skipped", "merge_verdict": "mergeable"}, True),
        ({"execution": "succeeded"}, True),
        ({"head_status": "succeeded"}, True),
        # A run that states any outcome owes one, whatever it calls its execution.
        ({"execution": "failed", "merge_verdict": "mergeable"}, True),
        ({"execution": "failed", "can_merge_without_human": True}, True),
        ({"execution": "not_run", "decision": "passed"}, True),
        ({"execution": "not_run", "release_decision": {"decision": "passed"}}, True),
        # Unhashable junk is read, not raised on.
        ({"execution": ["succeeded"], "verifier_schema_version": ["0.4"]}, False),
    ],
)
def test_the_predicate_reads_the_outcome_a_verifier_states(
    payload: dict, expected: bool
) -> None:
    payload = {"verifier_schema_version": CURRENT_VERIFIER, **payload}
    assert requires_verification_receipt(payload) is expected


@pytest.mark.parametrize("field", RECEIPT_ERA_FIELDS)
def test_a_legacy_label_on_a_receipt_era_payload_is_not_believed(field: str) -> None:
    legacy = {"verifier_schema_version": "0.4", **_SUCCEEDED}
    assert requires_verification_receipt(legacy) is False
    # 0.3 and 0.4 wrote `control` and `execution`, so those stay legacy.
    assert requires_verification_receipt({**legacy, "control": {"state": "complete"}}) is False
    # A field only receipt-era output carries, even null, is never legacy.
    assert requires_verification_receipt({**legacy, field: None}) is True
    # A relabelled run that states no outcome still owes nothing.
    assert requires_verification_receipt(
        {"verifier_schema_version": "0.4", "execution": "not_run", field: None}
    ) is False


@pytest.mark.parametrize(
    ("version", "report", "expected"),
    [
        (CURRENT_VERIFIER, None, False),
        (CURRENT_VERIFIER, {}, True),
        (CURRENT_VERIFIER, {"release_decision": {"decision": "passed"}}, True),
        ("0.22", {}, True),
        ("0.4", {"release_decision": {"decision": "passed"}}, False),
    ],
)
def test_a_report_beside_a_receipt_era_verifier_owes_a_receipt(
    version: str, report: dict | None, expected: bool
) -> None:
    no_outcome = {"verifier_schema_version": version, "execution": "not_run"}
    assert requires_verification_receipt(no_outcome, report_payload=report) is expected


_CONSUMERS = (
    "src/agents_shipgate/cli/attest.py",
    "src/agents_shipgate/cli/org.py",
    "scripts/github_action_outputs.py",
)


@pytest.mark.parametrize("path", _CONSUMERS)
def test_no_consumer_lists_receipt_era_versions(path: str) -> None:
    """Naming current versions is the defect; only legacy ones may be named."""

    source = (REPO_ROOT / path).read_text(encoding="utf-8")
    assert "shipgate.verify_run/v" not in source
    receipt_era = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Set | ast.Tuple | ast.List):
            for element in node.elts:
                if (
                    isinstance(element, ast.Constant)
                    and isinstance(element.value, str)
                    and re.fullmatch(r"0\.\d+", element.value)
                    and element.value not in VERIFIER_SCHEMA_VERSIONS_BEFORE_RECEIPTS
                ):
                    receipt_era.append(element.value)
    assert not receipt_era, f"{path} names receipt-era versions {receipt_era}"
    assert "requires_verification_receipt" in source or "load_verified_receipt" in source


# --- an Action pinned to an engine older than the predicate -----------------


def test_action_outputs_on_an_engine_without_the_predicate(
    run: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``shipgate_version`` may install an engine that predates the predicate.

    The step must still import, still validate a receipt that is present, and
    publish nothing unchecked when one is absent.
    """

    import agents_shipgate.schemas.verification_identity as identity
    import scripts.github_action_outputs as action_outputs

    _repo_path, out = run
    monkeypatch.delattr(identity, "requires_verification_receipt")
    try:
        older = importlib.reload(action_outputs)
        assert older.requires_verification_receipt(_json(out / "verifier.json")) is False

        assert older.extract_outputs(out)["receipt_id"] == _json(out / RECEIPT)["receipt_id"]
        _tamper(out, "receipt_id")
        with pytest.raises(ValueError, match="receipt_id must hash"):
            older.extract_outputs(out)
        (out / RECEIPT).unlink()
        outputs = older.extract_outputs(out)
        assert outputs["receipt_id"] == ""
        assert outputs["verification_receipt_json"] == ""
    finally:
        monkeypatch.undo()
        importlib.reload(action_outputs)
    assert action_outputs.requires_verification_receipt is requires_verification_receipt
