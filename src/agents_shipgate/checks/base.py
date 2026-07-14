from __future__ import annotations

from pathlib import Path

from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.domain import Tool
from agents_shipgate.core.policy_evidence import finding_support, predicate_evidence
from agents_shipgate.schemas.common import (
    ProvenanceKind,
    SourceReference,
    parse_confidence,
    parse_severity,
)
from agents_shipgate.schemas.patches import Patch
from agents_shipgate.schemas.report import CapabilityPolicyEvidence, Finding


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
    policy_evidence_pointer: str | None = None,
    capability_refs: list[str] | None = None,
    capability_policy_evidence: CapabilityPolicyEvidence | None = None,
    capability_trace_refs: list[str] | None = None,
) -> Finding:
    support = _heuristic_support(provenance_kind)
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
        policy_evidence_source=_policy_evidence_source(
            context, policy_evidence_pointer
        ),
        capability_refs=capability_refs or [],
        capability_policy_evidence=capability_policy_evidence,
        capability_trace_refs=capability_trace_refs or [],
        support=support,
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
    policy_evidence_pointer: str | None = None,
    capability_refs: list[str] | None = None,
    capability_trace_refs: list[str] | None = None,
) -> Finding:
    # Reviewer-grade dual-source provenance: for agent-level findings
    # the primary ``source`` IS the manifest pointer. Setting
    # ``policy_evidence_source`` from the same pointer would produce
    # identical (path, start_line, pointer) tuples and force every
    # downstream renderer (packet markdown, SARIF, scenario YAML) to
    # dedupe before emitting. Skip it here so the contract carries
    # the secondary pointer only when it genuinely differs from the
    # primary (the ``tool_finding`` case, where source is the tool's
    # location and policy_evidence is the manifest).
    support = _heuristic_support(provenance_kind)
    return Finding(
        check_id=check_id,
        title=title,
        severity=parse_severity(severity),
        category=category,
        agent_id=context.agent.id,
        evidence=evidence,
        confidence=parse_confidence(confidence),
        provenance_kind=provenance_kind,
        source=_agent_finding_source(context, policy_evidence_pointer),
        policy_evidence_source=None,
        capability_refs=capability_refs or [],
        capability_trace_refs=capability_trace_refs or [],
        support=support,
        recommendation=recommendation,
        patches=patches,
    )


def _heuristic_support(provenance_kind: ProvenanceKind):
    if provenance_kind not in {"keyword_heuristic", "regex_heuristic"}:
        return None
    basis = (
        "inferred_keyword"
        if provenance_kind == "keyword_heuristic"
        else "inferred_regex"
    )
    return finding_support(
        [
            predicate_evidence(
                "heuristic_check_applicability",
                "indeterminate",
                confidence="medium",
                evidence_bases=[basis],
                policy_eligible=False,
                why="Heuristic evidence is advisory and cannot contribute to release gating.",
            )
        ]
    )


def _manifest_ref(config_path: Path) -> str:
    return config_path.name


def _agent_finding_source(
    context: ScanContext, policy_evidence_pointer: str | None
) -> SourceReference:
    """Return the primary ``Finding.source`` for an agent-level finding.

    For agent findings the "primary" source IS the manifest pointer
    (there is no underlying Tool). Structured ``path`` / ``start_line``
    / ``pointer`` carry the v0.19 reviewer-grade fields so SARIF and
    report.json show the manifest line. ``ref`` and ``location``
    intentionally stay as the bare manifest filename so ``_run_id``
    — which hashes legacy ``source.ref`` / ``source.location`` for
    backwards compatibility — does NOT churn for findings that
    previously emitted ``SourceReference(type="manifest", ref=<name>)``
    only. The new manifest pointer is reachable via the structured
    ``pointer`` field plus ``policy_evidence_source`` for downstream
    consumers; the legacy hash inputs are deliberately frozen.
    """
    ref = _manifest_ref(context.config_path)
    if policy_evidence_pointer is None:
        return SourceReference(type="manifest", ref=ref)
    line = _lookup_position(context, policy_evidence_pointer)
    return SourceReference(
        type="manifest",
        ref=ref,
        path=ref,
        start_line=line,
        pointer=policy_evidence_pointer,
    )


def _policy_evidence_source(
    context: ScanContext, policy_evidence_pointer: str | None
) -> SourceReference | None:
    """Build the secondary ``Finding.policy_evidence_source`` pointer.

    Returns ``None`` when no manifest pointer was supplied. When a
    pointer is supplied we always emit a structured ``SourceReference``
    (path + pointer), and add ``start_line`` only when the manifest
    position index can resolve it. The legacy ``location`` field stays
    None — populating it would shift ``_run_id`` for every scan that
    upgrades, and SARIF / packet renderers already prefer the
    structured fields.
    """
    if policy_evidence_pointer is None:
        return None
    ref = _manifest_ref(context.config_path)
    line = _lookup_position(context, policy_evidence_pointer)
    return SourceReference(
        type="manifest",
        ref=f"{ref}#{policy_evidence_pointer}",
        path=ref,
        start_line=line,
        pointer=policy_evidence_pointer,
    )


def _lookup_position(
    context: ScanContext, pointer: str
) -> int | None:
    pos = context.manifest_positions.lookup(pointer)
    if pos is None:
        return None
    line, _column = pos
    return line


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
