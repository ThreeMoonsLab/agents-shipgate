"""Signed human authorization artifacts for exact coding-agent operations.

These models deliberately keep human authority outside the repository being
reviewed.  A request is content-addressed but unsigned; an authorization binds
that exact request to one authenticated principal and one short validity
window.  The detached Ed25519 proof is verified by
``agents_shipgate.core.human_authorization`` against an external trust policy.
"""

from __future__ import annotations

import base64
import hashlib
import re
import shlex
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from agents_shipgate.schemas.verification_identity import (
    CONTENT_ID_PATTERN,
    GIT_OBJECT_PATTERN,
    content_id,
)

HUMAN_AUTHORIZATION_REQUEST_SCHEMA_VERSION = "shipgate.human_authorization_request/v1"
HUMAN_AUTHORIZATION_SCHEMA_VERSION = "shipgate.human_authorization/v1"
HUMAN_AUTHORIZATION_EVALUATION_SCHEMA_VERSION = "shipgate.human_authorization_evaluation/v1"
HUMAN_AUTHORIZATION_TRUST_POLICY_SCHEMA_VERSION = "shipgate.human_authorization_trust_policy/v1"
HUMAN_AUTHORIZATION_SCHEMA_PATH = "docs/human-authorization-schema.v1.json"
HUMAN_AUTHORIZATION_TRUST_POLICY_ACCOUNT_PATH = (
    "~/.config/agents-shipgate/human-authorization-trust-policy.json"
)
HUMAN_AUTHORIZATION_SIGNATURE_DOMAIN = b"shipgate.human_authorization/v1\x00"

_STRICT = ConfigDict(extra="forbid")
_BASE64URL_PATTERN = r"^[A-Za-z0-9_-]+$"
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_SHELL_UNSAFE_RE = re.compile(r"[\x00\r\n;&|`$<>(){}!]")
_HTTPS_ENDPOINT_RE = re.compile(r"^[A-Za-z0-9._~+:/-]+$")
_HOST_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_REPOSITORY_COMPONENT_RE = re.compile(r"^[A-Za-z0-9._~+-]+$")

NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]
Ed25519PublicKeyText = Annotated[
    str,
    StringConstraints(
        min_length=43,
        max_length=43,
        pattern=r"^[A-Za-z0-9_-]{43}$",
    ),
]
Ed25519SignatureText = Annotated[
    str,
    StringConstraints(
        min_length=86,
        max_length=86,
        pattern=r"^[A-Za-z0-9_-]{86}$",
    ),
]
CanonicalHttpsGitEndpointText = Annotated[
    str,
    StringConstraints(
        min_length=1,
        pattern=(
            r"^https://[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?/"
            r"(?:[A-Za-z0-9._~+-]+/)*[A-Za-z0-9._~+-]+$"
        ),
    ),
]
FullHeadsRefText = Annotated[
    str,
    StringConstraints(
        pattern=(
            r"^refs/heads/(?:[A-Za-z0-9]|"
            r"[A-Za-z0-9][A-Za-z0-9._/-]*[A-Za-z0-9])$"
        )
    ),
]


def _identity_payload(model: BaseModel, identity_field: str) -> dict[str, Any]:
    return model.model_dump(
        mode="json",
        exclude={identity_field, "schema_version"},
        exclude_none=False,
    )


def _decode_base64url(value: str, *, expected_size: int, label: str) -> bytes:
    if not re.fullmatch(_BASE64URL_PATTERN, value):
        raise ValueError(f"{label} must be unpadded base64url")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{label} must be valid base64url") from exc
    if len(decoded) != expected_size:
        raise ValueError(f"{label} must decode to exactly {expected_size} bytes")
    canonical = base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=")
    if canonical != value:
        raise ValueError(f"{label} must use canonical unpadded base64url")
    return decoded


def decode_ed25519_public_key(value: str) -> bytes:
    """Decode one canonical raw Ed25519 public key."""

    return _decode_base64url(value, expected_size=32, label="public_key")


def decode_ed25519_signature(value: str) -> bytes:
    """Decode one canonical detached Ed25519 signature."""

    return _decode_base64url(value, expected_size=64, label="signature")


