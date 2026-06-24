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
    (REPO_ROOT / "docs/attestation-schema.v0.4.json").read_text(encoding="utf-8")
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
            "capability_lock": "agents-shipgate-reports/capabilities.lock.json",
            "capability_lock_diff_json": (
                "agents-shipgate-reports/capability-lock-diff.json"
            ),
        },
    }


def _report_payload() -> dict:
    return {
        "human_ack": {
            "required": True,
            "satisfied": False,
            "acks": [
                {
                    "owner": "alice",
                    "reason": "security reviewed",
                    "affected_surface": "shipgate.yaml",
                    "expires": "2026-12-31",
                    "source": "shipgate.yaml#/human_ack/0",
                }
            ],
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
        source=Path("verifier.json"),
        redacted=True,
        report=_report_payload(),
    )
    assert att["attestation_schema_version"] == "0.4"
    assert att["run_id"] is None
    assert att["verify_run_sha256"] is None
    assert att["event_time"] is None
    assert att["org"] == {
        "org_id": None,
        "repo": None,
        "service": None,
        "tier": None,
        "pr_number": None,
        "workflow_run_id": None,
        "actor": None,
        "merge_sha": None,
    }
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
    assert att["capability_lock"]["path"] is None
    assert att["capability_diff"] is None
    assert att["policy_packs"] == []


def test_verify_run_context_is_explicit_and_deterministic() -> None:
    verify_run = {
        "run_id": "sha256:" + "a" * 64,
        "inputs": {
            "policy_packs": [
                {
                    "id": "org",
                    "name": "Org Release",
                    "version": "3",
                    "path": "vendor/org.yaml",
                    "sha256": "sha256:" + "b" * 64,
                    "sha256_status": "verified",
                    "rule_count": 2,
                }
            ]
        },
    }
    att = build_attestation_payload(
        _verifier_payload(),
        source=Path("verifier.json"),
        redacted=True,
        report=_report_payload(),
        verify_run=verify_run,
        verify_run_sha256="c" * 64,
        run_context={
            "event_time": "2026-06-21T12:00:00Z",
            "source_url": "https://github.com/acme/repo/pull/42",
            "branch": "feature/agent",
            "base_sha": "d" * 40,
            "head_sha": "e" * 40,
        },
    )

    assert att["run_id"] == "sha256:" + "a" * 64
    assert att["verify_run_sha256"] == "c" * 64
    assert att["event_time"] == "2026-06-21T12:00:00Z"
    assert att["source_url"] == "https://github.com/acme/repo/pull/42"
    assert att["branch"] == "feature/agent"
    assert att["base_sha"] == "d" * 40
    assert att["head_sha"] == "e" * 40
    assert att["policy_packs"] == [
        {
            "id": "org",
            "name": "Org Release",
            "version": "3",
            "path": "vendor/org.yaml",
            "sha256": "sha256:" + "b" * 64,
            "status": "verified",
            "rule_count": 2,
        }
    ]
    jsonschema.validate(att, SCHEMA)


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
        "acks": [
            {
                "owner": "alice",
                "reason": "security reviewed",
                "affected_surface": "shipgate.yaml",
                "expires": "2026-12-31",
                "source": "shipgate.yaml#/human_ack/0",
            }
        ],
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
    assert att["human_ack"]["acks"] == []
    assert att["policy_snapshot_sha256"] is None


def test_org_context_is_deterministic_and_explicit_flags_win() -> None:
    att = build_attestation_payload(
        _verifier_payload(),
        source=Path("verifier.json"),
        redacted=True,
        report=_report_payload(),
        org_context={
            "org_id": "acme",
            "repo": "github.com/acme/support-agent",
            "service": "support-agent",
            "tier": "production",
            "actor": "octocat",
        },
    )
    assert att["org"] == {
        "org_id": "acme",
        "repo": "github.com/acme/support-agent",
        "service": "support-agent",
        "tier": "production",
        "pr_number": None,
        "workflow_run_id": None,
        "actor": "octocat",
        "merge_sha": None,
    }


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


def test_capability_artifact_bindings_resolve_siblings(tmp_path: Path) -> None:
    lock_payload = {
        "capability_lock_schema_version": "0.2",
        "summary": {"capability_count": 2},
        "hashes": {
            "semantic_capability_set_hash": "sem_head",
            "evidence_set_hash": "ev_head",
            "source_set_hash": "src_head",
        },
    }
    diff_payload = {
        "capability_lock_diff_schema_version": "0.3",
        "base": {"semantic_capability_set_hash": "sem_base"},
        "head": {"semantic_capability_set_hash": "sem_head"},
        "summary": {
            "added": 1,
            "removed": 0,
            "reidentified": 0,
            "changed": 0,
            "evidence_changed": 0,
            "unchanged": 1,
        },
    }
    lock_text = json.dumps(lock_payload, sort_keys=True)
    diff_text = json.dumps(diff_payload, sort_keys=True)
    (tmp_path / "capabilities.lock.json").write_text(lock_text, encoding="utf-8")
    (tmp_path / "capability-lock-diff.json").write_text(
        diff_text,
        encoding="utf-8",
    )

    att = build_attestation_payload(
        _verifier_payload(),
        source=tmp_path / "verifier.json",
        redacted=True,
    )

    assert att["capability_lock"] == {
        "path": "capabilities.lock.json",
        "sha256": hashlib.sha256(lock_text.encode("utf-8")).hexdigest(),
        "capability_lock_schema_version": "0.2",
        "semantic_capability_set_hash": "sem_head",
        "evidence_set_hash": "ev_head",
        "source_set_hash": "src_head",
        "capability_count": 2,
    }
    assert att["capability_diff"]["path"] == "capability-lock-diff.json"
    assert att["capability_diff"]["sha256"] == hashlib.sha256(
        diff_text.encode("utf-8")
    ).hexdigest()
    assert att["capability_diff"]["capability_lock_diff_schema_version"] == "0.3"
    assert att["capability_diff"]["base_semantic_capability_set_hash"] == "sem_base"
    assert att["capability_diff"]["head_semantic_capability_set_hash"] == "sem_head"
    assert att["capability_diff"]["summary"]["added"] == 1
    jsonschema.validate(att, SCHEMA)


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


def test_attest_cli_reads_org_config_and_github_actions_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "verifier.json").write_text(
        json.dumps(_verifier_payload()), encoding="utf-8"
    )
    (tmp_path / "report.json").write_text(
        json.dumps(_report_payload()), encoding="utf-8"
    )
    (tmp_path / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: test
agent:
  name: test-agent
  declared_purpose: ["test"]
environment:
  target: staging
organization:
  id: acme
  repo: github.com/acme/from-config
  service: support-agent
  tier: production
tool_sources:
  - id: api
    type: openapi
    path: openapi.yaml
""",
        encoding="utf-8",
    )
    event = tmp_path / "event.json"
    event.write_text(
        json.dumps(
            {
                "pull_request": {
                    "number": 42,
                    "updated_at": "2026-06-21T12:00:00Z",
                    "html_url": "https://github.com/acme/from-env/pull/42",
                    "base": {"sha": "a" * 40},
                    "head": {"sha": "b" * 40, "ref": "feature/support"},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/from-env")
    monkeypatch.setenv("GITHUB_RUN_ID", "9001")
    monkeypatch.setenv("GITHUB_ACTOR", "octocat")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_SHA", "f" * 40)
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))

    result = runner.invoke(
        app,
        [
            "attest",
            "--from",
            str(tmp_path / "verifier.json"),
            "--config",
            str(tmp_path / "shipgate.yaml"),
            "--repo",
            "acme/explicit",
            "--ci-context",
            "github-actions",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["org"] == {
        "org_id": "acme",
        "repo": "acme/explicit",
        "service": "support-agent",
        "tier": "production",
        "pr_number": "42",
        "workflow_run_id": "9001",
        "actor": "octocat",
        "merge_sha": "f" * 40,
    }
    assert payload["event_time"] == "2026-06-21T12:00:00Z"
    assert payload["source_url"] == "https://github.com/acme/from-env/pull/42"
    assert payload["branch"] == "feature/support"
    assert payload["base_sha"] == "a" * 40
    assert payload["head_sha"] == "b" * 40


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
