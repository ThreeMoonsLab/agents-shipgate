"""Coverage is category-aware, one comparison, and a frozen schema stays frozen.

Follow-ups to #411 (the #409 monotone rule), each a defect that shipped with it:

* the monotone comparison read a total rank, so a higher-risk declaration
  discharged a category it does not cover;
* the policy path compared a *different* rank table, so the two surfaces could
  contradict each other on one action;
* four published schema documents gained an emitted enum value while keeping
  their version identifiers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from agents_shipgate.core.action_semantics import (
    ACTION_EFFECT_RANK,
    BUILTIN_EFFECT_OBLIGATIONS,
    builtin_obligations,
)
from agents_shipgate.core.domain import Tool, ToolRiskHint
from agents_shipgate.core.semantic_assessment import (
    assess_tool_semantics,
    declaration_covers,
    effect_repair,
)
from agents_shipgate.schemas.manifest import ActionDeclarationConfig
from agents_shipgate.schemas.surfaces import ActionEffect

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DOCS = _REPO_ROOT / "docs"
_EFFECTS = sorted(ACTION_EFFECT_RANK)


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
        "risk_hints": [
            ToolRiskHint(
                tag="external_write",
                source="name",
                confidence="medium",
                basis="inferred_keyword",
            )
        ],
    }
    values.update(updates)
    return Tool.model_validate(values)


def _declaration(**updates: object) -> ActionDeclarationConfig:
    payload: dict[str, object] = {
        "tool": "send_email",
        "effect": "read",
        "authority": {"mode": "none"},
    }
    payload.update(updates)
    return ActionDeclarationConfig.model_validate(payload)


def _issue_kinds(tool: Tool, declaration: ActionDeclarationConfig):
    assessment = assess_tool_semantics(tool, declaration)
    return assessment, {issue.kind for issue in assessment.effect.issues}


# --------------------------------------------------------------------------
# Orthogonal obligations
# --------------------------------------------------------------------------


def test_a_higher_ranked_declaration_cannot_discharge_a_different_category() -> None:
    """Effects are risk-ordered; their obligations are not.

    `financial_write` outranks `external_communication` and requires approval,
    audit, and idempotency — but not confirmation, which is exactly what
    communicating outward requires. Reading rank alone made this action
    pass-eligible with no gap and no `SHIP-ACTION-EXTERNAL-COMMUNICATION-AUDIT-MISSING`,
    while the external-write risk tag sat untouched in the same report.
    """

    assessment, kinds = _issue_kinds(_tool(), _declaration(effect="financial_write"))

    assert "declaration_below_inferred_evidence" in kinds
    assert assessment.pass_eligible is False


def test_the_uncovered_row_does_not_call_a_higher_effect_weaker() -> None:
    """The row has to be true of its own state.

    "`financial_write` is weaker than `external_communication`" is false, and it
    sends the reviewer to raise an effect that already outranks the observation.
    """

    assessment, _ = _issue_kinds(_tool(), _declaration(effect="financial_write"))
    issue = next(
        item
        for item in assessment.effect.issues
        if item.kind == "declaration_below_inferred_evidence"
    )

    assert "does not carry the controls required by" in issue.message
    assert "weaker than" not in issue.message
    # And the published repair keeps the declared effect, naming the category
    # as a reviewed risk tag so its controls apply. "Raise the effect" would
    # ask for a *lower* assessment here.
    repair = effect_repair(assessment.effect)
    assert repair.kind == "declare_risk_tags"
    assert repair.risk_tags == ("external_communication",)
    assert "Raise" not in repair.instruction


def test_a_de_escalating_declaration_still_asks_to_be_raised() -> None:
    """Negative control: the #409 case keeps its own wording and remedy."""

    assessment, kinds = _issue_kinds(_tool(), _declaration())
    issue = next(
        item
        for item in assessment.effect.issues
        if item.kind == "declaration_below_inferred_evidence"
    )

    assert "declaration_below_inferred_evidence" in kinds
    assert "is weaker than inferred" in issue.message
    repair = effect_repair(assessment.effect)
    assert repair.kind == "raise_effect"
    assert repair.effect == "external_communication"
    assert repair.instruction == (
        "Raise action_surface.actions[].effect to 'external_communication'"
    )


@pytest.mark.parametrize("effect", ["write", "financial_write", "destructive"])
def test_escalating_over_an_unobliged_observation_stays_silent(effect: str) -> None:
    """The monotone rule is intact where the categories do line up."""

    tool = _tool(
        risk_hints=[
            ToolRiskHint(
                tag="writes_data",
                source="name",
                confidence="medium",
                basis="inferred_keyword",
            )
        ]
    )
    assessment, kinds = _issue_kinds(tool, _declaration(effect=effect))

    assert "declaration_below_inferred_evidence" not in kinds
    assert assessment.pass_eligible is True