def ed25519_key_id(public_key: bytes) -> str:
    """Return the stable key identifier used by trust policies and proofs."""

    if len(public_key) != 32:
        raise ValueError("Ed25519 public keys must contain exactly 32 raw bytes")
    return f"sha256:{hashlib.sha256(public_key).hexdigest()}"


def _utc(value: datetime, *, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must include a UTC offset")
    return value.astimezone(UTC)


def _inert_text(value: str, *, label: str) -> str:
    """Validate display-only metadata without imposing shell token rules."""

    if _CONTROL_RE.search(value):
        raise ValueError(f"{label} contains a control character")
    return value


def _safe_shell_token(value: str, *, label: str) -> str:
    """Reject shell syntax in values that are rendered into an operation."""

    if _SHELL_UNSAFE_RE.search(value):
        raise ValueError(f"{label} contains a control character or shell metacharacter")
    return value


def _portable_path(value: str) -> str:
    _inert_text(value, label="path")
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or path.is_absolute()
        or ".." in path.parts
        or path.as_posix() != value
    ):
        raise ValueError("paths must be normalized portable repository-relative paths")
    return value


def _git_destination_ref(value: str) -> str:
    _safe_shell_token(value, label="destination_ref")
    if not value.startswith("refs/heads/"):
        raise ValueError("destination_ref must be a full refs/heads/* ref")
    if any(character.isspace() or ord(character) < 32 for character in value):
        raise ValueError("destination_ref must not contain whitespace or controls")
    if any(token in value for token in ("..", "@{", "//")):
        raise ValueError("destination_ref is not a valid normalized Git ref")
    if any(character in value for character in "~^:?*[\\"):
        raise ValueError("destination_ref contains a Git-ref metacharacter")
    parts = value.split("/")
    if any(
        not part
        or part in {".", ".."}
        or part.startswith(".")
        or part.endswith(".")
        or part.endswith(".lock")
        for part in parts
    ):
        raise ValueError("destination_ref contains an invalid Git-ref component")
    return value


