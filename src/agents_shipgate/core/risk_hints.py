from __future__ import annotations

import re
from collections.abc import Iterable

from agents_shipgate.core.domain import (
    Scope,
    SideEffect,
    Tool,
    ToolRiskHint,
)
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.core.tool_identity import resolve_selectors_by_tool_id
from agents_shipgate.schemas.common import (
    confidence_rank,
    parse_confidence,
)
from agents_shipgate.schemas.manifest import AgentsShipgateManifest
from agents_shipgate.schemas.surfaces import ActionEffect

HIGH_RISK_TAGS = {
    "destructive",
    "external_write",
    "financial_action",
    "customer_communication",
    "sensitive_data_access",
    "infrastructure_change",
    "code_execution",
}

WRITE_TAGS = {"write", "destructive", "external_write"}

# Keyword classifier sets. Word-tokenized, so each keyword must match a full
# alphabetic token in the tool name or description (or in the joined auth
# scopes). This avoids substring false positives like "deploy" matching the
# token "deployments" — listing deployments is a read, not an infra change.
# Plurals are listed explicitly where production scopes commonly use them
# (e.g. "stripe:refunds:write"). Compound forms like "deployment" or
# "deployments" are intentionally NOT included for write-implying tags so
# read-only listings don't trip mutation flags.
READ_ONLY_KEYWORDS = {
    "get",
    "list",
    "lookup",
    "preview",
    "search",
    "status",
    "view",
}
WRITE_KEYWORDS = {
    "cancel",
    "charge",
    "create",
    "delete",
    "issue",
    "refund",
    "remove",
    "send",
    "update",
    "write",
}
DESTRUCTIVE_KEYWORDS = {"cancel", "delete", "destroy", "remove"}
FINANCIAL_KEYWORDS = {
    "billing",
    "charge",
    "charges",
    "invoice",
    "invoices",
    "payment",
    "payments",
    "refund",
    "refunds",
}
COMMS_KEYWORDS = {"email", "emails", "message", "messages", "sms"}
EXTERNAL_ACTION_KEYWORDS = {"customer", "external", "send"}
SENSITIVE_KEYWORDS = {
    "credential",
    "credentials",
    "personal",
    "pii",
    "secret",
    "secrets",
    "ssn",
}
CODE_EXEC_KEYWORDS = {"bash", "command", "execute", "python", "shell"}
INFRASTRUCTURE_KEYWORDS = {
    "aws",
    "azure",
    "cluster",
    "clusters",
    "deploy",
    "droplet",
    "droplets",
    "gcp",
    "kubernetes",
    "terraform",
}

_KEYWORD_GATED_SOURCE_TYPES = {
    "openai_api",
    "anthropic_api",
    "sdk_function",
    "langchain_function",
    "langchain_structured_tool",
    "crewai_function",
    "crewai_class_tool",
    "n8n_ai_tool",
    "n8n_workflow_tool",
    "n8n_code_tool",
    "n8n_http_tool",
    "n8n_mcp_client_tool",
    "n8n_inventory",
    "conductor_mcp_call",
}


def enrich_tools_with_risk_hints(manifest: AgentsShipgateManifest, tools: list[Tool]) -> list[Tool]:
    enriched = [tool.model_copy(deep=True) for tool in tools]
    for tool in enriched:
        _add_automatic_hints(tool)
    legacy_overrides = [
        override.model_copy(update={"tool": name})
        for name, override in manifest.risk_overrides.tools.items()
    ]
    selectors = [
        *legacy_overrides,
        *getattr(manifest.risk_overrides, "selectors", []),
    ]
    resolved, enriched = resolve_selectors_by_tool_id(
        enriched,
        selectors,
        manifest_path="/risk_overrides",
        ambiguous_kind="ambiguous_legacy_tool_identity",
        copy_tools=False,
    )
    for tool in enriched:
        override = resolved.get(tool.id)
        if override is not None:
            _apply_manual_override(tool, override)
    return enriched


def has_risk_tag(tool: Tool, tags: Iterable[str], min_confidence: str | None = None) -> bool:
    wanted = set(tags)
    best_confidence = 0
    for hint in tool.risk_hints:
        if hint.tag in wanted:
            best_confidence = max(best_confidence, confidence_rank(hint.confidence))
    if min_confidence:
        return best_confidence >= confidence_rank(min_confidence)
    return best_confidence > 0