def test_a_declared_risk_tag_accounts_for_the_effect_it_asserts() -> None:
    """Coverage reads the whole reviewed surface, not the `effect` field alone.

    `risk_tags: [financial_action]` produces a policy-eligible `financial_write`
    claim and applies the financial-write controls, so a heuristic reading the
    same effect is already accounted for.
    """

    tool = _tool(
        name="create_refund",
        risk_hints=[
            ToolRiskHint(
                tag="financial_action",
                source="keyword",
                confidence="medium",
                basis="inferred_keyword",
            )
        ],
    )
    with_tag = ActionDeclarationConfig.model_validate(
        {
            "tool": "create_refund",
            "effect": "destructive",
            "risk_tags": ["financial_action"],
            "authority": {"mode": "none"},
        }
    )
    assessment, kinds = _issue_kinds(tool, with_tag)

    assert kinds == set()
    assert assessment.pass_eligible is True

    # Drop the tag and the same heuristic is unaccounted for: `destructive`
    # outranks `financial_write` but obliges neither audit nor idempotency.
    without_tag = ActionDeclarationConfig.model_validate(
        {"tool": "create_refund", "effect": "destructive", "authority": {"mode": "none"}}
    )
    _, kinds_without = _issue_kinds(tool, without_tag)

    assert "declaration_below_inferred_evidence" in kinds_without


def test_every_uncovered_observation_is_named() -> None:
    """A reviewer cannot acknowledge evidence the row never showed them."""

    tool = _tool(
        risk_hints=[
            ToolRiskHint(
                tag="external_write",
                source="name",
                confidence="medium",
                basis="inferred_keyword",
            ),
            ToolRiskHint(
                tag="financial_action",
                source="body",
                confidence="medium",
                basis="inferred_keyword",
            ),
        ]
    )
    assessment, _ = _issue_kinds(tool, _declaration())
    message = next(
        item.message
        for item in assessment.effect.issues
        if item.kind == "declaration_below_inferred_evidence"
    )

    assert "inferred 'financial_write' evidence (risk_hint:body)" in message
    assert "also unaccounted for: 'external_communication' (risk_hint:name)" in message


def test_the_published_repair_closes_the_row_it_is_printed_on() -> None:
    """Exhaustive: apply what the row advertises and the row must be gone.

    A published next step that cannot change the answer is the recurring defect
    here. Three ways the instruction failed: it named the strongest observation
    and left a second one uncovered; it fell through to "declare the ``write``
    controls" for an effect that obliges none; and — checking only that the row
    itself disappeared — the ``declare_risk_tags`` route swapped a review-tier
    row for a *blocking* ``conflicting_effect_evidence`` on 281 of the 390
    declared/observed pairs that take it (#424).

    So "closed" is the whole effect dimension being clean, not the absence of
    one kind. A repair that trades one finding for a worse one has not closed
    anything.

    This walks every declared effect against every one-, two-, and
    three-observation combination, applies the repair the row publishes, and
    re-resolves.
    """

    import itertools

    tags = {
        "external_communication": "external_write",
        "financial_write": "financial_action",
        "destructive": "destructive",
        "production_operation": "production_operation",
        "code_execution": "code_execution",
        "privileged_data_access": "privileged_data_access",
        "identity_access": "identity_access",
        "write": "writes_data",
    }

    def observed(effects: tuple[str, ...]) -> Tool:
        return _tool(
            risk_hints=[
                ToolRiskHint(
                    tag=tags[effect],
                    source=f"hint{index}",
                    confidence="medium",
                    basis="inferred_keyword",
                )
                for index, effect in enumerate(effects)
            ]
        )

    unclosed: list[str] = []
    routes: set[str] = set()
    exercised = 0
    seeded = 0
    for count in (1, 2, 3):
        for effects in itertools.combinations(sorted(tags), count):
            tool = observed(effects)
            # Rows that already carry a reviewed tag as well as bare ones.
            # `risk_tags` is one key, so applying the repair replaces it: a
            # repair naming only the new category deleted a covering tag and
            # reopened the row on the next scan (#424 review). The seed is a
            # category the tool really does read, so it covers something.
            for seed in ((), (effects[0],)):
                for declared in _EFFECTS:
                    base: dict = {
                        "tool": "send_email",
                        "effect": declared,
                        "authority": {"mode": "none"},
                    }
                    if seed:
                        base["risk_tags"] = list(seed)
                    assessment, kinds = _issue_kinds(
                        tool, ActionDeclarationConfig.model_validate(base)
                    )
                    if "declaration_below_inferred_evidence" not in kinds:
                        continue
                    exercised += 1
                    seeded += bool(seed)
                    repair = effect_repair(assessment.effect)
                    routes.add(repair.kind)
                    repaired = dict(base)
                    if repair.kind == "raise_effect":
                        repaired["effect"] = repair.effect
                    else:
                        # Replaced, not unioned: that is what pasting a block
                        # that names the key does.
                        repaired["risk_tags"] = list(repair.risk_tags)
                    repaired_assessment, after = _issue_kinds(
                        tool, ActionDeclarationConfig.model_validate(repaired)
                    )
                    if after or not repaired_assessment.pass_eligible:
                        unclosed.append(
                            f"declared={declared} observed={effects} seed={seed} "
                            f"repair={repair.kind}:{repair.effect or repair.risk_tags} "
                            f"-> {sorted(after) or 'not pass-eligible'}"
                        )

    assert exercised > 100, "the matrix stopped exercising the rule"
    assert seeded > 50, "the matrix stopped exercising already-tagged rows"
    assert routes == {"raise_effect", "declare_risk_tags"}, (
        f"the matrix stopped walking both published routes: {sorted(routes)}"
    )
    assert not unclosed, "\n".join(unclosed)


