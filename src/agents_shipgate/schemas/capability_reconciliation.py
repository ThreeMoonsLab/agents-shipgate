from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator, model_validator

CAPABILITY_INTENT_SCHEMA_VERSION = "0.1"
CAPABILITY_OBSERVATION_SCHEMA_VERSION = "0.1"
CAPABILITY_RECONCILIATION_SCHEMA_VERSION = "0.1"

_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_RESOURCE_CLASS_RE = re.compile(r"^[a-z][a-z0-9._:-]{0,127}$")
Sha256Value = Annotated[str, Field(pattern=_HASH_RE.pattern)]

TargetKind = Literal["tool", "mcp", "network", "filesystem", "resource"]
CoverageMode = Literal["closed_world", "partial"]
InventoryCoverage = Literal["complete", "partial", "unknown"]
PrincipalKind = Literal[
    "human_impersonation",
    "delegated_agent",
    "service_account",
    "assumed_role",
    "agent_principal",
    "workload_identity",
    "ambient",
    "anonymous",
    "unknown",
    "other",
]


def _require_sha256(value: str) -> str:
    if not _HASH_RE.fullmatch(value):
        raise ValueError("must use algorithm-prefixed lowercase sha256:<64 hex> form")
    return value


def _normalize_safe_token(value: str) -> str:
    normalized = value.strip()
    if not _SAFE_TOKEN_RE.fullmatch(normalized):
        raise ValueError("must be a non-secret identifier using letters, numbers, . _ : or -")
    return normalized


def _normalize_resource_class(value: str) -> str:
    normalized = value.strip().lower()
    if not _RESOURCE_CLASS_RE.fullmatch(normalized):
        raise ValueError("must be a lowercase resource-class slug")
    return normalized


class ReconciliationSubjectV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str
    environment: str | None = None

    @field_validator("agent_id")
    @classmethod
    def validate_agent_id(cls, value: str) -> str:
        return _normalize_safe_token(value)

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, value: str | None) -> str | None:
        return _normalize_safe_token(value) if value is not None else None


class ToolTargetV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["tool"] = "tool"
    capability_id: str | None = None
    tool_name: str
    provider: str | None = None
    operation: str | None = None

    @field_validator("capability_id", "tool_name", "provider", "operation")
    @classmethod
    def validate_tokens(cls, value: str | None) -> str | None:
        return _normalize_safe_token(value) if value is not None else None


class McpTargetV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["mcp"] = "mcp"
    capability_id: str | None = None
    mcp_server: str
    tool_name: str
    operation: str | None = None

    @field_validator("capability_id", "mcp_server", "tool_name", "operation")
    @classmethod
    def validate_tokens(cls, value: str | None) -> str | None:
        return _normalize_safe_token(value) if value is not None else None


class NetworkTargetV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["network"] = "network"
    domain: str = Field(pattern=r"^[^:/?#@*]+$")
    scheme: Literal["http", "https", "other"] | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    resource_class: str = "url"
    granularity: Literal["exact", "class"] = "class"
    locator_sha256: Sha256Value | None = None

    @field_validator("domain")
    @classmethod
    def normalize_domain(cls, value: str) -> str:
        raw = value.strip().rstrip(".")
        if any(token in raw for token in ("://", "/", "?", "#", "@", "*")):
            raise ValueError("domain must be hostname-only without URL, userinfo, or wildcard data")
        try:
            normalized = raw.encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise ValueError("domain is not a valid IDNA hostname") from exc
        if not normalized or len(normalized) > 253:
            raise ValueError("domain must be a non-empty hostname")
        labels = normalized.split(".")
        if any(
            not label
            or len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            or not re.fullmatch(r"[a-z0-9-]+", label)
            for label in labels
        ):
            raise ValueError("domain is not a valid hostname")
        return normalized

    @field_validator("resource_class")
    @classmethod
    def validate_resource_class(cls, value: str) -> str:
        return _normalize_resource_class(value)

    @field_validator("locator_sha256")
    @classmethod
    def validate_locator_hash(cls, value: str | None) -> str | None:
        return _require_sha256(value) if value is not None else None

    @model_validator(mode="after")
    def validate_granularity(self) -> NetworkTargetV1:
        if self.granularity == "exact" and self.locator_sha256 is None:
            raise ValueError("exact network targets require locator_sha256")
        if self.granularity == "class" and self.locator_sha256 is not None:
            raise ValueError("class network targets must not carry locator_sha256")
        return self


