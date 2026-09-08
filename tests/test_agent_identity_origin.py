"""A fixture identity must not become a reviewed product declaration (#533)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from agents_shipgate.cli.discovery.signals import detect_workspace, select_agent_name
from agents_shipgate.cli.main import app


def _agent(path: Path, name: str = "AlphaFixture") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "from google.adk.agents import LlmAgent\n"
        f"worker = LlmAgent(name={name!r})\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    "relative",
    ["tests/test_agent.py", "skills/recipe/resources/templates/app/agent.py"],
)
def test_non_product_identity_stays_visible_and_unasserted(tmp_path, relative):
    _agent(tmp_path / relative)
    detected = detect_workspace(tmp_path)
    candidate = next(c for c in detected.agent_name_candidates if c.value == "AlphaFixture")
    assert candidate.selectable is False
    assert any("only in non-product code" in reason for reason in candidate.rationale)
    assert select_agent_name(detected.agent_name_candidates) is None
    assert detected.is_agent_project is True

    result = CliRunner().invoke(
        app, ["init", "--workspace", str(tmp_path), "--write", "--json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert yaml.safe_load((tmp_path / "shipgate.yaml").read_text())["agent"]["name"] == "CHANGE_ME"
    assert payload["auto_detected"]["agent_name"] is None
    assert any(p["path"] == "agent.name" for p in payload["placeholders"])
    # Existing purpose/permission review still applies. Identity-specific
    # recovery after those are supplied is a separate follow-up (#543).
    assert payload["control"]["control_state"] == "human_review_required"
    assert payload["control"]["next_action"]["actor"] == "human"


@pytest.mark.parametrize("product_path", ["a_agent.py", "z_agent.py"])
def test_product_declaration_keeps_a_shared_name_selectable_in_either_order(tmp_path, product_path):
    _agent(tmp_path / "tests/test_agent.py", "ProductAgent")
    _agent(tmp_path / product_path, "ProductAgent")
    detected = detect_workspace(tmp_path)
    selected = select_agent_name(detected.agent_name_candidates)
    assert selected is not None and selected.value == "ProductAgent"
    assert selected.path == product_path
    assert not any("only in non-product code" in reason for reason in selected.rationale)


def test_project_name_corroboration_cannot_make_a_test_identity_selectable(tmp_path):
    project = tmp_path / "AlphaFixture"
    _agent(project / "tests/test_agent.py")
    detected = detect_workspace(project)
    candidate = next(c for c in detected.agent_name_candidates if c.value == "AlphaFixture")
    assert any("corroborated" in reason for reason in candidate.rationale)
    assert candidate.selectable is False