def test_a_repair_never_drops_the_reading_the_reviewer_declared() -> None:
    """Raising is advertised only when it also covers the declared value.

    Otherwise "raise the effect" quietly asks for a *lower* assessment on the
    dimension the reviewer had already judged.
    """

    tool = _tool(
        risk_hints=[
            ToolRiskHint(
                tag="external_write",
                source="name",
                confidence="medium",
                basis="inferred_keyword",
            )
        ]
    )
    assessment, _ = _issue_kinds(tool, _declaration(effect="financial_write"))
    repair = effect_repair(assessment.effect)

    assert repair.kind == "declare_risk_tags"
    assert repair.effect is None


# --------------------------------------------------------------------------
# A reviewed risk tag is not source evidence (#424)
# --------------------------------------------------------------------------


def _policy_eligible_effects(assessment) -> set[str]:
    """What `_control_effects` unions — the categories whose controls apply.

    Asserted here rather than through the lens because it is the same set by
    construction, and a repair that closes the row by making the observation
    *disappear* would look identical to one that closes it by making the
    category's controls apply. Only the second is a repair.
    """

    return {
        claim.value
        for claim in assessment.effect.claims
        if claim.policy_eligible and claim.value in ACTION_EFFECT_RANK
    }


def test_the_tag_route_closes_the_row_it_is_printed_on() -> None:
    """The issue's own reproduction, applied verbatim.

    `declaration_below_inferred_evidence` publishes two routes, and the second
    names the row's own repair: add the uncovered category as a reviewed
    `risk_tags` entry. Reading that entry back as *source* evidence made the
    edit produce a blocking `conflicting_effect_evidence` whose message blamed
    the reviewer's own manifest — a published next step that could not close
    the row it was printed on.
    """

    tool = _tool(
        risk_hints=[
            ToolRiskHint(
                tag="external_write",
                source="keyword",
                confidence="medium",
                basis="inferred_keyword",
            ),
            ToolRiskHint(
                tag="destructive",
                source="keyword",
                confidence="medium",
                basis="inferred_keyword",
            ),
        ]
    )
    raised, kinds = _issue_kinds(tool, _declaration(effect="external_communication"))
    assert kinds == {"declaration_below_inferred_evidence"}

    repair = effect_repair(raised.effect)
    assert repair.kind == "declare_risk_tags"
    assert repair.risk_tags == ("destructive",)

    repaired, after = _issue_kinds(
        tool,
        _declaration(
            effect="external_communication", risk_tags=list(repair.risk_tags)
        ),
    )
    assert after == set()
    assert repaired.pass_eligible is True
    # And it closed by *applying* the destructive controls, not by silencing
    # the observation: the declared tag is policy-eligible evidence.
    assert "destructive" in _policy_eligible_effects(repaired)
    assert repaired.conservative_effect == "destructive"


