from __future__ import annotations

import base64
import json
import os
import stat
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from agents_shipgate.core.human_authorization import (
    HumanAuthorizationTrustPolicyError,
    _validate_trust_path_node,
    default_human_authorization_trust_policy_path,
    evaluate_human_authorization,
    human_authorization_signature_payload,
    load_external_trust_policy,
)
from agents_shipgate.schemas.human_authorization import (
    AuthorizationEvaluationV1,
    HumanAuthorizationPrincipalV1,
    HumanAuthorizationProofV1,
    HumanAuthorizationRequestV1,
    HumanAuthorizationReviewItemV1,
    HumanAuthorizationStatementV1,
    HumanAuthorizationTrustPolicyV1,
    HumanAuthorizationV1,
    TrustedEd25519KeyV1,
    authorization_review_items,
    build_git_push_operation,
    build_human_authorization,
    build_human_authorization_request,
    build_human_authorization_trust_policy,
    ed25519_key_id,
)
from agents_shipgate.schemas.verification_identity import content_id

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
BASE_COMMIT = "9" * 40
MERGE_BASE = "8" * 40
BASE_TREE = "a" * 40
HEAD_TREE = "b" * 40
HEAD_COMMIT = "c" * 40
LEASE_OID = "d" * 40
DESTINATION_REF = "refs/heads/codex/human-authorization-state"
REPOSITORY_ID = "example.test/ThreeMoonsLab/agents-shipgate"
PUSH_URL = "https://example.test/ThreeMoonsLab/agents-shipgate.git"


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _content(character: str) -> str:
    return f"sha256:{character * 64}"


def _trusted_key(
    private_key: Ed25519PrivateKey,
    *,
    provider: str = "github",
    principal: str = "github:user:reviewer",
) -> TrustedEd25519KeyV1:
    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return TrustedEd25519KeyV1(
        key_id=ed25519_key_id(raw),
        public_key=_b64url(raw),
        provider=provider,
        principal=principal,
        valid_from=NOW - timedelta(days=1),
        valid_until=NOW + timedelta(days=1),
    )


def _review_items() -> list[HumanAuthorizationReviewItemV1]:
    return [
        HumanAuthorizationReviewItemV1(
            review_item_id="binding-graph",
            check_id="SHIP-VERIFY-BINDING-GRAPH",
            support_hash=_content("1"),
            paths=["docs/review (final).md", "shipgate.yaml"],
        ),
        HumanAuthorizationReviewItemV1(
            review_item_id="protected-workflow",
            check_id="SHIP-VERIFY-PROTECTED-WORKFLOW",
            fingerprint="fp_workflow!reviewed",
            paths=[".github/workflows/agents-shipgate.yml"],
        ),
    ]


def _request(
    review_items: list[HumanAuthorizationReviewItemV1] | None = None,
    *,
    source_commit_sha: str = HEAD_COMMIT,
    destination_ref: str = DESTINATION_REF,
    expected_lease_oid: str = LEASE_OID,
) -> HumanAuthorizationRequestV1:
    operation = build_git_push_operation(
        destination_repository_id=REPOSITORY_ID,
        push_url=PUSH_URL,
        source_commit_sha=source_commit_sha,
        destination_ref=destination_ref,
        expected_lease_oid=expected_lease_oid,
    )
    return build_human_authorization_request(
        repository_id=REPOSITORY_ID,
        source_receipt_id=_content("5"),
        source_artifact_set_id=_content("6"),
        source_engine_requirement_id=_content("7"),
        source_executor_id=_content("8"),
        verification_request_id=_content("2"),
        subject_id=_content("3"),
        decision_id=_content("4"),
        base_commit_sha=BASE_COMMIT,
        merge_base_sha=MERGE_BASE,
        base_tree_sha=BASE_TREE,
        head_tree_sha=HEAD_TREE,
        source_head_commit_sha=source_commit_sha,
        review_items=review_items or _review_items(),
        operation=operation,
    )


