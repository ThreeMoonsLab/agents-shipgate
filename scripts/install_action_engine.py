#!/usr/bin/env python3
"""Install the Action's engine over a hash-locked runtime (#570, #1032).

Every route first installs ``constraints/action-runtime.txt`` with
``--require-hashes``: the runtime half of the closure the test suite runs on,
so two runs of one pinned Action execute the same dependency bytes. The engine
then goes in with ``--no-deps``, which cannot move that closure:

``shipgate_version``
    ``agents-shipgate==<version>`` from PyPI. A version below
    :data:`MINIMUM_ENGINE_VERSION` is refused before anything is installed: the
    Action's output step imports what only 1.0.0 and later ship, so an older
    engine ran the whole scan and then failed with an ``ImportError`` and empty
    outputs.
neither input
    This Action's own source, built by the hash-locked backend
    (``constraints/build-backend.txt``) with ``--no-build-isolation``, so the
    build resolves nothing from the index either.
``shipgate_wheel`` + ``shipgate_wheel_sha256``
    One local candidate wheel, captured privately, checked against its digest
    and installed from those same bytes. No candidate code is imported to
    validate its metadata.

With ``--no-deps`` pip never compares the engine's own requirements with the
lock, so the installer does: ``pip check`` naming the engine is a version skew,
refused at install rather than met as an ``ImportError`` in a later step.

The Action passes ``--venv-parent "$RUNNER_TEMP"``. The engine and its locked
closure then go into a virtual environment of their own, created there from the
runner's Python, so the exact pins never move a package the caller installed
into that Python. The step outputs name the environment's interpreter
(``python``) and console-script directory (``bin``), which the Action's later
steps call by path, and the ``agents-shipgate`` command (``cli``). Only a
directory holding the ``agents-shipgate`` and ``shipgate`` entry points goes on
``GITHUB_PATH``, so the caller's later steps can run the CLI without finding
this environment's ``python`` or ``pip``. Without the option, as the release
engine smoke runs it, the installer installs into the interpreter running it.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import sysconfig
import tempfile
import venv
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from email.parser import BytesParser
from pathlib import Path
from types import SimpleNamespace

ACTION_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_LOCK = ACTION_ROOT / "constraints" / "action-runtime.txt"
BUILD_BACKEND_LOCK = ACTION_ROOT / "constraints" / "build-backend.txt"
IDENTITY_SCRIPT = Path(__file__).resolve().with_name("action_engine_identity.py")
DISTRIBUTION = "agents-shipgate"
#: The console scripts the caller's later steps may run. Nothing else from the
#: environment goes on their PATH.
CLI_COMMANDS = ("agents-shipgate", "shipgate")

#: The oldest engine the Action's steps can read. `github_action_outputs.py`
#: imports `validate_agent_control`, which 1.0.0 introduced.
MINIMUM_ENGINE_VERSION = "1.0.0"

#: One exact release: the PEP 440 public forms a release of this project uses.
#: A range or wildcard would let the index pick the engine, which is what a
#: version input exists to prevent.
_EXACT_VERSION = re.compile(
    r"v?(?P<release>[0-9]+(?:\.[0-9]+)*)"
    r"(?P<pre>(?:a|b|rc)[0-9]+)?"
    r"(?P<post>\.post[0-9]+)?"
    r"(?P<dev>\.dev[0-9]+)?"
)


def _ordering(match: re.Match[str]) -> tuple[tuple[int, ...], int]:
    """Enough of PEP 440 ordering to compare a release with the floor."""

    release = [int(part) for part in match["release"].split(".")]
    while release and release[-1] == 0:
        release.pop()
    # A pre-release, or a development release of the release itself (not of
    # a post-release), sorts before that release.
    before_final = bool(match["pre"]) or (bool(match["dev"]) and not match["post"])
    return tuple(release), 0 if before_final else 1


def check_engine_version(version: str, *, source: str = "shipgate_version") -> str:
    """The exact release ``version`` names, or :class:`ValueError` for a version skew."""

    match = _EXACT_VERSION.fullmatch(version.strip())
    if not match:
        raise ValueError(
            f"{source} must name one exact release such as {MINIMUM_ENGINE_VERSION}, "
            f"not {version!r}."
        )
    minimum = _EXACT_VERSION.fullmatch(MINIMUM_ENGINE_VERSION)
    assert minimum is not None
    exact = version.strip().removeprefix("v")
    if _ordering(match) < _ordering(minimum):
        raise ValueError(
            f"version skew: {source} {exact} is older than this Action supports. Its steps "
            f"import what agents-shipgate {MINIMUM_ENGINE_VERSION} introduced, so an older "
            "engine runs the scan and then fails the outputs step with an ImportError and "
            f"empty outputs. Set {source} to {MINIMUM_ENGINE_VERSION} or later, or pin the "
            "Action itself to the tag of the release that engine came from."
        )
    return exact


def _pip(python: str, *arguments: str, capture: bool = False) -> subprocess.CompletedProcess[str]:
    # `-P`: this runs in GITHUB_WORKSPACE, and `-m` would otherwise import a
    # `pip/` package from the pull request's checkout instead of pip.
    return subprocess.run(
        [python, "-P", "-m", "pip", *arguments],
        check=not capture,
        capture_output=capture,
        text=True,
    )


@dataclass(frozen=True)
class Environment:
    """Where the engine was installed: its interpreter and console scripts."""

    python: str
    bin: Path


class _EnvironmentBuilder(venv.EnvBuilder):
    """Records the interpreter and script paths of the environment it creates."""

    created: Environment | None = None

    def post_setup(self, context: SimpleNamespace) -> None:
        self.created = Environment(python=context.env_exec_cmd, bin=Path(context.bin_path))


def create_environment(parent: Path) -> Environment:
    """A fresh virtual environment under ``parent``, from the interpreter running this.

    Symlinked where `python -m venv` symlinks (everywhere but Windows), so the
    interpreter finds its shared library exactly as the runner's does. pip
    comes from the interpreter's own bundled `ensurepip`, which venv runs from
    inside the new environment's directory, not the workspace.
    """

    root = Path(tempfile.mkdtemp(prefix="agents-shipgate-", dir=parent)).resolve()
    builder = _EnvironmentBuilder(with_pip=True, symlinks=os.name != "nt")
    builder.create(root / "venv")
    if builder.created is None:  # pragma: no cover - venv always calls post_setup
        raise OSError(f"could not create a virtual environment under {root}")
    return builder.created


def current_environment() -> Environment:
    return Environment(python=sys.executable, bin=Path(sysconfig.get_path("scripts")))


def install_runtime(python: str) -> None:
    """The engine's dependencies, from the committed hash-locked closure."""

    _pip(python, "install", "--require-hashes", "--no-warn-script-location",
         "--requirement", str(RUNTIME_LOCK))


