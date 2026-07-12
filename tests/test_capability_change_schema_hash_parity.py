"""Regression: capability_change must stay empty on an identical tree.

Found via the benchmark miner on openai/openai-agents-python docs-only PR
#3392: every real verify with a base ref reported a wall of false
"broadened (schema_hash changed without a proven direction)" capability
members, while the capability lock-diff from the same run said every
capability was unchanged.

Root cause: the head public ``ActionFact`` kept the ``input_schema_hash``
``action_to_fact`` seeds from the raw ``{input_schema, parameters}`` (fields
that are *not* serialized on the public fact), while the base diff reference
re-derived a field-based hash from ``{input_fields, required_input_fields}``
via ``_refresh_public_action_hashes``. Two formulas over the same action ->
``schema_hash`` flipped for every capability on an otherwise-identical tree,
poisoning both ``capability_change`` and ``action_surface_diff`` (a spurious
``ACTION_MODIFIED`` per action). The lock path was already consistent because
both sides build through ``build_capability_facts``.

These tests pin the fix: the head and base public facts run the one
round-trip-stable hashing path, so an identical tree produces an empty
capability delta that agrees with the lock-diff.
"""

from __future__ import annotations

from pathlib import Path

from agents_shipgate.cli.scan import run_scan
from agents_shipgate.cli.scan.surface_redaction import (
    _build_public_action_surface_facts,
    _sanitize_existing_action_surface_facts,
)
from agents_shipgate.cli.verify.capability_review import build_capability_review
from agents_shipgate.core.capabilities import build_capability_facts
from agents_shipgate.core.capability_delta import diff_capability_fact_sets
from agents_shipgate.core.domain import AuthInfo, Tool, ToolParameter
from agents_shipgate.core.findings.verifier_blocks import _action_contexts
from agents_shipgate.core.lenses.action_surface import build_action_surface_facts
from agents_shipgate.core.privacy import RedactionStats
from agents_shipgate.schemas.manifest import AgentsShipgateManifest

AGENT_ID = "agent:parity/agent"

_OPENAPI = """
openapi: 3.1.0
info: {title: Catalog, version: "1"}
paths:
  /items:
    get:
      operationId: list_items
      parameters:
        - {name: q, in: query, schema: {type: string}}
        - {name: limit, in: query, schema: {type: integer}}
      responses: {"200": {description: ok}}
  /items/{item_id}:
    get:
      operationId: get_item
      parameters:
        - {name: item_id, in: path, required: true, schema: {type: string}}
      responses: {"200": {description: ok}}
"""

_MANIFEST = """
version: "0.1"
project: {name: parity}
agent:
  name: agent
  declared_purpose: [browse the catalog]
environment: {target: production_like}
tool_sources:
  - id: catalog-api
    type: openapi
    path: openapi.yaml
agent_bindings:
  declarations:
    - agent: root
      complete: true
      tools:
        - {tool: list_items, source_id: catalog-api}
        - {tool: get_item, source_id: catalog-api}
      handoffs: []
      reason: reviewed parity fixture binding
"""


def _write_tree(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "openapi.yaml").write_text(_OPENAPI, encoding="utf-8")
    (root / "shipgate.yaml").write_text(_MANIFEST, encoding="utf-8")
    return root / "shipgate.yaml"