def _grant(
    private_key: Ed25519PrivateKey,
    request: HumanAuthorizationRequestV1,
    *,
    issued_at: datetime = NOW - timedelta(seconds=5),
    not_before: datetime = NOW - timedelta(seconds=5),
    expires_at: datetime = NOW + timedelta(minutes=10),
    reason: str = "LGTM! (reviewed in Codex)",
    provider: str = "github",
    principal: str = "github:user:reviewer",
) -> HumanAuthorizationV1:
    statement = HumanAuthorizationStatementV1(
        request=request,
        principal=HumanAuthorizationPrincipalV1(
            provider=provider,
            subject=principal,
        ),
        reason=reason,
        issued_at=issued_at,
        not_before=not_before,
        expires_at=expires_at,
        nonce=_b64url(b"0123456789abcdef"),
    )
    signature = private_key.sign(human_authorization_signature_payload(statement))
    trusted = _trusted_key(
        private_key,
        provider=provider,
        principal=principal,
    )
    proof = HumanAuthorizationProofV1(
        key_id=trusted.key_id,
        signature=_b64url(signature),
    )
    return build_human_authorization(statement=statement, proof=proof)


def _write_policy(
    path: Path,
    key: TrustedEd25519KeyV1,
    *,
    repository_ids: list[str] | None = None,
    max_ttl_seconds: int = 900,
) -> HumanAuthorizationTrustPolicyV1:
    policy = build_human_authorization_trust_policy(
        repository_ids=repository_ids or [REPOSITORY_ID],
        keys=[key],
        max_ttl_seconds=max_ttl_seconds,
        clock_skew_seconds=30,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(policy.model_dump_json(indent=2), encoding="utf-8")
    path.chmod(0o600)
    return policy


@dataclass
class AuthorizationWorld:
    workspace: Path
    trust_policy_path: Path
    private_key: Ed25519PrivateKey
    trusted_key: TrustedEd25519KeyV1
    review_items: list[HumanAuthorizationReviewItemV1]
    request: HumanAuthorizationRequestV1
    grant: HumanAuthorizationV1
    policy: HumanAuthorizationTrustPolicyV1


@pytest.fixture
def world(tmp_path: Path) -> AuthorizationWorld:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    private_key = Ed25519PrivateKey.generate()
    trusted_key = _trusted_key(private_key)
    review_items = _review_items()
    request = _request(review_items)
    grant = _grant(private_key, request)
    trust_policy_path = tmp_path / "host-trust" / "policy.json"
    policy = _write_policy(trust_policy_path, trusted_key)
    return AuthorizationWorld(
        workspace=workspace,
        trust_policy_path=trust_policy_path,
        private_key=private_key,
        trusted_key=trusted_key,
        review_items=review_items,
        request=request,
        grant=grant,
        policy=policy,
    )


def _evaluate(
    world: AuthorizationWorld,
    grant: HumanAuthorizationV1 | dict[str, object] | None = None,
    *,
    request: HumanAuthorizationRequestV1 | None = None,
    review_items: list[HumanAuthorizationReviewItemV1] | None = None,
    trust_policy_path: Path | None = None,
    now: datetime = NOW,
) -> AuthorizationEvaluationV1:
    return evaluate_human_authorization(
        grant or world.grant,
        trust_policy_path=trust_policy_path or world.trust_policy_path,
        workspace=world.workspace,
        expected_request=request or world.request,
        expected_review_items=review_items or world.review_items,
        now=now,
    )


def test_exact_signed_authorization_accepts_only_bound_push(
    world: AuthorizationWorld,
) -> None:
    evaluation = _evaluate(world)

    expected_command = (
        f"git push --force-with-lease={DESTINATION_REF}:{LEASE_OID} "
        f"{PUSH_URL} {HEAD_COMMIT}:{DESTINATION_REF}"
    )
    assert evaluation.status == "accepted"
    assert evaluation.authorization_id == world.grant.authorization_id
    assert evaluation.trust_policy_id == world.policy.trust_policy_id
    assert evaluation.operation_id == world.request.operation.operation_id
    assert evaluation.command == expected_command
    assert evaluation.reason_codes == []


def test_changed_operation_and_request_are_rejected_without_command(
    world: AuthorizationWorld,
) -> None:
    changed_request = _request(
        world.review_items,
        expected_lease_oid="e" * 40,
    )
    changed_grant = _grant(world.private_key, changed_request)

    evaluation = _evaluate(world, changed_grant)

    assert evaluation.status == "rejected"
    assert "authorization_request_id_mismatch" in evaluation.reason_codes
    assert "authorization_request_mismatch" in evaluation.reason_codes
    assert evaluation.command is None


def test_signature_tampering_changes_grant_id_and_fails_closed(
    world: AuthorizationWorld,
) -> None:
    original_signature = base64.urlsafe_b64decode(
        world.grant.proof.signature + "=" * (-len(world.grant.proof.signature) % 4)
    )
    tampered_signature = bytes([original_signature[0] ^ 1]) + original_signature[1:]
    proof = HumanAuthorizationProofV1(
        key_id=world.grant.proof.key_id,
        signature=_b64url(tampered_signature),
    )
    tampered = build_human_authorization(
        statement=world.grant.statement,
        proof=proof,
    )

    evaluation = _evaluate(world, tampered)

    assert tampered.authorization_id != world.grant.authorization_id
    assert evaluation.status == "rejected"
    assert evaluation.reason_codes == ["signature_invalid"]
    assert evaluation.command is None


def test_expired_and_overlong_authorizations_are_rejected(
    world: AuthorizationWorld,
) -> None:
    expired = _grant(
        world.private_key,
        world.request,
        issued_at=NOW - timedelta(minutes=10),
        not_before=NOW - timedelta(minutes=10),
        expires_at=NOW,
    )
    overlong = _grant(
        world.private_key,
        world.request,
        issued_at=NOW - timedelta(seconds=5),
        not_before=NOW - timedelta(seconds=5),
        expires_at=NOW + timedelta(minutes=16),
    )

    expired_result = _evaluate(world, expired)
    overlong_result = _evaluate(world, overlong)

    assert "authorization_expired" in expired_result.reason_codes
    assert expired_result.command is None
    assert "authorization_ttl_exceeded" in overlong_result.reason_codes
    assert overlong_result.command is None


def test_partial_review_scope_is_rejected(world: AuthorizationWorld) -> None:
    partial_items = [world.review_items[0]]
    partial_request = _request(partial_items)
    partial_grant = _grant(world.private_key, partial_request)

    evaluation = _evaluate(world, partial_grant)

    assert evaluation.status == "rejected"
    assert "review_scope_mismatch" in evaluation.reason_codes
    assert evaluation.command is None


def test_untrusted_key_and_principal_mismatch_are_rejected(
    world: AuthorizationWorld,
) -> None:
    other_private_key = Ed25519PrivateKey.generate()
    other_key = _trusted_key(other_private_key)
    other_policy_path = world.trust_policy_path.parent / "other-policy.json"
    _write_policy(other_policy_path, other_key)

    untrusted = _evaluate(world, trust_policy_path=other_policy_path)
    assert "signing_key_not_trusted" in untrusted.reason_codes
    assert untrusted.command is None

    wrong_principal_key = _trusted_key(
        world.private_key,
        principal="github:user:different-reviewer",
    )
    wrong_principal_policy = world.trust_policy_path.parent / "wrong-principal.json"
    _write_policy(wrong_principal_policy, wrong_principal_key)
    mismatch = _evaluate(world, trust_policy_path=wrong_principal_policy)
    assert "principal_subject_mismatch" in mismatch.reason_codes
    assert mismatch.command is None


def test_workspace_or_relative_trust_policy_is_never_authoritative(
    world: AuthorizationWorld,
) -> None:
    in_workspace = world.workspace / "trust.json"
    _write_policy(in_workspace, world.trusted_key)

    inside_result = _evaluate(world, trust_policy_path=in_workspace)
    relative_result = _evaluate(world, trust_policy_path=Path("trust.json"))

    assert inside_result.reason_codes == ["trust_policy_inside_workspace"]
    assert relative_result.reason_codes == ["trust_policy_path_not_absolute"]
    assert inside_result.command is None
    assert relative_result.command is None


def test_symlinked_or_world_writable_trust_policy_is_rejected(
    world: AuthorizationWorld,
) -> None:
    symlink = world.trust_policy_path.parent / "policy-link.json"
    symlink.symlink_to(world.trust_policy_path)
    symlink_result = _evaluate(world, trust_policy_path=symlink)
    assert symlink_result.reason_codes == ["trust_policy_symlink"]

    world.trust_policy_path.chmod(0o666)
    writable_result = _evaluate(world)
    assert writable_result.reason_codes == ["trust_policy_insecure_permissions"]


def test_trusted_sticky_ancestor_preserves_policy_authority(
    world: AuthorizationWorld,
) -> None:
    sticky_parent = world.trust_policy_path.parent.parent / "sticky"
    sticky_policy = sticky_parent / "broker" / "policy.json"
    _write_policy(sticky_policy, world.trusted_key)
    sticky_parent.chmod(0o1777)
    try:
        evaluation = _evaluate(world, trust_policy_path=sticky_policy)
    finally:
        sticky_parent.chmod(0o700)

    assert evaluation.status == "accepted"
    assert evaluation.command == world.request.operation.command


def test_sticky_ancestor_owned_by_an_untrusted_account_is_rejected() -> None:
    effective_uid = os.geteuid()
    untrusted_uid = effective_uid + 1 if effective_uid != 0 else 1
    metadata = os.stat_result(
        (
            stat.S_IFDIR | 0o1777,
            0,
            0,
            1,
            untrusted_uid,
            0,
            0,
            0,
            0,
            0,
        )
    )

    with pytest.raises(HumanAuthorizationTrustPolicyError) as exc_info:
        _validate_trust_path_node(metadata, label="trust policy ancestor", directory=True)

    assert exc_info.value.code == "trust_policy_owner_mismatch"


def test_hard_linked_or_replaceable_trust_policy_path_is_rejected(
    world: AuthorizationWorld,
) -> None:
    hard_link = world.trust_policy_path.parent / "policy-hard-link.json"
    os.link(world.trust_policy_path, hard_link)
    hard_linked = _evaluate(world)
    assert hard_linked.reason_codes == ["trust_policy_hard_linked"]
    hard_link.unlink()

    insecure_parent = world.trust_policy_path.parent.parent / "replaceable"
    insecure_parent.mkdir()
    insecure_policy = insecure_parent / "policy.json"
    _write_policy(insecure_policy, world.trusted_key)
    insecure_parent.chmod(0o777)
    try:
        replaceable = _evaluate(world, trust_policy_path=insecure_policy)
    finally:
        insecure_parent.chmod(0o700)
    assert replaceable.reason_codes == ["trust_policy_insecure_ancestor"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("push_url", "https://example.test/repo.git;touch-pwned"),
        ("push_url", "https://example.test/repo.git\nother"),
        ("destination_ref", "refs/heads/main;touch-pwned"),
        ("destination_ref", "refs/heads/main\x00other"),
    ],
)
def test_rendered_git_operation_rejects_shell_syntax(
    field: str,
    value: str,
) -> None:
    arguments = {
        "destination_repository_id": REPOSITORY_ID,
        "push_url": PUSH_URL,
        "source_commit_sha": HEAD_COMMIT,
        "destination_ref": DESTINATION_REF,
        "expected_lease_oid": LEASE_OID,
    }
    arguments[field] = value

    with pytest.raises((ValueError, ValidationError)):
        build_git_push_operation(**arguments)


