from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import sysconfig
import zipfile
from pathlib import Path

import pytest
import yaml

from scripts import install_action_engine as installer
from scripts.install_action_engine import install_wheel
from scripts.update_locks import compile_command
from scripts.verify_dependency_lock import (
    CO_INSTALLED,
    LOCK_TARGETS,
    co_installed_problems,
    verify_lock_target,
)

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
    # What `steps.python.outputs.python-path` names on a runner: the
    # interpreter by path, with nothing put on PATH for it.
    env = {"SHIPGATE_PYTHON": str(checkout["bin"] / "python"), **env}
    assert set(step.get("env", {})) <= set(env), f"{name!r} reads env this test does not set"
    return subprocess.run(
        ["bash", "-c", step["run"] if run is None else run],
        cwd=checkout["workspace"], env={**_clean_env(checkout), **env},
        capture_output=True, text=True, timeout=120,
    )


#: Every Python interpreter a shell line starts, however it is spelled: the
#: Action's `"${SHIPGATE_PYTHON}"`, bare, `python3`, an absolute path or
#: `"${pythonLocation}/bin/python"`.
_PYTHON_INVOCATION = re.compile(
    r'"\$\{SHIPGATE_PYTHON\}"'
    r'|(?<![\w.-])(?:[\w${}./"-]*/)?python(?:3(?:\.\d+)?)?(?![\w.-])"?'
)
#: What may follow one: `-P` (workspace off `sys.path`), or a script path, whose
#: own directory goes first instead.
_SAFE_PYTHON_ARGUMENTS = re.compile(r' (?:-P(?= )|"\$\{GITHUB_ACTION_PATH\}/scripts/\w+\.py"(?=\s|$))')


