from __future__ import annotations

import pytest

from agents_shipgate.cli.scan import run_scan
from agents_shipgate.core.baseline import apply_baseline
from agents_shipgate.core.domain import AuthInfo, LoadedToolSource, Tool
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.core.findings.identity import finding_fingerprint, legacy_name_fingerprint
from agents_shipgate.core.findings.mutations import apply_suppressions
from agents_shipgate.core.semantic_assessment import attach_semantic_assessments
from agents_shipgate.core.tool_identity import (
    ToolSelectorIndex,
    build_tool_identity_catalog,
    resolve_selectors_by_tool_id,
    resolve_tool_selector,
)
from agents_shipgate.schemas.baseline import BaselineFile, BaselineFinding
from agents_shipgate.schemas.manifest import (
    ActionDeclarationConfig,
    SuppressionConfig,
    ToolIdentityBindingConfig,
    ToolIdentityConfig,
)
from agents_shipgate.schemas.report import Finding


def _source(source_id: str, *, scope: str, effect: str) -> LoadedToolSource:
    annotations = {"readOnlyHint": True} if effect == "read" else {"destructiveHint": True}
    return LoadedToolSource(
        source_id=source_id,
        source_type="mcp",
        tools=[
            Tool(
                id="tool:process_order",
                name="process_order",
                source_type="mcp",
                source_id=source_id,
                annotations=annotations,
                auth=AuthInfo(type="oauth2", scopes=[scope], mode="scoped"),
                extraction_confidence="high",
            )
        ],
    )


def test_same_name_providers_remain_distinct_without_evidence_leakage() -> None:
    tools, warnings = build_tool_identity_catalog(
        [
            _source("orders_a", scope="orders:read", effect="read"),
            _source("orders_b", scope="orders:delete", effect="destructive"),
        ],
        ToolIdentityConfig(),
    )

    assert warnings == []
    assert len(tools) == 2
    assert len({tool.id for tool in tools}) == 2
    by_provider = {tool.provider: tool for tool in tools}
    assert by_provider["orders_a"].auth.scopes == ["orders:read"]
    assert by_provider["orders_a"].annotations == {"readOnlyHint": True}
    assert by_provider["orders_b"].auth.scopes == ["orders:delete"]
    assert by_provider["orders_b"].annotations == {"destructiveHint": True}


def test_adding_and_removing_same_name_provider_keeps_existing_identity_stable() -> None:
    only_a, _ = build_tool_identity_catalog(
        [_source("orders_a", scope="orders:read", effect="read")],
        ToolIdentityConfig(),
    )
    with_b, _ = build_tool_identity_catalog(
        [
            _source("orders_a", scope="orders:read", effect="read"),
            _source("orders_b", scope="orders:delete", effect="destructive"),
        ],
        ToolIdentityConfig(),
    )
    a_with_b = next(tool for tool in with_b if tool.provider == "orders_a")

    assert only_a[0].id == a_with_b.id
    assert only_a[0].observation_id == a_with_b.observation_id


def test_identity_catalog_is_input_order_independent() -> None:
    sources = [
        _source("orders_a", scope="orders:read", effect="read"),
        _source("orders_b", scope="orders:delete", effect="destructive"),
    ]
    forward, _ = build_tool_identity_catalog(sources, ToolIdentityConfig())
    reverse, _ = build_tool_identity_catalog(list(reversed(sources)), ToolIdentityConfig())

    assert [tool.model_dump(mode="json") for tool in forward] == [
        tool.model_dump(mode="json") for tool in reverse
    ]


def test_bare_name_selector_applies_nowhere_and_prevents_pass() -> None:
    tools, _ = build_tool_identity_catalog(
        [
            _source("orders_a", scope="orders:read", effect="read"),
            _source("orders_b", scope="orders:read", effect="read"),
        ],
        ToolIdentityConfig(),
    )
    declaration = ActionDeclarationConfig(
        tool="process_order",
        effect="read",
        authority={"mode": "scoped", "auth_type": "oauth2"},
        scopes=["orders:read"],
    )
    resolved, tools = resolve_selectors_by_tool_id(
        tools,
        [declaration],
        manifest_path="/action_surface/actions",
    )
    assessed = attach_semantic_assessments(tools, resolved)

    assert resolved == {}
    assert all(not tool.semantic_assessment.pass_eligible for tool in assessed)
    assert all(
        any(issue.kind == "ambiguous_tool_selector" for issue in tool.identity_assessment.issues)
        for tool in assessed
    )


