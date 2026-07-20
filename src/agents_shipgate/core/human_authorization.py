"""Fail-closed verification for externally signed human authorizations.

This module verifies authority; it never creates it.  In particular, it has no
private-key loading or signing API.  The trust policy is fixed outside the
reviewed workspace, and an accepted evaluation exposes only the exact typed
operation covered by the signature.
"""

from __future__ import annotations

import errno
import json
import os
import stat
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import ValidationError

from agents_shipgate.schemas.human_authorization import (
    HUMAN_AUTHORIZATION_SIGNATURE_DOMAIN,
    HUMAN_AUTHORIZATION_TRUST_POLICY_ACCOUNT_PATH,
    AuthorizationEvaluationV1,
    HumanAuthorizationRequestV1,
    HumanAuthorizationReviewItemV1,
    HumanAuthorizationStatementV1,
    HumanAuthorizationTrustPolicyV1,
    HumanAuthorizationV1,
    canonical_review_items,
    decode_ed25519_public_key,
    decode_ed25519_signature,
    review_set_id,
)
from agents_shipgate.schemas.verification_identity import canonical_json

_MAX_TRUST_POLICY_BYTES = 1024 * 1024


class HumanAuthorizationTrustPolicyError(ValueError):
    """A stable fail-closed error raised while loading the external trust root."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def default_human_authorization_trust_policy_path() -> Path:
    """Return the non-workspace, non-environment-overridable default trust path.

    On POSIX, the account database is authoritative rather than ``HOME`` or
    ``XDG_CONFIG_HOME`` because a child coding-agent process can override those
    environment variables.  The fallback exists for non-POSIX platforms only.
    """

    try:
        import pwd

        home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    except (ImportError, KeyError, OSError):  # pragma: no cover - non-POSIX fallback
        home = Path.home()
    relative = HUMAN_AUTHORIZATION_TRUST_POLICY_ACCOUNT_PATH.removeprefix("~/")
    return home.joinpath(*relative.split("/"))


def human_authorization_signature_payload(
    statement: HumanAuthorizationStatementV1,
) -> bytes:
    """Return the domain-separated canonical bytes an external signer signs."""

    return HUMAN_AUTHORIZATION_SIGNATURE_DOMAIN + canonical_json(statement)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def load_external_trust_policy(
    path: Path,
    *,
    workspace: Path,
) -> HumanAuthorizationTrustPolicyV1:
    """Load one fixed trust policy, rejecting workspace-controlled locations."""

    candidate = Path(path)
    if not candidate.is_absolute():
        raise HumanAuthorizationTrustPolicyError(
            "trust_policy_path_not_absolute",
            "human authorization trust policy path must be absolute",
        )

    workspace_lexical = Path(os.path.abspath(workspace))
    candidate_lexical = Path(os.path.abspath(candidate))
    if _is_within(candidate_lexical, workspace_lexical):
        raise HumanAuthorizationTrustPolicyError(
            "trust_policy_inside_workspace",
            "human authorization trust policy must not be stored in the workspace",
        )
    try:
        workspace_resolved = workspace.resolve(strict=True)
    except OSError as exc:
        raise HumanAuthorizationTrustPolicyError(
            "workspace_unavailable",
            "authorization workspace could not be resolved",
        ) from exc
    try:
        raw, metadata = _read_trust_policy_no_follow(candidate)
    except HumanAuthorizationTrustPolicyError:
        raise
    except OSError as exc:
        raise HumanAuthorizationTrustPolicyError(
            "trust_policy_unavailable",
            "human authorization trust policy could not be read",
        ) from exc
    if _is_within(candidate_lexical, workspace_resolved):
        raise HumanAuthorizationTrustPolicyError(
            "trust_policy_inside_workspace",
            "human authorization trust policy must not resolve into the workspace",
        )
    if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise HumanAuthorizationTrustPolicyError(
            "trust_policy_insecure_permissions",
            "human authorization trust policy must not be group/world writable",
        )
    try:
        document = json.loads(raw)
        return HumanAuthorizationTrustPolicyV1.model_validate(document)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise HumanAuthorizationTrustPolicyError(
            "trust_policy_invalid",
            "human authorization trust policy is malformed or fails schema validation",
        ) from exc


def _read_trust_policy_no_follow(path: Path) -> tuple[bytes, os.stat_result]:
    """Read the policy from one no-follow descriptor chain and stable FD."""

    parts = path.parts
    if not parts or parts[0] != os.path.sep:
        raise HumanAuthorizationTrustPolicyError(
            "trust_policy_path_not_absolute",
            "human authorization trust policy path must be absolute",
        )
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptors: list[int] = []
    try:
        current = os.open(os.path.sep, directory_flags)
        descriptors.append(current)
        _validate_trust_path_node(os.fstat(current), label="trust policy root", directory=True)
        for part in parts[1:-1]:
            current = os.open(part, directory_flags, dir_fd=current)
            descriptors.append(current)
            _validate_trust_path_node(
                os.fstat(current),
                label="trust policy ancestor",
                directory=True,
            )
        file_descriptor = os.open(parts[-1], file_flags, dir_fd=current)
        descriptors.append(file_descriptor)
        before = os.fstat(file_descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise HumanAuthorizationTrustPolicyError(
                "trust_policy_not_regular_file",
                "human authorization trust policy must be a regular file",
            )
        _validate_trust_path_node(before, label="trust policy file", directory=False)
        if before.st_nlink != 1:
            raise HumanAuthorizationTrustPolicyError(
                "trust_policy_hard_linked",
                "human authorization trust policy must have exactly one hard link",
            )
        if before.st_size > _MAX_TRUST_POLICY_BYTES:
            raise HumanAuthorizationTrustPolicyError(
                "trust_policy_too_large",
                "human authorization trust policy exceeds the size limit",
            )
        chunks: list[bytes] = []
        remaining = _MAX_TRUST_POLICY_BYTES + 1
        while remaining:
            chunk = os.read(file_descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(file_descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise HumanAuthorizationTrustPolicyError(
                "trust_policy_changed_during_read",
                "human authorization trust policy changed while it was read",
            )
        if len(raw) > _MAX_TRUST_POLICY_BYTES:
            raise HumanAuthorizationTrustPolicyError(
                "trust_policy_too_large",
                "human authorization trust policy exceeds the size limit",
            )
        return raw, before
    except OSError as exc:
        code = (
            "trust_policy_symlink"
            if exc.errno == errno.ELOOP
            else "trust_policy_unavailable"
        )
        raise HumanAuthorizationTrustPolicyError(
            code,
            "human authorization trust policy path is unavailable or contains a symlink",
        ) from exc
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _validate_trust_path_node(
    metadata: os.stat_result,
    *,
    label: str,
    directory: bool,
) -> None:
    expected_type = stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode)
    if not expected_type:
        raise HumanAuthorizationTrustPolicyError(
            "trust_policy_path_type_invalid",
            f"{label} has an invalid filesystem type",
        )
    # A POSIX sticky directory lets untrusted users create siblings but not
    # unlink or rename a path component owned by root or the broker account.
    # The ownership check immediately below remains mandatory, and regular
    # files never receive this exception.
    trusted_sticky_directory = directory and bool(metadata.st_mode & stat.S_ISVTX)
    if (
        metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        and not trusted_sticky_directory
    ):
        raise HumanAuthorizationTrustPolicyError(
            "trust_policy_insecure_ancestor" if directory else "trust_policy_insecure_permissions",
            f"{label} must not be group/world writable",
        )
    effective_uid = os.geteuid() if hasattr(os, "geteuid") else None
    if effective_uid is not None and metadata.st_uid not in {0, effective_uid}:
        raise HumanAuthorizationTrustPolicyError(
            "trust_policy_owner_mismatch",
            f"{label} must be owned by root or the broker account",
        )


def _utc_now(now: datetime | None) -> datetime:
    value = now if now is not None else datetime.now(UTC)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("authorization evaluation clock must be timezone-aware")
    return value.astimezone(UTC)


def _rejected(
    reasons: Sequence[str],
    *,
    authorization: HumanAuthorizationV1 | None = None,
    trust_policy: HumanAuthorizationTrustPolicyV1 | None = None,
) -> AuthorizationEvaluationV1:
    statement = authorization.statement if authorization is not None else None
    request = statement.request if statement is not None else None
    proof = authorization.proof if authorization is not None else None
    principal = statement.principal if statement is not None else None
    return AuthorizationEvaluationV1.rejected(
        *sorted(set(reasons)),
        authorization_id=(authorization.authorization_id if authorization else None),
        authorization_request_id=(request.authorization_request_id if request else None),
        trust_policy_id=(trust_policy.trust_policy_id if trust_policy else None),
        key_id=(proof.key_id if proof else None),
        provider=(principal.provider if principal else None),
        principal=(principal.subject if principal else None),
        operation_id=(request.operation.operation_id if request else None),
        issued_at=(statement.issued_at if statement else None),
        expires_at=(statement.expires_at if statement else None),
    )


def evaluate_human_authorization(
    authorization: HumanAuthorizationV1 | Mapping[str, Any],
    *,
    trust_policy_path: Path,
    workspace: Path,
    expected_request: HumanAuthorizationRequestV1 | Mapping[str, Any],
    expected_review_items: Sequence[HumanAuthorizationReviewItemV1 | Mapping[str, Any]],
    now: datetime | None = None,
) -> AuthorizationEvaluationV1:
    """Verify an exact signed request against one external fixed trust policy.

    All input or trust failures return ``status='rejected'`` with no command.
    Only a fully valid, current, exact-match authorization returns the one
    rendered command covered by its typed ``git_push`` operation.
    """

    try:
        grant = (
            authorization
            if isinstance(authorization, HumanAuthorizationV1)
            else HumanAuthorizationV1.model_validate(authorization)
        )
    except (ValidationError, TypeError, ValueError):
        return _rejected(["authorization_malformed"])

    try:
        expected = (
            expected_request
            if isinstance(expected_request, HumanAuthorizationRequestV1)
            else HumanAuthorizationRequestV1.model_validate(expected_request)
        )
        expected_items = canonical_review_items(expected_review_items)
    except (ValidationError, TypeError, ValueError):
        return _rejected(["expected_authorization_context_invalid"], authorization=grant)

    try:
        policy = load_external_trust_policy(trust_policy_path, workspace=workspace)
    except HumanAuthorizationTrustPolicyError as exc:
        return _rejected([exc.code], authorization=grant)

    request = grant.statement.request
    reasons: list[str] = []

    expected_scope_id = review_set_id(expected_items)
    if expected.review_set_id != expected_scope_id:
        reasons.append("expected_review_scope_invalid")
    if request.review_set_id != expected_scope_id:
        reasons.append("review_scope_mismatch")
    if request.authorization_request_id != expected.authorization_request_id:
        reasons.append("authorization_request_id_mismatch")
    if request.model_dump(mode="json") != expected.model_dump(mode="json"):
        reasons.append("authorization_request_mismatch")
    if request.repository_id not in policy.repository_ids:
        reasons.append("repository_not_trusted")

    trusted_key = next(
        (key for key in policy.keys if key.key_id == grant.proof.key_id),
        None,
    )
    if trusted_key is None:
        reasons.append("signing_key_not_trusted")
    else:
        if trusted_key.provider != grant.statement.principal.provider:
            reasons.append("principal_provider_mismatch")
        if trusted_key.principal != grant.statement.principal.subject:
            reasons.append("principal_subject_mismatch")
        try:
            public_key = Ed25519PublicKey.from_public_bytes(
                decode_ed25519_public_key(trusted_key.public_key)
            )
            public_key.verify(
                decode_ed25519_signature(grant.proof.signature),
                human_authorization_signature_payload(grant.statement),
            )
        except (InvalidSignature, ValueError):
            reasons.append("signature_invalid")

    try:
        evaluated_at = _utc_now(now)
    except ValueError:
        reasons.append("evaluation_clock_invalid")
        evaluated_at = None

    if evaluated_at is not None:
        skew = timedelta(seconds=policy.clock_skew_seconds)
        statement = grant.statement
        if statement.issued_at > evaluated_at + skew:
            reasons.append("authorization_issued_in_future")
        if statement.not_before > evaluated_at + skew:
            reasons.append("authorization_not_yet_valid")
        # Expiry is deliberately strict: clock skew never extends authority.
        if evaluated_at >= statement.expires_at:
            reasons.append("authorization_expired")
        ttl = (statement.expires_at - statement.issued_at).total_seconds()
        if ttl > policy.max_ttl_seconds:
            reasons.append("authorization_ttl_exceeded")

        if trusted_key is not None:
            if trusted_key.valid_from is not None:
                if statement.issued_at < trusted_key.valid_from:
                    reasons.append("signing_key_not_yet_valid")
                if evaluated_at + skew < trusted_key.valid_from:
                    reasons.append("signing_key_not_yet_valid")
            if trusted_key.valid_until is not None:
                if statement.expires_at > trusted_key.valid_until:
                    reasons.append("signing_key_validity_exceeded")
                if evaluated_at >= trusted_key.valid_until:
                    reasons.append("signing_key_expired")

    if reasons:
        return _rejected(reasons, authorization=grant, trust_policy=policy)

    return AuthorizationEvaluationV1(
        status="accepted",
        authorization_id=grant.authorization_id,
        authorization_request_id=request.authorization_request_id,
        trust_policy_id=policy.trust_policy_id,
        key_id=grant.proof.key_id,
        provider=grant.statement.principal.provider,
        principal=grant.statement.principal.subject,
        operation_id=request.operation.operation_id,
        command=request.operation.command,
        issued_at=grant.statement.issued_at,
        expires_at=grant.statement.expires_at,
        reason_codes=[],
    )


__all__ = [
    "HumanAuthorizationTrustPolicyError",
    "default_human_authorization_trust_policy_path",
    "evaluate_human_authorization",
    "human_authorization_signature_payload",
    "load_external_trust_policy",
]
