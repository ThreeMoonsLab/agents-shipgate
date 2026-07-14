from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.attest import build_attestation_payload
from agents_shipgate.cli.main import app
from agents_shipgate.config.loader import load_manifest
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.schemas.baseline import (
    BaselineFile,
    BaselineFinding,
    BaselineProvenance,
)

runner = CliRunner()


def _write_minimal_manifest(tmp_path: Path, extra: str = "") -> Path:
    path = tmp_path / "shipgate.yaml"
    path.write_text(
        """
version: "0.1"
project:
  name: org-test
agent:
  name: org-agent
  declared_purpose: ["test org governance"]
environment:
  target: staging
tool_sources:
  - id: api
    type: openapi
    path: openapi.yaml
"""
        + extra,
        encoding="utf-8",
    )
    return path


def test_manifest_organization_parses_and_validates_structure(tmp_path: Path) -> None:
    manifest_path = _write_minimal_manifest(
        tmp_path,
        """
organization:
  id: acme
  repo: github.com/acme/support-agent
  service: support-agent
  tier: production
  teams:
    agent-platform:
      reviewers: ["@acme/agent-platform"]
  exception_policy:
    max_age_days: 180
  audit:
    registry: .agents-shipgate/registry.jsonl
""",
    )

    manifest = load_manifest(manifest_path)

    assert manifest.organization is not None
    assert manifest.organization.id == "acme"
    assert manifest.organization.teams["agent-platform"].reviewers == ["@acme/agent-platform"]
    assert manifest.organization.exception_policy.max_age_days == 180


def test_manifest_organization_rejects_invalid_max_age(tmp_path: Path) -> None:
    manifest_path = _write_minimal_manifest(
        tmp_path,
        """
organization:
  id: acme
  exception_policy:
    max_age_days: 0
""",
    )

    with pytest.raises(ConfigError, match="max_age_days must be positive"):
        load_manifest(manifest_path)


