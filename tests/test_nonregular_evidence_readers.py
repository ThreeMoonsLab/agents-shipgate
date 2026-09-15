"""Issue #577: evidence readers refuse non-regular inputs without blocking.

Opening a FIFO for reading waits for a writer, so a reader that opens a path
before it checks the file type can hang instead of refusing it. Every probe
that could hang runs in a child process with a hard timeout. A regression
fails the test; it cannot hang the suite.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import agents_shipgate
from agents_shipgate.cli.discovery.local_review import ensure_local_review_excludes
from agents_shipgate.cli.verify.orchestrator import (
    _declaration_continuation_holds,
    _load_cached_capability_lock,
)
from agents_shipgate.core.capability_lock import (
    build_capability_lock,
    render_capability_lock_json,
)
from agents_shipgate.core.domain import Agent
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.core.human_authorization import (
    HumanAuthorizationTrustPolicyError,
    load_external_trust_policy,
)
from agents_shipgate.core.verification_identity import read_regular_file_beneath
from agents_shipgate.schemas.declaration_continuation import (
    AppliedDeclaration,
    DeclarationContinuationV1,
)
from agents_shipgate.schemas.manifest import AgentsShipgateManifest

SRC = Path(agents_shipgate.__file__).resolve().parents[1]
# The outer bound only has to be finite: it covers interpreter start-up and the
# package import on a loaded `-n auto` runner. "Promptly" is asserted on the
# reader call alone, timed inside the child.
CHILD_TIMEOUT_SECONDS = 30
PROMPT_SECONDS = 10

needs_fifo = pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs are POSIX-only")
needs_dir_fd = pytest.mark.skipif(
    os.open not in os.supports_dir_fd,
    reason="descriptor-relative reads need dir_fd support",
)
needs_posix = pytest.mark.skipif(
    os.name != "posix", reason="O_NOFOLLOW file-type refusals are POSIX-only"
)


def _run_bounded(code: str, *args: str) -> dict:
    """Run ``code`` in a child, killing it if the reader blocks."""

    pythonpath = os.pathsep.join(
        part for part in (str(SRC), os.environ.get("PYTHONPATH", "")) if part
    )
    try:
        completed = subprocess.run(
            [sys.executable, "-c", textwrap.dedent(code), *args],
            capture_output=True,
            text=True,
            timeout=CHILD_TIMEOUT_SECONDS,
            env={**os.environ, "PYTHONPATH": pythonpath},
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"the reader blocked for {CHILD_TIMEOUT_SECONDS}s instead of "
            "refusing a non-regular input"
        )
    assert completed.returncode == 0, completed.stderr
    outcome = json.loads(completed.stdout.strip().splitlines()[-1])
    assert outcome["elapsed"] < PROMPT_SECONDS, outcome
    return outcome


# --- read_regular_file_beneath ----------------------------------------------

_BENEATH_CHILD = """
    import json, sys, time
    from pathlib import Path
    from agents_shipgate.core.verification_identity import read_regular_file_beneath

    started = time.monotonic()
    try:
        data = read_regular_file_beneath(Path(sys.argv[1]), sys.argv[2], max_size=1024)
        outcome = {"accepted": True, "size": len(data)}
    except ValueError as exc:
        outcome = {"accepted": False, "error": str(exc)}
    outcome["elapsed"] = time.monotonic() - started
    print(json.dumps(outcome))
