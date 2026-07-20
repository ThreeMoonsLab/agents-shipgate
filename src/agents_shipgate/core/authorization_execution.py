"""Guarded execution for one previously signed human authorization.

The signed grant remains the authority.  This module is only its execution
consumer: it revalidates the current repository identity, isolates Git from
workspace/global rewrite configuration, disables hooks, and invokes the typed
force-with-lease operation.  It never creates or signs authority.
"""

from __future__ import annotations

import os
import re
import selectors
import shlex
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import agents_shipgate
from agents_shipgate.schemas.human_authorization import GitPushOperationV1

_AUTHORIZATION_HEADER_RE = re.compile(
    r"^authorization:[ \t]+(?:basic|bearer)[ \t]+[A-Za-z0-9+/=_-]+$",
    re.IGNORECASE,
)
_MAX_AUTHORIZED_GRAPH_PACK_BYTES = 512 * 1024 * 1024
_MAX_AUTHORIZED_PROCESS_STDERR_BYTES = 1024 * 1024


def _authorization_python_launcher() -> Path:
    """Return the absolute launcher path without dereferencing a venv symlink."""

    return Path(os.path.abspath(sys.executable))


def authorization_execute_command(
    *,
    workspace: str | Path,
    receipt: str | Path,
    artifacts_root: str | Path,
) -> str:
    """Render the sole durable command exposed to the coding agent.

    Absolute paths plus isolated Python mode remove cwd, ``PATH``, user-site,
    and ``PYTHON*`` ambiguity.  A production host must still launch this argv
    from a sanitized, host-protected broker environment; the CLI cannot prove
    that boundary from inside the child process.
    """

    workspace_path = Path(workspace).resolve()
    receipt_path = Path(receipt)
    if not receipt_path.is_absolute():
        receipt_path = workspace_path / receipt_path
    artifacts_path = Path(artifacts_root)
    if not artifacts_path.is_absolute():
        artifacts_path = workspace_path / artifacts_path

    return shlex.join(
        [
            "/usr/bin/env",
            "-i",
            str(_authorization_python_launcher()),
            "-I",
            "-m",
            "agents_shipgate",
            "authorization",
            "execute",
            "--workspace",
            str(workspace_path),
            "--receipt",
            str(receipt_path.resolve()),
            "--artifacts-root",
            str(artifacts_path.resolve()),
        ]
    )


def ensure_authorization_runtime_is_external(workspace: Path) -> None:
    """Reject editable/workspace-owned authorization runtimes.

    This is defense in depth for the host-owned broker precondition.  A
    verifier imported from the repository it is authorizing is not an
    independent execution boundary and therefore cannot expose command
    authority.
    """

    root = workspace.resolve(strict=True)
    interpreter_launcher = _authorization_python_launcher()
    interpreter = interpreter_launcher.resolve(strict=True)
    package_file = Path(agents_shipgate.__file__ or "").resolve(strict=True)
    for label, candidate in (
        ("Python launcher", interpreter_launcher),
        ("Python interpreter", interpreter),
        ("Agents Shipgate distribution", package_file),
    ):
        if _path_is_within(candidate, root):
            raise ValueError(f"{label} must be installed outside the authorized workspace")
    metadata = interpreter.stat()
    if not stat.S_ISREG(metadata.st_mode) or not os.access(interpreter, os.X_OK):
        raise ValueError("authorization Python interpreter is not an executable regular file")
    if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise ValueError("authorization Python interpreter is group/world writable")
    environment_launcher = Path("/usr/bin/env").resolve(strict=True)
    launcher_metadata = environment_launcher.stat()
    if (
        not stat.S_ISREG(launcher_metadata.st_mode)
        or not os.access(environment_launcher, os.X_OK)
        or launcher_metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        or (hasattr(launcher_metadata, "st_uid") and launcher_metadata.st_uid != 0)
    ):
        raise ValueError("authorization environment launcher is not host protected")


