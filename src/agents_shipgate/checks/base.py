from __future__ import annotations

from pathlib import Path

from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.domain import Tool
from agents_shipgate.schemas.common import (
    ProvenanceKind,
    SourceReference,
    parse_confidence,
    parse_severity,
)
from agents_shipgate.schemas.patches import Patch
from agents_shipgate.schemas.report import Finding


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
    provenance_kind: ProvenanceKind,
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
        provenance_kind=provenance_kind,
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
    provenance_kind: ProvenanceKind,
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
        provenance_kind=provenance_kind,
        source=SourceReference(type="manifest", ref=_manifest_ref(context.config_path)),
        recommendation=recommendation,
        patches=patches,
    )


def _manifest_ref(config_path: Path) -> str:
    return config_path.name


# v0.14: framework checks (ADK, LangChain, CrewAI) fire against Tools
# whose source_type is either AST-extracted (Python code) or
# declaratively loaded (YAML config or inventory JSON). The provenance
# of a finding follows the underlying Tool: AST-extracted tools are
# subject to extraction error (`ast_extraction`), declaratively loaded
# ones are not (`static_declaration`). Used by tool-level callsites
# inside the framework checks. Agent-level callsites in those checks
# fire on declared agent-setup facts regardless of how individual
# tools were obtained, so they hard-code `static_declaration`.
_FRAMEWORK_DECLARATIVE_SOURCES = frozenset(
    {
        "google_adk_config",
        "langchain_inventory",
        "crewai_inventory",
    }
)


def framework_tool_provenance(tool: Tool) -> ProvenanceKind:
    if tool.source_type in _FRAMEWORK_DECLARATIVE_SOURCES:
        return "static_declaration"
    return "ast_extraction"
