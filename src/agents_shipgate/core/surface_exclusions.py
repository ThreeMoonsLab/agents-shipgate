"""Derive the exclusion ledger from the facts a run already produced.

One builder, not six emitters. Every narrowing stage already records what it
removed — ``binding_surface_facts`` partitions the catalog, ``source_warnings``
names the sources a loader could not read, the semantic assessment says which
surfaces it could not prove complete — and #403 is not that those facts are
missing. It is that each was represented differently, so nothing could ask the
one question that matters across all of them: *did the release decision see
this?*

Deriving rather than emitting is the point. An emitter has to be remembered at
each call site, which is exactly the habit that produced ``unbound_tools: 1``
beside ``gap_count: 0``: the count was written, the consequence was not. A
derivation reads the same facts the decision read and cannot fall out of step
with them, and :func:`agents_shipgate.core.semantic_consistency` then asserts
the two agree.

See :mod:`agents_shipgate.schemas.exclusions` for the record and the
conservation invariant it carries.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agents_shipgate.schemas.detect import DetectResult
from agents_shipgate.schemas.exclusions import (
    SurfaceExclusion,
    SurfaceExclusionLedger,
)
from agents_shipgate.schemas.report import EvidenceGap, ReadinessReport


def catalog_subject(row: Mapping[str, Any]) -> str:
    """The subject string a tool-catalog row is named by.

    Shared with :mod:`agents_shipgate.ci.release_decision` so a ledger entry
    and the evidence gap that accounts for it are joinable by value. Two
    spellings of the same tool would make the conservation check pass by
    accident in one direction and fail spuriously in the other.
    """

    name = str(row.get("name") or row.get("tool_id") or "")
    provider = row.get("provider")
    return f"{name} [{provider}]" if provider else name


def _catalog_by_id(report: ReadinessReport) -> dict[str, Mapping[str, Any]]:
    return {
        str(row["tool_id"]): row
        for row in report.tool_catalog
        if isinstance(row, Mapping) and row.get("tool_id")
    }


def _gap_subjects(gaps: Sequence[EvidenceGap], kinds: set[str]) -> set[str]:
    return {gap.subject for gap in gaps if gap.kind in kinds}


_BINDING_GAP_KINDS = {
    "missing_binding_evidence",
    "partial_binding_evidence",
    "unresolved_bound_tool",
    "unresolved_agent_binding",
    "conflicting_binding_evidence",
    "incomplete_handoff_graph",
    "invalid_tool_binding",
}


def build_surface_exclusions(report: ReadinessReport) -> SurfaceExclusionLedger:
    """Every subject this run removed from the analysed surface.

    Call after ``report.release_decision`` is built: accounting is decided by
    whether a gap row names the subject, so the gaps have to exist first.
    """

    decision = report.release_decision
    gaps: Sequence[EvidenceGap] = (
        decision.evidence_coverage.evidence_gaps if decision is not None else ()
    )
    binding_gap_subjects = _gap_subjects(gaps, _BINDING_GAP_KINDS)

    entries: list[SurfaceExclusion] = []
    entries.extend(_binding_exclusions(report, binding_gap_subjects))
    entries.extend(_surface_completeness_exclusions(gaps))
    entries.extend(_adapter_parse_exclusions(gaps))
    return SurfaceExclusionLedger.from_entries(entries)


def _binding_exclusions(
    report: ReadinessReport,
    gap_subjects: set[str],
) -> list[SurfaceExclusion]:
    """Catalog tools the root-reachable graph did not carry into analysis.

    ``possible`` and ``unbound`` are both exclusions — everything downstream
    of the graph is narrowed to ``reachable_tool_ids`` — but they are not the
    same claim, so they carry different reasons and, usually, different
    accounting.
    """

    graph = report.binding_surface_facts
    catalog = _catalog_by_id(report)
    newly_unbound = (
        set(report.binding_surface_diff.added_unbound_tool_ids)
        if report.binding_surface_diff.enabled
        else set()
    )
    entries: list[SurfaceExclusion] = []
    for tool_id in sorted(graph.possible_tool_ids):
        row = catalog.get(tool_id, {"tool_id": tool_id})
        subject = catalog_subject(row)
        entries.append(
            SurfaceExclusion(
                stage="binding",
                subject=subject,
                reason="incomplete_binding_edge",
                source_ref=_row_ref(row),
                detail=(
                    "A binding edge reaches this tool but does not prove it "
                    "complete, so it is outside the proven capability surface."
                ),
                accounting=(
                    "evidence_gap" if subject in gap_subjects else "not_claimed"
                ),
            )
        )
    for tool_id in sorted(graph.unbound_tool_ids):
        row = catalog.get(tool_id, {"tool_id": tool_id})
        subject = catalog_subject(row)
        introduced = tool_id in newly_unbound
        entries.append(
            SurfaceExclusion(
                stage="binding",
                subject=subject,
                reason="newly_unbound_tool" if introduced else "unbound_tool",
                source_ref=_row_ref(row),
                detail=(
                    (
                        "This change put the tool in the catalog and left it "
                        "unbound from the root agent, so no check judged it."
                    )
                    if introduced
                    else (
                        "The tool is in the catalog and no edge binds it to "
                        "the root agent; nothing in the repository claims it "
                        "as reachable capability."
                    )
                ),
                accounting=(
                    "evidence_gap" if subject in gap_subjects else "not_claimed"
                ),
            )
        )
    return entries


def _surface_completeness_exclusions(
    gaps: Sequence[EvidenceGap],
) -> list[SurfaceExclusion]:
    """Tools whose own surface enumeration could not be established.

    An ``incomplete_surface`` tool is analysed, but only the part of it that
    was read: a toolkit factory, a wildcard MCP export, an adapter that
    cannot say what it enumerated. The excluded subject is the unread
    remainder, which has no name — so the tool names it.
    """

    return [
        SurfaceExclusion(
            stage="surface_completeness",
            subject=gap.subject,
            reason="surface_not_enumerated",
            source_ref=gap.source_ref,
            detail=(
                "The tool's own surface could not be established as complete, "
                "so an unknown remainder of it was never analysed."
            ),
            accounting="evidence_gap",
        )
        for gap in gaps
        if gap.kind == "incomplete_surface"
    ]


def _adapter_parse_exclusions(gaps: Sequence[EvidenceGap]) -> list[SurfaceExclusion]:
    """Declared inputs a loader read only in part."""

    return [
        SurfaceExclusion(
            stage="adapter_parse",
            subject=gap.subject,
            reason="source_degraded",
            source_ref=gap.source_ref,
            detail=(
                "A source loader degraded while reading a declared input, so "
                "part of that input never entered the catalog."
            ),
            accounting="evidence_gap",
        )
        for gap in gaps
        if gap.kind == "source_warning"
    ]


def _row_ref(row: Mapping[str, Any]) -> str | None:
    for key in ("source_ref", "source_path", "source_pointer"):
        value = row.get(key)
        if value:
            return str(value)
    source_id = row.get("source_type")
    return str(source_id) if source_id else None


def build_detect_exclusions(result: DetectResult) -> SurfaceExclusionLedger:
    """Everything ``detect`` decided not to look at, in the shared shape.

    Discovery narrows twice and reported each narrowing its own way: a capped
    Python parse as ``python_parse_truncated``, an unresolvable manifest scope
    as ``agent_scope``, a glob-matched file the real adapter rejects as an
    ``excluded_sources`` dict. All three are the same event — a subject left
    the surface — and the routing consequence is the one thing a caller needs
    from all three at once.

    Derived from a finished :class:`DetectResult` rather than emitted while it
    is assembled, for the same reason as the report ledger: the facts already
    exist, and a derivation cannot disagree with them.
    """

    entries: list[SurfaceExclusion] = []
    signals = result.workspace_signals
    if result.python_parse_truncated:
        entries.append(
            SurfaceExclusion(
                stage="discovery",
                subject=".",
                reason="walk_capped",
                source_ref=None,
                detail=(
                    f"The Python parse stopped at {signals.python_file_count} "
                    f"of {signals.python_file_total} files, so this "
                    "classification describes the part of the workspace that "
                    "was read."
                ),
                accounting="route_blocked",
            )
        )
    if result.agent_scope == "ambiguous":
        # Every candidate is excluded from being *the* scope until a human
        # picks one; naming them individually is what makes the list routable.
        entries.extend(
            SurfaceExclusion(
                stage="scope_resolution",
                subject=candidate.path,
                reason="scope_contested",
                source_ref=candidate.marker,
                detail=(
                    "This project defines agents and is not the only one, so "
                    "no single manifest scope was resolved for it."
                ),
                accounting="route_blocked",
            )
            for candidate in result.agent_project_candidates
        )
    elif result.agent_scope == "unknown":
        entries.append(
            SurfaceExclusion(
                stage="scope_resolution",
                subject=".",
                reason="scope_unknown",
                source_ref=None,
                detail=(
                    "Discovery was capped before it could tell whether one "
                    f"manifest describes this workspace; {signals.project_root_count} "
                    "candidate project scopes exist."
                ),
                accounting="route_blocked",
            )
        )
    for excluded in result.excluded_sources:
        path = str(excluded.get("path") or "")
        if not path:
            continue
        entries.append(
            SurfaceExclusion(
                stage="discovery",
                subject=path,
                reason="source_rejected",
                source_ref=str(excluded.get("type") or "") or None,
                detail=str(excluded.get("reason") or "")
                or "A glob-matched candidate the real input adapter rejects.",
                accounting="not_claimed",
            )
        )
    return SurfaceExclusionLedger.from_entries(entries)


__all__ = [
    "build_detect_exclusions",
    "build_surface_exclusions",
    "catalog_subject",
]
