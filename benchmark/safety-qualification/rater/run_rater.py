"""Run one blind rater session over one packet and archive what it did.

One run is one fresh agent session that receives a packet built by
:mod:`build_packet` — and nothing else — and ends with one
``IndependentHumanLabelV1``. The two model families share this code path;
they differ only in the command line and in how the final message is pulled
out of the transcript::

    <out>/
      transcripts/<sha256>.jsonl      the complete stdout event stream
      transcripts/<sha256>.stderr.txt  what the CLI wrote to stderr
      labels/<case-id>.<role>.json     the validated label + transcript_sha256

Amendment 1 condition 2 — blindness, enforced mechanically:

- **No project memory.** The Claude CLI reads ``~/.claude`` (auto-memory
  under ``projects/<cwd>/memory``, the user ``CLAUDE.md``, ``settings.json``)
  and ``~/.claude.json``. Two ``--home-mode`` settings close that:

  - ``isolated`` (default): ``HOME`` is an empty directory created for the
    run and the CLI runs ``--bare`` (no hooks, no plugins, no auto-memory,
    no ``CLAUDE.md`` discovery, no keychain). ``--bare`` authenticates only
    through ``ANTHROPIC_API_KEY``, so the runner refuses up front when that
    is unset rather than launching a session that cannot log in — an
    OAuth credential is bound to the real ``HOME`` and cannot be carried
    into an empty one (verified on 2.1.126: an identity-only copy of
    ``~/.claude.json`` still reports "Not logged in").
  - ``shared``: ``HOME`` stays the caller's, for OAuth. The runner then
    checks the exact files the CLI would read and refuses if any exists:
    ``~/.claude/CLAUDE.md``, and ``~/.claude/projects/<encoded packet
    path>/`` (where auto-memory and prior sessions for that directory
    live). Settings are still cut off with ``--setting-sources ""``.

  In both modes ``--setting-sources ""`` loads no settings file from
  anywhere, ``--no-session-persistence`` writes nothing back, the working
  directory is the packet (which the runner checks carries no ``CLAUDE.md``
  and no ``.claude/``), and the label record names the mode used.
- **No network, no other files.** The tool set is restricted to ``Read``,
  ``Grep`` and ``Glob`` (``--tools`` removes every other built-in tool
  including ``Bash``, ``WebFetch`` and ``WebSearch``; ``--allowedTools`` and
  ``--disallowedTools`` say it again at the permission layer), MCP servers are
  disabled with ``--strict-mcp-config`` and no config, and the permission mode
  is ``dontAsk`` so nothing outside the allowlist can be approved
  interactively. The environment passed to the CLI is rebuilt from a short
  allowlist, so no ``CLAUDE_*`` or ``ANTHROPIC_*`` variable that changes
  behaviour leaks in — only the credential needed to authenticate.
- **The packet is re-hashed immediately before launch.** What
  :mod:`build_packet` guaranteed is a property of the packet *as built*; a
  session is given the packet *as it stands now*. Between those two moments a
  file can be edited, so :func:`prepare` re-hashes every file and compares it
  with ``MANIFEST.json``, and a mismatch refuses the run. Without that the
  label record would bind the hash of a manifest describing a packet that no
  longer exists, and a contaminated packet would produce an admissible label.
- **The OpenAI family gets its own Codex home.** ``codex`` reads global
  ``AGENTS.md`` and ``AGENTS.override.md`` from ``$CODEX_HOME``, and its
  ``config.toml`` there can mount MCP servers and enable web search — so
  pointing ``CODEX_HOME`` at the caller's real profile would reopen every
  door ``HOME`` was replaced to close. ``isolated`` builds a fresh Codex home
  per run holding one written ``config.toml`` (no MCP servers, web search
  off, read-only sandbox, no approvals) and authenticates through
  ``OPENAI_API_KEY``; ``shared`` keeps the real one only after checking it
  carries no global instruction file and configures no MCP server, web
  search, or instructions override.
- **No instruction file above the packet either.** Both CLIs discover project
  instructions by walking *up* from the working directory. ``--bare`` turns
  that off, so it is a shared-mode concern only, and
  :func:`check_no_ancestor_instructions` refuses the same names
  :func:`check_packet_carries_no_instructions` refuses at the packet root when
  they appear on the path leading to it. A packet built inside a checkout --
  which the calibration round's own commands invite, since they name a packet
  directory and not where it lives -- would otherwise put that project's
  ``CLAUDE.md`` and ``AGENTS.md`` into a rater's context.
- **No verifier output, no other label.** The packet contains none, by
  construction (:mod:`build_packet`); this runner adds none.

Before any of that is put to use, :func:`probe_cli` runs the family's CLI once
and refuses if it cannot report a version. The two harnesses were written
against CLIs this project could not run, and an unrunnable CLI does not fail
like "no admissible label": a missing binary raises from inside the run, a
launcher whose vendored binary is absent exits with a Node stack trace, and a
binary macOS refuses to load is killed by a signal with no output at all. The
version it reports is the fallback attribution for a client whose transcript
does not name itself.

Condition 1 — two model families: :func:`claim_family` reserves the case for
this family with ``O_EXCL`` and *then* reads the sibling role's claim, so two
roles started concurrently cannot both conclude there is nothing to compare
with. It refuses before the session starts. Nothing downstream can see this — two sessions
of one family already differ in the session uuid, which is all
``SafetyCorpusCaseV1`` asks of a ``reviewer_id`` pair. It can only compare
against a claim it can find, so **both roles of a case must be run into one
``--out``**; the label records ``family_independence``, which reads
``"unchecked"`` when there was nothing to compare with.

Condition 3 — attribution and archived transcripts: the transcript file is
named by the sha256 of its bytes; ``reviewer_id`` is
``<family>:<model>:<session id>``, where the model is what the CLI reports it
ran and the session id is the one this runner generated for the session. The
record also names the client build, taken from the session's own transcript
where the stream carries it and from :func:`probe_cli` otherwise.

The OpenAI family runs ``codex exec`` with ``--sandbox read-only``; the
subprocess boundary is :func:`run_subprocess` and is the only thing tests
mock. A ``--dry-run`` prints the exact command and environment without
launching anything.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# `benchmark/.../rater/run_rater.py` is documented as something you run
# straight from a checkout, so it puts this checkout's `src/` first on the
# path itself. "First" is the point: an interpreter that has some other
# `agents_shipgate` -- an older install, or the empty husk an uninstall leaves
# behind, which imports as a namespace package and then fails on the first
# submodule -- would otherwise decide what this harness runs against.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from agents_shipgate.schemas.safety_qualification import IndependentHumanLabelV1  # noqa: E402

# `benchmark/safety-qualification/` is not an importable package name, so the
# sibling module is loaded by path -- under the same name the tests use, and
# reusing an already-loaded copy, so there is exactly one `PacketError` class
# in the process and `except` can catch it.
_BUILD_PACKET = "rater_build_packet"


def _load_build_packet():
    loaded = sys.modules.get(_BUILD_PACKET)
    if loaded is not None:
        return loaded
    spec = importlib.util.spec_from_file_location(
        _BUILD_PACKET, Path(__file__).resolve().parent / "build_packet.py"
    )
    if spec is None or spec.loader is None:  # pragma: no cover - packaging accident
        raise RuntimeError("build_packet.py is not beside run_rater.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


build_packet = _load_build_packet()

FAMILIES = ("claude", "openai")
HOME_MODES = ("isolated", "shared")
ROLES = ("security_governance", "framework_tooling")
DECISIONS = ("passed", "review_required", "insufficient_evidence", "blocked")
LABEL_KEYS = frozenset({"decision", "rationale", "evidence_references"})

CLAUDE_TOOLS = ("Read", "Grep", "Glob")
CLAUDE_DENIED_TOOLS = (
    "Bash",
    "WebFetch",
    "WebSearch",
    "Edit",
    "Write",
    "MultiEdit",
    "NotebookEdit",
    "Task",
    "Agent",
    "TodoWrite",
)
NETWORK_TOOLS = frozenset({"Bash", "WebFetch", "WebSearch"})

# Environment variables that may reach the rater CLI. Everything else is
# dropped; HOME is replaced per run.
_ENV_PASSTHROUGH = ("PATH", "TMPDIR", "LANG", "LC_ALL", "TERM", "SHELL", "USER")
_CLAUDE_CREDENTIAL_ENV = ("ANTHROPIC_API_KEY",)
_OPENAI_CREDENTIAL_ENV = ("OPENAI_API_KEY",)
# No default model for the openai family, deliberately. codex names neither
# the model nor its own version in its event stream, so whatever is recorded
# in `reviewer_id` is what the caller asked for -- and a default would make
# that a guess that is *usually* right, which is the worst kind. Amendment 1
# condition 3 wants `reviewer_id` to name the model that ran; the only way to
# mean it here is to require the caller to say. (The name this used to
# default to, `gpt-5-codex`, was written from memory and is rejected outright
# by a ChatGPT-account login: "not supported when using Codex with a ChatGPT
# account".)

# Written into the isolated Codex home. Every line closes a door the real
# profile could open: no MCP servers, no web search, no approvals, and a
# read-only sandbox restated where config outranks a forgotten flag. The home
# itself is a per-run temporary directory that is deleted when the session
# ends, so history and session files cannot outlive the run either; turning
# persistence off says so rather than leaving it to the directory's lifetime.
#
# Unverified against a real ``codex``: the installed 0.85.0 build is signed
# with a revoked certificate, so macOS kills it at exec and no local run can
# check this (`cut-c-preconditions.md`, precondition 3). Written from the
# published reference, and to be checked against the installed CLI's own
# ``--help`` -- with ``--check-cli`` and then one live run -- before the
# calibration round counts an OpenAI-family label.
_ISOLATED_CODEX_CONFIG = """\
# Written per run by run_rater.py. A rater session sees the packet, and
# nothing else.
sandbox_mode = "read-only"
approval_policy = "never"

