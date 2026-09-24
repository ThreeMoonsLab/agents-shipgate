import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


def test_gitlab_ci_examples_are_parseable_and_store_reports():
    for path in sorted(Path("examples/gitlab-ci").glob("*.yml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        job = data["agents_shipgate"]

        assert "python -P -m pip install" in "\n".join(job["script"])
        assert "agents-shipgate scan" in "\n".join(job["script"])
        assert job["artifacts"]["when"] == "always"
        assert "agents-shipgate-reports/" in job["artifacts"]["paths"]


def test_circleci_examples_are_parseable_and_store_reports():
    for path in sorted(Path("examples/circleci").glob("*.yml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        job = data["jobs"]["agents-shipgate"]
        steps = job["steps"]

        assert job["docker"][0]["image"] == "cimg/python:3.12"
        assert any(_run_command(step).startswith("python -P -m pip install") for step in steps)
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


_PYTHON_MODULE_OR_STDIN = re.compile(
    r'(?<![\w.-])(?:[\w${}./"-]*/)?python(?:3(?:\.\d+)?)?'
    r'(?![\w.-])"?\s+(?P<flags>(?:-[A-Za-z]+\s+)*)'
    r"(?P<entry>-m\s+(?:pip|agents_shipgate)\b|-(?=\s|$))"
)


def _unsafe_checkout_invocations(text: str) -> list[str]:
    return [
        match.group(0)
        for match in _PYTHON_MODULE_OR_STDIN.finditer(text)
        if "-P" not in match.group("flags").split()
    ]


def test_ci_installations_use_safe_import_path():
    paths = [
        *Path("examples/gitlab-ci").glob("*.yml"),
        *Path("examples/circleci").glob("*.yml"),
    ]
    # Only CI documentation: the local trigger hook intentionally imports its repo.
    document = Path("docs/integrations.md").read_text(encoding="utf-8")
    ci_sections = document.split("## Local Diagnostics")[0]
    ci_sections += document.split("## GitLab CI", 1)[1].split("## MCP server", 1)[0]
    for path in paths:
        assert not _unsafe_checkout_invocations(path.read_text()), path
    assert not _unsafe_checkout_invocations(ci_sections)


@pytest.mark.parametrize("command", [
    "python -m pip install agents-shipgate",
    "python3.12 -m agents_shipgate scan",
    "python - <<'PY'",
    "echo setup && python -m pip install agents-shipgate",
    '"/usr/bin/python3.12" -m pip install agents-shipgate',
    '${PYTHON_ROOT}/python -m agents_shipgate scan',
])
def test_install_guard_rejects_unsafe_commands(command):
    assert _unsafe_checkout_invocations(command)


@pytest.mark.parametrize("command", [
    "python -P -m pip install agents-shipgate",
    "python3.12 -P -m agents_shipgate scan",
    "python -P - <<'PY'",
    '"/usr/bin/python3.12" -P -m pip install agents-shipgate',
])
def test_install_guard_accepts_safe_commands(command):
    assert not _unsafe_checkout_invocations(command)


def test_safe_path_prevents_checkout_pip_shadowing(tmp_path):
    # Synthetic module: never install anything or execute a subject repository.
    (tmp_path / "pip.py").write_text("print('CHECKOUT_SHADOW_MARKER')\n")
    env = {k: v for k, v in os.environ.items()
           if k not in {"PYTHONPATH", "PYTHONSAFEPATH"}}
    unsafe = subprocess.run(
        [sys.executable, "-m", "pip", "--version"], cwd=tmp_path,
        env=env, capture_output=True, text=True, check=True, timeout=30,
    )
    safe = subprocess.run(
        [sys.executable, "-P", "-m", "pip", "--version"], cwd=tmp_path,
        env=env, capture_output=True, text=True, check=True, timeout=30,
    )
    assert "CHECKOUT_SHADOW_MARKER" in unsafe.stdout
    assert "CHECKOUT_SHADOW_MARKER" not in safe.stdout
    assert safe.stdout.startswith("pip ")
