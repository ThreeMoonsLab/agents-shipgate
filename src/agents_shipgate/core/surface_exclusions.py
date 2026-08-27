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

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, cast, get_args

from agents_shipgate.core.adopter_text import (
    AGENT_ID_PATTERN,
    FINGERPRINT_PATTERN,
    OBSERVATION_ID_PATTERN,
    TOOL_ID_PATTERN,
)
from agents_shipgate.core.domain import SourceSurfaceOmission
from agents_shipgate.schemas.bindings import AgentBindingIssue, AgentBindingNode
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


def unavailable_base_subject(report: ReadinessReport) -> str:
    """The subject naming the base comparison this run could not perform.

    Lives here beside :func:`catalog_subject` for the same reason: a subject
    two modules both construct needs one function, or the join between them
    works by coincidence. The exclusion ledger points ``unverified`` rows at
    this gap, ``ci.release_decision`` emits it, and
    ``core.semantic_consistency`` checks the pair.
    """

    version = report.binding_surface_diff.base_report_schema_version
    return (
        f"base comparison (report schema {version})"
        if version
        else "base comparison"
    )


#: A derived id anywhere inside a string, not only as the whole of it, with the
#: noun each shape names so a guard can say which kind reached a display string.
#:
#: ``_stable_id`` builds every tool id as ``tool_v<n>`` plus a sha256 digest, and
#: the spellings that reached users wrapped one in a label —
#: ``create_refund [tool_v2_6dcebe…]`` — so a guard comparing the whole subject
#: against the catalog never saw it. Matching the *shape* also covers an id that
#: no longer resolves: a stale or plugin-supplied id is exactly as unreadable as
#: a current one, and a current-catalog check cannot recognise it at all.
#:
#: The agent shape is the same rule one subject kind later:
#: ``core.agent_bindings`` builds an agent id as ``agent_v1:`` plus a truncated
#: sha256, and the binding gaps fell back to it whenever the issue named no
#: tool — so ``samples/conductor_agent`` shipped ``…is incomplete
#: (agent_v1:7205d836…)`` as the sentence under the verdict. A guard scoped to
#: one kind of id passes vacuously for every other one (#329).
#:
#: The patterns themselves live in :mod:`agents_shipgate.core.adopter_text`,
#: which owns the reader-facing half of the same rule. Two copies would drift,
#: and the drift would be silent in exactly the direction that matters.
#: All four shapes, not the two that had been seen in a subject. A
#: ``source_warning`` is copied verbatim into an ``EvidenceGap.subject``, so
#: loader text carrying an observation id or a fingerprint reached the field
#: this rule exists to keep readable while passing validation — the same
#: "scoped to today's instances" mistake one level down.
DERIVED_ID_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("tool", TOOL_ID_PATTERN),
    ("agent", AGENT_ID_PATTERN),
    ("observation", OBSERVATION_ID_PATTERN),
    ("finding", FINGERPRINT_PATTERN),
)


#: The one position a derived id can occupy in a display subject *without*
#: being an adopter-controlled name: the bracketed qualifier
#: ``catalog_subject`` appends, as in ``create_refund [tool_v2_6dcebe…]``.
_BRACKETED_QUALIFIER = re.compile(r"\[([^\[\]]*)\]")


def derived_id_kind(value: str) -> str | None:
    """Which derived identifier ``value`` carries *as an identifier*.

    ``"tool"``, ``"agent"``, ``"observation"``, ``"finding"``, or ``None``.

    Shape alone is not enough, and getting that wrong is expensive in one
    direction only: this predicate aborts a scan, so a false positive on an
    adopter-controlled name is an outage rather than a lint. A tool may legally
    be named ``tool_v2_deadbeef`` or ``tool_v2_deadbeef-helper``, and word
    boundaries cannot separate those from the real thing — ``-`` and ``.`` are
    name characters (#329 review 3).

    So position decides. A derived id is refused when it is the *whole*
    subject, or when it sits inside the bracketed qualifier, which is the only
    part of a subject that emitters build rather than adopters — that is where
    every spelling this rule was written for actually appeared. A shape in the
    *name* position, with a qualifier beside it, is a name.
    """

    stripped = value.strip()
    for noun, pattern in DERIVED_ID_PATTERNS:
        match = pattern.fullmatch(stripped)
        if match is not None:
            return noun
        for qualifier in _BRACKETED_QUALIFIER.findall(stripped):
            if pattern.search(qualifier):
                return noun
    return None


