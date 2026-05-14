from pathlib import Path

from agents_shipgate.checks._framework_common import (
    DynamicSurfaceConfig,
    collect_dynamic_surface_findings,
)
from agents_shipgate.config.schema import AgentsShipgateManifest
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.models import Agent, SourceReference


def test_collect_dynamic_surface_findings_applies_per_surface_config():
    context = _context()
    surfaces = [
        {"kind": "skip", "source_ref": "workflow.json#node:skip"},
        {"kind": "emit", "source_ref": "workflow.json#node:emit"},
    ]

    findings = collect_dynamic_surface_findings(
        context,
        surfaces=surfaces,
        config=DynamicSurfaceConfig(
            check_id="SHIP-TEST-DYNAMIC",
            title="Dynamic surface",
            severity="high",
            category="test",
            confidence="medium",
            evidence_key="surface",
            recommendation="Declare the surface.",
            suppress=lambda surface: surface["kind"] == "skip",
            explicit_inventory_value=lambda _surface: True,
            source_for=lambda surface: SourceReference(
                type="workflow",
                ref=surface["source_ref"],
                path="workflow.json",
                pointer="/nodes/emit",
            ),
        ),
    )

    assert len(findings) == 1
    assert findings[0].check_id == "SHIP-TEST-DYNAMIC"
    assert findings[0].agent_id == "agent:test"
    assert findings[0].evidence == {
        "surface": {"kind": "emit", "source_ref": "workflow.json#node:emit"},
        "explicit_inventory": True,
    }
    assert findings[0].source is not None
    assert findings[0].source.type == "workflow"
    assert findings[0].source.pointer == "/nodes/emit"


def test_collect_dynamic_surface_findings_defaults_to_manifest_source():
    findings = collect_dynamic_surface_findings(
        _context(),
        surfaces=[{"kind": "dynamic"}],
        config=DynamicSurfaceConfig(
            check_id="SHIP-TEST-DYNAMIC",
            title="Dynamic surface",
            severity="medium",
            category="test",
            confidence="high",
            evidence_key="surface",
            recommendation="Declare the surface.",
            suppress=lambda _surface: False,
        ),
    )

    assert findings[0].evidence["explicit_inventory"] is False
    assert findings[0].source is not None
    assert findings[0].source.type == "manifest"
    assert findings[0].source.ref == "shipgate.yaml"


def _context() -> ScanContext:
    manifest = AgentsShipgateManifest.model_validate(
        {
            "version": "0.1",
            "project": {"name": "test"},
            "agent": {"name": "test-agent", "declared_purpose": ["test"]},
            "environment": {"target": "local"},
            "tool_sources": [{"id": "tools", "type": "mcp", "path": "tools.json"}],
        }
    )
    return ScanContext(
        manifest=manifest,
        agent=Agent(id="agent:test", name="test-agent"),
        tools=[],
        config_path=Path("shipgate.yaml"),
    )
