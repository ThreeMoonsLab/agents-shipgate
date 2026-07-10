from __future__ import annotations

from agents_shipgate.checks._framework_common import (
    DynamicSurfaceConfig,
    collect_dynamic_surface_findings,
)
from agents_shipgate.core.artifact_models import ConductorArtifacts
from agents_shipgate.core.context import ScanContext
from agents_shipgate.schemas.common import SourceReference
from agents_shipgate.schemas.report import Finding


def run(context: ScanContext) -> list[Finding]:
    artifacts = context.artifact("conductor", ConductorArtifacts)
    if not artifacts:
        return []
    return collect_dynamic_surface_findings(
        context,
        surfaces=artifacts.dynamic_tool_surfaces,
        config=DynamicSurfaceConfig(
            check_id="SHIP-CONDUCTOR-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE",
            title="Conductor tool surface cannot be statically enumerated",
            severity="high",
            category="conductor",
            confidence="medium",
            evidence_key="surface",
            recommendation=(
                "Use literal MCP server and method bindings, a static LLM tool "
                "advertisement, or an exact local sub-workflow target before release review."
            ),
            suppress=lambda _surface: False,
            source_for=_source,
            provenance_kind="static_declaration",
        ),
    )


def _source(surface: object) -> SourceReference | None:
    if not isinstance(surface, dict):
        return None
    path = surface.get("source_path")
    pointer = surface.get("source_pointer")
    if not isinstance(path, str):
        return None
    return SourceReference(
        type="conductor_workflow",
        ref=f"{path}#{pointer}" if isinstance(pointer, str) else path,
        path=path,
        pointer=pointer if isinstance(pointer, str) else None,
    )