def test_qualified_selector_resolves_exactly_one_provider() -> None:
    tools, _ = build_tool_identity_catalog(
        [
            _source("orders_a", scope="orders:read", effect="read"),
            _source("orders_b", scope="orders:read", effect="read"),
        ],
        ToolIdentityConfig(),
    )
    selector = {"tool": "process_order", "source_id": "orders_b"}
    result = resolve_tool_selector(tools, selector)

    assert result.resolved
    assert result.matches[0].provider == "orders_b"


def test_reviewed_binding_is_the_only_cross_source_merge_path() -> None:
    config = ToolIdentityConfig(
        bindings=[
            ToolIdentityBindingConfig(
                id="orders_process",
                provider="orders_runtime",
                reason="reviewed export and framework declaration are the same mount",
                primary={"source_id": "orders_a", "tool": "process_order"},
                members=[
                    {"source_id": "orders_a", "tool": "process_order"},
                    {"source_id": "orders_b", "tool": "process_order"},
                ],
            )
        ]
    )
    tools, warnings = build_tool_identity_catalog(
        [
            _source("orders_a", scope="orders:read", effect="read"),
            _source("orders_b", scope="orders:read", effect="read"),
        ],
        config,
    )

    assert warnings == []
    assert len(tools) == 1
    assert tools[0].provider == "orders_runtime"
    assert tools[0].identity_assessment.binding_id == "orders_process"
    assert len(tools[0].observation_ids) == 2


def test_duplicate_observation_tuple_is_rejected() -> None:
    duplicate = _source("orders", scope="orders:read", effect="read")
    duplicate.tools.append(duplicate.tools[0].model_copy(deep=True))

    try:
        build_tool_identity_catalog([duplicate], ToolIdentityConfig())
    except InputParseError as exc:
        assert "Duplicate tool observation identity" in str(exc)
    else:  # pragma: no cover - explicit safety assertion
        raise AssertionError("duplicate observation tuple was accepted")


def test_fingerprint_v2_and_legacy_baseline_do_not_cross_providers() -> None:
    findings = [
        Finding(
            check_id="SHIP-X",
            title="same finding",
            severity="medium",
            category="test",
            tool_id=f"tool_v2_{provider}",
            tool_name="process_order",
            evidence={"field": "value"},
            recommendation="review",
        )
        for provider in ("a", "b")
    ]
    for finding in findings:
        finding.fingerprint = finding_fingerprint(finding)
    assert findings[0].fingerprint != findings[1].fingerprint

    legacy = legacy_name_fingerprint(findings[0])
    baseline = BaselineFile(
        created_at="2026-01-01T00:00:00Z",
        source_report_run_id="legacy",
        findings=[
            BaselineFinding(
                fingerprint=legacy,
                check_id="SHIP-X",
                tool_name="process_order",
                severity="medium",
                title="same finding",
            )
        ],
    )
    apply_baseline(
        findings,
        baseline,
        display_path="baseline.json",
        legacy_fingerprints=[legacy, legacy],
    )

    assert [finding.baseline_status for finding in findings] == ["new", "new"]


def test_ambiguous_name_suppression_applies_nowhere() -> None:
    tools, _ = build_tool_identity_catalog(
        [
            _source("orders_a", scope="orders:read", effect="read"),
            _source("orders_b", scope="orders:read", effect="read"),
        ],
        ToolIdentityConfig(),
    )
    findings = [
        Finding(
            check_id="SHIP-X",
            title="same finding",
            severity="medium",
            category="test",
            tool_id=tool.id,
            tool_name=tool.name,
            recommendation="review",
        )
        for tool in tools
    ]

    apply_suppressions(
        findings,
        [SuppressionConfig(check_id="SHIP-X", tool="process_order", reason="legacy debt")],
        tools,
    )

    assert not any(finding.suppressed for finding in findings)


