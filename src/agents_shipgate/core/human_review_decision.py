"""Evaluate externally signed review decisions without changing static evidence.

The caller supplies eligibility from its trusted integration boundary. There is
no loader that turns a repository-authored username or JSON context into trust,
no private-key API, no network call, and no operation execution here.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import ValidationError

from agents_shipgate.core.current_control import CurrentControlUnavailable, read_current_control
from agents_shipgate.core.human_authorization import (
    HumanAuthorizationTrustPolicyError,
    default_human_authorization_trust_policy_path,
    load_external_trust_policy,
)
from agents_shipgate.core.human_review_request import project_human_review_request
from agents_shipgate.core.verification_identity import (
    load_validated_receipt_artifacts,
    sha256_bytes,
    validate_engine_requirement,
)
from agents_shipgate.schemas.agent_control import HumanControlAction
from agents_shipgate.schemas.human_authorization import (
    TrustedEd25519KeyV1,
    decode_ed25519_public_key,
    decode_ed25519_signature,
)
from agents_shipgate.schemas.human_review_decision import (
    HUMAN_REVIEW_DECISION_SIGNATURE_DOMAIN,
    ApplicableHumanReviewDecisionV1,
    HumanReviewDecisionStatementV1,
    HumanReviewDecisionV1,
    HumanReviewEvaluationV1,
    InapplicableHumanReviewDecisionV1,
)
from agents_shipgate.schemas.human_review_request import HumanReviewRequestV1
from agents_shipgate.schemas.report import ReadinessReport
from agents_shipgate.schemas.verification_identity import (
    VerificationPlan,
    VerificationReceipt,
    canonical_json,
    content_id,
)
from agents_shipgate.schemas.verifier import VerifierArtifact

Principal = tuple[str, str]


@dataclass(frozen=True)
class TrustedReviewEligibility:
    """Host-supplied identity/eligibility, not a parseable proof of itself.

    Only a trusted caller may construct this from authenticated identity and
    current eligibility evidence. In particular, a GitHub integration must use
    credentials isolated from the coding agent and check author/bot identity.
    Instantiating this class does not prove those preconditions. #337 owns that
    production boundary; the host-neutral tests use an isolated fixture signer.
    """

    repository_id: str
    review_instance_id: str
    eligible_principals: frozenset[Principal]
    author_principals: frozenset[Principal]
    human_principals: frozenset[Principal]
    bot_principals: frozenset[Principal]
    keys: tuple[TrustedEd25519KeyV1, ...]
    max_ttl_seconds: int = 900

    @property
    def context_id(self) -> str:
        return content_id({
            "repository_id": self.repository_id,
            "review_instance_id": self.review_instance_id,
            "eligible_principals": sorted(self.eligible_principals),
            "author_principals": sorted(self.author_principals),
            "human_principals": sorted(self.human_principals),
            "bot_principals": sorted(self.bot_principals),
            "keys": sorted((key.model_dump(mode="json") for key in self.keys), key=lambda key: key["key_id"]),
            "max_ttl_seconds": self.max_ttl_seconds,
        })


@dataclass(frozen=True)
class _CurrentReview:
    request: HumanReviewRequestV1
    receipt_id: str
    control_id: str
    next_action: HumanControlAction


class _ReviewEvidenceError(ValueError):
    pass


def _current_review(workspace: Path, reports_dir: Path) -> _CurrentReview:
    # The existing observer is shared with `agent control`; do not implement
    # a weaker "HEAD still matches" freshness test for this consumer.
    from agents_shipgate.cli.current_workspace import live_workspace

    workspace = workspace.resolve()
    reports_dir = reports_dir.resolve()
    observed = read_current_control(
        reports_dir,
        live=lambda: live_workspace(workspace, reports_dir),
        capture=("verification_plan", "verification_receipt", "verifier", "report"),
    )
    data = observed.artifacts
    plan = VerificationPlan.model_validate_json(data["verification_plan"])
    receipt = VerificationReceipt.model_validate_json(data["verification_receipt"])
    verifier = VerifierArtifact.model_validate_json(data["verifier"])
    report = ReadinessReport.model_validate_json(data["report"])
    # The current pointer binds its own selected artifacts. Its receipt binds
    # a larger closure, including the optional review request. Validate that
    # closure too, and require it to be the receipt the pointer just captured.
    closure_receipt, closure = load_validated_receipt_artifacts(
        receipt_path=reports_dir / "verification-receipt.json", root=reports_dir,
    )
    if closure_receipt != receipt:
        raise _ReviewEvidenceError("receipt_generation_changed")
    try:
        validate_engine_requirement(plan.engine, plugins_enabled=plan.inputs.options.get("plugins_enabled"))
    except ValueError as exc:
        raise _ReviewEvidenceError("verification_engine_changed") from exc
    request = project_human_review_request(verifier=verifier, report=report, plan=plan)
    if request is None:
        raise _ReviewEvidenceError("request_class_not_eligible")
    if verifier.control.next_action is None or verifier.control.next_action.actor != "human":
        raise _ReviewEvidenceError("current_human_route_required")
    expected = json.dumps(request.model_dump(mode="json"), indent=2, sort_keys=True).encode("utf-8")
    bound = receipt.artifact_manifest.artifacts.get("human_review_request_json")
    if bound is None or bound.sha256 != sha256_bytes(expected) or bound.size_bytes != len(expected):
        raise _ReviewEvidenceError("published_request_differs_from_current_evidence")
    if closure.get("human_review_request_json") != expected:
        raise _ReviewEvidenceError("published_request_differs_from_current_evidence")
    for attribute, expected_value in (
        ("request_id", plan.request_id), ("subject_id", plan.subject.subject_id),
        ("input_set_id", plan.inputs.input_set_id),
        ("engine_requirement_id", plan.engine.engine_requirement_id),
        ("decision_id", verifier.decision_id),
    ):
        if getattr(receipt, attribute) != expected_value:
            raise _ReviewEvidenceError("receipt_identity_mismatch")
    return _CurrentReview(
        request=request,
        receipt_id=receipt.receipt_id,
        control_id=observed.pointer.current_control_id,
        next_action=HumanControlAction.model_validate(verifier.control.next_action.model_dump(mode="json")),
    )


def human_review_decision_signature_payload(statement: HumanReviewDecisionStatementV1) -> bytes:
    return HUMAN_REVIEW_DECISION_SIGNATURE_DOMAIN + canonical_json(statement)


def evaluate_human_review_decision(
    decision: HumanReviewDecisionV1 | Mapping[str, Any] | None,
    *,
    workspace: Path,
    reports_dir: Path,
    trusted_eligibility: TrustedReviewEligibility | None,
    now: datetime | None = None,
) -> HumanReviewEvaluationV1:
    """Evaluate one current, complete-scope decision; never grant authority.

    The verification graph is reconstructed from one current, receipt-validated
    snapshot. Signer-supplied receipt/provenance labels are not inputs. No file
    is written, and the original static artifacts and control stay unchanged.
    The fixed external host trust policy, shared with push authorization, is
    independently loaded; the signature domain and permitted consequence are
    separate. A context cannot introduce its own trusted signing key.
    `applicable` describes this evaluation, not persistence. The integration
    must preserve the returned evidence and signed decision separately.
    """

    current_time = now if now is not None else datetime.now(UTC)
    if not isinstance(current_time, datetime) or current_time.tzinfo is None or current_time.utcoffset() is None:
        return InapplicableHumanReviewDecisionV1(
            evaluated_at=datetime.now(UTC), reason_codes=["evaluation_clock_invalid"]
        )
    current_time = current_time.astimezone(UTC)

    def refuse(*reasons: str) -> InapplicableHumanReviewDecisionV1:
        return InapplicableHumanReviewDecisionV1(
            evaluated_at=current_time, reason_codes=sorted(set(reasons))
        )

    if decision is None:
        return refuse("decision_missing")
    if not isinstance(trusted_eligibility, TrustedReviewEligibility):
        return refuse("trusted_eligibility_unavailable")
    try:
        context = replace(
            trusted_eligibility,
            keys=tuple(TrustedEd25519KeyV1.model_validate(key.model_dump(mode="json"))
                       for key in trusted_eligibility.keys),
        )
        context_id = context.context_id
    except (AttributeError, ValidationError, TypeError, ValueError):
        return refuse("trusted_eligibility_invalid")
    if (
        not context.repository_id or not context.review_instance_id
        or not context.author_principals or not context.human_principals
        or type(context.max_ttl_seconds) is not int
        or not 1 <= context.max_ttl_seconds <= 86400
        or len({key.key_id for key in context.keys}) != len(context.keys)
    ):
        return refuse("trusted_eligibility_invalid")
    try:
        signed = HumanReviewDecisionV1.model_validate(
            decision.model_dump(mode="json") if isinstance(decision, HumanReviewDecisionV1) else decision
        )
    except (ValidationError, TypeError, ValueError):
        return refuse("decision_malformed")
    try:
        current = _current_review(workspace, reports_dir)
    except _ReviewEvidenceError as exc:
        return refuse(str(exc))
    except CurrentControlUnavailable as exc:
        return refuse("current_verification_unavailable", "current_control_" + exc.reason)
    except (OSError, ValueError, KeyError):
        return refuse("current_verification_unavailable")
    try:
        policy_path = default_human_authorization_trust_policy_path()
        policy = load_external_trust_policy(policy_path, workspace=workspace)
    except HumanAuthorizationTrustPolicyError as exc:
        return refuse(exc.code)
    statement = signed.statement
    reasons: list[str] = []
    if current.request.repository_id not in policy.repository_ids:
        reasons.append("repository_not_trusted")
    if statement.request != current.request:
        reasons.append("current_request_mismatch")
    if context.repository_id != current.request.repository_id:
        reasons.append("repository_eligibility_mismatch")
    if statement.review_instance_id != context.review_instance_id:
        reasons.append("review_instance_mismatch")
    if statement.eligibility_context_id != context_id:
        reasons.append("eligibility_context_mismatch")
    principal = (statement.principal.provider, statement.principal.subject)
    if principal not in context.human_principals:
        reasons.append("reviewer_human_identity_unestablished")
    if principal not in context.eligible_principals:
        reasons.append("reviewer_not_eligible")
    if principal in context.author_principals:
        reasons.append("author_cannot_review_own_change")
    if principal in context.bot_principals:
        reasons.append("bot_cannot_supply_human_decision")
    key = next((key for key in policy.keys if key.key_id == signed.proof.key_id), None)
    expected_key = next((key for key in context.keys if key.key_id == signed.proof.key_id), None)
    # The context may restrict host trust, but cannot add a key to it. An
    # agent-created context plus its own private key is never sufficient.
    if key is None or key != expected_key:
        reasons.append("signing_key_untrusted")
    else:
        # Revalidate the key bytes/id too; a caller-mutated model instance is
        # not a way around the schema's key-identity check.
        try:
            key = TrustedEd25519KeyV1.model_validate(key.model_dump(mode="json"))
            if (key.provider, key.principal) != principal:
                reasons.append("principal_key_mismatch")
            if key.valid_from is not None and (statement.issued_at < key.valid_from or current_time < key.valid_from):
                reasons.append("signing_key_not_yet_valid")
            if key.valid_until is not None and (current_time >= key.valid_until or statement.expires_at > key.valid_until):
                reasons.append("signing_key_expired_or_outlived")
            Ed25519PublicKey.from_public_bytes(decode_ed25519_public_key(key.public_key)).verify(
                decode_ed25519_signature(signed.proof.signature),
                human_review_decision_signature_payload(statement),
            )
        except (InvalidSignature, ValidationError, ValueError, TypeError):
            reasons.append("decision_signature_invalid")
    if statement.issued_at > current_time:
        reasons.append("decision_not_yet_valid")
    if statement.expires_at <= current_time:
        reasons.append("decision_expired")
    if (statement.expires_at - statement.issued_at).total_seconds() > min(context.max_ttl_seconds, policy.max_ttl_seconds):
        reasons.append("decision_ttl_exceeded")
    if reasons:
        return refuse(*reasons)
    # Confirm the generation again after crypto/eligibility work. A valid
    # signature must not turn a workspace that moved during the read into a
    # applicable decision on stale evidence.
    try:
        if load_external_trust_policy(policy_path, workspace=workspace) != policy:
            return refuse("trust_policy_changed")
        if _current_review(workspace, reports_dir) != current:
            return refuse("current_verification_changed")
    except HumanAuthorizationTrustPolicyError as exc:
        return refuse("trust_policy_changed", exc.code)
    except (CurrentControlUnavailable, OSError, ValueError, KeyError):
        return refuse("current_verification_changed")
    return ApplicableHumanReviewDecisionV1(
        evaluated_at=current_time,
        outcome=statement.outcome,
        review_decision_id=signed.review_decision_id,
        review_request_id=current.request.review_request_id,
        source_receipt_id=current.receipt_id,
        current_control_id=current.control_id,
        review_instance_id=context.review_instance_id,
        eligibility_context_id=context_id,
        trust_policy_id=policy.trust_policy_id,
        principal=statement.principal,
        next_action=current.next_action,
        expires_at=statement.expires_at,
    )
