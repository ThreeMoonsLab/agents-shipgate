from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from agents_shipgate.checks.base import agent_finding
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.models import (
    Finding,
    SourceReference,
    parse_confidence,
    parse_severity,
)


def _identity_surface(surface: object) -> object:
    return surface


def _explicit_inventory_false(_surface: object) -> bool:
    return False


@dataclass(frozen=True)
class DynamicSurfaceConfig:
    check_id: str
    title: str
    severity: str
    category: str
    confidence: str
    evidence_key: str
    recommendation: str
    suppress: Callable[[object], bool]
    evidence_value: Callable[[object], object] = _identity_surface
    explicit_inventory_value: Callable[[object], bool] = _explicit_inventory_false
    source_for: Callable[[object], SourceReference | None] | None = None


def collect_dynamic_surface_findings(
    context: ScanContext,
    *,
    surfaces: Iterable[object],
    config: DynamicSurfaceConfig,
) -> list[Finding]:
    findings: list[Finding] = []
    for surface in surfaces:
        if config.suppress(surface):
            continue
        evidence = {
            config.evidence_key: config.evidence_value(surface),
            "explicit_inventory": config.explicit_inventory_value(surface),
        }
        source = config.source_for(surface) if config.source_for else None
        if source is None:
            findings.append(
                agent_finding(
                    check_id=config.check_id,
                    title=config.title,
                    severity=config.severity,
                    category=config.category,
                    evidence=evidence,
                    confidence=config.confidence,
                    recommendation=config.recommendation,
                    context=context,
                )
            )
            continue
        findings.append(
            Finding(
                check_id=config.check_id,
                title=config.title,
                severity=parse_severity(config.severity),
                category=config.category,
                agent_id=context.agent.id,
                evidence=evidence,
                confidence=parse_confidence(config.confidence),
                source=source,
                recommendation=config.recommendation,
            )
        )
    return findings
