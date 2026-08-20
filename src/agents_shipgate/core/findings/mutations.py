from __future__ import annotations

from collections import Counter

from agents_shipgate.core.check_ids import (
    UNSUPPRESSIBLE_FINDING_CATEGORIES,
    expands_to_check_id,
)
from agents_shipgate.core.domain import Tool
from agents_shipgate.core.tool_identity import tool_identity_aliases
from agents_shipgate.schemas.common import Severity
from agents_shipgate.schemas.manifest import SuppressionConfig
from agents_shipgate.schemas.report import (
    NO_HEURISTICS_EXCLUDED_PROVENANCE_KINDS,
    Finding,
    HeuristicsFilter,
)

NO_HEURISTICS_SUPPRESSION_REASON = "filtered by --no-heuristics"


def apply_suppressions(
    findings: list[Finding], suppressions: list[SuppressionConfig], tools: list[Tool] | None = None
) -> list[Finding]:
    for finding in findings:
        # Trust-root / verify findings are the reward-hacking guard and
        # cannot be silenced by a manifest checks.ignore entry — a PR that
        # edits shipgate.yaml to suppress them must NOT pass. See
        # UNSUPPRESSIBLE_FINDING_CATEGORIES.
        if finding.category in UNSUPPRESSIBLE_FINDING_CATEGORIES:
            continue
        match = _matching_suppression(finding, suppressions, tools=tools)
        if match:
            finding.suppressed = True
            finding.suppression_reason = match.reason
    return findings


def apply_severity_overrides(
    findings: list[Finding], overrides: dict[str, Severity]
) -> list[Finding]:
    for finding in findings:
        override = _severity_override_for_check(finding.check_id, overrides)
        if override:
            # Keep this audit field out of fingerprinting so overrides can be
            # applied before or after ID assignment without changing identity.
            finding.evidence.setdefault("default_severity", finding.severity)
            finding.severity = override
    return findings


def apply_no_heuristics_filter(
    findings: list[Finding],
    *,
    enabled: bool,
) -> HeuristicsFilter:
    """Suppress heuristic-provenance findings when ``--no-heuristics`` is set."""

    excluded = set(NO_HEURISTICS_EXCLUDED_PROVENANCE_KINDS)
    envelope = HeuristicsFilter(
        enabled=enabled,
        excluded_provenance_kinds=list(NO_HEURISTICS_EXCLUDED_PROVENANCE_KINDS),
    )
    if not enabled:
        return envelope

    counts: Counter[str] = Counter()
    for finding in findings:
        if finding.provenance_kind not in excluded:
            continue
        counts[finding.provenance_kind] += 1
        if finding.suppressed:
            continue
        finding.suppressed = True
        finding.suppression_reason = NO_HEURISTICS_SUPPRESSION_REASON

    envelope.filtered_finding_count = sum(counts.values())
    envelope.filtered_by_kind = dict(counts)
    return envelope


def _matching_suppression(
    finding: Finding,
    suppressions: list[SuppressionConfig],
    *,
    tools: list[Tool] | None = None,
) -> SuppressionConfig | None:
    for suppression in suppressions:
        if not expands_to_check_id(suppression.check_id, finding.check_id):
            continue
        if not suppression.tool and not suppression.tool_id:
            return suppression
        tool = next((item for item in tools or [] if item.id == finding.tool_id), None)
        # Same alias rule as every other selector consumer: a source-qualified
        # `checks.ignore` row must not go inert because a reviewed binding
        # rekeyed the tool it names (#386 review).
        aliases = tool_identity_aliases(tool) if tool is not None else None
        if suppression.tool_id:
            if aliases is None:
                if suppression.tool_id != finding.tool_id:
                    continue
            elif not aliases.matches(tool_id=suppression.tool_id):
                continue
        elif suppression.tool:
            if suppression.tool != finding.tool_name:
                continue
            if tools is not None and sum(tool.name == suppression.tool for tool in tools) != 1:
                continue
        if suppression.provider and (tool is None or tool.provider != suppression.provider):
            continue
        if suppression.source_type or suppression.source_id:
            if aliases is None or not aliases.matches(
                source_type=suppression.source_type or None,
                source_id=suppression.source_id or None,
            ):
                continue
        return suppression
    return None


def _severity_override_for_check(
    check_id: str, overrides: dict[str, Severity]
) -> Severity | None:
    if override := overrides.get(check_id):
        return override
    for configured_check_id, override in overrides.items():
        if expands_to_check_id(configured_check_id, check_id):
            return override
    return None
