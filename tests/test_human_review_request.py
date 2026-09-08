"""An eligible review question is checkable, reproducible and non-authorizing."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from agents_shipgate.core.human_review_request import project_human_review_request
from agents_shipgate.schemas.human_review_request import (
    HUMAN_REVIEW_REQUEST_FILENAME,
    HUMAN_REVIEW_REQUEST_SCHEMA_PATH,
    HumanReviewPostconditionV1,
    HumanReviewRequestV1,
)
from agents_shipgate.schemas.verification_identity import VerificationPlan
from tests.test_authorization_verify_integration import _committed_review_repo, _git, _verify


@pytest.fixture(scope="module")
def review_case(tmp_path_factory):
    repo = _committed_review_repo(tmp_path_factory.mktemp("review-request"))
    verifier, report, exit_code = _verify(repo)
    assert exit_code == 0 and report is not None
    plan = VerificationPlan.model_validate_json(
        (repo / "agents-shipgate-reports/verification-plan.json").read_text()
    )
    return repo, verifier, report, plan


def _request(case):
    _, verifier, report, plan = case
    return project_human_review_request(verifier=verifier, report=report, plan=plan)


def test_real_verifier_publishes_the_question_with_current_scope_and_no_authority(review_case):
    repo, verifier, report, plan = review_case
    request = _request(review_case)
    assert request is not None
    published = repo / "agents-shipgate-reports" / HUMAN_REVIEW_REQUEST_FILENAME
    assert json.loads(published.read_text()) == request.model_dump(mode="json")
    assert request.verification_request_id == plan.request_id
    assert request.input_set_id == plan.inputs.input_set_id
    assert request.decision_id == verifier.decision_id
    assert request.source_head_commit_sha == plan.subject.git.source_head_commit_sha
    assert request.review_items[0].paths == ["tools.json"]
    assert "docs.lookup" in request.questions[0].question
    assert request.postcondition.accepted == "record_acceptance_preserve_gate"
    assert request.postcondition.rejected == "record_rejection_preserve_gate"
    assert request.postcondition.disputed == "record_dispute_preserve_gate"
    assert not request.postcondition.grants_merge_authority
    assert not request.postcondition.grants_completion_authority
    assert verifier.control.state == "review_publishable"
    assert not verifier.control.permissions.merge
    assert not verifier.control.permissions.report_complete
    assert report.release_decision.decision == "review_required"
    # The terminal artifact closure binds the exact request bytes too.
    manifest = json.loads((published.parent / "verification-artifacts.json").read_text())
    assert HUMAN_REVIEW_REQUEST_FILENAME in json.dumps(manifest)


@pytest.mark.parametrize("mutation", [
    "blocked", "critical", "gate_governing", "coverage_gap", "partial_binding",
    "worktree", "missing_base", "plugins", "accepted_debt", "no_report",
])
def test_excluded_request_classes_keep_the_existing_route(review_case, mutation):
    repo, verifier, report, plan = copy.deepcopy(review_case)
    original_control = verifier.control.model_dump(mode="json")
    if mutation == "blocked":
        verifier.decision = "blocked"
    elif mutation == "critical":
        report.release_decision.review_items[0].severity = "critical"
    elif mutation == "gate_governing":
        report.release_decision.review_items[0].check_id = "SHIP-VERIFY-POLICY-WEAKENED"
    elif mutation == "coverage_gap":
        report.release_decision.evidence_coverage.source_warning_count = 1
    elif mutation == "partial_binding":
        report.release_decision.evidence_coverage.binding_coverage.pass_eligible = False
    elif mutation == "worktree":
        plan.subject.git.snapshot_kind = "worktree_overlay"
    elif mutation == "missing_base":
        plan.subject.git.base_tree_sha = None
    elif mutation == "plugins":
        plan.inputs.options["plugins_enabled"] = True
    elif mutation == "accepted_debt":
        report.release_decision.review_items[0].baseline_status = "existing"
    else:
        report = None
    assert _request((repo, verifier, report, plan)) is None
    assert verifier.control.model_dump(mode="json") == original_control


def test_request_projection_is_deterministic_and_does_not_mutate_static_inputs(review_case):
    _, verifier, report, plan = review_case
    before = [model.model_dump_json() for model in (verifier, report, plan)]
    first = _request(review_case)
    second = _request(review_case)
    assert first.model_dump_json() == second.model_dump_json()
    assert [model.model_dump_json() for model in (verifier, report, plan)] == before


@pytest.mark.parametrize("change", ["authority", "prose_acceptance", "question_scope", "class"])
def test_schema_and_model_reject_forged_completion_and_wrong_grammar(review_case, change):
    request = _request(review_case)
    payload = request.model_dump(mode="json")
    schema = json.loads(Path(HUMAN_REVIEW_REQUEST_SCHEMA_PATH).read_text())
    validator = Draft202012Validator(schema)
    assert not list(validator.iter_errors(payload))
    if change == "authority":
        payload["postcondition"]["grants_merge_authority"] = True
    elif change == "prose_acceptance":
        payload["accepted"] = "Looks good to me"
    elif change == "question_scope":
        payload["questions"] = []
    else:
        payload["request_class"] = "arbitrary_gate_override"
    assert list(validator.iter_errors(payload))
    with pytest.raises(ValidationError):
        HumanReviewRequestV1.model_validate(payload)


def test_model_checks_content_identity_in_addition_to_json_schema(review_case):
    payload = _request(review_case).model_dump(mode="json")
    payload["questions"][0]["question"] = "A different question with the old identity"
    with pytest.raises(ValidationError, match="hash the complete request"):
        HumanReviewRequestV1.model_validate(payload)


@pytest.mark.parametrize("section,field,value", [
    (None, "low_confidence_tool_count", 1),
    (None, "policy_gap_count", 1),
    ("semantic_coverage", "gap_count", 1),
    ("semantic_coverage", "review_concern_count", 1),
    ("semantic_coverage", "pass_eligible_actions", 0),
    ("identity_coverage", "gap_count", 1),
    ("identity_coverage", "pass_eligible_tools", 0),
    ("identity_coverage", "ambiguous_name_count", 1),
    ("binding_coverage", "gap_count", 1),
])
def test_each_independent_evidence_gap_excludes_the_question(review_case, section, field, value):
    case = copy.deepcopy(review_case)
    coverage = case[2].release_decision.evidence_coverage
    target = getattr(coverage, section) if section else coverage
    setattr(target, field, value)
    assert _request(case) is None


def test_later_passed_run_removes_the_old_question_and_its_artifact_binding(tmp_path):
    repo = _committed_review_repo(tmp_path)
    first, _, _ = _verify(repo)
    request_path = repo / "agents-shipgate-reports" / HUMAN_REVIEW_REQUEST_FILENAME
    assert request_path.is_file()
    assert "human_review_request_json" in first.artifacts
    tools_path = repo / "tools.json"
    payload = json.loads(tools_path.read_text())
    payload["tools"][0]["description"] = "Read the internal documentation catalog."
    tools_path.write_text(json.dumps(payload))
    _git(repo, "add", "tools.json")
    _git(repo, "commit", "-qm", "resolve the documentation concern")
    current, report, _ = _verify(repo)
    assert report.release_decision.decision == "passed"
    assert current.control.state == "complete"
    assert not request_path.exists()
    assert "human_review_request_json" not in current.artifacts
    manifest = (request_path.parent / "verification-artifacts.json").read_text()
    assert HUMAN_REVIEW_REQUEST_FILENAME not in manifest


def test_runtime_and_discovery_name_the_same_request_contract():
    from agents_shipgate.schemas.contract import build_contract_payload

    runtime = build_contract_payload().model_dump(mode="json")
    discovery = json.loads(Path(".well-known/agents-shipgate.json").read_text())
    for field in ("human_review_request_schema_version", "human_review_request_schema_path",
                  "human_review_request_artifact"):
        assert runtime[field] == discovery[field]
    assert runtime["artifacts"]["human_review_request"] == discovery["artifacts"]["human_review_request"]
    assert "human_review_request" in runtime["external_integration_surfaces"]
    assert "human_review_request" in discovery["external_integration_surfaces"]


# The schema identifiers below are already published. #536 must not silently
# widen any of them when adding a postcondition to a new, separate artifact.
# Successor grammars get successor filenames; historical bytes stay readable.
# #561 adds only explanatory metadata to the existing open EvidenceGap. Its
# two additive nodes are checked separately below, then removed to compare
# the entire preceding grammar against its ORIGINAL digest. No control field,
# constraint, or other schema content is exempted from the freeze.
FROZEN_CONTROL_SCHEMAS = {
    "verifier-schema.v0.16.json": "cfa834d9bf047d3e39ffed531f19fbd7ed2cd6e82353789dddb7a458dfae408a",
    "agent-handoff-schema.v8.json": "036dc757914a297b34ebb9b7ca10c21869c5c91820fa7f07bda415c1effe30c3",
    "preflight-schema.v0.4.json": "048c4785253afa476e7b86f6175d61328ea299641f6253c3d4315c9d61d67583",
    "agent-result-schema.v3.json": "ad762ecbbcde20b6cbc117337b4e0ad208708ab062ccc851ffe26b4ff63df232",
    "agent-boundary-result-schema.v2.json": "179f849080fabdc59cdf4b86b5ec6a0d9c605e1ac31eb130ee2463c6a0ab361d",
    "verify-run-schema.v5.json": "19deb3ba50d3f610325b6e7457ad000ccbcbb737e03cb8bb0e8011c69343f141",
    "agent-control-schema.v1.json": "a1515f5770bedecd7c22d8d35d82fa2b335feb18e1c8293099dec71ff6e9a0b1",
}


@pytest.mark.parametrize("filename,digest", FROZEN_CONTROL_SCHEMAS.items())
def test_review_postcondition_does_not_widen_a_published_control_grammar(filename, digest):
    raw = (Path("docs") / filename).read_bytes()
    schema = json.loads(raw)
    if filename == "verifier-schema.v0.16.json":
        original = copy.deepcopy(schema)
        gap = original["$defs"]["EvidenceGap"]
        assert gap.get("additionalProperties", True) is True
        assert "recovery" not in gap.get("required", [])
        assert gap["properties"].pop("recovery") == {
            "anyOf": [{"$ref": "#/$defs/CoverageRecovery"}, {"type": "null"}],
            "default": None,
        }
        from agents_shipgate.schemas.coverage_recovery import CoverageRecovery

        assert original["$defs"].pop("CoverageRecovery") == CoverageRecovery.model_json_schema()
        raw = (json.dumps(original, indent=2, sort_keys=True) + "\n").encode()
    assert hashlib.sha256(raw).hexdigest() == digest
    action = {"actor": "human", "kind": "review", "command": None,
              "expects": None, "why": "Review the exact scope"}
    action_schema = {"$ref": "#/$defs/HumanControlAction", "$defs": schema["$defs"]}
    validator = Draft202012Validator(action_schema)
    assert not list(validator.iter_errors(action))
    action["expects"] = "A populated postcondition is not part of this grammar"
    assert list(validator.iter_errors(action))


@pytest.mark.parametrize("field", ["request_id", "subject_id", "input_set_id", "engine_requirement_id"])
def test_projection_refuses_mixed_run_identities(review_case, field):
    case = copy.deepcopy(review_case)
    setattr(case[1], field, "sha256:" + "f" * 64)
    assert _request(case) is None


@pytest.mark.parametrize("field", [
    "authenticated_human_required", "trusted_eligibility_required",
    "author_and_bot_excluded", "current_request_required",
    "complete_review_set_required", "review_instance_binding_required",
    "unexpired_decision_required", "grants_merge_authority", "grants_completion_authority",
])
@pytest.mark.parametrize("numeric_kind", [int, float])
def test_postcondition_boolean_grammar_does_not_accept_numeric_flags(field, numeric_kind):
    payload = HumanReviewPostconditionV1().model_dump(mode="json")
    payload[field] = numeric_kind(payload[field])
    assert list(Draft202012Validator(HumanReviewPostconditionV1.model_json_schema()).iter_errors(payload))
    with pytest.raises(ValidationError, match="JSON booleans"):
        HumanReviewPostconditionV1.model_validate(payload)


def test_failed_rerun_clears_a_previous_review_request(tmp_path):
    repo = _committed_review_repo(tmp_path)
    _verify(repo)
    request_path = repo / "agents-shipgate-reports" / HUMAN_REVIEW_REQUEST_FILENAME
    assert request_path.exists()
    _git(repo, "rm", "shipgate.yaml")
    _git(repo, "commit", "-qm", "remove the required manifest")
    verifier, _, exit_code = _verify(repo)
    assert exit_code != 0
    assert not request_path.exists()
    assert "human_review_request_json" not in verifier.artifacts
