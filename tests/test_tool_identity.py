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