[tools]
web_search = false

[history]
persistence = "none"

[mcp_servers]
"""


# The command each family's invocation builder spells. Kept beside the probe
# rather than inside the builders, so "can this CLI run at all" is answerable
# without constructing an invocation.
CLI_BINARIES = {"claude": "claude", "openai": "codex"}
_PROBE_TIMEOUT = 120


class RaterError(RuntimeError):
    """The run did not produce an admissible label."""


@dataclass(frozen=True)
class Invocation:
    argv: tuple[str, ...]
    cwd: Path
    env: dict[str, str]
    stdin_text: str
    session_id: str


@dataclass
class RaterResult:
    label_path: Path
    transcript_path: Path
    transcript_sha256: str
    label: IndependentHumanLabelV1
    model: str
    session_id: str
    cli_version: str = ""
    diagnostics: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Command lines
# --------------------------------------------------------------------------


def _real_home() -> Path:
    return Path(os.environ.get("HOME") or Path.home())


def _encoded_project_dir(path: Path) -> str:
    """The directory name the Claude CLI uses under ``~/.claude/projects`` for a cwd."""

    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def check_packet_carries_no_instructions(packet: Path) -> None:
    for name in ("CLAUDE.md", ".claude", "AGENTS.md"):
        if (packet / name).exists():
            raise RaterError(
                f"packet root carries {name}, which the CLI would read as instructions"
            )


def check_shared_codex_home_is_instruction_free(codex_home: Path) -> None:
    """Refuse a shared Codex home that could still speak to the session.

    ``AGENTS.md`` and ``AGENTS.override.md`` at the Codex home are global
    instructions prepended to every session, and ``--ignore-user-config`` does
    not cover them -- it is documented as ``config.toml`` only. So they are
    what is left to refuse, and a file with no content in it cannot instruct
    anyone, so an empty one is not a reason to stop.

    **What this deliberately no longer refuses.** It used to reject a profile
    whose ``config.toml`` mounted MCP servers, enabled web search, or named an
    instructions file. ``--ignore-user-config`` now loads none of that file
    while still authenticating from the profile, so those refusals stopped
    describing a risk and started describing an ordinary codex install --
    two MCP servers in a developer's profile is the normal case, and shared
    mode is the *only* mode an OAuth login can use. A guard that rejects every
    real machine does not get run; it gets worked around.
    """

    for name in ("AGENTS.md", "AGENTS.override.md"):
        instructions = codex_home / name
        if not instructions.is_file():
            continue
        try:
            speaks = bool(instructions.read_text(encoding="utf-8").strip())
        except (OSError, UnicodeDecodeError):
            speaks = True  # unreadable is not the same as empty
        if speaks:
            raise RaterError(
                f"{instructions} has content: codex prepends it to every session. "
                "Move it aside, or use --home-mode isolated."
            )


def check_shared_home_is_memory_free(packet: Path, home: Path) -> None:
    """Refuse a shared HOME that holds anything the CLI would read for this packet."""

    user_memory = home / ".claude" / "CLAUDE.md"
    if user_memory.exists():
        raise RaterError(f"{user_memory} exists; a shared HOME must carry no user instructions")
    project_dir = home / ".claude" / "projects" / _encoded_project_dir(packet)
    if project_dir.exists():
        raise RaterError(
            f"{project_dir} exists: the CLI has prior sessions or memory for this packet path; "
            "build the packet at a fresh path or use --home-mode isolated"
        )


def probe_cli(family: str, *, timeout: int = _PROBE_TIMEOUT) -> str:
    """Return the version the family's CLI reports, or refuse.

    Neither harness was written against a CLI this project could run: the
    Claude one was smoke-tested against an expired credential and the codex
    one against remembered flags. An unrunnable CLI does not fail like "no
    admissible label" -- a missing binary raises ``FileNotFoundError`` from
    inside the run, a launcher whose vendored binary is absent exits non-zero
    with a Node stack trace, and a binary macOS refuses to load is killed by
    a signal with no output at all. Each of those is a different remedy, so
    each gets its own sentence here, before a packet is handed to anything.

    What it prints is the **fallback** client attribution. Where a family's
    stream names its own build -- ``claude_code_version`` on the Claude
    ``init`` event -- :func:`claude_final` supplies that instead, because a
    version read before launch is what was on ``PATH``, not what ran.
    """

    binary = CLI_BINARIES.get(family)
    if binary is None:
        raise RaterError(f"unknown family {family!r}; expected one of {FAMILIES}")
    try:
        completed = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=timeout, check=False
        )
    except FileNotFoundError as error:
        raise RaterError(f"{binary} is not on PATH, so no {family} session can run") from error
    except subprocess.TimeoutExpired as error:
        raise RaterError(f"{binary} --version did not answer within {timeout}s") from error
    if completed.returncode < 0:
        raise RaterError(
            f"{binary} was killed by signal {-completed.returncode} before printing a "
            "version. On macOS that is what a revoked or invalid code signature looks "
            f"like; check it with `spctl -a -vv -t execute $(command -v {binary})` and "
            "reinstall the CLI."
        )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        raise RaterError(
            f"{binary} --version exited {completed.returncode}: "
            + (detail[0] if detail else "no output")
        )
    version = (completed.stdout or "").strip()
    if not version:
        raise RaterError(f"{binary} --version printed nothing, so no client build can be recorded")
    return version


# Names a session started in a directory below them would read as instructions.
# `.claude` is here for a project directory; the CLI's own home is checked by
# `check_shared_home_is_memory_free` instead.
ANCESTOR_INSTRUCTION_NAMES = (
    "CLAUDE.md",
    "AGENTS.md",
    "AGENTS.override.md",
    ".mcp.json",
    ".claude",
)


def check_no_ancestor_instructions(packet: Path) -> None:
    """Refuse a shared-HOME run whose packet sits below an instruction file.

    Both CLIs discover project instructions by walking **up** from the working
    directory, and shared mode is the mode that leaves that discovery on.
    ``check_packet_carries_no_instructions`` refuses those names at the packet
    root; a packet built one directory below a checkout -- which is what the
    calibration round's own commands invite, since they name a packet
    directory and not where it lives -- is the same exposure by a longer path.
    """

    home = _real_home().resolve()
    for parent in packet.resolve().parents:
        for name in ANCESTOR_INSTRUCTION_NAMES:
            if name == ".claude" and parent == home:
                continue
            if (parent / name).exists():
                raise RaterError(
                    f"{parent / name} sits above the packet and a shared-HOME session "
                    "discovers instructions by walking up from its working directory; "
                    "build the packet outside any checkout, or use --home-mode isolated"
                )
        if parent == home:
            break


def _claimed_family(claim: Path) -> str:
    try:
        recorded = json.loads(claim.read_text(encoding="utf-8")).get("family")
    except (OSError, json.JSONDecodeError) as error:
        raise RaterError(
            f"{claim} cannot be read, so family independence is unknown: {error}"
        ) from error
    if recorded not in FAMILIES:
        # Not "different, therefore fine". A claim that does not name a family
        # cannot be compared with, and passing by default would go on to record
        # that it *was* compared -- a positive claim where `unchecked` is the
        # truth, and the one thing worse than silence.
        raise RaterError(
            f"{claim} names family {recorded!r}, which is not one of {FAMILIES}, so "
            "Amendment 1 condition 1 cannot be checked against it"
        )
    return recorded


def claim_family(out: Path, case_id: str, role: str, family: str) -> str:
    """Reserve ``<case_id, role>`` for ``family``, then check the sibling role.

    Amendment 1 condition 1 -- the two roles on different model families -- is
    the condition the artifact can least afford to get wrong, because
    ``kappa >= 0.80`` between two sessions of one model partly measures a model
    agreeing with itself, and the floor is then easier than the base decision
    intended. Nothing downstream can catch it: ``SafetyCorpusCaseV1`` requires
    the two ``reviewer_id`` values to differ, and two sessions of one family
    differ anyway, in the session uuid.

    **Write first, then read.** Reading the sibling's *label* was a
    time-of-check gate around a session that runs for minutes: two roles
    started together each saw no sibling label, each recorded ``unchecked``,
    and both wrote same-family labels. An operator parallelising 112 sessions
    would hit that as a matter of course. The claim is created with ``O_EXCL``
    before the session starts and *then* the sibling is read, so whichever way
    two concurrent runs interleave, at least one of them sees the other's
    claim -- and if they share a family, that one refuses.

    Re-running a role is allowed as long as the family has not changed;
    swapping families under an existing claim is exactly what this exists to
    stop. Returns what is recorded on the label as ``family_independence``.

    **The sibling still has to be somewhere this can look**: claims live under
    ``out``, so two roles written to two ``--out`` directories are never
    compared, and both then read ``"unchecked"``. The first role of a case is
    legitimately ``"unchecked"``; a case whose *both* records say so is one
    where nobody ever compared, which a freeze step can see and an operator's
    memory cannot.
    """

    claims = out / "claims"
    claims.mkdir(parents=True, exist_ok=True)
    mine = claims / f"{case_id}.{role}.json"
    payload = json.dumps({"case_id": case_id, "role": role, "family": family}, sort_keys=True)
    try:
        with open(mine, "x", encoding="utf-8") as handle:
            handle.write(payload + "\n")
    except FileExistsError:
        already = _claimed_family(mine)
        if already != family:
            raise RaterError(
                f"{mine} already claims {case_id}/{role} for family {already!r}; "
                f"re-running it as {family!r} would swap the families this case was "
                "labeled under"
            ) from None

    other = next(role_name for role_name in ROLES if role_name != role)
    sibling = claims / f"{case_id}.{other}.json"
    if not sibling.is_file():
        return "unchecked"
    recorded = _claimed_family(sibling)
    if recorded == family:
        raise RaterError(
            f"{sibling} claims the {other} label for {case_id} for family {recorded!r}; "
            "Amendment 1 condition 1 requires the two roles on different model families"
        )
    return f"checked against {other} ({recorded})"


def _base_env(credential_names: tuple[str, ...], home: Path) -> dict[str, str]:
    env = {name: os.environ[name] for name in _ENV_PASSTHROUGH if name in os.environ}
    for name in credential_names:
        if name in os.environ:
            env[name] = os.environ[name]
    env["HOME"] = str(home)
    return env


def _resolve_home(home_mode: str, packet: Path, isolated_home: Path) -> Path:
    if home_mode not in HOME_MODES:
        raise RaterError(f"unknown home mode {home_mode!r}; expected one of {HOME_MODES}")
    if home_mode == "isolated":
        return isolated_home
    real = _real_home()
    check_shared_home_is_memory_free(packet, real)
    check_no_ancestor_instructions(packet)
    return real


def claude_invocation(
    packet: Path, *, model: str | None, home: Path, session_id: str, home_mode: str
) -> Invocation:
    effective_home = _resolve_home(home_mode, packet, home)
    argv = ["claude", "-p"]
    if home_mode == "isolated":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RaterError(
                "--home-mode isolated runs the CLI --bare, which authenticates only through "
                "ANTHROPIC_API_KEY, and it is unset; export it, or use --home-mode shared "
                "to keep the caller's HOME (OAuth) under the file-level memory checks"
            )
        argv.append("--bare")
    argv += [
        "--output-format",
        "stream-json",
        "--verbose",
        "--tools",
        ",".join(CLAUDE_TOOLS),
        "--allowedTools",
        ",".join(CLAUDE_TOOLS),
        "--disallowedTools",
        ",".join(CLAUDE_DENIED_TOOLS),
        "--permission-mode",
        "dontAsk",
        "--strict-mcp-config",
        "--setting-sources",
        "",
        "--no-session-persistence",
        "--session-id",
        session_id,
    ]
    if model:
        argv += ["--model", model]
    env = _base_env(_CLAUDE_CREDENTIAL_ENV, effective_home)
    env.update(
        {
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "DISABLE_AUTOUPDATER": "1",
            "DISABLE_TELEMETRY": "1",
            "DISABLE_ERROR_REPORTING": "1",
        }
    )
    task = (packet / "TASK.md").read_text(encoding="utf-8")
    return Invocation(tuple(argv), packet, env, task, session_id)


def openai_invocation(
    packet: Path, *, model: str | None, home: Path, session_id: str, home_mode: str
) -> Invocation:
    if not model:
        raise RaterError(
            "the openai family needs --model: codex does not name the model in its "
            "event stream, so an unnamed one would be recorded in reviewer_id as "
            "whatever this harness guessed"
        )
    effective_home = _resolve_home(home_mode, packet, home)
    argv = [
        "codex",
        "exec",
        "--sandbox",
        "read-only",
        "-C",
        str(packet),
        "--json",
        "--skip-git-repo-check",
        # A config key codex does not recognise is otherwise ignored in
        # silence, which for this config would mean the sandbox, the web
        # search switch or the history setting simply not applying.
        "--strict-config",
        # codex's `--no-session-persistence`: nothing about the session is
        # written to disk, rather than left to a directory's lifetime.
        "--ephemeral",
        # On the command line, not only in `_ISOLATED_CODEX_CONFIG`: shared
        # mode passes `--ignore-user-config` and so supplies no config file at
        # all, and codex's documented default when `web_search` is unset is
        # `"cached"` -- a rater with a search tool backed by everything outside
        # the packet. Telling the model not to use it is not the contract;
        # not having it is. (`--strict-config` validates this override too,
        # and `web_search=false` is rejected by it, so the spelling is pinned.)
        "-c",
        'web_search="disabled"',
        "--model",
        model,
        "-",
    ]
    env = _base_env(_OPENAI_CREDENTIAL_ENV, effective_home)
    # codex reads global AGENTS.md and config.toml from CODEX_HOME, so the
    # real profile is not a credential store this run can borrow: it is a
    # second instruction surface. Isolated mode builds its own; shared mode
    # keeps the real one, and shuts the config half of it with
    # `--ignore-user-config`.
    if home_mode == "isolated":
        if not os.environ.get("OPENAI_API_KEY"):
            raise RaterError(
                "--home-mode isolated gives codex a fresh CODEX_HOME, which carries no "
                "credential, so it authenticates only through OPENAI_API_KEY, and it is "
                "unset; export it, or use --home-mode shared to keep the caller's codex "
                "profile under the instruction checks"
            )
        codex_home = effective_home / ".codex"
        codex_home.mkdir(parents=True, exist_ok=True)
        (codex_home / "config.toml").write_text(_ISOLATED_CODEX_CONFIG, encoding="utf-8")
    else:
        codex_home = Path(os.environ.get("CODEX_HOME") or _real_home() / ".codex")
        check_shared_codex_home_is_instruction_free(codex_home)
        # "Do not load $CODEX_HOME/config.toml; auth still uses CODEX_HOME" --
        # exactly the split shared mode needs, since OAuth lives in that
        # profile and everything else in it may not reach a blind session. An
        # older codex without the flag exits on it, which is the right way to
        # find out.
        argv.insert(2, "--ignore-user-config")
    env["CODEX_HOME"] = str(codex_home)
    task = (packet / "TASK.md").read_text(encoding="utf-8")
    return Invocation(tuple(argv), packet, env, task, session_id)


_INVOCATIONS = {"claude": claude_invocation, "openai": openai_invocation}


# --------------------------------------------------------------------------
# Subprocess boundary (the only thing tests mock)
# --------------------------------------------------------------------------


def run_subprocess(invocation: Invocation, *, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(invocation.argv),
        cwd=str(invocation.cwd),
        env=invocation.env,
        input=invocation.stdin_text,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


# --------------------------------------------------------------------------
# Transcript → final text
# --------------------------------------------------------------------------


def _events(transcript: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in transcript.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return events


def claude_final(transcript: str) -> tuple[str, str | None, str | None]:
    """Return (final text, model, client version) from a ``stream-json`` transcript.

    The final text is the ``result`` event's ``result``; the model and the
    client build both come from the ``init`` system event. Taking the client
    build from here rather than from :func:`probe_cli` is the difference
    between recording what ran and recording what was on ``PATH`` a moment
    earlier. A transcript without a successful ``result`` event is not a
    completed session.
    """

    events = _events(transcript)
    model = None
    client = None
    for event in events:
        if event.get("type") == "system" and event.get("subtype") == "init":
            model = event.get("model") or model
            client = event.get("claude_code_version") or client
    results = [e for e in events if e.get("type") == "result"]
    if len(results) != 1:
        raise RaterError(f"expected exactly one result event, found {len(results)}")
    result = results[0]
    if result.get("subtype") != "success" or result.get("is_error"):
        detail = result.get("result") if isinstance(result.get("result"), str) else ""
        raise RaterError(
            f"session did not complete successfully (subtype={result.get('subtype')}, "
            f"is_error={result.get('is_error')}): {detail[:300]}"
        )
    text = result.get("result")
    if not isinstance(text, str):
        raise RaterError("result event carries no final text")
    return text, model, client


def openai_final(transcript: str) -> tuple[str, str | None, str | None]:
    """Return (final text, model, client version) from a ``codex exec --json`` transcript.

    The final text is the last completed ``agent_message`` item. codex reports
    neither the model nor its own version in its event stream, so both are
    whatever the caller establishes: the model from the command line, the
    client from :func:`probe_cli`.
    """

    events = _events(transcript)
    messages = [
        e.get("item", {}).get("text")
        for e in events
        if e.get("type") == "item.completed"
        and isinstance(e.get("item"), dict)
        and e["item"].get("type") == "agent_message"
    ]
    messages = [m for m in messages if isinstance(m, str)]
    # A failed turn says why; "no completed agent message" says only that the
    # session produced nothing, which is the symptom of every possible cause.
    # An unusable model, a revoked credential and a refused sandbox all arrive
    # here, and all three have different remedies.
    for event in events:
        if event.get("type") in {"turn.failed", "error"}:
            detail = event.get("message") or (event.get("error") or {}).get("message") or ""
            raise RaterError(f"codex reported {event['type']}: {str(detail)[:400]}")
    if not messages:
        raise RaterError("no completed agent message in the codex transcript")
    if not any(e.get("type") == "turn.completed" for e in events):
        raise RaterError("codex turn did not complete")
    return messages[-1], None, None


_FINALS = {"claude": claude_final, "openai": openai_final}


# --------------------------------------------------------------------------
# Final text → label
# --------------------------------------------------------------------------


def parse_label_object(text: str) -> dict[str, Any]:
    """Exactly one JSON object with exactly the contract's keys, or a RaterError.

    A single surrounding code fence is tolerated because it is a formatting
    slip, not a second object; anything else that is not one object fails.
    """

    body = text.strip()
    if body.startswith("```"):
        first_newline = body.find("\n")
        if first_newline == -1 or not body.endswith("```"):
            raise RaterError("final message is a malformed code fence")
        body = body[first_newline + 1 : -3].strip()
    if not (body.startswith("{") and body.endswith("}")):
        raise RaterError("final message is not exactly one JSON object")
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as error:
        raise RaterError(f"final message is not exactly one JSON object: {error}") from error
    if not isinstance(parsed, dict):
        raise RaterError("final message is not a JSON object")
    keys = set(parsed)
    if keys != LABEL_KEYS:
        raise RaterError(f"label keys must be exactly {sorted(LABEL_KEYS)}, got {sorted(keys)}")
    if parsed["decision"] not in DECISIONS:
        raise RaterError(f"decision {parsed['decision']!r} is not one of {DECISIONS}")
    refs = parsed["evidence_references"]
    if not isinstance(refs, list) or not refs or not all(isinstance(r, str) for r in refs):
        raise RaterError("evidence_references must be a non-empty list of strings")
    if not isinstance(parsed["rationale"], str):
        raise RaterError("rationale must be a string")
    return parsed


def build_label(parsed: dict[str, Any], *, role: str, reviewer_id: str) -> IndependentHumanLabelV1:
    try:
        return IndependentHumanLabelV1(
            role=role,
            reviewer_id=reviewer_id,
            decision=parsed["decision"],
            rationale=parsed["rationale"],
            evidence_references=tuple(parsed["evidence_references"]),
            shipgate_output_seen=False,
        )
    except ValueError as error:
        raise RaterError(f"label does not validate: {error}") from error


# --------------------------------------------------------------------------
# One run
# --------------------------------------------------------------------------


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _as_text(stream: str | bytes | None) -> str:
    """``TimeoutExpired`` carries bytes even when the run asked for text."""

    if stream is None:
        return ""
    return stream if isinstance(stream, str) else stream.decode("utf-8", "replace")


def _archive_transcript(out: Path, stdout: str, stderr: str) -> tuple[Path, str]:
    """Write the session's streams content-addressed; returns (path, sha256)."""

    digest = _sha256_text(stdout)
    transcripts = out / "transcripts"
    transcripts.mkdir(parents=True, exist_ok=True)
    path = transcripts / f"{digest}.jsonl"
    path.write_text(stdout, encoding="utf-8")
    (transcripts / f"{digest}.stderr.txt").write_text(stderr, encoding="utf-8")
    return path, digest


