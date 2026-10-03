from __future__ import annotations

import json
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator
from test_current_control import _live, _verify
from test_current_control import repo as repo  # noqa: F401
from test_openshell_inputs import POLICY, REGISTRATION, comparison
from test_openshell_routes import invoke
from test_partial_host_comparison import _repository

from agents_shipgate.core.current_control import CurrentControlUnavailable, read_current_control
from agents_shipgate.core.host_grants import (
    build_host_boundary_snapshot,
    host_audit_inventory,
    inventory_is_complete,
)
from agents_shipgate.core.openshell_compare import compare_openshell_grants


def profile(host="api.provider.example", *, access="read-only"):
    return {"id": "github", "resource_version": 1,
            "endpoints": [{"host": host, "port": 443, "protocol": "rest",
                           "access": access, "enforcement": "enforce"}],
            "binaries": ["/usr/bin/client"]}


def selection(*, providers=True):
    return {"version": 2, "runtime_version": "0.1.2", "compositions": [{
        "name": "worker", "workspace": "team-a", "catalog_mode": "imported",
        "global_policy": {"state": "absent"}, "image_policy": {"state": "absent"},
        "saved_policy": {"state": "selected", "path": "base.policy"},
        "profile_catalog": [{"path": "profiles/github.profile", "scope": "platform"}],
        "providers": [{"name": "Work GitHub!", "profile_id": "github", "endpoint_resolution": "profile"}] if providers else [],
    }]}


def files(selected=None, provider=None):
    return {REGISTRATION: selected or selection(), "base.policy": POLICY,
            "profiles/github.profile": provider or profile()}


def inventory(root, contents):
    for path, content in contents.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content if isinstance(content, str) else json.dumps(content))
    return host_audit_inventory(root)


def grant(inv):
    row, = [row for row in inv["grants"] if row["kind"] == "openshell_policy"]
    return row


def test_unchanged_base_changed_provider_routes_composed_authority(tmp_path):
    root = _repository(tmp_path, files(), {"profiles/github.profile": profile(access="read-write")})
    result = comparison(root, head="HEAD")
    assert result.comparison_status == "comparable"
    row, = [row for row in result.rows if row.subject.startswith("openshell")]
    assert row.direction == "widened" and row.expands
    checked = invoke(root, "check", "--base", "main", "--head", "HEAD", "--format", "agent-boundary-json")
    assert any(row["evidence"].get("direction") == "widened" for row in checked["violations"])
    assert checked["control"]["permissions"]["report_complete"] is False


@pytest.mark.parametrize("attach", [True, False])
def test_provider_attach_detach_preserves_base_and_names_direction(tmp_path, attach):
    before, after = selection(providers=not attach), selection(providers=attach)
    root = _repository(tmp_path, files(before), {REGISTRATION: after})
    result = comparison(root, head="HEAD")
    assert any(row.direction == ("widened" if attach else "narrowed") for row in result.rows)


def test_workspace_override_and_content_identity_are_explicit(tmp_path):
    selected = selection()
    selected["compositions"][0]["profile_catalog"].append({"path": "profiles/local", "scope": "workspace", "workspace": "team-a"})
    inv = inventory(tmp_path, {**files(selected), "profiles/local": profile("local.example")})
    assert inventory_is_complete(inv)
    facts = grant(inv)["facts"]
    endpoints = facts["policy"]["network_policies"]["_provider_work_github"]["endpoints"]
    assert endpoints[0]["host"] == "local.example"
    contributors = facts["composition"]["contributors"]
    local, = [item for item in contributors if item["scope"] == "workspace"]
    platform, = [item for item in contributors if item["scope"] == "platform"]
    assert local["selected"] and not platform["selected"]
    assert local["profile_id"] == platform["profile_id"] == "github"
    assert local["content_sha256"] != platform["content_sha256"]


def test_provider_name_collisions_append_without_overwriting(tmp_path):
    selected = selection()
    spec = selected["compositions"][0]
    for name in ("work-github", "K", "..."):
        spec["providers"].append({"name": name, "profile_id": "github", "endpoint_resolution": "profile"})
    inv = inventory(tmp_path, files(selected))
    assert inventory_is_complete(inv)
    facts = grant(inv)["facts"]
    assert set(facts["policy"]["network_policies"]) == {"api", "_provider_work_github", "_provider_work_github_2", "_provider_unnamed", "_provider_unnamed_2"}
    assert len([item for item in facts["composition"]["contributors"] if item["provider"]]) == 4


@pytest.mark.parametrize("change", ["activate", "remove", "replace"])
def test_global_replaces_and_removal_restores_provider_and_saved_policy(tmp_path, change):
    selected = selection()
    active = deepcopy(selected)
    active["compositions"][0]["global_policy"] = {"state": "selected", "path": "global.policy"}
    global_policy = POLICY.replace("api.example.com", "global.example")
    before = selected if change == "activate" else active
    updates = {REGISTRATION: selected if change == "remove" else active}
    if change == "replace":
        updates["global.policy"] = global_policy.replace("global.example", "replacement.example")
    root = _repository(tmp_path, {**files(before), "global.policy": global_policy}, updates)
    result = comparison(root, head="HEAD")
    assert any(row.expands for row in result.rows)
    inv = host_audit_inventory(root)
    facts = grant(inv)["facts"]
    assert facts["composition"]["selected_policy"] == ("saved" if change == "remove" else "global")
    assert ("_provider_work_github" in facts["policy"]["network_policies"]) == (change == "remove")


