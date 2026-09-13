"""The advisory release path: two verifications, one publisher, chosen by reviewed code (#648).

`docs/release-evidence-policy-decision.md` § Amendment 5 admits a `v*` release
that makes no qualified blocking claim, on conditions this file holds. Each
condition is a place where the advisory line could borrow the qualified line's
authority, or the qualified line could silently lose its evidence, so each gets
a check against the workflow graph or a refusal with a negative control.

The qualified path's own invariants stay in `tests/test_release_pipeline.py`.
"""

from __future__ import annotations

import json
import re
import subprocess
import zipfile
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts import release_channel as rc
from scripts._release_support import ReleaseError
from scripts.release_cadence import tag_channel
from scripts.release_publication import (
    ADVISORY,
    CHANNEL_ASSETS,
    CHANNEL_SIGNED_ASSETS,
    QUALIFIED,
    build_manifest,
    signature_bundles,
    verify_manifest,
)
from scripts.verify_wheel_provenance import verify_wheel_provenance

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github/workflows"
VERSION = "9.9.9"
TAG = f"v{VERSION}"
SOURCE = "a" * 40
WHEEL_FILENAME = f"agents_shipgate-{VERSION}-py3-none-any.whl"
POLICY_STEP = "Exhaustive safety qualification policy re-derivation"
SHARED_ARTIFACT_STEPS = (
    "Checkout",
    "Resolve candidate version and tag",
    "Set up Python",
    "Require a CHANGELOG section for this tag",
    "Install the hash-locked sealing toolchain",
    "Confirm the sealer is verifying the tested commit",
    "Build a wheel from the checked-out source",
    "Upload candidate bundle",
)
DERIVED_OUTPUTS = {"channel", "rehearsal_workflow", "release_assets", "signed_assets"}


def _load(name: str) -> dict[str, Any]:
    document = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    if True in document:
        document["on"] = document.pop(True)
    return document


def _release() -> dict[str, Any]:
    return _load("release.yml")


def _advisory() -> dict[str, Any]:
    return _load("release-advisory-verify.yml")


