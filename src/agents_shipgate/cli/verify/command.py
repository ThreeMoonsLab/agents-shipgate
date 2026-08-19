from __future__ import annotations

import json
import logging
import os
from pathlib import Path, PurePosixPath

import typer

from agents_shipgate.cli._artifact_lifecycle import ArtifactLifecycleError
from agents_shipgate.cli._helpers import (
    _diagnose_config_error,
    _echo_next_action_hint,
    _parse_fail_on,
)
from agents_shipgate.cli.agent_mode import emit_agent_mode_error, is_agent_mode
from agents_shipgate.cli.current_workspace import live_workspace
from agents_shipgate.cli.diagnostics import top_next_actions
from agents_shipgate.cli.discovery.gitignore_block import REPORTS_DIR_NAME
from agents_shipgate.cli.workspace_guard import require_workspace
from agents_shipgate.core.agent_control_envelope import (
    control_headline_lines,
    denied_control_envelope,
    envelope_from_verifier,
    render_agent_control_envelope,
    single_line_text,
)
from agents_shipgate.core.current_control import (
    CurrentControlPublishError,
    CurrentControlUnavailable,
    read_current_control,
)
from agents_shipgate.core.disclaimers import STATIC_VERDICT_DISCLAIMER
from agents_shipgate.core.errors import AgentsShipgateError, ConfigError, InputParseError
from agents_shipgate.core.logging import configure_logging
from agents_shipgate.report.summary_text import primary_evidence_remediation_text
from agents_shipgate.schemas.agent_control_envelope import (
    AgentControlEnvelope,
    AgentControlOperation,
)
from agents_shipgate.schemas.current_control import VERIFIER_ARTIFACT_KEY
from agents_shipgate.schemas.diagnostics import NextAction
from agents_shipgate.schemas.verifier import VerifierArtifact

from .git import ensure_git_workspace, staged_paths_under
from .orchestrator import run_preview, run_verify

logger = logging.getLogger(__name__)


def _unsafe_config_identity_error(exc: ConfigError) -> bool:
    """Whether recovery must not offer edits or a replay command."""

    message = str(exc)
    identity_failure = any(
        marker in message
        for marker in (
            "must be inside --workspace",
            "must not contain symlink components",
            "must use the exact filesystem spelling",
            "could not be inspected safely",
        )
    )
    return identity_failure and (
        message.startswith("--config")
        or message.startswith("Head manifest")
        or message.startswith("Base manifest")
    )


