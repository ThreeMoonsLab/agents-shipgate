"""Which Python is running Shipgate, which Shipgate it is running, and what disagrees.

Agents Shipgate has three versions in play at any moment and no surface that
states them together: the distribution installed in the running interpreter,
the package that actually got imported, and the source tree the caller believes
they are working on. When those diverge the symptom is never "your versions
diverge" — it is a subcommand that looks missing, a fix that appears not to
take, or a console script that dies with ``ModuleNotFoundError`` before a line
of Shipgate runs (#334, and the epic's step 5 in #338).

Everything here is answered by *reading*, never by running: no interpreter is
spawned to interrogate it, no console script is executed to see what it does.
That is not only the trust-model invariant the whole package is held to
(``tests/test_adapter_static_only.py`` bans ``subprocess`` and the ``os.exec*``
family under ``src/``) — it is also the only thing that works, because the
environments worth diagnosing are precisely the ones where running something is
what fails. A stale console script is therefore identified from its shebang: if
the interpreter it names is gone, the script cannot start, and that is provable
without touching it.

**Stdlib only, on purpose.** The repository launcher imports this module to
explain an import failure, so it must not depend on anything whose absence it
is trying to report. ``agents_shipgate.invocation`` is the one first-party
import, and it has the same property.
"""

from __future__ import annotations

import os
import re
import sys
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _distribution_version
from pathlib import Path

import agents_shipgate
from agents_shipgate.invocation import (
    CONSOLE_SCRIPTS,
    Invocation,
    render_command,
    resolve_invocation,
)

__all__ = [
    "DISTRIBUTION_NAME",
    "MINIMUM_PYTHON",
    "REPOSITORY_LAUNCHER",
    "EnvironmentProbe",
    "describe_environment",
    "environment_report",
    "find_source_checkout",
    "probe_environment",
]

#: The distribution name ``pip`` knows this package by.
DISTRIBUTION_NAME = "agents-shipgate"

#: The floor from ``pyproject.toml``'s ``requires-python``. Duplicated rather
#: than parsed because this module must answer even when the source tree is not
#: on disk; ``tests/test_environment.py`` pins the two together.
MINIMUM_PYTHON: tuple[int, int] = (3, 12)

#: The repository-local launcher a source checkout provides. It is deliberately
#: spelled like the ``shipgate`` console script — it is the same CLI — and is
#: distinguished from one by living next to ``pyproject.toml`` rather than on
#: ``PATH``.
REPOSITORY_LAUNCHER = "shipgate"

# Enough of a wrapper to hold its shebang and, for the shell trampoline below,
# the ``exec`` line naming an absolute interpreter path. Console-script wrappers
# on Windows are compiled binaries, so this is read defensively and may decode
# to nothing useful; that is reported as an unknown interpreter, not as a fault.
_WRAPPER_READ_BYTES = 1024

# Shells a console-script wrapper can be written in. `pip` falls back to one
# whenever the interpreter path cannot go in a shebang.
_SHELLS = frozenset({"sh", "bash", "dash", "ksh", "zsh"})

# The interpreter a shell wrapper hands off to:
#
#     #!/bin/sh
#     '''exec' "/path with space/bin/python" "$0" "$@"
#
# Quoted or bare, and the ``exec`` may carry a trailing quote from the
# polyglot heredoc that makes the same file valid Python.
_EXEC_TARGET = re.compile(r"""exec['"]?\s+(?:"([^"]+)"|'([^']+)'|(\S+))""")


@dataclass(frozen=True)
class EnvironmentProbe:
    """Everything :func:`describe_environment` reads from the outside world.

    Collected in one impure place (:func:`probe_environment`) so the judging
    is a pure function of stated facts. The five environments this has to get
    right — clean checkout, editable install, released install, stale console
    script, unsupported interpreter — are then reachable in a test by
    *describing* them, rather than by building five real installations.
    """

    executable: str
    python_version: tuple[int, int, int]
    package_file: str | None
    imported_version: str | None
    installed_version: str | None
    path_entries: tuple[str, ...]
    invocation: Invocation
    search_from: tuple[Path, ...] = ()