def test_the_other_spelling_of_a_reviewed_tag_reads_the_same_way() -> None:
    """`risk_overrides.tags` says the same thing once for a whole selector.

    It reaches the effect dimension as `risk_hint:manual` with basis
    `reviewed_declaration` rather than through the action row, so a fix scoped
    to the action row alone would leave the sibling surface contradicting the
    declaration beside it.
    """

    tool = _tool(
        risk_hints=[
            ToolRiskHint(
                tag="external_write",
                source="name",
                confidence="medium",
                basis="inferred_keyword",
            ),
            # Exactly what `_apply_manual_override` writes for a
            # `risk_overrides.tags` entry.
            ToolRiskHint(
                tag="destructive",
                source="manual",
                confidence="high",
                basis="reviewed_declaration",
            ),
        ]
    )
    assessment, kinds = _issue_kinds(
        tool, _declaration(effect="external_communication")
    )

    assert kinds == set()
    assert assessment.pass_eligible is True
    assert "destructive" in _policy_eligible_effects(assessment)


def test_the_row_never_names_the_manifest_as_the_source_agreeing_with_it() -> None:
    """A row saying the source agrees has to mean the source.

    The row states both readings so an override is one line to write, and the
    corroboration half says, in so many words, that *source* evidence agrees.
    The filter behind it excluded the action row only, so a reviewed
    `risk_overrides.tags` entry matching the declared effect was named to the
    reviewer as the source agreeing with them — the manifest confirming itself,
    which the comment above that filter already forbids (#424 review).
    """

    tool = _tool(
        risk_hints=[
            ToolRiskHint(
                tag="financial_action",
                source="name",
                confidence="medium",
                basis="inferred_keyword",
            ),
            # `risk_overrides.tags: [external_write]` — the same value the
            # declaration below states.
            ToolRiskHint(
                tag="external_write",
                source="manual",
                confidence="high",
                basis="reviewed_declaration",
            ),
        ]
    )
    assessment, _ = _issue_kinds(tool, _declaration(effect="external_communication"))
    message = next(
        item.message
        for item in assessment.effect.issues
        if item.kind == "declaration_below_inferred_evidence"
    )

    assert "source evidence agrees" not in message
    assert "risk_hint:manual" not in message
    # Negative control: a claim the source really did publish still corroborates.
    annotated, _ = _issue_kinds(
        _tool(
            annotations={"httpMethod": "POST"},
            risk_hints=[
                ToolRiskHint(
                    tag="financial_action",
                    source="name",
                    confidence="medium",
                    basis="inferred_keyword",
                )
            ],
        ),
        _declaration(effect="write"),
    )
    corroborated = next(
        item.message
        for item in annotated.effect.issues
        if item.kind == "declaration_below_inferred_evidence"
    )
    assert "source evidence agrees with the declaration (openapi_method)" in corroborated


def test_only_the_manifest_can_write_a_reviewed_risk_hint() -> None:
    """The trust premise the exclusion rests on, pinned.

    Excluding `risk_hint:manual` from the source-evidence comparison is only
    safe because tool-published content cannot produce one. Two things make
    that true and both are one edit away from not being: `_validated_hint_basis`
    grants the `reviewed_declaration` basis to no other hint source, and no
    adapter writes a hint with that basis or that source. Adapters read
    untrusted files; if one could emit either, a server could describe itself
    into a claim the resolver stops reading as its own.
    """

    from agents_shipgate.core.semantic_assessment import _validated_hint_basis

    tool = _tool()
    for source in ("mcp_annotation", "openapi_method", "keyword", "n8n_static", "name"):
        hint = ToolRiskHint(
            tag="destructive",
            source=source,
            confidence="high",
            basis="reviewed_declaration",
        )
        assert _validated_hint_basis(tool, hint, None) == "unknown", (
            f"{source} was granted the reviewed basis"
        )

    adapters = _REPO_ROOT / "src" / "agents_shipgate" / "inputs"
    offenders = [
        str(path.relative_to(_REPO_ROOT))
        for path in sorted(adapters.rglob("*.py"))
        if "reviewed_declaration" in (text := path.read_text(encoding="utf-8"))
        or '"manual"' in text
    ]
    assert not offenders, (
        "an adapter can now write a reviewed risk hint: " + ", ".join(offenders)
    )


