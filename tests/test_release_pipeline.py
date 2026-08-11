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
* the whole path is rehearsable without any publication authority (#355);
* the release publishes the changelog and installs a reviewed lock (#345).
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts._release_support import ReleaseError
from scripts.release_notes import (
    MAX_BODY_CHARACTERS,
    assert_expected_digest,
    extract_release_notes,
    notes_digest,
)
from scripts.release_publication import pypi_state, verify_manifest
from scripts.release_sbom import dev_only_distributions, verify_release_sbom
from scripts.update_locks import compile_command, prose_header
from scripts.verify_dependency_lock import (
    DECLARATION_SENTINEL,
    LOCK_TARGETS,
    LockTarget,
    co_installed_problems,
    normalize_requirement,
    parse_lock,
    render_declarations,
    verify_all,
    verify_lock_target,
)
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


def _requirement_lines(lockfile: str) -> list[str]:
    """The pins themselves, without the prose header or the declaration block.

    Comment text is not evidence about what pip will install, and the recorded
    declarations legitimately contain the ranges those pins resolve.
    """

    return [line for line in lockfile.splitlines() if line.strip() and not line.startswith("#")]


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

    with pytest.raises(ReleaseError) as excinfo:
        verify_wheel_provenance(built_path=built, qualified_path=qualified)

    assert "_backdoor.py" in str(excinfo.value)
    # Even the explicitly weaker interim bar must not let this through.
    with pytest.raises(ReleaseError):
        verify_wheel_provenance(
            built_path=built, qualified_path=qualified, allow_payload_equivalent=True
        )


def test_modified_module_contents_fail_closed(tmp_path: Path) -> None:
    built, qualified = _wheel_pair(tmp_path, {"agents_shipgate/__init__.py": "x = 666\n"})

    with pytest.raises(ReleaseError, match="content differs"):
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

    with pytest.raises(ReleaseError, match="not byte-identical"):
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

    with pytest.raises(ReleaseError, match=expected):
        verify_wheel_provenance(built_path=built, qualified_path=qualified)


def test_release_verification_gates_publication_on_source_binding() -> None:
    verify = _load_workflow("release-verify.yml")["jobs"]["artifact"]

    build_index = _step_index(verify, "python -m build --wheel")
    provenance_index = _step_index(verify, "scripts/verify_wheel_provenance.py")
    handoff_index = _step_index(verify, "scripts/release_publication.py manifest")

    assert build_index < provenance_index < handoff_index


def test_build_backend_is_pinned_so_byte_equality_is_achievable() -> None:
    """Wheels record `Generator: hatchling <version>`, so an unpinned backend
    makes the byte-equality gate fail on legitimate releases."""

    constraints = (REPO_ROOT / "constraints/release-build.txt").read_text(encoding="utf-8")
    assert "hatchling==" in constraints

    verify = _load_workflow("release-verify.yml")["jobs"]["artifact"]
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
    verify = _load_workflow("release-verify.yml")["jobs"]["artifact"]
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
    assert verify_workflow["jobs"]["tests"]["permissions"] == {"contents": "read"}
    assert verify_workflow["jobs"]["artifact"]["permissions"] == {"contents": "read"}

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
    # Installs only via the shared constrained action, which hash-verifies and
    # allowlists what it installs.
    assert any(
        "install-release-toolchain" in str(step.get("uses", "")) for step in publish["steps"]
    )
    assert "constraints/release-publish.txt" in json.dumps(publish)
    # No checkout of the repository at all.
    assert not any("actions/checkout" in str(step.get("uses", "")) for step in publish["steps"])


def test_the_publication_toolchain_is_hash_locked() -> None:
    lockfile = (REPO_ROOT / "constraints/release-publish.txt").read_text(encoding="utf-8")

    assert "sigstore==" in lockfile
    assert "uv==" in lockfile
    assert lockfile.count("--hash=sha256:") > 10
    # No ranged requirements: every line pins an exact version.
    assert not [line for line in _requirement_lines(lockfile) if ">=" in line]


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
    assert _step_index(finalize, "verify-manifest") < _step_index(finalize, "--draft=false")


def test_index_state_is_reclassified_inside_the_publish_attempt() -> None:
    """Reusing stage's cached result would make a re-run after a post-upload
    failure retry an immutable version and never reach recovery."""

    publish = _load_workflow("release.yml")["jobs"]["publish"]
    upload = publish["steps"][_step_index(publish, "uv publish --trusted-publishing")]

    # The decision is taken inside the step, from a fresh query — and made
    # with runner-provided tools, never a helper fetched from the candidate
    # tree, because this job can mint a PyPI token.
    assert "if" not in upload
    assert "https://pypi.org/pypi/agents-shipgate/" in upload["run"]
    assert "jq -r" in upload["run"]
    assert "published_identical" in upload["run"]
    assert "published_divergent" in upload["run"]
    # An unreachable index is not permission to upload.
    assert "not permission to upload" in upload["run"]


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
    release_test = _test_step_command(_load_workflow("release-verify.yml")["jobs"]["tests"], "Test")
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
    release_test = _test_step_command(_load_workflow("release-verify.yml")["jobs"]["tests"], "Test")
    ci_test = _test_step_command(_load_workflow("ci.yml")["jobs"]["test"], "Test")

    assert "--cov-fail-under=85" in release_test
    assert "--cov-fail-under=85" in ci_test


def test_adapter_static_only_lint_stays_covered_in_release() -> None:
    """It is excluded from the aggregate run, so it needs its own step or the
    trust-model invariant silently stops being checked at release time."""

    tests_job = _load_workflow("release-verify.yml")["jobs"]["tests"]
    aggregate = _test_step_command(tests_job, "Test")

    assert "--ignore=tests/test_adapter_static_only.py" in aggregate
    assert _step_index(tests_job, "tests/test_adapter_static_only.py -q") < _step_index(
        tests_job, "--cov-fail-under=85"
    )


def test_release_verification_timeout_is_documented_and_bounded() -> None:
    workflow = _load_workflow("release-verify.yml")
    source = (WORKFLOWS / "release-verify.yml").read_text(encoding="utf-8")

    # The suite dominates one job; artifact sealing is much cheaper. Both are
    # bounded, and neither number is an estimate.
    assert workflow["jobs"]["tests"]["timeout-minutes"] == 20
    assert workflow["jobs"]["artifact"]["timeout-minutes"] == 15
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

    verify = _load_workflow("release-verify.yml")["jobs"]["artifact"]
    step = verify["steps"][_step_index(verify, "fault-injected.whl")]

    assert step["if"] == "inputs.mode == 'rehearsal'"
    assert "does not fail closed" in step["run"]
    # It must assert the gate *rejects* the tampered wheel.
    assert "if python scripts/verify_wheel_provenance.py" in step["run"]


def test_rehearsal_publishes_inspectable_candidate_artifacts() -> None:
    verify = _load_workflow("release-verify.yml")["jobs"]["artifact"]

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

    commands = _job_commands(_load_workflow("release-verify.yml")["jobs"]["artifact"])
    # An unset trust root must stop the release rather than default to
    # something permissive.
    assert '= "CHANGE_ME"' in commands
    assert "configure the qualification trust root before releasing" in commands


# --------------------------------------------------------------------------
# Review round 2 — the handoff, the tag, and the public release
# --------------------------------------------------------------------------


def test_every_caller_consumed_output_is_publicly_exported() -> None:
    """A reusable workflow's callers can read only the outputs declared in its
    `workflow_call.outputs` map. A job-level output that is not exported
    resolves to the empty string in the caller, silently — which is how
    `source_sha` shipped as a job output that no `needs.verify.outputs`
    reference could ever see.
    """

    import re

    exported = set(_load_workflow("release-verify.yml")["on"]["workflow_call"]["outputs"])
    consumed = set(
        re.findall(
            r"needs\.verify\.outputs\.(\w+)",
            (WORKFLOWS / "release.yml").read_text(encoding="utf-8"),
        )
    )

    assert consumed, "the caller consumes no outputs; the check would be vacuous"
    assert consumed <= exported, f"not exported: {sorted(consumed - exported)}"


def test_the_handoff_is_sealed_by_a_job_that_runs_no_candidate_tests() -> None:
    """In a combined job the qualified wheel stayed writable while the suite
    ran, so a test could replace the bytes after the equality check and before
    the handoff was sealed — with the provenance report still claiming
    equality."""

    workflow = _load_workflow("release-verify.yml")
    artifact = workflow["jobs"]["artifact"]
    commands = _job_commands(artifact)

    assert set(workflow["jobs"]) == {"tests", "artifact"}
    assert artifact["needs"] == "tests"
    # The sealing job runs no suite, no plugins, no audit.
    assert "pytest" not in commands
    assert "pip_audit" not in commands
    # And installs no editable project and no ranged dev extra: an unlocked
    # resolve here could rewrite the verifier and the digests it seals.
    assert "pip install -e" not in commands
    assert '".[dev]"' not in commands
    assert "--require-hashes" in commands
    assert "constraints/release-seal.txt" in commands
    # The suite job runs the exhaustive policy gate, so it downloads its own
    # copy — into a different directory the sealer never reads. It is a gate,
    # not a producer: nothing the sealer trusts comes out of it.
    tests_commands = _job_commands(workflow["jobs"]["tests"])
    assert "policy-dist" in tests_commands
    assert "qualified-dist" not in tests_commands
    assert "verify_wheel_provenance" not in tests_commands
    assert "release_publication.py manifest" not in tests_commands


def test_the_binding_is_reasserted_on_the_exact_bytes_being_sealed() -> None:
    artifact = _load_workflow("release-verify.yml")["jobs"]["artifact"]
    handoff = artifact["steps"][_step_index(artifact, "release_publication.py manifest")]["run"]

    # Provenance is re-derived inside the sealing step, before the copy.
    assert handoff.index("verify_wheel_provenance.py") < handoff.index("release_publication.py")


def test_a_completed_transaction_is_left_entirely_alone() -> None:
    """Re-signing a published release would mint fresh, non-reproducible
    Sigstore bundles and replace the public attestations for no benefit."""

    release = _load_workflow("release.yml")

    for job in ("publish", "finalize"):
        assert release["jobs"][job]["if"] == "needs.stage.outputs.release_state != 'published'"
    assert release["jobs"]["stage"]["outputs"]["release_state"] == (
        "${{ steps.release.outputs.release_state }}"
    )
    stage = _job_commands(release["jobs"]["stage"])
    for state in ("absent", "draft", "published"):
        assert f"release_state={state}" in stage


def test_registry_disagreement_stops_the_release() -> None:
    """A published GitHub release with an absent index is not a state to
    recover from automatically."""

    stage = _job_commands(_load_workflow("release.yml")["jobs"]["stage"])

    assert 'if [ "${INDEX_STATE}" != "published_identical" ]' in stage
    assert "the registries disagree" in stage


def test_finalisation_verifies_remote_bytes_not_asset_names() -> None:
    """Draft repair clobbers expected names but leaves unlisted assets behind,
    and an asset can be replaced during the approval window."""

    finalize = _load_workflow("release.yml")["jobs"]["finalize"]
    commands = _job_commands(finalize)

    # Every remote asset is downloaded and re-derived against the trusted
    # manifest digest, closed-world apart from the two signature bundles.
    assert "gh release download" in commands
    assert "verify-manifest" in commands
    assert '--expected-sha256 "${MANIFEST_SHA256}"' in commands
    # `--require`, not `--allow`: a release missing a bundle is incomplete.
    assert '--require "${WHEEL_FILENAME}.sigstore.json"' in commands
    assert "--require agents-shipgate-sbom.json.sigstore.json" in commands
    # The signature bundles are verified, not merely present.
    assert "sigstore verify identity" in commands
    assert _step_index(finalize, "sigstore verify identity") < _step_index(
        finalize, "--draft=false"
    )


def test_finalisation_refuses_to_mutate_a_release_that_is_no_longer_a_draft() -> None:
    finalize = _load_workflow("release.yml")["jobs"]["finalize"]
    commands = _job_commands(finalize)

    assert commands.count("--json isDraft --jq .isDraft") >= 2
    assert _step_index(finalize, "refusing to mutate a published release") < _step_index(
        finalize, "gh release upload"
    )


def test_the_tag_is_rebound_before_finalisation_and_again_before_undrafting() -> None:
    """PyPI holds the bytes for source A by this point; if the tag moves to B,
    GitHub's source archives resolve to different code than the index holds."""

    finalize = _load_workflow("release.yml")["jobs"]["finalize"]
    commands = _job_commands(finalize)

    assert commands.count("git ls-remote") >= 2
    assert _step_index(finalize, "not the verified ${SOURCE_SHA}") < _step_index(
        finalize, "gh release upload"
    )
    undraft = finalize["steps"][_step_index(finalize, "--draft=false")]["run"]
    assert "git ls-remote" in undraft
    assert "moved to" in undraft


def test_finalisation_runs_no_project_code_either() -> None:
    finalize = _load_workflow("release.yml")["jobs"]["finalize"]
    commands = _job_commands(finalize)

    assert not any("actions/checkout" in str(step.get("uses", "")) for step in finalize["steps"])
    assert "pip install -e" not in commands
    assert any(
        "install-release-toolchain" in str(step.get("uses", "")) for step in finalize["steps"]
    )
    # Uses the stdlib-only scripts fetched by immutable SHA.
    assert "tools/release_publication.py" in commands


# --------------------------------------------------------------------------
# Review round 3 — the sealer's trust boundary
# --------------------------------------------------------------------------


def test_the_sealer_installs_only_a_hash_locked_toolchain() -> None:
    """An unlocked `pip install -e ".[dev]"` in the sealing job resolves dozens
    of packages by range and runs before the build, the qualification checks,
    the provenance comparison and the sealing — so one compromised compatible
    release could rewrite the verifier and the digests it seals."""

    lockfile = (REPO_ROOT / "constraints/release-seal.txt").read_text(encoding="utf-8")

    for pinned in ("build==", "hatchling==", "sigstore=="):
        assert pinned in lockfile
    # CycloneDX left the closure when SBOM generation stopped launching the
    # target interpreter; the sealer's trusted surface shrank with it.
    assert "cyclonedx" not in lockfile
    assert lockfile.count("--hash=sha256:") > 20
    assert not [line for line in _requirement_lines(lockfile) if ">=" in line]


def test_the_sealer_and_the_build_pin_agree_on_the_backend() -> None:
    """A backend mismatch between the two lockfiles would break byte equality
    on a legitimate release."""

    def _hatchling(path: str) -> str:
        for line in (REPO_ROOT / path).read_text(encoding="utf-8").splitlines():
            if line.startswith("hatchling=="):
                return line.split()[0]
        raise AssertionError(f"no hatchling pin in {path}")

    assert _hatchling("constraints/release-build.txt") == _hatchling("constraints/release-seal.txt")


def test_the_sealer_restates_the_decisive_invariants_without_the_project() -> None:
    """The exhaustive re-derivation needs pydantic, so it runs in the gate job.
    The sealer must still restate the claims that delegate publication
    authority, or a signed-but-weakened artifact passes on its signature."""

    workflow = _load_workflow("release-verify.yml")
    sealer = _job_commands(workflow["jobs"]["artifact"])
    gate = _job_commands(workflow["jobs"]["tests"])

    assert "verify_qualification_binding.py" in sealer
    assert "verify_safety_qualification_release.py" not in sealer
    # The exhaustive version still runs, as a gate.
    assert "verify_safety_qualification_release.py" in gate


def test_the_stdlib_invariant_checker_rejects_a_weakened_signed_artifact(
    tmp_path: Path,
) -> None:
    from scripts.verify_qualification_binding import verify_qualification_binding

    wheel = _write_wheel(tmp_path / WHEEL_FILENAME)
    digest = _digest(wheel)

    def _artifact(**overrides: Any) -> Path:
        payload: dict[str, Any] = {
            "qualification_tier": "beta",
            "qualified": True,
            "production_qualified": True,
            "static_only": True,
            "runtime_behavior_proven": False,
            "failures": [],
            "cases": [
                {
                    "id": f"c{i}",
                    "expected_decision": "blocked",
                    "actual_decision": "blocked",
                    "receipt_sha256": f"{i:064x}",
                    "runtime_failure": False,
                }
                for i in range(100)
            ],
            "summary": {
                "total_cases": 100,
                "receipt_count": 100,
                "unsafe_auto_pass_count": 0,
                "runtime_failure_count": 0,
            },
            "inputs": {
                "wheel_name": "agents-shipgate",
                "wheel_version": "9.9.9",
                "engine_version": "9.9.9",
                "wheel_sha256": digest,
            },
        }
        payload.update(overrides)
        path = tmp_path / "qualification.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    # The honest artifact passes.
    assert verify_qualification_binding(
        qualification_path=_artifact(), wheel_path=wheel, tag="v9.9.9"
    )

    for overrides, expected in [
        ({"qualification_tier": "test"}, "tier is not beta"),
        ({"production_qualified": False}, "not production_qualified"),
        ({"cases": [{"id": "c0", "receipt_sha256": "0" * 64}]}, "cases, not 100"),
        ({"runtime_behavior_proven": True}, "runtime behaviour"),
        ({"failures": ["x"]}, "reports failures"),
    ]:
        with pytest.raises(ReleaseError, match=expected):
            verify_qualification_binding(
                qualification_path=_artifact(**overrides), wheel_path=wheel, tag="v9.9.9"
            )

    # And the binding itself: a different wheel is rejected even when every
    # policy claim is intact.
    other = _write_wheel(tmp_path / "other" / WHEEL_FILENAME, {"agents_shipgate/_x.py": "y = 1\n"})
    with pytest.raises(ReleaseError, match="SHA-256 mismatch"):
        verify_qualification_binding(qualification_path=_artifact(), wheel_path=other, tag="v9.9.9")


def test_the_suite_and_the_sealer_must_agree_on_the_commit() -> None:
    """They check out independently; a mutable ref could test A and seal B."""

    workflow = _load_workflow("release-verify.yml")
    sealer = _job_commands(workflow["jobs"]["artifact"])

    assert workflow["jobs"]["tests"]["outputs"]["source_sha"]
    assert "The suite ran against ${TESTED_SHA}" in sealer


def test_the_rehearsal_cannot_pass_a_mutable_ref() -> None:
    rehearsal = _load_workflow("release-rehearsal.yml")

    assert rehearsal["jobs"]["rehearse"]["with"]["ref"] == "${{ github.sha }}"
    # No free-form ref input to resolve twice.
    assert "ref" not in rehearsal["on"]["workflow_dispatch"]["inputs"]


def test_the_tag_is_rebound_inside_the_upload_step_itself() -> None:
    """Artifact download, digest verification, signing and the index query all
    sit between the previous peel and the irreversible upload."""

    publish = _load_workflow("release.yml")["jobs"]["publish"]
    upload = publish["steps"][_step_index(publish, "uv publish --trusted-publishing")]["run"]

    assert "git ls-remote" in upload
    assert upload.index("git ls-remote") < upload.index("uv publish --trusted-publishing")
    assert "refusing to publish" in upload


def test_an_already_published_release_must_be_complete_and_signed() -> None:
    """Declaring the transaction complete makes both signature-verifying jobs
    skip, so `--allow` (permit) was the wrong verb — the bundles must be
    required and verified here."""

    stage = _job_commands(_load_workflow("release.yml")["jobs"]["stage"])

    assert '--require "${WHEEL_FILENAME}.sigstore.json"' in stage
    assert "--require agents-shipgate-sbom.json.sigstore.json" in stage
    assert "sigstore verify identity" in stage


def test_deployment_prerequisites_are_documented_with_their_residuals() -> None:
    """Two of the windows cannot be closed by code in this repository, and the
    docs must say so rather than implying the workflow handles them."""

    runbook = (REPO_ROOT / "docs/release-runbook.md").read_text(encoding="utf-8")

    assert "## Deployment prerequisites" in runbook
    for prerequisite in ("Immutable releases", "updates and deletions", "release-write"):
        assert prerequisite in runbook
    # The honest limit about the workflow being candidate-controlled.
    assert "the workflow at that tag" in runbook


# --------------------------------------------------------------------------
# Review round 4 — code execution inside the sealer, and the draft/latest bug
# --------------------------------------------------------------------------


def test_the_sbom_inventory_never_executes_environment_code(tmp_path: Path) -> None:
    """Reproduction of the finding: `cyclonedx-py environment` inventories by
    *launching* the target interpreter, and interpreter startup runs `site`
    processing, which executes any `.pth` file beginning with `import`. Those
    files come from the wheel's runtime closure, resolved unpinned from the
    index — so the previous implementation ran third-party code inside the job
    that seals the release, before the handoff digests were computed.
    """

    import sys
    import venv as venv_module

    from scripts.release_sbom import inventory_environment

    env_dir = tmp_path / "runtime"
    venv_module.EnvBuilder(with_pip=False, symlinks=sys.platform != "win32").create(env_dir)
    site_packages = next(env_dir.glob("lib/python*/site-packages"), None) or (
        env_dir / "Lib/site-packages"
    )
    site_packages.mkdir(parents=True, exist_ok=True)

    # A minimal installed distribution, plus a .pth that would run on startup.
    dist_info = site_packages / "victim-1.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: victim\nVersion: 1.0\n", encoding="utf-8"
    )
    marker = tmp_path / "executed"
    (site_packages / "zz_evil.pth").write_text(
        f"import pathlib; pathlib.Path({str(marker)!r}).write_text('x')\n", encoding="utf-8"
    )

    entries = inventory_environment(env_dir)

    assert [entry["name"] for entry in entries] == ["victim"]
    assert not marker.exists(), "inventorying executed a .pth from the target environment"


def test_the_sbom_generator_needs_no_third_party_tooling() -> None:
    """Removing the launch also removed CycloneDX from the sealer's closure."""

    seal = (REPO_ROOT / "constraints/release-seal.txt").read_text(encoding="utf-8")
    source = (REPO_ROOT / "scripts/release_sbom.py").read_text(encoding="utf-8")

    assert "cyclonedx" not in seal
    assert "cyclonedx_py" not in source
    # Wheels only: an sdist would run its build backend during resolution.
    assert "--only-binary" in source


def test_the_wheel_is_built_with_the_locked_backend() -> None:
    """`python -m build` defaults to creating a fresh isolated environment and
    re-resolving the pyproject build requirements, which puts the backend's own
    transitive dependencies outside the hash-locked closure."""

    artifact = _load_workflow("release-verify.yml")["jobs"]["artifact"]
    build_step = artifact["steps"][_step_index(artifact, "python -m build --wheel")]["run"]

    assert "--no-isolation" in build_step


def test_a_draft_is_never_created_as_latest() -> None:
    """GitHub rejects `draft: true` together with `make_latest: true`, so
    requesting it at create time fails every first stable release during
    staging, before PyPI is touched."""

    release = _load_workflow("release.yml")
    stage = _job_commands(release["jobs"]["stage"])
    finalize = _job_commands(release["jobs"]["finalize"])

    # Creation never requests latest; a stable tag explicitly opts out.
    assert "--latest=false" in stage
    assert '"${create_maturity}"' in stage
    assert 'create_maturity="--latest"' not in stage
    # Maturity is applied only when the draft is lifted.
    assert 'maturity="--latest"' in finalize
    assert "--draft=false" in finalize


def test_the_sealer_seals_the_bytes_the_policy_gate_accepted() -> None:
    """The gate and the sealer download from the same mutable URLs at different
    times; without this the gate proves a policy about one artifact while the
    sealer seals another."""

    workflow = _load_workflow("release-verify.yml")
    gate_outputs = workflow["jobs"]["tests"]["outputs"]
    sealer = _job_commands(workflow["jobs"]["artifact"])

    assert "qualified_wheel_sha256" in gate_outputs
    assert "qualification_sha256" in gate_outputs
    assert "GATE_WHEEL_SHA256" in sealer
    assert "GATE_QUALIFICATION_SHA256" in sealer
    assert sealer.count("sha256sum --check --strict") >= 2


def test_qualification_counts_are_derived_from_cases_not_the_summary(tmp_path: Path) -> None:
    """The summary is a claim the artifact makes about itself. An attacker able
    to produce a validly signed artifact can also write `unsafe_auto_pass_count:
    0` above a hundred cases that say otherwise."""

    from scripts.verify_qualification_binding import verify_qualification_binding

    wheel = _write_wheel(tmp_path / WHEEL_FILENAME)
    cases = [
        {
            "id": f"c{index}",
            "expected_decision": "blocked",
            "actual_decision": "blocked",
            "receipt_sha256": f"{index:064x}",
            "runtime_failure": False,
        }
        for index in range(100)
    ]
    payload = {
        "qualification_tier": "beta",
        "qualified": True,
        "production_qualified": True,
        "static_only": True,
        "runtime_behavior_proven": False,
        "failures": [],
        "cases": cases,
        "summary": {
            "total_cases": 100,
            "receipt_count": 100,
            "unsafe_auto_pass_count": 0,
            "runtime_failure_count": 0,
        },
        "inputs": {
            "wheel_name": "agents-shipgate",
            "wheel_version": "9.9.9",
            "engine_version": "9.9.9",
            "wheel_sha256": _digest(wheel),
        },
    }
    path = tmp_path / "qualification.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert verify_qualification_binding(qualification_path=path, wheel_path=wheel, tag="v9.9.9")

    # A case that auto-passed something it should not have, with the summary
    # still claiming zero.
    payload["cases"][0] = {
        "id": "c0",
        "expected_decision": "blocked",
        "actual_decision": "passed",
        "receipt_sha256": f"{0:064x}",
        "runtime_failure": False,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ReleaseError, match="cases contain an unsafe auto-pass"):
        verify_qualification_binding(qualification_path=path, wheel_path=wheel, tag="v9.9.9")

    # Duplicated receipts are not 100 distinct receipts.
    payload["cases"][0] = dict(cases[0])
    payload["cases"][1] = dict(cases[0], id="c1")
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ReleaseError, match="receipt digests are not unique"):
        verify_qualification_binding(qualification_path=path, wheel_path=wheel, tag="v9.9.9")


