from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Any

from agents_shipgate.checks import (
    adk,
    api,
    auth,
    codex_boundary,
    codex_plugin,
    crewai,
    documentation,
    evidence,
    host_boundary,
    inventory,
    langchain,
    manifest_consistency,
    manifest_scope,
    mcp_permissions,
    n8n,
    policy,
    schema,
    side_effects,
    verify,
    verify_agent_instructions,
    verify_baseline_waiver,
    verify_capability_scope,
    verify_ci_gate,
    verify_policy,
    verify_trigger_drift,
)
from agents_shipgate.checks._metadata_loader import load_check_metadata
from agents_shipgate.checks.plugin_validation import (
    ValidatedPlugin,
    run_validated_plugin,
    validate_entry_point,
)
from agents_shipgate.core.check_ids import (
    LEGACY_CHECK_ID_ALIASES,
    known_check_ids_with_legacy,
)
from agents_shipgate.core.context import ScanContext
from agents_shipgate.schemas.checks import CheckMetadata
from agents_shipgate.schemas.report import Finding

BUILTIN_CHECKS: list[Callable[[ScanContext], list[Finding]]] = [
    inventory.run,
    documentation.run,
    schema.run,
    auth.run,
    manifest_scope.run,
    policy.run,
    evidence.run,
    side_effects.run,
    api.run,
    adk.run,
    langchain.run,
    crewai.run,
    codex_boundary.run,
    mcp_permissions.run,
    codex_plugin.run,
    host_boundary.run,
    n8n.run,
    verify.run,
    # M3 (v0.22): Tier B trust-root weakening checks. All category
    # "verify" (suppression-immune + floor-protected) and gated on a
    # VerificationContext — they emit nothing on a plain scan.
    verify_policy.run,
    verify_baseline_waiver.run,
    verify_ci_gate.run,
    verify_agent_instructions.run,
    verify_trigger_drift.run,
    verify_capability_scope.run,
]


# Loaded at import time from per-category YAML under docs/checks/.
# To add or modify a check's metadata, edit docs/checks/<category>.yaml
# and regenerate docs/checks.json with
# `python scripts/generate_schemas.py`. The check callable itself still
# lives in checks/<category>.py and must be registered in BUILTIN_CHECKS
# above. See agents_shipgate.checks._metadata_loader for loader rules
# (filename → category, id → docs_url, strict duplicate detection).
CHECK_METADATA: list[CheckMetadata] = load_check_metadata()


# Back-compat alias. v0.x callers (third-party tooling that imported
# ``LoadedPluginCheck`` for typing) get the same shape — a frozen pair
# of ``check`` callable and ``info`` dict. ``ValidatedPlugin`` is the
# richer record used internally by the registry post-M5.
@dataclass(frozen=True)
class LoadedPluginCheck:
    check: Callable[[ScanContext], list[Finding]]
    info: dict[str, Any]


def run_checks(
    context: ScanContext,
    *,
    plugins_enabled: bool | None = None,
    loaded_plugins: list[dict[str, Any]] | None = None,
    extra_known_check_ids: set[str] | None = None,
) -> list[Finding]:
    findings: list[Finding] = []
    plugin_records = _plugin_check_records(plugins_enabled=plugins_enabled)
    # Order: surface plugin provenance into ``loaded_plugins`` first so
    # invalid plugins still appear in the report even when they don't
    # run. ``run_validated_plugin`` will mutate ``record.info`` in place
    # to append ``runtime_errors`` after the check fires.
    if loaded_plugins is not None:
        loaded_plugins.extend(record.info for record in plugin_records)
    for check in BUILTIN_CHECKS:
        findings.extend(check(context))
    for record in plugin_records:
        if not record.valid:
            continue
        findings.extend(run_validated_plugin(record, context))
    findings.extend(
        manifest_consistency.run(
            context,
            known_check_ids=known_check_ids_with_legacy(
                {
                    *(metadata.id for metadata in CHECK_METADATA),
                    *(
                        record.metadata.id
                        for record in plugin_records
                        if record.metadata is not None
                    ),
                    *(extra_known_check_ids or set()),
                }
            ),
        )
    )
    return findings


