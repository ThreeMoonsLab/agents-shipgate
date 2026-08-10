from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from agents_shipgate.cli.agent_mode import emit_agent_mode_error
from agents_shipgate.cli.verify.git import (
    commit_sha,
    ensure_git_workspace,
    merge_base_sha,
    repository_identity,
    tree_sha,
    working_tree_context,
)
from agents_shipgate.core.agent_control_envelope import (
    AgentControlRouteUnavailable,
    envelope_from_pointer,
    render_agent_control_envelope,
)
from agents_shipgate.core.agent_handoff import build_agent_handoff
from agents_shipgate.core.current_control import (
    CurrentControlUnavailable,
    LiveWorkspace,
    read_current_control,
)
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.schemas.contract import COMMANDS, DEFAULT_PATHS
from agents_shipgate.schemas.current_control import (
    CURRENT_CONTROL_ARTIFACT_NAME,
    CurrentControlPointer,
)
from agents_shipgate.schemas.diagnostics import NextAction
from agents_shipgate.schemas.verifier import VerifierArtifact

agent_app = typer.Typer(
    help="Agent-native projection commands.",
    no_args_is_help=True,
)

# Refusal reason -> (agent-mode error kind, exit code). Reasons about the
# artifact set itself map to the missing/parse family; reasons about currency
# map to "other". Anything unlisted falls through to the conservative 4.
_UNAVAILABLE_EXIT: dict[str, tuple[str, int]] = {
    "missing": ("input_parse_error", 3),
    "unreadable": ("input_parse_error", 3),
    "invalid_schema": ("input_parse_error", 3),
    "unsafe_pointer": ("input_parse_error", 3),
    "artifact_unreadable": ("input_parse_error", 3),
    "artifact_mismatch": ("input_parse_error", 3),
    "generation_changed": ("other_error", 4),
    "workspace_changed": ("other_error", 4),
    "workspace_unverified": ("other_error", 4),
    "workspace_unverifiable": ("other_error", 4),
    "receipt_mismatch": ("other_error", 4),
}


@agent_app.command("handoff")
def handoff(
    source: Path = typer.Option(
        Path("agents-shipgate-reports/verifier.json"),
        "--from",
        help="Path to verifier.json.",
    ),
    report: Path | None = typer.Option(
        None,
        "--report",
        help=("Optional report.json path. Defaults to the sibling report.json when present."),
    ),
    verify_run: Path | None = typer.Option(
        None,
        "--verify-run",
        help=(
            "Optional verify-run.json path. Defaults to the sibling verify-run.json when present."
        ),
    ),
    out: Path | None = typer.Option(
        None,
        "--out",
        help="Optional output path for agent-handoff.json.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Print the handoff JSON to stdout.",
    ),
) -> None:
    """Render the shipgate.agent_handoff/v6 artifact from verifier outputs."""

    try:
        verifier_payload = _load_required_json(source, "verifier.json")
        report_payload = _load_optional_json(
            explicit=report,
            fallback=source.parent / "report.json",
            label="report.json",
        )
        verify_run_payload = _load_optional_json(
            explicit=verify_run,
            fallback=source.parent / "verify-run.json",
            label="verify-run.json",
        )
        payload = build_agent_handoff(
            verifier=verifier_payload,
            report=report_payload,
            verify_run=verify_run_payload,
        )
    except (InputParseError, ValueError) as exc:
        typer.echo(f"Input parsing error: {exc}", err=True)
        raise typer.Exit(3) from exc
    except Exception as exc:  # noqa: BLE001 - CLI boundary.
        typer.echo(f"Agents Shipgate error: {exc}", err=True)
        raise typer.Exit(4) from exc

    rendered = json.dumps(payload.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered, encoding="utf-8")
    if json_output or out is None:
        typer.echo(rendered.rstrip())
    else:
        typer.echo(f"Wrote agent handoff to {out}")


