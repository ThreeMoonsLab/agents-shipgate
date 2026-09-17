"""#817: `diff` in a partial clone names the hydration, never a traceback.

A `git clone --filter=blob:none` holds the blobs of what it checked out and no
others; a `--filter=tree:0` clone holds no trees beyond that either. `diff`
reads its base side from Git with lazy fetching disabled, so in such a clone
the base tree's objects are missing, and the object copy escaped as a
`ConfigError` (or, treeless, a `CalledProcessError`) traceback: exit 1, nothing
on stdout, nothing in agent mode to route on.

It now refuses the way the shallow case does (#683): exit 2, one message naming
the side it could not read, and in agent mode an `objects_missing` line whose
next action is the hydration command. The wording is `verify`'s own for the
same reason, and its example no longer keeps the clone's filter.

Each recovery is followed, not only printed: the published command is run and
the same comparison repeated. A recovery nobody ran is a guess, and the one
`verify` used to print (`git fetch --refetch origin`) was a wrong one.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.cli.verify.git import (
    DEFAULT_HYDRATE_COMMAND,
    objects_missing_remediation,
    promised_objects_missing,
    promisor_remote,
)

runner = CliRunner()

REPO_ROOT = Path(__file__).resolve().parent.parent
SETTINGS = ".claude/settings.json"
BASE_SETTINGS = '{"permissions": {"allow": ["Read"], "deny": ["Bash(git push *)"]}}\n'
HEAD_SETTINGS = '{"permissions": {"allow": ["Read", "Bash(git push *)"]}}\n'

#: Git that ignores the caller's repository, hooks, global configuration and any
#: `GIT_NO_LAZY_FETCH` the caller exported: the fixture and every recovery run
#: the way an operator's shell would, so a fetch they allow is really allowed.
_GIT_ENV = {
    **{key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}


def _git(cwd: Path, *args: str, check: bool = True, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
         "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main",
         "-c", f"core.hooksPath={os.devnull}", *args],
        cwd=cwd, check=check, capture_output=True, text=True, env={**_GIT_ENV, **env},
    )


@pytest.fixture
def origin(tmp_path: Path) -> Path:
    """The issue's repository: `base` denies `git push`, `main` allows it."""

    repo = tmp_path / "origin"
    (repo / ".claude").mkdir(parents=True)
    _git(tmp_path, "init", "-q", str(repo))
    _git(repo, "config", "uploadpack.allowFilter", "true")
    (repo / SETTINGS).write_text(BASE_SETTINGS, encoding="utf-8")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "branch", "base")
    (repo / SETTINGS).write_text(HEAD_SETTINGS, encoding="utf-8")
    # A file outside the host surface changes too: the base owns blobs no
    # checkout of the head ever fetches, on and off the surface.
    (repo / "README.md").write_text("head\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "head")
    return repo


def _partial_clone(
    origin: Path, name: str, filter_spec: str = "blob:none", *extra: str
) -> Path:
    """A partial clone hydrated by a checkout of the head, and nothing else."""

    clone = origin.parent / name
    _git(origin.parent, "clone", "-q", f"--filter={filter_spec}", "--no-checkout",
         *extra, origin.as_uri(), str(clone))
    remote = _git(clone, "remote").stdout.split()[0]
    applied = _git(
        clone, "config", "--get", f"remote.{remote}.partialclonefilter", check=False
    ).stdout.strip()
    if applied != filter_spec:  # pragma: no cover - host Git policy
        pytest.skip(f"local Git did not apply the {filter_spec} partial-clone filter")
    _git(clone, "checkout", "-q", "main")
    return clone


def _missing_objects(clone: Path, ref: str) -> list[str]:
    """Objects `ref`'s tree needs that this clone does not hold, read without fetching."""

    walk = _git(clone, "rev-list", "--objects", "--no-walk", "--missing=print", ref,
                GIT_NO_LAZY_FETCH="1")
    return sorted(line for line in walk.stdout.splitlines() if line.startswith("?"))


def _object_store(clone: Path) -> list[str]:
    root = clone / ".git" / "objects"
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))


def _agent_lines(stderr: str) -> list[dict]:
    return [json.loads(line) for line in stderr.splitlines() if line.startswith("{")]


