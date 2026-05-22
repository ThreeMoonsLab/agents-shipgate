"""Contract test for the harness layout convention (E6).

Pins the rules documented in ``harness/README.md`` so a new harness
family that misses one of the three required files fails the test
loudly. The test is parametrized over :func:`discover_harnesses()` so
new families are picked up automatically — there is no wiring step
when a new family is added.

What this test does NOT pin:

- The internal shape of any family (drivers, scorers, observers, etc.
  are family-private).
- The Typer / argparse choice — ``app`` only has to be callable.
- The packaged-vs-unpackaged decision (pyproject.toml drives that).

The rules pinned here are exactly the three documented in
``harness/__init__.py`` and ``harness/README.md``. Keep this test, the
discovery code, and the README in sync — any change to one should
update the other two in the same commit.
"""
from __future__ import annotations

import subprocess
import sys

import pytest

from harness import HARNESS_DIR, HarnessSpec, discover_harnesses

# Families that MUST be present. Updated when a new family lands. The
# whole point of this guard is to catch an accidental removal — e.g.
# someone renaming ``harness/adoption/`` without updating the
# dispatcher would leave ``discover_harnesses()`` empty and we want
# the test to fail loudly.
_EXPECTED_FAMILIES: frozenset[str] = frozenset({"adoption"})

_DISCOVERED: list[HarnessSpec] = discover_harnesses()


def test_discover_harnesses_returns_at_least_expected_set() -> None:
    """``discover_harnesses()`` must return EVERY known family.

    Guards against an accidental refactor that breaks the discovery
    walk (e.g. moving ``cli.py`` to a different filename, deleting
    ``__init__.py``, or shipping a family without a docstring).
    """
    discovered_names = {spec.name for spec in _DISCOVERED}
    missing = _EXPECTED_FAMILIES - discovered_names
    assert not missing, (
        f"discover_harnesses() did not find expected harness "
        f"families: {sorted(missing)}. Either the layout convention "
        f"regressed or _EXPECTED_FAMILIES is stale."
    )


def test_discover_harnesses_is_sorted_deterministic() -> None:
    """Ordering is part of the contract — ``python -m harness list``
    and parametrized tests rely on it being deterministic."""
    names = [spec.name for spec in _DISCOVERED]
    assert names == sorted(names), (
        f"discover_harnesses() returned non-sorted names: {names}. "
        f"The sort step in harness/__init__.py was removed?"
    )


@pytest.mark.parametrize(
    "spec",
    _DISCOVERED,
    ids=lambda s: s.name,
)
def test_every_harness_subpackage_conforms(spec: HarnessSpec) -> None:
    """Rule 1, 2, 3 from harness/README.md — every discovered family
    must satisfy all three.

    Rule 1: ``__init__.py`` exists with a non-empty docstring.
    Rule 2: ``cli.py`` exists and exposes ``app`` (callable).
    Rule 3: ``__main__.py`` exists.

    The three rules are tested together so the failure message names
    the family and tells the reviewer exactly which file is missing
    or malformed. ``discover_harnesses()`` already filters out
    non-conforming directories — so this test catches the case where
    a family is *partially* compliant (passes discovery but is
    missing one of the contract files).
    """
    package_dir = spec.package_dir
    name = spec.name

    init_path = package_dir / "__init__.py"
    assert init_path.exists(), (
        f"harness/{name}/__init__.py is missing (rule 1)"
    )
    init_doc = (init_path.read_text(encoding="utf-8") or "").strip()
    assert init_doc, (
        f"harness/{name}/__init__.py is empty (rule 1 requires a "
        f"non-empty docstring)"
    )
    assert spec.description, (
        f"harness/{name}/__init__.py docstring's first line is empty "
        f"(rule 1 requires it to be the one-line family description)"
    )

    cli_path = package_dir / "cli.py"
    assert cli_path.exists(), (
        f"harness/{name}/cli.py is missing (rule 2)"
    )
    assert callable(spec.app), (
        f"harness/{name}/cli.py:app is not callable (rule 2 requires "
        f"a zero-arg callable, typically a typer.Typer instance)"
    )

    main_path = package_dir / "__main__.py"
    assert main_path.exists(), (
        f"harness/{name}/__main__.py is missing (rule 3). The "
        f"dispatcher forwards via ``python -m harness.{name}`` which "
        f"requires __main__.py to exist."
    )


