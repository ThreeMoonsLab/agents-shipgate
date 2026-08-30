from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

from agents_shipgate.core.errors import ConfigError
from agents_shipgate.schemas.safety_qualification import (
    QualificationInputDigestV1,
    SafetyQualificationCaseResultV1,
    SafetyQualificationRequirementsV1,
    SafetyQualificationResultV1,
    SafetyQualificationStratumV1,
    SafetyQualificationSummaryV1,
    pre_release_safety_requirements,
    production_safety_requirements,
    tier_for_requirements,
)
from scripts.run_safety_qualification import _confusion_matrix, _metric, sha256_file
from scripts.verify_safety_qualification_release import (
    main,
    verify_release_qualification,
)

VERSION = "0.16.0b7"
POST_1_0_VERSION = "1.0.0"
REPO_ROOT = Path(__file__).resolve().parent.parent


def _write_wheel(path: Path, version: str = VERSION) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            f"agents_shipgate-{version}.dist-info/METADATA",
            f"Metadata-Version: 2.4\nName: agents-shipgate\nVersion: {version}\n",
        )


def _result(
    wheel: Path,
    *,
    requirements: SafetyQualificationRequirementsV1 | None = None,
    version: str = VERSION,
) -> SafetyQualificationResultV1:
    """A perfectly conforming artifact for whichever named policy is supplied.

    Everything is derived from ``requirements`` -- counts, thresholds, metric
    denominators, matrix profiles -- so a policy change cannot leave this
    builder asserting the previous one's numbers.
    """

    requirements = requirements or production_safety_requirements()
    tier = tier_for_requirements(requirements)
    cases: list[SafetyQualificationCaseResultV1] = []
    strata: list[SafetyQualificationStratumV1] = []
    for requirement in requirements.required_strata:
        holdout = math.ceil(requirement.count * requirements.minimum_holdout_fraction_per_stratum)
        for index in range(requirement.count):
            cases.append(
                SafetyQualificationCaseResultV1(
                    id=(f"{requirement.profile}-{requirement.expected_decision}-{index:02d}"),
                    profile=requirement.profile,
                    split="holdout" if index < holdout else "tuning",
                    expected_decision=requirement.expected_decision,
                    actual_decision=requirement.expected_decision,
                    exact=True,
                    receipt_sha256=f"{len(cases) + 1:064x}",
                    runtime_failure=False,
                )
            )
        strata.append(
            SafetyQualificationStratumV1(
                profile=requirement.profile,
                expected_decision=requirement.expected_decision,
                required_count=requirement.count,
                actual_count=requirement.count,
                tuning_count=requirement.count - holdout,
                holdout_count=holdout,
                minimum_holdout_count=holdout,
                receipt_count=requirement.count,
                unsafe_auto_pass_count=0,
                runtime_failure_count=0,
            )
        )

    total = len(cases)
    outcome_counts = {
        decision: sum(case.expected_decision == decision for case in cases)
        for decision in ("passed", "review_required", "insufficient_evidence", "blocked")
    }
    metrics = [
        _metric(
            name="unsafe_auto_pass_rate",
            numerator=0,
            denominator=total - outcome_counts["passed"],
            requirement=f"<= {requirements.maximum_unsafe_auto_passes} cases",
            passed=True,
        ),
        _metric(
            name="safe_pass_rate",
            numerator=outcome_counts["passed"],
            denominator=outcome_counts["passed"],
            requirement=f">= {requirements.minimum_safe_passes} cases",
            passed=True,
        ),
        _metric(
            name="blocked_exact_rate",
            numerator=outcome_counts["blocked"],
            denominator=outcome_counts["blocked"],
            requirement=f">= {requirements.minimum_blocked_exact} cases",
            passed=True,
        ),
        _metric(
            name="review_exact_rate",
            numerator=outcome_counts["review_required"],
            denominator=outcome_counts["review_required"],
            requirement=f">= {requirements.minimum_review_exact} cases",
            passed=True,
        ),
        _metric(
            name="insufficient_evidence_exact_rate",
            numerator=outcome_counts["insufficient_evidence"],
            denominator=outcome_counts["insufficient_evidence"],
            requirement=f">= {requirements.minimum_insufficient_evidence_exact} cases",
            passed=True,
        ),
        _metric(
            name="overall_exact_rate",
            numerator=total,
            denominator=total,
            requirement="reported for audit; outcome-specific thresholds govern",
            passed=True,
        ),
    ]
    matrices = [_confusion_matrix(cases, profile="all")]
    matrices.extend(
        _confusion_matrix(cases, profile=profile)
        for profile in sorted({item.profile for item in requirements.required_strata})
    )
    return SafetyQualificationResultV1(
        qualification_tier=tier,
        qualified=True,
        production_qualified=tier == "beta",
        inputs=QualificationInputDigestV1(
            wheel_name="agents-shipgate",
            wheel_version=version,
            engine_version=version,
            wheel_sha256=sha256_file(wheel),
            corpus_name="frozen-corpus.json",
            corpus_id="beta-corpus-v1",
            corpus_sha256="1" * 64,
            labels_sha256="2" * 64,
            policy_sha256="3" * 64,
            receipt_index_name="receipt-index.json",
            receipt_index_sha256="4" * 64,
        ),
        requirements=requirements,
        summary=SafetyQualificationSummaryV1(
            total_cases=total,
            qualified_origin_cases=requirements.minimum_qualified_origins,
            primary_label_agreements=total,
            cohen_kappa=1.0,
            receipt_count=total,
            exact_count=total,
            unsafe_auto_pass_count=0,
            runtime_failure_count=0,
            outcome_counts=outcome_counts,
        ),
        strata=strata,
        confusion_matrices=matrices,
        intervals=metrics,
        cases=cases,
        failures=[],
    )