class FilesystemTargetV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["filesystem"] = "filesystem"
    path_class: Literal[
        "workspace",
        "temporary",
        "home",
        "system",
        "secret",
        "external",
        "unknown",
    ]
    resource_class: str = "file"
    granularity: Literal["exact", "class"] = "class"
    locator_sha256: Sha256Value | None = None

    @field_validator("resource_class")
    @classmethod
    def validate_resource_class(cls, value: str) -> str:
        return _normalize_resource_class(value)

    @field_validator("locator_sha256")
    @classmethod
    def validate_locator_hash(cls, value: str | None) -> str | None:
        return _require_sha256(value) if value is not None else None

    @model_validator(mode="after")
    def validate_granularity(self) -> FilesystemTargetV1:
        if self.granularity == "exact" and self.locator_sha256 is None:
            raise ValueError("exact filesystem targets require locator_sha256")
        if self.granularity == "class" and self.locator_sha256 is not None:
            raise ValueError("class filesystem targets must not carry locator_sha256")
        return self


class ResourceTargetV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["resource"] = "resource"
    resource_class: str
    provider: str | None = None
    granularity: Literal["exact", "class"] = "class"
    locator_sha256: Sha256Value | None = None

    @field_validator("resource_class")
    @classmethod
    def validate_resource_class(cls, value: str) -> str:
        return _normalize_resource_class(value)

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str | None) -> str | None:
        return _normalize_safe_token(value) if value is not None else None

    @field_validator("locator_sha256")
    @classmethod
    def validate_locator_hash(cls, value: str | None) -> str | None:
        return _require_sha256(value) if value is not None else None

    @model_validator(mode="after")
    def validate_granularity(self) -> ResourceTargetV1:
        if self.granularity == "exact" and self.locator_sha256 is None:
            raise ValueError("exact resource targets require locator_sha256")
        if self.granularity == "class" and self.locator_sha256 is not None:
            raise ValueError("class resource targets must not carry locator_sha256")
        return self


CapabilityTargetV1 = Annotated[
    ToolTargetV1 | McpTargetV1 | NetworkTargetV1 | FilesystemTargetV1 | ResourceTargetV1,
    Field(discriminator="kind"),
]


def _policy_selector_contains(
    selector: CapabilityTargetV1,
    candidate: CapabilityTargetV1,
) -> bool:
    if selector.kind != candidate.kind:
        return False
    if selector.kind == "tool":
        if (
            selector.capability_id is not None
            and candidate.capability_id is not None
            and selector.capability_id == candidate.capability_id
        ):
            return True
        return (
            selector.tool_name == candidate.tool_name
            and selector.provider == candidate.provider
            and selector.operation == candidate.operation
        )
    if selector.kind == "mcp":
        if (
            selector.capability_id is not None
            and candidate.capability_id is not None
            and selector.capability_id == candidate.capability_id
        ):
            return True
        return (
            selector.mcp_server == candidate.mcp_server
            and selector.tool_name == candidate.tool_name
            and selector.operation == candidate.operation
        )
    if selector.kind == "network":
        same_class = (
            selector.domain == candidate.domain
            and selector.scheme == candidate.scheme
            and selector.port == candidate.port
            and selector.resource_class == candidate.resource_class
        )
    elif selector.kind == "filesystem":
        same_class = (
            selector.path_class == candidate.path_class
            and selector.resource_class == candidate.resource_class
        )
    else:
        same_class = (
            selector.resource_class == candidate.resource_class
            and selector.provider == candidate.provider
        )
    if not same_class:
        return False
    if selector.granularity == "class":
        return True
    return (
        candidate.granularity == "exact"
        and selector.locator_sha256 == candidate.locator_sha256
    )


class PrincipalBindingV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: PrincipalKind
    credential_mode: str
    auth_type: str | None = None
    authority_source: str | None = None
    authority_mode: Literal["none", "scoped", "unscoped", "ambient", "unknown"] = "unknown"
    scopes: tuple[str, ...] = Field(default_factory=tuple)
    principal_ref_sha256: Sha256Value | None = None

    @field_validator("credential_mode", "auth_type", "authority_source")
    @classmethod
    def validate_tokens(cls, value: str | None) -> str | None:
        return _normalize_safe_token(value) if value is not None else None

    @field_validator("scopes")
    @classmethod
    def normalize_scopes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted({_normalize_safe_token(scope) for scope in value}))
        return normalized

    @field_validator("principal_ref_sha256")
    @classmethod
    def validate_principal_hash(cls, value: str | None) -> str | None:
        return _require_sha256(value) if value is not None else None