def test_org_status_reports_verified_policy_pack_and_registry(tmp_path: Path) -> None:
    pack = tmp_path / "org-pack.yaml"
    pack.write_text(
        """
name: Org Pack
rules: []
""",
        encoding="utf-8",
    )
    digest = hashlib.sha256(pack.read_bytes()).hexdigest()
    _write_minimal_manifest(
        tmp_path,
        f"""
organization:
  id: acme
  audit:
    registry: .agents-shipgate/registry.jsonl
checks:
  policy_packs:
    - id: org-release
      path: org-pack.yaml
      source: github.com/acme/shipgate-policies@v3
      sha256: {digest}
""",
    )

    result = runner.invoke(
        app,
        [
            "org",
            "status",
            "--workspace",
            str(tmp_path),
            "--as-of",
            "2026-06-12",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["org_governance_schema_version"] == "0.1"
    assert payload["summary"]["policy_pack_count"] == 1
    assert payload["policy_packs"][0]["status"] == "verified"
    assert payload["policy_packs"][0]["source"] == "github.com/acme/shipgate-policies@v3"
    assert payload["registry"] == {
        "path": ".agents-shipgate/registry.jsonl",
        "exists": False,
    }
    assert payload["violations"] == []


def test_org_status_exit_20_for_unpinned_sourced_policy_pack(tmp_path: Path) -> None:
    (tmp_path / "org-pack.yaml").write_text("name: Org Pack\nrules: []\n", encoding="utf-8")
    _write_minimal_manifest(
        tmp_path,
        """
organization:
  id: acme
checks:
  policy_packs:
    - path: org-pack.yaml
      source: github.com/acme/shipgate-policies@v3
""",
    )

    result = runner.invoke(
        app,
        [
            "org",
            "status",
            "--workspace",
            str(tmp_path),
            "--as-of",
            "2026-06-12",
            "--json",
        ],
    )

    assert result.exit_code == 20, result.output
    payload = json.loads(result.output)
    assert payload["policy_packs"][0]["violations"] == ["policy_pack_unpinned"]
    assert payload["violations"][0]["kind"] == "policy_pack_unpinned"


def test_org_status_normalizes_exception_records_and_expiry(tmp_path: Path) -> None:
    agents_dir = tmp_path / ".agents-shipgate"
    agents_dir.mkdir()
    baseline = BaselineFile(
        created_at="2026-01-01T00:00:00Z",
        source_report_run_id="run-1",
        findings=[
            BaselineFinding(
                fingerprint="fp_expired",
                check_id="SHIP-TEST",
                tool_name="refund",
                severity="high",
                title="Expired accepted debt",
                provenance=BaselineProvenance(
                    scanner_version="0.13.0",
                    run_id="run-1",
                    recorded_at="2026-01-01T00:00:00Z",
                    owner="alice",
                    reason="temporary acceptance",
                    expires=date(2026, 1, 31),
                ),
            )
        ],
    )
    (agents_dir / "baseline.json").write_text(
        baseline.model_dump_json(indent=2, exclude_none=False) + "\n",
        encoding="utf-8",
    )
    _write_minimal_manifest(
        tmp_path,
        """
organization:
  id: acme
  exception_policy:
    require_owner: true
    require_reason: true
    require_expiry: true
    max_age_days: 180
checks:
  ignore:
    - check_id: SHIP-IGNORED
      tool: legacy_refund
      owner: bob
      reason: deprecated endpoint
      expires: 2026-12-31
  severity_overrides:
    SHIP-OVERRIDE:
      severity: medium
      owner: carol
      reason: internal-only workflow
      expires: 2026-12-31
  acknowledge_overrides:
    - check_id: SHIP-OVERRIDE
      owner: carol
      reason: reviewed override
      expires: 2026-12-31
human_ack:
  - affected_surface: policy
    owner: dave
    reason: approved by security
    expires: "2026-12-31"
""",
    )

    result = runner.invoke(
        app,
        [
            "org",
            "status",
            "--workspace",
            str(tmp_path),
            "--as-of",
            "2026-06-12",
            "--json",
        ],
    )

    assert result.exit_code == 20, result.output
    payload = json.loads(result.output)
    by_kind = {record["kind"]: record for record in payload["exceptions"]}
    assert set(by_kind) == {
        "baseline",
        "human_ack",
        "override_acknowledgement",
        "severity_override",
        "suppression",
    }
    assert by_kind["suppression"]["owner"] == "bob"
    assert by_kind["severity_override"]["owner"] == "carol"
    assert by_kind["override_acknowledgement"]["owner"] == "carol"
    assert by_kind["baseline"]["violations"] == ["expired"]
    assert payload["violations"] == [
        {
            "kind": "expired",
            "source": ".agents-shipgate/baseline.json",
            "record_kind": "baseline",
            "subject": "refund",
        }
    ]


def test_org_status_surfaces_host_grant_drift(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"github": {"command": "npx", "args": ["server"]}}}),
        encoding="utf-8",
    )
    save = runner.invoke(
        app,
        ["audit", "--host", "--workspace", str(tmp_path), "--save-baseline", "--json"],
    )
    assert save.exit_code == 0, save.output
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "github": {"command": "npx", "args": ["server"]},
                    "payments": {"url": "https://mcp.payments.test/sse"},
                }
            }
        ),
        encoding="utf-8",
    )
    _write_minimal_manifest(
        tmp_path,
        """
organization:
  id: acme
""",
    )

    result = runner.invoke(
        app,
        [
            "org",
            "status",
            "--workspace",
            str(tmp_path),
            "--as-of",
            "2026-06-12",
            "--json",
        ],
    )

    assert result.exit_code == 20, result.output
    payload = json.loads(result.output)
    assert payload["summary"]["host_grant_drift"] is True
    assert payload["host_grant_drift"]["has_drift"] is True
    assert payload["violations"] == [
        {
            "kind": "host_grant_drift",
            "source": ".agents-shipgate/host-grants.json",
            "record_kind": "host_grants",
            "subject": "host_grants",
        }
    ]


def test_org_status_treats_incomparable_host_baseline_as_violation(
    tmp_path: Path,
) -> None:
    baseline = tmp_path / ".agents-shipgate" / "host-grants.json"
    baseline.parent.mkdir(parents=True)
    baseline.write_text(
        json.dumps(
            {
                "host_grants_schema_version": "0.1",
                "inventory_sha256": "legacy",
                "inventory": {"mcp_servers": []},
            }
        ),
        encoding="utf-8",
    )
    _write_minimal_manifest(tmp_path, "\norganization:\n  id: acme\n")

    result = runner.invoke(
        app,
        [
            "org",
            "status",
            "--workspace",
            str(tmp_path),
            "--as-of",
            "2026-06-12",
            "--json",
        ],
    )

    assert result.exit_code == 20, result.output
    payload = json.loads(result.output)
    assert payload["summary"]["host_grant_drift"] is True
    assert payload["host_grant_drift"]["comparison_status"] == "incomparable"
    assert payload["host_grant_drift"]["has_drift"] is None
    assert payload["violations"] == [
        {
            "kind": "host_grant_drift",
            "source": ".agents-shipgate/host-grants.json",
            "record_kind": "host_grants",
            "subject": "host_grants",
        }
    ]