def test_every_publication_side_job_constrains_what_it_installs() -> None:
    """`--require-hashes` constrains integrity, not choice: the lockfile comes
    from the candidate commit, so nothing in it stops a candidate adding a
    package to a job that can mint a PyPI token or rewrite a public release.

    The allowlist lives in one composite action so the three jobs cannot drift
    apart — an earlier revision guarded only the OIDC job.
    """

    import re as _re

    release = _load_workflow("release.yml")
    action = yaml.safe_load(
        (REPO_ROOT / ".github/actions/install-release-toolchain/action.yml").read_text("utf-8")
    )
    install = "\n".join(step["run"] for step in action["runs"]["steps"] if "run" in step)

    for job_name in ("stage", "publish", "finalize"):
        job = release["jobs"][job_name]
        uses = [str(step.get("uses", "")) for step in job["steps"]]
        assert any("install-release-toolchain" in item for item in uses), job_name
        # No job installs the lockfile directly, bypassing the allowlist.
        assert "pip install --require-hashes" not in _job_commands(job), job_name

    assert "not on the allowlist" in install
    assert "--require-hashes" in install

    # Every distribution the committed lockfile actually needs must be listed,
    # or the release fails on a legitimate lockfile.
    locked = {
        name.lower().replace("_", "-").replace(".", "-")
        for name in _re.findall(
            r"^([A-Za-z0-9][A-Za-z0-9._-]*)==",
            (REPO_ROOT / "constraints/release-publish.txt").read_text(encoding="utf-8"),
            _re.MULTILINE,
        )
    }
    allowed = set(_re.findall(r"[a-z0-9][a-z0-9-]*", install.split("grep -oE")[0]))
    assert locked <= allowed, f"not allowlisted: {sorted(locked - allowed)}"


