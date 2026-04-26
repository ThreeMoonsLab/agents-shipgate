from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Any

from agents_shipgate.checks import (
    api,
    auth,
    documentation,
    inventory,
    manifest_consistency,
    manifest_scope,
    policy,
    schema,
    side_effects,
)
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.models import CheckMetadata, Finding

BUILTIN_CHECKS: list[Callable[[ScanContext], list[Finding]]] = [
    inventory.run,
    documentation.run,
    schema.run,
    auth.run,
    manifest_scope.run,
    policy.run,
    side_effects.run,
    api.run,
]


CHECK_METADATA: list[CheckMetadata] = [
    CheckMetadata(id="SHIP-INVENTORY-NOT-ENUMERABLE", category="inventory", default_severity="high", description="Tool surface cannot be enumerated from declared inputs.", rationale="A release gate must fail closed when it cannot see the agent's tools.", fires_when="No tools are loaded from required manifest sources.", evidence_fields=["tool_sources"], recommendation="Declare at least one local MCP JSON or OpenAPI tool source."),
    CheckMetadata(id="SHIP-INVENTORY-WILDCARD-TOOLS", category="inventory", default_severity="high", description="Wildcard or all-tools exposure is declared.", rationale="Wildcard tools make review and least-privilege reasoning impossible.", fires_when="A source declares all tools or wildcard exposure.", evidence_fields=["source_id", "source_ref"], recommendation="Replace wildcard exposure with an explicit allowlist."),
    CheckMetadata(id="SHIP-INVENTORY-TOOL-SURFACE-TOO-LARGE", category="inventory", default_severity="medium", description="Tool surface exceeds the MVP review threshold.", rationale="Large tool surfaces are harder to reason about during promotion.", fires_when="The normalized tool count exceeds the built-in threshold.", evidence_fields=["tool_count", "threshold"], recommendation="Split or reduce the tool surface before release."),
    CheckMetadata(id="SHIP-INVENTORY-LOW-CONFIDENCE-PRODUCTION-SURFACE", category="inventory", default_severity="high", description="Production target includes low-confidence tool extraction.", rationale="Production promotion should not depend primarily on best-effort SDK inference.", fires_when="environment.target is production and tools include lower-confidence extraction.", evidence_fields=["tools"], recommendation="Declare those tools through manifest, MCP, or OpenAPI inputs."),
    CheckMetadata(id="SHIP-DOC-MISSING-DESCRIPTION", category="documentation", default_severity="medium", description="Tool description is missing or too short.", rationale="Poor tool descriptions increase wrong-tool and reviewer misunderstanding risk.", fires_when="A tool description is missing or shorter than the minimum.", evidence_fields=["description_length"], recommendation="Add a clear capability description."),
    CheckMetadata(id="SHIP-DOC-INJECTION-RISK", category="security", default_severity="medium", description="Tool description contains instruction-override-like language.", rationale="Tool metadata can be placed into model context and should not contain prompt-like directives.", fires_when="Description text matches instruction override patterns. Severity is high only when multiple patterns match on a write/high-risk tool.", evidence_fields=["matched"], recommendation="Rewrite the description as neutral metadata."),
    CheckMetadata(id="SHIP-DOC-SECRET-IN-DESCRIPTION", category="security", default_severity="medium", description="Tool description contains a secret-like value.", rationale="Credentials in tool metadata can leak into reports, prompts, or logs.", fires_when="Description contains known key formats or labeled secret-like values. Severity is high only when multiple patterns match on a write/high-risk tool.", evidence_fields=["matched"], recommendation="Remove and rotate the exposed secret."),
    CheckMetadata(id="SHIP-SCHEMA-BROAD-FREE-TEXT", category="schema", default_severity="high", description="Action-like tool accepts broad free-form input.", rationale="Broad action/body/update fields increase blast radius for write tools.", fires_when="A write/action-like tool has free-form command/action/update-style parameters.", evidence_fields=["parameter", "type"], recommendation="Constrain the field with structured schema or enums."),
    CheckMetadata(id="SHIP-SCHEMA-MISSING-BOUNDS", category="schema", default_severity="high", description="Risky numeric parameter lacks a maximum bound.", rationale="Unbounded counts or financial amounts weaken blast-radius control.", fires_when="A risky numeric parameter lacks a maximum.", evidence_fields=["parameter", "type"], recommendation="Add a maximum or equivalent policy limit."),
    CheckMetadata(id="SHIP-SCHEMA-FREEFORM-OUTPUT", category="schema", default_severity="medium", description="Tool returns free-form string output.", rationale="Free-form tool output may carry prompt injection into later model context.", fires_when="A tool output schema is string or an SDK function returns str.", evidence_fields=["output_schema"], recommendation="Prefer structured output for model-consumed tool results."),
    CheckMetadata(id="SHIP-AUTH-MISSING-SCOPE", category="auth", default_severity="high", description="Scope-requiring tool lacks declared auth scopes.", rationale="Reviewers cannot assess least privilege without scope metadata.", fires_when="A write or sensitive-data tool has no auth scopes.", evidence_fields=["risk_tags"], recommendation="Declare scopes in OpenAPI, MCP, or manifest metadata."),
    CheckMetadata(id="SHIP-AUTH-MANIFEST-BROAD-SCOPE", category="auth", default_severity="high", description="Manifest declares broad permission scopes.", rationale="Broad manifest scopes weaken least-privilege review.", fires_when="permissions.scopes contains wildcard/admin-like scopes.", evidence_fields=["scopes"], recommendation="Replace with operation-specific scopes."),
    CheckMetadata(id="SHIP-AUTH-TOOL-BROAD-SCOPE", category="auth", default_severity="high", description="Tool declares broad auth scopes.", rationale="Tool-level broad scopes may grant more power than the operation needs.", fires_when="A tool auth scope is wildcard/admin-like.", evidence_fields=["scopes"], recommendation="Use narrower tool scopes."),
    CheckMetadata(id="SHIP-AUTH-SCOPE-COVERAGE-MISSING", category="auth", default_severity="high", description="Tool-required scopes are not covered by manifest permissions.scopes.", rationale="The manifest should describe the actual permissions needed by the release.", fires_when="A tool scope is absent from permissions.scopes and not covered by a wildcard.", evidence_fields=["tool_scopes", "manifest_scopes", "missing_scopes"], recommendation="Add or reconcile required scopes."),
    CheckMetadata(id="SHIP-SCOPE-TOOL-OUTSIDE-PURPOSE", category="scope", default_severity="high", description="Write-capable tool contradicts a read-only declared purpose.", rationale="Declared purpose should constrain the attached tool surface.", fires_when="Purpose text is read-only but attached tools are write-capable.", evidence_fields=["declared_purpose", "risk_tags"], recommendation="Remove the tool or update release scope."),
    CheckMetadata(id="SHIP-SCOPE-PROHIBITED-TOOL-PRESENT", category="scope", default_severity="high", description="Tool appears to overlap with a manifest prohibited action.", rationale="Prohibited actions should not be contradicted by attached tool capabilities.", fires_when="Tool name/description/risk tags overlap prohibited_actions without a mitigating policy.", evidence_fields=["prohibited_action", "risk_tags"], recommendation="Remove or narrow the tool, or revise policy/scope text."),
    CheckMetadata(id="SHIP-POLICY-APPROVAL-MISSING", category="policy", default_severity="critical", description="High-risk tool lacks a declared approval policy.", rationale="High-risk actions need explicit approval before promotion.", fires_when="Financial/destructive/infrastructure/code-exec risk exists without approval policy.", evidence_fields=["risk_tags", "policy_match"], recommendation="Declare an approval policy or remove the tool."),
    CheckMetadata(id="SHIP-POLICY-CONFIRMATION-MISSING", category="policy", default_severity="high", description="Destructive/external/customer-communication tool lacks a confirmation policy.", rationale="Destructive and external actions should require explicit confirmation.", fires_when="Risk tags require confirmation but no confirmation policy matches.", evidence_fields=["risk_tags", "policy_match"], recommendation="Declare confirmation policy or remove the tool."),
    CheckMetadata(id="SHIP-SIDEFX-IDEMPOTENCY-MISSING", category="side_effects", default_severity="high", description="Risky write tool lacks idempotency evidence; critical when retry is known.", rationale="Retries against non-idempotent writes can duplicate financial or external side effects.", fires_when="Risky write tool lacks idempotency annotation, key, or policy.", evidence_fields=["risk_tags", "retry_policy_known"], recommendation="Add idempotency evidence or policy."),
    CheckMetadata(id="SHIP-API-FUNCTION-SCHEMA-STRICTNESS", category="api", default_severity="high", description="OpenAI API function schema is not strict enough for reliable tool calls.", rationale="Strict schemas reduce ambiguous tool arguments and downstream side-effect risk.", fires_when="An OpenAI API function lacks strict=true, object parameters, additionalProperties=false, complete required fields, or bounded risky fields.", evidence_fields=["issues", "risk_tags"], recommendation="Use strict function schemas with explicit required fields and constrained risky parameters."),
    CheckMetadata(id="SHIP-API-STRUCTURED-OUTPUT-READINESS", category="api", default_severity="medium", description="OpenAI API structured output schema is missing or under-specified.", rationale="Downstream release decisions need explicit, structured success/refusal/review modeling.", fires_when="No response format exists, a response schema is too broad, decision/status fields lack enums, or refusal/needs_review/error modeling is absent.", evidence_fields=["path", "issues", "high_risk_tools"], recommendation="Declare a strict response format with decision/status enums, needs_review/refusal/error fields, and critical fields."),
    CheckMetadata(id="SHIP-API-PROMPT-TOOL-SCOPE-MISMATCH", category="api", default_severity="high", description="Prompt scope contradicts enabled OpenAI API tools.", rationale="Prompt instructions should match the actual write/high-risk tool surface.", fires_when="Prompt text says read-only/advice-only while write tools are enabled, or high-risk tools lack approval/confirmation instructions.", evidence_fields=["tools"], recommendation="Align prompt scope with enabled tools and add approval/confirmation instructions."),
    CheckMetadata(id="SHIP-API-OPERATIONAL-READINESS", category="api", default_severity="medium", description="OpenAI API flow lacks minimal operational readiness metadata.", rationale="Retries, timeouts, and success/failure schemas are needed to reason about side effects and failures.", fires_when="Retry policy, timeout metadata, tool output success/failure modeling, or simple trace approval evidence is missing or contradictory.", evidence_fields=["retry_policy", "high_risk_tools", "tool_output_schemas"], recommendation="Declare retry, timeout, output success/failure, and approval evidence for high-risk API flows."),
    CheckMetadata(id="SHIP-MANIFEST-STALE-SUPPRESSION", category="manifest", default_severity="medium", description="A suppression references a missing check or tool.", rationale="Stale suppressions hide intent and make release review harder to audit.", fires_when="checks.ignore contains an unknown check_id or a tool name that is not loaded.", evidence_fields=["check_id", "tool", "issues"], recommendation="Remove stale suppressions or update them to current check IDs and tool names."),
    CheckMetadata(id="SHIP-MANIFEST-STALE-POLICY", category="manifest", default_severity="medium", description="A policy references a missing tool.", rationale="Approval, confirmation, and idempotency policies should map to the actual release surface.", fires_when="A policy entry names a tool that is not loaded.", evidence_fields=["policy", "tool"], recommendation="Remove stale policy entries or update them to current tool names."),
    CheckMetadata(id="SHIP-MANIFEST-STALE-RISK-OVERRIDE", category="manifest", default_severity="medium", description="A risk override references a missing tool.", rationale="Risk overrides should not outlive the tool they describe.", fires_when="risk_overrides.tools contains a tool that is not loaded.", evidence_fields=["tool"], recommendation="Remove stale risk overrides or update them to current tool names."),
    CheckMetadata(id="SHIP-MANIFEST-HIGH-RISK-OWNER-MISSING", category="manifest", default_severity="high", description="Production high-risk tool has no declared owner.", rationale="High-risk production tools need an accountable owning team for review and remediation.", fires_when="environment.target is production_like or production and a high-risk tool lacks owner metadata.", evidence_fields=["environment", "risk_tags"], recommendation="Declare an owner for each high-risk production tool."),
    CheckMetadata(id="SHIP-MANIFEST-UNUSED-SCOPE", category="manifest", default_severity="medium", description="Manifest declares permission scopes unused by loaded tools.", rationale="Unused permissions weaken least-privilege review and often indicate stale config.", fires_when="permissions.scopes includes a scope not required by any loaded tool.", evidence_fields=["scope", "tool_scopes"], recommendation="Remove unused scopes or add tool metadata showing why they are required."),
]