def install_version(python: str, exact: str) -> None:
    install_runtime(python)
    _pip(python, "install", "--no-deps", "--no-warn-script-location", f"{DISTRIBUTION}=={exact}")


def install_source(python: str) -> None:
    install_runtime(python)
    # The build backend is locked as well, and installed first, so the build
    # needs no isolated environment resolved from the index.
    _pip(
        python, "install", "--require-hashes", "--no-warn-script-location",
        "--requirement", str(BUILD_BACKEND_LOCK),
    )
    _pip(python, "install", "--no-deps", "--no-build-isolation", "--no-warn-script-location",
         str(ACTION_ROOT))


def install_wheel(
    *,
    workspace: Path,
    wheel: str,
    sha256: str,
    version: str = "",
    prepare_environment: Callable[[], str] | None = None,
) -> None:
    """Capture, check and install one candidate wheel.

    ``prepare_environment`` runs only once every check has passed: it readies
    the environment (and its locked runtime) and returns the interpreter to
    install into. Without it the wheel goes into the interpreter running this.
    """

    if version:
        raise ValueError("shipgate_wheel cannot be combined with shipgate_version")
    if not wheel or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ValueError("shipgate_wheel and its full lowercase shipgate_wheel_sha256 are required")
    root = workspace.resolve(strict=True)
    path = Path(wheel)
    path = path if path.is_absolute() else root / path
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ValueError("The candidate wheel must be inside GITHUB_WORKSPACE") from exc
    if ".." in relative.parts:
        raise ValueError("The candidate wheel path cannot contain parent traversal")
    cursor = root
    info = root.lstat()
    for part in relative.parts:
        cursor /= part
        info = cursor.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("The candidate wheel path cannot traverse symlinks or reparse points")
    if not stat.S_ISREG(info.st_mode) or info.st_size > 128 * 1024 * 1024:
        raise ValueError("The candidate wheel must be a regular file of at most 128 MiB")
    match = re.fullmatch(r"agents_shipgate-([0-9]+\.[0-9]+\.[0-9]+)-py3-none-any\.whl", path.name)
    if not match:
        raise ValueError("Expected an agents_shipgate X.Y.Z py3-none-any wheel filename")
    check_engine_version(match[1], source="shipgate_wheel")
    with tempfile.TemporaryDirectory(prefix="shipgate-action-wheel-") as directory:
        snapshot = Path(directory) / path.name
        # O_BINARY matters on Windows runners: without it os.open() hands back
        # a text-mode descriptor, and a zip read through one is rewritten at
        # every CRLF and truncated at the first 0x1A byte — so a wheel that is
        # exactly right never captures. The stdlib ORs it for the same reason
        # (`_pyio.FileIO`, `tempfile._bin_openflags`); it is 0 elsewhere.
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_BINARY", 0),
        )
        with os.fdopen(descriptor, "rb") as source, snapshot.open("wb") as destination:
            current = os.fstat(source.fileno())
            if not stat.S_ISREG(current.st_mode) or (current.st_dev, current.st_ino, current.st_size) != (
                info.st_dev, info.st_ino, info.st_size
            ):
                raise ValueError("The candidate wheel changed before capture")
            remaining = info.st_size + 1
            while remaining:
                chunk = source.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                destination.write(chunk)
                remaining -= len(chunk)
            if destination.tell() != info.st_size:
                raise ValueError("The candidate wheel size changed during capture")
        if hashlib.sha256(snapshot.read_bytes()).hexdigest() != sha256:
            raise ValueError("Candidate wheel SHA-256 mismatch; nothing was installed")
        with zipfile.ZipFile(snapshot) as archive:
            names = archive.namelist()
            expected = f"agents_shipgate-{match[1]}.dist-info/METADATA"
            metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
            if metadata_names != [expected] or len(names) != len(set(names)):
                raise ValueError("Candidate wheel must have one matching distribution METADATA")
            if archive.getinfo(expected).file_size > 1024 * 1024:
                raise ValueError("Candidate wheel METADATA exceeds 1 MiB")
            with archive.open(expected) as member:
                raw_metadata = member.read(1024 * 1024 + 1)
            if len(raw_metadata) > 1024 * 1024:
                raise ValueError("Candidate wheel METADATA exceeds 1 MiB")
            metadata = BytesParser().parsebytes(raw_metadata)
            if metadata.get_all("Name") != ["agents-shipgate"] or metadata.get_all("Version") != [
                match[1]
            ]:
                raise ValueError("Candidate wheel METADATA disagrees with its filename")
        python = prepare_environment() if prepare_environment is not None else sys.executable
        # Reinstall even when the runner already has the same version: its
        # bytes may be a different development build of that version.
        _pip(python, "install", "--force-reinstall", "--no-deps", "--no-warn-script-location",
             str(snapshot))


