"""Tests for v0.20 third-party adapter discovery + validation.

Mirrors ``tests/test_plugin_validation.py`` shape: each load-time gate
gets a dedicated test that synthesizes an entry-point and asserts the
resulting ``loaded_adapters[]`` row. Plus end-to-end tests covering:

- gating by ``AGENTS_SHIPGATE_ENABLE_PLUGINS`` env / ``plugins_enabled`` override
- gating by ``--no-plugins`` (override=False forces off)
- valid third-party adapter end-to-end: registered, invoked, surfaced
  in ``loaded_adapters[]`` with status ``valid``
- ``--strict-plugins`` exits non-zero when an adapter fails validation
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Literal

import pytest

from agents_shipgate.cli.scan import run_scan
from agents_shipgate.inputs import protocol as protocol_module
from agents_shipgate.inputs.adapter_validation import (
    BAD_PROTOCOL,
    BAD_SCOPE,
    LOAD_FAILED,
    SOURCE_TYPE_COLLISION,
    VALID,
    LoadedAdapter,
    run_validated_adapter,
    strict_adapter_failure_messages,
    validate_adapter_entry_point,
)
from agents_shipgate.inputs.protocol import (
    REGISTRY,
    AdapterRegistry,
    LoadedAdapterResult,
    discover_third_party_adapters,
)
from agents_shipgate.schemas.manifest import (
    AgentsShipgateManifest,
    ToolSourceConfig,
)

CLEAN_FIXTURE = Path("samples/clean_read_only_agent/shipgate.yaml")


# --- entry-point synthesis -------------------------------------------------


class _Dist:
    metadata = {"Name": "test-adapter-dist"}
    version = "1.2.3"


def _entry_point(
    load_fn: Any,
    *,
    name: str = "third-party-adapter",
    value: str = "third_party_pkg.module:Adapter",
    dist: Any = None,
) -> Any:
    """Synthesize an entry-point object suitable for monkeypatching
    ``importlib.metadata.entry_points``.

    Matches the shape we use in ``test_plugin_validation.py``: a
    plain object with name/value/dist/load attributes.
    """

    class EP:
        pass

    EP.name = name
    EP.value = value
    EP.dist = dist if dist is not None else _Dist()
    EP.load = staticmethod(load_fn)
    return EP()


def _patch_adapter_entries(monkeypatch, entries: list[Any]) -> None:
    """Monkeypatch the adapter entry-points lookup + enable plugins."""

    monkeypatch.setenv("AGENTS_SHIPGATE_ENABLE_PLUGINS", "1")

    def fake_entry_points(*, group: str) -> list[Any]:
        if group == protocol_module.ADAPTER_ENTRY_POINT_GROUP:
            return entries
        return []

    monkeypatch.setattr(protocol_module, "entry_points", fake_entry_points)


@pytest.fixture(autouse=True)
def _populate_registry(monkeypatch):
    """v0.20 PR #111 review fix: discovery no longer mutates the
    global ``REGISTRY``. The autouse fixture only triggers lazy
    population so subsequent tests see a stable builtin set; no
    cleanup is needed because ``_load_inputs`` operates on a per-scan
    ``REGISTRY.clone()`` and third-party additions land there, never
    on the global.
    """

    REGISTRY._ensure_populated()
    yield


# --- valid adapter shapes (positive controls) ------------------------------


class _ThirdPartyAdapter:
    """A valid third-party adapter that doesn't collide with builtins."""

    source_type: ClassVar[str] = "third_party_demo"
    scope: ClassVar[Literal["per_source", "per_scan"]] = "per_scan"
    artifact_class: ClassVar[type | None] = None

    def load(
        self,
        source: ToolSourceConfig | None,
        base_dir: Path,
        manifest: AgentsShipgateManifest,
    ) -> LoadedAdapterResult:
        return LoadedAdapterResult()


def test_valid_third_party_adapter_lands_in_loaded_adapters(monkeypatch, tmp_path):
    """End-to-end: a valid third-party adapter (class or instance) is
    discovered, registered, and shows up in ``report.loaded_adapters[]``
    with ``validation_status == "valid"``.
    """

    _patch_adapter_entries(
        monkeypatch, [_entry_point(lambda: _ThirdPartyAdapter)]
    )

    report, exit_code = run_scan(
        config_path=CLEAN_FIXTURE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )

    assert exit_code == 0
    assert len(report.loaded_adapters) == 1
    record = report.loaded_adapters[0]
    assert record["validation_status"] == VALID
    assert record["source_type"] == "third_party_demo"
    assert record["validation_errors"] == []
    assert record["runtime_errors"] == []
    assert record["distribution"] == "test-adapter-dist"
    assert record["version"] == "1.2.3"
    # v0.20 PR #111 review fix: the per-scan registry holds the
    # third-party adapter for the duration of this scan, but the
    # global REGISTRY stays builtin-only across the process. A later
    # ``--no-plugins`` scan sees a clean slate.
    assert "third_party_demo" not in REGISTRY._adapters, (
        "global REGISTRY must stay builtin-only — discovery should "
        "mutate a per-scan clone, not the global"
    )


