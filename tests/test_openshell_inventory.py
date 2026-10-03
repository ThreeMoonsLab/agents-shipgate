from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from agents_shipgate.core.host_grants import (
    HostStaticParseCache,
    build_host_boundary_snapshot,
    build_host_drift_payload,
    build_host_grants_baseline,
    host_audit_inventory,
    inventory_is_complete,
    render_host_audit_markdown,
)
from agents_shipgate.core.openshell import OpenShellReadError, parse_policy, parse_selection
from agents_shipgate.schemas.host_grants import (
    HostGrantsInventoryArtifactV7,
    HostGrantsInventoryV7,
)

POLICY = """version: 1
filesystem_policy:
  read_only: [/usr]
  read_write: [/tmp]
network_policies:
  github:
    binaries: [{path: /usr/bin/gh}]
    endpoints:
      - host: api.github.com
        port: 443
        protocol: rest
        access: read-only
"""


def select(root: Path, *, role: str = "authored", path: str = "configs/arbitrary.rules") -> Path:
    registration = root / ".shipgate/openshell.json"
    registration.parent.mkdir(parents=True, exist_ok=True)
    registration.write_text(json.dumps({
        "version": 1, "runtime_version": "0.1.2", "policies": [{"path": path, "role": role}],
    }))
    policy = root / path
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_text(POLICY)
    return policy


def test_selected_arbitrary_filename_has_typed_facts_and_defaults(tmp_path: Path) -> None:
    select(tmp_path)
    inventory = host_audit_inventory(tmp_path)
    assert inventory_is_complete(inventory)
    grant, = inventory["grants"]
    assert grant["host"] == "openshell" and grant["access"] == "unknown"
    facts = grant["facts"]
    assert facts["role"] == "authored" and facts["runtime_version"] == "0.1.2"
    assert facts["policy_schema_version"] == 1
    endpoint = facts["policy"]["network_policies"]["github"]["endpoints"][0]
    assert endpoint["enforcement"] == "audit"
    assert facts["policy"]["filesystem_policy"]["include_workdir"] is False
    assert "/filesystem_policy/include_workdir" in facts["defaulted_fields"]
    assert "/network_policies/github/endpoints/0/enforcement" in facts["defaulted_fields"]
    assert facts["runtime_freshness_verified"] is False
    assert "openshell_policy" in render_host_audit_markdown(inventory)
    schema = json.loads((Path(__file__).parents[1] / "docs/host-grants-inventory-schema.v0.9.json").read_text())
    Draft202012Validator(schema).validate(inventory)
    with pytest.raises(ValidationError):
        HostGrantsInventoryV7.model_validate(inventory)


def test_effective_snapshot_role_is_explicit_and_is_not_runtime_attestation(tmp_path: Path) -> None:
    select(tmp_path, role="effective_snapshot")
    facts = host_audit_inventory(tmp_path)["grants"][0]["facts"]
    assert facts["role"] == "effective_snapshot"
    assert not facts["runtime_freshness_verified"]


def test_v08_baseline_round_trip_and_frozen_v07_schema(tmp_path: Path) -> None:
    select(tmp_path)
    inventory = host_audit_inventory(tmp_path)
    baseline = build_host_grants_baseline(inventory)
    drift = build_host_drift_payload(baseline=baseline, inventory=inventory, baseline_file="baseline.json")
    assert drift["comparison_status"] == "comparable" and drift["has_drift"] is False
    old = json.loads((Path(__file__).parents[1] / "docs/host-grants-inventory-schema.v0.7.json").read_text())
    frozen = HostGrantsInventoryArtifactV7.model_json_schema()
    for key in ("$id", "$schema", "title", "description"):
        old.pop(key, None)
        frozen.pop(key, None)
    assert old == frozen


def test_unselected_yaml_is_not_a_policy(tmp_path: Path) -> None:
    (tmp_path / "sandbox.yaml").write_text(POLICY)
    assert host_audit_inventory(tmp_path)["grants"] == []


REJECTED_POLICIES = [
    "", "[]", "version: 1\nversion: 1", "version: true", "version: 2",
    "version: 1\nunknown: true", "version: 1\nfilesystem_policy: null",
    "version: 1\nfilesystem_policy: {include_workdir: on}",
    "version: 1\nfilesystem_policy: {read_write: [/]}" ,
    "version: 1\nfilesystem_policy: {read_only: [/tmp/../etc]}",
    "version: 1\nprocess: {run_as_user: '0'}",
    "version: 1\nnetwork_policies: {rule: {endpoints: [{host: api.example.com, port: 70000}]}}",
    "version: 1\nnetwork_middlewares: {redactor: {config: {token: secret}}}",
    "version: 1\nx: &a [1]\ny: *a", "version: 1\nfilesystem_policy: {<<: {read_only: [/usr]}}",
    "version: 1\n---\nversion: 1", "version: 1\nx: !!python/object:danger {}",
    '{"version": 1, "process": {"run_as_user": "\\ud800"}}',
]


@pytest.mark.parametrize("text", REJECTED_POLICIES)
def test_rejected_input_is_named_partial_not_empty_complete(tmp_path: Path, text: str) -> None:
    select(tmp_path).write_text(text)
    inventory = host_audit_inventory(tmp_path)
    assert not inventory_is_complete(inventory)
    assert inventory["grants"] == []
    issue, = inventory["issues"]
    assert issue["blocking"] and issue["source"] == "configs/arbitrary.rules"
    assert next(item for item in inventory["host_coverage"] if item["host"] == "openshell")["status"] == "partial"


