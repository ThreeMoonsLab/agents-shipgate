from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agents_shipgate.core.artifacts import ArtifactBag
from agents_shipgate.core.domain import LoadedToolSource, Tool
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.core.tool_identity import build_tool_identity_catalog
from agents_shipgate.inputs.protocol import REGISTRY, LoadedAdapterResult, ToolSourceAdapter
from agents_shipgate.schemas.manifest import (
    AgentsShipgateManifest,
    ToolIdentityConfig,
    ToolSourceConfig,
)

logger = logging.getLogger(__name__)


def _load_sources(
    manifest: AgentsShipgateManifest,
    base_dir: Path,
    *,
    verbose: bool,
    registry: Any = None,
    third_party_records: dict[str, Any] | None = None,
    plugins_enabled: bool | None = None,
) -> tuple[list[LoadedToolSource], ArtifactBag]:
    """Dispatch every adapter through the supplied ``registry``.

    Returns ``(loaded_sources, artifact_bag)``. ``artifact_bag`` is a
    typed ``ArtifactBag`` with per-scan adapter artifacts keyed by
    ``source_type``. Most per-source adapters (mcp, openapi,
    openai_agents_sdk) return tools only; codex_config is per-source
    and returns boundary artifacts.

    Ordering is deterministic and matches the legacy run_scan order:

      1. per-source loaders in tool_sources declared order
      2. per-scan adapters in registry iteration order:
         google_adk → langchain → crewai → n8n → openai_api
         → anthropic_api → codex_plugin → validation

    Per-scan adapters are invoked unconditionally in pass 2, in
    canonical order — NOT in tool_sources declared order. This matches
    today's run_scan exactly: framework loaders fire once per scan in
    fixed order, and the manifest-only loaders (openai_api,
    anthropic_api) and codex_plugin trail them.
    Per-scan source types appearing in tool_sources are ignored by
    pass 1 — they would be redundant; framework loaders already iterate
    every matching entry internally via the manifest.

    v0.20 (PR #111 review fix): ``registry`` is the per-scan registry
    built by ``_load_inputs`` (``REGISTRY.clone()`` plus any
    third-party adapters validated in this scan). Defaults to the
    module-global ``REGISTRY`` only for callers that bypass
    ``_load_inputs`` (notably the legacy tests in
    ``tests/test_adapter_registry.py``). New code should always pass
    a per-scan registry.

    v0.20 (PR #111 review fix follow-up #2): ``third_party_records``
    maps each validated third-party ``source_type`` to its
    ``LoadedAdapter`` record (from ``discover_third_party_adapters``).
    When set, the dispatcher routes those adapters through
    ``run_validated_adapter`` so any exception during their
    ``load()`` call is captured into
    ``loaded_adapters[].runtime_errors`` and the scan continues in
    lenient mode (or trips ``--strict-plugins`` exit 4 in strict
    mode). Built-in adapters keep the direct call shape — a built-in
    raising means the scanner itself is broken and must abort loudly.

    ``plugins_enabled`` is forwarded into ``AdapterRegistry.require`` so
    unknown third-party source-type errors reflect explicit CLI
    overrides such as ``--no-plugins`` instead of only inspecting the
    environment.
    """
    if registry is None:
        registry = REGISTRY
    if third_party_records is None:
        third_party_records = {}
    per_source_loaded: list[LoadedToolSource] = []
    per_scan_loaded: list[LoadedToolSource] = []
    bag = ArtifactBag()
    # ``(type, id) -> configured id`` for pass 2. Keyed on the pair itself
    # rather than on a joined string, because the id is repository-chosen and a
    # separator is one more thing that can appear inside it. The pair is the
    # key because the id alone is not a foreign key: see ``_configured_id_for``.
    configured_ids_by_source_id = {
        (source.type, source.id.strip()): source.id
        for source in manifest.tool_sources
    }

    # Pass 1 — per-source adapters only, in tool_sources declared
    # order. Per-scan source types (langchain, crewai, etc.) are
    # skipped here; pass 2 invokes them in canonical registry order
    # regardless of where they appear in tool_sources. Keeping adapter
    # invocation deterministic also keeps observation and warning order stable.
    for source in manifest.tool_sources:
        adapter = registry.require(source.type, plugins_enabled=plugins_enabled)
        if adapter.scope != "per_source":
            continue
        third_party_record = third_party_records.get(source.type)
        result = _invoke_per_source_adapter(
            adapter,
            source,
            base_dir,
            manifest,
            verbose=verbose,
            third_party_record=third_party_record,
        )
        if result is None:
            # Third-party adapter raised at runtime; the wrapper
            # captured the failure into runtime_errors and we skip
            # absorbing the (None) result.
            continue
        _absorb(
            result,
            source.type,
            per_source_loaded,
            bag,
            adapter,
            configured_source_id=source.id,
        )

    # Pass 2 — every per-scan adapter fires once, in registry order.
    # Covers framework adapters (always check their manifest section
    # internally and may emit zero LoadedToolSource entries when not
    # configured) and manifest-only adapters (openai_api,
    # anthropic_api, n8n).
    for adapter in registry.per_scan_adapters():
        third_party_record = third_party_records.get(adapter.source_type)
        if third_party_record is not None:
            from agents_shipgate.inputs.adapter_validation import (
                run_validated_adapter,
            )

            result = run_validated_adapter(
                third_party_record,
                source=None,
                base_dir=base_dir,
                manifest=manifest,
            )
            if result is None:
                continue
        else:
            result = adapter.load(None, base_dir, manifest)
        _absorb(
            result,
            adapter.source_type,
            per_scan_loaded,
            bag,
            adapter,
            configured_ids_by_source_id=configured_ids_by_source_id,
        )

    return per_source_loaded + per_scan_loaded, bag