def test_adapter_instance_value_is_accepted(monkeypatch, tmp_path):
    """``entry_point.load()`` may return either an instance or a class.
    The validator handles both shapes.
    """

    _patch_adapter_entries(
        monkeypatch, [_entry_point(lambda: _ThirdPartyAdapter())]
    )

    report, _ = run_scan(
        config_path=CLEAN_FIXTURE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    record = report.loaded_adapters[0]
    assert record["validation_status"] == VALID


# --- gate 1: load_failed ---------------------------------------------------


def test_gate_load_failure(monkeypatch, tmp_path):
    def boom():
        raise ImportError("synthetic broken adapter module")

    _patch_adapter_entries(monkeypatch, [_entry_point(boom)])

    report, exit_code = run_scan(
        config_path=CLEAN_FIXTURE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )

    assert exit_code == 0, "lenient default must continue on adapter failures"
    assert len(report.loaded_adapters) == 1
    record = report.loaded_adapters[0]
    assert record["validation_status"] == LOAD_FAILED
    assert any("synthetic broken adapter module" in err for err in record["validation_errors"])
    assert record["source_type"] is None


# --- gate 2: bad_protocol --------------------------------------------------


def test_gate_bad_protocol_missing_source_type(monkeypatch, tmp_path):
    class _NoSourceType:
        scope: ClassVar[Literal["per_source", "per_scan"]] = "per_source"
        artifact_class: ClassVar[type | None] = None

        def load(self, source, base_dir, manifest):
            return LoadedAdapterResult()

    _patch_adapter_entries(monkeypatch, [_entry_point(lambda: _NoSourceType)])

    report, _ = run_scan(
        config_path=CLEAN_FIXTURE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    record = report.loaded_adapters[0]
    assert record["validation_status"] == BAD_PROTOCOL
    assert any("source_type" in err for err in record["validation_errors"])


def test_gate_bad_protocol_empty_source_type(monkeypatch, tmp_path):
    class _EmptySourceType:
        source_type: ClassVar[str] = ""
        scope: ClassVar[Literal["per_source", "per_scan"]] = "per_source"
        artifact_class: ClassVar[type | None] = None

        def load(self, source, base_dir, manifest):
            return LoadedAdapterResult()

    _patch_adapter_entries(monkeypatch, [_entry_point(lambda: _EmptySourceType)])

    report, _ = run_scan(
        config_path=CLEAN_FIXTURE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    record = report.loaded_adapters[0]
    assert record["validation_status"] == BAD_PROTOCOL


def test_gate_bad_protocol_load_not_callable(monkeypatch, tmp_path):
    class _NotCallableLoad:
        source_type: ClassVar[str] = "bad_load"
        scope: ClassVar[Literal["per_source", "per_scan"]] = "per_source"
        artifact_class: ClassVar[type | None] = None
        load = "not a method"  # type: ignore[assignment]

    _patch_adapter_entries(monkeypatch, [_entry_point(lambda: _NotCallableLoad)])

    report, _ = run_scan(
        config_path=CLEAN_FIXTURE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    record = report.loaded_adapters[0]
    assert record["validation_status"] == BAD_PROTOCOL
    assert any("load" in err for err in record["validation_errors"])


# --- gate 3: bad_scope -----------------------------------------------------


def test_gate_bad_scope(monkeypatch, tmp_path):
    class _BadScope:
        source_type: ClassVar[str] = "bad_scope_demo"
        scope: ClassVar[Any] = "whenever_i_feel_like_it"  # not in {per_source, per_scan}
        artifact_class: ClassVar[type | None] = None

        def load(self, source, base_dir, manifest):
            return LoadedAdapterResult()

    _patch_adapter_entries(monkeypatch, [_entry_point(lambda: _BadScope)])

    report, _ = run_scan(
        config_path=CLEAN_FIXTURE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    record = report.loaded_adapters[0]
    assert record["validation_status"] == BAD_SCOPE
    assert any("scope" in err for err in record["validation_errors"])


# --- gate 4: source_type_collision (the load-bearing trust rule) -----------


@pytest.mark.parametrize("builtin_source_type", ["mcp", "openapi", "langchain", "validation"])
def test_gate_source_type_collision_with_builtin(
    monkeypatch, tmp_path, builtin_source_type
):
    """A third-party adapter MUST NOT shadow a built-in source_type.

    This is the load-bearing trust rule — without it, a malicious
    plugin could displace ``mcp`` (or any other) and intercept every
    scan that loads an MCP source.
    """

    @dataclass
    class _Collider:
        source_type: ClassVar[str] = builtin_source_type
        scope: ClassVar[Literal["per_source", "per_scan"]] = "per_source"
        artifact_class: ClassVar[type | None] = None

        def load(self, source, base_dir, manifest):
            return LoadedAdapterResult()

    _patch_adapter_entries(monkeypatch, [_entry_point(lambda: _Collider)])

    report, _ = run_scan(
        config_path=CLEAN_FIXTURE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    record = report.loaded_adapters[0]
    assert record["validation_status"] == SOURCE_TYPE_COLLISION
    assert any(builtin_source_type in err for err in record["validation_errors"])
    assert any("reserved by a built-in" in err for err in record["validation_errors"])


def test_gate_source_type_collision_between_third_parties(monkeypatch, tmp_path):
    """Two third-party adapters cannot claim the same source_type."""

    class _Twin1:
        source_type: ClassVar[str] = "twin"
        scope: ClassVar[Literal["per_source", "per_scan"]] = "per_scan"
        artifact_class: ClassVar[type | None] = None

        def load(self, source, base_dir, manifest):
            return LoadedAdapterResult()

    class _Twin2(_Twin1):
        pass

    _patch_adapter_entries(
        monkeypatch,
        [
            _entry_point(lambda: _Twin1, name="first", value="pkg:T1"),
            _entry_point(lambda: _Twin2, name="second", value="pkg:T2"),
        ],
    )

    report, _ = run_scan(
        config_path=CLEAN_FIXTURE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    assert len(report.loaded_adapters) == 2
    statuses = sorted(rec["validation_status"] for rec in report.loaded_adapters)
    assert statuses == [SOURCE_TYPE_COLLISION, VALID]
    collider = next(
        rec for rec in report.loaded_adapters
        if rec["validation_status"] == SOURCE_TYPE_COLLISION
    )
    assert any(
        "already registered by another third-party adapter" in err
        for err in collider["validation_errors"]
    )


# --- gating: AGENTS_SHIPGATE_ENABLE_PLUGINS + --no-plugins ----------------


def test_discovery_disabled_by_default(monkeypatch, tmp_path):
    """Without the env var set, discovery is a no-op."""

    monkeypatch.delenv("AGENTS_SHIPGATE_ENABLE_PLUGINS", raising=False)

    def fake_entry_points(*, group: str) -> list[Any]:
        if group == protocol_module.ADAPTER_ENTRY_POINT_GROUP:
            return [_entry_point(lambda: _ThirdPartyAdapter)]
        return []

    monkeypatch.setattr(protocol_module, "entry_points", fake_entry_points)

    report, _ = run_scan(
        config_path=CLEAN_FIXTURE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    assert report.loaded_adapters == [], (
        "discovery must be opt-in via AGENTS_SHIPGATE_ENABLE_PLUGINS"
    )
    assert "third_party_demo" not in REGISTRY._adapters


def test_no_plugins_flag_overrides_env(monkeypatch, tmp_path):
    """``--no-plugins`` (plugins_enabled=False) forces discovery off
    even when the env var is set.
    """

    _patch_adapter_entries(
        monkeypatch, [_entry_point(lambda: _ThirdPartyAdapter)]
    )

    report, _ = run_scan(
        config_path=CLEAN_FIXTURE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
        plugins_enabled=False,
    )
    assert report.loaded_adapters == []
    assert "third_party_demo" not in REGISTRY._adapters


# --- review-fix regression tests (PR #111 P1 #1, P1 #2, P1 #3) ------------


class _RecordingAdapter:
    """A per_scan third-party adapter that records every load() call,
    so tests can prove the adapter ran (or didn't) across multiple
    scans within the same process.
    """

    source_type: ClassVar[str] = "recording_demo"
    scope: ClassVar[Literal["per_source", "per_scan"]] = "per_scan"
    artifact_class: ClassVar[type | None] = None

    invocations: ClassVar[int] = 0

    @classmethod
    def reset(cls) -> None:
        cls.invocations = 0

    def load(
        self,
        source: ToolSourceConfig | None,
        base_dir: Path,
        manifest: AgentsShipgateManifest,
    ) -> LoadedAdapterResult:
        type(self).invocations += 1
        return LoadedAdapterResult()


def test_no_plugins_disables_third_party_after_prior_enabled_scan(
    monkeypatch, tmp_path
):
    """**Regression for PR #111 P1 #1.** A second in-process scan
    with ``plugins_enabled=False`` must NOT execute a third-party
    adapter discovered by the first scan.

    Pre-fix: the first scan registered the adapter into the global
    ``REGISTRY``. The second scan skipped discovery but the
    dispatcher still resolved the adapter from the polluted
    global. We assert both behaviors are now correct:

    - second-scan ``loaded_adapters == []`` (already worked pre-fix)
    - second-scan adapter ``invocations`` count does NOT increase
      (the actual regression — pre-fix this WAS incremented)
    """

    _RecordingAdapter.reset()
    _patch_adapter_entries(monkeypatch, [_entry_point(lambda: _RecordingAdapter)])

    # Scan 1: plugins enabled → adapter discovered, registered (per-scan),
    # and invoked once via pass-2 per_scan loop.
    report1, _ = run_scan(
        config_path=CLEAN_FIXTURE,
        output_dir=tmp_path / "scan1",
        formats=["json"],
        ci_mode="advisory",
    )
    assert len(report1.loaded_adapters) == 1
    assert report1.loaded_adapters[0]["validation_status"] == VALID
    assert _RecordingAdapter.invocations == 1

    # Scan 2: same process, plugins disabled. Discovery is a no-op
    # AND the dispatcher must not see the adapter from scan 1.
    report2, _ = run_scan(
        config_path=CLEAN_FIXTURE,
        output_dir=tmp_path / "scan2",
        formats=["json"],
        ci_mode="advisory",
        plugins_enabled=False,
    )
    assert report2.loaded_adapters == [], (
        "scan 2 with plugins_enabled=False must report an empty "
        "loaded_adapters[]"
    )
    assert _RecordingAdapter.invocations == 1, (
        "scan 2 must NOT execute the third-party adapter registered "
        "by scan 1; global REGISTRY pollution would have re-run it"
    )
    assert "recording_demo" not in REGISTRY._adapters


def test_second_scan_does_not_misclassify_third_party_as_collision(
    monkeypatch, tmp_path
):
    """**Regression for PR #111 P1 #2.** A stable third-party adapter
    discovered across two consecutive scans must produce
    ``validation_status == "valid"`` on BOTH scans, not collide with
    its own registration from scan one.

    Pre-fix: scan 2's collision set was ``REGISTRY._adapters.keys()``
    which already contained the scan-1 adapter, so scan 2 reported
    ``source_type_collision`` while the dispatcher still executed the
    stale registered adapter. Both report truthfulness and
    ``--strict-plugins`` were broken.
    """

    _RecordingAdapter.reset()
    _patch_adapter_entries(monkeypatch, [_entry_point(lambda: _RecordingAdapter)])

    report1, _ = run_scan(
        config_path=CLEAN_FIXTURE,
        output_dir=tmp_path / "scan1",
        formats=["json"],
        ci_mode="advisory",
    )
    report2, _ = run_scan(
        config_path=CLEAN_FIXTURE,
        output_dir=tmp_path / "scan2",
        formats=["json"],
        ci_mode="advisory",
    )

    assert report1.loaded_adapters[0]["validation_status"] == VALID
    assert report2.loaded_adapters[0]["validation_status"] == VALID, (
        "second scan must classify the same adapter as valid, not "
        "as source_type_collision against itself"
    )
    assert _RecordingAdapter.invocations == 2, (
        "adapter should have run exactly once per scan"
    )


class _PerSourceThirdPartyAdapter:
    """A per_source third-party adapter — proves manifests can
    reference custom source types after PR #111 P1 #3.
    """

    source_type: ClassVar[str] = "demo_source"
    scope: ClassVar[Literal["per_source", "per_scan"]] = "per_source"
    artifact_class: ClassVar[type | None] = None

    invocations: ClassVar[int] = 0

    @classmethod
    def reset(cls) -> None:
        cls.invocations = 0

    def load(
        self,
        source: ToolSourceConfig | None,
        base_dir: Path,
        manifest: AgentsShipgateManifest,
    ) -> LoadedAdapterResult:
        type(self).invocations += 1
        # Minimal valid LoadedAdapterResult; the per_source contract
        # returns at least one LoadedToolSource so the dispatcher can
        # absorb it without error.
        from agents_shipgate.core.domain import LoadedToolSource

        return LoadedAdapterResult(
            tool_sources=[
                LoadedToolSource(
                    source_id=source.id if source else "demo",
                    source_type="demo_source",
                    tools=[],
                    warnings=[],
                )
            ],
        )


def test_per_source_third_party_adapter_referenced_from_manifest(
    monkeypatch, tmp_path
):
    """**Regression for PR #111 P1 #3.** A manifest referencing a
    third-party per_source adapter via ``tool_sources[].type`` must
    load successfully. Pre-fix ``ToolSourceConfig.type`` was a closed
    ``Literal`` of built-in source types, so manifest validation
    rejected the custom type before discovery ran.
    """

    _PerSourceThirdPartyAdapter.reset()

    # Build a minimal manifest referencing the third-party source type.
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "tools.json").write_text('{"tools": []}', encoding="utf-8")
    manifest_path = workspace / "shipgate.yaml"
    manifest_path.write_text(
        """version: "0.1"
project:
  name: third-party-demo
agent:
  name: demo
  declared_purpose: ["test third-party per_source adapter"]
environment:
  target: local
tool_sources:
  - id: demo
    type: demo_source
    path: tools.json
""",
        encoding="utf-8",
    )

    _patch_adapter_entries(
        monkeypatch, [_entry_point(lambda: _PerSourceThirdPartyAdapter)]
    )

    report, exit_code = run_scan(
        config_path=manifest_path,
        output_dir=tmp_path / "out",
        formats=["json"],
        ci_mode="advisory",
    )

    assert exit_code == 0, (
        f"scan should succeed with a third-party per_source adapter; "
        f"got exit={exit_code}"
    )
    assert _PerSourceThirdPartyAdapter.invocations == 1
    assert len(report.loaded_adapters) == 1
    assert report.loaded_adapters[0]["validation_status"] == VALID
    assert report.loaded_adapters[0]["source_type"] == "demo_source"


# --- review-fix: tighter signature validation (PR #111 P2 #4) -------------


def test_gate_bad_protocol_load_too_few_positional(monkeypatch, tmp_path):
    """**Regression for PR #111 P2 #4.** A ``load`` method that
    accepts fewer than 3 positional arguments must fail at the gate,
    not crash the dispatcher at runtime.
    """

    class _OnlyOnePositional:
        source_type: ClassVar[str] = "too_few_args"
        scope: ClassVar[Literal["per_source", "per_scan"]] = "per_scan"
        artifact_class: ClassVar[type | None] = None

        def load(self, source):  # type: ignore[override]
            return LoadedAdapterResult()

    _patch_adapter_entries(monkeypatch, [_entry_point(lambda: _OnlyOnePositional)])

    report, _ = run_scan(
        config_path=CLEAN_FIXTURE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    record = report.loaded_adapters[0]
    assert record["validation_status"] == BAD_PROTOCOL
    assert any(
        "at least 3 positional" in err for err in record["validation_errors"]
    ), record["validation_errors"]


def test_gate_bad_protocol_load_required_keyword_only(monkeypatch, tmp_path):
    """**Regression for PR #111 P2 #4.** Required keyword-only
    parameters must fail at the gate. The dispatcher calls
    ``load(source, base_dir, manifest)`` with no kwargs.
    """

    class _RequiredKwOnly:
        source_type: ClassVar[str] = "kw_only_required"
        scope: ClassVar[Literal["per_source", "per_scan"]] = "per_scan"
        artifact_class: ClassVar[type | None] = None

        def load(self, source, base_dir, manifest, *, must_set):  # type: ignore[override]
            return LoadedAdapterResult()

    _patch_adapter_entries(monkeypatch, [_entry_point(lambda: _RequiredKwOnly)])

    report, _ = run_scan(
        config_path=CLEAN_FIXTURE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    record = report.loaded_adapters[0]
    assert record["validation_status"] == BAD_PROTOCOL
    assert any(
        "keyword-only" in err and "must_set" in err
        for err in record["validation_errors"]
    ), record["validation_errors"]


def test_gate_bad_protocol_accepts_var_positional(monkeypatch, tmp_path):
    """An adapter using ``*args`` to absorb the three positional
    arguments is valid (it CAN bind 3 args, even though no required
    positional slots are declared).
    """

    class _VarPositional:
        source_type: ClassVar[str] = "var_args_demo"
        scope: ClassVar[Literal["per_source", "per_scan"]] = "per_scan"
        artifact_class: ClassVar[type | None] = None

        def load(self, *args):  # type: ignore[override]
            return LoadedAdapterResult()

    _patch_adapter_entries(monkeypatch, [_entry_point(lambda: _VarPositional)])

    report, _ = run_scan(
        config_path=CLEAN_FIXTURE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    record = report.loaded_adapters[0]
    assert record["validation_status"] == VALID, record["validation_errors"]


def test_gate_bad_protocol_accepts_optional_keyword_only(monkeypatch, tmp_path):
    """Optional keyword-only parameters (with defaults) don't break
    the dispatcher's call shape; the gate accepts them.
    """

    class _OptionalKwOnly:
        source_type: ClassVar[str] = "optional_kw_demo"
        scope: ClassVar[Literal["per_source", "per_scan"]] = "per_scan"
        artifact_class: ClassVar[type | None] = None

        def load(self, source, base_dir, manifest, *, opt=None):  # type: ignore[override]
            return LoadedAdapterResult()

    _patch_adapter_entries(monkeypatch, [_entry_point(lambda: _OptionalKwOnly)])

    report, _ = run_scan(
        config_path=CLEAN_FIXTURE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    record = report.loaded_adapters[0]
    assert record["validation_status"] == VALID, record["validation_errors"]


# --- isolated unit tests on the validator (no scan needed) ----------------


def test_validate_entry_point_rejects_uninstantiable_class():
    """A class that can't be instantiated with zero args fails the
    bad_protocol gate at instantiation time (before checking attrs).
    """

    class _Picky:
        def __init__(self, required_arg):
            pass

    record = validate_adapter_entry_point(
        _entry_point(lambda: _Picky),
        builtin_source_types=set(),
        already_registered_source_types=set(),
    )
    assert record.info["validation_status"] == BAD_PROTOCOL
    assert any(
        "could not be instantiated" in err
        for err in record.info["validation_errors"]
    )


def test_discover_writes_to_loaded_adapters_list(monkeypatch):
    """The ``loaded_adapters`` out-parameter list is appended to in
    discovery order, matching the entry-points iteration order.
    """

    monkeypatch.setenv("AGENTS_SHIPGATE_ENABLE_PLUGINS", "1")
    monkeypatch.setattr(
        protocol_module,
        "entry_points",
        lambda *, group: (
            [
                _entry_point(lambda: _ThirdPartyAdapter, name="alpha", value="pkg:A"),
            ]
            if group == protocol_module.ADAPTER_ENTRY_POINT_GROUP
            else []
        ),
    )

    registry = AdapterRegistry(autopopulate=False)
    loaded: list[dict[str, Any]] = []
    records = discover_third_party_adapters(
        registry,
        plugins_enabled=True,
        loaded_adapters=loaded,
    )
    assert len(records) == 1
    assert loaded[0]["name"] == "alpha"
    assert loaded[0]["source_type"] == "third_party_demo"
    assert loaded[0]["validation_status"] == VALID


# --- strict_adapter_failure_messages + --strict-plugins -------------------


def test_strict_failure_messages_collects_validation_errors():
    rows = [
        {
            "name": "n",
            "value": "v",
            "validation_status": LOAD_FAILED,
            "validation_errors": ["entry_point.load() raised: ImportError(...)"],
            "runtime_errors": [],
        },
        {
            "name": "good",
            "value": "good:pkg",
            "validation_status": VALID,
            "validation_errors": [],
            "runtime_errors": [],
        },
    ]
    messages = strict_adapter_failure_messages(rows)
    assert len(messages) == 1
    assert "adapter 'v'" in messages[0]
    assert "ImportError" in messages[0]


def test_strict_plugins_exits_nonzero_on_adapter_failure(monkeypatch, tmp_path):
    """v0.20: ``--strict-plugins`` extends to adapter validation
    failures via ``strict_adapter_failure_messages``. A failing
    third-party adapter elevates the exit code to 4 even when the
    scan itself would have exited 0.
    """

    # Drive through the public CLI to exercise the _apply_strict_plugins
    # path; only that path actually surfaces adapter messages.
    from typer.testing import CliRunner

    from agents_shipgate.cli.main import app

    def boom():
        raise ImportError("synthetic broken third-party adapter")

    monkeypatch.setenv("AGENTS_SHIPGATE_ENABLE_PLUGINS", "1")
    monkeypatch.setattr(
        protocol_module,
        "entry_points",
        lambda *, group: (
            [_entry_point(boom)]
            if group == protocol_module.ADAPTER_ENTRY_POINT_GROUP
            else []
        ),
    )

    # Newer Typer CliRunner doesn't accept ``mix_stderr``; output is the
    # combined stdout+stderr stream when the underlying click runner
    # merges them. That's fine for substring assertion.
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "scan",
            "--config",
            str(CLEAN_FIXTURE),
            "--out",
            str(tmp_path),
            "--ci-mode",
            "advisory",
            "--strict-plugins",
        ],
    )
    assert result.exit_code == 4, (
        f"expected --strict-plugins exit 4, got {result.exit_code}; "
        f"output={result.output!r}"
    )
    assert "adapter" in result.output
    assert "synthetic broken third-party adapter" in result.output


# --- run_validated_adapter (runtime safety net) ---------------------------


def test_run_validated_adapter_captures_exceptions():
    class _Crashy:
        source_type: ClassVar[str] = "crashy"
        scope: ClassVar[Literal["per_source", "per_scan"]] = "per_scan"
        artifact_class: ClassVar[type | None] = None

        def load(self, source, base_dir, manifest):
            raise RuntimeError("simulated crash inside adapter.load()")

    loaded = LoadedAdapter(
        adapter=_Crashy(),
        info={
            "name": "crashy",
            "value": "pkg:Crashy",
            "validation_status": VALID,
            "validation_errors": [],
            "runtime_errors": [],
            "source_type": "crashy",
        },
    )
    result = run_validated_adapter(
        loaded, source=None, base_dir=Path("."), manifest=None
    )
    assert result is None
    assert any(
        "simulated crash inside adapter.load()" in err
        for err in loaded.runtime_errors
    )
    assert loaded.info["runtime_errors"] == loaded.runtime_errors


def test_run_validated_adapter_rejects_wrong_return_type():
    class _BadReturn:
        source_type: ClassVar[str] = "badreturn"
        scope: ClassVar[Literal["per_source", "per_scan"]] = "per_scan"
        artifact_class: ClassVar[type | None] = None

        def load(self, source, base_dir, manifest):
            return "not a LoadedAdapterResult"

    loaded = LoadedAdapter(
        adapter=_BadReturn(),
        info={
            "name": "badreturn",
            "value": "pkg:BadReturn",
            "validation_status": VALID,
            "validation_errors": [],
            "runtime_errors": [],
            "source_type": "badreturn",
        },
    )
    result = run_validated_adapter(
        loaded, source=None, base_dir=Path("."), manifest=None
    )
    assert result is None
    assert any(
        "LoadedAdapterResult" in err for err in loaded.runtime_errors
    )


def test_run_validated_adapter_rejects_smuggled_artifact():
    """Load-bearing rule: an adapter declaring ``artifact_class=X``
    must not return an artifact of any other type. This is the
    artifact-smuggling-prevention rule that mirrors the
    Finding.check_id smuggling rule in plugin_validation.
    """

    class _ExpectedArtifact:
        pass

    class _OtherArtifact:
        pass

    class _Smuggler:
        source_type: ClassVar[str] = "smuggler"
        scope: ClassVar[Literal["per_source", "per_scan"]] = "per_scan"
        artifact_class: ClassVar[type | None] = _ExpectedArtifact

        def load(self, source, base_dir, manifest):
            return LoadedAdapterResult(artifact=_OtherArtifact())

    loaded = LoadedAdapter(
        adapter=_Smuggler(),
        info={
            "name": "smuggler",
            "value": "pkg:Smuggler",
            "validation_status": VALID,
            "validation_errors": [],
            "runtime_errors": [],
            "source_type": "smuggler",
        },
    )
    result = run_validated_adapter(
        loaded, source=None, base_dir=Path("."), manifest=None
    )
    assert result is None
    assert any(
        "_OtherArtifact" in err and "_ExpectedArtifact" in err
        for err in loaded.runtime_errors
    )


# --- PR #111 follow-up review (round 3) ----------------------------------


class _RuntimeCrashingAdapter:
    """A third-party per_scan adapter that raises at runtime.

    Pre-fix: ``adapter.load()`` was called directly in the dispatcher,
    so the exception propagated, ``run_scan`` aborted with the raw
    ``RuntimeError``, and ``--strict-plugins`` never got a chance to
    inspect ``loaded_adapters[].runtime_errors``.
    """

    source_type: ClassVar[str] = "runtime_crash_demo"
    scope: ClassVar[Literal["per_source", "per_scan"]] = "per_scan"
    artifact_class: ClassVar[type | None] = None

    def load(
        self,
        source: ToolSourceConfig | None,
        base_dir: Path,
        manifest: AgentsShipgateManifest,
    ) -> LoadedAdapterResult:
        raise RuntimeError("simulated runtime crash inside adapter.load()")


def test_runtime_error_in_third_party_adapter_captured_not_propagated(
    monkeypatch, tmp_path
):
    """**Regression for PR #111 review follow-up P1 #1.** A
    third-party adapter that raises at runtime must NOT abort
    ``run_scan``. The dispatcher routes its ``load()`` through
    ``run_validated_adapter``, which captures the exception into
    ``loaded_adapters[].runtime_errors``. The scan completes; the
    report is emitted; ``--strict-plugins`` (when set) exits 4.
    """

    _patch_adapter_entries(
        monkeypatch, [_entry_point(lambda: _RuntimeCrashingAdapter)]
    )

    report, exit_code = run_scan(
        config_path=CLEAN_FIXTURE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )

    # Scan must succeed; report must be emitted.
    assert exit_code == 0, (
        f"adapter runtime crash must not abort the scan in lenient "
        f"mode; got exit_code={exit_code}"
    )
    assert len(report.loaded_adapters) == 1
    record = report.loaded_adapters[0]
    # Adapter passed all four load-time gates.
    assert record["validation_status"] == VALID
    # Runtime error captured, not propagated.
    assert any(
        "simulated runtime crash inside adapter.load()" in err
        for err in record["runtime_errors"]
    ), f"runtime_errors not captured: {record['runtime_errors']!r}"


def test_strict_plugins_exits_on_third_party_adapter_runtime_error(
    monkeypatch, tmp_path
):
    """End-to-end: ``--strict-plugins`` elevates exit code 4 when a
    third-party adapter raises at runtime — not just when it fails
    validation. Closes the loop on the previously-bypassed contract.
    """

    from typer.testing import CliRunner

    from agents_shipgate.cli.main import app

    monkeypatch.setenv("AGENTS_SHIPGATE_ENABLE_PLUGINS", "1")
    monkeypatch.setattr(
        protocol_module,
        "entry_points",
        lambda *, group: (
            [_entry_point(lambda: _RuntimeCrashingAdapter)]
            if group == protocol_module.ADAPTER_ENTRY_POINT_GROUP
            else []
        ),
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "scan",
            "--config",
            str(CLEAN_FIXTURE),
            "--out",
            str(tmp_path),
            "--ci-mode",
            "advisory",
            "--strict-plugins",
        ],
    )
    assert result.exit_code == 4, (
        f"expected --strict-plugins exit 4 on adapter runtime error, "
        f"got {result.exit_code}; output={result.output!r}"
    )
    assert "adapter" in result.output
    assert "simulated runtime crash inside adapter.load()" in result.output


def test_doctor_resolves_third_party_source_types(monkeypatch, tmp_path):
    """**Regression for PR #111 review follow-up P1 #2.** ``doctor``
    (``inspect_sources``) must run third-party adapter discovery so a
    manifest referencing ``tool_sources[].type: demo_source`` can be
    introspected, not crash with ``ConfigError: No adapter
    registered``.
    """

    from agents_shipgate.cli.scan import inspect_sources
    from agents_shipgate.core.domain import LoadedToolSource as _LTS

    _patch_adapter_entries(
        monkeypatch, [_entry_point(lambda: _PerSourceThirdPartyAdapter)]
    )
    _PerSourceThirdPartyAdapter.reset()

    workspace = tmp_path / "doctor_workspace"
    workspace.mkdir()
    (workspace / "tools.json").write_text('{"tools": []}', encoding="utf-8")
    manifest_path = workspace / "shipgate.yaml"
    manifest_path.write_text(
        """version: "0.1"
project:
  name: doctor-third-party-demo
agent:
  name: demo
  declared_purpose: ["doctor inspects third-party per_source adapter"]
environment:
  target: local
tool_sources:
  - id: demo
    type: demo_source
    path: tools.json
""",
        encoding="utf-8",
    )

    payload = inspect_sources(config_path=manifest_path)

    # Doctor returns a payload — no ConfigError raised.
    assert payload["project"] == "doctor-third-party-demo"
    # The third-party adapter was invoked and the source ID surfaces
    # in the sources list.
    sources = payload["sources"]
    assert any(s["id"] == "demo" for s in sources), (
        f"doctor's sources list missing the third-party demo source: "
        f"{sources!r}"
    )
    # Adapter discovery results are surfaced in the payload so an
    # operator can see what was loaded without running a full scan.
    loaded = payload["loaded_adapters"]
    assert len(loaded) == 1
    assert loaded[0]["source_type"] == "demo_source"
    assert loaded[0]["validation_status"] == VALID
    # And the adapter actually ran (per-source load is called from
    # _load_sources pass 1).
    assert _PerSourceThirdPartyAdapter.invocations == 1
    # Pin the no-warnings invariant — discovery must NOT have emitted
    # warnings for a clean third-party load. ``_LTS`` import is kept
    # solely to avoid an "unused import" lint failure under
    # ``from __future__ import annotations``.
    assert _LTS is not None
    assert payload["warnings"] == [], (
        f"unexpected warnings for clean third-party load: "
        f"{payload['warnings']!r}"
    )


def test_markdown_report_renders_loaded_adapters_section(
    monkeypatch, tmp_path
):
    """**Regression for PR #111 review follow-up P2 #3.** The
    Markdown report (``report.md``) must include a ``Loaded
    Adapters`` section listing each third-party adapter and its
    validation status. A ``load_failed`` adapter is shown alongside
    its validation_errors so reviewers don't have to open
    ``report.json`` to see it.
    """

    def boom():
        raise ImportError("simulated broken adapter for markdown test")

    _patch_adapter_entries(monkeypatch, [_entry_point(boom)])

    report, _ = run_scan(
        config_path=CLEAN_FIXTURE,
        output_dir=tmp_path,
        formats=["json", "markdown"],
        ci_mode="advisory",
    )

    # The JSON contains the load_failed row (already proven by an
    # earlier test); now confirm the markdown does too. Note: the
    # ``_safe_markdown_text`` helper escapes underscores so
    # ``load_failed`` renders as ``load\_failed`` in the raw bytes
    # (and as ``load_failed`` in any markdown viewer). The assertions
    # below accept both forms so a future change to the escaping
    # rules doesn't make the test brittle.
    md_path = tmp_path / "report.md"
    assert md_path.exists(), "report.md was not emitted"
    md = md_path.read_text(encoding="utf-8")
    assert "## Loaded Adapters" in md, (
        "Markdown report must include a Loaded Adapters section; "
        "got:\n" + md[:2000]
    )
    assert "load_failed" in md or "load\\_failed" in md, (
        "Markdown report must show validation_status of failed "
        f"adapters; got:\n{md[:2000]}"
    )
    assert "simulated broken adapter for markdown test" in md, (
        "Markdown report must show the validation_error so the "
        "reviewer can act on it without opening report.json"
    )
