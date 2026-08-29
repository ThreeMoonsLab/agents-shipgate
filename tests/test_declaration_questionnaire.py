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
    effect_is_bounded,
    effect_is_measured,
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


def _mcp_workspace(
    tmp_path: Path,
    *,
    tools: list[dict],
    actions: list[dict],
    risk_overrides: dict | None = None,
) -> Path:
    (tmp_path / "tools.json").write_text(json.dumps({"tools": tools}), encoding="utf-8")
    manifest: dict = {
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
    if risk_overrides:
        manifest["risk_overrides"] = risk_overrides
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
# 4. The order ranks by the ceiling, not by what was already inferred (#419)
# --------------------------------------------------------------------------
#
# A proposal is offered only where something was observed, and the order used
# to be the strength of that same observation — so the questions that arrived
# with a draft answer systematically outranked the questions that arrived
# blank. On the reference walk that put three already-drafted mail tools at
# Q2-Q4 and the financial write, the single question that produced both
# ``critical`` blockers, at Q6.


def _walk_shaped_catalog() -> list[Tool]:
    """The fifth ``adk-samples#1745`` walk in miniature.

    Three tools the scan read as outward communication — the ones that arrive
    with a proposed answer — and four it read nothing about at all, including
    the financial write that produces every blocker once it is answered.
    """

    tools = [
        _observing("external_communication", id="t_send", name="send_email"),
        _observing("external_communication", id="t_list", name="list_messages"),
        _observing("external_communication", id="t_mgr", name="get_manager_email"),
        _tool(id="t_order", name="create_sap_sales_order"),
        _tool(id="t_status", name="update_opportunity_status"),
        _tool(id="t_map", name="map_salesforce_account_to_sap_bp"),
        _tool(id="t_items", name="get_opportunity_line_items"),
    ]
    for tool in tools:
        tool.semantic_assessment = assess_tool_semantics(tool, None)
    return tools


def _has_proposal(tool: Tool) -> bool:
    assert tool.semantic_assessment is not None
    return propose_effect_declaration(_readings(tool)) is not None


def test_the_first_effect_question_is_one_the_scan_could_not_draft() -> None:
    """The reference walk's headline symptom, asserted (#419).

    An action nothing was observed about is not a low-risk action; it is an
    unmeasured one, and it is exactly where a human answer carries the most new
    information. A reader working top to bottom must not have to confirm three
    drafts before reaching it.
    """

    tools = _walk_shaped_catalog()
    by_id = {tool.id: tool for tool in tools}

    effect_questions = [
        question
        for question in declaration_questions(tools)
        if question.dimension == "effect"
    ]

    first = effect_questions[0]
    assert not _has_proposal(by_id[first.subject_id]), (
        f"the questionnaire leads with {first.subject!r}, which already carries "
        "a proposed answer"
    )
    # And the money question is reached before any of the drafted ones.
    order = [question.subject_id for question in effect_questions]
    assert order.index("t_order") < min(
        order.index(subject_id) for subject_id in ("t_send", "t_list", "t_mgr")
    )


def test_a_drafted_question_never_precedes_an_unbounded_one() -> None:
    """The invariant, stated on the mechanism rather than on the symptom.

    Ordering asks :func:`effect_is_bounded` — has anything other than a human's
    answer already held this effect down. Where nothing has, the question sorts
    first, and on a catalog like this one that half is also exactly the half no
    proposal is offered for, which is the correlation the issue reported.

    Note what is deliberately *not* asserted: that a blank question precedes a
    drafted one full stop. An action's own authority question arrives blank and
    still follows its effect question — they are one manifest row and one
    block, and separating them would number that block "Questions 1 and 7".
    """

    tools = _walk_shaped_catalog()
    bounded = {
        tool.id: effect_is_bounded(tool.semantic_assessment.effect)  # type: ignore[union-attr]
        for tool in tools
    }
    questions = declaration_questions(tools)
    # Every question here is about one action, so ``subject_id`` is a tool id.
    assert {question.subject_kind for question in questions} == {"action"}

    last_unbounded = max(
        index
        for index, question in enumerate(questions)
        if not bounded[question.subject_id]
    )
    first_bounded = min(
        index for index, question in enumerate(questions) if bounded[question.subject_id]
    )
    assert last_unbounded < first_bounded, [
        (question.subject, question.dimension, bounded[question.subject_id])
        for question in questions
    ]
    # On this catalog the bounded half is exactly the drafted half, which is
    # what makes the statement above about proposals at all.
    for question in questions:
        if question.dimension != "effect":
            continue
        tool = next(item for item in tools if item.id == question.subject_id)
        assert _has_proposal(tool) is bounded[question.subject_id]


def test_an_unbounded_action_outranks_every_bounded_one() -> None:
    """Even a measured ``destructive``: the ceiling is above the whole table.

    The unbounded action is named as harmlessly as the vocabulary allows — it
    lands in the band's *lowest* tier — so nothing but "nothing bounds it" can
    be putting it first.
    """

    razed = _observing("destructive", id="t_razed", name="drop_all_tables")
    unread = _tool(id="t_unread", name="get_status")
    for tool in (razed, unread):
        tool.semantic_assessment = assess_tool_semantics(tool, None)

    questions = declaration_questions([razed, unread])

    assert [question.subject_id for question in questions][:2] == [
        "t_unread",
        "t_unread",
    ]


def test_unbounded_questions_are_ordered_by_what_their_names_suggest() -> None:
    """The tiebreaker, and the only place a name is allowed to decide anything.

    Every action here is equally unbounded, so without the band the order is
    alphabetical — which is no order at all for a reader with limited
    attention. The band never establishes an effect; it decides which blank a
    reader sees first among blanks they have to fill either way.
    """

    tools = [
        _tool(id="t_get", name="get_account"),
        _tool(id="t_handle", name="handle_account"),
        _tool(id="t_delete", name="delete_account"),
    ]
    for tool in tools:
        tool.semantic_assessment = assess_tool_semantics(tool, None)

    ordered = [
        question.subject_id
        for question in declaration_questions(tools)
        if question.dimension == "effect"
    ]

    assert ordered == ["t_delete", "t_handle", "t_get"]


def test_a_name_cannot_reorder_an_action_the_scan_actually_read() -> None:
    """The negative control for the band.

    A repository names its own tools. If the band applied to measured actions,
    calling a financial write ``get_status`` would push the question that fires
    both blockers below one the scan read as a plain write.
    """

    money = _observing("financial_write", id="t_money", name="get_status")
    plain = _observing("write", id="t_plain", name="create_everything")
    for tool in (money, plain):
        tool.semantic_assessment = assess_tool_semantics(tool, None)

    ordered = [
        question.subject_id
        for question in declaration_questions([money, plain])
        if question.dimension == "effect"
    ]

    assert ordered == ["t_money", "t_plain"]


def test_the_band_reaches_nothing_but_the_questionnaire_order() -> None:
    """A name-shaped hint may order questions and nothing else (#419).

    Stated as a source fact rather than a behavioural one, because the failure
    it guards against is a *future* call site: the moment this is read
    somewhere a claim, an issue, or a verdict can see, an unreviewed reading of
    a repository-chosen string becomes evidence.
    """

    root = Path(__file__).resolve().parents[1] / "src" / "agents_shipgate"
    callers = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if "name_shape_band" in path.read_text(encoding="utf-8")
    }

    assert callers == {"core/risk_hints.py", "core/declaration_questions.py"}, (
        "a name-shaped reading reached a new module; it may order questions and "
        "nothing else"
    )


