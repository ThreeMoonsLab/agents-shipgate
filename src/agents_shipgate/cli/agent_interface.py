from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from agents_shipgate.cli.agent_mode import emit_agent_mode_error
from agents_shipgate.cli.current_workspace import (
    default_reports_dir,
    explicit_output_path,
    is_default_reports_dir,
    is_same_directory,
    live_workspace,
    output_directory_remedy,
)
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
    LiveWorkspaceCause,
    read_current_control,
)
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.invocation import join_argv, render_command, split_invocation
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
        cause = exc.cause
        if exc.reports_dir_refusal is not None:
            recovery = _recovery_away_from(
                exc, reports_dir=reports_dir, workspace=workspace
            )
            guidance = recovery.why
        elif cause is not None and cause.kind == "repository_configuration":
            recovery = _recovery_under_repository_configuration(cause, workspace=workspace)
            guidance = recovery.why
        elif cause is not None and cause.kind == "resource_limit":
            recovery = _recovery_within_read_bounds(
                cause, command=recovery.command, reports_dir=reports_dir
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
    relative against the current directory, the rule an explicit ``verify
    --out`` follows too (:func:`explicit_output_path`, #818) — and is never
    retargeted. When it holds no pointer the read refuses; nothing else is
    searched.

    The directory returned first is absolute, so each refusal and recovery
    command names one that opens from wherever it is read: a relative spelling
    in a recovery command resolves against wherever that command is typed.

    The spelling is unchanged wherever it already worked: the caller's own for
    an explicit path, and for the default, relative to the current directory
    when the directory lies beneath it — so ``--workspace .`` prints exactly
    what it always did — and absolute otherwise.
    """

    if requested is not None:
        return explicit_output_path(requested), requested.as_posix()
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

    The one token it may not keep as recorded is where the command writes: a
    refresh of *this* directory must publish into it, from whatever directory
    the command is typed in (:func:`_publishing_into`, #818).
    """

    produced = _producing_verification_command(exc)
    if produced is None:
        return _recovery_verify_command(workspace, reports_dir)
    return _publishing_into(produced, reports_dir=reports_dir)


def _recovery_away_from(
    exc: CurrentControlUnavailable, *, reports_dir: Path, workspace: Path
) -> NextAction:
    """The recovery for a reports directory no pointer can be current in (#804).

    Every other currency refusal is cleared by re-running into the same
    directory, and the producing run's exact command is the best route. Here
    that command, as recorded, is the one route that cannot work: ``verify``
    refuses the directory before writing, so following it would end at a
    config error, not at a current pointer. So the route is that same command
    with only its ``--out`` removed, publishing into its workspace's default
    reports directory. Everything else it carries stays: rebuilding a bare
    ``verify`` dropped ``--config`` and ``--base``, and a run that then skipped
    the local base read a ``human_review_required`` widening as ``complete``
    (#804 review) — the defect #429 already recorded for ``--no-base``, policy
    and baseline options.

    When the refused directory *is* the default one, no command can be named
    without inventing a destination, so the route is a ``review`` step whose
    advice never includes omitting ``--out``. The default is recognized by
    physical identity, so a case-variant or symlinked spelling of it is still
    the default.
    """

    refusal = exc.reports_dir_refusal
    unavailable = (
        "Until a pointer reads cleanly, treat completion, merge, and any cached "
        "must_stop as unavailable rather than acting on a remembered result."
    )
    if is_default_reports_dir(workspace, reports_dir):
        return NextAction(
            kind="review",
            why=(
                f"No pointer in {reports_dir} can be current, because it {refusal}. "
                f"To re-run `{render_command(['verify'])}`: "
                f"{output_directory_remedy(default=True)} Read a directory named "
                f"with --out back with `agent control --reports-dir`. {unavailable}"
            ),
            expects=(
                "A verification published into a directory that is gitignored, "
                "outside the repository, or holds nothing but Shipgate artifacts "
                "outside any trust root, read from that directory."
            ),
        )
    default = default_reports_dir(workspace)
    produced = _producing_verification_command(exc)
    command = (
        _without_output_directory(produced) if produced is not None else None
    ) or verify_command_for(workspace, None)
    return NextAction(
        kind="command",
        command=command,
        why=(
            f"No pointer in {reports_dir} can be current, because it {refusal}. "
            "Re-run the verification without --out, which publishes into "
            f"{default}, and read {default / CURRENT_CONTROL_ARTIFACT_NAME} "
            f"(`agent control` without --reports-dir). {unavailable}"
        ),
        expects=(
            "current-control.json is published into the workspace's default "
            "reports directory, and it reads cleanly from there."
        ),
    )


def _recovery_under_repository_configuration(
    cause: LiveWorkspaceCause, *, workspace: Path
) -> NextAction:
    """The recovery when the repository's own Git configuration is the cause (#813).

    Every other currency recovery names a run that can change the answer. No
    run changes this one. The worktree readers refuse executable ``filter.*``
    drivers, ``filter=`` attributes, repository-local ``diff.*`` configuration
    and a non-empty ``.git/info/attributes``, and that is how the repository is
    configured: ``verify`` reads the working tree under it with or without
    ``--head``, and so does this refresh, for a committed-tree pointer too.
    Naming the producing ``verify`` again — the route this used to get —
    published a pointer the next refresh refused the same way, forever.

    So it is a ``review`` step for a human, naming what was refused. It never
    advises removing the configuration: Git LFS and git-crypt put it there on
    purpose, removing it breaks those checkouts, and only someone who knows why
    it is there can decide. ``diff`` is named because it still answers in such
    a repository — it reads the host files without Git's worktree filters — and
    grants no authority.
    """

    diff = render_command(["diff", "--workspace", _cwd_anchored(workspace)])
    return NextAction(
        kind="review",
        why=(
            f"{cause.text} That is how this repository is configured, not "
            "something a run changes: re-running verify, with or without "
            "--head, reads the working tree under the same configuration, so no "
            "pointer in this repository can be shown current while it is in "
            f"place. A human decides how this change is reviewed; `{diff}` still "
            "shows its host-grant changes, read-only and without authority. "
            "Until then, treat completion, merge, and any cached must_stop as "
            "unavailable rather than acting on a remembered result."
        ),
        expects=(
            "A human decision on how to review this change without a current "
            "Shipgate pointer for this repository."
        ),
    )


def _recovery_within_read_bounds(
    cause: LiveWorkspaceCause, *, command: str | None, reports_dir: Path
) -> NextAction:
    """The recovery when the change set outgrew a read bound, or Git timed out (#813).

    The producing run's command is still the way back, but not on its own: an
    uncommitted change larger than the bound is read the same way by that run
    and by the refresh after it. Committing it, or keeping generated output out
    of the working tree, is what clears a bound; a timeout may clear on a
    re-run alone.
    """

    return NextAction(
        kind="command",
        command=command,
        why=(
            f"{cause.text} Commit or shrink the uncommitted change (for example, "
            "keep generated or vendored output out of the working tree), then "
            "run this verification again and read "
            f"{reports_dir / CURRENT_CONTROL_ARTIFACT_NAME}; after a Git "
            "timeout, running it again may be enough. Until it reads cleanly, "
            "treat completion, merge, and any cached must_stop as unavailable "
            "rather than acting on a remembered result."
        ),
        expects=(
            "current-control.json is present, valid, every artifact it binds "
            "matches its recorded hash, and it still describes this workspace."
        ),
    )


def _producing_verification_command(exc: CurrentControlUnavailable) -> str | None:
    """The bound verifier's own ``fix_task.verification_command``, when validated."""

    data = exc.artifacts.get(VERIFIER_ARTIFACT_KEY)
    if data is None:
        return None
    try:
        verifier = VerifierArtifact.model_validate_json(data)
    except ValueError:
        return None
    if verifier.fix_task is None:
        return None
    return verifier.fix_task.verification_command or None


def _without_output_directory(command: str) -> str | None:
    """``command`` with its ``--out`` removed and every other token kept.

    ``None`` when the command has no faithful argv form, or is not a
    ``verify`` invocation: the caller then names a command of its own rather
    than guessing at a string it cannot parse.
    """

    split = _verify_argv(command)
    if split is None:
        return None
    executable, args = split
    return join_argv([*executable, *_with_output_directory(args, ())])


def _publishing_into(command: str, *, reports_dir: Path) -> str:
    """``command`` with its ``--out`` naming ``reports_dir``, absolutely.

    Returned unchanged when it already publishes there from any directory: an
    absolute ``--out`` that physically is ``reports_dir``, or no ``--out`` and
    an absolute ``--workspace`` whose default reports directory it is. Anything
    else is respelled, with every other token kept in place.

    ``1.0.0`` recorded a non-default ``--out`` relative to the Git root. Typed
    anywhere else that spelling resolves against the current directory (#818),
    so replaying it verbatim wrote a directory beside the caller, left the
    pointer being refreshed superseded, and the next refresh named the same
    command again. A command with no faithful argv form, or that is not a
    ``verify`` invocation, is left as recorded: nothing here can rewrite a
    string it cannot parse without guessing at it.
    """

    split = _verify_argv(command)
    if split is None:
        return command
    executable, args = split
    written = _written_directory(args)
    if written is not None and is_same_directory(written, reports_dir):
        return command
    return join_argv(
        [*executable, *_with_output_directory(args, ("--out", str(reports_dir)))]
    )


def _verify_argv(command: str) -> tuple[list[str], list[str]] | None:
    """``(executable, args)`` for a rendered ``verify`` command, else ``None``."""

    split = split_invocation(command)
    if split is None or "verify" not in split[1]:
        return None
    return split


def _with_output_directory(args: list[str], replacement: tuple[str, ...]) -> list[str]:
    """``args`` with every ``--out`` removed and ``replacement`` in the first one's place.

    With no ``--out`` to replace, ``replacement`` goes before a trailing
    ``--json``, where :func:`verify_command_for` places its options.
    """

    kept: list[str] = []
    pending = list(replacement)
    skip = False
    for token in args:
        if skip:
            skip = False
            continue
        if token == "--out" or token.startswith("--out="):
            skip = token == "--out"
            kept.extend(pending)
            pending = []
            continue
        kept.append(token)
    if pending:
        at = len(kept) - 1 if kept and kept[-1] == "--json" else len(kept)
        kept[at:at] = pending
    return kept


def _written_directory(args: list[str]) -> Path | None:
    """The directory a ``verify`` argv publishes into, wherever it is typed.

    ``None`` when that depends on the directory it is typed in: a relative
    ``--out``, or no ``--out`` and a relative or omitted ``--workspace``. The
    last spelling of an option is the one ``verify`` uses.
    """

    values: dict[str, str] = {}
    for index, token in enumerate(args):
        for option in ("--out", "--workspace"):
            if token == option and index + 1 < len(args):
                values[option] = args[index + 1]
            elif token.startswith(f"{option}="):
                values[option] = token.split("=", 1)[1]
    out = values.get("--out")
    if out is not None:
        return Path(out) if Path(out).is_absolute() else None
    workspace = values.get("--workspace")
    if workspace is None or not Path(workspace).is_absolute():
        return None
    return default_reports_dir(Path(workspace))


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