def _steps(job: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {step["name"]: step for step in job["steps"]}


def _index(job: dict[str, Any], name: str) -> int:
    return [step["name"] for step in job["steps"]].index(name)


def _commands(job: dict[str, Any]) -> str:
    return "\n".join(_code(step["run"]) for step in job.get("steps") or [] if "run" in step)


def _code(script: str) -> str:
    """A shell script without its comment lines. The workflows explain their
    flags in comments beside them, so a substring check over the raw text can
    pass on the explanation after the flag itself is gone."""

    return "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))


def _wheel(path: Path, *, version: str = VERSION, source: str = SOURCE, extra: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            f"agents_shipgate-{version}.dist-info/METADATA",
            f"Metadata-Version: 2.4\nName: agents-shipgate\nVersion: {version}\n",
        )
        archive.writestr("agents_shipgate/__init__.py", f'__version__ = "{version}"\n{extra}')
        archive.writestr(
            "agents_shipgate/_meta/release-source.json",
            json.dumps(
                {
                    "schema_version": "shipgate.release_source/v1",
                    "source_commit": source,
                    "package_version": version,
                }
            ),
        )
    return path


def _declaration(tmp_path: Path, channels: dict[str, str]) -> Path:
    path = tmp_path / "release-channels.json"
    path.write_text(json.dumps({"schema": rc.SCHEMA, "channels": channels}), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# The switch: exactly one verification, chosen by the declaration
# --------------------------------------------------------------------------


def test_release_runs_exactly_one_verification_chosen_by_the_declared_channel() -> None:
    jobs = _release()["jobs"]

    assert list(jobs) == [
        "channel",
        "verify",
        "verify_advisory",
        "candidate",
        "stage",
        "publish",
        "finalize",
    ]
    gates = {
        jobs["verify"]["if"]: jobs["verify"]["uses"],
        jobs["verify_advisory"]["if"]: jobs["verify_advisory"]["uses"],
    }
    assert gates == {
        "needs.channel.outputs.channel == 'qualified'": "./.github/workflows/release-verify.yml",
        "needs.channel.outputs.channel == 'advisory'": "./.github/workflows/release-advisory-verify.yml",
    }
    # Exhaustive over the channels reviewed code can declare, so a third
    # channel cannot be added without a verification of its own.
    assert {condition.rsplit("'", 2)[1] for condition in gates} == rc.CHANNELS
    for name in ("verify", "verify_advisory"):
        assert jobs[name]["needs"] == "channel"
        assert jobs[name]["with"] == {
            "ref": "${{ github.sha }}",
            "release_tag": "${{ github.ref_name }}",
            "mode": "release",
        }


def test_the_channel_is_read_from_the_tagged_tree_before_anything_is_built() -> None:
    channel = _release()["jobs"]["channel"]

    assert "needs" not in channel
    assert channel["permissions"] == {"contents": "read"}
    checkout = channel["steps"][0]
    assert checkout["with"]["ref"] == "${{ github.sha }}"
    assert ".github/release-channels.json" in checkout["with"]["sparse-checkout"]
    resolve = _steps(channel)["Read the channel reviewed code declares for this version"]
    assert resolve["run"] == 'python scripts/release_channel.py resolve --tag "${RELEASE_TAG}"'
    assert resolve["env"] == {"RELEASE_TAG": "${{ github.ref_name }}"}
    assert channel["outputs"] == {"channel": "${{ steps.channel.outputs.channel }}"}


def test_the_candidate_job_hands_both_outcomes_to_the_fail_closed_selector() -> None:
    candidate = _release()["jobs"]["candidate"]

    assert candidate["needs"] == ["channel", "verify", "verify_advisory"]
    assert candidate["if"] == "${{ !cancelled() && needs.channel.result == 'success' }}"
    assert candidate["permissions"] == {"contents": "read"}
    select = _steps(candidate)["Select the declared channel's verified candidate"]
    assert select["env"] == {
        "CHANNEL": "${{ needs.channel.outputs.channel }}",
        "RELEASE_TAG": "${{ github.ref_name }}",
        "QUALIFIED_RESULT": "${{ needs.verify.result }}",
        "ADVISORY_RESULT": "${{ needs.verify_advisory.result }}",
        "QUALIFIED_OUTPUTS": "${{ toJSON(needs.verify.outputs) }}",
        "ADVISORY_OUTPUTS": "${{ toJSON(needs.verify_advisory.outputs) }}",
    }
    assert "python scripts/release_channel.py candidate" in select["run"]
    for flag in ("--qualified-result", "--advisory-result", "--qualified-outputs", "--advisory-outputs", "--tag"):
        assert flag in select["run"], flag
    # Every value publication reads comes out of this job, and out of the
    # selector's step, under its own name.
    expected = set(rc.CANDIDATE_OUTPUTS) | DERIVED_OUTPUTS
    assert set(candidate["outputs"]) == expected
    for key, value in candidate["outputs"].items():
        assert value == f"${{{{ steps.select.outputs.{key} }}}}", key


def _names_each_dependency_success(job: dict[str, Any]) -> bool:
    condition = str(job.get("if", ""))
    needs = job["needs"] if isinstance(job["needs"], list) else [job["needs"]]
    return (
        condition.startswith("${{ !cancelled() && ")
        and "||" not in condition
        and all(f"needs.{name}.result == 'success'" in condition for name in needs)
    )


def test_every_job_after_the_switch_names_each_dependency_success() -> None:
    """A skipped verification job would skip everything downstream on a default
    condition, so the jobs after `candidate` override it. The override must not
    become permission to run after a failure: each names the success of every
    job it needs."""

    jobs = _release()["jobs"]

    for name in ("stage", "publish", "finalize"):
        assert _names_each_dependency_success(jobs[name]), name
    for name in ("publish", "finalize"):
        assert "needs.stage.outputs.release_state != 'published'" in jobs[name]["if"], name

    # Negative control: the checker notices one dependency going unnamed.
    weakened = dict(jobs["finalize"])
    weakened["if"] = weakened["if"].replace("needs.publish.result == 'success' && ", "")
    assert not _names_each_dependency_success(weakened)


def test_publication_reads_only_the_selected_candidate() -> None:
    release = _release()
    candidate_outputs = set(release["jobs"]["candidate"]["outputs"])

    for name in ("stage", "publish", "finalize"):
        text = yaml.safe_dump(release["jobs"][name])
        referenced = set(re.findall(r"needs\.(\w+)\.", text))
        assert referenced <= {"candidate", "stage", "publish"}, (name, referenced)
        consumed = set(re.findall(r"needs\.candidate\.outputs\.(\w+)", text))
        assert consumed, name
        assert consumed <= candidate_outputs, (name, consumed - candidate_outputs)


@pytest.mark.parametrize("workflow", ["release-verify.yml", "release-advisory-verify.yml"])
def test_both_verifications_export_every_value_publication_reads(workflow: str) -> None:
    exported = set(_load(workflow)["on"]["workflow_call"]["outputs"])

    assert set(rc.CANDIDATE_OUTPUTS) <= exported, set(rc.CANDIDATE_OUTPUTS) - exported


# --------------------------------------------------------------------------
# The advisory verification: no qualification step, the shared steps unchanged
# --------------------------------------------------------------------------


def test_the_advisory_verification_holds_no_publication_authority() -> None:
    advisory = _advisory()
    release = _release()

    assert advisory["permissions"] == {"contents": "read", "actions": "read"}
    assert advisory["jobs"]["tests"]["permissions"] == {"contents": "read"}
    assert advisory["jobs"]["artifact"]["permissions"] == {"contents": "read", "actions": "read"}
    assert release["jobs"]["verify_advisory"]["permissions"] == {"contents": "read", "actions": "read"}
    for job in (*advisory["jobs"].values(), release["jobs"]["channel"], release["jobs"]["candidate"]):
        assert "id-token" not in job.get("permissions", {})
        assert "write" not in job.get("permissions", {}).values()
        assert "environment" not in job
    text = (WORKFLOWS / "release-advisory-verify.yml").read_text(encoding="utf-8")
    for verb in ("uv publish", "gh release create", "gh release upload", "sigstore sign"):
        assert verb not in text, verb


def test_the_advisory_verification_contains_no_qualification_step_to_skip() -> None:
    """Amendment 2's C3, applied again: the advisory line calls a workflow with
    no qualification step in it, rather than a qualified workflow with steps
    turned off."""

    text = (WORKFLOWS / "release-advisory-verify.yml").read_text(encoding="utf-8")

    for qualified_only in (
        "SAFETY_QUALIFICATION",
        "safety-qualification",
        "verify_qualification_binding",
        "verify_safety_qualification_release",
        "release-trust-roots",
        "--qualified",
    ):
        assert qualified_only not in text, qualified_only
    assert "inputs.mode ==" not in json.dumps(
        [step for job in _advisory()["jobs"].values() for step in job["steps"]
         if step["name"] != "Prove the provenance gate fails closed"]
    )


def test_the_steps_both_lines_share_are_identical() -> None:
    """Held equal rather than trusted to stay in step: a fix to the suite, the
    lock check, the build or the handoff upload lands once, in
    `release-verify.yml`, and this fails until the advisory copy matches."""

    qualified = _load("release-verify.yml")["jobs"]
    advisory = _advisory()["jobs"]

    assert [s for s in qualified["tests"]["steps"] if s["name"] != POLICY_STEP] == advisory["tests"]["steps"]
    for key in ("runs-on", "timeout-minutes", "permissions"):
        assert qualified["tests"][key] == advisory["tests"][key], key
    qualified_steps, advisory_steps = _steps(qualified["artifact"]), _steps(advisory["artifact"])
    for name in SHARED_ARTIFACT_STEPS:
        assert advisory_steps[name] == qualified_steps[name], name


def test_the_advisory_verification_refuses_a_version_declared_qualified() -> None:
    artifact = _advisory()["jobs"]["artifact"]
    refuse = _steps(artifact)["Refuse a version that is not declared advisory"]

    assert 'python scripts/release_channel.py resolve --tag "${RELEASE_TAG}"' in refuse["run"]
    assert '[ "${channel}" != "advisory" ]' in refuse["run"]
    assert "exit 1" in refuse["run"]
    assert _index(artifact, "Refuse a version that is not declared advisory") < _index(
        artifact, "Build a wheel from the checked-out source"
    )


def test_the_advisory_release_publishes_the_exercised_wheel_bound_to_its_source() -> None:
    """#570: the published bytes are the ones a pre-publication smoke ran
    through both the CLI and the Action, and they are what the tag builds."""

    artifact = _advisory()["jobs"]["artifact"]
    steps = _steps(artifact)
    order = [
        "Build a wheel from the checked-out source",
        "Download the candidate the Release Engine Smoke exercised",
        "Require the smoke evidence to cover this exact candidate",
        "Bind the exercised wheel to the tagged source tree",
        "Generate wheel-scoped SBOM",
        "Write the advisory statement",
        "Assemble content-addressed candidate handoff",
        "Upload candidate bundle",
    ]
    assert [_index(artifact, name) for name in order] == sorted(_index(artifact, name) for name in order)

    download = steps["Download the candidate the Release Engine Smoke exercised"]["run"]
    assert "actions/workflows/release-engine-smoke.yml/runs?head_sha=${SOURCE_SHA}&status=success" in download
    assert "No successful Release Engine Smoke run" in download
    assert '[ "${#wheels[@]}" -ne 1 ]' in download
    # The artifact names are the smoke workflow's own, read from it.
    smoke = _load("release-engine-smoke.yml")["jobs"]
    uploaded = {
        step["with"]["name"]
        for job in smoke.values()
        for step in job["steps"]
        if "actions/upload-artifact" in str(step.get("uses"))
    }
    assert uploaded == {"unqualified-engine-wheel", "unqualified-engine-smoke"}
    for name in uploaded:
        assert f"--name {name}" in download, name
    compare = _commands(smoke["downstream"])
    assert "release_engine_smoke.py compare > .shipgate-smoke/distribution-evidence.json" in compare

    evidence = steps["Require the smoke evidence to cover this exact candidate"]["run"]
    assert "release_channel.py exercised" in evidence
    assert "--evidence exercised-evidence/distribution-evidence.json" in evidence
    for name in ("Bind the exercised wheel to the tagged source tree", "Assemble content-addressed candidate handoff"):
        assert "--exercised \"${EXERCISED_WHEEL}\"" in steps[name]["run"], name
        assert '--source-commit "${SOURCE_SHA}"' in steps[name]["run"], name
    handoff = steps["Assemble content-addressed candidate handoff"]["run"]
    assert "--channel advisory" in handoff
    listed = set(re.findall(r"--asset candidate/([\w.-]+)", handoff))
    assert listed == CHANNEL_ASSETS[ADVISORY]


def test_every_advisory_rehearsal_proves_its_provenance_gate_fails_closed() -> None:
    drill = _steps(_advisory()["jobs"]["artifact"])["Prove the provenance gate fails closed"]

    assert drill["if"] == "inputs.mode == 'rehearsal'"
    assert "if python scripts/verify_wheel_provenance.py" in drill["run"]
    assert "--exercised fault-injected.whl" in drill["run"]
    assert "does not fail closed" in drill["run"]


def test_the_advisory_rehearsal_cannot_publish_and_calls_the_advisory_verification() -> None:
    rehearsal = _load("release-advisory-rehearsal.yml")

    assert set(rehearsal["on"]) == {"workflow_dispatch"}
    assert "ref" not in rehearsal["on"]["workflow_dispatch"]["inputs"]
    assert rehearsal["permissions"] == {"contents": "read", "actions": "read"}
    assert list(rehearsal["jobs"]) == ["rehearse"]
    job = rehearsal["jobs"]["rehearse"]
    assert job["uses"] == "./.github/workflows/release-advisory-verify.yml"
    assert job["with"] == {
        "ref": "${{ github.sha }}",
        "release_tag": "${{ inputs.release_tag }}",
        "mode": "rehearsal",
    }
    assert "id-token" not in job["permissions"]


# --------------------------------------------------------------------------
# One publisher, keyed by the declared channel
# --------------------------------------------------------------------------


def test_staging_requires_the_declared_channels_own_rehearsal_and_asset_set() -> None:
    stage = _release()["jobs"]["stage"]
    steps = _steps(stage)

    rehearsal = steps["Require a successful rehearsal of this exact candidate"]
    assert rehearsal["env"]["REHEARSAL_WORKFLOW"] == "${{ needs.candidate.outputs.rehearsal_workflow }}"
    assert "actions/workflows/${REHEARSAL_WORKFLOW:?" in _code(rehearsal["run"])
    assert "head_sha=${SOURCE_SHA}&status=success" in rehearsal["run"]
    assert rc.REHEARSAL_WORKFLOWS == {
        QUALIFIED: "release-rehearsal.yml",
        ADVISORY: "release-advisory-rehearsal.yml",
    }
    for workflow in rc.REHEARSAL_WORKFLOWS.values():
        assert (WORKFLOWS / workflow).is_file(), workflow

    handoff = steps["Verify the candidate handoff"]
    assert '--expected-channel "${CHANNEL}"' in _code(handoff["run"])
    assert handoff["env"]["CHANNEL"] == "${{ needs.candidate.outputs.channel }}"

    draft = steps["Create or repair the draft release"]
    assert draft["env"]["RELEASE_ASSETS"] == "${{ needs.candidate.outputs.release_assets }}"
    assert draft["env"]["SIGNED_ASSETS"] == "${{ needs.candidate.outputs.signed_assets }}"
    published = _code(draft["run"]).split("Published already", 1)[-1]
    assert '--expected-channel "${CHANNEL}"' in published
    assert "--require-signatures" in published
    assert '"${signed_assets[@]}"' in published
    # No channel's file names are spelled in the publisher any more; they come
    # from `release_publication` through the candidate.
    for asset in ("safety-qualification.json", "advisory-statement.json"):
        assert asset not in _commands(stage), asset


def test_signing_and_finalisation_follow_the_declared_channel() -> None:
    jobs = _release()["jobs"]

    sign = _steps(jobs["publish"])["Sign release artifacts"]
    assert sign["env"]["SIGNED_ASSETS"] == "${{ needs.candidate.outputs.signed_assets }}"
    assert 'sigstore sign --output-directory dist --overwrite "${targets[@]}"' in _code(sign["run"])

    finalize = _steps(jobs["finalize"])
    closed = _code(finalize["Validate the exact remote asset set, byte for byte"]["run"])
    assert '--expected-channel "${CHANNEL}"' in closed
    assert "--require-signatures" in closed
    verify = _code(finalize["Verify the attached signatures"]["run"])
    assert 'for target in "${WHEEL_FILENAME}" "${signed_assets[@]}"; do' in verify
    assert "sigstore verify identity" in verify


# --------------------------------------------------------------------------
# One table of channels
# --------------------------------------------------------------------------


def test_every_channel_table_names_the_same_channels() -> None:
    assert set(CHANNEL_ASSETS) == set(CHANNEL_SIGNED_ASSETS) == set(rc.REHEARSAL_WORKFLOWS) == rc.CHANNELS
    assert (QUALIFIED, ADVISORY) == (rc.QUALIFIED, rc.ADVISORY)


def test_each_channel_signs_only_what_it_ships_and_only_the_qualified_line_is_qualified() -> None:
    for channel, signed in CHANNEL_SIGNED_ASSETS.items():
        assert set(signed) <= CHANNEL_ASSETS[channel], channel
    assert rc.STATEMENT_FILENAME in CHANNEL_ASSETS[ADVISORY]
    assert rc.STATEMENT_FILENAME in CHANNEL_SIGNED_ASSETS[ADVISORY]
    assert not any("qualification" in name for name in CHANNEL_ASSETS[ADVISORY])
    assert {"safety-qualification.json", "safety-qualification.sigstore.json"} <= CHANNEL_ASSETS[QUALIFIED]
    assert rc.STATEMENT_FILENAME not in CHANNEL_ASSETS[QUALIFIED]


# --------------------------------------------------------------------------
# The closed asset set per channel
# --------------------------------------------------------------------------


def _candidate_dir(tmp_path: Path, names: set[str]) -> tuple[Path, list[Path]]:
    wheel = _wheel(tmp_path / WHEEL_FILENAME)
    assets = []
    for name in sorted(names):
        path = tmp_path / name
        path.write_text("{}\n", encoding="utf-8")
        assets.append(path)
    return wheel, assets


@pytest.mark.parametrize("channel", sorted(CHANNEL_ASSETS))
def test_a_manifest_records_its_channel_and_verifies_only_as_that_channel(tmp_path: Path, channel: str) -> None:
    wheel, assets = _candidate_dir(tmp_path, set(CHANNEL_ASSETS[channel]))
    manifest = tmp_path / "candidate-manifest.json"
    build_manifest(
        tag=TAG, source_commit=SOURCE, wheel_path=wheel, asset_paths=assets,
        output_path=manifest, channel=channel,
    )
    digest = __import__("hashlib").sha256(manifest.read_bytes()).hexdigest()

    assert json.loads(manifest.read_text(encoding="utf-8"))["channel"] == channel
    assert verify_manifest(manifest_path=manifest, expected_sha256=digest, expected_channel=channel)
    other = ADVISORY if channel == QUALIFIED else QUALIFIED
    with pytest.raises(ReleaseError, match=f"records the {channel} channel"):
        verify_manifest(manifest_path=manifest, expected_sha256=digest, expected_channel=other)


@pytest.mark.parametrize(
    ("channel", "names", "message"),
    [
        (ADVISORY, CHANNEL_ASSETS[ADVISORY] | {"safety-qualification.json"}, "not permitted safety-qualification.json"),
        (ADVISORY, CHANNEL_ASSETS[ADVISORY] - {"advisory-statement.json"}, "missing advisory-statement.json"),
        (QUALIFIED, CHANNEL_ASSETS[QUALIFIED] - {"safety-qualification.sigstore.json"}, "missing safety-qualification.sigstore.json"),
        (QUALIFIED, CHANNEL_ASSETS[QUALIFIED] | {"advisory-statement.json"}, "not permitted advisory-statement.json"),
        ("preview", set(), "Unknown release channel"),
    ],
)
def test_a_channel_manifest_refuses_any_other_asset_set(tmp_path: Path, channel: str, names: set[str], message: str) -> None:
    wheel, assets = _candidate_dir(tmp_path, set(names))

    with pytest.raises(ReleaseError, match=message):
        build_manifest(
            tag=TAG, source_commit=SOURCE, wheel_path=wheel, asset_paths=assets,
            output_path=tmp_path / "candidate-manifest.json", channel=channel,
        )


def test_a_manifest_from_before_channels_verifies_only_as_qualified(tmp_path: Path) -> None:
    """`release-verify.yml` is unchanged and records no channel. Its manifest is
    the qualified line's by construction; it must never pass as advisory."""

    wheel, assets = _candidate_dir(tmp_path, set(CHANNEL_ASSETS[QUALIFIED]))
    manifest = tmp_path / "candidate-manifest.json"
    build_manifest(tag=TAG, source_commit=SOURCE, wheel_path=wheel, asset_paths=assets, output_path=manifest)
    digest = __import__("hashlib").sha256(manifest.read_bytes()).hexdigest()

    assert "channel" not in json.loads(manifest.read_text(encoding="utf-8"))
    assert verify_manifest(manifest_path=manifest, expected_sha256=digest, expected_channel=QUALIFIED)
    with pytest.raises(ReleaseError, match="records the qualified channel"):
        verify_manifest(manifest_path=manifest, expected_sha256=digest, expected_channel=ADVISORY)


@pytest.mark.parametrize("channel", sorted(CHANNEL_ASSETS))
def test_a_finished_release_must_carry_every_bundle_its_channel_signs(tmp_path: Path, channel: str) -> None:
    wheel, assets = _candidate_dir(tmp_path, set(CHANNEL_ASSETS[channel]))
    manifest = tmp_path / "candidate-manifest.json"
    build_manifest(
        tag=TAG, source_commit=SOURCE, wheel_path=wheel, asset_paths=assets,
        output_path=manifest, channel=channel,
    )
    digest = __import__("hashlib").sha256(manifest.read_bytes()).hexdigest()
    bundles = signature_bundles(channel, WHEEL_FILENAME)
    assert bundles == {f"{name}.sigstore.json" for name in (WHEEL_FILENAME, *CHANNEL_SIGNED_ASSETS[channel])}

    for bundle in sorted(bundles):
        for present in bundles - {bundle}:
            (tmp_path / present).write_text("bundle", encoding="utf-8")
        (tmp_path / bundle).unlink(missing_ok=True)
        with pytest.raises(ReleaseError, match=re.escape(bundle)):
            verify_manifest(
                manifest_path=manifest, expected_sha256=digest,
                expected_channel=channel, require_signatures=True,
            )
    for bundle in bundles:
        (tmp_path / bundle).write_text("bundle", encoding="utf-8")
    assert verify_manifest(
        manifest_path=manifest, expected_sha256=digest, expected_channel=channel, require_signatures=True
    )


def test_requiring_signatures_without_a_declared_channel_is_refused(tmp_path: Path) -> None:
    wheel, assets = _candidate_dir(tmp_path, set(CHANNEL_ASSETS[ADVISORY]))
    manifest = tmp_path / "candidate-manifest.json"
    build_manifest(
        tag=TAG, source_commit=SOURCE, wheel_path=wheel, asset_paths=assets,
        output_path=manifest, channel=ADVISORY,
    )
    digest = __import__("hashlib").sha256(manifest.read_bytes()).hexdigest()

    with pytest.raises(ReleaseError, match="needs the declared channel"):
        verify_manifest(manifest_path=manifest, expected_sha256=digest, require_signatures=True)


def test_the_declared_channel_is_mandatory_on_the_command_line() -> None:
    import sys

    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts/release_publication.py"), "verify-manifest",
         "--manifest", "missing.json", "--expected-sha256", "0" * 64],
        capture_output=True, text=True, check=False,
    )

    assert result.returncode != 0
    assert "--expected-channel" in result.stderr