def authorization_workspace_root(workspace: Path) -> Path:
    """Resolve the checkout root with the protected Git/read-only environment."""

    requested = workspace.resolve(strict=True)
    git = _trusted_git_executable(requested)
    return _git_root(git, requested)


def execute_pinned_git_push(
    operation: GitPushOperationV1,
    *,
    workspace: Path,
    expected_source_tree_sha: str,
    revalidate_authority: Callable[[], None] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Execute a typed push in a config-isolated temporary bare Git view."""

    requested_workspace = workspace.resolve()
    git = _trusted_git_executable(requested_workspace)
    root = _git_root(git, requested_workspace)
    if _active_replace_refs(git, root):
        raise ValueError("Git replacement refs make authorized execution ineligible")
    if _repository_identity(git, root) != operation.destination_repository_id:
        raise ValueError("current repository identity differs from the signed destination")
    if _commit_sha(git, root, operation.source_commit_sha) != operation.source_commit_sha:
        raise ValueError("current repository lacks the exact signed source commit")
    if _tree_sha(git, root, operation.source_commit_sha) != expected_source_tree_sha:
        raise ValueError("raw signed source tree differs from the verified tree")

    with tempfile.TemporaryDirectory(prefix="agents-shipgate-authorized-push-") as raw:
        execution_root = Path(raw)
        git_dir = execution_root / "git"
        for relative in ("objects/info", "objects/pack", "refs/heads", "refs/tags"):
            (git_dir / relative).mkdir(parents=True)
        uses_sha256 = len(operation.source_commit_sha) == 64
        config_lines = [
            "[core]",
            f"\trepositoryformatversion = {1 if uses_sha256 else 0}",
            "\tbare = true",
            "\thooksPath = /dev/null",
        ]
        if uses_sha256:
            config_lines.extend(["[extensions]", "\tobjectFormat = sha256"])
        (git_dir / "config").write_text("\n".join(config_lines) + "\n", encoding="utf-8")
        (git_dir / "HEAD").write_text("ref: refs/heads/shipgate-empty\n", encoding="ascii")
        (execution_root / "home").mkdir()
        (execution_root / "xdg").mkdir()

        snapshot_env = _sanitized_git_environment(
            git=git,
            execution_root=execution_root,
        )
        _copy_verified_source_graph(
            git=git,
            workspace=root,
            git_dir=git_dir,
            source_commit_sha=operation.source_commit_sha,
            env=snapshot_env,
        )
        argv = [
            str(git),
            "--no-replace-objects",
            f"--git-dir={git_dir}",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "http.sslVerify=true",
            "-c",
            "http.followRedirects=false",
        ]
        isolated_tree = _run_isolated_git(
            argv[:3],
            ["rev-parse", f"{operation.source_commit_sha}^{{tree}}"],
            cwd=root,
            env=snapshot_env,
        ).stdout.strip()
        if isolated_tree != expected_source_tree_sha:
            raise ValueError("isolated push source tree differs from the verified tree")

        push_env = dict(snapshot_env)
        push_env["GIT_ALLOW_PROTOCOL"] = "https"
        header = _authorization_header(
            git=git,
            workspace=root,
            push_url=operation.push_url,
        )
        if header is not None:
            push_env["SHIPGATE_HTTP_AUTHORIZATION"] = header
            auth_scope = _https_auth_scope(operation.push_url)
            argv.extend(
                [
                    "--config-env",
                    f"http.{auth_scope}.extraHeader=SHIPGATE_HTTP_AUTHORIZATION",
                ]
            )

        # Graph preparation and credential discovery can take long enough for
        # a grant to expire or a key to be revoked. Reload and re-evaluate
        # authority only after both are ready, immediately before dispatching
        # the sole network-capable subprocess.
        if revalidate_authority is not None:
            revalidate_authority()
        argv.extend(operation.argv()[1:])
        return _run_process(
            argv,
            cwd=root,
            env=push_env,
            text=True,
            capture_output=True,
            check=False,
            timeout=120,
        )


def _sanitized_git_environment(
    *,
    git: Path,
    execution_root: Path,
) -> dict[str, str]:
    # Start from a small display-only allowlist. In particular, do not inherit
    # loader injection, shell startup, proxy, CA override, SSL key logging, or
    # Git config/transport variables from the coding-agent process.
    env = {
        key: os.environ[key]
        for key in ("LANG", "LC_ALL", "LC_CTYPE")
        if key in os.environ
    }
    env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_EXEC_PATH": str(_trusted_git_exec_path(git)),
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_ALLOW_PROTOCOL": "",
            "GIT_PROTOCOL_FROM_USER": "0",
            "HOME": str(execution_root / "home"),
            "XDG_CONFIG_HOME": str(execution_root / "xdg"),
            "NO_PROXY": "*",
            "no_proxy": "*",
            "PATH": os.pathsep.join(
                dict.fromkeys(
                    [
                        str(git.parent),
                        "/usr/bin",
                        "/bin",
                        "/usr/sbin",
                        "/sbin",
                    ]
                )
            ),
        }
    )
    return env


def _authorization_header(*, git: Path, workspace: Path, push_url: str) -> str | None:
    result = _run_process(
        [
            str(git),
            "--no-replace-objects",
            "-C",
            str(workspace),
            "config",
            "--get-urlmatch",
            "http.extraheader",
            push_url,
        ],
        capture_output=True,
        env=_read_only_git_environment(git),
        text=True,
        check=False,
        timeout=10,
    )
    if result.returncode not in {0, 1}:
        raise ValueError("could not read the repository's scoped HTTPS authorization header")
    headers = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not headers:
        return None
    if len(headers) != 1 or not _AUTHORIZATION_HEADER_RE.fullmatch(headers[0]):
        raise ValueError("repository HTTPS authorization header is ambiguous or unsafe")
    return headers[0]


def _https_auth_scope(push_url: str) -> str:
    # Scope copied credentials to the exact canonical repository endpoint.
    return push_url


def _trusted_git_executable(workspace: Path) -> Path:
    candidate = Path("/usr/bin/git").resolve(strict=True)
    if _path_is_within(candidate, workspace):
        raise ValueError("host Git executable resolves inside the workspace")
    _require_root_protected_executable(candidate, label="Git executable")
    transport = _trusted_git_exec_path(candidate) / "git-remote-https"
    _require_root_protected_executable(transport.resolve(strict=True), label="Git HTTPS helper")
    return candidate


def _trusted_git_exec_path(git: Path) -> Path:
    exec_path_result = _run_process(
        [str(git), "--exec-path"],
        capture_output=True,
        env=_read_only_git_environment(git, include_exec_path=False),
        text=True,
        check=False,
        timeout=10,
    )
    if exec_path_result.returncode != 0:
        raise ValueError("could not resolve the protected Git transport directory")
    exec_path = Path(exec_path_result.stdout.strip()).resolve(strict=True)
    if not exec_path.is_dir():
        raise ValueError("protected Git transport path is not a directory")
    return exec_path


def _git_root(git: Path, workspace: Path) -> Path:
    result = _run_git(git, workspace, ["rev-parse", "--show-toplevel"])
    root = Path(result.stdout.strip()).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("authorization workspace is not inside a Git checkout")
    return root


def _commit_sha(git: Path, workspace: Path, ref: str) -> str | None:
    result = _run_git(
        git,
        workspace,
        ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        check=False,
    )
    return result.stdout.strip() or None if result.returncode == 0 else None


def _tree_sha(git: Path, workspace: Path, ref: str) -> str | None:
    result = _run_git(
        git,
        workspace,
        ["rev-parse", "--verify", "--quiet", f"{ref}^{{tree}}"],
        check=False,
    )
    return result.stdout.strip() or None if result.returncode == 0 else None


def _active_replace_refs(git: Path, workspace: Path) -> list[str]:
    result = _run_git(
        git,
        workspace,
        ["for-each-ref", "--format=%(refname)", "refs/replace/"],
        check=False,
    )
    if result.returncode != 0:
        raise ValueError("could not inspect Git replacement refs")
    return sorted(line.strip() for line in result.stdout.splitlines() if line.strip())


def _copy_verified_source_graph(
    *,
    git: Path,
    workspace: Path,
    git_dir: Path,
    source_commit_sha: str,
    env: dict[str, str],
) -> None:
    """Pack one source graph into isolated storage and fsck it before push."""

    pack_path = git_dir.parent / "reachable.pack"
    with pack_path.open("wb") as output:
        packed = _run_bounded_stdout_process(
            [
                str(git),
                "--no-replace-objects",
                "-C",
                str(workspace),
                "pack-objects",
                "--stdout",
                "--revs",
                "--missing=error",
            ],
            input=f"{source_commit_sha}\n".encode("ascii"),
            output=output,
            env=env,
            max_bytes=_MAX_AUTHORIZED_GRAPH_PACK_BYTES,
            timeout=120,
        )
    if packed.returncode != 0:
        detail = packed.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"could not snapshot the signed Git object graph: {detail}")
    if pack_path.stat().st_size > _MAX_AUTHORIZED_GRAPH_PACK_BYTES:
        raise ValueError("signed Git object graph exceeds the 512 MiB execution limit")
    with pack_path.open("rb") as source:
        indexed = _run_process(
            [
                str(git),
                "--no-replace-objects",
                f"--git-dir={git_dir}",
                "index-pack",
                "--stdin",
            ],
            stdin=source,
            capture_output=True,
            check=False,
            env=env,
            timeout=120,
        )
    if indexed.returncode != 0:
        detail = indexed.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"signed Git object snapshot failed index validation: {detail}")
    checked = _run_isolated_git(
        [str(git), "--no-replace-objects", f"--git-dir={git_dir}"],
        ["fsck", "--strict", "--no-reflogs", "--no-dangling", source_commit_sha],
        cwd=workspace,
        env=env,
    )
    if checked.returncode != 0:  # pragma: no cover - helper raises first
        raise ValueError("signed Git object snapshot failed integrity validation")


def _repository_identity(git: Path, workspace: Path) -> str:
    result = _run_git(git, workspace, ["remote", "get-url", "origin"], check=False)
    if result.returncode != 0:
        raise ValueError("current repository has no origin identity")
    value = result.stdout.strip()
    parsed = urlsplit(value) if "://" in value else None
    if parsed is not None:
        host = (parsed.hostname or "").lower()
        path = parsed.path
    else:
        match = re.fullmatch(r"(?:[^@/:]+@)?([^/:]+):(.+)", value)
        if match is None:
            raise ValueError("current origin is not a stable network repository URL")
        host, path = match.group(1).lower(), match.group(2)
    normalized = path.strip("/")
    if normalized.endswith(".git"):
        normalized = normalized[:-4]
    if not host or not normalized:
        raise ValueError("current origin has no credential-free repository identity")
    return f"{host}/{normalized}"


def _run_git(
    git: Path,
    workspace: Path,
    args: list[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return _run_process(
        [str(git), "--no-replace-objects", "-C", str(workspace), *args],
        capture_output=True,
        env=_read_only_git_environment(git),
        text=True,
        check=check,
        timeout=10,
    )


def _run_isolated_git(
    prefix: list[str],
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    result = _run_process(
        [*prefix, *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit {result.returncode}"
        raise ValueError(f"could not resolve the isolated push source: {detail}")
    return result


def _run_process(
    cmd: list[str],
    *,
    capture_output: bool = False,
    check: bool = False,
    env: dict[str, str],
    text: bool = False,
    timeout: int,
    cwd: Path | None = None,
    input: bytes | None = None,
    stdout: Any = None,
    stderr: Any = None,
    stdin: Any = None,
) -> subprocess.CompletedProcess:
    """Single audited no-shell boundary for protected Git execution."""

    try:
        return subprocess.run(
            cmd,
            capture_output=capture_output,
            check=check,
            cwd=cwd,
            env=env,
            input=input,
            stderr=stderr,
            stdin=stdin,
            stdout=stdout,
            text=text,
            timeout=timeout,
        )
    except subprocess.SubprocessError as exc:
        raise ValueError(f"protected Git subprocess failed: {exc}") from exc


def _run_bounded_stdout_process(
    cmd: list[str],
    *,
    input: bytes,
    output: Any,
    env: dict[str, str],
    max_bytes: int,
    timeout: int,
) -> subprocess.CompletedProcess[bytes]:
    """Stream one process stdout to disk without trusting its output size.

    Stderr goes to a separate temporary file, so neither pipe can deadlock.
    The parent enforces both the byte ceiling and wall-clock deadline; this
    avoids ``preexec_fn``, which is unsafe when a protected broker has threads.
    """

    deadline = time.monotonic() + timeout
    try:
        process = subprocess.Popen(
            cmd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        raise ValueError(f"protected Git subprocess failed to start: {exc}") from exc

    captured_stderr = bytearray()
    stderr_truncated = False
    try:
        if (
            process.stdin is None
            or process.stdout is None
            or process.stderr is None
        ):  # pragma: no cover - fixed argv.
            raise ValueError("protected Git subprocess pipes were unavailable")
        try:
            process.stdin.write(input)
        except BrokenPipeError:
            pass
        finally:
            try:
                process.stdin.close()
            except BrokenPipeError:
                pass

        written = 0
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(cmd, timeout)
                events = selector.select(remaining)
                if not events:
                    raise subprocess.TimeoutExpired(cmd, timeout)
                for key, _mask in events:
                    chunk = os.read(key.fd, 1024 * 1024)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    if key.data == "stdout":
                        capacity = max_bytes - written
                        if len(chunk) > capacity:
                            raise ValueError(
                                "signed Git object graph exceeds the 512 MiB execution limit"
                            )
                        output.write(chunk)
                        written += len(chunk)
                        continue
                    remaining_stderr = (
                        _MAX_AUTHORIZED_PROCESS_STDERR_BYTES - len(captured_stderr)
                    )
                    if remaining_stderr > 0:
                        captured_stderr.extend(chunk[:remaining_stderr])
                    stderr_truncated = stderr_truncated or len(chunk) > remaining_stderr

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(cmd, timeout)
        returncode = process.wait(timeout=remaining)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("signed Git object graph exceeded the 120-second timeout") from exc
    finally:
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()
        if process.stdin is not None and not process.stdin.closed:
            process.stdin.close()

    if stderr_truncated:
        captured_stderr.extend(b"\n[stderr truncated by Agents Shipgate]\n")
    return subprocess.CompletedProcess(
        cmd,
        returncode,
        stdout=None,
        stderr=bytes(captured_stderr),
    )


def _read_only_git_environment(
    git: Path,
    *,
    include_exec_path: bool = True,
) -> dict[str, str]:
    env = {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_ALLOW_PROTOCOL": "",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_PROTOCOL_FROM_USER": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "HOME": os.devnull,
        "PATH": os.pathsep.join((str(git.parent), "/usr/bin", "/bin")),
    }
    if include_exec_path:
        env["GIT_EXEC_PATH"] = str(_trusted_git_exec_path(git))
    return env


def _require_root_protected_executable(path: Path, *, label: str) -> None:
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode) or not os.access(path, os.X_OK):
        raise ValueError(f"{label} is not an executable regular file")
    if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise ValueError(f"{label} is group/world writable")
    if hasattr(metadata, "st_uid") and metadata.st_uid != 0:
        raise ValueError(f"{label} is not owned by the host root account")
    for parent in path.parents:
        parent_metadata = parent.stat()
        if parent_metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise ValueError(f"{label} has an unprotected ancestor directory")
        if hasattr(parent_metadata, "st_uid") and parent_metadata.st_uid != 0:
            raise ValueError(f"{label} has a non-host-owned ancestor directory")


def _path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


__all__ = [
    "authorization_execute_command",
    "authorization_workspace_root",
    "ensure_authorization_runtime_is_external",
    "execute_pinned_git_push",
]