def test_the_proposal_gate_says_exactly_what_is_drafted() -> None:
    """Exhaustive: ``effect_is_measured`` agrees with what is actually drafted.

    It is the proposal gate and nothing else. Ordering asks
    :func:`effect_is_bounded` instead — see the test below for the shapes where
    the two deliberately disagree.
    """

    checked = 0
    for count in (0, 1, 2):
        for effects in itertools.combinations(sorted(_TAG_FOR_EFFECT), count):
            readings = _readings(_observing(*effects))
            checked += 1
            assert effect_is_measured(readings) is (
                propose_effect_declaration(readings) is not None
            ), f"{effects}: the proposal gate disagrees with the proposal"
    assert checked > 40, "the sweep stopped exercising the gate"


#: The three ways an action's effect can be **read** and still be *proven*.
#:
#: Each is bounded — the answer cannot come out anything but ``read`` — and
#: each is invisible to :func:`effect_is_measured`, which returns ``False`` for
#: every read-only reading so that ``effect: read`` is never pre-filled from a
#: heuristic (#357). Every one is named to look as mutating as the name band
#: can score, so a test that passes cannot be passing by accident.
_BOUNDED_READS: dict[str, dict[str, object]] = {
    "openapi_get": {
        "name": "delete_account",
        "source_type": "openapi",
        "annotations": {"httpMethod": "GET"},
    },
    "mcp_read_only_hint": {
        "name": "drop_everything",
        "source_type": "mcp",
        "annotations": {"mcp_server": True, "readOnlyHint": True},
    },
}