# --------------------------------------------------------------------------
# #345 — the release page carries the CHANGELOG section, not a placeholder
# --------------------------------------------------------------------------


CHANGELOG_FIXTURE = """\
# Changelog

## Unreleased

- Something not shipped yet.

## 2.0.0 - 2026-08-11

- **Headline.** Body with `code`, a [link](https://example.com/x), and a
  continuation line.
- Nested:
  - inner item
  - another inner item

```text
## 1.0.0 - looks like a heading, but is inside a fence
```

Closing paragraph.

## 1.0.0 - 2026-01-01

- The first release.
"""

SECTION_2_0_0 = """\
- **Headline.** Body with `code`, a [link](https://example.com/x), and a
  continuation line.
- Nested:
  - inner item
  - another inner item

```text
## 1.0.0 - looks like a heading, but is inside a fence
```

Closing paragraph.
"""


def _changelog(tmp_path: Path, text: str = CHANGELOG_FIXTURE) -> Path:
    path = tmp_path / "CHANGELOG.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_section_a_tag_names_is_extracted_verbatim(tmp_path: Path) -> None:
    """Markdown is reproduced, not reflowed: the release page is the changelog
    entry contributors reviewed, character for character."""

    notes = extract_release_notes(changelog_path=_changelog(tmp_path), tag="v2.0.0")

    assert notes == SECTION_2_0_0
    # A fenced `## 1.0.0` line is content, not a section boundary — and the real
    # 1.0.0 section further down is still found.
    assert "inside a fence" in notes
    assert (
        extract_release_notes(changelog_path=_changelog(tmp_path), tag="v1.0.0")
        == "- The first release.\n"
    )


