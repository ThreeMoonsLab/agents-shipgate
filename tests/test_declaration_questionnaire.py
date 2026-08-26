"""Evidence-first effects, the questionnaire, and the progress counter (#410).

Three properties this increment lives or dies on, and each has a test that
would fail if it were lost:

1. **A proposal is never weaker than what was observed.** Pre-filling a value
   is only safe because the worst a reviewer who confirms it without thinking
   can do is over-declare. Exhaustive over every reading combination.
2. **A proposal comes from an observation, never from an absence and never from
   a heuristic reading of ``read``.** Those are the two directions where
   pre-filling would put shipgate's own guess in the trust root.
3. **The counter and the questionnaire agree, and neither counts what the scan
   proved by itself.** A progress bar whose denominator includes questions
   nobody was asked is worse than no progress bar.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest
import yaml

from agents_shipgate.ci.release_decision import REVIEW_REQUIRED_SENTINEL
from agents_shipgate.cli.scan import run_scan
from agents_shipgate.cli.scan.declarations import build_declaration_scaffold
from agents_shipgate.core.declaration_questions import (
    ANSWERABLE_ISSUE_KINDS,
    DIMENSION_BY_GAP_KIND,
    declaration_questions,
    progress_sentence,
)
from agents_shipgate.core.domain import Tool, ToolRiskHint
from agents_shipgate.core.semantic_assessment import (
    assess_tool_semantics,
    confirmed_basis,
    declaration_covers,
    effect_readings,
    propose_effect_declaration,
)
from agents_shipgate.schemas.manifest import ActionDeclarationConfig
from agents_shipgate.schemas.report import (
    DeclarationQuestionCoverage,
    DeclarationQuestionRow,
    EvidenceGap,
    EvidenceGapAction,
    EvidenceReading,
)
from agents_shipgate.schemas.surfaces import ActionEffect

#: One risk-hint tag per effect, so a synthetic tool can be given exactly the
#: readings a case needs.
_TAG_FOR_EFFECT = {
    "external_communication": "external_write",
    "financial_write": "financial_action",
    "destructive": "destructive",
    "production_operation": "production_operation",
    "code_execution": "code_execution",
    "privileged_data_access": "privileged_data_access",
    "identity_access": "identity_access",
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


def _readings(tool: Tool) -> list:
    return effect_readings(assess_tool_semantics(tool, None).effect)


# --------------------------------------------------------------------------
# 1. A proposal is never weaker than what was observed
# --------------------------------------------------------------------------


def test_a_proposal_accounts_for_every_reading_it_is_printed_under() -> None:
    """Exhaustive: the value offered must cover everything the row shows.

    This is the whole safety argument for pre-filling. If a proposal could sit
    below one of the readings printed above it, confirming it would be a
    quieter version of exactly the under-declaration #409 exists to catch.
    """

    checked = 0
    for count in (1, 2, 3):
        for effects in itertools.combinations(sorted(_TAG_FOR_EFFECT), count):
            readings = _readings(_observing(*effects))
            proposal = propose_effect_declaration(readings)
            if proposal is None:
                continue
            checked += 1
            asserted = {proposal.effect, *proposal.risk_tags}
            for reading in readings:
                assert any(
                    declaration_covers(value, reading.effect) for value in asserted
                ), (
                    f"{effects}: proposed {sorted(asserted)} does not account for "
                    f"an observed {reading.effect}"
                )
    assert checked > 100, "the sweep stopped exercising the proposal path"


#: Structural evidence a real tool carries alongside its heuristics, each of
#: which produces a **policy-eligible** effect claim. Crossed with the
#: heuristic combinations below, because those are the claims that can turn a
#: too-weak declaration into a blocking ``conflicting_effect_evidence`` rather
#: than a review-level row.
_STRUCTURAL_VARIANTS: dict[str, dict[str, object]] = {
    "none": {},
    "write_scope": {"auth": {"scopes": ["thing:write"]}},
    "delete_scope": {"auth": {"scopes": ["thing:delete"]}},
    "http_post": {"annotations": {"httpMethod": "POST"}},
    "read_only_hint": {"annotations": {"readOnlyHint": True}},
    "mcp_no_annotations": {"source_type": "mcp", "annotations": {"mcp_server": True}},
}

#: The two gap kinds whose ``declaration_template`` carries a pre-filled
#: proposal. Anything else either offers a blank or offers no template at all,
#: so a proposal is never printed on it.
_PREFILLED_GAP_KINDS = frozenset({"missing_effect_evidence", "inferred_effect_only"})


def test_the_proposal_closes_the_row_it_is_printed_on() -> None:
    """Exhaustive: paste what the questionnaire offers and the gap must be gone.

    A published repair is verified by applying it (PR #413 review 1). Here the
    repair is a whole declaration rather than an edit to one, so "gone" means
    the effect dimension carries no declaration-answerable issue at all — not
    the row it replaced, and not a *different* one either, which is how the
    first #409 override shipped a repair that traded one gap for another.

    Structural evidence is crossed in because it is policy-eligible: a
    declaration ranked below one of those claims is a blocking conflict, not a
    review row, and that is the failure this sweep exists to rule out.
    """

    answerable = ANSWERABLE_ISSUE_KINDS["effect"]
    applied = 0
    for variant, extra in _STRUCTURAL_VARIANTS.items():
        for count in (1, 2, 3):
            for effects in itertools.combinations(sorted(_TAG_FOR_EFFECT), count):
                tool = _observing(*effects, **extra)
                undeclared = {
                    issue.kind for issue in assess_tool_semantics(tool, None).effect.issues
                }
                if not undeclared & _PREFILLED_GAP_KINDS:
                    # No pre-filled template is published for this shape, so
                    # there is no proposal for a reviewer to confirm.
                    continue
                proposal = propose_effect_declaration(_readings(tool))
                if proposal is None:
                    continue
                payload: dict[str, object] = {
                    "tool": "send_email",
                    "effect": proposal.effect,
                    "authority": {"mode": "none"},
                }
                if proposal.risk_tags:
                    payload["risk_tags"] = list(proposal.risk_tags)
                assessment = assess_tool_semantics(
                    tool, ActionDeclarationConfig.model_validate(payload)
                )
                applied += 1
                remaining = {
                    issue.kind for issue in assessment.effect.issues
                } & answerable
                assert not remaining, (
                    f"{variant}/{effects}: confirming the proposed {payload} left "
                    f"{sorted(remaining)}"
                )
    assert applied > 400, "the sweep stopped exercising the proposal path"


# --------------------------------------------------------------------------
# 2. Only an observation may seed a proposal
# --------------------------------------------------------------------------


def test_a_protocol_default_is_not_an_observation() -> None:
    """An unannotated MCP tool keeps its blank.

    The protocol default fires *because* the server published nothing about
    this tool. Pre-filling `write` from it would be an assertion drawn from an
    absence — and it would arrive on every unannotated tool of a 117-tool
    server at once, which is the blanket-accept the blank protects against.
    """

    tool = _tool(source_type="mcp", annotations={"mcp_server": True})
    readings = _readings(tool)

    assert [(reading.effect, reading.observed) for reading in readings] == [
        ("write", False)
    ]
    assert propose_effect_declaration(readings) is None


def test_a_default_is_not_folded_into_an_observation_that_reads_the_same() -> None:
    """Provenance class is part of a reading's identity, not a flag to OR.

    An unannotated MCP tool with a `writes_data` hint has *two* readings that
    both say `write`: one observed, one assumed. Merging them produced a single
    `observed=True` row carrying `mcp_protocol_default` among its sources, so
    the questionnaire printed the default under "what this scan read this
    action's effect as" — which is precisely what a default is not.
    """

    tool = _observing(
        "write", source_type="mcp", annotations={"mcp_server": True}
    )
    readings = _readings(tool)

    assert [(r.effect, r.sources, r.observed) for r in readings] == [
        ("write", ("risk_hint:hint0",), True),
        ("write", ("mcp_protocol_default",), False),
    ]
    # The proposal is unaffected: it reasons over values, and the observation
    # is still what unlocks it.
    proposal = propose_effect_declaration(readings)
    assert proposal is not None and proposal.effect == "write"


def test_a_heuristic_cannot_propose_that_an_action_is_read_only() -> None:
    """#357, at the one place pre-filling could quietly reverse it.

    Every other proposal is at or above the evidence, so confirming one
    over-declares. `read` is the single direction where blanket acceptance
    loses safety, and a keyword match is not allowed to establish it.
    """

    readings = _readings(_observing("read"))

    assert [reading.effect for reading in readings] == ["read"]
    assert propose_effect_declaration(readings) is None


def test_a_proposal_is_always_a_value_from_the_closed_vocabulary() -> None:
    """No repository can put a word of its own choosing in front of a reviewer.

    The proposal is written into a YAML document a human pastes into the trust
    root, so where its value comes from is a security property, not a detail.
    """

    vocabulary = set(ActionEffect.__args__)  # type: ignore[attr-defined]
    for count in (1, 2):
        for effects in itertools.combinations(sorted(_TAG_FOR_EFFECT), count):
            proposal = propose_effect_declaration(_readings(_observing(*effects)))
            if proposal is None:
                continue
            assert proposal.effect in vocabulary
            assert set(proposal.risk_tags) <= vocabulary


def test_a_manifest_cannot_be_the_source_that_contradicts_itself() -> None:
    """Found by the sweep above, and it is not specific to a proposal.

    ``risk_tags`` are the repair the ``declaration_below_inferred_evidence``
    row publishes. Applying them to a tool whose server said ``readOnlyHint:
    true`` was reported as "high-confidence read and side-effect evidence
    conflict" attributed to ``tool_source`` — but the side-effect half was the
    reviewer's own line. Escalating past an annotation is exactly what the
    monotone rule allows, and an annotation is untrusted server content that
    may not block a human from over-declaring.
    """

    tool = _observing("code_execution", annotations={"readOnlyHint": True})

    conflicted = assess_tool_semantics(
        tool,
        ActionDeclarationConfig.model_validate(
            {
                "tool": "send_email",
                "effect": "external_communication",
                "risk_tags": ["code_execution"],
                "authority": {"mode": "none"},
            }
        ),
    )
    assert [issue.kind for issue in conflicted.effect.issues] == []
    assert conflicted.effect.status == "declared"

    # Two *sources* disagreeing is still a conflict — that is what the branch
    # is for, and nothing here weakens it.
    two_sources = assess_tool_semantics(
        _tool(annotations={"readOnlyHint": True, "destructiveHint": True}),
        ActionDeclarationConfig.model_validate(
            {"tool": "send_email", "effect": "destructive", "authority": {"mode": "none"}}
        ),
    )
    assert "conflicting_effect_evidence" in {
        issue.kind for issue in two_sources.effect.issues
    }


# --------------------------------------------------------------------------
# 3. The counter counts questions, not declarations
# --------------------------------------------------------------------------


def _mcp_workspace(tmp_path: Path, *, tools: list[dict], actions: list[dict]) -> Path:
    (tmp_path / "tools.json").write_text(json.dumps({"tools": tools}), encoding="utf-8")
    manifest = {
        "version": "0.1",
        "project": {"name": "questionnaire"},
        "agent": {"name": "asst", "declared_purpose": ["test the questionnaire"]},
        "environment": {"target": "local"},
        "tool_sources": [{"id": "src", "type": "mcp", "path": "tools.json"}],
        "agent_bindings": {
            "declarations": [
                {
                    "agent": "root",
                    "complete": True,
                    "tools": [
                        {"tool": tool["name"], "source_id": "src"} for tool in tools
                    ],
                    "handoffs": [],
                    "reason": "reviewed fixture binding",
                }
            ]
        },
    }
    if actions:
        manifest["action_surface"] = {"actions": actions}
    config = tmp_path / "shipgate.yaml"
    config.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    return config


def _coverage(tmp_path: Path, config: Path) -> DeclarationQuestionCoverage:
    report, _ = run_scan(
        config_path=config,
        output_dir=tmp_path / "out",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    assert report.release_decision is not None
    return (
        report.release_decision.evidence_coverage.semantic_coverage.declaration_questions
    )


def test_what_the_scan_proves_is_never_asked_about(tmp_path: Path) -> None:
    """The denominator is questions, not declarations.

    A tool whose effect an annotation establishes and whose authority its auth
    block establishes was never asked anything — so declaring both must not
    show up as two answers. Counting them would let a repository improve its
    progress bar by restating facts the scan already had.
    """

    config = _mcp_workspace(
        tmp_path,
        tools=[
            {
                "name": "docs.lookup",
                "description": "Look up an article.",
                "annotations": {"readOnlyHint": True},
                "auth": {"type": "oauth2", "scopes": ["docs:read"]},
            }
        ],
        actions=[
            {
                "tool": "docs.lookup",
                "effect": "read",
                "scopes": ["docs:read"],
                "authority": {
                    "mode": "scoped",
                    "auth_type": "oauth2",
                    "credential_mode": "delegated",
                },
            }
        ],
    )

    coverage = _coverage(tmp_path, config)

    assert coverage.model_dump() == {
        "total": 0,
        "answered": 0,
        "open": 0,
        "open_by_dimension": {},
        "open_questions": [],
    }
    assert progress_sentence(coverage) == ""


def test_answering_a_question_moves_the_counter(tmp_path: Path) -> None:
    """Open, then answered — with the same tool and the same evidence."""

    tools = [
        {
            "name": "wire_payment",
            "description": "Send a payment.",
            "auth": {"type": "oauth2", "scopes": ["pay:write"]},
        }
    ]
    before = _coverage(tmp_path, _mcp_workspace(tmp_path, tools=tools, actions=[]))

    assert before.total == 1
    assert before.open == 1
    assert before.open_by_dimension == {"effect": 1}
    assert [row.dimension for row in before.open_questions] == ["effect"]
    assert progress_sentence(before) == (
        "Declaration question: 0 of 1 answered; 1 open (1 effect)."
    )

    after_dir = tmp_path / "answered"
    after_dir.mkdir()
    after = _coverage(
        after_dir,
        _mcp_workspace(
            after_dir,
            tools=tools,
            actions=[{"tool": "wire_payment", "effect": "financial_write"}],
        ),
    )

    assert after.model_dump() == {
        "total": 1,
        "answered": 1,
        "open": 0,
        "open_by_dimension": {},
        "open_questions": [],
    }
    assert progress_sentence(after) == "Declaration question: 1 of 1 answered."


def test_the_answerable_kinds_are_real_gap_kinds_and_belong_to_one_dimension() -> None:
    """The routing table is the only spelling; a typo in it would be silent.

    Three surfaces read it — which dimension a gap row answers, which rows
    publish their readings, and which issues make a dimension a question. An
    entry that matches no real gap kind would quietly remove a question from
    all three at once.
    """

    from typing import get_args

    valid = set(get_args(EvidenceGap.model_fields["kind"].annotation))
    seen: set[str] = set()
    for dimension, kinds in ANSWERABLE_ISSUE_KINDS.items():
        assert kinds <= valid, f"{dimension}: {sorted(kinds - valid)} are not gap kinds"
        assert not kinds & seen, "a gap kind cannot answer two dimensions"
        seen |= kinds
    assert set(DIMENSION_BY_GAP_KIND) == seen


#: Stands in for "the pin this scan derives for that tool", which cannot be
#: written into a static table. Substituted for the real value below.
_PIN = "<CURRENT_PIN>"


#: One reachable configuration per answerable kind, with the declaration that
#: is supposed to close it. `conflicting_effect_evidence` appears twice on
#: purpose: it is raised about two different surfaces, and only one of them is
#: a question a declaration can answer.
_ROUND_TRIP_CASES: dict[str, tuple[dict, dict, dict]] = {
    # kind: (tool kwargs, declaration that raised it or {}, the answer)
    "missing_effect_evidence": (
        {"source_type": "mcp", "annotations": {"mcp_server": True}},
        {},
        {"effect": "write"},
    ),
    "inferred_effect_only": (
        {"risk_hints": "external_communication"},
        {},
        {"effect": "external_communication"},
    ),
    "declaration_below_inferred_evidence": (
        {"risk_hints": "external_communication"},
        {"effect": "read"},
        {"effect": "external_communication"},
    ),
    "conflicting_effect_evidence": (
        {"annotations": {"httpMethod": "POST"}},
        {"effect": "read"},
        {"effect": "write"},
    ),
    "missing_authority_evidence": (
        {},
        {},
        {"authority": {"mode": "none"}},
    ),
    "conflicting_authority_evidence": (
        {"auth": {"type": "oauth2", "scopes": ["a:write"]}},
        {"authority": {"mode": "none"}},
        {
            "scopes": ["a:write"],
            "authority": {"mode": "scoped", "auth_type": "oauth2"},
        },
    ),
    # A pinned declaration whose pin names evidence this scan does not read.
    # The answer is the same declaration re-confirmed against what it reads
    # now — the one-line edit the row asks for (#410 §E).
    "declaration_drift": (
        {"risk_hints": "external_communication"},
        {"effect": "external_communication", "basis": "confirmed:0"},
        {"effect": "external_communication", "basis": _PIN},
    ),
}


def _with_pin(values: dict, tool) -> dict:
    """Resolve the ``_PIN`` sentinel against what this scan reads for ``tool``."""

    pin = confirmed_basis(effect_readings(assess_tool_semantics(tool, None).effect))
    return {key: (pin if value == _PIN else value) for key, value in values.items()}


def test_every_answerable_kind_has_an_answer_that_closes_it() -> None:
    """The finish line the counter advertises has to be reachable.

    `partial_authority_evidence` was counted as a question while the resolver
    preserved it *whatever the manifest declared* — so an MCP tool published
    with scopes and no auth type asked one authority question that writing the
    exact scoped block the scaffold requested left at `0 of 1 answered`
    forever. This walks every kind in the table: raise it, apply the answer,
    re-resolve, and require the question to be answered.
    """

    dimension_of = {
        kind: dimension
        for dimension, kinds in ANSWERABLE_ISSUE_KINDS.items()
        for kind in kinds
    }
    assert set(_ROUND_TRIP_CASES) >= set(dimension_of), (
        "a kind was added to ANSWERABLE_ISSUE_KINDS with no round-trip case"
    )

    for kind, (tool_kwargs, raising, answer) in _ROUND_TRIP_CASES.items():
        dimension = dimension_of[kind]
        hint = tool_kwargs.pop("risk_hints", None) if "risk_hints" in tool_kwargs else None
        tool = _observing(hint, **tool_kwargs) if hint else _tool(**tool_kwargs)
        raised = assess_tool_semantics(
            tool,
            ActionDeclarationConfig.model_validate({"tool": "send_email", **raising})
            if raising
            else None,
        )
        assert kind in {issue.kind for issue in _dim_issues(raised, dimension)}, (
            f"{kind}: the fixture no longer raises it"
        )

        answered = assess_tool_semantics(
            tool,
            ActionDeclarationConfig.model_validate(
                {"tool": "send_email", **_with_pin(answer, tool)}
            ),
        )
        tool.semantic_assessment = answered
        questions = {
            (question.dimension, question.answered)
            for question in declaration_questions([tool])
        }
        assert (dimension, False) not in questions, (
            f"{kind}: applying {answer} left the {dimension} question open — "
            "the counter advertises a finish line this answer cannot reach"
        )


def _rendered_gaps(tmp_path: Path, *, tools: list[dict], actions: list[dict]):
    """The evidence-gap rows a real scan publishes, as the reader receives them."""

    report, _ = run_scan(
        config_path=_mcp_workspace(tmp_path, tools=tools, actions=actions),
        output_dir=tmp_path / "out",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    assert report.release_decision is not None
    return report, report.release_decision.evidence_coverage.evidence_gaps


def test_a_source_owned_row_points_at_the_source_it_names(tmp_path: Path) -> None:
    """A row's machine-readable target must agree with its own instruction.

    `partial_authority_evidence` correctly says "a reviewed action declaration
    cannot close this row" — and then set `path` to
    `shipgate.yaml#action_surface.actions[...]`, which is where a coding agent
    and the short-form `Fix at …` line both go. The contradiction is
    operational, not cosmetic: it sends the reader to write exactly the block
    the sentence above it says will not work.
    """

    report, gaps = _rendered_gaps(
        tmp_path,
        tools=[
            {
                "name": "docs.lookup",
                "description": "Look up an article.",
                "annotations": {"readOnlyHint": True},
                # Scopes with no auth type: `_source_authority` reads this as
                # `partial`, and no declaration changes it.
                "auth": {"scopes": ["docs:read"]},
            }
        ],
        actions=[],
    )
    gap = next(item for item in gaps if item.kind == "partial_authority_evidence")
    action = gap.next_action

    assert action.kind == "provide_source"
    assert action.declaration_template is None
    assert action.path == "tools.json#/tools/0"
    assert "action_surface" not in (action.path or "")
    assert "cannot close this row" in action.expects
    # And the short forms that project this row agree with it.
    assert report.release_decision is not None
    assert "shipgate.yaml#action_surface" not in report.release_decision.reason


def test_a_self_contradicting_source_is_not_sent_to_the_manifest(tmp_path: Path) -> None:
    """The published repair, not only the counter.

    Excluding this row from the questionnaire was half the fix. The other half
    is what the row still told people to do: the generic conflict branch kept
    publishing every effect value, the manifest action row, and "add a
    conservative reviewed action declaration" — and adding the exact
    conservative `effect: destructive` leaves the identical conflict.
    """

    tools = [
        {
            "name": "confused",
            "description": "Two annotations.",
            "annotations": {"readOnlyHint": True, "destructiveHint": True},
        }
    ]
    _, gaps = _rendered_gaps(tmp_path, tools=tools, actions=[])
    action = next(
        item for item in gaps if item.kind == "conflicting_effect_evidence"
    ).next_action

    assert action.kind == "provide_source"
    assert action.declaration_template is None
    assert action.path == "tools.json#/tools/0"
    # Publishing the effect vocabulary here invites the declaration the row
    # cannot be closed by.
    assert not set(action.accepted_values) & set(ActionEffect.__args__)  # type: ignore[attr-defined]
    assert "A reviewed action declaration cannot close this row" in action.expects

    # ...and that is true: the conservative declaration leaves the same row.
    answered = tmp_path / "answered"
    answered.mkdir()
    _, after = _rendered_gaps(
        answered,
        tools=tools,
        actions=[{"tool": "confused", "effect": "destructive", "authority": {"mode": "none"}}],
    )
    assert "conflicting_effect_evidence" in {item.kind for item in after}


def test_a_reviewed_risk_override_is_the_manifest_speaking(tmp_path: Path) -> None:
    """`risk_overrides.tags` is the manifest's other positive-risk surface.

    It reaches the effect dimension as `risk_hint:manual` with basis
    `reviewed_declaration` — not one of `DECLARATION_CLAIM_SOURCES` — so the
    source-conflict test counted a human's reviewed tag as the *source*
    contradicting a `readOnlyHint`. Declaring the matching effect and risk tag
    left the same conflict, contradicting the rule that an untrusted
    annotation may never block a reviewed over-declaration.
    """

    config = _mcp_workspace(
        tmp_path,
        tools=[
            {
                "name": "overridden",
                "description": "A reviewed override.",
                "annotations": {"readOnlyHint": True},
            }
        ],
        actions=[
            {
                "tool": "overridden",
                "effect": "code_execution",
                "risk_tags": ["code_execution"],
                "authority": {"mode": "none"},
            }
        ],
    )
    manifest = yaml.safe_load(config.read_text(encoding="utf-8"))
    manifest["risk_overrides"] = {
        "tools": {
            "overridden": {
                "tags": ["code_execution"],
                "confidence": "manual",
                "reason": "reviewed - this tool shells out",
            }
        }
    }
    config.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

    report, _ = run_scan(
        config_path=config,
        output_dir=tmp_path / "out",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )

    assert report.release_decision is not None
    coverage = report.release_decision.evidence_coverage
    assert "conflicting_effect_evidence" not in {
        gap.kind for gap in coverage.evidence_gaps
    }
    questions = coverage.semantic_coverage.declaration_questions
    assert questions.open == 0 and questions.answered == questions.total


def test_a_source_that_contradicts_itself_is_not_a_declaration_question() -> None:
    """The second branch of `conflicting_effect_evidence`, which no answer closes.

    A server publishing both `readOnlyHint: true` and `destructiveHint: true`
    contradicts itself, and the resolver reads that before it reads the
    manifest — so the issue is attributed to `tool_source` and stands whatever
    is declared. Counting it is the `partial_authority_evidence` mistake one
    branch deeper.
    """

    tool = _tool(annotations={"readOnlyHint": True, "destructiveHint": True})
    for declared in (None, "destructive", "read"):
        assessment = assess_tool_semantics(
            tool,
            None
            if declared is None
            else ActionDeclarationConfig.model_validate(
                {"tool": "send_email", "effect": declared}
            ),
        )
        assert "conflicting_effect_evidence" in {
            issue.kind for issue in assessment.effect.issues
        }
        tool.semantic_assessment = assessment
        assert not [
            question
            for question in declaration_questions([tool])
            if question.dimension == "effect"
        ], f"declared={declared!r}: counted a question no declaration can close"


def _dim_issues(assessment, dimension: str):
    return assessment.effect.issues if dimension == "effect" else assessment.authority.issues


def test_the_counts_are_internally_consistent() -> None:
    """``total == answered + open``, and the breakdown sums to ``open``."""

    tools = [
        _observing("financial_write"),
        _observing("external_communication", id="t2", name="send_email2"),
        _tool(id="t3", name="quiet", annotations={"readOnlyHint": True}),
    ]
    for tool in tools:
        tool.semantic_assessment = assess_tool_semantics(tool, None)
    questions = declaration_questions(tools)
    still_open = [question for question in questions if not question.answered]
    by_dimension: dict[str, int] = {}
    for question in still_open:
        by_dimension[question.dimension] = by_dimension.get(question.dimension, 0) + 1

    assert len(questions) == len(still_open) + sum(
        1 for question in questions if question.answered
    )
    assert sum(by_dimension.values()) == len(still_open)


def test_questions_lead_with_the_action_that_can_move_the_verdict() -> None:
    """Money and outward communication first (the walk-4 finding).

    Declaring 2 of 12 tools reached a verdict on ``adk-samples#1745``, and both
    were of this kind. Alphabetical order would have buried them.
    """

    quiet = _observing("write", id="t_a", name="a_write_tool")
    money = _observing("financial_write", id="t_z", name="z_wire_payment")
    for tool in (quiet, money):
        tool.semantic_assessment = assess_tool_semantics(tool, None)

    questions = declaration_questions([quiet, money])

    # Both tools owe both dimensions, so the assertion is about which action
    # leads, and that effect leads authority within it.
    assert [
        (question.subject.split(" ")[0], question.dimension) for question in questions
    ] == [
        ("z_wire_payment", "effect"),
        ("z_wire_payment", "authority"),
        ("a_write_tool", "effect"),
        ("a_write_tool", "authority"),
    ]


# --------------------------------------------------------------------------
# The rendered questionnaire
# --------------------------------------------------------------------------


def _gap(
    kind: str,
    *,
    subject_id: str,
    template: dict | None,
    readings: list[EvidenceReading] | None = None,
    name: str = "send_email",
) -> EvidenceGap:
    return EvidenceGap(
        kind=kind,  # type: ignore[arg-type]
        subject=f"{name} [closer]",
        subject_id=subject_id,
        source_type="google_adk",
        source_ref="agent.py",
        why="test",
        next_action=EvidenceGapAction(
            kind="declare_action_effect",  # type: ignore[arg-type]
            path=f"shipgate.yaml#action_surface.actions[tool='{name}']",
            why="test",
            expects="Correct the source annotations, then rerun verification.",
            declaration_template=template,
            observed_readings=readings or [],
        ),
    )


def _coverage_rows(*rows: tuple[str, str], answered: int = 0) -> DeclarationQuestionCoverage:
    by_dimension: dict[str, int] = {}
    for _, dimension in rows:
        by_dimension[dimension] = by_dimension.get(dimension, 0) + 1
    return DeclarationQuestionCoverage(
        total=len(rows) + answered,
        answered=answered,
        open=len(rows),
        open_by_dimension=by_dimension,
        open_questions=[
            DeclarationQuestionRow(
                subject="send_email [closer]", subject_id=subject_id, dimension=dimension
            )
            for subject_id, dimension in rows
        ],
    )


def test_the_questionnaire_numbers_agree_with_the_published_counter() -> None:
    """Every open question is numbered exactly once, out of the same total."""

    gaps = [
        _gap(
            "inferred_effect_only",
            subject_id="t1",
            template={"tool": "send_email", "effect": "financial_write"},
            readings=[EvidenceReading(effect="financial_write", sources=["risk_hint:keyword"])],
        ),
        _gap(
            "missing_authority_evidence",
            subject_id="t1",
            template={"tool": "send_email", "authority": {"mode": REVIEW_REQUIRED_SENTINEL}},
        ),
    ]
    coverage = _coverage_rows(("t1", "effect"), ("t1", "authority"), answered=1)

    scaffold = build_declaration_scaffold(gaps, questions=coverage)

    assert scaffold is not None
    assert "Declaration questions: 1 of 3 answered; 2 open (1 effect, 1 authority)." in scaffold
    # One manifest row answers both, so the banner names a range rather than
    # pretending the file has fewer questions than the counter reports.
    assert "Questions 1–2 of 2 · effect, authority · send_email [closer]" in scaffold
    assert "Question 1 of" not in scaffold
    assert scaffold.count("tool: send_email") == 1


def test_a_counted_question_with_no_blank_is_still_shown() -> None:
    """Numbering may not skip.

    A conflict between two sources is a real open question — it is counted and
    it is the next thing to resolve — but its repair is in the source, so no
    template is offered. Dropping it would leave the file disagreeing with its
    own header about how many questions there are.
    """

    gaps = [
        _gap("conflicting_effect_evidence", subject_id="t1", template=None),
        _gap(
            "missing_authority_evidence",
            subject_id="t2",
            name="lookup",
            template={"tool": "lookup", "authority": {"mode": REVIEW_REQUIRED_SENTINEL}},
        ),
    ]
    coverage = _coverage_rows(("t1", "effect"), ("t2", "authority"))

    scaffold = build_declaration_scaffold(gaps, questions=coverage)

    assert scaffold is not None
    assert "Question 1 of 2 · effect" in scaffold
    assert "Question 2 of 2 · authority" in scaffold
    assert "No block is offered for this one" in scaffold
    assert "Correct the source annotations" in scaffold


def test_a_pre_filled_value_says_it_is_a_proposal_at_the_cursor() -> None:
    """A reader who scrolls straight to the field must still be told.

    A filled-in value that looks like something they wrote on an earlier pass
    is the one way a proposal could be mistaken for a decision.
    """

    gaps = [
        _gap(
            "inferred_effect_only",
            subject_id="t1",
            template={"tool": "send_email", "effect": "external_communication"},
            readings=[
                EvidenceReading(effect="external_communication", sources=["risk_hint:keyword"])
            ],
        )
    ]

    scaffold = build_declaration_scaffold(gaps, questions=_coverage_rows(("t1", "effect")))

    assert scaffold is not None
    assert "What this scan read this action's effect as:" in scaffold
    assert "external_communication — risk_hint:keyword" in scaffold
    assert "proposed from the evidence above — keep it to confirm, or replace it." in scaffold
    assert yaml.safe_load(scaffold)["effect"] == "external_communication"


def test_a_default_reading_is_shown_but_marked_as_not_evidence() -> None:
    gaps = [
        _gap(
            "missing_effect_evidence",
            subject_id="t1",
            template={"tool": "send_email", "effect": REVIEW_REQUIRED_SENTINEL},
            readings=[
                EvidenceReading(
                    effect="write", sources=["mcp_protocol_default"], observed=False
                )
            ],
        )
    ]

    scaffold = build_declaration_scaffold(gaps, questions=_coverage_rows(("t1", "effect")))

    assert scaffold is not None
    assert "Assumed in the absence of evidence, and never proposed from:" in scaffold
    assert "write — mcp_protocol_default" in scaffold
    assert "Proposed below:" not in scaffold
    assert yaml.safe_load(scaffold)["effect"] == REVIEW_REQUIRED_SENTINEL


def test_repository_controlled_text_cannot_forge_a_reading_line() -> None:
    """A claim source embeds repository-controlled names (``risk_hint:<src>``).

    The readings are comments directly above a value a reader pastes, so a
    forged newline there would render a filled-in field nobody wrote — the
    self-declaration surface #268 closed.
    """

    gaps = [
        _gap(
            "inferred_effect_only",
            subject_id="t1",
            template={"tool": "send_email", "effect": "write"},
            readings=[
                EvidenceReading(
                    effect="write",
                    sources=["risk_hint:x\neffect: read\n# "],
                )
            ],
        )
    ]

    scaffold = build_declaration_scaffold(gaps, questions=_coverage_rows(("t1", "effect")))

    assert scaffold is not None
    body = yaml.safe_load(scaffold)
    assert body == {"tool": "send_email", "effect": "write"}
    assert "\neffect: read" not in scaffold


def test_every_counted_question_is_accounted_for_exactly_once() -> None:
    """The numbering is a promise: 1..open, each appearing once.

    Blocks are merged by manifest target, and a merge that kept the first
    block's identity would leave the questions the second one answered
    numbered by the counter and answered by nothing in the file.
    """

    template = {"tool": "send_email", "effect": "write"}
    gaps = [
        # Byte-identical templates at one path collapse to a single block. Both
        # subjects' questions must survive that collapse.
        _gap("inferred_effect_only", subject_id="t1", template=dict(template)),
        _gap("inferred_effect_only", subject_id="t2", template=dict(template)),
        _gap(
            "missing_authority_evidence",
            subject_id="t3",
            name="lookup",
            template={"tool": "lookup", "authority": {"mode": REVIEW_REQUIRED_SENTINEL}},
        ),
    ]
    coverage = _coverage_rows(("t1", "effect"), ("t2", "effect"), ("t3", "authority"))

    scaffold = build_declaration_scaffold(gaps, questions=coverage)

    assert scaffold is not None
    for number in range(1, coverage.open + 1):
        rendered = [
            line
            for line in scaffold.splitlines()
            if f"Question {number} of {coverage.open}" in line
            or f"Questions {number}–" in line
            or f"–{number} of {coverage.open}" in line
        ]
        assert len(rendered) == 1, f"question {number} appears {len(rendered)} times"


def test_the_pr_comment_reports_progress_and_omits_it_when_nothing_was_asked(
    tmp_path: Path,
) -> None:
    """The reviewer surface for `verify` gets the finish line too.

    A gap tally tells a reviewer what is wrong; the counter tells the author
    how much is left, and the PR is where they read it.
    """

    from agents_shipgate.cli.verify.orchestrator import _derive_verifier_control
    from agents_shipgate.report.pr_comment import render_pr_comment
    from agents_shipgate.schemas.verifier import (
        AuthorizationEvaluationV1,
        VerifierArtifact,
        VerifierCapabilityReview,
        VerifierDiffStatus,
        map_merge_verdict,
    )

    config = _mcp_workspace(
        tmp_path,
        tools=[
            {
                "name": "wire_payment",
                "description": "Send a payment.",
                "auth": {"type": "oauth2", "scopes": ["pay:write"]},
            }
        ],
        actions=[],
    )
    report, _ = run_scan(
        config_path=config,
        output_dir=tmp_path / "out",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    assert report.release_decision is not None
    review = VerifierCapabilityReview()
    verdict = map_merge_verdict(report.release_decision.decision)
    verifier = VerifierArtifact(
        workspace=str(tmp_path),
        diff_status=VerifierDiffStatus(),
        config="shipgate.yaml",
        authorization=AuthorizationEvaluationV1.not_requested(),
        trigger={"rationale": "1 run_shipgate rule(s) matched."},
        execution="succeeded",
        head_status="succeeded",
        release_decision=report.release_decision,
        decision=report.release_decision.decision,
        merge_verdict=verdict,
        applicability="verified",
        control=_derive_verifier_control(
            execution="succeeded",
            merge_verdict=verdict,
            release_decision=report.release_decision,
            fix_task=None,
            capability_review=review,
            headline="test",
            first_next_action_override=None,
            base_status="succeeded",
            base_ref="origin/main",
            diff_status=VerifierDiffStatus(completeness="complete"),
        ),
        headline="test",
        capability_review=review,
    )

    comment = render_pr_comment(verifier, report=report)
    assert "- Declaration question: 0 of 1 answered; 1 open \\(1 effect\\)" in comment

    report.release_decision.evidence_coverage.semantic_coverage.declaration_questions = (
        DeclarationQuestionCoverage()
    )
    assert "Declaration question" not in render_pr_comment(verifier, report=report)


def test_two_tools_sharing_a_display_name_keep_their_questions_contiguous() -> None:
    """A block may only claim numbers it owns.

    Two canonical tools can render the same `name [provider]` label. Ordering
    by dimension before tool interleaved them — A.effect, B.effect,
    A.authority — so the row merged for A owned questions 1 and 3, and the
    banner announced "Questions 1-3", claiming the one in between that belongs
    to B.
    """

    tools = []
    for tool_id in ("t_a", "t_b"):
        tool = _observing("external_communication", id=tool_id)
        tool.semantic_assessment = assess_tool_semantics(tool, None)
        tools.append(tool)

    numbered = list(enumerate(declaration_questions(tools), start=1))
    per_tool: dict[str, list[int]] = {}
    for number, question in numbered:
        per_tool.setdefault(question.subject_id, []).append(number)

    assert len(per_tool) == 2
    for tool_id, numbers in per_tool.items():
        assert numbers[-1] - numbers[0] == len(numbers) - 1, (
            f"{tool_id} owns non-contiguous questions {numbers}"
        )


def test_a_banner_never_renders_a_gap_in_its_numbers_as_a_range() -> None:
    """The renderer must not be able to lie even if ordering changes.

    Contiguity is a property of the ordering; a banner that renders any set as
    a span is one edit away from claiming another action's question.
    """

    from agents_shipgate.cli.scan.declarations import _numbers_phrase

    assert _numbers_phrase([3]) == "Question 3"
    assert _numbers_phrase([1, 2]) == "Questions 1–2"
    assert _numbers_phrase([1, 3]) == "Questions 1 and 3"
    assert _numbers_phrase([1, 3, 5]) == "Questions 1, 3 and 5"


def test_blocks_and_unanswerable_notes_are_interleaved_by_number() -> None:
    """Numbering that does not run in order is worse than no numbering.

    Blocks and comment-only entries are two renderings of one queue. Emitting
    every block and *then* every note printed a file numbered 2, 3, 1.
    """

    gaps = [
        # An action with a conflict (no block) and an open authority question
        # (a block), plus a second action that leads on risk.
        _gap("conflicting_effect_evidence", subject_id="t1", template=None, name="quiet"),
        _gap(
            "missing_authority_evidence",
            subject_id="t1",
            name="quiet",
            template={"tool": "quiet", "authority": {"mode": REVIEW_REQUIRED_SENTINEL}},
        ),
        _gap(
            "inferred_effect_only",
            subject_id="t2",
            name="wire_payment",
            template={"tool": "wire_payment", "effect": "financial_write"},
            readings=[EvidenceReading(effect="financial_write", sources=["risk_hint:keyword"])],
        ),
    ]
    coverage = _coverage_rows(("t2", "effect"), ("t1", "effect"), ("t1", "authority"))

    scaffold = build_declaration_scaffold(gaps, questions=coverage)

    assert scaffold is not None
    banners = [line for line in scaffold.splitlines() if "── Question" in line]
    numbers = [int(line.split()[3].rstrip("s")) for line in banners]
    assert numbers == sorted(numbers), banners


def test_a_long_subject_cannot_run_the_banner_off_the_line() -> None:
    """A tool name is repository-controlled and unbounded.

    Eliding it is safe here and nowhere near a machine route: the block
    directly beneath carries the exact `tool` and `tool_id` to act on.
    """

    gaps = [
        _gap(
            "inferred_effect_only",
            subject_id="t1",
            name="x" * 300,
            template={"tool": "x" * 300, "effect": "write"},
            readings=[EvidenceReading(effect="write", sources=["risk_hint:keyword"])],
        )
    ]

    scaffold = build_declaration_scaffold(gaps, questions=_coverage_rows(("t1", "effect")))

    assert scaffold is not None
    banners = [line for line in scaffold.splitlines() if "── Question" in line]
    assert banners
    for line in banners:
        assert len(line) <= 78, line
    # No line the generator composes may end in whitespace: a trailing space is
    # invisible in review and every diff tool flags it.
    for line in scaffold.splitlines():
        assert line == line.rstrip(), f"trailing whitespace: {line!r}"
    # The full name is still there, in the value a reader has to paste. The
    # banner elides; the machine route never does.
    assert yaml.safe_load(scaffold)["tool"] == "x" * 300


def test_a_forged_subject_cannot_break_out_of_the_question_banner() -> None:
    """The banner interpolates a tool name, which the repository controls."""

    gaps = [
        _gap(
            "inferred_effect_only",
            subject_id="t1",
            template={"tool": "send_email", "effect": "write"},
            readings=[EvidenceReading(effect="write", sources=["risk_hint:keyword"])],
        )
    ]
    gaps[0].subject = "send_email\neffect: read\n# [closer]"

    scaffold = build_declaration_scaffold(gaps, questions=_coverage_rows(("t1", "effect")))

    assert scaffold is not None
    assert yaml.safe_load(scaffold) == {"tool": "send_email", "effect": "write"}
    assert "\neffect: read" not in scaffold


def test_the_row_the_cli_names_is_the_first_question(tmp_path: Path) -> None:
    """One order, not two.

    ``Improve evidence:``, the decision ``reason``, and
    ``first_recommended_action`` all project ``primary_evidence_gap`` — the
    first addressable row of ``evidence_gaps``. The questionnaire numbers its
    blocks from ``open_questions``. Left unaligned, one led with whatever
    sorted first by tool name and the other with whatever could move the
    verdict.
    """

    from agents_shipgate.core.evidence_actions import primary_evidence_gap

    config = _mcp_workspace(
        tmp_path,
        tools=[
            {
                "name": "a_send_email",
                "description": "Send an email to the customer.",
                "auth": {"type": "oauth2", "scopes": ["mail:send"]},
            },
            {
                "name": "z_refund_payment",
                "description": "Issue a refund for an order.",
                "auth": {"type": "oauth2", "scopes": ["pay:refund"]},
            },
        ],
        actions=[],
    )
    report, _ = run_scan(
        config_path=config,
        output_dir=tmp_path / "out",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )

    assert report.release_decision is not None
    coverage = report.release_decision.evidence_coverage
    first_question = coverage.semantic_coverage.declaration_questions.open_questions[0]
    # The risky action leads, not the alphabetically-first one.
    assert first_question.subject.startswith("z_refund_payment")

    selected = primary_evidence_gap(coverage)
    assert selected is not None
    assert selected.subject_id == first_question.subject_id


# --------------------------------------------------------------------------
# End to end: the adoption walk this increment exists for
# --------------------------------------------------------------------------


@pytest.mark.parametrize("workspace_name", ["walk"])
def test_confirming_one_proposal_reaches_a_verdict(
    tmp_path: Path, workspace_name: str
) -> None:
    """The measured outcome from the fourth ``adk-samples#1745`` walk.

    Baseline: the money tool's effect is inferred, nothing is declared, and the
    run reports no critical finding. Confirm the one value the questionnaire
    proposes and the gate names the risk. That is the whole point of the
    increment — the questions that matter are answerable from the file itself.
    """

    workspace = tmp_path / workspace_name
    workspace.mkdir()
    config = _mcp_workspace(
        workspace,
        tools=[
            {
                "name": "create_sales_order",
                "description": "Create a sales order and charge the customer.",
                "auth": {"type": "oauth2", "scopes": ["sap:orders.write"]},
            }
        ],
        actions=[],
    )
    report, _ = run_scan(
        config_path=config,
        output_dir=workspace / "out",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    assert report.release_decision is not None
    gap = next(
        item
        for item in report.release_decision.evidence_coverage.evidence_gaps
        if item.kind in ANSWERABLE_ISSUE_KINDS["effect"]
    )
    template = gap.next_action.declaration_template
    assert template is not None
    assert template["effect"] == "financial_write"
    assert REVIEW_REQUIRED_SENTINEL not in json.dumps(template)

    # Confirm exactly what the row published — nothing else changes.
    answered_dir = tmp_path / "answered"
    answered_dir.mkdir()
    answered = _mcp_workspace(
        answered_dir,
        tools=[
            {
                "name": "create_sales_order",
                "description": "Create a sales order and charge the customer.",
                "auth": {"type": "oauth2", "scopes": ["sap:orders.write"]},
            }
        ],
        actions=[{key: value for key, value in template.items() if key != "tool_id"}],
    )
    after, _ = run_scan(
        config_path=answered,
        output_dir=answered_dir / "out",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )

    assert after.release_decision is not None
    assert after.release_decision.decision == "blocked"
    assert {item.check_id for item in after.release_decision.blockers} >= {
        "SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING"
    }
    questions = (
        after.release_decision.evidence_coverage.semantic_coverage.declaration_questions
    )
    assert questions.answered == 1
    assert "effect" not in questions.open_by_dimension
