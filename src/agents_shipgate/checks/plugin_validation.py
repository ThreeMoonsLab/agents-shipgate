"""Plugin validation gates for the `agents_shipgate.checks` entry-point group.

Five validation gates, each producing a single ``ValidationFailure`` row
that lands in ``loaded_plugins[].validation_errors``:

1. **load** — ``entry_point.load()`` raises (broken module import, syntax
   error, missing dependency). Exception is captured; the plugin is
   recorded with ``validation_status="load_failed"`` and skipped.
2. **signature** — the loaded object is not callable, or its call signature
   does not accept exactly one positional parameter (``ScanContext``).
3. **metadata** — ``AGENTS_SHIPGATE_METADATA`` is missing, malformed, or
   fails ``CheckMetadata.model_validate``. Both ``id`` and ``check_id``
   keys are accepted via the ``CheckMetadata`` alias.
4. **id_collision** — the plugin's check ID shadows a built-in (or one of
   its legacy aliases), or is already registered by an earlier plugin
   in the same scan.
5. **bad_floor** — already enforced inside ``CheckMetadata`` via a model
   validator. Surfaced explicitly here so the failure shows up under a
   stable label rather than as a generic metadata error.

Runtime validation (``run_validated_plugin``) is a separate concern from
the load-time gates. It wraps the actual check call and ensures:

* Exceptions during the call are captured (the scan continues), but the
  plugin's record gains a ``runtime_errors`` entry.
* The returned object must be a ``list``; otherwise we drop the result.
* Each item must be a ``Finding``; non-Finding items are dropped with an
  error row.
* Each ``Finding.check_id`` must match the plugin's declared metadata
  ``id``. Mismatches are dropped — a plugin cannot smuggle findings under
  another check ID. This is the load-bearing rule for plugin trust.

A plugin landing here in ``valid`` state with empty ``runtime_errors``
behaves exactly like a v0.x plugin. Everything else surfaces in
``report.loaded_plugins[]`` so the operator can see what was skipped and
why without reading scanner logs.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.models import CheckMetadata, Finding

PluginCheck = Callable[[ScanContext], list[Finding]]

# Stable enum surfaced to consumers via ``loaded_plugins[].validation_status``.
# Ordering is informational; consumers should compare to literal strings.
ValidationStatus = str
VALID = "valid"
LOAD_FAILED = "load_failed"
BAD_SIGNATURE = "bad_signature"
BAD_METADATA = "bad_metadata"
ID_COLLISION = "id_collision"
BAD_FLOOR = "bad_floor"


@dataclass
class ValidatedPlugin:
    """One row in the plugin-load result table.

    ``check`` is ``None`` when validation failed; ``run_checks`` skips
    plugins with ``check is None`` but still surfaces them under
    ``loaded_plugins[]``.

    ``info`` is the dict shape persisted into the JSON report (see
    ``ReadinessReport.loaded_plugins``). The dict is intentionally
    mutable so the post-call runtime wrapper can append
    ``runtime_errors`` after the check has executed.
    """

    check: PluginCheck | None
    metadata: CheckMetadata | None
    info: dict[str, Any]
    runtime_errors: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return self.info.get("validation_status") == VALID


def validate_entry_point(
    entry_point: Any,
    *,
    builtin_ids: set[str],
    already_registered_plugin_ids: set[str],
) -> ValidatedPlugin:
    """Run the five load-time gates against a single entry point.

    Returns a ``ValidatedPlugin`` whose ``info`` dict carries the
    decision (``validation_status``, ``validation_errors``, plus the
    legacy ``name``/``value``/``distribution``/``version``/``check_id``
    fields a v0.x consumer expects).
    """

    info = _base_info(entry_point)

    # Gate 1: load
    try:
        loaded = entry_point.load()
    except Exception as exc:  # noqa: BLE001 — capture is the point
        return _fail(info, LOAD_FAILED, f"entry_point.load() raised: {exc!r}")

    # Gate 2: signature
    sig_error = _signature_error(loaded)
    if sig_error is not None:
        return _fail(info, BAD_SIGNATURE, sig_error)

    # Gate 3: metadata
    metadata_value = getattr(loaded, "AGENTS_SHIPGATE_METADATA", None)
    if metadata_value is None:
        return _fail(info, BAD_METADATA, "AGENTS_SHIPGATE_METADATA is missing")
    try:
        metadata = _coerce_metadata(metadata_value)
    except (ValidationError, TypeError, ValueError) as exc:
        # A floor-above-default failure raises ValidationError from
        # CheckMetadata's model validator; we surface it under a more
        # specific status below.
        if _is_floor_error(exc):
            return _fail(info, BAD_FLOOR, _format_validation_error(exc))
        return _fail(info, BAD_METADATA, _format_validation_error(exc))

    info["check_id"] = metadata.id

    # Gate 4: ID collision
    if metadata.id in builtin_ids:
        return _fail(
            info,
            ID_COLLISION,
            f"check_id {metadata.id!r} is reserved by a built-in check",
        )
    if metadata.id in already_registered_plugin_ids:
        return _fail(
            info,
            ID_COLLISION,
            f"check_id {metadata.id!r} is already registered by another plugin",
        )

    # All gates pass.
    info["validation_status"] = VALID
    info["validation_errors"] = []
    return ValidatedPlugin(check=loaded, metadata=metadata, info=info)


def run_validated_plugin(
    plugin: ValidatedPlugin, context: ScanContext
) -> list[Finding]:
    """Execute a validated plugin and return only the findings that pass
    runtime validation.

    Mutates ``plugin.info["runtime_errors"]`` in place. ``plugin.check``
    must not be ``None``; callers should skip plugins where
    ``plugin.valid`` is ``False``.
    """

    if plugin.check is None or plugin.metadata is None:  # defensive
        raise RuntimeError("run_validated_plugin called on an invalid plugin record")

    runtime_errors: list[str] = []

    try:
        raw = plugin.check(context)
    except Exception as exc:  # noqa: BLE001 — capture is the point
        runtime_errors.append(f"plugin call raised: {exc!r}")
        _set_runtime_errors(plugin, runtime_errors)
        return []

    if not isinstance(raw, list):
        runtime_errors.append(
            f"plugin must return list[Finding]; got {type(raw).__name__!r}"
        )
        _set_runtime_errors(plugin, runtime_errors)
        return []

    declared_id = plugin.metadata.id
    valid: list[Finding] = []
    for item in raw:
        if not isinstance(item, Finding):
            runtime_errors.append(
                f"non-Finding item dropped: {type(item).__name__!r}"
            )
            continue
        if item.check_id != declared_id:
            runtime_errors.append(
                f"finding check_id={item.check_id!r} does not match plugin "
                f"declared id={declared_id!r}; dropped"
            )
            continue
        valid.append(item)

    _set_runtime_errors(plugin, runtime_errors)
    return valid


def strict_failure_messages(loaded_plugins: list[dict[str, Any]]) -> list[str]:
    """Collect human-readable reasons strict-mode should fail on.

    Returns an empty list if every plugin is ``valid`` with empty
    ``runtime_errors``. ``--strict-plugins`` callers should turn a
    non-empty list into a non-zero exit.
    """

    messages: list[str] = []
    for record in loaded_plugins:
        status = record.get("validation_status")
        if status and status != VALID:
            errors = record.get("validation_errors") or [status]
            for err in errors:
                messages.append(
                    f"plugin {record.get('value') or record.get('name')!r}: {err}"
                )
        runtime_errors = record.get("runtime_errors") or []
        for err in runtime_errors:
            messages.append(
                f"plugin {record.get('value') or record.get('name')!r}: {err}"
            )
    return messages


# --- internals -------------------------------------------------------------


def _base_info(entry_point: Any) -> dict[str, Any]:
    dist = getattr(entry_point, "dist", None)
    return {
        "name": _maybe_str(getattr(entry_point, "name", None)),
        "value": _maybe_str(getattr(entry_point, "value", None)),
        "distribution": _distribution_name(dist),
        "version": _distribution_version(dist),
        "check_id": None,
        "validation_status": VALID,
        "validation_errors": [],
        "runtime_errors": [],
    }


def _fail(info: dict[str, Any], status: ValidationStatus, message: str) -> ValidatedPlugin:
    info["validation_status"] = status
    info["validation_errors"] = [message]
    return ValidatedPlugin(check=None, metadata=None, info=info)


def _signature_error(loaded: Any) -> str | None:
    """Decide if ``loaded`` has a callable signature compatible with
    ``Callable[[ScanContext], list[Finding]]``.

    Returns a human-readable error if the plugin would always fail to
    bind at call time. The validation contract is: a plugin whose
    ``validation_status`` is ``valid`` will be called as
    ``plugin(context)`` — no other arguments. Any required parameter
    that cannot be satisfied by that single positional argument must
    fail this gate, not slip through to ``runtime_errors`` later.
    """

    if not callable(loaded):
        return f"plugin object is not callable (got {type(loaded).__name__!r})"
    try:
        sig = inspect.signature(loaded)
    except (TypeError, ValueError):
        # Builtins / C extensions / lambdas with weird signatures; accept
        # them — the runtime wrapper will catch arity mismatches on call.
        return None
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
    # Need at least one positional slot for ``ScanContext``. Extra
    # positional parameters with defaults are allowed — common shape for
    # plugins that take optional config. Reject only zero-positional and
    # ≥2 required-positional cases.
    if not positional:
        return "plugin must accept a ScanContext positional parameter"
    required_positional = [
        p
        for p in positional
        if p.kind != inspect.Parameter.VAR_POSITIONAL
        and p.default is inspect.Parameter.empty
    ]
    if len(required_positional) > 1:
        return (
            "plugin must accept exactly one required positional parameter "
            f"(ScanContext); got {len(required_positional)}"
        )
    # Required keyword-only parameters cannot be filled by the
    # ``plugin(context)`` call shape — accepting these as ``valid``
    # would lie about runtime callability. Optional kw-only params
    # (with defaults) and ``**kwargs`` are fine.
    required_kw_only = [
        p
        for p in sig.parameters.values()
        if p.kind == inspect.Parameter.KEYWORD_ONLY
        and p.default is inspect.Parameter.empty
    ]
    if required_kw_only:
        names = ", ".join(p.name for p in required_kw_only)
        return (
            "plugin signature has required keyword-only parameter(s) "
            f"({names}); the validation contract calls plugins as "
            "plugin(context) with no keyword arguments"
        )
    return None


def _coerce_metadata(value: Any) -> CheckMetadata:
    if isinstance(value, CheckMetadata):
        # Re-validate to enforce the floor invariant on objects that may
        # have been constructed without going through model validation
        # (e.g., test helpers using ``__init__``).
        return CheckMetadata.model_validate(value.model_dump())
    if isinstance(value, dict):
        return CheckMetadata.model_validate(value)
    raise TypeError(
        f"AGENTS_SHIPGATE_METADATA must be CheckMetadata or dict; "
        f"got {type(value).__name__!r}"
    )


def _is_floor_error(exc: BaseException) -> bool:
    return "floor_severity" in str(exc)


def _format_validation_error(exc: BaseException) -> str:
    if isinstance(exc, ValidationError):
        first = exc.errors()[0]
        loc = ".".join(str(part) for part in first.get("loc", ())) or "<root>"
        return f"metadata invalid at {loc}: {first.get('msg', '')}"
    return f"metadata invalid: {exc}"


def _set_runtime_errors(plugin: ValidatedPlugin, errors: list[str]) -> None:
    plugin.runtime_errors = errors
    plugin.info["runtime_errors"] = errors


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
