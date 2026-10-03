from __future__ import annotations

import copy
import hashlib
import json

import pytest

from agents_shipgate.core.capability_diff_rows import capability_diff_rows
from agents_shipgate.core.host_grants import (
    diff_host_grants,
    host_grant_direction_unknown,
    host_grant_expansion_signals,
)
from agents_shipgate.core.openshell import parse_policy
from agents_shipgate.core.openshell_compare import compare_openshell_grants


def policy(*, access="read-only", enforcement="enforce", host="api.example.com", binary="/usr/bin/client"):
    return {"version": 1, "filesystem_policy": {"read_only": ["/data"], "read_write": []},
            "network_policies": {"client": {"binaries": [{"path": binary}],
                                          "endpoints": [{"host": host, "port": 443,
                                                         "protocol": "rest", "access": access,
                                                         "enforcement": enforcement}]}}}


def endpoint(value):
    return value["network_policies"]["client"]["endpoints"][0]


def grant(value, *, role="authored"):
    facts = {"runtime_version": "0.1.2", "role": role, "policy_schema_version": 1,
             "policy": parse_policy(json.dumps(value)).model_dump(mode="json")}
    return {"grant_id": "one", "kind": "openshell_policy", "host": "openshell", "source": "arbitrary.rules",
            "scope": "repository", "risk": "unknown", "config_sha256": hashlib.sha256(json.dumps(facts).encode()).hexdigest(),
            "facts": facts}


def compare(old, new):
    return compare_openshell_grants(grant(old), grant(new))


def test_enforcement_removal_uses_audit_default_and_publishes_same_direction():
    old, new = policy(), policy()
    del endpoint(new)["enforcement"]
    before, after = grant(old), grant(new)
    result = compare_openshell_grants(before, after)
    assert result.direction == "widened"
    change = {"baseline": before, "current": after}
    signals = host_grant_expansion_signals([change])
    row, = capability_diff_rows({"changes": [change], "expansion_signals": signals})
    assert row.direction == "widened" and row.expands
    assert result.explanation in row.why
    assert "runtime enforcement and freshness are unverified" in row.why
    assert "facts" in row.before and "facts" in row.after


@pytest.mark.parametrize("old,new,direction", [("full", "read-write", "narrowed"),
                                              ("read-only", "read-write", "widened"),
                                              ("read-write", "full", "widened")])
def test_rest_access_presets(old, new, direction):
    assert compare(policy(access=old), policy(access=new)).direction == direction


def test_audit_access_edit_does_not_change_allowed_requests():
    assert compare(policy(enforcement="audit"), policy(access="full", enforcement="audit")).direction == "equivalent"


def test_full_enforce_to_audit_is_equivalent_in_the_supported_well_formed_domain():
    assert compare(policy(access="full"), policy(access="full", enforcement="audit")).direction == "equivalent"


def test_redundant_allow_and_renaming_reordering_are_quiet():
    old, new = policy(), policy()
    new["network_policies"]["renamed"] = new["network_policies"].pop("client")
    new["network_policies"]["redundant"] = copy.deepcopy(new["network_policies"]["renamed"])
    new["network_policies"]["redundant"]["name"] = "display name"
    assert compare(old, new).direction == "equivalent"
    assert diff_host_grants({"grants": [grant(old)]}, {"grants": [grant(new)]}) == []


def test_duplicate_denial_removal_does_not_expand():
    old = policy(access="full")
    endpoint(old)["deny_rules"] = [{"method": "DELETE", "path": "/records"}]
    old["network_policies"]["second"] = copy.deepcopy(old["network_policies"]["client"])
    new = copy.deepcopy(old)
    endpoint(new)["deny_rules"] = []
    assert compare(old, new).direction == "equivalent"
    new["network_policies"]["second"]["endpoints"][0]["deny_rules"] = []
    assert compare(old, new).direction == "widened"


def test_global_denial_overrides_an_allow_in_another_rule():
    old = policy(access="full")
    endpoint(old)["deny_rules"] = [{"method": "*", "path": "/records"}]
    new = copy.deepcopy(old)
    new["network_policies"]["extra"] = copy.deepcopy(old["network_policies"]["client"])
    extra = new["network_policies"]["extra"]["endpoints"][0]
    extra.pop("access")
    extra["rules"] = [{"allow": {"method": "POST", "path": "/records"}}]
    extra["deny_rules"] = []
    assert compare(old, new).direction == "equivalent"


def test_uninspected_overlap_adds_no_request_access():
    old, new = policy(), policy()
    new["network_policies"]["l4"] = {"binaries": [{"path": "/usr/bin/client"}],
                                      "endpoints": [{"host": "api.example.com", "port": 443}]}
    assert compare(old, new).direction == "equivalent"


def test_correlation_is_not_flattened():
    old = policy(host="one.example.com", binary="/bin/one")
    old["network_policies"]["second"] = policy(host="two.example.com", binary="/bin/two")["network_policies"]["client"]
    new = copy.deepcopy(old)
    endpoint(new)["host"] = "two.example.com"
    new["network_policies"]["second"]["endpoints"][0]["host"] = "one.example.com"
    result = compare(old, new)
    assert result.direction == "mixed" and result.widened and result.narrowed
    assert host_grant_direction_unknown({"baseline": grant(old), "current": grant(new)})


