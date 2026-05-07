from __future__ import annotations

from pathlib import Path

from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.models import (
    Finding,
    SourceReference,
    Tool,
    parse_confidence,
    parse_severity,
)
from agents_shipgate.core.patches import Patch


def tool_finding(
    *,
    tool: Tool,
    check_id: str,
    title: str,
    severity: str,
    category: str,
    evidence: dict[str, object],
    confidence: str,
    recommendation: str,
    context: ScanContext,
    patches: list[Patch] | None = None,
) -> Finding:
    return Finding(
        check_id=check_id,
        title=title,
        severity=parse_severity(severity),
        category=category,
        tool_id=tool.id,
        tool_name=tool.name,
        agent_id=context.agent.id,
        evidence=evidence,
        confidence=parse_confidence(confidence),
        source=SourceReference(
            type=tool.source_type,
            ref=tool.source_ref,
            location=tool.source_location,
            path=tool.source_path,
            start_line=tool.source_start_line,
            end_line=tool.source_end_line,
            start_column=tool.source_start_column,
            pointer=tool.source_pointer,
        ),
        recommendation=recommendation,
        patches=patches,
    )


def agent_finding(
    *,
    check_id: str,
    title: str,
    severity: str,
    category: str,
    evidence: dict[str, object],
    confidence: str,
    recommendation: str,
    context: ScanContext,
    patches: list[Patch] | None = None,
) -> Finding:
    return Finding(
        check_id=check_id,
        title=title,
        severity=parse_severity(severity),
        category=category,
        agent_id=context.agent.id,
        evidence=evidence,
        confidence=parse_confidence(confidence),
        source=SourceReference(type="manifest", ref=_manifest_ref(context.config_path)),
        recommendation=recommendation,
        patches=patches,
    )


def _manifest_ref(config_path: Path) -> str:
    return config_path.name
