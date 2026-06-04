from __future__ import annotations

import hashlib
import json
from pathlib import Path

import jsonschema
import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.attest import build_attestation_payload
from agents_shipgate.cli.main import app

runner = CliRunner()
REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads(
    (REPO_ROOT / "docs/attestation-schema.v0.1.json").read_text(encoding="utf-8")
)


def _verifier_payload() -> dict:
    return {
        "base_ref": "origin/main",
        "head_ref": "HEAD",
        "base_tree_sha": "7d4d5f5e125a9bbcf16d4091fa0038bd229e1c7c",
        "head_tree_sha": "a1b2c3d4e5f6071829abcdef0123456789abcdef",
        "mode": "advisory",
        "merge_verdict": "blocked",
        "applicability": "verified",
        "can_merge_without_human": False,
        "decision": "blocked",
        "release_decision": {"decision": "blocked"},
        "human_review": {"required": True, "why": "Approval evidence is missing."},
        "capability_review": {
            "added": 1,
            "modified": 0,
            "removed": 0,
            "trust_root_touched": True,
            "policy_weakened": False,
            # Intentionally out of order to prove change_ids is sorted.
            "top_changes": [{"id": "cap_b"}, {"id": "cap_a"}, {"id": "cap_a"}],
        },
        "artifacts": {
            "verifier_json": "agents-shipgate-reports/verifier.json",
            "report_json": "agents-shipgate-reports/report.json",
            "pr_comment": "agents-shipgate-reports/pr-comment.md",
        },
    }


def _report_payload() -> dict:
    return {
        "human_ack": {
            "required": True,
            "satisfied": False,
            "acks": [],
            "outstanding": ["shipgate.yaml (manifest)"],
        },
        "effective_policy": {
            "ci_mode": "advisory",
            "fail_on": ["critical"],
            "suppressed_check_ids": [],
        },
    }


# --- core projection --------------------------------------------------------


def test_build_attestation_core_fields() -> None:
    att = build_attestation_payload(
        _verifier_payload(),
        source=Path("agents-shipgate-reports/verifier.json"),
        redacted=True,
        report=_report_payload(),
    )
    assert att["attestation_schema_version"] == "0.1"
    assert att["base_tree_sha"] == "7d4d5f5e125a9bbcf16d4091fa0038bd229e1c7c"
    assert att["head_tree_sha"] == "a1b2c3d4e5f6071829abcdef0123456789abcdef"
    assert att["verdict"] == {
        "merge_verdict": "blocked",
        "decision": "blocked",
        "applicability": "verified",
        "can_merge_without_human": False,
    }
    assert att["capability"]["added"] == 1
    assert att["capability"]["trust_root_touched"] is True
    assert att["capability"]["change_ids"] == ["cap_a", "cap_b"]  # sorted, de-duped