def risk_tags(tool: Tool, min_confidence: str | None = None) -> list[str]:
    threshold = confidence_rank(min_confidence) if min_confidence else 0
    return sorted(
        {hint.tag for hint in tool.risk_hints if confidence_rank(hint.confidence) >= threshold}
    )


def is_effectively_read_only(tool: Tool) -> bool:
    # Compatibility projection; effect semantics live only in the resolver.
    from agents_shipgate.core.semantic_assessment import assess_tool_semantics

    assessment = tool.semantic_assessment or assess_tool_semantics(tool)
    return (
        assessment.conservative_effect == "read"
        and assessment.effect.status in {"declared", "structural"}
        and not assessment.effect.issues
    )


def is_high_risk_tool(tool: Tool) -> bool:
    from agents_shipgate.core.semantic_assessment import assess_tool_semantics

    assessment = tool.semantic_assessment or assess_tool_semantics(tool)
    return assessment.conservative_effect in {
        "destructive",
        "external_communication",
        "financial_write",
        "production_operation",
        "privileged_data_access",
        "code_execution",
        "identity_access",
    }


def is_write_tool(tool: Tool) -> bool:
    from agents_shipgate.core.semantic_assessment import assess_tool_semantics

    assessment = tool.semantic_assessment or assess_tool_semantics(tool)
    return assessment.conservative_effect != "read"


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.lower()))


def _add_automatic_hints(tool: Tool) -> None:
    text = f"{tool.name} {tool.description or ''}"
    tokens = _tokenize(text)
    scope_tokens = _tokenize(" ".join(tool.auth.scopes))
    method = str(tool.annotations.get("httpMethod") or "").upper()

    # SDK preview-only safety net runs first so subsequent classifiers can
    # rely on is_effectively_read_only short-circuiting. A function named
    # *_preview that does not declare an HTTP method is treated as read-only
    # at high confidence and exempt from name-keyword write inference.
    is_sdk_preview = tool.source_type == "sdk_function" and "preview" in tokens and not method
    if is_sdk_preview:
        _add_hint(tool, "read_only", "keyword", "high", {"preview_only": True})

    keyword_eligible = tool.source_type in _KEYWORD_GATED_SOURCE_TYPES and not is_sdk_preview
    keyword_source = (
        "openai_api_keyword"
        if tool.source_type == "openai_api"
        else "anthropic_api_keyword"
        if tool.source_type == "anthropic_api"
        else "sdk_keyword"
        if tool.source_type == "sdk_function"
        else "keyword"
    )

    if keyword_eligible and READ_ONLY_KEYWORDS & tokens:
        _add_hint(tool, "read_only", keyword_source, "medium", {})
    if keyword_eligible and WRITE_KEYWORDS & tokens:
        _add_hint(tool, "write", keyword_source, "medium", {})
    if tool.annotations.get("readOnlyHint") is True:
        _add_hint(tool, "read_only", "mcp_annotation", "high", {"readOnlyHint": True})
    if tool.annotations.get("destructiveHint") is True:
        _add_hint(tool, "destructive", "mcp_annotation", "high", {"destructiveHint": True})
        _add_hint(tool, "write", "mcp_annotation", "high", {"destructiveHint": True})

    if method == "DELETE" or DESTRUCTIVE_KEYWORDS & tokens:
        _add_hint(
            tool,
            "destructive",
            # Only DELETE is structural protocol evidence. A risky name or
            # description on GET/POST remains heuristic even though the tool
            # also has an HTTP method; labeling it openapi_method would make
            # the central resolver discard the keyword claim as a duplicate.
            "openapi_method" if method == "DELETE" else "keyword",
            "high" if method == "DELETE" else "medium",
            {"method": method or None},
        )
    if method in {"POST", "PUT", "PATCH", "DELETE"}:
        _add_hint(tool, "write", "openapi_method", "high", {"method": method})

    # Hint generation precedes the one declaration-aware semantic assessment.
    # Calling the full resolver from inside its own input-enrichment phase used
    # to evaluate each tool up to five times and made later hints depend on
    # partially-built semantic output.  These local projections preserve the
    # legacy classifier decisions using only the hints available at this point;
    # the central resolver remains the sole authority for the final effect.
    has_read_hint = has_risk_tag(tool, {"read_only"})
    has_write_hint = has_risk_tag(tool, WRITE_TAGS)
    provisional_conservative_read = has_read_hint and not has_write_hint
    provisional_effectively_read_only = (
        method in {"GET", "HEAD", "OPTIONS"}
        or tool.annotations.get("readOnlyHint") is True
    ) and provisional_conservative_read
    provisional_write = not provisional_conservative_read

    financial_in_text = bool(FINANCIAL_KEYWORDS & tokens)
    financial_in_scope = bool(FINANCIAL_KEYWORDS & scope_tokens)
    if financial_in_text or financial_in_scope:
        if provisional_effectively_read_only:
            confidence = "low"
        elif provisional_write or "write" in scope_tokens:
            confidence = "high"
        else:
            confidence = "low"
        _add_hint(
            tool,
            "financial_action",
            "auth_scope" if financial_in_scope else "keyword",
            confidence,
            {"scopes": tool.auth.scopes, "method": method or None},
        )
    if COMMS_KEYWORDS & (tokens | scope_tokens):
        # The previous staged resolver saw a just-added financial hint as a
        # positive effect before classifying communication; preserve that
        # ordering without performing another assessment.
        comms_write = provisional_write or financial_in_text or financial_in_scope
        comms_read_only = provisional_effectively_read_only and not (
            financial_in_text or financial_in_scope
        )
        confidence = (
            "high" if comms_write else "low" if comms_read_only else "medium"
        )
        _add_hint(tool, "customer_communication", "keyword", confidence, {"method": method or None})
        # Once customer_communication existed, the former staged assessment
        # was no longer effectively read-only, even when its confidence was
        # low. Preserve that exact hint set without invoking the resolver.
        if EXTERNAL_ACTION_KEYWORDS & tokens:
            _add_hint(tool, "external_write", "keyword", confidence, {"method": method or None})
    if SENSITIVE_KEYWORDS & (tokens | scope_tokens):
        _add_hint(tool, "sensitive_data_access", "keyword", "medium", {})
    if CODE_EXEC_KEYWORDS & tokens:
        _add_hint(tool, "code_execution", "keyword", "medium", {})
    if INFRASTRUCTURE_KEYWORDS & tokens:
        _add_hint(tool, "infrastructure_change", "keyword", "medium", {})

    # GET endpoints without any mutation evidence are read-only with high
    # confidence. Bumping from medium to high lets is_effectively_read_only
    # short-circuit policy/scope checks for clear reads, even when they pick
    # up a topical keyword like "kubernetes" elsewhere in the path.
    if method == "GET" and not has_risk_tag(tool, WRITE_TAGS):
        _add_hint(tool, "read_only", "openapi_method", "high", {"method": method})


