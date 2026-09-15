from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

from scripts.install_action_engine import install_wheel

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def wheel(tmp_path: Path) -> Path:
    path = tmp_path / "agents_shipgate-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("agents_shipgate-1.0.0.dist-info/METADATA",
                         "Name: agents-shipgate\nVersion: 1.0.0\n")
    return path


def test_installer_pins_captured_bytes_even_if_original_is_replaced(
    wheel: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = wheel.read_bytes()
    installed = []

    def pip(argv: list[str], **kwargs: object) -> None:
        wheel.write_bytes(b"replacement")
        snapshot = Path(argv[-1])
        assert snapshot != wheel
        assert snapshot.read_bytes() == original
        assert snapshot.name == wheel.name
        assert "--force-reinstall" in argv and "--no-deps" in argv
        # `-P`: this runs in GITHUB_WORKSPACE, and `-m` would search it first.
        assert argv[1:4] == ["-P", "-m", "pip"]
        installed.append(snapshot)

    monkeypatch.setattr(subprocess, "run", pip)
    install_wheel(workspace=wheel.parent, wheel=wheel.name,
                  sha256=hashlib.sha256(original).hexdigest())
    assert len(installed) == 1
    assert not installed[0].exists()


@pytest.mark.parametrize("case", ["wrong-hash", "missing-hash", "missing-path", "version", "url"])
def test_invalid_selection_never_installs(
    wheel: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    def pip(*args: object, **kwargs: object) -> None:
        pytest.fail("invalid wheel selection reached pip")
    monkeypatch.setattr(subprocess, "run", pip)
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    kwargs = dict(workspace=wheel.parent, wheel=wheel.name, sha256=digest)
    if case == "wrong-hash":
        kwargs["sha256"] = "a" * 64
    elif case == "missing-hash":
        kwargs["sha256"] = ""
    elif case == "missing-path":
        kwargs["wheel"] = ""
    elif case == "version":
        kwargs["version"] = "1.0.0"
    else:
        kwargs["wheel"] = "https://example.invalid/candidate.whl"
    with pytest.raises((ValueError, OSError)):
        install_wheel(**kwargs)


def test_installer_rejects_symlink_and_outside_workspace(wheel: Path, tmp_path: Path) -> None:
    link = tmp_path / "linked.whl"
    try:
        link.symlink_to(wheel)
    except OSError:
        pytest.skip("symlinks unavailable")
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="symlinks"):
        install_wheel(workspace=tmp_path, wheel=link.name, sha256=digest)
    nested = tmp_path / "nested"
    nested.mkdir()
    with pytest.raises(ValueError, match="inside"):
        install_wheel(workspace=nested, wheel=str(wheel), sha256=digest)


def test_hash_match_does_not_accept_conflicting_metadata(wheel: Path) -> None:
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("agents_shipgate-1.0.0.dist-info/METADATA",
                         "Name: agents-shipgate\nVersion: 0.1.0\n")
    with pytest.raises(ValueError, match="METADATA"):
        install_wheel(workspace=wheel.parent, wheel=wheel.name,
                      sha256=hashlib.sha256(wheel.read_bytes()).hexdigest())


def test_compressed_oversized_metadata_is_refused_before_install(
    wheel: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("agents_shipgate-1.0.0.dist-info/METADATA", "x" * (1024 * 1024 + 1))
    assert wheel.stat().st_size < 10_000
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("pip ran"))
    with pytest.raises(ValueError, match="METADATA exceeds"):
        install_wheel(workspace=wheel.parent, wheel=wheel.name,
                      sha256=hashlib.sha256(wheel.read_bytes()).hexdigest())


# The Action's composite steps run in GITHUB_WORKSPACE: the pull request's own
# files. `python -m …` and a script on stdin put that directory first on
# sys.path, so a PR shipping `pip/__main__.py` ran in place of pip before the
# engine was installed, and a PR's `agents_shipgate/` package could stand in for
# the engine in the merge-verdict step (#780 review). Each test below executes
# the real step from action.yml against a checkout that carries both impostors.

_BASH_STEPS = pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None,
    reason="The composite steps are bash scripts.",
)
_INSTALL_ENV = {"SHIPGATE_VERSION": "", "SHIPGATE_WHEEL": "", "SHIPGATE_WHEEL_SHA256": ""}


def _action_steps() -> dict[str, dict]:
    action = yaml.safe_load((REPO_ROOT / "action.yml").read_text(encoding="utf-8"))
    return {step["name"]: step for step in action["runs"]["steps"]}


