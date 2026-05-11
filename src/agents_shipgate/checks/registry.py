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
    crewai,
    documentation,
    evidence,
    inventory,
    langchain,
    manifest_consistency,
    manifest_scope,
    n8n,
    policy,
    schema,
    side_effects,
)
from agents_shipgate.core.check_ids import known_check_ids_with_legacy
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.models import CheckMetadata, Finding

# Base for `docs_url` values surfaced through `list-checks --json` and
# (in PR 3) per-finding `docs_url`. Stable per-check anchors are H3
# headings in `docs/checks.md` (`### SHIP-...`); GitHub lowercases the
# anchor.
_DOCS_URL_BASE = (
    "https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/checks.md"
)


def _docs_url(check_id: str) -> str:
    return f"{_DOCS_URL_BASE}#{check_id.lower()}"


# Per-check remediation policy overrides. Defaults from `CheckMetadata`:
# `autofix_safe=False`, `requires_human_review=True`,
# `suggested_patch_kind="manual"`. Listed entries override only the
# fields they specify; everything else inherits the safe-closed default.
#
# Important contract: `autofix_safe` and `requires_human_review` at the
# catalog level describe the *worst-case* per-check outcome — what an
# agent should assume when it has only `list-checks --json` and no
# scan output. A check whose generator USUALLY emits a safe non-manual
# patch but falls back to `ManualPatch` in edge cases (ambiguous
# duplicate matches, missing evidence fields, etc.) MUST keep the
# safe-closed defaults at this level. The mirror Finding-level fields
# (PR 3) read the actual emitted patches and can be more permissive —
# they tell the truth for that specific finding instance.
#
# `suggested_patch_kind` is informational here: it documents the kind
# the generator *targets* when conditions are clean. An agent that sees
# `suggested_patch_kind: "remove_pointer"` should still consult the
# per-Finding fields (or `Finding.patches` directly) to know whether
# this particular instance produced a remove_pointer or fell back to
# a ManualPatch.
_REMEDIATION_OVERRIDES: dict[str, dict[str, object]] = {
    # Stale-manifest checks: generator targets remove_pointer when the
    # match is unique. Falls back to ManualPatch when ≥ 2 manifest
    # entries match the same evidence (ambiguous removal — see
    # `checks/patches.py::_gen_stale_*`). Catalog stays conservative:
    # `autofix_safe=False`, `requires_human_review=True`. Per-Finding
    # fields will mark `autofix_safe=True` only for findings whose
    # actual emitted patch is high-confidence non-manual.
    "SHIP-MANIFEST-STALE-SUPPRESSION": {
        "suggested_patch_kind": "remove_pointer",
    },
    "SHIP-MANIFEST-STALE-POLICY": {
        "suggested_patch_kind": "remove_pointer",
    },
    "SHIP-MANIFEST-STALE-RISK-OVERRIDE": {
        "suggested_patch_kind": "remove_pointer",
    },
    # Scope coverage: generator emits append_pointer at medium
    # confidence (NOT default-applied — adding scopes can encode policy
    # choices, so `apply-patches --confidence high` skips it). Catalog
    # stays conservative for the same reason as above.
    "SHIP-AUTH-SCOPE-COVERAGE-MISSING": {
        "suggested_patch_kind": "append_pointer",
    },
}


def _meta(**kwargs: object) -> CheckMetadata:
    """Construct a `CheckMetadata` with auto-derived `docs_url` and
    per-check remediation policy applied from `_REMEDIATION_OVERRIDES`.

    Per-call kwargs win over the override table, so a future entry can
    pin `autofix_safe`, `requires_human_review`, or `suggested_patch_kind`
    inline if the override approach becomes unwieldy.
    """
    check_id = kwargs["id"]
    overrides = _REMEDIATION_OVERRIDES.get(check_id, {})  # type: ignore[arg-type]
    merged: dict[str, object] = {
        "docs_url": _docs_url(check_id),  # type: ignore[arg-type]
        **overrides,
        **kwargs,
    }
    return CheckMetadata(**merged)  # type: ignore[arg-type]

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
    n8n.run,
]


