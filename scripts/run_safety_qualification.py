#!/usr/bin/env python3
"""Build a deterministic, fail-closed beta safety qualification artifact.

This runner does not execute Agents Shipgate and never substitutes a scan for a
verifier receipt. It validates frozen human labels, content-addressed verifier
artifacts, and the exact built wheel before scoring the beta acceptance policy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import zipfile
from collections import Counter
from collections.abc import Iterable
from email.parser import BytesParser
from email.policy import default as email_policy
from fractions import Fraction
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from agents_shipgate.core.errors import ConfigError
from agents_shipgate.schemas.common import ReleaseDecisionStatus
from agents_shipgate.schemas.disclaimers import STATIC_VERDICT_DISCLAIMER
from agents_shipgate.schemas.report import ReadinessReport
from agents_shipgate.schemas.safety_qualification import (
    FrozenSafetyCorpusV1,
    QualificationInputDigestV1,
    SafetyConfusionMatrixV1,
    SafetyConfusionRowV1,
    SafetyCorpusCaseV1,
    SafetyProfile,
    SafetyQualificationCaseResultV1,
    SafetyQualificationFailureV1,
    SafetyQualificationMetricV1,
    SafetyQualificationRequirementsV1,
    SafetyQualificationResultV1,
    SafetyQualificationStratumV1,
    SafetyQualificationSummaryV1,
    SafetyReceiptEntryV1,
    SafetyReceiptIndexV1,
    WilsonIntervalV1,
    production_safety_requirements,
)
from agents_shipgate.schemas.verifier import VerifierArtifact
from agents_shipgate.schemas.verify_run import VerifyRunArtifact

DECISIONS: tuple[ReleaseDecisionStatus, ...] = (
    "passed",
    "review_required",
    "insufficient_evidence",
    "blocked",
)
QUALIFYING_ORIGINS = {"real_history", "rejected_or_reverted", "design_partner"}
EXPECTED_MERGE_VERDICT = {
    "passed": "mergeable",
    "review_required": "human_review_required",
    "insufficient_evidence": "insufficient_evidence",
    "blocked": "blocked",
}


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_mapping(path: Path, *, description: str) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"{description} not found: {path}")
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ConfigError(f"Unable to read {description} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigError(f"{description} must contain one JSON/YAML object: {path}")
    return value


def load_frozen_corpus(path: Path) -> FrozenSafetyCorpusV1:
    try:
        return FrozenSafetyCorpusV1.model_validate(
            _load_mapping(path, description="Frozen safety corpus")
        )
    except ValidationError as exc:
        raise ConfigError(f"Invalid frozen safety corpus {path}: {exc}") from exc


def load_receipt_index(path: Path) -> SafetyReceiptIndexV1:
    try:
        return SafetyReceiptIndexV1.model_validate(
            _load_mapping(path, description="Safety receipt index")
        )
    except ValidationError as exc:
        raise ConfigError(f"Invalid safety receipt index {path}: {exc}") from exc


def inspect_wheel(path: Path) -> tuple[str, str, str]:
    """Return canonical distribution name, version, and content digest."""

    if not path.is_file() or path.suffix != ".whl":
        raise ConfigError(f"Built wheel not found or not a .whl file: {path}")
    try:
        with zipfile.ZipFile(path) as archive:
            metadata_names = [
                name
                for name in archive.namelist()
                if name.endswith(".dist-info/METADATA") and "/" in name
            ]
            if len(metadata_names) != 1:
                raise ConfigError(
                    f"Wheel must contain exactly one .dist-info/METADATA file: {path}"
                )
            metadata = BytesParser(policy=email_policy).parsebytes(archive.read(metadata_names[0]))
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        raise ConfigError(f"Invalid built wheel {path}: {exc}") from exc

    name = str(metadata.get("Name", "")).strip()
    version = str(metadata.get("Version", "")).strip()
    canonical_name = name.lower().replace("_", "-").replace(".", "-")
    if canonical_name != "agents-shipgate" or not version:
        raise ConfigError(
            f"Qualification requires an agents-shipgate wheel with Name and Version: {path}"
        )
    return canonical_name, version, sha256_file(path)


def hash_policy_bundle(paths: Iterable[Path]) -> str:
    """Hash policy inputs by stable logical name and file contents."""

    records: dict[str, str] = {}
    supplied = list(paths)
    if not supplied:
        raise ConfigError("At least one --policy file or directory is required")
    for root in supplied:
        if root.is_file():
            entries = [(root.name, root)]
        elif root.is_dir():
            entries = [
                (f"{root.name}/{item.relative_to(root).as_posix()}", item)
                for item in sorted(root.rglob("*"))
                if item.is_file()
            ]
        else:
            raise ConfigError(f"Qualification policy input not found: {root}")
        if not entries:
            raise ConfigError(f"Qualification policy directory is empty: {root}")
        for logical_name, file_path in entries:
            if logical_name in records:
                raise ConfigError(f"Duplicate qualification policy logical path: {logical_name}")
            records[logical_name] = sha256_file(file_path)
    encoded = json.dumps(records, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _sha256_bytes(encoded)


def _confined_path(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    resolved_root = root.resolve()
    if not candidate.is_relative_to(resolved_root):
        raise ValueError(f"path escapes qualification receipt root: {relative}")
    return candidate


def _normalize_sha256(value: str | None) -> str | None:
    if value is None:
        return None
    return value.removeprefix("sha256:")


def _read_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"artifact is not a JSON object: {path.name}")
    return value


def _receipt_artifact(
    *,
    root: Path,
    entry: SafetyReceiptEntryV1,
    key: str,
    receipt: VerifyRunArtifact,
) -> tuple[Path, dict[str, Any]]:
    ref = receipt.artifacts.get(key)
    if ref is None or not ref.sha256:
        raise ValueError(f"verify-run receipt is missing content-addressed {key}")
    artifact_path = _confined_path(root, ref.path)
    if not artifact_path.is_file():
        raise ValueError(f"verify-run {key} artifact is missing: {ref.path}")
    actual_hash = sha256_file(artifact_path)
    if actual_hash != _normalize_sha256(ref.sha256):
        raise ValueError(f"verify-run {key} artifact hash mismatch: {ref.path}")
    return artifact_path, _read_json_object(artifact_path)


def _evaluate_receipt(
    *,
    index_root: Path,
    entry: SafetyReceiptEntryV1,
    wheel_version: str,
    report_schema_version: str,
) -> tuple[ReleaseDecisionStatus | None, str | None, list[str]]:
    errors: list[str] = []
    try:
        receipt_path = _confined_path(index_root, entry.receipt_path)
        artifact_root = _confined_path(index_root, entry.artifact_root)
        if not receipt_path.is_file():
            raise ValueError(f"verify-run receipt is missing: {entry.receipt_path}")
        receipt_hash = sha256_file(receipt_path)
        if receipt_hash != entry.receipt_sha256:
            raise ValueError(f"verify-run receipt hash mismatch: {entry.receipt_path}")
        receipt = VerifyRunArtifact.model_validate(_read_json_object(receipt_path))
        if receipt.run_id != entry.run_id:
            raise ValueError("receipt-index run_id does not match verify-run receipt")
        if receipt.tool.name != "agents-shipgate" or receipt.tool.version != wheel_version:
            raise ValueError("verify-run tool version is not the exact built wheel version")
        if receipt.subject.base_ref is None:
            raise ValueError("qualification receipt must identify a base ref")
        if not receipt.subject.base_tree_sha or not receipt.subject.head_tree_sha:
            raise ValueError("qualification receipt must bind base and head tree SHAs")
        if receipt.subject.base_tree_sha == receipt.subject.head_tree_sha:
            raise ValueError("qualification receipt base and head trees must differ")
        if receipt.outcome.base_status not in {"succeeded", "cache_hit"}:
            raise ValueError("qualification receipt base verifier did not succeed")
        if receipt.outcome.head_status != "succeeded":
            raise ValueError("qualification receipt head verifier did not succeed")
        if not receipt.inputs.config_sha256:
            raise ValueError("qualification receipt is missing its config digest")

        verifier_path, verifier_payload = _receipt_artifact(
            root=artifact_root,
            entry=entry,
            key="verifier_json",
            receipt=receipt,
        )
        verifier = VerifierArtifact.model_validate(verifier_payload)
        _report_path, report = _receipt_artifact(
            root=artifact_root,
            entry=entry,
            key="report_json",
            receipt=receipt,
        )
        report_model = ReadinessReport.model_validate(report)
        if report_model.report_schema_version != report_schema_version:
            raise ValueError(
                "qualification report schema mismatch: expected "
                f"{report_schema_version}, got {report_model.report_schema_version!r}"
            )

        report_release = report.get("release_decision")
        if not isinstance(report_release, dict):
            raise ValueError("qualification report has no release_decision object")
        if report_release.get("static_analysis_only") is not True:
            raise ValueError("qualification report must declare static_analysis_only=true")
        if report_release.get("runtime_behavior_verified") is not False:
            raise ValueError("qualification report must not claim runtime behavior was verified")
        if report_release.get("static_verdict_disclaimer") != STATIC_VERDICT_DISCLAIMER:
            raise ValueError("qualification report has a missing or changed static disclaimer")
        if report_model.release_decision is None:
            raise ValueError("qualification report release_decision failed typed validation")
        report_decision = report_model.release_decision.decision
        receipt_decision = receipt.outcome.decision
        verifier_decision = verifier.decision
        if receipt_decision not in DECISIONS:
            raise ValueError(f"verify-run emitted unknown decision: {receipt_decision!r}")
        if report_decision != receipt_decision or verifier_decision != receipt_decision:
            raise ValueError("verify-run, verifier, and report decisions are not identical")
        expected_merge = EXPECTED_MERGE_VERDICT[receipt_decision]
        if receipt.outcome.merge_verdict != expected_merge:
            raise ValueError("verify-run decision and merge_verdict are inconsistent")
        expected_can_merge = receipt_decision == "passed"
        if receipt.outcome.can_merge_without_human != expected_can_merge:
            raise ValueError("verify-run decision and can_merge_without_human are inconsistent")
        if verifier.merge_verdict != receipt.outcome.merge_verdict:
            raise ValueError("verifier and verify-run merge_verdict values differ")
        if verifier.can_merge_without_human != receipt.outcome.can_merge_without_human:
            raise ValueError("verifier and verify-run merge permission values differ")
        if verifier.head_status != "succeeded":
            raise ValueError(f"verifier artifact did not succeed: {verifier_path.name}")
        if verifier.base_status not in {"succeeded", "cache_hit"}:
            raise ValueError("verifier artifact base run did not succeed")
        if verifier.head_exit_code != receipt.outcome.exit_code:
            raise ValueError("verifier and verify-run exit codes differ")
        if verifier.config != receipt.subject.config:
            raise ValueError("verifier and verify-run config bindings differ")
        if verifier.mode != receipt.inputs.ci_mode:
            raise ValueError("verifier and verify-run CI modes differ")
        if verifier.artifacts.get("report_json") != receipt.artifacts["report_json"].path:
            raise ValueError("verifier and verify-run report artifact paths differ")
        if (
            verifier.base_tree_sha != receipt.subject.base_tree_sha
            or verifier.head_tree_sha != receipt.subject.head_tree_sha
            or verifier.base_ref != receipt.subject.base_ref
            or verifier.head_ref != receipt.subject.head_ref
        ):
            raise ValueError("verifier and verify-run subject bindings differ")

        evidence_coverage = report_release.get("evidence_coverage")
        semantic_coverage = (
            evidence_coverage.get("semantic_coverage")
            if isinstance(evidence_coverage, dict)
            else None
        )
        if not isinstance(semantic_coverage, dict):
            raise ValueError("qualification report is missing semantic_coverage")
        required_semantic_fields = {
            "total_actions",
            "pass_eligible_actions",
            "gap_count",
            "review_concern_count",
            "reason_counts",
        }
        if not required_semantic_fields.issubset(semantic_coverage):
            raise ValueError("qualification report semantic_coverage is incomplete")
        if receipt_decision == "passed" and (
            semantic_coverage["gap_count"] != 0
            or semantic_coverage["review_concern_count"] != 0
            or semantic_coverage["pass_eligible_actions"] != semantic_coverage["total_actions"]
        ):
            raise ValueError("passed receipt violates evidence-backed pass invariants")
        binding_coverage = evidence_coverage.get("binding_coverage")
        if not isinstance(binding_coverage, dict):
            raise ValueError("qualification report is missing binding_coverage")
        required_binding_fields = {
            "total_catalog_tools",
            "reachable_tools",
            "possible_tools",
            "unbound_tools",
            "pass_eligible",
            "gap_count",
            "reason_counts",
        }
        if not required_binding_fields.issubset(binding_coverage):
            raise ValueError("qualification report binding_coverage is incomplete")
        if receipt_decision == "passed" and (
            binding_coverage["gap_count"] != 0
            or binding_coverage["possible_tools"] != 0
            or binding_coverage["pass_eligible"] is not True
        ):
            raise ValueError("passed receipt violates binding-backed pass invariants")
        return receipt_decision, receipt_hash, []
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        errors.append(str(exc))
    return None, None, errors


def cohen_kappa(cases: Iterable[SafetyCorpusCaseV1]) -> tuple[float, int]:
    rows = list(cases)
    if not rows:
        return -1.0, 0
    agreements = sum(
        case.security_governance_label.decision == case.framework_tooling_label.decision
        for case in rows
    )
    left = Counter(case.security_governance_label.decision for case in rows)
    right = Counter(case.framework_tooling_label.decision for case in rows)
    observed = Fraction(agreements, len(rows))
    expected = sum(
        Fraction(left[item], len(rows)) * Fraction(right[item], len(rows)) for item in DECISIONS
    )
    if expected == 1:
        # Kappa is undefined when both raters use only one category. Treat
        # undefined agreement as below threshold instead of converting it to
        # a perfect score.
        return -1.0, agreements
    return float((observed - expected) / (1 - expected)), agreements


def wilson_interval(numerator: int, denominator: int) -> WilsonIntervalV1:
    if denominator <= 0:
        return WilsonIntervalV1(lower=0.0, upper=1.0)
    z = 1.959963984540054
    proportion = numerator / denominator
    adjustment = z * z / denominator
    center = (proportion + adjustment / 2.0) / (1.0 + adjustment)
    margin = (
        z
        * math.sqrt(
            proportion * (1.0 - proportion) / denominator
            + z * z / (4.0 * denominator * denominator)
        )
        / (1.0 + adjustment)
    )
    return WilsonIntervalV1(
        lower=round(max(0.0, center - margin), 6),
        upper=round(min(1.0, center + margin), 6),
    )


def _metric(
    *,
    name: str,
    numerator: int,
    denominator: int,
    requirement: str,
    passed: bool,
) -> SafetyQualificationMetricV1:
    return SafetyQualificationMetricV1(
        name=name,
        numerator=numerator,
        denominator=denominator,
        rate=round(numerator / denominator, 6) if denominator else 0.0,
        interval=wilson_interval(numerator, denominator),
        requirement=requirement,
        passed=passed,
    )


def _failure(
    failures: list[SafetyQualificationFailureV1],
    *,
    code: str,
    message: str,
    case: SafetyCorpusCaseV1 | None = None,
    profile: SafetyProfile | None = None,
    expected_decision: ReleaseDecisionStatus | None = None,
) -> None:
    failures.append(
        SafetyQualificationFailureV1(
            code=code,
            message=message,
            case_id=case.id if case else None,
            profile=case.profile if case else profile,
            expected_decision=(case.expected_decision if case else expected_decision),
        )
    )


def _confusion_matrix(
    cases: list[SafetyQualificationCaseResultV1],
    *,
    profile: SafetyProfile | str,
) -> SafetyConfusionMatrixV1:
    selected = cases if profile == "all" else [case for case in cases if case.profile == profile]
    rows = []
    for expected in DECISIONS:
        actual_counts: dict[ReleaseDecisionStatus, int] = {decision: 0 for decision in DECISIONS}
        for case in selected:
            if case.expected_decision == expected and case.actual_decision is not None:
                actual_counts[case.actual_decision] += 1
        rows.append(SafetyConfusionRowV1(expected=expected, actual=actual_counts))
    return SafetyConfusionMatrixV1(profile=profile, rows=rows)  # type: ignore[arg-type]


def run_safety_qualification(
    *,
    wheel_path: Path,
    corpus_path: Path,
    receipt_index_path: Path,
    policy_paths: Iterable[Path],
    requirements: SafetyQualificationRequirementsV1 | None = None,
) -> SafetyQualificationResultV1:
    active_requirements = requirements or production_safety_requirements()
    production_requirements = production_safety_requirements()
    production_tier = active_requirements == production_requirements

    wheel_name, wheel_version, wheel_sha256 = inspect_wheel(wheel_path)
    corpus = load_frozen_corpus(corpus_path)
    receipt_index = load_receipt_index(receipt_index_path)
    corpus_sha256 = sha256_file(corpus_path)
    receipt_index_sha256 = sha256_file(receipt_index_path)
    policy_sha256 = hash_policy_bundle(policy_paths)

    failures: list[SafetyQualificationFailureV1] = []
    bindings = {
        "wheel_sha256": (receipt_index.wheel_sha256, wheel_sha256),
        "engine_version": (receipt_index.engine_version, wheel_version),
        "corpus_sha256": (receipt_index.corpus_sha256, corpus_sha256),
        "labels_sha256": (receipt_index.labels_sha256, corpus.frozen_labels_sha256),
        "policy_sha256": (receipt_index.policy_sha256, policy_sha256),
    }
    for binding, (declared, actual) in bindings.items():
        if declared != actual:
            _failure(
                failures,
                code="input_binding_mismatch",
                message=f"{binding} in the receipt index does not match the supplied input",
            )

    requirements_by_stratum = {
        (item.profile, item.expected_decision): item.count
        for item in active_requirements.required_strata
    }
    if len(requirements_by_stratum) != len(active_requirements.required_strata):
        raise ConfigError("Qualification requirements contain duplicate strata")

    corpus_counts = Counter((case.profile, case.expected_decision) for case in corpus.cases)
    extra_strata = sorted(set(corpus_counts) - set(requirements_by_stratum))
    for profile, decision in extra_strata:
        _failure(
            failures,
            code="unexpected_corpus_stratum",
            message="corpus contains a profile/outcome stratum not allowed by the policy",
            profile=profile,
            expected_decision=decision,
        )

    receipts_by_case = {entry.case_id: entry for entry in receipt_index.receipts}
    corpus_ids = {case.id for case in corpus.cases}
    for case_id in sorted(set(receipts_by_case) - corpus_ids):
        _failure(
            failures,
            code="unexpected_receipt",
            message=f"receipt index contains a case absent from the frozen corpus: {case_id}",
        )

    case_results: list[SafetyQualificationCaseResultV1] = []
    for case in corpus.cases:
        entry = receipts_by_case.get(case.id)
        if entry is None:
            _failure(
                failures,
                code="missing_receipt",
                message="frozen corpus case has no verify-run receipt",
                case=case,
            )
            case_results.append(
                SafetyQualificationCaseResultV1(
                    id=case.id,
                    profile=case.profile,
                    split=case.split,
                    expected_decision=case.expected_decision,
                    runtime_failure=True,
                )
            )
            continue
        actual, receipt_sha256, receipt_errors = _evaluate_receipt(
            index_root=receipt_index_path.parent,
            entry=entry,
            wheel_version=wheel_version,
            report_schema_version=active_requirements.required_report_schema_version,
        )
        if receipt_errors:
            for error in receipt_errors:
                _failure(
                    failures,
                    code="invalid_verifier_receipt",
                    message=error,
                    case=case,
                )
        result = SafetyQualificationCaseResultV1(
            id=case.id,
            profile=case.profile,
            split=case.split,
            expected_decision=case.expected_decision,
            actual_decision=actual,
            exact=actual == case.expected_decision,
            receipt_sha256=receipt_sha256,
            runtime_failure=bool(receipt_errors),
        )
        case_results.append(result)
        if actual == "passed" and case.expected_decision != "passed":
            _failure(
                failures,
                code="unsafe_auto_pass",
                message="human-adjudicated non-safe case emitted passed",
                case=case,
            )

    qualified_origin_cases = sum(case.origin in QUALIFYING_ORIGINS for case in corpus.cases)
    if qualified_origin_cases < active_requirements.minimum_qualified_origins:
        _failure(
            failures,
            code="qualified_origin_shortfall",
            message=(
                f"need at least {active_requirements.minimum_qualified_origins} real-history, "
                "rejected/reverted, or design-partner cases; "
                f"found {qualified_origin_cases}"
            ),
        )

    kappa, primary_agreements = cohen_kappa(corpus.cases)
    if kappa < active_requirements.minimum_kappa:
        _failure(
            failures,
            code="label_agreement_below_threshold",
            message=(f"Cohen's kappa {kappa:.6f} is below {active_requirements.minimum_kappa:.6f}"),
        )

    strata: list[SafetyQualificationStratumV1] = []
    for profile, decision in sorted(requirements_by_stratum):
        required_count = requirements_by_stratum[(profile, decision)]
        cases = [
            case
            for case in corpus.cases
            if case.profile == profile and case.expected_decision == decision
        ]
        results = [
            result
            for result in case_results
            if result.profile == profile and result.expected_decision == decision
        ]
        holdout_count = sum(case.split == "holdout" for case in cases)
        minimum_holdout = math.ceil(
            required_count * active_requirements.minimum_holdout_fraction_per_stratum
        )
        if len(cases) != required_count:
            _failure(
                failures,
                code="corpus_stratum_mismatch",
                message=f"stratum requires {required_count} cases; found {len(cases)}",
                profile=profile,
                expected_decision=decision,
            )
        if holdout_count < minimum_holdout:
            _failure(
                failures,
                code="holdout_shortfall",
                message=(
                    f"stratum requires at least {minimum_holdout} frozen holdout cases; "
                    f"found {holdout_count}"
                ),
                profile=profile,
                expected_decision=decision,
            )
        unsafe_count = sum(
            result.actual_decision == "passed" and decision != "passed" for result in results
        )
        strata.append(
            SafetyQualificationStratumV1(
                profile=profile,
                expected_decision=decision,
                required_count=required_count,
                actual_count=len(cases),
                tuning_count=sum(case.split == "tuning" for case in cases),
                holdout_count=holdout_count,
                minimum_holdout_count=minimum_holdout,
                receipt_count=sum(result.actual_decision is not None for result in results),
                unsafe_auto_pass_count=unsafe_count,
                runtime_failure_count=sum(result.runtime_failure for result in results),
            )
        )

    unsafe_denominator = sum(result.expected_decision != "passed" for result in case_results)
    unsafe_auto_passes = sum(
        result.expected_decision != "passed" and result.actual_decision == "passed"
        for result in case_results
    )
    safe_denominator = sum(result.expected_decision == "passed" for result in case_results)
    safe_passes = sum(
        result.expected_decision == "passed" and result.actual_decision == "passed"
        for result in case_results
    )
    blocked_denominator = sum(result.expected_decision == "blocked" for result in case_results)
    blocked_exact = sum(
        result.expected_decision == "blocked" and result.actual_decision == "blocked"
        for result in case_results
    )
    review_denominator = sum(
        result.expected_decision == "review_required" for result in case_results
    )
    review_exact = sum(
        result.expected_decision == "review_required"
        and result.actual_decision == "review_required"
        for result in case_results
    )
    ie_denominator = sum(
        result.expected_decision == "insufficient_evidence" for result in case_results
    )
    ie_exact = sum(
        result.expected_decision == "insufficient_evidence"
        and result.actual_decision == "insufficient_evidence"
        for result in case_results
    )
    exact_count = sum(result.exact for result in case_results)
    runtime_failures = sum(result.runtime_failure for result in case_results)

    metrics = [
        _metric(
            name="unsafe_auto_pass_rate",
            numerator=unsafe_auto_passes,
            denominator=unsafe_denominator,
            requirement=f"<= {active_requirements.maximum_unsafe_auto_passes} cases",
            passed=unsafe_auto_passes <= active_requirements.maximum_unsafe_auto_passes,
        ),
        _metric(
            name="safe_pass_rate",
            numerator=safe_passes,
            denominator=safe_denominator,
            requirement=f">= {active_requirements.minimum_safe_passes} cases",
            passed=safe_passes >= active_requirements.minimum_safe_passes,
        ),
        _metric(
            name="blocked_exact_rate",
            numerator=blocked_exact,
            denominator=blocked_denominator,
            requirement=f">= {active_requirements.minimum_blocked_exact} cases",
            passed=blocked_exact >= active_requirements.minimum_blocked_exact,
        ),
        _metric(
            name="review_exact_rate",
            numerator=review_exact,
            denominator=review_denominator,
            requirement=f">= {active_requirements.minimum_review_exact} cases",
            passed=review_exact >= active_requirements.minimum_review_exact,
        ),
        _metric(
            name="insufficient_evidence_exact_rate",
            numerator=ie_exact,
            denominator=ie_denominator,
            requirement=(f">= {active_requirements.minimum_insufficient_evidence_exact} cases"),
            passed=ie_exact >= active_requirements.minimum_insufficient_evidence_exact,
        ),
        _metric(
            name="overall_exact_rate",
            numerator=exact_count,
            denominator=len(case_results),
            requirement="reported for audit; outcome-specific thresholds govern",
            passed=True,
        ),
    ]
    for metric in metrics:
        if not metric.passed:
            _failure(
                failures,
                code="beta_threshold_failed",
                message=(
                    f"{metric.name}: {metric.numerator}/{metric.denominator}; "
                    f"required {metric.requirement}"
                ),
            )
    if runtime_failures:
        _failure(
            failures,
            code="runtime_failure",
            message=f"{runtime_failures} case(s) lacked a complete, valid verifier receipt",
        )
    for profile in sorted({case.profile for case in corpus.cases}):
        unsafe_count = sum(
            result.profile == profile
            and result.expected_decision != "passed"
            and result.actual_decision == "passed"
            for result in case_results
        )
        if unsafe_count:
            _failure(
                failures,
                code="profile_unsafe_auto_pass",
                message=f"profile produced {unsafe_count} unsafe auto-pass(es)",
                profile=profile,
            )
        profile_runtime_failures = sum(
            result.profile == profile and result.runtime_failure for result in case_results
        )
        if profile_runtime_failures:
            _failure(
                failures,
                code="profile_runtime_failure",
                message=f"profile produced {profile_runtime_failures} runtime failure(s)",
                profile=profile,
            )

    confusion_matrices = [_confusion_matrix(case_results, profile="all")]
    confusion_matrices.extend(
        _confusion_matrix(case_results, profile=profile)
        for profile in sorted({case.profile for case in corpus.cases})
    )
    outcome_counts: dict[ReleaseDecisionStatus, int] = {
        decision: sum(result.expected_decision == decision for result in case_results)
        for decision in DECISIONS
    }
    qualified = not failures
    return SafetyQualificationResultV1(
        qualification_tier="beta" if production_tier else "test",
        qualified=qualified,
        production_qualified=qualified and production_tier,
        inputs=QualificationInputDigestV1(
            wheel_name=wheel_name,
            wheel_version=wheel_version,
            engine_version=wheel_version,
            wheel_sha256=wheel_sha256,
            corpus_name=corpus_path.name,
            corpus_id=corpus.corpus_id,
            corpus_sha256=corpus_sha256,
            labels_sha256=corpus.frozen_labels_sha256,
            policy_sha256=policy_sha256,
            receipt_index_name=receipt_index_path.name,
            receipt_index_sha256=receipt_index_sha256,
        ),
        requirements=active_requirements,
        summary=SafetyQualificationSummaryV1(
            total_cases=len(corpus.cases),
            qualified_origin_cases=qualified_origin_cases,
            primary_label_agreements=primary_agreements,
            cohen_kappa=round(kappa, 6),
            receipt_count=sum(result.actual_decision is not None for result in case_results),
            exact_count=exact_count,
            unsafe_auto_pass_count=unsafe_auto_passes,
            runtime_failure_count=runtime_failures,
            outcome_counts=outcome_counts,
        ),
        strata=strata,
        confusion_matrices=confusion_matrices,
        intervals=metrics,
        cases=case_results,
        failures=failures,
    )


def render_safety_qualification_json(result: SafetyQualificationResultV1) -> str:
    return json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"


def qualification_exit_code(result: SafetyQualificationResultV1) -> int:
    return 0 if result.production_qualified else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate frozen labels and real verifier receipts for beta qualification."
    )
    parser.add_argument("--wheel", type=Path, required=True, help="Built agents-shipgate wheel")
    parser.add_argument("--corpus", type=Path, required=True, help="Frozen labeled corpus")
    parser.add_argument(
        "--receipts", type=Path, required=True, help="Content-addressed receipt index"
    )
    parser.add_argument(
        "--policy",
        type=Path,
        action="append",
        required=True,
        help="Qualification policy file or directory; repeatable",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("safety-qualification.json"),
        help="Deterministic output path",
    )
    parser.add_argument("--json", action="store_true", help="Also write the artifact to stdout")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = run_safety_qualification(
            wheel_path=args.wheel,
            corpus_path=args.corpus,
            receipt_index_path=args.receipts,
            policy_paths=args.policy,
        )
    except (ConfigError, OSError, ValueError) as exc:
        sys.stderr.write(f"Safety qualification configuration error: {exc}\n")
        return 2
    rendered = render_safety_qualification_json(result)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(rendered, encoding="utf-8")
    if args.json:
        sys.stdout.write(rendered)
    else:
        sys.stdout.write(f"Wrote safety qualification to {args.out}\n")
    return qualification_exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
