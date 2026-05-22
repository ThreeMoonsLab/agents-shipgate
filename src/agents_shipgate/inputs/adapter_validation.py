"""Adapter validation gates for the ``agents_shipgate.adapters`` entry-point group.

v0.20: third-party adapter discovery follow-up to v0.18's M5 plugin
validation. Plugins (check functions) and adapters (input loaders) are
the two extension surfaces; this module gives adapters the same
structural trust enforcement plugins already have.

Four load-time gates, each producing a single ``ValidationFailure`` row
that lands in ``loaded_adapters[].validation_errors``:

1. **load** — ``entry_point.load()`` raises (broken module import,
   syntax error, missing dependency). Exception is captured; the
   adapter is recorded with ``validation_status="load_failed"`` and
   skipped.
2. **bad_protocol** — the loaded value cannot produce a
   ``ToolSourceAdapter`` instance. Either it's not callable (cannot
   instantiate) or the resulting instance lacks the required ClassVar
   attributes (``source_type``, ``scope``, ``artifact_class``) or the
   ``load`` method.
3. **bad_scope** — ``scope`` is not one of ``"per_source"`` /
   ``"per_scan"``. The dispatcher's pass-1/pass-2 design depends on
   this enum; an out-of-range value would silently skip the adapter.
4. **source_type_collision** — the adapter's ``source_type`` shadows
   a built-in (``mcp`` / ``openapi`` / ``langchain`` / etc.) or one
   already registered by an earlier third-party adapter in the same
   scan. This is the load-bearing trust rule — without it, a malicious
   plugin could displace a built-in adapter and intercept all scans
   targeting that source type.

Runtime validation (``run_validated_adapter``) is a separate concern
from the load-time gates. It wraps the actual ``adapter.load()`` call
and ensures:

* Exceptions during the call are captured (the scan continues), but
  the adapter's record gains a ``runtime_errors`` entry.
* The returned object must be a ``LoadedAdapterResult``; otherwise we
  drop the result.
* When the adapter declares a non-None ``artifact_class``, the
  returned ``artifact`` (if non-None) must be an instance of that
  class. This is the load-bearing artifact-smuggling prevention rule.

An adapter landing here in ``valid`` state with empty
``runtime_errors`` behaves exactly like a built-in adapter. Everything
else surfaces in ``report.loaded_adapters[]`` so the operator can see
what was skipped and why without reading scanner logs.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any

# Stable enum surfaced to consumers via
# ``loaded_adapters[].validation_status``. Ordering is informational;
# consumers should compare to literal strings.
ValidationStatus = str
VALID = "valid"
LOAD_FAILED = "load_failed"
BAD_PROTOCOL = "bad_protocol"
BAD_SCOPE = "bad_scope"
SOURCE_TYPE_COLLISION = "source_type_collision"

_VALID_SCOPES: frozenset[str] = frozenset({"per_source", "per_scan"})


@dataclass
class LoadedAdapter:
    """One row in the adapter-load result table.

    ``adapter`` is ``None`` when validation failed; the discovery
    function skips invalid adapters but still surfaces them under
    ``loaded_adapters[]`` (parallel to ``loaded_plugins[]``).

    ``info`` is the dict shape persisted into the JSON report (see
    ``ReadinessReport.loaded_adapters``). The dict is intentionally
    mutable so the runtime wrapper can append ``runtime_errors`` after
    the adapter has been invoked.
    """

    adapter: Any | None  # ToolSourceAdapter; typed Any here to avoid the import cycle
    info: dict[str, Any]
    runtime_errors: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return self.info.get("validation_status") == VALID


def validate_adapter_entry_point(
    entry_point: Any,
    *,
    builtin_source_types: set[str],
    already_registered_source_types: set[str],
) -> LoadedAdapter:
    """Run the four load-time gates against a single entry point.

    Returns a ``LoadedAdapter`` whose ``info`` dict carries the
    decision (``validation_status``, ``validation_errors``, plus the
    ``name`` / ``value`` / ``distribution`` / ``version`` /
    ``source_type`` fields for ``report.loaded_adapters[]``).
    """

    info = _base_info(entry_point)

    # Gate 1: load
    try:
        loaded = entry_point.load()
    except Exception as exc:  # noqa: BLE001 — capture is the point
        return _fail(info, LOAD_FAILED, f"entry_point.load() raised: {exc!r}")

    # Gate 2: bad_protocol — instantiate (if class) and check Protocol shape.
    try:
        adapter = loaded() if isinstance(loaded, type) else loaded
    except Exception as exc:  # noqa: BLE001 — capture is the point
        return _fail(
            info,
            BAD_PROTOCOL,
            f"adapter class could not be instantiated with no args: {exc!r}",
        )

    proto_error = _protocol_error(adapter)
    if proto_error is not None:
        return _fail(info, BAD_PROTOCOL, proto_error)

    source_type = adapter.source_type
    scope = adapter.scope
    info["source_type"] = source_type

    # Gate 3: bad_scope — must be exactly one of the two literals.
    if scope not in _VALID_SCOPES:
        return _fail(
            info,
            BAD_SCOPE,
            f"adapter scope must be 'per_source' or 'per_scan'; got {scope!r}",
        )

    # Gate 4: source_type_collision — load-bearing trust rule.
    if source_type in builtin_source_types:
        return _fail(
            info,
            SOURCE_TYPE_COLLISION,
            f"source_type {source_type!r} is reserved by a built-in adapter",
        )
    if source_type in already_registered_source_types:
        return _fail(
            info,
            SOURCE_TYPE_COLLISION,
            f"source_type {source_type!r} is already registered by another "
            "third-party adapter",
        )

    # All gates pass.
    info["validation_status"] = VALID
    info["validation_errors"] = []
    return LoadedAdapter(adapter=adapter, info=info)


def run_validated_adapter(
    loaded: LoadedAdapter,
    *,
    source: Any,
    base_dir: Any,
    manifest: Any,
) -> Any | None:
    """Invoke a validated adapter's ``load()`` with runtime safety nets.

    Returns the ``LoadedAdapterResult`` if the call succeeds AND the
    returned artifact (if any) is an instance of the adapter's declared
    ``artifact_class``. Returns ``None`` on any runtime failure;
    failures are captured in ``loaded.runtime_errors`` (and mirrored
    into ``loaded.info['runtime_errors']``).

    Callers should treat ``None`` as "adapter produced no usable
    result" and skip it. This mirrors ``run_validated_plugin`` in
    ``checks/plugin_validation.py``.
    """

    if loaded.adapter is None:  # defensive
        raise RuntimeError("run_validated_adapter called on an invalid adapter record")

    runtime_errors: list[str] = []

    try:
        result = loaded.adapter.load(source, base_dir, manifest)
    except Exception as exc:  # noqa: BLE001 — capture is the point
        runtime_errors.append(f"adapter.load() raised: {exc!r}")
        _set_runtime_errors(loaded, runtime_errors)
        return None

    # Lazy import to avoid the inputs.protocol ↔ inputs.adapter_validation
    # cycle. ``LoadedAdapterResult`` is defined in inputs/protocol.py.
    from agents_shipgate.inputs.protocol import LoadedAdapterResult

    if not isinstance(result, LoadedAdapterResult):
        runtime_errors.append(
            "adapter.load() must return LoadedAdapterResult; got "
            f"{type(result).__name__!r}"
        )
        _set_runtime_errors(loaded, runtime_errors)
        return None

    # Smuggling prevention: when the adapter declares a non-None
    # artifact_class, the returned artifact must be an instance of it.
    # The dispatcher (cli/scan.py:_absorb) also enforces this for
    # built-ins; recording it as a runtime error here gives external
    # consumers (loaded_adapters[].runtime_errors) visibility before
    # the dispatcher's TypeError fires.
    declared_cls = getattr(loaded.adapter, "artifact_class", None)
    if (
        declared_cls is not None
        and result.artifact is not None
        and not isinstance(result.artifact, declared_cls)
    ):
        runtime_errors.append(
            f"adapter declared artifact_class={declared_cls.__name__} but "
            f"returned {type(result.artifact).__name__}; dropping artifact"
        )
        _set_runtime_errors(loaded, runtime_errors)
        return None

    _set_runtime_errors(loaded, runtime_errors)
    return result


def strict_adapter_failure_messages(
    loaded_adapters: list[dict[str, Any]],
) -> list[str]:
    """Collect human-readable reasons strict-mode should fail on.

    Returns an empty list if every adapter is ``valid`` with empty
    ``runtime_errors``. ``--strict-plugins`` callers should turn a
    non-empty list into a non-zero exit (alongside
    ``strict_failure_messages`` from ``plugin_validation``).
    """

    messages: list[str] = []
    for record in loaded_adapters:
        status = record.get("validation_status")
        if status and status != VALID:
            errors = record.get("validation_errors") or [status]
            for err in errors:
                messages.append(
                    f"adapter {record.get('value') or record.get('name')!r}: {err}"
                )
        runtime_errors = record.get("runtime_errors") or []
        for err in runtime_errors:
            messages.append(
                f"adapter {record.get('value') or record.get('name')!r}: {err}"
            )
    return messages


# --- internals -------------------------------------------------------------


_REQUIRED_PROTOCOL_ATTRS = ("source_type", "scope", "artifact_class")


def _base_info(entry_point: Any) -> dict[str, Any]:
    dist = getattr(entry_point, "dist", None)
    return {
        "name": _maybe_str(getattr(entry_point, "name", None)),
        "value": _maybe_str(getattr(entry_point, "value", None)),
        "distribution": _distribution_name(dist),
        "version": _distribution_version(dist),
        "source_type": None,
        "validation_status": VALID,
        "validation_errors": [],
        "runtime_errors": [],
    }


def _fail(info: dict[str, Any], status: ValidationStatus, message: str) -> LoadedAdapter:
    info["validation_status"] = status
    info["validation_errors"] = [message]
    return LoadedAdapter(adapter=None, info=info)


def _protocol_error(adapter: Any) -> str | None:
    """Return a human-readable error if ``adapter`` does not satisfy
    the ``ToolSourceAdapter`` Protocol; ``None`` if it does.

    The Protocol requires three ClassVar attributes and a ``load``
    method. ``isinstance`` against ``ToolSourceAdapter`` (which is
    ``@runtime_checkable``) would catch most of this, but it can't
    distinguish between a missing attribute and a wrongly-typed one,
    and the error message it produces is opaque. We do an explicit
    check here.
    """

    for attr in _REQUIRED_PROTOCOL_ATTRS:
        if not hasattr(adapter, attr):
            return (
                f"adapter is missing required ClassVar {attr!r} "
                "(ToolSourceAdapter Protocol)"
            )

    source_type = getattr(adapter, "source_type", None)
    if not isinstance(source_type, str) or not source_type:
        return (
            "adapter source_type must be a non-empty string; got "
            f"{source_type!r}"
        )

    load_method = getattr(adapter, "load", None)
    if not callable(load_method):
        return "adapter.load must be a callable method"

    try:
        sig = inspect.signature(load_method)
    except (TypeError, ValueError):
        # Builtins / C extensions; accept — runtime wrapper will catch.
        return None
    # ToolSourceAdapter.load takes (source, base_dir, manifest). The
    # bound method drops ``self``, leaving 3 positional parameters.
    positional = [
        p
        for p in sig.parameters.values()
        if p.kind
        in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.VAR_POSITIONAL,
        )
    ]
    required = [
        p
        for p in positional
        if p.kind != inspect.Parameter.VAR_POSITIONAL
        and p.default is inspect.Parameter.empty
    ]
    # The wrapper calls adapter.load(source, base_dir, manifest) — three
    # positional arguments. Accept up to 3 required positional slots;
    # extra optional slots are fine.
    if len(required) > 3:
        return (
            "adapter.load must accept at most 3 required positional "
            f"parameters (source, base_dir, manifest); got {len(required)}"
        )
    if not positional:
        return (
            "adapter.load must accept positional arguments "
            "(source, base_dir, manifest)"
        )
    return None


def _set_runtime_errors(loaded: LoadedAdapter, errors: list[str]) -> None:
    loaded.runtime_errors = errors
    loaded.info["runtime_errors"] = errors


def _maybe_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _distribution_name(dist: Any) -> str | None:
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


def _distribution_version(dist: Any) -> str | None:
    if dist is None:
        return None
    version = getattr(dist, "version", None)
    return str(version) if version else None