def check_catalog(*, plugins_enabled: bool | None = None) -> list[CheckMetadata]:
    metadata = [*CHECK_METADATA]
    for record in _plugin_check_records(plugins_enabled=plugins_enabled):
        if record.metadata is None:
            # Invalid plugins (failed load/signature/metadata gates) do
            # not appear in the catalog. Their provenance still shows up
            # in ``report.loaded_plugins`` when a scan runs.
            continue
        metadata.append(record.metadata)
    for check in metadata:
        if check.docs_url is None:
            check.docs_url = f"docs/checks.md#{check.id.lower()}"
    return sorted(metadata, key=lambda check: check.id)


def check_functions(
    *, plugins_enabled: bool | None = None
) -> list[Callable[[ScanContext], list[Finding]]]:
    return [
        *BUILTIN_CHECKS,
        *(
            record.check
            for record in _plugin_check_records(plugins_enabled=plugins_enabled)
            if record.valid and record.check is not None
        ),
    ]


def _plugin_checks(
    *, plugins_enabled: bool | None = None
) -> list[Callable[[ScanContext], list[Finding]]]:
    """Back-compat accessor for the bare list of valid plugin callables.

    Kept stable for ``tests/test_plugins.py`` and any external code that
    imported the helper. Invalid plugins are filtered out — matching the
    v0.x behavior of "callable plugins only".
    """

    return [
        record.check
        for record in _plugin_check_records(plugins_enabled=plugins_enabled)
        if record.valid and record.check is not None
    ]


def _plugin_check_records(
    *,
    plugins_enabled: bool | None = None,
) -> list[ValidatedPlugin]:
    """Discover and validate every third-party plugin entry point.

    Returns one ``ValidatedPlugin`` per non-builtin entry point — including
    those that failed validation. Invalid records carry ``check=None`` and
    appear in ``report.loaded_plugins`` so the operator can see what was
    skipped and why without reading scanner logs.
    """

    if not _plugins_enabled(plugins_enabled):
        return []

    builtin_ids: set[str] = {metadata.id for metadata in CHECK_METADATA}
    builtin_ids.update(LEGACY_CHECK_ID_ALIASES.keys())

    records: list[ValidatedPlugin] = []
    already_registered: set[str] = set()

    for entry_point in entry_points(group="agents_shipgate.checks"):
        if _is_builtin_entry_point(entry_point):
            continue
        record = validate_entry_point(
            entry_point,
            builtin_ids=builtin_ids,
            already_registered_plugin_ids=already_registered,
        )
        records.append(record)
        if record.metadata is not None:
            already_registered.add(record.metadata.id)

    return records


def _plugins_enabled(override: bool | None = None) -> bool:
    if override is not None:
        return override
    value = os.environ.get("AGENTS_SHIPGATE_ENABLE_PLUGINS", "")
    return value.lower() in {"1", "true", "yes", "on"}


def _is_builtin_entry_point(entry_point: Any) -> bool:
    dist = getattr(entry_point, "dist", None)
    distribution_name = _distribution_name(dist)
    if _normalize_distribution_name(distribution_name) == "agents-shipgate":
        return True
    if dist is None:
        return str(getattr(entry_point, "value", "")).startswith(
            "agents_shipgate.checks."
        )
    return False


def _normalize_distribution_name(value: str | None) -> str:
    return (value or "").replace("_", "-").lower()


def _distribution_name(dist: Any) -> str | None:
    """Internal helper retained for ``_is_builtin_entry_point``.

    The richer entry-point introspection moved to
    ``plugin_validation._distribution_name``; this thin wrapper exists
    so builtin detection logic doesn't need to import private symbols
    from the validation module.
    """

    if dist is None:
        return None
    metadata = getattr(dist, "metadata", None)
    if metadata is not None:
        try:
            name = metadata.get("Name")
        except AttributeError:
            name = None
        if isinstance(name, str):
            return name
    name = getattr(dist, "name", None)
    return str(name) if name else None