@agent_app.command("control")
def control(
    workspace: Path = typer.Option(
        Path("."),
        "--workspace",
        help=(
            "Repository the pointer must still describe. Drift in HEAD, the "
            "tree, or the worktree overlay refuses the read."
        ),
    ),
    reports_dir: Path = typer.Option(
        Path(DEFAULT_PATHS["reports_dir"]),
        "--reports-dir",
        help="Directory holding current-control.json.",
    ),
    format_: str = typer.Option(
        "control",
        "--format",
        help=(
            "control (default): the compact shipgate.agent_control/v1 envelope "
            "— state, permissions, next action, and hashed artifact paths in "
            "one object. pointer: the raw shipgate.current_control/v1 pointer."
        ),
    ),
) -> None:
    """Read the current control identity using the generation-safe protocol.

    This is the one refresh entry point.  A zero exit means the printed answer
    was validated against every artifact it binds, still describes ``--workspace``
    as it stands right now, and did not move while it was read.  A non-zero exit
    means no control identity is current here: the caller holds no authority and
    must not fall back on a control state it cached earlier in the conversation.

    The default output is the compact envelope rather than the pointer itself.
    The pointer deliberately records no route — reproducing one there would make
    it a second decision — so a caller reading it still had to open the handoff
    to learn what to do next.  The envelope answers both questions in one read,
    by joining the pointer's currency guarantee to the route the bound verifier
    already published.  ``--format pointer`` returns the underlying artifact
    unchanged.
    """

    if format_ not in {"control", "pointer"}:
        guidance = "Re-run with --format control or --format pointer."
        typer.echo(f"Config error: --format must be control or pointer, not {format_!r}", err=True)
        emit_agent_mode_error(
            "config_error",
            message=f"--format must be control or pointer, not {format_!r}",
            exit_code=2,
            next_action=guidance,
            next_actions=[
                NextAction(
                    kind="review", why=guidance, expects="A supported --format value."
                ).model_dump(mode="json")
            ],
        )
        raise typer.Exit(2)

    try:
        result = read_current_control(
            reports_dir, live=_live_workspace(workspace, reports_dir)
        )
    except CurrentControlUnavailable as exc:
        guidance = (
            "Re-run `agents-shipgate verify` and read "
            f"{reports_dir / CURRENT_CONTROL_ARTIFACT_NAME} again. Until it "
            "reads cleanly, treat completion, merge, and any cached must_stop "
            "as unavailable rather than acting on a remembered result."
        )
        # Two different failures, two different exit codes: the artifact set is
        # unreadable or inconsistent (3, the missing/parse family), or it reads
        # fine but no longer describes anything current (4). Both deny
        # authority; the split tells a caller whether to repair a directory or
        # simply re-verify.
        kind, exit_code = _UNAVAILABLE_EXIT.get(exc.reason, ("other_error", 4))
        typer.echo(f"Current control is unavailable ({exc.reason}): {exc}", err=True)
        emit_agent_mode_error(
            kind,
            message=str(exc),
            exit_code=exit_code,
            next_action=guidance,
            next_actions=[
                NextAction(
                    kind="command",
                    command=COMMANDS["verify_pr"],
                    why=guidance,
                    expects=(
                        "current-control.json is present, valid, every artifact "
                        "it binds matches its recorded hash, and it still "
                        "describes this workspace."
                    ),
                ).model_dump(mode="json")
            ],
        )
        raise typer.Exit(exit_code) from exc

    if format_ == "pointer":
        typer.echo(json.dumps(result.pointer.model_dump(mode="json"), indent=2, sort_keys=True))
        return

    bound_verifier = _bound_verifier(reports_dir, result.pointer)
    try:
        envelope = envelope_from_pointer(
            result.pointer,
            verifier=bound_verifier,
            # The exit code the producing run recorded, not this reader's.
            exit_code=None if bound_verifier is None else bound_verifier.head_exit_code,
            artifact_root=reports_dir.as_posix(),
        )
    except AgentControlRouteUnavailable as exc:
        guidance = (
            "Run `agents-shipgate verify` in this workspace. The pointer that "
            "is current was published by a command that reaches no release "
            "decision, so there is no route to return; inventing one would be "
            "a route that does not reproduce this subject."
        )
        typer.echo(f"Current control carries no route: {exc}", err=True)
        emit_agent_mode_error(
            "other_error",
            message=str(exc),
            exit_code=4,
            next_action=guidance,
            next_actions=[
                NextAction(
                    kind="command",
                    command=COMMANDS["verify_pr"],
                    why=guidance,
                    expects=(
                        "A verify run publishes a pointer that binds verifier.json, "
                        "which carries the exact next action."
                    ),
                ).model_dump(mode="json")
            ],
        )
        raise typer.Exit(4) from exc

    typer.echo(render_agent_control_envelope(envelope))


