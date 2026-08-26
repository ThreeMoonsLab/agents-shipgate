from __future__ import annotations

import logging
import os
from pathlib import Path

import typer

from agents_shipgate.cli._artifact_lifecycle import ArtifactLifecycleError
from agents_shipgate.cli._helpers import (
    _apply_strict_plugins,
    _diagnose_config_error,
    _echo_next_action_hint,
    _parse_fail_on,
    _parse_formats,
    _parse_packet_formats,
    _print_cli_summary,
    _resolve_config_paths,
    _run_multi_scan,
)
from agents_shipgate.cli.agent_mode import emit_agent_mode_error as _emit_agent_mode_error
from agents_shipgate.cli.diagnostics import input_parse_recovery, top_next_actions
from agents_shipgate.cli.scan.orchestrator import run_scan
from agents_shipgate.cli.workspace_guard import require_workspace
from agents_shipgate.core.agent_controls import git_root_for
from agents_shipgate.core.current_control import CurrentControlPublishError
from agents_shipgate.core.errors import AgentsShipgateError, ConfigError, InputParseError
from agents_shipgate.core.logging import configure_logging
from agents_shipgate.core.trust_roots import inspect_lexical_path_identity
from agents_shipgate.schemas.diagnostics import NextAction
from agents_shipgate.schemas.verification import VerificationContext

logger = logging.getLogger(__name__)


def _lexical_git_root(path: Path) -> Path | None:
    """Find the nearest Git root before any repository-internal symlink.

    Symlinks before the first Git marker are invocation anchors (for example
    ``repo-alias -> repo``). Once a Git marker has established the repository,
    a later symlink is a mutable path inside that repository and discovery
    must not cross it into another checkout.
    """

    selected: Path | None = None
    anchors = [*reversed(path.parents), path]
    for anchor in anchors:
        try:
            is_symlink = anchor.is_symlink()
        except OSError:
            return None
        if is_symlink:
            if selected is not None:
                break
            continue
        if (anchor / ".git").exists():
            selected = anchor
    if selected is None:
        return None
    try:
        return selected.resolve()
    except OSError:
        return None


def _build_verification_context(
    changed_files: Path | None,
) -> VerificationContext | None:
    """Build the verification context from a --changed-files file.

    Returns None when no file is given (plain scan behavior). Raises
    ConfigError when the path is supplied but unreadable — a CLI flag
    value problem, not a manifest problem.
    """
    if changed_files is None:
        return None
    try:
        text = changed_files.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(
            f"--changed-files file could not be read: {changed_files} ({exc})"
        ) from exc
    # Preserve exact path spellings. The transport delimiter is the literal
    # LF byte only: ``str.splitlines`` would also split legal POSIX filenames
    # at VT, FF, NEL, U+2028, and U+2029 and could detach a custom manifest
    # identity from its trust-root finding.
    paths = [line for line in text.split("\n") if line]
    return VerificationContext(changed_files=paths, diff_text_available=False)