@pytest.mark.parametrize(
    "push_url",
    [
        "http://example.test/ThreeMoonsLab/agents-shipgate.git",
        "ssh://git@example.test/ThreeMoonsLab/agents-shipgate.git",
        "git@example.test:ThreeMoonsLab/agents-shipgate.git",
        "file:///tmp/agents-shipgate.git",
        "/tmp/agents-shipgate.git",
        "../agents-shipgate.git",
        "https://token@example.test/ThreeMoonsLab/agents-shipgate.git",
        "https://example.test/ThreeMoonsLab/agents-shipgate.git?token=secret",
        "https://example.test/ThreeMoonsLab/agents-shipgate.git#alternate",
        "https://example.test/ThreeMoonsLab/../agents-shipgate.git",
    ],
)
def test_git_push_operation_rejects_noncanonical_or_mutable_endpoints(
    push_url: str,
) -> None:
    with pytest.raises((ValueError, ValidationError)):
        build_git_push_operation(
            destination_repository_id=REPOSITORY_ID,
            push_url=push_url,
            source_commit_sha=HEAD_COMMIT,
            destination_ref=DESTINATION_REF,
            expected_lease_oid=LEASE_OID,
        )


def test_git_push_operation_binds_repository_identity_and_pinned_url() -> None:
    operation = build_git_push_operation(
        destination_repository_id=REPOSITORY_ID,
        push_url=PUSH_URL,
        source_commit_sha=HEAD_COMMIT,
        destination_ref=DESTINATION_REF,
        expected_lease_oid=LEASE_OID,
    )

    assert operation.destination_repository_id == REPOSITORY_ID
    assert operation.push_url == PUSH_URL
    assert operation.argv() == [
        "git",
        "push",
        f"--force-with-lease={DESTINATION_REF}:{LEASE_OID}",
        PUSH_URL,
        f"{HEAD_COMMIT}:{DESTINATION_REF}",
    ]

    with pytest.raises((ValueError, ValidationError), match="repository identity"):
        build_git_push_operation(
            destination_repository_id="attacker.test/ThreeMoonsLab/agents-shipgate",
            push_url=PUSH_URL,
            source_commit_sha=HEAD_COMMIT,
            destination_ref=DESTINATION_REF,
            expected_lease_oid=LEASE_OID,
        )


