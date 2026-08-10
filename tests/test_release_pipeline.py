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

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
import yaml

from agents_shipgate.core.errors import ConfigError
from scripts._release_support import ReleaseError
from scripts.release_publication import pypi_state, verify_manifest
from scripts.release_sbom import dev_only_distributions, verify_release_sbom
from scripts.verify_wheel_provenance import compare_wheels, verify_wheel_provenance

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github/workflows"

WHEEL_FILENAME = "agents_shipgate-9.9.9-py3-none-any.whl"
WHEEL_METADATA = "Metadata-Version: 2.4\nName: agents-shipgate\nVersion: 9.9.9\n"
DIST_INFO = "agents_shipgate-9.9.9.dist-info"


def _write_wheel(path: Path, members: dict[str, str] | None = None, **kwargs: Any) -> Path:
    """Write a minimal but structurally valid wheel."""

    payload = {f"{DIST_INFO}/METADATA": WHEEL_METADATA, "agents_shipgate/__init__.py": "x = 1\n"}
    payload.update(members or {})
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", **kwargs) as archive:
        for name, content in payload.items():
            archive.writestr(name, content)
    return path


def _wheel_pair(
    tmp_path: Path, qualified_members: dict[str, str] | None = None, **kwargs: Any
) -> tuple[Path, Path]:
    """Built and qualified wheels sharing one basename, in separate directories."""

    built = _write_wheel(tmp_path / "built" / WHEEL_FILENAME)
    qualified = _write_wheel(tmp_path / "qualified" / WHEEL_FILENAME, qualified_members, **kwargs)
    return built, qualified


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def _job_commands(job: dict[str, Any]) -> str:
    return "\n".join(step["run"] for step in (job.get("steps") or []) if "run" in step)


# --------------------------------------------------------------------------
# #342 — the published wheel comes from the tagged source
# --------------------------------------------------------------------------


def test_identical_wheels_are_bound_by_bytes(tmp_path: Path) -> None:
    built, qualified = _wheel_pair(tmp_path)

    record = verify_wheel_provenance(built_path=built, qualified_path=qualified)

    assert record["provenance_mode"] == "identical_bytes"
    assert record["byte_reproducible"] is True


def test_qualified_wheel_carrying_extra_code_fails_closed(tmp_path: Path) -> None:
    """The attack #342 describes: a wheel with the right Name and Version but
    contents no tagged commit produced."""

    built, qualified = _wheel_pair(tmp_path, {"agents_shipgate/_backdoor.py": "import os\n"})

    with pytest.raises(ConfigError) as excinfo:
        verify_wheel_provenance(built_path=built, qualified_path=qualified)

    assert "_backdoor.py" in str(excinfo.value)
    # Even the explicitly weaker interim bar must not let this through.
    with pytest.raises(ConfigError):
        verify_wheel_provenance(
            built_path=built, qualified_path=qualified, allow_payload_equivalent=True
        )


def test_modified_module_contents_fail_closed(tmp_path: Path) -> None:
    built, qualified = _wheel_pair(tmp_path, {"agents_shipgate/__init__.py": "x = 666\n"})

    with pytest.raises(ConfigError, match="content differs"):
        verify_wheel_provenance(built_path=built, qualified_path=qualified)


def test_container_only_difference_is_rejected_unless_explicitly_allowed(tmp_path: Path) -> None:
    """A repacked archive with identical payload is a reproducibility gap, not
    a source mismatch — but the weaker bar must be opted into, never inferred."""

    built = _write_wheel(tmp_path / "built" / WHEEL_FILENAME, compression=zipfile.ZIP_STORED)
    qualified = _write_wheel(
        tmp_path / "qualified" / WHEEL_FILENAME, compression=zipfile.ZIP_DEFLATED
    )

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