def test_stale_suppression_routes_to_catalog_review_not_identity_gap(tmp_path) -> None:
    (tmp_path / "tools.json").write_text(
        """{
  "tools": [
    {
      "name": "lookup_case",
      "description": "Look up one support case without modifying it.",
      "annotations": {"readOnlyHint": true},
      "auth": {"mode": "none"},
      "inputSchema": {"type": "object", "properties": {}}
    }
  ]
}
""",
        encoding="utf-8",
    )
    (tmp_path / "shipgate.yaml").write_text(
        """version: "0.1"
project: {name: stale-suppression}
agent:
  name: stale-suppression-agent
  declared_purpose: [look up support cases]
environment: {target: local}
tool_sources:
  - id: support
    type: mcp
    path: tools.json
agent_bindings:
  declarations:
    - agent: root
      complete: true
      tools:
        - {tool: lookup_case, source_id: support}
      handoffs: []
      reason: reviewed stale-suppression fixture binding
checks:
  ignore:
    - check_id: SHIP-DOC-MISSING-DESCRIPTION
      tool: removed_tool
      reason: historical debt for a removed capability
""",
        encoding="utf-8",
    )

    report, exit_code = run_scan(
        config_path=tmp_path / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        packet_enabled=False,
    )

    assert exit_code == 0
    assert report.release_decision.decision == "review_required"
    assert any(
        finding.check_id == "SHIP-MANIFEST-STALE-SUPPRESSION"
        for finding in report.findings
    )
    assert report.release_decision.evidence_coverage.semantic_coverage.pass_eligible_actions == 1
    assert report.release_decision.evidence_coverage.identity_coverage.gap_count == 0


@pytest.mark.parametrize("safe_provider_count", [1, 2, 10, 100])
def test_one_ambiguous_same_name_selector_cannot_be_diluted(
    safe_provider_count: int,
) -> None:
    sources = [
        _source(f"orders_{index:03d}", scope="orders:read", effect="read")
        for index in range(safe_provider_count + 1)
    ]
    tools, _ = build_tool_identity_catalog(sources, ToolIdentityConfig())
    declaration = ActionDeclarationConfig(
        tool="process_order",
        effect="read",
        authority={"mode": "scoped", "auth_type": "oauth2"},
        scopes=["orders:read"],
    )
    resolved, tools = resolve_selectors_by_tool_id(
        tools,
        [declaration],
        manifest_path="/action_surface/actions",
    )
    assessed = attach_semantic_assessments(tools, resolved)

    assert resolved == {}
    assert not any(tool.semantic_assessment.pass_eligible for tool in assessed)


# --- #386: an inventory completes a source instead of shadowing it -----------


def _adk_source(source_id: str, *names: str) -> LoadedToolSource:
    """A statically extracted ADK source: medium confidence, AST-only type."""

    return LoadedToolSource(
        source_id=source_id,
        source_type="google_adk",
        tools=[
            Tool(
                id=f"tool:{name}",
                name=name,
                source_type="google_adk_function",
                source_id=source_id,
                source_ref="agent.py",
                extraction_confidence="medium",
            )
            for name in names
        ],
    )


def _adk_inventory(
    path: str, *names: str, completes: str | None = None
) -> LoadedToolSource:
    """A reviewed inventory as the ADK adapter loads one."""

    source_id = f"google_adk_inventory:{path}"
    return LoadedToolSource(
        source_id=source_id,
        source_type="google_adk_inventory",
        is_tool_inventory=True,
        completes_source_id=completes,
        tools=[
            Tool(
                id=f"inv:{name}",
                name=name,
                source_type="google_adk_inventory",
                source_id=source_id,
                source_ref=path,
                annotations={"readOnlyHint": True},
                extraction_confidence="high",
            )
            for name in names
        ],
    )


def test_inventory_completing_a_source_does_not_grow_the_catalog() -> None:
    """#386 acceptance 2: the prescribed fix must not add duplicate entries."""

    tools, warnings = build_tool_identity_catalog(
        [
            _adk_source("adk_agent", "get_manager_email", "send_email"),
            _adk_inventory(
                "tool-inventory.json",
                "get_manager_email",
                "send_email",
                completes="adk_agent",
            ),
        ],
        ToolIdentityConfig(),
    )

    assert warnings == []
    assert [tool.name for tool in sorted(tools, key=lambda item: item.name)] == [
        "get_manager_email",
        "send_email",
    ]
    # The reviewed inventory is the primary, so the merged tool carries its
    # high extraction confidence — which is what closes `incomplete_surface`.
    assert {tool.extraction_confidence for tool in tools} == {"high"}
    assert {tool.provider for tool in tools} == {"adk_agent"}
    assert all(len(tool.observation_ids) == 2 for tool in tools)


