"""Human-artifact ordering for a repository's first Shipgate contact.

This module is deliberately a presentation layer.  It carries no field into
``report.json`` or ``packet.json`` and it never derives a release decision.
The one decision rendered by every caller remains
``report.release_decision.decision``.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Literal

from agents_shipgate.core.action_semantics import ACTION_EFFECT_RANK, effect_phrase
from agents_shipgate.core.evidence_actions import display_literal
from agents_shipgate.core.findings.subject_rollup import SubjectGroup, roll_up_findings
from agents_shipgate.core.findings.verifier_blocks import build_capability_change
from agents_shipgate.core.policy_reason_codes import is_adoption_evidence
from agents_shipgate.schemas.capability_change import (
    CapabilityChangeBlock,
    CapabilityChangeDirection,
    CapabilityChangeMember,
)
from agents_shipgate.schemas.common import SourceReference
from agents_shipgate.schemas.packet import EvidencePacket
from agents_shipgate.schemas.report import ReadinessReport
from agents_shipgate.schemas.text import has_visible_content


@dataclass(frozen=True)
class HumanArtifactContext:
    """Ephemeral facts that affect human ordering but not machine output."""

    manifest_committed: bool | None = None
    manifest_introduced: bool = False

    @property
    def is_cold(self) -> bool:
        return bool(self.manifest_committed is False or self.manifest_introduced)


_SURFACE_WRITE_ACTION_LIMIT = 8


@dataclass(frozen=True)
class SurfaceLead:
    tool_count: int
    source_count: int
    effect_counts: tuple[tuple[str, int], ...]
    write_actions: tuple[tuple[str, str], ...]
    source_unit: Literal["source", "source type"] = "source"

    def text_lines(self) -> list[str]:
        source_noun = self.source_unit if self.source_count == 1 else f"{self.source_unit}s"
        lines = [f"Surface: {self.tool_count} tools from {self.source_count} {source_noun}."]
        if self.effect_counts:
            effects = ", ".join(
                f"{count} {effect_phrase(effect)}" for effect, count in self.effect_counts
            )
            lines.append(f"Effects: {effects}.")
        else:
            lines.append("Effects: no root-reachable action effects were classified.")
        if self.write_actions:
            actions = ", ".join(
                f"{display_literal(name)} ({effect_phrase(effect)})"
                for name, effect in self.write_actions[:_SURFACE_WRITE_ACTION_LIMIT]
            )
            hidden = len(self.write_actions) - _SURFACE_WRITE_ACTION_LIMIT
            if hidden > 0:
                actions += f", … and {hidden} more action{'s' if hidden != 1 else ''}"
            lines.append(f"Write/destructive actions: {actions}.")
        else:
            lines.append("Write/destructive actions: none.")
        return lines


@dataclass(frozen=True)
class CapabilityDeltaSubject:
    subject: str
    changes: tuple[str, ...]


CapabilityDeltaBucket = Literal["added", "modified", "removed"]
CapabilityDeltaOutsideAnalysisStatus = Literal[
    "not_requested",
    "unavailable",
    "complete",
]

_DIRECTION_TO_BUCKET: dict[CapabilityChangeDirection, CapabilityDeltaBucket] = {
    "added": "added",
    "broadened": "modified",
    "narrowed": "modified",
    "removed": "removed",
}
_CAPABILITY_IMPACT_ORDER = {
    "blocks_release": 0,
    "insufficient_evidence": 1,
    "review_required": 2,
    "informational": 3,
    "none": 4,
}
_CAPABILITY_DIRECTION_ORDER: dict[CapabilityChangeDirection, int] = {
    "added": 0,
    "broadened": 1,
    "narrowed": 2,
    "removed": 3,
}


@dataclass(frozen=True)
class CapabilityDeltaSubjectGroup:
    """Every canonical capability-change row about one reader subject.

    ``changes``, ``change_types``, and ``change_buckets`` are parallel tuples:
    no row is discarded merely because another row names the same subject.
    That is the distinction #439 needs — one added tool is one subject while
    its tool-catalog and action-surface changes both remain visible.
    """

    subject: str
    changes: tuple[str, ...]
    change_types: tuple[str, ...]
    change_buckets: tuple[CapabilityDeltaBucket, ...]
    sources: tuple[CapabilityDeltaSource, ...]

    @property
    def change_count(self) -> int:
        return len(self.changes)


@dataclass(frozen=True)
class CapabilityDeltaSource:
    """One complete-report source retained for a grouped human row."""

    path: str
    start_line: int | None


@dataclass(frozen=True)
class CapabilityDeltaOutsideAnalysis:
    """Base-relative subjects the binding graph left outside analysis.

    This axis is deliberately not joined to the analysed subject groups.  A
    tool can lose its binding and therefore be both removed from analysed
    capability and newly outside analysis, and the two substrates use
    different identities (display tool strings versus canonical tool ids).
    """

    status: CapabilityDeltaOutsideAnalysisStatus
    newly_outside_subjects: int


@dataclass(frozen=True)
class CapabilityDeltaSubjectRollup:
    """The complete subject-keyed human projection of ``capability_change``.

    Directional counts are memberships, not a partition: one subject that is
    both added and broadened counts once under ``added_subjects`` and once
    under ``modified_subjects``, while ``total_subjects`` still counts it once.
    Counts are computed before ``subject_limit`` is applied, so truncation can
    always disclose exactly how many subjects it hid.
    """

    enabled: bool
    total_subjects: int
    added_subjects: int
    modified_subjects: int
    removed_subjects: int
    change_count: int
    subjects: tuple[CapabilityDeltaSubjectGroup, ...]
    hidden_subjects: int
    outside_analysis: CapabilityDeltaOutsideAnalysis


@dataclass(frozen=True)
class ColdReaderLead:
    surface: SurfaceLead
    delta_subjects: tuple[CapabilityDeltaSubject, ...]
    finding_groups: tuple[SubjectGroup, ...]
    has_active_block_tier: bool


def should_render_surface_first(
    report: ReadinessReport,
    *,
    context: HumanArtifactContext | None = None,
) -> bool:
    """Whether this human artifact should lead with capability value.

    A blocker, a block verdict, or even an inconsistent report that carries an
    active block-tier finding keeps the established verdict-first order.  A
    cold ``review_required`` run still leads with the surface: review is not a
    block-tier result, and first-adoption verification legitimately produces
    that shape when the introduced manifest is the review item.
    """

    decision = report.release_decision
    if decision is None or decision.decision not in {
        "insufficient_evidence",
        "review_required",
    }:
        return False
    if decision.blockers or any(
        finding.blocks_release and not finding.suppressed for finding in report.findings
    ):
        return False
    cold = context.is_cold if context is not None else False
    return cold or _report_proves_manifest_introduction(report)


def should_render_packet_surface_first(
    packet: EvidencePacket,
    *,
    context: HumanArtifactContext | None,
    cold_lead: ColdReaderLead | None,
) -> bool:
    """Packet equivalent using its decision and report-derived cold lead."""

    decision = packet.release_decision
    return bool(
        context is not None
        and cold_lead is not None
        and context.is_cold
        and decision.decision in {"insufficient_evidence", "review_required"}
        and not decision.blockers
        and not cold_lead.has_active_block_tier
    )


def surface_lead(report: ReadinessReport) -> SurfaceLead:
    """Project the already-recorded surface into a compact human lead."""

    sources = {
        (fact.source_type, fact.source_id or fact.provider)
        for fact in report.tool_surface_facts.tools
    }
    source_unit: Literal["source", "source type"] = "source"
    if not sources:
        sources = {
            (action.source_type, action.source_id or action.provider)
            for action in report.action_surface_facts.actions
        }
    if not sources:
        sources = {(source_type, source_type) for source_type in report.tool_surface.sources}
        source_unit = "source type"

    counts = Counter(action.effect for action in report.action_surface_facts.actions)
    effect_counts = tuple(
        sorted(
            counts.items(),
            key=lambda item: (
                ACTION_EFFECT_RANK.get(item[0], 99),
                item[0],
            ),
        )
    )
    write_actions = tuple(
        sorted(
            {
                (action.tool_name, action.effect)
                for action in report.action_surface_facts.actions
                if action.effect in {"write", "financial_write", "destructive"}
            },
            key=lambda item: (
                -ACTION_EFFECT_RANK.get(item[1], 99),
                item[0],
                item[1],
            ),
        )
    )
    return SurfaceLead(
        tool_count=report.tool_surface.total_tools,
        source_count=len(sources),
        source_unit=source_unit,
        effect_counts=effect_counts,
        write_actions=write_actions,
    )


def capability_delta_by_subject(
    report: ReadinessReport,
) -> list[CapabilityDeltaSubject]:
    """Compatibility view of the canonical subject rollup.

    Existing human surfaces consume only ``subject`` plus rendered ``changes``.
    Keep that shape while deriving it from the structured projection so a
    second grouping rule cannot drift from the PR-comment rollup.
    """

    return [
        CapabilityDeltaSubject(
            subject=group.subject,
            changes=group.changes,
        )
        for group in capability_delta_subject_rollup(report).subjects
    ]


def capability_delta_subject_rollup(
    report: ReadinessReport,
    *,
    subject_limit: int | None = None,
) -> CapabilityDeltaSubjectRollup:
    """Group every canonical change by reader subject, then optionally bound it.

    The grouping happens over the complete ``capability_change`` member set;
    ``subject_limit`` is applied only after groups and counts exist.  This is
    the subject equivalent of ``project_top_findings``'s truncation contract:
    duplicate rows for one subject never consume another subject's slot, and
    hidden counts describe what was actually omitted rather than the nominal
    limit.
    """

    outside_analysis = _outside_analysis_delta(report)
    change = _capability_change_block(report)
    if not change.enabled:
        return CapabilityDeltaSubjectRollup(
            enabled=False,
            total_subjects=0,
            added_subjects=0,
            modified_subjects=0,
            removed_subjects=0,
            change_count=0,
            subjects=(),
            hidden_subjects=0,
            outside_analysis=outside_analysis,
        )

    grouped: dict[str, list[CapabilityChangeMember]] = defaultdict(list)
    labels: dict[str, str] = {}
    bucket_subjects: dict[CapabilityDeltaBucket, set[str]] = {
        "added": set(),
        "modified": set(),
        "removed": set(),
    }
    members = _capability_change_members(change)
    source_by_finding_id = {
        finding.id: finding.source
        for finding in report.findings
        if finding.id and finding.source is not None and finding.source.path
    }
    for member in members:
        subject_key, subject_label = _capability_reader_subject(member)
        grouped[subject_key].append(member)
        labels.setdefault(subject_key, subject_label)
        bucket_subjects[_DIRECTION_TO_BUCKET[member.direction]].add(subject_key)

    all_subjects: list[CapabilityDeltaSubjectGroup] = []
    for subject_key in sorted(
        grouped,
        key=lambda key: _capability_group_sort_key(key, grouped[key]),
    ):
        subject_members = sorted(grouped[subject_key], key=_capability_member_sort_key)
        all_subjects.append(
            CapabilityDeltaSubjectGroup(
                subject=labels[subject_key],
                changes=tuple(
                    _delta_description(member, group_subject=subject_key)
                    for member in subject_members
                ),
                change_types=tuple(_delta_change_type(member) for member in subject_members),
                change_buckets=tuple(
                    _DIRECTION_TO_BUCKET[member.direction] for member in subject_members
                ),
                sources=_capability_group_sources(
                    subject_members,
                    source_by_finding_id=source_by_finding_id,
                ),
            )
        )
    bounded_limit = len(all_subjects) if subject_limit is None else max(0, subject_limit)
    shown_subjects = tuple(all_subjects[:bounded_limit])
    return CapabilityDeltaSubjectRollup(
        enabled=True,
        total_subjects=len(all_subjects),
        added_subjects=len(bucket_subjects["added"]),
        modified_subjects=len(bucket_subjects["modified"]),
        removed_subjects=len(bucket_subjects["removed"]),
        change_count=len(members),
        subjects=shown_subjects,
        hidden_subjects=max(0, len(all_subjects) - len(shown_subjects)),
        outside_analysis=outside_analysis,
    )


def _capability_change_block(report: ReadinessReport) -> CapabilityChangeBlock:
    if report.capability_change is not None:
        return report.capability_change
    # Match ``build_capability_review`` for older/test callers that predate the
    # canonical report block.  This remains a presentation projection: the
    # shared builder reads existing surface diffs and introduces no decision.
    return build_capability_change(report)


def _capability_change_members(
    change: CapabilityChangeBlock,
) -> tuple[CapabilityChangeMember, ...]:
    return (
        *change.added,
        *change.broadened,
        *change.narrowed,
        *change.removed,
    )


def _capability_group_sort_key(
    subject_key: str,
    members: list[CapabilityChangeMember],
) -> tuple[int, int, str, str]:
    """Keep the most consequential complete subject groups inside the limit."""

    return (
        min(_CAPABILITY_IMPACT_ORDER.get(member.release_impact, 99) for member in members),
        min(_CAPABILITY_DIRECTION_ORDER.get(member.direction, 99) for member in members),
        subject_key,
        min(member.id for member in members),
    )


def _capability_member_sort_key(
    member: CapabilityChangeMember,
) -> tuple[int, int, str, str, str, str, str]:
    """Keep each selected subject's highest-signal details visible first."""

    return (
        _CAPABILITY_IMPACT_ORDER.get(member.release_impact, 99),
        _CAPABILITY_DIRECTION_ORDER.get(member.direction, 99),
        member.subject_kind,
        member.action or "",
        member.scope or "",
        member.tool,
        member.id,
    )