def test_org_policy_packs_command_reports_rule_counts_and_owner(tmp_path: Path) -> None:
    pack = tmp_path / "org-pack.yaml"
    pack.write_text(
        """
name: Org Pack
version: "3"
owner: agent-platform
rules:
  - id: ORG-READINESS
    title: Require readiness
    category: org_policy
    severity: high
    recommendation: add controls
    match:
      risk_tags: [external_side_effects]
""",
        encoding="utf-8",
    )
    digest = hashlib.sha256(pack.read_bytes()).hexdigest()
    _write_minimal_manifest(
        tmp_path,
        f"""
organization:
  id: acme
checks:
  policy_packs:
    - id: org-release
      path: org-pack.yaml
      source: github.com/acme/shipgate-policies@v3
      sha256: {digest}
""",
    )

    result = runner.invoke(
        app,
        [
            "org",
            "policy-packs",
            "--workspace",
            str(tmp_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["org_governance_schema_version"] == "0.1"
    assert payload["policy_pack_count"] == 1
    [record] = payload["policy_packs"]
    assert record["id"] == "org-release"
    assert record["name"] == "Org Pack"
    assert record["version"] == "3"
    assert record["owner"] == "agent-platform"
    assert record["rule_count"] == 1
    assert record["status"] == "verified"


def test_org_bundle_projects_platform_artifacts_without_second_gate(
    tmp_path: Path,
) -> None:
    (tmp_path / "openapi.yaml").write_text("openapi: 3.0.0\ninfo: {}\npaths: {}\n")
    pack = tmp_path / "org-pack.yaml"
    pack.write_text("name: Org Pack\nowner: platform\nrules: []\n", encoding="utf-8")
    digest = hashlib.sha256(pack.read_bytes()).hexdigest()
    manifest = _write_minimal_manifest(
        tmp_path,
        f"""
organization:
  id: acme
  repo: org/support
  service: support-agent
  tier: production
checks:
  policy_packs:
    - id: org-release
      path: org-pack.yaml
      source: github.com/acme/shipgate-policies@v3
      sha256: {digest}
""",
    )
    reports = tmp_path / "agents-shipgate-reports"
    reports.mkdir()
    _write_json(
        reports / "report.json",
        {
            "release_decision": {
                "decision": "blocked",
                "blockers": [{"id": "F1"}],
                "review_items": [],
            },
            "human_ack": {
                "required": True,
                "satisfied": False,
                "outstanding": ["policy"],
                "acks": [],
            },
            "effective_policy": {"ci_mode": "advisory"},
            "privacy_audit": {"enabled": True, "redacted_occurrence_count": 0},
        },
    )
    _write_json(
        reports / "verify-run.json",
        {
            "run_id": "sha256:" + "a" * 64,
            "inputs": {
                "policy_packs": [
                    {
                        "id": "org-release",
                        "path": "org-pack.yaml",
                        "sha256": digest,
                        "rule_count": 0,
                    }
                ]
            },
        },
    )
    _write_json(
        reports / "verifier.json",
        {
            "base_ref": "origin/main",
            "head_ref": "HEAD",
            "mode": "advisory",
            "merge_verdict": "blocked",
            "decision": "blocked",
            "applicability": "verified",
            "can_merge_without_human": False,
            "release_decision": {"decision": "blocked"},
            "human_review": {"required": True},
            "capability_review": {
                "added": 1,
                "modified": 0,
                "removed": 0,
                "trust_root_touched": True,
                "policy_weakened": False,
                "top_changes": [{"id": "cap_refund"}],
            },
            "artifacts": {
                "verifier_json": "verifier.json",
                "report_json": "report.json",
                "verify_run_json": "verify-run.json",
            },
        },
    )
    verifier_payload = json.loads((reports / "verifier.json").read_text(encoding="utf-8"))
    report_payload = json.loads((reports / "report.json").read_text(encoding="utf-8"))
    verify_run_payload = json.loads((reports / "verify-run.json").read_text(encoding="utf-8"))
    attestation_payload = build_attestation_payload(
        verifier_payload,
        source=reports / "verifier.json",
        redacted=True,
        report=report_payload,
        verify_run=verify_run_payload,
        verify_run_sha256=hashlib.sha256((reports / "verify-run.json").read_bytes()).hexdigest(),
        org_context={
            "org_id": "acme",
            "repo": "org/support",
            "service": "support-agent",
            "tier": "production",
        },
    )
    attestation_rendered = (
        json.dumps(
            attestation_payload,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    (reports / "attestation.json").write_text(attestation_rendered, encoding="utf-8")
    attestation_sha256 = hashlib.sha256(attestation_rendered.encode("utf-8")).hexdigest()
    out = reports / "org-evidence-bundle.json"

    result = runner.invoke(
        app,
        [
            "org",
            "bundle",
            "--workspace",
            str(tmp_path),
            "--config",
            str(manifest),
            "--from",
            str(reports / "verifier.json"),
            "--out",
            str(out),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload == json.loads(out.read_text(encoding="utf-8"))
    assert payload["org_evidence_bundle_schema_version"] == ("shipgate.org_evidence_bundle/v2")
    assert payload["gating_signal"] == "release_decision.decision"
    assert payload["attestation"]["run_id"] == "sha256:" + "a" * 64
    assert payload["registry_row"]["repo"] == "org/support"
    assert payload["registry_row"]["run_id"] == "sha256:" + "a" * 64
    assert payload["registry_row"]["source_attestation_sha256"] == attestation_sha256
    assert payload["org_status"]["summary"]["policy_pack_count"] == 1
    assert payload["policy_packs"][0]["status"] == "verified"
    assert payload["host_grants"]["host_grants_inventory_schema_version"] == "0.2"
    assert payload["artifacts"]["verifier"]["sha256"]


def test_org_bundle_accepts_v03_attestation_file(tmp_path: Path) -> None:
    (tmp_path / "openapi.yaml").write_text("openapi: 3.0.0\ninfo: {}\npaths: {}\n")
    manifest = _write_minimal_manifest(
        tmp_path,
        """
organization:
  id: acme
  repo: org/support
""",
    )
    reports = tmp_path / "agents-shipgate-reports"
    reports.mkdir()
    _write_json(
        reports / "report.json",
        {"release_decision": {"decision": "passed"}, "human_ack": {}},
    )
    _write_json(
        reports / "verifier.json",
        {
            "base_ref": "origin/main",
            "head_ref": "HEAD",
            "mode": "advisory",
            "merge_verdict": "mergeable",
            "decision": "passed",
            "applicability": "verified",
            "can_merge_without_human": True,
            "release_decision": {"decision": "passed"},
            "human_review": {"required": False},
            "capability_review": {
                "added": 0,
                "modified": 0,
                "removed": 0,
                "trust_root_touched": False,
                "policy_weakened": False,
                "top_changes": [],
            },
            "artifacts": {
                "verifier_json": "verifier.json",
                "report_json": "report.json",
            },
        },
    )
    v03_attestation = {
        "attestation_schema_version": "0.3",
        "cli_version": "0.14.0",
        "org": {
            "org_id": "acme",
            "repo": "org/support",
            "service": None,
            "tier": None,
            "pr_number": None,
            "workflow_run_id": None,
            "actor": None,
            "merge_sha": None,
        },
        "source_verifier": "verifier.json",
        "redacted": True,
        "base_ref": "origin/main",
        "head_ref": "HEAD",
        "base_tree_sha": None,
        "head_tree_sha": None,
        "mode": "advisory",
        "verdict": {
            "merge_verdict": "mergeable",
            "decision": "passed",
            "applicability": "verified",
            "can_merge_without_human": True,
        },
        "capability": {
            "added": 0,
            "modified": 0,
            "removed": 0,
            "trust_root_touched": False,
            "policy_weakened": False,
            "change_ids": [],
        },
        "capability_lock": {
            "path": None,
            "sha256": None,
            "capability_lock_schema_version": None,
            "semantic_capability_set_hash": None,
            "evidence_set_hash": None,
            "source_set_hash": None,
            "capability_count": None,
        },
        "capability_diff": None,
        "human_ack": {
            "required": False,
            "satisfied": None,
            "outstanding": [],
            "acks": [],
        },
        "policy_snapshot_sha256": None,
        "artifact_sha256": {},
    }
    _write_json(reports / "attestation.json", v03_attestation)

    result = runner.invoke(
        app,
        [
            "org",
            "bundle",
            "--workspace",
            str(tmp_path),
            "--config",
            str(manifest),
            "--from",
            str(reports / "verifier.json"),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["attestation"]["attestation_schema_version"] == "0.5"
    assert payload["attestation"]["run_id"] is None
    assert payload["attestation"]["policy_packs"] == []


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