@pytest.mark.parametrize(
    ("renamed", "expected"),
    [
        ("agents_shipgate-9.9.9-py2-none-any.whl", "compatibility tags"),
        ("agents_shipgate-9.9.10-py3-none-any.whl", "version"),
        ("other_dist-9.9.9-py3-none-any.whl", "distribution"),
    ],
)
def test_identical_bytes_under_a_different_filename_are_rejected(
    tmp_path: Path, renamed: str, expected: str
) -> None:
    """Filename version and compatibility tags are what an installer resolves
    against, so identical bytes under another name are a different artifact."""

    built = _write_wheel(tmp_path / "built" / WHEEL_FILENAME)
    qualified = _write_wheel(tmp_path / "qualified" / renamed)

    with pytest.raises(ConfigError, match=expected):
        verify_wheel_provenance(built_path=built, qualified_path=qualified)


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
# #342 / review — the candidate is pinned to an immutable commit
# --------------------------------------------------------------------------


def test_verification_runs_against_an_immutable_commit_not_a_symbolic_ref() -> None:
    """`github.ref` is re-resolved by the checkout action, so a tag moved
    between the push event and the checkout would build a different commit than
    the one provenance records."""

    verify = _load_workflow("release.yml")["jobs"]["verify"]

    assert verify["with"]["ref"] == "${{ github.sha }}"
    assert "github.ref " not in json.dumps(verify["with"])


def test_provenance_is_keyed_to_the_resolved_checkout_sha() -> None:
    verify = _load_workflow("release-verify.yml")["jobs"]["verify"]
    commands = _job_commands(verify)

    # Resolved after checkout rather than taken from the event.
    assert 'source_sha="$(git rev-parse HEAD)"' in commands
    assert '--source-commit "${SOURCE_SHA}"' in commands
    assert verify["outputs"]["source_sha"] == "${{ steps.candidate.outputs.source_sha }}"


def test_tag_is_reconfirmed_before_each_irreversible_step() -> None:
    """A tag can move or be deleted after verification; both the staging job
    and the token-bearing publish job re-peel it before acting."""

    release = _load_workflow("release.yml")

    for job_name in ("stage", "publish"):
        commands = _job_commands(release["jobs"][job_name])
        assert "git ls-remote" in commands, job_name
        assert "refs/tags/${RELEASE_TAG}^{}" in commands, job_name
        assert "not the verified ${SOURCE_SHA}" in commands, job_name

    # gh refuses to create a release for a tag that is not on the remote.
    assert "--verify-tag" in _job_commands(release["jobs"]["stage"])


# --------------------------------------------------------------------------
# #356 — the SBOM describes the shipped wheel
# --------------------------------------------------------------------------


def _sbom(
    components: list[str],
    *,
    digest: str,
    version: str = "9.9.9",
    subject_ref: str = "agents-shipgate==9.9.9",
    dependencies: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "metadata": {
            "component": {
                "type": "library",
                "bom-ref": subject_ref,
                "name": "agents-shipgate",
                "version": version,
                "hashes": [{"alg": "SHA-256", "content": digest}],
            }
        },
        "components": [{"name": name, "version": "1.0"} for name in components],
        **({"dependencies": dependencies} if dependencies is not None else {}),
    }


def test_dev_only_guard_covers_the_release_tooling_that_leaked_before() -> None:
    forbidden = dev_only_distributions(REPO_ROOT / "pyproject.toml")

    # Exactly the packages `cyclonedx-py environment` over `.[dev]` inventoried.
    assert {"pytest", "ruff", "twine", "sigstore", "cyclonedx-bom", "pip-audit"} <= forbidden
    # Runtime dependencies must never be classified as dev-only.
    assert not forbidden & {"pydantic", "typer", "pyyaml", "cryptography", "packaging"}


def test_sbom_containing_a_dev_only_dependency_is_rejected(tmp_path: Path) -> None:
    wheel = _write_wheel(tmp_path / WHEEL_FILENAME)
    sbom_path = tmp_path / "sbom.json"
    sbom_path.write_text(
        json.dumps(_sbom(["pydantic", "pytest"], digest=_digest(wheel))), encoding="utf-8"
    )

    with pytest.raises(ReleaseError, match="dev-only"):
        verify_release_sbom(wheel_path=wheel, sbom_path=sbom_path)


