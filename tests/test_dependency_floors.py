"""The runtime floors are tested versions, and pip installs this release beside them (#1028).

With each floor at the newest upstream release, `pip install agents-shipgate`
in an agent project that pinned `cryptography==46.0.7` or `packaging==24.2`, or
used CrewAI (`pydantic<2.13`), quietly installed 0.15.0. `scripts/dependency_floors.py`
is what CI's `dependency-floors` job runs to keep that from coming back; these
tests hold its parts offline, and one resolves the real scenarios against the
package index when the index can be reached.
"""

from __future__ import annotations

import base64
import hashlib
import socket
import tomllib
import zipfile
from email.parser import BytesParser
from email.policy import default as email_policy
from pathlib import Path

import pytest
import yaml
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from scripts import dependency_floors as floors
from scripts._release_support import ReleaseError
from scripts.verify_dependency_lock import parse_lock

REPO_ROOT = Path(__file__).resolve().parent.parent

# The runtime dependencies that keep an upper cap, and the break each one
# stands for. Every other runtime dependency is uncapped: a cap with no known
# break only means that the next routine major sends pip back to an old
# Agents Shipgate (CONTRIBUTING.md § Changing dependencies).
CAPPED = {
    "pydantic": "Pydantic 3 is announced as a breaking release",
    "pyyaml": "a new PyYAML major is a declared break",
    "ruamel-yaml": "ruamel.yaml's 0.x series changes its loader API between minors",
    "typer": "Typer is 0.x; 1.0 is its declared break",
}


def _declared(name: str) -> Requirement:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    for spec in project["project"]["dependencies"]:
        requirement = Requirement(spec)
        if canonicalize_name(requirement.name) == canonicalize_name(name):
            return requirement
    raise AssertionError(f"pyproject.toml declares no {name}")


def _project(tmp_path: Path, dependencies: list[str], *, version: str = "9.9.9") -> Path:
    listed = ", ".join(f'"{spec}"' for spec in dependencies)
    (tmp_path / "pyproject.toml").write_text(
        "[project]\n"
        'name = "agents-shipgate"\n'
        f'version = "{version}"\n'
        'requires-python = ">=3.12"\n'
        f"dependencies = [{listed}]\n",
        encoding="utf-8",
    )
    return tmp_path


# ---------------------------------------------------------------------------
# The declarations
# ---------------------------------------------------------------------------


def test_every_runtime_dependency_declares_one_floor_and_a_cap_only_where_a_break_is_known():
    declared = floors.runtime_floors()

    assert {floor.name for floor in declared} == {
        "cryptography",
        "packaging",
        "pydantic",
        "pyyaml",
        "ruamel-yaml",
        "typer",
    }
    capped = {
        floor.name
        for floor in declared
        if any(item.operator == "<" for item in floor.requirement.specifier)
    }
    assert capped == set(CAPPED), (
        "An upper cap is declared only where a break is known; record the break "
        "in CAPPED, or drop the cap."
    )


@pytest.mark.parametrize("scenario", floors.SCENARIOS, ids=lambda scenario: scenario.name)
def test_the_floors_admit_each_scenarios_direct_pins(scenario: floors.Scenario):
    """The offline half of the co-installation check, and the part #1028 broke.

    A scenario pin on a package Agents Shipgate also declares must satisfy that
    declaration, or pip can only answer with an older Agents Shipgate. The
    transitive half (CrewAI's `pydantic<2.13`) needs the index, below.
    """

    for pin in scenario.pins:
        requirement = Requirement(pin)
        (version,) = [item.version for item in requirement.specifier]
        try:
            declared = _declared(requirement.name)
        except AssertionError:
            continue
        assert declared.specifier.contains(version, prereleases=True), (
            f"{declared} excludes {pin}: {scenario.why}"
        )


def test_the_floors_admit_crewais_pydantic_range():
    assert _declared("pydantic").specifier.contains("2.12.5")