@pytest.mark.parametrize("missing", ["saved", "profile", "context", "duplicate", "interceptor", "endpointless", "reserved"])
def test_unresolved_composition_is_named_incomplete(tmp_path, missing):
    selected = selection()
    spec = selected["compositions"][0]
    contents = files(selected)
    if missing == "saved":
        spec["saved_policy"] = {"state": "absent"}
    elif missing == "profile":
        del contents["profiles/github.profile"]
    elif missing == "context":
        del spec["providers"][0]["endpoint_resolution"]
    elif missing in {"duplicate", "interceptor"}:
        spec["catalog_mode"] = "combined"
        spec["profile_catalog"].append({"path": "profiles/other", "scope": "platform" if missing == "duplicate" else "interceptor"})
        contents["profiles/other"] = profile()
    elif missing == "endpointless":
        contents["profiles/github.profile"]["endpoints"] = []
    else:
        contents["base.policy"] = POLICY.replace("  api:", "  _provider_api:")
    inv = inventory(tmp_path, contents)
    assert not inventory_is_complete(inv)
    assert any(issue["host"] == "openshell" and issue["blocking"] for issue in inv["issues"])
    assert not inv["grants"]


def test_credentials_are_separate_from_network_and_values_are_rejected(tmp_path):
    provider = profile()
    provider["credentials"] = [{"name": "github_token", "env_vars": ["GITHUB_TOKEN"], "auth_style": "bearer", "header_name": "authorization"}]
    inv = inventory(tmp_path, files(provider=provider))
    assert inventory_is_complete(inv)
    facts = grant(inv)["facts"]
    credentials, = facts["credential_use"]
    assert credentials["credential_values_read"] is False
    assert credentials["runtime_authorization_verified"] is False
    assert "binaries" not in credentials and "access" not in credentials["endpoints"][0]
    old = grant(inv)
    provider["endpoints"][0]["access"] = "read-write"
    changed = grant(inventory(tmp_path, files(provider=provider)))
    assert compare_openshell_grants(old, changed).direction == "widened"
    provider["credentials"][0]["value"] = "sk-live-0123456789abcdef0123456789abcdef"
    failed = inventory(tmp_path, files(provider=provider))
    assert not inventory_is_complete(failed)
    assert provider["credentials"][0]["value"] not in json.dumps(failed)


def test_snapshot_metadata_keeps_freshness_and_field_lifetimes_explicit(tmp_path):
    selected = {"version": 2, "runtime_version": "0.1.2", "policies": [{"path": "export", "role": "effective_snapshot", "snapshot": {"source": "operator export", "revision": "sandbox-42"}}]}
    inv = inventory(tmp_path, {REGISTRATION: selected, "export": POLICY})
    facts = grant(inv)["facts"]
    assert facts["snapshot"]["revision"] == "sandbox-42"
    assert facts["runtime_freshness_verified"] is False
    assert "filesystem_policy" in facts["startup_bound_fields"]
    assert "network_policies" in facts["dynamic_fields"]
    from pathlib import Path

    schema = json.loads((Path(__file__).parents[1] / "docs/host-grants-inventory-schema.v0.9.json").read_text())
    Draft202012Validator(schema).validate(inv)


def test_every_dependency_is_bound_including_ignored_suppressed_profile(repo):
    selected = selection()
    selected["compositions"][0]["global_policy"] = {"state": "selected", "path": "global.policy"}
    inventory(repo, {**files(selected), "global.policy": POLICY})
    # This profile has no network contribution while a global policy is active.
    (repo / ".gitignore").write_text("agents-shipgate-reports/\nprofiles/\n")
    _verify(repo, archive_head=False)
    plan = json.loads((repo / "agents-shipgate-reports/verification-plan.json").read_text())
    paths = {item["path"] for item in plan["inputs"]["options"]["dependency_inputs"]["files"]}
    assert {REGISTRATION, "base.policy", "global.policy", "profiles/github.profile"} <= paths
    read_current_control(repo / "agents-shipgate-reports", live=lambda: _live(repo))
    (repo / "profiles/github.profile").write_text(json.dumps(profile("changed.example")))
    with pytest.raises(CurrentControlUnavailable):
        read_current_control(repo / "agents-shipgate-reports", live=lambda: _live(repo))


def test_shared_read_session_captures_all_local_contributors(tmp_path):
    inventory(tmp_path, files())
    snapshot = build_host_boundary_snapshot(tmp_path)
    assert {REGISTRATION, "base.policy", "profiles/github.profile"} <= snapshot.cache.openshell_selected_paths