@pytest.mark.parametrize("shape", sorted(_BOUNDED_READS))
def test_a_proven_read_is_not_treated_as_unread(shape: str) -> None:
    """A structural ``read`` is bounded, however mutating its name looks.

    ``effect_is_measured`` is a proposal-safety rule: it says ``False`` for an
    OpenAPI ``GET`` and a trusted ``readOnlyHint`` because a pre-filled
    ``effect: read`` is the one direction a confirmed guess loses safety in.
    Ranking questions with it put those actions at the *ceiling* and then let a
    repository-chosen name break the tie — this issue's own defect inverted.
    """

    proven = _tool(id="t_proven", **_BOUNDED_READS[shape])
    unknown = _tool(id="t_unknown", name="get_status")
    for tool in (proven, unknown):
        tool.semantic_assessment = assess_tool_semantics(tool, None)
    # The premise: the scan established one and knows nothing about the other,
    # and neither can be drafted from.
    assert effect_is_bounded(proven.semantic_assessment.effect)  # type: ignore[union-attr]
    assert not effect_is_bounded(unknown.semantic_assessment.effect)  # type: ignore[union-attr]
    assert not _has_proposal(proven) and not _has_proposal(unknown)

    questions = declaration_questions([proven, unknown])

    assert questions[0].subject_id == "t_unknown", [
        (question.subject, question.dimension, question.rank, question.shape)
        for question in questions
    ]
    # And nothing about the proven action is ordered by its name.
    assert [question.shape for question in questions if question.subject_id == "t_proven"] == [0]


def test_a_partly_answered_action_keeps_the_rank_its_answer_gave_it() -> None:
    """A reviewed ``effect: read`` bounds the action; its authority still gaps.

    The declaration leaves no reading behind — declaration claims are excluded
    from readings on purpose, a row is not evidence about itself — so a rule
    written on readings alone reads this action as unread and floats its
    remaining authority question to the top of the file, above questions nobody
    has answered anything about.
    """

    declared = _tool(id="t_declared", name="purge_records")
    declared.semantic_assessment = assess_tool_semantics(
        declared, ActionDeclarationConfig(tool="purge_records", effect="read")
    )
    unknown = _tool(id="t_unknown", name="get_status")
    unknown.semantic_assessment = assess_tool_semantics(unknown, None)

    questions = declaration_questions([declared, unknown])
    still_open = [question for question in questions if not question.answered]

    # The effect question was answered; the authority question was not.
    assert {(q.dimension, q.subject_id) for q in still_open} == {
        ("authority", "t_declared"),
        ("effect", "t_unknown"),
        ("authority", "t_unknown"),
    }
    assert [question.subject_id for question in still_open][0] == "t_unknown"
    assert [q.shape for q in questions if q.subject_id == "t_declared"] == [0, 0]


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


