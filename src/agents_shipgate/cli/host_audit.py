"""``shipgate audit --host`` CLI wrapper."""

from __future__ import annotations

import json
import os
import shlex
import stat
import tempfile
from pathlib import Path

import typer

from agents_shipgate.cli.agent_mode import emit_agent_mode_error_action
from agents_shipgate.core.host_grants import (
    DEFAULT_BASELINE_FILE,
    HOST_GRANTS_INVENTORY_SCHEMA_VERSION,
    HOST_GRANTS_SCHEMA_VERSION,
    INCOMPARABLE_BASELINE_REVIEW,
    build_host_drift_payload,
    build_host_grants_baseline,
    diff_host_grants,
    host_audit_inventory,
    host_grant_expansion_signals,
    host_grants_sha256,
    inventory_is_complete,
    load_host_grants_baseline,
    normalized_host_grants,
    redacted_config_sha256,
    render_host_audit_markdown,
    render_host_drift_markdown,
)
from agents_shipgate.schemas.diagnostics import NextAction

_BaselineFileState = tuple[os.stat_result, str]


def _io_error(message: str, *, next_action: str) -> typer.Exit:
    """Report a filesystem failure on both channels.

    ``docs/errors.json`` gives filesystem and unexpected failures
    ``other_error`` and exit 4; reporting them as ``config_error``/2 tells an
    agent to go re-read its flags when the flags were fine and the path was
    not writable.
    """

    typer.echo(message, err=True)
    emit_agent_mode_error_action(
        "other_error",
        message=message,
        exit_code=4,
        action=NextAction(kind="review", why=next_action),
    )
    return typer.Exit(4)


def _config_error(
    message: str,
    *,
    next_action: str,
    command: str | None = "agents-shipgate audit --host",
) -> typer.Exit:
    """Report flag misuse on both channels and return the exit to raise.

    Agent-facing docs promise that with ``AGENTS_SHIPGATE_AGENT_MODE=1`` a
    failing command emits a structured error line on stderr, carrying both the
    legacy ``next_action`` string and the ranked ``next_actions`` array.
    ``audit`` printed prose only, so an agent that mis-invoked it had to parse
    English or guess.
    """

    typer.echo(message, err=True)
    action = (
        NextAction(
            kind="command",
            command=command,
            why=next_action,
            expects="A host-capability audit that completes.",
        )
        if command
        else NextAction(kind="review", why=next_action)
    )
    emit_agent_mode_error_action(
        "config_error",
        message=message,
        exit_code=2,
        action=action,
    )
    return typer.Exit(2)


