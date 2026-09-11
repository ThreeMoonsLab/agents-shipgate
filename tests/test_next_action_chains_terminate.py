"""#650: an emitted next action must lead somewhere.

`control.next_action` is what a coding agent follows, so a next action that
points at a command which refuses — or back at the command that emitted it —
is worse than none at all. On a repository with recognized host
configuration and no manifest, the tool sent a reader round three commands
forever:

    verify --preview  ->  "Next: init --write"
    init --write      ->  refuses (host-only needs no manifest), "Next: audit --host"
    audit --host      ->  "Next: verify --preview"          <- back to the start

These cases follow every emitted chain to a terminal and fail on a repeat.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app

runner = CliRunner()

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE = REPO_ROOT / "samples" / "ai_generated_refund_pr"

#: A chain longer than this is not a chain, it is a maze.
MAX_STEPS = 6

#: Commands a chain may name. An emitted step outside this set is itself a
#: finding: the contract promises a Shipgate command the caller can run.
RUNNABLE = {
    "check", "verify", "init", "audit", "scan", "detect", "preflight", "doctor",
}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def host_only_repo(tmp_path: Path) -> Path:
    """Recognized host configuration, no manifest — the reported shape."""

    repo = tmp_path / "host-only"
    (repo / ".claude").mkdir(parents=True)
    (repo / ".claude" / "settings.json").write_text(
        '{"permissions": {"allow": ["Bash(pytest *)"]}}', encoding="utf-8"
    )
    (repo / ".mcp.json").write_text(
        '{"mcpServers": {"fs": {"command": "npx", "args": ["server-filesystem"]}}}',
        encoding="utf-8",
    )
    (repo / ".gitignore").write_text("agents-shipgate-reports/\n", encoding="utf-8")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    return repo


@pytest.fixture
def manifest_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "manifest"
    repo.mkdir()
    for name in ("shipgate.yaml", "tools.json"):
        (repo / name).write_text((SAMPLE / name).read_text(encoding="utf-8"), encoding="utf-8")
    (repo / ".gitignore").write_text("agents-shipgate-reports/\n", encoding="utf-8")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    return repo


def _emitted_next_command(output: str) -> str | None:
    """The one command this output tells the caller to run next.

    Both surfaces count, because both produced the reported cycle: the
    control envelope an agent reads, and the `Next:` line a person reads.
    Prose advice that names no command is a terminal — it hands the reader
    a decision rather than another lap.
    """

    try:
        payload = json.loads(output)
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        command = ((payload.get("control") or {}).get("next_action") or {}).get("command")
        if command:
            return str(command)
    for line in output.splitlines():
        if line.startswith("Next command: "):
            return line[len("Next command: ") :].strip()
        # Not anchored to end-of-line: the host-audit footer reads
        # "Next: `... --json` for release gating.", and requiring the line to
        # end at the backtick made this reader miss the exact cycle it exists
        # to catch. Verified by replaying against pre-fix `main`.
        matched = re.match(r"Next: `([^`]+)`", line.strip())
        if matched:
            return matched.group(1)
    return None


def _argv(command: str) -> list[str]:
    """The Shipgate argv inside an emitted command string."""

    tokens = shlex.split(command)
    while tokens and tokens[0] not in RUNNABLE:
        tokens = tokens[1:]
    return tokens


#: Flags that change how a result is printed, not which question is asked.
#: The cycle they hid: `audit --host` emitted `verify --preview --json`,
#: which emitted `init --write --json`, which emitted `audit --host --json`.
#: Keyed on raw argv those are four distinct nodes and the walk looks
#: finite; keyed on the question asked it is the reported three-command
#: loop. Verified by replaying against pre-fix `main`.
_PRESENTATION_FLAGS = {"--json", "--format", "--out"}


def _identity(argv: list[str]) -> tuple[str, ...]:
    """What this step *asks*, with presentation stripped."""

    kept: list[str] = []
    skip_value = False
    for token in argv:
        if skip_value:
            skip_value = False
            continue
        if token in _PRESENTATION_FLAGS:
            skip_value = token != "--json"
            continue
        kept.append(token)
    return tuple(kept)


def _workspace_of(argv: list[str]) -> str | None:
    for index, token in enumerate(argv):
        if token == "--workspace" and index + 1 < len(argv):
            return str(Path(argv[index + 1]).resolve())
    return None


def _follow(entry: list[str]) -> list[tuple[str, ...]]:
    """Run ``entry``, then whatever it names, until a terminal.

    Returns the chain actually walked. Raises on a repeat, because a repeat
    is the defect: following the tool's own advice must not return you to a
    command you already ran.
    """

    chain: list[tuple[str, ...]] = []
    argv = entry
    expected_workspace = _workspace_of(entry)
    for _ in range(MAX_STEPS):
        key = _identity(argv)
        assert key not in chain, (
            "emitted next actions cycle: "
            + " -> ".join(" ".join(step) for step in [*chain, _identity(argv)])
        )
        chain.append(key)
        result = runner.invoke(app, argv)
        command = _emitted_next_command(result.output or "")
        if command is None:
            return chain
        argv = _argv(command)
        assert argv, f"emitted a next command this CLI cannot run: {command!r}"
        assert argv[0] in RUNNABLE, (
            f"emitted next command {argv[0]!r} is not a Shipgate command: {command!r}"
        )
        # A step that drops the workspace does not continue this walk — it
        # starts a different one, against whatever directory the caller
        # happens to be in. That is the invocation policy's "silently
        # retarget the authorized operation", emitted by the tool itself,
        # and it is how the reported cycle stayed invisible: the chain
        # wandered into another repository and terminated there.
        if expected_workspace is not None:
            assert _workspace_of(argv) == expected_workspace, (
                f"emitted next command left the audited workspace: {command!r}"
            )
    raise AssertionError(
        f"chain did not terminate within {MAX_STEPS} steps: "
        + " -> ".join(" ".join(step) for step in chain)
    )


ENTRY_POINTS = [
    ["check", "--agent", "claude-code", "--format", "agent-boundary-json"],
    ["verify", "--preview"],
    ["verify", "--preview", "--format", "text"],
    ["init"],
    ["audit", "--host"],
    ["audit", "--host", "--json"],
    ["scan"],
]


@pytest.mark.parametrize(
    "entry", ENTRY_POINTS, ids=lambda entry: "-".join(entry).replace("--", "")
)
def test_every_chain_terminates_on_a_host_only_repository(
    host_only_repo: Path, entry: list[str]
) -> None:
    _follow([*entry, "--workspace", str(host_only_repo)])


@pytest.mark.parametrize(
    "entry", ENTRY_POINTS, ids=lambda entry: "-".join(entry).replace("--", "")
)
def test_every_chain_terminates_on_a_manifest_repository(
    manifest_repo: Path, entry: list[str]
) -> None:
    entry = [*entry, "--workspace", str(manifest_repo)]
    if entry[0] == "scan":
        entry = ["scan", "--config", str(manifest_repo / "shipgate.yaml")]
    _follow(entry)


def test_the_reported_cycle_specifically(host_only_repo: Path) -> None:
    """preview -> init -> audit -> preview, named so a regression is
    recognizable rather than just 'the property test went red'."""

    chain = _follow(["verify", "--preview", "--workspace", str(host_only_repo)])
    commands = [step[0] for step in chain]

    assert commands[0] == "verify"
    assert commands.count("verify") == 1, f"returned to verify: {commands}"
    assert "audit" in commands, f"never reached the host route: {commands}"


def test_the_host_route_is_reached_within_two_steps(host_only_repo: Path) -> None:
    chain = _follow(["verify", "--preview", "--workspace", str(host_only_repo)])

    assert [step[0] for step in chain][:3] == ["verify", "init", "audit"]


def test_a_preview_that_evaluated_nothing_does_not_say_it_failed(
    host_only_repo: Path,
) -> None:
    """"failed" beside "Exit code: 0" was the catch-all word for a run with
    no release decision; the machine fields said `not_run` all along."""

    result = runner.invoke(
        app,
        ["verify", "--preview", "--workspace", str(host_only_repo), "--format", "text"],
    )

    assert "Agents Shipgate verify: failed" not in result.output
    assert "Agents Shipgate verify: not evaluated" in result.output


def test_a_real_failure_is_still_called_a_failure() -> None:
    """The word only moves for runs that did not evaluate."""

    from agents_shipgate.cli.verify.command import _unevaluated_verdict_word

    class _Verifier:
        def __init__(self, head_status: str, execution: str | None = None) -> None:
            self.head_status = head_status
            self.execution = execution

    assert _unevaluated_verdict_word(_Verifier("failed", "failed")) == "failed"
    assert _unevaluated_verdict_word(_Verifier("skipped")) == "skipped"
    assert _unevaluated_verdict_word(_Verifier("not_run", "not_run")) == "not evaluated"
