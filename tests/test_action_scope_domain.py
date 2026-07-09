"""Pinning tests for the typed ``Scope`` / ``SideEffect`` / ``Action`` types
added to ``core.domain`` and the typed accessors in ``core.risk_hints``.

These cover three contracts:

1. ``Scope.parse`` is permissive (never raises) and produces structural
   parts for the common provider conventions (Stripe / AWS / GitHub /
   OpenAI / wildcards / broad tokens).
2. ``tool_side_effect`` produces a ``SideEffect`` whose ``effect`` agrees
   byte-for-byte with the legacy ``_infer_effect`` over the same tool —
   so swapping the typed accessor into ``report/action_surface_diff.py``
   does not move the wire-format ``ActionFact.effect``.
3. ``build_action`` → ``action_to_fact`` produces an ``ActionFact`` that
   is byte-equal to the legacy ``_action_from_tool`` body. The whole
   point of the typed-Action refactor is that wire output is unchanged.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agents_shipgate.core.domain import (
    Action,
    AuthInfo,
    Scope,
    SideEffect,
    Tool,
    ToolParameter,
    ToolRiskHint,
)
from agents_shipgate.core.lenses.action_surface import (
    _infer_effect,
    _normalized_risk_tags,
    action_to_fact,
    build_action,
)
from agents_shipgate.core.risk_hints import (
    canonical_risk_tags,
    is_high_risk_tool,
    parse_scopes,
    tool_side_effect,
)
from agents_shipgate.schemas.manifest import (
    ActionDeclarationConfig,
    AgentsShipgateManifest,
)


def _tool(
    name: str,
    *,
    source: str = "openapi",
    annotations: dict | None = None,
    scopes: list[str] | None = None,
    hints: list[tuple[str, str]] | None = None,
    parameters: list[str] | None = None,
    description: str | None = None,
) -> Tool:
    return Tool(
        id=f"t_{name}",
        name=name,
        description=description,
        source_type=source,
        annotations=annotations or {},
        auth=AuthInfo(scopes=scopes or []),
        risk_hints=[
            ToolRiskHint(tag=tag, source="test", confidence=conf)
            for tag, conf in (hints or [])
        ],
        parameters=[ToolParameter(name=p) for p in (parameters or [])],
        extraction_confidence="high",
    )


# --- Scope parsing --------------------------------------------------------


@pytest.mark.parametrize(
    "raw,provider,resource,verb",
    [
        # Stripe-style 3-part
        ("stripe:refunds:write", "stripe", "refunds", "write"),
        # AWS-style 2-part triple (service:action with wildcard resource)
        ("s3:bucket:*", "s3", "bucket", "*"),
        # GitHub-style 2-part with non-verb tail
        ("repo:status", "repo", "status", None),
        # OpenAI / dot-separated
        ("models.read", "models", None, "read"),
        ("workspace.users.write", "workspace", "users", "write"),
        # Path-style fallback
        ("aws/s3/get", "aws", "s3", "get"),
        # Wildcard handling — never a verb
        ("stripe:*", "stripe", "*", None),
        # Broad tokens — no structural parts
        ("admin", None, None, None),
        ("*", None, None, None),
        # 4+ parts collapse middle into resource
        ("stripe:refunds:items:write", "stripe", "refunds:items", "write"),
        # Single token
        ("github_token", "github_token", None, None),
        # Empty
        ("", None, None, None),
    ],
)
def test_scope_parse(raw: str, provider: str | None, resource: str | None, verb: str | None) -> None:
    parsed = Scope.parse(raw)
    assert parsed.raw == raw
    assert parsed.provider == provider
    assert parsed.resource == resource
    assert parsed.verb == verb
    assert str(parsed) == raw


def test_scope_parse_strips_whitespace() -> None:
    # Leading/trailing whitespace is normalized off the raw string so
    # downstream string comparisons (scope coverage diff) stay stable.
    parsed = Scope.parse("  stripe:refunds:write  ")
    assert parsed.raw == "stripe:refunds:write"
    assert parsed.provider == "stripe"


def test_scope_parse_handles_none_safely() -> None:
    # The parser is documented as "never raises" — including on None.
    assert Scope.parse(None).raw == ""  # type: ignore[arg-type]


def test_scope_is_broad_matches_heuristics_module() -> None:
    # ``Scope.is_broad`` delegates to ``core.heuristics.is_broad_scope``
    # so this asserts behavioral parity, not just method existence.
    from agents_shipgate.core.heuristics import is_broad_scope

    for raw in ["admin", "*", "stripe:*", "s3:bucket:*", "admin:read", "/admin/"]:
        assert Scope.parse(raw).is_broad() is is_broad_scope(raw), raw

    for raw in ["stripe:refunds:write", "repo:status", "models.read"]:
        assert Scope.parse(raw).is_broad() is is_broad_scope(raw), raw


def test_scope_is_read_write_classifiers() -> None:
    assert Scope.parse("stripe:refunds:read").is_read()
    assert not Scope.parse("stripe:refunds:read").is_write()
    assert Scope.parse("stripe:refunds:write").is_write()
    assert not Scope.parse("stripe:refunds:write").is_read()
    assert Scope.parse("email:send").is_write()
    # Wildcard is NOT a verb — both classifiers return False.
    assert not Scope.parse("stripe:*").is_read()
    assert not Scope.parse("stripe:*").is_write()
    # Unknown verb tokens stay False — be permissive, don't guess.
    assert not Scope.parse("stripe:refunds:explode").is_write()


def test_scope_is_frozen() -> None:
    parsed = Scope.parse("stripe:refunds:write")
    # ``model_config = ConfigDict(frozen=True)`` → Pydantic raises
    # ValidationError on attribute set.
    with pytest.raises(ValidationError):
        parsed.raw = "mutated"  # type: ignore[misc]


def test_parse_scopes_preserves_order_and_drops_blanks() -> None:
    tool = _tool("t", scopes=["stripe:refunds:write", "  ", "stripe:*", ""])
    parsed = parse_scopes(tool)
    assert [s.raw for s in parsed] == ["stripe:refunds:write", "stripe:*"]
    assert parsed[1].is_broad()


# --- SideEffect derivation ------------------------------------------------


def test_side_effect_high_risk_classifier() -> None:
    assert SideEffect(effect="destructive").is_high_risk
    assert SideEffect(effect="financial_write").is_high_risk
    assert SideEffect(effect="external_communication").is_high_risk
    assert SideEffect(effect="code_execution").is_high_risk
    assert SideEffect(effect="identity_access").is_high_risk
    assert SideEffect(effect="production_operation").is_high_risk
    assert not SideEffect(effect="read").is_high_risk
    assert not SideEffect(effect="write").is_high_risk
    # Structural overrides — financial / code_execution flags promote
    # to high_risk even when ``effect`` doesn't.
    assert SideEffect(effect="write", financial=True).is_high_risk
    assert SideEffect(effect="write", code_execution=True).is_high_risk


def test_side_effect_is_frozen() -> None:
    se = SideEffect(effect="write")
    with pytest.raises(ValidationError):
        se.financial = True  # type: ignore[misc]


# --- Typed accessor parity with legacy logic ------------------------------

# Every case below covers a code path in ``_normalized_risk_tags`` and
# ``_infer_effect``. The parity guarantee — typed accessor returns the
# same value as the legacy helper — is what unblocks deleting the
# duplicate logic in a future PR. If either side drifts, this test
# catches it immediately.

PARITY_TOOLS = [
    _tool("read_get", annotations={"httpMethod": "GET"}),
    _tool(
        "refund_post",
        annotations={"httpMethod": "POST"},
        hints=[("financial_action", "high"), ("write", "high")],
    ),
    _tool(
        "destroy",
        annotations={"httpMethod": "DELETE"},
        hints=[("destructive", "high"), ("write", "high")],
    ),
    _tool(
        "send_email",
        hints=[("customer_communication", "medium"), ("external_write", "medium")],
    ),
    _tool("exec_cmd", hints=[("code_execution", "medium")]),
    _tool("infra_apply", hints=[("infrastructure_change", "medium")]),
    _tool(
        "read_only_get",
        annotations={"readOnlyHint": True, "httpMethod": "GET"},
    ),
    _tool(
        "with_idem_key",
        annotations={"httpMethod": "POST"},
        hints=[("write", "high")],
        parameters=["idempotency_key", "amount"],
    ),
    _tool(
        "with_idem_hint",
        annotations={"httpMethod": "POST", "idempotentHint": True},
        hints=[("write", "high")],
    ),
    _tool("sensitive", hints=[("sensitive_data_access", "medium")]),
]


@pytest.mark.parametrize("tool", PARITY_TOOLS, ids=lambda t: t.name)
def test_canonical_risk_tags_matches_legacy(tool: Tool) -> None:
    assert canonical_risk_tags(tool) == _normalized_risk_tags(tool)


@pytest.mark.parametrize("tool", PARITY_TOOLS, ids=lambda t: t.name)
def test_tool_side_effect_effect_matches_legacy(tool: Tool) -> None:
    legacy = _infer_effect(tool, _normalized_risk_tags(tool))
    typed = tool_side_effect(tool).effect
    assert typed == legacy


def test_tool_side_effect_populates_structural_fields() -> None:
    se = tool_side_effect(
        _tool(
            "refund",
            annotations={"httpMethod": "POST"},
            hints=[("financial_action", "high"), ("write", "high")],
        )
    )
    assert se.effect == "financial_write"
    assert se.financial is True
    assert se.externally_visible is True
    assert se.is_high_risk

    # A structural GET is pass-eligible read evidence. Canonical risk tags are
    # now a projection of the central assessment, so the typed side effect is
    # consistently reversible even before legacy hint enrichment runs.
    se_read_plain = tool_side_effect(_tool("read_plain", annotations={"httpMethod": "GET"}))
    assert se_read_plain.effect == "read"
    assert not se_read_plain.is_high_risk
    assert se_read_plain.reversibility == "reversible"

    # MCP-style readOnlyHint promotes ``is_effectively_read_only`` to
    # True, which adds ``read_only`` to canonical tags, which yields
    # ``reversibility="reversible"``.
    se_read_hint = tool_side_effect(
        _tool("read_hint", annotations={"readOnlyHint": True, "httpMethod": "GET"})
    )
    assert se_read_hint.effect == "read"
    assert se_read_hint.reversibility == "reversible"

    se_destr = tool_side_effect(
        _tool("destroy", annotations={"httpMethod": "DELETE"}, hints=[("destructive", "high")])
    )
    assert se_destr.reversibility == "irreversible"


def test_tool_side_effect_idempotency_three_state() -> None:
    # Known True via idempotency_key parameter
    se_param = tool_side_effect(_tool("a", parameters=["idempotency_key"]))
    assert se_param.idempotency_known is True
    # Known True via annotation
    se_anno = tool_side_effect(_tool("b", annotations={"idempotentHint": True}))
    assert se_anno.idempotency_known is True
    # Unknown — neither signal present
    se_unknown = tool_side_effect(_tool("c"))
    assert se_unknown.idempotency_known is None


# --- Action ↔ ActionFact equivalence --------------------------------------

# The whole refactor's invariant: ``action_to_fact(build_action(...))``
# produces an ``ActionFact`` that's byte-equal to what the legacy
# ``_action_from_tool`` body produced. Because ``_action_from_tool`` now
# delegates to these two helpers, this test pins the round-trip through
# the typed Action.


def _empty_manifest() -> AgentsShipgateManifest:
    # Mirrors the canonical minimal-manifest shape used by
    # ``tests/test_action_surface_diff.py::_manifest`` so the typed-action
    # tests exercise build_action against the same manifest fields. The
    # ``tool_sources`` placeholder is required by ``AgentsShipgateManifest``'s
    # "at least one input source" model_validator; tests don't actually
    # load it.
    return AgentsShipgateManifest.model_validate(
        {
            "version": "0.1",
            "project": {"name": "action-scope-domain-test"},
            "agent": {"name": "agent", "declared_purpose": ["test typed action"]},
            "environment": {"target": "production_like"},
            "tool_sources": [{"id": "tools", "type": "mcp", "path": "tools.json"}],
        }
    )


def test_build_action_then_action_to_fact_is_pure_function() -> None:
    """Same input → same Action → same ActionFact, twice."""
    manifest = _empty_manifest()
    tool = _tool(
        "stripe.create_refund",
        scopes=["stripe:refunds:write", "stripe:*"],
        annotations={"httpMethod": "POST"},
        hints=[("financial_action", "high"), ("write", "high")],
        parameters=["amount", "idempotency_key"],
    )
    action_a = build_action(manifest, agent_id="agent-1", tool=tool, declaration=None)
    action_b = build_action(manifest, agent_id="agent-1", tool=tool, declaration=None)
    assert action_a.model_dump() == action_b.model_dump()
    fact_a = action_to_fact(action_a)
    fact_b = action_to_fact(action_b)
    assert fact_a.model_dump() == fact_b.model_dump()


def test_build_action_typed_fields_match_action_fact() -> None:
    """The typed Action surfaces match the ActionFact serialization."""
    manifest = _empty_manifest()
    tool = _tool(
        "stripe.create_refund",
        scopes=["stripe:refunds:write", "stripe:*"],
        annotations={"httpMethod": "POST"},
        hints=[("financial_action", "high"), ("write", "high")],
        parameters=["amount", "idempotency_key"],
    )
    action = build_action(manifest, agent_id="agent-1", tool=tool, declaration=None)
    fact = action_to_fact(action)

    # Effect — single source of truth
    assert action.effect == fact.effect
    # Scopes — typed list vs wire list
    assert action.scope_strings == fact.required_scopes
    # Risk tags — wire format is identical to the action's list
    assert action.risk_tags == fact.risk_tags
    # Approval / safeguards / evidence — typed dataclass-like fields vs
    # the nested wire models.
    assert action.approval_required == fact.approval_policy.required
    assert action.safeguard_idempotency == fact.safeguards.idempotency
    # The typed Action recognizes the broad scope.
    assert action.has_broad_scope is True


def test_action_to_fact_preserves_action_id() -> None:
    manifest = _empty_manifest()
    tool = _tool("explicit_provider_tool")
    action = build_action(manifest, agent_id="agent-z", tool=tool, declaration=None)
    fact = action_to_fact(action)
    assert fact.action_id.startswith("agent-z:")
    assert fact.action_id == action.action_id


def test_action_is_extra_forbid() -> None:
    """Action is ``extra='forbid'`` — typo-catching guarantee for callers."""
    with pytest.raises(ValidationError):
        Action(
            action_id="x",
            agent_id="x",
            tool_id="x",
            tool_name="x",
            provider="x",
            source_type="x",
            operation="x",
            side_effect=SideEffect(effect="read"),
            risk_tagz=[],  # type: ignore[call-arg]  # intentional typo: tests extra=forbid
        )


# --- is_high_risk parity with legacy ``is_high_risk_tool`` ----------------

# Review finding: if checks migrate from ``is_high_risk_tool(tool)`` to
# ``tool_side_effect(tool).is_high_risk``, sensitive/privileged-data tools
# must not silently lose high-risk classification. The legacy
# ``HIGH_RISK_TAGS`` includes ``sensitive_data_access``; the typed
# classifier must agree.


@pytest.mark.parametrize("tool", PARITY_TOOLS, ids=lambda t: t.name)
def test_side_effect_is_high_risk_matches_legacy(tool: Tool) -> None:
    """For every PARITY_TOOLS case, the typed and legacy classifiers agree."""
    legacy = is_high_risk_tool(tool)
    typed = tool_side_effect(tool).is_high_risk
    assert typed == legacy, f"{tool.name}: legacy={legacy} typed={typed}"


def test_side_effect_is_high_risk_covers_sensitive_data() -> None:
    """Sensitive-data tools land as high-risk under both predicates.

    Pre-fix the typed classifier returned False for
    ``effect="privileged_data_access"`` — that drift would have let
    migrated checks stop requiring high-risk controls on sensitive tools.
    """
    tool = _tool("sensitive", hints=[("sensitive_data_access", "medium")])
    assert is_high_risk_tool(tool) is True
    se = tool_side_effect(tool)
    assert se.handles_sensitive_data is True
    assert se.effect == "privileged_data_access"
    assert se.is_high_risk is True


def test_side_effect_is_high_risk_via_structural_field_when_effect_lower() -> None:
    """A tool whose declared effect is ``write`` but which handles
    sensitive data still classifies as high-risk via the structural
    ``handles_sensitive_data`` field."""
    se = SideEffect(effect="write", handles_sensitive_data=True)
    assert se.is_high_risk is True
    se2 = SideEffect(effect="write", financial=True)
    assert se2.is_high_risk is True
    se3 = SideEffect(effect="write", code_execution=True)
    assert se3.is_high_risk is True
    # Plain write with no structural escalators stays low-risk.
    assert SideEffect(effect="write").is_high_risk is False


# --- Declaration-only SideEffect derivation (review finding #2) -----------

# When a manifest declares ``action_surface.actions[].effect`` *without*
# matching ``risk_tags``, ``build_action`` previously derived ``effect``
# from the declaration but derived structural fields only from tags —
# producing a contradictory ``SideEffect`` (e.g. ``effect=financial_write``
# with ``financial=False``). The fix routes both call sites through the
# shared ``derive_side_effect`` helper which feeds ``effect`` into every
# structural field.


def _tool_for_declaration() -> Tool:
    """Plain GET tool with no risk_hints — leaves the inferred surface
    empty so any structural derivation in ``SideEffect`` must come from
    the declaration's ``effect`` field."""
    return _tool("declared_only_tool", annotations={"httpMethod": "GET"})