def _names_the_engine(line: str) -> bool:
    return re.match(r"agents[-_.]shipgate\s", line.strip(), flags=re.IGNORECASE) is not None


def require_engine_requirements(python: str) -> None:
    """Refuse an engine whose declared requirements the runtime lock does not meet.

    Only lines about the engine count: without ``--venv-parent`` the runner's
    Python may already hold packages a caller's earlier steps installed, and
    their state is not this install's to judge.
    """

    result = _pip(python, "check", capture=True)
    if result.returncode == 0:
        return
    problems = [
        line.strip().rstrip(".")
        for line in f"{result.stdout}\n{result.stderr}".splitlines()
        if _names_the_engine(line)
    ]
    if problems:
        raise ValueError(
            "version skew: the installed engine needs what this Action's hash-locked "
            f"runtime ({RUNTIME_LOCK.relative_to(ACTION_ROOT).as_posix()}) does not provide: "
            f"{'; '.join(problems)}. Pin the Action to the release tag that matches "
            "shipgate_version, or leave shipgate_version empty to install the Action's own source."
        )


def expose_cli(environment: Environment, directory: Path) -> Path:
    """A directory holding only the CLI entry points, for the caller's PATH.

    Each entry is a symlink to the environment's console script, whose first
    line names the environment's interpreter, so it runs the engine without
    putting that interpreter, or pip, on anyone's PATH. Windows gets a copy of
    the launcher `.exe`, which finds its interpreter the same way.
    """

    suffix = ".exe" if os.name == "nt" else ""
    directory.mkdir()
    for name in CLI_COMMANDS:
        target = environment.bin / f"{name}{suffix}"
        if not target.is_file():
            raise ValueError(f"the engine installed no {name} command at {target}")
        entry = directory / target.name
        if os.name == "nt":
            shutil.copy2(target, entry)
        else:
            entry.symlink_to(target)
    return directory / f"{CLI_COMMANDS[0]}{suffix}"