# --------------------------------------------------------------------------
# Provenance names its subject
# --------------------------------------------------------------------------


def test_an_exercised_wheel_is_bound_by_bytes_and_never_called_qualified(tmp_path: Path) -> None:
    built = _wheel(tmp_path / "built" / WHEEL_FILENAME)
    exercised = _wheel(tmp_path / "exercised" / WHEEL_FILENAME)

    record = verify_wheel_provenance(
        built_path=built, qualified_path=exercised, source_commit=SOURCE, subject="exercised"
    )

    assert record["provenance_mode"] == "identical_bytes"
    assert {"exercised_wheel", "exercised_wheel_sha256"} <= set(record)
    assert not any("qualified" in key for key in record)

    tampered = _wheel(tmp_path / "tampered" / WHEEL_FILENAME, extra="x = 2\n")
    with pytest.raises(ReleaseError, match="does not match the exercised wheel"):
        verify_wheel_provenance(built_path=built, qualified_path=tampered, subject="exercised")


def test_the_provenance_command_takes_exactly_one_subject(tmp_path: Path) -> None:
    import sys

    built = _wheel(tmp_path / "built" / WHEEL_FILENAME)
    script = str(REPO_ROOT / "scripts/verify_wheel_provenance.py")

    both = subprocess.run(
        [sys.executable, script, "--built", str(built), "--qualified", str(built), "--exercised", str(built)],
        capture_output=True, text=True, check=False,
    )
    neither = subprocess.run(
        [sys.executable, script, "--built", str(built)], capture_output=True, text=True, check=False
    )

    assert both.returncode != 0 and "not allowed with argument" in both.stderr
    assert neither.returncode != 0 and "one of the arguments" in neither.stderr