def agent_subject(node: AgentBindingNode) -> str:
    """The subject string an agent-binding node is named by.

    ``catalog_subject`` for agents, and deliberately the same shape: the
    declared name, qualified by the source it was read from when there is one,
    because two sources can define an agent of the same name. The reader
    recognises ``closer_agent [google_adk:agent.py]``; they cannot do anything
    at all with ``agent_v1:507abc67``.

    An unnamed agent — an extractor that resolved no literal ``name=`` — falls
    back to where it was read, never to the id. Returning the id here would
    not merely be unreadable: :func:`derived_id_kind` refuses a derived id in
    any gap subject, so the fallback would abort the scan it was written to
    describe.

    A qualifier equal to the name is dropped. The qualifier exists to separate
    two sources defining an agent of the same name, and it separates nothing
    when it *is* the name — a ``tool_sources[].binding`` surface is named by
    its own source id, so it read as ``github_mcp [github_mcp]`` in the
    sentence under the verdict.
    """

    name = node.name.strip()
    if not name:
        return node.source_ref or node.source_pointer or node.source_id or "unnamed agent"
    if not node.source_id or node.source_id == name:
        return name
    return f"{name} [{node.source_id}]"


def agent_label_index(agents: Iterable[AgentBindingNode]) -> dict[str, str]:
    """Map agent id to the one display label that names that agent.

    Resolved from the binding graph through one index, for the reason
    :func:`catalog_label_index` exists: an emitter that renders a label from
    its own fields produces a second spelling of the same subject.
    """

    return {node.agent_id: agent_subject(node) for node in agents}


def catalog_label_index(rows: Iterable[Any]) -> dict[str, str]:
    """Map canonical tool id to the one display label that names that tool.

    Built from the tool catalog and shared by every emitter that labels a tool,
    because rendering from anything else produces a second label for the same
    subject. ``ActionFact.provider`` is the live example: it is
    ``_normalize_token(provider or source_id or source_type)``, so a source id
    of ``my api`` labels one gap ``create_refund [my_api]`` while a
    catalog-backed gap labels the same tool ``create_refund [my api]``.

    Accepts catalog rows as ``Tool`` objects or as mappings; the report carries
    them both ways depending on the stage.
    """

    index: dict[str, str] = {}
    for row in rows:
        if isinstance(row, Mapping):
            tool_id = row.get("tool_id") or row.get("id")
            payload: Mapping[str, Any] = row
        else:
            tool_id = getattr(row, "id", None)
            payload = {
                "name": getattr(row, "name", None),
                "provider": getattr(row, "provider", None),
            }
        if tool_id:
            index[str(tool_id)] = catalog_subject(payload)
    return index


def tool_label(
    tool_id: str | None,
    index: Mapping[str, str],
    *,
    name: str | None = None,
) -> str | None:
    """The display label for a tool, or ``None`` when nothing can name it.

    Never returns a canonical id. ``subject`` is a label and ``subject_id``
    carries identity, so a tool the catalog cannot name is better handed to the
    caller's next fallback than labelled with a digest a reader cannot act on.
    """

    if tool_id:
        label = index.get(tool_id)
        if label:
            return label
    return name or None


def _catalog_by_id(report: ReadinessReport) -> dict[str, Mapping[str, Any]]:
    return {
        str(row["tool_id"]): row
        for row in report.tool_catalog
        if isinstance(row, Mapping) and row.get("tool_id")
    }