def verify(
    workspace: Path = typer.Option(
        Path("."),
        "--workspace",
        help="Workspace/git checkout containing the head tree.",
    ),
    config: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        help=(
            "Path to shipgate.yaml. An explicit relative path is relative to "
            "--workspace; when omitted, the default is shipgate.yaml at the "
            "Git root."
        ),
    ),
    base: str | None = typer.Option(
        None,
        "--base",
        help=(
            "Local base ref/SHA for PR diff. Verify never fetches it. When "
            "omitted, verify auto-detects the default branch (origin/HEAD, "
            "origin/main, origin/master) if it points at a different commit "
            "than the head. Local main/master are used only when passed "
            "explicitly; --no-base disables auto-detection."
        ),
    ),
    no_base: bool = typer.Option(
        False,
        "--no-base",
        help=(
            "Disable base auto-detection when --base is omitted; scan only "
            "the working tree or explicit head."
        ),
    ),
    head: str | None = typer.Option(
        None,
        "--head",
        help=(
            "Local head ref/SHA to diff and scan from an isolated archive. "
            "Omit to scan the checked-out workspace."
        ),
    ),
    preview: bool = typer.Option(
        False,
        "--preview",
        help=(
            "Lightweight relevance check: evaluate triggers and report "
            "whether Shipgate is relevant + what to run next, WITHOUT "
            "running a scan, requiring a manifest, or writing any files "
            "beyond the verifier artifacts. Exits 0 for every workspace it "
            "evaluates; a --workspace that does not exist is refused as an "
            "invocation error (exit 2) before anything is created."
        ),
    ),
    out: Path | None = typer.Option(
        None,
        "--out",
        help="Output directory for verifier and scan artifacts.",
    ),
    format_: str | None = typer.Option(
        None,
        "--format",
        help=(
            "Verifier stdout format: text, json (full verifier artifact), or "
            "control (the compact shipgate.agent_control/v1 envelope — the "
            "promoted shape for a coding-agent control loop). Defaults to "
            "text, or json when a coding-agent environment is detected. Scan "
            "artifacts are fixed."
        ),
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help=(
            "Shortcut for the coding-agent surface: --format json. Emits the "
            "full verifier controller artifact."
        ),
    ),
    ci_mode: str | None = typer.Option(
        None,
        "--ci-mode",
        help="advisory or strict. Overrides manifest ci.mode for the head scan.",
    ),
    fail_on: str | None = typer.Option(
        None,
        "--fail-on",
        help="Comma-separated severities that fail CI.",
    ),
    baseline: Path | None = typer.Option(
        None,
        "--baseline",
        help="Path to a local baseline JSON for the head scan.",
    ),
    baseline_mode: str = typer.Option(
        "new-findings",
        "--baseline-mode",
        help="Baseline comparison mode. Supported value: new-findings.",
    ),
    diff_from: Path | None = typer.Option(
        None,
        "--diff-from",
        help="Explicit prior report.json or baseline JSON for head diff.",
    ),
    authorization: Path | None = typer.Option(
        None,
        "--authorization",
        help=(
            "Signed shipgate.human_authorization/v1 grant emitted by a trusted "
            "coding host. The grant must live outside the evaluated workspace."
        ),
    ),
    policy_packs: list[Path] | None = typer.Option(
        None,
        "--policy-pack",
        help="Additional declarative YAML policy pack path. May be repeated.",
    ),
    no_plugins: bool = typer.Option(
        False,
        "--no-plugins",
        help="Do not load third-party check plugins or adapters.",
    ),
    strict_plugins: bool = typer.Option(
        False,
        "--strict-plugins",
        help="Exit 4 if any loaded plugin or third-party adapter failed validation.",
    ),
    suggest_patches: bool = typer.Option(
        False,
        "--suggest-patches",
        help="Attach suggested patches to head scan findings.",
    ),
    no_heuristics: bool = typer.Option(
        False,
        "--no-heuristics",
        help="Filter heuristic findings before the head release decision.",
    ),
    pr_comment_style: str = typer.Option(
        "capability-review",
        "--pr-comment-style",
        help=(
            "PR comment renderer: capability-review (default) or findings "
            "(legacy v1 style, available for one minor release cycle)."
        ),
    ),
    verbose: bool = typer.Option(False, "--verbose", help="Show debug details."),
) -> None:
    """Run the canonical ongoing-PR verifier around the existing scan engine."""

    # Ahead of every other check, including flag parsing: a workspace that is
    # not there has no output directory to resolve, and preview used to create
    # the whole mistyped path before saying so (#389).
    require_workspace(workspace)

    # Flag parsing gets its own try block, mirroring scan: a ConfigError
    # raised here is about flag values, not the manifest — emitting a
    # manifest diagnostic ("run init") would route the caller to the
    # wrong fix.
    try:
        configure_logging(verbose=verbose)
        stdout_format = _resolve_verify_format(format_, json_output=json_output, preview=preview)
        if ci_mode and ci_mode not in {"advisory", "strict"}:
            raise ConfigError("--ci-mode must be advisory or strict")
        for label, value in (("--base", base), ("--head", head)):
            if value is not None and (
                not value or value.startswith("-") or any(char in value for char in "\0\r\n")
            ):
                raise ConfigError(
                    f"{label} must be non-empty, must not begin with '-', "
                    "and must not contain control delimiters"
                )
        parsed_fail_on = _parse_fail_on(fail_on)
        parsed_pr_comment_style = _parse_pr_comment_style(pr_comment_style)
        if preview and authorization is not None:
            raise ConfigError("--authorization cannot be combined with --preview")
    except ConfigError as exc:
        typer.echo(f"Config error: {exc}", err=True)
        guidance = "Fix the invalid CLI flag value referenced in the error and re-run verify."
        emit_agent_mode_error(
            "config_error",
            message=str(exc),
            exit_code=2,
            next_action=guidance,
            next_actions=[
                NextAction(
                    kind="review",
                    why=guidance,
                    expects=("Re-run with a flag value the option parser accepts."),
                ).model_dump(mode="json")
            ],
        )
        raise typer.Exit(2) from exc

    try:
        effective_config = config
        if effective_config is None:
            if preview:
                try:
                    config_root = ensure_git_workspace(workspace.resolve())
                except ConfigError:
                    config_root = workspace.resolve()
            else:
                config_root = ensure_git_workspace(workspace.resolve())
            effective_config = config_root / "shipgate.yaml"
        if preview:
            verifier, _report, exit_code = run_preview(
                workspace=workspace,
                config=effective_config,
                base=base,
                head=head,
                out=out,
                pr_comment_style=parsed_pr_comment_style,
                auto_base=base is None and not no_base,
            )
        else:
            head_ref = head or "HEAD"
            verifier, _report, exit_code = run_verify(
                workspace=workspace,
                config=effective_config,
                base=base,
                head=head_ref,
                archive_head=head is not None,
                out=out,
                ci_mode=ci_mode,
                fail_on=parsed_fail_on,
                baseline=baseline,
                baseline_mode=baseline_mode,
                diff_from=diff_from,
                authorization=authorization,
                policy_packs=policy_packs,
                plugins_enabled=False if no_plugins else None,
                strict_plugins=strict_plugins,
                suggest_patches=suggest_patches,
                no_heuristics=no_heuristics,
                pr_comment_style=parsed_pr_comment_style,
                verbose=verbose,
                auto_base=base is None and not no_base,
            )
    except ConfigError as exc:
        typer.echo(f"Config error: {exc}", err=True)
        if _unsafe_config_identity_error(exc):
            guidance = (
                "Review the configured manifest identity and rerun verify only "
                "after selecting an exact in-workspace, non-symlink path."
            )
            flattened = [
                NextAction(
                    kind="review",
                    why=guidance,
                    expects=(
                        "The configured manifest uses its exact stored identity "
                        "inside the evaluated workspace."
                    ),
                )
            ]
        else:
            diagnostics = _diagnose_config_error(
                config=str(config or Path("shipgate.yaml")),
                workspace=workspace,
                exc=exc,
                plugins_enabled=False if no_plugins else None,
            )
            flattened = top_next_actions(diagnostics)
        _echo_next_action_hint(flattened)
        emit_agent_mode_error(
            "config_error",
            message=str(exc),
            exit_code=2,
            next_action=flattened[0].to_legacy_string(),
            next_actions=[a.model_dump(mode="json") for a in flattened],
        )
        raise typer.Exit(2) from exc
    except InputParseError as exc:
        typer.echo(f"Input parsing error: {exc}", err=True)
        guidance = (
            "Inspect the file referenced in the error; ensure it exists, "
            "is valid, and resolves under the manifest directory."
        )
        emit_agent_mode_error(
            "input_parse_error",
            message=str(exc),
            exit_code=3,
            next_action=guidance,
            next_actions=[
                NextAction(
                    kind="review",
                    why=guidance,
                    expects=(
                        "Referenced file is present, parseable, and inside the manifest directory."
                    ),
                ).model_dump(mode="json")
            ],
        )
        raise typer.Exit(3) from exc
    except ArtifactLifecycleError as exc:
        typer.echo(f"Agents Shipgate error: {exc}", err=True)
        guidance = (
            f"Remove {exc.path} and re-run verify; Agents Shipgate will not "
            "write a replacement verifier route while a stale verifier "
            "artifact survives."
        )
        emit_agent_mode_error(
            "other_error",
            message=str(exc),
            exit_code=4,
            next_action=guidance,
            next_actions=[
                NextAction(
                    kind="edit",
                    path=str(exc.path),
                    why=guidance,
                    expects=(
                        "The stale verifier artifact is absent, then verify "
                        "writes one content-addressed artifact set."
                    ),
                ).model_dump(mode="json")
            ],
        )
        raise typer.Exit(4) from exc
    except CurrentControlPublishError as exc:
        # The pointer stays non-terminal, so nothing in the directory is
        # current. Say so plainly rather than letting the caller assume the
        # artifacts that were written are usable.
        typer.echo(f"Agents Shipgate error: {exc}", err=True)
        guidance = (
            f"Make {exc.path} writable, then re-run verify. Until the control "
            "pointer publishes, no decision in this directory is current and "
            "no cached control state may be acted on."
        )
        emit_agent_mode_error(
            "other_error",
            message=str(exc),
            exit_code=4,
            next_action=guidance,
            next_actions=[
                NextAction(
                    kind="edit",
                    path=str(exc.path),
                    why=guidance,
                    expects=(
                        "current-control.json publishes, naming the control "
                        "identity that is current."
                    ),
                ).model_dump(mode="json")
            ],
        )
        raise typer.Exit(4) from exc
    except AgentsShipgateError as exc:
        typer.echo(f"Agents Shipgate error: {exc}", err=True)
        guidance = (
            "Re-run with --verbose for a stack trace, then file an issue if "
            "the error is not actionable."
        )
        emit_agent_mode_error(
            "other_error",
            message=str(exc),
            exit_code=4,
            next_action=guidance,
            next_actions=[NextAction(kind="review", why=guidance).model_dump(mode="json")],
        )
        raise typer.Exit(4) from exc
    except Exception as exc:  # noqa: BLE001 - CLI boundary.
        if verbose:
            logger.exception("unhandled exception")
        typer.echo(f"Internal error: {exc}", err=True)
        emit_agent_mode_error(
            "internal_error",
            message=str(exc),
            exit_code=4,
            next_action=(
                "Re-run with --verbose for a stack trace, then file an issue if "
                "the error is not actionable."
            ),
        )
        raise typer.Exit(4) from exc

    _warn_if_reports_staged(workspace, out)

    # Rendering runs inside the structured error boundary. Every envelope
    # invariant restates one the verifier already enforces, so a failure here
    # means two layers disagree — an internal bug, and one that must reach a
    # coding agent as the documented `internal_error`/exit 4 line rather than as
    # a bare traceback and exit 1.
    try:
        _emit_verify_stdout(
            verifier,
            workspace=workspace,
            exit_code=exit_code,
            preview=preview,
            stdout_format=stdout_format,
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary.
        if verbose:
            logger.exception("control projection failed")
        typer.echo(f"Internal error: {exc}", err=True)
        guidance = (
            "Re-run with --verbose for a stack trace, then file an issue. Until "
            "this run reports a control state, treat it as authorizing nothing."
        )
        emit_agent_mode_error(
            "internal_error",
            message=str(exc),
            exit_code=4,
            next_action=guidance,
            next_actions=[NextAction(kind="review", why=guidance).model_dump(mode="json")],
        )
        raise typer.Exit(4) from exc
    raise typer.Exit(exit_code)


def _emit_verify_stdout(
    verifier: VerifierArtifact,
    *,
    workspace: Path,
    exit_code: int,
    preview: bool,
    stdout_format: str,
) -> None:
    """Write the one stdout document this run promised."""

    if stdout_format == "json":
        typer.echo(json.dumps(verifier.model_dump(mode="json"), indent=2))
    elif stdout_format == "control":
        typer.echo(
            render_agent_control_envelope(
                _verify_envelope(verifier, workspace, exit_code, preview=preview)
            )
        )
    else:
        verdict = (
            verifier.release_decision.decision
            if verifier.release_decision is not None
            else ("skipped" if verifier.head_status == "skipped" else "failed")
        )
        # Lead with the operational answer. The release verdict below is the
        # gate's word on the change; these lines are the reader's word on what
        # they may do next, and printing the verdict first is what let
        # "succeeded" read as "done" in the #338 walkthrough. A drift refusal
        # arrives here as a denying envelope, not an exception, so the human
        # still sees the verdict and the exit code.
        for line in control_headline_lines(
            _verify_envelope(verifier, workspace, exit_code, preview=preview)
        ):
            typer.echo(line)
        # Every value below is repository-derived — a trigger rationale, a tool
        # name reaching the remediation text, a ref — and none is under
        # Shipgate's control. Sanitizing only the control headline left the
        # rest: a tool name containing newlines printed forged `Control:
        # complete` and `You may: merge` lines further down the same output.
        typer.echo(f"Agents Shipgate verify: {single_line_text(str(verdict))}")
        typer.echo(f"Trigger: {single_line_text(str(verifier.trigger.get('rationale')))}")
        typer.echo(f"Base status: {single_line_text(str(verifier.base_status))}")
        typer.echo(f"Exit code: {exit_code}")
        if (
            verifier.release_decision is not None
            and verifier.release_decision.decision == "insufficient_evidence"
        ):
            remediation = primary_evidence_remediation_text(
                verifier.release_decision.evidence_coverage
            )
            # This one is deliberately multi-line (#358 renders "Run: <cmd>" on
            # its own line), so each line is sanitized rather than the whole.
            for index, part in enumerate(remediation.splitlines() or [""]):
                prefix = "Improve evidence: " if index == 0 else ""
                typer.echo(f"{prefix}{single_line_text(part)}")
        typer.echo(f"Static-verdict boundary: {STATIC_VERDICT_DISCLAIMER}")


def _warn_if_reports_staged(workspace: Path, out: Path | None) -> None:
    """Advisory nudge when generated reports are staged for commit.

    Agents that run verify and then ``git add .`` stage the generated
    reports directory — a blocker in 7/31 W24 adoption cells. ``init``
    gitignores it; this warns when an existing checkout has it staged.
    Written to stderr only: never affects the verdict, exit code, or the
    stdout JSON contract. Silent outside a git checkout.
    """

    # verify resolves the reports dir relative to the GIT ROOT (run_verify),
    # so probe the root, not --workspace: a subdirectory --workspace would
    # otherwise miss root-level staged reports.
    try:
        root = ensure_git_workspace(workspace)
    except ConfigError:
        return
    if out is None:
        target = REPORTS_DIR_NAME
    else:
        try:
            target = out.resolve().relative_to(root).as_posix()
        except ValueError:
            target = out.name
    staged = staged_paths_under(root, target)
    if not staged:
        return
    shown = ", ".join(staged[:3]) + (" …" if len(staged) > 3 else "")
    typer.echo(
        f"warning: {len(staged)} generated Agents Shipgate report file(s) staged "
        f"for commit ({shown}). These are build artifacts — unstage them with "
        f"`git restore --staged {target}/` (agents-shipgate init gitignores this "
        f"directory).",
        err=True,
    )


def _resolve_verify_format(value: str | None, *, json_output: bool, preview: bool) -> str:
    """Resolve the stdout format from flags and the agent-mode environment.

    Precedence: explicit ``--format`` > ``--json`` shortcut > agent-mode
    auto-detection > text.

    Agent-mode auto-detection still resolves to ``json``. ``control`` is the
    promoted shape for a coding-agent control loop and is far cheaper, but
    silently swapping what an already-installed agent receives on stdout is a
    compatibility event, not a default change; the rollout is #323's.
    """
    if value is not None:
        return _parse_verify_format(value)
    if json_output or is_agent_mode():
        return "json"
    return "text"


def _parse_verify_format(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"text", "human"}:
        return "text"
    if normalized == "json":
        return "json"
    if normalized == "control":
        return "control"
    if normalized == "agent":
        raise ConfigError(
            "--format agent was removed in the 0.14.0 contract cleanup; use --format json"
        )
    raise ConfigError("--format must be text, json, or control for verify")


def _verify_envelope(
    verifier: VerifierArtifact,
    workspace: Path,
    exit_code: int,
    *,
    preview: bool,
) -> AgentControlEnvelope:
    """Project this run onto ``shipgate.agent_control/v1``.

    This goes through exactly the protocol ``agents-shipgate agent control``
    uses — the generation-safe read, validated against the live workspace, with
    the verifier captured inside it. Two entry points into one decision must
    apply one currency test: when they did not, a `--head` run in a dirty
    worktree reported `complete` with `permissions.merge=true` here while
    `agent control` was simultaneously refusing the same directory as
    `workspace_changed`.

    The captured verifier is then required to be *this invocation's*. Taking
    whatever generation is current was wrong in the other direction: a preview
    run reported a concurrent passing run's `complete`, `passed`, and
    `merge=true` under `source="run"`, printing that run's exit code while the
    process exited with its own. ``source="run"`` is a claim about whose result
    this is, so the identities must match exactly and a mismatch fails closed.

    A refusal does not raise. ``verify``'s exit code is the CI gate signal and
    must not change, so the run's verdict is still reported — with authority
    withheld and the refusal as the reason.
    """

    operation: AgentControlOperation = "preview" if preview else "verify"

    def denied(reason: str) -> AgentControlEnvelope:
        return denied_control_envelope(
            operation=operation,
            source="run",
            execution=verifier.execution,
            exit_code=exit_code,
            reason=reason,
        )

    reports_dir = _reports_dir_from_artifacts(verifier, workspace)
    if reports_dir is None:
        return denied(
            "This run recorded no verifier artifact path, so the control "
            "identity it published could not be located or validated."
        )
    try:
        result = read_current_control(
            reports_dir,
            live=lambda: live_workspace(workspace, reports_dir),
            capture=(VERIFIER_ARTIFACT_KEY,),
        )
    except CurrentControlUnavailable as exc:
        return denied(str(exc))
    captured = result.artifacts.get(VERIFIER_ARTIFACT_KEY)
    if captured is None:
        return denied(
            "The control identity this run published binds no verifier "
            "artifact, so no validated route could be recovered."
        )
    try:
        current = VerifierArtifact.model_validate_json(captured)
    except ValueError as exc:
        return denied(f"The bound verifier artifact could not be read: {exc}")
    if (current.request_id, current.decision_id) != (verifier.request_id, verifier.decision_id):
        return denied(
            "Another run published over this directory while this one was "
            "reporting; the control identity that is current closes a different "
            "request, so this run cannot speak for it. Re-run verification."
        )
    return envelope_from_verifier(
        current,
        operation=operation,
        source="run",
        exit_code=exit_code,
        pointer=result.pointer,
        artifact_root=_artifact_root(verifier, workspace),
    )


def _artifact_root(verifier: VerifierArtifact, workspace: Path) -> str | None:
    """The reports directory as the *invoking shell* would spell it.

    The verifier records paths relative to the Git root. The envelope is printed
    to stdout with no directory context, so a caller running from anywhere other
    than the Git root could not open them: only `workspace / path` existed.
    Rebasing onto the current working directory makes the emitted path openable
    exactly as given, which is what the schema promises.
    """

    reports_dir = _reports_dir_from_artifacts(verifier, workspace)
    if reports_dir is None:
        return None
    try:
        relative = PurePosixPath(Path(os.path.relpath(reports_dir, Path.cwd())).as_posix())
    except (OSError, ValueError):
        # Different drives on Windows, or an unreadable cwd.
        return reports_dir.as_posix()
    # A relative spelling only when it stays inside the invoking directory.
    # Climbing out of it is correct but neither shorter nor clearer than the
    # absolute path, and it spends the size budget on `../` segments.
    if relative.parts and relative.parts[0] == "..":
        return reports_dir.as_posix()
    return relative.as_posix()


def _reports_dir_from_artifacts(verifier: VerifierArtifact, workspace: Path) -> Path | None:
    recorded = verifier.artifacts.get("verifier_json")
    if not recorded:
        return None
    path = Path(recorded)
    if not path.is_absolute():
        try:
            path = ensure_git_workspace(workspace.resolve()) / path
        except ConfigError:
            path = workspace.resolve() / path
    return path.parent


def _parse_pr_comment_style(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"capability-review", "capability_review"}:
        return "capability-review"
    if normalized in {"findings", "v1-findings", "legacy"}:
        return "findings"
    raise ConfigError("--pr-comment-style must be capability-review or findings")


__all__ = ["verify"]