"""


@needs_fifo
def test_fifo_without_a_writer_is_refused_promptly(tmp_path: Path) -> None:
    os.mkfifo(tmp_path / "input.json")

    outcome = _run_bounded(_BENEATH_CHILD, str(tmp_path), "input.json")

    assert outcome["accepted"] is False
    assert outcome["error"] == "receipt artifact is not a regular file: input.json"


@needs_fifo
def test_fifo_with_a_writer_cannot_become_evidence(tmp_path: Path) -> None:
    fifo = tmp_path / "input.json"
    os.mkfifo(fifo)
    # Read-write keeps a writer attached without waiting for a reader, and the
    # bytes sit in the pipe: a reader that got past the type check would hash them.
    writer = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)
    try:
        os.write(writer, b'{"forged": true}')
        outcome = _run_bounded(_BENEATH_CHILD, str(tmp_path), "input.json")
    finally:
        os.close(writer)

    assert outcome["accepted"] is False
    assert outcome["error"] == "receipt artifact is not a regular file: input.json"


@needs_fifo
def test_fifo_as_a_parent_component_is_refused_promptly(tmp_path: Path) -> None:
    os.mkfifo(tmp_path / "nested")

    outcome = _run_bounded(_BENEATH_CHILD, str(tmp_path), "nested/input.json")

    assert outcome["accepted"] is False
    assert outcome["error"].startswith(
        "could not safely read receipt artifact 'nested/input.json':"
    )


@needs_dir_fd
def test_directory_cannot_become_evidence(tmp_path: Path) -> None:
    (tmp_path / "input.json").mkdir()

    with pytest.raises(ValueError, match="^receipt artifact is not a regular file: input.json$"):
        read_regular_file_beneath(tmp_path, "input.json", max_size=1024)


@needs_dir_fd
def test_regular_file_keeps_its_bytes_size_limit_and_confinement(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "nested").mkdir(parents=True)
    # CR LF, a 0x1A byte and a NUL: what a text-mode or truncating read would change.
    payload = b'{"artifact": 1}\r\n\x1a\x00tail'
    (root / "nested" / "input.json").write_bytes(payload)

    data = read_regular_file_beneath(root, "nested/input.json", max_size=len(payload))
    assert data == payload
    assert hashlib.sha256(data).hexdigest() == hashlib.sha256(payload).hexdigest()

    with pytest.raises(ValueError, match="exceeds its size limit"):
        read_regular_file_beneath(root, "nested/input.json", max_size=len(payload) - 1)
    for escaping in ("../outside.json", str(tmp_path / "outside.json")):
        with pytest.raises(ValueError, match="path is not portable"):
            read_regular_file_beneath(root, escaping, max_size=len(payload))

    (tmp_path / "outside.json").write_bytes(payload)
    (root / "leaf-link.json").symlink_to(tmp_path / "outside.json")
    (root / "parent-link").symlink_to(tmp_path, target_is_directory=True)
    for linked in ("leaf-link.json", "parent-link/outside.json"):
        with pytest.raises(ValueError, match="could not safely read receipt artifact"):
            read_regular_file_beneath(root, linked, max_size=len(payload))


# --- the human-authorization trust policy ------------------------------------

_TRUST_POLICY_CHILD = """
    import json, sys, time
    from pathlib import Path
    from agents_shipgate.core.human_authorization import (
        HumanAuthorizationTrustPolicyError,
        load_external_trust_policy,
    )

    started = time.monotonic()
    try:
        load_external_trust_policy(Path(sys.argv[1]), workspace=Path(sys.argv[2]))
        outcome = {"accepted": True}
    except HumanAuthorizationTrustPolicyError as exc:
        outcome = {"accepted": False, "code": exc.code}
    outcome["elapsed"] = time.monotonic() - started
    print(json.dumps(outcome))
"""


def _trust_layout(tmp_path: Path) -> tuple[Path, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    trust = tmp_path / "host-trust"
    trust.mkdir(mode=0o700)
    return workspace, trust / "policy.json"


@needs_fifo
def test_trust_policy_fifo_is_refused_promptly(tmp_path: Path) -> None:
    workspace, policy = _trust_layout(tmp_path)
    os.mkfifo(policy)

    outcome = _run_bounded(_TRUST_POLICY_CHILD, str(policy), str(workspace))

    assert outcome == {**outcome, "accepted": False, "code": "trust_policy_not_regular_file"}


@needs_dir_fd
def test_trust_policy_directory_is_refused(tmp_path: Path) -> None:
    workspace, policy = _trust_layout(tmp_path)
    policy.mkdir(mode=0o700)

    with pytest.raises(HumanAuthorizationTrustPolicyError) as refused:
        load_external_trust_policy(policy, workspace=workspace)
    assert refused.value.code == "trust_policy_not_regular_file"


# --- the local-review Git exclude file ---------------------------------------

_LOCAL_REVIEW_CHILD = """
    import json, sys, time
    from pathlib import Path
    from agents_shipgate.cli.discovery.local_review import ensure_local_review_excludes
    from agents_shipgate.core.errors import ConfigError

    started = time.monotonic()
    try:
        ensure_local_review_excludes(Path(sys.argv[1]))
        outcome = {"accepted": True}
    except ConfigError as exc:
        outcome = {"accepted": False, "error": str(exc)}
    outcome["elapsed"] = time.monotonic() - started
    print(json.dumps(outcome))
