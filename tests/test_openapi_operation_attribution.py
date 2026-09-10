from __future__ import annotations

import copy
import hashlib
import json

import pytest
import yaml

from agents_shipgate.cli.scan.orchestrator import run_scan
from agents_shipgate.core.operation_attribution import (
    ReconstructedOperationBase,
    compare_operations,
)


def spec(values=("alpha", "beta")):
    return {
        "openapi": "3.0.3",
        "info": {"title": "Archived documents", "version": "1"},
        "servers": [{"url": "https://example.test/api"}],
        "security": [],
        "paths": {
            "/documents/{id}": {
                "delete": {
                    "operationId": "remove_document",
                    "parameters": [
                        {
                            "name": "id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "enum": list(values)},
                        }
                    ],
                    "responses": {"204": {"description": "Removed"}},
                }
            }
        },
    }


def workspace(root, document=None, *, approval=False):
    root.mkdir(exist_ok=True)
    manifest = {
        "version": "0.1",
        "project": {"name": "documents", "owner": "test"},
        "agent": {"name": "document_helper", "declared_purpose": ["Manage document records"]},
        "environment": {"target": "local"},
        "tool_sources": [{"id": "documents", "type": "openapi", "path": "api.json"}],
        # Deliberately synthetic test input, never release qualification or
        # an assertion about deployed wiring. The unbound negative removes it.
        "agent_bindings": {
            "declarations": [
                {
                    "agent": "root",
                    "complete": True,
                    "tools": [{"tool": "remove_document", "source_id": "documents"}],
                    "handoffs": [],
                    "reason": "Synthetic isolated policy-check regression fixture",
                }
            ]
        },
        "policies": {"require_approval_for_tools": ["remove_document"] if approval else []},
        "ci": {"mode": "advisory"},
    }
    (root / "shipgate.yaml").write_text(yaml.safe_dump(manifest))
    (root / "api.json").write_text(json.dumps(document or spec()))
    return manifest


def scan(root, out):
    report, _ = run_scan(config_path=root / "shipgate.yaml", output_dir=out, plugins_enabled=False)
    return report


def test_real_delete_finding_has_exact_claim_and_dependency_join(tmp_path):
    root = tmp_path / "repo"
    workspace(root)
    report = scan(root, tmp_path / "reports")
    (row,) = report.tool_surface_facts.operation_attributions
    assert row.status == "observed", row
    finding = next(f for f in report.findings if f.check_id == row.check_id)
    assert row.finding_fingerprints == [finding.fingerprint]
    assert finding.capability_refs == [row.capability_id]
    assert row.predicates[0].claim_ids
    assert row.missing_approval is True
    assert finding.support is None  # Attribution must not alter gating support.
    assert row.operation.declared_targets == [
        "https://example.test/api/documents/alpha",
        "https://example.test/api/documents/beta",
    ]
    for evidence in row.operation.inputs:
        assert evidence.sha256 == hashlib.sha256((root / evidence.path).read_bytes()).hexdigest()
    assert row.finding_exclusion_eligible is False
    assert row.deployed_reachability == "unknown"


@pytest.mark.parametrize(
    "values,direction",
    [
        (("alpha", "beta"), "unchanged"),
        (("alpha",), "narrowed"),
        (("alpha", "beta", "gamma"), "widened"),
    ],
)
def test_domain_comparison_does_not_resolve_missing_approval(tmp_path, values, direction):
    root = tmp_path / "repo"
    workspace(root)
    old = scan(root, tmp_path / "before")
    workspace(root, spec(values))
    new = scan(root, tmp_path / "after")
    base = ReconstructedOperationBase(
        "a" * 40, tuple(old.tool_surface_facts.operation_attributions)
    )
    (comparison,) = compare_operations(new.tool_surface_facts.operation_attributions, base)
    assert comparison.declared_target_domain == direction, comparison
    assert comparison.approval_predicate == "standing_weakness"
    assert old.release_decision.decision == new.release_decision.decision
    assert {f.fingerprint for f in old.findings} == {f.fingerprint for f in new.findings}


