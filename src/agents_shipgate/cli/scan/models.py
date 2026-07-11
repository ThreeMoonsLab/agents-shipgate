from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents_shipgate.core.artifact_models import (
    AnthropicArtifacts,
    CodexPluginArtifacts,
    CrewAiArtifacts,
    GoogleAdkArtifacts,
    LangChainArtifacts,
    N8nArtifacts,
    OpenAIApiArtifacts,
    ValidationArtifacts,
)
from agents_shipgate.core.artifacts import ArtifactBag
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.domain import Agent, LoadedToolSource, Tool, ToolkitScopeBound
from agents_shipgate.core.lenses.tool_surface import ToolSurfaceDiffReference
from agents_shipgate.core.privacy import RedactionStats
from agents_shipgate.schemas.bindings import AgentBindingGraphAssessment, BindingSurfaceDiff
from agents_shipgate.schemas.codex_plugin import CodexPluginSurface
from agents_shipgate.schemas.manifest import AgentsShipgateManifest
from agents_shipgate.schemas.report import PolicyAudit
from agents_shipgate.schemas.surfaces import ActionSurfaceFacts


@dataclass(frozen=True)
class _ResolvedManifest:
    """Phase 1 output: manifest after CLI overrides applied."""

    manifest: AgentsShipgateManifest
    manifest_positions: Any
    base_dir: Path


@dataclass(frozen=True)
class _LoadedInputs:
    """Phase 2 output: source loading + artifact extraction + warnings.

    Warning buckets are kept separate so Phase 3 (``_build_tools_and_agent``)
    can interleave tool-identity warnings between ``source_only_warnings`` and
    ``artifact_warnings``, preserving the pre-decomp deterministic order:

        source → identity → artifact → placeholder → policy_pack → dedup

    Collapsing them into a single ``warnings`` list here (the P3 bug that
    this split fixes) would push identity warnings to the end, changing
    ``report.source_warnings`` order for fixtures with both identity and
    artifact/policy-pack warnings.
    """

    loaded_sources: list[LoadedToolSource]
    artifact_bag: ArtifactBag
    policy_packs: Any  # LoadedPolicyPacks
    source_only_warnings: list[str]   # per-source warnings, no dedup yet
    artifact_warnings_list: list[str]      # from _artifact_warnings(artifact_bag)
    placeholder_warnings: list[str]   # from _manifest_placeholder_warnings
    policy_pack_warnings: list[str]   # from policy_packs.warnings
    # v0.20: third-party adapter provenance from
    # ``discover_third_party_adapters``. Both valid and invalid records
    # appear here; ``loaded_adapters[].validation_status == "valid"``
    # distinguishes them. Empty list when --no-plugins is set or no
    # third-party adapters are installed.
    loaded_adapters: list[dict[str, Any]]
    adk: GoogleAdkArtifacts | None
    langchain: LangChainArtifacts | None
    crewai: CrewAiArtifacts | None
    n8n: N8nArtifacts | None
    api: OpenAIApiArtifacts | None
    anthropic: AnthropicArtifacts | None
    codex_plugin: CodexPluginArtifacts | None
    validation: ValidationArtifacts | None


@dataclass(frozen=True)
class _ToolsAndAgent:
    """Phase 3 output: flattened/deduped/enriched tools + Agent + final warnings."""

    tools: list[Tool]
    tool_catalog: list[Tool]
    agent: Agent
    binding_graph: AgentBindingGraphAssessment
    warnings: list[str]  # deduplicated source warnings
    # Statically-parsed least-privilege bounds on dynamically-loaded
    # toolkits, aggregated across all loaded sources. Empty for the common
    # case (no recognized agent-toolkit constructor).
    toolkit_bounds: list[ToolkitScopeBound] = field(default_factory=list)


@dataclass(frozen=True)
class _DiffReferences:
    """Phase 4 output: optional baseline + diff_from references."""

    baseline_file: Any  # BaselineFile | None
    baseline_display_path: str | None
    diff_reference: ToolSurfaceDiffReference | None
    diff_reference_error: str | None


@dataclass(frozen=True)
class _ChecksDecision:
    """Phase 5 output: action surface + checks + severity + remediation."""

    action_surface_facts: ActionSurfaceFacts
    action_surface_diff: Any  # ActionSurfaceDiff (internal/semantic)
    # Fail-soft warnings raised while building the action surface (e.g.
    # duplicate action_id collisions from OpenAPI specs whose paths
    # normalize identically). Merged into ``report.source_warnings`` so a
    # third-party spec degrades to review_required instead of crashing.
    action_surface_warnings: list[str]
    findings: list[Any]  # list[Finding]
    legacy_fingerprints: list[str]
    override_resolution: Any  # SeverityOverrideResolution
    heuristics_filter: Any  # HeuristicsFilter
    loaded_plugins: list[dict[str, str | None]]
    context: ScanContext


@dataclass(frozen=True)
class _OutputPlan:
    """Phase 6 output: file paths + packet format set + privacy stats.

    ``privacy_stats`` is intentionally mutable — the sanitization phase
    accumulates redaction counts into it. The dataclass is ``frozen`` only
    in the sense that the field bindings don't change; the contained
    ``RedactionStats`` mutates in place.
    """

    out_dir: Path
    generated_paths: dict[str, Path]
    packet_format_set: set[str]
    output_surfaces: list[str]
    privacy_stats: RedactionStats
    generated_report_refs: Any


@dataclass
class _SanitizedSurfaces:
    """Phase 7 output: every ``public_*`` value flowing into report/packet.

    Not frozen — the baseline-integrity branch appends to ``findings``
    in place and refreshes derivative fields. After Phase 7 returns,
    every value here has been passed through ``redact_data`` /
    ``sanitize_*`` exactly once. Phase 8+ (``build_report`` /
    ``build_packet`` / ``_write_*``) MUST NOT re-redact and MUST NOT
    touch any raw (non-``public_*``) value.
    """

    manifest: AgentsShipgateManifest
    manifest_dir: str
    project: Any
    environment: Any
    agent: Agent
    tools: list[Tool]
    tool_catalog: list[Tool]
    binding_graph: AgentBindingGraphAssessment
    binding_surface_diff: BindingSurfaceDiff
    findings: list[Any]
    source_warnings: list[str]
    api_artifacts: OpenAIApiArtifacts | None
    anthropic_artifacts: AnthropicArtifacts | None
    validation_artifacts: ValidationArtifacts | None
    api_surface: Any
    anthropic_surface: Any
    frameworks_surface: Any
    codex_plugin_surface: CodexPluginSurface | None
    policy_audit: PolicyAudit
    loaded_policy_packs: list[Any]
    loaded_plugins: Any
    loaded_adapters: Any  # v0.20: list[dict[str, Any]]; sanitized via redact_data
    diff_reference: ToolSurfaceDiffReference | None
    base_action_surface_facts: ActionSurfaceFacts | None
    action_surface_facts: ActionSurfaceFacts
    action_surface_diff: Any
    capability_runtime_evidence: Any
    tool_surface_facts: Any
    tool_surface_diff: Any
    baseline_summary: Any
    privacy_audit: Any
    heuristics_filter: Any
