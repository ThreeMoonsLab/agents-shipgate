"""Adapter Protocol + AdapterRegistry for tool-source loading.

Every tool-source or scan-artifact loader (mcp, openapi,
openai_agents_sdk, google_adk, langchain, crewai, codex_config, codex_plugin,
openai_api, anthropic_api, n8n, validation) is exposed as a
``ToolSourceAdapter``. The CLI's ``_load_sources`` walks ``REGISTRY``
to dispatch.

Adding a new built-in adapter in v0.12+ is a two-file change:

  1. Drop a class in ``inputs/<name>.py`` matching the Protocol.
  2. Add it to the tuple in ``_register_builtin_adapters()`` below
     in canonical order (see the docstring there).

**Third-party adapter discovery (v0.20+).** Third-party adapters
register through the ``agents_shipgate.adapters`` entry-point group.
``discover_third_party_adapters()`` validates each entry point through
the four gates in ``inputs/adapter_validation.py`` (load,
bad_protocol, bad_scope, source_type_collision) and registers the
valid ones onto the supplied registry. Discovery is opt-in: it runs
only when ``AGENTS_SHIPGATE_ENABLE_PLUGINS=1`` is set (or an explicit
``plugins_enabled=True`` override is passed), and ``--no-plugins``
forces it off. Both valid and invalid records surface in
``ReadinessReport.loaded_adapters[]`` so reviewers can see what was
skipped without reading scanner logs. Runtime errors and
artifact-class smuggling attempts are also captured into
``loaded_adapters[].runtime_errors`` via ``run_validated_adapter``.

Manifest-only adapters (``openai_api``, ``anthropic_api``, ``n8n``,
``validation``) are registered under string keys that are NOT in
``ToolSourceConfig.type``'s Literal — they never appear in a user's
``tool_sources`` list and always run once per scan via the dispatcher's
pass-2 loop.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, ClassVar, Literal, Protocol, runtime_checkable

# Re-export for backward compatibility; protocol.py was ArtifactBag's
# original home in v0.11 R1.
from agents_shipgate.core.artifacts import ArtifactBag as ArtifactBag
from agents_shipgate.core.domain import LoadedToolSource
from agents_shipgate.schemas.manifest import (
    AgentsShipgateManifest,
    ToolSourceConfig,
)


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

    def clone(self) -> AdapterRegistry:
        """v0.20 (PR #111 review follow-up): return a shallow-copy
        registry snapshot for use as a *per-scan* adapter registry.

        The clone shares no mutable state with the original — its
        ``_adapters`` dict is a fresh copy. Subsequent ``register()`` /
        third-party additions on the clone do NOT propagate to the
        global ``REGISTRY``. Closes two P1 review findings:

        - ``--no-plugins`` on a later in-process scan now actually
          disables third-party adapters discovered by an earlier scan
          (which previously persisted in the global registry).
        - Collision detection on the second scan sees only the
          builtins (clone-time state), not adapters carried over from
          scan one, so a stable third-party adapter no longer
          mis-reports as ``source_type_collision``.

        Tests that monkeypatch ``REGISTRY._adapters`` before
        ``_load_inputs`` runs are preserved: the clone captures the
        global's state at that moment, including monkeypatch
        modifications.
        """

        clone = AdapterRegistry(autopopulate=False)
        # Lazy-populate ourselves first so the clone inherits the
        # canonical builtin set even when this is the global REGISTRY's
        # first read.
        self._ensure_populated()
        clone._adapters = dict(self._adapters)
        clone._populated = True
        return clone

    def get(self, source_type: str) -> ToolSourceAdapter | None:
        self._ensure_populated()
        return self._adapters.get(source_type)

    def require(
        self, source_type: str, *, plugins_enabled: bool | None = None
    ) -> ToolSourceAdapter:
        self._ensure_populated()
        adapter = self._adapters.get(source_type)
        if adapter is None:
            from agents_shipgate.core.errors import ConfigError

            # v0.20 (PR #111 review follow-up #5): the original error
            # message ("Add the adapter to _register_builtin_adapters")
            # was contributor-facing and obsolete after the
            # ``tool_sources[].type: str`` relaxation. Users hitting
            # this in production have three real remediation paths;
            # surface them in the message so ``str(exc)`` is
            # actionable on its own. The agent-mode next_actions
            # built by ``diagnose_unknown_adapter_source_type`` cover
            # the same paths in structured form.
            discovery_enabled = _adapter_plugins_enabled(plugins_enabled)
            if discovery_enabled:
                remediation = (
                    "Either (a) install the third-party adapter "
                    "package that registers this source_type, or "
                    "(b) fix a typo of a built-in name "
                    "(`agents-shipgate list-checks --json` exposes "
                    "the catalog and `agents-shipgate doctor --json` "
                    "lists discovered adapters)."
                )
            else:
                remediation = (
                    "Either (a) enable third-party adapter discovery "
                    "by setting `AGENTS_SHIPGATE_ENABLE_PLUGINS=1` "
                    "(or removing `--no-plugins`) and ensure the "
                    "adapter package is installed, or (b) fix a typo "
                    "of a built-in name (built-ins are: mcp, openapi, "
                    "openai_agents_sdk, google_adk, langchain, crewai, "
                    "codex_config, codex_plugin)."
                )
            raise ConfigError(
                f"No adapter registered for source type "
                f"{source_type!r}. {remediation}"
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

        per-source loaders (declared order, including codex_config when declared)
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
    from agents_shipgate.inputs.codex_config import CodexConfigAdapter
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
        CodexConfigAdapter(),
        CodexPluginAdapter(),
        ValidationAdapter(),
    ):
        registry.register(adapter)


# v0.20: Third-party adapter discovery via the
# ``agents_shipgate.adapters`` entry-point group. Parallels the
# ``agents_shipgate.checks`` plugin discovery in ``checks/registry.py``
# but is gated by the same env var / CLI flag so a single
# ``--no-plugins`` blocks both. See ``inputs/adapter_validation.py``
# for the four-gate validation pipeline.

ADAPTER_ENTRY_POINT_GROUP = "agents_shipgate.adapters"


def discover_third_party_adapters(
    registry: AdapterRegistry,
    *,
    plugins_enabled: bool | None = None,
    loaded_adapters: list[dict[str, Any]] | None = None,
) -> list[Any]:
    """Walk ``entry_points('agents_shipgate.adapters')``, validate each
    third-party adapter, and register the valid ones onto ``registry``.

    **Per-scan registry contract (v0.20 review fix).** ``registry``
    MUST be a per-scan instance (typically ``REGISTRY.clone()`` from
    ``_load_inputs``), NOT the global ``REGISTRY`` itself. Mutating
    the global broke two trust invariants:

    1. ``--no-plugins`` on a later scan didn't disable adapters
       registered by an earlier scan, because the dispatcher still
       resolved them through the global registry.
    2. Collision detection on the second scan misclassified
       previously-registered third-party adapters as
       ``source_type_collision`` against themselves.

    Callers pass a clone so each scan starts from a known-good
    builtins-only snapshot.

    Returns the list of ``LoadedAdapter`` records (both valid and
    invalid). When ``loaded_adapters`` is provided, the per-row
    ``info`` dicts are appended to it for downstream
    ``ReadinessReport.loaded_adapters[]`` surfacing.

    Discovery is opt-in: a no-op when
    ``AGENTS_SHIPGATE_ENABLE_PLUGINS`` is unset and
    ``plugins_enabled`` is not explicitly ``True``. ``--no-plugins``
    on the CLI translates to ``plugins_enabled=False`` and forces this
    off even when the env var is set.
    """

    from agents_shipgate.inputs.adapter_validation import (
        LoadedAdapter,
        validate_adapter_entry_point,
    )

    if not _adapter_plugins_enabled(plugins_enabled):
        return []

    # Ensure builtins are populated before we measure the collision
    # set — otherwise a third-party adapter declaring source_type="mcp"
    # would be erroneously accepted on a freshly-instantiated registry.
    registry._ensure_populated()
    builtin_source_types = set(registry._adapters.keys())
    already_registered: set[str] = set()

    records: list[LoadedAdapter] = []

    for entry_point in entry_points(group=ADAPTER_ENTRY_POINT_GROUP):
        if _is_builtin_adapter_entry_point(entry_point):
            continue
        record = validate_adapter_entry_point(
            entry_point,
            builtin_source_types=builtin_source_types,
            already_registered_source_types=already_registered,
        )
        records.append(record)
        if loaded_adapters is not None:
            loaded_adapters.append(record.info)
        if record.adapter is not None:
            # Register on the live registry so subsequent
            # ``REGISTRY.require(source_type)`` calls from the
            # dispatcher resolve the third-party adapter. Track the
            # source_type for the collision gate in this same loop.
            registry.register(record.adapter)
            already_registered.add(record.adapter.source_type)

    return records


def _adapter_plugins_enabled(override: bool | None = None) -> bool:
    """Mirror ``checks/registry.py:_plugins_enabled`` so a single
    ``AGENTS_SHIPGATE_ENABLE_PLUGINS`` env var / ``--no-plugins``
    flag governs BOTH plugin checks AND third-party adapters.
    """

    if override is not None:
        return override
    value = os.environ.get("AGENTS_SHIPGATE_ENABLE_PLUGINS", "")
    return value.lower() in {"1", "true", "yes", "on"}


def _is_builtin_adapter_entry_point(entry_point: Any) -> bool:
    """True for entry points that came from the agents-shipgate
    distribution itself.

    The built-in adapters do NOT register through entry points today
    (they are registered explicitly via
    ``_register_builtin_adapters``), so this is defensive: it future-
    proofs the discovery against a hypothetical future where a built-
    in adapter migrates to entry-point discovery. Without this guard,
    that migration would cause every built-in adapter to be flagged
    as a source_type collision against itself.
    """

    dist = getattr(entry_point, "dist", None)
    distribution_name = _adapter_distribution_name(dist)
    if _adapter_normalize_distribution_name(distribution_name) == "agents-shipgate":
        return True
    if dist is None:
        return str(getattr(entry_point, "value", "")).startswith(
            "agents_shipgate.inputs."
        )
    return False


def _adapter_distribution_name(dist: Any) -> str | None:
    if dist is None:
        return None
    metadata = getattr(dist, "metadata", None)
    if metadata is not None:
        try:
            name = metadata.get("Name")
        except AttributeError:
            name = None
        if isinstance(name, str) and name:
            return name
    name = getattr(dist, "name", None)
    return str(name) if name else None


def _adapter_normalize_distribution_name(value: str | None) -> str:
    return (value or "").replace("_", "-").lower()
