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
    SafetyQualificationResultV1,
    SafetyQualificationStratumV1,
    SafetyQualificationSummaryV1,
    production_safety_requirements,
)
from scripts.run_safety_qualification import _confusion_matrix, _metric, sha256_file
from scripts.verify_safety_qualification_release import (
    main,
    verify_release_qualification,
)

VERSION = "0.16.0b7"
REPO_ROOT = Path(__file__).resolve().parent.parent


def _write_wheel(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "agents_shipgate-0.16.0b7.dist-info/METADATA",
            "Metadata-Version: 2.4\nName: agents-shipgate\nVersion: 0.16.0b7\n",
        )


def _production_result(wheel: Path) -> SafetyQualificationResultV1:
    requirements = production_safety_requirements()
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

    safe = sum(case.expected_decision == "passed" for case in cases)
    blocked = sum(case.expected_decision == "blocked" for case in cases)
    review = sum(case.expected_decision == "review_required" for case in cases)
    insufficient = sum(case.expected_decision == "insufficient_evidence" for case in cases)
    metrics = [
        _metric(
            name="unsafe_auto_pass_rate",
            numerator=0,
            denominator=70,
            requirement="<= 0 cases",
            passed=True,
        ),
        _metric(
            name="safe_pass_rate",
            numerator=safe,
            denominator=safe,
            requirement=">= 27 cases",
            passed=True,
        ),
        _metric(
            name="blocked_exact_rate",
            numerator=blocked,
            denominator=blocked,
            requirement=">= 30 cases",
            passed=True,
        ),
        _metric(
            name="review_exact_rate",
            numerator=review,
            denominator=review,
            requirement=">= 19 cases",
            passed=True,
        ),
        _metric(
            name="insufficient_evidence_exact_rate",
            numerator=insufficient,
            denominator=insufficient,
            requirement=">= 19 cases",
            passed=True,
        ),
        _metric(
            name="overall_exact_rate",
            numerator=100,
            denominator=100,
            requirement="reported for audit; outcome-specific thresholds govern",
            passed=True,
        ),
    ]
    matrices = [_confusion_matrix(cases, profile="all")]
    matrices.extend(
        _confusion_matrix(cases, profile=profile)
        for profile in (
            "mcp_openapi_declared_binding",
            "openai_agents_sdk",
            "langchain_crewai",
            "google_adk",
            "n8n",
            "multi_agent_handoffs",
            "coding_agent_trust_roots",
        )
    )
    return SafetyQualificationResultV1(
        qualification_tier="beta",
        qualified=True,
        production_qualified=True,
        inputs=QualificationInputDigestV1(
            wheel_name="agents-shipgate",
            wheel_version=VERSION,
            engine_version=VERSION,
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
            total_cases=100,
            qualified_origin_cases=40,
            primary_label_agreements=100,
            cohen_kappa=1.0,
            receipt_count=100,
            exact_count=100,
            unsafe_auto_pass_count=0,
            runtime_failure_count=0,
            outcome_counts={
                "passed": 30,
                "review_required": 20,
                "insufficient_evidence": 20,
                "blocked": 30,
            },
        ),
        strata=strata,
        confusion_matrices=matrices,
        intervals=metrics,
        cases=cases,
        failures=[],
    )


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    wheel = tmp_path / "agents_shipgate-0.16.0b7-py3-none-any.whl"
    _write_wheel(wheel)
    qualification = tmp_path / "safety-qualification.json"
    qualification.write_text(
        json.dumps(_production_result(wheel).model_dump(mode="json"), indent=2, sort_keys=True)
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


def test_release_workflow_reuses_signed_qualified_wheel_before_publish() -> None:
    workflow_path = REPO_ROOT / ".github/workflows/release.yml"
    workflow = workflow_path.read_text(encoding="utf-8")

    signature_index = workflow.index("sigstore verify identity")
    binding_index = workflow.index("scripts/verify_safety_qualification_release.py")
    publish_index = workflow.index("uv publish --trusted-publishing always")
    assert signature_index < binding_index < publish_index
    assert "SAFETY_QUALIFICATION_WHEEL_URL" in workflow
    assert "SAFETY_QUALIFICATION_JSON_URL" in workflow
    assert "SAFETY_QUALIFICATION_SIGSTORE_BUNDLE_URL" in workflow
    assert 'uv publish --trusted-publishing always "dist/${QUALIFIED_WHEEL_FILENAME}"' in workflow
    assert "python -m build" not in workflow
    assert "dist/*.tar.gz" not in workflow
    assert "dist/safety-qualification.json" in workflow
    assert "dist/safety-qualification.sigstore.json" in workflow

    parsed = yaml.safe_load(workflow)
    for step in parsed["jobs"]["release"]["steps"]:
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
