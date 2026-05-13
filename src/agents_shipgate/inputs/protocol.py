"""Adapter Protocol + AdapterRegistry for tool-source loading.

Every tool-source or scan-artifact loader (mcp, openapi,
openai_agents_sdk, google_adk, langchain, crewai, codex_plugin,
openai_api, anthropic_api, n8n, validation) is exposed as a
``ToolSourceAdapter``. The CLI's ``_load_sources`` walks ``REGISTRY``
to dispatch.

Adding a new builtin adapter in v0.12+ is a two-file change:

  1. Drop a class in ``inputs/<name>.py`` matching the Protocol.
  2. Add it to the tuple in ``_register_builtin_adapters()`` below
     in canonical order (see the docstring there).

Entry-point discovery for third-party plugins is a v0.12 follow-up.

Manifest-only adapters (``openai_api``, ``anthropic_api``, ``n8n``,
``validation``) are registered under string keys that are NOT in
``ToolSourceConfig.type``'s Literal — they never appear in a user's
``tool_sources`` list and always run once per scan via the dispatcher's
pass-2 loop.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Literal, Protocol, runtime_checkable

from agents_shipgate.config.schema import AgentsShipgateManifest, ToolSourceConfig

# Re-export for backward compatibility; protocol.py was ArtifactBag's
# original home in v0.11 R1.
from agents_shipgate.core.artifacts import ArtifactBag as ArtifactBag
from agents_shipgate.core.models import LoadedToolSource


@dataclass(frozen=True)
class LoadedAdapterResult:
    """Result returned by ``ToolSourceAdapter.load()``.

    - ``tool_sources``: zero or more ``LoadedToolSource`` entries.
      Per-source adapters typically return exactly one; per-scan
      adapters may return many (one per discovered framework
      entrypoint) or zero (if no inputs).
    - ``artifact``: optional framework-scoped artifact
      (``GoogleAdkArtifacts``, ``LangChainArtifacts``,
      ``OpenAIApiArtifacts``, etc.). ``None`` for pure adapters that
      don't produce a manifest-level artifact bag. The type MUST match
      the adapter's ``artifact_class`` (validated at dispatch time).
    - ``warnings``: adapter-level warnings that don't belong to any
      single ``LoadedToolSource`` (e.g., "manifest.langchain section is
      empty but tool_sources declares a langchain entry"). Per-tool-
      source warnings continue to live on ``LoadedToolSource.warnings``.
    """

    tool_sources: list[LoadedToolSource] = field(default_factory=list)
    artifact: object | None = None
    warnings: list[str] = field(default_factory=list)


@runtime_checkable
class ToolSourceAdapter(Protocol):
    """Adapter contract for a single tool-source loader.

    ``source_type`` is a string identifying the adapter:
      - For per-source adapters, this matches ``ToolSourceConfig.type``
        (e.g., "mcp").
      - For per-scan framework adapters, this also matches a
        ``ToolSourceConfig.type`` Literal (e.g., "langchain"). The
        adapter runs once per scan when EITHER a matching
        ``tool_sources`` entry is present OR the corresponding
        top-level manifest section is populated.
      - For per-scan manifest-only adapters ("openai_api",
        "anthropic_api", "n8n", "validation"), the string is the
        artifact key — never appears in user ``tool_sources`` entries;
        the adapter always runs once per scan.

    ``scope`` controls dispatch:
      - "per_source": called once per matching ``tool_sources`` entry.
      - "per_scan":   called once per scan (registry dedupes by
                      source_type).

    ``artifact_class`` is the type returned in
    ``LoadedAdapterResult.artifact`` (or ``None`` for pure adapters).
    The dispatcher validates returned artifacts via ``isinstance`` and
    ``ArtifactBag`` uses it for typed retrieval.
    """

    source_type: ClassVar[str]
    scope: ClassVar[Literal["per_source", "per_scan"]]
    artifact_class: ClassVar[type | None]

    def load(
        self,
        source: ToolSourceConfig | None,
        base_dir: Path,
        manifest: AgentsShipgateManifest,
    ) -> LoadedAdapterResult: ...


class AdapterRegistry:
    """Ordered registry of ``ToolSourceAdapter`` instances.

    Iteration is in registration order — used by the scan dispatcher
    to preserve the canonical cohort ordering today's ``run_scan``
    produces (per-source loaders → framework adapters → manifest-only
    adapters). Duplicate registration raises ``RuntimeError``.

    Builtin adapters are populated lazily on first access (see
    ``_ensure_populated``). This avoids a circular-import hazard: each
    adapter module imports ``LoadedAdapterResult`` from this module
    while ``_register_builtin_adapters`` imports the adapter modules.
    Delaying the registration call to first read guarantees every
    adapter module has finished its top-level import before its
    sibling modules are imported during registration.
    """

    def __init__(self, *, autopopulate: bool = True) -> None:
        self._adapters: dict[str, ToolSourceAdapter] = {}
        self._autopopulate = autopopulate
        self._populated = not autopopulate

    def _ensure_populated(self) -> None:
        if self._populated:
            return
        # Set first to prevent re-entry from inside _register_builtin_adapters.
        self._populated = True
        _register_builtin_adapters(self)

    def register(self, adapter: ToolSourceAdapter) -> None:
        if adapter.source_type in self._adapters:
            raise RuntimeError(
                f"ToolSourceAdapter for {adapter.source_type!r} is already registered"
            )
        self._adapters[adapter.source_type] = adapter

    def get(self, source_type: str) -> ToolSourceAdapter | None:
        self._ensure_populated()
        return self._adapters.get(source_type)

    def require(self, source_type: str) -> ToolSourceAdapter:
        self._ensure_populated()
        adapter = self._adapters.get(source_type)
        if adapter is None:
            from agents_shipgate.core.errors import ConfigError

            raise ConfigError(
                f"No adapter registered for source type {source_type!r}. "
                "Add the adapter to "
                "agents_shipgate.inputs.protocol._register_builtin_adapters()."
            )
        return adapter

    def __iter__(self) -> Iterator[ToolSourceAdapter]:
        self._ensure_populated()
        return iter(self._adapters.values())

    def __contains__(self, source_type: object) -> bool:
        self._ensure_populated()
        return source_type in self._adapters

    def __len__(self) -> int:
        self._ensure_populated()
        return len(self._adapters)

    def per_scan_adapters(self) -> Iterator[ToolSourceAdapter]:
        self._ensure_populated()
        for adapter in self._adapters.values():
            if adapter.scope == "per_scan":
                yield adapter


REGISTRY = AdapterRegistry()


def _register_builtin_adapters(registry: AdapterRegistry) -> None:
    """Populate ``registry`` in canonical cohort order.

    The order matters. Pass-1 of the dispatcher walks
    ``manifest.tool_sources`` and routes through whichever adapter
    matches; pass-2 walks ``REGISTRY.per_scan_adapters()`` (in this
    order) for any per-scan adapter not yet invoked. The resulting
    output ordering mirrors the legacy ``run_scan``:

        per-source loaders (declared order)
        → google_adk → langchain → crewai → n8n
        → openai_api → anthropic_api → codex_plugin → validation

    Adapter modules are imported lazily here to avoid a top-level
    cycle: each adapter module imports ``LoadedAdapterResult`` from
    this module at module load time. Doing the imports inside this
    function (called from ``AdapterRegistry._ensure_populated``) means
    every adapter module has finished its top-level imports before its
    siblings are imported here.
    """
    from agents_shipgate.inputs.anthropic_api import AnthropicAPIAdapter
    from agents_shipgate.inputs.codex_plugin import CodexPluginAdapter
    from agents_shipgate.inputs.crewai import CrewAIAdapter
    from agents_shipgate.inputs.google_adk import GoogleADKAdapter
    from agents_shipgate.inputs.langchain import LangChainAdapter
    from agents_shipgate.inputs.mcp import MCPAdapter
    from agents_shipgate.inputs.n8n import N8nAdapter
    from agents_shipgate.inputs.openai_api import OpenAIAPIAdapter
    from agents_shipgate.inputs.openai_sdk_static import OpenAISDKAdapter
    from agents_shipgate.inputs.openapi import OpenAPIAdapter
    from agents_shipgate.inputs.validation import ValidationAdapter

    for adapter in (
        MCPAdapter(),
        OpenAPIAdapter(),
        OpenAISDKAdapter(),
        GoogleADKAdapter(),
        LangChainAdapter(),
        CrewAIAdapter(),
        N8nAdapter(),
        OpenAIAPIAdapter(),
        AnthropicAPIAdapter(),
        CodexPluginAdapter(),
        ValidationAdapter(),
    ):
        registry.register(adapter)