@pytest.mark.parametrize(
    ("tool_updates", "declaration_updates"),
    [
        # A source annotation the server published.
        ({"annotations": {"httpMethod": "POST"}}, {}),
        # A permission list the source itself carries.
        ({"auth": {"type": "oauth2", "scopes": ["crm:write"]}}, {}),
        # A permission list the *manifest* declares. #417 deliberately made a
        # declared `crm.delete` grant bound the action's effect, so this one
        # must keep contradicting a weaker declaration even though it is
        # manifest-owned — which is why the exclusion is the two risk-tag
        # spellings and never `_is_manifest_owned` wholesale.
        (
            {},
            {
                "scopes": ["crm.delete"],
                "authority": {"mode": "scoped", "auth_type": "oauth2"},
            },
        ),
    ],
)
def test_a_declared_tag_never_launders_away_evidence_it_does_not_own(
    tool_updates: dict, declaration_updates: dict
) -> None:
    """Negative control: the exclusion is scoped to reviewed risk tags.

    Adding `risk_tags: [destructive]` accounts for the *heuristic* reading of
    the same category. It may not answer a claim the reviewer did not write —
    protocol structure, a source's own scopes, or a declared grant.
    """

    for risk_tags in ([], ["destructive"]):
        _, kinds = _issue_kinds(
            _tool(**tool_updates),
            _declaration(effect="read", risk_tags=risk_tags, **declaration_updates),
        )
        assert "conflicting_effect_evidence" in kinds, (
            f"risk_tags={risk_tags} silenced evidence it does not own"
        )


# --------------------------------------------------------------------------
# One comparison, both surfaces
# --------------------------------------------------------------------------


@pytest.mark.parametrize("declared", _EFFECTS)
@pytest.mark.parametrize("inferred", _EFFECTS)
def test_both_rank_tables_must_agree_before_a_declaration_covers(
    declared: str, inferred: str
) -> None:
    """The pairwise matrix, over both published rank orders.

    `_EFFECT_RANK` puts `privileged_data_access` above `write`;
    `ACTION_EFFECT_RANK` puts it below. Picking a winner would either loosen an
    existing gate path or leave the declaration rule and the policy path
    contradicting each other on the same action.
    """

    from agents_shipgate.core.semantic_assessment import _EFFECT_RANK

    covers = declaration_covers(declared, inferred)
    if declared == inferred:
        assert covers
        return
    expected = (
        _EFFECT_RANK[cast(ActionEffect, declared)] >= _EFFECT_RANK[cast(ActionEffect, inferred)]
        and ACTION_EFFECT_RANK[cast(ActionEffect, declared)]
        >= ACTION_EFFECT_RANK[cast(ActionEffect, inferred)]
        and builtin_obligations(cast(ActionEffect, inferred)).issubset(
            builtin_obligations(cast(ActionEffect, declared))
        )
    )
    assert covers is expected


def test_the_policy_path_asks_the_same_question_as_the_declaration_rule() -> None:
    """The two consumers must not reach opposite answers on one action.

    Verified through the lens rather than by reading: an action the declaration
    rule leaves silent must not be raised as `mixed_policy_evidence`, because no
    override can close a row the declaration rule never opened.
    """

    from agents_shipgate.core.lenses.action_surface import (
        _non_authoritative_effect_escalation_support,
        build_action_surface_facts,
    )
    from agents_shipgate.core.semantic_assessment import attach_semantic_assessments
    from agents_shipgate.schemas.manifest import AgentsShipgateManifest

    tool = _tool(
        name="read_record",
        risk_hints=[
            ToolRiskHint(
                tag="writes_data",
                source="name",
                confidence="medium",
                basis="inferred_keyword",
            )
        ],
    )
    declaration = ActionDeclarationConfig.model_validate(
        {
            "tool": "read_record",
            "effect": "privileged_data_access",
            "authority": {"mode": "none"},
        }
    )
    _, kinds = _issue_kinds(tool, declaration)
    manifest = AgentsShipgateManifest.model_validate(
        {
            "version": "0.1",
            "project": {"name": "coverage"},
            "agent": {"name": "agent", "declared_purpose": ["read"]},
            "environment": {"target": "local"},
            "tool_sources": [{"id": "closer", "type": "mcp", "path": "tools.json"}],
            "action_surface": {"actions": [declaration.model_dump(exclude_none=True)]},
        }
    )
    tools = attach_semantic_assessments([tool], {tool.id: declaration})
    facts = build_action_surface_facts(manifest, agent_id="agent", tools=tools)
    support = _non_authoritative_effect_escalation_support(facts.actions[0])

    silent_here = "declaration_below_inferred_evidence" not in kinds
    silent_there = support is None
    assert silent_here == silent_there


#: The built-in action-control checks whose ``missing`` list *is* the obligation
#: set for an effect. Other checks also publish a ``missing`` field (manifest
#: hygiene, side-effect hygiene); folding those in would compare the table
#: against controls it never claimed to describe.
_BUILTIN_CONTROL_CHECKS = frozenset(
    {
        "SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING",
        "SHIP-ACTION-EXTERNAL-COMMUNICATION-AUDIT-MISSING",
        "SHIP-ACTION-DESTRUCTIVE-ROLLBACK-MISSING",
        "SHIP-ACTION-POLICY-VIOLATION",
    }
)