# --------------------------------------------------------------------------
# `candidate`: forward the declared channel's values, and nothing else
# --------------------------------------------------------------------------


def _outputs(**overrides: str) -> str:
    values = {
        "version": VERSION,
        "release_tag": TAG,
        "source_sha": SOURCE,
        "wheel_filename": WHEEL_FILENAME,
        "wheel_sha256": "1" * 64,
        "manifest_sha256": "2" * 64,
        "release_notes_sha256": "3" * 64,
        "artifact_name": f"release-candidate-{TAG}",
    }
    values.update(overrides)
    return json.dumps(values, indent=2)


@pytest.mark.parametrize("channel", sorted(rc.CHANNELS))
def test_candidate_forwards_the_declared_channels_values(channel: str) -> None:
    declared = _outputs()
    values = rc.candidate(
        channel,
        tag=TAG,
        qualified_result="success" if channel == QUALIFIED else "skipped",
        advisory_result="success" if channel == ADVISORY else "skipped",
        qualified_outputs=declared if channel == QUALIFIED else "{}",
        advisory_outputs=declared if channel == ADVISORY else "{}",
    )

    assert {key: values[key] for key in rc.CANDIDATE_OUTPUTS} == json.loads(declared)
    assert values["channel"] == channel
    assert values["rehearsal_workflow"] == rc.REHEARSAL_WORKFLOWS[channel]
    assert values["release_assets"].split() == sorted(CHANNEL_ASSETS[channel])
    assert tuple(values["signed_assets"].split()) == CHANNEL_SIGNED_ASSETS[channel]


