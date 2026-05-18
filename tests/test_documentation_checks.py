from pathlib import Path

from agents_shipgate.checks.documentation import run
from agents_shipgate.config.loader import load_manifest
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.domain import (
    Agent,
    Tool,
)


def _context_for_tool(tool: Tool) -> ScanContext:
    manifest = load_manifest(Path("samples/support_refund_agent/shipgate.yaml"))
    return ScanContext(
        manifest=manifest,
        agent=Agent(id="agent:test/test", name="test"),
        tools=[tool],
        config_path=Path("shipgate.yaml"),
    )


def test_secret_check_does_not_flag_required_api_key_docs():
    tool = Tool(
        id="tool:docs",
        name="docs",
        source_type="mcp",
        description="Call this endpoint with api_key: required in the request metadata.",
    )

    findings = run(_context_for_tool(tool))

    assert [finding.check_id for finding in findings] == []


def test_secret_check_flags_secret_like_labeled_value():
    tool = Tool(
        id="tool:docs",
        name="docs",
        source_type="mcp",
        description="Call this endpoint with api_key: abcdefghijklmnop1234567890.",
    )

    findings = run(_context_for_tool(tool))

    assert [finding.check_id for finding in findings] == ["SHIP-DOC-SECRET-IN-DESCRIPTION"]
    assert findings[0].severity == "medium"


def test_injection_check_does_not_flag_benign_term_mentions():
    tool = Tool(
        id="tool:docs",
        name="docs",
        source_type="mcp",
        description="Stores notes about system prompt evaluations and developer message reviews.",
    )

    findings = run(_context_for_tool(tool))

    assert [finding.check_id for finding in findings] == []


def test_injection_check_does_not_flag_benign_you_are_now_phrase():
    tool = Tool(
        id="tool:docs",
        name="docs",
        source_type="mcp",
        description="You are now ready to fetch order details after authentication.",
    )

    findings = run(_context_for_tool(tool))

    assert [finding.check_id for finding in findings] == []