def canonical_https_git_endpoint(value: str) -> tuple[str, str]:
    """Return one canonical HTTPS push URL and its credential-free repo ID.

    Human authorization v1 intentionally accepts only conventional HTTPS
    repository endpoints.  Local paths, ``file:`` locators, SSH/scp syntax,
    embedded credentials, and query/fragment state are not stable enough to
    carry execution authority.  The repository identity excludes transport
    syntax and the conventional ``.git`` suffix, while the URL remains the
    concrete destination used by the exact operation.
    """

    if not isinstance(value, str) or not value:
        raise ValueError("push_url must be a non-empty HTTPS URL")
    if not value.isascii() or _CONTROL_RE.search(value) or not _HTTPS_ENDPOINT_RE.fullmatch(value):
        raise ValueError("push_url contains an unsafe or non-ASCII token")
    parsed = urlsplit(value)
    if parsed.scheme.lower() != "https":
        raise ValueError("push_url must use the https scheme")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("push_url must not embed credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("push_url must not contain a query or fragment")
    host = (parsed.hostname or "").lower()
    if not host or len(host) > 253 or any(
        not _HOST_LABEL_RE.fullmatch(label) for label in host.split(".")
    ):
        raise ValueError("push_url must contain a valid DNS host")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("push_url contains an invalid port") from exc
    if port not in {None, 443}:
        raise ValueError("push_url must use the default HTTPS port")
    if parsed.path.endswith("/"):
        raise ValueError("push_url repository path must not end with a slash")
    components = parsed.path.removeprefix("/").split("/")
    if (
        not parsed.path.startswith("/")
        or not components
        or any(
            not component
            or component in {".", ".."}
            or not _REPOSITORY_COMPONENT_RE.fullmatch(component)
            for component in components
        )
    ):
        raise ValueError("push_url must contain a normalized repository path")
    canonical_url = f"https://{host}/{'/'.join(components)}"
    repository_components = list(components)
    if repository_components[-1].endswith(".git"):
        repository_components[-1] = repository_components[-1][:-4]
    if not repository_components[-1]:
        raise ValueError("push_url must name a repository")
    repository_id = f"{host}/{'/'.join(repository_components)}"
    return canonical_url, repository_id


class HumanAuthorizationReviewItemV1(BaseModel):
    """One complete reviewer obligation included in the signed review set."""

    model_config = _STRICT

    review_item_id: NonEmptyText
    check_id: NonEmptyText
    fingerprint: NonEmptyText | None = None
    support_hash: NonEmptyText | None = None
    paths: list[str] = Field(default_factory=list)

    @field_validator("review_item_id", "check_id", "fingerprint", "support_hash")
    @classmethod
    def _text_is_inert(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        return _inert_text(value, label=info.field_name)

    @field_validator("paths")
    @classmethod
    def _paths_are_portable(cls, value: list[str]) -> list[str]:
        normalized = [_portable_path(path) for path in value]
        if normalized != sorted(set(normalized)):
            raise ValueError("review item paths must be sorted and unique")
        return normalized


def canonical_review_items(
    items: Sequence[HumanAuthorizationReviewItemV1 | Mapping[str, Any]],
) -> list[HumanAuthorizationReviewItemV1]:
    """Validate and deterministically order a complete review-item set."""

    validated = [
        item
        if isinstance(item, HumanAuthorizationReviewItemV1)
        else HumanAuthorizationReviewItemV1.model_validate(item)
        for item in items
    ]
    ordered = sorted(
        validated,
        key=lambda item: (
            item.review_item_id,
            item.check_id,
            item.fingerprint or "",
            item.support_hash or "",
            tuple(item.paths),
        ),
    )
    ids = [item.review_item_id for item in ordered]
    if len(ids) != len(set(ids)):
        raise ValueError("review_item_id values must be unique")
    return ordered


def review_set_id(
    items: Sequence[HumanAuthorizationReviewItemV1 | Mapping[str, Any]],
) -> str:
    """Hash the full, ordered review scope an authorizer inspected."""

    ordered = canonical_review_items(items)
    return content_id(
        {
            "review_items": [item.model_dump(mode="json") for item in ordered],
        }
    )


def authorization_review_items(
    release_decision: Mapping[str, Any],
) -> list[HumanAuthorizationReviewItemV1]:
    """Project the one closed review set used by request and verifier paths.

    The projection intentionally binds stable item/check/fingerprint/support
    identity plus every source and policy-source path.  It rejects incomplete
    structures instead of silently dropping fields, so request construction
    and later authorization evaluation cannot disagree about review scope.
    """

    if not isinstance(release_decision, Mapping):
        raise ValueError("release_decision must be one object")
    if release_decision.get("decision") != "review_required":
        raise ValueError("human authorization requires decision='review_required'")
    raw_items = release_decision.get("review_items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("review_required decision must carry a non-empty review_items set")

    projected: list[HumanAuthorizationReviewItemV1] = []
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, Mapping):
            raise ValueError(f"review_items[{index}] must be one object")
        review_item_id = raw.get("id")
        check_id = raw.get("check_id")
        fingerprint = raw.get("fingerprint")
        if not isinstance(review_item_id, str) or not review_item_id.strip():
            raise ValueError(f"review_items[{index}].id must be a non-empty string")
        if not isinstance(check_id, str) or not check_id.strip():
            raise ValueError(f"review_items[{index}].check_id must be a non-empty string")
        if fingerprint is not None and not isinstance(fingerprint, str):
            raise ValueError(f"review_items[{index}].fingerprint must be a string or null")

        support = raw.get("support")
        if support is not None and not isinstance(support, Mapping):
            raise ValueError(f"review_items[{index}].support must be an object or null")
        support_hash = support.get("support_hash") if support is not None else None
        if support_hash is not None and not isinstance(support_hash, str):
            raise ValueError(f"review_items[{index}].support.support_hash must be a string or null")

        paths: list[str] = []
        for source_name in ("source", "policy_evidence_source"):
            source = raw.get(source_name)
            if source is None:
                continue
            if not isinstance(source, Mapping):
                raise ValueError(f"review_items[{index}].{source_name} must be an object or null")
            if "path" not in source:
                raise ValueError(
                    f"review_items[{index}].{source_name}.path must be present"
                )
            path = source.get("path")
            if path is None:
                continue
            if not isinstance(path, str) or not path:
                raise ValueError(
                    f"review_items[{index}].{source_name}.path must be a non-empty string or null"
                )
            paths.append(path)

        projected.append(
            HumanAuthorizationReviewItemV1(
                review_item_id=review_item_id,
                check_id=check_id,
                fingerprint=fingerprint,
                support_hash=support_hash,
                paths=sorted(set(paths)),
            )
        )
    return canonical_review_items(projected)


class GitPushOperationV1(BaseModel):
    """One exact force-with-lease push; no shell or wildcard semantics."""

    model_config = _STRICT

    operation_id: str = Field(pattern=CONTENT_ID_PATTERN)
    kind: Literal["git_push"] = "git_push"
    destination_repository_id: str = Field(min_length=1)
    push_url: CanonicalHttpsGitEndpointText
    source_commit_sha: str = Field(pattern=GIT_OBJECT_PATTERN)
    destination_ref: FullHeadsRefText
    expected_lease_oid: str = Field(pattern=GIT_OBJECT_PATTERN)
    force_with_lease: Literal[True] = True

    _destination_is_valid = field_validator("destination_ref")(_git_destination_ref)

    @model_validator(mode="after")
    def _operation_id_is_content_addressed(self) -> GitPushOperationV1:
        canonical_url, repository_id = canonical_https_git_endpoint(self.push_url)
        if self.push_url != canonical_url:
            raise ValueError("push_url must use its canonical HTTPS representation")
        if self.destination_repository_id != repository_id:
            raise ValueError(
                "destination_repository_id must match the credential-free push_url identity"
            )
        expected = content_id(_identity_payload(self, "operation_id"))
        if self.operation_id != expected:
            raise ValueError("operation_id must hash the exact typed operation")
        return self

    def argv(self) -> list[str]:
        """Return the only command vector authorized by this operation."""

        return [
            "git",
            "push",
            f"--force-with-lease={self.destination_ref}:{self.expected_lease_oid}",
            self.push_url,
            f"{self.source_commit_sha}:{self.destination_ref}",
        ]

    @property
    def command(self) -> str:
        return shlex.join(self.argv())


def build_git_push_operation(
    *,
    destination_repository_id: str,
    push_url: str,
    source_commit_sha: str,
    destination_ref: str,
    expected_lease_oid: str,
) -> GitPushOperationV1:
    """Build a content-addressed exact force-with-lease operation."""

    canonical_url, derived_repository_id = canonical_https_git_endpoint(push_url)
    if destination_repository_id != derived_repository_id:
        raise ValueError(
            "destination_repository_id must equal the repository identity derived from push_url"
        )
    payload = {
        "kind": "git_push",
        "destination_repository_id": destination_repository_id,
        "push_url": canonical_url,
        "source_commit_sha": source_commit_sha,
        "destination_ref": destination_ref,
        "expected_lease_oid": expected_lease_oid,
        "force_with_lease": True,
    }
    return GitPushOperationV1(operation_id=content_id(payload), **payload)


class HumanAuthorizationRequestV1(BaseModel):
    """Unsigned, content-addressed challenge shown by a trusted host to a human."""

    model_config = _STRICT

    schema_version: Literal["shipgate.human_authorization_request/v1"] = (
        HUMAN_AUTHORIZATION_REQUEST_SCHEMA_VERSION
    )
    authorization_request_id: str = Field(pattern=CONTENT_ID_PATTERN)
    repository_id: str = Field(min_length=1)
    source_receipt_id: str = Field(pattern=CONTENT_ID_PATTERN)
    source_artifact_set_id: str = Field(pattern=CONTENT_ID_PATTERN)
    source_engine_requirement_id: str = Field(pattern=CONTENT_ID_PATTERN)
    source_executor_id: str = Field(pattern=CONTENT_ID_PATTERN)
    verification_request_id: str = Field(pattern=CONTENT_ID_PATTERN)
    subject_id: str = Field(pattern=CONTENT_ID_PATTERN)
    decision_id: str = Field(pattern=CONTENT_ID_PATTERN)
    decision: Literal["review_required"] = "review_required"
    base_commit_sha: str = Field(pattern=GIT_OBJECT_PATTERN)
    merge_base_sha: str = Field(pattern=GIT_OBJECT_PATTERN)
    base_tree_sha: str = Field(pattern=GIT_OBJECT_PATTERN)
    head_tree_sha: str = Field(pattern=GIT_OBJECT_PATTERN)
    source_head_commit_sha: str = Field(pattern=GIT_OBJECT_PATTERN)
    worktree_overlay_sha256: None = None
    review_set_id: str = Field(pattern=CONTENT_ID_PATTERN)
    review_items: list[HumanAuthorizationReviewItemV1] = Field(min_length=1)
    operation: GitPushOperationV1

    @field_validator("repository_id")
    @classmethod
    def _repository_id_is_inert(cls, value: str) -> str:
        _inert_text(value, label="repository_id")
        if value != value.strip():
            raise ValueError("repository_id must not contain surrounding whitespace")
        return value

    @model_validator(mode="after")
    def _request_is_one_complete_identity(self) -> HumanAuthorizationRequestV1:
        self.review_items = canonical_review_items(self.review_items)
        if self.review_set_id != review_set_id(self.review_items):
            raise ValueError("review_set_id must hash the complete review_items list")
        if self.operation.source_commit_sha != self.source_head_commit_sha:
            raise ValueError("git_push source must equal the reviewed source head commit")
        if self.operation.destination_repository_id != self.repository_id:
            raise ValueError(
                "git_push destination repository must equal the reviewed repository identity"
            )
        expected = content_id(_identity_payload(self, "authorization_request_id"))
        if self.authorization_request_id != expected:
            raise ValueError("authorization_request_id must hash the complete request")
        return self


def build_human_authorization_request(
    *,
    repository_id: str,
    source_receipt_id: str,
    source_artifact_set_id: str,
    source_engine_requirement_id: str,
    source_executor_id: str,
    verification_request_id: str,
    subject_id: str,
    decision_id: str,
    base_commit_sha: str,
    merge_base_sha: str,
    base_tree_sha: str,
    head_tree_sha: str,
    source_head_commit_sha: str,
    review_items: Sequence[HumanAuthorizationReviewItemV1 | Mapping[str, Any]],
    operation: GitPushOperationV1,
) -> HumanAuthorizationRequestV1:
    """Build the exact unsigned challenge a host may present for approval."""

    ordered = canonical_review_items(review_items)
    payload = {
        "repository_id": repository_id,
        "source_receipt_id": source_receipt_id,
        "source_artifact_set_id": source_artifact_set_id,
        "source_engine_requirement_id": source_engine_requirement_id,
        "source_executor_id": source_executor_id,
        "verification_request_id": verification_request_id,
        "subject_id": subject_id,
        "decision_id": decision_id,
        "decision": "review_required",
        "base_commit_sha": base_commit_sha,
        "merge_base_sha": merge_base_sha,
        "base_tree_sha": base_tree_sha,
        "head_tree_sha": head_tree_sha,
        "source_head_commit_sha": source_head_commit_sha,
        "worktree_overlay_sha256": None,
        "review_set_id": review_set_id(ordered),
        "review_items": ordered,
        "operation": operation,
    }
    identity_payload = {
        key: (
            value.model_dump(mode="json")
            if isinstance(value, BaseModel)
            else [item.model_dump(mode="json") for item in value]
            if isinstance(value, list)
            else value
        )
        for key, value in payload.items()
    }
    return HumanAuthorizationRequestV1(
        authorization_request_id=content_id(identity_payload),
        **payload,
    )


class HumanAuthorizationPrincipalV1(BaseModel):
    model_config = _STRICT

    provider: Literal["codex", "claude-code", "github", "local-hardware"]
    subject: NonEmptyText

    @field_validator("subject")
    @classmethod
    def _subject_is_inert(cls, value: str) -> str:
        return _inert_text(value, label="principal subject")


class HumanAuthorizationStatementV1(BaseModel):
    """Canonical payload covered by the detached signature."""

    model_config = _STRICT

    request: HumanAuthorizationRequestV1
    principal: HumanAuthorizationPrincipalV1
    reason: NonEmptyText
    issued_at: datetime
    not_before: datetime
    expires_at: datetime
    nonce: str = Field(min_length=22, max_length=128, pattern=_BASE64URL_PATTERN)

    @field_validator("reason")
    @classmethod
    def _reason_is_inert(cls, value: str) -> str:
        return _inert_text(value, label="reason")

    @field_validator("nonce")
    @classmethod
    def _nonce_has_entropy(cls, value: str) -> str:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        if len(decoded) < 16:
            raise ValueError("nonce must contain at least 128 bits")
        return value

    @field_validator("issued_at", "not_before", "expires_at")
    @classmethod
    def _times_are_utc(cls, value: datetime, info) -> datetime:
        return _utc(value, label=info.field_name)

    @model_validator(mode="after")
    def _time_window_is_ordered(self) -> HumanAuthorizationStatementV1:
        if self.issued_at > self.not_before:
            raise ValueError("issued_at must not be after not_before")
        if self.not_before >= self.expires_at:
            raise ValueError("not_before must be before expires_at")
        return self


class HumanAuthorizationProofV1(BaseModel):
    model_config = _STRICT

    algorithm: Literal["ed25519"] = "ed25519"
    key_id: str = Field(pattern=CONTENT_ID_PATTERN)
    signature: Ed25519SignatureText

    @field_validator("signature")
    @classmethod
    def _signature_is_canonical(cls, value: str) -> str:
        decode_ed25519_signature(value)
        return value


class HumanAuthorizationV1(BaseModel):
    """Signed authorization for exactly one request and operation."""

    model_config = _STRICT

    schema_version: Literal["shipgate.human_authorization/v1"] = HUMAN_AUTHORIZATION_SCHEMA_VERSION
    authorization_id: str = Field(pattern=CONTENT_ID_PATTERN)
    statement: HumanAuthorizationStatementV1
    proof: HumanAuthorizationProofV1

    @model_validator(mode="after")
    def _authorization_id_is_content_addressed(self) -> HumanAuthorizationV1:
        expected = content_id(
            {
                "statement": self.statement.model_dump(mode="json"),
                "proof": self.proof.model_dump(mode="json"),
            }
        )
        if self.authorization_id != expected:
            raise ValueError("authorization_id must hash the complete signed grant")
        return self


def human_authorization_id(
    statement: HumanAuthorizationStatementV1,
    proof: HumanAuthorizationProofV1,
) -> str:
    """Hash the complete signed grant, including proof and signing-key identity."""

    return content_id(
        {
            "statement": statement.model_dump(mode="json"),
            "proof": proof.model_dump(mode="json"),
        }
    )


def build_human_authorization(
    *,
    statement: HumanAuthorizationStatementV1,
    proof: HumanAuthorizationProofV1,
) -> HumanAuthorizationV1:
    """Assemble a grant from an externally produced detached signature.

    This helper never accepts a private key and never signs.  The signature is
    produced by the trusted host or human authenticator outside Shipgate.
    """

    return HumanAuthorizationV1(
        authorization_id=human_authorization_id(statement, proof),
        statement=statement,
        proof=proof,
    )


class TrustedEd25519KeyV1(BaseModel):
    """One host/admin-controlled key and the identity it is allowed to assert."""

    model_config = _STRICT

    key_id: str = Field(pattern=CONTENT_ID_PATTERN)
    public_key: Ed25519PublicKeyText
    provider: Literal["codex", "claude-code", "github", "local-hardware"]
    principal: NonEmptyText
    valid_from: datetime | None = None
    valid_until: datetime | None = None

    @field_validator("public_key")
    @classmethod
    def _key_is_canonical(cls, value: str) -> str:
        decode_ed25519_public_key(value)
        return value

    @field_validator("principal")
    @classmethod
    def _principal_is_inert(cls, value: str) -> str:
        return _inert_text(value, label="trusted principal")

    @field_validator("valid_from", "valid_until")
    @classmethod
    def _key_times_are_utc(cls, value: datetime | None, info) -> datetime | None:
        return _utc(value, label=info.field_name) if value is not None else None

    @model_validator(mode="after")
    def _key_identity_matches_bytes(self) -> TrustedEd25519KeyV1:
        raw = decode_ed25519_public_key(self.public_key)
        if self.key_id != ed25519_key_id(raw):
            raise ValueError("key_id must be the SHA-256 digest of the raw public key")
        if (
            self.valid_from is not None
            and self.valid_until is not None
            and self.valid_from >= self.valid_until
        ):
            raise ValueError("trusted key valid_from must be before valid_until")
        return self


class HumanAuthorizationTrustPolicyV1(BaseModel):
    """Fixed external trust policy; never valid merely because it is in the repo."""

    model_config = _STRICT

    schema_version: Literal["shipgate.human_authorization_trust_policy/v1"] = (
        HUMAN_AUTHORIZATION_TRUST_POLICY_SCHEMA_VERSION
    )
    trust_policy_id: str = Field(pattern=CONTENT_ID_PATTERN)
    repository_ids: list[NonEmptyText] = Field(min_length=1)
    max_ttl_seconds: int = Field(default=900, ge=1, le=86400)
    clock_skew_seconds: int = Field(default=30, ge=0, le=300)
    keys: list[TrustedEd25519KeyV1] = Field(min_length=1)

    @model_validator(mode="after")
    def _policy_is_fixed_and_content_addressed(self) -> HumanAuthorizationTrustPolicyV1:
        for repository_id in self.repository_ids:
            _inert_text(repository_id, label="repository_id")
        if self.repository_ids != sorted(set(self.repository_ids)):
            raise ValueError("repository_ids must be sorted and unique")
        self.keys = sorted(self.keys, key=lambda key: key.key_id)
        key_ids = [key.key_id for key in self.keys]
        if len(key_ids) != len(set(key_ids)):
            raise ValueError("trusted key_id values must be unique")
        expected = content_id(_identity_payload(self, "trust_policy_id"))
        if self.trust_policy_id != expected:
            raise ValueError("trust_policy_id must hash the complete fixed policy")
        return self


def build_human_authorization_trust_policy(
    *,
    repository_ids: Sequence[str],
    keys: Sequence[TrustedEd25519KeyV1 | Mapping[str, Any]],
    max_ttl_seconds: int = 900,
    clock_skew_seconds: int = 30,
) -> HumanAuthorizationTrustPolicyV1:
    """Build a content-addressed policy for storage outside the workspace."""

    normalized_repositories = sorted(set(repository_ids))
    normalized_keys = sorted(
        [
            key if isinstance(key, TrustedEd25519KeyV1) else TrustedEd25519KeyV1.model_validate(key)
            for key in keys
        ],
        key=lambda key: key.key_id,
    )
    payload = {
        "repository_ids": normalized_repositories,
        "max_ttl_seconds": max_ttl_seconds,
        "clock_skew_seconds": clock_skew_seconds,
        "keys": [key.model_dump(mode="json") for key in normalized_keys],
    }
    return HumanAuthorizationTrustPolicyV1(
        trust_policy_id=content_id(payload),
        repository_ids=normalized_repositories,
        max_ttl_seconds=max_ttl_seconds,
        clock_skew_seconds=clock_skew_seconds,
        keys=normalized_keys,
    )


AuthorizationEvaluationStatus = Literal[
    "not_requested",
    "accepted",
    "rejected",
    "not_applicable",
]


class AuthorizationEvaluationV1(BaseModel):
    """Fail-closed authorization evaluation consumed by verifier projections."""

    model_config = _STRICT

    schema_version: Literal["shipgate.human_authorization_evaluation/v1"] = (
        HUMAN_AUTHORIZATION_EVALUATION_SCHEMA_VERSION
    )
    status: AuthorizationEvaluationStatus
    authorization_id: str | None = Field(default=None, pattern=CONTENT_ID_PATTERN)
    authorization_request_id: str | None = Field(default=None, pattern=CONTENT_ID_PATTERN)
    trust_policy_id: str | None = Field(default=None, pattern=CONTENT_ID_PATTERN)
    key_id: str | None = Field(default=None, pattern=CONTENT_ID_PATTERN)
    provider: str | None = None
    principal: str | None = None
    operation_id: str | None = Field(default=None, pattern=CONTENT_ID_PATTERN)
    command: str | None = None
    issued_at: datetime | None = None
    expires_at: datetime | None = None
    reason_codes: list[str] = Field(default_factory=list)

    @field_validator("issued_at", "expires_at")
    @classmethod
    def _evaluation_times_are_utc(cls, value: datetime | None, info) -> datetime | None:
        return _utc(value, label=info.field_name) if value is not None else None

    @field_validator("provider", "principal", "command")
    @classmethod
    def _evaluation_text_is_inert(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError(f"{info.field_name} must not be blank")
        if "\x00" in value or "\r" in value or "\n" in value:
            raise ValueError(f"{info.field_name} contains a control character")
        return value

    @field_validator("reason_codes")
    @classmethod
    def _reason_codes_are_normalized(cls, value: list[str]) -> list[str]:
        if any(not item or not item.strip() for item in value):
            raise ValueError("reason_codes must be non-empty strings")
        if value != sorted(set(value)):
            raise ValueError("reason_codes must be sorted and unique")
        return value

    @model_validator(mode="after")
    def _status_controls_authority(self) -> AuthorizationEvaluationV1:
        authority = (
            self.authorization_id,
            self.authorization_request_id,
            self.trust_policy_id,
            self.key_id,
            self.provider,
            self.principal,
            self.operation_id,
            self.command,
            self.issued_at,
            self.expires_at,
        )
        if self.status == "accepted":
            if not all(value is not None for value in authority):
                raise ValueError("accepted authorization requires complete authority fields")
            if self.reason_codes:
                raise ValueError("accepted authorization cannot carry rejection reasons")
            return self
        if self.command is not None:
            raise ValueError("non-accepted authorization cannot expose a command")
        if self.status == "rejected" and not self.reason_codes:
            raise ValueError("rejected authorization requires at least one reason code")
        if self.status in {"not_requested", "not_applicable"}:
            if any(value is not None for value in authority):
                raise ValueError(f"{self.status} authorization cannot carry authority fields")
        return self

    @classmethod
    def not_requested(cls) -> AuthorizationEvaluationV1:
        return cls(status="not_requested")

    @classmethod
    def not_applicable(cls, reason_code: str | None = None) -> AuthorizationEvaluationV1:
        reasons = [reason_code] if reason_code else []
        return cls(status="not_applicable", reason_codes=reasons)

    @classmethod
    def rejected(
        cls,
        *reason_codes: str,
        authorization_id: str | None = None,
        authorization_request_id: str | None = None,
        trust_policy_id: str | None = None,
        key_id: str | None = None,
        provider: str | None = None,
        principal: str | None = None,
        operation_id: str | None = None,
        issued_at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> AuthorizationEvaluationV1:
        return cls(
            status="rejected",
            authorization_id=authorization_id,
            authorization_request_id=authorization_request_id,
            trust_policy_id=trust_policy_id,
            key_id=key_id,
            provider=provider,
            principal=principal,
            operation_id=operation_id,
            issued_at=issued_at,
            expires_at=expires_at,
            reason_codes=sorted(set(reason_codes or ("authorization_rejected",))),
        )


__all__ = [
    "AuthorizationEvaluationStatus",
    "AuthorizationEvaluationV1",
    "GitPushOperationV1",
    "HUMAN_AUTHORIZATION_EVALUATION_SCHEMA_VERSION",
    "HUMAN_AUTHORIZATION_REQUEST_SCHEMA_VERSION",
    "HUMAN_AUTHORIZATION_SCHEMA_PATH",
    "HUMAN_AUTHORIZATION_SCHEMA_VERSION",
    "HUMAN_AUTHORIZATION_SIGNATURE_DOMAIN",
    "HUMAN_AUTHORIZATION_TRUST_POLICY_SCHEMA_VERSION",
    "HumanAuthorizationPrincipalV1",
    "HumanAuthorizationProofV1",
    "HumanAuthorizationRequestV1",
    "HumanAuthorizationReviewItemV1",
    "HumanAuthorizationStatementV1",
    "HumanAuthorizationTrustPolicyV1",
    "HumanAuthorizationV1",
    "TrustedEd25519KeyV1",
    "build_human_authorization",
    "build_git_push_operation",
    "build_human_authorization_request",
    "build_human_authorization_trust_policy",
    "authorization_review_items",
    "canonical_https_git_endpoint",
    "canonical_review_items",
    "decode_ed25519_public_key",
    "decode_ed25519_signature",
    "ed25519_key_id",
    "human_authorization_id",
    "review_set_id",
]