def probe_environment(
    *,
    workspace: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> EnvironmentProbe:
    """Read the running process and its ``PATH``."""

    source_env = os.environ if env is None else env
    try:
        cwd = Path.cwd()
    except OSError:  # pragma: no cover - deleted working directory
        cwd = Path(os.sep)
    return EnvironmentProbe(
        executable=sys.executable,
        python_version=sys.version_info[:3],
        package_file=getattr(agents_shipgate, "__file__", None),
        imported_version=getattr(agents_shipgate, "__version__", None),
        installed_version=_installed_version(),
        path_entries=tuple(
            entry for entry in source_env.get("PATH", "").split(os.pathsep) if entry
        ),
        invocation=resolve_invocation(env=source_env),
        # Where to look for the checkout the caller believes they are working
        # on. Searched before the imported package's own root — see
        # `find_source_checkout` for why that order is load-bearing.
        search_from=tuple(path for path in (workspace, cwd) if path is not None),
    )


def _installed_version() -> str | None:
    """The version ``pip`` reports for this interpreter, or ``None`` if absent.

    Absent is the *normal* state of a clean source checkout run through the
    launcher, so it is a fact rather than a fault.
    """

    try:
        return _distribution_version(DISTRIBUTION_NAME)
    except PackageNotFoundError:
        return None
    except Exception:  # pragma: no cover - a corrupt metadata directory
        return None


def environment_report(
    *,
    workspace: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """The environment block for the live process."""

    return describe_environment(probe_environment(workspace=workspace, env=env))


def describe_environment(probe: EnvironmentProbe) -> dict[str, object]:
    """Report the environment and everything inconsistent about it."""

    import_source = _describe_import_source(probe.package_file)
    import_root = import_source.get("root")
    source_root = find_source_checkout(
        *probe.search_from,
        Path(str(import_root)) if import_root else None,
    )
    source_tree = _describe_source_tree(source_root, import_source)
    console_scripts = _describe_console_scripts(probe)
    launcher = {
        "source": probe.invocation.source,
        "executable": list(probe.invocation.tokens),
        "console_scripts": console_scripts,
    }
    interpreter = {
        "executable": probe.executable,
        "version": ".".join(str(part) for part in probe.python_version),
        "minimum_supported": ".".join(str(part) for part in MINIMUM_PYTHON),
        "supported": probe.python_version[:2] >= MINIMUM_PYTHON,
    }
    return {
        "interpreter": interpreter,
        "launcher": launcher,
        "import_source": import_source,
        "installed_version": probe.installed_version,
        "imported_version": probe.imported_version,
        "source_tree": source_tree,
        "mismatches": _mismatches(
            probe,
            interpreter=interpreter,
            import_source=import_source,
            source_tree=source_tree,
            console_scripts=console_scripts,
        ),
    }


def _describe_import_source(package_file: str | None) -> dict[str, object]:
    """Where the ``agents_shipgate`` that is running came from.

    ``kind`` answers the question a version string cannot: an editable install
    and a launcher-driven checkout both report ``source_checkout`` because both
    really are executing the tree the caller is editing, while a wheel in
    ``site-packages`` is a build that no edit will reach.
    """

    if not package_file:
        return {"package_path": None, "root": None, "kind": "unknown"}
    package_path = Path(package_file)
    # ``<root>/agents_shipgate/__init__.py`` — the entry that was on the path.
    root = package_path.parent.parent
    parts = {part.lower() for part in root.parts}
    if root.name == "src" and _reads_our_pyproject(root.parent):
        kind = "source_checkout"
    elif parts & {"site-packages", "dist-packages"}:
        kind = "installed"
    elif _reads_our_pyproject(root.parent):
        kind = "source_checkout"
    else:
        kind = "unknown"
    return {"package_path": str(package_path), "root": str(root), "kind": kind}


def find_source_checkout(*starts: Path | None) -> Path | None:
    """The nearest enclosing Agents Shipgate checkout, searching each start.

    Caller-first, import-last, and the order is the whole point. This answers
    "which checkout does the caller believe they are working on", so it has to
    be able to differ from where the code came from — otherwise the case worth
    catching cannot be seen at all.

    Taking the import root first looked equivalent and is not. A ``git
    worktree`` has no virtualenv of its own, so its editable install points at
    the *main* checkout: import-first would find that tree, agree with itself,
    and report nothing, while every edit in the worktree went unexecuted.
    Caller-first names the worktree, sees the import outside it, and says so.
    """

    for start in starts:
        if start is None:
            continue
        absolute = _absolute(start)
        for candidate in (absolute, *absolute.parents):
            if _reads_our_pyproject(candidate):
                return candidate
    return None


def _absolute(path: Path) -> Path:
    """Absolute, but without following symlinks.

    Every path comparison here uses this one normalization, so that a checkout
    reached through a symlink keeps the identity the caller gave it. Resolving
    instead would report a path nobody typed, and — worse — would place a
    symlinked ``src`` outside the checkout that contains it, turning an
    ordinary layout into a reported "you are running the wrong build".
    """

    return Path(os.path.abspath(path))


def _reads_our_pyproject(directory: Path) -> bool:
    return _pyproject_version(directory) is not None


def _pyproject_version(directory: Path) -> str | None:
    """This checkout's declared version, or ``None`` if it is not our checkout.

    Reads the manifest rather than trusting the directory name: a vendored copy
    or an unrelated ``src/`` layout must not be mistaken for the source tree the
    running code is supposed to have come from.
    """

    manifest = directory / "pyproject.toml"
    try:
        raw = manifest.read_bytes()
    except OSError:
        return None
    try:
        parsed = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
    project = parsed.get("project")
    if not isinstance(project, dict) or project.get("name") != DISTRIBUTION_NAME:
        return None
    declared = project.get("version")
    # A checkout that builds its version dynamically is still our checkout.
    return declared if isinstance(declared, str) else ""


def _describe_source_tree(
    root: Path | None, import_source: Mapping[str, object]
) -> dict[str, object]:
    if root is None:
        return {"root": None, "version": None, "launcher": None, "contains_import": None}
    declared = _pyproject_version(root)
    launcher = root / REPOSITORY_LAUNCHER
    import_root = import_source.get("root")
    contains_import = bool(import_root) and _is_within(Path(str(import_root)), root)
    return {
        "root": str(root),
        "version": declared or None,
        "launcher": str(launcher) if launcher.is_file() else None,
        "contains_import": contains_import,
    }


def _is_within(candidate: Path, parent: Path) -> bool:
    try:
        _absolute(candidate).relative_to(_absolute(parent))
    except ValueError:
        return False
    return True


def _describe_console_scripts(probe: EnvironmentProbe) -> list[dict[str, object]]:
    """The console scripts a bare ``agents-shipgate`` would actually reach.

    Only the first hit per name is reported, because that is the one the shell
    would run; the rest are shadowed and describing them would suggest a choice
    the caller does not have.
    """

    described: list[dict[str, object]] = []
    for name in CONSOLE_SCRIPTS:
        path = _which(name, probe.path_entries)
        if path is None:
            continue
        interpreter = _shebang_interpreter(path)
        described.append(
            {
                "name": name,
                "path": str(path),
                "interpreter": interpreter,
                "interpreter_exists": (
                    None if interpreter is None else Path(interpreter).is_file()
                ),
                "runs_this_interpreter": (
                    None
                    if interpreter is None
                    else _same_file(interpreter, probe.executable)
                ),
            }
        )
    return described


def _which(name: str, path_entries: Sequence[str]) -> Path | None:
    """``PATH`` lookup that does not consult the *live* ``PATH``.

    ``shutil.which`` reads ``os.environ`` and the real filesystem cwd, which
    would make the five environment cases untestable without mutating the
    process running the tests.
    """

    suffixes = ("", ".exe", ".bat", ".cmd") if os.name == "nt" else ("",)
    for entry in path_entries:
        for suffix in suffixes:
            candidate = Path(entry) / f"{name}{suffix}"
            try:
                if candidate.is_file():
                    return candidate
            except OSError:  # pragma: no cover - unreadable PATH entry
                continue
    return None


def _shebang_interpreter(script: Path) -> str | None:
    """The interpreter a console-script wrapper ultimately runs, if it names one.

    Two wrapper shapes, because `pip` writes both. The ordinary one names the
    interpreter in its shebang. When the interpreter path contains a space —
    which a checkout under ``~/my projects/`` produces — a shebang cannot carry
    it, so `pip` writes a shell trampoline instead: ``#!/bin/sh`` followed by an
    ``exec`` of the quoted real interpreter. Reading only the shebang there
    reported ``/bin/sh``, which exists and is not the running interpreter, so a
    perfectly healthy install raised ``console_script_runs_other_interpreter``
    once per alias — and the interpreter that could actually go stale was
    invisible.

    ``None`` covers every honest unknown: a compiled Windows wrapper, an
    unreadable file, a ``#!/usr/bin/env python`` line that defers the choice
    back to ``PATH``, a shell wrapper whose handoff is not recognised.
    Reporting one of those as a missing interpreter would accuse a working
    install.
    """

    try:
        with script.open("rb") as handle:
            head = handle.read(_WRAPPER_READ_BYTES)
    except OSError:
        return None
    if not head.startswith(b"#!"):
        return None
    # Lenient because the read is cut at a fixed byte count and may split a
    # multi-byte character; every shebang worth reading is ASCII.
    text = head.decode("utf-8", errors="replace")
    tokens = text.splitlines()[0][2:].strip().split()
    if not tokens:
        return None
    interpreter = tokens[0]
    name = Path(interpreter).name
    if name == "env":
        # ``#!/usr/bin/env python3`` resolves through PATH at exec time, so this
        # file does not name an interpreter — it names a search.
        return None
    if name in _SHELLS:
        return _trampoline_target(text)
    return interpreter


def _trampoline_target(text: str) -> str | None:
    """The interpreter a shell wrapper ``exec``s, or ``None`` if unrecognised."""

    match = _EXEC_TARGET.search(text)
    if match is None:
        return None
    target = next(group for group in match.groups() if group is not None)
    # ``exec "$0"`` and friends are the wrapper re-entering itself, not an
    # interpreter path.
    return target if target.startswith(("/", "\\")) or ":" in target else None


def _same_file(left: str, right: str) -> bool:
    try:
        return Path(left).resolve() == Path(right).resolve()
    except OSError:  # pragma: no cover - unreadable path
        return left == right


def _mismatch(
    code: str,
    *,
    severity: str,
    detail: str,
    command: str | None = None,
) -> dict[str, object]:
    entry: dict[str, object] = {"code": code, "severity": severity, "detail": detail}
    if command:
        entry["command"] = command
    return entry


def _launcher_command(source_tree: Mapping[str, object], args: Sequence[str]) -> str | None:
    """A Shipgate command spelled for the checkout's own launcher.

    The recovery for every "you are running a different build than you are
    editing" mismatch is the same command through the entry point that cannot
    get it wrong, so it is rendered through the invocation policy (#322) with
    that entry point spliced in rather than assembled by hand.
    """

    launcher = source_tree.get("launcher")
    if not isinstance(launcher, str) or not launcher:
        return None
    return render_command(list(args), prefix=Invocation((launcher,), "override"))


def _mismatches(
    probe: EnvironmentProbe,
    *,
    interpreter: Mapping[str, object],
    import_source: Mapping[str, object],
    source_tree: Mapping[str, object],
    console_scripts: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Everything about this environment that will mislead someone.

    ``error`` means a command will fail or has already run the wrong code;
    ``warning`` means two facts disagree in a way that will eventually explain
    a confusing result. Absent versions are never a mismatch: a clean checkout
    with no installed distribution is the state the launcher exists to support.
    """

    found: list[dict[str, object]] = []

    if not interpreter["supported"]:
        found.append(
            _mismatch(
                "interpreter_unsupported",
                severity="error",
                detail=(
                    f"{probe.executable} is Python {interpreter['version']}; "
                    f"Agents Shipgate requires {interpreter['minimum_supported']} "
                    "or newer. Run the repository launcher, which selects a "
                    "supported interpreter, or set AGENTS_SHIPGATE_PYTHON to one."
                ),
            )
        )

    imported = probe.imported_version
    tree_version = source_tree.get("version")
    contains_import = source_tree.get("contains_import")

    if source_tree.get("root") and contains_import is False:
        found.append(
            _mismatch(
                "import_outside_source_tree",
                severity="error",
                detail=(
                    f"agents_shipgate was imported from {import_source.get('root')} "
                    f"(version {imported or 'unknown'}), which is outside the "
                    f"checkout at {source_tree['root']} "
                    f"(version {tree_version or 'unknown'}). Edits to that "
                    "checkout are not what this command ran."
                ),
                command=_launcher_command(source_tree, ["doctor", "--json"]),
            )
        )
    elif contains_import and imported and tree_version and imported != tree_version:
        found.append(
            _mismatch(
                "source_tree_version_differs",
                severity="warning",
                detail=(
                    f"The checkout at {source_tree['root']} declares "
                    f"{tree_version} in pyproject.toml but its package reports "
                    f"{imported}. One of the two was edited without the other."
                ),
            )
        )

    installed = probe.installed_version
    if (
        installed
        and imported
        and installed != imported
        # A source checkout out-voting the installed distribution is the
        # intended state, not a defect: an editable install's metadata is
        # captured once and lags every version bump, and running a worktree
        # through the launcher shadows the install on purpose. Both are already
        # visible as `import_source.kind` plus the two version fields. Two
        # *installed* copies disagreeing is different — nothing chose that, and
        # which one answers depends on path order.
        and import_source.get("kind") != "source_checkout"
    ):
        found.append(
            _mismatch(
                "installed_version_differs",
                severity="warning",
                detail=(
                    f"The distribution installed in {probe.executable} is "
                    f"{installed}, but the package imported from "
                    f"{import_source.get('root')} reports {imported}. Two "
                    "installed copies are shadowing each other; which one runs "
                    "depends on path order."
                ),
            )
        )

    for script in console_scripts:
        if script.get("interpreter_exists") is False:
            found.append(
                _mismatch(
                    "console_script_interpreter_missing",
                    severity="error",
                    detail=(
                        f"{script['path']} runs {script['interpreter']}, which "
                        "does not exist. Running it fails before Agents Shipgate "
                        "starts. Remove or reinstall it; until then use the "
                        "entry point below."
                    ),
                    command=render_command(["doctor", "--json"], prefix=probe.invocation),
                )
            )
        elif script.get("runs_this_interpreter") is False:
            found.append(
                _mismatch(
                    "console_script_runs_other_interpreter",
                    severity="warning",
                    detail=(
                        f"{script['path']} runs {script['interpreter']}, not "
                        f"{probe.executable}. A bare `{script['name']}` executes "
                        "a different installation than this command did."
                    ),
                    command=render_command(["doctor", "--json"], prefix=probe.invocation),
                )
            )

    return found