def _configured_manifest_identity(
    *,
    config_path: Path,
    workspace: Path | None,
) -> tuple[str, Path]:
    """Bind the loaded path to one repo-relative manifest identity.

    The returned physical path is the same normalized lexical path inspected
    here. The scan must load that path rather than the pre-normalized CLI
    spelling: on POSIX, ``link/../gate.yml`` follows ``link`` before applying
    ``..``, so inspecting the normalized path but loading the original would
    validate one manifest and evaluate another.
    """

    lexical = Path(os.path.abspath(os.path.normpath(os.fspath(config_path))))
    try:
        canonical_parent = lexical.parent.resolve()
    except OSError:
        canonical_parent = lexical.parent
    # Resolve this only to choose the comparison root. The identity mapper
    # below starts from the first *outer* lexical anchor that resolves inside
    # that root, then inspects every repository-relative component without
    # following it. That distinction allows `/var` or `repo-alias -> repo`
    # while still rejecting `repo/gate-dir -> .`, whose retargeting changes
    # which manifest the scan loads.
    candidate = canonical_parent / lexical.name
    lexical_git_root = _lexical_git_root(lexical.parent)
    cwd = Path.cwd().resolve()
    if lexical_git_root is not None:
        root = lexical_git_root
    elif workspace is not None:
        workspace_root = workspace.resolve()
        root = git_root_for(workspace_root) or workspace_root
    elif lexical.is_relative_to(cwd) or candidate.is_relative_to(cwd):
        # A caller standing inside a non-Git project still supplies a concrete
        # lexical anchor. Prefer it to a Git repository reached only by an
        # internal config-path symlink.
        root = git_root_for(cwd) or cwd
    else:
        canonical_git_root = git_root_for(canonical_parent)
        if canonical_git_root is None:
            raise ConfigError(
                "--changed-files paths are repository-relative, but no "
                "repository root could be proven for --config "
                f"{config_path}. Invoke scan from the repository root or "
                "use a Git/workspace-rooted checkout."
            )
        # No lexical workspace/cwd root owns the config path, so a Git root
        # reached through an external alias is the only remaining provable
        # repository anchor.
        root = canonical_git_root

    relative: Path | None = None
    for anchor in reversed(lexical.parents):
        try:
            resolved_anchor = anchor.resolve()
            anchor_tail = resolved_anchor.relative_to(root)
            lexical_tail = lexical.relative_to(anchor)
        except (OSError, ValueError):
            continue
        relative = anchor_tail / lexical_tail
        break
    if relative is None:
        raise ConfigError(
            "--config could not be mapped into the changed-files workspace: "
            f"{config_path}"
        )
    if "\n" in relative.as_posix() or "\r" in relative.as_posix():
        raise ConfigError(
            "--config cannot contain a newline when --changed-files uses a "
            "line-delimited path transport."
        )

    # Normalize filesystem-proved case/Unicode aliases, but never follow a
    # symlink below the repository anchor. There can be more than one alias
    # component, so correct one stored entry at a time and inspect again.
    for _attempt in range(len(relative.parts) + 1):
        issue = inspect_lexical_path_identity(root, relative)
        if issue is None:
            return relative.as_posix(), lexical
        if issue.kind == "symlink":
            raise ConfigError(
                "--config must not contain repository symlink components: "
                f"{issue.requested}"
            )
        if issue.kind != "alias" or issue.actual is None:
            detail = f": {issue.detail}" if issue.detail else ""
            raise ConfigError(
                "--config could not be inspected safely: "
                f"{issue.requested}{detail}"
            )
        requested = root / relative
        try:
            suffix = requested.relative_to(issue.requested)
            relative = (issue.actual / suffix).relative_to(root)
        except ValueError as exc:
            raise ConfigError(
                "--config alias could not be mapped safely: "
                f"{issue.requested}"
            ) from exc
    # The loop is bounded by the number of components. Reaching this point
    # means aliases did not converge to one stored path.
    raise ConfigError(f"--config identity did not converge safely: {config_path}")


