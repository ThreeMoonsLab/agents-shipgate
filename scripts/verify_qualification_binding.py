#!/usr/bin/env python3
"""Re-derive the decisive qualification invariants without the project installed.

`scripts/verify_safety_qualification_release.py` re-derives the *whole* policy —
every stratum, Wilson interval, and confusion matrix — but it imports the
project's pydantic schemas, which means the job running it has the editable
package and the ranged `dev` extra installed. That job is a fine place for a
thorough gate, and a poor place to seal a release: a compromised compatible
dependency there could rewrite the verifier itself.

This module exists so the sealing job can restate the claims that actually
delegate publication authority, using nothing but the standard library:

* the artifact is **qualified** under a policy the tag's version admits --
  the 100-case ``beta`` policy, or, for a ``0.x`` tag only, the 56-case
  ``pre_1_0`` policy approved in
  ``docs/release-evidence-policy-decision.md`` (issue #341);
* it claims ``production_qualified`` exactly when it claims the ``beta`` tier;
* it is static-only and does not claim runtime behaviour was proven;
* it carries no failures, and exactly as many cases and receipts as that
  policy requires, in exactly that policy's 28 profile x outcome strata;
* every case has a unique, non-blank id and a terminal verifier decision;
* it meets that policy's per-outcome exact-match floors and per-stratum
  holdout minimum, both re-derived from the raw cases;
* its declared ``requirements`` block *is* that policy, field for field --
  including the report schema version, which nothing in ``cases`` can attest;
* the envelope it claims can express the tier it claims;
* it reports **zero** unsafe auto-passes, overall and per profile;
* and — the binding that matters — its recorded wheel name, version and
  SHA-256 are the wheel about to be published.

Signature verification stays outside: the release workflow runs
`sigstore verify identity` against the committed trust root *before* this file
is parsed, so what is checked here is a payload already proven to come from the
trusted signer. Together they mean a signed-but-weakened artifact cannot slip
through on the strength of its signature alone.

Run from the repo root:

    python scripts/verify_qualification_binding.py \\
        --qualification qualified-dist/safety-qualification.json \\
        --wheel qualified-dist/agents_shipgate-0.16.0-py3-none-any.whl \\
        --tag v0.16.0
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

if __package__:
    from scripts._release_support import (
        PRODUCTION_QUALIFICATION_TIER,
        QUALIFICATION_DECISIONS,
        SHA256_PATTERN,
        ReleaseError,
        accepted_qualification_tiers,
        describe_accepted_tiers,
        inspect_wheel,
        qualification_envelope_admits_tier,
        qualification_policy,
    )
else:  # ``python scripts/verify_qualification_binding.py``
    from _release_support import (
        PRODUCTION_QUALIFICATION_TIER,
        QUALIFICATION_DECISIONS,
        SHA256_PATTERN,
        ReleaseError,
        accepted_qualification_tiers,
        describe_accepted_tiers,
        inspect_wheel,
        qualification_envelope_admits_tier,
        qualification_policy,
    )


def _require(errors: list[str], condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


def _is_rate(value: Any, *, low: float, high: float) -> bool:
    """A finite JSON number inside ``[low, high]``.

    ``True`` is an ``int`` in Python and is not a number here. Neither is
    ``inf``: the JSON literal ``1e309`` loads as ``inf``, which satisfies any
    ``>=`` floor while the exhaustive gate rejects it against its ``<= 1.0``
    invariant.
    """

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value) and low <= value <= high


def _is_count(value: Any, *, low: int, high: int) -> bool:
    """A genuine integer count inside ``[low, high]``."""

    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def _requirements_errors(declared: Any, policy: Any) -> list[str]:
    """Compare the artifact's own ``requirements`` block against the policy.

    The sealer re-derives the strata and floors from the cases, but the report
    schema version has no representation in the cases at all -- so without this
    an artifact could restate the approved ``0.43`` as ``0.1`` and still seal.
    """

    if not isinstance(declared, dict):
        return ["artifact has no requirements object"]
    expected = policy.as_requirements_payload()
    if set(declared) != set(expected):
        return [f"requirements block does not carry exactly the {policy.tier} policy's fields"]
    errors: list[str] = []
    for key, want in expected.items():
        got = declared[key]
        if key == "required_strata":
            rows = (
                sorted(
                    (row.get("profile"), row.get("expected_decision"), row.get("count"))
                    for row in got
                    if isinstance(row, dict)
                )
                if isinstance(got, list)
                else None
            )
            if rows != sorted(
                (row["profile"], row["expected_decision"], row["count"]) for row in want
            ):
                errors.append(
                    f"requirements required_strata differ from the {policy.tier} policy"
                )
            continue
        if got != want:
            errors.append(
                f"requirements {key} is {got!r}, not the {policy.tier} policy's {want!r}"
            )
    return errors


def verify_qualification_binding(
    *, qualification_path: Path, wheel_path: Path, tag: str
) -> dict[str, Any]:
    """Fail closed unless the signed artifact qualifies exactly this wheel."""

    if not qualification_path.is_file():
        raise ReleaseError(f"Safety qualification artifact not found: {qualification_path}")
    try:
        payload = json.loads(qualification_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"Invalid safety qualification artifact: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReleaseError(f"{qualification_path} must contain a JSON object")

    wheel_name, wheel_version, wheel_sha256 = inspect_wheel(wheel_path)
    inputs = payload.get("inputs")
    summary = payload.get("summary")
    cases = payload.get("cases")
    errors: list[str] = []

    _require(errors, isinstance(inputs, dict), "artifact has no inputs object")
    _require(errors, isinstance(summary, dict), "artifact has no summary object")
    _require(errors, isinstance(cases, list), "artifact has no cases array")
    if errors:
        raise ReleaseError(f"{qualification_path} is malformed: " + "; ".join(errors))

    assert isinstance(inputs, dict) and isinstance(summary, dict) and isinstance(cases, list)

    # Which policy governs is decided by the wheel's version, never by the
    # artifact. An artifact naming a tier this version does not admit is
    # rejected *and* falls back to the production policy, so a bad tier can
    # never shrink the population checked below.
    accepted_tiers = accepted_qualification_tiers(wheel_version)
    tier = payload.get("qualification_tier")
    tier_accepted = tier in accepted_tiers
    policy = qualification_policy(tier if tier_accepted else PRODUCTION_QUALIFICATION_TIER)
    required_case_count = policy.case_count
    declared_envelope = payload.get("schema_version")

    _require(
        errors,
        tier_accepted,
        f"qualification tier is not {describe_accepted_tiers(accepted_tiers)}",
    )
    # A legacy envelope's reader admits `beta` and `test` only, so labelling a
    # pre-1.0 artifact with one hands an old reader something it cannot parse.
    # The pydantic model refuses the same pairing; this is the raw-JSON half,
    # because nothing else here looks at the envelope at all.
    _require(
        errors,
        qualification_envelope_admits_tier(declared_envelope, tier),
        f"qualification envelope {declared_envelope!r} cannot carry "
        f"qualification_tier {tier!r}",
    )
    _require(errors, payload.get("qualified") is True, "artifact is not qualified")
    if tier == PRODUCTION_QUALIFICATION_TIER:
        _require(
            errors,
            payload.get("production_qualified") is True,
            "artifact is not production_qualified",
        )
    else:
        _require(
            errors,
            payload.get("production_qualified") is False,
            "artifact claims production_qualified without the production policy",
        )
    _require(errors, payload.get("static_only") is True, "artifact is not static-only")
    _require(
        errors,
        payload.get("runtime_behavior_proven") is False,
        "artifact claims runtime behaviour was proven",
    )
    _require(errors, payload.get("failures") == [], "artifact reports failures")

    _require(
        errors,
        len(cases) == required_case_count,
        f"artifact carries {len(cases)} cases, not {required_case_count}",
    )
    _require(
        errors,
        summary.get("total_cases") == required_case_count,
        f"summary total_cases is not {required_case_count}",
    )
    _require(
        errors,
        summary.get("receipt_count") == required_case_count,
        f"summary receipt_count is not {required_case_count}",
    )

    # Derived from the cases, not read from the summary. The summary is a
    # claim the artifact makes about itself; an attacker who can produce a
    # validly signed artifact can also write `unsafe_auto_pass_count: 0` above
    # a hundred cases that say otherwise.
    derived_unsafe = sum(
        1
        for case in cases
        if isinstance(case, dict)
        and case.get("expected_decision") != "passed"
        and case.get("actual_decision") == "passed"
    )
    derived_runtime_failures = sum(
        1 for case in cases if isinstance(case, dict) and case.get("runtime_failure")
    )
    receipts = [
        str(case.get("receipt_sha256", ""))
        for case in cases
        if isinstance(case, dict) and case.get("receipt_sha256")
    ]

    _require(errors, derived_unsafe == 0, "cases contain an unsafe auto-pass")
    _require(errors, derived_runtime_failures == 0, "cases contain a runtime failure")

    # A total case count is not a policy. Without the strata and the
    # exact-match floors below, 56 cases in no stratum at all, or 56 correctly
    # stratified cases with two safe passes missing, passed here while the
    # exhaustive gate rejected them -- so the dependency-compromise boundary
    # this file exists to hold would have been decorative.
    objects = [case for case in cases if isinstance(case, dict)]
    _require(errors, len(objects) == len(cases), "a case entry is not a JSON object")

    # Identity and terminality come first: the floors below count *matches*, so
    # a case with a null `actual_decision` merely fails to count toward its
    # floor rather than being rejected, and 56 rows sharing one id look like 56
    # distinct cases as long as their receipt digests differ.
    identifiers = [case.get("id") for case in objects]
    named = [value for value in identifiers if isinstance(value, str) and value.strip()]
    _require(
        errors, len(named) == len(identifiers), "a case id is missing, blank, or not a string"
    )
    _require(errors, len(set(named)) == len(named), "case ids are not unique")
    _require(
        errors,
        all(case.get("expected_decision") in QUALIFICATION_DECISIONS for case in objects),
        "a case expected_decision is not one of the four terminal decisions",
    )
    _require(
        errors,
        all(case.get("actual_decision") in QUALIFICATION_DECISIONS for case in objects),
        "a case has no terminal actual verifier decision",
    )

    errors.extend(_requirements_errors(payload.get("requirements"), policy))

    strata = Counter(
        (str(case.get("profile")), str(case.get("expected_decision"))) for case in objects
    )
    _require(
        errors,
        dict(strata) == policy.strata,
        f"case profile/outcome strata do not match the {policy.tier} policy",
    )

    for decision in QUALIFICATION_DECISIONS:
        exact = sum(
            1
            for case in objects
            if case.get("expected_decision") == decision
            and case.get("actual_decision") == decision
        )
        floor = policy.minimum_exact[decision]
        _require(
            errors,
            exact >= floor,
            f"{decision} exact-match floor failed: {exact} of "
            f"{policy.outcome_total(decision)}, need {floor}",
        )

    for profile in sorted(policy.profile_counts):
        _require(
            errors,
            not any(
                case.get("profile") == profile
                and case.get("expected_decision") != "passed"
                and case.get("actual_decision") == "passed"
                for case in objects
            ),
            f"profile {profile} contains an unsafe auto-pass",
        )

    for (profile, decision), stratum_size in sorted(policy.strata.items()):
        holdout = sum(
            1
            for case in objects
            if case.get("profile") == profile
            and case.get("expected_decision") == decision
            and case.get("split") == "holdout"
        )
        _require(
            errors,
            holdout >= policy.minimum_holdout(stratum_size),
            f"stratum {profile}/{decision} holdout requirement failed",
        )

    # The only two policy floors that are *not* derivable from ``cases``: no
    # case row carries its origin or the label agreement it came from. They are
    # read from the summary and named as such, rather than silently skipped
    # because they are inconvenient to re-derive here.
    _require(
        errors,
        _is_count(
            summary.get("qualified_origin_cases"),
            low=policy.minimum_qualified_origins,
            high=policy.case_count,
        ),
        f"summary qualified_origin_cases is not an integer between the {policy.tier} "
        f"minimum of {policy.minimum_qualified_origins} and {policy.case_count}",
    )
    _require(
        errors,
        _is_rate(summary.get("cohen_kappa"), low=policy.minimum_kappa, high=1.0),
        f"summary cohen_kappa is not a finite value between the {policy.tier} floor "
        f"of {policy.minimum_kappa} and 1.0",
    )
    _require(
        errors,
        len(receipts) == required_case_count,
        f"cases carry {len(receipts)} receipts, not {required_case_count}",
    )
    _require(
        errors,
        all(SHA256_PATTERN.fullmatch(digest) for digest in receipts),
        "a case receipt digest is malformed",
    )
    _require(errors, len(set(receipts)) == len(receipts), "case receipt digests are not unique")

    # The summary must agree with what the cases actually say; a disagreement
    # means the artifact is internally inconsistent regardless of which side is
    # right.
    _require(
        errors,
        summary.get("unsafe_auto_pass_count") == derived_unsafe,
        "summary unsafe_auto_pass_count disagrees with the cases",
    )
    _require(
        errors,
        summary.get("runtime_failure_count") == derived_runtime_failures,
        "summary runtime_failure_count disagrees with the cases",
    )

    # The binding. Everything above is a claim; this is what ties the claim to
    # the bytes that will reach the index.
    _require(errors, inputs.get("wheel_name") == wheel_name, "qualified wheel name mismatch")
    _require(
        errors, inputs.get("wheel_version") == wheel_version, "qualified wheel version mismatch"
    )
    _require(
        errors, inputs.get("engine_version") == wheel_version, "qualified engine version mismatch"
    )
    recorded_digest = str(inputs.get("wheel_sha256", ""))
    _require(
        errors,
        bool(SHA256_PATTERN.fullmatch(recorded_digest)),
        "qualified wheel SHA-256 is malformed",
    )
    _require(errors, recorded_digest == wheel_sha256, "qualified wheel SHA-256 mismatch")
    _require(errors, tag == f"v{wheel_version}", "release tag does not match wheel version")

    if errors:
        raise ReleaseError(
            "Release safety qualification rejected: " + "; ".join(sorted(set(errors)))
        )
    return {
        "qualification_tier": tier,
        "wheel_name": wheel_name,
        "wheel_version": wheel_version,
        "wheel_sha256": wheel_sha256,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Re-derive the decisive qualification invariants and wheel binding."
    )
    parser.add_argument("--qualification", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        record = verify_qualification_binding(
            qualification_path=args.qualification, wheel_path=args.wheel, tag=args.tag
        )
    except (ReleaseError, OSError, ValueError) as exc:
        sys.stderr.write(f"Qualification binding error: {exc}\n")
        return 1
    sys.stdout.write(
        f"OK: signed {record['qualification_tier']} qualification is bound to "
        f"{record['wheel_name']} {record['wheel_version']} ({record['wheel_sha256']}).\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