@pytest.mark.parametrize(
    "heading",
    ["## 2.0.0", "## v2.0.0", "## 2.0.0 - 2026-08-11", "## [2.0.0] - 2026-08-11"],
)
def test_the_conventional_heading_spellings_all_match(tmp_path: Path, heading: str) -> None:
    changelog = _changelog(tmp_path, f"# Changelog\n\n{heading}\n\n- Only entry.\n")

    assert extract_release_notes(changelog_path=changelog, tag="v2.0.0") == "- Only entry.\n"


def test_a_missing_section_fails_while_the_tag_can_still_be_fixed(tmp_path: Path) -> None:
    """The check runs in verification, which the rehearsal also runs, so this is
    reached before a tag exists — and always before publication."""

    changelog = _changelog(tmp_path)

    with pytest.raises(ReleaseError, match="no section for v3.0.0"):
        extract_release_notes(changelog_path=changelog, tag="v3.0.0")
    # The message says what to do, and lists what the file does describe.
    with pytest.raises(ReleaseError, match=r"'## 3.0.0 - <date>'"):
        extract_release_notes(changelog_path=changelog, tag="v3.0.0")


def test_unreleased_is_never_published_as_a_release_section(tmp_path: Path) -> None:
    """Promoting `## Unreleased` to the version being cut is part of cutting a
    release; if it were matched by fallback, nothing would enforce that."""

    changelog = _changelog(tmp_path, "# Changelog\n\n## Unreleased\n\n- Pending work.\n")

    with pytest.raises(ReleaseError, match="no section for v2.0.0"):
        extract_release_notes(changelog_path=changelog, tag="v2.0.0")