def test_sbom_for_a_runtime_only_environment_is_accepted(tmp_path: Path) -> None:
    wheel = _write_wheel(tmp_path / WHEEL_FILENAME)
    sbom_path = tmp_path / "sbom.json"
    sbom_path.write_text(
        json.dumps(_sbom(["pydantic", "typer"], digest=_digest(wheel))), encoding="utf-8"
    )

    assert verify_release_sbom(wheel_path=wheel, sbom_path=sbom_path)


def test_sbom_describing_different_bytes_fails_before_publication(tmp_path: Path) -> None:
    wheel = _write_wheel(tmp_path / WHEEL_FILENAME)
    other = _write_wheel(tmp_path / "other" / WHEEL_FILENAME, {"agents_shipgate/_x.py": "y = 2\n"})
    sbom_path = tmp_path / "sbom.json"
    sbom_path.write_text(json.dumps(_sbom(["pydantic"], digest=_digest(other))), encoding="utf-8")

    with pytest.raises(ReleaseError, match="no SHA-256 matching the wheel"):
        verify_release_sbom(wheel_path=wheel, sbom_path=sbom_path)


def test_sbom_without_a_bound_component_is_rejected(tmp_path: Path) -> None:
    wheel = _write_wheel(tmp_path / WHEEL_FILENAME)
    sbom_path = tmp_path / "sbom.json"
    sbom_path.write_text(json.dumps({"bomFormat": "CycloneDX", "components": []}), encoding="utf-8")

    with pytest.raises(ReleaseError, match="no metadata.component"):
        verify_release_sbom(wheel_path=wheel, sbom_path=sbom_path)


def test_sbom_describing_the_subject_twice_is_rejected(tmp_path: Path) -> None:
    """Promoting the wheel into `metadata.component` while leaving it in
    `components` would leave consumers unable to tell which node the document
    is about, and the dependency graph keyed to the wrong one."""

    wheel = _write_wheel(tmp_path / WHEEL_FILENAME)
    sbom_path = tmp_path / "sbom.json"
    sbom_path.write_text(
        json.dumps(_sbom(["agents-shipgate", "pydantic"], digest=_digest(wheel))), encoding="utf-8"
    )

    with pytest.raises(ReleaseError, match="described twice"):
        verify_release_sbom(wheel_path=wheel, sbom_path=sbom_path)


def test_sbom_subject_must_have_exactly_one_dependency_node(tmp_path: Path) -> None:
    wheel = _write_wheel(tmp_path / WHEEL_FILENAME)
    sbom_path = tmp_path / "sbom.json"
    sbom_path.write_text(
        json.dumps(
            _sbom(
                ["pydantic"],
                digest=_digest(wheel),
                dependencies=[{"ref": "pydantic==1.0", "dependsOn": []}],
            )
        ),
        encoding="utf-8",
    )

    with pytest.raises(ReleaseError, match="dependency nodes for the declared subject"):
        verify_release_sbom(wheel_path=wheel, sbom_path=sbom_path)

    sbom_path.write_text(
        json.dumps(
            _sbom(
                ["pydantic"],
                digest=_digest(wheel),
                dependencies=[{"ref": "agents-shipgate==9.9.9", "dependsOn": ["pydantic==1.0"]}],
            )
        ),
        encoding="utf-8",
    )
    assert verify_release_sbom(wheel_path=wheel, sbom_path=sbom_path)


def test_staging_rechecks_the_sbom_binding_before_publication() -> None:
    release = _load_workflow("release.yml")

    assert "scripts/release_sbom.py verify" in _job_commands(release["jobs"]["stage"])
    assert release["jobs"]["publish"]["needs"] == ["verify", "stage"]


# --------------------------------------------------------------------------
# #343 — separated verification, recoverable publication
# --------------------------------------------------------------------------


