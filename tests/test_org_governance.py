from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

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
    assert manifest.organization.teams["agent-platform"].reviewers == [
        "@acme/agent-platform"
    ]
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
    (tmp_path / "org-pack.yaml").write_text(
        "name: Org Pack\nrules: []\n", encoding="utf-8"
    )
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
