"""#685: permission changes, not arbitrary workflow edits, drive rows."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.capability_diff_rows import capability_diff_rows
from agents_shipgate.core.host_grants import (
    _workflow_grant,
    diff_host_grants,
    host_grant_expansion_signals,
)

FIXTURES = Path(__file__).parent / "fixtures/workflows/685"


def _comparison(before, after):
    def snapshot(value):
        return {"grants": [_workflow_grant(value, source=".github/workflows/ci.yml")]}
    changes = diff_host_grants(snapshot(before), snapshot(after))
    payload = {"changes": changes, "expansion_signals": host_grant_expansion_signals(changes)}
    return capability_diff_rows(payload)


def _workflow(default_permissions=None, **job):
    result = {"on": ["push"], "jobs": {"test": {"run": "echo fixture", **job}}}
    if default_permissions is not None:
        result["permissions"] = default_permissions
    return result


@pytest.mark.parametrize("permission", [{"contents": "read"}, {"contents": "write"}, "write-all"])
def test_script_only_edit_is_quiet(permission):
    assert _comparison(_workflow(permission), _workflow(permission, run="echo revised")) == []


@pytest.mark.parametrize("before,after,expanded", [
    ({"contents": "read"}, {"contents": "write"}, True),
    ({"contents": "write"}, {"contents": "read"}, False),
    ("write-all", {"contents": "write"}, False),
    ({"contents": "write"}, "write-all", True),
    ({"contents": "write"}, {"contents": "write", "packages": "write"}, True),
])
def test_write_comparison_uses_both_sides(before, after, expanded):
    rows = _comparison(_workflow(before), _workflow(after))
    assert len(rows) == 1
    assert rows[0].expands is expanded
    assert rows[0].before != rows[0].after


def test_removing_a_job_override_reveals_inherited_write():
    before = _workflow({"contents": "write"}, permissions={})
    after = _workflow({"contents": "write"})
    rows = _comparison(before, after)
    assert len(rows) == 1 and rows[0].expands
    assert rows[0].before != rows[0].after


def test_reusable_secret_delegation_is_named_and_directional():
    before = _workflow({"contents": "read"}, uses="./.github/workflows/deploy.yml")
    after = _workflow({"contents": "read"}, uses="./.github/workflows/deploy.yml", secrets="inherit")
    row, = _comparison(before, after)
    assert row.direction == "widened" and row.severity == "high"
    assert "secrets: inherit" in row.after and "deploy.yml" in row.after
    assert "secret" in row.why and "write to the repository" not in row.why
    narrowed, = _comparison(after, before)
    assert not narrowed.expands


def test_inherited_secrets_change_recipient():
    before = _workflow(uses="org/repo/.github/workflows/deploy.yml@v1", secrets="inherit")
    after = _workflow(uses="org/repo/.github/workflows/deploy.yml@v2", secrets="inherit")
    row, = _comparison(before, after)
    assert row.expands and row.before != row.after


def test_new_trigger_does_not_reannounce_existing_writes():
    before = _workflow({"contents": "write"})
    after = {**before, "on": ["push", "workflow_dispatch"]}
    row, = _comparison(before, after)
    assert not row.expands
    assert "workflow_dispatch" in row.after and row.before != row.after


def test_new_recipient_of_inherited_write_remains_visible():
    before = _workflow({"contents": "write"})
    after = {**before, "jobs": {**before["jobs"], "new_job": {"run": "echo fixture"}}}
    row, = _comparison(before, after)
    assert row.expands and "new_job" in row.after


def test_explicit_job_permissions_replace_rather_than_merge_defaults():
    before = _workflow({"contents": "write"}, permissions={"packages": "read"})
    after = _workflow({"contents": "write"}, permissions={"contents": "write"})
    row, = _comparison(before, after)
    assert row.expands
    assert "test: contents: write" not in row.before
    assert "test: contents: write" in row.after


def test_identical_inherited_and_explicit_permissions_are_quiet():
    inherited = _workflow({"contents": "write"})
    explicit = _workflow({"contents": "write"}, permissions={"contents": "write"})
    assert _comparison(inherited, explicit) == []
    assert _comparison(explicit, inherited) == []


def test_overridden_root_permissions_do_not_widen_any_job():
    before = _workflow({"contents": "read"}, permissions={"contents": "read"})
    after = _workflow({"contents": "write"}, permissions={"contents": "read"})
    assert _comparison(before, after) == []
    grant = _workflow_grant(after, source=".github/workflows/ci.yml")
    assert grant["access"] == "read" and grant["risk"] == "low"


@pytest.mark.parametrize("before", [_workflow({}), _workflow(permissions={})])
def test_removing_a_restriction_exposes_unknown_repository_defaults(before):
    row, = _comparison(before, _workflow())
    assert row.direction == "changed" and not row.expands
    assert row.severity == "unknown"
    assert "repository defaults" in row.after and "unknown" in row.why
    assert row.before != row.after


def test_explicit_writes_after_unknown_defaults_are_not_proven_widening():
    row, = _comparison(_workflow(), _workflow({"contents": "write"}))
    assert row.direction == "changed" and not row.expands
    assert row.severity == "high"
    assert "unknown" in row.before and "contents: write" in row.after


def test_legacy_baseline_is_readable_but_does_not_assert_no_reusable_calls(tmp_path):
    from agents_shipgate.cli.host_audit import host_audit_inventory
    from agents_shipgate.core.host_grants import (
        build_host_drift_payload,
        build_host_grants_baseline,
        host_grants_sha256,
        load_host_grants_baseline,
    )
    path = tmp_path / ".github/workflows/ci.yml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(_workflow({"contents": "read"})))
    current = host_audit_inventory(tmp_path)
    legacy = build_host_grants_baseline(current)
    legacy["host_grants_schema_version"] = "0.3"
    for grant in legacy["inventory"]["grants"]:
        if grant["kind"] == "workflow":
            del grant["reusable_calls"]
            del grant["effective_write_scopes"]
            del grant["permission_contexts"]
    legacy["inventory_sha256"] = host_grants_sha256(legacy["inventory"])
    baseline_path = tmp_path / "baseline.json"
    original = json.dumps(legacy)
    baseline_path.write_text(original)
    loaded = load_host_grants_baseline(baseline_path)
    assert loaded == legacy
    drift = build_host_drift_payload(baseline=loaded, inventory=current, baseline_file=str(baseline_path))
    assert drift["comparison_status"] == "incomparable"
    assert drift["has_drift"] is None
    assert baseline_path.read_text() == original


@pytest.mark.parametrize("start,end", [("base", "delegation"), ("delegation", "script")])
def test_pinned_workflow_scenarios_through_real_cli(tmp_path, start, end):
    repo = tmp_path / "repo"
    target = repo / ".github/workflows"
    shutil.copytree(FIXTURES / start, target)
    def git(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    git("init", "-q", "-b", "main")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.invalid")
    git("add", ".")
    git("commit", "-qm", "base")
    shutil.copytree(FIXTURES / end, target, dirs_exist_ok=True)
    result = CliRunner().invoke(app, ["diff", "--workspace", str(repo), "--base", "HEAD", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["comparison_status"] == "comparable"
    if end == "script":
        assert payload["rows"] == []
    else:
        row, = [r for r in payload["rows"] if r["subject"].endswith("/release.yml")]
        assert row["direction"] == "widened"
        assert "secrets: inherit" in row["after"]
        assert "sourceforge-mirror.yml" in row["after"]
        assert len(payload["rows"]) == 2  # The new workflow must not disappear.


def test_reduced_fixture_preserves_the_actual_permission_transition():
    before = yaml.safe_load((FIXTURES / "base/release.yml").read_text())
    after = yaml.safe_load((FIXTURES / "delegation/release.yml").read_text())
    assert before.get("permissions") == after.get("permissions")
    assert after["jobs"]["sourceforge"]["secrets"] == "inherit"