def _tool_source_index(
    tools: list[Tool],
) -> dict[str, tuple[str | None, int | None]]:
    """Build a tool-name → ``(source_path, source_start_line)`` map for
    surface-diff enrichment.

    Used by ``enrich_action_surface_diff_with_source`` and
    ``enrich_tool_surface_diff_with_source`` to append
    ``(source: path:line)`` to change-row ``reason`` strings, and by
    the packet builder to suffix §3A / §3B highlights. Empty when the
    tool list is empty so callers can rely on a boolean test.
    """
    return {
        tool.id: (tool.source_path, tool.source_start_line)
        for tool in tools
    }


def _artifact_warnings(artifact_bag: ArtifactBag) -> list[str]:
    warnings: list[str] = []
    for artifact in artifact_bag.raw().values():
        artifact_warnings = getattr(artifact, "warnings", None)
        if isinstance(artifact_warnings, list):
            warnings.extend(str(warning) for warning in artifact_warnings)
    return warnings


def _absorb(
    result: LoadedAdapterResult,
    source_type: str,
    sink: list[LoadedToolSource],
    bag: ArtifactBag,
    adapter: ToolSourceAdapter,
    configured_source_id: str | None = None,
    configured_ids_by_source_id: Mapping[tuple[str, str], str] | None = None,
) -> None:
    for loaded in result.tool_sources:
        loaded.configured_source_id = _configured_id_for(
            loaded,
            adapter,
            configured_source_id,
            configured_ids_by_source_id,
        )
    sink.extend(result.tool_sources)
    if result.artifact is not None:
        if adapter.artifact_class is not None and not isinstance(
            result.artifact, adapter.artifact_class
        ):
            raise TypeError(
                f"Adapter {adapter.source_type!r} declared "
                f"artifact_class={adapter.artifact_class.__name__} but "
                f"returned {type(result.artifact).__name__}"
            )
        bag.set(source_type, result.artifact)
    if result.warnings:
        sink.append(
            LoadedToolSource(
                source_id=f"adapter:{source_type}",
                source_type=source_type,
                warnings=list(result.warnings),
            )
        )


