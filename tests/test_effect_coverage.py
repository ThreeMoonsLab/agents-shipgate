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
import subprocess
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
    effect_remedy_instruction,
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
    # And the published remedy names controls rather than a lower effect.
    instruction = effect_remedy_instruction(assessment.effect)
    assert "Declare the external_communication controls" in instruction
    assert "Raise" not in instruction


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
    assert effect_remedy_instruction(assessment.effect) == (
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


def test_the_builtin_obligation_table_matches_the_controls_that_fire(tmp_path) -> None:
    """Pin the table against the branches it mirrors, through a real scan.

    The obligations live as inline literals in
    ``_current_action_policy_findings``. Walking every entry keeps the table the
    comparator reads from drifting off the controls the gate actually applies.
    """

    from agents_shipgate.cli.scan import run_scan

    for effect, obligations in sorted(BUILTIN_EFFECT_OBLIGATIONS.items()):
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
            raw = finding.evidence.get("missing")
            if not isinstance(raw, list):
                continue
            for item in raw:
                if isinstance(item, str):
                    missing.add(item)
                elif isinstance(item, dict) and isinstance(item.get("path"), str):
                    missing.add(item["path"])
        assert obligations.issubset(missing), (
            f"{effect}: table claims {sorted(obligations)} but the scan reported "
            f"{sorted(missing)} missing on an action declaring none of them"
        )


# --------------------------------------------------------------------------
# A published schema identifier never gains a value
# --------------------------------------------------------------------------

#: Every published document that projects the evidence-gap union, with the
#: version this change froze and the version that carries the new value.
#: `generate_schemas.py --check` proves committed == generated; it cannot prove
#: that a *content* change moved the version, so a new enum can be written into
#: a frozen document with CI green and every pinned consumer left rejecting the
#: artifacts that document is supposed to describe.
_FROZEN_AND_CURRENT_SCHEMAS = [
    ("report-schema.v0.35.json", "report-schema.v0.36.json"),
    ("packet-schema.v0.12.json", "packet-schema.v0.13.json"),
    ("verifier-schema.v0.9.json", "verifier-schema.v0.10.json"),
    ("capability-lock-schema.v0.6.json", "capability-lock-schema.v0.7.json"),
    ("capability-lock-diff-schema.v0.7.json", "capability-lock-diff-schema.v0.8.json"),
]


@pytest.mark.parametrize(("frozen", "current"), _FROZEN_AND_CURRENT_SCHEMAS)
def test_the_new_gap_kind_reaches_only_the_current_schema(frozen: str, current: str) -> None:
    frozen_text = (_DOCS / frozen).read_text(encoding="utf-8")
    current_text = (_DOCS / current).read_text(encoding="utf-8")

    assert "declaration_below_inferred_evidence" not in frozen_text, (
        f"{frozen} is published and pinned; a consumer validating against it would "
        "reject artifacts this version never described"
    )
    assert "declaration_below_inferred_evidence" in current_text


@pytest.mark.parametrize(("frozen", "current"), _FROZEN_AND_CURRENT_SCHEMAS)
def test_a_frozen_schema_keeps_the_bytes_it_was_published_with(
    frozen: str, current: str
) -> None:
    """The freeze is about bytes, not only about the enum that exposed it."""

    del current
    published = subprocess.run(
        ["git", "show", f"0c5f40fc~1:docs/{frozen}"],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        check=False,
    )
    if published.returncode != 0:
        pytest.skip(f"docs/{frozen} has no pre-#411 revision to compare")
    assert published.stdout == (_DOCS / frozen).read_text(encoding="utf-8")


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
        ("report.json", "report-schema.v0.36.json"),
        ("packet.json", "packet-schema.v0.13.json"),
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