def test_inventory_without_a_source_binding_still_adds_separate_tools() -> None:
    """The pre-#386 spelling keeps working; it is now named, not silent."""

    tools, warnings = build_tool_identity_catalog(
        [
            _adk_source("adk_agent", "get_manager_email"),
            _adk_inventory("tool-inventory.json", "get_manager_email"),
        ],
        ToolIdentityConfig(),
    )

    assert len(tools) == 2
    assert len(warnings) == 1
    assert "declares no source_id" in warnings[0]
    assert "source_id='adk_agent'" in warnings[0]


def test_inventory_entries_the_source_does_not_expose_stay_standalone() -> None:
    """An inventory exists to disclose tools static extraction missed."""

    tools, warnings = build_tool_identity_catalog(
        [
            _adk_source("adk_agent", "get_manager_email"),
            _adk_inventory(
                "tool-inventory.json",
                "get_manager_email",
                "hidden_tool",
                completes="adk_agent",
            ),
        ],
        ToolIdentityConfig(),
    )

    assert warnings == []
    by_name = {tool.name: tool for tool in tools}
    assert sorted(by_name) == ["get_manager_email", "hidden_tool"]
    assert len(by_name["get_manager_email"].observation_ids) == 2
    assert len(by_name["hidden_tool"].observation_ids) == 1


def test_unknown_inventory_source_is_reported_rather_than_silently_inert() -> None:
    tools, warnings = build_tool_identity_catalog(
        [
            _adk_source("adk_agent", "get_manager_email"),
            _adk_inventory(
                "tool-inventory.json", "get_manager_email", completes="adk_agnt"
            ),
        ],
        ToolIdentityConfig(),
    )

    assert len(tools) == 2
    assert len(warnings) == 1
    assert "no tool source is configured" in warnings[0]
    assert "'adk_agent'" in warnings[0]


def test_inventory_naming_itself_is_reported() -> None:
    inventory = _adk_inventory("tool-inventory.json", "get_manager_email")
    inventory.completes_source_id = inventory.source_id
    tools, warnings = build_tool_identity_catalog(
        [_adk_source("adk_agent", "get_manager_email"), inventory],
        ToolIdentityConfig(),
    )

    assert len(tools) == 2
    assert len(warnings) == 1
    assert "which is the inventory itself" in warnings[0]


def test_ambiguous_completion_target_fails_closed() -> None:
    """Two observations of one name cannot be joined by the inventory alone."""

    duplicate = _adk_source("adk_agent", "get_manager_email")
    duplicate.tools.append(
        Tool(
            id="tool:get_manager_email_b",
            name="get_manager_email",
            source_type="google_adk_function",
            source_id="adk_agent",
            source_ref="other.py",
            extraction_confidence="medium",
        )
    )
    tools, warnings = build_tool_identity_catalog(
        [
            duplicate,
            _adk_inventory(
                "tool-inventory.json", "get_manager_email", completes="adk_agent"
            ),
        ],
        ToolIdentityConfig(),
    )

    assert len(tools) == 3
    assert len(warnings) == 1
    assert "more than one observation" in warnings[0]
    assert "tool_identity.bindings" in warnings[0]


def test_a_reviewed_binding_outranks_the_desugared_one() -> None:
    """An explicit human declaration wins; both claiming would invalidate it."""

    inventory_id = "google_adk_inventory:tool-inventory.json"
    config = ToolIdentityConfig(
        bindings=[
            ToolIdentityBindingConfig(
                id="reviewed",
                provider="reviewed_provider",
                reason="the reviewer already joined these",
                primary={"source_id": "adk_agent", "tool": "get_manager_email"},
                members=[
                    {"source_id": "adk_agent", "tool": "get_manager_email"},
                    {"source_id": inventory_id, "tool": "get_manager_email"},
                ],
            )
        ]
    )
    tools, warnings = build_tool_identity_catalog(
        [
            _adk_source("adk_agent", "get_manager_email"),
            _adk_inventory(
                "tool-inventory.json", "get_manager_email", completes="adk_agent"
            ),
        ],
        config,
    )

    assert warnings == []
    assert len(tools) == 1
    # The reviewer chose the AST observation as primary; desugaring must not
    # override that choice by adding a competing binding.
    assert tools[0].provider == "reviewed_provider"
    assert tools[0].identity_assessment.binding_id == "reviewed"


