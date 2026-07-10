from __future__ import annotations

import logging

from agents_shipgate.core.artifact_models import (
    ConductorArtifacts,
    CrewAiArtifacts,
    GoogleAdkArtifacts,
    LangChainArtifacts,
    N8nArtifacts,
)
from agents_shipgate.core.domain import Tool
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.core.lenses.action_surface import (
    build_action_surface_facts,
    public_action_schema_hash,
)
from agents_shipgate.core.lenses.tool_surface import ToolSurfaceDiffReference, _stable_hash
from agents_shipgate.core.privacy import RedactionStats, redact_data, sanitize_model
from agents_shipgate.schemas.codex_plugin import CodexPluginSurface
from agents_shipgate.schemas.manifest import AgentsShipgateManifest
from agents_shipgate.schemas.surfaces import (
    ActionFact,
    ActionSurfaceFacts,
    ActionSurfaceHashes,
    ToolSurfaceFacts,
)

logger = logging.getLogger(__name__)

def _frameworks_surface(
    adk_artifacts: GoogleAdkArtifacts | None,
    langchain_artifacts: LangChainArtifacts | None = None,
    crewai_artifacts: CrewAiArtifacts | None = None,
    n8n_artifacts: N8nArtifacts | None = None,
    conductor_artifacts: ConductorArtifacts | None = None,
) -> dict[str, object]:
    surface: dict[str, object] = {}
    if adk_artifacts:
        surface["google_adk"] = adk_artifacts.surface_summary()
    if langchain_artifacts:
        surface["langchain"] = langchain_artifacts.surface_summary()
    if crewai_artifacts:
        surface["crewai"] = crewai_artifacts.surface_summary()
    if n8n_artifacts:
        surface["n8n"] = n8n_artifacts.surface_summary()
    if conductor_artifacts:
        surface["conductor"] = conductor_artifacts.surface_summary()
    return surface


def _build_public_action_surface_facts(
    *,
    raw_facts: ActionSurfaceFacts,
    manifest: AgentsShipgateManifest,
    agent_id: str,
    tools: list[Tool],
    stats: RedactionStats,
) -> ActionSurfaceFacts:
    try:
        public_facts = sanitize_model(
            build_action_surface_facts(
                manifest,
                agent_id=agent_id,
                tools=tools,
            ),
            ActionSurfaceFacts,
            stats=stats,
            path="action_surface_facts",
        )
    except ConfigError:
        logger.debug(
            "redacted action surface collapsed distinct raw action ids; "
            "falling back to a sanitized raw snapshot with public-only "
            "ordinal disambiguators"
        )
        return _sanitize_existing_action_surface_facts(
            raw_facts,
            stats=stats,
            path="action_surface_facts",
        )
    # Re-derive the public hashes from the PUBLIC (post-redaction) fields,
    # exactly as the base diff reference does via
    # ``_sanitize_existing_action_surface_facts`` -> ``_refresh_public_action_hashes``.
    # Both sides therefore hash the same post-redaction inputs: head and
    # base stay byte-for-byte comparable even when redaction rewrites a
    # field name. Skipping this once let the head keep a pre-redaction hash
    # while the base re-derived a post-redaction one, so ``schema_hash``
    # differed for every capability on an otherwise-identical tree --
    # flipping the whole action surface to "modified" and every
    # capability_change member to a false "broadened (schema_hash changed
    # without a proven direction)".
    _refresh_public_action_surface_hashes(public_facts)
    return public_facts


def _sanitize_existing_action_surface_facts(
    facts: ActionSurfaceFacts,
    *,
    stats: RedactionStats,
    path: str,
) -> ActionSurfaceFacts:
    public_facts = sanitize_model(
        facts,
        ActionSurfaceFacts,
        stats=stats,
        path=path,
    )
    _disambiguate_public_action_ids(public_facts)
    return public_facts


def _disambiguate_public_action_ids(facts: ActionSurfaceFacts) -> None:
    seen: dict[str, int] = {}
    for action in facts.actions:
        count = seen.get(action.action_id, 0) + 1
        seen[action.action_id] = count
        if count > 1:
            action.action_id = f"{action.action_id}#{count}"
    _refresh_public_action_surface_hashes(facts)


def _refresh_public_action_surface_hashes(facts: ActionSurfaceFacts) -> None:
    """Re-derive every action's public hashes from its public fields.

    The single hashing path shared by the fresh head facts and the
    round-tripped base diff reference, so both sides derive
    ``input_schema_hash`` / ``hashes`` from the same post-redaction,
    serializable inputs. Keeping this the only producer of public action
    hashes is what stops head and base from drifting onto two different
    ``schema_hash`` formulas for an identical action.
    """
    for action in facts.actions:
        _refresh_public_action_hashes(action)


def _refresh_public_action_hashes(action: ActionFact) -> None:
    schema_hash = public_action_schema_hash(
        action.input_fields, action.required_input_fields
    )
    policy_hash = _stable_hash(
        {
            "approval": action.approval_policy.model_dump(mode="json"),
            "safeguards": action.safeguards.model_dump(mode="json"),
            "evidence": action.evidence.model_dump(mode="json"),
        }
    )
    risk_hash = _stable_hash(
        {
            "effect": action.effect,
            "risk_tags": action.risk_tags,
            "required_scopes": action.required_scopes,
        }
    )
    action.input_schema_hash = schema_hash
    action.hashes = ActionSurfaceHashes(
        identity_hash=_stable_hash(action.action_id),
        schema_hash=schema_hash,
        policy_hash=policy_hash,
        risk_hash=risk_hash,
    )


def _sanitize_codex_plugin_surface(
    surface: CodexPluginSurface | None,
    *,
    stats: RedactionStats,
) -> CodexPluginSurface | None:
    if surface is None:
        return None
    return sanitize_model(
        surface,
        CodexPluginSurface,
        stats=stats,
        path="codex_plugin_surface",
    )


def _sanitize_diff_reference(
    reference: ToolSurfaceDiffReference | None,
    *,
    stats: RedactionStats,
) -> ToolSurfaceDiffReference | None:
    if reference is None:
        return None
    facts = (
        sanitize_model(
            reference.facts,
            ToolSurfaceFacts,
            stats=stats,
            path="tool_surface_diff.base.facts",
        )
        if reference.facts is not None
        else None
    )
    action_facts = (
        _sanitize_existing_action_surface_facts(
            reference.action_facts,
            stats=stats,
            path="action_surface_diff.base.facts",
        )
        if reference.action_facts is not None
        else None
    )
    findings = (
        [
            item.__class__.model_validate(
                redact_data(
                    item.model_dump(mode="python"),
                    stats=stats,
                    path="tool_surface_diff.base.findings[]",
                )
            )
            for item in reference.findings
        ]
        if reference.findings is not None
        else None
    )
    return ToolSurfaceDiffReference(
        kind=reference.kind,
        facts=facts,
        path=redact_data(reference.path, stats=stats, path="tool_surface_diff.base.path"),
        report_schema_version=reference.report_schema_version,
        baseline_schema_version=reference.baseline_schema_version,
        action_facts=action_facts,
        findings=findings,
        notes=tuple(
            redact_data(
                list(reference.notes),
                stats=stats,
                path="tool_surface_diff.base.notes[]",
            )
        ),
        action_notes=tuple(
            redact_data(
                list(reference.action_notes),
                stats=stats,
                path="action_surface_diff.base.notes[]",
            )
        ),
    )