def audit(
    workspace: Path = typer.Option(
        Path("."),
        "--workspace",
        help="Workspace to inventory.",
    ),
    host: bool = typer.Option(
        False,
        "--host",
        help="Inventory coding-agent host grants (MCP servers, permission rules, hooks, workflow scopes).",
    ),
    scope: str = typer.Option(
        "repository",
        "--scope",
        help=(
            "Static inventory scope: repository (default, portable) or "
            "local-static (also reads supported on-disk user/managed config)."
        ),
    ),
    save_baseline: bool = typer.Option(
        False,
        "--save-baseline",
        help=(
            "Record the current host-grant inventory as the acknowledged "
            "baseline (writes the --baseline-file)."
        ),
    ),
    drift: bool = typer.Option(
        False,
        "--drift",
        help="Diff the current host grants against the saved baseline and report drift.",
    ),
    baseline_file: Path = typer.Option(
        DEFAULT_BASELINE_FILE,
        "--baseline-file",
        help="Host-grants baseline location (committed; default .agents-shipgate/host-grants.json).",
    ),
    fail_on_drift: bool = typer.Option(
        False,
        "--fail-on-drift",
        help="With --drift: exit 20 when any drift is found (for scheduled CI gates).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit JSON instead of Markdown.",
    ),
    out: Path | None = typer.Option(
        None,
        "--out",
        help="Write the JSON payload to this path in addition to normal output.",
    ),
) -> None:
    """Zero-config, read-only audits. Currently supports --host."""

    if not host:
        raise _config_error(
            "Nothing to audit: pass --host for the host-capability inventory.",
            next_action="Re-run as `agents-shipgate audit --host`.",
        )
    if scope not in {"repository", "local-static"}:
        raise _config_error(
            "--scope must be 'repository' or 'local-static'.",
            next_action="Re-run audit with --scope repository or --scope local-static.",
        )
    if save_baseline and drift:
        raise _config_error(
            "--save-baseline and --drift are mutually exclusive: record the "
            "acknowledged state or compare against it, not both.",
            next_action="Re-run audit with either --save-baseline or --drift, not both.",
        )
    if fail_on_drift and not drift:
        raise _config_error(
            "--fail-on-drift requires --drift.",
            next_action="Re-run audit with --drift, or drop --fail-on-drift.",
        )

    inventory_scope = "local_static" if scope == "local-static" else "repository"
    inventory = host_audit_inventory(workspace, scope=inventory_scope)
    resolved_baseline = (
        baseline_file
        if baseline_file.is_absolute()
        else workspace.resolve() / baseline_file
    )

    if save_baseline:
        write_target = _baseline_write_target(
            workspace=workspace,
            baseline_file=baseline_file,
        )
        existing = _refuse_invalid_baseline_overwrite(write_target)
        try:
            payload = build_host_grants_baseline(inventory)
        except ValueError as exc:
            raise _config_error(
                str(exc),
                next_action=(
                    "Resolve the incomplete host inventory, then re-run "
                    "`agents-shipgate audit --host --save-baseline`."
                ),
            ) from exc
        text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        try:
            if existing is not None and existing[1] == text:
                status = "unchanged"
            else:
                _atomic_write_baseline(
                    write_target,
                    text,
                    expected=existing,
                )
                status = "updated" if existing is not None else "created"
        except OSError as exc:
            # --baseline-file naming a directory raised IsADirectoryError
            # straight through typer: a traceback and exit 1.
            raise _io_error(
                f"Could not write the host-grants baseline {baseline_file}: {exc}",
                next_action=(
                    "Point --baseline-file at a writable file path, then re-run "
                    "the audit."
                ),
            ) from exc
        outcome = {
            "baseline_file": str(baseline_file),
            "inventory_sha256": payload["inventory_sha256"],
            "scope": payload["scope"],
            "status": status,
        }
        _write_json_out(out, outcome)
        if json_output:
            typer.echo(json.dumps(outcome, indent=2, sort_keys=True))
        else:
            typer.echo(
                f"Host-grants baseline {status}: {baseline_file} "
                f"(sha256 {payload['inventory_sha256'][:12]}…). Commit it; "
                "verify treats .agents-shipgate/ edits as trust-root changes."
            )
        return

    if drift:
        try:
            baseline = load_host_grants_baseline(resolved_baseline)
        except ValueError as exc:
            # The loader converts every read OSError into ValueError, so the
            # I/O cause has to be recovered from __cause__ — otherwise a
            # directory passed as --baseline-file is reported as flag misuse
            # and the recovery drops the drift request entirely.
            # A *missing* baseline is the documented "record one first" case
            # and keeps its config_error/2 contract; only a path that exists
            # and cannot be read is a filesystem failure.
            if isinstance(exc.__cause__, OSError) and not isinstance(
                exc.__cause__, FileNotFoundError
            ):
                raise _io_error(
                    f"Could not read the host-grants baseline {baseline_file}: "
                    f"{exc.__cause__}",
                    next_action=(
                        "Point --baseline-file at a readable baseline file, "
                        "then re-run the audit."
                    ),
                ) from exc
            # A baseline that exists but does not load — malformed, unknown
            # schema, integrity-failed — must never be recovered by writing
            # over it. Recommending --save-baseline there replaced the failed
            # artifact with the *current* grants, silently acknowledging them
            # and destroying the evidence a human needed to look at. Only a
            # genuinely absent baseline can be recorded without losing
            # anything, and that command carries the scope it was asked for.
            missing = isinstance(
                exc.__cause__, FileNotFoundError
            ) and not resolved_baseline.is_symlink()
            if missing:
                _baseline_write_target(
                    workspace=workspace,
                    baseline_file=baseline_file,
                )
            raise _config_error(
                (
                    _missing_baseline_error_message(
                        exc,
                        baseline_file=baseline_file,
                    )
                    if missing
                    else _existing_baseline_error_message(
                        exc,
                        baseline_file=baseline_file,
                    )
                ),
                next_action=(
                    "Record the first baseline for this exact workspace, "
                    "scope, and target."
                    if missing
                    else "Inspect the existing baseline file and repair or "
                    "replace it deliberately; do not overwrite it with the "
                    "current grants, which would acknowledge them unreviewed."
                ),
                command=(
                    "agents-shipgate audit --host --workspace "
                    f"{shlex.quote(str(workspace))} --scope {shlex.quote(scope)} "
                    f"--save-baseline --baseline-file "
                    f"{shlex.quote(str(baseline_file))}"
                    if missing
                    else None
                ),
            ) from exc
        payload = build_host_drift_payload(
            baseline=baseline,
            inventory=inventory,
            baseline_file=str(baseline_file),
        )
        if json_output:
            _write_json_out(out, payload)
            typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        else:
            _write_json_out(out, payload)
            typer.echo(render_host_drift_markdown(payload), nl=False)
        if fail_on_drift and (
            payload["comparison_status"] != "comparable" or payload["has_drift"]
        ):
            raise typer.Exit(20)
        return

    if json_output:
        _write_json_out(out, inventory)
        typer.echo(json.dumps(inventory, indent=2, sort_keys=True))
        return
    _write_json_out(out, inventory)
    typer.echo(render_host_audit_markdown(inventory), nl=False)


