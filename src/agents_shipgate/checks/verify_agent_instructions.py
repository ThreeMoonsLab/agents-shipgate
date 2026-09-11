"""Deprecated instruction-semantics check; retained for compatibility (#516).

Path membership cannot establish that prose weakened an instruction. The
published ID remains registered for at least one minor-version cycle, so old
configuration and reports still resolve it, but new runs emit no findings.
Structured permission, hook, MCP and skill-command readers remain separate.
The generic trust-root route is unchanged; its prose-only boundary is #545.
"""

from __future__ import annotations

from agents_shipgate.core.context import ScanContext
from agents_shipgate.schemas.report import Finding

CHECK_ID = "SHIP-VERIFY-AGENT-INSTRUCTIONS-WEAKENED"


def run(context: ScanContext) -> list[Finding]:
    """Keep the shipped check callable without asserting prose semantics."""
    return []