def _gap_subjects(gaps: Sequence[EvidenceGap], kinds: frozenset[str]) -> set[str]:
    return {gap.subject for gap in gaps if gap.kind in kinds}


def _gapped_tool_ids(gaps: Sequence[EvidenceGap], kinds: frozenset[str]) -> set[str]:
    """Canonical ids of the tools these gaps are about.

    Joining on ``subject`` — the ``name [provider]`` display label — is what a
    review caught: two catalog ids can render the same label, so one gap marked
    both rows accounted-for and the ledger reported twice the gating it had.
    Identity joins on the id; the label is for reading.
    """

    return {
        gap.subject_id
        for gap in gaps
        if gap.kind in kinds and gap.subject_id
    }


#: Gap kinds a binding-stage exclusion can be accounted for by. Public so
#: ``core.semantic_consistency`` checks the same set the ledger joins on — a
#: private copy there would drift and quietly stop checking anything.
#:
#: Derived from ``AgentBindingIssue.kind`` rather than restated, because a
#: hand-kept copy is exactly what drifted: it omitted
#: ``invalid_binding_annotation``, so a tool-scoped gap of that kind left its
#: ledger row ``not_claimed`` while the decision carried the gap (PR #404
#: review). ``invalid_tool_binding`` is added on top — it is an
#: ``EvidenceGap`` kind with no ``AgentBindingIssue`` counterpart.
BINDING_GAP_KINDS = frozenset(get_args(AgentBindingIssue.model_fields["kind"].annotation)) | {
    "invalid_tool_binding",
}

def build_surface_exclusions(
    report: ReadinessReport,
    *,
    source_omissions: Sequence[SourceSurfaceOmission] = (),
) -> SurfaceExclusionLedger:
    """Every subject this run removed from the analysed surface.

    Call after ``report.release_decision`` is built: accounting is decided by
    whether a gap row names the subject, so the gaps have to exist first.
    """

    decision = report.release_decision
    gaps: Sequence[EvidenceGap] = (
        decision.evidence_coverage.evidence_gaps if decision is not None else ()
    )
    # ``subject_kind`` is what tells the two id spaces apart, and this join is
    # in the tool-id one. A binding gap raised about a ``tool_sources`` entry
    # carries a *source* id, and the schema says a consumer joining one against
    # the other must be able to distinguish them rather than discover the
    # difference on a collision (#432).
    binding_gap_subject_by_id = {
        gap.subject_id: gap.subject
        for gap in gaps
        if gap.kind in BINDING_GAP_KINDS
        and gap.subject_id
        and gap.subject_kind == "action"
    }

    entries: list[SurfaceExclusion] = []
    entries.extend(_binding_exclusions(report, binding_gap_subject_by_id, gaps))
    entries.extend(_surface_completeness_exclusions(gaps))
    entries.extend(_adapter_parse_exclusions(source_omissions, gaps))
    return SurfaceExclusionLedger.from_entries(entries)