def test_a_blank_the_scan_read_nothing_for_says_so() -> None:
    """Silence at the block is what the header exists to correct (#419).

    The header explains that the top of the file is the unread half. A block
    that printed nothing at all left the reader to read that silence as
    "nothing to see here" — the reading the whole ordering exists to correct.
    """

    note = "This scan read nothing about this action's effect"

    blank = build_declaration_scaffold(
        [
            _gap(
                "missing_effect_evidence",
                subject_id="t1",
                template={"tool": "send_email", "effect": REVIEW_REQUIRED_SENTINEL},
            )
        ],
        questions=_coverage_rows(("t1", "effect")),
    )
    assert blank is not None
    assert note in " ".join(blank.split())

    # Not where the scan did read something, however weak the reading.
    drafted = build_declaration_scaffold(
        [
            _gap(
                "inferred_effect_only",
                subject_id="t1",
                template={"tool": "send_email", "effect": "external_communication"},
                readings=[
                    EvidenceReading(
                        effect="external_communication", sources=["risk_hint:keyword"]
                    )
                ],
            )
        ],
        questions=_coverage_rows(("t1", "effect")),
    )
    assert drafted is not None
    assert note not in " ".join(drafted.split())

    # And not on a block that never asked about an effect at all.
    authority = build_declaration_scaffold(
        [
            EvidenceGap(
                kind="missing_authority_evidence",
                subject="src [tool_source]",
                subject_id="src",
                subject_kind="tool_source",
                source_type="mcp",
                why="test",
                next_action=EvidenceGapAction(
                    kind="declare_action_authority",
                    path="shipgate.yaml#tool_sources[id='src'].authority",
                    why="test",
                    expects="Declare reviewed authority, then rerun verification.",
                    declaration_template={
                        "id": "src",
                        "authority": {"mode": REVIEW_REQUIRED_SENTINEL},
                    },
                ),
            )
        ],
        questions=DeclarationQuestionCoverage(
            total=1,
            answered=0,
            open=1,
            open_by_dimension={"authority": 1},
            open_questions=[
                DeclarationQuestionRow(
                    subject="src [tool_source]",
                    subject_id="src",
                    subject_kind="tool_source",
                    dimension="authority",
                )
            ],
        ),
    )
    assert authority is not None
    assert note not in " ".join(authority.split())


def test_the_note_claims_a_position_only_when_it_has_one() -> None:
    """``questions=None`` is supported, and it numbers nothing (#419 review).

    A report written before ``declaration_questions`` existed carries no
    coverage, so the blocks keep gap emission order and a blank can follow a
    bounded question. "It is asked before the ones the scan could read for
    itself" is then a sentence the file itself disproves two lines up.
    """

    placement = "It is asked before the ones the scan could read for itself."
    gaps = [
        _gap(
            "inferred_effect_only",
            subject_id="t1",
            name="send_email",
            template={"tool": "send_email", "effect": "external_communication"},
            readings=[
                EvidenceReading(
                    effect="external_communication", sources=["risk_hint:keyword"]
                )
            ],
        ),
        _gap(
            "missing_effect_evidence",
            subject_id="t2",
            name="create_order",
            template={"tool": "create_order", "effect": REVIEW_REQUIRED_SENTINEL},
        ),
    ]

    unnumbered = build_declaration_scaffold(gaps)
    assert unnumbered is not None
    unnumbered_prose = " ".join(unnumbered.split())
    # The blank still says what it is; it just does not claim a place in a
    # queue that was never built.
    assert "This scan read nothing about this action's effect" in unnumbered_prose
    assert placement not in unnumbered_prose
    # The premise: emission order really does put the drafted block first here.
    assert unnumbered.index("create_order") > unnumbered.index("send_email")

    numbered = build_declaration_scaffold(
        gaps, questions=_coverage_rows(("t2", "effect"), ("t1", "effect"))
    )
    assert numbered is not None
    assert placement in " ".join(numbered.split())