"""


def _repository_with_exclude_path(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    exclude = repo / ".git" / "info" / "exclude"
    exclude.parent.mkdir(exist_ok=True)
    exclude.unlink(missing_ok=True)
    return repo, exclude


@needs_fifo
def test_local_review_exclude_fifo_is_refused_promptly(tmp_path: Path) -> None:
    repo, exclude = _repository_with_exclude_path(tmp_path)
    os.mkfifo(exclude)

    outcome = _run_bounded(_LOCAL_REVIEW_CHILD, str(repo))

    assert outcome["accepted"] is False
    assert outcome["error"].startswith("Refusing to use a non-regular file:")


@needs_posix
def test_local_review_exclude_directory_is_refused(tmp_path: Path) -> None:
    repo, exclude = _repository_with_exclude_path(tmp_path)
    exclude.mkdir()

    with pytest.raises(ConfigError, match="Refusing to use a non-regular file"):
        ensure_local_review_excludes(repo)


# --- verify: the declaration-continuation receipt ------------------------------

_CONTINUATION_CHILD = """
    import json, sys, time
    from pathlib import Path
    from agents_shipgate.cli.verify.orchestrator import _declaration_continuation_holds

    root = Path(sys.argv[1])
    started = time.monotonic()
    holds = _declaration_continuation_holds(
        git_root=root,
        config_path=root / "shipgate.yaml",
        config_relative=Path("shipgate.yaml"),
        out_dir=root / "sg-out",
        comparison_ref="HEAD",
        gate_introduced=False,
    )
    print(json.dumps({"holds": holds, "elapsed": time.monotonic() - started}))
"""


@needs_fifo
def test_declaration_continuation_fifo_is_refused_promptly(tmp_path: Path) -> None:
    (tmp_path / "sg-out").mkdir()
    os.mkfifo(tmp_path / "sg-out" / "declaration-continuation.json")

    outcome = _run_bounded(_CONTINUATION_CHILD, str(tmp_path))

    assert outcome["holds"] is False


@needs_dir_fd
def test_declaration_continuation_directory_is_refused(tmp_path: Path) -> None:
    (tmp_path / "sg-out" / "declaration-continuation.json").mkdir(parents=True)

    assert (
        _declaration_continuation_holds(
            git_root=tmp_path,
            config_path=tmp_path / "shipgate.yaml",
            config_relative=Path("shipgate.yaml"),
            out_dir=tmp_path / "sg-out",
            comparison_ref="HEAD",
            gate_introduced=False,
        )
        is False
    )


# --- verify: the cached base capability lock -----------------------------------

_CACHED_LOCK_CHILD = """
    import json, sys, time
    from pathlib import Path
    from agents_shipgate.cli.verify.orchestrator import _load_cached_capability_lock

    started = time.monotonic()
    lock, notes = _load_cached_capability_lock(Path(sys.argv[1]) / "report.json")
    print(json.dumps({
        "loaded": lock is not None,
        "notes": notes,
        "elapsed": time.monotonic() - started,
    }))