@dataclass(frozen=True)
class LoadedPluginCheck:
    check: Callable[[ScanContext], list[Finding]]
    info: dict[str, str | None]


def run_checks(
    context: ScanContext,
    *,
    plugins_enabled: bool | None = None,
    loaded_plugins: list[dict[str, str | None]] | None = None,
) -> list[Finding]:
    findings: list[Finding] = []
    plugin_checks = _plugin_check_records(plugins_enabled=plugins_enabled)
    if loaded_plugins is not None:
        loaded_plugins.extend(record.info for record in plugin_checks)
    for check in [*BUILTIN_CHECKS, *(record.check for record in plugin_checks)]:
        findings.extend(check(context))
    findings.extend(
        manifest_consistency.run(
            context,
            known_check_ids={metadata.id for metadata in CHECK_METADATA},
        )
    )
    return findings


def check_catalog(*, plugins_enabled: bool | None = None) -> list[CheckMetadata]:
    metadata = [*CHECK_METADATA]
    for check in _plugin_checks(plugins_enabled=plugins_enabled):
        plugin_metadata = getattr(check, "AGENTS_SHIPGATE_METADATA", None)
        if plugin_metadata is None:
            continue
        metadata.append(_metadata_from_plugin(plugin_metadata))
    for check in metadata:
        if check.docs_url is None:
            check.docs_url = f"docs/checks.md#{check.id.lower()}"
    return sorted(metadata, key=lambda check: check.id)


