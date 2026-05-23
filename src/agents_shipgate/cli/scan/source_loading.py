from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from agents_shipgate.cli.discovery.placeholders import collect_placeholders
from agents_shipgate.core.artifacts import ArtifactBag
from agents_shipgate.core.domain import LoadedToolSource, Tool
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.inputs.protocol import REGISTRY, LoadedAdapterResult, ToolSourceAdapter
from agents_shipgate.schemas.manifest import AgentsShipgateManifest, ToolSourceConfig

logger = logging.getLogger(__name__)

def _resolve_source_paths(
    manifest, base_dir: Path, config_path: Path
) -> list[dict[str, object]]:
    """Return required tool_sources whose declared path is unusable.

    Two failure modes are flagged so doctor can surface them as a
    ``SHIP-DIAG-MISSING-SOURCE-FILE`` diagnostic instead of crashing in
    a downstream loader:

    - ``reason="missing"`` — the file does not exist.
    - ``reason="outside_manifest_dir"`` — the file exists but escapes the
      manifest's containment boundary (loaders mirror this check and
      would raise ``InputParseError``).

    Optional sources are not reported here — the existing
    ``_load_sources`` flow handles them with a warning. Returned entries
    carry the source id, the declared path string, the 1-indexed line
    number in the manifest text where the path appears (best-effort),
    and the failure reason.
    """
    unresolved: list[dict[str, object]] = []
    try:
        manifest_text = config_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        manifest_text = ""
    text_lines = manifest_text.splitlines()
    base_resolved = base_dir.resolve()
    for source in manifest.tool_sources:
        if source.optional:
            continue
        if source.path is None:
            continue
        raw_path = Path(source.path)
        candidate = (
            raw_path if raw_path.is_absolute() else base_resolved / raw_path
        ).resolve()
        if not candidate.exists():
            reason = "missing"
        else:
            try:
                candidate.relative_to(base_resolved)
            except ValueError:
                reason = "outside_manifest_dir"
            else:
                continue
        line_no: int | None = None
        needle = f"path: {source.path}"
        for index, line in enumerate(text_lines, start=1):
            if needle in line:
                line_no = index
                break
        unresolved.append(
            {
                "id": source.id,
                "declared_path": source.path,
                "line": line_no,
                "reason": reason,
            }
        )
    return unresolved


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
    ``source_type``. Per-source adapters (mcp, openapi,
    openai_agents_sdk) never populate artifacts.

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

    # Pass 1 — per-source adapters only, in tool_sources declared
    # order. Per-scan source types (langchain, crewai, etc.) are
    # skipped here; pass 2 invokes them in canonical registry order
    # regardless of where they appear in tool_sources. This protects
    # the dedup tie-break in _flatten_and_deduplicate_tools from
    # changing based on user-facing tool_sources ordering.
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
        _absorb(result, source.type, per_source_loaded, bag, adapter)

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
        _absorb(result, adapter.source_type, per_scan_loaded, bag, adapter)

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
        tool.name: (tool.source_path, tool.source_start_line)
        for tool in tools
    }


def _artifact_warnings(artifact_bag: ArtifactBag) -> list[str]:
    warnings: list[str] = []
    for artifact in artifact_bag.raw().values():
        artifact_warnings = getattr(artifact, "warnings", None)
        if isinstance(artifact_warnings, list):
            warnings.extend(str(warning) for warning in artifact_warnings)
    return warnings


def _manifest_placeholder_warnings(config_path: Path) -> list[str]:
    """Return source-warning strings for each ``CHANGE_ME`` placeholder
    surviving in the manifest text.

    Doctor already surfaces these as ``SHIP-DIAG-CHANGE-ME-PLACEHOLDERS``
    diagnostics; the same fact also needs to flow into the scan so the
    existing ``source_warning_count > 0 → review_required`` branch in
    release_decision.evidence_coverage trips. Read failures (missing
    file, non-UTF8 content) yield no warnings — the manifest loader runs
    immediately before and will have already raised a structured error
    in that case.
    """
    try:
        manifest_text = config_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    placeholders = collect_placeholders(manifest_text)
    name = config_path.name
    return [
        f"{name}:{entry['line']} — CHANGE_ME placeholder at "
        f"{entry.get('path', '<root>')!r}; replace before treating this "
        "report as evidence."
        for entry in placeholders
    ]