def _unsafe_python_invocations(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("#"):
        return []
    return [
        match.group(0)
        for match in _PYTHON_INVOCATION.finditer(stripped)
        if not _SAFE_PYTHON_ARGUMENTS.match(stripped, match.end())
    ]


@pytest.mark.parametrize("line", [
    "python -m pip install agents-shipgate",
    'python -P -m pip install "agents-shipgate==1.0.0" && python -m evil',
    '"${pythonLocation}/bin/python" -m pip install agents-shipgate',
    "/usr/bin/python3 -m pip install agents-shipgate",
    "python3.12 -m pip install agents-shipgate",
    '"${pythonLocation}/bin/python3.12" -m pip install agents-shipgate',
    "if python - <<PY; then",
    '"${SHIPGATE_PYTHON}" -m pip install agents-shipgate',
    '"${SHIPGATE_PYTHON}" - <<\'PY\'',
])
def test_the_python_rule_flags_every_unsafe_spelling(line: str) -> None:
    assert _unsafe_python_invocations(line), line


@pytest.mark.parametrize("line", [
    'python -P -m pip install "agents-shipgate==${SHIPGATE_VERSION}"',
    'python "${GITHUB_ACTION_PATH}/scripts/github_action_outputs.py"',
    "python -P - <<PY",
    "# python -m pip is what v1.0.0 ran",
    '"${SHIPGATE_PYTHON}" "${GITHUB_ACTION_PATH}/scripts/install_action_engine.py"',
    '"${SHIPGATE_PYTHON}" -P - <<\'PY\'',
])
def test_the_python_rule_accepts_the_safe_spellings(line: str) -> None:
    assert not _unsafe_python_invocations(line), line


def test_every_python_the_action_starts_keeps_the_workspace_off_sys_path() -> None:
    """`python -P …`, or a script path, whose own directory goes first instead."""

    offenders = [
        (name, line.strip(), _unsafe_python_invocations(line))
        for name, step in _action_steps().items()
        for line in str(step.get("run", "")).splitlines()
        if _unsafe_python_invocations(line)
    ]
    assert not offenders, offenders
    installer = (REPO_ROOT / "scripts/install_action_engine.py").read_text(encoding="utf-8")
    assert re.findall(r'sys\.executable, "[^"]*"', installer) == ['sys.executable, "-P"']


_RUNTIME = ["install", "--require-hashes", "--no-warn-script-location",
            "--requirement", str(REPO_ROOT / "constraints/action-runtime.txt")]


@_BASH_STEPS
@pytest.mark.parametrize("route", ["version", "source", "wheel"])
def test_install_step_runs_pip_and_not_the_pull_requests_pip(
    pull_request_checkout: dict[str, Path], tmp_path: Path, route: str,
) -> None:
    """Every route: the hash-locked runtime first, then the engine with `--no-deps` (#1032)."""

    checkout = pull_request_checkout
    output = tmp_path / "github-output"
    output.write_text("", encoding="utf-8")
    env = {**_INSTALL_ENV, "GITHUB_OUTPUT": str(output)}
    if route == "version":
        env["SHIPGATE_VERSION"] = "1.0.0"
        expected = [
            _RUNTIME,
            ["install", "--no-deps", "--no-warn-script-location", "agents-shipgate==1.0.0"],
        ]
    elif route == "source":
        expected = [
            _RUNTIME,
            ["install", "--require-hashes", "--no-warn-script-location",
             "--requirement", str(REPO_ROOT / "constraints/build-backend.txt")],
            ["install", "--no-deps", "--no-build-isolation", "--no-warn-script-location",
             str(REPO_ROOT)],
        ]
    else:
        wheel = checkout["workspace"] / "agents_shipgate-1.0.0-py3-none-any.whl"
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr("agents_shipgate-1.0.0.dist-info/METADATA",
                             "Name: agents-shipgate\nVersion: 1.0.0\n")
        env["SHIPGATE_WHEEL"] = wheel.name
        env["SHIPGATE_WHEEL_SHA256"] = hashlib.sha256(wheel.read_bytes()).hexdigest()
        expected = [
            _RUNTIME,
            ["install", "--force-reinstall", "--no-deps", "--no-warn-script-location", wheel.name],
        ]

    result = _run_step(checkout, "Install Agents Shipgate", env)

    assert not (checkout["markers"] / "workspace-pip").exists(), "the PR's pip/ ran"
    assert not (checkout["markers"] / "workspace-agents_shipgate").exists(), "the PR's engine ran"
    assert result.returncode == 0, result.stdout + result.stderr
    calls = [
        json.loads(line)
        for line in (checkout["markers"] / "trusted-pip").read_text(encoding="utf-8").splitlines()
    ]
    if route == "wheel":
        # The captured snapshot's private path, not the workspace file.
        assert calls[1][-1].endswith(f"/{wheel.name}") and calls[1][-1] != str(wheel), calls
        calls[1][-1] = wheel.name
    # Then pip's own requirement check, read for lines naming the engine.
    assert calls == [*expected, ["check"]], calls
    # Later steps call the console scripts here; nothing put them on PATH.
    assert output.read_text(encoding="utf-8") == f"bin={sysconfig.get_path('scripts')}\n"
    if route == "version":
        from agents_shipgate.core.verification_identity import _engine_distribution_sha256

        assert f"verification_identity.engine_distribution_sha256={_engine_distribution_sha256()}" in result.stdout
    else:
        assert "verification_identity.engine_distribution_sha256=" not in result.stdout


@_BASH_STEPS
@pytest.mark.parametrize("version", ["0.15.0", "0.8.0", "1.0.0rc1", "1.0.0.dev3", "1.*", ">=1.0"])
def test_an_engine_older_than_1_0_is_refused_at_install_as_a_version_skew(
    pull_request_checkout: dict[str, Path], version: str,
) -> None:
    """`github_action_outputs.py` imports what 1.0.0 introduced (#1032).

    0.15.0 used to install, run the whole scan, and then fail the outputs step
    with an ImportError and empty outputs. Now nothing is installed at all.
    """

    checkout = pull_request_checkout
    result = _run_step(
        checkout, "Install Agents Shipgate", {**_INSTALL_ENV, "SHIPGATE_VERSION": version}
    )
    assert result.returncode != 0
    assert not (checkout["markers"] / "trusted-pip").exists(), "pip ran for a refused version"
    if "*" in version or ">" in version:
        assert "must name one exact release" in result.stderr, result.stderr
    else:
        assert "version skew" in result.stderr and "1.0.0 or later" in result.stderr, result.stderr
    assert "engine_distribution_sha256" not in result.stdout


@pytest.mark.parametrize(
    ("version", "exact"),
    [("1.0.0", "1.0.0"), ("1.0", "1.0"), ("1", "1"), ("v1.3.0", "1.3.0"), (" 1.3.0\n", "1.3.0"),
     ("1.0.0.post1", "1.0.0.post1"), ("1.0.0.post1.dev2", "1.0.0.post1.dev2"),
     ("1.0.1rc1", "1.0.1rc1"), ("2.0.0", "2.0.0"), ("10.0.0", "10.0.0")],
)
def test_the_engine_floor_admits_1_0_0_and_every_later_release(version: str, exact: str) -> None:
    assert installer.check_engine_version(version) == exact


@pytest.mark.parametrize("version", ["0.15.0", "0.99.99", "1.0.0rc9", "1.0.0b1", "1.0.0.dev1", "0.1"])
def test_the_engine_floor_refuses_every_release_before_1_0_0(version: str) -> None:
    with pytest.raises(ValueError, match="version skew"):
        installer.check_engine_version(version)


@pytest.mark.parametrize("version", ["", " ", "1.*", ">=1.0", "1.3.0,<2", "latest", "1.3.0 ; x"])
def test_the_version_input_names_one_exact_release(version: str) -> None:
    with pytest.raises(ValueError, match="must name one exact release"):
        installer.check_engine_version(version)


def test_a_candidate_wheel_below_the_floor_is_refused_before_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    wheel = tmp_path / "agents_shipgate-0.15.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("agents_shipgate-0.15.0.dist-info/METADATA",
                         "Name: agents-shipgate\nVersion: 0.15.0\n")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("pip ran"))
    with pytest.raises(ValueError, match="version skew: shipgate_wheel 0.15.0"):
        install_wheel(workspace=tmp_path, wheel=wheel.name,
                      sha256=hashlib.sha256(wheel.read_bytes()).hexdigest(),
                      install_dependencies=lambda: pytest.fail("dependencies installed"))