def _apply_manual_override(tool: Tool, override) -> None:
    if override.owner:
        tool.owner = override.owner
    if override.remove_tags:
        remove = set(override.remove_tags)
        protected = sorted(
            {
                f"{hint.tag} ({hint.source})"
                for hint in tool.risk_hints
                if hint.tag in remove and not _is_removable_heuristic_hint(hint)
            }
        )
        if protected:
            raise ConfigError(
                "risk_overrides selector for "
                f"{tool.name!r} may remove keyword/regex hints only; "
                "structural or manually declared evidence cannot be removed: "
                + ", ".join(protected)
                + "."
            )
        tool.risk_hints = [
            hint
            for hint in tool.risk_hints
            if hint.tag not in remove or not _is_removable_heuristic_hint(hint)
        ]
    for tag in override.tags:
        _add_hint(
            tool,
            tag,
            "manual",
            "high",
            {"reason": override.reason, "confidence": override.confidence},
        )


def _is_removable_heuristic_hint(hint: ToolRiskHint) -> bool:
    source = hint.source.lower()
    return source == "keyword" or source == "regex" or source.endswith("_keyword")


def _add_hint(
    tool: Tool, tag: str, source: str, confidence: str, evidence: dict[str, object]
) -> None:
    confidence_value = confidence if confidence in {"low", "medium", "high"} else "medium"
    for existing in tool.risk_hints:
        if existing.tag == tag and existing.source == source:
            if confidence_rank(confidence_value) > confidence_rank(existing.confidence):
                existing.confidence = parse_confidence(confidence_value)
                existing.evidence.update(evidence)
            return
    tool.risk_hints.append(
        ToolRiskHint(
            tag=tag,
            source=source,
            confidence=parse_confidence(confidence_value),
            evidence={key: value for key, value in evidence.items() if value is not None},
        )
    )