def test_the_header_sentence_and_the_realised_order_agree(tmp_path: Path) -> None:
    """The file's own promise, checked against the file (#419).

    Rendered end to end, because the defect this closes was precisely a header
    describing an order the questionnaire did not use: it claimed to lead with
    what could move the verdict while leading with what the scan had already
    read for itself.

    All three shapes the header has to describe are present, because the
    unbounded half is not only the actions with nothing printed above them:

    * ``create_sap_sales_order`` — nothing read at all;
    * ``fetch_thing`` — only the MCP protocol default, an absence of evidence;
    * ``sync_ledger`` — a heuristic reading of ``read``, printed above the
      block, which this resolver may not act on (#357) and which therefore
      bounds nothing. A header saying the top of the file is what the scan
      "could read nothing about" was false for exactly this one.
    """

    config = _mcp_workspace(
        tmp_path,
        tools=[
            {
                "name": "send_email",
                "description": "Send an email to the customer.",
                "auth": {"type": "oauth2", "scopes": ["mail:send"]},
            },
            {"name": "create_sap_sales_order", "description": "Create the order in SAP."},
            {"name": "fetch_thing", "description": "A tool."},
            {"name": "sync_ledger", "description": "A tool."},
        ],
        actions=[],
        risk_overrides={
            "tools": {"sync_ledger": {"tags": ["read_only"], "reason": "reviewed"}}
        },
    )
    run_scan(
        config_path=config,
        output_dir=tmp_path / "out",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )

    scaffold = (tmp_path / "out" / "suggested-declarations.yaml").read_text(
        encoding="utf-8"
    )

    # Unwrapped: the header is prose wrapped into comment lines, and asserting
    # on the wrapping instead of the sentence is a test that fails on a rename.
    prose = " ".join(
        " ".join(
            line.lstrip("#").strip()
            for line in scaffold.splitlines()
            if line.startswith("#")
        ).split()
    )
    assert "the actions nothing has pinned down" in prose
    assert "Then the ones the scan did establish, strongest first" in prose

    # Every blank effect block precedes every drafted one, in the file itself.
    drafted: list[int] = []
    blank: list[int] = []
    for index, line in enumerate(scaffold.splitlines()):
        stripped = line.strip()
        if not stripped.startswith("effect:"):
            continue
        value = stripped.split(":", 1)[1].strip()
        (blank if value == REVIEW_REQUIRED_SENTINEL else drafted).append(index)

    assert blank and drafted, "the fixture stopped carrying both kinds"
    assert max(blank) < min(drafted), scaffold

    # The third shape specifically: a heuristic ``read`` is printed, and the
    # block still sits in the half the header says comes first.
    heuristic_read = next(
        index
        for index, line in enumerate(scaffold.splitlines())
        if "read — risk_hint:manual" in line
    )
    assert heuristic_read < min(drafted), scaffold


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


# --- the committed questionnaire (#425) --------------------------------------
#
# Everything above this line is in-process: synthetic tools, hand-built
# coverage models, workspaces assembled in ``tmp_path``. That is what let #419
# ship an ordering that sent a structurally proven ``GET`` named
# ``delete_account`` to the top of the file, against a fully green suite —
# every sample answered every question it was asked, so no committed artifact
# rendered a questionnaire at all and none of them could disagree with one.
#
# ``samples/google_adk_cold_start_agent`` is the fixture that does. The
# assertions below are what stop it from quietly becoming another ``0 of 0``
# sample: a golden that renders nothing still byte-compares equal to a scan
# that renders nothing.

COLD_START_MANIFEST = Path("samples/google_adk_cold_start_agent/shipgate.yaml")
COLD_START_EXPECTED = COLD_START_MANIFEST.parent / "expected"
COLD_START_SCAFFOLD = COLD_START_EXPECTED / "suggested-declarations.yaml"
COLD_START_REPORT = COLD_START_EXPECTED / "report.json"


def _cold_start_golden() -> dict:
    return json.loads(COLD_START_REPORT.read_text(encoding="utf-8"))


def _cold_start_coverage(report: dict) -> dict:
    return report["release_decision"]["evidence_coverage"]["semantic_coverage"][
        "declaration_questions"
    ]