@pytest.mark.parametrize(
    ("advisory_outputs", "qualified_outputs", "message"),
    [
        (_outputs(release_tag="v9.9.8"), "{}", "not the pushed tag"),
        (_outputs(manifest_sha256=""), "{}", "did not export manifest_sha256"),
        (json.dumps({"version": VERSION}), "{}", "did not export release_tag"),
        (_outputs(), _outputs(), "undeclared qualified verification exported"),
        (_outputs(), json.dumps({"version": ""}), None),
        (_outputs(artifact_name="x\nwheel_sha256=evil"), "{}", "spans lines"),
        ("not json", "{}", "not valid JSON"),
        ('{"version": "1", "version": "2"}', "{}", "duplicate key"),
        (json.dumps({"version": 1}), "{}", "map names to strings"),
    ],
)
def test_candidate_refuses_anything_but_the_declared_channels_complete_values(
    advisory_outputs: str, qualified_outputs: str, message: str | None
) -> None:
    if message is None:
        # An undeclared job's *empty* outputs are what a skipped job exports.
        assert rc.candidate(
            ADVISORY, tag=TAG, qualified_result="skipped", advisory_result="success",
            qualified_outputs=qualified_outputs, advisory_outputs=advisory_outputs,
        )["channel"] == ADVISORY
        return
    with pytest.raises(rc.ChannelError, match=message):
        rc.candidate(
            ADVISORY, tag=TAG, qualified_result="skipped", advisory_result="success",
            qualified_outputs=qualified_outputs, advisory_outputs=advisory_outputs,
        )