@pytest.fixture
def pull_request_checkout(tmp_path: Path) -> dict[str, Path]:
    """A checkout impersonating pip and the engine, and where the real ones live.

    Each impostor writes a marker when imported. The trusted stand-ins sit on
    PYTHONPATH — behind the working directory for `python -m` and stdin, ahead
    of site-packages — so they are reached only when the checkout is off
    sys.path, and nothing reaches a package index.
    """

    markers = tmp_path / "markers"
    markers.mkdir()
    workspace = tmp_path / "workspace"
    for package in ("pip", "agents_shipgate"):
        (workspace / package).mkdir(parents=True)
        body = (
            "import pathlib\n"
            f"pathlib.Path({str(markers / f'workspace-{package}')!r}).write_text('ran')\n"
        )
        (workspace / package / "__init__.py").write_text(body, encoding="utf-8")
        (workspace / package / "__main__.py").write_text(body, encoding="utf-8")
    trusted = tmp_path / "trusted"
    (trusted / "pip").mkdir(parents=True)
    (trusted / "pip" / "__init__.py").write_text("", encoding="utf-8")
    (trusted / "pip" / "__main__.py").write_text(
        "import json, pathlib, sys\n"
        f"with pathlib.Path({str(markers / 'trusted-pip')!r}).open('a') as log:\n"
        "    log.write(json.dumps(sys.argv[1:]) + '\\n')\n",
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    python = bin_dir / "python"
    python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    python.chmod(0o755)
    return {"workspace": workspace, "markers": markers, "trusted": trusted, "bin": bin_dir}


def _clean_env(checkout: dict[str, Path]) -> dict[str, str]:
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("PYTHON", "PIP_", "GITHUB_", "AGENTS_SHIPGATE_"))
    }
    env.update({
        "PATH": f"{checkout['bin']}{os.pathsep}{env.get('PATH', '')}",
        "PYTHONPATH": os.pathsep.join([str(checkout["trusted"]), str(REPO_ROOT / "src")]),
        "GITHUB_ACTION_PATH": str(REPO_ROOT),
        "GITHUB_WORKSPACE": str(checkout["workspace"]),
    })
    return env


def _run_step(
    checkout: dict[str, Path], name: str, env: dict[str, str], *, run: str | None = None,
) -> subprocess.CompletedProcess[str]:
    step = _action_steps()[name]
    assert set(step.get("env", {})) <= set(env), f"{name!r} reads env this test does not set"
    return subprocess.run(
        ["bash", "-c", step["run"] if run is None else run],
        cwd=checkout["workspace"], env={**_clean_env(checkout), **env},
        capture_output=True, text=True, timeout=120,
    )


_SAFE_PYTHON = re.compile(r'python (?:-P |"\$\{GITHUB_ACTION_PATH\}/scripts/\w+\.py"$)')


def test_every_python_the_action_starts_keeps_the_workspace_off_sys_path() -> None:
    """`python -P …`, or a script path, whose own directory goes first instead."""

    offenders = [
        (name, line.strip())
        for name, step in _action_steps().items()
        for line in str(step.get("run", "")).splitlines()
        if not line.strip().startswith("#")
        and re.search(r"(?<![\w./-])python3?(?![\w.-])", line)
        and not _SAFE_PYTHON.fullmatch(line.strip()) and not _SAFE_PYTHON.match(line.strip())
    ]
    assert not offenders, offenders
    installer = (REPO_ROOT / "scripts/install_action_engine.py").read_text(encoding="utf-8")
    assert re.findall(r'sys\.executable, "[^"]*"', installer) == ['sys.executable, "-P"']