def _cold_start_scan(tmp_path: Path) -> Path:
    """Scan the fixture with the formats the goldens were generated with."""

    run_scan(
        config_path=COLD_START_MANIFEST,
        output_dir=tmp_path,
        formats=["json", "markdown"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    return tmp_path


def test_cold_start_scaffold_matches_its_golden(tmp_path):
    """The file an adopter is told to edit, byte for byte.

    ``report.md`` and ``packet.*`` have had this for releases;
    ``suggested-declarations.yaml`` had no golden at all, so the header, the
    ``Question N of M`` banners, the reading lines, the proposal annotations
    and the block ordering were pinned by nothing committed.
    """

    out = _cold_start_scan(tmp_path)

    written = out / "suggested-declarations.yaml"
    # Named before it is read. ``_write_suggested_declarations`` *deletes* the
    # file rather than writing one when no gap carries a template, so the
    # likeliest regression here is its absence — and reading it unconditionally
    # would report that as a path that does not exist rather than as the thing
    # it is.
    assert written.is_file(), (
        "The scan wrote no suggested-declarations.yaml. This fixture is the "
        "only sample that renders a declaration questionnaire; if its "
        "questions were closed on purpose, the goldens beside it no longer "
        "pin anything and the fixture needs a new open question, not a "
        "regenerated golden."
    )

    actual = written.read_text(encoding="utf-8")
    expected = COLD_START_SCAFFOLD.read_text(encoding="utf-8")

    assert actual == expected, (
        "samples/google_adk_cold_start_agent/expected/suggested-declarations.yaml "
        "is stale. Regenerate it with a real scan — see that sample's README — "
        "and read the diff: a change in block ORDER is a change in which "
        "question this tool asks a human first."
    )


def test_cold_start_golden_still_asks_open_questions():
    """The fixture's whole job, asserted rather than assumed.

    Every other sample answers everything it is asked, and a byte comparison
    against a questionnaire that renders nothing passes just as happily as one
    against a questionnaire that renders every rung. So the properties that
    make the golden worth having are named here rather than left to a count
    that goes stale: if a future change to the engine, or an over-helpful edit
    to the manifest, closes these questions, this fails instead of the goldens
    silently emptying. ``COLD_START_QUESTION_ORDER`` below is what pins how
    many there are, and what each of them is for.
    """

    coverage = _cold_start_coverage(_cold_start_golden())

    assert coverage["open"] > 0, (
        "samples/google_adk_cold_start_agent is the only sample that renders a "
        "declaration questionnaire. With no open questions its goldens pin "
        "nothing about ordering, numbering, or the progress counter."
    )
    assert coverage["answered"] > 0, (
        "The progress sentence is only interesting between the endpoints. With "
        "0 answered it reads the same as every unstarted repository, and the "
        "counterfactual that decides `answered` is not exercised at all."
    )
    assert coverage["total"] == coverage["answered"] + coverage["open"]
    assert len(coverage["open_questions"]) == coverage["open"]
    assert sum(coverage["open_by_dimension"].values()) == coverage["open"]

    rows = coverage["open_questions"]
    # Every field of the model, present on at least one row, because
    # ``test_sample_expected_report_json_has_no_structural_drift`` compares
    # field PATHS: a member no golden carries is a member that test cannot see.
    assert set(DeclarationQuestionRow.model_fields) <= {
        key for row in rows for key in row
    }
    # Both id spaces and both dimensions, so a consumer joining one against the
    # other has a committed counterexample.
    assert {row["subject_kind"] for row in rows} == {"action", "tool_source"}
    assert {row["dimension"] for row in rows} == set(DIMENSION_BY_GAP_KIND.values())


def test_cold_start_golden_open_questions_match_a_fresh_scan(tmp_path):
    """The order, by value, not only by field path.

    The scaffold comparison above already fails on a reordering, but it fails
    as a wall of diff. This one names the permutation directly, and it is the
    assertion that would have caught #419: revert ``_reach`` in
    ``core/declaration_questions.py`` to rank by ``conservative_effect`` and
    the list below changes.
    """

    out = _cold_start_scan(tmp_path)
    fresh = json.loads((out / "report.json").read_text(encoding="utf-8"))

    assert _cold_start_coverage(fresh) == _cold_start_coverage(_cold_start_golden())


def test_cold_start_golden_leads_with_the_first_question(tmp_path):
    """One order, published three times, pinned once.

    ``_in_question_order`` permutes the published gap rows into the order the
    questionnaire asks them, and ``primary_evidence_gap`` — and so the decision
    reason and ``first_recommended_action`` — takes the first addressable row
    of that list. Nothing committed showed the two agreeing, because no sample
    had two declaration questions to put in an order.
    """

    golden = _cold_start_golden()
    coverage = _cold_start_coverage(golden)
    gaps = golden["release_decision"]["evidence_coverage"]["evidence_gaps"]

    question_rows = [
        (gap["subject_kind"], gap["subject_id"], DIMENSION_BY_GAP_KIND[gap["kind"]])
        for gap in gaps
        if gap["kind"] in DIMENSION_BY_GAP_KIND
    ]
    expected = [
        (row["subject_kind"], row["subject_id"], row["dimension"])
        for row in coverage["open_questions"]
    ]

    assert question_rows == expected

    leader = coverage["open_questions"][0]
    reason = golden["release_decision"]["reason"]
    first_action = golden["agent_summary"]["first_recommended_action"]["why"]
    assert leader["subject"] in reason
    assert leader["answer_path"] in reason
    assert leader["answer_path"] in first_action


def test_cold_start_scaffold_renders_both_halves_of_a_blank():
    """A proposal and a blank, in one committed file.

    The two are rendered by different branches and mean opposite things: a
    pre-filled value is a reading the scan is willing to stand behind, and
    ``<REVIEW_REQUIRED>`` is one it refuses to guess. A golden carrying only
    one of them would let the other regress unnoticed.
    """

    scaffold = COLD_START_SCAFFOLD.read_text(encoding="utf-8")

    assert REVIEW_REQUIRED_SENTINEL in scaffold
    assert "effect: financial_write" in scaffold
    assert "proposed from the evidence above" in scaffold
    # The counter, rendered at neither endpoint. Matched against the flowed
    # comment text rather than the raw file: the header is wrapped, so a
    # sentence that grew past one line would fail this for the wrong reason.
    flowed = " ".join(
        line.lstrip("#").strip()
        for line in scaffold.splitlines()
        if line.startswith("#")
    )
    assert (
        progress_sentence(
            DeclarationQuestionCoverage.model_validate(
                _cold_start_coverage(_cold_start_golden())
            )
        )
        in flowed
    )


#: The order the fixture exists to hold, stated where a reviewer can read it.
#:
#: A byte comparison against a 250-line YAML file fails loudly but says nothing
#: about *what* moved. This says it: ``(subject, dimension)`` in the order the
#: questionnaire asks them, annotated with the ``(reach, name band)`` each row
#: is there to hold. Alphabetical order is deliberately wrong twice — the band
#: is the only reason ``update_case_index`` precedes ``assemble_case_timeline``
#: and ``list_case_attachments`` follows both.
COLD_START_QUESTION_ORDER: tuple[tuple[str, str], ...] = (
    # Unbounded, name bands as mutating (+1). The source block inherits the
    # strongest reach of the actions it answers for, so it leads.
    ("adk_ops [tool_source]", "authority"),
    ("update_case_index [adk_ops]", "effect"),
    # Unbounded, name says nothing (0). ``ops.export_case_bundle`` brings the
    # one question no block answers, and it sits between its own effect block
    # and the next subject's rather than after every block in the file.
    ("assemble_case_timeline [adk_ops]", "effect"),
    ("ops.export_case_bundle [adk_ops:mcp:1]", "effect"),
    ("ops.export_case_bundle [adk_ops:mcp:1]", "authority"),
    ("ops.queue_backfill [adk_ops:mcp:1]", "effect"),
    ("ops.queue_backfill [adk_ops:mcp:1]", "authority"),
    # Unbounded, name bands as retrieving (-1).
    ("list_case_attachments [adk_ops]", "effect"),
    # Bounded: the scan read a financial write for itself.
    ("issue_goodwill_refund [adk_ops]", "effect"),
    # Bounded at the floor: a structurally proven read. Its name bands as
    # mutating, so ranking on "was a side effect measured" would put it first.
    ("support.get_update_history [adk_ops:mcp:1]", "authority"),
)


def test_cold_start_golden_asks_its_questions_in_the_pinned_order():
    """The permutation, spelled out rather than left in a diff.

    Deliberately against the *golden* and not against a fresh scan — the two
    comparisons above already fail the moment a scan disagrees with the
    committed file. This one fails one step later, when someone regenerates the
    goldens to make those pass: the order has to be restated here, next to the
    reason each row holds its place, rather than absorbed into a YAML diff
    nobody reads. Flatten ``name_shape_band`` and the unbounded rows collapse
    into plain alphabetical order; go back to ranking on whether a side effect
    was *measured* rather than *bounded* and the proven read at the end jumps
    to the front.
    """

    coverage = _cold_start_coverage(_cold_start_golden())
    realised = tuple(
        (row["subject"], row["dimension"]) for row in coverage["open_questions"]
    )

    assert realised == COLD_START_QUESTION_ORDER


def test_cold_start_questionnaire_covers_every_rung_it_claims_to():
    """A regenerated golden must not quietly lose the reason it exists.

    The order is only proven by a fixture that has something to order, and the
    rungs are what give it that. Read off the published rows rather than the
    rendered prose, so this keeps meaning what it says when the wording of a
    banner changes.
    """

    golden = _cold_start_golden()
    coverage = _cold_start_coverage(golden)
    # Indexed to lists, not to single rows. One question is one blank a
    # reviewer fills, and several published rows can ask for it: five kinds map
    # to the ``effect`` dimension alone, so a dict of one row per key would
    # keep whichever was emitted last and quietly evaluate every assertion
    # below against a row nobody named.
    gaps: dict[tuple[str, str | None, str], list[dict]] = {}
    for gap in golden["release_decision"]["evidence_coverage"]["evidence_gaps"]:
        dimension = DIMENSION_BY_GAP_KIND.get(gap["kind"])
        if dimension is None:
            continue
        key = (gap["subject_kind"], gap["subject_id"], dimension)
        gaps.setdefault(key, []).append(gap)
    effect_actions = [
        gap["next_action"]
        for row in coverage["open_questions"]
        if row["dimension"] == "effect"
        for gap in gaps[(row["subject_kind"], row["subject_id"], "effect")]
    ]
    readings = [action["observed_readings"] for action in effect_actions]

    # Nothing read at all: the blank whose answer is the only bound there is.
    assert any(not reading for reading in readings)
    # A protocol default: a value standing in for the absence of evidence, and
    # the one reading nothing is ever proposed from.
    assert any(
        reading and not any(item["observed"] for item in reading)
        for reading in readings
    )
    # Read as risky, so the block arrives with a proposal instead of a blank.
    proposals = [
        action["declaration_template"]["effect"]
        for action in effect_actions
        if action.get("declaration_template")
    ]
    assert REVIEW_REQUIRED_SENTINEL in proposals
    assert [value for value in proposals if value != REVIEW_REQUIRED_SENTINEL] == [
        "financial_write"
    ]
    # A question the counter counts and no block can answer: the repair is to
    # correct one of two disagreeing statements, not to fill in a blank, so the
    # questionnaire prints a note in its numbered place instead of a template.
    open_keys = {
        (row["subject_kind"], row["subject_id"], row["dimension"])
        for row in coverage["open_questions"]
    }
    unfillable = [
        gap
        for key, rows in gaps.items()
        if key in open_keys
        for gap in rows
        if gap["next_action"].get("declaration_template") is None
    ]
    assert unfillable
    assert {gap["kind"] for gap in unfillable} == {"conflicting_authority_evidence"}

    # One block a reviewer edits once, answering for several actions. Asserted
    # as the *absence* of the per-action rows it replaces: the source question
    # is only worth having if the actions behind it are not asked separately.
    source_rows = [
        row
        for row in coverage["open_questions"]
        if row["subject_kind"] == "tool_source"
    ]
    assert len(source_rows) == 1
    assert source_rows[0]["answer_path"].endswith("].authority")
    asked_separately = {
        row["subject_id"]
        for row in coverage["open_questions"]
        if row["dimension"] == "authority" and row["subject_kind"] == "action"
    }
    # Restricted to the configured source the block belongs to: the MCP tools
    # this agent also reaches come from a per-scan surface with no
    # ``tool_sources`` row, so no source block can answer for them and they
    # rightly keep their own rows.
    absorbed = {
        action["tool_id"]
        for action in golden["action_surface_facts"]["actions"]
        if action["source_id"] == source_rows[0]["subject_id"]
        and any(
            issue["kind"] == "missing_authority_evidence"
            for issue in action["semantic_assessment"]["authority"]["issues"]
        )
    }
    assert len(absorbed) > 1, (
        "The source-wide authority question is only worth committing if more "
        "than one action is waiting on that single block."
    )
    assert not absorbed & asked_separately