def test_the_pipeline_separates_verification_staging_publication_and_finalisation() -> None:
    release = _load_workflow("release.yml")

    assert list(release["jobs"]) == ["verify", "stage", "publish", "finalize"]
    assert release["jobs"]["verify"]["uses"] == "./.github/workflows/release-verify.yml"
    assert release["jobs"]["stage"]["needs"] == "verify"
    assert release["jobs"]["publish"]["needs"] == ["verify", "stage"]
    assert release["jobs"]["finalize"]["needs"] == ["verify", "stage", "publish"]


def test_write_and_oidc_authority_are_never_held_together() -> None:
    """A job that can mint a PyPI token must not also be able to rewrite the
    repository, and vice versa."""

    release = _load_workflow("release.yml")
    verify_workflow = _load_workflow("release-verify.yml")

    assert release["permissions"] == {}
    assert release["jobs"]["verify"]["permissions"] == {"contents": "read"}
    assert verify_workflow["permissions"] == {"contents": "read"}
    assert verify_workflow["jobs"]["verify"]["permissions"] == {"contents": "read"}

    assert release["jobs"]["stage"]["permissions"] == {"contents": "write", "actions": "read"}
    assert release["jobs"]["publish"]["permissions"] == {"id-token": "write"}
    assert release["jobs"]["finalize"]["permissions"] == {"contents": "write"}

    for job_name in ("verify", "stage", "finalize"):
        assert "id-token" not in release["jobs"][job_name].get("permissions", {})
    assert "contents" not in release["jobs"]["publish"]["permissions"]
    assert release["jobs"]["publish"]["environment"] == "pypi"


def test_the_token_bearing_job_installs_no_project_code() -> None:
    """A compromised build backend or dev dependency in the job holding
    `id-token: write` could request the PyPI token directly."""

    publish = _load_workflow("release.yml")["jobs"]["publish"]
    commands = _job_commands(publish)

    assert "pip install -e" not in commands
    assert '".[dev]"' not in commands
    # Installs only the hash-locked closure, and verifies hashes on install.
    assert "--require-hashes" in commands
    assert "constraints/release-publish.txt" in json.dumps(publish)
    # No checkout of the repository at all.
    assert not any("actions/checkout" in str(step.get("uses", "")) for step in publish["steps"])


def test_the_publication_toolchain_is_hash_locked() -> None:
    lockfile = (REPO_ROOT / "constraints/release-publish.txt").read_text(encoding="utf-8")

    assert "sigstore==" in lockfile
    assert "uv==" in lockfile
    assert lockfile.count("--hash=sha256:") > 10
    # No ranged requirements: every line pins an exact version.
    assert ">=" not in lockfile.replace("# ", "")


def test_release_concurrency_is_serialised_across_the_pypi_project() -> None:
    concurrency = _load_workflow("release.yml")["concurrency"]

    # A per-tag group would let two tags race to publish the same distribution.
    assert "${{" not in concurrency["group"]
    assert concurrency["cancel-in-progress"] is False


def test_index_state_is_classified_before_any_release_is_mutated() -> None:
    """A divergent version must fail with both registries untouched."""

    stage = _load_workflow("release.yml")["jobs"]["stage"]

    assert _step_index(stage, "scripts/release_publication.py pypi-state") < _step_index(
        stage, "gh release create"
    )


def test_only_draft_releases_are_ever_mutated() -> None:
    """Clobbering a published release's assets on a re-run would replace public
    bytes that PyPI can no longer be made to match."""

    stage = _load_workflow("release.yml")["jobs"]["stage"]
    commands = _job_commands(stage)

    assert "--json isDraft --jq .isDraft" in commands
    # The published branch verifies and reports, and does not upload.
    published_branch = commands.split("Published already", 1)[-1]
    assert "gh release upload" not in published_branch
    assert "leaving it untouched" in published_branch