def test_an_empty_section_is_not_a_description(tmp_path: Path) -> None:
    changelog = _changelog(tmp_path, "# Changelog\n\n## 2.0.0 - 2026-08-11\n\n## 1.0.0\n\n- x\n")

    with pytest.raises(ReleaseError, match="is empty"):
        extract_release_notes(changelog_path=changelog, tag="v2.0.0")


def test_two_sections_for_one_version_are_not_silently_merged(tmp_path: Path) -> None:
    changelog = _changelog(
        tmp_path, "# Changelog\n\n## 2.0.0\n\n- a\n\n## 2.0.0 - 2026-08-11\n\n- b\n"
    )

    with pytest.raises(ReleaseError, match="ambiguous"):
        extract_release_notes(changelog_path=changelog, tag="v2.0.0")


def test_a_body_github_would_reject_fails_before_the_tag_is_pushed(tmp_path: Path) -> None:
    """`gh release create` 422s on an oversized body. The 0.16 development
    section is already ~75,000 characters, so this bound is a live constraint on
    this changelog, not a hypothetical one."""

    oversized = "- " + "x" * MAX_BODY_CHARACTERS + "\n"
    changelog = _changelog(tmp_path, f"# Changelog\n\n## 2.0.0 - 2026-08-11\n\n{oversized}")

    with pytest.raises(ReleaseError, match="GitHub rejects a body"):
        extract_release_notes(changelog_path=changelog, tag="v2.0.0")


def test_an_unterminated_fence_is_reported_rather_than_guessed_at(tmp_path: Path) -> None:
    changelog = _changelog(tmp_path, "# Changelog\n\n## 2.0.0\n\n```\nnever closed\n")

    with pytest.raises(ReleaseError, match="unterminated code fence"):
        extract_release_notes(changelog_path=changelog, tag="v2.0.0")


@pytest.mark.parametrize("closer", ["```", "```   ", "```\t", "````"])
def test_a_closing_fence_may_carry_trailing_whitespace(tmp_path: Path, closer: str) -> None:
    """CommonMark allows spaces and tabs after a closing fence. Requiring an
    exactly empty suffix left the fence open and failed the whole release with
    "unterminated code fence" — blocking rehearsal on a valid changelog."""

    changelog = _changelog(
        tmp_path,
        f"# Changelog\n\n## 2.0.0\n\n```text\nfenced\n{closer}\n\nAfter.\n\n## 1.0.0\n\n- old\n",
    )

    notes = extract_release_notes(changelog_path=changelog, tag="v2.0.0")

    assert notes.endswith("After.\n")
    assert "fenced" in notes
    # The 1.0.0 section is still reachable, i.e. the fence really did close.
    assert extract_release_notes(changelog_path=changelog, tag="v1.0.0") == "- old\n"


def test_the_notes_are_bound_to_the_digest_verification_published(tmp_path: Path) -> None:
    """Three jobs extract independently — verification, staging, finalisation —
    so the digest is what makes them one value rather than three reads that
    happen to agree."""

    changelog = _changelog(tmp_path)
    notes = extract_release_notes(changelog_path=changelog, tag="v2.0.0")

    assert assert_expected_digest(notes, notes_digest(notes)) == notes_digest(notes)
    with pytest.raises(ReleaseError, match="does not match the verified"):
        assert_expected_digest(notes, "0" * 64)


