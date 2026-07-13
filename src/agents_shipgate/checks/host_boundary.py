"""Host boundary category — coding-agent host configuration governance.

Diff-aware mirror of ``checks.codex_boundary`` for the HOST side: Claude
Code settings (``.claude/settings.json`` / ``.claude/settings.local.json``),
project MCP server declarations (``.mcp.json``, ``.cursor/mcp.json``,
``.vscode/mcp.json``), and GitHub workflow permission expansion
(``.github/workflows/*.yml|yaml``). Fires only when a
:class:`VerificationContext` is present — plain ``scan``
(``context.verification is None``) emits nothing.
"""

from __future__ import annotations

from agents_shipgate.core.agent_boundary import assessment_for_scan_context
from agents_shipgate.core.context import ScanContext
from agents_shipgate.schemas.common import SourceReference, parse_confidence, parse_severity
from agents_shipgate.schemas.report import Finding


def run(context: ScanContext) -> list[Finding]:
    verification = context.verification
    if verification is None:
        return []
    assessment = assessment_for_scan_context(context)
    findings: list[Finding] = []
    for violated in assessment.violations:
        if not violated.id.startswith(("HOST-", "BOUNDARY-")):
            continue
        findings.append(
            Finding(
                check_id=violated.check_id,
                title=violated.title,
                severity=parse_severity(_severity_for(violated.risk_level)),
                category="host_boundary",
                agent_id=context.agent.id,
                evidence=violated.evidence,
                confidence=parse_confidence("high"),
                provenance_kind="static_declaration",
                source=SourceReference(type="changed_file", path=violated.path),
                recommendation=violated.recommendation,
                blocks_release=violated.action == "block",
            )
        )
    return findings


def _severity_for(risk_level: str) -> str:
    return {
        "none": "info",
        "low": "low",
        "medium": "medium",
        "high": "high",
        "critical": "critical",
    }.get(risk_level, "medium")