def test_build_action_declaration_only_financial_write() -> None:
    """Manifest declaration ``effect: financial_write`` with no
    ``risk_tags`` still yields ``financial=True``,
    ``externally_visible=True``, and ``is_high_risk=True``."""
    manifest = _empty_manifest()
    tool = _tool_for_declaration()
    declaration = ActionDeclarationConfig(tool=tool.name, effect="financial_write")
    action = build_action(manifest, agent_id="agent-1", tool=tool, declaration=declaration)
    assert action.effect == "financial_write"
    assert action.side_effect.financial is True
    assert action.side_effect.externally_visible is True
    assert action.side_effect.is_high_risk is True


def test_build_action_declaration_only_destructive() -> None:
    """Manifest declaration ``effect: destructive`` with no destructive
    tag yields ``reversibility="irreversible"`` and ``is_high_risk=True``."""
    manifest = _empty_manifest()
    tool = _tool_for_declaration()
    declaration = ActionDeclarationConfig(tool=tool.name, effect="destructive")
    action = build_action(manifest, agent_id="agent-1", tool=tool, declaration=declaration)
    assert action.effect == "destructive"
    assert action.side_effect.reversibility == "irreversible"
    assert action.side_effect.is_high_risk is True


def test_build_action_declaration_only_code_execution() -> None:
    """Declaration ``effect: code_execution`` lights up the structural
    code_execution flag even without a matching tag."""
    manifest = _empty_manifest()
    tool = _tool_for_declaration()
    declaration = ActionDeclarationConfig(tool=tool.name, effect="code_execution")
    action = build_action(manifest, agent_id="agent-1", tool=tool, declaration=declaration)
    assert action.effect == "code_execution"
    assert action.side_effect.code_execution is True
    assert action.side_effect.is_high_risk is True