@pytest.mark.parametrize(
    "mutation",
    [
        "query_boolean",
        "missing_parameter",
        "server_variable",
        "external_ref",
        "schema_composition",
        "duplicate_parameter",
        "callback",
        "missing_security",
        "unknown_security",
        "operation_body",
    ],
)
def test_unmodeled_input_cannot_be_attributed(tmp_path, mutation):
    document = spec()
    operation = document["paths"]["/documents/{id}"]["delete"]
    parameter = operation["parameters"][0]
    if mutation == "query_boolean":
        operation["parameters"].append(
            {"name": "approved", "in": "query", "schema": {"type": "boolean"}}
        )
    elif mutation == "missing_parameter":
        operation["parameters"] = []
    elif mutation == "server_variable":
        document["servers"] = [
            {"url": "https://{host}/", "variables": {"host": {"default": "example.test"}}}
        ]
    elif mutation == "external_ref":
        parameter["schema"] = {"$ref": "https://example.test/schema.json"}
    elif mutation == "schema_composition":
        parameter["schema"] = {"allOf": [parameter["schema"]]}
    elif mutation == "duplicate_parameter":
        operation["parameters"].append(copy.deepcopy(parameter))
    elif mutation == "callback":
        operation["callbacks"] = {}
    elif mutation == "missing_security":
        del document["security"]
    elif mutation == "unknown_security":
        document["security"] = [{"undeclared": []}]
    elif mutation == "operation_body":
        operation["requestBody"] = {}
    root = tmp_path / "repo"
    workspace(root, document)
    report = scan(root, tmp_path / "reports")
    (row,) = report.tool_surface_facts.operation_attributions
    assert row.status != "observed"
    assert row.finding_exclusion_eligible is False


def test_supplied_report_never_grants_reconstructed_provenance(tmp_path):
    root = tmp_path / "repo"
    workspace(root)
    scan(root, tmp_path / "base")
    report, _ = run_scan(
        config_path=root / "shipgate.yaml",
        output_dir=tmp_path / "after",
        diff_from_path=tmp_path / "base/report.json",
        plugins_enabled=False,
    )
    (row,) = report.tool_surface_diff.operation_comparisons
    assert row.evidence_source == "unavailable"
    assert row.declared_target_domain == "unresolved"


@pytest.mark.parametrize(
    "mutation", ["duplicate_key", "unbound", "conflicting_effect", "long_server", "target_budget"]
)
def test_evidence_refusals_preserve_unknown(tmp_path, mutation, monkeypatch):
    root = tmp_path / "repo"
    manifest = workspace(root)
    if mutation == "duplicate_key":
        text = (root / "api.json").read_text()
        (root / "api.json").write_text(
            text.replace('"security": []', '"security": [{"unknown": []}], "security": []')
        )
    elif mutation == "unbound":
        del manifest["agent_bindings"]
        (root / "shipgate.yaml").write_text(yaml.safe_dump(manifest))
    elif mutation == "conflicting_effect":
        manifest["action_surface"] = {"actions": [{"tool": "remove_document", "effect": "read"}]}
        (root / "shipgate.yaml").write_text(yaml.safe_dump(manifest))
    elif mutation == "long_server":
        document = spec()
        document["servers"] = [{"url": "https://example.test/" + "a" * 2048}]
        (root / "api.json").write_text(json.dumps(document))
    else:
        from agents_shipgate.inputs import openapi_operation_contract

        monkeypatch.setattr(openapi_operation_contract, "MAX_TARGET_BYTES", 1)
    report = scan(root, tmp_path / "reports")
    (row,) = report.tool_surface_facts.operation_attributions
    assert row.status != "observed"
    assert row.finding_exclusion_eligible is False


@pytest.mark.parametrize(
    "mutation", ["operation_rename", "path_change", "security_change", "unresolved_base_reference"]
)
def test_identity_configuration_and_missing_base_never_prove_improvement(tmp_path, mutation):
    root = tmp_path / "repo"
    document = spec()
    if mutation == "unresolved_base_reference":
        document["paths"]["/documents/{id}"] = {"$ref": "#/components/pathItems/Documents"}
    workspace(root, document)
    old = scan(root, tmp_path / "before")
    document = spec()
    if mutation == "operation_rename":
        document["paths"]["/documents/{id}"]["delete"]["operationId"] = "erase_document"
    elif mutation == "path_change":
        document["paths"]["/records/{id}"] = document["paths"].pop("/documents/{id}")
    elif mutation == "security_change":
        document["components"] = {
            "securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}}
        }
        document["security"] = [{"bearer": []}]
    manifest = workspace(root, document)
    if mutation == "operation_rename":
        manifest["agent_bindings"]["declarations"][0]["tools"][0]["tool"] = "erase_document"
        (root / "shipgate.yaml").write_text(yaml.safe_dump(manifest))
    new = scan(root, tmp_path / "after")
    base = ReconstructedOperationBase(
        "a" * 40, tuple(old.tool_surface_facts.operation_attributions)
    )
    comparisons = compare_operations(new.tool_surface_facts.operation_attributions, base)
    assert comparisons
    assert all(row.declared_target_domain == "unresolved" for row in comparisons)
    assert all(row.approval_predicate == "unresolved" for row in comparisons)
    if mutation == "operation_rename":
        (row,) = comparisons
        assert row.before.capability_id != row.after.capability_id
        assert row.reason == "operation_or_capability_identity_changed"