def test_the_builtin_obligation_table_matches_the_controls_that_fire(tmp_path) -> None:
    """Pin the table against the branches it mirrors, through a real scan.

    The obligations live as inline literals in
    ``_current_action_policy_findings``. This walks **every** effect, not only
    the ones in the table, and compares the exact set both ways.

    One direction is the unsafe one. ``issubset`` alone still passed when a
    branch gained a control the table omits, and skipped an effect deleted from
    the table entirely — and it is precisely those cases where
    ``declaration_covers`` would then discharge an observation whose new control
    never gets applied (PR #413 review 4).
    """

    from agents_shipgate.cli.scan import run_scan

    for effect in sorted(ACTION_EFFECT_RANK):
        obligations = BUILTIN_EFFECT_OBLIGATIONS.get(effect, frozenset())
        workspace = tmp_path / effect
        workspace.mkdir()
        (workspace / "tools.json").write_text(
            json.dumps({"tools": [{"name": "act", "description": "An action."}]}),
            encoding="utf-8",
        )
        (workspace / "shipgate.yaml").write_text(
            f"""
version: "0.1"
project: {{name: obligations}}
agent:
  name: agent
  declared_purpose: [act]
environment: {{target: local}}
tool_sources:
  - id: src
    type: mcp
    path: tools.json
agent_bindings:
  declarations:
    - agent: root
      complete: true
      tools: [{{tool: act, source_id: src}}]
      handoffs: []
      reason: reviewed test binding
action_surface:
  actions:
    - tool: act
      source_id: src
      effect: {effect}
      authority:
        mode: none
""",
            encoding="utf-8",
        )
        report, _ = run_scan(
            config_path=workspace / "shipgate.yaml",
            output_dir=workspace / "out",
            formats=["json"],
            ci_mode="advisory",
            packet_enabled=False,
        )
        missing: set[str] = set()
        for finding in report.findings:
            if finding.check_id not in _BUILTIN_CONTROL_CHECKS:
                continue
            raw = finding.evidence.get("missing")
            if not isinstance(raw, list):
                continue
            for item in raw:
                if isinstance(item, str):
                    missing.add(item)
                elif isinstance(item, dict) and isinstance(item.get("path"), str):
                    missing.add(item["path"])
        assert missing == set(obligations), (
            f"{effect}: BUILTIN_EFFECT_OBLIGATIONS says {sorted(obligations)}, but the "
            f"built-in controls reported {sorted(missing)} missing on an action "
            "declaring none of them. A control the table omits is one "
            "declaration_covers can discharge without ever applying it."
        )


# --------------------------------------------------------------------------
# A published schema identifier never gains a value
# --------------------------------------------------------------------------