def _baseline_write_target(*, workspace: Path, baseline_file: Path) -> Path:
    workspace_root = workspace.resolve()
    raw_target = (
        baseline_file
        if baseline_file.is_absolute()
        else workspace_root / baseline_file
    )
    target = Path(os.path.abspath(raw_target))
    if (
        not baseline_file.is_absolute()
        and target != workspace_root
        and workspace_root not in target.parents
    ):
        raise _unsafe_baseline_path(
            target,
            "a relative --baseline-file must stay inside --workspace",
        )
    _reject_baseline_symlinks(target)
    return target


def _reject_baseline_symlinks(target: Path) -> None:
    try:
        redirected = target.resolve() != target
    except (OSError, RuntimeError) as exc:
        raise _unsafe_baseline_path(target, f"could not resolve the path: {exc}") from exc
    if redirected:
        raise _unsafe_baseline_path(target, "the path contains a symbolic link")


def _unsafe_baseline_path(path: Path, reason: str) -> typer.Exit:
    return _config_error(
        f"Refusing to write host-grants baseline {path}: {reason}.",
        next_action=(
            "Choose a regular, non-linked baseline path. Relative baseline "
            "paths must remain inside the requested workspace."
        ),
        command=None,
    )


def _file_identity(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        value.st_dev, value.st_ino, value.st_size,
        value.st_mtime_ns, value.st_nlink, value.st_mode,
    )


def _refuse_invalid_baseline_overwrite(
    baseline_file: Path,
) -> _BaselineFileState | None:
    try:
        before = baseline_file.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _unsafe_baseline_path(
            baseline_file,
            f"could not inspect the existing target: {exc}",
        ) from exc
    if stat.S_ISDIR(before.st_mode):
        return None
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise _unsafe_baseline_path(
            baseline_file,
            "the existing target is not a single-link regular file",
        )
    try:
        baseline = load_host_grants_baseline(baseline_file)
    except ValueError as exc:
        raise _config_error(
            _existing_baseline_error_message(exc, baseline_file=baseline_file),
            next_action=INCOMPARABLE_BASELINE_REVIEW,
            command=None,
        ) from exc
    if (
        baseline.get("host_grants_schema_version") != HOST_GRANTS_SCHEMA_VERSION
        or baseline.get("_load_error")
    ):
        reason = str(baseline.get("_load_error") or "unsupported_baseline_schema")
        raise _config_error(
            f"Refusing to overwrite existing host-grants baseline "
            f"{baseline_file}: {reason}. The file was left unchanged.",
            next_action=INCOMPARABLE_BASELINE_REVIEW,
            command=None,
        )
    try:
        text = baseline_file.read_text(encoding="utf-8")
        after = baseline_file.lstat()
    except OSError as exc:
        raise _unsafe_baseline_path(
            baseline_file,
            f"the existing target changed or became unreadable: {exc}",
        ) from exc
    if _file_identity(before) != _file_identity(after):
        raise _unsafe_baseline_path(
            baseline_file,
            "the existing target changed while it was being validated",
        )
    return after, text