# --- #386 review: completion must not rekey or hollow out the tool -----------


def _n8n_source(source_id: str, name: str) -> LoadedToolSource:
    """A medium-confidence tool carrying evidence only the source knows."""

    return LoadedToolSource(
        source_id=source_id,
        source_type="n8n",
        tools=[
            Tool(
                id=f"tool:{name}",
                name=name,
                source_type="n8n_http_request_tool",
                source_id=source_id,
                source_ref="workflows/agent.json",
                description="Call the billing API.",
                output_schema={
                    "type": "object",
                    "properties": {"invoice_id": {"type": "string"}},
                },
                owner="billing-team",
                function_signature=f"{name}(invoice_id: str) -> dict",
                auth=AuthInfo(
                    type="apiKey",
                    mode="unscoped",
                    credential_mode="static",
                    source="workflow_credentials",
                    explicit=True,
                ),
                extraction_confidence="medium",
            )
        ],
    )


def _n8n_inventory(path: str, name: str, *, completes: str) -> LoadedToolSource:
    """A reviewed inventory that says nothing about auth, output, or owner."""

    source_id = f"n8n_inventory:{path}"
    return LoadedToolSource(
        source_id=source_id,
        source_type="n8n_inventory",
        is_tool_inventory=True,
        completes_source_id=completes,
        tools=[
            Tool(
                id=f"inv:{name}",
                name=name,
                source_type="n8n_inventory",
                source_id=source_id,
                source_ref=path,
                extraction_confidence="high",
            )
        ],
    )


def test_completion_keeps_the_completed_source_as_a_selector_identity() -> None:
    """A source-qualified selector must survive the prescribed remediation.

    Making the inventory ``primary`` rekeys the canonical tool's ``source_id``.
    Shipgate emits source-qualified action rows itself, so without member-source
    aliases its own scaffold stops resolving the moment the user applies its own
    inventory instruction (#386 review).
    """

    before, _ = build_tool_identity_catalog(
        [_adk_source("adk_agent", "get_manager_email")], ToolIdentityConfig()
    )
    after, _ = build_tool_identity_catalog(
        [
            _adk_source("adk_agent", "get_manager_email"),
            _adk_inventory(
                "tool-inventory.json", "get_manager_email", completes="adk_agent"
            ),
        ],
        ToolIdentityConfig(),
    )

    selector = {"tool": "get_manager_email", "source_id": "adk_agent"}
    assert resolve_tool_selector(before, selector).resolved
    assert resolve_tool_selector(after, selector).resolved
    # The inventory's own identity resolves too, as does the pre-merge type.
    assert resolve_tool_selector(
        after, {"tool": "get_manager_email", "source_type": "google_adk_function"}
    ).resolved
    assert resolve_tool_selector(
        after,
        {
            "tool": "get_manager_email",
            "source_id": "google_adk_inventory:tool-inventory.json",
        },
    ).resolved


def test_source_qualifiers_must_be_satisfied_by_one_observation() -> None:
    """Aliasing widens identity, it does not let qualifiers be mixed and matched.

    Checking ``source_type`` and ``source_id`` independently would resolve a
    selector pairing one member's type with another member's id — a tool
    neither observation describes.
    """

    tools, _ = build_tool_identity_catalog(
        [
            _adk_source("adk_agent", "get_manager_email"),
            _adk_inventory(
                "tool-inventory.json", "get_manager_email", completes="adk_agent"
            ),
        ],
        ToolIdentityConfig(),
    )

    crossed = resolve_tool_selector(
        tools,
        {
            "tool": "get_manager_email",
            "source_type": "google_adk_function",
            "source_id": "google_adk_inventory:tool-inventory.json",
        },
    )
    assert not crossed.resolved
    assert crossed.kind == "unresolved_tool_selector"


