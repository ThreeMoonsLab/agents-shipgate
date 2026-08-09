"""Release-integrity contracts for the tag-triggered publication pipeline.

These tests guard properties that are otherwise invisible until a `v*` tag is
pushed — the one moment when getting them wrong is irreversible, because PyPI
uploads cannot be replaced. They cover the five controls that make a release
verifiable rather than merely automated:

* the published wheel is the one the tagged source produces (#342);
* the signed SBOM describes that wheel, not the CI environment (#356);
* verification and publication are separate, and a partial publish is
  recoverable (#343);
* the correctness suite is deterministic and matches CI's selection (#344);
* the whole path is rehearsable without any publication authority (#355).
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
import yaml

from agents_shipgate.core.errors import ConfigError
from scripts.release_publication import pypi_state, verify_manifest
from scripts.release_sbom import dev_only_distributions, verify_release_sbom
from scripts.verify_wheel_provenance import compare_wheels, verify_wheel_provenance

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github/workflows"

WHEEL_METADATA = "Metadata-Version: 2.4\nName: agents-shipgate\nVersion: 9.9.9\n"
DIST_INFO = "agents_shipgate-9.9.9.dist-info"


def _write_wheel(path: Path, members: dict[str, str] | None = None, **kwargs: Any) -> Path:
    """Write a minimal but structurally valid wheel."""

    payload = {f"{DIST_INFO}/METADATA": WHEEL_METADATA, "agents_shipgate/__init__.py": "x = 1\n"}
    payload.update(members or {})
    with zipfile.ZipFile(path, "w", **kwargs) as archive:
        for name, content in payload.items():
            archive.writestr(name, content)
    return path


def _load_workflow(name: str) -> dict[str, Any]:
    document = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    # PyYAML resolves the bare key `on` to the boolean True (YAML 1.1), so
    # triggers are normalised here rather than at every call site.
    if True in document:
        document["on"] = document.pop(True)
    return document


def _step_index(job: dict[str, Any], needle: str) -> int:
    for index, step in enumerate(job["steps"]):
        if needle in json.dumps(step):
            return index
    raise AssertionError(f"no step matching {needle!r}")


# --------------------------------------------------------------------------
# #342 — the published wheel comes from the tagged source
# --------------------------------------------------------------------------


def test_identical_wheels_are_bound_by_bytes(tmp_path: Path) -> None:
    built = _write_wheel(tmp_path / "built.whl")
    qualified = _write_wheel(tmp_path / "qualified.whl")

    record = verify_wheel_provenance(built_path=built, qualified_path=qualified)

    assert record["provenance_mode"] == "identical_bytes"
    assert record["byte_reproducible"] is True


def test_qualified_wheel_carrying_extra_code_fails_closed(tmp_path: Path) -> None:
    """The attack #342 describes: a wheel with the right Name and Version but
    contents no tagged commit produced."""

    built = _write_wheel(tmp_path / "built.whl")
    qualified = _write_wheel(
        tmp_path / "qualified.whl", {"agents_shipgate/_backdoor.py": "import os\n"}
    )

    with pytest.raises(ConfigError) as excinfo:
        verify_wheel_provenance(built_path=built, qualified_path=qualified)

    assert "_backdoor.py" in str(excinfo.value)
    # Even the explicitly weaker interim bar must not let this through.
    with pytest.raises(ConfigError):
        verify_wheel_provenance(
            built_path=built, qualified_path=qualified, allow_payload_equivalent=True
        )


def test_modified_module_contents_fail_closed(tmp_path: Path) -> None:
    built = _write_wheel(tmp_path / "built.whl")
    qualified = _write_wheel(
        tmp_path / "qualified.whl", {"agents_shipgate/__init__.py": "x = 666\n"}
    )

    with pytest.raises(ConfigError) as excinfo:
        verify_wheel_provenance(built_path=built, qualified_path=qualified)

    assert "content differs" in str(excinfo.value)


def test_container_only_difference_is_rejected_unless_explicitly_allowed(tmp_path: Path) -> None:
    """A repacked archive with identical payload is a reproducibility gap, not
    a source mismatch — but the weaker bar must be opted into, never inferred."""

    built = _write_wheel(tmp_path / "built.whl", compression=zipfile.ZIP_STORED)
    qualified = _write_wheel(tmp_path / "qualified.whl", compression=zipfile.ZIP_DEFLATED)

    mode, differences = compare_wheels(built, qualified)
    assert mode == "identical_payload"
    assert differences == []

    with pytest.raises(ConfigError, match="not byte-identical"):
        verify_wheel_provenance(built_path=built, qualified_path=qualified)

    record = verify_wheel_provenance(
        built_path=built, qualified_path=qualified, allow_payload_equivalent=True
    )
    assert record["provenance_mode"] == "identical_payload"
    assert record["byte_reproducible"] is False


def test_release_verification_gates_publication_on_source_binding() -> None:
    verify = _load_workflow("release-verify.yml")["jobs"]["verify"]

    build_index = _step_index(verify, "python -m build --wheel")
    provenance_index = _step_index(verify, "scripts/verify_wheel_provenance.py")
    handoff_index = _step_index(verify, "scripts/release_publication.py manifest")

    assert build_index < provenance_index < handoff_index


def test_build_backend_is_pinned_so_byte_equality_is_achievable() -> None:
    """Wheels record `Generator: hatchling <version>`, so an unpinned backend
    makes the byte-equality gate fail on legitimate releases."""

    constraints = (REPO_ROOT / "constraints/release-build.txt").read_text(encoding="utf-8")
    assert "hatchling==" in constraints

    verify = _load_workflow("release-verify.yml")["jobs"]["verify"]
    build_step = verify["steps"][_step_index(verify, "python -m build --wheel")]
    assert build_step["env"]["PIP_CONSTRAINT"] == "constraints/release-build.txt"


# --------------------------------------------------------------------------
# #356 — the SBOM describes the shipped wheel
# --------------------------------------------------------------------------


def _sbom(components: list[str], *, digest: str, version: str = "9.9.9") -> dict[str, Any]:
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "metadata": {
            "component": {
                "type": "library",
                "name": "agents-shipgate",
                "version": version,
                "hashes": [{"alg": "SHA-256", "content": digest}],
            }
        },
        "components": [{"name": name, "version": "1.0"} for name in components],
    }


def _digest(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_dev_only_guard_covers_the_release_tooling_that_leaked_before() -> None:
    forbidden = dev_only_distributions(REPO_ROOT / "pyproject.toml")

    # Exactly the packages `cyclonedx-py environment` over `.[dev]` inventoried.
    assert {"pytest", "ruff", "twine", "sigstore", "cyclonedx-bom", "pip-audit"} <= forbidden
    # Runtime dependencies must never be classified as dev-only.
    assert not forbidden & {"pydantic", "typer", "pyyaml", "cryptography", "packaging"}


def test_sbom_containing_a_dev_only_dependency_is_rejected(tmp_path: Path) -> None:
    wheel = _write_wheel(tmp_path / "agents_shipgate-9.9.9-py3-none-any.whl")
    sbom_path = tmp_path / "sbom.json"
    sbom_path.write_text(
        json.dumps(_sbom(["pydantic", "pytest"], digest=_digest(wheel))), encoding="utf-8"
    )

    with pytest.raises(ConfigError, match="dev-only"):
        verify_release_sbom(wheel_path=wheel, sbom_path=sbom_path)


def test_sbom_for_a_runtime_only_environment_is_accepted(tmp_path: Path) -> None:
    wheel = _write_wheel(tmp_path / "agents_shipgate-9.9.9-py3-none-any.whl")
    sbom_path = tmp_path / "sbom.json"
    sbom_path.write_text(
        json.dumps(_sbom(["agents-shipgate", "pydantic", "typer"], digest=_digest(wheel))),
        encoding="utf-8",
    )

    assert verify_release_sbom(wheel_path=wheel, sbom_path=sbom_path)


def test_sbom_describing_different_bytes_fails_before_publication(tmp_path: Path) -> None:
    wheel = _write_wheel(tmp_path / "agents_shipgate-9.9.9-py3-none-any.whl")
    other = _write_wheel(tmp_path / "other.whl", {"agents_shipgate/_extra.py": "y = 2\n"})
    sbom_path = tmp_path / "sbom.json"
    sbom_path.write_text(json.dumps(_sbom(["pydantic"], digest=_digest(other))), encoding="utf-8")

    with pytest.raises(ConfigError, match="no SHA-256 matching the wheel"):
        verify_release_sbom(wheel_path=wheel, sbom_path=sbom_path)


def test_sbom_without_a_bound_component_is_rejected(tmp_path: Path) -> None:
    wheel = _write_wheel(tmp_path / "agents_shipgate-9.9.9-py3-none-any.whl")
    sbom_path = tmp_path / "sbom.json"
    sbom_path.write_text(json.dumps({"bomFormat": "CycloneDX", "components": []}), encoding="utf-8")

    with pytest.raises(ConfigError, match="no metadata.component"):
        verify_release_sbom(wheel_path=wheel, sbom_path=sbom_path)


def test_publication_rechecks_the_sbom_binding_before_uploading() -> None:
    publish = _load_workflow("release.yml")["jobs"]["publish"]

    assert _step_index(publish, "scripts/release_sbom.py verify") < _step_index(
        publish, "uv publish"
    )


# --------------------------------------------------------------------------
# #343 — separated verification, recoverable publication
# --------------------------------------------------------------------------


def test_verification_and_publication_are_separate_jobs() -> None:
    release = _load_workflow("release.yml")

    assert set(release["jobs"]) == {"verify", "publish"}
    assert release["jobs"]["verify"]["uses"] == "./.github/workflows/release-verify.yml"
    assert release["jobs"]["publish"]["needs"] == "verify"


def test_permissions_are_least_privilege() -> None:
    release = _load_workflow("release.yml")
    verify_workflow = _load_workflow("release-verify.yml")

    # No ambient authority at workflow level.
    assert release["permissions"] == {}
    # Verification is read-only, in both the caller and the called workflow.
    assert release["jobs"]["verify"]["permissions"] == {"contents": "read"}
    assert verify_workflow["permissions"] == {"contents": "read"}
    assert verify_workflow["jobs"]["verify"]["permissions"] == {"contents": "read"}
    assert "id-token" not in verify_workflow["jobs"]["verify"]["permissions"]
    # Write and OIDC are scoped to publication alone.
    assert release["jobs"]["publish"]["permissions"] == {
        "contents": "write",
        "id-token": "write",
    }
    assert release["jobs"]["publish"]["environment"] == "pypi"


def test_release_concurrency_is_serialised_across_the_pypi_project() -> None:
    concurrency = _load_workflow("release.yml")["concurrency"]

    # A per-tag group would let two tags race to publish the same distribution.
    assert "${{" not in concurrency["group"]
    assert concurrency["cancel-in-progress"] is False


def test_draft_release_exists_before_publication_and_is_finalised_after() -> None:
    publish = _load_workflow("release.yml")["jobs"]["publish"]

    draft_index = _step_index(publish, "--draft \\")
    publish_index = _step_index(publish, "uv publish")
    validate_index = _step_index(publish, "is missing required asset")
    finalise_index = _step_index(publish, "--draft=false")

    # A failure after the immutable upload must leave a discoverable draft
    # holding the authoritative assets.
    assert draft_index < publish_index < validate_index < finalise_index


def test_publication_is_idempotence_aware() -> None:
    publish = _load_workflow("release.yml")["jobs"]["publish"]
    upload = publish["steps"][_step_index(publish, "uv publish")]

    assert upload["if"] == "steps.index.outputs.should_publish == 'true'"


def test_pre_release_tags_are_not_promoted_to_latest() -> None:
    """The project tags betas (`v0.16.0b7`). Finalising those with `--latest`
    would advertise a pre-release as the current version."""

    publish = _load_workflow("release.yml")["jobs"]["publish"]
    draft = publish["steps"][_step_index(publish, "--draft \\")]["run"]

    assert "--prerelease" in draft
    assert "^v[0-9]+\\.[0-9]+\\.[0-9]+$" in draft
    # Finalisation reuses the maturity decided at draft time rather than
    # hardcoding one.
    finalise = publish["steps"][_step_index(publish, "--draft=false")]["run"]
    assert "RELEASE_MATURITY" in finalise


def test_handoff_rejects_an_asset_swapped_after_verification(tmp_path: Path) -> None:
    wheel = _write_wheel(tmp_path / "agents_shipgate-9.9.9-py3-none-any.whl")
    manifest = tmp_path / "candidate-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "release_tag": "v9.9.9",
                "assets": [{"filename": wheel.name, "sha256": _digest(wheel)}],
            }
        ),
        encoding="utf-8",
    )
    expected = _digest(manifest)

    assert verify_manifest(manifest_path=manifest, expected_sha256=expected)

    _write_wheel(wheel, {"agents_shipgate/_swapped.py": "z = 3\n"})
    with pytest.raises(ConfigError, match="does not match the verified"):
        verify_manifest(manifest_path=manifest, expected_sha256=expected)


def test_handoff_rejects_a_manifest_rewritten_to_match_a_swap(tmp_path: Path) -> None:
    """The manifest digest travels through the job-output channel, so
    rewriting the manifest inside the artifact store does not help."""

    wheel = _write_wheel(tmp_path / "agents_shipgate-9.9.9-py3-none-any.whl")
    manifest = tmp_path / "candidate-manifest.json"
    manifest.write_text(
        json.dumps({"assets": [{"filename": wheel.name, "sha256": _digest(wheel)}]}),
        encoding="utf-8",
    )
    verified_digest = _digest(manifest)

    _write_wheel(wheel, {"agents_shipgate/_swapped.py": "z = 3\n"})
    manifest.write_text(
        json.dumps({"assets": [{"filename": wheel.name, "sha256": _digest(wheel)}]}),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="artifact handoff was modified"):
        verify_manifest(manifest_path=manifest, expected_sha256=verified_digest)


@pytest.mark.parametrize(
    ("files", "expected_state", "should_publish"),
    [
        ([], "absent", True),
        ([{"digests": {"sha256": "MATCH"}}], "published_identical", False),
    ],
)
def test_pypi_state_classification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    files: list[dict[str, Any]],
    expected_state: str,
    should_publish: bool,
) -> None:
    wheel = _write_wheel(tmp_path / "agents_shipgate-9.9.9-py3-none-any.whl")
    digest = _digest(wheel)
    resolved = [
        {"digests": {"sha256": digest if item["digests"]["sha256"] == "MATCH" else "other"}}
        for item in files
    ]
    monkeypatch.setattr(
        "scripts.release_publication._fetch_release_files", lambda *a, **k: resolved
    )

    result = pypi_state(wheel_path=wheel)

    assert result["state"] == expected_state
    assert result["should_publish"] is should_publish


def test_republishing_a_version_with_different_bytes_is_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wheel = _write_wheel(tmp_path / "agents_shipgate-9.9.9-py3-none-any.whl")
    monkeypatch.setattr(
        "scripts.release_publication._fetch_release_files",
        lambda *a, **k: [{"digests": {"sha256": "0" * 64}}],
    )

    with pytest.raises(ConfigError, match="already on the index with different bytes"):
        pypi_state(wheel_path=wheel)


def test_an_unreachable_index_is_not_read_as_permission_to_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wheel = _write_wheel(tmp_path / "agents_shipgate-9.9.9-py3-none-any.whl")

    def _explode(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        raise ConfigError("Unable to query https://pypi.org/pypi: timed out")

    monkeypatch.setattr("scripts.release_publication._fetch_release_files", _explode)

    with pytest.raises(ConfigError, match="Unable to query"):
        pypi_state(wheel_path=wheel)


# --------------------------------------------------------------------------
# #344 — deterministic correctness evidence
# --------------------------------------------------------------------------


def _test_step_command(job: dict[str, Any], name: str) -> str:
    for step in job["steps"]:
        if step.get("name") == name:
            return str(step["run"])
    raise AssertionError(f"no step named {name!r}")


def test_release_matches_ci_parallelism_and_excludes_perf() -> None:
    release_test = _test_step_command(
        _load_workflow("release-verify.yml")["jobs"]["verify"], "Test"
    )
    ci_test = _test_step_command(_load_workflow("ci.yml")["jobs"]["test"], "Test")

    # Same supported parallelism as CI: a release candidate should not spend
    # its budget re-running serially what CI already parallelises.
    assert "-n auto" in release_test
    assert "-n auto" in ci_test
    # Latency budgets stay a merge-time gate; shared-runner timing noise must
    # not be able to fail a release candidate.
    assert '-m "not perf"' in release_test
    assert "tests/test_latency_budget.py" not in release_test


def test_release_does_not_weaken_the_coverage_floor() -> None:
    release_test = _test_step_command(
        _load_workflow("release-verify.yml")["jobs"]["verify"], "Test"
    )
    ci_test = _test_step_command(_load_workflow("ci.yml")["jobs"]["test"], "Test")

    assert "--cov-fail-under=85" in release_test
    assert "--cov-fail-under=85" in ci_test


def test_adapter_static_only_lint_stays_covered_in_release() -> None:
    """It is excluded from the aggregate run, so it needs its own step or the
    trust-model invariant silently stops being checked at release time."""

    verify = _load_workflow("release-verify.yml")["jobs"]["verify"]
    aggregate = _test_step_command(verify, "Test")

    assert "--ignore=tests/test_adapter_static_only.py" in aggregate
    assert _step_index(verify, "tests/test_adapter_static_only.py -q") < _step_index(
        verify, "--cov-fail-under=85"
    )


def test_release_verification_timeout_is_documented_and_bounded() -> None:
    verify = _load_workflow("release-verify.yml")["jobs"]["verify"]
    source = (WORKFLOWS / "release-verify.yml").read_text(encoding="utf-8")

    assert verify["timeout-minutes"] == 25
    # The number has to be traceable to a measurement, not an estimate.
    assert "Measured, not estimated" in source


def test_perf_marker_is_declared_so_the_exclusion_is_meaningful() -> None:
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert "perf: latency-budget" in pyproject


# --------------------------------------------------------------------------
# #355 — the rehearsal cannot publish
# --------------------------------------------------------------------------


def test_rehearsal_exercises_the_same_verification_workflow() -> None:
    rehearsal = _load_workflow("release-rehearsal.yml")
    release = _load_workflow("release.yml")

    shared = "./.github/workflows/release-verify.yml"
    assert rehearsal["jobs"]["rehearse"]["uses"] == shared
    assert release["jobs"]["verify"]["uses"] == shared
    assert rehearsal["jobs"]["rehearse"]["with"]["mode"] == "rehearsal"


def test_rehearsal_has_no_publication_job_to_instantiate() -> None:
    rehearsal = _load_workflow("release-rehearsal.yml")

    # Asserted against the parsed workflow rather than its text: the file
    # explains in prose which publication verbs it deliberately omits, and a
    # substring check would match that explanation.
    assert set(rehearsal["jobs"]) == {"rehearse"}
    for job in rehearsal["jobs"].values():
        # A pure reusable-workflow call. There is no step list to smuggle a
        # publish command into.
        assert "steps" not in job
        assert "environment" not in job
        assert "run" not in job


def test_rehearsal_holds_no_write_or_oidc_authority() -> None:
    rehearsal = _load_workflow("release-rehearsal.yml")

    assert rehearsal["permissions"] == {"contents": "read"}
    assert rehearsal["jobs"]["rehearse"]["permissions"] == {"contents": "read"}
    # A reusable workflow cannot be granted more than it declares, so this cap
    # holds even if a caller asks for more.
    assert _load_workflow("release-verify.yml")["permissions"] == {"contents": "read"}


def test_rehearsal_is_manually_dispatchable_and_not_tag_triggered() -> None:
    triggers = _load_workflow("release-rehearsal.yml")["on"]

    assert set(triggers) == {"workflow_dispatch"}


def test_rehearsal_publishes_inspectable_candidate_artifacts() -> None:
    verify = _load_workflow("release-verify.yml")["jobs"]["verify"]

    upload = verify["steps"][_step_index(verify, "actions/upload-artifact")]
    assert upload["with"]["if-no-files-found"] == "error"
    assert _step_index(verify, "GITHUB_STEP_SUMMARY") > _step_index(
        verify, "actions/upload-artifact"
    )