def _capability_group_sources(
    members: list[CapabilityChangeMember],
    *,
    source_by_finding_id: dict[str, SourceReference],
) -> tuple[CapabilityDeltaSource, ...]:
    """Retain provenance without depending on the truncated verifier projection."""

    sources: list[CapabilityDeltaSource] = []
    seen: set[tuple[str, int | None]] = set()
    for member in members:
        for finding_id in member.related_finding_ids:
            source = source_by_finding_id.get(finding_id)
            if source is None or not source.path:
                continue
            key = (source.path, source.start_line)
            if key not in seen:
                seen.add(key)
                sources.append(
                    CapabilityDeltaSource(
                        path=display_literal(source.path),
                        start_line=source.start_line,
                    )
                )
            # Match the stable verifier projection: one source per change,
            # chosen by the member's sorted related-finding ids.
            break
    return tuple(sources)


def _capability_reader_subject(member: CapabilityChangeMember) -> tuple[str, str]:
    """Stable group key plus a visible, injective display label.

    ``tool`` is the reader's unit for ordinary changes: it intentionally folds
    the tool-catalog and action-surface rows #439 observed.  A policy-drift row
    may carry no tool at all, so fall through to its scope/action and finally
    its stable member id instead of rendering an empty heading.
    """

    for candidate in (member.tool, member.scope, member.action, member.id):
        if not isinstance(candidate, str):
            continue
        rendered = display_literal(candidate)
        if has_visible_content(rendered):
            return candidate, rendered
    fallback = "unknown capability"
    return fallback, fallback


