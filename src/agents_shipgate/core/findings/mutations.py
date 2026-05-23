from __future__ import annotations

from agents_shipgate.core.check_ids import expands_to_check_id
from agents_shipgate.schemas.common import Severity
from agents_shipgate.schemas.manifest import SuppressionConfig
from agents_shipgate.schemas.report import Finding


def apply_suppressions(
    findings: list[Finding], suppressions: list[SuppressionConfig]
) -> list[Finding]:
    for finding in findings:
        match = _matching_suppression(finding, suppressions)
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


def _matching_suppression(
    finding: Finding, suppressions: list[SuppressionConfig]
) -> SuppressionConfig | None:
    for suppression in suppressions:
        if not expands_to_check_id(suppression.check_id, finding.check_id):
            continue
        if not suppression.tool:
            return suppression
        possible_tools = {
            finding.tool_name,
            finding.tool_id,
            finding.tool_id.replace("tool:", "") if finding.tool_id else None,
        }
        if suppression.tool in possible_tools:
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