@pytest.mark.parametrize(
    ("returncode", "report", "refused"),
    [
        (0, "No broken requirements found.\n", False),
        # A caller's earlier step may leave its own packages broken; not ours to judge.
        (1, "typer 0.27.3 requires shellingham, which is not installed.\n", False),
        (1, "agents-shipgate 9.9.9 requires newdep, which is not installed.\n", True),
        (1, "agents-shipgate 9.9.9 has requirement pydantic>=3, but you have pydantic 2.14.3.\n", True),
        (1, "agents_shipgate 9.9.9 requires newdep, which is not installed.\n", True),
    ],
)
def test_an_engine_the_lock_does_not_satisfy_is_a_version_skew(
    monkeypatch: pytest.MonkeyPatch, returncode: int, report: str, refused: bool,
) -> None:
    """`--no-deps` stops pip comparing the engine's requirements with the lock; this does."""

    def pip(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert argv[1:] == ["-P", "-m", "pip", "check"] and kwargs["capture_output"] is True
        return subprocess.CompletedProcess(argv, returncode, stdout=report, stderr="")

    monkeypatch.setattr(subprocess, "run", pip)
    if refused:
        with pytest.raises(ValueError, match="version skew: the installed engine needs") as caught:
            installer.require_engine_requirements()
        assert report.strip().rstrip(".") in str(caught.value)
        assert "constraints/action-runtime.txt" in str(caught.value)
    else:
        installer.require_engine_requirements()


def test_the_runtime_lock_is_a_gated_lock_with_every_pin_hashed() -> None:
    """The file the installer passes to `--require-hashes` is one the lock gate checks."""

    target = next(t for t in LOCK_TARGETS if t.lock == "constraints/action-runtime.txt")
    assert installer.RUNTIME_LOCK == REPO_ROOT / target.lock
    assert installer.BUILD_BACKEND_LOCK == REPO_ROOT / "constraints/build-backend.txt"
    assert (target.source, target.extras) == ("pyproject.toml", ())
    assert verify_lock_target(target) == []
    # Compiled from what the suite ran, and held to it and to the backend it is
    # installed beside: a pin that moved apart from either fails the lock gate.
    assert compile_command(target)[-3:] == ["--constraint", "constraints/dev.txt", "pyproject.toml"]
    assert ("constraints/action-runtime.txt", "constraints/build-backend.txt") in CO_INSTALLED
    assert ("constraints/dev.txt", "constraints/action-runtime.txt") in CO_INSTALLED
    assert co_installed_problems() == []


def test_the_action_calls_its_python_and_console_scripts_by_path() -> None:
    """`update-environment: false`: the caller's later steps keep their own Python."""

    steps = _action_steps()
    setup = steps["Set up Python"]
    assert setup["id"] == "python" and setup["with"]["update-environment"] is False
    assert steps["Install Agents Shipgate"]["id"] == "install"
    commands = re.compile(r'(?<![\w/-])(?:agents-shipgate|shipgate)(?= )')
    for name, step in steps.items():
        run = str(step.get("run", ""))
        code = "\n".join(line for line in run.splitlines() if not line.strip().startswith("#"))
        env = step.get("env", {})
        if "${SHIPGATE_PYTHON}" in code:
            assert env["SHIPGATE_PYTHON"] == "${{ steps.python.outputs.python-path }}", name
        if "${SHIPGATE_BIN}" in code:
            assert env["SHIPGATE_BIN"] == "${{ steps.install.outputs.bin }}", name
        # No interpreter or console script resolved through PATH.
        assert not re.search(r"(?<![\w{/.-])python3?(?:\.\d+)?\s", code), (name, code)
        bare = [match.group(0) for match in commands.finditer(code)]
        assert not bare, (name, bare)
    assert sum("${SHIPGATE_BIN}/" in str(step.get("run", "")) for step in steps.values()) == 5


@_BASH_STEPS
def test_an_engine_without_the_identity_still_installs(
    pull_request_checkout: dict[str, Path], tmp_path: Path,
) -> None:
    """The identity line is a diagnostic: an engine that cannot compute it still installs."""

    checkout = pull_request_checkout
    legacy = tmp_path / "legacy-engine"
    (legacy / "agents_shipgate").mkdir(parents=True)
    (legacy / "agents_shipgate" / "__init__.py").write_text('__version__ = "1.0.0"\n', encoding="utf-8")
    env = {**_INSTALL_ENV, "SHIPGATE_VERSION": "1.0.0"}
    run = f'export PYTHONPATH="{legacy}{os.pathsep}$PYTHONPATH"\n' + _action_steps()[
        "Install Agents Shipgate"
    ]["run"]
    result = _run_step(checkout, "Install Agents Shipgate", env, run=run)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "::warning::installed engine identity unavailable" in result.stdout
    assert "engine_distribution_sha256=" not in result.stdout


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
    env = {key: "" for key in _action_steps()[name].get("env", {}) if key != "SHIPGATE_PYTHON"}
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
    verdict = steps["Apply Agents Shipgate merge verdict policy"]["run"]
    assert '"${SHIPGATE_PYTHON}" -P - <<' in verdict
    source = (REPO_ROOT / "scripts/install_action_engine.py").read_text(encoding="utf-8")
    assert '[sys.executable, "-P", "-m", "pip", *arguments]' in source

    # The installer's pip command, in the directory the install step runs in.
    _run_step(checkout, "Install Agents Shipgate", dict(_INSTALL_ENV),
              run='"${SHIPGATE_PYTHON}" -m pip install agents-shipgate')
    assert (checkout["markers"] / "workspace-pip").exists()
    _run_step(checkout, "Apply Agents Shipgate merge verdict policy",
              {"FAIL_ON_MERGE_VERDICTS": "blocked", "MERGE_VERDICT": "blocked"},
              run=verdict.replace('"${SHIPGATE_PYTHON}" -P ', '"${SHIPGATE_PYTHON}" '))
    assert (checkout["markers"] / "workspace-agents_shipgate").exists()