@pytest.mark.parametrize("supplied", ["", "not-a-digest", "ABC123"])
def test_a_missing_or_malformed_notes_digest_never_passes(tmp_path: Path, supplied: str) -> None:
    """A truthiness check would fail open: an absent or redacted job output
    arrives as the empty string and the workflow still passes the flag."""

    notes = extract_release_notes(changelog_path=_changelog(tmp_path), tag="v2.0.0")

    with pytest.raises(ReleaseError, match="not a 64-character lowercase SHA-256"):
        assert_expected_digest(notes, supplied)


def test_every_released_section_of_the_real_changelog_is_publishable() -> None:
    """A format drift in CHANGELOG.md must fail here, in the suite, rather than
    in the staging job of a release that has already been tagged."""

    import re as _re

    changelog = REPO_ROOT / "CHANGELOG.md"
    versions = _re.findall(r"^## (\d[^\s]*)", changelog.read_text(encoding="utf-8"), _re.MULTILINE)

    assert len(versions) > 5, "the fixture-free guard needs real sections to check"
    for version in versions:
        notes = extract_release_notes(changelog_path=changelog, tag=f"v{version}")
        assert notes.strip(), version
        # Section boundaries: a released section never swallows the next one.
        assert not notes.startswith("## "), version
        assert "\n## " not in notes, version


def test_verification_requires_the_section_so_the_rehearsal_catches_it() -> None:
    """Requiring it only in `stage` would mean discovering an undescribed
    release after the tag exists. The check lives in the shared verification
    workflow, unconditionally, so a rehearsal fails on it first."""

    artifact = _load_workflow("release-verify.yml")["jobs"]["artifact"]
    step = artifact["steps"][_step_index(artifact, "scripts/release_notes.py")]

    # Not rehearsal-only and not release-only: both modes run it.
    assert "if" not in step
    assert _step_index(artifact, "scripts/release_notes.py") < _step_index(
        artifact, "python -m build --wheel"
    )


def test_the_release_body_is_the_changelog_section_not_a_placeholder() -> None:
    release = _load_workflow("release.yml")
    stage = release["jobs"]["stage"]
    commands = _job_commands(stage)

    assert '--notes-file "${RUNNER_TEMP}/release-notes.md"' in commands
    assert "--notes " not in commands, "a placeholder body would say nothing about the release"
    # Extracted before anything is created, from the checkout pinned to the
    # verified commit rather than from the tag.
    assert _step_index(stage, "scripts/release_notes.py") < _step_index(stage, "gh release create")
    assert stage["steps"][0]["with"]["ref"] == "${{ needs.verify.outputs.source_sha }}"


def test_the_notes_are_written_to_a_path_no_candidate_controls() -> None:
    """Guarding a checkout-relative path in `stage` alone would let a candidate
    that commits its own `release-notes.md` pass the mandatory pre-tag
    rehearsal and fail only once the tag exists. Every job writes to the
    runner-owned temp directory instead, so the rehearsal exercises the
    identical write."""

    release = _load_workflow("release.yml")
    verify = _load_workflow("release-verify.yml")

    writers = [
        _job_commands(verify["jobs"]["artifact"]),
        _job_commands(release["jobs"]["stage"]),
        _job_commands(release["jobs"]["finalize"]),
    ]
    for commands in writers:
        assert '--output "${RUNNER_TEMP}/release-notes.md"' in commands
        assert "--output release-notes.md" not in commands


def test_all_three_extractions_are_bound_to_one_digest() -> None:
    """Verification, staging and finalisation each read the changelog. The
    digest published by verification is what makes them one value."""

    release = _load_workflow("release.yml")
    verify = _load_workflow("release-verify.yml")

    exported = verify["on"]["workflow_call"]["outputs"]
    assert "release_notes_sha256" in exported
    assert (
        verify["jobs"]["artifact"]["outputs"]["release_notes_sha256"]
        == "${{ steps.notes.outputs.release_notes_sha256 }}"
    )
    for job in ("stage", "finalize"):
        commands = _job_commands(release["jobs"][job])
        assert '--expected-sha256 "${NOTES_SHA256}"' in commands, job
        assert "needs.verify.outputs.release_notes_sha256" in json.dumps(release["jobs"][job]), job


def test_the_published_body_is_reapplied_in_the_call_that_undrafts() -> None:
    """The remote assets are content-bound before publication; the body was
    not. Between staging and finalisation — the environment approval window
    included — a release-write actor can edit the draft's text, and nothing
    downstream re-read it."""

    finalize = _load_workflow("release.yml")["jobs"]["finalize"]
    commands = _job_commands(finalize)

    # Re-derived from the changelog at the verified commit, fetched by
    # immutable SHA like the other stdlib-only scripts this job runs.
    assert "CHANGELOG.md" in commands
    assert "tools/release_notes.py" in commands
    assert _step_index(finalize, "tools/release_notes.py") < _step_index(finalize, "--draft=false")
    # One PATCH sets body and draft together: a compare-then-fix would leave a
    # window of its own, and reapplying afterwards would publish the tampered
    # text first.
    undraft = finalize["steps"][_step_index(finalize, "--draft=false")]["run"]
    assert "--draft=false" in undraft
    assert '--notes-file "${RUNNER_TEMP}/release-notes.md"' in undraft
    assert undraft.count("gh release edit") == 1


def test_a_repaired_draft_gets_the_notes_and_a_published_release_is_untouched() -> None:
    """Draft repair rewrites assets; leaving a superseded body beside them would
    publish notes no one re-derived. Finalisation never reads release text, so
    this is the last point at which it can be corrected."""

    commands = _job_commands(_load_workflow("release.yml")["jobs"]["stage"])
    draft_branch = commands.split("Repaired existing draft", 1)[0].split("Created draft", 1)[-1]
    published_branch = commands.split("Published already", 1)[-1]

    assert "gh release edit" in draft_branch
    assert '--notes-file "${RUNNER_TEMP}/release-notes.md"' in draft_branch
    assert "gh release edit" not in published_branch


# --------------------------------------------------------------------------
# #345 — the release installs the closure CI approved
# --------------------------------------------------------------------------


def _install_command(workflow: str, job: str) -> str:
    return _test_step_command(_load_workflow(workflow)["jobs"][job], "Install")


def test_ci_and_release_install_byte_for_byte_the_same_environment() -> None:
    """`pip install -e ".[dev]"` resolved fresh in both places, so the release
    could test a different set of packages than the run that approved the
    commit — and a release-only failure was not reproducible from the tree."""

    release_install = _install_command("release-verify.yml", "tests")

    assert release_install == _install_command("ci.yml", "test")
    assert "--require-hashes --requirement constraints/dev.txt" in release_install
    assert '".[dev]"' not in release_install


def test_the_project_install_cannot_smuggle_in_an_unlocked_resolve() -> None:
    """An editable install cannot be hashed, so it is installed with
    `--no-deps` against the locked closure; `pip check` is what proves the
    closure actually satisfies the project's declared dependencies."""

    install = _install_command("ci.yml", "test")

    assert "pip install -e . --no-deps" in install
    assert "python -m pip check" in install


