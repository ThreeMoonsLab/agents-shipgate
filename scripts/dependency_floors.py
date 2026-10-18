#!/usr/bin/env python3
"""Hold the runtime dependency floors to versions that have been tested (#1028).

The floors in ``pyproject.toml``'s ``[project].dependencies`` are a contract
with every environment Agents Shipgate is installed into, not a record of what
CI happens to run. When they tracked the newest upstream release, an agent
project that already pinned ``cryptography==46.0.7`` or ``packaging==24.2``
did not get an error from ``pip install agents-shipgate``: pip quietly chose
0.15.0, the newest release whose metadata still admitted those pins, an engine
without the verifier and diff surfaces the documentation describes.

So a floor is held to two facts, each checked by a subcommand that CI's
``dependency-floors`` job runs on every pull request:

``coinstall``
    ``pip install --dry-run --report`` of this tree's declarations next to
    pins real agent projects carry. Each scenario must resolve *this* version
    of Agents Shipgate together with the pin, not fall back or fail. Needs the
    package index.

``compile`` / ``installed``
    ``uv pip compile --resolution lowest-direct`` of the runtime requirements
    and the test tools the job runs, with hashes: every direct requirement at
    the lowest version it admits, everything else at the newest. It fails
    unless each runtime dependency resolved to exactly its declared floor — a
    floor that some other requirement silently raised would otherwise be
    "tested" at a version it is not. ``installed`` then re-checks the
    interpreter the tests will run in.

A floor moves only when the code needs something the old version lacks, or a
version at or above it is known to break Agents Shipgate (CONTRIBUTING.md
§ Changing dependencies). Upper caps are kept only where a break is known.

    python scripts/dependency_floors.py coinstall
    python scripts/dependency_floors.py compile --uv "$(command -v uv)" --output floors.txt
    python scripts/dependency_floors.py installed
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess  # noqa: S404 - drives pip and uv, explicit developer tools
import sys
import tempfile
import tomllib
import zipfile
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

if __package__:
    from scripts._release_support import ReleaseError
    from scripts.verify_dependency_lock import parse_lock
else:  # ``python scripts/dependency_floors.py``
    from _release_support import ReleaseError
    from verify_dependency_lock import parse_lock

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The interpreter the resolutions target: `requires-python`'s floor, which is
#: also the version CI runs. Every scenario below must hold there.
TARGET_PYTHON = "3.12"

#: The test tools the floors job installs beside the runtime closure, pinned to
#: the versions `constraints/dev.txt` locks, so the tools are not a variable:
#: what differs from the ordinary suite is the runtime dependencies under test
#: and what they pull in.
TEST_TOOLS = ("pytest", "pytest-xdist", "hypothesis", "jsonschema")
DEV_LOCK = "constraints/dev.txt"


@dataclass(frozen=True)
class Scenario:
    """One environment an adopter already has, and must still install into."""

    name: str
    pins: tuple[str, ...]
    why: str


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        name="cryptography 46",
        pins=("cryptography==46.0.7",),
        why=(
            "with the floor at 50.0.1, an environment holding this pin got "
            "agents-shipgate 0.15.0 from pip, with no warning"
        ),
    ),
    Scenario(
        name="packaging 24",
        pins=("packaging==24.2",),
        why=(
            "langchain-core 0.3 to 1.0 cap packaging<26, so agent projects "
            "carry 24.x and 25.x; the 26.3 floor sent them to 0.15.0"
        ),
    ),
    Scenario(
        name="CrewAI",
        pins=("crewai==1.15.27",),
        why=(
            "CrewAI requires pydantic<2.13; the 2.13.4 floor made pip fall back "
            "to an old release or fail with ResolutionImpossible"
        ),
    ),
)


# ---------------------------------------------------------------------------
# The declarations
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Floor:
    """One runtime requirement and the lowest version it admits."""

    requirement: Requirement
    version: Version

    @property
    def name(self) -> str:
        return canonicalize_name(self.requirement.name)


def project_table(root: Path = REPO_ROOT) -> dict:
    pyproject = root / "pyproject.toml"
    project = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project")
    if not isinstance(project, dict):
        raise ReleaseError(f"{pyproject} has no [project] table.")
    return project


def runtime_floors(root: Path = REPO_ROOT) -> list[Floor]:
    """Every runtime requirement, each with the one floor it must declare.

    The shape is a policy, so it is enforced rather than assumed: a ``>=``
    floor, an optional ``<`` cap, no marker, extra or URL. Anything else would
    make "the lowest version this admits" a question this script answers by
    guessing — and the lowest-direct job would then test something other than
    what an adopter's resolver can pick.
    """

    floors: list[Floor] = []
    for spec in project_table(root).get("dependencies") or []:
        try:
            requirement = Requirement(spec)
        except InvalidRequirement as exc:
            raise ReleaseError(
                f"pyproject.toml declares an unparsable requirement {spec!r}: {exc}"
            ) from exc
        problems = []
        if requirement.marker is not None:
            problems.append("an environment marker")
        if requirement.extras:
            problems.append("extras")
        if requirement.url:
            problems.append("a direct URL")
        lower = [item for item in requirement.specifier if item.operator == ">="]
        other = [item for item in requirement.specifier if item.operator not in {">=", "<"}]
        if len(lower) != 1:
            problems.append(f"{len(lower)} `>=` floors instead of one")
        if other:
            problems.append(f"the operator(s) {', '.join(str(item) for item in other)}")
        if problems:
            raise ReleaseError(
                f"pyproject.toml declares {spec!r} with {', '.join(problems)}. A runtime "
                "requirement is a `>=` floor and, only where a break is known, a `<` cap."
            )
        try:
            version = Version(lower[0].version)
        except InvalidVersion as exc:
            raise ReleaseError(
                f"pyproject.toml declares an invalid floor in {spec!r}: {exc}"
            ) from exc
        floors.append(Floor(requirement=requirement, version=version))
    if not floors:
        raise ReleaseError("pyproject.toml declares no runtime dependencies.")
    return floors


# ---------------------------------------------------------------------------
# coinstall
# ---------------------------------------------------------------------------


def _record_line(name: str, data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return f"{name},sha256={digest},{len(data)}"


def metadata_wheel(directory: Path, root: Path = REPO_ROOT) -> tuple[Path, str]:
    """A wheel carrying only this tree's resolution metadata.

    A resolver reads four facts from a wheel — name, version, `Requires-Python`
    and `Requires-Dist` — and the build backend copies each of them from
    `[project]` verbatim. Writing them directly asks pip exactly the question
    the published wheel will ask, without a build backend whose own
    dependencies (`packaging`, for one) would have to be installed beside the
    floors under test.
    """

    project = project_table(root)
    name = str(project["name"])
    version = str(project["version"])
    stem = f"{canonicalize_name(name).replace('-', '_')}-{version}"
    lines = [
        "Metadata-Version: 2.1",
        f"Name: {name}",
        f"Version: {version}",
    ]
    if project.get("requires-python"):
        lines.append(f"Requires-Python: {project['requires-python']}")
    lines += [f"Requires-Dist: {spec}" for spec in project.get("dependencies") or []]
    files = {
        f"{stem}.dist-info/METADATA": ("\n".join(lines) + "\n").encode(),
        f"{stem}.dist-info/WHEEL": (
            b"Wheel-Version: 1.0\nGenerator: scripts/dependency_floors.py\n"
            b"Root-Is-Purelib: true\nTag: py3-none-any\n"
        ),
    }
    record_name = f"{stem}.dist-info/RECORD"
    record = "\n".join([*(_record_line(path, data) for path, data in files.items()), f"{record_name},,"])
    files[record_name] = (record + "\n").encode()
    wheel = directory / f"{stem}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for path, data in files.items():
            archive.writestr(path, data)
    return wheel, version


@dataclass(frozen=True)
class Resolution:
    scenario: Scenario
    problems: tuple[str, ...]
    installed: dict[str, str]


def judge_report(
    scenario: Scenario, report: dict, *, distribution: str, version: str
) -> Resolution:
    """Whether a `pip --report` resolution kept this release and each pin."""

    installed = {
        canonicalize_name(item["metadata"]["name"]): item["metadata"]["version"]
        for item in report.get("install") or []
    }
    problems = []
    resolved = installed.get(canonicalize_name(distribution))
    if resolved != version:
        problems.append(
            f"{distribution} resolved to {resolved or 'nothing'}, not {version}"
        )
    for pin in scenario.pins:
        requirement = Requirement(pin)
        name = canonicalize_name(requirement.name)
        got = installed.get(name)
        if got is None or not requirement.specifier.contains(got, prereleases=True):
            problems.append(f"{pin} was not kept: resolved {name}=={got or 'nothing'}")
    return Resolution(scenario=scenario, problems=tuple(problems), installed=installed)


def resolve(
    scenario: Scenario,
    wheel: Path,
    *,
    version: str,
    report_path: Path,
    python_version: str = TARGET_PYTHON,
    distribution: str = "agents-shipgate",
) -> Resolution:
    """One `pip install --dry-run --report`, from the index, ignoring what is installed."""

    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--dry-run",
        "--ignore-installed",
        "--disable-pip-version-check",
        "--quiet",
        "--python-version",
        python_version,
        "--only-binary=:all:",
        "--report",
        str(report_path),
        str(wheel),
        *scenario.pins,
    ]
    result = subprocess.run(  # noqa: S603 - argument list built here
        command, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        tail = "\n".join(result.stderr.strip().splitlines()[-6:])
        return Resolution(
            scenario=scenario,
            problems=(f"pip could not resolve it (exit {result.returncode}):\n{tail}",),
            installed={},
        )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    return judge_report(scenario, report, distribution=distribution, version=version)


def coinstall(*, python_version: str = TARGET_PYTHON, root: Path = REPO_ROOT) -> int:
    distribution = str(project_table(root)["name"])
    failed = 0
    with tempfile.TemporaryDirectory(prefix="shipgate-floors-") as raw:
        directory = Path(raw)
        wheel, version = metadata_wheel(directory, root)
        for index, scenario in enumerate(SCENARIOS):
            resolution = resolve(
                scenario,
                wheel,
                version=version,
                report_path=directory / f"report-{index}.json",
                python_version=python_version,
                distribution=distribution,
            )
            label = f"{scenario.name} ({' '.join(scenario.pins)})"
            if resolution.problems:
                failed += 1
                lines = [f"FAIL: {label}: {scenario.why}"]
                lines += [f"  - {problem}" for problem in resolution.problems]
            else:
                lines = [f"OK: {label} resolves {distribution} {version} on Python {python_version}"]
            # Flushed per scenario: each is a resolution against the index, and
            # a CI log that shows nothing until the last one ends hides which
            # scenario is slow.
            print("\n".join(lines), flush=True)
    return 1 if failed else 0


# ---------------------------------------------------------------------------
# compile / installed
# ---------------------------------------------------------------------------


def test_tool_pins(root: Path = REPO_ROOT) -> list[str]:
    pins = parse_lock(root / DEV_LOCK)
    requirements = []
    for tool in TEST_TOOLS:
        branches = pins.get(canonicalize_name(tool), [])
        unconditional = [branch for branch in branches if not branch.marker and branch.version]
        if len(unconditional) != 1:
            raise ReleaseError(
                f"{DEV_LOCK} does not pin {tool} exactly once without a marker; "
                "the floors job takes its test tools from there."
            )
        requirements.append(f"{tool}=={unconditional[0].version}")
    return requirements


def compile_input(root: Path = REPO_ROOT) -> str:
    floors = runtime_floors(root)
    lines = [
        "# Generated by scripts/dependency_floors.py: the runtime requirements as",
        "# pyproject.toml declares them, and the test tools at their locked versions.",
        *(str(floor.requirement) for floor in floors),
        *test_tool_pins(root),
    ]
    return "\n".join(lines) + "\n"


def compile_command(source: Path, *, uv: str, python_version: str) -> list[str]:
    return [
        uv,
        "pip",
        "compile",
        "--resolution",
        "lowest-direct",
        "--generate-hashes",
        "--no-header",
        "--python-version",
        python_version,
        str(source),
    ]


def floor_problems(lock: Path, floors: list[Floor]) -> list[str]:
    """Each runtime dependency the lowest-direct resolution did not hold at its floor."""

    pins = parse_lock(lock)
    problems = []
    for floor in floors:
        branches = pins.get(floor.name, [])
        if len(branches) != 1 or branches[0].version is None:
            problems.append(f"{floor.name} is not pinned exactly once in the resolution")
            continue
        (branch,) = branches
        if Version(branch.version) != floor.version:
            raisers = ", ".join(branch.requesters) or "an unrecorded requester"
            problems.append(
                f"{floor.name} resolved to {branch.version}, not its floor {floor.version} "
                f"(requested by {raisers}). Something else requires more than the floor, "
                "so the job would not be testing the version pyproject.toml admits."
            )
    return problems


def compile_floors(
    output: Path, *, uv: str, python_version: str = TARGET_PYTHON, root: Path = REPO_ROOT
) -> int:
    floors = runtime_floors(root)
    with tempfile.TemporaryDirectory(prefix="shipgate-floors-") as raw:
        source = Path(raw) / "floors.in"
        source.write_text(compile_input(root), encoding="utf-8")
        result = subprocess.run(  # noqa: S603 - argument list built here
            compile_command(source, uv=uv, python_version=python_version),
            capture_output=True,
            text=True,
            check=False,
        )
    if result.returncode != 0:
        raise ReleaseError(f"uv pip compile --resolution lowest-direct failed:\n{result.stderr}")
    output.write_text(result.stdout, encoding="utf-8")
    problems = floor_problems(output, floors)
    if problems:
        raise ReleaseError("\n  - ".join(["The floors did not resolve as declared:", *problems]))
    for floor in floors:
        sys.stdout.write(f"OK: {floor.name}=={floor.version} ({floor.requirement})\n")
    return 0


def installed_problems(floors: list[Floor]) -> list[str]:
    problems = []
    for floor in floors:
        try:
            found = metadata.version(floor.name)
        except metadata.PackageNotFoundError:
            problems.append(f"{floor.name} is not installed")
            continue
        if Version(found) != floor.version:
            problems.append(f"{floor.name} is {found}, not its floor {floor.version}")
    return problems


def check_installed(root: Path = REPO_ROOT) -> int:
    floors = runtime_floors(root)
    problems = installed_problems(floors)
    if problems:
        raise ReleaseError(
            "\n  - ".join(["This interpreter is not at the declared floors:", *problems])
        )
    for floor in floors:
        sys.stdout.write(f"OK: {floor.name}=={floor.version} installed\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    coinstall_parser = commands.add_parser("coinstall", help="pip dry-run co-installation scenarios")
    coinstall_parser.add_argument("--python-version", default=TARGET_PYTHON)
    compile_parser = commands.add_parser("compile", help="lowest-direct resolution of the floors")
    compile_parser.add_argument("--uv", default="uv", help="path to the uv executable")
    compile_parser.add_argument("--output", type=Path, required=True)
    compile_parser.add_argument("--python-version", default=TARGET_PYTHON)
    commands.add_parser("installed", help="check this interpreter is at the floors")
    args = parser.parse_args(argv)

    try:
        if args.command == "coinstall":
            return coinstall(python_version=args.python_version)
        if args.command == "compile":
            return compile_floors(args.output, uv=args.uv, python_version=args.python_version)
        return check_installed()
    except (ReleaseError, OSError) as exc:
        sys.stderr.write(f"Dependency floor error: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