def check_functions(
    *, plugins_enabled: bool | None = None
) -> list[Callable[[ScanContext], list[Finding]]]:
    return [*BUILTIN_CHECKS, *_plugin_checks(plugins_enabled=plugins_enabled)]


def _plugin_checks(
    *, plugins_enabled: bool | None = None
) -> list[Callable[[ScanContext], list[Finding]]]:
    return [
        record.check
        for record in _plugin_check_records(plugins_enabled=plugins_enabled)
    ]


def _plugin_check_records(
    *,
    plugins_enabled: bool | None = None,
) -> list[LoadedPluginCheck]:
    if not _plugins_enabled(plugins_enabled):
        return []
    checks: list[LoadedPluginCheck] = []
    for entry_point in entry_points(group="agents_shipgate.checks"):
        if _is_builtin_entry_point(entry_point):
            continue
        loaded = entry_point.load()
        if callable(loaded):
            checks.append(
                LoadedPluginCheck(
                    check=loaded,
                    info=_plugin_info(entry_point, loaded),
                )
            )
    return checks


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


def _plugin_info(
    entry_point: Any,
    loaded: Callable[[ScanContext], list[Finding]],
) -> dict[str, str | None]:
    metadata = getattr(loaded, "AGENTS_SHIPGATE_METADATA", None)
    check_id: str | None = None
    if isinstance(metadata, CheckMetadata):
        check_id = metadata.id
    elif isinstance(metadata, dict) and isinstance(metadata.get("id"), str):
        check_id = metadata["id"]
    dist = getattr(entry_point, "dist", None)
    return {
        "name": str(getattr(entry_point, "name", "")) or None,
        "value": str(getattr(entry_point, "value", "")) or None,
        "distribution": _distribution_name(dist),
        "version": _distribution_version(dist),
        "check_id": check_id,
    }


def _distribution_name(dist: Any) -> str | None:
    if dist is None:
        return None
    metadata = getattr(dist, "metadata", None)
    if metadata is not None:
        name = metadata.get("Name")
        if isinstance(name, str):
            return name
    name = getattr(dist, "name", None)
    return str(name) if name else None


def _distribution_version(dist: Any) -> str | None:
    if dist is None:
        return None
    version = getattr(dist, "version", None)
    return str(version) if version else None


def _normalize_distribution_name(value: str | None) -> str:
    return (value or "").replace("_", "-").lower()


def _metadata_from_plugin(value: Any) -> CheckMetadata:
    if isinstance(value, CheckMetadata):
        return value
    if isinstance(value, dict):
        return CheckMetadata.model_validate(value)
    raise TypeError("AGENTS_SHIPGATE_METADATA must be CheckMetadata or dict")
