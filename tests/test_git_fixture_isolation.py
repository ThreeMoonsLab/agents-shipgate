"""A test run that Git started leaves the repository it started from alone.

Git hands every program it starts (a shell alias, a hook, ``git rebase
--exec``, ``git bisect run``) ``GIT_DIR``, and from a linked worktree that is
an absolute path into the shared repository. ``GIT_DIR`` outranks ``-C`` and
``cwd``, so a suite started that way ran each fixture's ``git init``,
``git config`` and ``git add`` in its temporary directory against the
developer's repository. On 2026-11-20 a full run from a linked worktree set
``core.bare = true``, a ``Test`` identity and ``commit.gpgsign = false`` in
the shared ``.git/config``, which broke Git in every worktree until it was
repaired by hand, and replaced that worktree's index with the fixture's one
file. ``test_check_fixtures_ignore_external_excludes[command]`` starts the
suite through a Git alias, so any full run from a linked worktree did this.

The root ``conftest.py`` now drops every inherited ``GIT_*`` variable before
any fixture runs. These tests pin that:

* no inherited ``GIT_*`` variable reaches a test, only the configuration the
  conftest itself sets;
* a suite a Git alias starts in a linked worktree of a throwaway repository,
  running a fixture that builds a two-commit range, leaves that repository's
  shared config, the worktree's index and its ``HEAD`` as they were.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: What ``_isolate_git_configuration`` in the root conftest sets for each test.
_SUITE_GIT_CONFIGURATION = frozenset(
    {
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_KEY_0",
        "GIT_CONFIG_VALUE_0",
    }
)

#: The fixture that corrupted the shared config: ``_repo`` runs ``git init``,
#: ``git config``, ``git add``, ``git commit`` and ``git checkout -b``.
_RANGE_FIXTURE_NODE = (
    f"{REPO_ROOT / 'tests' / 'test_check_unmodelled_host_config_keys.py'}"
    "::test_check_answers_an_unmodelled_settings_key_in_every_format"
    "[local_settings_enabled_plugins-git_range]"
)
_PROBE_NODE = f"{Path(__file__).resolve()}::test_no_inherited_git_variable_reaches_a_test"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


def test_no_inherited_git_variable_reaches_a_test() -> None:
    stray = sorted(
        name
        for name in os.environ
        if name.startswith("GIT_") and name not in _SUITE_GIT_CONFIGURATION
    )
    assert stray == [], f"inherited Git variables reached a test: {stray}"


def test_a_suite_git_starts_in_a_linked_worktree_leaves_that_repository_alone(
    tmp_path: Path,
) -> None:
    shared = tmp_path / "shared"
    linked = tmp_path / "linked"
    _git(tmp_path, "init", "-q", "-b", "main", str(shared))
    (shared / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    _git(shared, "add", "tracked.txt")
    _git(
        shared,
        "-c", "user.name=Owner", "-c", "user.email=owner@example.test",
        "-c", "commit.gpgsign=false",
        "commit", "-qm", "base",
    )
    _git(shared, "worktree", "add", "-q", "-b", "linked", str(linked))
    config = shared / ".git" / "config"
    config_before = config.read_text(encoding="utf-8")
    index_before = _git(linked, "ls-files", "--stage")
    head_before = _git(linked, "rev-parse", "HEAD")

    child_env = {
        name: value
        for name, value in os.environ.items()
        if name not in {"PYTEST_ADDOPTS", "SHIPGATE_TEST_SHARD", "SHIPGATE_TEST_SHARDS"}
    }

    def run_alias(command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "git", "-C", str(linked),
                "-c", f"alias.shipgate-fixture=!{shlex.join(command)}", "shipgate-fixture",
            ],
            env=child_env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    # The hazard this guards against: Git points the alias at the shared
    # repository. Without it this test would pass however the suite behaved.
    probe = run_alias([sys.executable, "-c", "import os; print(os.environ.get('GIT_DIR', ''))"])
    assert probe.returncode == 0, probe.stderr
    handed = probe.stdout.strip()
    assert handed and Path(handed).resolve().is_relative_to((shared / ".git").resolve()), handed

    result = run_alias(
        [
            sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
            _PROBE_NODE, _RANGE_FIXTURE_NODE,
        ]
    )

    assert config.read_text(encoding="utf-8") == config_before, (
        "the suite wrote to the shared config of the repository it was started from"
    )
    assert _git(linked, "ls-files", "--stage") == index_before
    assert _git(linked, "rev-parse", "HEAD") == head_before
    assert result.returncode == 0, result.stdout + result.stderr