def _bound_verifier(reports_dir: Path, pointer: CurrentControlPointer) -> VerifierArtifact | None:
    """Load the verifier artifact this pointer binds, or ``None``.

    Reading it back is safe without re-hashing: :func:`read_current_control`
    validated every bound artifact against its recorded hash immediately before
    returning, and rejected the read outright if the pointer moved underneath
    it.  A parse failure here is therefore a malformed artifact rather than a
    stale one, and resolves to "no route" rather than a crash.
    """

    ref = pointer.artifacts.get("verifier")
    if ref is None:
        return None
    try:
        return VerifierArtifact.model_validate_json((reports_dir / ref.path).read_bytes())
    except (OSError, ValueError):
        return None


def _live_workspace(workspace: Path, reports_dir: Path) -> LiveWorkspace | None:
    """Resolve the repository as it stands now, or ``None`` outside Git.

    ``None`` is not "no drift" — it means the comparison could not be made, and
    the reader refuses completion authority on that basis rather than assuming
    the pointer still holds.

    The reports directory is excluded from the change set for the same reason
    ``verify`` excludes it when building the plan: the run's own output is not
    part of the change it evaluated, and including it here would make every
    refresh disagree with the decision it is checking.
    """

    try:
        root = ensure_git_workspace(workspace.resolve())
        try:
            changed, _ = working_tree_context(root, exclude=reports_dir)
            changed_paths: tuple[str, ...] | None = tuple(changed)
        except Exception:  # noqa: BLE001 - an unreadable worktree is "unverified".
            changed_paths = None
        return LiveWorkspace(
            root=root,
            repository=repository_identity(root),
            head_commit_sha=commit_sha(root, "HEAD"),
            head_tree_sha=tree_sha(root, "HEAD"),
            changed_paths=changed_paths,
            resolve_commit=lambda ref: _safe_commit_sha(root, ref),
            resolve_merge_base=lambda base, head: _safe_merge_base(root, base, head),
        )
    except Exception:  # noqa: BLE001 - an unresolvable workspace is "unverified".
        return None


def _safe_commit_sha(root: Path, ref: str) -> str | None:
    """Resolve a ref recorded in a pointer; ``None`` when it no longer exists.

    A base ref that has been deleted is drift, not a crash — and it is drift the
    caller must see, so a failure here resolves to ``None`` and compares unequal
    rather than propagating.
    """

    try:
        return commit_sha(root, ref)
    except Exception:  # noqa: BLE001 - an unresolvable ref is drift.
        return None


def _safe_merge_base(root: Path, base: str, head: str) -> str | None:
    try:
        return merge_base_sha(root, base, head)
    except Exception:  # noqa: BLE001 - an unresolvable range is drift.
        return None


def _load_required_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise InputParseError(f"{label} not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise InputParseError(f"{label} is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise InputParseError(f"{label} must contain an object: {path}")
    return payload


def _load_optional_json(
    *,
    explicit: Path | None,
    fallback: Path,
    label: str,
) -> dict[str, Any] | None:
    path = explicit or fallback
    if explicit is None and not path.is_file():
        return None
    return _load_required_json(path, label)


__all__ = ["agent_app", "control", "handoff"]