def _check_packet(packet: Path) -> dict[str, Any]:
    """The packet as it stands now is the packet the manifest describes.

    Existence is not the property that matters. ``build_packet`` proved the
    packet was clean when it was built; this runs at launch, so every file is
    re-hashed against ``MANIFEST.json`` and any difference -- an edited
    ``TASK.md``, a note added under ``repo/``, a file removed -- refuses the
    run. Otherwise the label would record the hash of a manifest that no
    longer describes what the rater read.
    """

    manifest_path = packet / "MANIFEST.json"
    for name in ("repo", "diff.patch", "LABELING.md", "TASK.md", "MANIFEST.json"):
        if not (packet / name).exists():
            raise RaterError(f"packet is missing {name}")
    try:
        build_packet.verify_manifest(packet)
    except build_packet.PacketError as error:
        raise RaterError(
            f"packet does not match its manifest, so it is not the built one: {error}"
        ) from error
    except (KeyError, json.JSONDecodeError) as error:
        raise RaterError(f"packet manifest is unreadable: {error}") from error
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def prepare(
    *,
    family: str,
    role: str,
    packet: Path,
    model: str | None,
    home: Path,
    home_mode: str = "isolated",
) -> tuple[Invocation, dict[str, Any]]:
    if family not in FAMILIES:
        raise RaterError(f"unknown family {family!r}; expected one of {FAMILIES}")
    if role not in ROLES:
        raise RaterError(f"unknown role {role!r}; expected one of {ROLES}")
    manifest = _check_packet(packet)
    if manifest.get("role") != role:
        raise RaterError(
            f"packet was built for role {manifest.get('role')!r}; run it with that role"
        )
    check_packet_carries_no_instructions(packet)
    session_id = str(uuid.uuid4())
    invocation = _INVOCATIONS[family](
        packet, model=model, home=home, session_id=session_id, home_mode=home_mode
    )
    return invocation, manifest