def test_operation_overrides_preserve_parameter_location_and_security_alternatives(tmp_path):
    root = tmp_path / "repo"
    document = spec()
    path = document["paths"]["/documents/{id}"]
    path["parameters"] = copy.deepcopy(path["delete"]["parameters"])
    path["parameters"][0]["schema"]["enum"] = ["outer"]
    path["servers"] = [{"url": "https://path.example.test"}]
    path["delete"]["servers"] = [{"url": "https://operation.example.test"}]
    document["components"] = {
        "securitySchemes": {
            "bearer": {"type": "http", "scheme": "bearer"},
            "key": {"type": "apiKey", "in": "header", "name": "X-Api-Key"},
        }
    }
    path["delete"]["security"] = [{"bearer": [], "key": []}, {"key": []}]
    workspace(root, document)
    report = scan(root, tmp_path / "report")
    (row,) = report.tool_surface_facts.operation_attributions
    assert row.operation.status == "observed"
    assert row.operation.declared_targets == [
        "https://operation.example.test/documents/alpha",
        "https://operation.example.test/documents/beta",
    ]
    assert json.loads(row.operation.security_contract)["alternatives"] == [
        {"bearer": [], "key": []},
        {"key": []},
    ]


def test_duplicate_yaml_keys_and_aliases_are_profile_refusals(tmp_path):
    from agents_shipgate.inputs.openapi_operation_contract import unambiguous_document

    assert not unambiguous_document("value: one\nvalue: two\n")
    assert not unambiguous_document("a: &a {x: y}\nb: *a\n")
    assert not unambiguous_document("a: &a {x: y}\nb: {<<: *a}\n")


def test_conflicting_canonical_evidence_cannot_compare(tmp_path):
    root = tmp_path / "repo"
    workspace(root)
    report = scan(root, tmp_path / "report")
    (attribution,) = report.tool_surface_facts.operation_attributions
    base = ReconstructedOperationBase("a" * 40, (attribution, attribution.model_copy(deep=True)))
    (row,) = compare_operations([attribution], base)
    assert row.declared_target_domain == "unresolved"
    assert row.finding_exclusion_eligible is False


@pytest.mark.parametrize("family", ["openai_api", "anthropic"])
def test_independent_policy_artifact_is_not_a_closed_manifest_dependency(tmp_path, family):
    root = tmp_path / "repo"
    manifest = workspace(root)
    manifest[family] = {"policy_rules": ["approval.json"]}
    (root / "shipgate.yaml").write_text(yaml.safe_dump(manifest))
    (root / "approval.json").write_text(json.dumps({"approval_required": []}))
    old = scan(root, tmp_path / "before")
    (root / "approval.json").write_text(json.dumps({"approval_required": ["remove_document"]}))
    new = scan(root, tmp_path / "after")
    for report in (old, new):
        (row,) = report.tool_surface_facts.operation_attributions
        assert row.status == "unresolved"
        assert row.reason == "policy_dependency_not_closed_by_profile"
    assert any(f.check_id == "SHIP-POLICY-APPROVAL-MISSING" for f in old.findings)
    assert not any(f.check_id == "SHIP-POLICY-APPROVAL-MISSING" for f in new.findings)
    base = ReconstructedOperationBase(
        "a" * 40, tuple(old.tool_surface_facts.operation_attributions)
    )
    assert all(
        row.approval_predicate == "unresolved"
        for row in compare_operations(new.tool_surface_facts.operation_attributions, base)
    )


def test_additional_get_catalog_cannot_be_an_unrecorded_identity_dependency(tmp_path):
    root = tmp_path / "repo"
    manifest = workspace(root)
    other = spec()
    path = other["paths"]["/documents/{id}"]
    path["get"] = path.pop("delete")
    path["get"]["operationId"] = "lookup_document"
    (root / "other.json").write_text(json.dumps(other))
    manifest["tool_sources"].append({"id": "other", "type": "openapi", "path": "other.json"})
    (root / "shipgate.yaml").write_text(yaml.safe_dump(manifest))
    report = scan(root, tmp_path / "reports")
    (row,) = report.tool_surface_facts.operation_attributions
    assert row.status == "unresolved"
    assert row.reason == "policy_dependency_not_closed_by_profile"