class IntentCoverageV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: CoverageMode = "partial"
    mcp: CoverageMode = "partial"
    network: CoverageMode = "partial"
    filesystem: CoverageMode = "partial"
    resource: CoverageMode = "partial"

    def for_kind(self, kind: TargetKind) -> CoverageMode:
        return getattr(self, kind)


class IntentRuleV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    effect: Literal["allow", "deny"]
    target: CapabilityTargetV1
    expected_principal: PrincipalBindingV1 | None = None

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        return _normalize_safe_token(value)


class CapabilityIntentPolicyV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability_intent_schema_version: Literal["0.1"] = CAPABILITY_INTENT_SCHEMA_VERSION
    experimental: Literal[True] = True
    subject: ReconciliationSubjectV1
    owner: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    coverage: IntentCoverageV1 = Field(default_factory=IntentCoverageV1)
    rules: list[IntentRuleV1] = Field(default_factory=list)

    @field_validator("owner", "reason")
    @classmethod
    def strip_human_fields(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must be non-empty")
        return normalized

    @model_validator(mode="after")
    def validate_unique_rules(self) -> CapabilityIntentPolicyV1:
        seen_ids: set[str] = set()
        seen_targets: dict[str, tuple[str, str]] = {}
        for rule in self.rules:
            if rule.id in seen_ids:
                raise ValueError(f"duplicate intent rule id: {rule.id}")
            seen_ids.add(rule.id)
            target_key = rule.target.model_dump_json(exclude_none=True)
            previous = seen_targets.get(target_key)
            if previous is not None:
                previous_id, previous_effect = previous
                if previous_effect != rule.effect:
                    raise ValueError(
                        "conflicting overlapping intent rules: "
                        f"{previous_id} ({previous_effect}) and {rule.id} ({rule.effect})"
                    )
                raise ValueError(
                    "duplicate or conflicting intent target: "
                    f"{previous_id} and {rule.id}"
                )
            seen_targets[target_key] = (rule.id, rule.effect)
        for index, left in enumerate(self.rules):
            for right in self.rules[index + 1 :]:
                if left.effect == right.effect:
                    continue
                if _policy_selector_contains(
                    left.target, right.target
                ) or _policy_selector_contains(right.target, left.target):
                    raise ValueError(
                        "conflicting overlapping intent rules: "
                        f"{left.id} ({left.effect}) and {right.id} ({right.effect})"
                    )
        return self


class InventoryCoverageByKindV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: InventoryCoverage = "unknown"
    mcp: InventoryCoverage = "unknown"
    network: InventoryCoverage = "unknown"
    filesystem: InventoryCoverage = "unknown"
    resource: InventoryCoverage = "unknown"

    def for_kind(self, kind: TargetKind) -> InventoryCoverage:
        return getattr(self, kind)


class ObservationSourceV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["sandbox_policy", "sandbox_run", "eval_run"]
    vendor: str
    producer_version: str | None = None
    collection_mode: Literal["learning", "enforced", "evaluation"]
    run_id: str
    run_sha256: Sha256Value
    policy_sha256: Sha256Value | None
    grant_inventory_coverage: InventoryCoverageByKindV1 = Field(
        default_factory=InventoryCoverageByKindV1
    )
    observation_collection_coverage: Literal[
        "complete_for_run", "partial", "unknown"
    ] = "unknown"
    behavioral_coverage: Literal["sampled"] = "sampled"

    @field_validator("id", "vendor", "producer_version", "run_id")
    @classmethod
    def validate_tokens(cls, value: str | None) -> str | None:
        return _normalize_safe_token(value) if value is not None else None

    @field_validator("run_sha256")
    @classmethod
    def validate_run_hash(cls, value: str) -> str:
        return _require_sha256(value)

    @field_validator("policy_sha256")
    @classmethod
    def validate_policy_hash(cls, value: str | None) -> str | None:
        return _require_sha256(value) if value is not None else None


class GrantRecordV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source_id: str
    target: CapabilityTargetV1
    principal: PrincipalBindingV1

    @field_validator("id", "source_id")
    @classmethod
    def validate_ids(cls, value: str) -> str:
        return _normalize_safe_token(value)


class ObservationRecordV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source_id: str
    sequence: int = Field(ge=0)
    correlation_id: str | None = None
    target: CapabilityTargetV1
    outcome: Literal["attempted", "allowed", "denied"]
    principal: PrincipalBindingV1

    @field_validator("id", "source_id", "correlation_id")
    @classmethod
    def validate_ids(cls, value: str | None) -> str | None:
        return _normalize_safe_token(value) if value is not None else None


class CapabilityObservationBundleV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability_observation_schema_version: Literal["0.1"] = (
        CAPABILITY_OBSERVATION_SCHEMA_VERSION
    )
    experimental: Literal[True] = True
    evidence_trust: Literal["untrusted"] = "untrusted"
    subject: ReconciliationSubjectV1
    sources: list[ObservationSourceV1] = Field(default_factory=list)
    grants: list[GrantRecordV1] = Field(default_factory=list)
    observations: list[ObservationRecordV1] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_references(self) -> CapabilityObservationBundleV1:
        source_ids = [source.id for source in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("observation source ids must be unique")
        known_sources = set(source_ids)
        row_ids: set[str] = set()
        for row in [*self.grants, *self.observations]:
            if row.id in row_ids:
                raise ValueError(f"duplicate grant/observation id: {row.id}")
            row_ids.add(row.id)
            if row.source_id not in known_sources:
                raise ValueError(
                    f"row {row.id} references unknown source_id {row.source_id}"
                )
        return self


MembershipState = Literal["present", "absent", "unknown"]
ObservationMembership = Literal["observed", "not_observed"]
ReconciliationSignal = Literal[
    "excess_authority",
    "policy_unclassified_grant",
    "behavioral_drift",
    "denied_attempt",
    "declared_ungranted",
    "granted_unobserved",
    "allowed_ungranted",
    "intended_undeclared",
    "declared_unintended",
    "principal_changed",
    "principal_mismatch",
    "aligned",
]
EvidenceGapKind = Literal[
    "missing_policy_hash",
    "policy_snapshot_mismatch",
    "ambiguous_target",
    "unmapped_target",
    "incomplete_intent_inventory",
    "incomplete_declared_inventory",
    "incomplete_grant_inventory",
    "conflicting_grants",
    "unsupported_resource_comparison",
]


class ReconciliationInputBindingV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["capability_lock", "intent_policy", "evidence", "base_evidence"]
    path: str
    sha256: Sha256Value
    schema_version: str

    @field_validator("sha256")
    @classmethod
    def validate_hash(cls, value: str) -> str:
        return _require_sha256(value)


class SetMembershipV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intended: MembershipState
    declared: MembershipState
    granted: MembershipState
    observed: ObservationMembership


class AuthorityDeltaV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    before: PrincipalBindingV1
    after: PrincipalBindingV1


class ReconciliationResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    cohort_id: str
    target: CapabilityTargetV1
    membership: SetMembershipV1
    outcomes: tuple[Literal["attempted", "allowed", "denied"], ...] = Field(
        default_factory=tuple
    )
    signals: tuple[ReconciliationSignal, ...]
    primary_signal: ReconciliationSignal
    disposition: Literal["informational", "investigate", "human_review"]
    evidence_refs: tuple[str, ...] = Field(default_factory=tuple)
    authority_delta: AuthorityDeltaV1 | None = None


class ReconciliationEvidenceGapV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    cohort_id: str
    kind: EvidenceGapKind
    target: CapabilityTargetV1 | None = None
    message: str
    evidence_refs: tuple[str, ...] = Field(default_factory=tuple)


class CandidatePolicyChangeV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    cohort_id: str
    target: CapabilityTargetV1
    action: Literal[
        "remove_or_narrow_grant",
        "investigate_runtime_route",
        "investigate_denied_attempt",
        "consider_minimal_grant",
        "review_principal",
        "add_or_correct_static_declaration",
        "review_intent_or_declaration",
        "review_intent_policy",
        "inspect_enforcement_integrity",
    ]
    rationale: str
    evidence_refs: tuple[str, ...] = Field(default_factory=tuple)
    actor: Literal["human"] = "human"
    approval_state: Literal["unreviewed"] = "unreviewed"
    auto_apply: Literal[False] = False
    safe_to_attempt: Literal[False] = False
    requires_human_review: Literal[True] = True


class ReconciliationCohortCoverageV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intended: dict[TargetKind, CoverageMode]
    declared: Literal["complete", "partial"]
    granted: dict[TargetKind, InventoryCoverage]
    observed: Literal["sampled"] = "sampled"


class ReconciliationSourceRunV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    kind: Literal["sandbox_policy", "sandbox_run", "eval_run"]
    collection_mode: Literal["learning", "enforced", "evaluation"]
    run_id: str
    run_sha256: Sha256Value
    policy_sha256: Sha256Value | None

    @field_validator("run_sha256")
    @classmethod
    def validate_run_hash(cls, value: str) -> str:
        return _require_sha256(value)

    @field_validator("policy_sha256")
    @classmethod
    def validate_policy_hash(cls, value: str | None) -> str | None:
        return _require_sha256(value) if value is not None else None


class ReconciliationCohortV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    policy_sha256: str | None
    source_refs: tuple[str, ...]
    source_runs: tuple[ReconciliationSourceRunV1, ...]
    coverage: ReconciliationCohortCoverageV1
    result_count: int
    evidence_gap_count: int
    signal_counts: dict[str, int] = Field(default_factory=dict)


class ReconciliationSummaryV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cohort_count: int
    target_count: int
    result_count: int
    evidence_gap_count: int
    candidate_policy_change_count: int
    signal_counts: dict[str, int] = Field(default_factory=dict)


class CapabilityReconciliationV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability_reconciliation_schema_version: Literal["0.1"] = (
        CAPABILITY_RECONCILIATION_SCHEMA_VERSION
    )
    experimental: Literal[True] = True
    local_imported_evidence_only: Literal[True] = True
    live_collection_performed: Literal[False] = False
    release_gate_effect: Literal[False] = False
    evidence_trust: Literal["untrusted"] = "untrusted"
    subject: ReconciliationSubjectV1
    capability_lock_semantic_hash: Sha256Value
    inputs: tuple[ReconciliationInputBindingV1, ...]
    summary: ReconciliationSummaryV1
    cohorts: tuple[ReconciliationCohortV1, ...]
    results: tuple[ReconciliationResultV1, ...]
    evidence_gaps: tuple[ReconciliationEvidenceGapV1, ...]
    candidate_policy_changes: tuple[CandidatePolicyChangeV1, ...]
    notes: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("capability_lock_semantic_hash")
    @classmethod
    def validate_semantic_hash(cls, value: str) -> str:
        return _require_sha256(value)


class CapabilityIntentPolicyArtifactV1(RootModel[CapabilityIntentPolicyV1]):
    root: CapabilityIntentPolicyV1


class CapabilityObservationArtifactV1(RootModel[CapabilityObservationBundleV1]):
    root: CapabilityObservationBundleV1


class CapabilityReconciliationArtifactV1(RootModel[CapabilityReconciliationV1]):
    root: CapabilityReconciliationV1


__all__ = [
    "CAPABILITY_INTENT_SCHEMA_VERSION",
    "CAPABILITY_OBSERVATION_SCHEMA_VERSION",
    "CAPABILITY_RECONCILIATION_SCHEMA_VERSION",
    "AuthorityDeltaV1",
    "CandidatePolicyChangeV1",
    "CapabilityIntentPolicyArtifactV1",
    "CapabilityIntentPolicyV1",
    "CapabilityObservationArtifactV1",
    "CapabilityObservationBundleV1",
    "CapabilityReconciliationArtifactV1",
    "CapabilityReconciliationV1",
    "CapabilityTargetV1",
    "EvidenceGapKind",
    "FilesystemTargetV1",
    "GrantRecordV1",
    "IntentCoverageV1",
    "IntentRuleV1",
    "InventoryCoverageByKindV1",
    "McpTargetV1",
    "NetworkTargetV1",
    "ObservationRecordV1",
    "ObservationSourceV1",
    "PrincipalBindingV1",
    "ReconciliationCohortCoverageV1",
    "ReconciliationCohortV1",
    "ReconciliationEvidenceGapV1",
    "ReconciliationInputBindingV1",
    "ReconciliationResultV1",
    "ReconciliationSignal",
    "ReconciliationSourceRunV1",
    "ReconciliationSubjectV1",
    "ReconciliationSummaryV1",
    "ResourceTargetV1",
    "SetMembershipV1",
    "ToolTargetV1",
]