@_BASH_STEPS
@pytest.mark.parametrize("route", ["version", "source", "wheel"])
def test_install_step_runs_pip_and_not_the_pull_requests_pip(
    pull_request_checkout: dict[str, Path], route: str,
) -> None:
    checkout = pull_request_checkout
    env = dict(_INSTALL_ENV)
    if route == "version":
        env["SHIPGATE_VERSION"] = "1.0.0"
        target = "agents-shipgate==1.0.0"
    elif route == "source":
        target = str(REPO_ROOT)
    else:
        wheel = checkout["workspace"] / "agents_shipgate-1.0.0-py3-none-any.whl"
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr("agents_shipgate-1.0.0.dist-info/METADATA",
                             "Name: agents-shipgate\nVersion: 1.0.0\n")
        env["SHIPGATE_WHEEL"] = wheel.name
        env["SHIPGATE_WHEEL_SHA256"] = hashlib.sha256(wheel.read_bytes()).hexdigest()
        target = wheel.name

    result = _run_step(checkout, "Install Agents Shipgate", env)

    assert not (checkout["markers"] / "workspace-pip").exists(), "the PR's pip/ ran"
    assert result.returncode == 0, result.stdout + result.stderr
    calls = (checkout["markers"] / "trusted-pip").read_text(encoding="utf-8").splitlines()
    assert len(calls) == 1, calls
    argv = json.loads(calls[0])
    assert argv[0] == "install" and argv[-1].endswith(target), argv


@_BASH_STEPS
@pytest.mark.parametrize(("verdict", "expected"), [("blocked", 20), ("human_review_required", 0)])
def test_merge_verdict_step_imports_the_installed_engine(
    pull_request_checkout: dict[str, Path], verdict: str, expected: int,
) -> None:
    checkout = pull_request_checkout
    result = _run_step(
        checkout, "Apply Agents Shipgate merge verdict policy",
        {"FAIL_ON_MERGE_VERDICTS": "blocked", "MERGE_VERDICT": verdict},
    )
    assert not (checkout["markers"] / "workspace-agents_shipgate").exists(), result.stderr
    assert result.returncode == expected, result.stdout + result.stderr


@_BASH_STEPS
@pytest.mark.parametrize("name", [
    "Extract Agents Shipgate outputs",
    "Emit Agents Shipgate annotations",
    "Build Agents Shipgate check-run payload",
])
def test_script_path_steps_do_not_search_the_workspace(
    pull_request_checkout: dict[str, Path], tmp_path: Path, name: str,
) -> None:
    """`python <path>` puts the script's directory first, never the checkout."""

    checkout = pull_request_checkout
    for handle in ("github-output", "step-summary"):
        (tmp_path / handle).write_text("", encoding="utf-8")
    env = {key: "" for key in _action_steps()[name].get("env", {})}
    env.update({
        "OUTPUT_DIR": "agents-shipgate-reports",
        "GITHUB_OUTPUT": str(tmp_path / "github-output"),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "step-summary"),
    })
    result = _run_step(checkout, name, env)
    assert not (checkout["markers"] / "workspace-agents_shipgate").exists(), result.stderr
    assert "No module named" not in result.stderr, result.stderr


@_BASH_STEPS
def test_the_console_script_does_not_search_the_workspace(
    pull_request_checkout: dict[str, Path],
) -> None:
    """The run step calls `agents-shipgate`; an entry-point script is not `-m`."""

    checkout = pull_request_checkout
    script = Path(sys.executable).with_name("agents-shipgate")
    if not script.is_file():
        pytest.skip("no agents-shipgate console script beside this interpreter")
    env = _clean_env(checkout)
    env.pop("PYTHONPATH")
    result = subprocess.run([str(script), "--version"], cwd=checkout["workspace"], env=env,
                            capture_output=True, text=True, timeout=120)
    assert not (checkout["markers"] / "workspace-agents_shipgate").exists(), result.stderr


@_BASH_STEPS
def test_negative_control_an_unguarded_python_runs_the_pull_requests_code(
    pull_request_checkout: dict[str, Path],
) -> None:
    """Without `-P`, the same steps on the same checkout do run the impostors.

    Keeps the tests above from passing because the fixture stopped reproducing
    the shadowing, rather than because the Action avoids it.
    """

    checkout = pull_request_checkout
    steps = _action_steps()
    install = steps["Install Agents Shipgate"]["run"]
    verdict = steps["Apply Agents Shipgate merge verdict policy"]["run"]
    assert install.count("python -P -m pip install") == 2 and "python -P - <<" in verdict

    _run_step(checkout, "Install Agents Shipgate", {**_INSTALL_ENV, "SHIPGATE_VERSION": "1.0.0"},
              run=install.replace("python -P ", "python "))
    assert (checkout["markers"] / "workspace-pip").exists()
    _run_step(checkout, "Apply Agents Shipgate merge verdict policy",
              {"FAIL_ON_MERGE_VERDICTS": "blocked", "MERGE_VERDICT": "blocked"},
              run=verdict.replace("python -P ", "python "))
    assert (checkout["markers"] / "workspace-agents_shipgate").exists()
