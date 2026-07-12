"""Unit tests for the ``inputs/protocol.py`` adapter Protocol + registry.

Coverage builds out across the v0.11 migration steps; see
``docs/plans/please-carefully-plan-for-hidden-quasar.md`` for the full
case plan. Steps 1 and 3 add registration / Protocol / ArtifactBag
coverage; Step 6 adds dispatch-loop coverage (config-only frameworks,
ordering, type-validation).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Literal

import pytest

from agents_shipgate.cli.scan.source_loading import _load_sources
from agents_shipgate.config.loader import load_manifest
from agents_shipgate.core.artifact_models import (
    AnthropicArtifacts,
    CodexPluginArtifacts,
    CrewAiArtifacts,
    GoogleAdkArtifacts,
    LangChainArtifacts,
    OpenAIApiArtifacts,
    ValidationArtifacts,
)
from agents_shipgate.core.errors import ConfigError, InputParseError
from agents_shipgate.inputs.protocol import (
    REGISTRY,
    AdapterRegistry,
    ArtifactBag,
    LoadedAdapterResult,
    ToolSourceAdapter,
)
from agents_shipgate.schemas.manifest import (
    AgentsShipgateManifest,
    ToolSourceConfig,
)


@dataclass
class _StubArtifact:
    name: str


class _StubAdapter:
    source_type: ClassVar[str] = "stub"
    scope: ClassVar[Literal["per_source", "per_scan"]] = "per_source"
    artifact_class: ClassVar[type | None] = None

    def load(
        self,
        source: ToolSourceConfig | None,
        base_dir: Path,
        manifest: AgentsShipgateManifest,
    ) -> LoadedAdapterResult:
        return LoadedAdapterResult()


def test_register_rejects_duplicate():
    registry = AdapterRegistry(autopopulate=False)
    registry.register(_StubAdapter())
    with pytest.raises(RuntimeError, match="already registered"):
        registry.register(_StubAdapter())


def test_artifact_bag_get_returns_none_when_missing():
    bag = ArtifactBag()
    assert bag.get("missing", _StubArtifact) is None


def test_artifact_bag_get_returns_typed_value():
    bag = ArtifactBag()
    artifact = _StubArtifact(name="hello")
    bag.set("stub", artifact)
    retrieved = bag.get("stub", _StubArtifact)
    assert retrieved is artifact
    assert retrieved.name == "hello"


def test_artifact_bag_get_raises_on_type_mismatch():
    bag = ArtifactBag()
    bag.set("stub", "not an artifact")
    with pytest.raises(TypeError, match="expected _StubArtifact"):
        bag.get("stub", _StubArtifact)


def test_stub_adapter_satisfies_protocol():
    assert isinstance(_StubAdapter(), ToolSourceAdapter)


def test_adapters_registered_for_every_tool_source_type():
    """Every built-in source type allowed in ``tool_sources[].type``
    must have a matching adapter in ``REGISTRY``. Catches drift if a
    new source type lands without registration.

    v0.20 PR #111 review fix: ``ToolSourceConfig.type`` is now ``str``
    (relaxed from Literal so third-party adapters can register custom
    types). The curated set of built-ins lives in
    ``BUILTIN_TOOL_SOURCE_TYPES`` — this test iterates that.
    """

    from agents_shipgate.schemas.manifest import BUILTIN_TOOL_SOURCE_TYPES

    assert BUILTIN_TOOL_SOURCE_TYPES, "expected at least one built-in source type"
    for source_type in BUILTIN_TOOL_SOURCE_TYPES:
        assert source_type in REGISTRY, (
            f"no adapter registered for tool source type {source_type!r}"
        )


def test_manifest_only_adapters_registered():
    """Manifest-only adapters live outside the
    ``ToolSourceConfig.type`` Literal but must still be in the
    registry under per_scan scope."""

    for source_type in ("openai_api", "anthropic_api", "n8n", "validation"):
        adapter = REGISTRY.get(source_type)
        assert adapter is not None, f"{source_type!r} adapter not registered"
        assert adapter.scope == "per_scan"


def test_require_unknown_source_type_raises_config_error():
    """``REGISTRY.require`` fails loud on unknown types — protects
    against silent drops if a future schema literal forgets to
    register an adapter."""

    with pytest.raises(ConfigError, match="No adapter registered"):
        REGISTRY.require("does_not_exist")


def test_every_registered_adapter_satisfies_protocol():
    """Every adapter in the populated ``REGISTRY`` satisfies the
    ``ToolSourceAdapter`` Protocol — guards against an adapter class
    missing a required ``ClassVar``."""

    for adapter in REGISTRY:
        assert isinstance(adapter, ToolSourceAdapter), (
            f"{type(adapter).__name__} does not satisfy ToolSourceAdapter Protocol"
        )


def test_canonical_registration_order():
    """Pin the canonical cohort order. The dispatcher's pass-2 loop
    iterates ``REGISTRY`` in this order; any reshuffle will change
    the output ordering of ``_load_sources`` and therefore the
    dedup-tie-break outcome in ``_flatten_and_deduplicate_tools``."""

    expected = [
        "mcp",
        "openapi",
        "openai_agents_sdk",
        "google_adk",
        "langchain",
        "crewai",
        "n8n",
        "conductor",
        "openai_api",
        "anthropic_api",
        "codex_config",
        "codex_plugin",
        "validation",
    ]
    actual = [adapter.source_type for adapter in REGISTRY]
    assert actual == expected


# ---------------------------------------------------------------------------
# Dispatch-loop coverage. The dispatcher under test is
# ``cli/scan/source_loading.py:_load_sources``.
# ---------------------------------------------------------------------------


REFUND_SAMPLE = Path("samples/support_refund_agent/shipgate.yaml")
GOOGLE_ADK_SAMPLE = Path("samples/google_adk_agent/shipgate.yaml")


def _force_registry_populated() -> None:
    """Trigger ``AdapterRegistry`` lazy population.

    Tests that monkeypatch ``REGISTRY._adapters`` must run this first.
    Without it, the first read inside the dispatcher triggers
    ``_register_builtin_adapters`` against an ``_adapters`` dict that
    already contains the stub, which raises ``RuntimeError`` for
    duplicate registration. Reading via ``list(REGISTRY)`` runs
    ``_ensure_populated`` exactly once and is a no-op on subsequent
    calls.
    """
    list(REGISTRY)


def test_dispatch_optional_source_warning(tmp_path):
    """An ``optional`` source that fails to load surfaces a warning-
    only ``LoadedToolSource`` and does NOT escape the dispatcher."""

    manifest = load_manifest(REFUND_SAMPLE)
    # All refund sources are required today; rewrite one to point at a
    # missing file with optional=True.
    rewritten = [
        ToolSourceConfig(
            id="missing_mcp",
            type="mcp",
            path="does/not/exist.json",
            optional=True,
        )
    ]
    manifest_with_optional = manifest.model_copy(
        update={"tool_sources": rewritten}
    )
    base_dir = REFUND_SAMPLE.resolve().parent
    loaded, _bag = _load_sources(manifest_with_optional, base_dir, verbose=False)
    warning_sources = [
        src for src in loaded if src.source_id == "missing_mcp"
    ]
    assert warning_sources, "expected a warning-only LoadedToolSource for the optional source"
    assert warning_sources[0].warnings
    assert "failed to load" in warning_sources[0].warnings[0]


def test_dispatch_required_source_raises(tmp_path):
    """A non-optional source pointing at a missing file propagates
    ``InputParseError`` from the dispatcher."""

    manifest = load_manifest(REFUND_SAMPLE)
    rewritten = [
        ToolSourceConfig(
            id="missing_mcp",
            type="mcp",
            path="does/not/exist.json",
            optional=False,
        )
    ]
    manifest_with_missing = manifest.model_copy(
        update={"tool_sources": rewritten}
    )
    base_dir = REFUND_SAMPLE.resolve().parent
    with pytest.raises(InputParseError):
        _load_sources(manifest_with_missing, base_dir, verbose=False)


def test_framework_adapter_invoked_for_config_only():
    """**P1 regression guard.** Schema permits
    ``manifest.google_adk`` to be configured without a matching
    ``tool_sources`` entry. The dispatcher's pass 2 must invoke
    ``GoogleADKAdapter`` even when the user removes the redundant
    ``tool_sources`` row."""

    manifest = load_manifest(GOOGLE_ADK_SAMPLE)
    config_only = manifest.model_copy(update={"tool_sources": []})
    # The sample still has the top-level `google_adk:` section, so pass 2
    # should pick it up.
    base_dir = GOOGLE_ADK_SAMPLE.resolve().parent
    _loaded, bag = _load_sources(config_only, base_dir, verbose=False)
    artifacts = bag.get("google_adk", GoogleAdkArtifacts)
    assert artifacts is not None, (
        "GoogleADKAdapter must run when manifest.google_adk is configured "
        "even if no tool_sources entry matches"
    )


def test_manifest_only_adapters_run_with_empty_tool_sources():
    """openai_api, anthropic_api, n8n, and validation adapters fire via pass 2
    regardless of ``tool_sources`` contents. With an empty
    ``tool_sources`` and no manifest sections configured for these
    adapters, the dispatcher must still call them (they return
    artifact=None, but the invocation itself shouldn't be skipped)."""

    manifest = load_manifest(REFUND_SAMPLE)
    base = manifest.model_copy(update={"tool_sources": [], "openai_api": None})
    base_dir = REFUND_SAMPLE.resolve().parent
    _loaded, bag = _load_sources(base, base_dir, verbose=False)
    # No artifacts because configs are absent. The test passes if the
    # dispatcher didn't raise.
    assert bag.get("openai_api", OpenAIApiArtifacts) is None
    assert bag.get("anthropic_api", AnthropicArtifacts) is None
    assert bag.get("validation", ValidationArtifacts) is None


def test_per_scan_framework_adapter_invoked_once_with_multiple_tool_sources(monkeypatch):
    """Duplicate ``tool_sources`` entries of the same framework type
    invoke the adapter exactly once."""

    _force_registry_populated()
    call_count = {"value": 0}

    class _RecordingLangChain:
        source_type: ClassVar[str] = "langchain"
        scope: ClassVar[Literal["per_source", "per_scan"]] = "per_scan"
        artifact_class: ClassVar[type | None] = LangChainArtifacts

        def load(self, source, base_dir, manifest):
            call_count["value"] += 1
            return LoadedAdapterResult(artifact=LangChainArtifacts())

    monkeypatch.setitem(REGISTRY._adapters, "langchain", _RecordingLangChain())
    manifest = load_manifest(REFUND_SAMPLE)
    duplicated = manifest.model_copy(
        update={
            "tool_sources": [
                ToolSourceConfig(id="lc1", type="langchain", path="a.py"),
                ToolSourceConfig(id="lc2", type="langchain", path="b.py"),
            ]
        }
    )
    base_dir = REFUND_SAMPLE.resolve().parent
    _load_sources(duplicated, base_dir, verbose=False)
    assert call_count["value"] == 1


def test_dispatcher_validates_adapter_artifact_class(monkeypatch):
    """**P1 regression guard.** If an adapter declares one
    ``artifact_class`` but returns a different type, the dispatcher
    raises ``TypeError`` rather than letting the wrong-typed artifact
    leak into ``ScanContext``/packet rendering."""

    _force_registry_populated()

    class _LyingAdapter:
        source_type: ClassVar[str] = "langchain"
        scope: ClassVar[Literal["per_source", "per_scan"]] = "per_scan"
        artifact_class: ClassVar[type | None] = LangChainArtifacts

        def load(self, source, base_dir, manifest):
            # Declares LangChainArtifacts but returns CrewAiArtifacts.
            return LoadedAdapterResult(artifact=CrewAiArtifacts())

    monkeypatch.setitem(REGISTRY._adapters, "langchain", _LyingAdapter())
    manifest = load_manifest(REFUND_SAMPLE)
    triggered = manifest.model_copy(
        update={
            "tool_sources": [
                ToolSourceConfig(id="lc", type="langchain", path="a.py"),
            ]
        }
    )
    base_dir = REFUND_SAMPLE.resolve().parent
    with pytest.raises(TypeError, match="declared artifact_class=LangChainArtifacts"):
        _load_sources(triggered, base_dir, verbose=False)


def test_codex_plugin_artifact_in_bag():
    """Smoke test for the manifest-only ``codex_plugin`` extraction:
    artifact_bag exposes the typed value to scan.py."""

    manifest = load_manifest(REFUND_SAMPLE)
    base_dir = REFUND_SAMPLE.resolve().parent
    _loaded, bag = _load_sources(manifest, base_dir, verbose=False)
    artifact = bag.get("codex_plugin", CodexPluginArtifacts)
    # Sample doesn't configure codex_plugins, so artifact is None — but
    # the typed get() succeeded without TypeError.
    assert artifact is None or isinstance(artifact, CodexPluginArtifacts)


def test_per_scan_order_is_canonical_not_tool_sources(monkeypatch):
    """**P2 regression guard.** Per-scan adapter output order is fixed
    by ``REGISTRY`` registration order, NOT by ``manifest.tool_sources``
    declaration order. Otherwise duplicate tie-break in
    ``_flatten_and_deduplicate_tools`` (which keeps the first source
    when priorities tie) would change based on user-facing yaml
    ordering."""

    _force_registry_populated()
    invocation_order: list[str] = []

    def _make_recorder(name: str, artifact_cls: type) -> object:
        class _Recorder:
            source_type: ClassVar[str] = name
            scope: ClassVar[Literal["per_source", "per_scan"]] = "per_scan"
            artifact_class: ClassVar[type | None] = artifact_cls

            def load(self, source, base_dir, manifest):
                invocation_order.append(name)
                return LoadedAdapterResult(artifact=artifact_cls())

        return _Recorder()

    monkeypatch.setitem(
        REGISTRY._adapters, "langchain", _make_recorder("langchain", LangChainArtifacts)
    )
    monkeypatch.setitem(
        REGISTRY._adapters, "google_adk", _make_recorder("google_adk", GoogleAdkArtifacts)
    )

    manifest = load_manifest(REFUND_SAMPLE)
    # Declare langchain BEFORE google_adk in tool_sources. If the
    # dispatcher honored tool_sources order for per-scan adapters,
    # langchain would invoke first. Canonical REGISTRY order is
    # google_adk → langchain.
    swapped = manifest.model_copy(
        update={
            "tool_sources": [
                ToolSourceConfig(id="lc", type="langchain", path="a.py"),
                ToolSourceConfig(id="adk", type="google_adk", path="b.py"),
            ]
        }
    )
    base_dir = REFUND_SAMPLE.resolve().parent
    _load_sources(swapped, base_dir, verbose=False)
    # google_adk is registered before langchain in _register_builtin_adapters.
    assert invocation_order.index("google_adk") < invocation_order.index("langchain")