@pytest.mark.parametrize("path", ["../escape.yaml", "/tmp/escape.yaml", "a/../../escape", "https://example.com/policy", "a\\b", "a/./b"])
def test_registration_refuses_escape_remote_and_noncanonical_paths(path: str) -> None:
    with pytest.raises(OpenShellReadError):
        parse_selection(json.dumps({"version": 1, "runtime_version": "0.1.2", "policies": [{"path": path, "role": "authored"}]}))


def test_missing_policy_and_outside_symlink_do_not_read_external_bytes(tmp_path: Path) -> None:
    policy = select(tmp_path)
    policy.unlink()
    assert not inventory_is_complete(host_audit_inventory(tmp_path))
    policy.symlink_to("/etc/passwd")
    inventory = host_audit_inventory(tmp_path)
    assert not inventory_is_complete(inventory)
    assert inventory["grants"] == []


def test_in_tree_link_and_repeated_selection_use_one_bound_read(tmp_path: Path) -> None:
    policy = select(tmp_path)
    target = tmp_path / "actual.yaml"
    policy.rename(target)
    policy.symlink_to("../actual.yaml")
    nested = tmp_path / "project/.shipgate/openshell.json"
    nested.parent.mkdir(parents=True)
    nested.write_bytes((tmp_path / ".shipgate/openshell.json").read_bytes())
    cache = HostStaticParseCache()
    inventory = build_host_boundary_snapshot(tmp_path, cache=cache).inventory
    assert inventory_is_complete(inventory)
    assert cache.read_counts[str(target)] == 1
    assert cache.parse_counts[str(target)] == 1
    assert len([item for item in inventory["artifacts"] if item["kind"] == "openshell_policy"]) == 1
    assert len(inventory["grants"]) == 2
    artifact = next(item for item in inventory["artifacts"] if item["kind"] == "openshell_policy")
    assert artifact["resolved_through"] == ["actual.yaml"]


def test_bounds_and_duplicate_registration(tmp_path: Path) -> None:
    with pytest.raises(OpenShellReadError):
        parse_policy("version: 1\nx: " + "[" * 60 + "0" + "]" * 60)
    policy = select(tmp_path)
    policy.write_text("#" + "x" * (1024 * 1024))
    assert not inventory_is_complete(host_audit_inventory(tmp_path))
    with pytest.raises(OpenShellReadError):
        parse_selection('{"version":1,"version":1,"runtime_version":"0.1.2","policies":[]}')


def test_repeated_registrations_cannot_multiply_projection_without_a_bound(tmp_path: Path) -> None:
    references = []
    for index in range(33):
        path = f"policy-{index}.yaml"
        (tmp_path / path).write_text("version: 1")
        references.append({"path": path, "role": "authored"})
    for prefix in ("a", "b"):
        registration = tmp_path / prefix / ".shipgate/openshell.json"
        registration.parent.mkdir(parents=True)
        registration.write_text(json.dumps({"version": 1, "runtime_version": "0.1.2", "policies": references}))
    inventory = host_audit_inventory(tmp_path)
    assert not inventory_is_complete(inventory)
    assert len(inventory["grants"]) == 64
    assert any("64 policy references" in issue["message"] for issue in inventory["issues"])


def test_credentials_never_reach_json_markdown_or_errors(tmp_path: Path) -> None:
    token = "ghp_" + "a" * 30
    policy = select(tmp_path)
    policy.write_text(POLICY.replace("api.github.com", token))
    inventory = host_audit_inventory(tmp_path)
    assert not inventory_is_complete(inventory)
    assert token not in json.dumps(inventory) + render_host_audit_markdown(inventory)
    policy.write_text("version: 1\nunknown: " + token)
    assert token not in json.dumps(host_audit_inventory(tmp_path))
    policy.write_text(POLICY.replace("access: read-only", "rules: [{allow: {method: GET, path: /, query: {api_key: PRIVATE_VALUE}}}]"))
    inventory = host_audit_inventory(tmp_path)
    assert not inventory_is_complete(inventory)
    assert "PRIVATE_VALUE" not in json.dumps(inventory) + render_host_audit_markdown(inventory)


def test_deterministic_inventory_and_distinct_document_versions(tmp_path: Path) -> None:
    select(tmp_path)
    assert host_audit_inventory(tmp_path) == host_audit_inventory(tmp_path)
    registration = tmp_path / ".shipgate/openshell.json"
    registration.write_text(registration.read_text().replace("0.1.2", "1.0"))
    assert not inventory_is_complete(host_audit_inventory(tmp_path))


def test_mcp_options_and_string_revisions_are_read_without_tool_effect_claims() -> None:
    policy = parse_policy("""version: 1
network_policies:
  mcp:
    binaries: [{path: /usr/bin/python}]
    endpoints:
      - host: mcp.example.com
        port: 443
        protocol: mcp
        mcp: {versions: [2025-03-26], allow_all_known_mcp_methods: true}
""")
    assert policy.network_policies["mcp"].endpoints[0].mcp.versions == ["2025-03-26"]
