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
  policy requires;
* it reports **zero** unsafe auto-passes;
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
import sys
from pathlib import Path
from typing import Any

if __package__:
    from scripts._release_support import (
        PRODUCTION_QUALIFICATION_TIER,
        QUALIFICATION_CASE_COUNTS,
        SHA256_PATTERN,
        ReleaseError,
        accepted_qualification_tiers,
        describe_accepted_tiers,
        inspect_wheel,
    )
else:  # ``python scripts/verify_qualification_binding.py``
    from _release_support import (
        PRODUCTION_QUALIFICATION_TIER,
        QUALIFICATION_CASE_COUNTS,
        SHA256_PATTERN,
        ReleaseError,
        accepted_qualification_tiers,
        describe_accepted_tiers,
        inspect_wheel,
    )


def _require(errors: list[str], condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


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
    # rejected *and* falls back to the production case count, so a bad tier
    # can never shrink the population checked below.
    accepted_tiers = accepted_qualification_tiers(wheel_version)
    tier = payload.get("qualification_tier")
    tier_accepted = tier in accepted_tiers
    required_case_count = (
        QUALIFICATION_CASE_COUNTS[tier]
        if tier_accepted
        else QUALIFICATION_CASE_COUNTS[PRODUCTION_QUALIFICATION_TIER]
    )

    _require(
        errors,
        tier_accepted,
        f"qualification tier is not {describe_accepted_tiers(accepted_tiers)}",
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