@pytest.mark.parametrize("oid_length", [39, 41, 63, 65])
def test_git_push_operation_rejects_non_git_object_id_lengths(oid_length: int) -> None:
    with pytest.raises((ValueError, ValidationError)):
        build_git_push_operation(
            destination_repository_id=REPOSITORY_ID,
            push_url=PUSH_URL,
            source_commit_sha="c" * oid_length,
            destination_ref=DESTINATION_REF,
            expected_lease_oid=LEASE_OID,
        )


def test_authorization_request_rejects_destination_repository_drift() -> None:
    operation = build_git_push_operation(
        destination_repository_id=REPOSITORY_ID,
        push_url=PUSH_URL,
        source_commit_sha=HEAD_COMMIT,
        destination_ref=DESTINATION_REF,
        expected_lease_oid=LEASE_OID,
    )

    with pytest.raises(ValidationError, match="reviewed repository identity"):
        build_human_authorization_request(
            repository_id="attacker.test/ThreeMoonsLab/agents-shipgate",
            source_receipt_id=_content("5"),
            source_artifact_set_id=_content("6"),
            source_engine_requirement_id=_content("7"),
            source_executor_id=_content("8"),
            verification_request_id=_content("2"),
            subject_id=_content("3"),
            decision_id=_content("4"),
            base_commit_sha=BASE_COMMIT,
            merge_base_sha=MERGE_BASE,
            base_tree_sha=BASE_TREE,
            head_tree_sha=HEAD_TREE,
            source_head_commit_sha=HEAD_COMMIT,
            review_items=_review_items(),
            operation=operation,
        )