def _atomic_write_baseline(
    target: Path,
    text: str,
    *,
    expected: _BaselineFileState | None,
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    _reject_baseline_symlinks(target)
    expected_stat = expected[0] if expected is not None else None

    def ensure_unchanged() -> None:
        try:
            current = target.lstat()
        except FileNotFoundError:
            current = None
        if expected_stat is None:
            if current is None:
                return
            if stat.S_ISDIR(current.st_mode):
                raise IsADirectoryError(f"{target} is a directory")
            raise _unsafe_baseline_path(
                target,
                "the target appeared while the baseline was being prepared",
            )
        if current is not None and stat.S_ISDIR(current.st_mode):
            raise IsADirectoryError(f"{target} is a directory")
        if current is None or _file_identity(current) != _file_identity(expected_stat):
            raise _unsafe_baseline_path(
                target,
                "the target changed while the baseline was being prepared",
            )

    ensure_unchanged()
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            delete=False,
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
        ) as handle:
            temp_path = Path(handle.name)
            temp_path.chmod(
                stat.S_IMODE(expected_stat.st_mode)
                if expected_stat is not None
                else 0o644
            )
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        _reject_baseline_symlinks(target)
        ensure_unchanged()
        os.replace(temp_path, target)
        temp_path = None
        _fsync_directory(target.parent)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _fsync_directory(directory: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(directory, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _baseline_error_detail(exc: ValueError) -> str:
    detail = str(exc)
    for marker in (
        " After human review, re-record it:",
        " Re-record it:",
        " Record one first:",
    ):
        if marker in detail:
            detail = detail.split(marker, 1)[0]
            break
    return detail.rstrip().rstrip(".")


def _missing_baseline_error_message(
    exc: ValueError,
    *,
    baseline_file: Path,
) -> str:
    """Describe absence without repeating the loader's unscoped command."""

    return (
        f"{_baseline_error_detail(exc)}. No baseline was written to "
        f"{baseline_file}."
    )


def _existing_baseline_error_message(exc: ValueError, *, baseline_file: Path) -> str:
    """Preserve the loader diagnosis without its destructive rerun advice."""

    return (
        f"Refusing to overwrite existing host-grants baseline "
        f"{baseline_file}: {_baseline_error_detail(exc)}. "
        "The file was left unchanged."
    )


def _write_json_out(out: Path | None, payload: dict) -> None:
    if out is None:
        return
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        # An unwritable --out reached the user as a Rich traceback and exit 1,
        # which is neither the documented exit code nor something an agent can
        # route on.
        raise _io_error(
            f"Could not write --out {out}: {exc}",
            next_action=(
                "Point --out at a writable file path, then re-run the audit."
            ),
        ) from exc


__all__ = [
    "DEFAULT_BASELINE_FILE",
    "HOST_GRANTS_INVENTORY_SCHEMA_VERSION",
    "HOST_GRANTS_SCHEMA_VERSION",
    "audit",
    "build_host_drift_payload",
    "build_host_grants_baseline",
    "diff_host_grants",
    "host_audit_inventory",
    "host_grant_expansion_signals",
    "host_grants_sha256",
    "inventory_is_complete",
    "load_host_grants_baseline",
    "normalized_host_grants",
    "redacted_config_sha256",
    "render_host_audit_markdown",
    "render_host_drift_markdown",
]
