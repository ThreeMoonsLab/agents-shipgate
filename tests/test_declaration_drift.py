"""Pinning a declaration to the evidence that justified it (#410 §E).

Declarations are matched by name and nothing ever re-opened one, so a green
gate at month twelve could rest on a description of a function that no longer
does what it did. ``action_surface.actions[].basis`` records which evidence an
answer was given against; every scan re-derives it and compares.

Three properties carry the whole feature, and each has a test that would fail
if it were lost:

1. **The pin does not move when the answer arrives.** If it did, confirming a
   proposal would produce a drift row on the very next scan — a treadmill, and
   the one failure mode that would make pinning worse than not pinning.
2. **A pin that matches is completely silent, and a pin that does not re-opens
   the question.** No verdict, gap, or counter may change in the first case;
   all three must in the second.
3. **The row that re-opens carries the value that closes it.** A published next
   step that cannot change the answer is the defect this repository keeps
   finding; here the drift row must hand the reviewer the exact new pin.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from agents_shipgate.ci.release_decision import _semantic_gap
from agents_shipgate.cli.scan import run_scan
from agents_shipgate.core.declaration_questions import (
    ANSWERABLE_ISSUE_KINDS,
    declaration_questions,
)
from agents_shipgate.core.domain import SemanticClaim, Tool, ToolRiskHint
from agents_shipgate.core.semantic_assessment import (
    EffectReading,
    _readings_from_claims,
    assess_tool_semantics,
    confirmed_basis,
    effect_derivation_id,
    effect_readings,
)
from agents_shipgate.schemas.manifest import ActionDeclarationConfig
from agents_shipgate.schemas.manifest.action_surface import CONFIRMED_BASIS_PREFIX

_TAG_FOR_EFFECT = {
    "external_communication": "external_write",
    "financial_write": "financial_action",
    "destructive": "destructive",
    "write": "writes_data",
    "read": "read_only",
}


def _tool(**updates: object) -> Tool:
    values: dict[str, object] = {
        "id": "google_adk:closer:send_email",
        "name": "send_email",
        "source_type": "google_adk",
        "source_id": "closer",
        "provider": "closer",
        "source_pointer": "agent.py",
        "extraction_confidence": "high",
        "extraction": {"surface": "enumerated"},
    }
    values.update(updates)
    return Tool.model_validate(values)


def _observing(*effects: str, **updates: object) -> Tool:
    return _tool(
        risk_hints=[
            ToolRiskHint(
                tag=_TAG_FOR_EFFECT[effect],
                source=f"hint{index}",
                confidence="medium",
                basis="inferred_keyword",
            )
            for index, effect in enumerate(effects)
        ],
        **updates,
    )


def _pin(tool: Tool, declaration: ActionDeclarationConfig | None = None) -> str:
    return confirmed_basis(effect_readings(assess_tool_semantics(tool, declaration).effect))


def _issue_kinds(tool: Tool, declaration: ActionDeclarationConfig | None) -> set[str]:
    return {issue.kind for issue in assess_tool_semantics(tool, declaration).effect.issues}


# --------------------------------------------------------------------------
# 1. The pin does not move when the answer arrives
# --------------------------------------------------------------------------


_DECLARATION_SHAPES: tuple[dict, ...] = (
    {"effect": "write"},
    {"effect": "destructive"},
    {"effect": "write", "risk_tags": ["external_communication"]},
    {"effect": "read", "override": {"evidence": "read the body", "reason": "mock"}},
    {"effect": "write", "scopes": ["crm:write"]},
    {
        "effect": "write",
        "authority": {"mode": "scoped", "auth_type": "oauth2"},
        "scopes": ["crm:write"],
    },
)


@pytest.mark.parametrize("shape", _DECLARATION_SHAPES)
@pytest.mark.parametrize(
    "effects",
    [(), ("read",), ("external_communication",), ("write", "destructive")],
)
def test_the_pin_is_the_same_before_and_after_the_answer(
    shape: dict, effects: tuple[str, ...]
) -> None:
    """The false-drift class, pinned closed.

    A reviewer confirms the proposal the scaffold offers, pastes the `basis`
    line it stamped, and re-scans. If any declaration shape moved the digest,
    that rescan would open a `declaration_drift` row against an answer given
    seconds earlier — and every adopter would learn to ignore the row.
    """

    tool = _observing(*effects) if effects else _tool()
    declaration = ActionDeclarationConfig.model_validate({"tool": "send_email", **shape})

    assert _pin(tool, None) == _pin(tool, declaration), (
        f"{shape} moved the pin; a reviewer would get a drift row for their "
        "own answer"
    )


def test_an_mcp_protocol_default_is_not_pinned() -> None:
    """The one claim whose *presence* depends on a declaration existing.

    An unannotated MCP tool gets a `write` protocol default only while nothing
    is declared. Digesting it would make the pin move the instant an answer
    arrived, which is the same defect as the parametrised case above but
    reachable only through the MCP path.
    """

    mcp = _tool(source_type="mcp", annotations={"mcp_server": True})
    undeclared = assess_tool_semantics(mcp, None)

    assert any(
        claim.source == "mcp_protocol_default" for claim in undeclared.effect.claims
    ), "fixture no longer produces a protocol default"
    assert _pin(mcp, None) == _pin(
        mcp, ActionDeclarationConfig(tool="send_email", effect="write")
    )


def test_a_second_producer_for_a_known_reading_does_not_move_the_pin() -> None:
    """No treadmill. A shipgate release that adds a heuristic must not re-open
    every pinned declaration on every adopter at once — corroboration of a
    reading the reviewer already answered is not new information about the
    action.
    """

    one = _observing("external_communication")
    two = _tool(
        risk_hints=[
            ToolRiskHint(
                tag="external_write",
                source=name,
                confidence="medium",
                basis="inferred_keyword",
            )
            for name in ("keyword", "regex")
        ]
    )

    assert _pin(one) == _pin(two)


def test_replacing_authoritative_evidence_with_a_heuristic_moves_the_pin() -> None:
    """The blind spot a digest of effect *strings* alone had.

    A tool published with `readOnlyHint: true` beside a `read_only` keyword
    hint reads `read` twice over. Delete the annotation and it still reads
    `read` — from the heuristic alone — so a digest over the effect string held
    the pin steady while the evidence the reviewer actually leaned on
    disappeared. `read` is the worst classification to lose it on: a heuristic
    may never establish read-only on its own (#357), so the safety-sensitive
    answer survived on evidence that could not have produced it.
    """

    def tool(annotated: bool, extra_hint: bool = False) -> Tool:
        annotations = {"mcp_server": True}
        if annotated:
            annotations["readOnlyHint"] = True
        hints = [
            ToolRiskHint(
                tag="read_only", source="keyword", confidence="medium", basis="inferred_keyword"
            )
        ]
        if extra_hint:
            hints.append(
                ToolRiskHint(
                    tag="read_only", source="regex", confidence="medium", basis="inferred_regex"
                )
            )
        return _tool(source_type="mcp", annotations=annotations, risk_hints=hints)

    authoritative = _pin(tool(annotated=True))

    # Replacement moves it …
    assert _pin(tool(annotated=False)) != authoritative
    # … and a stale pin now says so, where it used to stay silent.
    stale = ActionDeclarationConfig(
        tool="send_email", effect="read", basis=authoritative
    )
    assert "declaration_drift" in _issue_kinds(tool(annotated=False), stale)

    # … while corroboration is still quiet: strength is the strongest class per
    # reading, not a per-producer flag, so a second heuristic agreeing with an
    # annotation changes nothing.
    assert _pin(tool(annotated=True, extra_hint=True)) == authoritative


def test_a_published_reading_carries_what_the_pin_digests() -> None:
    """A consumer has to be able to reproduce the pin from the row it is on.

    The pin is over `(effect, strength)` for the observed readings, and the
    strength half was not published: a reading whose authoritative claim had
    been deleted looked identical on the wire to one that never had it.
    """

    tool = _tool(
        source_type="mcp",
        annotations={"mcp_server": True, "readOnlyHint": True},
        risk_hints=[
            ToolRiskHint(
                tag="external_write",
                source="keyword",
                confidence="medium",
                basis="inferred_keyword",
            )
        ],
    )
    tool.semantic_assessment = assess_tool_semantics(tool, None)
    gap = _semantic_gap(tool, kind="inferred_effect_only", why="test")
    published = gap.next_action.observed_readings

    assert published, "the fixture publishes no readings"
    assert {row.effect: row.policy_eligible for row in published} == {
        "read": True,
        "external_communication": False,
    }
    reproduced = confirmed_basis(
        [
            EffectReading(
                effect=row.effect,
                sources=tuple(row.sources),
                observed=row.observed,
                policy_eligible=row.policy_eligible,
            )
            for row in published
        ]
    )
    assert reproduced == (gap.next_action.declaration_template or {}).get("basis")


def test_a_new_or_removed_reading_moves_the_pin() -> None:
    """What the pin exists to catch, in both directions."""

    base = _pin(_observing("external_communication"))

    assert _pin(_observing("external_communication", "financial_write")) != base
    assert _pin(_tool()) != base


def test_adding_a_reviewed_risk_override_does_not_reopen_a_pinned_answer() -> None:
    """A manifest edit is not new source evidence for the answer it decorates.

    ``risk_overrides.tags`` reaches the effect claims as ``risk_hint:manual``
    rather than through the action row. Counting that claim as a reading moved
    the pin when the reviewed tag arrived, immediately reopening an otherwise
    unchanged answer as ``declaration_drift``.
    """

    before = _observing("write")
    basis = _pin(before)
    declaration = ActionDeclarationConfig(
        tool="send_email", effect="write", basis=basis
    )
    after = _observing("write")
    after.risk_hints.append(
        ToolRiskHint(
            tag="destructive",
            source="manual",
            confidence="high",
            basis="reviewed_declaration",
        )
    )

    assert _pin(after, declaration) == basis
    assert "declaration_drift" not in _issue_kinds(after, declaration)


def test_the_derivation_id_is_short_and_stable() -> None:
    """It is written into a manifest a human reads and a reviewer diffs."""

    readings = effect_readings(assess_tool_semantics(_observing("write"), None).effect)
    first = effect_derivation_id(readings)

    assert first == effect_derivation_id(readings)
    assert len(first) == 12
    assert set(first) <= set("0123456789abcdef")
    assert confirmed_basis(readings) == f"{CONFIRMED_BASIS_PREFIX}{first}"


# --------------------------------------------------------------------------
# 2. Matching is silent; not matching re-opens
# --------------------------------------------------------------------------


def test_a_matching_pin_changes_nothing() -> None:
    tool = _observing("external_communication")
    answer = {"tool": "send_email", "effect": "external_communication"}
    unpinned = ActionDeclarationConfig.model_validate(answer)
    pinned = ActionDeclarationConfig.model_validate({**answer, "basis": _pin(tool)})

    before = assess_tool_semantics(tool, unpinned)
    after = assess_tool_semantics(tool, pinned)

    assert before.model_dump() == after.model_dump()


def test_a_stale_pin_re_opens_the_question() -> None:
    tool = _observing("external_communication")
    stale = ActionDeclarationConfig(
        tool="send_email", effect="external_communication", basis="confirmed:000000000000"
    )

    assessment = assess_tool_semantics(tool, stale)
    tool.semantic_assessment = assessment

    assert "declaration_drift" in {issue.kind for issue in assessment.effect.issues}
    assert not assessment.pass_eligible
    assert ("effect", False) in {
        (question.dimension, question.answered)
        for question in declaration_questions([tool])
    }


def test_an_unpinned_declaration_never_drifts() -> None:
    """Every manifest written before this field existed behaves as it did."""

    tool = _observing("external_communication")
    unpinned = ActionDeclarationConfig(tool="send_email", effect="external_communication")

    assert "declaration_drift" not in _issue_kinds(tool, unpinned)


def test_drift_and_the_monotone_rule_are_different_statements() -> None:
    """#409 asks whether the declaration is *below* today's evidence; §E asks
    whether today's evidence is what was answered at all. A tool that gains a
    stronger reading raises both, and neither substitutes for the other: the
    first is closed by accounting for the new effect, the second by re-reading
    and re-confirming.
    """

    pinned_when_only_writing = _pin(_observing("write"))
    now_also_destructive = _observing("write", "destructive")
    declaration = ActionDeclarationConfig(
        tool="send_email", effect="write", basis=pinned_when_only_writing
    )

    kinds = _issue_kinds(now_also_destructive, declaration)

    assert {"declaration_drift", "declaration_below_inferred_evidence"} <= kinds

    # Accounting for the new effect closes one row and leaves the other: the
    # reviewer has said what the action does, not that they re-read it.
    accounted = ActionDeclarationConfig(
        tool="send_email", effect="destructive", basis=pinned_when_only_writing
    )
    remaining = _issue_kinds(now_also_destructive, accounted)

    assert "declaration_below_inferred_evidence" not in remaining
    assert "declaration_drift" in remaining


def test_drift_is_an_answerable_question_kind() -> None:
    assert "declaration_drift" in ANSWERABLE_ISSUE_KINDS["effect"]


# --------------------------------------------------------------------------
# 3. The row that re-opens carries the value that closes it
# --------------------------------------------------------------------------


def test_the_drift_row_hands_over_the_pin_that_closes_it() -> None:
    tool = _observing("external_communication", "financial_write")
    stale = ActionDeclarationConfig(
        tool="send_email", effect="financial_write", basis="confirmed:000000000000"
    )
    tool.semantic_assessment = assess_tool_semantics(tool, stale)
    current = _pin(tool, stale)

    gap = _semantic_gap(
        tool,
        kind="declaration_drift",
        why="test",
        issue_source="action_surface_declaration",
    )
    template = gap.next_action.declaration_template or {}

    assert template.get("basis") == current
    # The answer as it stands travels with the pin, so the block is a faithful
    # replacement for the row it re-confirms rather than a lossy one.
    assert template.get("effect") == "financial_write"
    assert current in gap.next_action.expects
    assert "external_communication" in gap.next_action.expects
    # And it is answerable where it says it is.
    assert gap.next_action.path is not None
    assert "action_surface.actions" in gap.next_action.path

    closed = ActionDeclarationConfig.model_validate(
        {key: value for key, value in template.items() if key != "tool_id"}
    )
    assert "declaration_drift" not in _issue_kinds(tool, closed)


def test_every_effect_answer_template_carries_a_pin() -> None:
    """A question answered without a pin can never re-open, so the field would
    only ever reach manifests by hand. Every route that offers an effect answer
    stamps it.
    """

    tool = _observing("external_communication")
    tool.semantic_assessment = assess_tool_semantics(tool, None)

    for kind in ("missing_effect_evidence", "inferred_effect_only"):
        gap = _semantic_gap(tool, kind=kind, why="test")
        template = gap.next_action.declaration_template or {}
        assert template.get("basis") == _pin(tool), kind


def test_a_hand_written_placeholder_pin_is_legal_and_self_correcting() -> None:
    """How an existing declaration adopts pinning.

    The value cannot be known before a scan publishes it, so a short
    placeholder has to be accepted and answered with the real one — a config
    error here would leave the only route to a pin "read the schema and guess".
    """

    tool = _observing("write")
    placeholder = ActionDeclarationConfig(tool="send_email", effect="write", basis="confirmed:0")
    tool.semantic_assessment = assess_tool_semantics(tool, placeholder)

    assert "declaration_drift" in {
        issue.kind for issue in tool.semantic_assessment.effect.issues
    }
    gap = _semantic_gap(
        tool,
        kind="declaration_drift",
        why="test",
        issue_source="action_surface_declaration",
    )
    assert (gap.next_action.declaration_template or {}).get("basis") == _pin(tool)


# --------------------------------------------------------------------------
# The manifest field itself
# --------------------------------------------------------------------------


def test_a_pin_must_pin_an_answer() -> None:
    """`basis` records the evidence an *answer* was given against. Beside no
    answer it records a confirmation of nothing.
    """

    with pytest.raises(ValidationError):
        ActionDeclarationConfig(tool="send_email", basis="confirmed:abc123")

    # Either route out of a below-evidence row is an answer, so either may be
    # pinned.
    ActionDeclarationConfig(tool="send_email", effect="write", basis="confirmed:abc123")
    ActionDeclarationConfig(
        tool="send_email", risk_tags=["destructive"], basis="confirmed:abc123"
    )


@pytest.mark.parametrize(
    "value",
    ["", "confirmed:", "abc123", "confirmed:XYZ", "confirmed: abc", "sha256:abc123"],
)
def test_a_pin_that_is_not_a_pin_is_refused(value: str) -> None:
    with pytest.raises(ValidationError):
        ActionDeclarationConfig(tool="send_email", effect="write", basis=value)


def test_the_published_schema_states_the_same_rule_the_cli_enforces() -> None:
    """This file is advertised for live editor validation; a schema that
    accepts what the CLI refuses is worse than no schema.
    """

    schema = json.loads(Path("docs/manifest-v0.1.json").read_text(encoding="utf-8"))
    action = schema["$defs"]["ActionDeclarationConfig"]

    assert action["properties"]["basis"]["anyOf"][0]["pattern"].startswith("^confirmed:")
    conditions = [rule for rule in action["allOf"] if "basis" in rule["if"]["required"]]
    assert len(conditions) == 1
    branches = conditions[0]["then"]["anyOf"]
    assert {"effect"} in [set(branch["required"]) for branch in branches]
    assert {"risk_tags"} in [set(branch["required"]) for branch in branches]


# --------------------------------------------------------------------------
# End to end, through a real scan
# --------------------------------------------------------------------------


def _workspace(tmp_path: Path, *, tools: list[dict], actions: list[dict]) -> Path:
    (tmp_path / "tools.json").write_text(json.dumps({"tools": tools}), encoding="utf-8")
    manifest = {
        "version": "0.1",
        "project": {"name": "drift"},
        "agent": {"name": "asst", "declared_purpose": ["exercise drift pinning"]},
        "environment": {"target": "local"},
        "tool_sources": [{"id": "src", "type": "mcp", "path": "tools.json"}],
        "agent_bindings": {
            "declarations": [
                {
                    "agent": "root",
                    "complete": True,
                    "tools": [{"tool": tool["name"], "source_id": "src"} for tool in tools],
                    "handoffs": [],
                    "reason": "reviewed fixture binding",
                }
            ]
        },
        "action_surface": {"actions": actions},
    }
    config = tmp_path / "shipgate.yaml"
    config.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    return config


def _gap_kinds(tmp_path: Path, config: Path) -> list[str]:
    report, _ = run_scan(
        config_path=config,
        output_dir=tmp_path / "out",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    assert report.release_decision is not None
    return [gap.kind for gap in report.release_decision.evidence_coverage.evidence_gaps]


def test_the_loop_closes_through_real_scans(tmp_path: Path) -> None:
    """Pin, move the code, re-confirm — the whole point, end to end.

    Every other test here checks one hop. This walks the loop the feature
    exists for: a declaration confirmed against what the scan read is silent,
    the day the code stops matching it the question comes back, and the value
    the row hands over closes it. Through real scans, because the pin is
    stamped by one surface (the gap's template) and compared by another (the
    resolver), and a unit test holds both ends itself.
    """

    def scan(step: str, annotated: bool, basis: str | None):
        root = tmp_path / step
        root.mkdir()
        tool = {
            "name": "docs.lookup",
            "description": "Look up an internal documentation article by its id.",
            "inputSchema": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
                "additionalProperties": False,
            },
        }
        if annotated:
            tool["annotations"] = {"readOnlyHint": True}
        action = {"tool": "docs.lookup", "source_id": "src", "effect": "write"}
        if basis:
            action["basis"] = basis
        report, _ = run_scan(
            config_path=_workspace(root, tools=[tool], actions=[action]),
            output_dir=root / "out",
            formats=["json"],
            ci_mode="advisory",
            packet_enabled=False,
        )
        assert report.release_decision is not None
        gaps = report.release_decision.evidence_coverage.evidence_gaps
        drift = [gap for gap in gaps if gap.kind == "declaration_drift"]
        return report, drift

    # 1. Unpinned: whatever the scan read, and no pin to compare. The pin is
    #    taken from the claims *this scan published*, not re-derived from a
    #    synthetic tool — a test that builds its own copy of the input is
    #    asserting that two derivations agree, which is the thing under test.
    first, drift = scan("first", annotated=True, basis=None)
    assert not drift
    published = first.action_surface_facts.actions[0].semantic_assessment.effect.claims
    assert any(claim.source == "mcp_annotation" for claim in published)
    pin = confirmed_basis(
        _readings_from_claims([SemanticClaim.model_validate(c.model_dump()) for c in published])
    )

    # 2. Pinned against exactly that: silent.
    _, drift = scan("pinned", annotated=True, basis=pin)
    assert not drift, "a matching pin must change nothing"

    # 3. The annotation goes away. The question comes back, and the row hands
    #    over the value that closes it.
    _, drift = scan("moved", annotated=False, basis=pin)
    assert len(drift) == 1
    new_pin = (drift[0].next_action.declaration_template or {}).get("basis")
    assert new_pin and new_pin != pin
    assert str(new_pin) in drift[0].next_action.expects

    # 4. Re-confirmed with it: silent again.
    _, drift = scan("reconfirmed", annotated=False, basis=str(new_pin))
    assert not drift, "the value the row published did not close the row"


def test_a_scan_publishes_the_drift_row_the_resolver_raised(tmp_path: Path) -> None:
    tools = [{"name": "send_email", "description": "Send an email to a customer."}]
    config = _workspace(
        tmp_path,
        tools=tools,
        actions=[
            {
                "tool": "send_email",
                "effect": "external_communication",
                "basis": "confirmed:000000000000",
            }
        ],
    )

    assert "declaration_drift" in _gap_kinds(tmp_path, config)
