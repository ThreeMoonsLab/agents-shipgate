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

import hashlib
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
    here. Two ways the single-value instruction failed: it named the strongest
    observation and left a second one uncovered, and it fell through to
    "declare the ``write`` controls" for an effect that obliges none.

    This walks every declared effect against every one- and two-observation
    combination, applies the repair the row publishes, and re-resolves.
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
    exercised = 0
    for count in (1, 2):
        for effects in itertools.combinations(sorted(tags), count):
            tool = observed(effects)
            for declared in _EFFECTS:
                base = {
                    "tool": "send_email",
                    "effect": declared,
                    "authority": {"mode": "none"},
                }
                assessment, kinds = _issue_kinds(
                    tool, ActionDeclarationConfig.model_validate(base)
                )
                if "declaration_below_inferred_evidence" not in kinds:
                    continue
                exercised += 1
                repair = effect_repair(assessment.effect)
                repaired = dict(base)
                if repair.kind == "raise_effect":
                    repaired["effect"] = repair.effect
                else:
                    repaired["risk_tags"] = list(repair.risk_tags)
                _, after = _issue_kinds(
                    tool, ActionDeclarationConfig.model_validate(repaired)
                )
                if "declaration_below_inferred_evidence" in after:
                    unclosed.append(
                        f"declared={declared} observed={effects} "
                        f"repair={repair.kind}:{repair.effect or repair.risk_tags}"
                    )

    assert exercised > 100, "the matrix stopped exercising the rule"
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


#: sha256 of each frozen document as published. Checked in rather than read
#: from git history: the main CI job checks out with ``fetch-depth: 1``, so a
#: ``git show <old-rev>`` guard skipped in exactly the runs that must enforce
#: this — a silent skip on the invariant, which is worse than not having it
#: (PR #413 review 3). A deliberate re-freeze updates the hash here, in the
#: same commit, where a reviewer sees it.
_PUBLISHED_SCHEMA_SHA256 = {
    "report-schema.v0.35.json": (
        "cd3a971dd6a02676cfa0db798a443715778b364bd80510b23f3c83ea876003cf"
    ),
    "packet-schema.v0.12.json": (
        "8bef394df037374a14153b743f46a4e279f65b8e1312842efcdec92a07d0bcb5"
    ),
    "verifier-schema.v0.9.json": (
        "d477db7c202ba9c0629fa28ade7a69d37758052de370aa3246135ede9eeefeaf"
    ),
    "capability-lock-schema.v0.6.json": (
        "0e43ccadea3258323cac279a59a431e771c2abbe9f4333c853ec5d1401f5285a"
    ),
    "capability-lock-diff-schema.v0.7.json": (
        "e73210870bb5b1181fcbb90872ced3c71f3ecc1f11124188aa21579694eec93a"
    ),
    "report-schema.v0.36.json": (
        "c9c0d7576b23fbacfff2da390d462b66add673f448c28eb8295938cf2f986308"
    ),
    "packet-schema.v0.13.json": (
        "80e6665a4a4d4778b2259c96f756983b8f6857ebca3cb06ecf724c80cfae06eb"
    ),
    "verifier-schema.v0.10.json": (
        "c582772273cadcb8abd2137b03486528d681093e088a7284e754a8c75f92e727"
    ),
}


def test_every_frozen_schema_has_a_pinned_hash() -> None:
    """No frozen document may sit outside the guard."""

    assert {frozen for frozen, _, _ in _FROZEN_AND_CURRENT_SCHEMAS} == set(
        _PUBLISHED_SCHEMA_SHA256
    )


@pytest.mark.parametrize(("frozen", "current", "marker"), _FROZEN_AND_CURRENT_SCHEMAS)
def test_a_frozen_schema_keeps_the_bytes_it_was_published_with(
    frozen: str, current: str, marker: str
) -> None:
    """The freeze is about bytes, not only about the token that exposed it."""

    del current, marker
    digest = hashlib.sha256((_DOCS / frozen).read_bytes()).hexdigest()

    assert digest == _PUBLISHED_SCHEMA_SHA256[frozen], (
        f"docs/{frozen} is published and pinned; consumers validate against "
        "these exact bytes. Emit new content under a new version instead."
    )


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
        ("report.json", "report-schema.v0.37.json"),
        ("packet.json", "packet-schema.v0.14.json"),
    ):
        payload = json.loads((tmp_path / artifact).read_text(encoding="utf-8"))
        schema = json.loads((_DOCS / schema_name).read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(payload)


def test_a_lock_written_under_the_prior_schema_still_loads(tmp_path) -> None:
    """Bumping the lock schema must not orphan every committed lock file.

    The normalizer only handled `0.1`–`0.4`, so advancing `0.6` → `0.7` would
    have made every checked-in `capabilities.lock.json` unloadable — a larger
    break than the bump repairs.
    """

    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_capability_lock import _lock  # noqa: PLC0415
    from test_capability_lock import _tool as _lock_tool

    from agents_shipgate.core.capability_lock import (
        load_capability_lock,
        render_capability_lock_json,
    )
    from agents_shipgate.schemas.capabilities import CAPABILITY_LOCK_SCHEMA_VERSION

    payload = json.loads(
        render_capability_lock_json(_lock([_lock_tool("alpha.read", scopes=["alpha:read"])]))
    )
    payload["capability_lock_schema_version"] = "0.6"
    path = tmp_path / "legacy.v06.lock.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    loaded = load_capability_lock(path)

    assert loaded.capability_lock_schema_version == CAPABILITY_LOCK_SCHEMA_VERSION


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