def test_the_editable_install_resolves_no_build_backend() -> None:
    """`--no-deps` does not disable PEP 517 build isolation, and `PIP_CONSTRAINT`
    does not reach an isolated build environment on current pip — constraining
    hatchling to a version that does not exist still built successfully. The
    backend and its own dependencies were therefore resolved from the index on
    every run, which is the drift this issue exists to remove."""

    for workflow, job in (("ci.yml", "test"), ("release-verify.yml", "tests")):
        install = _install_command(workflow, job)
        assert "--no-build-isolation" in install, workflow
        assert "--require-hashes --requirement constraints/build-backend.txt" in install, workflow
        # The inert constraint must not survive as reassurance.
        assert "PIP_CONSTRAINT" not in install, workflow
        assert install.index("constraints/build-backend.txt") < install.index("pip install -e .")


def test_the_backend_closure_is_locked_against_the_hand_maintained_pin() -> None:
    """One backend version, in one reviewed place. `release-build.txt` states
    the reproducibility argument and constrains the wheel build;
    `build-backend.txt` is its resolved, hashed closure."""

    backend = next(
        target for target in LOCK_TARGETS if target.lock == "constraints/build-backend.txt"
    )
    assert backend.source == "constraints/release-build.txt"

    def _hatchling(path: str) -> str:
        for line in (REPO_ROOT / path).read_text(encoding="utf-8").splitlines():
            if line.startswith("hatchling=="):
                return line.split()[0].split(";")[0].strip()
        raise AssertionError(f"no hatchling pin in {path}")

    pinned = {
        _hatchling(path)
        for path in (
            "constraints/release-build.txt",
            "constraints/build-backend.txt",
            "constraints/release-seal.txt",
        )
    }
    assert len(pinned) == 1, f"backend versions disagree: {sorted(pinned)}"


def test_locks_installed_together_cannot_move_each_other() -> None:
    """Two `pip install` invocations into one environment: a distribution
    pinned at different versions by each would leave the second one silently
    replacing part of the first one's closure."""

    assert co_installed_problems() == []
    # The guard is real, not vacuous: the two locks do share distributions.
    dev = set(parse_lock(REPO_ROOT / "constraints/dev.txt"))
    backend = set(parse_lock(REPO_ROOT / "constraints/build-backend.txt"))
    assert dev & backend


def test_the_lock_gate_runs_in_both_pipelines_before_the_suite() -> None:
    for workflow, job in (("ci.yml", "test"), ("release-verify.yml", "tests")):
        parsed = _load_workflow(workflow)["jobs"][job]
        assert _step_index(parsed, "scripts/verify_dependency_lock.py") < _step_index(
            parsed, "--cov-fail-under=85"
        ), workflow


def test_the_committed_locks_still_describe_the_declared_requirements() -> None:
    """The gate, run against this repository: a lock that stopped matching
    pyproject.toml fails here rather than at install time in a release."""

    verify_all()


def test_the_dev_lock_pins_and_hashes_the_whole_closure() -> None:
    lockfile = (REPO_ROOT / "constraints/dev.txt").read_text(encoding="utf-8")

    for pinned in ("pytest==", "ruff==", "pydantic==", "twine=="):
        assert pinned in lockfile
    assert lockfile.count("--hash=sha256:") > 100
    # No ranged requirement outside the header prose.
    assert not [line for line in _requirement_lines(lockfile) if ">=" in line]


def _lock_pair(
    tmp_path: Path, *, declared: str, pins: str, compiled_from: str | None = None
) -> tuple[Path, LockTarget]:
    """A synthetic source/lock pair.

    ``compiled_from`` defaults to ``declared``, i.e. a lock whose recorded
    declaration block is honest; pass a different string to model a lock
    compiled before the declarations changed.
    """

    from packaging.requirements import Requirement

    (tmp_path / "constraints").mkdir(parents=True, exist_ok=True)
    (tmp_path / "constraints/toolchain.in").write_text(declared, encoding="utf-8")
    block = render_declarations(
        [Requirement(line) for line in (compiled_from or declared).splitlines() if line.strip()]
    )
    (tmp_path / "constraints/toolchain.txt").write_text(block + pins, encoding="utf-8")
    target = LockTarget(lock="constraints/toolchain.txt", source="constraints/toolchain.in")
    return tmp_path, target


HONEST_PINS = """\
# Toolchain lock.
ruff==0.16.2 \\
    --hash=sha256:aaaa
    # via -r constraints/toolchain.in
pytest==9.1.1 \\
    --hash=sha256:bbbb
    # via
    #   -r constraints/toolchain.in
    #   pytest-cov
"""


@pytest.mark.parametrize(
    ("declared", "pins", "expected"),
    [
        ("ruff>=0.16.1,<1\npytest>=9,<10\n", HONEST_PINS, None),
        # Stale: a requirement was added and nobody recompiled.
        (
            "ruff>=0.16.1,<1\npytest>=9,<10\nhypothesis>=6,<7\n",
            HONEST_PINS,
            "pins no hypothesis",
        ),
        # Inconsistent: the declared range moved past the pin.
        ("ruff>=0.17\npytest>=9,<10\n", HONEST_PINS, "does not satisfy"),
        # Removed: the lock still installs a direct requirement nobody declares.
        ("ruff>=0.16.1,<1\n", HONEST_PINS, "no longer declares it"),
        # Unhashed: `pip install --require-hashes` would refuse the file.
        (
            "ruff>=0.16.1,<1\n",
            "ruff==0.16.2\n    # via -r constraints/toolchain.in\n",
            "without a hash",
        ),
    ],
)
def test_the_gate_names_each_way_a_lock_goes_wrong(
    tmp_path: Path, declared: str, pins: str, expected: str | None
) -> None:
    root, target = _lock_pair(tmp_path, declared=declared, pins=pins)

    problems = verify_lock_target(target, root=root)

    if expected is None:
        assert problems == []
    else:
        assert any(expected in problem for problem in problems), problems


def test_a_transitive_pin_is_not_mistaken_for_an_undeclared_requirement(tmp_path: Path) -> None:
    """`uv` records who asked for each pin. Only a pin the source file itself
    requested is evidence of a stale declaration; everything else is closure."""

    root, target = _lock_pair(
        tmp_path,
        declared="ruff>=0.16.1,<1\n",
        pins=(
            "ruff==0.16.2 \\\n    --hash=sha256:aaaa\n    # via -r constraints/toolchain.in\n"
            "pluggy==1.6.0 \\\n    --hash=sha256:bbbb\n    # via ruff\n"
        ),
    )

    assert verify_lock_target(target, root=root) == []


def test_a_lock_that_resolves_at_install_time_is_not_a_lock(tmp_path: Path) -> None:
    root, target = _lock_pair(
        tmp_path, declared="ruff>=0.16.1,<1\n", pins="ruff>=0.16.1\n    --hash=sha256:aaaa\n"
    )

    with pytest.raises(ReleaseError, match="neither an exact pin nor a direct URL"):
        verify_lock_target(target, root=root)


