"""Offline Unit Tests for CrewAI Delegation & Tool-Failure Anti-Pattern Example."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents_shipgate.cli.scan import run_scan
from examples.crewai_delegation_antipattern.hardened_crew import (
    create_hardened_crew,
    safe_fetch_vendor_invoice,
)
from examples.crewai_delegation_antipattern.unbounded_crew import create_unbounded_crew


def test_unbounded_crew_has_anti_pattern_characteristics() -> None:
    """Verify that the unbounded crew model lacks circuit breakers and enables delegation."""
    crew = create_unbounded_crew()
    assert len(crew.agents) == 2

    researcher = crew.agents[0]
    manager = crew.agents[1]

    # Verify anti-pattern attributes
    assert researcher.allow_delegation is True
    assert manager.allow_delegation is True
    assert getattr(researcher, "max_execution_time", None) is None
    assert getattr(manager, "max_execution_time", None) is None


def test_hardened_crew_enforces_circuit_breakers_and_disabled_delegation() -> None:
    """Verify that the hardened crew model enforces max_iter, max_execution_time, and disabled delegation."""
    crew = create_hardened_crew()
    assert len(crew.agents) == 1

    agent = crew.agents[0]
    assert agent.allow_delegation is False
    assert agent.max_iter == 5
    assert agent.max_execution_time == 60


def test_guarded_tool_returns_structured_json_error_on_exception() -> None:
    """Verify that safe_fetch_vendor_invoice catches errors and returns a structured JSON payload."""
    result_str = safe_fetch_vendor_invoice._run(invoice_id="INV-9901")
    assert isinstance(result_str, str)

    payload = json.loads(result_str)
    assert payload["status"] == "error"
    assert payload["error_code"] == "VENDOR_API_TIMEOUT"
    assert payload["retryable"] is True
    assert "INV-9901" in payload["message"]


def test_shipgate_scan_verifies_hardened_crewai_example(tmp_path: Path) -> None:
    """Verify that agents-shipgate scan processes shipgate.yaml in the example directory cleanly."""
    example_dir = Path(__file__).parents[1] / "examples" / "crewai_delegation_antipattern"
    config_path = example_dir / "shipgate.yaml"

    assert config_path.exists(), f"Config missing at {config_path}"

    report, exit_code = run_scan(
        config_path=config_path,
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    assert report.frameworks["crewai"]["agent_count"] >= 1
    inventory = {tool["name"]: tool for tool in report.tool_inventory}
    assert "safe_fetch_vendor_invoice" in inventory