def test_build_action_declaration_only_privileged_data_access() -> None:
    """Declaration ``effect: privileged_data_access`` lights up
    ``handles_sensitive_data`` even without a sensitive tag."""
    manifest = _empty_manifest()
    tool = _tool_for_declaration()
    declaration = ActionDeclarationConfig(tool=tool.name, effect="privileged_data_access")
    action = build_action(manifest, agent_id="agent-1", tool=tool, declaration=declaration)
    assert action.effect == "privileged_data_access"
    assert action.side_effect.handles_sensitive_data is True
    assert action.side_effect.is_high_risk is True


# --- Wildcard slotting contract (review open question) --------------------

# The parser slots wildcards by *position*, not by axis:
# - 2-part ``provider:*`` puts the wildcard in the resource axis
#   (2-part scopes have no canonical verb position).
# - 3+-part ``provider:resource:*`` puts the wildcard in the verb axis
#   (AWS IAM convention: trailing ``*`` is the action wildcard).
# Both forms return ``is_broad()=True`` and ``is_read()/is_write()=False``.


def test_wildcard_slotting_two_part_lands_in_resource() -> None:
    parsed = Scope.parse("stripe:*")
    assert parsed.provider == "stripe"
    assert parsed.resource == "*"
    assert parsed.verb is None
    assert parsed.is_broad() is True
    assert parsed.is_read() is False
    assert parsed.is_write() is False


def test_wildcard_slotting_three_part_lands_in_verb() -> None:
    parsed = Scope.parse("s3:bucket:*")
    assert parsed.provider == "s3"
    assert parsed.resource == "bucket"
    assert parsed.verb == "*"
    assert parsed.is_broad() is True
    # Wildcard is NOT a canonical action verb — read/write classifiers
    # return False so least-privilege gating treats it as ambiguous.
    assert parsed.is_read() is False
    assert parsed.is_write() is False


def test_wildcard_slotting_four_plus_part_keeps_verb_wildcard() -> None:
    parsed = Scope.parse("aws:iam:role:*")
    assert parsed.provider == "aws"
    assert parsed.resource == "iam:role"
    assert parsed.verb == "*"
    assert parsed.is_broad() is True
