"""Successor evidence never rewrites a predecessor's closed structural claims."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from test_agent_handoff import _verifier_payload
from test_current_control import _verify
from test_current_control import repo as repo

from agents_shipgate.core.agent_handoff import build_agent_handoff
from agents_shipgate.core.host_grants import (
    build_host_drift_payload,
    host_audit_inventory,
    host_grants_sha256,
    load_host_grants_baseline,
)
from agents_shipgate.core.preflight import build_preflight_result
from agents_shipgate.schemas.agent_handoff import AgentHandoffArtifact
from agents_shipgate.schemas.host_grants import HostArtifactV2
from agents_shipgate.schemas.preflight import PreflightResultV3, TrustRootNodeV1
from agents_shipgate.schemas.verifier import VerifierArtifact

ROOT = Path(__file__).resolve().parents[1]


def test_old_models_reject_structural_claims_and_old_baseline_is_not_restamped(repo):
    (repo / "AGENTS.md").write_text("Explain the result.\n")
    inventory = host_audit_inventory(repo)
    artifact = next(row for row in inventory["artifacts"] if row["path"] == "AGENTS.md")
    with pytest.raises(ValidationError, match="instruction_structure"):
        HostArtifactV2.model_validate(artifact)
    old_artifact = {key: value for key, value in artifact.items() if key != "instruction_structure"}
    HostArtifactV2.model_validate(old_artifact)
    old = {"scope": "repository", "artifacts": [old_artifact], "grants": [], "host_coverage": []}
    baseline = {"host_grants_schema_version": "0.2", "scope": "repository",
                "inventory": old, "inventory_sha256": host_grants_sha256(old)}
    path = repo / "old-baseline.json"
    path.write_text(json.dumps(baseline))
    captured = path.read_bytes()
    read = load_host_grants_baseline(path)
    assert read["host_grants_schema_version"] == "0.2"
    drift = build_host_drift_payload(baseline=read, inventory=inventory, baseline_file=str(path))
    assert drift["comparison_status"] == "incomparable"
    assert drift["has_drift"] is None
    assert "baseline_instruction_structure_unavailable" in drift["incomparable_reasons"]
    assert path.read_bytes() == captured
    schema = json.loads((ROOT / "docs/host-grants-inventory-schema.v0.3.json").read_text())
    Draft202012Validator(schema).validate(inventory)


def test_old_graph_cannot_assert_structure_and_current_rule_never_grants_authority(repo):
    (repo / "AGENTS.md").write_text("Explain.\n")
    result = build_preflight_result(workspace=repo)
    node = next(row for row in result.trust_root_graph.nodes if row.instruction_structures)
    with pytest.raises(ValidationError, match="instruction_structures"):
        TrustRootNodeV1.model_validate(node.model_dump(mode="json"))
    payload = result.model_dump(mode="json")
    Draft202012Validator(json.loads((ROOT / "docs/preflight-schema.v0.5.json").read_text())).validate(payload)
    legacy = {key: value for key, value in payload.items() if key in PreflightResultV3.model_fields}
    legacy["preflight_schema_version"] = "0.3"
    legacy["trust_root_graph"]["schema_version"] = "0.1"
    for node in legacy["trust_root_graph"]["nodes"]:
        node.pop("instruction_structures", None)
    old = PreflightResultV3.model_validate(legacy)
    compared = build_preflight_result(workspace=repo, base_preflight=old)
    assert compared.requires_human_review
    assert all(rule.grants_authority is False for rule in result.conditional_file_edits)


@pytest.mark.parametrize("artifact", ["verifier", "handoff"])
def test_legacy_operational_reader_preserves_deny_list_without_synthesizing_rule(artifact):
    verifier = VerifierArtifact.model_validate(_verifier_payload())
    model = verifier if artifact == "verifier" else build_agent_handoff(verifier=verifier)
    current = model.model_dump(mode="json")
    legacy = copy.deepcopy(current)
    legacy.pop("conditional_file_edits")
    legacy["forbidden_file_edits"] = ["**/AGENTS.md"]
    key = "verifier_schema_version" if artifact == "verifier" else "schema_version"
    legacy[key] = "0.16" if artifact == "verifier" else "shipgate.agent_handoff/v8"
    reader = VerifierArtifact if artifact == "verifier" else AgentHandoffArtifact
    read = reader.model_validate(legacy)
    assert read.forbidden_file_edits == ["**/AGENTS.md"]
    assert read.conditional_file_edits == []
    with pytest.raises(ValidationError, match="conditional"):
        reader.model_validate({**legacy, "conditional_file_edits": []})


@pytest.mark.parametrize("missing", ["control", "diff_status", "authorization"])
def test_v016_migration_cannot_fill_missing_authority_or_input_health(missing):
    current = VerifierArtifact.model_validate(_verifier_payload()).model_dump(mode="json")
    current.pop("conditional_file_edits")
    current["verifier_schema_version"] = "0.16"
    schema = json.loads((ROOT / "docs/verifier-schema.v0.16.json").read_text())
    Draft202012Validator(schema).validate(current)
    del current[missing]
    assert list(Draft202012Validator(schema).iter_errors(current))
    with pytest.raises(ValidationError):
        VerifierArtifact.model_validate(current)


def test_emitted_preflight_route_replays_the_actual_custom_manifest_with_spaces(repo, monkeypatch):
    import difflib
    import shlex
    import subprocess

    moved = repo.parent / "workspace with spaces"
    repo.rename(moved)
    repo = moved
    original = (repo / "shipgate.yaml").read_text()
    custom = "AGENTS.md"
    (repo / custom).write_text(original)
    changed = original + "\n# A changed manifest remains a protected manifest.\n"
    diff = 'diff --git a/AGENTS.md b/AGENTS.md\n' + ''.join(difflib.unified_diff(
        original.splitlines(True), changed.splitlines(True), fromfile='a/AGENTS.md', tofile='b/AGENTS.md',
    ))
    monkeypatch.setenv("AGENTS_SHIPGATE_CLI", str(ROOT / "shipgate"))
    result = build_preflight_result(workspace=repo, config=Path(custom), changed_files=[custom])
    command = result.conditional_file_edits[0].preflight_command
    argv = shlex.split(command)
    assert argv[argv.index("--workspace") + 1] == str(repo)
    assert argv[argv.index("--config") + 1] == custom
    replay = subprocess.run(argv, input=json.dumps({"changed_files": [custom], "diff_text": diff}),
                            capture_output=True, text=True, check=True)
    answer = json.loads(replay.stdout)
    assert answer["requires_human_review"]
    assert answer["protected_surface_touches"][0]["kind"] == "manifest"
    assert answer["protected_surface_touches"][0].get("instruction_structure_unchanged") is not True


def test_verifier_and_handoff_conditional_route_preserve_custom_subject(repo):
    import shlex

    moved = repo.parent / "verifier workspace with spaces"
    repo.rename(moved)
    config = Path("custom manifest.yaml")
    (moved / config).write_bytes((moved / "shipgate.yaml").read_bytes())
    verifier, _, exit_code = _verify(moved, config=config)
    assert exit_code == 0
    handoff = build_agent_handoff(verifier=verifier)
    assert handoff.conditional_file_edits == verifier.conditional_file_edits
    argv = shlex.split(verifier.conditional_file_edits[0].preflight_command)
    assert argv[0] == "agents-shipgate"
    assert argv[argv.index("--workspace") + 1] == str(moved)
    assert argv[argv.index("--config") + 1] == str(config)