CHECK_METADATA: list[CheckMetadata] = [
    _meta(id="SHIP-INVENTORY-NOT-ENUMERABLE", category="inventory", default_severity="high", description="Tool surface cannot be enumerated from declared inputs.", rationale="A release gate must fail closed when it cannot see the agent's tools.", fires_when="No tools are loaded from required manifest sources.", evidence_fields=["tool_sources"], recommendation="Declare at least one local MCP JSON or OpenAPI tool source."),
    _meta(id="SHIP-INVENTORY-WILDCARD-TOOLS", category="inventory", default_severity="high", description="Wildcard or all-tools exposure is declared.", rationale="Wildcard tools make review and least-privilege reasoning impossible.", fires_when="A source declares all tools or wildcard exposure.", evidence_fields=["source_id", "source_ref"], recommendation="Replace wildcard exposure with an explicit allowlist."),
    _meta(id="SHIP-INVENTORY-TOOL-SURFACE-TOO-LARGE", category="inventory", default_severity="medium", description="Tool surface exceeds the MVP review threshold.", rationale="Large tool surfaces are harder to reason about during promotion.", fires_when="The normalized tool count exceeds the built-in threshold.", evidence_fields=["tool_count", "threshold"], recommendation="Split or reduce the tool surface before release."),
    _meta(id="SHIP-INVENTORY-LOW-CONFIDENCE-PRODUCTION-SURFACE", category="inventory", default_severity="high", description="Production target includes low-confidence tool extraction.", rationale="Production promotion should not depend primarily on best-effort SDK inference.", fires_when="environment.target is production and tools include lower-confidence extraction.", evidence_fields=["tools"], recommendation="Declare those tools through manifest, MCP, or OpenAPI inputs."),
    _meta(id="SHIP-DOC-MISSING-DESCRIPTION", category="documentation", default_severity="medium", description="Tool description is missing or too short.", rationale="Poor tool descriptions increase wrong-tool and reviewer misunderstanding risk.", fires_when="A tool description is missing or shorter than the minimum.", evidence_fields=["description_length"], recommendation="Add a clear capability description."),
    _meta(id="SHIP-DOC-INJECTION-RISK", category="security", default_severity="medium", description="Tool description contains instruction-override-like language.", rationale="Tool metadata can be placed into model context and should not contain prompt-like directives.", fires_when="Description text matches instruction override patterns. Severity is high only when multiple patterns match on a write/high-risk tool.", evidence_fields=["matched"], recommendation="Rewrite the description as neutral metadata."),
    _meta(id="SHIP-DOC-SECRET-IN-DESCRIPTION", category="security", default_severity="medium", description="Tool description contains a secret-like value.", rationale="Credentials in tool metadata can leak into reports, prompts, or logs.", fires_when="Description contains known key formats or labeled secret-like values. Severity is high only when multiple patterns match on a write/high-risk tool.", evidence_fields=["matched"], recommendation="Remove and rotate the exposed secret."),
    _meta(id="SHIP-SCHEMA-BROAD-FREE-TEXT", category="schema", default_severity="high", description="Action-like tool accepts broad free-form input.", rationale="Broad action/body/update fields increase blast radius for write tools.", fires_when="A write/action-like tool has free-form command/action/update-style parameters.", evidence_fields=["parameter", "type"], recommendation="Constrain the field with structured schema or enums."),
    _meta(id="SHIP-SCHEMA-MISSING-BOUNDS", category="schema", default_severity="high", description="Risky numeric parameter lacks a maximum bound.", rationale="Unbounded counts or financial amounts weaken blast-radius control.", fires_when="A risky numeric parameter lacks a maximum.", evidence_fields=["parameter", "type"], recommendation="Add a maximum or equivalent policy limit."),
    _meta(id="SHIP-SCHEMA-FREEFORM-OUTPUT", category="schema", default_severity="medium", description="Tool returns free-form string output.", rationale="Free-form tool output may carry prompt injection into later model context.", fires_when="A tool output schema is string or an SDK function returns str.", evidence_fields=["output_schema"], recommendation="Prefer structured output for model-consumed tool results."),
    _meta(id="SHIP-AUTH-MISSING-SCOPE", category="auth", default_severity="high", description="Scope-requiring tool lacks declared auth scopes.", rationale="Reviewers cannot assess least privilege without scope metadata.", fires_when="A write or sensitive-data tool has no auth scopes.", evidence_fields=["risk_tags"], recommendation="Declare scopes in OpenAPI, MCP, or manifest metadata."),
    _meta(id="SHIP-AUTH-MANIFEST-BROAD-SCOPE", category="auth", default_severity="high", description="Manifest declares broad permission scopes.", rationale="Broad manifest scopes weaken least-privilege review.", fires_when="permissions.scopes contains wildcard/admin-like scopes.", evidence_fields=["scopes"], recommendation="Replace with operation-specific scopes."),
    _meta(id="SHIP-AUTH-TOOL-BROAD-SCOPE", category="auth", default_severity="high", description="Tool declares broad auth scopes.", rationale="Tool-level broad scopes may grant more power than the operation needs.", fires_when="A tool auth scope is wildcard/admin-like.", evidence_fields=["scopes"], recommendation="Use narrower tool scopes."),
    _meta(id="SHIP-AUTH-SCOPE-COVERAGE-MISSING", category="auth", default_severity="high", description="Tool-required scopes are not covered by manifest permissions.scopes.", rationale="The manifest should describe the actual permissions needed by the release.", fires_when="A tool scope is absent from permissions.scopes and not covered by a wildcard.", evidence_fields=["tool_scopes", "manifest_scopes", "missing_scopes"], recommendation="Add or reconcile required scopes."),
    _meta(id="SHIP-SCOPE-TOOL-OUTSIDE-PURPOSE", category="scope", default_severity="high", description="Write-capable tool contradicts a read-only declared purpose.", rationale="Declared purpose should constrain the attached tool surface.", fires_when="Purpose text is read-only but attached tools are write-capable.", evidence_fields=["declared_purpose", "risk_tags"], recommendation="Remove the tool or update release scope."),
    _meta(id="SHIP-SCOPE-PROHIBITED-TOOL-PRESENT", category="scope", default_severity="high", description="Tool appears to overlap with a manifest prohibited action.", rationale="Prohibited actions should not be contradicted by attached tool capabilities.", fires_when="Tool name/description/risk tags overlap prohibited_actions without a mitigating policy.", evidence_fields=["prohibited_action", "risk_tags"], recommendation="Remove or narrow the tool, or revise policy/scope text."),
    _meta(id="SHIP-POLICY-APPROVAL-MISSING", category="policy", default_severity="critical", description="High-risk tool lacks a declared approval policy.", rationale="High-risk actions need explicit approval before promotion.", fires_when="Financial/destructive/infrastructure/code-exec risk exists without approval policy.", evidence_fields=["risk_tags", "policy_match"], recommendation="Declare an approval policy or remove the tool."),
    _meta(id="SHIP-POLICY-CONFIRMATION-MISSING", category="policy", default_severity="high", description="Destructive/external/customer-communication tool lacks a confirmation policy.", rationale="Destructive and external actions should require explicit confirmation.", fires_when="Risk tags require confirmation but no confirmation policy matches.", evidence_fields=["risk_tags", "policy_match"], recommendation="Declare confirmation policy or remove the tool."),
    _meta(id="SHIP-EVIDENCE-APPROVAL-TRACE-MISSING", category="evidence", default_severity="high", description="Local HITL approval trace evidence is missing or incomplete for an approval-required tool.", rationale="Limited automation review depends on reviewer-visible local evidence that approval-controlled actions were approved before the tool call; absence of local evidence does not prove the runtime control is absent.", fires_when="validation.required_evidence.approval_trace_required is true and no loaded local approval trace shows approved=true for an approval-required tool.", evidence_fields=["tool_name", "required", "reason", "trace_files", "approved_tools", "source_provenance"], recommendation="Add or fix local approval trace evidence, or change the validation review posture."),
    _meta(id="SHIP-EVIDENCE-OVERRIDE-REASON-MISSING", category="evidence", default_severity="high", description="Local HITL override reason evidence is missing or incomplete.", rationale="Override, bypass, and auto-approval events need reviewer-visible local reasons for governance review; absence of local evidence does not prove the runtime control is absent.", fires_when="validation.required_evidence.override_reason_required is true and override logs are absent, empty, unloadable, or contain events without non-empty reasons.", evidence_fields=["required", "reason", "override_log_files", "events_missing_reason", "source_provenance"], recommendation="Record non-empty reasons in local override, bypass, and auto-approval evidence."),
    _meta(id="SHIP-EVIDENCE-HIGH-RISK-EXCLUSION-MISSING", category="evidence", default_severity="high", description="Local high-risk auto-approval exclusion evidence is missing or incomplete.", rationale="High-risk tools that already declare approval policy need separate local evidence that they are excluded from auto-approval review posture; absence of local evidence does not prove the runtime control is absent.", fires_when="validation.required_evidence.high_risk_auto_approval_exclusion_required is true and a high-risk tool with declared approval policy is not listed in loaded high_risk_auto_approval_exclusions.", evidence_fields=["required", "reason", "risk_tags", "exclusion_files", "excluded_tools", "source_provenance"], recommendation="Document high-risk approval-controlled tools in local high_risk_auto_approval_exclusions with reasons."),
    _meta(id="SHIP-EVIDENCE-HITL-PROMOTION-CRITERIA-MISSING", category="evidence", default_severity="high", description="Local HITL promotion criteria evidence is missing or incomplete.", rationale="A limited auto-approval review posture needs local criteria evidence; Shipgate structures the missing evidence for reviewers but does not certify runtime enforcement.", fires_when="validation.target_review_posture is limited_auto_approval and promotion criteria are absent, unloadable, or the canonical required-evidence flags are not true in the manifest and criteria file.", evidence_fields=["target_review_posture", "reason", "criteria_files", "manifest_flags_missing", "criteria_flags_missing", "source_provenance"], recommendation="Add or fix local promotion criteria evidence documenting the review posture and required evidence flags."),
    _meta(id="SHIP-SIDEFX-IDEMPOTENCY-MISSING", category="side_effects", default_severity="high", description="Risky write tool lacks idempotency evidence; critical when retry is known.", rationale="Retries against non-idempotent writes can duplicate financial or external side effects.", fires_when="Risky write tool lacks idempotency annotation, key, or policy.", evidence_fields=["risk_tags", "retry_policy_known"], recommendation="Add idempotency evidence or policy."),
    _meta(id="SHIP-API-FUNCTION-SCHEMA-STRICTNESS", category="api", default_severity="high", description="OpenAI API function schema is not strict enough for reliable tool calls.", rationale="Strict schemas reduce ambiguous tool arguments and downstream side-effect risk.", fires_when="An OpenAI API function lacks strict=true, object parameters, additionalProperties=false, complete required fields, or bounded risky fields.", evidence_fields=["issues", "risk_tags"], recommendation="Use strict function schemas with explicit required fields and constrained risky parameters."),
    _meta(id="SHIP-API-STRUCTURED-OUTPUT-READINESS", category="api", default_severity="medium", description="OpenAI API structured output schema is missing or under-specified.", rationale="Downstream release decisions need explicit, structured success/refusal/review modeling.", fires_when="No response format exists, a response schema is too broad, decision/status fields lack enums, or refusal/needs_review/error modeling is absent.", evidence_fields=["path", "issues", "high_risk_tools"], recommendation="Declare a strict response format with decision/status enums, needs_review/refusal/error fields, and critical fields."),
    _meta(id="SHIP-API-PROMPT-TOOL-SCOPE-MISMATCH", category="api", default_severity="high", description="Prompt scope contradicts enabled OpenAI API tools.", rationale="Prompt instructions should match the actual write/high-risk tool surface.", fires_when="Prompt text says read-only/advice-only while write tools are enabled, or high-risk tools lack approval/confirmation instructions.", evidence_fields=["tools"], recommendation="Align prompt scope with enabled tools and add approval/confirmation instructions."),
    _meta(id="SHIP-API-RETRY-POLICY-MISSING", category="api", default_severity="medium", description="OpenAI API high-risk flow lacks retry policy metadata.", rationale="Retries need explicit policy metadata so reviewers can reason about duplicate side effects.", fires_when="High-risk OpenAI API tools exist and no retry_policy is declared.", evidence_fields=["high_risk_tools"], recommendation="Declare retry_policy in openai_api.policy_rules or model_config."),
    _meta(id="SHIP-API-TIMEOUT-MISSING", category="api", default_severity="medium", description="OpenAI API high-risk flow lacks timeout metadata.", rationale="Timeouts define failure behavior and reduce ambiguous tool-call continuation.", fires_when="High-risk OpenAI API tools exist and no timeout metadata is declared.", evidence_fields=["high_risk_tools"], recommendation="Declare tool-call timeout metadata for high-risk OpenAI API flows."),
    _meta(id="SHIP-API-TEST-CASES-MISSING", category="api", default_severity="medium", description="OpenAI API high-risk flow lacks test case metadata.", rationale="High-risk tool-call flows should have release evidence before promotion.", fires_when="High-risk OpenAI API tools exist and no test cases are declared.", evidence_fields=["high_risk_tools"], recommendation="Add simple OpenAI API test cases for high-risk tool-call flows."),
    _meta(id="SHIP-API-TOOL-OUTPUT-SCHEMA-MISSING", category="api", default_severity="medium", description="OpenAI API high-risk tool lacks success/failure output modeling.", rationale="Tool output schemas help release reviewers reason about downstream failure handling.", fires_when="A high-risk OpenAI API tool lacks declared success/failure output schema metadata.", evidence_fields=["tool_output_schemas"], recommendation="Declare success_fields and failure_fields for high-risk OpenAI API tools."),
    _meta(id="SHIP-API-RETRY-WITHOUT-IDEMPOTENCY", category="api", default_severity="high", description="OpenAI API write tool may be retried without idempotency evidence.", rationale="Retries against non-idempotent writes can duplicate financial, destructive, or external side effects.", fires_when="Retry policy is declared and a risky write tool lacks idempotency evidence.", evidence_fields=["retry_policy", "risk_tags"], recommendation="Add idempotency evidence for retried risky OpenAI API tools or avoid retrying those side effects."),
    _meta(id="SHIP-API-TRACE-APPROVAL-MISSING", category="api", default_severity="medium", description="OpenAI API trace sample shows a policy-controlled tool without approval.", rationale="Trace samples should demonstrate approval behavior for tools that require approval.", fires_when="A trace sample marks approved=false for a tool with approval policy evidence.", evidence_fields=["tool_name", "approved"], recommendation="Require approval before calling policy-controlled OpenAI API tools."),
    _meta(id="SHIP-API-TRACE-CONFIRMATION-MISSING", category="api", default_severity="medium", description="OpenAI API trace sample shows a policy-controlled tool without confirmation.", rationale="Trace samples should demonstrate explicit confirmation for tools that require confirmation.", fires_when="A trace sample marks confirmed=false for a tool with confirmation policy evidence.", evidence_fields=["tool_name", "confirmed"], recommendation="Require explicit confirmation before calling policy-controlled OpenAI API tools."),
    _meta(id="SHIP-API-OPERATIONAL-READINESS", category="api", default_severity="medium", description="Deprecated compatibility alias for the v0.3 OpenAI API operational readiness bundle.", rationale="v0.4 emits atomic OpenAI API readiness check IDs, but this ID remains available for existing suppressions, severity overrides, baselines, SARIF consumers, and explain/list-checks workflows during the deprecation window.", fires_when="Not emitted by v0.4 scans; matching configuration expands to the v0.4 atomic OpenAI API operational readiness checks.", evidence_fields=["legacy_check_id"], recommendation="Migrate suppressions, severity overrides, and baselines to the specific v0.4 SHIP-API-* readiness check IDs."),
    _meta(id="SHIP-ADK-DYNAMIC-TOOLSET-NOT-ENUMERABLE", category="adk", default_severity="high", description="Google ADK toolset cannot be statically enumerated.", rationale="Release review needs an explicit tool inventory; ADK MCP/OpenAPI toolsets may resolve tools dynamically at runtime.", fires_when="A Google ADK toolset is dynamic or unresolved and no explicit MCP/OpenAPI/tool inventory input is declared.", evidence_fields=["toolset", "explicit_inventory"], recommendation="Provide explicit MCP/OpenAPI/tool inventory inputs for dynamic ADK toolsets."),
    _meta(id="SHIP-ADK-MCP-TOOLSET-UNFILTERED", category="adk", default_severity="high", description="Google ADK McpToolset lacks a static tool filter.", rationale="Unfiltered MCP toolsets can expose more tools than reviewers expect.", fires_when="A Google ADK McpToolset has no static tool_filter.", evidence_fields=["source_ref", "agent_name", "inventory_path"], recommendation="Declare a tool_filter and provide a local MCP tool inventory."),
    _meta(id="SHIP-ADK-FUNCTION-TOOL-METADATA-MISSING", category="adk", default_severity="medium", description="Google ADK function tool lacks static metadata.", rationale="Static review depends on descriptions and parameter schemas because user ADK code is not imported.", fires_when="A Google ADK function/config tool lacks description or parameter metadata.", evidence_fields=["missing", "source_type"], recommendation="Add docstrings, annotations, or explicit local inventory metadata."),
    _meta(id="SHIP-ADK-LONGRUNNING-CONTRACT-MISSING", category="adk", default_severity="high", description="Google ADK long-running tool lacks an operation contract.", rationale="Long-running tools need explicit status and operation-id semantics for safe continuation.", fires_when="A LongRunningFunctionTool lacks structured status/progress and operation id output evidence.", evidence_fields=["output_schema"], recommendation="Document operation id, status/progress fields, and completion behavior."),
    _meta(id="SHIP-ADK-GUARDRAIL-EVIDENCE-MISSING", category="adk", default_severity="high", description="High-risk Google ADK tools lack static guardrail evidence.", rationale="Callbacks and plugins are the static ADK surface where release reviewers can see guardrail intent.", fires_when="High-risk ADK tools are present without callbacks, plugins, or equivalent manifest policy evidence.", evidence_fields=["tools"], recommendation="Attach ADK callbacks/plugins or manifest policies that document guardrails."),
    _meta(id="SHIP-ADK-EVAL-COVERAGE-MISSING", category="adk", default_severity="medium", description="Google ADK eval coverage is not declared.", rationale="ADK releases should include response and tool-trajectory eval evidence before promotion.", fires_when="Google ADK inputs target production_like or production and no eval files are declared.", evidence_fields=["agent_count", "eval_file_count"], recommendation="Declare ADK eval files for expected responses and tool-use trajectories."),
    _meta(id="SHIP-LANGCHAIN-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE", category="langchain", default_severity="high", description="LangChain tool surface cannot be statically enumerated.", rationale="LangChain and LangGraph expose ad hoc tool lists and agent-bound tools rather than a consistent toolset abstraction, so the check names the broader tool surface instead of ADK's toolset.", fires_when="A LangChain or LangGraph tool binding is dynamic or unresolved and no explicit local inventory is declared.", evidence_fields=["surface", "explicit_inventory"], recommendation="Provide explicit MCP-style tool inventory metadata for dynamic LangChain tool lists."),
    _meta(id="SHIP-LANGCHAIN-FUNCTION-TOOL-METADATA-MISSING", category="langchain", default_severity="medium", description="LangChain function tool lacks static metadata.", rationale="Static review depends on descriptions and parameter schemas because user LangChain code is not imported.", fires_when="A LangChain @tool or StructuredTool lacks description or parameter metadata.", evidence_fields=["missing", "source_type"], recommendation="Add docstrings, descriptions, type annotations, args_schema, or explicit local inventory metadata."),
    _meta(id="SHIP-CREWAI-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE", category="crewai", default_severity="high", description="CrewAI tool surface cannot be statically enumerated.", rationale="CrewAI exposes ad hoc agent-bound tool lists rather than a consistent toolset abstraction, so the check names the broader tool surface instead of ADK's toolset.", fires_when="A CrewAI agent tool binding is dynamic or unresolved and no explicit local inventory is declared.", evidence_fields=["surface", "explicit_inventory"], recommendation="Provide explicit MCP-style tool inventory metadata for dynamic CrewAI tool lists."),
    _meta(id="SHIP-CREWAI-FUNCTION-TOOL-METADATA-MISSING", category="crewai", default_severity="medium", description="CrewAI function tool lacks static metadata.", rationale="Static review depends on descriptions and parameter schemas because user CrewAI code is not imported.", fires_when="A CrewAI @tool or BaseTool class lacks description or parameter metadata.", evidence_fields=["missing", "source_type"], recommendation="Add docstrings, descriptions, type annotations, args_schema, or explicit local inventory metadata."),
    _meta(id="SHIP-N8N-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE", category="n8n", default_severity="high", description="n8n tool surface cannot be statically enumerated.", rationale="Release review needs an explicit local inventory when n8n workflow JSON uses runtime tool names, unresolved workflow references, wildcard MCP exposure, or community tool nodes without static metadata. This is high severity in every environment because static release evidence cannot prove the actual tool inventory.", fires_when="An n8n workflow has an unresolved DB workflow id, runtime expression in a tool name, wildcard MCP Server/Client exposure without an inventory, or an uninventoried community/custom tool node.", evidence_fields=["surface", "explicit_inventory"], recommendation="Provide explicit local n8n/MCP tool inventory metadata or replace runtime/wildcard n8n tool exposure."),
    _meta(id="SHIP-N8N-MCP-CLIENT-TOOLSET-UNFILTERED", category="n8n", default_severity="high", description="n8n MCP Client Tool exposes an unfiltered toolset.", rationale="All-tools and all-except MCP client selections can expose more tools than reviewers expect. The check is environment-sensitive because the selector is straightforward to narrow before production, while production-like use increases blast radius.", fires_when="An n8n MCP Client Tool uses All or All Except selection and no explicit local inventory is declared.", evidence_fields=["source_ref", "node_id", "selection_mode", "explicit_inventory"], recommendation="Select an explicit MCP tool allowlist in n8n or provide a local MCP inventory."),
    _meta(id="SHIP-N8N-AI-TOOL-METADATA-MISSING", category="n8n", default_severity="medium", description="n8n AI-exposed tool lacks static metadata.", rationale="Static review depends on descriptions and parameter schemas because Shipgate does not execute n8n workflows.", fires_when="An n8n AI-exposed tool lacks description or static parameter metadata.", evidence_fields=["missing", "source_type"], recommendation="Add n8n tool descriptions, $fromAI() parameter metadata, workflow input schemas, or explicit inventory metadata."),
    _meta(id="SHIP-N8N-CREDENTIAL-EVIDENCE-MISSING", category="n8n", default_severity="high", description="n8n credential stubs are not declared.", rationale="Credential type evidence lets reviewers assess high-risk integrations without exposing secret values.", fires_when="Production-like n8n workflows reference credentials but no local credential stubs are declared.", evidence_fields=["credential_ref_count", "credential_stub_file_count"], recommendation="Declare local n8n credential stubs."),
    _meta(id="SHIP-N8N-EVAL-COVERAGE-MISSING", category="n8n", default_severity="medium", description="n8n eval coverage is not declared.", rationale="n8n AI workflow releases should include response and tool-trajectory eval evidence before promotion.", fires_when="n8n inputs target production_like or production and no eval files are declared.", evidence_fields=["workflow_count", "ai_agent_count", "eval_file_count"], recommendation="Declare n8n eval files for expected responses and tool-use trajectories."),
    _meta(id="SHIP-N8N-SECRET-IN-WORKFLOW-PARAMETER", category="security", default_severity="high", description="n8n workflow JSON contains a secret-like value.", rationale="Workflow JSON, pinned data, static data, and node notes can be committed or reported; hardcoded secret-like values should be moved into credentials or variables.", fires_when="A static n8n workflow parameter, node note, pinData entry, or staticData entry matches a secret-like token pattern. Evidence is redacted and contains only source location and secret kind, never a secret verifier.", evidence_fields=["source_ref", "parameter_pointer", "secret_kind"], recommendation="Move secret values into n8n credentials or variables and rotate the exposed value."),
    _meta(id="SHIP-MANIFEST-STALE-SUPPRESSION", category="manifest", default_severity="medium", description="A suppression references a missing check or tool.", rationale="Stale suppressions hide intent and make release review harder to audit.", fires_when="checks.ignore contains an unknown check_id or a tool name that is not loaded.", evidence_fields=["check_id", "tool", "issues"], recommendation="Remove stale suppressions or update them to current check IDs and tool names."),
    _meta(id="SHIP-MANIFEST-STALE-POLICY", category="manifest", default_severity="medium", description="A policy references a missing tool.", rationale="Approval, confirmation, and idempotency policies should map to the actual release surface.", fires_when="A policy entry names a tool that is not loaded.", evidence_fields=["policy", "tool"], recommendation="Remove stale policy entries or update them to current tool names."),
    _meta(id="SHIP-MANIFEST-STALE-RISK-OVERRIDE", category="manifest", default_severity="medium", description="A risk override references a missing tool.", rationale="Risk overrides should not outlive the tool they describe.", fires_when="risk_overrides.tools contains a tool that is not loaded.", evidence_fields=["tool"], recommendation="Remove stale risk overrides or update them to current tool names."),
    _meta(id="SHIP-MANIFEST-HIGH-RISK-OWNER-MISSING", category="manifest", default_severity="high", description="Production high-risk tool has no declared owner.", rationale="High-risk production tools need an accountable owning team for review and remediation.", fires_when="environment.target is production_like or production and a high-risk tool lacks owner metadata.", evidence_fields=["environment", "risk_tags"], recommendation="Declare an owner for each high-risk production tool."),
    _meta(id="SHIP-MANIFEST-UNUSED-SCOPE", category="manifest", default_severity="medium", description="Manifest declares permission scopes unused by loaded tools.", rationale="Unused permissions weaken least-privilege review and often indicate stale config.", fires_when="permissions.scopes includes a scope not required by any loaded tool.", evidence_fields=["scope", "tool_scopes"], recommendation="Remove unused scopes or add tool metadata showing why they are required."),
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
    extra_known_check_ids: set[str] | None = None,
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
            known_check_ids=known_check_ids_with_legacy(
                {
                    *(metadata.id for metadata in CHECK_METADATA),
                    *(extra_known_check_ids or set()),
                }
            ),
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
