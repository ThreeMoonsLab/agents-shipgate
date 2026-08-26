"""The adoption ladder, template environments, and manifest protection (#410 §G).

Three separate mechanisms, one purpose: make every intermediate state of an
adoption a named place with a next step, instead of a verdict that reads like a
failure.

* ``environment.target: template`` is the honest answer for a repository that
  ships to be copied. It has no deployment, so it has no credentials, and
  asking each of its actions which credential it runs with asks a question the
  repository cannot answer in principle. It answers the authority dimension —
  and never silently: every action it answers for is a review concern, so a
  template repository can reach ``review_required`` and never ``passed``.
* ``SHIP-TRUST-MANIFEST-UNPROTECTED`` says whether changing the gate takes a
  named human's approval, at the one moment that matters — an enforced gate —
  and never claims the half it cannot see.
* The ladder names where an adoption stands and what moves it up.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from agents_shipgate.cli.scan import run_scan
from agents_shipgate.core.adoption_ladder import adoption_rung, audit_rung
from agents_shipgate.core.manifest_protection import (
    ManifestProtection,
    _matches,
    manifest_protection,
)
from agents_shipgate.core.semantic_assessment import assess_tool_semantics
from agents_shipgate.schemas.manifest import (
    ActionDeclarationConfig,
    AgentsShipgateManifest,
)

# --------------------------------------------------------------------------
# environment.target: template
# --------------------------------------------------------------------------


def _workspace(
    tmp_path: Path,
    *,
    tools: list[dict],
    target: str = "template",
    actions: list[dict] | None = None,
    sources: list[dict] | None = None,
    ci: dict | None = None,
) -> Path:
    (tmp_path / "tools.json").write_text(json.dumps({"tools": tools}), encoding="utf-8")
    source = {"id": "src", "type": "mcp", "path": "tools.json"}
    source.update(sources[0] if sources else {})
    manifest: dict = {
        "version": "0.1",
        "project": {"name": "ladder"},
        "agent": {"name": "asst", "declared_purpose": ["exercise the ladder"]},
        "environment": {"target": target},
        "tool_sources": [source],
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
    }
    if actions:
        manifest["action_surface"] = {"actions": actions}
    if ci:
        manifest["ci"] = ci
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
    return report.release_decision


_READ_ONLY_TOOL = {
    "name": "docs.lookup",
    "description": "Look up an internal documentation article by its id.",
    "annotations": {"readOnlyHint": True},
    "inputSchema": {
        "type": "object",
        "properties": {"article_id": {"type": "string"}},
        "required": ["article_id"],
        "additionalProperties": False,
    },
}


def test_a_template_answers_the_authority_question_it_cannot_be_asked(
    tmp_path: Path,
) -> None:
    """One line answers what would otherwise be one question per action."""

    without = _scan(
        tmp_path / "a", _workspace(_mk(tmp_path / "a"), tools=[_READ_ONLY_TOOL], target="local")
    )
    with_template = _scan(
        tmp_path / "b", _workspace(_mk(tmp_path / "b"), tools=[_READ_ONLY_TOOL])
    )

    reasons = without.evidence_coverage.semantic_coverage.reason_counts
    assert reasons.get("missing_authority_evidence")
    assert not with_template.evidence_coverage.semantic_coverage.reason_counts.get(
        "missing_authority_evidence"
    )


def test_a_template_can_never_reach_passed(tmp_path: Path) -> None:
    """The property that stops it being the cheap way out.

    An adopter who declares `template` gets past the authority question and no
    further: the answer is that nothing here is deployed, which is exactly the
    thing a production adopter still has to state.
    """

    decision = _scan(tmp_path, _workspace(_mk(tmp_path), tools=[_READ_ONLY_TOOL]))
    coverage = decision.evidence_coverage.semantic_coverage

    assert decision.decision == "review_required"
    assert coverage.reason_counts.get("template_environment_authority") == 1
    assert coverage.review_concern_count >= 1
    assert "environment.target: template" in decision.reason


def test_a_template_never_overrides_a_declared_authority(tmp_path: Path) -> None:
    """The repository-wide claim is a default, not an override. An action that
    states its own authority has made the more specific statement.
    """

    config = _workspace(
        _mk(tmp_path),
        tools=[_READ_ONLY_TOOL],
        actions=[
            {
                "tool": "docs.lookup",
                "effect": "read",
                "scopes": ["docs:read"],
                "authority": {"mode": "scoped", "auth_type": "oauth2"},
            }
        ],
    )
    decision = _scan(tmp_path, config)
    coverage = decision.evidence_coverage.semantic_coverage

    assert not coverage.reason_counts.get("template_environment_authority")


def test_a_template_never_empties_a_declared_permission_list(tmp_path: Path) -> None:
    """A bare `scopes` list is a reviewed statement that this action *is*
    granted something. Letting `mode: none` win over it would both contradict
    the reviewer and quietly empty the list every surface judges the action on
    — the exact fail-open shape #410 increment 3 closed.
    """

    tool = dict(_READ_ONLY_TOOL, name="crm.delete")
    tool.pop("annotations")
    declaration = {"tool": "crm.delete", "effect": "destructive", "scopes": ["crm:delete"]}
    config = _workspace(_mk(tmp_path), tools=[tool], actions=[declaration])

    report, _ = run_scan(
        config_path=config,
        output_dir=tmp_path / "out",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    facts = report.action_surface_facts.actions
    assert facts, "fixture produced no action facts"
    assert facts[0].required_scopes == ["crm:delete"]


def test_a_template_never_subtracts_what_a_source_published(tmp_path: Path) -> None:
    """The fail-open this claim is one line away from.

    The reviewed record supplies the *whole* permission list, so applying a
    repository-wide "nothing here holds a credential" over a tool that
    publishes `oauth2 + docs:read` would empty that action's
    `required_scopes` — and `SHIP-AUTH-SCOPE-COVERAGE-MISSING` would silently
    stop seeing anything to cover. A statement about deployment may not
    subtract evidence a source proved, so the claim simply does not apply to
    an action whose source published one.
    """

    tool = dict(_READ_ONLY_TOOL)
    tool["auth"] = {"type": "oauth2", "scopes": ["docs:read"]}

    def findings_and_scopes(target: str) -> tuple[set[str], list[str]]:
        root = _mk(tmp_path / target)
        config = _workspace(root, tools=[tool], target=target)
        report, _ = run_scan(
            config_path=config,
            output_dir=root / "out",
            formats=["json"],
            ci_mode="advisory",
            packet_enabled=False,
        )
        return (
            {finding.check_id for finding in report.findings},
            list(report.action_surface_facts.actions[0].required_scopes),
        )

    local_findings, local_scopes = findings_and_scopes("local")
    template_findings, template_scopes = findings_and_scopes("template")

    assert "SHIP-AUTH-SCOPE-COVERAGE-MISSING" in local_findings
    assert local_scopes == ["docs:read"]
    # Declaring `template` may add rows; it may never remove one.
    assert local_findings <= template_findings
    assert template_scopes == local_scopes


_AUTH_SHAPES: tuple[dict, ...] = (
    {},
    {"type": "oauth2", "scopes": ["a:read"]},
    {"type": "api_key"},
    {"scopes": ["a:read"]},
    {"mode": "none"},
    {"mode": "ambient"},
    {"mode": "unscoped"},
    {"explicit": True},
    {"invalid_annotations": ["not a boolean"]},
    {"alternatives": [{"anonymous": True, "schemes": []}]},
    {
        "alternatives": [
            {
                "anonymous": False,
                "schemes": [{"name": "o", "type": "oauth2", "scopes": ["a:read"]}],
            },
            {"anonymous": False, "schemes": [{"name": "k", "type": "apiKey", "scopes": []}]},
        ]
    },
)

_DECLARATION_SHAPES: tuple[dict | None, ...] = (
    None,
    {"effect": "read"},
    {"effect": "read", "scopes": ["a:read"]},
    {"effect": "read", "authority": {"mode": "scoped", "auth_type": "oauth2"},
     "scopes": ["a:read"]},
)


def test_the_template_fallback_yields_to_every_field_it_would_replace() -> None:
    """Keyed off the record's own fields, so a fifth one cannot be forgotten.

    Installing the template record overwrites *all* of `ReviewedAuthority` —
    mode, auth type, credential mode, scopes. The first version of the guard
    tested only what `_source_authority` grades, which is three of the four: a
    tool publishing `credential_mode: service_account` and nothing else graded
    `unknown`, took `mode: none`, and had its credential mode cleared, so
    `credential_modes` policy selectors stopped matching it.
    """

    from dataclasses import fields

    from agents_shipgate.core.domain import Tool
    from agents_shipgate.core.semantic_assessment import ReviewedAuthority

    replaced = {field.name for field in fields(ReviewedAuthority)} - {
        # Not evidence about the action — they name which manifest site the
        # record came from.
        "claim_source",
        "pointer",
    }
    assert replaced == {"mode", "auth_type", "credential_mode", "scopes"}, replaced

    #: One `auth` shape per replaced field, publishing that field and nothing
    #: else. `mode` and `scopes` are graded together with `auth_type` by
    #: `_source_authority`, so each gets the minimal shape that makes it speak.
    publishing = {
        "mode": {"mode": "ambient"},
        "auth_type": {"type": "api_key"},
        "credential_mode": {"credential_mode": "service_account"},
        "scopes": {"type": "oauth2", "scopes": ["a:read"]},
    }
    assert set(publishing) == replaced

    for field, auth in publishing.items():
        tool = Tool.model_validate(
            {
                "id": "t",
                "name": "t",
                "source_type": "mcp",
                "source_id": "s",
                "extraction_confidence": "high",
                "extraction": {"surface": "enumerated"},
                "auth": auth,
            }
        )
        assessment = assess_tool_semantics(tool, None, environment_target="template")
        assert not any(
            claim.source == "environment_template_authority"
            for claim in assessment.authority.claims
        ), f"a source publishing {field} was overwritten by the template default"


def test_a_template_never_narrows_the_credential_mode_selector() -> None:
    """The consequence the erasure actually had, asserted where it lands.

    `credential_modes` policy selectors read
    `CapabilityFactV1.authority.credential_mode`, which is copied from the
    *resolved* semantic authority. Clearing it does not weaken a rule's
    finding — it takes the capability out of the rule's reach, and the rule
    goes on reporting nothing because it now sees nothing.

    Asserted at the capability fact rather than through a scan on purpose. No
    first-party adapter emits a bare `credential_mode` today (MCP and n8n both
    set `explicit` alongside it, which `_source_authority` already grades as
    published), so a scan-level comparison would pass for the wrong reason and
    keep passing if the predicate regressed. `AuthInfo` accepts the shape and a
    third-party adapter may produce it, which is what makes the hole real.
    """

    from agents_shipgate.core.capabilities import capability_fact_from_action
    from agents_shipgate.core.domain import Tool
    from agents_shipgate.core.lenses.action_surface import build_action

    def credential_mode_seen_by_policy(target: str) -> str | None:
        tool = Tool.model_validate(
            {
                "id": "t",
                "name": "svc.run",
                "source_type": "mcp",
                "source_id": "src",
                "extraction_confidence": "high",
                "extraction": {"surface": "enumerated"},
                "auth": {"credential_mode": "service_account"},
            }
        )
        manifest = _manifest(environment={"target": target})
        tool.semantic_assessment = assess_tool_semantics(
            tool, None, environment_target=target
        )
        action = build_action(manifest, agent_id="agent", tool=tool, declaration=None)
        return capability_fact_from_action(action, tool).authority.credential_mode

    assert credential_mode_seen_by_policy("local") == "service_account"
    assert credential_mode_seen_by_policy("template") == "service_account"


def test_the_template_claim_only_ever_stands_where_it_is_the_answer() -> None:
    """Why counting it needs no case analysis, pinned rather than assumed.

    The concern counter guards on `status == "declared"` so it can never
    describe a contested authority as one taken from the template. That guard
    is a no-op today *because* the claim is only built where nothing else
    published anything — and this sweep is what makes that an invariant rather
    than a coincidence. If a future change let the claim apply over ambiguous
    or invalid source evidence, this fails and the guard starts earning its
    keep instead of quietly becoming a lie.
    """

    from agents_shipgate.core.domain import Tool

    standing = 0
    for auth in _AUTH_SHAPES:
        for shape in _DECLARATION_SHAPES:
            tool = Tool.model_validate(
                {
                    "id": "t",
                    "name": "t",
                    "source_type": "mcp",
                    "source_id": "s",
                    "extraction_confidence": "high",
                    "extraction": {"surface": "enumerated"},
                    "auth": auth,
                }
            )
            declaration = (
                ActionDeclarationConfig.model_validate({"tool": "t", **shape})
                if shape
                else None
            )
            assessment = assess_tool_semantics(
                tool, declaration, environment_target="template"
            )
            if not any(
                claim.source == "environment_template_authority"
                for claim in assessment.authority.claims
            ):
                continue
            standing += 1
            assert assessment.authority.status == "declared", (
                f"auth={auth} declaration={shape}: the template claim stands "
                "beside a status it did not decide"
            )
            assert assessment.authority.mode == "none"
    assert standing, "no shape reached the template claim; the sweep proves nothing"


def test_the_reason_names_the_stronger_concern_first(tmp_path: Path) -> None:
    """Both concerns share the review tier, and the sentence has to lead with
    the sharper one: an action running on a known unscoped credential is a more
    specific thing to look at than a repository that is not deployed at all.
    """

    from agents_shipgate.ci.release_decision import _decision_reason
    from agents_shipgate.schemas.report import (
        EvidenceCoverageDecision,
        SemanticCoverageDecision,
    )

    evidence = EvidenceCoverageDecision(
        level="static",
        human_review_recommended=False,
        source_warning_count=0,
        low_confidence_tool_count=0,
        semantic_coverage=SemanticCoverageDecision(
            review_concern_count=2,
            reason_counts={"unscoped_authority": 1, "template_environment_authority": 1},
        ),
    )
    reason = _decision_reason("review_required", [], [], evidence)

    assert reason.index("unscoped or ambient") < reason.index("environment.target")


def test_the_template_default_is_a_reviewed_claim_with_its_own_source() -> None:
    """An audit reading the claims has to be able to say where the answer came
    from: this one came from a statement about the repository, not about the
    action or its source.
    """

    from agents_shipgate.core.domain import Tool

    tool = Tool.model_validate(
        {
            "id": "mcp:src:docs.lookup",
            "name": "docs.lookup",
            "source_type": "mcp",
            "source_id": "src",
            "extraction_confidence": "high",
            "extraction": {"surface": "enumerated"},
            "annotations": {"readOnlyHint": True},
        }
    )
    assessment = assess_tool_semantics(tool, None, environment_target="template")

    sources = {claim.source for claim in assessment.authority.claims}
    assert "environment_template_authority" in sources
    assert assessment.authority.mode == "none"


def _mk(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


# --------------------------------------------------------------------------
# SHIP-TRUST-MANIFEST-UNPROTECTED
# --------------------------------------------------------------------------


def _checkout(root: Path) -> Path:
    """Mark ``root`` as a git checkout.

    CODEOWNERS is a forge convention: outside a repository there is no pull
    request for a rule to gate, so `manifest_protection` reports no coverage
    however many files of that name are lying around. Every test about *rules*
    therefore has to be in a checkout, or it is testing the fail-closed path by
    accident — which is how the first version of these fixtures passed while
    saying nothing about the rules they wrote.
    """

    (root / ".git").mkdir(exist_ok=True)
    return root


def _findings(tmp_path: Path, config: Path) -> list:
    report, _ = run_scan(
        config_path=config,
        output_dir=tmp_path / "out",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    return report.findings


def _unprotected(findings: list) -> list:
    return [f for f in findings if f.check_id == "SHIP-TRUST-MANIFEST-UNPROTECTED"]


def test_manifest_protection_is_silent_while_the_gate_is_advisory(tmp_path: Path) -> None:
    """Who may change the gate matters once the gate is enforcing something.
    Repeating it on every advisory scan is noise at the one moment there is
    nothing to act on — `doctor` already names it as the step to rung 3.
    """

    root = _checkout(_mk(tmp_path / "advisory"))
    config = _workspace(root, tools=[_READ_ONLY_TOOL], target="local")

    assert not _unprotected(_findings(root, config))


def test_an_enforced_gate_with_no_owner_is_named(tmp_path: Path) -> None:
    root = _checkout(_mk(tmp_path / "strict"))
    config = _workspace(
        root, tools=[_READ_ONLY_TOOL], target="local", ci={"mode": "strict"}
    )

    found = _unprotected(_findings(root, config))

    assert len(found) == 1
    assert found[0].severity == "low"
    # Guidance, never a review item: branch protection is the other half and no
    # file in a checkout can read it, so this must not decide a verdict.
    assert found[0].requires_human_review is False
    assert found[0].evidence["branch_protection"] == "not statically verifiable"


def test_a_covering_codeowners_rule_closes_it(tmp_path: Path) -> None:
    root = _checkout(_mk(tmp_path / "owned"))
    config = _workspace(
        root, tools=[_READ_ONLY_TOOL], target="local", ci={"mode": "strict"}
    )
    (root / ".github").mkdir()
    (root / ".github" / "CODEOWNERS").write_text("* @platform-team\n", encoding="utf-8")

    assert not _unprotected(_findings(root, config))


def test_the_last_matching_rule_decides(tmp_path: Path) -> None:
    """CODEOWNERS is last-wins, and an ownerless rule removes ownership. A
    first-match reading would report a manifest as protected by a broad rule
    that a later, narrower one had already exempted.
    """

    root = _checkout(_mk(tmp_path / "exempted"))
    config = _workspace(
        root, tools=[_READ_ONLY_TOOL], target="local", ci={"mode": "strict"}
    )
    (root / ".github").mkdir()
    (root / ".github" / "CODEOWNERS").write_text(
        "* @platform-team\n/shipgate.yaml\n", encoding="utf-8"
    )

    assert len(_unprotected(_findings(root, config))) == 1


def test_a_checkout_with_no_codeowners_is_uncovered(tmp_path: Path) -> None:
    root = _checkout(_mk(tmp_path))
    config = _workspace(root, tools=[_READ_ONLY_TOOL], target="local")
    protection = manifest_protection(config)

    assert protection == ManifestProtection(
        manifest_path="shipgate.yaml",
        codeowners_path=None,
        covered=False,
        matching_pattern=None,
        owners=(),
    )


def test_a_stray_codeowners_outside_a_checkout_proves_nothing(tmp_path: Path) -> None:
    """Fail closed on the question, not just on the file.

    CODEOWNERS governs pull requests in a repository. A directory that is not a
    checkout has neither, so a file of that name is a filename — and reading it
    as protection suppressed the guidance finding and, through the ladder,
    awarded rung 3.
    """

    root = _mk(tmp_path)
    config = _workspace(root, tools=[_READ_ONLY_TOOL], target="local")
    (root / ".github").mkdir()
    (root / ".github" / "CODEOWNERS").write_text("* @platform-team\n", encoding="utf-8")

    protection = manifest_protection(config)

    assert not protection.covered
    assert protection.codeowners_path is None


def test_a_rule_that_names_nobody_owns_nothing(tmp_path: Path) -> None:
    """GitHub skips tokens that are not owners, and last-wins makes that
    decisive: a narrower rule with a typo *removes* the ownership a broad rule
    granted, so counting the typo reports the manifest protected by the very
    line that unprotected it.
    """

    root = _checkout(_mk(tmp_path))
    config = _workspace(root, tools=[_READ_ONLY_TOOL], target="local")
    (root / ".github").mkdir()
    (root / ".github" / "CODEOWNERS").write_text(
        "* @platform-team\n/shipgate.yaml not-an-owner\n", encoding="utf-8"
    )

    protection = manifest_protection(config)

    assert not protection.covered
    assert protection.matching_pattern == "/shipgate.yaml"
    assert protection.owners == ()


@pytest.mark.parametrize(
    ("pattern", "path", "covered"),
    [
        ("*", "shipgate.yaml", True),
        ("shipgate.yaml", "apps/api/shipgate.yaml", True),
        ("/shipgate.yaml", "apps/api/shipgate.yaml", False),
        ("*.yaml", "apps/shipgate.yaml", True),
        ("/apps/", "apps/api/shipgate.yaml", True),
        ("/apps/", "other/shipgate.yaml", False),
        ("apps/**/shipgate.yaml", "apps/shipgate.yaml", True),
        ("apps/**/shipgate.yaml", "apps/a/b/shipgate.yaml", True),
        ("apps/*/shipgate.yaml", "apps/a/b/shipgate.yaml", False),
        ("**/shipgate.yaml", "shipgate.yaml", True),
        ("apps/**", "other/shipgate.yaml", False),
        ("shipgate.yml", "shipgate.yaml", False),
        ("docs/", "shipgate.yaml", False),
        # A trailing slash restricts to a directory's *contents*. A manifest
        # path is always a file, so this pair is about the matcher rather than
        # about any caller — and without it the trailing slash could be dropped
        # entirely and nothing would notice.
        ("docs/", "docs/shipgate.yaml", True),
        ("docs/", "docs", False),
    ],
)
def test_the_codeowners_pattern_subset(pattern: str, path: str, covered: bool) -> None:
    assert _matches(pattern, path) is covered


# --------------------------------------------------------------------------
# The ladder
# --------------------------------------------------------------------------


def _manifest(**overrides) -> AgentsShipgateManifest:
    payload: dict = {
        "version": "0.1",
        "project": {"name": "ladder"},
        "agent": {"name": "asst", "declared_purpose": ["exercise the ladder"]},
        "environment": {"target": "local"},
        "tool_sources": [{"id": "src", "type": "mcp", "path": "tools.json"}],
    }
    payload.update(overrides)
    return AgentsShipgateManifest.model_validate(payload)


def _protection(covered: bool) -> ManifestProtection:
    return ManifestProtection(
        manifest_path="shipgate.yaml",
        codeowners_path=".github/CODEOWNERS" if covered else None,
        covered=covered,
        matching_pattern="*" if covered else None,
        owners=("@team",) if covered else (),
    )


_ANSWERED = {"action_surface": {"actions": [{"tool": "docs.lookup", "effect": "read"}]}}
#: A perfectly valid action row that answers **neither** declaration
#: dimension — the questionnaire counts effect and authority only.
_CONTROLS_ONLY = {
    "action_surface": {
        "actions": [{"tool": "docs.lookup", "approval": {"required": True}}]
    }
}
_STRICT = {"ci": {"mode": "strict"}}


@pytest.mark.parametrize(
    ("overrides", "covered", "rung"),
    [
        ({}, False, 1),
        ({}, True, 1),
        # A control declaration is not a semantic answer.
        (_CONTROLS_ONLY, False, 1),
        (_ANSWERED, False, 2),
        ({**_ANSWERED, **_STRICT}, False, 2),
        (_ANSWERED | {"environment": {"target": "template"}}, True, 2),
        ({**_ANSWERED, **_STRICT}, True, 3),
        ({**_STRICT, "environment": {"target": "template"}}, True, 3),
        # Rung 3 states a posture, and a repository whose surface resolves
        # structurally can hold that posture without ever having been asked a
        # declaration question. A cumulative ladder would strand it at 1.
        (_STRICT, True, 3),
    ],
)
def test_the_rung_is_the_highest_one_this_repository_already_meets(
    overrides: dict, covered: bool, rung: int
) -> None:
    assert adoption_rung(_manifest(**overrides), _protection(covered)).number == rung


def test_no_rung_claims_a_verdict_or_an_enforcement(tmp_path: Path) -> None:
    """The line every over-claim in review crossed, stated once.

    A rung reads the manifest and the checkout. It cannot know what a scan will
    decide — a fully structural surface owes no declaration questions and can
    reach `review_required` or `passed` from rung 1 — and it cannot know what
    CI or a branch will enforce: the generated workflow passes
    `ci_mode: advisory`, which overrides the manifest, and branch protection is
    a repository setting no file here can read.
    """

    verdicts = {"insufficient_evidence", "blocked", "passed"}
    for overrides, covered in (({}, False), (_ANSWERED, False), ({**_ANSWERED, **_STRICT}, True)):
        rung = adoption_rung(_manifest(**overrides), _protection(covered))
        text = f"{rung.you_get} {rung.exit_criterion}"
        assert not (verdicts & set(text.split())), f"rung {rung.number} names a verdict: {text}"

    top = adoption_rung(_manifest(**_ANSWERED, **_STRICT), _protection(True))
    # It may say what a run in this repository's declared mode does; it may not
    # say that CI runs in that mode, so the limit has to be stated.
    assert "cannot see" in top.exit_criterion
    assert "ci_mode: strict" in top.exit_criterion


def test_a_rung_names_only_the_conditions_that_are_actually_unmet() -> None:
    """Telling an adopter who already set `ci.mode: strict` to set it again is
    how a next step stops being read at all.
    """

    already_strict = adoption_rung(
        _manifest(**_ANSWERED, **_STRICT), _protection(False)
    )
    neither = adoption_rung(_manifest(**_ANSWERED), _protection(False))

    assert already_strict.blocking == ("manifest_unprotected",)
    assert "ci.mode: strict" not in already_strict.exit_criterion
    assert "CODEOWNERS" in already_strict.exit_criterion
    assert neither.blocking == ("ci_mode_not_strict", "manifest_unprotected")
    assert "ci.mode: strict" in neither.exit_criterion


def test_the_top_rung_names_what_it_cannot_establish() -> None:
    top = adoption_rung(_manifest(**_ANSWERED, **_STRICT), _protection(True))

    assert top.blocking == ()
    assert top.summary().startswith("rung 3 · Strict")


def test_the_audit_rung_carries_a_next_step_that_would_work() -> None:
    """Rung 0's exit criterion is a command an adopter runs.

    Bare `agents-shipgate init` is a dry run that writes nothing and defaults to
    the process's cwd — a next step that cannot advance the rung it is printed
    on. The caller renders the invocation, so this asserts the rung uses it
    verbatim rather than composing one of its own.
    """

    rung = audit_rung("shipgate init --workspace /repo --write")

    assert rung.number == 0
    assert "shipgate detect" in rung.you_get
    assert "audit --host" in rung.you_get
    assert "shipgate init --workspace /repo --write" in rung.exit_criterion


def test_the_audit_rungs_command_routes_to_the_right_workspace(tmp_path: Path) -> None:
    """Rung 0's next step has to point at the directory `doctor` was aimed at.

    `Path(config).parent` gets the glob form wrong — `-c '/repo/*/shipgate.yaml'`
    resolves to a literal `*` directory — so the command is routed through the
    same helper the missing-manifest diagnostic already uses.
    """

    root = _mk(tmp_path / "repo")
    (root / "nested").mkdir()
    from typer.testing import CliRunner

    from agents_shipgate.cli.main import app

    result = CliRunner().invoke(app, ["doctor", "--config", str(root / "*" / "shipgate.yaml")])

    assert "Adoption: rung 0" in result.output
    assert f"--workspace {root}" in result.output
    assert "--write" in result.output
    assert "*" not in result.output.split("--workspace ", 1)[1].split()[0]


def test_adoption_reads_the_manifest_the_user_wrote(tmp_path: Path) -> None:
    """`doctor` drops unresolved sources before loading, and that copy is not
    the adoption answer.

    A `tool_sources` entry whose path is broken still carries the reviewed
    `authority` a human wrote. Reading the filtered manifest reported such a
    repository as rung 1 — "nothing answered yet" — while the file on disk
    plainly held a rung-2 answer, so `doctor` told the adopter to go and do
    work they had already done.
    """

    import json

    from typer.testing import CliRunner

    from agents_shipgate.cli.main import app

    root = _mk(tmp_path)
    (root / "tools.json").write_text(json.dumps({"tools": [_READ_ONLY_TOOL]}), encoding="utf-8")
    manifest = {
        "version": "0.1",
        "project": {"name": "ladder"},
        "agent": {"name": "asst", "declared_purpose": ["exercise the ladder"]},
        "environment": {"target": "local"},
        "tool_sources": [
            {"id": "present", "type": "mcp", "path": "tools.json"},
            {
                "id": "gone",
                "type": "mcp",
                "path": "missing.json",
                "authority": {"mode": "none"},
            },
        ],
    }
    config = root / "shipgate.yaml"
    config.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

    result = CliRunner().invoke(app, ["doctor", "--config", str(config), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)[0]
    assert payload["unresolved_sources"], "the fixture no longer exercises filtering"
    assert payload["adoption"]["rung"] == 2


def test_doctor_publishes_the_rung(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from agents_shipgate.cli.main import app

    root = _mk(tmp_path)
    config = _workspace(root, tools=[_READ_ONLY_TOOL], target="local")
    result = CliRunner().invoke(app, ["doctor", "--config", str(config), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)[0]
    assert payload["adoption"]["rung"] == 1
    assert payload["adoption"]["exit_criterion"]


# --------------------------------------------------------------------------
# One permission list, across the third site it can be written at
# --------------------------------------------------------------------------


_SCOPE_SHAPES: tuple[tuple[str, dict | None, dict | None], ...] = (
    ("nothing declared", None, None),
    ("bare action scopes", {"effect": "write", "scopes": ["crm:write"]}, None),
    (
        "action authority",
        {
            "effect": "write",
            "scopes": ["crm:write"],
            "authority": {"mode": "scoped", "auth_type": "oauth2"},
        },
        None,
    ),
    (
        "source authority",
        None,
        {"authority": {"mode": "scoped", "auth_type": "oauth2", "scopes": ["crm:read"]}},
    ),
    ("declared none", {"effect": "read", "authority": {"mode": "none"}}, None),
)


@pytest.mark.parametrize(("label", "action", "source"), _SCOPE_SHAPES)
@pytest.mark.parametrize("target", ["local", "template"])
def test_one_permission_list_survives_the_template_site(
    tmp_path: Path, label: str, action: dict | None, source: dict | None, target: str
) -> None:
    """``environment.target: template`` is a third manifest site a reviewed
    authority can come from, and every surface that publishes an action's
    permissions has to resolve it the same way.

    ``CapabilityFactV1`` *requires* the action fact's ``required_scopes`` and
    the authority dimension's ``scopes`` to be one list, and it only checks on
    the base-comparison path — so a divergence introduced here would surface as
    ``internal_error`` on somebody's ``verify --base``, not in this suite. This
    is the assertion that stops that.
    """

    tool = dict(_READ_ONLY_TOOL, name="crm.write")
    tool.pop("annotations")
    root = _mk(tmp_path / f"{label}-{target}".replace(" ", "-"))
    config = _workspace(
        root,
        tools=[tool],
        target=target,
        actions=[{"tool": "crm.write", **action}] if action else None,
        sources=[source] if source else None,
    )
    report, _ = run_scan(
        config_path=config,
        output_dir=root / "out",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )

    facts = report.action_surface_facts.actions
    assert facts, "fixture produced no action facts"
    for fact in facts:
        assert fact.semantic_assessment is not None
        assert fact.required_scopes == sorted(set(fact.semantic_assessment.authority.scopes)), (
            f"{label} @ {target}: the action fact and its authority dimension "
            "publish different permission lists"
        )