def register(app: typer.Typer) -> None:
    @app.command(hidden=True)
    def scan(
        config: str = typer.Option(
            "shipgate.yaml",
            "--config",
            "-c",
            help="Path or quoted glob for shipgate.yaml.",
        ),
        workspace: Path | None = typer.Option(
            None,
            "--workspace",
            help="Scan every shipgate.yaml below this workspace.",
        ),
        out: Path | None = typer.Option(
            None,
            "--out",
            help="Output directory for reports. Overrides manifest output.directory.",
        ),
        formats: str = typer.Option(
            "markdown,json",
            "--format",
            help="Comma-separated report formats: markdown,json,sarif.",
        ),
        ci_mode: str | None = typer.Option(
            None,
            "--ci-mode",
            help="advisory or strict. Overrides manifest ci.mode.",
        ),
        fail_on: str | None = typer.Option(
            None,
            "--fail-on",
            help="Comma-separated severities that fail CI, for example critical,high.",
        ),
        baseline: Path | None = typer.Option(
            None,
            "--baseline",
            help="Path to a local baseline JSON. Strict mode fails only on new findings.",
        ),
        diff_from: Path | None = typer.Option(
            None,
            "--diff-from",
            help="Prior report.json or v0.3 baseline JSON used for tool-surface diff.",
        ),
        baseline_mode: str = typer.Option(
            "new-findings",
            "--baseline-mode",
            help="Baseline comparison mode. Supported value: new-findings.",
        ),
        policy_packs: list[Path] | None = typer.Option(
            None,
            "--policy-pack",
            help="Additional declarative YAML policy pack path. May be supplied multiple times.",
        ),
        deep_import: bool = typer.Option(
            False,
            "--deep-import",
            help="Deferred. Explicit import execution is not supported yet.",
            hidden=True,
        ),
        no_plugins: bool = typer.Option(
            False,
            "--no-plugins",
            help=(
                "Do not load third-party check plugins OR third-party adapters "
                "(v0.20+: agents_shipgate.adapters entry-point group), even "
                "when AGENTS_SHIPGATE_ENABLE_PLUGINS is set."
            ),
        ),
        strict_plugins: bool = typer.Option(
            False,
            "--strict-plugins",
            help=(
                "Exit non-zero (code 4) if any loaded plugin OR third-party "
                "adapter (v0.20+) failed validation or produced runtime "
                "errors. Default lenient mode records the failure in "
                "report.loaded_plugins / report.loaded_adapters but proceeds "
                "with the scan."
            ),
        ),
        suggest_patches: bool = typer.Option(
            False,
            "--suggest-patches",
            help=(
                "Attach machine-applicable patches (or ManualPatch fallback) to "
                "every active finding. Use `agents-shipgate apply-patches` to "
                "apply them; the report stays read-only."
            ),
        ),
        no_heuristics: bool = typer.Option(
            False,
            "--no-heuristics",
            help=(
                "Filter out findings whose provenance_kind is "
                "keyword_heuristic or regex_heuristic (v0.21+). Filtered "
                "findings stay in findings[] marked suppressed; they no "
                "longer gate release. static_declaration, ast_extraction, "
                "and policy_pack findings are unaffected. The "
                "report.heuristics_filter envelope records aggregate "
                "counts. Useful for security/GRC reviewers who want "
                "declared-only findings."
            ),
        ),
        packet: bool | None = typer.Option(
            None,
            "--packet/--no-packet",
            help=(
                "Emit the Release Evidence Packet alongside report.{md,json}. "
                "Defaults to manifest output.packet.enabled (true unless the "
                "manifest disables it). Use --no-packet to override."
            ),
        ),
        packet_format: str | None = typer.Option(
            None,
            "--packet-format",
            help=(
                "Comma-separated packet formats: md,json,html,pdf. "
                "Default from manifest output.packet.formats (md,json,html). "
                "PDF requires the [pdf] extras."
            ),
        ),
        changed_files: Path | None = typer.Option(
            None,
            "--changed-files",
            help=(
                "Path to a file of newline-separated changed paths "
                "(repo-relative). Supplies the verification context so "
                "trust-root checks (e.g. SHIP-VERIFY-TRUST-ROOT-TOUCHED) "
                "fire. Single-config scans only; without it, plain scan "
                "behavior is unchanged."
            ),
        ),
        verbose: bool = typer.Option(False, "--verbose", help="Show debug extraction details."),
    ) -> None:
        """Run the deterministic merge gate for AI-generated agent capability changes."""
        require_workspace(workspace)
        # Parse CLI options first, in their own try block. ConfigError raised
        # here is about flag values, not the manifest — emitting a manifest
        # diagnostic ("edit shipgate.yaml") would route the agent to the
        # wrong fix.
        try:
            configure_logging(verbose=verbose)
            parsed_formats = _parse_formats(formats)
            parsed_packet_formats = _parse_packet_formats(packet_format)
            if ci_mode and ci_mode not in {"advisory", "strict"}:
                raise ConfigError("--ci-mode must be advisory or strict")
            parsed_fail_on = _parse_fail_on(fail_on)
            verification_context = _build_verification_context(changed_files)
        except ConfigError as exc:
            typer.echo(f"Config error: {exc}", err=True)
            guidance = (
                "Fix the invalid CLI flag value referenced in the error and "
                "re-run scan."
            )
            _emit_agent_mode_error(
                "config_error",
                message=str(exc),
                next_action=guidance,
                next_actions=[
                    NextAction(
                        kind="review",
                        why=guidance,
                        expects=(
                            "Re-run with a flag value the option parser accepts."
                        ),
                    ).model_dump(mode="json")
                ],
            )
            raise typer.Exit(2) from exc

        try:
            config_paths = _resolve_config_paths(config=config, workspace=workspace)
            if verification_context is not None and len(config_paths) > 1:
                # --changed-files describes ONE PR's diff against ONE
                # manifest's trust roots. Fanning the same changed-files
                # list across every resolved manifest would mis-attribute
                # a trust-root touch to unrelated manifests (e.g. flag both
                # a/ and b/ for an edit under a/). Reject rather than guess.
                raise ConfigError(
                    "--changed-files is single-config only, but "
                    f"{len(config_paths)} manifests resolved. Re-run scan "
                    "against one manifest (-c <path>) when supplying "
                    "--changed-files."
                )
            if verification_context is not None and len(config_paths) == 1:
                configured_manifest_path, validated_config_path = (
                    _configured_manifest_identity(
                        config_path=config_paths[0],
                        workspace=workspace,
                    )
                )
                config_paths[0] = validated_config_path
                verification_context = verification_context.model_copy(
                    update={
                        "configured_manifest_path": configured_manifest_path
                    }
                )
            if len(config_paths) == 1:
                report, exit_code = run_scan(
                    config_path=config_paths[0],
                    output_dir=out,
                    formats=parsed_formats,
                    ci_mode=ci_mode,
                    fail_on=parsed_fail_on,
                    baseline_path=baseline,
                    diff_from_path=diff_from,
                    baseline_mode=baseline_mode,
                    deep_import=deep_import,
                    policy_pack_paths=policy_packs,
                    plugins_enabled=False if no_plugins else None,
                    verbose=verbose,
                    suggest_patches=suggest_patches,
                    packet_enabled=packet,
                    packet_formats=parsed_packet_formats,
                    no_heuristics=no_heuristics,
                    verification_context=verification_context,
                )
                exit_code = _apply_strict_plugins(
                    report, exit_code, strict_plugins=strict_plugins
                )
                _print_cli_summary(report, ci_mode or "advisory", exit_code, verbose=verbose)
                raise typer.Exit(exit_code)
            exit_code = _run_multi_scan(
                config_paths=config_paths,
                out=out,
                formats=parsed_formats,
                ci_mode=ci_mode,
                fail_on=parsed_fail_on,
                baseline=baseline,
                diff_from=diff_from,
                baseline_mode=baseline_mode,
                deep_import=deep_import,
                policy_packs=policy_packs or [],
                plugins_enabled=False if no_plugins else None,
                verbose=verbose,
                suggest_patches=suggest_patches,
                packet_enabled=packet,
                packet_formats=parsed_packet_formats,
                strict_plugins=strict_plugins,
                no_heuristics=no_heuristics,
            )
        except ConfigError as exc:
            typer.echo(f"Config error: {exc}", err=True)
            diagnostics = _diagnose_config_error(
                config=config,
                workspace=workspace,
                exc=exc,
                plugins_enabled=False if no_plugins else None,
            )
            flattened = top_next_actions(diagnostics)
            _echo_next_action_hint(flattened)
            _emit_agent_mode_error(
                "config_error",
                message=str(exc),
                next_action=flattened[0].to_legacy_string(),
                next_actions=[a.model_dump(mode="json") for a in flattened],
            )
            raise typer.Exit(2) from exc
        except InputParseError as exc:
            typer.echo(f"Input parsing error: {exc}", err=True)
            actions = input_parse_recovery(exc, manifest_path=config)
            _echo_next_action_hint(actions)
            _emit_agent_mode_error(
                "input_parse_error",
                message=str(exc),
                # The prose form, not `to_legacy_string()`: this surface has
                # always published the sentence here, and `Edit <path>` drops
                # the half that says what to change.
                next_action=actions[0].why,
                next_actions=[a.model_dump(mode="json") for a in actions],
                # The identity model stays available to machine consumers and
                # bug reports even though it left the sentence (#329). Absent
                # for the failures that carry no structured payload.
                details=exc.details or None,
            )
            raise typer.Exit(3) from exc
        except ArtifactLifecycleError as exc:
            typer.echo(f"Agents Shipgate error: {exc}", err=True)
            guidance = (
                f"Remove {exc.path} and re-run scan; Agents Shipgate will not "
                "write a replacement report while a stale verifier artifact "
                "survives."
            )
            actions = [
                NextAction(
                    kind="edit",
                    path=str(exc.path),
                    why=guidance,
                    expects=(
                        "The stale verifier artifact is absent, then scan writes "
                        "one current report set."
                    ),
                )
            ]
            _echo_next_action_hint(actions)
            _emit_agent_mode_error(
                "other_error",
                message=str(exc),
                next_action=guidance,
                next_actions=[action.model_dump(mode="json") for action in actions],
            )
            raise typer.Exit(4) from exc
        except CurrentControlPublishError as exc:
            # Fail closed: the pointer stayed non-terminal, so nothing in the
            # directory is current even though report artifacts were written.
            typer.echo(f"Agents Shipgate error: {exc}", err=True)
            guidance = (
                f"Make {exc.path} writable, then re-run scan. Until the "
                "control pointer publishes, no decision in this directory is "
                "current and no cached control state may be acted on."
            )
            actions = [
                NextAction(
                    kind="edit",
                    path=str(exc.path),
                    why=guidance,
                    expects=(
                        "current-control.json publishes, naming the control "
                        "identity that is current."
                    ),
                )
            ]
            _echo_next_action_hint(actions)
            _emit_agent_mode_error(
                "other_error",
                message=str(exc),
                next_action=guidance,
                next_actions=[action.model_dump(mode="json") for action in actions],
            )
            raise typer.Exit(4) from exc
        except AgentsShipgateError as exc:
            typer.echo(f"Agents Shipgate error: {exc}", err=True)
            guidance = (
                "Re-run with --verbose for a stack trace, then file an issue if "
                "the error is not actionable."
            )
            _emit_agent_mode_error(
                "other_error",
                message=str(exc),
                next_action=guidance,
                next_actions=[
                    NextAction(kind="review", why=guidance).model_dump(mode="json")
                ],
            )
            raise typer.Exit(4) from exc
        except typer.Exit:
            raise
        except Exception as exc:  # noqa: BLE001 - CLI boundary.
            if verbose:
                logger.exception("unhandled exception")
            typer.echo(f"Internal error: {exc}", err=True)
            guidance = (
                "Re-run with --verbose for a stack trace; this is a bug — please "
                "file an issue."
            )
            _emit_agent_mode_error(
                "internal_error",
                message=str(exc),
                next_action=guidance,
                next_actions=[
                    NextAction(kind="review", why=guidance).model_dump(mode="json")
                ],
            )
            raise typer.Exit(4) from exc

        raise typer.Exit(exit_code)