@pytest.mark.parametrize(
    ("spec", "problem"),
    [
        ("typer", "0 `>=` floors"),
        ("typer<1", "0 `>=` floors"),
        ("typer==0.16.0", "0 `>=` floors"),
        ("typer>=0.16.0,!=0.17.0", "the operator(s) !=0.17.0"),
        ("typer>=0.16.0; python_version < '3.13'", "an environment marker"),
        ("typer[all]>=0.16.0", "extras"),
    ],
)
def test_a_runtime_requirement_without_exactly_one_floor_is_refused(
    tmp_path: Path, spec: str, problem: str
):
    root = _project(tmp_path, [spec])

    with pytest.raises(ReleaseError, match="A runtime requirement is a `>=` floor") as caught:
        floors.runtime_floors(root)

    assert problem in str(caught.value)


def test_dependabot_moves_the_locks_and_never_a_floor():
    """`increase-if-necessary`, not Dependabot's library-detection default.

    Unset, Dependabot raised a floor (`increase`) whenever PyPI's summary for
    agents-shipgate differed from pyproject.toml's description, which every
    description edit between releases arranged. `lockfile-only` would stop it
    proposing updates to constraints/*.txt at all, because it reads them as
    requirement files rather than lockfiles.
    """

    config = yaml.safe_load((REPO_ROOT / ".github/dependabot.yml").read_text(encoding="utf-8"))
    (pip,) = [entry for entry in config["updates"] if entry["package-ecosystem"] == "pip"]

    assert pip["versioning-strategy"] == "increase-if-necessary"


def test_pull_request_ci_resolves_the_floors_and_runs_the_engine_there():
    """The job that makes a floor a tested version: resolve, install hashed, check, test."""

    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    triggers = workflow.get("on", workflow.get(True))
    runs = [
        str(step.get("run", ""))
        for step in workflow["jobs"]["dependency-floors"]["steps"]
    ]

    assert "pull_request" in triggers
    for needle in (
        "scripts/dependency_floors.py coinstall",
        "scripts/dependency_floors.py compile",
        "--require-hashes --requirement \"${RUNNER_TEMP}/floors.txt\"",
        "scripts/dependency_floors.py installed",
        "python -m pytest",
        "scripts/regenerate_goldens.py --check",
    ):
        assert any(needle in run for run in runs), needle


def test_the_floor_is_the_lowest_version_the_requirement_admits(tmp_path: Path):
    root = _project(tmp_path, ["typer>=0.16.0,<1", "cryptography>=44.0.3"])

    declared = {floor.name: str(floor.version) for floor in floors.runtime_floors(root)}

    assert declared == {"typer": "0.16.0", "cryptography": "44.0.3"}


# ---------------------------------------------------------------------------
# coinstall
# ---------------------------------------------------------------------------


def test_the_metadata_wheel_states_exactly_what_the_resolver_reads(tmp_path: Path):
    """Name, version, Requires-Python and Requires-Dist, copied from `[project]`."""

    wheel, version = floors.metadata_wheel(tmp_path)
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert version == project["version"]
    assert wheel.name == f"agents_shipgate-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        message = BytesParser(policy=email_policy).parsebytes(
            archive.read(f"agents_shipgate-{version}.dist-info/METADATA")
        )
        record = archive.read(f"agents_shipgate-{version}.dist-info/RECORD").decode()
        for line in record.splitlines():
            path, digest, size = line.split(",")
            if path.endswith("RECORD"):
                assert (digest, size) == ("", "")
                continue
            data = archive.read(path)
            expected = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
            assert digest == f"sha256={expected.decode()}"
            assert int(size) == len(data)

    assert sorted(names) == sorted(
        f"agents_shipgate-{version}.dist-info/{name}" for name in ("METADATA", "WHEEL", "RECORD")
    )
    assert message["Name"] == "agents-shipgate"
    assert message["Version"] == version
    assert message["Requires-Python"] == project["requires-python"]
    assert message.get_all("Requires-Dist") == project["dependencies"]


def _report(**installed: str) -> dict:
    return {
        "install": [
            {"metadata": {"name": name.replace("_", "-"), "version": version}}
            for name, version in installed.items()
        ]
    }


_CRYPTOGRAPHY = floors.Scenario(name="example", pins=("cryptography==46.0.7",), why="")


def test_a_resolution_that_keeps_this_release_and_the_pin_passes():
    resolution = floors.judge_report(
        _CRYPTOGRAPHY,
        _report(agents_shipgate="1.3.0", cryptography="46.0.7"),
        distribution="agents-shipgate",
        version="1.3.0",
    )

    assert resolution.problems == ()