# ---------------------------------------------------------------------------
# Typed accessors for the new ``Scope`` and ``SideEffect`` domain types.
#
# These are additive — every legacy string-based predicate above keeps its
# signature and behavior. The typed views below are derived from the same
# underlying ``tool.risk_hints`` + ``tool.auth.scopes`` + ``tool.annotations``
# data, so they cannot disagree with the string predicates.
# ---------------------------------------------------------------------------


# Risk-tag canonicalization. Several historical synonyms exist in
# ``ToolRiskHint.tag`` (``write`` vs ``writes_data``, ``external_write`` vs
# ``customer_communication`` vs ``external_communication``, etc.) — collapse
# them to a single canonical name so downstream effect inference and
# SideEffect derivation use one vocabulary. This mirrors
# ``core/lenses/action_surface.py::_RISK_TAG_MAP``; the duplication is
# intentional during the migration window, and a follow-up PR can collapse
# the action-surface site onto this constant.
CANONICAL_RISK_TAG_MAP: dict[str, str] = {
    "read_only": "read_only",
    "write": "writes_data",
    "writes_data": "writes_data",
    "external_write": "external_communication",
    "external_communication": "external_communication",
    "customer_communication": "external_communication",
    "financial_action": "financial_write",
    "financial_write": "financial_write",
    "external_side_effect": "external_communication",
    "destructive": "destructive",
    "infrastructure_change": "production_ops",
    "production_operation": "production_ops",
    "production_ops": "production_ops",
    "sensitive_data_access": "privileged_data",
    "privileged_data_access": "privileged_data",
    "privileged_data": "privileged_data",
    "code_execution": "code_execution",
    "identity_access": "identity_access",
    "network_access": "network_access",
    "filesystem_write": "filesystem_write",
    "customer_data": "customer_data",
    "secret_access": "secret_access",
    "irreversible": "irreversible",
    "unknown_side_effect": "unknown_side_effect",
}


def canonical_risk_tags(tool: Tool, min_confidence: str | None = None) -> list[str]:
    """Return ``tool.risk_hints`` tags canonicalized through ``CANONICAL_RISK_TAG_MAP``.

    Always includes ``read_only`` when ``is_effectively_read_only(tool)`` is
    True, so the caller doesn't need to special-case the read-only path.
    """
    threshold = confidence_rank(min_confidence) if min_confidence else 0
    tags = {
        CANONICAL_RISK_TAG_MAP.get(hint.tag, hint.tag)
        for hint in tool.risk_hints
        if confidence_rank(hint.confidence) >= threshold
    }
    if is_effectively_read_only(tool):
        tags.add("read_only")
    return sorted(tags)


def parse_scopes(tool: Tool) -> list[Scope]:
    """Return the tool's declared auth scopes as typed ``Scope`` values.

    Order is preserved from ``tool.auth.scopes`` so wire-shape round-trips
    are predictable. Empty / whitespace-only strings are dropped.
    """
    return [Scope.parse(raw) for raw in tool.auth.scopes if raw and raw.strip()]


def tool_side_effect(tool: Tool) -> SideEffect:
    """Derive a typed ``SideEffect`` from the tool's risk hints + annotations.

    Single source of truth for tool-context effect inference: this
    function and ``core/lenses/action_surface.py::_infer_effect`` must
    agree on the final ``effect`` value for any given tool. The
    structural-field derivation is delegated to ``derive_side_effect``
    so the action-surface path (which may override ``effect`` via a
    manifest declaration) cannot drift from this tool-context path.
    """
    # Local import avoids a module cycle while keeping the resolver as the
    # sole implementation of effect semantics.
    from agents_shipgate.core.semantic_assessment import assess_tool_semantics

    tags = set(canonical_risk_tags(tool))
    assessment = tool.semantic_assessment or assess_tool_semantics(tool)
    effect = assessment.conservative_effect
    return derive_side_effect(
        effect=effect,
        risk_tags=tags,
        idempotency_known=_derive_idempotency_known(tool),
    )


