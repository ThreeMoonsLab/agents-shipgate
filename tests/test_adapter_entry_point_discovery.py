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
def _reset_registry_after_test(monkeypatch):
    """Snapshot REGISTRY._adapters before each test so any third-party
    additions made during the test are cleaned up afterwards.

    Without this fixture, a third-party adapter registered in one test
    would leak into subsequent tests and cause source_type_collision
    false-positives.
    """

    REGISTRY._ensure_populated()
    builtin_snapshot = dict(REGISTRY._adapters)
    yield
    REGISTRY._adapters.clear()
    REGISTRY._adapters.update(builtin_snapshot)


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
    # REGISTRY mutation visible from the test (cleaned by autouse fixture).
    assert "third_party_demo" in REGISTRY._adapters


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