def publish(environment: Environment, cli: Path) -> None:
    """Hand the interpreter, scripts and CLI to later steps; put only the CLI on PATH."""

    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"python={environment.python}\n")
            handle.write(f"bin={environment.bin}\n")
            handle.write(f"cli={cli}\n")
    path = os.environ.get("GITHUB_PATH")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{cli.parent}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--venv-parent",
        type=Path,
        help="create the engine's own virtual environment under this directory",
    )
    args = parser.parse_args(argv)
    version = os.environ.get("SHIPGATE_VERSION", "")
    wheel = os.environ.get("SHIPGATE_WHEEL", "")
    wheel_sha256 = os.environ.get("SHIPGATE_WHEEL_SHA256", "")
    environment: Environment | None = None

    def prepare() -> str:
        nonlocal environment
        if args.venv_parent is not None:
            environment = create_environment(args.venv_parent)
        else:
            environment = current_environment()
        return environment.python

    def prepare_with_runtime() -> str:
        python = prepare()
        install_runtime(python)
        return python

    try:
        if wheel or wheel_sha256:
            install_wheel(
                workspace=Path(os.environ["GITHUB_WORKSPACE"]),
                wheel=wheel,
                sha256=wheel_sha256,
                version=version,
                prepare_environment=prepare_with_runtime,
            )
        elif version:
            # Refused before an environment exists or pip runs.
            exact = check_engine_version(version)
            install_version(prepare(), exact)
        else:
            install_source(prepare())
        assert environment is not None
        require_engine_requirements(environment.python)
        if args.venv_parent is not None:
            cli = expose_cli(environment, environment.bin.parent.parent / "cli")
            publish(environment, cli)
    except (ValueError, OSError, KeyError, zipfile.BadZipFile, subprocess.CalledProcessError) as exc:
        print(f"Agents Shipgate install refused: {exc}", file=sys.stderr)
        return 1
    if version and not (wheel or wheel_sha256):
        # A diagnostic: the identity of what was installed, from the
        # environment it was installed into. Run by script path.
        subprocess.run([environment.python, str(IDENTITY_SCRIPT)], check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