def test_candidate_refuses_before_reading_outputs_when_the_wrong_job_ran() -> None:
    with pytest.raises(rc.ChannelError, match="did not stay skipped"):
        rc.candidate(
            ADVISORY, tag=TAG, qualified_result="success", advisory_result="success",
            qualified_outputs="{}", advisory_outputs=_outputs(),
        )


def test_the_candidate_command_writes_every_value_to_github_output(tmp_path, monkeypatch) -> None:
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    assert rc.main([
        "candidate", "--channel", "advisory", "--tag", TAG,
        "--qualified-result", "skipped", "--advisory-result", "success",
        "--qualified-outputs", "{}", "--advisory-outputs", _outputs(),
    ]) == 0
    written = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())
    assert set(written) == set(rc.CANDIDATE_OUTPUTS) | DERIVED_OUTPUTS
    assert written["signed_assets"] == "agents-shipgate-sbom.json advisory-statement.json"


# --------------------------------------------------------------------------
# `exercised`: the smoke evidence must cover exactly this candidate
# --------------------------------------------------------------------------


def _evidence(tmp_path: Path, wheel: Path, **overrides: object) -> Path:
    record: dict[str, object] = {
        "source_commit": SOURCE,
        "wheel_sha256": __import__("hashlib").sha256(wheel.read_bytes()).hexdigest(),
        "local_and_action_agree": True,
        "qualified": False,
        "qualification_claim": "none: synthetic distribution smoke only",
    }
    record.update(overrides)
    path = tmp_path / "distribution-evidence.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def test_smoke_evidence_for_this_commit_and_these_bytes_is_accepted(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path / WHEEL_FILENAME)

    assert rc.confirm_exercised(_evidence(tmp_path, wheel), source_commit=SOURCE, wheel_path=wheel)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"local_and_action_agree": False}, "installed CLI and the Action agreeing"),
        ({"local_and_action_agree": "true"}, "installed CLI and the Action agreeing"),
        ({"qualified": True}, "as unqualified"),
        ({"source_commit": "b" * 40}, "exercised source"),
        ({"wheel_sha256": "0" * 64}, "exercised wheel"),
    ],
)
def test_smoke_evidence_that_does_not_cover_this_candidate_is_refused(
    tmp_path: Path, overrides: dict[str, object], message: str
) -> None:
    wheel = _wheel(tmp_path / WHEEL_FILENAME)

    with pytest.raises(rc.ChannelError, match=message):
        rc.confirm_exercised(_evidence(tmp_path, wheel, **overrides), source_commit=SOURCE, wheel_path=wheel)