def _delta_change_type(member: CapabilityChangeMember) -> str:
    return f"{member.subject_kind}_{member.direction}"


def _outside_analysis_delta(
    report: ReadinessReport,
) -> CapabilityDeltaOutsideAnalysis:
    diff = report.binding_surface_diff
    if diff.enabled:
        return CapabilityDeltaOutsideAnalysis(
            status="complete",
            newly_outside_subjects=len(set(diff.added_unbound_tool_ids)),
        )
    if diff.base_comparison_requested:
        return CapabilityDeltaOutsideAnalysis(
            status="unavailable",
            newly_outside_subjects=0,
        )
    return CapabilityDeltaOutsideAnalysis(
        status="not_requested",
        newly_outside_subjects=0,
    )


def cold_reader_lead(report: ReadinessReport) -> ColdReaderLead:
    """One immutable projection for packet renderers with no report schema."""

    return ColdReaderLead(
        surface=surface_lead(report),
        delta_subjects=tuple(capability_delta_by_subject(report)),
        finding_groups=tuple(roll_up_findings(report)),
        has_active_block_tier=any(
            finding.blocks_release and not finding.suppressed
            for finding in report.findings
        ),
    )


def _delta_description(
    member: CapabilityChangeMember,
    *,
    group_subject: str | None = None,
) -> str:
    target_value = next(
        (
            candidate
            for candidate in (member.action, member.scope, member.tool, member.id)
            if isinstance(candidate, str) and has_visible_content(candidate)
        ),
        "unknown capability",
    )
    detail = f"{member.direction} {member.subject_kind}"
    if target_value != group_subject:
        detail += f" {display_literal(target_value)}"
    if member.before_scope is not None or member.after_scope is not None:
        before = display_literal(member.before_scope or "none")
        after = display_literal(member.after_scope or "none")
        detail += f" ({before} -> {after})"
    if member.release_impact not in {"none", "informational"}:
        detail += f" — {effect_phrase(member.release_impact)}"
    return detail


def _report_proves_manifest_introduction(report: ReadinessReport) -> bool:
    return any(
        is_adoption_evidence(finding.check_id, finding.evidence) for finding in report.findings
    )
