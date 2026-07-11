from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast

from pydantic import ValidationError

from agents_shipgate.core.errors import InputParseError
from agents_shipgate.inputs.common import load_structured_file
from agents_shipgate.schemas.capabilities import CapabilityFactV1, CapabilityLockFileV1
from agents_shipgate.schemas.capability_reconciliation import (
    AuthorityDeltaV1,
    CandidatePolicyChangeV1,
    CapabilityIntentPolicyV1,
    CapabilityObservationBundleV1,
    CapabilityReconciliationV1,
    CapabilityTargetV1,
    CoverageMode,
    EvidenceGapKind,
    FilesystemTargetV1,
    GrantRecordV1,
    InventoryCoverage,
    McpTargetV1,
    MembershipState,
    NetworkTargetV1,
    ObservationRecordV1,
    ObservationSourceV1,
    PrincipalBindingV1,
    ReconciliationCohortCoverageV1,
    ReconciliationCohortV1,
    ReconciliationEvidenceGapV1,
    ReconciliationInputBindingV1,
    ReconciliationResultV1,
    ReconciliationSignal,
    ReconciliationSourceRunV1,
    ReconciliationSummaryV1,
    ResourceTargetV1,
    SetMembershipV1,
    TargetKind,
    ToolTargetV1,
)

_PRIMARY_SIGNAL_ORDER: tuple[ReconciliationSignal, ...] = (
    "principal_changed",
    "principal_mismatch",
    "allowed_ungranted",
    "excess_authority",
    "behavioral_drift",
    "intended_undeclared",
    "declared_unintended",
    "declared_ungranted",
    "policy_unclassified_grant",
    "denied_attempt",
    "granted_unobserved",
    "aligned",
)

_SIGNAL_ORDER = {signal: index for index, signal in enumerate(_PRIMARY_SIGNAL_ORDER)}


@dataclass(frozen=True)
class _TargetMatch:
    target: CapabilityTargetV1
    fact: CapabilityFactV1 | None
    reason: Literal["capability_id", "composite", "tool_name", "ambiguous", "unmapped"]


@dataclass
class _CohortData:
    partition_key: str
    policy_sha256: str | None
    sources: dict[str, ObservationSourceV1] = field(default_factory=dict)
    grants: list[GrantRecordV1] = field(default_factory=list)
    observations: list[ObservationRecordV1] = field(default_factory=list)


@dataclass(frozen=True)
class _PreparedRule:
    id: str
    effect: Literal["allow", "deny"]
    target: CapabilityTargetV1
    expected_principal: PrincipalBindingV1 | None


class _DeclaredIndex:
    def __init__(self, lock: CapabilityLockFileV1) -> None:
        self.facts = tuple(lock.capabilities)
        self.by_id = {fact.id: fact for fact in self.facts}
        self.by_tool: dict[str, list[CapabilityFactV1]] = defaultdict(list)
        for fact in self.facts:
            self.by_tool[fact.identity.tool_name].append(fact)
        for facts in self.by_tool.values():
            facts.sort(key=lambda fact: fact.id)

    def match(self, target: CapabilityTargetV1) -> _TargetMatch:
        if target.kind not in {"tool", "mcp"}:
            return _TargetMatch(target=target, fact=None, reason="unmapped")

        capability_id = target.capability_id
        if capability_id is not None:
            fact = self.by_id.get(capability_id)
            if fact is None:
                return _TargetMatch(target=target, fact=None, reason="unmapped")
            return _TargetMatch(
                target=_target_from_fact(fact),
                fact=fact,
                reason="capability_id",
            )

        candidates = list(self.by_tool.get(target.tool_name, ()))
        composite = [fact for fact in candidates if _fact_matches_composite(fact, target)]
        has_composite_selector = _has_composite_selector(target)
        if has_composite_selector and len(composite) == 1:
            fact = composite[0]
            return _TargetMatch(
                target=_target_from_fact(fact),
                fact=fact,
                reason="composite",
            )
        if has_composite_selector and len(composite) > 1:
            return _TargetMatch(target=target, fact=None, reason="ambiguous")
        if has_composite_selector and not composite:
            return _TargetMatch(target=target, fact=None, reason="unmapped")
        if len(candidates) == 1:
            fact = candidates[0]
            return _TargetMatch(
                target=_target_from_fact(fact),
                fact=fact,
                reason="tool_name",
            )
        if len(candidates) > 1:
            return _TargetMatch(target=target, fact=None, reason="ambiguous")
        return _TargetMatch(target=target, fact=None, reason="unmapped")


