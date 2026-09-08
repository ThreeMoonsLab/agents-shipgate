"""Bound external decisions over a real verifier run; no human trust is faked."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jsonschema import Draft202012Validator
from pydantic import TypeAdapter, ValidationError

from agents_shipgate.core import human_review_decision as review
from agents_shipgate.schemas.human_authorization import (
    HUMAN_AUTHORIZATION_SIGNATURE_DOMAIN,
    HumanAuthorizationPrincipalV1,
    HumanAuthorizationProofV1,
    HumanAuthorizationReviewItemV1,
    TrustedEd25519KeyV1,
    build_human_authorization_trust_policy,
    ed25519_key_id,
    review_set_id,
)
from agents_shipgate.schemas.human_review_decision import (
    HumanReviewDecisionStatementV1,
    HumanReviewDecisionV1,
    HumanReviewEvaluationV1,
    build_human_review_decision,
)
from agents_shipgate.schemas.human_review_request import (
    HumanReviewPostconditionV1,
    HumanReviewQuestionV1,
    HumanReviewRequestV1,
    build_human_review_request,
)
from agents_shipgate.schemas.verification_identity import canonical_json
from tests.test_authorization_verify_integration import (
    _b64url,
    _committed_review_repo,
    _git,
    _verify,
)

PRINCIPAL = ("local-hardware", "fixture-reviewer")
AUTHOR = ("local-hardware", "fixture-author")


def _case(tmp_path, *, two_items=False):
    repo = _committed_review_repo(tmp_path)
    if two_items:
        tools_path = repo / "tools.json"
        tools = json.loads(tools_path.read_text())
        tools["tools"][0]["description"] = "Read internal documentation."
        second = copy.deepcopy(tools["tools"][0])
        second["name"] = "docs.search"
        tools["tools"].append(second)
        tools_path.write_text(json.dumps(tools))
        manifest_path = repo / "shipgate.yaml"
        manifest = yaml.safe_load(manifest_path.read_text())
        action = copy.deepcopy(manifest["action_surface"]["actions"][0])
        action["tool"] = "docs.search"
        manifest["action_surface"]["actions"].append(action)
        manifest["agent_bindings"]["declarations"][0]["tools"].append(
            {"tool": "docs.search", "source_id": "docs_tools"}
        )
        manifest_path.write_text(yaml.safe_dump(manifest))
        _git(repo, "add", "shipgate.yaml", "tools.json")
        _git(repo, "commit", "-qm", "reviewed two-tool fixture base")
        for tool in tools["tools"]:
            tool.pop("description")
        tools_path.write_text(json.dumps(tools))
        _git(repo, "add", "tools.json")
        _git(repo, "commit", "-qm", "introduce two documentation concerns")
    verifier, report, code = _verify(repo)
    assert code == 0 and report.release_decision.decision == "review_required"
    out = repo / "agents-shipgate-reports"
    request = HumanReviewRequestV1.model_validate_json((out / "human-review-request.json").read_text())
    now = datetime.now(UTC).replace(microsecond=0)
    private_key = Ed25519PrivateKey.generate()
    public = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    key = TrustedEd25519KeyV1(
        key_id=ed25519_key_id(public), public_key=_b64url(public),
        provider=PRINCIPAL[0], principal=PRINCIPAL[1],
        valid_from=now - timedelta(hours=1), valid_until=now + timedelta(hours=2),
    )
    context = review.TrustedReviewEligibility(
        repository_id=request.repository_id,
        review_instance_id="fixture:review/1",
        eligible_principals=frozenset({PRINCIPAL}), author_principals=frozenset({AUTHOR}),
        human_principals=frozenset({PRINCIPAL, AUTHOR}), bot_principals=frozenset(), keys=(key,),
    )
    policy = build_human_authorization_trust_policy(
        repository_ids=[request.repository_id], keys=[key], max_ttl_seconds=900,
    )
    trust_path = tmp_path / "host" / "trust-policy.json"
    trust_path.parent.mkdir()
    trust_path.write_text(policy.model_dump_json())
    trust_path.chmod(0o600)
    return repo, out, request, now, private_key, context, verifier, trust_path


@pytest.fixture(scope="module")
def review_case(tmp_path_factory):
    return _case(tmp_path_factory.mktemp("review-decision"))


def _signed(case, *, outcome="accepted", request=None, context=None, issued_at=None, expires_at=None,
            principal=None, domain=None):
    _, _, original, now, private_key, trusted, _, _ = case
    context = context or trusted
    statement = HumanReviewDecisionStatementV1(
        request=request or original, review_instance_id=context.review_instance_id,
        eligibility_context_id=context.context_id,
        principal=principal or HumanAuthorizationPrincipalV1(provider=PRINCIPAL[0], subject=PRINCIPAL[1]),
        outcome=outcome, reason="Fixture reviewer decision; not a production approval.",
        issued_at=issued_at or now - timedelta(seconds=1), expires_at=expires_at or now + timedelta(minutes=5),
    )
    raw = (domain + canonical_json(statement)) if domain is not None else review.human_review_decision_signature_payload(statement)
    proof = HumanAuthorizationProofV1(key_id=context.keys[0].key_id, signature=_b64url(private_key.sign(raw)))
    return build_human_review_decision(statement=statement, proof=proof)


def _evaluate(case, decision, context=None, **kwargs):
    repo, out, _, now, _, original_context, _, trust_path = case
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(review, "default_human_authorization_trust_policy_path", lambda: trust_path)
        return review.evaluate_human_review_decision(
            decision, workspace=repo, reports_dir=out,
            trusted_eligibility=context if context is not None else original_context,
            now=kwargs.pop("now", now), **kwargs,
        )


def _bytes(out):
    return {path.name: path.read_bytes() for path in out.iterdir() if path.is_file()}


@pytest.mark.parametrize("outcome", ["accepted", "rejected", "disputed"])
def test_real_request_to_signed_decision_preserves_all_static_bytes_and_current_gate(review_case, outcome):
    _, out, request, _, _, _, verifier, _ = review_case
    before = _bytes(out)
    decision = _signed(review_case, outcome=outcome)
    result = _evaluate(review_case, decision)
    assert result.status == "applicable", result
    assert result.outcome == outcome
    assert result.authority == "none"
    assert result.review_request_id == request.review_request_id
    assert result.source_receipt_id == json.loads(before["verification-receipt.json"])["receipt_id"]
    assert result.trust_policy_id == json.loads(review_case[7].read_text())["trust_policy_id"]
    assert result.next_action.model_dump(mode="json") == verifier.control.next_action.model_dump(mode="json")
    assert result.next_action.command is None
    assert result.remaining_obligations == "existing_release_gate_and_control"
    assert _bytes(out) == before
    assert not verifier.control.permissions.merge
    assert not verifier.control.permissions.report_complete
    adapter = TypeAdapter(HumanReviewEvaluationV1)
    assert not list(Draft202012Validator(adapter.json_schema()).iter_errors(result.model_dump(mode="json")))
    assert adapter.validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize("field", [
    "repository_id", "source_head_commit_sha", "base_commit_sha", "merge_base_sha", "base_tree_sha",
    "head_tree_sha", "subject_id", "input_set_id", "engine_requirement_id", "verification_request_id",
    "decision_id", "review_scope",
])
def test_even_a_valid_signature_cannot_reuse_a_different_request_scope(review_case, field):
    payload = review_case[2].model_dump(mode="json", exclude={"schema_version", "review_request_id"})
    if field == "repository_id":
        payload[field] = "example.test/another/repository"
    elif field == "review_scope":
        payload["review_items"][0]["paths"] = ["different-tools.json"]
        payload["review_set_id"] = review_set_id(payload["review_items"])
    elif "sha" in field:
        payload[field] = "f" * 40
    else:
        payload[field] = "sha256:" + "f" * 64
    payload["review_items"] = [HumanAuthorizationReviewItemV1.model_validate(row) for row in payload["review_items"]]
    payload["questions"] = [HumanReviewQuestionV1.model_validate(row) for row in payload["questions"]]
    payload["postcondition"] = HumanReviewPostconditionV1.model_validate(payload["postcondition"])
    request = build_human_review_request(**payload)
    result = _evaluate(review_case, _signed(review_case, request=request))
    assert result.status == "not_applicable"
    assert "current_request_mismatch" in result.reason_codes
    assert result.next_action is None and result.authority == "none"


@pytest.mark.parametrize("kind,reason", [
    ("missing", "decision_missing"), ("malformed", "decision_malformed"),
    ("expired", "decision_expired"), ("future", "decision_not_yet_valid"),
    ("ttl", "decision_ttl_exceeded"), ("wrong_domain", "decision_signature_invalid"),
    ("later_pr", "review_instance_mismatch"), ("untrusted_key", "signing_key_untrusted"),
    ("author", "author_cannot_review_own_change"), ("bot", "bot_cannot_supply_human_decision"),
    ("unknown_human", "reviewer_human_identity_unestablished"),
    ("ineligible", "reviewer_not_eligible"), ("principal", "principal_key_mismatch"),
])
def test_negative_decisions_have_no_continuation(review_case, kind, reason):
    context = review_case[5]
    now = review_case[3]
    kwargs = {}
    if kind == "expired":
        kwargs = {"issued_at": now - timedelta(minutes=5), "expires_at": now}
    elif kind == "future":
        kwargs = {"issued_at": now + timedelta(seconds=1)}
    elif kind == "ttl":
        kwargs = {"expires_at": now + timedelta(minutes=30)}
    elif kind == "wrong_domain":
        kwargs = {"domain": HUMAN_AUTHORIZATION_SIGNATURE_DOMAIN}
    elif kind == "principal":
        kwargs = {"principal": HumanAuthorizationPrincipalV1(provider="local-hardware", subject="somebody-else")}
    elif kind == "author":
        context = replace(context, author_principals=frozenset({PRINCIPAL}))
    elif kind == "bot":
        context = replace(context, bot_principals=frozenset({PRINCIPAL}))
    elif kind == "unknown_human":
        context = replace(context, human_principals=frozenset({AUTHOR}))
    elif kind == "ineligible":
        context = replace(context, eligible_principals=frozenset())
    decision = _signed(review_case, context=context, **kwargs)
    if kind == "missing":
        decision = None
    elif kind == "malformed":
        decision = {"accepted": "looks good to me", "reviewer": "a-github-username"}
    elif kind == "later_pr":
        context = replace(context, review_instance_id="fixture:review/2")
    elif kind == "untrusted_key":
        context = replace(context, keys=())
    result = _evaluate(review_case, decision, context=context)
    assert result.status == "not_applicable"
    assert reason in result.reason_codes
    assert result.next_action is None and result.outcome is None and result.authority == "none"


def test_a_serialized_username_context_is_not_a_trust_source(review_case):
    repo, out, _, now, _, _, _, _ = review_case
    result = review.evaluate_human_review_decision(
        _signed(review_case), workspace=repo, reports_dir=out, now=now,
        trusted_eligibility={"provider": "github", "reviewer": "fixture-reviewer", "eligible": True},
    )
    assert result.status == "not_applicable"
    assert result.reason_codes == ["trusted_eligibility_unavailable"]


def test_one_new_commit_invalidates_a_valid_historical_decision(tmp_path):
    case = _case(tmp_path)
    decision = _signed(case)
    historical = decision.model_dump_json()
    assert _evaluate(case, decision).status == "applicable"
    _git(case[0], "commit", "--allow-empty", "-qm", "next change")
    retained = HumanReviewDecisionV1.model_validate_json(historical)
    assert retained.statement.outcome == "accepted"
    result = _evaluate(case, retained)
    assert result.status == "not_applicable"
    assert "current_verification_unavailable" in result.reason_codes
    assert any(code.startswith("current_control_") for code in result.reason_codes)


def test_interleaved_verification_cannot_record_the_old_generation(review_case, monkeypatch):
    original = review._current_review
    calls = 0

    def moved(*args):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise review._ReviewEvidenceError("generation moved")
        return original(*args)

    monkeypatch.setattr(review, "_current_review", moved)
    result = _evaluate(review_case, _signed(review_case))
    assert result.status == "not_applicable"
    assert result.reason_codes == ["current_verification_changed"]


def test_signed_scope_cannot_be_trimmed_to_a_text_only_acceptance(review_case):
    payload = _signed(review_case).model_dump(mode="json")
    payload["statement"]["request"]["review_items"] = []
    assert _evaluate(review_case, payload).status == "not_applicable"


def test_schema_rejects_merge_authority_or_a_command_on_an_invalid_decision(review_case):
    adapter = TypeAdapter(HumanReviewEvaluationV1)
    result = _evaluate(review_case, None).model_dump(mode="json")
    schema = Draft202012Validator(adapter.json_schema())
    assert not list(schema.iter_errors(result))
    for change in ({"authority": "merge"}, {"next_action": {"command": "git push"}}):
        payload = {**result, **change}
        assert list(schema.iter_errors(payload))
        with pytest.raises(ValidationError):
            adapter.validate_python(payload)



def test_a_valid_signature_on_only_one_of_two_review_items_is_not_applicable(tmp_path):
    case = _case(tmp_path, two_items=True)
    request = case[2]
    assert len(request.review_items) == 2
    assert _evaluate(case, _signed(case)).status == "applicable"
    fields = {name: getattr(request, name) for name in request.__class__.model_fields
              if name not in {"schema_version", "review_request_id"}}
    fields["review_items"] = request.review_items[:1]
    fields["questions"] = request.questions[:1]
    fields["review_set_id"] = review_set_id(fields["review_items"])
    partial = build_human_review_request(**fields)
    result = _evaluate(case, _signed(case, request=partial))
    assert result.status == "not_applicable"
    assert "current_request_mismatch" in result.reason_codes


def test_current_excluded_evidence_keeps_its_gate_and_cannot_reuse_an_old_decision(tmp_path):
    case = _case(tmp_path)
    decision = _signed(case)
    tools_path = case[0] / "tools.json"
    tools = json.loads(tools_path.read_text())
    tools["tools"][0]["annotations"] = {"readOnlyHint": False, "destructiveHint": True}
    tools_path.write_text(json.dumps(tools))
    _git(case[0], "add", "tools.json")
    _git(case[0], "commit", "-qm", "introduce conflicting effect evidence")
    verifier, _, _ = _verify(case[0])
    assert not (case[1] / "human-review-request.json").exists()
    before = _bytes(case[1])
    result = _evaluate(case, decision)
    assert result.status == "not_applicable"
    assert _bytes(case[1]) == before
    assert not verifier.control.permissions.merge
    assert not verifier.control.permissions.report_complete


def test_a_decision_and_its_evaluation_remain_inspectable_after_the_next_commit(tmp_path):
    case = _case(tmp_path)
    signed = _signed(case, outcome="disputed")
    result = _evaluate(case, signed)
    assert result.status == "applicable"
    # The integration persists these separately; evaluation never rewrites the
    # static set. These files are fixture storage, not trusted eligibility.
    decision_path = tmp_path / "decision.json"
    evaluation_path = tmp_path / "evaluation.json"
    decision_path.write_text(signed.model_dump_json())
    evaluation_path.write_text(result.model_dump_json())
    _git(case[0], "commit", "--allow-empty", "-qm", "next PR")
    assert HumanReviewDecisionV1.model_validate_json(decision_path.read_text()).statement.outcome == "disputed"
    stored = TypeAdapter(HumanReviewEvaluationV1).validate_json(evaluation_path.read_text())
    assert stored.outcome == "disputed" and stored.authority == "none"
    assert _evaluate(case, signed).status == "not_applicable"


@pytest.mark.parametrize("kind", ["missing_author", "bad_clock"])
def test_additional_trust_prerequisites_fail_closed(review_case, kind):
    context = review_case[5]
    now = review_case[3]
    if kind == "missing_author":
        context = replace(context, author_principals=frozenset())
    signed = _signed(review_case, context=context)
    result = _evaluate(
        review_case, signed, context=context,
        now=now.replace(tzinfo=None) if kind == "bad_clock" else now,
    )
    assert result.status == "not_applicable" and result.next_action is None
    assert result.reason_codes == [
        "evaluation_clock_invalid" if kind == "bad_clock" else "trusted_eligibility_invalid"
    ]



def test_an_agent_generated_key_and_context_cannot_supply_host_trust(review_case):
    attacker = Ed25519PrivateKey.generate()
    raw = attacker.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    key = TrustedEd25519KeyV1(key_id=ed25519_key_id(raw), public_key=_b64url(raw),
                            provider=PRINCIPAL[0], principal=PRINCIPAL[1])
    context = replace(review_case[5], keys=(key,))
    forged_case = (*review_case[:4], attacker, context, *review_case[6:])
    signed = _signed(forged_case)
    result = _evaluate(review_case, signed, context=context)
    assert result.status == "not_applicable"
    assert "signing_key_untrusted" in result.reason_codes


def test_a_repository_authored_trust_policy_is_refused(review_case):
    forged_case = (*review_case[:7], review_case[0] / "forged-trust-policy.json")
    result = _evaluate(forged_case, _signed(review_case))
    assert result.status == "not_applicable"
    assert result.reason_codes == ["trust_policy_inside_workspace"]


def test_host_trust_revocation_during_evaluation_invalidates_the_result(review_case, monkeypatch):
    original = review.load_external_trust_policy
    count = 0

    def revoked(*args, **kwargs):
        nonlocal count
        count += 1
        policy = original(*args, **kwargs)
        if count > 1:
            return build_human_authorization_trust_policy(
                repository_ids=["example.test/a-different/repository"], keys=policy.keys,
            )
        return policy

    monkeypatch.setattr(review, "load_external_trust_policy", revoked)
    result = _evaluate(review_case, _signed(review_case))
    assert result.status == "not_applicable"
    assert result.reason_codes == ["trust_policy_changed"]


@pytest.mark.parametrize("kind,reason", [
    ("expired_key", "signing_key_expired_or_outlived"),
    ("wrong_repository", "repository_not_trusted"),
    ("missing", "trust_policy_unavailable"),
    ("malformed", "trust_policy_invalid"),
])
def test_external_host_prerequisites_are_checked_against_real_files(tmp_path, kind, reason):
    case = _case(tmp_path)
    context = case[5]
    repositories = [case[2].repository_id]
    if kind == "expired_key":
        key = context.keys[0].model_copy(update={"valid_until": case[3] - timedelta(seconds=1)})
        context = replace(context, keys=(key,))
    elif kind == "wrong_repository":
        repositories = ["example.test/another/repository"]
    policy = build_human_authorization_trust_policy(repository_ids=repositories, keys=list(context.keys))
    case[7].write_text(policy.model_dump_json())
    if kind == "missing":
        case[7].unlink()
    elif kind == "malformed":
        case[7].write_text("not json")
    result = _evaluate(case, _signed(case, context=context), context=context)
    assert result.status == "not_applicable"
    assert reason in result.reason_codes


def test_host_policy_disappearing_after_signature_validation_is_not_current(review_case, monkeypatch):
    original = review.load_external_trust_policy
    count = 0

    def removed(*args, **kwargs):
        nonlocal count
        count += 1
        if count > 1:
            raise review.HumanAuthorizationTrustPolicyError("trust_policy_unavailable", "Fixture removed")
        return original(*args, **kwargs)

    monkeypatch.setattr(review, "load_external_trust_policy", removed)
    result = _evaluate(review_case, _signed(review_case))
    assert result.reason_codes == ["trust_policy_changed", "trust_policy_unavailable"]


def test_tampered_published_request_is_not_replaced_by_a_valid_signature(tmp_path):
    case = _case(tmp_path)
    signed = _signed(case)
    path = case[1] / "human-review-request.json"
    path.write_bytes(path.read_bytes() + b" ")
    result = _evaluate(case, signed)
    assert result.status == "not_applicable"
    assert "current_verification_unavailable" in result.reason_codes


@pytest.mark.parametrize("field,value", [
    ("reason", "   "), ("reason", "approve\nrun something"),
    ("issued_at", 0), ("issued_at", "2026-09-07T00:00:00"),
    ("expires_at", False),
])
def test_decision_grammar_rejects_ambiguous_text_and_time_inputs(review_case, field, value):
    payload = _signed(review_case).model_dump(mode="json")
    assert not list(Draft202012Validator(HumanReviewDecisionV1.model_json_schema()).iter_errors(payload))
    payload["statement"][field] = value
    assert _evaluate(review_case, payload).reason_codes == ["decision_malformed"]