def test_request_schema_marks_source_closure_ids_as_signer_authenticated() -> None:
    properties = HumanAuthorizationRequestV1.model_json_schema()["properties"]

    for field in ("source_receipt_id", "source_artifact_set_id"):
        description = properties[field]["description"]
        assert "Signer-authenticated provenance label" in description
        assert "do not independently transport or authenticate" in description


def test_display_metadata_allows_normal_punctuation_but_not_controls() -> None:
    request = _request()
    statement = HumanAuthorizationStatementV1(
        request=request,
        principal=HumanAuthorizationPrincipalV1(
            provider="github",
            subject="Reviewer! (release owner)",
        ),
        reason="LGTM! (reviewed end-to-end); ship it.",
        issued_at=NOW,
        not_before=NOW,
        expires_at=NOW + timedelta(minutes=1),
        nonce=_b64url(b"0123456789abcdef"),
    )
    assert statement.reason.endswith("ship it.")

    with pytest.raises(ValidationError):
        HumanAuthorizationPrincipalV1(
            provider="github",
            subject="Reviewer\nforged",
        )


def test_nonaccepted_evaluation_cannot_expose_a_command() -> None:
    with pytest.raises(ValidationError):
        AuthorizationEvaluationV1(
            status="rejected",
            command="git push origin HEAD:refs/heads/main",
            reason_codes=["signature_invalid"],
        )