def _diff(clone: Path, *args: str, agent_mode: str = "0"):
    return runner.invoke(
        app,
        ["diff", "--workspace", str(clone), *args],
        env={"AGENTS_SHIPGATE_AGENT_MODE": agent_mode},
    )


def _compared_rows(clone: Path, *args: str) -> list[dict]:
    result = _diff(clone, *args, "--json")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["comparison_status"] == "comparable", payload
    return payload["rows"]


# --- the refusal ------------------------------------------------------------


@pytest.mark.parametrize("filter_spec", ["blob:none", "tree:0"])
@pytest.mark.parametrize("json_output", [False, True])
@pytest.mark.parametrize("agent_mode", ["0", "1"])
def test_a_base_the_partial_clone_never_fetched_is_refused_with_its_hydration(
    origin: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    filter_spec: str,
    json_output: bool,
    agent_mode: str,
) -> None:
    # Hosted CI enables Rich color; the recovery must still be one copyable line.
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setenv("COLUMNS", "60")
    clone = _partial_clone(origin, "partial clone", filter_spec)
    missing = _missing_objects(clone, "origin/base")
    assert missing, "the fixture must leave base objects unfetched"
    store = _object_store(clone)

    result = _diff(
        clone, "--base", "origin/base", *(["--json"] if json_output else []),
        agent_mode=agent_mode,
    )

    assert result.exit_code == 2, result.output
    assert "Traceback" not in result.output
    assert result.stdout == ""
    message = result.stderr.splitlines()[0]
    base_commit = _git(clone, "rev-parse", "origin/base").stdout.strip()
    assert message.startswith(
        f"The base side of this diff, origin/base ({base_commit[:8]}), "
        "could not be read (objects_missing). "
    )
    recovery = [
        "git", "-C", str(clone), "fetch", "--refetch", "--no-filter", "origin"
    ]
    # One wording for one reason: `verify` says the same, with the example
    # command bound to this workspace.
    assert message.endswith(objects_missing_remediation(shlex.join(recovery)))
    errors = _agent_lines(result.stderr)
    if agent_mode == "1":
        assert len(errors) == 1
        (error,) = errors
        assert error["error"] == "objects_missing"
        assert error["exit_code"] == 2
        assert error["message"] == message
        (action,) = error["next_actions"]
        assert action["kind"] == "command"
        assert shlex.split(action["command"]) == recovery
        assert action["executable"] == ["git"] and action["args"] == recovery[1:]
        assert error["next_action"] == action["command"]
    else:
        assert errors == []

    # Nothing was fetched to get here, and nothing was written.
    assert _missing_objects(clone, "origin/base") == missing
    assert _object_store(clone) == store

    # Follow the published command from outside the checkout. It must restore
    # the same comparison, with no second fetch, init or wrapper.
    subprocess.run(recovery, cwd=tmp_path, check=True, capture_output=True, env=_GIT_ENV)
    assert _missing_objects(clone, "origin/base") == []
    rows = _compared_rows(clone, "--base", "origin/base")
    assert any(row["after"] == "Bash(git push *)" for row in rows), rows


def test_the_error_kind_and_exit_code_are_the_published_ones(origin: Path) -> None:
    catalog = json.loads((REPO_ROOT / "docs" / "errors.json").read_text(encoding="utf-8"))
    entry = next(item for item in catalog["errors"] if item["id"] == "objects_missing")
    clone = _partial_clone(origin, "partial")

    result = _diff(clone, "--base", "origin/base", "--json", agent_mode="1")

    (error,) = _agent_lines(result.stderr)
    assert result.exit_code == entry["exit_code"] == error["exit_code"] == 2
    assert set(error) - {"error", "command"} <= set(entry["additional_fields"]) | {"exit_code"}