def load_capability_intent_policy(path: Path) -> CapabilityIntentPolicyV1:
    return _load_model(path, CapabilityIntentPolicyV1, label="capability intent policy")


def load_capability_observation_bundle(path: Path) -> CapabilityObservationBundleV1:
    return _load_model(path, CapabilityObservationBundleV1, label="capability observation bundle")


def render_capability_reconciliation_json(artifact: CapabilityReconciliationV1) -> str:
    return json.dumps(artifact.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"


def reconcile_capabilities(
    *,
    lock: CapabilityLockFileV1,
    intent: CapabilityIntentPolicyV1,
    evidence_bundles: Iterable[CapabilityObservationBundleV1],
    base_evidence_bundles: Iterable[CapabilityObservationBundleV1] = (),
    input_bindings: Iterable[ReconciliationInputBindingV1] = (),
) -> CapabilityReconciliationV1:
    """Reconcile policy, static facts, sandbox grants, and sampled observations.

    The function consumes only already-loaded local artifacts. It never executes
    an agent, connects to a sandbox, calls a tool, or mutates policy. Results are
    non-gating audit evidence and every candidate policy change is human-only.
    """

    evidence = tuple(evidence_bundles)
    base_evidence = tuple(base_evidence_bundles)
    subject = intent.subject
    if lock.source.agent_id != subject.agent_id:
        raise InputParseError(
            "Capability lock agent_id does not match intent policy: "
            f"{lock.source.agent_id!r} != {subject.agent_id!r}"
        )
    if (
        subject.environment is not None
        and lock.source.environment_target is not None
        and lock.source.environment_target != subject.environment
    ):
        raise InputParseError(
            "Capability lock environment does not match intent policy: "
            f"{lock.source.environment_target!r} != {subject.environment!r}"
        )
    _validate_bundle_subjects(subject.agent_id, subject.environment, evidence, role="evidence")
    _validate_bundle_subjects(
        subject.agent_id,
        subject.environment,
        base_evidence,
        role="base evidence",
    )

    declared = _DeclaredIndex(lock)
    prepared_rules, rule_match_gaps = _prepare_rules(intent, declared)
    cohorts = _partition_evidence(evidence)
    base_grants = _base_grants(base_evidence, declared)
    declared_complete = (
        lock.source.source_warning_count == 0
        and lock.source.toolkit_bound_count == 0
        and lock.source.plugins_enabled
    )

    all_results: list[ReconciliationResultV1] = []
    all_gaps: list[ReconciliationEvidenceGapV1] = []
    all_proposals: list[CandidatePolicyChangeV1] = []
    cohort_rows: list[ReconciliationCohortV1] = []

    for cohort in sorted(cohorts.values(), key=lambda row: row.partition_key):
        cohort_id = _stable_id("cohort", cohort.partition_key)
        gaps: dict[tuple[str, str, str], ReconciliationEvidenceGapV1] = {}
        for gap_kind, target, message, refs in rule_match_gaps:
            _add_gap(gaps, cohort_id, gap_kind, target, message, refs)
        if cohort.policy_sha256 is None:
            _add_gap(
                gaps,
                cohort_id,
                "missing_policy_hash",
                None,
                "Source has no sandbox policy hash; it remains isolated and cannot support "
                "cross-source absence claims.",
                tuple(sorted(cohort.sources)),
            )

        grant_coverage = _aggregate_grant_coverage(cohort.sources.values())
        conflicting_grant_kinds = _add_conflicting_grant_gaps(
            gaps,
            cohort_id,
            cohort,
            declared,
        )
        for kind in conflicting_grant_kinds:
            grant_coverage[kind] = "unknown"
        if cohort.policy_sha256 is None:
            grant_coverage = {
                kind: "unknown"
                for kind in ("tool", "mcp", "network", "filesystem", "resource")
            }

        prepared_grants = [
            (grant, declared.match(grant.target))
            for grant in sorted(cohort.grants, key=lambda row: (row.source_id, row.id))
        ]
        prepared_observations = [
            (observation, declared.match(observation.target))
            for observation in sorted(
                cohort.observations,
                key=lambda row: (row.source_id, row.sequence, row.id),
            )
        ]
        _add_match_gaps(
            gaps,
            cohort_id,
            prepared_grants,
            prepared_observations,
        )

        universe: dict[str, CapabilityTargetV1] = {}
        for fact in declared.facts:
            target = _target_from_fact(fact)
            universe[_target_key(target)] = target
        for rule in prepared_rules:
            universe[_target_key(rule.target)] = rule.target
        for _row, match in [*prepared_grants, *prepared_observations]:
            universe[_target_key(match.target)] = match.target

        cohort_results: list[ReconciliationResultV1] = []
        cohort_proposals: list[CandidatePolicyChangeV1] = []
        for target_key in sorted(universe):
            target = universe[target_key]
            fact_match = declared.match(target)
            intended, intent_refs, expected_principals = _intent_membership(
                target,
                prepared_rules,
                intent.coverage.for_kind(cast(TargetKind, target.kind)),
            )
            declared_state = _declared_membership(target, fact_match, declared_complete)
            matching_grants = [
                grant
                for grant, match in prepared_grants
                if _selector_contains(match.target, target)
            ]
            granted = _granted_membership(
                target,
                matching_grants,
                grant_coverage[cast(TargetKind, target.kind)],
            )
            matching_observations = [
                observation
                for observation, match in prepared_observations
                if _selector_contains(target, match.target)
            ]
            observed = "observed" if matching_observations else "not_observed"
            outcomes = tuple(
                sorted(
                    {observation.outcome for observation in matching_observations},
                    key=("attempted", "allowed", "denied").index,
                )
            )

            evidence_refs = set(intent_refs)
            if fact_match.fact is not None:
                evidence_refs.add(f"capability:{fact_match.fact.id}")
            evidence_refs.update(
                f"grant:{grant.source_id}:{grant.id}" for grant in matching_grants
            )
            evidence_refs.update(
                f"observation:{row.source_id}:{row.id}" for row in matching_observations
            )

            principal_changed, authority_delta = _principal_change(
                target,
                matching_grants,
                base_grants,
            )
            principal_mismatch = _principal_mismatch(
                matching_grants,
                matching_observations,
                expected_principals,
                fact_match.fact,
            )
            signals = _signals(
                intended=intended,
                declared=declared_state,
                granted=granted,
                observed=observed,
                outcomes=outcomes,
                principal_changed=principal_changed,
                principal_mismatch=principal_mismatch,
            )
            primary = min(signals, key=_SIGNAL_ORDER.__getitem__)
            disposition = _disposition(signals, intended=intended)
            result = ReconciliationResultV1(
                id=_stable_id("rec", cohort_id, target_key),
                cohort_id=cohort_id,
                target=target,
                membership=SetMembershipV1(
                    intended=intended,
                    declared=declared_state,
                    granted=granted,
                    observed=observed,
                ),
                outcomes=outcomes,
                signals=tuple(sorted(signals, key=_SIGNAL_ORDER.__getitem__)),
                primary_signal=primary,
                disposition=disposition,
                evidence_refs=tuple(sorted(evidence_refs)),
                authority_delta=authority_delta,
            )
            cohort_results.append(result)
            cohort_proposals.extend(_proposals_for_result(result))

            if intended == "unknown":
                _add_gap(
                    gaps,
                    cohort_id,
                    "incomplete_intent_inventory",
                    target,
                    "Intent is partial for this target kind; absence is unknown.",
                    tuple(sorted(intent_refs)),
                )
            if declared_state == "unknown":
                gap_kind: EvidenceGapKind = (
                    "unsupported_resource_comparison"
                    if target.kind in {"network", "filesystem", "resource"}
                    else "incomplete_declared_inventory"
                )
                _add_gap(
                    gaps,
                    cohort_id,
                    gap_kind,
                    target,
                    "The static capability lock cannot prove absence for this target.",
                    tuple(sorted(evidence_refs)),
                )
            if granted == "unknown":
                _add_gap(
                    gaps,
                    cohort_id,
                    "incomplete_grant_inventory",
                    target,
                    "The sandbox grant inventory is not complete for this target kind.",
                    tuple(sorted(evidence_refs)),
                )

        cohort_results.sort(key=lambda row: (row.id, row.primary_signal))
        cohort_proposals = _dedupe_proposals(cohort_proposals)
        gap_rows = sorted(gaps.values(), key=lambda row: row.id)
        signal_counts = Counter(
            signal for result in cohort_results for signal in result.signals
        )
        cohort_rows.append(
            ReconciliationCohortV1(
                id=cohort_id,
                policy_sha256=cohort.policy_sha256,
                source_refs=tuple(sorted(cohort.sources)),
                source_runs=tuple(
                    ReconciliationSourceRunV1(
                        source_id=source.id,
                        kind=source.kind,
                        collection_mode=source.collection_mode,
                        run_id=source.run_id,
                        run_sha256=source.run_sha256,
                        policy_sha256=source.policy_sha256,
                    )
                    for source in sorted(cohort.sources.values(), key=lambda row: row.id)
                ),
                coverage=ReconciliationCohortCoverageV1(
                    intended={
                        kind: intent.coverage.for_kind(kind)
                        for kind in ("tool", "mcp", "network", "filesystem", "resource")
                    },
                    declared="complete" if declared_complete else "partial",
                    granted=grant_coverage,
                ),
                result_count=len(cohort_results),
                evidence_gap_count=len(gap_rows),
                signal_counts=dict(sorted(signal_counts.items())),
            )
        )
        all_results.extend(cohort_results)
        all_gaps.extend(gap_rows)
        all_proposals.extend(cohort_proposals)

    all_results.sort(key=lambda row: (row.cohort_id, row.id))
    all_gaps.sort(key=lambda row: (row.cohort_id, row.id))
    all_proposals = _dedupe_proposals(all_proposals)
    total_signal_counts = Counter(signal for row in all_results for signal in row.signals)
    return CapabilityReconciliationV1(
        subject=subject,
        capability_lock_semantic_hash=(
            f"sha256:{lock.hashes.semantic_capability_set_hash}"
            if not lock.hashes.semantic_capability_set_hash.startswith("sha256:")
            else lock.hashes.semantic_capability_set_hash
        ),
        inputs=tuple(sorted(input_bindings, key=lambda row: (row.role, row.path))),
        summary=ReconciliationSummaryV1(
            cohort_count=len(cohort_rows),
            target_count=len(all_results),
            result_count=len(all_results),
            evidence_gap_count=len(all_gaps),
            candidate_policy_change_count=len(all_proposals),
            signal_counts=dict(sorted(total_signal_counts.items())),
        ),
        cohorts=tuple(sorted(cohort_rows, key=lambda row: row.id)),
        results=tuple(all_results),
        evidence_gaps=tuple(all_gaps),
        candidate_policy_changes=tuple(all_proposals),
        notes=(
            "Observations are sampled, untrusted evidence and never establish runtime absence.",
            "Candidate policy changes are non-executable proposals requiring human review.",
            "This artifact does not affect report.json.release_decision.decision.",
        ),
    )


def _load_model(path: Path, model_type, *, label: str):
    try:
        return model_type.model_validate(load_structured_file(path))
    except ValidationError as exc:
        raise InputParseError(f"Invalid {label} {path}: {exc}") from exc


def _validate_bundle_subjects(
    agent_id: str,
    environment: str | None,
    bundles: Iterable[CapabilityObservationBundleV1],
    *,
    role: str,
) -> None:
    for bundle in bundles:
        if bundle.subject.agent_id != agent_id:
            raise InputParseError(
                f"{role.title()} agent_id does not match intent policy: "
                f"{bundle.subject.agent_id!r} != {agent_id!r}"
            )
        if environment is not None and bundle.subject.environment not in {None, environment}:
            raise InputParseError(
                f"{role.title()} environment does not match intent policy: "
                f"{bundle.subject.environment!r} != {environment!r}"
            )


def _prepare_rules(
    intent: CapabilityIntentPolicyV1,
    declared: _DeclaredIndex,
) -> tuple[
    tuple[_PreparedRule, ...],
    tuple[tuple[EvidenceGapKind, CapabilityTargetV1 | None, str, tuple[str, ...]], ...],
]:
    rows: list[_PreparedRule] = []
    gaps: list[tuple[EvidenceGapKind, CapabilityTargetV1 | None, str, tuple[str, ...]]] = []
    for rule in sorted(intent.rules, key=lambda row: row.id):
        match = declared.match(rule.target)
        rows.append(
            _PreparedRule(
                id=rule.id,
                effect=rule.effect,
                target=match.target,
                expected_principal=rule.expected_principal,
            )
        )
        if rule.target.kind in {"tool", "mcp"} and match.reason in {"ambiguous", "unmapped"}:
            kind: EvidenceGapKind = (
                "ambiguous_target" if match.reason == "ambiguous" else "unmapped_target"
            )
            gaps.append(
                (
                    kind,
                    rule.target,
                    f"Intent rule {rule.id} could not be matched to one static capability.",
                    (f"intent:{rule.id}",),
                )
            )
    return tuple(rows), tuple(gaps)


def _partition_evidence(
    bundles: Iterable[CapabilityObservationBundleV1],
) -> dict[str, _CohortData]:
    cohorts: dict[str, _CohortData] = {}
    global_source_ids: set[str] = set()
    for bundle in bundles:
        sources = {source.id: source for source in bundle.sources}
        for source in bundle.sources:
            if source.id in global_source_ids:
                raise InputParseError(
                    f"Evidence source id {source.id!r} is duplicated across bundles"
                )
            global_source_ids.add(source.id)
            partition = source.policy_sha256 or f"unbound:{source.id}"
            cohort = cohorts.setdefault(
                partition,
                _CohortData(partition_key=partition, policy_sha256=source.policy_sha256),
            )
            cohort.sources[source.id] = source
        for grant in bundle.grants:
            source = sources[grant.source_id]
            partition = source.policy_sha256 or f"unbound:{source.id}"
            cohorts[partition].grants.append(grant)
        for observation in bundle.observations:
            source = sources[observation.source_id]
            partition = source.policy_sha256 or f"unbound:{source.id}"
            cohorts[partition].observations.append(observation)
    return cohorts


def _base_grants(
    bundles: Iterable[CapabilityObservationBundleV1],
    declared: _DeclaredIndex,
) -> tuple[tuple[GrantRecordV1, ObservationSourceV1, CapabilityTargetV1], ...]:
    rows: list[tuple[GrantRecordV1, ObservationSourceV1, CapabilityTargetV1]] = []
    seen_source_ids: set[str] = set()
    for bundle in bundles:
        sources = {source.id: source for source in bundle.sources}
        overlap = seen_source_ids & sources.keys()
        if overlap:
            duplicate = sorted(overlap)[0]
            raise InputParseError(
                f"Base evidence source id {duplicate!r} is duplicated across bundles"
            )
        seen_source_ids.update(sources)
        for grant in bundle.grants:
            source = sources[grant.source_id]
            kind = cast(TargetKind, grant.target.kind)
            if source.grant_inventory_coverage.for_kind(kind) != "complete":
                continue
            rows.append((grant, source, declared.match(grant.target).target))
    return tuple(rows)


def _aggregate_grant_coverage(
    sources: Iterable[ObservationSourceV1],
) -> dict[TargetKind, InventoryCoverage]:
    result: dict[TargetKind, InventoryCoverage] = {}
    for kind in ("tool", "mcp", "network", "filesystem", "resource"):
        values = [source.grant_inventory_coverage.for_kind(kind) for source in sources]
        if "complete" in values:
            result[kind] = "complete"
        elif "partial" in values:
            result[kind] = "partial"
        else:
            result[kind] = "unknown"
    return result


def _add_conflicting_grant_gaps(
    gaps: dict[tuple[str, str, str], ReconciliationEvidenceGapV1],
    cohort_id: str,
    cohort: _CohortData,
    declared: _DeclaredIndex,
) -> set[TargetKind]:
    conflicting: set[TargetKind] = set()
    for kind in ("tool", "mcp", "network", "filesystem", "resource"):
        complete_sources = [
            source
            for source in cohort.sources.values()
            if source.grant_inventory_coverage.for_kind(kind) == "complete"
        ]
        if len(complete_sources) < 2:
            continue
        snapshots: dict[str, frozenset[str]] = {}
        for source in complete_sources:
            targets = frozenset(
                _target_key(declared.match(grant.target).target)
                + "|"
                + _principal_key(grant.principal)
                for grant in cohort.grants
                if grant.source_id == source.id and grant.target.kind == kind
            )
            snapshots[source.id] = targets
        if len(set(snapshots.values())) > 1:
            conflicting.add(kind)
            _add_gap(
                gaps,
                cohort_id,
                "conflicting_grants",
                None,
                f"Complete grant snapshots disagree for target kind {kind}.",
                tuple(sorted(snapshots)),
            )
    return conflicting


def _add_match_gaps(
    gaps: dict[tuple[str, str, str], ReconciliationEvidenceGapV1],
    cohort_id: str,
    grants: Iterable[tuple[GrantRecordV1, _TargetMatch]],
    observations: Iterable[tuple[ObservationRecordV1, _TargetMatch]],
) -> None:
    for prefix, rows in (("grant", grants), ("observation", observations)):
        for row, match in rows:
            if row.target.kind not in {"tool", "mcp"}:
                continue
            if match.reason not in {"ambiguous", "unmapped"}:
                continue
            kind: EvidenceGapKind = (
                "ambiguous_target" if match.reason == "ambiguous" else "unmapped_target"
            )
            _add_gap(
                gaps,
                cohort_id,
                kind,
                row.target,
                f"{prefix.title()} row {row.id} could not be matched to one static capability.",
                (f"{prefix}:{row.source_id}:{row.id}",),
            )


def _intent_membership(
    target: CapabilityTargetV1,
    rules: Iterable[_PreparedRule],
    coverage: CoverageMode,
) -> tuple[MembershipState, tuple[str, ...], tuple[PrincipalBindingV1, ...]]:
    matching = [rule for rule in rules if _selector_contains(rule.target, target)]
    refs = tuple(sorted(f"intent:{rule.id}" for rule in matching))
    principals = tuple(
        rule.expected_principal
        for rule in matching
        if rule.effect == "allow" and rule.expected_principal is not None
    )
    if any(rule.effect == "deny" for rule in matching):
        return "absent", refs, principals
    if any(rule.effect == "allow" for rule in matching):
        return "present", refs, principals
    if coverage == "closed_world":
        return "absent", refs, principals
    return "unknown", refs, principals


def _declared_membership(
    target: CapabilityTargetV1,
    match: _TargetMatch,
    declared_complete: bool,
) -> MembershipState:
    if target.kind in {"network", "filesystem", "resource"}:
        return "unknown"
    if match.fact is not None:
        return "present"
    if match.reason == "ambiguous":
        return "unknown"
    return "absent" if declared_complete else "unknown"


def _granted_membership(
    target: CapabilityTargetV1,
    matching_grants: list[GrantRecordV1],
    coverage: InventoryCoverage,
) -> MembershipState:
    if matching_grants:
        return "present"
    return "absent" if coverage == "complete" else "unknown"


def _signals(
    *,
    intended: MembershipState,
    declared: MembershipState,
    granted: MembershipState,
    observed: Literal["observed", "not_observed"],
    outcomes: tuple[str, ...],
    principal_changed: bool,
    principal_mismatch: bool,
) -> set[ReconciliationSignal]:
    signals: set[ReconciliationSignal] = set()
    if granted == "present" and intended == "absent":
        signals.add("excess_authority")
    elif granted == "present" and intended == "unknown":
        signals.add("policy_unclassified_grant")
    if observed == "observed" and declared == "absent":
        signals.add("behavioral_drift")
    if "denied" in outcomes:
        signals.add("denied_attempt")
    if declared == "present" and granted == "absent":
        signals.add("declared_ungranted")
    if granted == "present" and observed == "not_observed":
        signals.add("granted_unobserved")
    if "allowed" in outcomes and granted == "absent":
        signals.add("allowed_ungranted")
    if intended == "present" and declared == "absent":
        signals.add("intended_undeclared")
    if declared == "present" and intended == "absent":
        signals.add("declared_unintended")
    if principal_changed:
        signals.add("principal_changed")
    if principal_mismatch:
        signals.add("principal_mismatch")
    if not signals:
        signals.add("aligned")
    return signals


def _disposition(
    signals: set[ReconciliationSignal],
    *,
    intended: MembershipState,
) -> Literal["informational", "investigate", "human_review"]:
    human_review = {
        "principal_changed",
        "principal_mismatch",
        "allowed_ungranted",
        "excess_authority",
        "intended_undeclared",
        "declared_unintended",
    }
    if signals & human_review or (
        "declared_ungranted" in signals and intended == "present"
    ):
        return "human_review"
    if signals & {"behavioral_drift", "denied_attempt", "policy_unclassified_grant"}:
        return "investigate"
    return "informational"


def _principal_change(
    target: CapabilityTargetV1,
    current_grants: list[GrantRecordV1],
    base_grants: tuple[tuple[GrantRecordV1, ObservationSourceV1, CapabilityTargetV1], ...],
) -> tuple[bool, AuthorityDeltaV1 | None]:
    current_bindings = {_principal_key(grant.principal): grant.principal for grant in current_grants}
    previous_bindings = {
        _principal_key(grant.principal): grant.principal
        for grant, _source, base_target in base_grants
        if _selector_contains(base_target, target)
    }
    if len(current_bindings) != 1 or len(previous_bindings) != 1:
        return False, None
    current_key, current = next(iter(current_bindings.items()))
    previous_key, previous = next(iter(previous_bindings.items()))
    if current_key == previous_key:
        return False, None
    return True, AuthorityDeltaV1(before=previous, after=current)


def _principal_mismatch(
    grants: list[GrantRecordV1],
    observations: list[ObservationRecordV1],
    expected: tuple[PrincipalBindingV1, ...],
    fact: CapabilityFactV1 | None,
) -> bool:
    grant_principals = [grant.principal for grant in grants]
    observed_principals = [row.principal for row in observations]
    if expected and any(
        not any(_principal_compatible(principal, expectation) for expectation in expected)
        for principal in [*grant_principals, *observed_principals]
    ):
        return True
    if grant_principals and observed_principals:
        grant_keys = {_principal_key(principal) for principal in grant_principals}
        if any(_principal_key(principal) not in grant_keys for principal in observed_principals):
            return True
    if fact is not None:
        for principal in grant_principals:
            if (
                fact.authority.credential_mode is not None
                and principal.credential_mode != fact.authority.credential_mode
            ):
                return True
            if fact.authority.auth_type is not None and principal.auth_type != fact.authority.auth_type:
                return True
            if fact.authority.source is not None and principal.authority_source != fact.authority.source:
                return True
            if fact.authority.scopes and principal.scopes != fact.authority.scopes:
                return True
    return False


def _principal_compatible(actual: PrincipalBindingV1, expected: PrincipalBindingV1) -> bool:
    if expected.kind != "unknown" and actual.kind != expected.kind:
        return False
    if expected.credential_mode != "unknown" and actual.credential_mode != expected.credential_mode:
        return False
    for field_name in ("auth_type", "authority_source"):
        expected_value = getattr(expected, field_name)
        if expected_value is not None and getattr(actual, field_name) != expected_value:
            return False
    if expected.authority_mode != "unknown" and actual.authority_mode != expected.authority_mode:
        return False
    if expected.scopes and actual.scopes != expected.scopes:
        return False
    return True


def _principal_key(principal: PrincipalBindingV1) -> str:
    payload = principal.model_dump(mode="json", exclude={"principal_ref_sha256"})
    return _canonical_json(payload)


def _proposals_for_result(result: ReconciliationResultV1) -> list[CandidatePolicyChangeV1]:
    actions: list[tuple[str, str]] = []
    signals = set(result.signals)
    if "excess_authority" in signals:
        actions.append(
            (
                "remove_or_narrow_grant",
                "Grant exceeds the reviewed intended policy; prefer least-authority removal or narrowing.",
            )
        )
    if "behavioral_drift" in signals:
        actions.append(
            (
                "investigate_runtime_route",
                "Observed behavior has no complete static declaration; investigate before changing policy.",
            )
        )
    if "denied_attempt" in signals:
        actions.append(
            (
                "investigate_denied_attempt",
                "A sandbox denial is evidence to investigate, never an allowlist recommendation.",
            )
        )
    if (
        "declared_ungranted" in signals
        and result.membership.intended == "present"
        and result.membership.granted == "absent"
    ):
        actions.append(
            (
                "consider_minimal_grant",
                "The target is independently intended and declared but absent from a complete grant snapshot.",
            )
        )
    if signals & {"principal_changed", "principal_mismatch"}:
        actions.append(
            (
                "review_principal",
                "Authority binding changed or conflicts with reviewed expectations and requires human review.",
            )
        )
    if "intended_undeclared" in signals:
        actions.append(
            (
                "add_or_correct_static_declaration",
                "Reviewed intent is not represented by the complete static capability surface.",
            )
        )
    if "declared_unintended" in signals:
        actions.append(
            (
                "review_intent_or_declaration",
                "The static capability declaration falls outside reviewed intent.",
            )
        )
    if "policy_unclassified_grant" in signals:
        actions.append(
            (
                "review_intent_policy",
                "The grant cannot be classified because intended-policy coverage is partial.",
            )
        )
    if "allowed_ungranted" in signals:
        actions.append(
            (
                "inspect_enforcement_integrity",
                "An allowed event contradicts a complete effective grant snapshot.",
            )
        )
    return [
        CandidatePolicyChangeV1(
            id=_stable_id("proposal", result.cohort_id, result.id, action),
            cohort_id=result.cohort_id,
            target=result.target,
            action=cast(str, action),
            rationale=rationale,
            evidence_refs=result.evidence_refs,
        )
        for action, rationale in actions
    ]


def _dedupe_proposals(
    proposals: Iterable[CandidatePolicyChangeV1],
) -> list[CandidatePolicyChangeV1]:
    by_key = {(row.cohort_id, _target_key(row.target), row.action): row for row in proposals}
    return sorted(by_key.values(), key=lambda row: row.id)


def _target_from_fact(fact: CapabilityFactV1) -> CapabilityTargetV1:
    if fact.evidence.source_type == "mcp" and fact.evidence.source_id:
        return McpTargetV1(
            capability_id=fact.id,
            mcp_server=fact.evidence.source_id,
            tool_name=fact.identity.tool_name,
            operation=fact.identity.operation,
        )
    return ToolTargetV1(
        capability_id=fact.id,
        tool_name=fact.identity.tool_name,
        provider=fact.identity.provider,
        operation=fact.identity.operation,
    )


def _fact_matches_composite(fact: CapabilityFactV1, target: CapabilityTargetV1) -> bool:
    if target.kind == "mcp":
        if fact.evidence.source_type != "mcp":
            return False
        if fact.evidence.source_id != target.mcp_server:
            return False
        return target.operation is None or fact.identity.operation == target.operation
    if target.kind == "tool":
        if target.provider is not None and fact.identity.provider != target.provider:
            return False
        return target.operation is None or fact.identity.operation == target.operation
    return False


def _has_composite_selector(target: CapabilityTargetV1) -> bool:
    if target.kind == "mcp":
        return True
    if target.kind == "tool":
        return target.provider is not None or target.operation is not None
    return False


def _selector_contains(selector: CapabilityTargetV1, candidate: CapabilityTargetV1) -> bool:
    if selector.kind != candidate.kind:
        return False
    if selector.kind in {"tool", "mcp"}:
        return _target_key(selector) == _target_key(candidate)
    if selector.kind == "network":
        if (
            selector.domain != candidate.domain
            or selector.scheme != candidate.scheme
            or selector.port != candidate.port
            or selector.resource_class != candidate.resource_class
        ):
            return False
        return _granularity_contains(selector.granularity, selector.locator_sha256, candidate)
    if selector.kind == "filesystem":
        if (
            selector.path_class != candidate.path_class
            or selector.resource_class != candidate.resource_class
        ):
            return False
        return _granularity_contains(selector.granularity, selector.locator_sha256, candidate)
    if selector.kind == "resource":
        if (
            selector.resource_class != candidate.resource_class
            or selector.provider != candidate.provider
        ):
            return False
        return _granularity_contains(selector.granularity, selector.locator_sha256, candidate)
    return False


def _granularity_contains(
    selector_granularity: str,
    selector_locator: str | None,
    candidate: NetworkTargetV1 | FilesystemTargetV1 | ResourceTargetV1,
) -> bool:
    if selector_granularity == "class":
        return True
    return candidate.granularity == "exact" and selector_locator == candidate.locator_sha256


def _target_key(target: CapabilityTargetV1) -> str:
    if target.kind in {"tool", "mcp"} and target.capability_id is not None:
        return f"declared:{target.capability_id}"
    return _canonical_json(target.model_dump(mode="json", exclude_none=True))


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _add_gap(
    gaps: dict[tuple[str, str, str], ReconciliationEvidenceGapV1],
    cohort_id: str,
    kind: EvidenceGapKind,
    target: CapabilityTargetV1 | None,
    message: str,
    refs: tuple[str, ...],
) -> None:
    target_key = _target_key(target) if target is not None else ""
    key = (kind, target_key, message)
    if key in gaps:
        return
    gaps[key] = ReconciliationEvidenceGapV1(
        id=_stable_id("gap", cohort_id, kind, target_key, message),
        cohort_id=cohort_id,
        kind=kind,
        target=target,
        message=message,
        evidence_refs=tuple(sorted(refs)),
    )


__all__ = [
    "load_capability_intent_policy",
    "load_capability_observation_bundle",
    "reconcile_capabilities",
    "render_capability_reconciliation_json",
]
