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
from agents_shipgate.core.policy_reason_codes import is_adoption_evidence
from agents_shipgate.schemas.capability_change import CapabilityChangeMember
from agents_shipgate.schemas.packet import EvidencePacket
from agents_shipgate.schemas.report import ReadinessReport


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
    """The canonical capability delta grouped by the tool it changes."""

    change = report.capability_change
    if change is None or not change.enabled:
        return []
    grouped: dict[str, list[str]] = defaultdict(list)
    for member in (
        *change.added,
        *change.broadened,
        *change.narrowed,
        *change.removed,
    ):
        grouped[member.tool].append(_delta_description(member))
    return [
        CapabilityDeltaSubject(
            subject=display_literal(subject),
            changes=tuple(grouped[subject]),
        )
        for subject in sorted(grouped)
    ]


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


def _delta_description(member: CapabilityChangeMember) -> str:
    target = display_literal(member.action or member.scope or member.tool)
    detail = f"{member.direction} {member.subject_kind} {target}"
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
