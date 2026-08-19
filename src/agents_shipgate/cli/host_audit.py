"""``shipgate audit --host`` CLI wrapper."""

from __future__ import annotations

import errno
import json
import os
import shlex
import stat
import tempfile
from pathlib import Path

import typer

from agents_shipgate.cli.agent_mode import emit_agent_mode_error_action
from agents_shipgate.cli.workspace_guard import require_workspace
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
    load_host_grants_baseline_with_text,
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
    command: str | None = None,
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


def _audit_recovery_command(
    *,
    workspace: Path,
    host: bool,
    scope: str,
    save_baseline: bool,
    drift: bool,
    baseline_file: Path,
    fail_on_drift: bool,
    json_output: bool,
    out: Path | None,
) -> str:
    """Serialize one complete, shell-safe host-audit request.

    Recovery actions are an operational control surface: dropping a target or
    mode flag can make an agent inspect a different workspace or answer a
    different question.  Keep every effective request field, including output
    options, and let callers explicitly alter only the field they are fixing.
    """

    exact_workspace = Path(
        os.path.abspath(os.path.normpath(os.fspath(workspace)))
    )
    argv = ["agents-shipgate", "audit"]
    if host:
        argv.append("--host")
    argv.extend(("--workspace", str(exact_workspace), "--scope", scope))
    if save_baseline:
        argv.append("--save-baseline")
    if drift:
        argv.append("--drift")
    if save_baseline or drift or baseline_file != DEFAULT_BASELINE_FILE:
        argv.extend(("--baseline-file", str(baseline_file)))
    if fail_on_drift:
        argv.append("--fail-on-drift")
    if json_output:
        argv.append("--json")
    if out is not None:
        exact_out = (
            out
            if out.is_absolute()
            else Path(os.path.abspath(os.path.normpath(os.fspath(Path.cwd() / out))))
        )
        argv.extend(("--out", str(exact_out)))
    return shlex.join(argv)


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
    require_workspace(workspace)

    if not host:
        command = (
            _audit_recovery_command(
                workspace=workspace,
                host=True,
                scope=scope,
                save_baseline=save_baseline,
                drift=drift,
                baseline_file=baseline_file,
                fail_on_drift=fail_on_drift,
                json_output=json_output,
                out=out,
            )
            if (
                scope in {"repository", "local-static"}
                and not (save_baseline and drift)
                and not (fail_on_drift and not drift)
            )
            else None
        )
        raise _config_error(
            "Nothing to audit: pass --host for the host-capability inventory.",
            next_action="Re-run as `agents-shipgate audit --host`.",
            command=command,
        )
    if scope not in {"repository", "local-static"}:
        raise _config_error(
            "--scope must be 'repository' or 'local-static'.",
            next_action=(
                "Choose whether this request needs repository-only or local-static "
                "host coverage, then re-run it with that scope."
            ),
            command=None,
        )
    if save_baseline and drift:
        raise _config_error(
            "--save-baseline and --drift are mutually exclusive: record the "
            "acknowledged state or compare against it, not both.",
            next_action=(
                "Choose whether to acknowledge the current grants or compare them "
                "with the baseline, then re-run the original request with exactly "
                "one of --save-baseline or --drift."
            ),
            command=None,
        )
    if fail_on_drift and not drift:
        raise _config_error(
            "--fail-on-drift requires --drift.",
            next_action=(
                "Choose whether to run a drift gate or an advisory inventory, then "
                "re-run the original request with compatible flags."
            ),
            command=None,
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
        _reject_out_baseline_alias(out, write_target)
        existing = _refuse_invalid_baseline_overwrite(write_target)
        try:
            payload = build_host_grants_baseline(inventory)
        except ValueError as exc:
            coverage_review = _incomplete_inventory_review(inventory)
            raise _config_error(
                str(exc),
                next_action=coverage_review,
                command=None,
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
        _reject_out_baseline_alias(out, write_target)
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
        _reject_out_baseline_alias(out, resolved_baseline)
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
            # and destroying the evidence a human needed to look at.
            # A genuinely absent baseline is distinguishable from a malformed
            # one, but recording it still acknowledges the current grants.
            # The failed read-only drift request therefore routes to a human
            # instead of authorizing a state-changing save command.
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
                    "Human review is required before creating the first acknowledged "
                    f"host-grants baseline at {baseline_file}. Review the current "
                    f"{scope} inventory for workspace {workspace}, then record the "
                    "baseline only in a separate, explicitly approved request."
                    if missing
                    else "Inspect the existing baseline file and repair or "
                    "replace it deliberately; do not overwrite it with the "
                    "current grants, which would acknowledge them unreviewed."
                ),
                command=None,
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


def _incomplete_inventory_review(inventory: dict[str, object]) -> str:
    """Describe the exact coverage gap without authorizing baseline acceptance."""

    coverage_rows = inventory.get("host_coverage")
    incomplete: list[str] = []
    if isinstance(coverage_rows, list):
        for row in coverage_rows:
            if not isinstance(row, dict) or row.get("status") == "complete":
                continue
            host = str(row.get("host") or "unknown-host")
            status = str(row.get("status") or "unknown")
            issue_ids = [
                str(value)
                for value in (row.get("issue_ids") or [])
                if isinstance(value, str)
            ]
            suffix = f" ({', '.join(issue_ids)})" if issue_ids else ""
            incomplete.append(f"{host}={status}{suffix}")

    issue_rows = inventory.get("issues")
    sources: list[str] = []
    if isinstance(issue_rows, list):
        for issue in issue_rows:
            if not isinstance(issue, dict) or not issue.get("blocking"):
                continue
            host = str(issue.get("host") or "unknown-host")
            source = str(issue.get("source") or "unknown-source")
            sources.append(f"{host}:{source}")

    coverage_text = ", ".join(incomplete) or "coverage status unavailable"
    source_text = ", ".join(sources) or "no blocking source was identified"
    return (
        "Human review is required before acknowledging this inventory. Repair "
        f"the incomplete coverage ({coverage_text}); blocking sources: "
        f"{source_text}. Inspect a fresh host inventory after repair, then make "
        "a separate explicit baseline-acknowledgement decision."
    )


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
    except RuntimeError as exc:
        raise _unsafe_baseline_path(target, f"could not resolve the path: {exc}") from exc
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise _unsafe_baseline_path(
                target,
                f"the path contains a symbolic-link loop: {exc}",
            ) from exc
        raise _io_error(
            f"Could not inspect host-grants baseline path {target}: {exc}",
            next_action=(
                "Inspect the baseline path and its parent permissions, then re-run "
                "the audit."
            ),
        ) from exc
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


def _file_identity(
    value: os.stat_result,
) -> tuple[int, int, int, int, int, int, int]:
    return (
        value.st_dev, value.st_ino, value.st_size,
        value.st_mtime_ns, value.st_ctime_ns, value.st_nlink, value.st_mode,
    )


def _refuse_invalid_baseline_overwrite(
    baseline_file: Path,
) -> _BaselineFileState | None:
    try:
        before = baseline_file.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise _unsafe_baseline_path(
                baseline_file,
                f"the path contains a symbolic-link loop: {exc}",
            ) from exc
        raise _io_error(
            f"Could not inspect existing host-grants baseline {baseline_file}: {exc}",
            next_action=(
                "Inspect the baseline path and its permissions, then re-run the "
                "audit."
            ),
        ) from exc
    if stat.S_ISDIR(before.st_mode):
        return None
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise _unsafe_baseline_path(
            baseline_file,
            "the existing target is not a single-link regular file",
        )
    try:
        baseline, text = load_host_grants_baseline_with_text(baseline_file)
    except ValueError as exc:
        if isinstance(exc.__cause__, OSError):
            if exc.__cause__.errno == errno.ELOOP:
                raise _unsafe_baseline_path(
                    baseline_file,
                    f"the path contains a symbolic-link loop: {exc.__cause__}",
                ) from exc
            raise _io_error(
                f"Could not read existing host-grants baseline {baseline_file}: "
                f"{exc.__cause__}",
                next_action=(
                    "Inspect the baseline file and its permissions, then re-run "
                    "the audit."
                ),
            ) from exc
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
        after = baseline_file.lstat()
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise _unsafe_baseline_path(
                baseline_file,
                f"the path contains a symbolic-link loop: {exc}",
            ) from exc
        raise _io_error(
            f"Could not finish inspecting host-grants baseline {baseline_file}: {exc}",
            next_action=(
                "Inspect the baseline file and its permissions, then re-run the "
                "audit."
            ),
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
    temp_path: Path | None = None
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=out.parent,
            prefix=f".{out.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            temp_path = Path(handle.name)
        # Replacing the directory entry does not follow a last-moment symlink
        # or hardlink at ``out`` and therefore cannot write through it into
        # baseline evidence.
        os.replace(temp_path, out)
        temp_path = None
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
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _reject_out_baseline_alias(out: Path | None, baseline: Path) -> None:
    """Keep report output from truncating acknowledged baseline evidence."""

    if out is None:
        return
    output = Path(os.path.abspath(os.path.normpath(os.fspath(out))))
    baseline_path = Path(
        os.path.abspath(os.path.normpath(os.fspath(baseline)))
    )
    aliases = output == baseline_path
    try:
        aliases = aliases or output.resolve() == baseline_path.resolve()
    except (OSError, RuntimeError):
        pass
    if not aliases:
        try:
            aliases = os.path.samestat(output.stat(), baseline_path.stat())
        except OSError:
            pass
    if aliases:
        raise _config_error(
            f"--out must not alias --baseline-file ({baseline}).",
            next_action=(
                "Choose a distinct output path so audit reporting cannot "
                "overwrite acknowledged baseline evidence."
            ),
            command=None,
        )


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
