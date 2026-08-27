"""Per-source authority: one credential, one question (#410 increment 3).

Authority is a fact about a *deployment*, not about a function. Six Salesforce
tools behind one OAuth client have one answer, and asking for it once per tool
asks the same infrastructure question six times — which is not merely tedious.
It is what breeds the copy-paste that breeds wrong answers, and a wrong
authority declaration is the one that makes an unscoped production credential
read as ``mode: none``.

Four properties this increment lives or dies on, each with a test that would
fail if it were lost:

1. **The two sites obey one rule.** A ``scoped`` grant without scopes is
   unfillable wherever it is written, and a manifest that the action row
   rejects and the source block accepts is a second implementation with a
   safety gap in it.
2. **A source-wide declaration is held to exactly the conflict rule a
   per-action one is.** Otherwise the new spelling is a way to weaken published
   evidence in bulk — the #409 fail-open, restored at a wider scope.
3. **A question is one blank a reviewer fills.** That is the whole payoff: 117
   actions with no authority evidence owe one block, and a counter that calls
   that 117 questions describes one edit as a backlog.
4. **Nothing is prescribed where nothing can be written.** An action from a
   surface no ``tool_sources`` entry configures has no source block, and
   publishing one would send a reviewer to a manifest key the schema rejects.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from agents_shipgate.ci.release_decision import REVIEW_REQUIRED_SENTINEL
from agents_shipgate.cli.scan import run_scan
from agents_shipgate.cli.scan.declarations import scaffold_for_report
from agents_shipgate.core.declaration_questions import (
    ANSWERABLE_ISSUE_KINDS,
    SOURCE_ANSWERABLE_AUTHORITY_KINDS,
    declaration_answer_target,
    declaration_questions,
)
from agents_shipgate.core.domain import (
    DECLARED_SOURCE_AUTHORITY_SOURCE,
    REVIEWED_DECLARATION_CLAIM_SOURCES,
    Tool,
)
from agents_shipgate.core.evidence_actions import evidence_gap_headline
from agents_shipgate.core.semantic_assessment import (
    assess_tool_semantics,
    attach_semantic_assessments,
)
from agents_shipgate.schemas.manifest import (
    ActionDeclarationConfig,
    SourceAuthorityConfig,
    ToolSourceConfig,
)
from agents_shipgate.schemas.manifest._authority import AUTHORITY_MODE_VALUES
from agents_shipgate.schemas.manifest.action_surface import ActionAuthorityMode
from agents_shipgate.schemas.manifest.tool_sources import SourceAuthorityMode
from agents_shipgate.schemas.report import EvidenceGap
from agents_shipgate.schemas.semantic import AuthoritySemanticEvidence

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _tool(name: str = "send_email", **updates: object) -> Tool:
    values: dict[str, object] = {
        "id": f"mcp:crm:{name}",
        "name": name,
        "source_type": "mcp",
        "source_id": "crm",
        "provider": "crm",
        "source_pointer": "tools.json",
        "extraction_confidence": "high",
        "extraction": {"surface": "enumerated"},
        # What the dispatcher records: the configured ``tool_sources`` entry
        # this observation came from. The join runs on this, never on
        # ``source_id`` — see the provenance tests below.
        "configured_source_ids": ["crm"],
    }
    values.update(updates)
    return Tool.model_validate(values)


def _source(**authority: object) -> ToolSourceConfig:
    payload: dict[str, object] = {"id": "crm", "type": "mcp", "path": "tools.json"}
    if authority:
        payload["authority"] = authority
    return ToolSourceConfig.model_validate(payload)


def _authority_issues(assessment) -> set[str]:
    return {issue.kind for issue in assessment.authority.issues}


def _workspace(
    tmp_path: Path,
    *,
    tools: list[dict],
    source_authority: dict | None = None,
    actions: list[dict] | None = None,
) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "tools.json").write_text(json.dumps({"tools": tools}), encoding="utf-8")
    source: dict[str, object] = {"id": "crm", "type": "mcp", "path": "tools.json"}
    if source_authority is not None:
        source["authority"] = source_authority
    manifest: dict[str, object] = {
        "version": "0.1",
        "project": {"name": "source-authority"},
        "agent": {"name": "asst", "declared_purpose": ["exercise per-source authority"]},
        "environment": {"target": "local"},
        "tool_sources": [source],
        "agent_bindings": {
            "declarations": [
                {
                    "agent": "root",
                    "complete": True,
                    "tools": [{"tool": tool["name"], "source_id": "crm"} for tool in tools],
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


def _scan(tmp_path: Path, config: Path):
    report, _ = run_scan(
        config_path=config,
        output_dir=tmp_path / "out",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    assert report.release_decision is not None
    return report


def _mcp_tool(name: str, description: str, **extra: object) -> dict:
    return {
        "name": name,
        "description": description,
        "inputSchema": {"type": "object", "properties": {}},
        **extra,
    }


# --------------------------------------------------------------------------
# 1. The two sites obey one rule
# --------------------------------------------------------------------------

#: ``(mode, auth_type, scopes, reason)`` and whether a reviewer may write it.
#:
#: Both the accepted and the rejected halves matter. A table of legal answers
#: proves nothing about a validator that accepts everything.
_AUTHORITY_CASES: list[tuple[str, str | None, list[str], str | None, bool]] = [
    ("none", None, [], None, True),
    ("none", "oauth2", [], None, False),
    ("none", None, ["a"], None, False),
    ("scoped", "oauth2", ["a"], None, True),
    ("scoped", None, ["a"], None, False),
    ("scoped", "oauth2", [], None, False),
    ("unscoped", "oauth2", [], "shared admin token", True),
    ("unscoped", "oauth2", [], None, False),
    ("unscoped", "oauth2", ["a"], "shared admin token", False),
    ("unscoped", None, [], "shared admin token", False),
    ("ambient", None, [], "runs as the host process", True),
    ("ambient", None, [], None, False),
    ("ambient", None, ["a"], "runs as the host process", False),
]


@pytest.mark.parametrize(("mode", "auth_type", "scopes", "reason", "accepted"), _AUTHORITY_CASES)
def test_both_authority_sites_accept_and_reject_the_same_declarations(
    mode: str, auth_type: str | None, scopes: list[str], reason: str | None, accepted: bool
) -> None:
    """One rule, two spellings.

    The co-requirements are about the claim, not about where it is written, so
    a manifest one site rejects and the other accepts would be a safety gap
    reachable by moving four lines up the file.
    """

    authority = {"mode": mode}
    if auth_type is not None:
        authority["auth_type"] = auth_type
    if reason is not None:
        authority["reason"] = reason

    def _action() -> None:
        ActionDeclarationConfig.model_validate(
            {"tool": "send_email", "authority": authority, "scopes": list(scopes)}
        )

    def _source_block() -> None:
        ToolSourceConfig.model_validate(
            {
                "id": "crm",
                "type": "mcp",
                "path": "tools.json",
                "authority": {**authority, "scopes": list(scopes)},
            }
        )

    for name, build in (("action row", _action), ("source block", _source_block)):
        if accepted:
            build()
            continue
        with pytest.raises(ValidationError, match=f"'{mode}' requires"):
            build()
            pytest.fail(f"{name} accepted {authority} with scopes={scopes}")


def test_both_authority_sites_share_one_mode_vocabulary() -> None:
    """A mode added to one spelling has to be added to all three."""

    from typing import get_args

    assert set(get_args(ActionAuthorityMode)) == set(AUTHORITY_MODE_VALUES)
    assert set(get_args(SourceAuthorityMode)) == set(AUTHORITY_MODE_VALUES)


def test_a_blank_scope_is_rejected_wherever_it_is_written() -> None:
    """``scoped`` may not be satisfied by a list that grants nothing."""

    for payload, model in (
        ({"tool": "t", "scopes": ["  "]}, ActionDeclarationConfig),
        ({"mode": "scoped", "auth_type": "oauth2", "scopes": ["  "]}, SourceAuthorityConfig),
    ):
        with pytest.raises(ValidationError, match="concrete, non-blank scope strings"):
            model.model_validate(payload)


# --------------------------------------------------------------------------
# 2. A source-wide declaration is held to the per-action conflict rule
# --------------------------------------------------------------------------


def test_a_source_declaration_answers_every_action_of_its_source() -> None:
    """The point of the feature, stated as a resolver property."""

    source = _source(mode="scoped", auth_type="oauth2", scopes=["crm.read"])
    for name in ("send_email", "list_contacts", "create_invoice"):
        assessed = assess_tool_semantics(_tool(name), None, tool_source=source)
        assert assessed.authority.status == "declared"
        assert assessed.authority.mode == "scoped"
        assert assessed.authority.scopes == ["crm.read"]
        assert not assessed.authority.issues
        assert {claim.source for claim in assessed.authority.claims} == {
            DECLARED_SOURCE_AUTHORITY_SOURCE
        }


def test_an_action_declaration_overrides_the_source_it_belongs_to() -> None:
    """The more specific statement wins, and the claim says which one it was."""

    source = _source(mode="ambient", reason="inherited host credentials")
    declaration = ActionDeclarationConfig.model_validate(
        {
            "tool": "send_email",
            "scopes": ["crm.send"],
            "authority": {"mode": "scoped", "auth_type": "oauth2"},
        }
    )
    assessed = assess_tool_semantics(_tool(), declaration, tool_source=source)

    assert assessed.authority.mode == "scoped"
    assert assessed.authority.scopes == ["crm.send"]
    assert {claim.source for claim in assessed.authority.claims} == {
        "action_surface_declaration"
    }
    # The action row is now where this action's authority is answered, so the
    # source block must not be advertised as its repair.
    assert assessed.authority.answerable_source_id is None


def test_a_source_declaration_cannot_weaken_what_an_action_publishes() -> None:
    """The #409 rule, at source scope: bulk de-escalation is never quiet.

    Declaring ``mode: none`` across a source whose actions publish an OAuth
    scope is the convenient lie — it clears the authority dimension for every
    one of them in four lines. It must raise, on each action that disagrees,
    and the row must name the block that is wrong.
    """

    source = _source(mode="none")
    published = _tool(auth={"type": "oauth2", "scopes": ["crm.write"], "mode": "scoped"})
    assessed = assess_tool_semantics(published, None, tool_source=source)

    assert "conflicting_authority_evidence" in _authority_issues(assessed)
    assert assessed.authority.status == "conflicting"
    assert assessed.authority.mode == "unknown"
    conflict = next(
        issue
        for issue in assessed.authority.issues
        if issue.kind == "conflicting_authority_evidence"
    )
    assert conflict.source == DECLARED_SOURCE_AUTHORITY_SOURCE
    assert "tool_sources[id='crm']" in (conflict.source_pointer or "")


def test_a_source_declaration_may_broaden_what_an_action_publishes() -> None:
    """A superset is an explicit broadening, and stays visible as scopes."""

    source = _source(mode="scoped", auth_type="oauth2", scopes=["crm.read", "crm.write"])
    published = _tool(auth={"type": "oauth2", "scopes": ["crm.read"], "mode": "scoped"})
    assessed = assess_tool_semantics(published, None, tool_source=source)

    assert not assessed.authority.issues
    assert assessed.authority.scopes == ["crm.read", "crm.write"]


def test_a_source_declaration_cannot_replace_ambiguous_source_evidence() -> None:
    """The deliberate safety property, unchanged at the new site.

    "Reviewed authority cannot replace ambiguous or incomplete source authority
    alternatives" is why ``partial_authority_evidence`` is not a question. A
    source block must not become the way around it.
    """

    # An OpenAPI security requirement naming a scheme with no declared type:
    # the source says a credential is needed and does not say what kind.
    ambiguous = _tool(
        source_type="openapi",
        auth={"alternatives": [{"schemes": [{"name": "oauth", "type": None, "scopes": ["crm.read"]}]}]},
    )
    undeclared = assess_tool_semantics(ambiguous, None)
    assert "partial_authority_evidence" in _authority_issues(undeclared), (
        "the fixture no longer produces partial source authority evidence"
    )

    declared = assess_tool_semantics(
        ambiguous,
        None,
        tool_source=_source(mode="scoped", auth_type="oauth2", scopes=["crm.read"]),
    )
    assert "partial_authority_evidence" in _authority_issues(declared)
    assert declared.authority.status == "partial"
    # And it is not counted as a question either, at either site — a finish
    # line no answer reaches is worse than no finish line.
    ambiguous.semantic_assessment = declared
    assert not [
        question
        for question in declaration_questions([ambiguous])
        if question.dimension == "authority"
    ]


# --------------------------------------------------------------------------
# 3. A question is one blank a reviewer fills
# --------------------------------------------------------------------------


def test_actions_that_share_one_authority_block_are_one_question() -> None:
    """Twelve actions, one credential, one question."""

    tools = attach_semantic_assessments(
        [_tool(name) for name in ("a", "b", "c", "d")],
        {},
        tool_sources={"crm": _source()},
    )
    authority = [q for q in declaration_questions(tools) if q.dimension == "authority"]

    assert len(authority) == 1
    assert authority[0].subject_kind == "tool_source"
    assert authority[0].subject_id == "crm"
    assert authority[0].answer_path == "shipgate.yaml#tool_sources[id='crm'].authority"
    assert authority[0].answered is False


def test_answering_the_source_block_answers_the_question() -> None:
    """The counterfactual has to see a source-wide answer as an answer.

    ``answered`` is measured by re-resolving with no reviewed declaration at
    all. If the counterfactual kept the source block, every action would
    resolve clean without it and the question would read as never asked.
    """

    tools = attach_semantic_assessments(
        [_tool(name) for name in ("a", "b", "c")],
        {},
        tool_sources={"crm": _source(mode="scoped", auth_type="oauth2", scopes=["crm.read"])},
    )
    authority = [q for q in declaration_questions(tools) if q.dimension == "authority"]

    assert len(authority) == 1
    assert authority[0].answered is True


def test_an_action_answering_for_itself_is_a_separate_question() -> None:
    """Two blanks are two questions, and one of them can be finished first."""

    declaration = ActionDeclarationConfig.model_validate(
        {"tool": "a", "scopes": ["crm.read"], "authority": {"mode": "scoped", "auth_type": "oauth2"}}
    )
    tools = attach_semantic_assessments(
        [_tool(name) for name in ("a", "b", "c")],
        {"mcp:crm:a": declaration},
        tool_sources={"crm": _source()},
    )
    authority = sorted(
        (q for q in declaration_questions(tools) if q.dimension == "authority"),
        key=lambda q: q.subject_kind,
    )

    assert [(q.subject_kind, q.answered) for q in authority] == [
        ("action", True),
        ("tool_source", False),
    ]


def test_a_block_that_conflicts_with_one_action_leaves_that_action_asking() -> None:
    """Answering three of four actions does not finish the work, and says so.

    ``mode: none`` closes the authority dimension for the three actions that
    publish no credential — that answer really was given, and counting it as
    unanswered would be its own kind of lie. The fourth publishes an OAuth
    scope, disagrees, and keeps asking **on its own row**, because the
    judgement it needs is about that action: correct the block, or declare the
    exception here. So the counter reports two questions, one of them open, and
    nothing reads as finished.
    """

    tools = attach_semantic_assessments(
        [
            _tool("a"),
            _tool("b"),
            _tool("c"),
            _tool("d", auth={"type": "oauth2", "scopes": ["crm.write"], "mode": "scoped"}),
        ],
        {},
        tool_sources={"crm": _source(mode="none")},
    )
    questions = sorted(
        (q for q in declaration_questions(tools) if q.dimension == "authority"),
        key=lambda q: q.subject_kind,
    )

    assert [(q.subject_kind, q.subject_id, q.answered) for q in questions] == [
        ("action", "mcp:crm:d", False),
        ("tool_source", "crm", True),
    ]


def test_an_action_from_an_unconfigured_source_is_still_asked_on_its_own_row() -> None:
    """Never prescribe a block the manifest schema will not accept.

    A per-scan adapter stamps a ``source_id`` that ``tool_sources`` does not
    configure. Guessing a source block from it would publish a repair that
    cannot be written.
    """

    tools = attach_semantic_assessments([_tool("a", source_id="n8n_workflow")], {}, tool_sources={})
    authority = [q for q in declaration_questions(tools) if q.dimension == "authority"]

    assert [q.subject_kind for q in authority] == ["action"]
    assert authority[0].answer_path == "shipgate.yaml#action_surface.actions[tool='a']"


def test_only_the_kinds_a_source_block_can_close_route_to_one() -> None:
    """The routing table and the resolver must agree about what a block answers."""

    assert SOURCE_ANSWERABLE_AUTHORITY_KINDS == frozenset({"missing_authority_evidence"})
    # Routing a kind to a block that cannot close it is the defect the
    # questionnaire already fixed once, one level up: every kind the source
    # route claims has to be answerable by a reviewed declaration at all.
    assert SOURCE_ANSWERABLE_AUTHORITY_KINDS <= ANSWERABLE_ISSUE_KINDS["authority"]
    tool = _tool()
    tool.semantic_assessment = assess_tool_semantics(tool, None, tool_source=_source())
    assert declaration_answer_target(tool, "missing_authority_evidence").kind == "tool_source"
    for kind in ("conflicting_authority_evidence", "missing_effect_evidence"):
        assert declaration_answer_target(tool, kind).kind == "action"


# --------------------------------------------------------------------------
# 4. What the reader is handed
# --------------------------------------------------------------------------


def _authority_gaps(report) -> list[EvidenceGap]:
    return [
        gap
        for gap in report.release_decision.evidence_coverage.evidence_gaps
        if gap.kind == "missing_authority_evidence"
    ]


def test_one_row_names_the_source_and_says_how_many_actions_wait_on_it(tmp_path: Path) -> None:
    """One blank is one instruction — with the scale it is holding up attached."""

    config = _workspace(
        tmp_path,
        tools=[
            _mcp_tool("send_email", "Send an email to a customer."),
            _mcp_tool("list_contacts", "List the contacts in the CRM."),
            _mcp_tool("create_invoice", "Create an invoice and charge a customer."),
        ],
    )
    report = _scan(tmp_path, config)
    gaps = _authority_gaps(report)

    assert len(gaps) == 1
    row = gaps[0]
    assert row.subject == "crm [tool_source]"
    assert row.subject_kind == "tool_source"
    assert row.subject_id == "crm"
    assert "3 actions from tool source 'crm'" in row.why
    assert row.next_action.path == "shipgate.yaml#tool_sources[id='crm'].authority"
    # The file the source is read from, not the JSON pointer of whichever
    # action built the row. The issue's own pointer is per action and always
    # set for this kind, so a fallback chain that consulted it first could
    # never reach the file — the first version of this branch was dead.
    assert row.source_ref == "tools.json"
    assert evidence_gap_headline(row).startswith("a tool source has no declared authority")
    # The template is a ``tool_sources`` entry, and its scopes live inside the
    # authority block because a source has no sibling permission list.
    template = row.next_action.declaration_template
    assert template is not None
    assert template["id"] == "crm"
    assert set(template["authority"]) == {"mode", "auth_type", "scopes", "reason"}


def test_the_row_and_the_question_name_the_same_block(tmp_path: Path) -> None:
    """Two surfaces publish a route to one blank; they may not disagree.

    The evidence-gap row's ``next_action.path`` is what a coding agent and the
    short-form ``Fix at …`` line consume; the question's ``answer_path`` is
    what the questionnaire numbers a block by. A row that says one block while
    the counter numbers another is the defect this derivation exists to make
    impossible, so it is asserted rather than assumed.
    """

    config = _workspace(
        tmp_path,
        tools=[
            _mcp_tool("send_email", "Send an email to a customer."),
            _mcp_tool("list_contacts", "List the contacts in the CRM."),
        ],
    )
    report = _scan(tmp_path, config)
    coverage = (
        report.release_decision.evidence_coverage.semantic_coverage.declaration_questions
    )
    question = next(row for row in coverage.open_questions if row.dimension == "authority")
    row = _authority_gaps(report)[0]

    assert question.answer_path == row.next_action.path
    assert (question.subject_kind, question.subject_id) == (row.subject_kind, row.subject_id)
    assert question.subject == row.subject


def test_the_questionnaire_asks_the_source_once(tmp_path: Path) -> None:
    """Rendered, not merely counted — a numbering defect is only visible here."""

    config = _workspace(
        tmp_path,
        tools=[
            _mcp_tool("send_email", "Send an email to a customer."),
            _mcp_tool("list_contacts", "List the contacts in the CRM."),
            _mcp_tool("create_invoice", "Create an invoice and charge a customer."),
        ],
    )
    report = _scan(tmp_path, config)
    scaffold = scaffold_for_report(report)
    assert scaffold is not None

    assert scaffold.count("merge into: shipgate.yaml#tool_sources[id='crm'].authority") == 1
    assert "· authority · crm [tool_source]" in scaffold
    coverage = (
        report.release_decision.evidence_coverage.semantic_coverage.declaration_questions
    )
    assert coverage.open_by_dimension.get("authority") == 1
    # Every numbered block the counter promises is present exactly once.
    for number in range(1, coverage.open + 1):
        assert scaffold.count(f"Question {number} of {coverage.open} ") <= 1


def test_the_published_block_closes_the_question_it_is_printed_on(tmp_path: Path) -> None:
    """A published repair is verified by applying it, not by reading it."""

    tools = [
        _mcp_tool("send_email", "Send an email to a customer."),
        _mcp_tool("list_contacts", "List the contacts in the CRM."),
    ]
    report = _scan(tmp_path, _workspace(tmp_path, tools=tools))
    template = _authority_gaps(report)[0].next_action.declaration_template
    assert template is not None

    # What a reviewer does with the block: replace the blanks, delete the
    # optional line their mode does not need.
    answered = dict(template["authority"])
    answered["mode"] = "scoped"
    answered["auth_type"] = "oauth2"
    answered["scopes"] = ["crm.read"]
    del answered["reason"]
    assert REVIEW_REQUIRED_SENTINEL not in json.dumps(answered)

    reapplied = _scan(
        tmp_path / "second",
        _workspace(tmp_path / "second", tools=tools, source_authority=answered),
    )
    coverage = (
        reapplied.release_decision.evidence_coverage.semantic_coverage.declaration_questions
    )
    assert not _authority_gaps(reapplied)
    assert coverage.open_by_dimension.get("authority") is None
    assert coverage.answered >= 1


def test_the_grant_reaches_every_action_and_is_read_as_evidence_about_it(
    tmp_path: Path,
) -> None:
    """One permission list, published and judged the same way everywhere.

    ``CapabilityFactV1`` *requires* ``authority.scopes`` to equal the semantic
    authority's, and one of its builders reconstructs the fact from the
    action's ``required_scopes`` — so these are not two fields that merely
    ought to agree, they are one fact with two spellings, and a draft that kept
    a source's grant off the action raised a validation error on the
    base-vs-head path.

    The same list therefore has to bound the effect. A manifest asserting these
    actions require ``crm.delete`` while declaring one of them ``read`` is a
    contradiction, and it is the *scope* half that must not go quiet — that is
    the #409 failure mode with the assertion moved four lines up the file.
    """

    config = _workspace(
        tmp_path,
        tools=[
            _mcp_tool("send_email", "Send an email to a customer."),
            _mcp_tool("list_contacts", "List the contacts in the CRM."),
        ],
        source_authority={
            "mode": "scoped",
            "auth_type": "oauth2",
            "credential_mode": "service_account",
            "scopes": ["crm.delete", "crm.read"],
        },
    )
    report = _scan(tmp_path, config)

    for action in report.action_surface_facts.actions:
        assert action.semantic_assessment is not None
        authority = action.semantic_assessment.authority
        assert authority.mode == "scoped"
        assert authority.credential_mode == "service_account"
        assert list(authority.scopes) == ["crm.delete", "crm.read"]
        assert action.required_scopes == ["crm.delete", "crm.read"]
        # A delete-verb permission this manifest says the action requires
        # bounds its effect, exactly as it would written on the action row.
        assert action.effect == "destructive"


def test_the_action_fact_and_the_assessment_publish_one_permission_list(
    tmp_path: Path,
) -> None:
    """The invariant the capability standard already demands, asserted directly.

    ``CapabilityFactV1._semantic_projection_is_consistent`` raises when
    ``authority.scopes`` and the semantic authority disagree, and the
    base-vs-head builder reconstructs the fact from ``required_scopes`` — so a
    divergence is a crash, not a cosmetic mismatch. Asserted here for every
    shape a reviewed authority can take, because the end-to-end path that
    surfaced it only fires when a base report is available.
    """

    tools = [
        _mcp_tool("send_email", "Send an email to a customer."),
        _mcp_tool("list_contacts", "List the contacts in the CRM."),
    ]
    # Every shape a reviewed authority can take at either site, plus the one
    # that needs none: a bare ``scopes`` list with no ``authority`` block
    # anywhere. That last shape was deliberately absent while the two fields
    # still disagreed on it — the row's list against the source's published
    # one — and it is covered now that one resolver decides both
    # (``resolve_action_scopes``); the sweep in
    # ``tests/test_action_scope_projection.py`` walks the rest of that class.
    shapes: list[tuple[dict | None, list[dict] | None]] = [
        ({"mode": "scoped", "auth_type": "oauth2", "scopes": ["crm.read"]}, None),
        ({"mode": "none"}, None),
        (
            {"mode": "scoped", "auth_type": "oauth2", "scopes": ["crm.read"]},
            [{"tool": "send_email", "source_id": "crm", "authority": {"mode": "none"}}],
        ),
        (
            {"mode": "scoped", "auth_type": "oauth2", "scopes": ["crm.read"]},
            [{"tool": "send_email", "source_id": "crm", "scopes": ["crm.send"]}],
        ),
        (
            None,
            [
                {
                    "tool": "send_email",
                    "source_id": "crm",
                    "scopes": ["crm.send"],
                    "authority": {"mode": "scoped", "auth_type": "oauth2"},
                }
            ],
        ),
        # No reviewed authority at either site: the row's own permission list.
        (None, [{"tool": "send_email", "source_id": "crm", "scopes": ["crm.send"]}]),
    ]
    for index, (source_authority, actions) in enumerate(shapes):
        workspace = tmp_path / f"shape_{index}"
        report = _scan(
            workspace,
            _workspace(
                workspace,
                tools=tools,
                source_authority=source_authority,
                actions=actions,
            ),
        )
        for action in report.action_surface_facts.actions:
            assert action.semantic_assessment is not None
            assert action.required_scopes == sorted(
                set(action.semantic_assessment.authority.scopes)
            ), f"shape {index}: {action.tool_name} publishes two permission lists"


def test_declaring_none_across_a_published_credential_is_never_quiet(tmp_path: Path) -> None:
    """The adversarial declaration, at source scope (the #409 method).

    Four lines that would make every action of a source read as credential-free
    must not reach a clean authority dimension for the actions that publish one.
    """

    config = _workspace(
        tmp_path,
        tools=[
            _mcp_tool(
                "send_email",
                "Send an email to a customer.",
                auth={"type": "oauth2", "scopes": ["crm.send"]},
            ),
            _mcp_tool(
                "list_contacts",
                "List the contacts in the CRM.",
                auth={"type": "oauth2", "scopes": ["crm.read"]},
            ),
        ],
        source_authority={"mode": "none"},
    )
    report = _scan(tmp_path, config)
    coverage = report.release_decision.evidence_coverage.semantic_coverage

    assert coverage.pass_eligible_actions == 0
    assert coverage.reason_counts.get("conflicting_authority_evidence") == 2
    assert report.release_decision.decision != "passed"
    conflicts = [
        gap
        for gap in report.release_decision.evidence_coverage.evidence_gaps
        if gap.kind == "conflicting_authority_evidence"
    ]
    assert {gap.subject_kind for gap in conflicts} == {"action"}
    for gap in conflicts:
        assert "tool_sources[id='crm']" in gap.next_action.expects


def test_the_answer_routing_hint_never_reaches_the_published_evidence() -> None:
    """``answerable_source_id`` says where a blank is filled, not what is true.

    ``AuthoritySemanticEvidence`` forbids extras, so a routing hint leaking
    into the domain dump would both break the projection and invite a consumer
    to read it as a claim about the action.
    """

    assessed = assess_tool_semantics(_tool(), None, tool_source=_source())
    assert assessed.authority.answerable_source_id == "crm"
    assert "answerable_source_id" not in assessed.authority.model_dump(mode="json")
    AuthoritySemanticEvidence.model_validate(assessed.authority.model_dump(mode="python"))


def test_a_source_authority_claim_counts_as_a_reviewed_declaration() -> None:
    """The counterfactual is gated on this set; a miss makes it never run."""

    assert DECLARED_SOURCE_AUTHORITY_SOURCE in REVIEWED_DECLARATION_CLAIM_SOURCES


# --------------------------------------------------------------------------
# 5. It is still a declaration only a human may make
# --------------------------------------------------------------------------


def test_an_agent_may_not_author_a_source_authority_block(tmp_path: Path) -> None:
    """Appending a source is proposal-safe; declaring its authority is not.

    Preflight lets a coding agent author the one coverage-increasing manifest
    edit — a new ``tool_sources`` row pointing at an artifact that exists —
    precisely because such a row asserts nothing about what the agent may do.
    A row carrying ``authority`` asserts exactly that, so the new field must
    fall outside the allowlist rather than ride in on the surface that carries
    it. The allowlist is what makes this hold; the test is what keeps it true.
    """

    from agents_shipgate.core.boundary_diff import DiffFile, ResolvedFileText
    from agents_shipgate.core.manifest_proposals import (
        assess_coverage_increasing_tool_source_proposal,
    )

    root = tmp_path / "repo"
    root.mkdir()
    (root / "tools.json").write_text('{"tools": []}\n', encoding="utf-8")
    (root / "more.json").write_text('{"tools": []}\n', encoding="utf-8")
    old = (
        'version: "0.1"\n'
        "project:\n  name: authorship\n"
        "agent:\n  name: asst\n  declared_purpose:\n    - test authorship\n"
        "environment:\n  target: local\n"
        "tool_sources:\n  - id: tools\n    type: mcp\n    path: tools.json\n"
    )
    (root / "shipgate.yaml").write_text(old, encoding="utf-8")

    plain_rows = ["  - id: more", "    type: mcp", "    path: more.json"]
    authority_rows = [*plain_rows, "    authority:", "      mode: none"]

    def _assess(rows: list[str]):
        new_text = old + "".join(f"{line}\n" for line in rows)
        return assess_coverage_increasing_tool_source_proposal(
            workspace=root,
            diff_file=DiffFile(
                old_path="shipgate.yaml",
                new_path="shipgate.yaml",
                added_lines=list(rows),
            ),
            resolved=ResolvedFileText(
                old_text=old,
                new_text=new_text,
                source="test",
                old_sha256=None,
                new_sha256=None,
            ),
            manifest_dir=root,
        )

    safe = _assess(plain_rows)
    assert safe.proposal_safe is True, safe.reason
    assert safe.added_source_ids == ("more",)

    refused = _assess(authority_rows)
    assert refused.proposal_safe is False
    assert "authority-bearing" in refused.reason


def test_a_source_authority_placeholder_is_human_owned() -> None:
    """``doctor`` must not publish an executable edit for this block.

    Placeholder ownership is matched against every segment of the reported
    path, and ``authority`` is already a human-owned leaf name — which is what
    makes the new block human-owned without a second rule. Pinned because the
    consequence of losing it is an agent-executable edit for a declaration only
    a person may make.
    """

    from agents_shipgate.cli.discovery.placeholders import placeholder_owner

    for path in (
        "tool_sources[0].authority.mode",
        "tool_sources[0].authority.auth_type",
        "tool_sources[0].authority.scopes[0]",
    ):
        assert placeholder_owner(path) == "human", path
    # The row itself stays agent-owned: a missing source path is ordinary
    # repository reading, and routing it to a human stops a turn for nothing.
    assert placeholder_owner("tool_sources[0].path") == "coding_agent"


def test_every_gap_kind_a_source_can_own_has_its_own_phrase() -> None:
    """A row about a source must not be described in the voice of an action.

    ``_GAP_PHRASE`` is keyed by kind alone, so a source-scoped row inherited
    "an action has no declared authority (crm [tool_source])" — a sentence
    describing neither the subject nor the edit. The override table has to
    cover every kind that can carry ``subject_kind: tool_source``.

    That set is no longer the questionnaire's alone: a reviewed
    ``tool_sources[].binding`` that reaches no tool is a second, independent
    producer of a source-scoped row (#432). ``SOURCE_SCOPED_GAP_KINDS`` is
    where it is stated, beside the table it governs, and the questionnaire's
    kinds have to be inside it rather than equal to it.
    """

    from agents_shipgate.core.evidence_actions import (
        _GAP_PHRASE,
        _SOURCE_SCOPED_GAP_PHRASE,
        SOURCE_SCOPED_GAP_KINDS,
    )

    assert set(_SOURCE_SCOPED_GAP_PHRASE) == set(SOURCE_SCOPED_GAP_KINDS)
    assert set(SOURCE_ANSWERABLE_AUTHORITY_KINDS) <= SOURCE_SCOPED_GAP_KINDS
    for kind, phrase in _SOURCE_SCOPED_GAP_PHRASE.items():
        assert phrase != _GAP_PHRASE[kind]
        assert "action" not in phrase.split(" ")[1]


# --------------------------------------------------------------------------
# 6. One answer about what an action is granted
# --------------------------------------------------------------------------


def _granted(tmp_path: Path, *, source_authority: dict | None, action: dict | None):
    """``(required_scopes, resolved authority scopes)`` for one action."""

    config = _workspace(
        tmp_path,
        tools=[_mcp_tool("send_email", "Send an email to a customer.")],
        source_authority=source_authority,
        actions=[{"tool": "send_email", "source_id": "crm", **action}] if action else None,
    )
    report = _scan(tmp_path, config)
    fact = report.action_surface_facts.actions[0]
    assert fact.semantic_assessment is not None
    return fact.required_scopes, list(fact.semantic_assessment.authority.scopes)


def test_an_action_that_declares_no_credential_is_granted_nothing(tmp_path: Path) -> None:
    """A per-action ``mode: none`` is the operative authority, and it says none.

    The source block is not consulted for this action at all: its own reviewed
    authority answers the dimension, so it holds no credential and requires no
    permission — and both surfaces say so.
    """

    granted, resolved = _granted(
        tmp_path,
        source_authority={"mode": "scoped", "auth_type": "oauth2", "scopes": ["crm.read", "crm.send"]},
        action={"authority": {"mode": "none"}},
    )
    assert granted == resolved == []


def test_a_bare_scope_list_does_not_make_an_action_row_the_operative_authority(
    tmp_path: Path,
) -> None:
    """The two sites are alternatives, not a mixture.

    An action that needs a permission list different from the rest of its
    source declares its own ``authority`` block beside its ``scopes``. Falling
    back to the source's list is also the conservative direction: it can only
    widen what the checks see, never narrow it.
    """

    granted, resolved = _granted(
        tmp_path,
        source_authority={"mode": "scoped", "auth_type": "oauth2", "scopes": ["crm.read", "crm.send"]},
        action={"scopes": ["crm.read"]},
    )
    assert granted == resolved == ["crm.read", "crm.send"]


def test_with_no_reviewed_authority_a_declared_scope_list_still_stands(
    tmp_path: Path,
) -> None:
    """Unchanged where neither site declares authority — the pre-existing path."""

    granted, _resolved = _granted(
        tmp_path,
        source_authority=None,
        action={"scopes": ["crm.read"]},
    )
    assert granted == ["crm.read"]


def test_a_broad_credential_declared_once_is_still_flagged(tmp_path: Path) -> None:
    """The grant is not per-action evidence, and it is not invisible either.

    Not copying the credential's scopes onto each action is only safe because
    the grant still reaches the checks that judge *credentials*: the capability
    fact carries it, so a broad grant declared once for a whole source raises
    the same broad-scope finding it would raise written out per action. Without
    this, moving a scope list into the source block would be a way to quiet a
    real signal.
    """

    config = _workspace(
        tmp_path,
        tools=[_mcp_tool("read_thing", "Read a thing from the store.")],
        source_authority={"mode": "scoped", "auth_type": "oauth2", "scopes": ["admin:*"]},
    )
    report = _scan(tmp_path, config)

    assert "SHIP-AUTH-TOOL-BROAD-SCOPE" in {finding.check_id for finding in report.findings}
    fact = report.capability_facts[0]
    assert fact.semantic_assessment is not None
    assert list(fact.semantic_assessment.authority.scopes) == ["admin:*"]


def test_adding_the_block_survives_the_base_comparison(tmp_path: Path) -> None:
    """The path that found the divergence, kept as the guard for it.

    A unit assertion that two fields agree is easy to write against the shape
    you happened to think of. This runs the real base-vs-head comparison, which
    rebuilds each capability fact from the *serialized* action — the builder
    whose validator raised ``CapabilityFactV1.authority.scopes must project
    semantic authority`` and turned adding the block into an internal error.

    It also states what adding the block should do: touch the trust root, and
    route to a human. A declaration about what an agent may do is not a change
    an agent merges.
    """

    import subprocess

    from agents_shipgate.cli.verify.orchestrator import run_verify

    repo = tmp_path / "repo"
    tools = [_mcp_tool("read_thing", "Read a thing from the datastore.")]
    config = _workspace(repo, tools=tools)

    def _git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    _git("init")
    _git("config", "user.email", "test@example.test")
    _git("config", "user.name", "Test User")
    _git("add", ".")
    _git("commit", "-m", "before the declaration")

    _workspace(
        repo,
        tools=tools,
        source_authority={"mode": "scoped", "auth_type": "oauth2", "scopes": ["crm.read"]},
    )
    _git("add", ".")
    _git("commit", "-m", "declare the source authority")

    verifier, report, _exit = run_verify(
        workspace=repo,
        config=config,
        base="HEAD~1",
        head="HEAD",
        archive_head=True,
        out=repo / "agents-shipgate-reports",
        ci_mode="advisory",
        fail_on=None,
        baseline=None,
        baseline_mode="new-findings",
        diff_from=None,
        policy_packs=None,
        plugins_enabled=False,
        strict_plugins=False,
        suggest_patches=False,
        no_heuristics=False,
        verbose=False,
    )

    assert verifier.capability_review.trust_root_touched is True
    assert verifier.control.state == "review_publishable"
    assert report.release_decision is not None
    action = report.action_surface_facts.actions[0]
    assert action.semantic_assessment is not None
    assert action.required_scopes == sorted(set(action.semantic_assessment.authority.scopes))


def test_a_declared_effect_below_a_declared_grant_is_never_quiet(tmp_path: Path) -> None:
    """The adversarial declaration, with the grant moved to the source block.

    ``effect: read`` on an action the manifest says requires ``crm.delete`` is
    a contradiction, and the whole point of reading one resolved permission
    list is that moving the grant four lines up the file does not make it go
    quiet. Written on the action row this has always been a blocking conflict;
    it has to stay one written on the source.
    """

    config = _workspace(
        tmp_path,
        tools=[_mcp_tool("fetch_record", "Fetch a record by identifier.")],
        source_authority={"mode": "scoped", "auth_type": "oauth2", "scopes": ["crm.delete"]},
        actions=[{"tool": "fetch_record", "source_id": "crm", "effect": "read"}],
    )
    report = _scan(tmp_path, config)
    coverage = report.release_decision.evidence_coverage.semantic_coverage

    assert report.release_decision.decision == "blocked"
    assert coverage.pass_eligible_actions == 0
    assert "conflicting_effect_evidence" in {
        gap.kind for gap in report.release_decision.evidence_coverage.evidence_gaps
    }


# --------------------------------------------------------------------------
# 7. The join is provenance, not a shared namespace (#410 review)
# --------------------------------------------------------------------------


def test_a_declaration_never_reaches_a_source_that_merely_shares_an_id(
    tmp_path: Path,
) -> None:
    """``Tool.source_id`` is not a foreign key into ``tool_sources``.

    Configured ids are arbitrary and per-scan adapters mint fixed ones in the
    same namespace, so an MCP row calling itself ``openai_api`` had its
    reviewed authority applied to the OpenAI API surface — clearing
    ``missing_authority_evidence`` for actions nobody declared anything about,
    and moving them from non-eligible to eligible on that dimension. The join
    runs on the provenance the dispatcher recorded instead.
    """

    (tmp_path / "tools.json").write_text(
        json.dumps({"tools": [_mcp_tool("mcp_read", "Read a record from the store.")]}),
        encoding="utf-8",
    )
    (tmp_path / "openai-tools.json").write_text(
        json.dumps(
            [
                {
                    "type": "function",
                    "function": {
                        "name": "charge_card",
                        "description": "Charge a customer card for an order.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "agent.md").write_text("Assist with billing questions.\n", encoding="utf-8")
    manifest = {
        "version": "0.1",
        "project": {"name": "collision"},
        "agent": {"name": "a", "declared_purpose": ["exercise a source-id collision"]},
        "environment": {"target": "local"},
        # The id an unconfigured per-scan adapter also mints for itself.
        "tool_sources": [
            {
                "id": "openai_api",
                "type": "mcp",
                "path": "tools.json",
                "authority": {"mode": "none"},
            }
        ],
        "agent_bindings": {
            "declarations": [
                {
                    "agent": "root",
                    "complete": True,
                    "tools": [
                        {"tool": "mcp_read", "source_id": "openai_api"},
                        {"tool": "charge_card", "source_id": "openai_api"},
                    ],
                    "handoffs": [],
                    "reason": "reviewed fixture binding",
                }
            ]
        },
        "openai_api": {
            "prompt_files": ["agent.md"],
            "tools": [{"path": "openai-tools.json"}],
        },
    }
    config = tmp_path / "shipgate.yaml"
    config.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    report = _scan(tmp_path, config)

    by_name = {
        action.tool_name: action for action in report.action_surface_facts.actions
    }
    declared = by_name["mcp_read"].semantic_assessment
    borrowed = by_name["charge_card"].semantic_assessment
    assert declared is not None and borrowed is not None

    # The configured MCP source is answered.
    assert declared.authority.status == "declared"
    assert {claim.source for claim in declared.authority.claims} == {
        DECLARED_SOURCE_AUTHORITY_SOURCE
    }
    # The OpenAI API surface, which no configured entry produced, is not.
    assert borrowed.authority.status == "unknown"
    assert not borrowed.authority.claims
    assert "missing_authority_evidence" in _authority_issues(borrowed)


def test_a_declaration_reaches_a_source_whose_adapter_mints_its_own_ids() -> None:
    """The inverse failure: a row that matched nothing it was written for.

    A ``codex_config`` entry emits results whose ids are derived from the file
    it read (``codex_config_mcp:.codex/config.toml``), matching no configured
    row. Joining on the minted id silently applied its reviewed authority
    nowhere. The dispatcher records the entry every result of that call belongs
    to, whatever the adapter chose to call it.
    """

    from agents_shipgate.core.domain import LoadedToolSource
    from agents_shipgate.core.tool_identity import (
        build_tool_identity_catalog,
        configured_tool_source,
    )
    from agents_shipgate.schemas.manifest import ToolIdentityConfig

    configured = ToolSourceConfig.model_validate(
        {
            "id": "codex",
            "type": "codex_config",
            "path": ".",
            "authority": {"mode": "unscoped", "auth_type": "api_key", "reason": "shared token"},
        }
    )
    loaded = LoadedToolSource(
        source_id="codex_config_mcp:.codex/config.toml",
        source_type="codex_config_mcp",
        configured_source_id="codex",
        tools=[
            Tool.model_validate(
                {
                    "id": "seed",
                    "name": "billing.charge",
                    "source_type": "codex_config_mcp",
                    "source_id": "codex_config_mcp:.codex/config.toml",
                    "extraction_confidence": "high",
                    "extraction": {"surface": "enumerated"},
                }
            )
        ],
    )
    catalog, _warnings = build_tool_identity_catalog([loaded], ToolIdentityConfig())

    assert [tool.configured_source_ids for tool in catalog] == [["codex"]]
    assert configured_tool_source(catalog[0], {"codex": configured}) is configured


def test_a_binding_spanning_two_configured_sources_answers_for_neither() -> None:
    """One declaration does not speak for another deployment's credential.

    A reviewed ``tool_identity`` binding may merge observations from several
    configured sources. Their credentials are separate facts, so no single
    source-wide block is the answer and the question stays on the action row.
    """

    from agents_shipgate.core.domain import LoadedToolSource
    from agents_shipgate.core.tool_identity import (
        build_tool_identity_catalog,
        configured_tool_source,
    )
    from agents_shipgate.schemas.manifest import ToolIdentityConfig

    def _loaded(configured_id: str) -> LoadedToolSource:
        return LoadedToolSource(
            source_id=configured_id,
            source_type="mcp",
            configured_source_id=configured_id,
            tools=[
                Tool.model_validate(
                    {
                        "id": f"seed_{configured_id}",
                        "name": "charge",
                        "source_type": "mcp",
                        "source_id": configured_id,
                        "extraction_confidence": "high",
                        "extraction": {"surface": "enumerated"},
                    }
                )
            ],
        )

    identity = ToolIdentityConfig.model_validate(
        {
            "bindings": [
                {
                    "id": "charge",
                    "provider": "billing",
                    "primary": {"tool": "charge", "source_id": "east", "source_type": "mcp"},
                    "members": [
                        {"tool": "charge", "source_id": "east", "source_type": "mcp"},
                        {"tool": "charge", "source_id": "west", "source_type": "mcp"},
                    ],
                    "reason": "reviewed cross-region equivalence",
                }
            ]
        }
    )
    catalog, _warnings = build_tool_identity_catalog(
        [_loaded("east"), _loaded("west")], identity
    )

    assert len(catalog) == 1
    assert catalog[0].configured_source_ids == ["east", "west"]
    by_id = {
        "east": ToolSourceConfig.model_validate(
            {"id": "east", "type": "mcp", "path": "e.json", "authority": {"mode": "none"}}
        ),
        "west": ToolSourceConfig.model_validate(
            {"id": "west", "type": "mcp", "path": "w.json", "authority": {"mode": "none"}}
        ),
    }
    assert configured_tool_source(catalog[0], by_id) is None


# --------------------------------------------------------------------------
# 8. An omitted optional field is not a claim of absence (#410 review)
# --------------------------------------------------------------------------


def test_a_declaration_that_omits_credential_mode_does_not_erase_a_published_one() -> None:
    """Dropping a control by omission is still dropping it.

    ``credential_mode`` is optional. Overwriting a published
    ``service_account`` with ``None`` left the dimension ``declared`` and
    pass-eligible while capability policies matching
    ``credential_modes: [service_account]`` silently stopped matching.
    """

    published = _tool(
        auth={
            "type": "oauth2",
            "credential_mode": "service_account",
            "scopes": ["crm.read"],
            "alternatives": [
                {"schemes": [{"name": "o", "type": "oauth2", "scopes": ["crm.read"]}]}
            ],
        }
    )
    assessed = assess_tool_semantics(
        published,
        None,
        tool_source=_source(mode="scoped", auth_type="oauth2", scopes=["crm.read"]),
    )

    assert assessed.authority.status == "declared"
    assert assessed.authority.credential_mode == "service_account"

    # A declaration that *states* a different one is still a conflict.
    conflicting = assess_tool_semantics(
        published,
        None,
        tool_source=_source(
            mode="scoped",
            auth_type="oauth2",
            credential_mode="delegated",
            scopes=["crm.read"],
        ),
    )
    assert "conflicting_authority_evidence" in _authority_issues(conflicting)


def test_a_credential_free_mode_may_not_carry_a_credential_mode() -> None:
    """``none`` is the claim that no credential exists; a mode for one contradicts it.

    Both sites accepted `{mode: none, credential_mode: service_account}`, and
    on a structurally complete read action that contradictory pair was
    pass-eligible.
    """

    for payload, model in (
        (
            {"tool": "t", "authority": {"mode": "none", "credential_mode": "service_account"}},
            ActionDeclarationConfig,
        ),
        (
            {
                "id": "s",
                "type": "mcp",
                "path": "t.json",
                "authority": {"mode": "none", "credential_mode": "service_account"},
            },
            ToolSourceConfig,
        ),
    ):
        with pytest.raises(ValidationError, match="'none' requires no credential_mode"):
            model.model_validate(payload)