def test_draft_release_exists_before_publication_and_is_finalised_after() -> None:
    release = _load_workflow("release.yml")
    stage = _job_commands(release["jobs"]["stage"])
    finalize = release["jobs"]["finalize"]

    assert "--draft \\" in stage
    # A failure after the immutable upload leaves a discoverable draft holding
    # the authoritative assets.
    assert _step_index(finalize, "is missing required asset") < _step_index(
        finalize, "--draft=false"
    )


def test_publication_is_idempotence_aware() -> None:
    publish = _load_workflow("release.yml")["jobs"]["publish"]
    upload = publish["steps"][_step_index(publish, "uv publish")]

    assert upload["if"] == "needs.stage.outputs.should_publish == 'true'"


def test_release_assets_are_uploaded_from_an_explicit_allowlist() -> None:
    """`dist/*` would upload whatever happens to be in the directory."""

    stage = _job_commands(_load_workflow("release.yml")["jobs"]["stage"])

    assert 'gh release create "${RELEASE_TAG}" "${assets[@]}"' in stage
    assert "dist/*" not in stage


def test_pre_release_tags_are_not_promoted_to_latest() -> None:
    """The project tags betas (`v0.16.0b7`). Finalising those with `--latest`
    would advertise a pre-release as the current version."""

    release = _load_workflow("release.yml")

    for job_name in ("stage", "finalize"):
        commands = _job_commands(release["jobs"][job_name])
        assert "--prerelease" in commands, job_name
        assert "^v[0-9]+\\.[0-9]+\\.[0-9]+$" in commands, job_name


# --------------------------------------------------------------------------
# #343 / review — the content-addressed handoff
# --------------------------------------------------------------------------


def _manifest(tmp_path: Path, wheel: Path, extra: list[Path] | None = None) -> Path:
    assets = [{"filename": wheel.name, "sha256": _digest(wheel)}]
    for path in extra or []:
        assets.append({"filename": path.name, "sha256": _digest(path)})
    manifest = tmp_path / "candidate-manifest.json"
    manifest.write_text(json.dumps({"release_tag": "v9.9.9", "assets": assets}), encoding="utf-8")
    return manifest


def test_handoff_rejects_an_asset_swapped_after_verification(tmp_path: Path) -> None:
    wheel = _write_wheel(tmp_path / WHEEL_FILENAME)
    manifest = _manifest(tmp_path, wheel)
    expected = _digest(manifest)

    assert verify_manifest(manifest_path=manifest, expected_sha256=expected)

    _write_wheel(wheel, {"agents_shipgate/_swapped.py": "z = 3\n"})
    with pytest.raises(ReleaseError, match="does not match the verified"):
        verify_manifest(manifest_path=manifest, expected_sha256=expected)


def test_handoff_rejects_a_manifest_rewritten_to_match_a_swap(tmp_path: Path) -> None:
    """The manifest digest travels through the job-output channel, so
    rewriting the manifest inside the artifact store does not help."""

    wheel = _write_wheel(tmp_path / WHEEL_FILENAME)
    manifest = _manifest(tmp_path, wheel)
    verified_digest = _digest(manifest)

    _write_wheel(wheel, {"agents_shipgate/_swapped.py": "z = 3\n"})
    _manifest(tmp_path, wheel)

    with pytest.raises(ReleaseError, match="artifact handoff was modified"):
        verify_manifest(manifest_path=manifest, expected_sha256=verified_digest)


@pytest.mark.parametrize("supplied", ["", "not-a-digest", "abc123"])
def test_a_missing_or_malformed_expected_digest_never_passes(tmp_path: Path, supplied: str) -> None:
    """A truthiness check would fail open here: a redacted or absent job output
    arrives as the empty string, and the workflow still passes the flag."""

    wheel = _write_wheel(tmp_path / WHEEL_FILENAME)
    manifest = _manifest(tmp_path, wheel)

    with pytest.raises(ReleaseError, match="not a 64-character lowercase SHA-256"):
        verify_manifest(manifest_path=manifest, expected_sha256=supplied)