@pytest.mark.skipif(os.name != "posix", reason="POSIX account-home guarantee")
def test_default_trust_path_ignores_agent_controlled_home_and_xdg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = default_human_authorization_trust_policy_path()
    monkeypatch.setenv("HOME", "/tmp/attacker-home")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/attacker-xdg")

    after = default_human_authorization_trust_policy_path()

    assert after == before
    assert not str(after).startswith("/tmp/attacker-")


def test_trust_policy_round_trip_is_content_addressed(
    world: AuthorizationWorld,
) -> None:
    loaded = load_external_trust_policy(
        world.trust_policy_path,
        workspace=world.workspace,
    )
    assert loaded == world.policy

    document = json.loads(world.trust_policy_path.read_text(encoding="utf-8"))
    document["max_ttl_seconds"] += 1
    world.trust_policy_path.write_text(json.dumps(document), encoding="utf-8")
    world.trust_policy_path.chmod(0o600)
    result = _evaluate(world)
    assert result.reason_codes == ["trust_policy_invalid"]
    assert result.command is None


def test_complete_grant_identity_includes_proof(world: AuthorizationWorld) -> None:
    expected = content_id(
        {
            "statement": world.grant.statement.model_dump(mode="json"),
            "proof": world.grant.proof.model_dump(mode="json"),
        }
    )
    assert world.grant.authorization_id == expected


def test_release_decision_projection_binds_complete_review_scope() -> None:
    projected = authorization_review_items(
        {
            "decision": "review_required",
            "review_items": [
                {
                    "id": "review-1",
                    "fingerprint": "fp_review_1",
                    "check_id": "SHIP-VERIFY-TRUST-ROOT-TOUCHED",
                    "support": {"support_hash": _content("8")},
                    "source": {"path": "src/agent.py"},
                    "policy_evidence_source": {"path": "shipgate.yaml"},
                }
            ],
        }
    )

    assert projected == [
        HumanAuthorizationReviewItemV1(
            review_item_id="review-1",
            fingerprint="fp_review_1",
            check_id="SHIP-VERIFY-TRUST-ROOT-TOUCHED",
            support_hash=_content("8"),
            paths=["shipgate.yaml", "src/agent.py"],
        )
    ]


def test_release_decision_projection_accepts_explicit_null_source_path() -> None:
    projected = authorization_review_items(
        {
            "decision": "review_required",
            "review_items": [
                {
                    "id": "review-1",
                    "check_id": "SHIP-VERIFY-TRUST-ROOT-TOUCHED",
                    "source": {
                        "type": "verify",
                        "ref": "protected-surface",
                        "path": None,
                    },
                }
            ],
        }
    )

    assert projected == [
        HumanAuthorizationReviewItemV1(
            review_item_id="review-1",
            check_id="SHIP-VERIFY-TRUST-ROOT-TOUCHED",
            paths=[],
        )
    ]