def test_unreadable_smoke_evidence_is_refused(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path / WHEEL_FILENAME)
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")

    with pytest.raises(rc.ChannelError, match="unreadable smoke evidence"):
        rc.confirm_exercised(tmp_path / "broken.json", source_commit=SOURCE, wheel_path=wheel)
    with pytest.raises(rc.ChannelError, match="unreadable smoke evidence"):
        rc.confirm_exercised(tmp_path / "absent.json", source_commit=SOURCE, wheel_path=wheel)


def test_the_evidence_reader_agrees_with_what_the_smoke_writes() -> None:
    """The fields `confirm_exercised` reads are the ones
    `release_engine_smoke.py` writes, read from its source rather than assumed."""

    source = (REPO_ROOT / "scripts/release_engine_smoke.py").read_text(encoding="utf-8")

    for field in ('"source_commit": source_commit', '"wheel_sha256":', '"qualified": False', '"local_and_action_agree": True'):
        assert field in source, field


# --------------------------------------------------------------------------
# The advisory statement
# --------------------------------------------------------------------------


def _statement(tmp_path: Path, **overrides: Any) -> dict[str, object]:
    arguments: dict[str, Any] = {
        "tag": TAG,
        "source_commit": SOURCE,
        "wheel_path": _wheel(tmp_path / WHEEL_FILENAME),
        "smoke_run_id": "34726385422",
        "repository": "ThreeMoonsLab/agents-shipgate",
        "declaration": _declaration(tmp_path, {VERSION: ADVISORY, "9.9.10": QUALIFIED}),
    }
    arguments.update(overrides)
    return rc.advisory_statement(**arguments)