def test_completion_raises_confidence_without_erasing_source_evidence() -> None:
    """Completion must add extraction fidelity, not trade one gap for another.

    The merge starts from the primary, so a reviewed inventory that is silent
    about auth, output schema, and ownership used to overwrite all three with
    nothing — closing ``incomplete_surface`` and opening
    ``partial_authority_evidence`` in its place (#386 review).
    """

    tools, warnings = build_tool_identity_catalog(
        [
            _n8n_source("n8n_agent", "create_invoice"),
            _n8n_inventory("inv.json", "create_invoice", completes="n8n_agent"),
        ],
        ToolIdentityConfig(),
    )

    assert warnings == []
    (tool,) = tools
    # Raised by the inventory ...
    assert tool.extraction_confidence == "high"
    assert tool.source_type == "n8n_inventory"
    # ... without dropping what only the source knew.
    assert tool.auth.type == "apiKey"
    assert tool.auth.mode == "unscoped"
    assert tool.auth.credential_mode == "static"
    assert tool.auth.source == "workflow_credentials"
    assert tool.auth.explicit is True
    assert tool.owner == "billing-team"
    assert tool.output_schema["properties"] == {"invoice_id": {"type": "string"}}
    assert tool.function_signature == "create_invoice(invoice_id: str) -> dict"
    assert tool.description == "Call the billing API."


def test_backfill_never_overwrites_what_the_reviewed_primary_states() -> None:
    """Only empty slots are filled; the reviewed observation still wins."""

    inventory = _n8n_inventory("inv.json", "create_invoice", completes="n8n_agent")
    inventory.tools[0].owner = "security-team"
    inventory.tools[0].auth = AuthInfo(type="oauth2", mode="scoped", scopes=["billing"])
    source = _n8n_source("n8n_agent", "create_invoice")

    tools, _ = build_tool_identity_catalog([source, inventory], ToolIdentityConfig())

    (tool,) = tools
    assert tool.owner == "security-team"
    assert tool.auth.type == "oauth2"
    # Two populated, disagreeing values are a conflict, not a silent pick.
    issues = {issue.kind for issue in tool.identity_assessment.issues}
    assert "conflicting_tool_identity" in issues


# --- #386 follow-up review: identity aliases and preserved-evidence conflicts -


def test_the_id_an_observation_had_unbound_still_selects_the_merged_tool() -> None:
    """`_action_selector` emits `tool_id`, and completion changes it.

    `ToolSelectorIndex.resolve` prioritizes `tool_id`, so aliasing only the
    source qualifiers left the *generated* scaffold — which carries `tool`,
    `tool_id` and `source_id` together — resolving to nothing the moment the
    prescribed inventory was applied (#386 follow-up review).
    """

    before, _ = build_tool_identity_catalog(
        [_adk_source("adk_agent", "get_manager_email")], ToolIdentityConfig()
    )
    after, _ = build_tool_identity_catalog(
        [
            _adk_source("adk_agent", "get_manager_email"),
            _adk_inventory(
                "tool-inventory.json", "get_manager_email", completes="adk_agent"
            ),
        ],
        ToolIdentityConfig(),
    )

    # Completion really does rekey the canonical id ...
    assert before[0].id != after[0].id
    # ... and the pre-completion selector still resolves to the merged tool.
    scaffold = {
        "tool": "get_manager_email",
        "tool_id": before[0].id,
        "source_id": "adk_agent",
    }
    assert resolve_tool_selector(before, scaffold).resolved
    resolution = resolve_tool_selector(after, scaffold)
    assert resolution.resolved
    assert resolution.matches[0].id == after[0].id


def test_alias_ids_never_enter_the_catalog_partition() -> None:
    """Aliases are for resolution only.

    ``agent_bindings`` reads ``set(ToolSelectorIndex.by_id)`` as the whole
    catalog when it partitions reachable/possible/unbound, so an alias landing
    there invents a catalog member and breaks the tool_catalog/binding-graph
    consistency invariant.
    """

    tools, _ = build_tool_identity_catalog(
        [
            _adk_source("adk_agent", "get_manager_email"),
            _adk_inventory(
                "tool-inventory.json", "get_manager_email", completes="adk_agent"
            ),
        ],
        ToolIdentityConfig(),
    )
    index = ToolSelectorIndex.build(tools)

    assert set(index.by_id) == {tool.id for tool in tools}
    assert set(index.by_selectable_id) > set(index.by_id)


def _disagreeing_member(source_id: str, source_type: str, owner: str, out: str):
    return LoadedToolSource(
        source_id=source_id,
        source_type=source_type,
        tools=[
            Tool(
                id=f"x:{source_id}",
                name="lookup",
                source_type=source_type,
                source_id=source_id,
                source_ref=f"{source_id}.py",
                owner=owner,
                output_schema={"type": out},
                function_signature=f"lookup() -> {out}",
                auth=AuthInfo(credential_mode=f"cred-{owner}", source=f"src-{owner}"),
                extraction_confidence="medium",
            )
        ],
    )