def test_harness_dir_constant_points_at_real_directory() -> None:
    """``HARNESS_DIR`` is exposed as part of the public API. Tests and
    tooling rely on it pointing at ``<repo>/harness``."""
    assert HARNESS_DIR.is_dir()
    assert HARNESS_DIR.name == "harness"
    # The discovery walk is rooted here; confirm the canonical family
    # is present at the expected path.
    assert (HARNESS_DIR / "adoption" / "__init__.py").exists()


def _run_dispatcher(*args: str) -> subprocess.CompletedProcess[str]:
    """Run ``python -m harness ARGS`` from the repo root.

    Uses the same Python the test is running under and captures
    stdout/stderr/exit. Pin ``cwd`` to the repo root so the
    dispatcher's ``sys.path`` bootstrap behaves identically to a
    developer's manual invocation.
    """
    repo_root = HARNESS_DIR.parent
    return subprocess.run(
        [sys.executable, "-m", "harness", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )


def test_dispatcher_no_args_prints_usage_and_exits_zero() -> None:
    """``python -m harness`` (no args) prints usage and exits 0.

    The usage block must mention every discovered family by name so
    a developer running the bare command can see what's available
    without consulting docs.
    """
    result = _run_dispatcher()
    assert result.returncode == 0, result.stderr
    assert "Usage: python -m harness" in result.stdout
    assert "Discovered harness families:" in result.stdout
    for spec in _DISCOVERED:
        assert spec.name in result.stdout, (
            f"family {spec.name!r} missing from dispatcher usage output"
        )


def test_dispatcher_help_flag_matches_no_args() -> None:
    """``--help`` / ``-h`` / ``help`` all produce the same usage."""
    bare = _run_dispatcher().stdout
    for flag in ("--help", "-h", "help"):
        result = _run_dispatcher(flag)
        assert result.returncode == 0, (
            f"`python -m harness {flag}` exit={result.returncode}: "
            f"{result.stderr}"
        )
        assert result.stdout == bare, (
            f"`python -m harness {flag}` output diverged from bare "
            f"invocation"
        )


def test_dispatcher_list_emits_tab_separated_lines() -> None:
    """``python -m harness list`` is the pipe-friendly enumeration:
    one ``<name>\\t<description>`` line per discovered family, sorted.
    """
    result = _run_dispatcher("list")
    assert result.returncode == 0, result.stderr
    lines = [
        line for line in result.stdout.splitlines() if line.strip()
    ]
    assert len(lines) == len(_DISCOVERED), (
        f"list output line count {len(lines)} != discovered family "
        f"count {len(_DISCOVERED)}"
    )
    for spec, line in zip(_DISCOVERED, lines, strict=True):
        name, _, description = line.partition("\t")
        assert name == spec.name
        assert description == spec.description


def test_dispatcher_unknown_name_exits_two_with_helpful_error() -> None:
    """A non-existent harness name exits 2 (config-error convention)
    and the stderr message names available alternatives."""
    result = _run_dispatcher("definitely-not-a-real-harness")
    assert result.returncode == 2, (
        f"expected exit 2 for unknown harness, got {result.returncode}: "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "no harness named" in result.stderr
    assert "available:" in result.stderr
    # The error must list every real family so the developer can
    # correct the typo without re-running ``--help``.
    for spec in _DISCOVERED:
        assert spec.name in result.stderr, (
            f"unknown-harness error did not list available family "
            f"{spec.name!r}"
        )


def test_dispatcher_forwards_to_adoption_help() -> None:
    """``python -m harness adoption --help`` must produce help text
    indistinguishable (modulo TTY width) from ``python -m
    harness.adoption --help``. This is the load-bearing user
    expectation — if the prog-name diverges, the dispatcher has
    leaked a sys.argv mutation into the child.
    """
    dispatched = _run_dispatcher("adoption", "--help")
    assert dispatched.returncode == 0, dispatched.stderr
    repo_root = HARNESS_DIR.parent
    direct = subprocess.run(
        [sys.executable, "-m", "harness.adoption", "--help"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert direct.returncode == 0, direct.stderr
    # Both invocations must show the same prog-name in usage.
    # Sample a stable substring to avoid TTY-width line wrapping.
    expected = "python -m harness.adoption"
    assert expected in dispatched.stdout, (
        f"dispatched help does not show {expected!r}; got:\n"
        f"{dispatched.stdout[:500]}"
    )
    assert expected in direct.stdout, (
        f"direct help does not show {expected!r}; got:\n"
        f"{direct.stdout[:500]}"
    )


def test_excluded_subpackage_names_do_not_appear_as_harnesses() -> None:
    """``tests`` is reserved and any ``_``-prefixed subpackage is
    private. A future ``harness/tests/`` or ``harness/_helpers/``
    must NOT be discovered as a family.
    """
    for spec in _DISCOVERED:
        assert not spec.name.startswith("_"), (
            f"private subpackage {spec.name!r} should not have been "
            f"discovered as a harness family"
        )
        assert spec.name != "tests", (
            "reserved name 'tests' was discovered as a harness family"
        )


def test_no_partial_harness_subpackages_exist() -> None:
    """Every NON-PRIVATE subpackage under ``harness/`` must conform to
    the convention (or it should be renamed with a leading underscore).

    Without this test, a half-finished or malformed family
    (``__init__.py`` present but ``cli.py`` missing ``app``, or
    ``__main__.py`` deleted in a refactor) would be silently skipped
    by ``discover_harnesses()`` — invisible to the dispatcher and to
    the parametrized contract test above. This walk catches the
    "partial family" case loudly, as the README promises.

    To opt a subpackage out (internal helper, work in progress):
    prefix its name with ``_`` (e.g., ``_wip_perf_regression``). The
    discovery walk and this contract test both honor the prefix
    convention; underscored names are excluded structurally.
    """
    discovered_names = {spec.name for spec in _DISCOVERED}
    excluded = {"tests"}
    partial: list[tuple[str, str]] = []  # (name, reason)

    for child in sorted(HARNESS_DIR.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith("_") or child.name in excluded:
            continue
        if child.name == "__pycache__":
            continue
        if not (child / "__init__.py").exists():
            # Not a Python package at all; ignored by both discovery
            # and this test (matches pkgutil.iter_modules behavior).
            continue
        if child.name in discovered_names:
            # Already covered by the parametrized
            # test_every_harness_subpackage_conforms test above.
            continue
        # Reached here ⇒ this directory looks like an intentional
        # subpackage (has __init__.py, public name) but discovery
        # didn't pick it up. That means at least one rule is broken.
        cli = child / "cli.py"
        if not cli.exists():
            partial.append((child.name, "missing cli.py (rule 2)"))
            continue
        init_doc = (child / "__init__.py").read_text(encoding="utf-8").strip()
        if not init_doc:
            partial.append(
                (child.name, "__init__.py has no docstring (rule 1)")
            )
            continue
        if "app" not in cli.read_text(encoding="utf-8"):
            partial.append(
                (child.name, "cli.py does not define ``app`` (rule 2)")
            )
            continue
        partial.append(
            (child.name, "passed file checks but discovery rejected it")
        )

    assert not partial, (
        "Partial harness subpackages found under harness/. Either "
        "complete them (add the missing file), make them private "
        "(rename to ``_<name>``), or delete them. Offenders:\n  "
        + "\n  ".join(f"{name}: {reason}" for name, reason in partial)
    )


def test_readme_is_present_and_documents_the_convention() -> None:
    """A ``harness/README.md`` is the author-facing doc for the
    convention. Its presence is part of the contract — a new
    contributor adding a harness family will look here first.
    """
    readme = HARNESS_DIR / "README.md"
    assert readme.exists(), "harness/README.md is missing"
    text = readme.read_text(encoding="utf-8")
    for required_phrase in (
        "harness families",
        "Convention",
        "cli.py",
        "__main__.py",
        "discover_harnesses",
        "python -m harness",
    ):
        assert required_phrase in text, (
            f"harness/README.md is missing the required phrase "
            f"{required_phrase!r} — keep README, discovery code, and "
            f"this contract test in sync."
        )