@pytest.mark.parametrize("field,value", [("host", "*.example.com"), ("path", "/v1/**"),
                                          ("credential_binding", {"provider": "github"}),
                                          ("protocol", "graphql"), ("allow_encoded_slash", True)])
def test_unsupported_endpoint_semantics_never_become_safe_narrowing(field, value):
    old, new = policy(access="full"), policy()
    endpoint(new)[field] = value
    result = compare(old, new)
    assert result.direction == "unknown" and result.limits
    assert not result.narrowed and not result.widened


def test_request_glob_is_named_unknown():
    old, new = policy(access="full"), policy()
    endpoint(new).pop("access")
    endpoint(new)["rules"] = [{"allow": {"method": "GET", "path": "/v1/**"}}]
    assert compare(old, new).direction == "unknown"


def test_exact_request_set_addition_and_removal():
    old = policy()
    endpoint(old).pop("access")
    endpoint(old)["rules"] = [{"allow": {"method": "GET", "path": "/records"}}]
    new = copy.deepcopy(old)
    endpoint(new)["rules"].append({"allow": {"method": "POST", "path": "/records"}})
    assert compare(old, new).direction == "widened"
    assert compare(new, old).direction == "narrowed"


@pytest.mark.parametrize("field,old,new,direction", [
    ("read_write", [], ["/data"], "widened"),
    ("read_write", ["/data"], [], "narrowed"),
    ("read_only", ["/data"], ["/data", "/logs"], "widened"),
])
def test_exact_filesystem_direction(field, old, new, direction):
    left, right = policy(), policy()
    left["filesystem_policy"][field], right["filesystem_policy"][field] = old, new
    assert compare(left, right).direction == direction


def test_filesystem_ancestor_or_runtime_baseline_changes_are_unknown():
    left, right = policy(), policy()
    right["filesystem_policy"]["read_only"].append("/data/nested")
    assert compare(left, right).direction == "unknown"
    right = policy()
    right["filesystem_policy"]["read_only"].append("/usr")
    assert compare(left, right).direction == "unknown"


def test_landlock_omission_restores_best_effort():
    left, right = policy(), policy()
    left["landlock"] = {"compatibility": "hard_requirement"}
    assert compare(left, right).direction == "widened"
    assert compare(right, left).direction == "narrowed"


def test_process_workdir_role_and_missing_replacement_are_unproven():
    left, right = policy(), policy()
    right["process"] = {"run_as_user": "1001"}
    assert compare(left, right).direction == "unknown"
    right = policy()
    right["filesystem_policy"]["include_workdir"] = True
    assert compare(left, right).direction == "unknown"
    assert compare_openshell_grants(grant(left), grant(left, role="effective_snapshot")).direction == "unknown"
    assert compare_openshell_grants(grant(left), None).direction == "unknown"


def test_credential_change_is_unknown_even_if_display_hash_is_equal():
    left, right = grant(policy()), grant(policy())
    endpoint(right["facts"]["policy"])["credential_binding"] = {"provider": "github"}
    assert left["config_sha256"] == right["config_sha256"]
    assert host_grant_direction_unknown({"baseline": left, "current": right})


def test_mcp_tool_name_does_not_assert_business_effects():
    old, new = policy(), policy()
    target = endpoint(new)
    target.pop("access")
    target["protocol"] = "mcp"
    target["rules"] = [{"allow": {"tool": "delete_resource"}}]
    result = compare(old, new)
    assert result.direction == "unknown" and "protocol" in result.explanation
    assert not any(word in result.explanation for word in ("approval", "binding", "business effect"))


def test_comparison_work_is_bounded(monkeypatch):
    from agents_shipgate.core import openshell_compare
    monkeypatch.setattr(openshell_compare, "MAX_COMPARISON_CELLS", 1)
    assert compare(policy(), policy(access="full")).direction == "unknown"


def test_ancestor_denial_applies_to_a_child_binary_allow():
    old = policy(access="read-only", binary="/bin/ancestor")
    old["network_policies"]["child"] = policy(access="full", binary="/bin/child")["network_policies"]["client"]
    new = copy.deepcopy(old)
    endpoint(new)["deny_rules"] = [{"method": "POST", "path": "/records"}]
    # POST was already disallowed by the ancestor's own allow, but its new
    # denial also blocks a child allow when both selectors match the chain.
    assert compare(old, new).direction == "narrowed"


def test_inspection_suppresses_an_ancestor_l4_grant_as_well_as_adding_child_access():
    old = policy(binary="/bin/ancestor")
    endpoint(old).pop("access")
    endpoint(old).pop("protocol")
    new = copy.deepcopy(old)
    new["network_policies"]["child"] = policy(binary="/bin/child")["network_policies"]["client"]
    result = compare(old, new)
    assert result.direction == "mixed" and result.widened and result.narrowed


def test_overlapping_ancestor_modes_cannot_be_proven_safe():
    old = policy(binary="/bin/ancestor")
    new = copy.deepcopy(old)
    new["network_policies"]["child"] = policy(binary="/bin/child", enforcement="audit")["network_policies"]["client"]
    assert compare(old, new).direction == "unknown"