@pytest.mark.parametrize(
    "release_decision",
    [
        {"decision": "blocked", "review_items": [{"id": "x", "check_id": "C"}]},
        {"decision": "review_required", "review_items": []},
        {
            "decision": "review_required",
            "review_items": [{"id": "x", "check_id": "C", "source": {}}],
        },
        {
            "decision": "review_required",
            "review_items": [{"id": "x", "check_id": "C", "support": "forged"}],
        },
    ],
)
def test_release_decision_projection_rejects_incomplete_sets(
    release_decision: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        authorization_review_items(release_decision)


def _authorization_wire_schema(name: str) -> dict[str, object]:
    schema_path = Path(__file__).resolve().parent.parent / "docs/human-authorization-schema.v1.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    return schema["$defs"][name]


def test_wire_schema_rejects_impossible_authorization_evaluations(
    world: AuthorizationWorld,
) -> None:
    validator = Draft202012Validator(_authorization_wire_schema("AuthorizationEvaluationV1"))
    accepted_model = AuthorizationEvaluationV1(
        status="accepted",
        authorization_id=world.grant.authorization_id,
        authorization_request_id=world.request.authorization_request_id,
        trust_policy_id=world.policy.trust_policy_id,
        key_id=world.grant.proof.key_id,
        provider=world.grant.statement.principal.provider,
        principal=world.grant.statement.principal.subject,
        operation_id=world.request.operation.operation_id,
        command=world.request.operation.command,
        issued_at=world.grant.statement.issued_at,
        expires_at=world.grant.statement.expires_at,
    )
    assert accepted_model.status == "accepted"
    accepted = accepted_model.model_dump(mode="json")
    assert list(validator.iter_errors(accepted)) == []

    missing_command = dict(accepted)
    missing_command.pop("command")
    assert list(validator.iter_errors(missing_command))

    rejected_without_reason = {
        "schema_version": "shipgate.human_authorization_evaluation/v1",
        "status": "rejected",
        "reason_codes": [],
    }
    assert list(validator.iter_errors(rejected_without_reason))

    not_requested_with_authority = {
        "schema_version": "shipgate.human_authorization_evaluation/v1",
        "status": "not_requested",
        "authorization_id": world.grant.authorization_id,
        "reason_codes": [],
    }
    assert list(validator.iter_errors(not_requested_with_authority))

    rejected_with_command = {
        "schema_version": "shipgate.human_authorization_evaluation/v1",
        "status": "rejected",
        "command": world.request.operation.command,
        "reason_codes": ["signature_invalid"],
    }
    assert list(validator.iter_errors(rejected_with_command))


def test_wire_schema_constrains_keys_signatures_and_push_syntax(
    world: AuthorizationWorld,
) -> None:
    proof_validator = Draft202012Validator(_authorization_wire_schema("HumanAuthorizationProofV1"))
    proof = world.grant.proof.model_dump(mode="json")
    assert list(proof_validator.iter_errors(proof)) == []
    assert list(proof_validator.iter_errors({**proof, "signature": "not-base64url"}))

    key_validator = Draft202012Validator(_authorization_wire_schema("TrustedEd25519KeyV1"))
    key = world.trusted_key.model_dump(mode="json")
    assert list(key_validator.iter_errors(key)) == []
    assert list(key_validator.iter_errors({**key, "public_key": "short"}))

    operation_validator = Draft202012Validator(_authorization_wire_schema("GitPushOperationV1"))
    operation = world.request.operation.model_dump(mode="json")
    assert list(operation_validator.iter_errors(operation)) == []
    assert list(
        operation_validator.iter_errors({**operation, "push_url": "http://example.test/repo"})
    )
    assert list(operation_validator.iter_errors({**operation, "destination_ref": "main"}))


@pytest.mark.parametrize(
    "schema_name",
    ["verifier-schema.v0.16.json", "agent-handoff-schema.v9.json"],
)
def test_embedded_authorization_evaluation_schemas_are_fail_closed(
    schema_name: str,
) -> None:
    schema_path = Path(__file__).resolve().parent.parent / "docs" / schema_name
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    evaluation = schema["$defs"]["AuthorizationEvaluationV1"]
    validator = Draft202012Validator(evaluation)
    impossible = {
        "schema_version": "shipgate.human_authorization_evaluation/v1",
        "status": "accepted",
        "reason_codes": [],
    }
    assert list(validator.iter_errors(impossible))