def _binding_exclusions(
    report: ReadinessReport,
    gap_subject_by_tool_id: dict[str, str],
    gaps: Sequence[EvidenceGap],
) -> list[SurfaceExclusion]:
    """Catalog tools the root-reachable graph did not carry into analysis.

    ``possible`` and ``unbound`` are both exclusions — everything downstream
    of the graph is narrowed to ``reachable_tool_ids`` — but they are not the
    same claim, so they carry different reasons and, usually, different
    accounting.
    """

    graph = report.binding_surface_facts
    catalog = _catalog_by_id(report)
    diff = report.binding_surface_diff
    newly_unbound = set(diff.added_unbound_tool_ids) if diff.enabled else set()
    # Asked for and not performed: the run cannot tell a pre-existing exclusion
    # from one this change introduced, so neither `not_claimed` nor
    # `newly_unbound_tool` is a claim it is entitled to make.
    unverified = diff.base_comparison_requested and not diff.enabled

    base_gap_subject = next(
        (
            gap.subject
            for gap in gaps
            if gap.subject == unavailable_base_subject(report)
        ),
        None,
    )

    def _accounting(tool_id: str) -> tuple[str, str | None]:
        pointer = gap_subject_by_tool_id.get(tool_id)
        if pointer is not None:
            return "evidence_gap", pointer
        if unverified and base_gap_subject is not None:
            return "unverified", base_gap_subject
        return "not_claimed", None

    entries: list[SurfaceExclusion] = []
    for tool_id in sorted(graph.possible_tool_ids):
        row = catalog.get(tool_id, {"tool_id": tool_id})
        subject = catalog_subject(row)
        accounting, accounted_by = _accounting(tool_id)
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
                accounting=cast(Any, accounting),
                accounted_by=accounted_by,
            )
        )
    for tool_id in sorted(graph.unbound_tool_ids):
        row = catalog.get(tool_id, {"tool_id": tool_id})
        subject = catalog_subject(row)
        accounting, accounted_by = _accounting(tool_id)
        introduced = tool_id in newly_unbound
        entries.append(
            SurfaceExclusion(
                stage="binding",
                subject=subject,
                reason=(
                    "newly_unbound_tool"
                    if introduced
                    else "unverified_unbound_tool"
                    if unverified
                    else "unbound_tool"
                ),
                source_ref=_row_ref(row),
                detail=(
                    (
                        "This change put the tool in the catalog and left it "
                        "unbound from the root agent, so no check judged it."
                    )
                    if introduced
                    else (
                        "The tool is in the catalog and no edge binds it to "
                        "the root agent, and the base comparison that would "
                        "say whether this change introduced it could not be "
                        "performed."
                    )
                    if unverified
                    else (
                        "The tool is in the catalog and no edge binds it to "
                        "the root agent; nothing in the repository claims it "
                        "as reachable capability."
                    )
                ),
                accounting=cast(Any, accounting),
                accounted_by=accounted_by,
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
            accounted_by=gap.subject,
        )
        for gap in gaps
        if gap.kind == "incomplete_surface"
    ]


def _adapter_parse_exclusions(
    omissions: Sequence[SourceSurfaceOmission],
    gaps: Sequence[EvidenceGap],
) -> list[SurfaceExclusion]:
    """Entries an adapter read and refused, as the adapter recorded them.

    Not derived from ``source_warnings``. That mapping turned every warning
    into "part of that input never entered the catalog", which is false of most
    of them — ``samples/simple_crewai_agent`` records ``FileReadTool`` as
    low-confidence metadata and the tool is in the catalog, in the inventory,
    reachable, and high-confidence (PR #404 review). Prose cannot be told apart
    from prose, so adapters now record a typed
    :class:`~agents_shipgate.core.domain.SourceSurfaceOmission` at the sites
    where an entry is genuinely dropped, and only those become rows.

    Each omission carries the warning it raised, which is the ``source_warning``
    gap's subject — so the accounting is a join, not an assumption. An adapter
    that has not been taught to record omissions contributes nothing here, and
    a report that says the ledger is empty means the run proved nothing was
    dropped rather than that nobody looked.
    """

    gap_subjects = {gap.subject for gap in gaps if gap.kind == "source_warning"}
    return [
        SurfaceExclusion(
            stage="adapter_parse",
            subject=omission.subject,
            reason=omission.reason,
            source_ref=omission.warning,
            detail=omission.detail,
            accounting=(
                "evidence_gap" if omission.warning in gap_subjects else "not_claimed"
            ),
            accounted_by=(
                omission.warning if omission.warning in gap_subjects else None
            ),
        )
        for omission in omissions
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
    "BINDING_GAP_KINDS",
    "DERIVED_ID_PATTERNS",
    "agent_label_index",
    "agent_subject",
    "catalog_label_index",
    "derived_id_kind",
    "tool_label",
    "unavailable_base_subject",
    "build_detect_exclusions",
    "build_surface_exclusions",
    "catalog_subject",
]
