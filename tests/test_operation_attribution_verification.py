from __future__ import annotations

import hashlib
import json
import subprocess

import pytest
import yaml
from test_current_control import _live, _verify
from test_openapi_operation_attribution import scan, spec, workspace

from agents_shipgate.cli.verify import orchestrator
from agents_shipgate.core.current_control import CurrentControlUnavailable, read_current_control


def git(root, *args):
    return subprocess.run(
        ["git", *args], cwd=root, check=True, text=True, capture_output=True
    ).stdout.strip()


def repository(tmp_path):
    root = tmp_path / "repo"
    workspace(root)
    (root / ".gitignore").write_text("agents-shipgate-reports/\n")
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Test User")
    git(root, "config", "user.email", "test@example.test")
    git(root, "add", ".")
    git(root, "commit", "-qm", "Synthetic declared-operation base")
    return root


@pytest.mark.parametrize(
    "values,direction", [(("alpha",), "narrowed"), (("alpha", "beta", "gamma"), "widened")]
)
def test_committed_verify_reconstructs_operation_predicate_evidence(tmp_path, values, direction):
    root = repository(tmp_path)
    base = git(root, "rev-parse", "HEAD")
    (root / "api.json").write_text(json.dumps(spec(values)))
    git(root, "add", "api.json")
    git(root, "commit", "-qm", "Change declared targets")
    _verify(root, base=base)
    report = json.loads((root / "agents-shipgate-reports/report.json").read_text())
    (row,) = report["tool_surface_diff"]["operation_comparisons"]
    assert row["evidence_source"] == "reconstructed_git_base"
    assert row["base_tree"] == git(root, "rev-parse", base + "^{tree}")
    assert row["declared_target_domain"] == direction, row
    assert row["approval_predicate"] == "standing_weakness"
    assert row["before"]["operation"]["inputs"] != row["after"]["operation"]["inputs"]
    assert row["finding_exclusion_eligible"] is False
    plan = json.loads((root / "agents-shipgate-reports/verification-plan.json").read_text())
    dependencies = plan["inputs"]["options"]["dependency_inputs"]["files"]
    assert "api.json" in {item["path"] for item in dependencies}
    read_current_control(root / "agents-shipgate-reports", live=_live(root))


def test_self_consistent_forged_cache_is_rebuilt(tmp_path, monkeypatch):
    root = repository(tmp_path)
    base = git(root, "rev-parse", "HEAD")
    cache = tmp_path / "cache" / "entry" / "report.json"
    monkeypatch.setattr(orchestrator, "_cache_report_path", lambda **kwargs: cache)
    _verify(root, base=base)
    cached = json.loads(cache.read_text())
    cached["tool_surface_facts"]["operation_attributions"][0]["operation"]["declared_targets"] = [
        "https://example.test/forged"
    ]
    cache.write_text(json.dumps(cached))
    # Both files are writable by a local report producer; they prove no origin.
    cache.with_suffix(".sha256").write_text(hashlib.sha256(cache.read_bytes()).hexdigest())
    assert orchestrator._cache_report_valid(cache)
    _verify(root, base=base)
    report = json.loads((root / "agents-shipgate-reports/report.json").read_text())
    (row,) = report["tool_surface_diff"]["operation_comparisons"]
    assert row["declared_target_domain"] == "unchanged", row
    assert "forged" not in json.dumps(row)


def test_action_approval_declaration_is_attributed_to_its_actual_pointer(tmp_path):
    root = repository(tmp_path)
    base = git(root, "rev-parse", "HEAD")
    manifest = yaml.safe_load((root / "shipgate.yaml").read_text())
    # Synthetic test declaration: exercises an existing input route, not a
    # generated declaration or an assertion about a real deployed agent.
    manifest["action_surface"] = {
        "actions": [{"tool": "remove_document", "approval": {"required": True}}]
    }
    (root / "shipgate.yaml").write_text(yaml.safe_dump(manifest))
    git(root, "add", "shipgate.yaml")
    git(root, "commit", "-qm", "Synthetic approval declaration")
    _verify(root, base=base)
    report = json.loads((root / "agents-shipgate-reports/report.json").read_text())
    (row,) = report["tool_surface_diff"]["operation_comparisons"]
    assert row["approval_predicate"] == "resolved_by_declaration", row
    pointers = row["after"]["predicates"][2]["contributing_pointers"]
    assert "/action_surface/actions/0/approval" in pointers
    assert row["finding_exclusion_eligible"] is False


@pytest.mark.parametrize("changed_file", ["api.json", "shipgate.yaml"])
def test_live_control_rejects_ignored_source_drift(tmp_path, changed_file):
    root = repository(tmp_path)
    _verify(root, archive_head=False)
    reports = root / "agents-shipgate-reports"
    read_current_control(reports, live=_live(root))
    git(root, "update-index", "--assume-unchanged", changed_file)
    if changed_file == "api.json":
        (root / changed_file).write_text(json.dumps(spec(("gamma",))))
    else:
        manifest = yaml.safe_load((root / changed_file).read_text())
        manifest["policies"]["require_approval_for_tools"] = ["remove_document"]
        (root / changed_file).write_text(yaml.safe_dump(manifest))
    with pytest.raises(CurrentControlUnavailable):
        read_current_control(reports, live=_live(root))


def test_redaction_disables_comparison_and_removes_evidence_digests(tmp_path):
    root = tmp_path / "repo"
    document = spec()
    document["servers"] = [{"url": "https://example.test/sk-live-1234567890abcdefghijklmnopqrstuv"}]
    workspace(root, document)
    report = scan(root, tmp_path / "reports")
    (row,) = report.tool_surface_facts.operation_attributions
    assert row.status == "redacted", row
    assert not row.operation.declared_targets
    assert not row.operation.inputs
    payload = (tmp_path / "reports/report.json").read_text()
    assert "1234567890abcdefghijklmnopqrstuv" not in payload
    assert hashlib.sha256((root / "api.json").read_bytes()).hexdigest() not in json.dumps(
        row.model_dump()
    )
