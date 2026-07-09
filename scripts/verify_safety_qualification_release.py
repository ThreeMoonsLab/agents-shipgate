#!/usr/bin/env python3
"""Fail-closed release validation for a signed safety qualification artifact."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

from pydantic import ValidationError

from agents_shipgate.core.errors import ConfigError
from agents_shipgate.schemas.common import ReleaseDecisionStatus
from agents_shipgate.schemas.safety_qualification import (
    SafetyQualificationResultV1,
    production_safety_requirements,
)

if __package__:
    from scripts.run_safety_qualification import DECISIONS, inspect_wheel, wilson_interval
else:  # ``python scripts/verify_safety_qualification_release.py``
    from run_safety_qualification import DECISIONS, inspect_wheel, wilson_interval


def _load_qualification(path: Path) -> SafetyQualificationResultV1:
    if not path.is_file():
        raise ConfigError(f"Safety qualification artifact not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return SafetyQualificationResultV1.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
        raise ConfigError(f"Invalid safety qualification artifact {path}: {exc}") from exc


def _append(errors: list[str], condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


def _expected_counts() -> dict[tuple[str, ReleaseDecisionStatus], int]:
    return {
        (item.profile, item.expected_decision): item.count
        for item in production_safety_requirements().required_strata
    }


def verify_release_qualification(
    *,
    wheel_path: Path,
    qualification_path: Path,
    tag: str,
) -> SafetyQualificationResultV1:
    """Validate the signed payload's production claim and exact wheel binding.

    Signature verification intentionally stays outside this function: the
    release workflow verifies the Sigstore identity before parsing the signed
    JSON here.
    """

    result = _load_qualification(qualification_path)
    wheel_name, wheel_version, wheel_sha256 = inspect_wheel(wheel_path)
    requirements = production_safety_requirements()
    errors: list[str] = []

    _append(errors, bool(tag), "release tag is empty")
    _append(errors, tag == f"v{wheel_version}", "release tag does not match wheel version")
    _append(errors, result.qualification_tier == "beta", "qualification tier is not beta")
    _append(errors, result.qualified is True, "qualification artifact is not qualified")
    _append(
        errors,
        result.production_qualified is True,
        "qualification artifact is not production_qualified",
    )
    _append(errors, result.static_only is True, "qualification must be static-only")
    _append(
        errors,
        result.runtime_behavior_proven is False,
        "qualification must not claim runtime behavior was proven",
    )
    _append(errors, result.failures == [], "qualification artifact contains failures")
    _append(
        errors,
        result.requirements == requirements,
        "qualification requirements differ from the production beta policy",
    )

    inputs = result.inputs
    _append(errors, inputs.wheel_name == wheel_name, "qualified wheel name mismatch")
    _append(errors, inputs.wheel_version == wheel_version, "qualified wheel version mismatch")
    _append(errors, inputs.engine_version == wheel_version, "qualified engine version mismatch")
    _append(errors, inputs.wheel_sha256 == wheel_sha256, "qualified wheel SHA-256 mismatch")

    expected_strata = _expected_counts()
    expected_outcomes: dict[ReleaseDecisionStatus, int] = {
        decision: sum(
            count
            for (_profile, expected_decision), count in expected_strata.items()
            if expected_decision == decision
        )
        for decision in DECISIONS
    }
    cases = result.cases
    case_ids = [case.id for case in cases]
    _append(errors, len(cases) == 100, "qualification must contain exactly 100 cases")
    _append(errors, len(set(case_ids)) == len(case_ids), "qualification case ids are not unique")

    actual_expected_counts = Counter((case.profile, case.expected_decision) for case in cases)
    _append(
        errors,
        dict(actual_expected_counts) == expected_strata,
        "qualification case profile/outcome strata differ from production policy",
    )
    _append(
        errors,
        all(case.actual_decision is not None for case in cases),
        "qualification contains a case without an actual verifier decision",
    )
    _append(
        errors,
        all(not case.runtime_failure for case in cases),
        "qualification contains a runtime or receipt failure",
    )
    _append(
        errors,
        all(case.receipt_sha256 is not None for case in cases),
        "qualification contains a case without a receipt digest",
    )
    receipt_hashes = [case.receipt_sha256 for case in cases if case.receipt_sha256]
    _append(
        errors,
        all(re.fullmatch(r"[0-9a-f]{64}", digest) for digest in receipt_hashes),
        "qualification contains a malformed receipt digest",
    )
    _append(
        errors,
        len(set(receipt_hashes)) == len(receipt_hashes),
        "qualification receipt digests are not unique per case",
    )
    _append(
        errors,
        all(case.exact == (case.actual_decision == case.expected_decision) for case in cases),
        "qualification case exact flags are inconsistent",
    )

    unsafe_auto_passes = sum(
        case.expected_decision != "passed" and case.actual_decision == "passed" for case in cases
    )
    safe_passes = sum(
        case.expected_decision == "passed" and case.actual_decision == "passed" for case in cases
    )
    blocked_exact = sum(
        case.expected_decision == "blocked" and case.actual_decision == "blocked" for case in cases
    )
    review_exact = sum(
        case.expected_decision == "review_required" and case.actual_decision == "review_required"
        for case in cases
    )
    ie_exact = sum(
        case.expected_decision == "insufficient_evidence"
        and case.actual_decision == "insufficient_evidence"
        for case in cases
    )
    exact_count = sum(case.actual_decision == case.expected_decision for case in cases)

    _append(
        errors,
        unsafe_auto_passes <= requirements.maximum_unsafe_auto_passes,
        "qualification contains an unsafe auto-pass",
    )
    _append(
        errors,
        safe_passes >= requirements.minimum_safe_passes,
        "qualification safe-pass threshold failed",
    )
    _append(
        errors,
        blocked_exact >= requirements.minimum_blocked_exact,
        "qualification blocked-exact threshold failed",
    )
    _append(
        errors,
        review_exact >= requirements.minimum_review_exact,
        "qualification review-exact threshold failed",
    )
    _append(
        errors,
        ie_exact >= requirements.minimum_insufficient_evidence_exact,
        "qualification insufficient-evidence threshold failed",
    )
    for profile in sorted({case.profile for case in cases}):
        _append(
            errors,
            not any(
                case.profile == profile
                and case.expected_decision != "passed"
                and case.actual_decision == "passed"
                for case in cases
            ),
            f"qualification profile {profile} contains an unsafe auto-pass",
        )

    summary = result.summary
    _append(errors, summary.total_cases == 100, "qualification summary total is not 100")
    _append(
        errors,
        summary.qualified_origin_cases >= requirements.minimum_qualified_origins,
        "qualification lacks the required real-history/design-partner cases",
    )
    _append(
        errors,
        summary.cohen_kappa >= requirements.minimum_kappa,
        "qualification label agreement is below the production threshold",
    )
    _append(
        errors,
        0 <= summary.primary_label_agreements <= summary.total_cases,
        "qualification primary-label agreement count is invalid",
    )
    _append(
        errors,
        requirements.minimum_kappa <= summary.cohen_kappa <= 1.0,
        "qualification Cohen's kappa is outside its valid production range",
    )
    _append(errors, summary.receipt_count == 100, "qualification receipt count is not 100")
    _append(errors, summary.runtime_failure_count == 0, "qualification reports runtime failures")
    _append(
        errors,
        summary.unsafe_auto_pass_count == unsafe_auto_passes == 0,
        "qualification unsafe-auto-pass summary is inconsistent",
    )
    _append(
        errors,
        summary.exact_count == exact_count,
        "qualification exact-count summary is inconsistent",
    )
    _append(
        errors,
        summary.outcome_counts == expected_outcomes,
        "qualification outcome-count summary is inconsistent",
    )

    strata_by_key = {(row.profile, row.expected_decision): row for row in result.strata}
    _append(
        errors,
        len(strata_by_key) == len(result.strata) == len(expected_strata),
        "qualification strata are missing or duplicated",
    )
    for key, required_count in sorted(expected_strata.items()):
        row = strata_by_key.get(key)
        if row is None:
            errors.append(f"qualification is missing stratum {key[0]}/{key[1]}")
            continue
        stratum_cases = [case for case in cases if (case.profile, case.expected_decision) == key]
        holdout_count = sum(case.split == "holdout" for case in stratum_cases)
        minimum_holdout = math.ceil(
            required_count * requirements.minimum_holdout_fraction_per_stratum
        )
        _append(errors, row.required_count == required_count, f"stratum {key} policy mismatch")
        _append(errors, row.actual_count == len(stratum_cases), f"stratum {key} count mismatch")
        _append(
            errors,
            row.tuning_count + row.holdout_count == row.actual_count,
            f"stratum {key} split counts are inconsistent",
        )
        _append(
            errors,
            row.holdout_count == holdout_count and holdout_count >= minimum_holdout,
            f"stratum {key} holdout requirement failed",
        )
        _append(
            errors,
            row.minimum_holdout_count == minimum_holdout,
            f"stratum {key} minimum holdout is inconsistent",
        )
        _append(
            errors,
            row.receipt_count == len(stratum_cases),
            f"stratum {key} receipt count is incomplete",
        )
        _append(errors, row.runtime_failure_count == 0, f"stratum {key} has runtime failures")
        _append(errors, row.unsafe_auto_pass_count == 0, f"stratum {key} has unsafe passes")

    metrics = {metric.name: metric for metric in result.intervals}
    expected_metric_names = {
        "unsafe_auto_pass_rate",
        "safe_pass_rate",
        "blocked_exact_rate",
        "review_exact_rate",
        "insufficient_evidence_exact_rate",
        "overall_exact_rate",
    }
    _append(
        errors,
        set(metrics) == expected_metric_names and len(metrics) == len(result.intervals),
        "qualification metric interval set is incomplete or duplicated",
    )
    _append(
        errors,
        all(metric.passed for metric in result.intervals),
        "qualification contains a failed metric interval",
    )
    expected_metric_counts = {
        "unsafe_auto_pass_rate": (unsafe_auto_passes, 70),
        "safe_pass_rate": (safe_passes, 30),
        "blocked_exact_rate": (blocked_exact, 30),
        "review_exact_rate": (review_exact, 20),
        "insufficient_evidence_exact_rate": (ie_exact, 20),
        "overall_exact_rate": (exact_count, 100),
    }
    for metric in result.intervals:
        _append(
            errors,
            (metric.numerator, metric.denominator) == expected_metric_counts.get(metric.name),
            f"qualification metric {metric.name} counts are inconsistent",
        )
        expected_rate = metric.numerator / metric.denominator if metric.denominator else 0.0
        _append(
            errors,
            math.isclose(metric.rate, expected_rate, abs_tol=0.000001),
            f"qualification metric {metric.name} rate is inconsistent",
        )
        expected_interval = wilson_interval(metric.numerator, metric.denominator)
        _append(
            errors,
            metric.interval == expected_interval,
            f"qualification metric {metric.name} Wilson interval is inconsistent",
        )
        _append(
            errors,
            0.0 <= metric.interval.lower <= metric.interval.upper <= 1.0,
            f"qualification metric {metric.name} has an invalid interval",
        )

    expected_matrix_profiles = {
        "all",
        "mcp",
        "openapi",
        "explicit_framework_inventory",
        "coding_agent_trust_roots",
    }
    matrices = {matrix.profile: matrix for matrix in result.confusion_matrices}
    _append(
        errors,
        set(matrices) == expected_matrix_profiles
        and len(matrices) == len(result.confusion_matrices),
        "qualification confusion matrices are missing or duplicated",
    )
    for profile in sorted(expected_matrix_profiles):
        matrix = matrices.get(profile)
        if matrix is None:
            continue
        selected = (
            cases if profile == "all" else [case for case in cases if case.profile == profile]
        )
        rows = {row.expected: row for row in matrix.rows}
        _append(
            errors,
            set(rows) == set(DECISIONS) and len(rows) == len(matrix.rows),
            f"qualification confusion matrix {profile} has incomplete rows",
        )
        for expected in DECISIONS:
            row = rows.get(expected)
            if row is None:
                continue
            expected_actual = {
                actual: sum(
                    case.expected_decision == expected and case.actual_decision == actual
                    for case in selected
                )
                for actual in DECISIONS
            }
            _append(
                errors,
                row.actual == expected_actual,
                f"qualification confusion matrix {profile}/{expected} is inconsistent",
            )

    if errors:
        raise ConfigError(
            "Release safety qualification rejected: " + "; ".join(sorted(set(errors)))
        )
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify production safety qualification and exact release-wheel binding."
    )
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--qualification", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = verify_release_qualification(
            wheel_path=args.wheel,
            qualification_path=args.qualification,
            tag=args.tag,
        )
    except (ConfigError, OSError, ValueError) as exc:
        sys.stderr.write(f"Release safety qualification error: {exc}\n")
        return 1
    sys.stdout.write(
        "OK: production safety qualification is internally consistent and bound to "
        f"{result.inputs.wheel_name} {result.inputs.wheel_version} "
        f"({result.inputs.wheel_sha256}); signature verification remains a separate prerequisite.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
