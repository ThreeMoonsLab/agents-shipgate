from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import typer

from agents_shipgate.cli.agent_mode import emit_agent_mode_error
from agents_shipgate.cli.current_workspace import default_reports_dir, live_workspace
from agents_shipgate.cli.workspace_guard import require_workspace
from agents_shipgate.core.agent_control_envelope import (
    AgentControlRouteUnavailable,
    envelope_from_pointer,
    envelope_from_routeless_pointer,
    render_agent_control_envelope,
)
from agents_shipgate.core.agent_controls import _cwd_anchored, verify_command_for
from agents_shipgate.core.agent_handoff import build_agent_handoff
from agents_shipgate.core.current_control import (
    CurrentControlRead,
    CurrentControlUnavailable,
    read_current_control,
)
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.invocation import render_command
from agents_shipgate.schemas.contract import DEFAULT_PATHS
from agents_shipgate.schemas.current_control import (
    CURRENT_CONTROL_ARTIFACT_NAME,
    VERIFIER_ARTIFACT_KEY,
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
    reports_dir: Path | None = typer.Option(
        None,
        "--reports-dir",
        help=(
            "Directory holding current-control.json. Defaults to "
            f"{DEFAULT_PATHS['reports_dir']} under --workspace, where `verify "
            "--workspace` writes it by default. An explicit relative path "
            "resolves against the current directory."
        ),
        show_default=False,
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

    Without ``--reports-dir`` the pointer is read from where a default ``verify
    --workspace`` published it — under the workspace, from any current
    directory (#575). An explicit ``--reports-dir`` is read exactly as given,
    and no other directory is ever searched in its place.
    """
    require_workspace(workspace)

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

    # From here on the directory is absolute, so every refusal and recovery
    # names one that opens from wherever it is read; `artifact_root` is how the
    # envelope spells artifact paths for this caller.
    reports_dir, artifact_root = _reports_location(workspace, reports_dir)

    try:
        result = read_current_control(
            reports_dir,
            # A callable, so the protocol can re-observe it: a snapshot taken
            # here leaves a window in which HEAD advances before the return.
            live=lambda: live_workspace(workspace, reports_dir),
            # Captured inside the protocol, not reopened after it: the route
            # must come from the same generation whose identity was
            # confirmed. Keys the pointer does not bind are simply absent — a
            # `scan` binds no verifier.
            capture=(VERIFIER_ARTIFACT_KEY,),
        )
    except CurrentControlUnavailable as exc:
        guidance = (
            f"Re-run `{render_command(['verify'])}` and read "
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
        recovery = NextAction(
            kind="command",
            command=_superseded_recovery_command(
                exc, workspace=workspace, reports_dir=reports_dir
            ),
            why=guidance,
            expects=(
                "current-control.json is present, valid, every artifact "
                "it binds matches its recorded hash, and it still "
                "describes this workspace."
            ),
        )
        if exc.reports_dir_refusal is not None:
            recovery = _recovery_away_from(
                reports_dir, workspace=workspace, refusal=exc.reports_dir_refusal
            )
            guidance = recovery.why
        emit_agent_mode_error(
            kind,
            message=str(exc),
            exit_code=exit_code,
            next_action=guidance,
            next_actions=[recovery.model_dump(mode="json")],
        )
        raise typer.Exit(exit_code) from exc

    if format_ == "pointer":
        typer.echo(json.dumps(result.pointer.model_dump(mode="json"), indent=2, sort_keys=True))
        return

    try:
        bound_verifier = _bound_verifier(result)
    except AgentControlRouteUnavailable as exc:
        # The pointer and the artifact it binds disagree about which request
        # they close. That is an inconsistent set, not a current generation.
        _refuse_route(reports_dir, workspace, detail=str(exc))
        raise typer.Exit(4) from exc

    if bound_verifier is not None:
        envelope = envelope_from_pointer(
            result.pointer,
            verifier=bound_verifier,
            # The exit code the producing run recorded, not this reader's.
            exit_code=bound_verifier.head_exit_code,
            artifact_root=artifact_root,
        )
    elif result.pointer.lifecycle_state == "terminal" and result.pointer.operation == "scan":
        # Current, but published by a command that reaches no release decision.
        # Refusing conflated "nothing is current" with "what is current cannot
        # authorize a merge"; only the first justifies a non-zero exit. Scoped
        # to `scan`: a verify or preview pointer that binds no verifier lost an
        # artifact it must have written, which is an inconsistent generation.
        envelope = envelope_from_routeless_pointer(
            result.pointer,
            verify_command=_recovery_verify_command(workspace, reports_dir),
            decision_withheld=_scan_verdict_unavailable(),
            artifact_root=artifact_root,
        )
    elif result.pointer.lifecycle_state == "terminal":
        _refuse_route(
            reports_dir,
            workspace,
            detail=(
                f"The current control pointer was published by {result.pointer.operation!r} "
                "but binds no verifier artifact, so the generation is incomplete."
            ),
        )
        raise typer.Exit(4)
    else:
        # An in-progress marker really is "no decision is current here".
        _refuse_route(
            reports_dir,
            workspace,
            detail=(
                "A run is in progress in this directory, so no decision is "
                "current and no route can be returned."
            ),
        )
        raise typer.Exit(4)

    typer.echo(render_agent_control_envelope(envelope))


def _reports_location(workspace: Path, requested: Path | None) -> tuple[Path, str]:
    """The directory this refresh reads, and the spelling its artifacts print under.

    Omitted, it is where a default ``verify --workspace`` published: the rule is
    :func:`default_reports_dir`, the same one ``verify`` writes by, so the two
    cannot drift. Resolving the bare default against the caller's directory made
    a valid run under ``<repo>/agents-shipgate-reports`` read as ``missing``
    from anywhere else, and a caller standing in another verified repository
    had that repository's pointer read and checked against this one (#575).

    An explicit ``--reports-dir`` keeps its meaning — absolute as given,
    relative against the current directory — and is never retargeted. When it
    holds no pointer the read refuses; nothing else is searched.

    The directory returned first is absolute, so each refusal and recovery
    command names one that opens from wherever it is read. ``verify`` resolves a
    relative ``--out`` against the Git root rather than the caller, so echoing a
    relative spelling there would name a different directory.

    The spelling is unchanged wherever it already worked: the caller's own for
    an explicit path, and for the default, relative to the current directory
    when the directory lies beneath it — so ``--workspace .`` prints exactly
    what it always did — and absolute otherwise.
    """

    if requested is not None:
        return Path(_cwd_anchored(requested)), requested.as_posix()
    location = default_reports_dir(workspace)
    try:
        spelling = location.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        spelling = location.as_posix()
    return location, spelling


def _superseded_recovery_command(
    exc: CurrentControlUnavailable,
    *,
    workspace: Path,
    reports_dir: Path,
) -> str:
    """The rerun that refreshes *this* pointer, preferring the one that made it.

    A fixed ``verify --base origin/main --head HEAD`` was wrong for the route
    that most often lands here. The §D declaration route runs on the *working
    tree*, and its own command edits ``shipgate.yaml`` — so the mandatory
    refresh that follows it always refuses, and the recovery it advertised
    needed a remote-tracking ref that may not exist, otherwise scanned
    committed ``HEAD`` and so missed the very edit that superseded the pointer,
    dropped ``--no-base`` and any policy or baseline option, and wrote to the
    default reports directory rather than the one being refreshed (#429
    review).

    A currency refusal is not an integrity one: the pointer and every artifact
    it binds were hash-validated in the same pass, and only the workspace had
    moved. So the producing run's own ``fix_task.verification_command`` is
    available and exact, and it is what a continuation needs. It is used only
    to *name* a step — nothing here authorizes anything — and any refusal that
    could not validate the set falls back to a command rebuilt from the request
    the caller just made.
    """

    data = exc.artifacts.get(VERIFIER_ARTIFACT_KEY)
    if data is not None:
        try:
            verifier = VerifierArtifact.model_validate_json(data)
        except ValueError:
            verifier = None
        if verifier is not None and verifier.fix_task is not None:
            command = verifier.fix_task.verification_command
            if command:
                return command
    return _recovery_verify_command(workspace, reports_dir)


def _recovery_away_from(reports_dir: Path, *, workspace: Path, refusal: str) -> NextAction:
    """The recovery for a reports directory no pointer can be current in (#804).

    Every other currency refusal is cleared by re-running into the same
    directory, and the producing run's exact command is the best route. Here
    that command is the one route that cannot work: ``verify`` refuses the
    directory before writing, so following it would end at a config error, not
    at a current pointer. The route goes to the workspace's default reports
    directory instead; when that *is* the refused directory, no command can be
    named without inventing one, and a person or agent must choose where the
    reports go.
    """

    default = default_reports_dir(workspace)
    unavailable = (
        "Until a pointer reads cleanly, treat completion, merge, and any cached "
        "must_stop as unavailable rather than acting on a remembered result."
    )
    if Path(os.path.realpath(default)) == Path(os.path.realpath(reports_dir)):
        return NextAction(
            kind="review",
            why=(
                f"No pointer in {reports_dir} can be current, because it {refusal}. "
                f"Re-run `{render_command(['verify'])}` with --out naming a "
                "directory that is gitignored or outside the repository, and "
                f"read that directory with --reports-dir. {unavailable}"
            ),
            expects=(
                "A verification published into a directory that holds only "
                "Shipgate artifacts, read from that directory."
            ),
        )
    return NextAction(
        kind="command",
        command=verify_command_for(workspace, None),
        why=(
            f"No pointer in {reports_dir} can be current, because it {refusal}. "
            f"Re-run `{render_command(['verify'])}` without --out, which "
            f"publishes into {default}, and read "
            f"{default / CURRENT_CONTROL_ARTIFACT_NAME} (`agent control` without "
            f"--reports-dir). {unavailable}"
        ),
        expects=(
            "current-control.json is published into the workspace's default "
            "reports directory, and it reads cleanly from there."
        ),
    )


def _recovery_verify_command(workspace: Path, reports_dir: Path) -> str:
    """The verify invocation that refreshes *this* pointer.

    Both the workspace and the reports directory come from the request that was
    just validated. Emitting a bare `verify --workspace .` discarded the
    subject: following it checked a different manifest, wrote a second reports
    directory, and left the pointer being refreshed exactly as it was.

    The manifest is deliberately not guessed. `verify` resolves a relative
    `--config` against the Git root, and the pointer does not record which
    manifest the producing run used, so naming one would be inventing part of
    the subject rather than recovering it — and naming the wrong one silently
    verifies a different gate. The default resolution is the honest answer, and
    `--out` keeps the refresh pointed at the directory the caller asked about.
    """

    return verify_command_for(
        workspace,
        None,
        extra=("--out", str(reports_dir)),
    )


def _refuse_route(reports_dir: Path, workspace: Path, *, detail: str) -> None:
    """Report that no route is available, in the caller's own terms.

    The recovery command is generated from the requested workspace rather than
    a fixed `--workspace . --config shipgate.yaml --base origin/main` string:
    echoing a default discards the subject that was just validated and points
    the caller at a different repository than the one they asked about.
    """

    guidance = (
        "Run verify in this workspace to obtain a decision. Until one exists, "
        "treat completion, merge, and any cached must_stop as unavailable."
    )
    command = _recovery_verify_command(workspace, reports_dir)
    typer.echo(f"Current control carries no route: {detail}", err=True)
    emit_agent_mode_error(
        "other_error",
        message=detail,
        exit_code=4,
        next_action=guidance,
        next_actions=[
            NextAction(
                kind="command",
                command=command,
                why=guidance,
                expects=(
                    f"{reports_dir / CURRENT_CONTROL_ARTIFACT_NAME} binds a verifier "
                    "artifact carrying the exact next action."
                ),
            ).model_dump(mode="json")
        ],
    )


def _bound_verifier(result: CurrentControlRead) -> VerifierArtifact | None:
    """Parse the verifier bytes the generation-safe read already validated.

    The bytes come from :class:`CurrentControlRead`, hashed against the pointer
    inside the same pass that confirmed the pointer had not moved. Reopening the
    file here instead would be a second, unsynchronized read: a run republishing
    between the two would let this pointer's identity be reported beside a
    different generation's decision and permissions.

    A parse failure is therefore a malformed artifact rather than a stale one,
    and resolves to "no route" rather than a crash.
    """

    data = result.artifacts.get(VERIFIER_ARTIFACT_KEY)
    if data is None:
        return None
    try:
        verifier = VerifierArtifact.model_validate_json(data)
    except ValueError as exc:
        # Not the same thing as "no verifier was bound". A pointer that binds a
        # verifier it cannot parse is an inconsistent generation, and returning
        # `None` sent it down the routeless-scan path, where a hash-bound `{}`
        # under a terminal *verify* pointer exited 0 and kept update-PR
        # authority.
        raise AgentControlRouteUnavailable(
            f"The verifier artifact bound by the current control pointer is "
            f"malformed, so this generation is inconsistent: {exc}"
        ) from exc
    # The state tag alone is not identity. A verifier that closes a different
    # request than the pointer decided cannot supply this pointer's route, and
    # the difference is invisible in `control.state`.
    if (verifier.request_id, verifier.decision_id) != (result.pointer.request_id, result.pointer.decision_id):
        raise AgentControlRouteUnavailable(
            "The bound verifier reports a different request than the current "
            "control pointer decided, so no route for this generation could be "
            "recovered."
        )
    return verifier


def _scan_verdict_unavailable() -> str:
    """Why a `scan` generation publishes no release decision here.

    `scan` runs the release engine and binds its `report.json`, so the verdict
    exists and is byte-intact. It is deliberately **not** lifted into the
    envelope, and the reason is currency rather than integrity.

    A `scan` pointer records no HEAD, no worktree overlay, and no input set, so
    the generic comparison in `read_current_control` has nothing to compare and
    passes vacuously. Editing `shipgate.yaml`, or a `tools.json` the manifest
    references, or a policy pack, or a baseline, leaves the pointer reading
    cleanly with the old verdict — and an earlier revision of this branch
    published exactly that as an affirmative `passed`. A verdict a reader cannot
    check is worse than no verdict, so it stays withheld until a scan binds a
    reconfirmable snapshot of everything it read (tracked separately).

    What *is* published is the reason, so this stays distinguishable from output
    produced before any engine ran — the ambiguity #323 set out to remove. The
    reason says the verdict was *withheld*, never that none exists: a
    format-limited scan still reached one, and `report.sarif` even carries it
    under `runs[0].properties.release_decision`. Which artifact holds it is not
    the point; none of them can show it is still current.

    Authority is untouched either way: the state and `permissions` come from the
    pointer, and a scan authorizes no merge.
    """

    return (
        "This scan reached a release decision and it is withheld here: a scan "
        "binds no reconfirmable snapshot of the inputs it read — the manifest, "
        "its tool sources, policy packs, and baselines can all change without "
        "moving this pointer — so nothing in this directory can show the verdict "
        "still describes the workspace. Run verify to obtain one that can."
    )


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
