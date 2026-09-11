"""#543: a rejected fixture identity must not become an arbitrary edit later."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from agents_shipgate.cli.discovery import identity_recovery
from agents_shipgate.cli.discovery.identity_recovery import classify_agent_name
from agents_shipgate.cli.discovery.signals import detect_workspace
from agents_shipgate.cli.main import app
from agents_shipgate.core.errors import DiscoveryError

runner = CliRunner()
NON_PRODUCT_PATHS = [
    "eval/test_agent.py",
    "skills/recipe/resources/templates/app/agent.py",
]


def _write(workspace: Path, name: str, text: str) -> Path:
    path = workspace / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _agent(workspace: Path, path: str, name: str = "FixtureAgent") -> Path:
    return _write(workspace, path,
        f'from google.adk.agents import Agent\nroot_agent = Agent(name="{name}")\n')


def _tools(workspace: Path) -> None:
    _write(workspace, "mcp.tools.json", json.dumps({"tools": [{
        "name": "lookup", "description": "Read local fixture metadata.",
        "inputSchema": {"type": "object", "properties": {}},
    }]}))


def _manifest(workspace: Path) -> Path:
    _tools(workspace)
    return _write(workspace, "shipgate.yaml", yaml.safe_dump({
        "version": "0.1", "project": {"name": "identity-fixture"},
        "agent": {"name": "CHANGE_ME", "declared_purpose": ["Read fixture metadata"]},
        "environment": {"target": "local"},
        "tool_sources": [{"id": "lookup", "type": "mcp", "path": "mcp.tools.json"}],
    }, sort_keys=False))


def _doctor(manifest: Path, *, workspace: Path | None = None) -> dict:
    args = ["doctor", "--config", str(manifest), "--json"]
    if workspace is not None:
        args.extend(["--workspace", str(workspace)])
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    payloads = json.loads(result.stdout)
    return next(p for p in payloads if Path(p["config"]).resolve() == manifest.resolve())


def _human_identity(payload: dict) -> None:
    control = payload["control"]
    assert control["control_state"] == "human_review_required"
    assert not any(control["permissions"].values())
    assert control["next_action"]["command"] is None
    assert len(payload["next_actions"]) == 1
    action = payload["next_actions"][0]
    assert action["kind"] == "review"
    assert action["why"] == control["next_action"]["why"]
    assert "agent.name" in action["why"]
    assert "test/template" in action["why"]
    assert "arbitrary nonempty" in action["why"]
    for diagnostic in payload.get("diagnostics", []):
        for offered in diagnostic["next_actions"]:
            if "agent.name" in offered["why"]:
                assert offered["kind"] == "review"


@pytest.mark.parametrize("source", NON_PRODUCT_PATHS)
def test_init_then_purpose_only_edit_preserves_identity_obligation(tmp_path, source):
    _agent(tmp_path, source)
    _tools(tmp_path)
    result = runner.invoke(app, ["init", "--workspace", str(tmp_path), "--write", "--json"])
    assert result.exit_code == 0, result.output
    _human_identity(json.loads(result.stdout))
    manifest = tmp_path / "shipgate.yaml"
    data = yaml.safe_load(manifest.read_text())
    assert data["agent"]["name"] == "CHANGE_ME"
    # A fixture supplies only purpose. Identity remains the actual unresolved field.
    data["agent"]["declared_purpose"] = ["Read fixture metadata"]
    manifest.write_text(yaml.safe_dump(data, sort_keys=False))
    _human_identity(_doctor(manifest))


@pytest.mark.parametrize("product", ["aaa_product.py", "zzz_product.py"])
def test_product_declaration_remains_agent_recoverable(tmp_path, product):
    _agent(tmp_path, NON_PRODUCT_PATHS[0])
    _agent(tmp_path, product, "BillingAssistant")
    manifest = _manifest(tmp_path)
    payload = _doctor(manifest)
    assert payload["control"]["control_state"] == "agent_action_required"
    assert not any(payload["control"]["permissions"].values())
    action = payload["next_actions"][0]
    assert action["kind"] == "edit"
    assert product in action["why"]
    assert action["path"] == payload["control"]["next_action"]["path"]
    assert "supported product declaration" in action["expects"]


def test_doctor_recomputes_identity_without_manifest_change(tmp_path):
    _agent(tmp_path, NON_PRODUCT_PATHS[0])
    manifest = _manifest(tmp_path)
    original = manifest.read_bytes()
    before = _doctor(manifest)
    _human_identity(before)
    # A saved result is deliberately stale; doctor must not consume it.
    _write(tmp_path, "detect.json", detect_workspace(tmp_path).model_dump_json())
    product = _agent(tmp_path, "agent.py", "BillingAssistant")
    after = _doctor(manifest)
    assert after["control"]["control_state"] == "agent_action_required"
    assert after["control"]["input_id"] != before["control"]["input_id"]
    product.unlink()
    restored = _doctor(manifest)
    _human_identity(restored)
    assert restored["control"]["input_id"] == before["control"]["input_id"]
    assert manifest.read_bytes() == original


def test_workspace_doctor_does_not_borrow_a_sibling_name(tmp_path):
    scoped = tmp_path / "scoped"
    _agent(scoped, NON_PRODUCT_PATHS[0])
    manifest = _manifest(scoped)
    _agent(tmp_path / "sibling", "agent.py", "SiblingAssistant")
    _human_identity(_doctor(manifest, workspace=tmp_path))


@pytest.mark.parametrize("condition", ["cap", "ambiguous", "scope_cap", "unparsed", "unknown"])
def test_incomplete_discovery_is_not_non_product_only(tmp_path, condition):
    _agent(tmp_path, NON_PRODUCT_PATHS[0])
    detected = detect_workspace(tmp_path)
    assert classify_agent_name(detected).state == "non_product_only"
    changes = {
        "cap": {"python_parse_truncated": True},
        "ambiguous": {"agent_scope": "ambiguous"},
        "scope_cap": {"agent_scope_truncated": True},
        "unparsed": {"workspace_signals": detected.workspace_signals.model_copy(
            update={"python_file_total": detected.workspace_signals.python_file_total + 1})},
        "unknown": {"agent_scope": "unknown"},
    }
    assert classify_agent_name(detected.model_copy(update=changes[condition])).state == "unresolved"


@pytest.mark.parametrize("broken", [b"not valid python !!!", b"\xff"])
def test_unparsed_python_prevents_conclusive_identity_classification(tmp_path, broken):
    _agent(tmp_path, NON_PRODUCT_PATHS[0])
    (tmp_path / "broken.py").write_bytes(broken)
    detected = detect_workspace(tmp_path)
    assert not detected.python_parse_truncated
    assert detected.workspace_signals.python_file_count < detected.workspace_signals.python_file_total
    assert classify_agent_name(detected).state == "unresolved"


def test_real_cap_is_not_reoffered_as_unusable_doctor_retry(tmp_path, monkeypatch):
    _agent(tmp_path, NON_PRODUCT_PATHS[0])
    _agent(tmp_path, "zzz.py", "ProductAssistant")
    manifest = _manifest(tmp_path)
    monkeypatch.setattr(identity_recovery, "detect_workspace",
                        lambda workspace: detect_workspace(workspace, max_python_files=1))
    payload = _doctor(manifest)
    assert "test/template" not in json.dumps(payload["control"])
    assert "--max-python-files" not in json.dumps(payload["next_actions"])


def test_discovery_failure_preserves_existing_doctor_recovery(tmp_path, monkeypatch):
    manifest = _manifest(tmp_path)
    def refuse(workspace):
        raise DiscoveryError("private failure detail")
    monkeypatch.setattr(identity_recovery, "detect_workspace", refuse)
    payload = _doctor(manifest)
    assert not any(payload["control"]["permissions"].values())
    assert "private failure detail" not in json.dumps(payload)
    assert "test/template" not in json.dumps(payload["control"])


def test_resolved_identity_does_not_trigger_extra_discovery(tmp_path, monkeypatch):
    manifest = _manifest(tmp_path)
    manifest.write_text(manifest.read_text().replace("CHANGE_ME", "ReviewedFixture"))
    def unexpected(workspace):
        pytest.fail("No identity placeholder; no discovery is needed")
    monkeypatch.setattr(identity_recovery, "detect_workspace", unexpected)
    assert _doctor(manifest)["control"]["control_state"] == "agent_action_required"


@pytest.mark.parametrize("flags", [[], ["--minimal"], ["--minimal", "--write"]])
def test_dry_run_and_minimal_keep_existing_precedence(tmp_path, flags):
    _agent(tmp_path, NON_PRODUCT_PATHS[0])
    result = runner.invoke(app, ["init", "--workspace", str(tmp_path), "--json", *flags])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert not any(payload["control"]["permissions"].values())
    assert "test/template" not in payload["next_actions"][0]["why"]
    if "--write" not in flags:
        assert payload["control"]["control_state"] == "agent_action_required"
        assert not (tmp_path / "shipgate.yaml").exists()


def test_existing_manifest_identity_controls_init_not_new_template(tmp_path):
    _agent(tmp_path, NON_PRODUCT_PATHS[0])
    manifest = _manifest(tmp_path)
    before = manifest.read_bytes()
    result = runner.invoke(app, ["init", "--workspace", str(tmp_path), "--write", "--json"])
    assert result.exit_code == 2, result.output
    _human_identity(json.loads(result.stdout))
    assert manifest.read_bytes() == before


def test_source_failure_keeps_identity_in_agent_mode_error(tmp_path, monkeypatch):
    _agent(tmp_path, NON_PRODUCT_PATHS[0])
    manifest = _manifest(tmp_path)
    (tmp_path / "mcp.tools.json").write_text("not JSON")
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    result = runner.invoke(app, ["doctor", "--config", str(manifest), "--json"])
    assert result.exit_code == 3, result.output
    error = json.loads(next(line for line in result.stderr.splitlines() if line.startswith("{")))
    _human_identity(error)


def test_invalid_manifest_outranks_identity_discovery(tmp_path, monkeypatch):
    manifest = _write(tmp_path, "shipgate.yaml", "agent: {name: CHANGE_ME}\n")
    def unexpected(workspace):
        pytest.fail("A loader rejection must not trigger name discovery")
    monkeypatch.setattr(identity_recovery, "detect_workspace", unexpected)
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    result = runner.invoke(app, ["doctor", "--config", str(manifest), "--json"])
    assert result.exit_code == 2, result.output
    error = json.loads(next(line for line in result.stderr.splitlines() if line.startswith("{")))
    assert error["control"]["control_state"] == "agent_action_required"


def test_provisional_setup_keeps_identity_and_durable_choice_within_control_budget(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    _agent(tmp_path, NON_PRODUCT_PATHS[0])
    _tools(tmp_path)
    result = runner.invoke(app, ["init", "--workspace", str(tmp_path), "--local-review", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    _human_identity(payload)
    assert "adopt this provisional setup durably" in payload["control"]["next_action"]["why"]
    assert len(payload["control"]["next_action"]["why"].encode()) <= 400


def test_cross_module_name_is_evidence_without_reopening_the_declaration_path(tmp_path):
    manifest = _manifest(tmp_path)
    _write(tmp_path, "config.py", 'AGENT_NAME = "BillingAssistant"\n')
    _write(tmp_path, "agent.py", 'from google.adk.agents import Agent\n'
           'from config import AGENT_NAME\nroot_agent = Agent(name=AGENT_NAME)\n')
    before = _doctor(manifest)
    assert before["next_actions"][0]["kind"] == "edit"
    _write(tmp_path, "config.py", 'AGENT_NAME = "SupportAssistant"\n')
    after = _doctor(manifest)
    assert after["next_actions"][0]["kind"] == "edit"
    assert before["control"]["input_id"] != after["control"]["input_id"]


def test_init_refused_scope_outranks_name_obligation(tmp_path):
    for project in ("first", "second"):
        _write(tmp_path / project, "pyproject.toml", f'[project]\nname = "{project}"\n')
        _agent(tmp_path / project, "agent.py", f"{project}Assistant")
    result = runner.invoke(app, ["init", "--workspace", str(tmp_path), "--write", "--json"])
    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["manifest_status"] == "refused_unresolved_scope"
    assert "test/template" not in payload["next_actions"][0]["why"]
    assert not any(payload["control"]["permissions"].values())


@pytest.mark.parametrize("product_at_target", [True, False])
@pytest.mark.parametrize("command", ["doctor", "init"])
def test_linked_manifest_recovers_from_loaded_project_not_link_directory(
    tmp_path, product_at_target, command,
):
    actual, alias = tmp_path / "actual", tmp_path / "alias"
    actual_source = "agent.py" if product_at_target else NON_PRODUCT_PATHS[0]
    alias_source = NON_PRODUCT_PATHS[0] if product_at_target else "agent.py"
    _agent(actual, actual_source, "ActualAssistant")
    _agent(alias, alias_source, "UnrelatedAssistant")
    target = _manifest(actual)
    link = alias / "shipgate.yaml"
    link.symlink_to(target)
    if command == "doctor":
        payload = _doctor(link)
    else:
        result = runner.invoke(app, ["init", "--workspace", str(alias), "--write", "--json"])
        assert result.exit_code == 2, result.output
        payload = json.loads(result.stdout)
    if product_at_target:
        assert payload["control"]["control_state"] == "agent_action_required"
        if command == "doctor":
            action = payload["next_actions"][0]
            assert str(actual / "agent.py") in action["why"]
            assert str(alias / "agent.py") not in action["why"]
            assert action["path"].startswith(str(link))
    else:
        _human_identity(payload)
    assert not any(payload["control"]["permissions"].values())
    assert link.is_symlink()
    assert yaml.safe_load(target.read_text())["agent"]["name"] == "CHANGE_ME"


def test_linked_manifest_source_failure_keeps_actual_identity_obligation(tmp_path, monkeypatch):
    actual, alias = tmp_path / "actual", tmp_path / "alias"
    _agent(actual, NON_PRODUCT_PATHS[0])
    _agent(alias, "agent.py", "UnrelatedAssistant")
    target = _manifest(actual)
    (actual / "mcp.tools.json").write_text("not JSON")
    link = alias / "shipgate.yaml"
    link.symlink_to(target)
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    result = runner.invoke(app, ["doctor", "--config", str(link), "--json"])
    assert result.exit_code == 3, result.output
    error = json.loads(next(line for line in result.stderr.splitlines() if line.startswith("{")))
    _human_identity(error)