def test_the_expected_digest_flag_is_mandatory_on_the_command_line() -> None:
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "release_publication.py"),
            "verify-manifest",
            "--manifest",
            "missing.json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "--expected-sha256" in result.stderr


def test_handoff_rejects_files_the_manifest_does_not_list(tmp_path: Path) -> None:
    """An intact manifest beside an unlisted sdist would otherwise be uploaded
    unverified."""

    wheel = _write_wheel(tmp_path / WHEEL_FILENAME)
    manifest = _manifest(tmp_path, wheel)
    expected = _digest(manifest)
    (tmp_path / "agents_shipgate-9.9.9.tar.gz").write_text("smuggled", encoding="utf-8")

    with pytest.raises(ReleaseError, match="does not list"):
        verify_manifest(manifest_path=manifest, expected_sha256=expected)

    # Signature bundles are permitted only by explicit name.
    assert verify_manifest(
        manifest_path=manifest,
        expected_sha256=expected,
        allowed_extra={"agents_shipgate-9.9.9.tar.gz"},
    )


# --------------------------------------------------------------------------
# #343 / review — PyPI index classification
# --------------------------------------------------------------------------


def _record(
    filename: str = WHEEL_FILENAME,
    sha256: str | None = None,
    packagetype: str = "bdist_wheel",
    yanked: bool = False,
) -> dict[str, Any]:
    return {
        "filename": filename,
        "packagetype": packagetype,
        "digests": {"sha256": sha256 or "PLACEHOLDER"},
        "yanked": yanked,
    }


def _patch_index(
    monkeypatch: pytest.MonkeyPatch, records: list[dict[str, Any]], digest: str
) -> None:
    resolved = [
        {**item, "digests": {**item["digests"], "sha256": digest}}
        if item.get("digests", {}).get("sha256") == "PLACEHOLDER"
        else item
        for item in records
    ]
    monkeypatch.setattr(
        "scripts.release_publication._fetch_release_files", lambda *a, **k: resolved
    )


def test_an_absent_version_is_published(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wheel = _write_wheel(tmp_path / WHEEL_FILENAME)
    _patch_index(monkeypatch, [], _digest(wheel))

    result = pypi_state(wheel_path=wheel)

    assert result["state"] == "absent"
    assert result["should_publish"] is True


def test_the_exact_published_wheel_completes_an_interrupted_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wheel = _write_wheel(tmp_path / WHEEL_FILENAME)
    _patch_index(monkeypatch, [_record()], _digest(wheel))

    result = pypi_state(wheel_path=wheel)

    assert result["state"] == "published_identical"
    assert result["should_publish"] is False


@pytest.mark.parametrize(
    ("records", "why"),
    [
        (
            [_record(), _record(filename="agents_shipgate-9.9.9.tar.gz", packagetype="sdist")],
            "a divergent sdist alongside the matching wheel",
        ),
        ([_record(), _record(filename="agents_shipgate-9.9.9-py2-none-any.whl")], "a second wheel"),
        ([_record(filename="renamed-9.9.9-py3-none-any.whl")], "a renamed file"),
        ([_record(yanked=True)], "a yanked record"),
        ([_record(packagetype="sdist")], "the wrong package type"),
        ([{"filename": WHEEL_FILENAME, "packagetype": "bdist_wheel"}], "missing digests"),
        ([_record(sha256="0" * 64)], "different bytes"),
    ],
)
def test_anything_but_the_exact_single_wheel_is_divergent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    records: list[dict[str, Any]],
    why: str,
) -> None:
    """Digest *membership* is not enough: skipping the upload and finalising
    over any of these would ship a release this pipeline never verified."""

    wheel = _write_wheel(tmp_path / WHEEL_FILENAME)
    _patch_index(monkeypatch, records, _digest(wheel))

    with pytest.raises(ReleaseError, match="not as the single"):
        pypi_state(wheel_path=wheel)