"""


@needs_fifo
def test_cached_capability_lock_fifo_is_refused_promptly(tmp_path: Path) -> None:
    os.mkfifo(tmp_path / "capabilities.lock.json")

    outcome = _run_bounded(_CACHED_LOCK_CHILD, str(tmp_path))

    assert outcome["loaded"] is False
    assert outcome["notes"] == [
        "Cached base capability lock invalid; capability diff may fall back: "
        "cached base capability lock is not a regular file: capabilities.lock.json"
    ]


@needs_dir_fd
def test_cached_capability_lock_directory_is_refused(tmp_path: Path) -> None:
    (tmp_path / "capabilities.lock.json").mkdir()

    lock, notes = _load_cached_capability_lock(tmp_path / "report.json")

    assert lock is None
    assert notes == [
        "Cached base capability lock invalid; capability diff may fall back: "
        "cached base capability lock is not a regular file: capabilities.lock.json"
    ]


# --- verify: symlinked and undecodable entries ---------------------------------
#
# Routing both reads through ``read_regular_file_beneath`` also changed two
# outcomes that are not about FIFOs: a symlink is no longer followed, and a lock
# that is not UTF-8 falls back instead of raising ``UnicodeDecodeError`` past
# the ``(OSError, InputParseError)`` handler (#577 review). Each test first
# shows the same bytes accepted as a regular file, so the refusal is the link or
# the encoding and nothing else.

_FALL_BACK_NOTE = "Cached base capability lock invalid; capability diff may fall back: "


def _rendered_capability_lock() -> str:
    manifest = AgentsShipgateManifest.model_validate(
        {
            "version": "0.1",
            "project": {"name": "nonregular-readers"},
            "agent": {"name": "support-agent", "declared_purpose": ["Support workflows."]},
            "environment": {"target": "local"},
            "tool_sources": [
                {"id": "support_api", "type": "openapi", "path": "tools/support.openapi.yaml"}
            ],
        }
    )
    lock = build_capability_lock(
        manifest,
        agent=Agent(id="agent:one", name="support-agent"),
        tools=[],
        config_path=Path("shipgate.yaml"),
        manifest_dir=Path("."),
        cli_version="test-version",
        source_count=1,
        source_warning_count=0,
        toolkit_bound_count=0,
        plugins_enabled=True,
    )
    return render_capability_lock_json(lock)


@needs_dir_fd
def test_cached_capability_lock_symlink_is_refused(tmp_path: Path) -> None:
    rendered = _rendered_capability_lock()
    regular = tmp_path / "regular"
    regular.mkdir()
    (regular / "capabilities.lock.json").write_text(rendered, encoding="utf-8")
    lock, notes = _load_cached_capability_lock(regular / "report.json")
    assert lock is not None and notes == []

    (tmp_path / "elsewhere.lock.json").write_text(rendered, encoding="utf-8")
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / "capabilities.lock.json").symlink_to(tmp_path / "elsewhere.lock.json")

    lock, notes = _load_cached_capability_lock(linked / "report.json")

    assert lock is None
    assert len(notes) == 1
    assert notes[0].startswith(
        _FALL_BACK_NOTE
        + "could not safely read cached base capability lock 'capabilities.lock.json':"
    ), notes


@needs_dir_fd
def test_cached_capability_lock_that_is_not_utf8_falls_back(tmp_path: Path) -> None:
    (tmp_path / "capabilities.lock.json").write_bytes(
        _rendered_capability_lock().encode("utf-8").replace(b"{", b"{\xff", 1)
    )

    lock, notes = _load_cached_capability_lock(tmp_path / "report.json")

    assert lock is None
    assert len(notes) == 1
    assert notes[0].startswith(
        _FALL_BACK_NOTE + "'utf-8' codec can't decode byte 0xff"
    ), notes


@needs_dir_fd
def test_declaration_continuation_symlink_is_refused(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Shipgate Test",
                "-c",
                "user.email=test@example.invalid",
                "-c",
                "commit.gpgsign=false",
                *args,
            ],
            cwd=repo,
            check=True,
            capture_output=True,
        )

    git("init", "-q")
    (repo / "README.md").write_text("fixture\n", encoding="utf-8")
    git("add", "README.md")
    git("commit", "-q", "-m", "initial")
    # A first adoption: the manifest is not at the comparison ref, so a receipt
    # whose after-digest matches it holds on the introduction proof alone.
    manifest = b"version: '0.1'\n"
    (repo / "shipgate.yaml").write_bytes(manifest)
    receipt = DeclarationContinuationV1(
        manifest_path="shipgate.yaml",
        manifest_sha256_after=hashlib.sha256(manifest).hexdigest(),
        source_report_sha256="0" * 64,
        applied=[
            AppliedDeclaration(
                target_path="shipgate.yaml",
                selector={"tool": "support.refund"},
                declaration={"approval": "required"},
            )
        ],
    ).model_dump_json()
    out_dir = repo / "sg-out"
    out_dir.mkdir()
    arguments = {
        "git_root": repo,
        "config_path": repo / "shipgate.yaml",
        "config_relative": Path("shipgate.yaml"),
        "out_dir": out_dir,
        "comparison_ref": "HEAD",
        "gate_introduced": True,
    }
    (out_dir / "declaration-continuation.json").write_text(receipt, encoding="utf-8")
    assert _declaration_continuation_holds(**arguments) is True

    (out_dir / "declaration-continuation.json").unlink()
    (tmp_path / "elsewhere.json").write_text(receipt, encoding="utf-8")
    (out_dir / "declaration-continuation.json").symlink_to(tmp_path / "elsewhere.json")

    assert _declaration_continuation_holds(**arguments) is False