def _configured_id_for(
    loaded: LoadedToolSource,
    adapter: ToolSourceAdapter,
    configured_source_id: str | None,
    configured_ids_by_source_id: Mapping[tuple[str, str], str] | None,
) -> str | None:
    """Which ``tool_sources`` entry this result was produced for, or ``None``.

    Pass 1 knows exactly — the dispatcher called the adapter *with* the config
    object, so every result of that call belongs to it however the adapter
    chose to spell the ids it mints (``codex_config`` emits
    ``codex_config_mcp:<path>``, which matches no configured row).

    Pass 2 has no config object, because a per-scan adapter fires once and may
    cover several entries plus a top-level manifest section. It is attributed
    by the pair ``(minted source id, adapter source type)``: a framework
    adapter reads its own ``tool_sources`` rows and mints their ids, so the
    pair identifies the row. The *type* half is what makes this safe — the four
    manifest-only adapters (``openai_api``, ``anthropic_api``, ``n8n``,
    ``validation``) are rejected in ``tool_sources`` outright, so no configured
    row can ever carry their type, and their fixed minted ids can no longer
    collide with a row that happens to reuse the name (#410 review).
    """

    if configured_source_id is not None:
        return configured_source_id
    if not configured_ids_by_source_id:
        return None
    return configured_ids_by_source_id.get(
        (adapter.source_type, loaded.source_id.strip())
    )


def _invoke_per_source_adapter(
    adapter: ToolSourceAdapter,
    source: ToolSourceConfig,
    base_dir: Path,
    manifest: AgentsShipgateManifest,
    *,
    verbose: bool,
    third_party_record: Any = None,
) -> LoadedAdapterResult | None:
    """Invoke a per_source adapter and return its result.

    For **built-in** adapters: catch ``InputParseError`` only when the
    source is marked ``optional`` (returning a warning-only stub);
    any other exception propagates. A built-in raising means the
    scanner is broken and must abort loudly.

    For **third-party** adapters (``third_party_record`` is the
    matching ``LoadedAdapter``): route through
    ``run_validated_adapter``, which captures ALL exceptions into the
    record's ``runtime_errors`` list and returns ``None``. Returning
    ``None`` signals the caller to skip ``_absorb`` for this source —
    the scan continues in lenient mode and ``--strict-plugins`` sees
    the runtime error on exit.
    """

    if third_party_record is not None:
        from agents_shipgate.inputs.adapter_validation import (
            run_validated_adapter,
        )

        return run_validated_adapter(
            third_party_record,
            source=source,
            base_dir=base_dir,
            manifest=manifest,
        )
    try:
        return adapter.load(source, base_dir, manifest)
    except InputParseError:
        if source.optional:
            warning = f"Optional source {source.id} failed to load"
            if verbose:
                warning = (
                    f"{warning}; continuing because the source is marked optional"
                )
            return LoadedAdapterResult(
                tool_sources=[
                    LoadedToolSource(
                        source_id=source.id,
                        source_type=source.type,
                        warnings=[warning],
                    )
                ],
            )
        raise


def _build_canonical_tools(
    loaded_sources: list[LoadedToolSource],
    identity_config: ToolIdentityConfig | None = None,
    repeated_artifacts: frozenset[str] = frozenset(),
) -> tuple[list[Tool], list[str]]:
    """Build the provider-scoped identity catalog and return identity warnings."""

    return build_tool_identity_catalog(
        loaded_sources,
        identity_config or ToolIdentityConfig(),
        repeated_artifacts,
    )


def _flatten_and_deduplicate_tools(
    loaded_sources: list[LoadedToolSource],
    identity_config: ToolIdentityConfig | None = None,
) -> tuple[list[Tool], list[str]]:
    """Deprecated compatibility alias for pre-v0.30 internal callers."""

    return _build_canonical_tools(loaded_sources, identity_config)