def derive_side_effect(
    *,
    effect: ActionEffect,
    risk_tags: list[str] | set[str],
    idempotency_known: bool | None = None,
) -> SideEffect:
    """Single source of truth for ``SideEffect`` construction.

    Used by ``tool_side_effect`` (tool-context path, where ``effect`` is
    derived purely from tags) AND
    ``core/lenses/action_surface.py::build_action`` (manifest-context
    path, where ``effect`` may come from a user declaration). Routing
    both paths through this helper guarantees that the structural
    fields cannot contradict ``effect`` — a manifest declaring
    ``effect: financial_write`` with no matching ``risk_tags`` still
    yields ``SideEffect(financial=True, externally_visible=True,
    is_high_risk=True)``.

    Structural-field derivation rules (applied in order):

    - ``externally_visible`` — True if any of ``external_communication``
      / ``writes_data`` is in ``risk_tags``, OR ``effect`` is in
      ``{destructive, financial_write, external_communication}``.
    - ``handles_sensitive_data`` — True if ``risk_tags`` contains
      ``privileged_data`` or ``secret_access``, OR ``effect`` is
      ``privileged_data_access``.
    - ``financial`` — True if ``risk_tags`` contains ``financial_write``
      OR ``effect`` is ``financial_write``.
    - ``code_execution`` — True if ``risk_tags`` contains
      ``code_execution`` OR ``effect`` is ``code_execution``.
    - ``reversibility`` — ``irreversible`` if ``risk_tags`` contains
      ``destructive`` / ``irreversible`` OR ``effect`` is
      ``destructive``; ``reversible`` if ``risk_tags`` contains
      ``read_only`` and not ``writes_data``; ``unknown`` otherwise.
      (A declared ``effect: read`` *without* a ``read_only`` tag stays
      ``unknown`` — declared-read doesn't promise reversibility on its
      own; positive evidence is needed.)
    """
    tag_set: set[str] = set(risk_tags) if not isinstance(risk_tags, set) else risk_tags
    return SideEffect(
        effect=effect,
        externally_visible=(
            "external_communication" in tag_set
            or "writes_data" in tag_set
            or effect in {"destructive", "financial_write", "external_communication"}
        ),
        handles_sensitive_data=(
            "privileged_data" in tag_set
            or "secret_access" in tag_set
            or effect == "privileged_data_access"
        ),
        financial=("financial_write" in tag_set or effect == "financial_write"),
        code_execution=("code_execution" in tag_set or effect == "code_execution"),
        reversibility=(
            "irreversible"
            if ("destructive" in tag_set or "irreversible" in tag_set or effect == "destructive")
            else "reversible"
            if "read_only" in tag_set and "writes_data" not in tag_set
            else "unknown"
        ),
        idempotency_known=idempotency_known,
    )


def _derive_effect(tags: set[str], method: str) -> ActionEffect:
    """Effect-tier inference.

    Mirrors ``core/lenses/action_surface.py::_infer_effect`` exactly so
    Phase C can delete the duplicate. The ordering is significant —
    destructive > financial_write > external_communication > production_ops
    > code_execution > identity_access > privileged_data_access > write
    > read — and matches the documented severity escalation rules.
    """
    if "destructive" in tags:
        return "destructive"
    if "financial_write" in tags:
        return "financial_write"
    if "external_communication" in tags:
        return "external_communication"
    if "production_ops" in tags:
        return "production_operation"
    if "code_execution" in tags:
        return "code_execution"
    if "identity_access" in tags:
        return "identity_access"
    if "privileged_data" in tags:
        return "privileged_data_access"
    if "unknown_side_effect" in tags:
        return "write"
    if "writes_data" in tags:
        return "write"
    if method in {"POST", "PUT", "PATCH"}:
        return "write"
    if method == "DELETE":
        return "destructive"
    return "write"


def _derive_idempotency_known(tool: Tool) -> bool | None:
    """Three-state: True / False / None (unknown).

    ``True`` when an idempotency signal is declared (hint, key parameter,
    explicit annotation); ``False`` is reserved for cases where we
    know the tool is *not* idempotent (none today — kept for future
    runtime evidence); ``None`` otherwise.
    """
    if tool.annotations.get("idempotentHint") is True:
        return True
    if any(parameter.name == "idempotency_key" for parameter in tool.parameters):
        return True
    return None