#: Every published document a schema bump has rewritten, with the version that
#: change froze, the version that carries the new content, and the token that
#: distinguishes them. `generate_schemas.py --check` proves committed ==
#: generated; it cannot prove that a *content* change moved the version, so new
#: content can be written into a frozen document with CI green and every pinned
#: consumer left rejecting the artifacts that document is supposed to describe.
#:
#: The marker is per-row because each bump introduced something different: the
#: #409 rows gained a gap kind, the #410-increment-2 rows gained the
#: declaration-question projection. A single shared token would silently stop
#: testing the older rows the moment a newer bump were added.
_FROZEN_AND_CURRENT_SCHEMAS = [
    ("report-schema.v0.35.json", "report-schema.v0.36.json", "declaration_below_inferred_evidence"),
    ("packet-schema.v0.12.json", "packet-schema.v0.13.json", "declaration_below_inferred_evidence"),
    ("verifier-schema.v0.9.json", "verifier-schema.v0.10.json", "declaration_below_inferred_evidence"),
    (
        "capability-lock-schema.v0.6.json",
        "capability-lock-schema.v0.7.json",
        "declaration_below_inferred_evidence",
    ),
    (
        "capability-lock-diff-schema.v0.7.json",
        "capability-lock-diff-schema.v0.8.json",
        "declaration_below_inferred_evidence",
    ),
    ("report-schema.v0.36.json", "report-schema.v0.37.json", "DeclarationQuestionCoverage"),
    ("packet-schema.v0.13.json", "packet-schema.v0.14.json", "DeclarationQuestionCoverage"),
    ("verifier-schema.v0.10.json", "verifier-schema.v0.11.json", "DeclarationQuestionCoverage"),
    ("report-schema.v0.37.json", "report-schema.v0.38.json", "tool_source"),
    ("packet-schema.v0.14.json", "packet-schema.v0.15.json", "tool_source"),
    ("verifier-schema.v0.11.json", "verifier-schema.v0.12.json", "tool_source"),
    ("report-schema.v0.38.json", "report-schema.v0.39.json", "declaration_drift"),
    ("packet-schema.v0.15.json", "packet-schema.v0.16.json", "declaration_drift"),
    ("verifier-schema.v0.12.json", "verifier-schema.v0.13.json", "declaration_drift"),
    (
        "capability-lock-schema.v0.7.json",
        "capability-lock-schema.v0.8.json",
        "declaration_drift",
    ),
    (
        "capability-lock-diff-schema.v0.8.json",
        "capability-lock-diff-schema.v0.9.json",
        "declaration_drift",
    ),
    # #410 §F: the effective-policy snapshot gained the control pack in force,
    # so a base-vs-head comparison can see a pack moved to one that requires
    # less. Only the report schema embeds ``EffectivePolicy``.
    ("report-schema.v0.39.json", "report-schema.v0.40.json", "control_pack"),
    ("report-schema.v0.40.json", "report-schema.v0.41.json", "authorable_by"),
    ("packet-schema.v0.16.json", "packet-schema.v0.17.json", "authorable_by"),
    ("verifier-schema.v0.13.json", "verifier-schema.v0.15.json", "authorable_by"),
    # #432: the binding graph gained the entry points the reachability walk
    # started from, and every node gained the kind it is. Only the report
    # schema embeds ``AgentBindingGraphAssessment``.
    (
        "report-schema.v0.41.json",
        "report-schema.v0.42.json",
        "entry_point_agent_ids",
    ),
    ("report-schema.v0.42.json", "report-schema.v0.43.json", "declaration_review"),
    # The 1.0 freeze (#569). The two documents are byte-identical apart from
    # $id/title/version, so no member token distinguishes them -- the marker is
    # the version constant itself, which is the only thing that moved.
    ("report-schema.v0.43.json", "report-schema.v1.0.json", '"const": "1.0"'),
    ("packet-schema.v0.17.json", "packet-schema.v0.18.json", "declaration_review"),
    ("verifier-schema.v0.15.json", "verifier-schema.v0.16.json", "declaration_review"),
]


@pytest.mark.parametrize(("frozen", "current", "marker"), _FROZEN_AND_CURRENT_SCHEMAS)
def test_new_content_reaches_only_the_current_schema(
    frozen: str, current: str, marker: str
) -> None:
    frozen_text = (_DOCS / frozen).read_text(encoding="utf-8")
    current_text = (_DOCS / current).read_text(encoding="utf-8")

    assert marker not in frozen_text, (
        f"{frozen} is published and pinned; a consumer validating against it would "
        "reject artifacts this version never described"
    )
    assert marker in current_text


def test_every_frozen_row_is_under_the_byte_freeze() -> None:
    """The freeze is about bytes, not only about the token that exposed it.

    The bytes are pinned in one place, ``scripts/published_schema_pins.json``,
    over a set derived from the release tags rather than from a hand list, and
    ``tests/test_published_schema_freeze.py`` holds every published file to its
    pin (#1044). A row above naming an unpinned file would be a frozen document
    outside that guard.
    """

    pins = json.loads(
        (_REPO_ROOT / "scripts" / "published_schema_pins.json").read_text(encoding="utf-8")
    )["sha256"]

    assert {frozen for frozen, _, _ in _FROZEN_AND_CURRENT_SCHEMAS} <= set(pins)