def test_an_unreachable_index_is_not_read_as_permission_to_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wheel = _write_wheel(tmp_path / WHEEL_FILENAME)

    def _explode(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        raise ReleaseError("Unable to query https://pypi.org/pypi: timed out")

    monkeypatch.setattr("scripts.release_publication._fetch_release_files", _explode)

    with pytest.raises(ReleaseError, match="Unable to query"):
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
# #355 — the rehearsal cannot publish, and is required
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


def test_publication_requires_a_rehearsal_of_this_exact_candidate() -> None:
    """#355 made rehearsal mandatory in the runbook; without enforcement that
    acceptance criterion rests on operator discipline."""

    stage = _load_workflow("release.yml")["jobs"]["stage"]
    commands = _job_commands(stage)

    # Bound to the source tree (and therefore the workflow revision)...
    assert "actions/workflows/release-rehearsal.yml/runs?head_sha=${SOURCE_SHA}" in commands
    assert "status=success" in commands
    assert "No successful Release Rehearsal run" in commands
    # ...and to candidate identity, so a qualification artifact swapped between
    # the rehearsal and the tag is caught.
    assert "cmp -- rehearsed/candidate-manifest.json dist/candidate-manifest.json" in commands
    assert _step_index(stage, "release-rehearsal.yml/runs") < _step_index(
        stage, "gh release create"
    )


def test_every_rehearsal_proves_the_provenance_gate_fails_closed() -> None:
    """The deliberate failure-path exercise is executed, not documented."""

    verify = _load_workflow("release-verify.yml")["jobs"]["verify"]
    step = verify["steps"][_step_index(verify, "fault-injected.whl")]

    assert step["if"] == "inputs.mode == 'rehearsal'"
    assert "does not fail closed" in step["run"]
    # It must assert the gate *rejects* the tampered wheel.
    assert "if python scripts/verify_wheel_provenance.py" in step["run"]


def test_rehearsal_publishes_inspectable_candidate_artifacts() -> None:
    verify = _load_workflow("release-verify.yml")["jobs"]["verify"]

    upload = verify["steps"][_step_index(verify, "actions/upload-artifact")]
    assert upload["with"]["if-no-files-found"] == "error"
    assert _step_index(verify, "GITHUB_STEP_SUMMARY") > _step_index(
        verify, "actions/upload-artifact"
    )


# --------------------------------------------------------------------------
# Review — qualification trust roots are reviewed code, not mutable config
# --------------------------------------------------------------------------


def test_signer_identity_and_issuer_are_not_mutable_configuration() -> None:
    """An actor able to set variables could otherwise substitute fabricated
    qualification evidence *and* replace the identity that authenticates it, in
    one step. Source-to-wheel binding does not help: that attack reuses the
    legitimate wheel and forges only the safety claims about it."""

    verify_source = (WORKFLOWS / "release-verify.yml").read_text(encoding="utf-8")

    assert "vars.SAFETY_QUALIFICATION_SIGNER_IDENTITY" not in verify_source
    assert "vars.SAFETY_QUALIFICATION_OIDC_ISSUER" not in verify_source
    assert "steps.trust_roots.outputs.signer_identity" in verify_source
    assert "steps.trust_roots.outputs.oidc_issuer" in verify_source

    # Only content-addressed locations stay mutable.
    for name in (
        "SAFETY_QUALIFICATION_WHEEL_URL",
        "SAFETY_QUALIFICATION_JSON_URL",
        "SAFETY_QUALIFICATION_SIGSTORE_BUNDLE_URL",
    ):
        assert f"vars.{name}" in verify_source


def test_trust_roots_are_committed_and_fail_closed_while_unset() -> None:
    roots = json.loads((REPO_ROOT / ".github/release-trust-roots.json").read_text("utf-8"))

    assert set(roots) >= {"signer_identity", "oidc_issuer"}
    assert roots["oidc_issuer"].startswith("https://")

    commands = _job_commands(_load_workflow("release-verify.yml")["jobs"]["verify"])
    # An unset trust root must stop the release rather than default to
    # something permissive.
    assert '= "CHANGE_ME"' in commands
    assert "configure the qualification trust root before releasing" in commands