def test_the_statement_claims_nothing_qualified_and_names_its_evidence(tmp_path: Path) -> None:
    statement = _statement(tmp_path)

    assert statement["schema"] == rc.STATEMENT_SCHEMA
    assert statement["channel"] == ADVISORY
    assert statement["claims"] == {"qualification": "none", "default_ci_mode": "advisory", "blocking": "opt_in"}
    assert statement["release_tag"] == TAG and statement["source_commit"] == SOURCE
    assert statement["exact_candidate_evidence"] == {
        "workflow": ".github/workflows/release-engine-smoke.yml",
        "run_id": 34726385422,
        "run_url": "https://github.com/ThreeMoonsLab/agents-shipgate/actions/runs/34726385422",
        "wheel_sha256": statement["wheel_sha256"],
    }
    assert (REPO_ROOT / statement["readiness_record"]["path"]).is_file()
    assert (REPO_ROOT / statement["exact_candidate_evidence"]["workflow"]).is_file()
    # No tier anywhere: a reader looking for one finds nothing to misread.
    assert "tier" not in json.dumps(statement)
    assert all(statement["not_claimed"])


def test_the_statements_default_claim_is_the_actions_actual_default() -> None:
    inputs = yaml.safe_load((REPO_ROOT / "action.yml").read_text(encoding="utf-8"))["inputs"]

    assert inputs["ci_mode"]["default"] == "advisory"
    assert inputs["fail_on"]["default"] == ""


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"tag": "v9.9.10"}, "not declared advisory"),
        ({"tag": "v9.9.11"}, "no reviewed release channel is declared"),
        ({"source_commit": "abc"}, "full lowercase 40-character SHA"),
        ({"smoke_run_id": "0"}, "not a workflow run id"),
        ({"smoke_run_id": "12; rm -rf /"}, "not a workflow run id"),
        ({"repository": "not a repository"}, "not owner/name"),
    ],
)
def test_the_statement_is_refused_outside_its_preconditions(tmp_path: Path, overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(rc.ChannelError, match=message):
        _statement(tmp_path, **overrides)


def test_the_statement_is_refused_for_a_wheel_of_another_version(tmp_path: Path) -> None:
    other = _wheel(tmp_path / "other" / "agents_shipgate-9.9.8-py3-none-any.whl", version="9.9.8")

    with pytest.raises(rc.ChannelError, match="does not match wheel version"):
        _statement(tmp_path, wheel_path=other)


def test_the_statement_command_writes_deterministic_json(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path / WHEEL_FILENAME)
    declaration = _declaration(tmp_path, {VERSION: ADVISORY})
    arguments = [
        "statement", "--tag", TAG, "--source-commit", SOURCE, "--wheel", str(wheel),
        "--smoke-run-id", "7", "--repository", "o/r", "--declaration", str(declaration),
    ]

    assert rc.main([*arguments, "--output", str(tmp_path / "one.json")]) == 0
    assert rc.main([*arguments, "--output", str(tmp_path / "two.json")]) == 0
    # The rehearsal and the release must seal byte-identical manifests.
    assert (tmp_path / "one.json").read_bytes() == (tmp_path / "two.json").read_bytes()


# --------------------------------------------------------------------------
# No version carries both claims
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("declared", "message"),
    [
        ({"1.0.0": QUALIFIED}, "1.0.0 \\(tagged advisory, now qualified\\)"),
        ({}, "1.0.0 \\(tagged advisory, now undeclared\\)"),
    ],
)
def test_a_tagged_versions_channel_cannot_change(declared: dict[str, str], message: str) -> None:
    with pytest.raises(rc.ChannelError, match=message):
        rc.assert_history_preserved(declared, {"1.0.0": ADVISORY})
    rc.assert_history_preserved({"1.0.0": ADVISORY, "1.1.0": QUALIFIED}, {"1.0.0": ADVISORY})


def test_no_tagged_version_in_this_repository_has_changed_channel() -> None:
    """Each tag's channel is read from its own tree, the way the cadence reader
    reads it. Tags from before #648 carry no declaration and bind nothing."""

    tags = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "tag", "--list", "v*"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    tagged = {}
    for tag in tags:
        channel = tag_channel(REPO_ROOT, tag)
        if channel in rc.CHANNELS:
            tagged[tag[1:]] = channel

    rc.assert_history_preserved(rc.load_declaration(REPO_ROOT / rc.DECLARATION_PATH), tagged)