def format_dry_run(invocation: Invocation) -> str:
    lines = [
        f"cwd: {invocation.cwd}",
        f"argv: {shlex.join(invocation.argv)}",
        "env:",
    ]
    for key in sorted(invocation.env):
        value = invocation.env[key]
        if key.endswith("_KEY"):
            value = "<redacted>"
        lines.append(f"  {key}={value}")
    lines.append(f"stdin: TASK.md ({len(invocation.stdin_text)} chars)")
    lines.append(f"session_id: {invocation.session_id}")
    return "\n".join(lines)


def run_rater(
    *,
    family: str,
    role: str,
    packet: Path,
    out: Path,
    model: str | None = None,
    timeout: int = 3600,
    home_mode: str = "isolated",
    runner=run_subprocess,
    prober=probe_cli,
) -> RaterResult:
    packet = packet.resolve()
    out.mkdir(parents=True, exist_ok=True)
    cli_version = prober(family)
    with tempfile.TemporaryDirectory(prefix="rater-home-") as home:
        invocation, manifest = prepare(
            family=family,
            role=role,
            packet=packet,
            model=model,
            home=Path(home),
            home_mode=home_mode,
        )
        family_independence = claim_family(out, manifest["case_id"], role, family)
        try:
            completed = runner(invocation, timeout=timeout)
        except subprocess.TimeoutExpired as expired:
            # A session that ran for an hour and was killed is the one whose
            # transcript is most worth having: it says where it got stuck.
            # Nothing here produces a label -- the exception still propagates.
            _archive_transcript(out, _as_text(expired.stdout), _as_text(expired.stderr))
            raise

    transcript = completed.stdout or ""
    transcript_path, transcript_sha = _archive_transcript(out, transcript, completed.stderr or "")

    diagnostics: list[str] = []
    if completed.returncode != 0:
        diagnostics.append(f"cli exited {completed.returncode}")

    final_text, reported_model, reported_client = _FINALS[family](transcript)
    resolved_model = reported_model or model
    if family == "openai":
        resolved_model = model
    if not resolved_model:
        raise RaterError("the transcript does not name the model; pass --model")

    # The probe says what was on PATH; the transcript says what ran. Prefer
    # the transcript, and keep the probe's answer for the family whose stream
    # does not carry one.
    cli_version = reported_client or cli_version
    reviewer_id = f"{family}:{resolved_model}:{invocation.session_id}"
    parsed = parse_label_object(final_text)
    label = build_label(parsed, role=role, reviewer_id=reviewer_id)

    labels = out / "labels"
    labels.mkdir(exist_ok=True)
    case_id = manifest["case_id"]
    label_path = labels / f"{case_id}.{role}.json"
    record = {
        "case_id": case_id,
        "family": family,
        "home_mode": home_mode,
        "model": resolved_model,
        "session_id": invocation.session_id,
        "cli_version": cli_version,
        "family_independence": family_independence,
        "packet_manifest_sha256": _sha256_text(
            (packet / "MANIFEST.json").read_text(encoding="utf-8")
        ),
        "transcript_sha256": transcript_sha,
        "label": label.model_dump(mode="json"),
    }
    label_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return RaterResult(
        label_path=label_path,
        transcript_path=transcript_path,
        transcript_sha256=transcript_sha,
        label=label,
        model=resolved_model,
        session_id=invocation.session_id,
        cli_version=cli_version,
        diagnostics=diagnostics,
    )


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--family", required=True, choices=FAMILIES)
    parser.add_argument("--role", required=True, choices=ROLES)
    parser.add_argument("--packet", type=Path, help="required unless --check-cli")
    parser.add_argument("--out", type=Path, help="required unless --check-cli or --dry-run")
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=int, default=3600, help="seconds")
    parser.add_argument(
        "--home-mode",
        choices=HOME_MODES,
        default="isolated",
        help="isolated: empty HOME + --bare and a fresh CODEX_HOME (needs "
        "ANTHROPIC_API_KEY / OPENAI_API_KEY); shared: caller's HOME (OAuth) and "
        "codex profile, under file-level memory and instruction checks",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the command and environment; do not launch"
    )
    parser.add_argument(
        "--check-cli",
        action="store_true",
        help="run only the family's CLI version probe and print what it reports",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.check_cli:
        # Its own handler: this mode was never asking for a label, so the
        # "no admissible label" prefix would name the wrong failure.
        try:
            print(f"{CLI_BINARIES[args.family]}: {probe_cli(args.family)}")
        except RaterError as error:
            print(f"run_rater: {args.family} cannot run: {error}", file=sys.stderr)
            return 2
        return 0
    try:
        if args.packet is None:
            raise RaterError("--packet is required unless --check-cli is given")
        if args.dry_run:
            with tempfile.TemporaryDirectory(prefix="rater-home-") as home:
                invocation, _manifest = prepare(
                    family=args.family,
                    role=args.role,
                    packet=args.packet.resolve(),
                    model=args.model,
                    home=Path(home),
                    home_mode=args.home_mode,
                )
                print(format_dry_run(invocation))
            return 0
        if args.out is None:
            raise RaterError("--out is required to record a label and its transcript")
        result = run_rater(
            family=args.family,
            role=args.role,
            packet=args.packet,
            out=args.out,
            model=args.model,
            timeout=args.timeout,
            home_mode=args.home_mode,
        )
    except RaterError as error:
        print(f"run_rater: no admissible label: {error}", file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired:
        print("run_rater: no admissible label: the session timed out", file=sys.stderr)
        return 2
    print(f"label: {result.label_path}")
    print(f"transcript: {result.transcript_path}")
    print(f"reviewer_id: {result.label.reviewer_id}")
    print(f"cli: {CLI_BINARIES[args.family]} {result.cli_version}")
    for line in result.diagnostics:
        print(f"note: {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