def test_a_resolution_that_falls_back_to_an_old_release_fails():
    """The #1028 shape: pip kept the pin by choosing an older Agents Shipgate."""

    resolution = floors.judge_report(
        _CRYPTOGRAPHY,
        _report(agents_shipgate="0.15.0", cryptography="46.0.7"),
        distribution="agents-shipgate",
        version="1.3.0",
    )

    assert resolution.problems == ("agents-shipgate resolved to 0.15.0, not 1.3.0",)


def test_a_resolution_that_moves_the_projects_pin_fails():
    resolution = floors.judge_report(
        _CRYPTOGRAPHY,
        _report(agents_shipgate="1.3.0", cryptography="50.0.2"),
        distribution="agents-shipgate",
        version="1.3.0",
    )

    assert resolution.problems == (
        "cryptography==46.0.7 was not kept: resolved cryptography==50.0.2",
    )


def _index_reachable() -> bool:
    try:
        with socket.create_connection(("pypi.org", 443), timeout=3):
            return True
    except OSError:
        return False


@pytest.mark.slow
def test_pip_installs_this_release_beside_the_pins_agent_projects_carry(
    capsys: pytest.CaptureFixture[str],
):
    """The scripted `pip install --dry-run --report` check, against the real index.

    Skipped, not failed, when the index cannot be reached: an offline run has
    nothing to say about resolution. CI's `dependency-floors` job runs the same
    command on every pull request, where the index is always reachable.
    """

    if not _index_reachable():
        pytest.skip("pypi.org is not reachable; resolution needs the package index")

    status = floors.coinstall()

    output = capsys.readouterr().out
    assert status == 0, output
    assert output.count("OK: ") == len(floors.SCENARIOS), output


# ---------------------------------------------------------------------------
# compile / installed
# ---------------------------------------------------------------------------


def test_the_lowest_direct_input_is_the_declarations_and_the_locked_test_tools():
    text = floors.compile_input()
    lines = [line for line in text.splitlines() if line and not line.startswith("#")]
    locked = parse_lock(REPO_ROOT / floors.DEV_LOCK)

    runtime = [str(floor.requirement) for floor in floors.runtime_floors()]
    assert lines[: len(runtime)] == runtime
    assert lines[len(runtime) :] == [
        f"{tool}=={locked[canonicalize_name(tool)][0].version}" for tool in floors.TEST_TOOLS
    ]


def _lock(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "floors.txt"
    path.write_text(body, encoding="utf-8")
    return path


def test_a_floor_that_something_else_raised_is_named_with_what_raised_it(tmp_path: Path):
    root = _project(tmp_path, ["cryptography>=44.0.3", "typer>=0.16.0,<1"])
    lock = _lock(
        tmp_path,
        "cryptography==46.0.7 \\\n"
        "    --hash=sha256:aaaa\n"
        "    # via\n"
        "    #   -r floors.in\n"
        "    #   example-tool\n"
        "typer==0.16.0 \\\n"
        "    --hash=sha256:bbbb\n"
        "    # via -r floors.in\n",
    )

    (problem,) = floors.floor_problems(lock, floors.runtime_floors(root))

    assert problem.startswith("cryptography resolved to 46.0.7, not its floor 44.0.3")
    assert "example-tool" in problem


def test_a_resolution_at_every_floor_has_no_problems(tmp_path: Path):
    root = _project(tmp_path, ["cryptography>=44.0.3", "typer>=0.16.0,<1"])
    lock = _lock(
        tmp_path,
        "cryptography==44.0.3 \\\n    --hash=sha256:aaaa\n"
        "typer==0.16.0 \\\n    --hash=sha256:bbbb\n",
    )

    assert floors.floor_problems(lock, floors.runtime_floors(root)) == []


def test_the_installed_check_reads_each_floor_from_importlib_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = _project(tmp_path, ["cryptography>=44.0.3", "typer>=0.16.0,<1", "pyyaml>=6.0.2,<7"])
    installed = {"cryptography": "44.0.3", "typer": "0.27.3"}

    def version(name: str) -> str:
        if name not in installed:
            raise floors.metadata.PackageNotFoundError(name)
        return installed[name]

    monkeypatch.setattr(floors.metadata, "version", version)

    assert floors.installed_problems(floors.runtime_floors(root)) == [
        "typer is 0.27.3, not its floor 0.16.0",
        "pyyaml is not installed",
    ]