def _absorb(
    result: LoadedAdapterResult,
    source_type: str,
    sink: list[LoadedToolSource],
    bag: ArtifactBag,
    adapter: ToolSourceAdapter,
) -> None:
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


def _flatten_and_deduplicate_tools(
    loaded_sources: list[LoadedToolSource],
) -> tuple[list[Tool], list[str]]:
    by_id: dict[str, Tool] = {}
    warnings: list[str] = []
    for loaded in loaded_sources:
        for tool in loaded.tools:
            existing = by_id.get(tool.id)
            if not existing:
                by_id[tool.id] = tool
                continue
            if _source_priority(tool) > _source_priority(existing):
                kept, dropped = tool, existing
            else:
                kept, dropped = existing, tool
            by_id[tool.id] = _merge_duplicate_tool_metadata(kept, dropped)
            warnings.append(
                "Duplicate tool name "
                f"{tool.name!r}; kept {kept.source_type} source {kept.source_id!r} "
                f"and merged metadata from {dropped.source_type} source {dropped.source_id!r}."
            )
    return list(by_id.values()), warnings


def _source_priority(tool: Tool) -> int:
    # Anthropic and OpenAI artifacts are equally authoritative; on duplicate
    # tool names across them the first-loaded entry wins (OpenAI is loaded
    # first in run_scan), and a `Duplicate tool name` warning surfaces.
    return {
        "openai_api": 40,
        "anthropic_api": 40,
        "openapi": 30,
        "google_adk_inventory": 25,
        "langchain_inventory": 25,
        "crewai_inventory": 25,
        "codex_plugin_mcp_inventory": 25,
        "n8n_inventory": 25,
        "mcp": 20,
        "google_adk_function": 10,
        "langchain_function": 10,
        "langchain_structured_tool": 10,
        "crewai_function": 10,
        "crewai_class_tool": 10,
        "n8n_ai_tool": 10,
        "n8n_workflow_tool": 10,
        "n8n_code_tool": 10,
        "n8n_http_tool": 10,
        "n8n_mcp_client_tool": 10,
        "sdk_function": 10,
        "google_adk_config": 5,
        "crewai_prebuilt_tool": 5,
    }.get(tool.source_type, 0)


def _merge_duplicate_tool_metadata(kept: Tool, dropped: Tool) -> Tool:
    merged = kept.model_copy(deep=True)
    merged.annotations = {**dropped.annotations, **merged.annotations}
    seen_hints = {_risk_hint_key(hint) for hint in merged.risk_hints}
    for hint in dropped.risk_hints:
        key = _risk_hint_key(hint)
        if key in seen_hints:
            continue
        merged.risk_hints.append(hint.model_copy(deep=True))
        seen_hints.add(key)
    merged.auth = merged.auth.model_copy(deep=True)
    merged.auth.scopes = _merge_string_values(merged.auth.scopes, dropped.auth.scopes)
    if not merged.auth.type:
        merged.auth.type = dropped.auth.type
    if not merged.auth.credential_mode:
        merged.auth.credential_mode = dropped.auth.credential_mode
    if not merged.auth.source and dropped.auth.source:
        merged.auth.source = dropped.auth.source
    if merged.owner is None:
        merged.owner = dropped.owner
    return merged


def _risk_hint_key(hint) -> tuple[str, str, str, str]:
    evidence = json.dumps(hint.evidence, sort_keys=True, default=str)
    return hint.tag, hint.source, hint.confidence, evidence


def _merge_string_values(primary: list[str], secondary: list[str]) -> list[str]:
    merged: list[str] = []
    for value in [*primary, *secondary]:
        if value not in merged:
            merged.append(value)
    return merged
