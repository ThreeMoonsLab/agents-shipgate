from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

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
        findings.append(
            Finding(
                check_id=config.check_id,
                title=config.title,
                severity=parse_severity(config.severity),
                category=config.category,
                agent_id=context.agent.id,
                evidence={
                    config.evidence_key: config.evidence_value(surface),
                    "explicit_inventory": config.explicit_inventory_value(surface),
                },
                confidence=parse_confidence(config.confidence),
                source=_source_for(context, surface, config),
                recommendation=config.recommendation,
            )
        )
    return findings


def _source_for(
    context: ScanContext,
    surface: object,
    config: DynamicSurfaceConfig,
) -> SourceReference:
    source = config.source_for(surface) if config.source_for else None
    return source or SourceReference(type="manifest", ref=context.config_path.name)
