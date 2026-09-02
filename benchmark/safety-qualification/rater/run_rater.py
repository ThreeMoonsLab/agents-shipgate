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
- **No verifier output, no other label.** The packet contains none, by
  construction (:mod:`build_packet`); this runner adds none.

Condition 3 — attribution and archived transcripts: the transcript file is
named by the sha256 of its bytes; ``reviewer_id`` is
``<family>:<model>:<session id>``, where the model is what the CLI reports it
ran and the session id is the one this runner generated for the session.

The OpenAI family runs ``codex exec`` with ``--sandbox read-only``; the
subprocess boundary is :func:`run_subprocess` and is the only thing tests
mock. A ``--dry-run`` prints the exact command and environment without
launching anything.
"""

from __future__ import annotations

import argparse
import hashlib
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

from agents_shipgate.schemas.safety_qualification import IndependentHumanLabelV1

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
_DEFAULT_OPENAI_MODEL = "gpt-5-codex"


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
        "--model",
        model or _DEFAULT_OPENAI_MODEL,
        "-",
    ]
    env = _base_env(_OPENAI_CREDENTIAL_ENV, effective_home)
    # codex keeps its auth under ~/.codex; with HOME replaced it must be told
    # where that is. Pointing CODEX_HOME at the real one keeps the credential
    # and nothing else that matters here: codex has no per-project memory.
    # Unverified on this machine (the local codex install is broken).
    env["CODEX_HOME"] = os.environ.get("CODEX_HOME") or str(_real_home() / ".codex")
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


def claude_final(transcript: str) -> tuple[str, str | None]:
    """Return (final text, model) from a ``stream-json`` transcript.

    The final text is the ``result`` event's ``result``; the model is the
    ``init`` system event's ``model``. A transcript without a successful
    ``result`` event is not a completed session.
    """

    events = _events(transcript)
    model = None
    for event in events:
        if event.get("type") == "system" and event.get("subtype") == "init":
            model = event.get("model") or model
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
    return text, model


def openai_final(transcript: str) -> tuple[str, str | None]:
    """Return (final text, model) from a ``codex exec --json`` transcript.

    The final text is the last completed ``agent_message`` item. codex does
    not report the model in its event stream, so the model is whatever the
    command line asked for (recorded by the caller).
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
    if not messages:
        raise RaterError("no completed agent message in the codex transcript")
    if not any(e.get("type") == "turn.completed" for e in events):
        raise RaterError("codex turn did not complete")
    return messages[-1], None


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


def _check_packet(packet: Path) -> dict[str, Any]:
    manifest_path = packet / "MANIFEST.json"
    for name in ("repo", "diff.patch", "LABELING.md", "TASK.md", "MANIFEST.json"):
        if not (packet / name).exists():
            raise RaterError(f"packet is missing {name}")
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
) -> RaterResult:
    packet = packet.resolve()
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="rater-home-") as home:
        invocation, manifest = prepare(
            family=family,
            role=role,
            packet=packet,
            model=model,
            home=Path(home),
            home_mode=home_mode,
        )
        completed = runner(invocation, timeout=timeout)

    transcript = completed.stdout or ""
    transcript_sha = _sha256_text(transcript)
    transcripts = out / "transcripts"
    transcripts.mkdir(exist_ok=True)
    transcript_path = transcripts / f"{transcript_sha}.jsonl"
    transcript_path.write_text(transcript, encoding="utf-8")
    (transcripts / f"{transcript_sha}.stderr.txt").write_text(
        completed.stderr or "", encoding="utf-8"
    )

    diagnostics: list[str] = []
    if completed.returncode != 0:
        diagnostics.append(f"cli exited {completed.returncode}")

    final_text, reported_model = _FINALS[family](transcript)
    resolved_model = reported_model or model
    if family == "openai":
        resolved_model = model or _DEFAULT_OPENAI_MODEL
    if not resolved_model:
        raise RaterError("the transcript does not name the model; pass --model")

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
        diagnostics=diagnostics,
    )


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--family", required=True, choices=FAMILIES)
    parser.add_argument("--role", required=True, choices=ROLES)
    parser.add_argument("--packet", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=int, default=3600, help="seconds")
    parser.add_argument(
        "--home-mode",
        choices=HOME_MODES,
        default="isolated",
        help="isolated: empty HOME + --bare (needs ANTHROPIC_API_KEY); "
        "shared: caller's HOME (OAuth) under file-level memory checks",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the command and environment; do not launch"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
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
    for line in result.diagnostics:
        print(f"note: {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
