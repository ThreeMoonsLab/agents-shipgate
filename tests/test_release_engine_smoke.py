from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts import release_engine_smoke
from scripts.release_engine_smoke import _commit, compare, prepare

ROOT = Path(__file__).resolve().parents[1]
_ENV = "AGENTS_SHIPGATE_CANDIDATE_SOURCE_COMMIT"


def _result(root: Path, side: str, decision: str = "blocked") -> None:
    directory = root / ".shipgate-smoke" / side
    directory.mkdir(parents=True)
    (directory / "report.json").write_text(json.dumps({
        "release_decision": {"decision": decision},
        "findings": [{"check_id": "SHIP-POLICY-APPROVAL-MISSING", "severity": "critical"}],
    }))
    (directory / "verifier.json").write_text(json.dumps({"merge_verdict": "blocked"}))
    (directory / "verification-plan.json").write_text(json.dumps({"engine": {
        "version": "1.0.0", "engine_distribution_sha256": "sha256:" + "a" * 64,
    }}))


def test_smoke_requires_same_nonvacuous_capability_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _result(tmp_path, "local")
    _result(tmp_path, "ci")
    (tmp_path / ".shipgate-smoke/prepared.json").write_text(json.dumps({
        "qualified": False, "contract": {"cli_version": "1.0.0"},
    }))
    monkeypatch.setattr(release_engine_smoke, "_cli", lambda *args: {"cli_version": "1.0.0"})
    result = compare(tmp_path)
    assert result["local_and_action_agree"] is True
    assert result["qualified"] is False
    assert result["result"]["decision"] == "blocked"


@pytest.mark.parametrize("local,ci", [("passed", "passed"), ("blocked", "passed")])
def test_smoke_rejects_safe_pass_and_parity_failure(tmp_path: Path, local: str, ci: str) -> None:
    _result(tmp_path, "local", local)
    _result(tmp_path, "ci", ci)
    (tmp_path / ".shipgate-smoke/prepared.json").write_text("{}")
    with pytest.raises(ValueError, match="disagree"):
        compare(tmp_path)


def test_prepare_will_not_rewrite_a_checkout_it_was_not_told_is_disposable(
    tmp_path: Path,
) -> None:
    """Being at the candidate commit is what a maintainer's own checkout looks like.

    `prepare` makes two commits on the current HEAD and appends to
    `info/exclude`, so the caller has to say the checkout is expendable; the
    source-commit check cannot tell a runner from a release operator's tree.
    """
    with pytest.raises(ValueError, match="disposable"):
        prepare(tmp_path, "a" * 40, tmp_path / "candidate.whl")
    assert not (tmp_path / ".shipgate-smoke").exists()


def test_smoke_workflow_declares_its_checkout_disposable() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/release-engine-smoke.yml").read_text())
    steps = workflow["jobs"]["downstream"]["steps"]
    prepare_step = next(
        step for step in steps if "release_engine_smoke.py prepare" in step.get("run", "")
    )
    assert "--disposable-checkout" in prepare_step["run"]


def test_smoke_does_not_persist_a_commit_identity_into_the_checkout(tmp_path: Path) -> None:
    """A `git config user.email …` would outlive the run and re-author later work."""
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    (tmp_path / "tracked.txt").write_text("x\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    _commit(tmp_path, "smoke: base support tools")
    assert subprocess.run(
        ["git", "-C", str(tmp_path), "config", "--local", "--get-regexp", "^user\\."],
        capture_output=True, text=True,
    ).stdout == ""
    author = subprocess.check_output(
        ["git", "-C", str(tmp_path), "log", "-1", "--format=%ae"], text=True,
    ).strip()
    assert author == "distribution-smoke@example.invalid"


def test_smoke_cannot_publish_or_substitute_for_qualification() -> None:
    text = (ROOT / ".github/workflows/release-engine-smoke.yml").read_text()
    workflow = yaml.safe_load(text)
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["jobs"]) == {"candidate", "downstream"}
    assert "id-token" not in text
    assert "environment:" not in text
    assert "uv publish" not in text
    assert "run_safety_qualification" not in text
    assert "verify_safety_qualification" not in text
    for job in workflow["jobs"].values():
        checkout = next(step for step in job["steps"] if "actions/checkout@" in step.get("uses", ""))
        assert checkout["with"]["ref"] == "${{ github.sha }}"
        assert checkout["with"]["persist-credentials"] is False
    # `python -m hatchling build` does not enforce `[build-system] requires`
    # the way the sealer's `python -m build` does, so the only thing keeping
    # the smoke on the release backend is the hash-locked install ahead of it.
    # An index-resolved hatchling would stamp a different `Generator:` and the
    # "exact source candidate" would not be the bytes the sealer compares.
    candidate = workflow["jobs"]["candidate"]["steps"]
    runs = [step.get("run", "") for step in candidate]
    installed = [
        index for index, run in enumerate(runs)
        if "--require-hashes" in run and "constraints/build-backend.txt" in run
    ]
    built = [index for index, run in enumerate(runs) if "hatchling build" in run]
    assert len(installed) == 1 and len(built) == 1, (installed, built)
    assert installed[0] < built[0]

    downstream = workflow["jobs"]["downstream"]
    action = next(step for step in downstream["steps"] if step.get("uses") == "./")
    assert action["with"]["shipgate_wheel_sha256"] == "${{ needs.candidate.outputs.wheel_sha256 }}"
    assert action["with"]["pr_comment"] == "false"


def test_no_other_workflow_can_stamp_a_build_as_a_release_candidate() -> None:
    """Only two build steps in the repository may claim a source identity.

    A preview, rehearsal or CI build that acquired the stamp would emit an
    adopter workflow pinning an Action SHA and a package version that no
    published channel carries. Checking only the sealer would leave every
    other workflow free to add it.
    """
    stamped = []
    for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
        workflow = yaml.safe_load(path.read_text())
        assert _ENV not in (workflow.get("env") or {}), path.name
        for name, job in workflow["jobs"].items():
            assert _ENV not in (job.get("env") or {}), f"{path.name}:{name}"
            for step in job.get("steps", []):
                if _ENV in (step.get("env") or {}):
                    stamped.append((path.name, name, step["name"]))
    assert stamped == [
        ("release-engine-smoke.yml", "candidate", "Build the exact source candidate"),
        ("release-verify.yml", "artifact", "Build a wheel from the checked-out source"),
    ]


def test_candidate_build_mode_is_scoped_to_sealer_build_only() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/release-verify.yml").read_text())
    assert _ENV not in workflow.get("env", {})
    found = []
    for name, job in workflow["jobs"].items():
        assert _ENV not in job.get("env", {})
        for step in job.get("steps", []):
            if _ENV in step.get("env", {}):
                found.append((name, step["name"]))
    assert found == [("artifact", "Build a wheel from the checked-out source")]