def _fixture(
    tmp_path: Path,
    *,
    requirements: SafetyQualificationRequirementsV1 | None = None,
    version: str = VERSION,
) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    wheel = tmp_path / f"agents_shipgate-{version}-py3-none-any.whl"
    _write_wheel(wheel, version)
    qualification = tmp_path / "safety-qualification.json"
    qualification.write_text(
        json.dumps(
            _result(wheel, requirements=requirements, version=version).model_dump(mode="json"),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return wheel, qualification


def _mutate(path: Path, mutation) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutation(payload)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_release_validator_accepts_exact_production_artifact_and_wheel(
    tmp_path: Path,
) -> None:
    wheel, qualification = _fixture(tmp_path)

    result = verify_release_qualification(
        wheel_path=wheel,
        qualification_path=qualification,
        tag=f"v{VERSION}",
    )

    assert result.production_qualified is True
    assert result.inputs.wheel_sha256 == sha256_file(wheel)


def test_release_validator_rejects_wheel_hash_or_tag_mismatch(tmp_path: Path) -> None:
    wheel, qualification = _fixture(tmp_path)
    _mutate(
        qualification,
        lambda payload: payload["inputs"].__setitem__("wheel_sha256", "0" * 64),
    )

    with pytest.raises(ConfigError, match="wheel SHA-256 mismatch"):
        verify_release_qualification(
            wheel_path=wheel,
            qualification_path=qualification,
            tag=f"v{VERSION}",
        )

    wheel, qualification = _fixture(tmp_path / "tag")
    with pytest.raises(ConfigError, match="tag does not match"):
        verify_release_qualification(
            wheel_path=wheel,
            qualification_path=qualification,
            tag="v0.16.0b1",
        )


def test_release_validator_recomputes_runtime_and_threshold_invariants(
    tmp_path: Path,
) -> None:
    wheel, qualification = _fixture(tmp_path)

    def corrupt(payload: dict) -> None:
        payload["summary"]["runtime_failure_count"] = 1
        payload["cases"][0]["runtime_failure"] = True

    _mutate(qualification, corrupt)

    with pytest.raises(ConfigError, match="runtime"):
        verify_release_qualification(
            wheel_path=wheel,
            qualification_path=qualification,
            tag=f"v{VERSION}",
        )


def test_release_validator_rejects_relaxed_requirements_even_if_claimed_qualified(
    tmp_path: Path,
) -> None:
    wheel, qualification = _fixture(tmp_path)
    _mutate(
        qualification,
        lambda payload: payload["requirements"].__setitem__("minimum_safe_passes", 1),
    )

    with pytest.raises(ConfigError, match="requirements differ"):
        verify_release_qualification(
            wheel_path=wheel,
            qualification_path=qualification,
            tag=f"v{VERSION}",
        )


def test_release_validator_cli_fails_closed(tmp_path: Path) -> None:
    wheel, qualification = _fixture(tmp_path)

    assert (
        main(
            [
                "--wheel",
                str(wheel),
                "--qualification",
                str(qualification),
                "--tag",
                f"v{VERSION}",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "--wheel",
                str(wheel),
                "--qualification",
                str(qualification),
                "--tag",
                "v0.16.0b1",
            ]
        )
        == 1
    )


def test_a_pre_1_0_artifact_publishes_a_0_x_tag_and_nothing_later(
    tmp_path: Path,
) -> None:
    """The whole point of the #341 decision, and its limit.

    A 56-case ``pre_1_0`` artifact is a complete answer for a ``0.x`` tag, and
    is *not* an answer for ``1.0``: the same bytes that pass on ``v0.16.0b7``
    must fail on ``v1.0.0``, because the governing policy is read from the
    version and never from the artifact.
    """

    wheel, qualification = _fixture(
        tmp_path / "pre", requirements=pre_release_safety_requirements()
    )
    result = verify_release_qualification(
        wheel_path=wheel, qualification_path=qualification, tag=f"v{VERSION}"
    )

    assert result.qualification_tier == "pre_1_0"
    assert result.qualified is True
    assert result.production_qualified is False
    assert len(result.cases) == 56

    later_wheel, later_qualification = _fixture(
        tmp_path / "later",
        requirements=pre_release_safety_requirements(),
        version=POST_1_0_VERSION,
    )
    with pytest.raises(ConfigError, match="qualification tier is not beta"):
        verify_release_qualification(
            wheel_path=later_wheel,
            qualification_path=later_qualification,
            tag=f"v{POST_1_0_VERSION}",
        )


def test_a_pre_1_0_artifact_may_not_claim_a_legacy_envelope(tmp_path: Path) -> None:
    """The exhaustive gate rejects it while parsing, not after.

    A v4 reader admits `beta` and `test` only. Relabelling a conforming
    56-case artifact as v4 recreates exactly the combination the v5 bump exists
    to eliminate, so an old reader could still be handed something it cannot
    parse. A legacy envelope carrying legacy vocabulary is still read.
    """

    wheel, qualification = _fixture(tmp_path, requirements=pre_release_safety_requirements())
    _mutate(
        qualification,
        lambda payload: payload.__setitem__(
            "schema_version", "shipgate.safety_qualification/v4"
        ),
    )

    with pytest.raises(ConfigError, match="admits only qualification_tier"):
        verify_release_qualification(
            wheel_path=wheel, qualification_path=qualification, tag=f"v{VERSION}"
        )

    wheel, qualification = _fixture(
        tmp_path / "beta", requirements=production_safety_requirements()
    )
    _mutate(
        qualification,
        lambda payload: payload.__setitem__(
            "schema_version", "shipgate.safety_qualification/v4"
        ),
    )
    assert (
        verify_release_qualification(
            wheel_path=wheel, qualification_path=qualification, tag=f"v{VERSION}"
        ).schema_version
        == "shipgate.safety_qualification/v5"
    )


def test_a_production_artifact_still_publishes_a_0_x_tag(tmp_path: Path) -> None:
    """Carrying more evidence than the tag requires is never a rejection."""

    wheel, qualification = _fixture(tmp_path, requirements=production_safety_requirements())

    result = verify_release_qualification(
        wheel_path=wheel, qualification_path=qualification, tag=f"v{VERSION}"
    )

    assert result.qualification_tier == "beta"
    assert result.production_qualified is True


def test_the_tier_a_0_x_artifact_names_selects_the_counts_it_must_meet(
    tmp_path: Path,
) -> None:
    """Both tiers are admissible for ``0.x``, so neither may borrow the other's
    numbers: a 56-case corpus cannot call itself ``beta``, and a 100-case one
    cannot call itself ``pre_1_0``."""

    def _relabel(tier: str, production: bool):
        # Both fields together: relabelling only the tier trips the artifact's
        # own production_qualified invariant, which would prove that check
        # rather than the case count this test is about.
        def _apply(payload: dict) -> None:
            payload["qualification_tier"] = tier
            payload["production_qualified"] = production

        return _apply

    wheel, qualification = _fixture(
        tmp_path / "understated", requirements=pre_release_safety_requirements()
    )
    _mutate(qualification, _relabel("beta", True))
    with pytest.raises(ConfigError, match="exactly 100 cases"):
        verify_release_qualification(
            wheel_path=wheel, qualification_path=qualification, tag=f"v{VERSION}"
        )

    wheel, qualification = _fixture(
        tmp_path / "overstated", requirements=production_safety_requirements()
    )
    _mutate(qualification, _relabel("pre_1_0", False))
    with pytest.raises(ConfigError, match="exactly 56 cases"):
        verify_release_qualification(
            wheel_path=wheel, qualification_path=qualification, tag=f"v{VERSION}"
        )


def test_a_pre_1_0_artifact_may_not_claim_the_production_flag(tmp_path: Path) -> None:
    """``production_qualified`` keeps meaning "met the 100-case bar".

    The artifact schema refuses to construct the inconsistency at all, so the
    runner cannot emit one and the verifier rejects it while parsing -- before
    any policy check runs. Both directions are wrong: a pre-1.0 artifact
    claiming the flag, and a beta artifact disclaiming it.
    """

    wheel, qualification = _fixture(tmp_path, requirements=pre_release_safety_requirements())
    _mutate(qualification, lambda payload: payload.__setitem__("production_qualified", True))
    with pytest.raises(ConfigError, match="Invalid safety qualification artifact"):
        verify_release_qualification(
            wheel_path=wheel, qualification_path=qualification, tag=f"v{VERSION}"
        )

    wheel, qualification = _fixture(tmp_path / "beta", requirements=production_safety_requirements())
    _mutate(qualification, lambda payload: payload.__setitem__("production_qualified", False))
    with pytest.raises(ConfigError, match="Invalid safety qualification artifact"):
        verify_release_qualification(
            wheel_path=wheel, qualification_path=qualification, tag=f"v{VERSION}"
        )


def test_relaxing_the_pre_1_0_thresholds_is_rejected_like_relaxing_production(
    tmp_path: Path,
) -> None:
    """The smaller policy is re-derived, not trusted -- otherwise #341 would
    have created a bar that any signed artifact could restate downwards."""

    wheel, qualification = _fixture(tmp_path, requirements=pre_release_safety_requirements())
    _mutate(
        qualification,
        lambda payload: payload["requirements"].__setitem__("minimum_blocked_exact", 1),
    )

    with pytest.raises(ConfigError, match="requirements differ from the pre_1_0 policy"):
        verify_release_qualification(
            wheel_path=wheel, qualification_path=qualification, tag=f"v{VERSION}"
        )


def test_an_unnamed_tier_is_rejected_and_still_scored_against_production(
    tmp_path: Path,
) -> None:
    """A rejected tier must not shrink what the rest of the verifier checks."""

    wheel, qualification = _fixture(tmp_path, requirements=pre_release_safety_requirements())
    _mutate(qualification, lambda payload: payload.__setitem__("qualification_tier", "test"))

    with pytest.raises(ConfigError) as excinfo:
        verify_release_qualification(
            wheel_path=wheel, qualification_path=qualification, tag=f"v{VERSION}"
        )

    message = str(excinfo.value)
    assert "qualification tier is not one of beta, pre_1_0" in message
    assert "exactly 100 cases" in message


def test_release_workflow_reuses_signed_qualified_wheel_before_publish() -> None:
    """The published wheel stays the *qualified* one, and every binding
    precedes publication.

    Verification now builds a wheel from the tagged source too, but only to
    compare against the qualified wheel — the artifact that reaches PyPI is
    still the signed, qualified one, never a freshly built substitute.
    """

    verify = (REPO_ROOT / ".github/workflows/release-verify.yml").read_text(encoding="utf-8")
    release = (REPO_ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    # Ordering is asserted inside the sealing job. The exhaustive policy
    # re-derivation lives in the `tests` gate because it needs the project
    # installed; the sealer verifies the signature first, then restates the
    # decisive invariants, then binds the wheel to the tagged source.
    sealer = yaml.safe_load(verify)["jobs"]["artifact"]["steps"]
    order = [json.dumps(step) for step in sealer]

    def _at(needle: str) -> int:
        return next(i for i, step in enumerate(order) if needle in step)

    assert _at("sigstore verify identity") < _at("scripts/verify_qualification_binding.py")
    assert _at("scripts/verify_qualification_binding.py") < _at(
        "scripts/verify_wheel_provenance.py"
    )
    assert "SAFETY_QUALIFICATION_WHEEL_URL" in verify
    assert "SAFETY_QUALIFICATION_JSON_URL" in verify
    assert "SAFETY_QUALIFICATION_SIGSTORE_BUNDLE_URL" in verify

    # Publication lives in a different job that cannot start until the
    # verification job succeeds, so ordering is enforced by the dependency
    # graph rather than by step position.
    assert "uv publish --trusted-publishing always" not in verify
    parsed_release = yaml.safe_load(release)
    assert parsed_release["jobs"]["publish"]["needs"] == ["verify", "stage"]

    # The wheel is addressed by the filename verification approved, and the
    # source-built wheel never enters the publishable set.
    assert 'uv publish --trusted-publishing always "dist/${WHEEL_FILENAME}"' in release
    assert "source-build" not in release
    assert "dist/*.tar.gz" not in release
    assert "safety-qualification.json" in verify
    assert "safety-qualification.sigstore.json" in verify

    for workflow in ("release.yml", "release-verify.yml", "release-rehearsal.yml"):
        parsed = yaml.safe_load(
            (REPO_ROOT / ".github/workflows" / workflow).read_text(encoding="utf-8")
        )
        for job in parsed["jobs"].values():
            for step in job.get("steps") or []:
                if "run" in step:
                    subprocess.run(
                        ["bash", "-n", "-c", step["run"]],
                        check=True,
                        capture_output=True,
                        text=True,
                    )


def test_release_validator_is_directly_executable_from_the_documented_command() -> None:
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "verify_safety_qualification_release.py"),
            "--help",
        ],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "--qualification" in result.stdout
