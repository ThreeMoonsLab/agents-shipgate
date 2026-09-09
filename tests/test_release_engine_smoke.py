from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from scripts import release_engine_smoke
from scripts.release_engine_smoke import compare

ROOT = Path(__file__).resolve().parents[1]


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
    downstream = workflow["jobs"]["downstream"]
    action = next(step for step in downstream["steps"] if step.get("uses") == "./")
    assert action["with"]["shipgate_wheel_sha256"] == "${{ needs.candidate.outputs.wheel_sha256 }}"
    assert action["with"]["pr_comment"] == "false"


def test_candidate_build_mode_is_scoped_to_sealer_build_only() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/release-verify.yml").read_text())
    assert "AGENTS_SHIPGATE_CANDIDATE_SOURCE_COMMIT" not in workflow.get("env", {})
    found = []
    for name, job in workflow["jobs"].items():
        assert "AGENTS_SHIPGATE_CANDIDATE_SOURCE_COMMIT" not in job.get("env", {})
        for step in job.get("steps", []):
            if "AGENTS_SHIPGATE_CANDIDATE_SOURCE_COMMIT" in step.get("env", {}):
                found.append((name, step["name"]))
    assert found == [("artifact", "Build a wheel from the checked-out source")]