def test_identical_tree_verify_has_empty_capability_change(tmp_path):
    """End-to-end: base == head must yield no capability delta and no
    action-surface modifications (exercises the real head + base
    sanitization paths through ``run_scan --diff-from``)."""
    base_cfg = _write_tree(tmp_path / "base")
    head_cfg = _write_tree(tmp_path / "head")

    run_scan(
        config_path=base_cfg,
        output_dir=tmp_path / "base" / "reports",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    report, _ = run_scan(
        config_path=head_cfg,
        output_dir=tmp_path / "head" / "reports",
        formats=["json"],
        ci_mode="advisory",
        diff_from_path=tmp_path / "base" / "reports" / "report.json",
        packet_enabled=False,
    )

    # The diff must be active and have real actions to compare, or the
    # assertions below pass vacuously.
    assert report.action_surface_diff.enabled is True
    assert len(report.action_surface_facts.actions) == 2

    # The bug surfaced here: a spurious ACTION_MODIFIED per action.
    assert report.action_surface_diff.modified == []
    assert report.action_surface_diff.summary.actions_modified == 0

    # ... and a false "broadened" capability member per action.
    cap = report.capability_change
    assert cap is not None and cap.enabled is True
    assert cap.added == []
    assert cap.removed == []
    assert cap.broadened == []
    assert cap.narrowed == []

    # The reviewer rollup that consumers (PR comment / capability_review)
    # read must agree.
    review = build_capability_review(report)
    assert (review.added, review.modified, review.removed) == (0, 0, 0)


def test_head_and_base_public_facts_share_one_schema_hash(tmp_path):
    """Unit cross-check: for the same tree the head facts
    (``_build_public_action_surface_facts``) and the base diff reference
    (``_sanitize_existing_action_surface_facts``) derive identical action
    hashes, so the capability_change engine and the lock engine agree."""
    manifest = AgentsShipgateManifest.model_validate(
        {
            "version": "0.1",
            "project": {"name": "parity"},
            "agent": {"name": "agent", "declared_purpose": ["browse"]},
            "environment": {"target": "production_like"},
            "tool_sources": [
                {"id": "catalog", "type": "openapi", "path": "catalog.yaml"}
            ],
        }
    )
    tools = [
        Tool(
            id="tool:list_items",
            name="list_items",
            description="list items",
            source_type="openapi",
            source_id="catalog",
            source_ref="catalog.yaml",
            source_path="catalog.yaml",
            source_start_line=3,
            source_pointer="/paths/items",
            annotations={},
            input_schema={
                "type": "object",
                "properties": {"q": {"type": "string"}, "limit": {"type": "integer"}},
            },
            auth=AuthInfo(type="oauth", credential_mode="delegated", source="manifest", scopes=[]),
            risk_hints=[],
            parameters=[
                ToolParameter(name="q", type="string", required=False),
                ToolParameter(name="limit", type="integer", required=False),
            ],
            extraction_confidence="high",
        ),
        Tool(
            id="tool:get_item",
            name="get_item",
            description="get item",
            source_type="openapi",
            source_id="catalog",
            source_ref="catalog.yaml",
            source_path="catalog.yaml",
            source_start_line=9,
            source_pointer="/paths/items/{item_id}",
            annotations={},
            input_schema={
                "type": "object",
                "properties": {"item_id": {"type": "string"}},
                "required": ["item_id"],
            },
            auth=AuthInfo(type="oauth", credential_mode="delegated", source="manifest", scopes=[]),
            risk_hints=[],
            parameters=[ToolParameter(name="item_id", type="string", required=True)],
            extraction_confidence="high",
        ),
    ]

    raw_facts = build_action_surface_facts(manifest, agent_id=AGENT_ID, tools=tools)
    assert len(raw_facts.actions) == 2

    head = _build_public_action_surface_facts(
        raw_facts=raw_facts,
        manifest=manifest,
        agent_id=AGENT_ID,
        tools=tools,
        stats=RedactionStats(),
    )
    # Mirrors the base diff reference: a serialized prior public report fed
    # back through the diff-reference sanitizer.
    base = _sanitize_existing_action_surface_facts(
        raw_facts.model_copy(deep=True),
        stats=RedactionStats(),
        path="action_surface_diff.base.facts",
    )

    head_by_id = {a.action_id: a for a in head.actions}
    base_by_id = {a.action_id: a for a in base.actions}
    assert head_by_id.keys() == base_by_id.keys()
    for action_id, head_action in head_by_id.items():
        base_action = base_by_id[action_id]
        assert head_action.input_schema_hash == base_action.input_schema_hash, action_id
        assert head_action.hashes == base_action.hashes, action_id

    # capability_change engine (public ActionFact path): no delta.
    report_diff = diff_capability_fact_sets(
        _action_contexts(base.actions),
        _action_contexts(head.actions),
    )
    assert (len(report_diff.added), len(report_diff.removed), len(report_diff.changed)) == (0, 0, 0)
    assert report_diff.unchanged_count == 2

    # lock engine (Tool path): same answer on the same input.
    lock_diff = diff_capability_fact_sets(
        build_capability_facts(manifest, agent_id=AGENT_ID, tools=tools),
        build_capability_facts(manifest, agent_id=AGENT_ID, tools=tools),
    )
    assert (len(lock_diff.added), len(lock_diff.removed), len(lock_diff.changed)) == (0, 0, 0)
    assert lock_diff.unchanged_count == 2

    # The two engines must agree (the invariant the bug violated).
    assert len(report_diff.changed) == len(lock_diff.changed)