@pytest.mark.skipif(os.name != "posix", reason="the probe remote runs `touch`")
def test_diff_never_asks_the_promisor_remote_for_what_is_missing(
    origin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Shipgate sets `GIT_NO_LAZY_FETCH` itself; it does not rely on the caller.

    The promisor remote is replaced by one that records being contacted, and
    the caller's environment leaves lazy fetching on.
    """

    clone = _partial_clone(origin, "partial")
    marker = tmp_path / "promisor-contacted"
    _git(clone, "config", "protocol.ext.allow", "always")
    _git(clone, "config", "remote.origin.url", f"ext::touch {marker}")
    monkeypatch.delenv("GIT_NO_LAZY_FETCH", raising=False)

    result = _diff(clone, "--base", "origin/base", agent_mode="1")

    assert result.exit_code == 2, result.output
    assert _agent_lines(result.stderr)[0]["error"] == "objects_missing"
    assert not marker.exists()
    # The probe is live: an ordinary read of the same object does contact it.
    probe = _git(clone, "cat-file", "-p", f"origin/base:{SETTINGS}", check=False)
    assert probe.returncode != 0
    assert marker.exists()


def test_the_command_names_the_remote_the_clone_was_promised_by(
    origin: Path, tmp_path: Path
) -> None:
    clone = _partial_clone(origin, "partial", "blob:none", "--origin", "upstream")
    assert promisor_remote(clone) == "upstream"

    result = _diff(clone, "--base", "upstream/base", agent_mode="1")

    assert result.exit_code == 2, result.output
    command = shlex.split(_agent_lines(result.stderr)[0]["next_actions"][0]["command"])
    assert command[-1] == "upstream"
    subprocess.run(command, cwd=tmp_path, check=True, capture_output=True, env=_GIT_ENV)
    assert any(
        row["after"] == "Bash(git push *)"
        for row in _compared_rows(clone, "--base", "upstream/base")
    )


def test_an_object_missing_without_a_promise_is_not_called_a_partial_clone(
    origin: Path,
) -> None:
    """Corruption is not a partial clone, and hydrating is not its repair."""

    blob = _git(origin, "rev-parse", f"base:{SETTINGS}").stdout.strip()
    loose = origin / ".git" / "objects" / blob[:2] / blob[2:]
    assert loose.is_file()
    loose.unlink()
    base_commit = _git(origin, "rev-parse", "base").stdout.strip()

    assert promised_objects_missing(origin, base_commit) is False
    result = _diff(origin, "--base", "base", agent_mode="1")
    assert result.exit_code != 0
    assert "objects_missing" not in result.output


def test_an_ambiguous_promisor_is_not_guessed(origin: Path) -> None:
    clone = _partial_clone(origin, "partial")
    _git(clone, "remote", "add", "mirror", origin.as_uri())
    _git(clone, "config", "remote.mirror.promisor", "true")

    assert promisor_remote(clone) is None


# --- one wording, and an example that works ---------------------------------


def test_verify_preview_names_the_same_repair_and_its_example_hydrates(
    origin: Path, tmp_path: Path
) -> None:
    clone = _partial_clone(origin, "partial")

    def preview_status() -> dict:
        out = tmp_path / "preview-out"
        result = runner.invoke(
            app,
            ["verify", "--workspace", str(clone), "--preview", "--base", "origin/base",
             "--json", "--out", str(out)],
            env={"AGENTS_SHIPGATE_AGENT_MODE": "0"},
        )
        assert result.exit_code == 0, result.output
        return json.loads(result.stdout)["diff_status"]

    status = preview_status()
    assert status["reason"] == "objects_missing"
    assert status["remediation"] == objects_missing_remediation()
    refusal = _diff(clone, "--base", "origin/base").stderr.splitlines()[0]
    bound = shlex.join(
        ["git", "-C", str(clone), "fetch", "--refetch", "--no-filter", "origin"]
    )
    assert refusal.replace(bound, DEFAULT_HYDRATE_COMMAND).endswith(status["remediation"])

    # The example `verify` used to print keeps the clone's configured filter:
    # it refetches every commit and tree and still no blob.
    _git(clone, "fetch", "-q", "--refetch", "origin")
    assert _diff(clone, "--base", "origin/base").exit_code == 2
    assert preview_status()["reason"] == "objects_missing"

    # The example it prints now is the repair, run as written in the checkout.
    example = re.search(r"for example `([^`]+)`", status["remediation"])
    assert example is not None and example.group(1) == DEFAULT_HYDRATE_COMMAND
    subprocess.run(
        shlex.split(example.group(1)), cwd=clone, check=True, capture_output=True, env=_GIT_ENV
    )
    hydrated = preview_status()
    assert hydrated["completeness"] == "complete", hydrated
    assert hydrated["reason"] is None
    assert any(
        row["after"] == "Bash(git push *)"
        for row in _compared_rows(clone, "--base", "origin/base")
    )


# --- controls ----------------------------------------------------------------


@pytest.mark.parametrize("filter_spec", ["blob:none", "tree:0"])
def test_a_partial_clone_compares_against_a_base_it_already_holds(
    origin: Path, filter_spec: str
) -> None:
    """Only a base the clone lacks is refused: the checkout hydrated `HEAD`."""

    clone = _partial_clone(origin, "partial", filter_spec)
    (clone / SETTINGS).write_text(
        '{"permissions": {"allow": ["Read", "Bash(git push *)", "WebFetch(*)"]}}\n',
        encoding="utf-8",
    )

    rows = _compared_rows(clone, "--base", "HEAD")

    assert [row["after"] for row in rows] == ["WebFetch(*)"], rows


def test_a_shallow_partial_clone_is_refused_as_shallow_first(
    origin: Path, tmp_path: Path
) -> None:
    """History is repaired before objects, and each refusal names its own repair."""

    base_commit = _git(origin, "rev-parse", "base").stdout.strip()
    clone = tmp_path / "shallow partial"
    _git(tmp_path, "clone", "-q", "--depth", "1", "--filter=blob:none",
         origin.as_uri(), str(clone))

    shallow = _diff(clone, "--base", base_commit, "--json", agent_mode="1")

    assert shallow.exit_code == 2, shallow.output
    assert "This checkout is shallow" in shallow.stderr
    (error,) = _agent_lines(shallow.stderr)
    assert error["error"] == "config_error"
    unshallow = shlex.split(error["next_actions"][0]["command"])
    assert unshallow == ["git", "-C", str(clone), "fetch", "--unshallow"]

    subprocess.run(unshallow, cwd=tmp_path, check=True, capture_output=True, env=_GIT_ENV)
    blobless = _diff(clone, "--base", base_commit, "--json", agent_mode="1")

    assert blobless.exit_code == 2, blobless.output
    (error,) = _agent_lines(blobless.stderr)
    assert error["error"] == "objects_missing"
    hydrate = shlex.split(error["next_actions"][0]["command"])
    subprocess.run(hydrate, cwd=tmp_path, check=True, capture_output=True, env=_GIT_ENV)
    assert any(
        row["after"] == "Bash(git push *)"
        for row in _compared_rows(clone, "--base", base_commit)
    )


# --- the other commands in the same clone ------------------------------------


@pytest.mark.parametrize("output_format", ["agent-boundary-json", "agent-control-json", "text"])
def test_check_in_the_same_clone_stays_structured_and_fail_closed(
    origin: Path, output_format: str
) -> None:
    clone = _partial_clone(origin, "partial")

    result = runner.invoke(
        app,
        ["check", "--workspace", str(clone), "--base", "origin/base",
         "--format", output_format],
        env={"AGENTS_SHIPGATE_AGENT_MODE": "1"},
    )

    assert result.exit_code == 0, result.output
    assert "Traceback" not in result.output
    if output_format == "text":
        assert "human_review_required" in result.stdout
        assert "You may: nothing until this is resolved" in result.stdout
        return
    payload = json.loads(result.stdout)
    control = payload["control"] if output_format == "agent-boundary-json" else payload
    state = control["state"] if output_format == "agent-boundary-json" else control["control_state"]
    assert state == "human_review_required"
    assert not any(control["permissions"].values()), control["permissions"]


def test_verify_in_the_same_clone_stays_structured_and_fail_closed(
    origin: Path, tmp_path: Path
) -> None:
    clone = _partial_clone(origin, "partial")

    result = runner.invoke(
        app,
        ["verify", "--workspace", str(clone), "--base", "origin/base", "--json",
         "--out", str(tmp_path / "verify-out")],
        env={"AGENTS_SHIPGATE_AGENT_MODE": "1"},
    )

    assert result.exit_code == 0, result.output
    assert "Traceback" not in result.output
    payload = json.loads(result.stdout)
    assert payload["diff_status"]["reason"] == "objects_missing"
    assert payload["diff_status"]["remediation"] == objects_missing_remediation()
    assert payload["can_merge_without_human"] is False
    assert not any(payload["control"]["permissions"].values())