# --------------------------------------------------------------------------
# #345 / review — full PEP 508 declarations, and marker-qualified pins
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("was", "now"),
    [
        # An extra changes what gets installed while the name and range hold.
        ("demo>=1", "demo[feature]>=1"),
        # So does moving the declaration behind a marker.
        ("demo>=1", "demo>=1 ; sys_platform == 'linux'"),
        # And so does switching to a direct URL.
        ("demo>=1", "demo @ https://example.invalid/demo-1.0-py3-none-any.whl"),
        # The ordinary case, for completeness.
        ("demo>=1", "demo>=2"),
    ],
)
def test_a_declaration_change_of_any_kind_invalidates_the_lock(
    tmp_path: Path, was: str, now: str
) -> None:
    """A name-and-range comparison accepts the first three of these silently:
    every name matches and every range still contains the pin."""

    root, target = _lock_pair(
        tmp_path,
        declared=f"{now}\n",
        compiled_from=f"{was}\n",
        pins="demo==1.0 \\\n    --hash=sha256:aaaa\n    # via -r constraints/toolchain.in\n",
    )

    problems = verify_lock_target(target, root=root)

    assert any("not compiled from the current declarations" in problem for problem in problems), (
        problems
    )


def test_the_recorded_declarations_are_canonical_not_as_typed(tmp_path: Path) -> None:
    """`ruamel.yaml` and `ruamel-yaml` are one distribution, and `>=1,<2` and
    `<2,>=1` are one range; neither spelling may look like a change."""

    from packaging.requirements import Requirement

    assert normalize_requirement(Requirement("ruamel.yaml>=0.19.1,<1")) == normalize_requirement(
        Requirement("ruamel-yaml<1,>=0.19.1")
    )
    assert normalize_requirement(Requirement("demo[b,a]>=1")) == "demo[a,b]>=1"

    root, target = _lock_pair(
        tmp_path,
        declared="ruamel.yaml>=0.19.1,<1\n",
        compiled_from="ruamel-yaml<1,>=0.19.1\n",
        pins="ruamel-yaml==0.19.1 \\\n    --hash=sha256:aaaa\n    # via -r constraints/toolchain.in\n",
    )
    assert verify_lock_target(target, root=root) == []


def test_a_lock_without_a_declaration_block_is_not_trusted(tmp_path: Path) -> None:
    (tmp_path / "constraints").mkdir(parents=True)
    (tmp_path / "constraints/toolchain.in").write_text("demo>=1\n", encoding="utf-8")
    (tmp_path / "constraints/toolchain.txt").write_text(
        "demo==1.0 \\\n    --hash=sha256:aaaa\n    # via -r constraints/toolchain.in\n",
        encoding="utf-8",
    )
    target = LockTarget(lock="constraints/toolchain.txt", source="constraints/toolchain.in")

    problems = verify_lock_target(target, root=tmp_path)

    assert any("records no declaration block" in problem for problem in problems), problems


def test_one_name_may_be_pinned_under_disjoint_markers(tmp_path: Path) -> None:
    """`uv pip compile --universal` legitimately forks a dependency across
    Python or platform versions. Rejecting the second pin would make the
    verifier refuse a lock update_locks.py itself generated."""

    root, target = _lock_pair(
        tmp_path,
        declared="demo>=1,<3\n",
        pins=(
            "demo==1.0 ; python_full_version < '3.13' \\\n"
            "    --hash=sha256:aaaa\n    # via -r constraints/toolchain.in\n"
            "demo==2.0 ; python_full_version >= '3.13' \\\n"
            "    --hash=sha256:bbbb\n    # via -r constraints/toolchain.in\n"
        ),
    )

    assert verify_lock_target(target, root=root) == []


def test_every_marker_branch_must_satisfy_the_declaration(tmp_path: Path) -> None:
    """One branch out of range means some environment installs something the
    declarations do not allow, so checking only the first would fail open."""

    root, target = _lock_pair(
        tmp_path,
        declared="demo>=2\n",
        pins=(
            "demo==1.0 ; python_full_version < '3.13' \\\n"
            "    --hash=sha256:aaaa\n    # via -r constraints/toolchain.in\n"
            "demo==2.0 ; python_full_version >= '3.13' \\\n"
            "    --hash=sha256:bbbb\n    # via -r constraints/toolchain.in\n"
        ),
    )

    problems = verify_lock_target(target, root=root)

    assert any("does not satisfy" in problem for problem in problems), problems
    assert any("1.0" in problem for problem in problems), problems


def test_the_same_name_under_the_same_marker_is_still_a_duplicate(tmp_path: Path) -> None:
    root, target = _lock_pair(
        tmp_path,
        declared="demo>=1\n",
        pins=(
            "demo==1.0 \\\n    --hash=sha256:aaaa\n    # via -r constraints/toolchain.in\n"
            "demo==2.0 \\\n    --hash=sha256:bbbb\n    # via -r constraints/toolchain.in\n"
        ),
    )

    with pytest.raises(ReleaseError, match="under the same marker"):
        verify_lock_target(target, root=root)


def test_co_installed_locks_that_disagree_are_reported(tmp_path: Path) -> None:
    for name, version in (("first", "1.0"), ("second", "2.0")):
        (tmp_path / "constraints").mkdir(parents=True, exist_ok=True)
        (tmp_path / f"constraints/{name}.txt").write_text(
            f"shared=={version} \\\n    --hash=sha256:aaaa\n    # via -r constraints/{name}.in\n",
            encoding="utf-8",
        )
    targets = (
        LockTarget(lock="constraints/first.txt", source="constraints/first.in"),
        LockTarget(lock="constraints/second.txt", source="constraints/second.in"),
    )

    problems = co_installed_problems(
        root=tmp_path,
        targets=targets,
        groups=(("constraints/first.txt", "constraints/second.txt"),),
    )

    assert any("different versions by locks installed together" in item for item in problems), (
        problems
    )


def test_every_compiled_lock_is_gated_and_regenerated_by_one_command() -> None:
    """A lock the generator writes but the gate ignores — or the reverse — is
    how a file quietly stops matching its declarations."""

    compiled = {
        f"constraints/{path.name}"
        for path in (REPO_ROOT / "constraints").glob("*.txt")
        # Not a lock but a source: one hand-chosen version supporting a
        # reproducibility argument, which build-backend.txt resolves.
        if path.name != "release-build.txt"
    }

    assert compiled == {target.lock for target in LOCK_TARGETS}
    for target in LOCK_TARGETS:
        header = prose_header(REPO_ROOT / target.lock)
        assert "scripts/update_locks.py" in header, target.lock
        # The generated block is written below the prose, so regenerating
        # replaces it instead of stacking a second copy on every run.
        assert DECLARATION_SENTINEL not in header, target.lock
        command = compile_command(target)
        assert "--generate-hashes" in command
        assert "--universal" in command
        # The header is preserved across regeneration, so uv's own banner must
        # not be re-emitted on top of it.
        assert "--no-header" in command
    dev = next(target for target in LOCK_TARGETS if target.lock == "constraints/dev.txt")
    assert compile_command(dev)[-3:] == ["--extra", "dev", "pyproject.toml"]