def test_human_ack_and_policy_hash_from_report() -> None:
    report = _report_payload()
    att = build_attestation_payload(
        _verifier_payload(),
        source=Path("verifier.json"),
        redacted=True,
        report=report,
    )
    assert att["human_ack"] == {
        "required": True,
        "satisfied": False,
        "outstanding": ["shipgate.yaml (manifest)"],
    }
    expected = hashlib.sha256(
        json.dumps(
            report["effective_policy"], sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    assert att["policy_snapshot_sha256"] == expected


def test_no_report_degrades_gracefully() -> None:
    att = build_attestation_payload(
        _verifier_payload(),
        source=Path("verifier.json"),
        redacted=True,
        report=None,
    )
    # required falls back to verifier.human_review.required; satisfied unknown.
    assert att["human_ack"]["required"] is True
    assert att["human_ack"]["satisfied"] is None
    assert att["human_ack"]["outstanding"] == []
    assert att["policy_snapshot_sha256"] is None


def test_attestation_is_deterministic() -> None:
    args = dict(source=Path("verifier.json"), redacted=True, report=_report_payload())
    first = build_attestation_payload(_verifier_payload(), **args)
    second = build_attestation_payload(_verifier_payload(), **args)
    assert first == second
    # No wall-clock fields leak in: rendered form is byte-stable.
    render = lambda d: json.dumps(d, sort_keys=True, indent=2)  # noqa: E731
    assert render(first) == render(second)


def test_redaction_controls_source_path() -> None:
    redacted = build_attestation_payload(
        _verifier_payload(),
        source=Path("/Users/alice/Secret Client/verifier.json"),
        redacted=True,
    )
    assert redacted["source_verifier"] == "verifier.json"
    assert "Secret Client" not in json.dumps(redacted)

    full = build_attestation_payload(
        _verifier_payload(),
        source=Path("/tmp/shipgate/verifier.json"),
        redacted=False,
    )
    assert full["source_verifier"] == "/tmp/shipgate/verifier.json"


def test_artifact_hashes_resolve_siblings_of_verifier(tmp_path: Path) -> None:
    (tmp_path / "report.json").write_text("{}", encoding="utf-8")
    (tmp_path / "pr-comment.md").write_text("hello", encoding="utf-8")
    verifier = _verifier_payload()
    # Absolute, elsewhere-rooted artifact paths: only the basename is used.
    verifier["artifacts"] = {
        "report_json": "/somewhere/else/report.json",
        "pr_comment": "/somewhere/else/pr-comment.md",
        "missing": "/somewhere/else/nope.json",
    }
    att = build_attestation_payload(
        verifier, source=tmp_path / "verifier.json", redacted=True
    )
    assert att["artifact_sha256"]["pr_comment"] == hashlib.sha256(b"hello").hexdigest()
    assert att["artifact_sha256"]["report_json"] == hashlib.sha256(b"{}").hexdigest()
    assert "missing" not in att["artifact_sha256"]  # absent files are skipped


@pytest.mark.parametrize("report", [None, _report_payload()])
def test_attestation_validates_against_schema(report) -> None:
    att = build_attestation_payload(
        _verifier_payload(),
        source=Path("agents-shipgate-reports/verifier.json"),
        redacted=True,
        report=report,
    )
    jsonschema.validate(att, SCHEMA)


# --- CLI --------------------------------------------------------------------


def test_attest_cli_writes_json_and_enriches_from_sibling_report(tmp_path: Path) -> None:
    (tmp_path / "verifier.json").write_text(
        json.dumps(_verifier_payload()), encoding="utf-8"
    )
    (tmp_path / "report.json").write_text(
        json.dumps(_report_payload()), encoding="utf-8"
    )
    out = tmp_path / "attestation.json"

    result = runner.invoke(
        app,
        ["attest", "--from", str(tmp_path / "verifier.json"), "--out", str(out), "--json"],
    )

    assert result.exit_code == 0, result.output
    disk = json.loads(out.read_text(encoding="utf-8"))
    assert disk == json.loads(result.output)
    assert disk["verdict"]["merge_verdict"] == "blocked"
    # Sibling report.json was found and enriched the attestation.
    assert disk["human_ack"]["outstanding"] == ["shipgate.yaml (manifest)"]
    assert disk["policy_snapshot_sha256"] is not None
    jsonschema.validate(disk, SCHEMA)


def test_attest_cli_out_without_json_prints_written_message(tmp_path: Path) -> None:
    (tmp_path / "verifier.json").write_text(
        json.dumps(_verifier_payload()), encoding="utf-8"
    )
    out = tmp_path / "attestation.json"
    result = runner.invoke(
        app, ["attest", "--from", str(tmp_path / "verifier.json"), "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert result.output.strip() == f"Wrote attestation to {out}"


@pytest.mark.parametrize(
    ("contents", "expected"),
    [("", "not valid JSON"), ("[]", "must contain an object")],
)
def test_attest_cli_invalid_verifier_returns_3(tmp_path, contents, expected) -> None:
    (tmp_path / "verifier.json").write_text(contents, encoding="utf-8")
    result = runner.invoke(app, ["attest", "--from", str(tmp_path / "verifier.json")])
    assert result.exit_code == 3
    assert expected in result.output


def test_attest_cli_missing_verifier_returns_3(tmp_path: Path) -> None:
    result = runner.invoke(app, ["attest", "--from", str(tmp_path / "missing.json")])
    assert result.exit_code == 3
    assert "not found" in result.output