def _silent_primary() -> LoadedToolSource:
    return LoadedToolSource(
        source_id="reviewed_inv",
        source_type="mcp",
        tools=[
            Tool(
                id="p:lookup",
                name="lookup",
                source_type="mcp",
                source_id="reviewed_inv",
                extraction_confidence="high",
            )
        ],
    )


def _three_member_binding() -> ToolIdentityConfig:
    return ToolIdentityConfig(
        bindings=[
            ToolIdentityBindingConfig(
                id="b",
                provider="p",
                reason="reviewed",
                primary={"source_id": "reviewed_inv", "tool": "lookup"},
                members=[
                    {"source_id": "reviewed_inv", "tool": "lookup"},
                    {"source_id": "member_a", "tool": "lookup"},
                    {"source_id": "member_b", "tool": "lookup"},
                ],
            )
        ]
    )


def test_disagreeing_preserved_evidence_is_a_conflict_not_a_coin_flip() -> None:
    """Backfill must not resolve a contradiction by observation-id order.

    Two members reporting `owner: team-a` / `team-b` and `string` / `integer`
    produced a high-confidence, `pass_eligible=True` tool owned by whichever
    observation id sorted first, with no issues at all (#386 follow-up review).
    """

    tools, _ = build_tool_identity_catalog(
        [
            _silent_primary(),
            _disagreeing_member("member_a", "sdk_function", "team-a", "string"),
            _disagreeing_member("member_b", "openapi", "team-b", "integer"),
        ],
        _three_member_binding(),
    )

    (tool,) = tools
    messages = " ".join(
        issue.message
        for issue in tool.identity_assessment.issues
        if issue.kind == "conflicting_tool_identity"
    )
    for field in (
        "output_schema",
        "function_signature",
        "owner",
        "auth.credential_mode",
    ):
        assert field in messages, field
    # `auth.source` names the extractor, not the credential, so two readings of
    # one capability disagree by construction; it must not read as a conflict.
    assert "auth.source" not in messages
    assert tool.identity_assessment.pass_eligible is False


def test_a_populated_primary_disagreeing_with_a_member_is_also_a_conflict() -> None:
    """The primary is a contributor, not an exemption."""

    primary = _silent_primary()
    primary.tools[0].owner = "security-team"
    tools, _ = build_tool_identity_catalog(
        [
            primary,
            _disagreeing_member("member_a", "sdk_function", "team-a", "string"),
            _disagreeing_member("member_b", "sdk_function", "team-a", "string"),
        ],
        _three_member_binding(),
    )

    (tool,) = tools
    assert tool.owner == "security-team"
    assert any(
        issue.kind == "conflicting_tool_identity" and "owner" in issue.message
        for issue in tool.identity_assessment.issues
    )
    assert tool.identity_assessment.pass_eligible is False


def test_agreeing_members_do_not_manufacture_a_conflict() -> None:
    """Compatible evidence must stay quiet, or the check is just noise."""

    tools, _ = build_tool_identity_catalog(
        [
            _silent_primary(),
            _disagreeing_member("member_a", "sdk_function", "team-a", "string"),
            _disagreeing_member("member_b", "openapi", "team-a", "string"),
        ],
        _three_member_binding(),
    )

    (tool,) = tools
    assert tool.owner == "team-a"
    assert tool.identity_assessment.issues == []
    assert tool.identity_assessment.pass_eligible is True


def test_preserved_evidence_records_the_observation_that_supplied_it() -> None:
    """A backfilled value must stay traceable to the artifact declaring it."""

    tools, _ = build_tool_identity_catalog(
        [
            _n8n_source("n8n_agent", "create_invoice"),
            _n8n_inventory("inv.json", "create_invoice", completes="n8n_agent"),
        ],
        ToolIdentityConfig(),
    )

    (tool,) = tools
    assert tool.evidence_provenance["output_schema"]["ref"] == "workflows/agent.json"
    assert tool.evidence_provenance["owner"]["type"] == "n8n_http_request_tool"
    assert tool.evidence_provenance["auth.source"]["ref"] == "workflows/agent.json"
