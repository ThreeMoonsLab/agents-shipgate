from pathlib import Path

import yaml


def test_gitlab_ci_examples_are_parseable_and_store_reports():
    for path in sorted(Path("examples/gitlab-ci").glob("*.yml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        job = data["agents_shipgate"]

        assert "python -m pip install" in "\n".join(job["script"])
        assert "agents-shipgate scan" in "\n".join(job["script"])
        assert job["artifacts"]["when"] == "always"
        assert "agents-shipgate-reports/" in job["artifacts"]["paths"]


def test_circleci_examples_are_parseable_and_store_reports():
    for path in sorted(Path("examples/circleci").glob("*.yml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        job = data["jobs"]["agents-shipgate"]
        steps = job["steps"]

        assert job["docker"][0]["image"] == "cimg/python:3.12"
        assert any(_run_command(step).startswith("python -m pip install") for step in steps)
        assert any("agents-shipgate scan" in _run_command(step) for step in steps)
        assert any("store_artifacts" in step for step in steps if isinstance(step, dict))


def _run_command(step: object) -> str:
    if not isinstance(step, dict) or "run" not in step:
        return ""
    run = step["run"]
    if isinstance(run, str):
        return run
    if isinstance(run, dict):
        return str(run.get("command") or "")
    return ""
