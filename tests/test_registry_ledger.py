from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agents_shipgate.cli.main import app

runner = CliRunner()


def _attestation(verdict: str = "blocked", change_ids: list[str] | None = None) -> dict:
    return {
        "attestation_schema_version": "0.1",
        "cli_version": "0.13.0",
        "source_verifier": "agents-shipgate-reports/verifier.json",
        "redacted": True,
        "base_ref": "origin/main",
        "head_ref": "HEAD",
        "base_tree_sha": "a" * 40,
        "head_tree_sha": "b" * 40,
        "mode": "verify",
        "verdict": {
            "merge_verdict": verdict,
            "decision": verdict,
            "applicability": "applicable",
            "can_merge_without_human": verdict == "mergeable",
        },
        "capability": {
            "added": 1,
            "modified": 0,
            "removed": 0,
            "trust_root_touched": verdict == "blocked",
            "policy_weakened": False,
            "change_ids": change_ids or ["cap_0123456789abcdef"],
        },
        "human_ack": {"state": "none"},
        "policy_snapshot_sha256": "c" * 64,
        "artifact_sha256": {"verifier_json": "d" * 64},
    }


def _ingest(tmp_path: Path, attestation: dict, repo: str) -> Path:
    att_path = tmp_path / f"att-{repo.replace('/', '-')}.json"
    att_path.write_text(json.dumps(attestation), encoding="utf-8")
    ledger = tmp_path / "registry.jsonl"
    result = runner.invoke(
        app,
        [
            "registry",
            "ingest",
            "--attestation",
            str(att_path),
            "--registry",
            str(ledger),
            "--repo",
            repo,
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    return ledger


def test_ingest_is_idempotent(tmp_path: Path) -> None:
    attestation = _attestation()
    ledger = _ingest(tmp_path, attestation, "org/a")
    first = ledger.read_text(encoding="utf-8")

    att_path = tmp_path / "att-org-a.json"
    result = runner.invoke(
        app,
        [
            "registry",
            "ingest",
            "--attestation",
            str(att_path),
            "--registry",
            str(ledger),
            "--repo",
            "org/a",
            "--json",
        ],
    )
    assert result.exit_code == 0
    assert json.loads(result.output)["status"] == "exists"
    assert ledger.read_text(encoding="utf-8") == first


def test_query_filters_by_repo_verdict_and_capability(tmp_path: Path) -> None:
    ledger = _ingest(tmp_path, _attestation("blocked", ["cap_aaa"]), "org/a")
    _ingest(tmp_path, _attestation("mergeable", ["cap_bbb"]), "org/b")

    result = runner.invoke(
        app,
        [
            "registry",
            "query",
            "--registry",
            str(ledger),
            "--verdict",
            "blocked",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["count"] == 1
    assert payload["rows"][0]["repo"] == "org/a"

    result = runner.invoke(
        app,
        [
            "registry",
            "query",
            "--registry",
            str(ledger),
            "--capability-id",
            "cap_bbb",
            "--json",
        ],
    )
    assert json.loads(result.output)["rows"][0]["repo"] == "org/b"

    result = runner.invoke(
        app,
        [
            "registry",
            "query",
            "--registry",
            str(ledger),
            "--trust-root-touched",
            "--json",
        ],
    )
    assert json.loads(result.output)["count"] == 1


def test_ingest_rejects_non_attestation(tmp_path: Path) -> None:
    bogus = tmp_path / "bogus.json"
    bogus.write_text(json.dumps({"hello": "world"}), encoding="utf-8")
    result = runner.invoke(
        app,
        ["registry", "ingest", "--attestation", str(bogus)],
    )
    assert result.exit_code == 3


def test_query_human_output_lists_rows(tmp_path: Path) -> None:
    ledger = _ingest(tmp_path, _attestation(), "org/a")
    result = runner.invoke(app, ["registry", "query", "--registry", str(ledger)])
    assert result.exit_code == 0
    assert "verdict=blocked" in result.output
    assert "trust-root" in result.output
