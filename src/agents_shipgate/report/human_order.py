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
    CapabilityReleaseImpact,
)
from agents_shipgate.schemas.common import SourceReference
from agents_shipgate.schemas.packet import EvidencePacket
from agents_shipgate.schemas.report import ReadinessReport
from agents_shipgate.schemas.surfaces import ActionSurfaceChange
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
class _CapabilityReaderSubject:
    """Canonical grouping identity plus the deliberately separate label."""

    key: str
    name: str
    label: str


_CapabilityMemberSignature = tuple[str, str, str, str, str]


@dataclass(frozen=True)
class _CapabilitySubjectIndex:
    """Ephemeral bridge from compatibility rows to canonical tool identity.

    ``CapabilityChangeMember`` intentionally keeps its established wire shape,
    which names the owning tool but does not repeat ``tool_id`` or provider.
    The complete report already carries those identities in its action/tool
    facts and diffs.  This index joins them only for the human projection, so
    same-named tools from different providers stay separate without changing
    verifier.json.
    """

    subjects_by_key: dict[str, _CapabilityReaderSubject]
    keys_by_name: dict[str, tuple[str, ...]]
    keys_by_signature: dict[_CapabilityMemberSignature, tuple[str, ...]]
    key_by_tool_id: dict[str, str]
    key_by_action_id: dict[str, str]
    operation_by_action_id: dict[str, str]
    sources_by_member_key: dict[
        tuple[_CapabilityMemberSignature, str],
        tuple[CapabilityDeltaSource, ...],
    ]
    colliding_keys: frozenset[str]
    blocking_keys: frozenset[str]


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
    group_names: dict[str, str] = {}
    bucket_subjects: dict[CapabilityDeltaBucket, set[str]] = {
        "added": set(),
        "modified": set(),
        "removed": set(),
    }
    members = _capability_change_members(change)
    subject_index = _capability_subject_index(report)
    source_by_finding_id = {
        finding.id: (
            finding.source,
            subject_index.key_by_tool_id.get(finding.tool_id or ""),
        )
        for finding in report.findings
        if finding.id and finding.source is not None and finding.source.path
    }
    for member in members:
        for subject in _capability_reader_subjects(member, index=subject_index):
            grouped[subject.key].append(member)
            labels.setdefault(subject.key, subject.label)
            group_names.setdefault(subject.key, subject.name)
            bucket_subjects[_DIRECTION_TO_BUCKET[member.direction]].add(subject.key)
    labels = _injective_capability_labels(labels)

    all_subjects: list[CapabilityDeltaSubjectGroup] = []
    for subject_key in sorted(
        grouped,
        key=lambda key: _capability_group_sort_key(
            key,
            grouped[key],
            index=subject_index,
        ),
    ):
        subject_members = sorted(
            grouped[subject_key],
            key=lambda member: _capability_member_sort_key(
                member,
                release_impact=_capability_reader_impact(
                    member,
                    subject_key=subject_key,
                    index=subject_index,
                ),
            ),
        )
        all_subjects.append(
            CapabilityDeltaSubjectGroup(
                subject=labels[subject_key],
                changes=tuple(
                    _delta_description(
                        member,
                        group_subject=group_names[subject_key],
                        operation_by_action_id=subject_index.operation_by_action_id,
                        release_impact=_capability_reader_impact(
                            member,
                            subject_key=subject_key,
                            index=subject_index,
                        ),
                    )
                    for member in subject_members
                ),
                change_types=tuple(_delta_change_type(member) for member in subject_members),
                change_buckets=tuple(
                    _DIRECTION_TO_BUCKET[member.direction] for member in subject_members
                ),
                sources=_capability_group_sources(
                    subject_members,
                    source_by_finding_id=source_by_finding_id,
                    canonical_sources=_capability_member_sources(
                        subject_members,
                        subject_key=subject_key,
                        index=subject_index,
                    ),
                    subject_key=subject_key,
                    colliding_keys=subject_index.colliding_keys,
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
        # A compatibility member that omitted canonical identity may represent
        # more than one same-named provider tool.  Count the complete human
        # associations rendered above, while verifier.json keeps its existing
        # change-record counts unchanged.
        change_count=sum(len(subject_members) for subject_members in grouped.values()),
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
    *,
    index: _CapabilitySubjectIndex,
) -> tuple[int, int, str, str]:
    """Keep the most consequential complete subject groups inside the limit."""

    return (
        min(
            _CAPABILITY_IMPACT_ORDER.get(
                _capability_reader_impact(
                    member,
                    subject_key=subject_key,
                    index=index,
                ),
                99,
            )
            for member in members
        ),
        min(_CAPABILITY_DIRECTION_ORDER.get(member.direction, 99) for member in members),
        subject_key,
        min(member.id for member in members),
    )


def _capability_member_sort_key(
    member: CapabilityChangeMember,
    *,
    release_impact: str | None = None,
) -> tuple[int, int, str, str, str, str, str]:
    """Keep each selected subject's highest-signal details visible first."""

    return (
        _CAPABILITY_IMPACT_ORDER.get(release_impact or member.release_impact, 99),
        _CAPABILITY_DIRECTION_ORDER.get(member.direction, 99),
        member.subject_kind,
        member.action or "",
        member.scope or "",
        member.tool,
        member.id,
    )


def _capability_reader_impact(
    member: CapabilityChangeMember,
    *,
    subject_key: str,
    index: _CapabilitySubjectIndex,
) -> CapabilityReleaseImpact:
    """Remove name-scoped blocker bleed after canonical subject splitting."""

    if subject_key not in index.colliding_keys:
        return member.release_impact
    if subject_key in index.blocking_keys:
        return "blocks_release"
    if member.release_impact != "blocks_release":
        return member.release_impact
    # The compatibility builder related findings by display name. Once that
    # name resolves to several canonical tools, only an exact finding.tool_id
    # can keep the blocking label on one split group.
    if member.direction in {"added", "broadened"}:
        return "review_required"
    return "informational"


def _capability_group_sources(
    members: list[CapabilityChangeMember],
    *,
    source_by_finding_id: dict[str, tuple[SourceReference, str | None]],
    canonical_sources: tuple[CapabilityDeltaSource, ...] = (),
    subject_key: str,
    colliding_keys: frozenset[str],
) -> tuple[CapabilityDeltaSource, ...]:
    """Retain provenance without depending on the truncated verifier projection."""

    sources: list[CapabilityDeltaSource] = list(canonical_sources)
    seen: set[tuple[str, int | None]] = {
        (source.path, source.start_line) for source in canonical_sources
    }
    for member in members:
        for finding_id in member.related_finding_ids:
            source_entry = source_by_finding_id.get(finding_id)
            if source_entry is None:
                continue
            source, finding_subject_key = source_entry
            if subject_key in colliding_keys and finding_subject_key != subject_key:
                # A name-only relation cannot identify one of several
                # same-named providers. Preserve an exact finding.tool_id
                # source for its own group, while refusing to bleed that
                # provenance into its siblings.
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


def _capability_member_sources(
    members: list[CapabilityChangeMember],
    *,
    subject_key: str,
    index: _CapabilitySubjectIndex,
) -> tuple[CapabilityDeltaSource, ...]:
    """Return only sources that evidence these changes on this subject.

    Action facts establish identity, but unchanged sibling actions are not
    provenance for a changed member. Sources are therefore indexed by both
    the compatibility-member signature and canonical subject. An action diff
    may fall back to the matching action fact when its enriched source is
    absent; unrelated action facts never enter this map.
    """

    sources: list[CapabilityDeltaSource] = []
    seen: set[tuple[str, int | None]] = set()
    for member in members:
        signature = _capability_member_signature(
            member.direction,
            member.subject_kind,
            member.tool,
            member.action,
            member.scope,
        )
        for source in index.sources_by_member_key.get((signature, subject_key), ()):
            key = (source.path, source.start_line)
            if key not in seen:
                seen.add(key)
                sources.append(source)
    return tuple(sources)


def _capability_subject_index(report: ReadinessReport) -> _CapabilitySubjectIndex:
    """Join compatibility rows to canonical identities already in the report."""

    records: dict[str, dict[str, str]] = {}
    raw_keys_by_name: dict[str, set[str]] = defaultdict(set)
    raw_keys_by_signature: dict[_CapabilityMemberSignature, set[str]] = defaultdict(set)
    raw_key_by_action_id: dict[str, str] = {}
    operation_by_action_id: dict[str, str] = {}
    raw_sources_by_member_key: dict[
        tuple[_CapabilityMemberSignature, str],
        set[tuple[str, int | None]],
    ] = defaultdict(set)
    action_fact_source_by_id: dict[str, tuple[str, int | None]] = {}

    def add_subject(
        *,
        tool_id: str | None,
        name: str | None,
        qualifier: str | None = None,
    ) -> str | None:
        clean_name = name if isinstance(name, str) and has_visible_content(name) else ""
        if not clean_name:
            return None
        clean_qualifier = (
            qualifier if isinstance(qualifier, str) and has_visible_content(qualifier) else ""
        )
        clean_tool_id = (
            tool_id if isinstance(tool_id, str) and has_visible_content(tool_id) else ""
        )
        existing_name_keys = raw_keys_by_name.get(clean_name, set())
        if clean_tool_id:
            raw_key = clean_tool_id
        elif clean_qualifier:
            matching_qualifier_keys = tuple(
                key
                for key in existing_name_keys
                if records[key]["qualifier"] == clean_qualifier
            )
            raw_key = (
                matching_qualifier_keys[0]
                if len(matching_qualifier_keys) == 1
                else f"legacy:{clean_qualifier}:{clean_name}"
            )
        elif len(existing_name_keys) == 1:
            # Older action diffs omitted tool_id. If the complete report names
            # exactly one canonical tool with this display name, join it rather
            # than manufacturing a second legacy subject (#439).
            raw_key = next(iter(existing_name_keys))
        elif existing_name_keys:
            # More than one canonical provider owns this display name and the
            # row supplies no identity capable of choosing between them. Keep
            # the signature unresolved so the reader sees the explicit
            # ``identity unavailable`` group, never this implementation key.
            return None
        else:
            raw_key = f"legacy:{clean_qualifier}:{clean_name}"
        record = records.setdefault(
            raw_key,
            {"name": clean_name, "qualifier": clean_qualifier},
        )
        # Conflicting display metadata on one canonical id is not identity.
        # Pick a stable visible spelling; the key still prevents a false join.
        record["name"] = min(value for value in (record["name"], clean_name) if value)
        qualifiers = [value for value in (record["qualifier"], clean_qualifier) if value]
        record["qualifier"] = min(qualifiers) if qualifiers else ""
        raw_keys_by_name[clean_name].add(raw_key)
        return raw_key

    def add_signature(
        signature: _CapabilityMemberSignature,
        raw_key: str | None,
        *,
        source_path: str | None = None,
        source_start_line: int | None = None,
    ) -> None:
        if raw_key is not None:
            raw_keys_by_signature[signature].add(raw_key)
            if source_path and has_visible_content(source_path):
                raw_sources_by_member_key[(signature, raw_key)].add(
                    (source_path, source_start_line)
                )

    for tool in report.tool_surface_facts.tools:
        add_subject(
            tool_id=tool.tool_id,
            name=tool.name,
            qualifier=tool.provider or tool.source_id or tool.source_type,
        )
    for change in report.tool_surface_diff.tools:
        raw_key = add_subject(
            tool_id=change.tool_id,
            name=change.name,
            qualifier=change.provider or change.source_id or change.source_type,
        )
        direction = {
            "added": "added",
            "removed": "removed",
            "changed": "broadened",
        }[change.kind]
        add_signature(
            _capability_member_signature(direction, "tool", change.name, None, None),
            raw_key,
            source_path=change.source_path,
            source_start_line=change.source_start_line,
        )

    for action in report.action_surface_facts.actions:
        raw_key = add_subject(
            tool_id=action.tool_id,
            name=action.tool_name,
            qualifier=action.provider or action.source_id or action.source_type,
        )
        if raw_key is not None:
            raw_key_by_action_id[action.action_id] = raw_key
        if has_visible_content(action.operation):
            operation_by_action_id[action.action_id] = action.operation
        if action.source_path and has_visible_content(action.source_path):
            action_fact_source_by_id[action.action_id] = (
                action.source_path,
                action.source_start_line,
            )

    action_changes: tuple[tuple[str, list[ActionSurfaceChange]], ...] = (
        ("added", report.action_surface_diff.added),
        ("removed", report.action_surface_diff.removed),
        ("broadened", report.action_surface_diff.modified),
    )
    for direction, changes in action_changes:
        for change in changes:
            existing_action_key = raw_key_by_action_id.get(change.action_id)
            compatible_existing_key = (
                existing_action_key
                if existing_action_key is not None
                and not change.tool_id
                and (
                    not change.tool_name
                    or records[existing_action_key]["name"] == change.tool_name
                )
                else None
            )
            raw_key = compatible_existing_key or add_subject(
                tool_id=change.tool_id,
                name=change.tool_name,
            )
            if raw_key is not None and (
                existing_action_key is None or raw_key == existing_action_key
            ):
                # An ID-less diff may enrich a compatible fact mapping but may
                # not replace exact action identity. Conflicting explicit ids
                # remain separate evidence instead of silently changing owner.
                raw_key_by_action_id[change.action_id] = raw_key
            if change.operation and has_visible_content(change.operation):
                operation_by_action_id[change.action_id] = change.operation
            signature = _capability_member_signature(
                direction,
                "action",
                change.tool_name or "",
                change.action_id,
                None,
            )
            fallback_source = action_fact_source_by_id.get(change.action_id)
            add_signature(
                signature,
                raw_key,
                source_path=(
                    change.source_path
                    or (fallback_source[0] if fallback_source is not None else None)
                ),
                source_start_line=(
                    change.source_start_line
                    if change.source_path
                    else (fallback_source[1] if fallback_source is not None else None)
                ),
            )

    def keys_for_tool_reference(name: str, tool_id: str | None) -> tuple[str, ...]:
        if tool_id:
            # An explicit canonical id is identity even when this is the first
            # complete-report row that mentions it. Register it before any
            # display-name fallback; fanning an unseen id across same-named
            # providers discards the strongest evidence the diff carries.
            raw_key = add_subject(tool_id=tool_id, name=name)
            return (raw_key,) if raw_key is not None else ()
        candidates = raw_keys_by_name.get(name, set())
        if candidates:
            return tuple(sorted(candidates))
        raw_key = add_subject(tool_id=tool_id, name=name)
        return (raw_key,) if raw_key is not None else ()

    for scope_change in report.tool_surface_diff.scopes:
        direction = "narrowed" if scope_change.kind == "removed" else "broadened"
        # ``tool_names`` and ``tool_ids`` are independently sorted by the
        # producer, not parallel arrays. Resolve ids through their canonical
        # records instead of zipping two unrelated orders.
        for name in scope_change.tool_names:
            matching_ids = tuple(
                sorted(
                    tool_id
                    for tool_id in scope_change.tool_ids
                    if records.get(tool_id, {}).get("name") == name
                )
            )
            raw_keys = (
                matching_ids
                if matching_ids
                else keys_for_tool_reference(name, None)
            )
            for raw_key in raw_keys:
                add_signature(
                    _capability_member_signature(
                        direction,
                        "scope",
                        name,
                        None,
                        scope_change.scope,
                    ),
                    raw_key,
                )

    for control_change in report.tool_surface_diff.controls:
        direction = "narrowed" if control_change.kind == "added" else "broadened"
        for raw_key in keys_for_tool_reference(
            control_change.tool,
            control_change.tool_id,
        ):
            add_signature(
                _capability_member_signature(
                    direction,
                    "policy",
                    control_change.tool,
                    control_change.control,
                    None,
                ),
                raw_key,
                source_path=control_change.source_path,
                source_start_line=control_change.source_start_line,
            )

    for effect_change in report.tool_surface_diff.high_risk_effects:
        direction = "narrowed" if effect_change.kind == "removed" else "broadened"
        for raw_key in keys_for_tool_reference(effect_change.tool, effect_change.tool_id):
            add_signature(
                _capability_member_signature(
                    direction,
                    "action",
                    effect_change.tool,
                    effect_change.tag,
                    None,
                ),
                raw_key,
                source_path=effect_change.source_path,
                source_start_line=effect_change.source_start_line,
            )

    qualifier_counts = Counter(
        (record["name"], record["qualifier"])
        for record in records.values()
        if record["qualifier"]
    )
    subjects_by_key: dict[str, _CapabilityReaderSubject] = {}
    raw_to_group_key: dict[str, str] = {}
    for raw_key, record in sorted(records.items()):
        name = record["name"]
        qualifier = record["qualifier"]
        collides = len(raw_keys_by_name[name]) > 1
        if not collides:
            label = name
        elif qualifier and qualifier_counts[(name, qualifier)] == 1:
            label = f"{name} [{qualifier}]"
        else:
            identity = f"{qualifier}; {raw_key}" if qualifier else raw_key
            label = f"{name} [{identity}]"
        group_key = f"canonical:{raw_key}"
        raw_to_group_key[raw_key] = group_key
        subjects_by_key[group_key] = _CapabilityReaderSubject(
            key=group_key,
            name=name,
            label=display_literal(label),
        )

    return _CapabilitySubjectIndex(
        subjects_by_key=subjects_by_key,
        keys_by_name={
            name: tuple(raw_to_group_key[key] for key in sorted(raw_keys))
            for name, raw_keys in raw_keys_by_name.items()
        },
        keys_by_signature={
            signature: tuple(raw_to_group_key[key] for key in sorted(raw_keys))
            for signature, raw_keys in raw_keys_by_signature.items()
        },
        key_by_tool_id=dict(raw_to_group_key),
        key_by_action_id={
            action_id: raw_to_group_key[raw_key]
            for action_id, raw_key in raw_key_by_action_id.items()
            if raw_key in raw_to_group_key
        },
        operation_by_action_id=operation_by_action_id,
        sources_by_member_key={
            (signature, raw_to_group_key[raw_key]): tuple(
                CapabilityDeltaSource(
                    path=display_literal(path),
                    start_line=start_line,
                )
                for path, start_line in sorted(
                    sources,
                    key=lambda item: (item[0], item[1] if item[1] is not None else -1),
                )
            )
            for (signature, raw_key), sources in raw_sources_by_member_key.items()
            if raw_key in raw_to_group_key
        },
        colliding_keys=frozenset(
            raw_to_group_key[raw_key]
            for raw_keys in raw_keys_by_name.values()
            if len(raw_keys) > 1
            for raw_key in raw_keys
        ),
        blocking_keys=frozenset(
            raw_to_group_key[finding.tool_id]
            for finding in report.findings
            if not finding.suppressed
            and finding.blocks_release
            and finding.tool_id in raw_to_group_key
        ),
    )


def _capability_member_signature(
    direction: str,
    subject_kind: str,
    tool: str,
    action: str | None,
    scope: str | None,
) -> _CapabilityMemberSignature:
    return direction, subject_kind, tool, action or "", scope or ""


def _capability_reader_subjects(
    member: CapabilityChangeMember,
    *,
    index: _CapabilitySubjectIndex,
) -> tuple[_CapabilityReaderSubject, ...]:
    """Resolve one compatibility member to one or more canonical subjects."""

    keys: tuple[str, ...] = ()
    if member.action and member.action in index.key_by_action_id:
        keys = (index.key_by_action_id[member.action],)
    if not keys:
        signature = _capability_member_signature(
            member.direction,
            member.subject_kind,
            member.tool,
            member.action,
            member.scope,
        )
        keys = index.keys_by_signature.get(signature, ())
    if not keys and member.tool:
        name_keys = index.keys_by_name.get(member.tool, ())
        if len(name_keys) == 1:
            keys = name_keys
    if keys:
        return tuple(index.subjects_by_key[key] for key in dict.fromkeys(keys))

    if member.tool and len(index.keys_by_name.get(member.tool, ())) > 1:
        label = display_literal(f"{member.tool} [identity unavailable]")
        return (
            _CapabilityReaderSubject(
                key=f"legacy-ambiguous:{member.tool}",
                name=member.tool,
                label=label,
            ),
        )

    # Policy drift and frozen legacy reports may have no canonical tool
    # identity to join.  Keep the historical visible fallback without letting
    # it collide with a real canonical key.
    for candidate in (member.tool, member.scope, member.action, member.id):
        if not isinstance(candidate, str):
            continue
        rendered = display_literal(candidate)
        if has_visible_content(rendered):
            return (
                _CapabilityReaderSubject(
                    key=f"legacy-display:{candidate}",
                    name=candidate,
                    label=rendered,
                ),
            )
    fallback = "unknown capability"
    return (
        _CapabilityReaderSubject(
            key=f"legacy-display:{fallback}",
            name=fallback,
            label=fallback,
        ),
    )


def _injective_capability_labels(labels: dict[str, str]) -> dict[str, str]:
    """Make the final rendered headings unique without changing group keys."""

    unique: dict[str, str] = {}
    used: set[str] = set()
    for key, base_label in sorted(labels.items(), key=lambda item: (item[1], item[0])):
        label = base_label
        ordinal = 2
        while label in used:
            label = f"{base_label} [subject {ordinal}]"
            ordinal += 1
        unique[key] = label
        used.add(label)
    return unique


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
    operation_by_action_id: dict[str, str] | None = None,
    release_impact: CapabilityReleaseImpact | None = None,
) -> str:
    operation_by_action_id = operation_by_action_id or {}
    action = member.action
    if action in operation_by_action_id:
        action = operation_by_action_id[action]
    elif action and ":action_v2_" in action:
        # Canonical ids are useful machine join keys, not reader operations.
        # When a frozen report has no action diff/fact to resolve the id, the
        # rationale below still states the semantic change without leaking a
        # long opaque digest into the review.
        action = None
    target_value = next(
        (
            candidate
            for candidate in (action, member.scope, member.tool, member.id)
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
    qualifiers: list[str] = []
    rationale = display_literal(member.rationale)
    if has_visible_content(rationale) and _rationale_adds_detail(member, rationale):
        qualifiers.append(rationale.rstrip("."))
    effective_impact = release_impact or member.release_impact
    if effective_impact not in {"none", "informational"}:
        qualifiers.append(effect_phrase(effective_impact))
    if qualifiers:
        detail += f" — {'; '.join(qualifiers)}"
    return detail


def _rationale_adds_detail(member: CapabilityChangeMember, rationale: str) -> bool:
    """Suppress tautologies while retaining semantic field-level changes."""

    normalized = " ".join(rationale.casefold().rstrip(".").split())
    generic = {
        f"{member.subject_kind} {member.direction}",
        f"{member.direction} {member.subject_kind}",
        f"capability {member.direction}",
    }
    if normalized in generic:
        return False
    if member.direction in {"added", "removed"} and normalized.startswith(
        f"{member.subject_kind} {member.direction}:"
    ):
        return False
    return True


def _report_proves_manifest_introduction(report: ReadinessReport) -> bool:
    return any(
        is_adoption_evidence(finding.check_id, finding.evidence) for finding in report.findings
    )