def test_every_emitted_artifact_validates_against_its_own_current_schema(tmp_path) -> None:
    """The reason the freeze matters: what we emit must match what we publish."""

    from jsonschema import Draft202012Validator

    from agents_shipgate.cli.scan import run_scan

    run_scan(
        config_path=Path("samples/support_refund_agent/shipgate.yaml"),
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=True,
        packet_generated_at="2026-01-01T00:00:00+00:00",
    )

    for artifact, schema_name in (
        ("report.json", "report-schema.v1.0.json"),
        ("packet.json", "packet-schema.v0.18.json"),
    ):
        payload = json.loads((tmp_path / artifact).read_text(encoding="utf-8"))
        schema = json.loads((_DOCS / schema_name).read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(payload)


def test_every_lock_schema_of_the_current_standard_still_loads(tmp_path) -> None:
    """Bumping the lock schema must not orphan every committed lock file.

    The previous version of this test pinned `0.6`, so it kept passing while
    the `0.7` → `0.8` bump made every committed v0.7 lock unloadable: v0.7 was
    readable only because it *was* the current constant, and nothing put it in
    `_STRUCTURALLY_CURRENT_LOCK_SCHEMAS` when it stopped being one.

    So this walks the **published** schema set instead of a version literal,
    and asks the question that actually matters: a lock describing the current
    capability standard is one somebody can still diff, so it has to load.
    Older standards are a different case — they are not comparable and must be
    re-exported — and this deliberately says nothing about them.
    """

    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_capability_lock import _lock  # noqa: PLC0415
    from test_capability_lock import _tool as _lock_tool

    from agents_shipgate.core.capability_lock import (
        _CAPABILITY_STANDARD_BY_LOCK_SCHEMA,
        load_capability_lock,
        render_capability_lock_json,
    )
    from agents_shipgate.schemas.capabilities import (
        CAPABILITY_LOCK_SCHEMA_VERSION,
        CAPABILITY_STANDARD_VERSION,
    )

    published = sorted(
        path.name.removeprefix("capability-lock-schema.v").removesuffix(".json")
        for path in _DOCS.glob("capability-lock-schema.v*.json")
    )
    assert CAPABILITY_LOCK_SCHEMA_VERSION in published

    # Every published version needs an explicit row. The lookup defaults to the
    # *current* standard, so a missing one silently claims a historical lock is
    # comparable — which is what a version stops being covered by the constant
    # key the moment the next bump takes it (PR #413 review 5).
    assert set(published) <= set(_CAPABILITY_STANDARD_BY_LOCK_SCHEMA), sorted(
        set(published) - set(_CAPABILITY_STANDARD_BY_LOCK_SCHEMA)
    )

    payload = json.loads(
        render_capability_lock_json(_lock([_lock_tool("alpha.read", scopes=["alpha:read"])]))
    )
    checked = 0
    for version in published:
        if _CAPABILITY_STANDARD_BY_LOCK_SCHEMA[version] != CAPABILITY_STANDARD_VERSION:
            continue
        checked += 1
        path = tmp_path / f"lock.v{version}.json"
        path.write_text(
            json.dumps({**payload, "capability_lock_schema_version": version}, indent=2),
            encoding="utf-8",
        )
        loaded = load_capability_lock(path)
        assert loaded.capability_lock_schema_version == CAPABILITY_LOCK_SCHEMA_VERSION, version
    assert checked > 1, (
        "only the current schema describes this standard; the test cannot see "
        "an orphaned prior version"
    )


def test_the_published_examples_are_artifacts_this_runtime_could_emit() -> None:
    """JSON Schema validation is not enough for an example.

    `capability-lock-diff.v0.8.example.json` validated cleanly while embedding
    lock refs at `0.6` — a tuple the CLI cannot produce, because the loader
    advances a `0.6` lock and `_lock_ref` stamps the current version. And the
    lock example attributed v0.7 output to a CLI release that emitted v0.6. An
    example a reader copies has to be one the tool would actually write
    (PR #413 review 6).
    """

    from agents_shipgate import __version__ as cli_version
    from agents_shipgate.schemas.capabilities import (
        CAPABILITY_LOCK_DIFF_SCHEMA_VERSION,
        CAPABILITY_LOCK_SCHEMA_VERSION,
        CAPABILITY_STANDARD_VERSION,
        CapabilityLockDiffV1,
        CapabilityLockFileV1,
    )

    lock = CapabilityLockFileV1.model_validate(
        json.loads(
            (_DOCS / "examples" / f"capability-lock.v{CAPABILITY_LOCK_SCHEMA_VERSION}.example.json")
            .read_text(encoding="utf-8")
        )
    )
    assert lock.capability_lock_schema_version == CAPABILITY_LOCK_SCHEMA_VERSION
    assert lock.cli_version == cli_version, (
        "the example attributes this output to a release that emitted a different "
        "lock schema"
    )

    diff = CapabilityLockDiffV1.model_validate(
        json.loads(
            (
                _DOCS
                / "examples"
                / f"capability-lock-diff.v{CAPABILITY_LOCK_DIFF_SCHEMA_VERSION}.example.json"
            ).read_text(encoding="utf-8")
        )
    )
    assert diff.capability_lock_diff_schema_version == CAPABILITY_LOCK_DIFF_SCHEMA_VERSION
    # Both sides are read through the loader before a diff is taken, so a diff
    # this runtime emits can only ever reference the current lock schema.
    for side in (diff.base, diff.head):
        assert side.capability_lock_schema_version == CAPABILITY_LOCK_SCHEMA_VERSION

    assert CAPABILITY_STANDARD_VERSION == "0.5"
