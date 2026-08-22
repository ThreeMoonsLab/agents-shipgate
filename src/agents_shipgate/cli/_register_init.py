from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path

import typer

from agents_shipgate.cli.agent_mode import emit_agent_mode_error as _emit_agent_mode_error
from agents_shipgate.cli.discovery import (
    DEFAULT_MAX_PYTHON_FILES,
    detect_workspace,
    render_auto_manifest,
    render_manifest_template,
    select_agent_name,
    write_ci_workflow,
)
from agents_shipgate.cli.discovery.agent_instructions import (
    InvalidSelector,
    apply_agent_instructions,
    parse_selector,
)
from agents_shipgate.cli.discovery.agent_instructions.adoption_kit import (
    AdoptionKitError,
    load_adoption_kit_config,
)
from agents_shipgate.cli.discovery.agent_instructions.targets import SPECS as _AI_SPECS
from agents_shipgate.cli.discovery.gitignore_block import (
    GitignoreOutcomeStatus,
    ensure_reports_gitignore,
)
from agents_shipgate.cli.discovery.local_contract import LOCAL_CONTRACT_RELATIVE_PATH
from agents_shipgate.cli.discovery.placeholders import collect_placeholders
from agents_shipgate.cli.discovery.scope import repository_root
from agents_shipgate.cli.scope_routing import (
    MAX_LISTED_SCOPE_CANDIDATES,
    is_adopted,
    scope_candidate_actions,
)
from agents_shipgate.cli.setup_control import (
    SETUP_COMPLETE,
    SETUP_INCOMPLETE,
    setup_control_envelope,
    setup_input_id,
)
from agents_shipgate.cli.workspace_guard import require_workspace
from agents_shipgate.core.errors import DiscoveryError
from agents_shipgate.invocation import render_command
from agents_shipgate.schemas.agent_control import AgentActionKind
from agents_shipgate.schemas.detect import AgentProjectCandidate
from agents_shipgate.schemas.diagnostics import NextAction


def _validate_manifest_text(text: str) -> None:
    """Run the generated manifest through the schema before write."""
    import yaml

    from agents_shipgate.schemas.manifest import AgentsShipgateManifest

    data = yaml.safe_load(text)
    AgentsShipgateManifest.model_validate(data)


_VERIFY_ALIAS_COMMAND = "agents-shipgate verify --json"
_MAKEFILE_ALIAS_BLOCK = (
    "\n# agents-shipgate:start verify alias\n"
    ".PHONY: shipgate-verify\n"
    "shipgate-verify:\n"
    f"\t{_VERIFY_ALIAS_COMMAND}\n"
    "# agents-shipgate:end\n"
)


def _apply_claude_code_extras(workspace: Path, *, write: bool) -> dict[str, object]:
    """Install hooks + conventional verify aliases for the Claude Code setup."""
    from agents_shipgate.cli.install_hooks import render_or_install_hooks
    from agents_shipgate.core.errors import ConfigError

    hooks: dict[str, object]
    try:
        result = render_or_install_hooks(
            workspace=workspace,
            target="claude-code",
            write=write,
            config=Path("shipgate.yaml"),
            base="origin/main",
            head="",
            ci_mode="advisory",
        )
        hooks = {
            "settings_path": result.settings_path,
            "settings_status": result.settings_status,
            "script_path": result.script_path,
            "script_status": result.script_status,
        }
    except ConfigError as exc:
        hooks = {"status": "error", "message": str(exc)}
    return {
        "hooks": hooks,
        "verify_alias": {
            "makefile": _upsert_makefile_alias(workspace, write=write),
            "package_json": _upsert_package_json_alias(workspace, write=write),
        },
    }


def _upsert_makefile_alias(workspace: Path, *, write: bool) -> dict[str, str]:
    for name in ("Makefile", "makefile", "GNUmakefile"):
        path = workspace / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return {"path": str(path), "status": "skipped_unreadable", "message": str(exc)}
        if "shipgate-verify:" in text or "# agents-shipgate:start verify alias" in text:
            return {"path": str(path), "status": "unchanged"}
        if not write:
            return {"path": str(path), "status": "planned"}
        suffix = "" if text.endswith("\n") else "\n"
        path.write_text(text + suffix + _MAKEFILE_ALIAS_BLOCK, encoding="utf-8")
        return {"path": str(path), "status": "appended"}
    return {"status": "skipped_missing"}


def _upsert_package_json_alias(workspace: Path, *, write: bool) -> dict[str, str]:
    path = workspace / "package.json"
    if not path.is_file():
        return {"status": "skipped_missing"}
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"path": str(path), "status": "skipped_invalid", "message": str(exc)}
    if not isinstance(data, dict):
        return {"path": str(path), "status": "skipped_invalid"}
    scripts = data.get("scripts")
    if scripts is not None and not isinstance(scripts, dict):
        return {"path": str(path), "status": "skipped_invalid"}
    if scripts and "shipgate:verify" in scripts:
        return {"path": str(path), "status": "unchanged"}
    if not write:
        return {"path": str(path), "status": "planned"}
    data.setdefault("scripts", {})["shipgate:verify"] = _VERIFY_ALIAS_COMMAND
    indent = _detect_json_indent(text)
    rendered = json.dumps(data, indent=indent, ensure_ascii=False)
    if text.endswith("\n"):
        rendered += "\n"
    path.write_text(rendered, encoding="utf-8")
    return {"path": str(path), "status": "appended"}


def _detect_json_indent(text: str) -> int:
    for line in text.splitlines()[1:]:
        stripped = line.lstrip(" ")
        if stripped and len(line) > len(stripped):
            return len(line) - len(stripped)
    return 2


def _describe_candidate(
    candidate: AgentProjectCandidate, *, workspace: Path | None = None
) -> str:
    """One candidate as a line a human can choose from.

    Not every project names its agent in a string literal — a config-driven
    ``LlmAgent(name=CONFIG.agent_name)`` has none to parse — so the marker
    that made the directory a project stands in for it rather than leaving
    an empty pair of brackets.

    A candidate that already carries a manifest says so. The routing published
    beside this list sends those to ``doctor``, because the ``init --write``
    the next line recommends refuses a manifest it will not overwrite — and a
    list that reads identically for both leaves the human form of this refusal
    contradicting the JSON form of the same run (#397 review).
    """

    detail = ", ".join(candidate.agent_names) or (candidate.marker or "project root")
    adopted = (
        workspace is not None
        and candidate.path != "."
        and is_adopted(workspace / candidate.path) is not None
    )
    return f"{candidate.path} ({detail})" + (" — already adopted" if adopted else "")


def _scan_command_config(target: Path) -> str:
    """How the follow-up ``scan`` should name the manifest just written.

    ``scan -c shipgate.yaml`` resolves against the working directory, so a
    manifest written into ``apps/a`` is not found from the repository root
    and the emitted next action exits 2 (#363 review). The bare spelling is
    kept when the manifest really is in the current directory, so the
    ordinary root adoption emits exactly what it always emitted.
    """

    try:
        if target.parent == Path.cwd().resolve():
            return target.name
    except OSError:  # pragma: no cover - unreadable cwd
        pass
    return str(target)


def _requested_setup_flags(
    *,
    ci: bool,
    claude_code: bool,
    agent_instructions: str | None,
) -> list[str]:
    """The setup this invocation asked for, as flags a rerun must repeat.

    A recovery command that drops ``--ci`` or an agent-instruction
    selection completes with less than the caller requested and reports
    success for it. Mirrors ``_rerun_options`` in the verifier, for the
    same reason.

    ``--agent-instructions-kit`` is deliberately not here: it is a path,
    and a path is only meaningful relative to a workspace. See
    :func:`agents_shipgate.cli.scope_routing.rebased_kit_flags`.
    """

    flags: list[str] = []
    if ci:
        flags.append("--ci")
    if claude_code:
        flags.append("--claude-code")
    if agent_instructions is not None:
        flags.append(f"--agent-instructions={agent_instructions}")
    return flags


def _unresolved_scope_message(
    workspace: Path,
    candidates: list[AgentProjectCandidate],
    *,
    scope: str,
    truncated: bool = False,
    parse_truncated: bool = False,
    project_roots: int = 0,
    python_file_total: int = 0,
) -> str:
    if scope == "single" and parse_truncated:
        # The scope is settled and the *classification* is not. Rendering a
        # manifest here declares an agent name and a tool surface read from
        # part of the tree — on the reported repository, `CHANGE_ME` and no
        # tools at all, written with exit 0 (#399 review).
        return "\n".join(
            [
                f"Refusing to write shipgate.yaml: discovery of {workspace} "
                f"stopped at the Python-file cap, so the agent name and tool "
                "surface a manifest would declare were read from part of the "
                "workspace.",
                f"Re-run with --max-python-files {python_file_total}, a bound "
                "that covers every Python file here, or point --workspace at "
                "the project you are changing.",
            ]
        )
    if scope == "unknown":
        lines = [
            f"Refusing to write shipgate.yaml: discovery stopped at the "
            f"Python-file cap while {workspace} holds {project_roots} "
            "candidate project scopes, so whether one manifest describes it "
            "was not established.",
        ]
        if candidates:
            lines.append("Projects found before the cap:")
            for candidate in candidates[:MAX_LISTED_SCOPE_CANDIDATES]:
                lines.append(f"  - {_describe_candidate(candidate, workspace=workspace)}")
    else:
        lines = [
            f"Refusing to write shipgate.yaml: {workspace} holds "
            f"{len(candidates)} self-contained projects that define agents, "
            "and one manifest describes one agent surface.",
            "Candidate project directories:",
        ]
        for candidate in candidates[:MAX_LISTED_SCOPE_CANDIDATES]:
            lines.append(f"  - {_describe_candidate(candidate, workspace=workspace)}")
    remaining = len(candidates) - MAX_LISTED_SCOPE_CANDIDATES
    if remaining > 0:
        lines.append(
            f"  - ... ({remaining} more; see auto_detected.agent_project_candidates "
            "in --json)"
        )
    if parse_truncated and scope != "unknown":
        # The refusal hands this list over as the thing to choose from, so it
        # has to say when the walk that produced it was cut short. Without
        # this an adopter reads their own project's absence as an answer
        # (#395); the uncapped project-root census bounds the claim.
        lines.append(
            "This list may be incomplete: discovery stopped at the "
            f"Python-file cap in a workspace holding {project_roots} candidate "
            "project scopes, so any project in the part of the tree that was "
            "not read is missing from it."
            if truncated
            else (
                "Discovery also stopped at the Python-file cap, so the agent "
                "name and tool surface a manifest would declare were read "
                "from part of the workspace."
            )
        )
    lines.append(
        "Re-run init with --workspace pointed at the project you are changing, "
        "or pass --allow-unresolved-scope to write one manifest for this "
        "workspace as a whole."
    )
    if any(
        candidate.path != "." and is_adopted(workspace / candidate.path) is not None
        for candidate in candidates
    ):
        lines.append(
            "A project marked already adopted has a manifest init will not "
            "overwrite; ask doctor --config <that manifest> what it still "
            "owes instead."
        )
    if scope == "unknown" or parse_truncated:
        lines.append(
            f"Re-run with --max-python-files {python_file_total}, a bound that "
            "covers every Python file here, to settle what the capped pass "
            "could not."
        )
    return "\n".join(lines)


def _unresolved_scope_actions(
    workspace: Path,
    candidates: list[AgentProjectCandidate],
    *,
    scope: str,
    setup_flags: list[str],
    kit: Path | None,
    truncated: bool = False,
    parse_truncated: bool = False,
    python_file_total: int = 0,
    setup_command: list[str] | None = None,
    refreshes_existing: bool = False,
) -> list[NextAction]:
    """Rank the decision above the commands that carry it out.

    For an *ambiguous* scope, rank 1 is deliberately not a command:
    promoting one candidate would make the same arbitrary pick this refusal
    exists to prevent. The per-candidate commands follow, in path order, so
    a caller that knows which project it is changing can match on the path
    rather than trust an ordering. Each repeats the setup flags this
    invocation asked for — a recovery that silently drops ``--ci`` or an
    agent-instruction selection completes with less than the caller
    requested.

    A capped parse is a different obligation wearing the same shape. Nothing
    has been chosen there because nothing has been *seen*, and finishing the
    scan is mechanical — so whenever the parse was cut short, rank 1 is this
    same ``init`` invocation with a bound that covers every Python file. It
    settles the scan and carries out what the caller asked for in one step,
    and it cannot loop: at that bound the next run either writes or refuses
    with a list that is an enumeration rather than a lower bound. Asking a
    human to choose from a list the refusal itself calls incomplete is the
    thing to avoid, whether the scope is ``unknown`` or already contested
    (#399 review).
    """

    retry = (
        NextAction(
            kind="command",
            command=render_command(
                [
                    *(setup_command or ["init", "--workspace", str(workspace), "--write"]),
                    "--max-python-files",
                    str(python_file_total),
                    "--json",
                ]
            ),
            why=(
                "Discovery stopped at its Python-file cap, so what this run "
                "read is part of the workspace. This is the same setup at a "
                "bound that covers every Python file: it settles the scan, "
                "and either writes or refuses with a settled candidate list."
            ),
            expects=(
                "Either shipgate.yaml written from a complete parse, or a "
                "refusal whose agent_project_candidates are an enumeration."
            ),
        )
        if parse_truncated and python_file_total > 0
        else None
    )
    why = (
        f"{workspace} defines agents in {len(candidates)} separate projects; "
        "pick the one this change belongs to. Shipgate will not choose for "
        "you — the manifest declares one agent's name, purpose, and tool "
        "surface."
        + (
            " That list is a lower bound, not an enumeration: discovery "
            "stopped at the Python-file cap, so re-run detection with a "
            "higher --max-python-files before concluding a project is absent."
            if truncated
            else ""
        )
        if scope != "unknown"
        else (
            f"Discovery of {workspace} was capped before it could tell whether "
            "one manifest describes it. Finish the scan with the command "
            "above, or name the project you are changing."
        )
    )
    if scope == "single" and parse_truncated:
        why = (
            f"Discovery of {workspace} stopped at its Python-file cap, so the "
            "agent name and tool surface a manifest would declare were read "
            "from part of the workspace. Finish the scan with the command "
            "above, or point --workspace at the project you are changing."
        )
    decision = NextAction(
        kind="review",
        why=why,
        expects=(
            "One project directory chosen from "
            "auto_detected.agent_project_candidates."
        ),
    )
    return [
        *([retry] if retry is not None else []),
        decision,
        *scope_candidate_actions(
            workspace,
            candidates,
            setup_flags=setup_flags,
            kit=kit,
            init_refreshes_existing=refreshes_existing,
        ),
    ]


def _claude_code_outcome_lines(outcome: dict[str, object]) -> list[str]:
    lines: list[str] = []
    hooks = outcome.get("hooks")
    if isinstance(hooks, dict):
        if hooks.get("status") == "error":
            lines.append(f"Claude Code hooks: error — {hooks.get('message')}")
        else:
            lines.append(
                "Claude Code hooks: "
                f"{hooks.get('settings_path')} ({hooks.get('settings_status')}), "
                f"{hooks.get('script_path')} ({hooks.get('script_status')})"
            )
    alias = outcome.get("verify_alias")
    if isinstance(alias, dict):
        for key in ("makefile", "package_json"):
            entry = alias.get(key)
            if not isinstance(entry, dict):
                continue
            status = entry.get("status")
            if status == "skipped_missing":
                continue
            label = entry.get("path", key)
            lines.append(f"Verify alias ({key}): {label} ({status})")
    return lines


def _manifest_placeholders(
    target: Path,
    *,
    template: str,
    placeholders: list[dict[str, object]],
    write: bool,
) -> tuple[list[dict[str, object]], bytes | None, str | None]:
    """The placeholders of the manifest a caller would actually be routed to.

    Returns them together with the exact bytes they were read from, so the
    identity of this answer and the answer itself come from one snapshot rather
    than two reads that an edit could land between.

    When a manifest exists on disk it is the authority, whether this run wrote it
    or found it.

    A dry run carries no routing obligation at all: nothing was written, so there
    is no manifest for a person to review and the honest next step is to write
    one. The template's own placeholders stay in the payload's ``placeholders``
    field, where they always were — they describe what the caller will owe *after*
    writing, not what anyone owes now.

    The third element is the loader's objection to those bytes, or ``None`` when
    they load. Scanning only for placeholders treated *any* existing file as a
    configured manifest: an empty ``shipgate.yaml`` has no ``CHANGE_ME`` in it,
    so ``init --write --agent-instructions=...`` reported ``setup_complete`` and
    handed back a verify command that exits 2. Manifest validity is a setup fact
    in #323, and this is where the setup route learns it.
    """

    if write or target.exists():
        try:
            data = target.read_bytes()
        except OSError as exc:
            # A *refused* write leaves no file at all — an ambiguous scope, for
            # instance — and that is not a defective manifest, it is the absence
            # of one, which the caller's own status already describes. Only a
            # file that exists and cannot be read is a defect.
            if not target.exists():
                return [], None, None
            # Unreadable is not "clean". Fall back to the template's obligations
            # rather than reporting an unverified all-clear.
            return placeholders, None, str(exc)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            # A manifest that is not UTF-8 is not a manifest. Replacing the bad
            # bytes would hand the route a *different*, valid document.
            return [], data, f"{target} is not valid UTF-8: {exc}"
        return collect_placeholders(text), data, _manifest_defect(text)
    return [], None, None


def _manifest_defect(text: str) -> str | None:
    """The loader's objection to this manifest, or ``None`` when it loads.

    Deliberately the same loader the rest of the CLI uses, rather than a
    lighter-weight parse: a route that declares setup complete is asserting the
    next command will run, and the only thing that can support that is what the
    next command will do.
    """

    from agents_shipgate.config.loader import load_manifest_text
    from agents_shipgate.core.errors import ConfigError

    try:
        load_manifest_text(text)
    except ConfigError as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001 - any loader objection routes the same way.
        return str(exc)
    return None


def _init_reason(manifest_status: str, *, target: Path, write: bool) -> str:
    if manifest_status == "written":
        return f"Wrote {target}."
    if manifest_status == "skipped_existing":
        return f"{target} already exists and was left untouched."
    if not write:
        return f"Rendered a manifest for {target} without writing it."
    return "init made no manifest change."


def _init_advance(
    *,
    workspace: Path,
    target: Path,
    write: bool,
    manifest_status: str,
    manifest_exit: int,
    next_action_create: NextAction,
    skipped_target: object | None,
    scope_actions: Sequence[NextAction] = (),
    manifest_defect: str | None = None,
    setup_flags: Sequence[str] = (),
    workflow_status: str | None = None,
) -> tuple[NextAction, AgentActionKind, str, bool]:
    """The step init already names, typed for the control envelope.

    Every branch reuses a route the command publishes elsewhere rather than
    composing a new one, so ``control.next_action`` and the JSON payload's
    ``next_action`` cannot drift apart. The dry-run branch is the only place a
    command is spelled here, and it is the ``--write`` form of the invocation
    the caller just made, which the human-readable output already tells them to
    run.

    A refused instruction target outranks the manifest route: init reports a
    non-zero exit for it, and a control state that pointed past it would call a
    failed run's onward step the next thing to do.

    ``setup_flags`` repeats what this invocation asked for. A dry run advanced to
    a bare ``init --write``, which silently drops ``--ci``, an
    ``--agent-instructions`` selection, and ``--allow-unresolved-scope`` — the
    last of which makes the emitted command exit 2 in the very monorepo that
    needed it. The scoped-refusal recovery already threads them; the dry run has
    the same obligation, and for the same reason ``_requested_setup_flags``
    states: a recovery that completes with less than the caller requested
    reports success for work it did not do.
    """

    if manifest_defect is not None and manifest_status != "written":
        # A file that exists is not a configured manifest. Scanning it for
        # placeholders found none in an *empty* `shipgate.yaml`, so the refresh
        # path called it `setup_complete` and handed back a verify command that
        # exits 2 on the same file. Route to the repair, and let doctor confirm
        # it — the same command that would have reported the defect.
        return (
            NextAction(
                kind="edit",
                path=str(target),
                why=f"{target} exists but does not load: {manifest_defect}",
                expects="doctor loads the manifest without a config error.",
            ),
            "configure",
            SETUP_INCOMPLETE,
            True,
        )
    if scope_actions:
        # `init --write` refused to write anything: the workspace defines agents
        # in several projects, so no single manifest describes it. That is an
        # obligation this run produced and it outranks everything else here —
        # there is no manifest yet for a declaration to be owed on. Rank 1 is
        # deliberately the *choice*, which is a human route; the per-candidate
        # commands ride along as alternatives (#363/#370).
        return (scope_actions[0], "discover", SETUP_INCOMPLETE, True)
    if skipped_target is not None:
        return (
            NextAction(
                kind="edit",
                path=getattr(skipped_target, "path", str(target)),
                why=getattr(skipped_target, "message", None)
                or "This target is in a state init will not overwrite.",
                expects=(
                    "The file is absent or carries the managed block, then "
                    f"re-run init --write --agent-instructions="
                    f"{getattr(skipped_target, 'name', 'default')}."
                ),
            ),
            "configure",
            SETUP_INCOMPLETE,
            True,
        )
    if manifest_status == "skipped_existing" and manifest_exit == 0:
        # The manifest was left alone *on purpose*: `--agent-instructions` makes
        # this the advertised refresh command, and init reports success. Sending
        # the caller to edit a manifest nothing is wrong with would invent an
        # obligation out of a run that had none. The workspace is already
        # configured, so the outstanding step is the gate.
        return (
            NextAction(
                kind="command",
                command=render_command(["verify", "--workspace", str(workspace), "--json"]),
                why=(
                    "The manifest and the requested agent instructions are in "
                    "place. The outstanding step is the release gate."
                ),
                expects="A verifier run that publishes a control identity for this workspace.",
            ),
            "verify",
            SETUP_COMPLETE,
            False,
        )
    if manifest_status == "skipped_existing":
        return (
            NextAction(
                kind="edit",
                path=str(target),
                why=(
                    f"{target} already exists. Edit it directly or remove it "
                    "before re-running init --write."
                ),
                expects=(
                    "The manifest reflects the desired tool sources, agent "
                    "declared_purpose, and policies."
                ),
            ),
            "configure",
            SETUP_INCOMPLETE,
            False,
        )
    if not write:
        # "Nothing was written" was false whenever `--ci` was passed: that flag
        # is orthogonal to `--write`, so the same payload reported
        # `workflow.status="written"` a few fields above this sentence. Name the
        # thing that was actually withheld — the manifest — and account for the
        # file this run did write.
        withheld = (
            "The CI workflow was written. The manifest was not: re-run with --write "
            "to commit the rendered manifest and the rest of the setup this "
            "invocation asked for."
            if workflow_status == "written"
            else "The manifest was not written. Re-run with --write to commit the "
            "rendered manifest and the setup this invocation asked for."
        )
        return (
            NextAction(
                kind="command",
                command=render_command(
                    ["init", "--workspace", str(workspace), "--write", *setup_flags]
                ),
                why=withheld,
                expects=f"{target} exists.",
            ),
            "initialize",
            SETUP_INCOMPLETE,
            False,
        )
    return (next_action_create, "rerun", SETUP_COMPLETE, False)


def register(app: typer.Typer) -> None:
    @app.command(hidden=True)
    def init(
        workspace: Path = typer.Option(Path("."), "--workspace", help="Workspace to inspect."),
        write: bool = typer.Option(
            False,
            "--write",
            help=(
                "Write shipgate.yaml if it does not exist. Also ensures "
                ".gitignore ignores agents-shipgate-reports/ via a managed "
                "block (idempotent; respects an existing line or explicit "
                "!negation; never blocks init)."
            ),
        ),
        json_output: bool = typer.Option(
            False,
            "--json",
            help="Emit a structured summary (path, placeholders, next_action) on stdout.",
        ),
        minimal: bool = typer.Option(
            False,
            "--minimal",
            help="Use the legacy CHANGE_ME-heavy template instead of auto-detection.",
        ),
        max_python_files: int = typer.Option(
            DEFAULT_MAX_PYTHON_FILES,
            "--max-python-files",
            help=(
                "Cap on .py files to AST-parse while auto-detecting. Mirrors "
                "`detect`. A capped parse refuses to write, because the "
                "manifest would declare a tool surface read from part of the "
                "tree; re-run with the value detect reports as "
                "workspace_signals.python_file_total."
            ),
            hidden=True,
        ),
        allow_unresolved_scope: bool = typer.Option(
            False,
            "--allow-unresolved-scope",
            help=(
                "Write one manifest for a workspace whose manifest scope is "
                "unresolved — agents in several self-contained projects, or "
                "discovery capped before it could tell. Without this, --write "
                "refuses and lists the candidate project directories instead "
                "of adopting the first agent name it parsed."
            ),
        ),
        auto: bool = typer.Option(
            False,
            "--auto",
            help="(No-op alias.) Auto-detection is the default in v0.6+.",
            hidden=True,
        ),
        ci: bool = typer.Option(
            False,
            "--ci",
            help=(
                "Also generate .github/workflows/agents-shipgate.yml. Refuses to "
                "overwrite. Skips with a message if another workflow already "
                "calls ThreeMoonsLab/agents-shipgate."
            ),
        ),
        claude_code: bool = typer.Option(
            False,
            "--claude-code",
            help=(
                "One-shot Claude Code setup. Implies "
                "--agent-instructions=claude-md,claude-code-skill (unless "
                "--agent-instructions is passed explicitly), installs the "
                "Claude Code hooks (PostToolUse trigger + Stop verifier via "
                "install-hooks), and adds an `agents-shipgate verify --json` "
                "alias to Makefile / package.json scripts when those files "
                "exist. Dry-run without --write."
            ),
        ),
        agent_instructions: str | None = typer.Option(
            None,
            "--agent-instructions",
            help=(
                "Render or write agent-instruction snippets for the target repo. "
                "Pass --agent-instructions=default for the recommended downstream "
                "kit (AGENTS.md, Cursor rule, Claude command, and local contract), "
                "--agent-instructions=all for every supported target, "
                "--agent-instructions=agents-md,codex-skill for an explicit "
                "subset, or --agent-instructions=none to opt out. "
                "Without --write, snippets are printed to stdout (or returned in "
                "--json). With --write, snippets are written to AGENTS.md, "
                ".agents/skills/agents-shipgate/, "
                ".claude/skills/agents-shipgate/, CLAUDE.md, "
                ".claude/commands/shipgate.md, .cursor/rules/agents-shipgate.mdc, "
                f"{LOCAL_CONTRACT_RELATIVE_PATH}, and the PR template via managed "
                "`<!-- agents-shipgate:start -->` markers (idempotent where host "
                "files are shared, full-file/skill-bundle safe-update checks "
                "elsewhere). Strict CI and baselines remain opt-in human "
                "decisions; generated CI stays advisory by default."
            ),
        ),
        agent_instructions_kit: Path | None = typer.Option(
            None,
            "--agent-instructions-kit",
            help=(
                "Optional repo-local adoption-kit YAML config for file-tree "
                "agent-instruction targets. Relative paths resolve under "
                "--workspace. When omitted, init auto-discovers "
                ".agents-shipgate/adoption-kit.yaml."
            ),
        ),
    ) -> None:
        """Draft a starter shipgate.yaml from a workspace.

        Default (v0.6+): walk the workspace, detect agent framework(s), and
        emit a near-complete manifest. Use --minimal to fall back to the
        pre-v0.6 CHANGE_ME-heavy template.
        """
        require_workspace(workspace)
        workspace_resolved = workspace.resolve()
        target = workspace / "shipgate.yaml"

        # Parse --agent-instructions selector early so invalid input fails before
        # any filesystem mutation. ``None`` = flag absent.
        requested_targets: list[str] | None
        if agent_instructions is None:
            requested_targets = None
        else:
            try:
                requested_targets = parse_selector(agent_instructions)
            except InvalidSelector as exc:
                typer.echo(str(exc), err=True)
                _emit_agent_mode_error(
                    "config_error",
                    message=str(exc),
                    next_action=(
                        "Pass --agent-instructions=default, --agent-instructions=all, "
                        "--agent-instructions=none, or a comma-separated subset."
                    ),
                    next_actions=[
                        NextAction(
                            kind="command",
                            command="agents-shipgate init --agent-instructions=default",
                            why=str(exc),
                            expects=(
                                "Snippets render for the recommended downstream "
                                "agent kit (AGENTS.md, Cursor rule, Claude "
                                "command, and local contract)."
                            ),
                        ).model_dump(mode="json")
                    ],
                )
                raise typer.Exit(2) from exc

        if claude_code and requested_targets is None:
            requested_targets = parse_selector("claude-md,claude-code-skill")

        excluded_sources: list[dict[str, str]] = []
        # Manifest scope, decided before anything is written. `--minimal`
        # never adopts a detected agent name or tool surface, so its output
        # cannot be silently mis-scoped and the refusal does not apply.
        scope_candidates: list[AgentProjectCandidate] = []
        detected_scope = "single"
        # Whether that candidate list is an enumeration or a lower bound, and
        # the uncapped project-root census that bounds it (#395).
        scope_truncated = False
        scope_parse_truncated = False
        scope_project_roots = 0
        scope_python_files = 0
        if minimal:
            template = render_manifest_template(workspace_resolved)
            placeholders = collect_placeholders(template)
            auto_detected: dict[str, object] = {}
            next_action_create = NextAction(
                kind="command",
                command=render_command(
                    ["scan", "-c", _scan_command_config(target.resolve())]
                ),
                why=(
                    "Replace every value listed in placeholders[] in shipgate.yaml, "
                    "then scan the declared tool surface."
                ),
                expects="A readiness report under agents-shipgate-reports/.",
            )
        else:
            try:
                detect_result = detect_workspace(
                    workspace_resolved, max_python_files=max_python_files
                )
            except DiscoveryError as exc:
                message = (
                    "Workspace discovery could not establish bounded coverage: "
                    f"{exc}"
                )
                action = NextAction(
                    kind="review",
                    why=message,
                    expects=(
                        "Reduce the repository inventory or inspect the Git "
                        "failure, then rerun init."
                    ),
                )
                typer.echo(message, err=True)
                _emit_agent_mode_error(
                    "other_error",
                    message=message,
                    next_action=action.to_legacy_string(),
                    next_actions=[action.model_dump(mode="json")],
                )
                raise typer.Exit(4) from exc
            template = render_auto_manifest(workspace_resolved, detect_result)
            # Validation gate: refuse to emit a manifest the schema would reject.
            try:
                _validate_manifest_text(template)
            except Exception as exc:  # noqa: BLE001 - validation surface
                typer.echo(f"Generated manifest failed validation: {exc}", err=True)
                minimal_action = NextAction(
                    kind="command",
                    command="agents-shipgate init --minimal",
                    why=(
                        "Auto-detected manifest failed schema validation. "
                        "Fall back to the legacy CHANGE_ME-heavy template."
                    ),
                    expects=(
                        "shipgate.yaml renders with placeholder fields "
                        "you fill in manually."
                    ),
                )
                _emit_agent_mode_error(
                    "internal_error",
                    message=f"Generated manifest failed validation: {exc}",
                    next_action=minimal_action.to_legacy_string(),
                    next_actions=[minimal_action.model_dump(mode="json")],
                )
                raise typer.Exit(4) from exc
            placeholders = collect_placeholders(template)
            # Same call the renderer makes, not a second copy of the rule, so
            # the JSON summary can never claim a name the YAML left as
            # CHANGE_ME. ``None`` means every candidate failed the quality
            # floor and the manifest carries the placeholder instead.
            selected_agent_name = select_agent_name(detect_result.agent_name_candidates)
            chosen_agent_name = (
                selected_agent_name.value if selected_agent_name is not None else None
            )
            auto_detected = {
                "is_agent_project": detect_result.is_agent_project,
                "frameworks": [
                    {
                        "type": fw.type,
                        "score": fw.score,
                        "confidence": fw.confidence,
                    }
                    for fw in detect_result.frameworks
                ],
                # The actual value the manifest will carry (None when the
                # template falls back to CHANGE_ME).
                "agent_name": chosen_agent_name,
                # Ranked candidate list carrying the evidence behind each
                # rank, so an agent can both override the choice and check
                # why it was made. Without the rationale a reordering
                # regression looks identical to correct behaviour.
                "agent_name_candidates": [
                    c.model_dump(mode="json")
                    for c in detect_result.agent_name_candidates
                ],
                # Which directory this manifest is entitled to describe. On
                # "ambiguous", `chosen_agent_name` above is one of several
                # unrelated agents, so --write refuses (#363).
                "agent_scope": detect_result.agent_scope,
                # Whether `agent_project_candidates` below enumerates the
                # workspace or only the part of it the parse reached (#395),
                # and the workspace signals the truncation claim is measured
                # against. `project_root_count` is the number the refusal
                # message quotes, so a caller that reads the message has to be
                # able to read the number too (#399 review).
                "agent_scope_truncated": detect_result.agent_scope_truncated,
                # The raw completeness fact. A manifest rendered from a capped
                # parse declares a tool surface read from part of the tree, so
                # `--write` refuses on it — and a caller has to be able to read
                # why (#399 review).
                "python_parse_truncated": detect_result.python_parse_truncated,
                "workspace_signals": detect_result.workspace_signals.model_dump(
                    mode="json"
                ),
                "agent_project_candidates": [
                    candidate.model_dump(mode="json")
                    for candidate in detect_result.agent_project_candidates
                ],
            }
            scope_candidates = list(detect_result.agent_project_candidates)
            detected_scope = detect_result.agent_scope
            scope_truncated = detect_result.agent_scope_truncated
            scope_parse_truncated = detect_result.python_parse_truncated
            scope_project_roots = detect_result.workspace_signals.project_root_count
            scope_python_files = detect_result.workspace_signals.python_file_total
            excluded_sources = detect_result.excluded_sources
            if excluded_sources:
                # Glob-matched files the input adapters reject — dropped from
                # tool_sources so the manifest scans, surfaced here so the
                # decision is visible to JSON consumers.
                auto_detected["excluded_sources"] = excluded_sources
            next_action_create = NextAction(
                kind="command",
                command=render_command(
                    [
                        "scan",
                        "-c",
                        _scan_command_config(target.resolve()),
                        "--suggest-patches",
                    ]
                ),
                why=(
                    "Review the auto-detected manifest, then scan the declared "
                    "tool surface with patch suggestions."
                ),
                expects="A readiness report under agents-shipgate-reports/.",
            )

        kit_config = None
        if agent_instructions_kit is not None or requested_targets is not None:
            try:
                kit_config = load_adoption_kit_config(
                    workspace_resolved,
                    agent_instructions_kit,
                )
            except AdoptionKitError as exc:
                path = str(exc.path or agent_instructions_kit or workspace_resolved)
                typer.echo(str(exc), err=True)
                _emit_agent_mode_error(
                    "config_error",
                    path=path,
                    message=str(exc),
                    next_action=f"Edit {path}",
                    next_actions=[
                        NextAction(
                            kind="edit",
                            path=path,
                            why=str(exc),
                            expects=(
                                "Adoption-kit config uses schema_version: 1 "
                                "and each overrides_dir resolves under the workspace."
                            ),
                        ).model_dump(mode="json")
                    ],
                )
                raise typer.Exit(2) from exc

        # Manifest action — orthogonal to --ci. Track outcome instead of
        # exiting immediately so --ci can still run when the manifest exists.
        manifest_status = "not_attempted"
        manifest_exit = 0
        manifest_message: str | None = None
        manifest_skip_pending = False
        # A refused run writes nothing at all — not the workflow, not the
        # agent-instruction snippets, not the reports .gitignore block. The
        # scope it would have used is exactly what is in question, so leaving
        # managed edits behind in a directory Shipgate declined to adopt
        # would put unrelated modifications in the pull request (#363).
        scope_refused = False
        if write:
            if target.exists():
                manifest_status = "skipped_existing"
                manifest_exit = 2
                manifest_message = f"Config already exists: {target}"
                # Defer the agent-mode error emit. When --agent-instructions is
                # set the user's primary intent is refreshing snippets, and an
                # already-existing manifest is informational, not a failure.
                manifest_skip_pending = True
            elif scope_parse_truncated or (
                detected_scope != "single" and not allow_unresolved_scope
            ):
                manifest_status = "refused_unresolved_scope"
                manifest_exit = 2
                manifest_message = _unresolved_scope_message(
                    workspace_resolved,
                    scope_candidates,
                    scope=detected_scope,
                    truncated=scope_truncated,
                    parse_truncated=scope_parse_truncated,
                    project_roots=scope_project_roots,
                    python_file_total=scope_python_files,
                )
                scope_refused = True
            else:
                target.write_text(template, encoding="utf-8")
                manifest_status = "written"
                manifest_message = f"Wrote {target}"

        # Workflow action — independent of manifest action.
        workflow_outcome: dict[str, object] | None = None
        workflow_requested = ci and not scope_refused
        if workflow_requested:
            # GitHub loads workflows from the repository root only, so a
            # scoped adoption still wires CI there — with a config path
            # relative to that root (#363).
            result = write_ci_workflow(
                workspace_resolved,
                repository_root=repository_root(workspace_resolved),
            )
            workflow_outcome = {
                "status": result.status,
                "path": result.path,
                "message": result.message,
            }
            if result.cross_reference_path is not None:
                workflow_outcome["cross_reference_path"] = result.cross_reference_path

        # Agent-instructions action — independent of manifest and workflow actions.
        agent_instructions_outcome: dict[str, object] | None = None
        agent_instructions_exit = 0
        agent_instructions_targets: list[object] = []
        if requested_targets is not None and not scope_refused:
            ai_result = apply_agent_instructions(
                workspace_resolved,
                requested_targets,
                write=write,
                kit_config=kit_config,
            )
            agent_instructions_outcome = ai_result.to_json()
            agent_instructions_exit = ai_result.exit_code
            agent_instructions_targets = list(ai_result.targets)
            local_contract_target = next(
                (t for t in agent_instructions_targets if t.name == "local-contract"),
                None,
            )
        else:
            local_contract_target = None

        # Gitignore action — runs unconditionally on --write so the reports
        # directory created by the first `scan` is never silently committed.
        # Idempotent: subsequent runs see the managed block and report
        # `unchanged`. Never blocks `init` (exit_contribution is 0) — the
        # outcome is advisory, surfaced in --json and as a one-line message.
        # Also runs when the manifest already exists so repos that adopted
        # Shipgate before this CLI version was released get the line on their
        # next `init --write`.
        gitignore_outcome = (
            ensure_reports_gitignore(workspace_resolved, write=write)
            if write and not scope_refused
            else None
        )

        # Claude Code extras — hooks plus a conventional verify alias.
        # Best-effort and advisory: failures are reported in the outcome,
        # never as an init exit code (the instructions/manifest actions
        # above carry the contract).
        claude_code_outcome: dict[str, object] | None = None
        if claude_code and not scope_refused:
            claude_code_outcome = _apply_claude_code_extras(workspace_resolved, write=write)

        # Idempotency reconciliation: when --agent-instructions selects at least
        # one real target AND the manifest already exists, treat the manifest
        # action as already-done so `init --write --agent-instructions=<target>`
        # is safe to rerun (the advertised refresh command). The manifest_status
        # field still reports "skipped_existing" so callers can detect.
        #
        # `=none` parses to an empty list — no instruction action runs, so this
        # accommodation does NOT apply and manifest skip remains exit 2 (matches
        # plain `init --write`).
        if requested_targets and manifest_status == "skipped_existing":
            manifest_exit = 0
            manifest_skip_pending = False
        scope_actions: list[NextAction] = []
        if scope_refused:
            scope_actions = _unresolved_scope_actions(
                workspace_resolved,
                scope_candidates,
                scope=detected_scope,
                setup_flags=_requested_setup_flags(
                    ci=ci,
                    claude_code=claude_code,
                    agent_instructions=agent_instructions,
                ),
                kit=agent_instructions_kit,
                truncated=scope_truncated,
                parse_truncated=scope_parse_truncated,
                python_file_total=scope_python_files,
                # The same setup this run asked for, so the retry completes it
                # rather than silently dropping --ci or an instruction target.
                setup_command=[
                    "init",
                    "--workspace",
                    str(workspace_resolved),
                    "--write",
                    *_requested_setup_flags(
                        ci=ci,
                        claude_code=claude_code,
                        agent_instructions=agent_instructions,
                    ),
                ],
                # With an instruction target selected, `init --write` leaves an
                # existing manifest alone and still exits 0 — the advertised
                # refresh command — so an adopted candidate keeps its `init`
                # route rather than being handed to `doctor` (#397 review).
                refreshes_existing=bool(requested_targets),
            )

        # Routing. Computed from the manifest that is *on disk*, not from the
        # template: on `skipped_existing` the template was never written, so its
        # placeholders describe a file that does not exist while the real
        # manifest — which may still hold an unresolved human-owned declaration
        # — goes uninspected. Dropping them there turned
        # `init --write --agent-instructions=...` into a route around the human
        # ownership boundary: the same unedited manifest reported
        # `setup_complete -> verify`.
        control_placeholders, control_manifest_bytes, manifest_defect = _manifest_placeholders(
            target, template=template, placeholders=placeholders, write=write
        )
        advance, advance_kind, advance_decision, advance_blocking = _init_advance(
            workspace=workspace,
            target=target,
            write=write,
            manifest_status=manifest_status,
            manifest_exit=manifest_exit,
            next_action_create=next_action_create,
            skipped_target=next(
                (t for t in agent_instructions_targets if t.status.startswith("skipped")),
                None,
            )
            if agent_instructions_exit
            else None,
            scope_actions=scope_actions,
            manifest_defect=manifest_defect,
            # Everything this invocation asked for, so the follow-up is
            # equivalent to the dry run it advances. `_requested_setup_flags`
            # covers what the *scoped refusal* must repeat; the dry run re-runs
            # this same workspace, so it owes more:
            #
            #   --minimal                 selects a different template, so
            #                             dropping it writes something other
            #                             than what was previewed;
            #   --allow-unresolved-scope  the accepted root boundary, without
            #                             which the command exits 2 in the very
            #                             monorepo that needed it;
            #   --agent-instructions-kit  the kit that was previewed;
            #   --json                    the caller is in the JSON control
            #                             loop and gets human prose back.
            #
            # The scoped refusal deliberately repeats none of the last three:
            # there the point is to choose a *different* workspace.
            setup_flags=[
                *(["--minimal"] if minimal else []),
                *_requested_setup_flags(
                    ci=ci,
                    claude_code=claude_code,
                    agent_instructions=agent_instructions,
                ),
                *(["--allow-unresolved-scope"] if allow_unresolved_scope else []),
                *(
                    ["--agent-instructions-kit", str(agent_instructions_kit)]
                    if agent_instructions_kit is not None
                    else []
                ),
                *(["--json"] if json_output else []),
            ],
            # `--ci` writes without `--write`, so the dry-run route has to know
            # whether this run already produced a file.
            workflow_status=(
                str(workflow_outcome["status"]) if workflow_outcome is not None else None
            ),
        )
        routing = setup_control_envelope(
            operation="init",
            input_id=setup_input_id(
                operation="init",
                workspace=workspace_resolved,
                manifest_path=target if control_manifest_bytes is not None else None,
                manifest_bytes=control_manifest_bytes,
                routing_facts=(
                    manifest_status,
                    manifest_exit,
                    agent_instructions_exit,
                    control_placeholders,
                    manifest_defect,
                    advance_decision,
                    # The selected route itself. On one unchanged empty
                    # workspace a plain dry run, `--ci`, and
                    # `--agent-instructions=agents-md` produced three different
                    # commands under one identity — so a cache keyed by the
                    # documented identity could reuse a different requested
                    # setup. Hashing the action covers every flag that can
                    # reach it, including ones added later.
                    advance.model_dump(mode="json") if advance is not None else None,
                    # The #370 scope facts select this route whenever the
                    # workspace holds more than one project; without them the
                    # candidate list could change while the identity of the
                    # answer about it did not.
                    detected_scope,
                    scope_truncated,
                    [candidate.model_dump(mode="json") for candidate in scope_candidates],
                    [action.model_dump(mode="json") for action in scope_actions],
                ),
            ),
            reason=_init_reason(manifest_status, target=target, write=write),
            advance=advance,
            advance_kind=advance_kind,
            advance_decision=advance_decision,
            advance_blocking=advance_blocking,
            advance_alternatives=scope_actions[1:],
            recheck_command=render_command(
                ["doctor", "--config", str(target.resolve()), "--json"]
            ),
            placeholders=control_placeholders,
            manifest_display_path=str(target),
            exit_code=max(manifest_exit, agent_instructions_exit) or None,
        )
        if scope_refused:
            _emit_agent_mode_error(
                "config_error",
                path=str(target),
                message=manifest_message,
                exit_code=manifest_exit,
                # The one selected route, as everywhere else on this command.
                # Composing an independent list here would put a different
                # rank-1 on the error stream than the payload carries.
                next_action=routing.legacy_next_action,
                next_actions=routing.json_actions(),
                control=routing.envelope.model_dump(mode="json"),
                agent_scope=detected_scope,
                agent_scope_truncated=scope_truncated,
                project_root_count=scope_project_roots,
                agent_project_candidates=[
                    candidate.model_dump(mode="json") for candidate in scope_candidates
                ],
            )
        if manifest_skip_pending:
            # The same selected route the stdout payload carries. Composing an
            # independent one here reproduced, on the error stream, exactly the
            # split the stdout fields were just unified to remove: stdout said
            # `human_review_required` with no command while stderr handed the
            # agent `Edit shipgate.yaml` for a declaration only a person may make.
            _emit_agent_mode_error(
                "config_already_exists",
                path=str(target),
                next_action=routing.legacy_next_action,
                next_actions=routing.json_actions(),
                control=routing.envelope.model_dump(mode="json"),
            )


        # Output
        if json_output:
            payload: dict[str, object] = {
                "path": str(target),
                "created": manifest_status == "written",
                "manifest_status": manifest_status,
                # The placeholders of the manifest at `path`, which is what the
                # control route was selected from and what its "and N more in
                # placeholders[]" refers to. On `skipped_existing` this used to
                # be the *template's* list — locations in a file that was never
                # written — so a caller resolving them edited the wrong lines.
                # For the common `written` case the two are identical.
                "placeholders": control_placeholders if write or target.exists() else placeholders,
            }
            if manifest_message:
                payload["manifest_message"] = manifest_message
            if not write:
                payload["template"] = template
            if auto_detected:
                payload["auto_detected"] = auto_detected
            if workflow_outcome is not None:
                payload["workflow"] = workflow_outcome
            if agent_instructions_outcome is not None:
                payload["agent_instructions"] = agent_instructions_outcome
            if local_contract_target is not None:
                payload["local_contract"] = local_contract_target.to_json()
            if gitignore_outcome is not None:
                payload["gitignore"] = gitignore_outcome.to_json()
            if claude_code_outcome is not None:
                payload["claude_code"] = claude_code_outcome
            payload["next_action"] = routing.legacy_next_action
            payload["next_actions"] = routing.json_actions()
            payload["control"] = routing.envelope.model_dump(mode="json")
            typer.echo(json.dumps(payload, indent=2))
        else:
            if not write:
                if requested_targets is not None:
                    # Manifest + each requested target, separated by section headers
                    # so the output is unambiguous.
                    typer.echo("--- shipgate.yaml ---")
                    typer.echo(template)
                    for outcome in agent_instructions_targets:
                        relative = _AI_SPECS[outcome.name].relative_path
                        typer.echo("")
                        typer.echo(f"--- {relative} ---")
                        typer.echo(outcome.rendered or "")
                else:
                    typer.echo(template)
            else:
                if manifest_status == "written":
                    typer.echo(manifest_message)
                    if excluded_sources:
                        typer.echo(
                            f"Excluded {len(excluded_sources)} detected file(s) "
                            "scan cannot parse as tool sources; see the comments "
                            "in shipgate.yaml."
                        )
                    if placeholders:
                        typer.echo(
                            f"Replace these placeholders before scanning: "
                            f"{', '.join(sorted({entry['path'] for entry in placeholders}))}"
                        )
                elif manifest_status in ("skipped_existing", "refused_unresolved_scope"):
                    typer.echo(manifest_message, err=True)
            if workflow_outcome is not None:
                stream = (
                    sys.stderr if workflow_outcome["status"].startswith("skipped") else sys.stdout
                )
                print(workflow_outcome["message"], file=stream)
            if write and agent_instructions_targets:
                for outcome in agent_instructions_targets:
                    stream = sys.stderr if outcome.status.startswith("skipped") else sys.stdout
                    if outcome.message:
                        print(outcome.message, file=stream)
            if gitignore_outcome is not None:
                # Quiet the no-op cases (already_present + unchanged) to keep
                # the success path scannable. Everything else — created,
                # updated, migrated, skipped_*, error — gets a line so an
                # adopter sees what changed (or why nothing did).
                noisy_status = {
                    GitignoreOutcomeStatus.ALREADY_PRESENT,
                    GitignoreOutcomeStatus.UNCHANGED,
                }
                if gitignore_outcome.status not in noisy_status:
                    stream = (
                        sys.stderr
                        if gitignore_outcome.status.value.startswith("skipped")
                        or gitignore_outcome.status is GitignoreOutcomeStatus.ERROR
                        else sys.stdout
                    )
                    print(gitignore_outcome.message, file=stream)
            if claude_code_outcome is not None:
                for line in _claude_code_outcome_lines(claude_code_outcome):
                    typer.echo(line)

        # Surface a structured next_action JSON line for the rank-1 skipped target
        # so coding-agent callers can route to a fix without scraping stdout. Gated
        # on AGENTS_SHIPGATE_AGENT_MODE=1 by `_emit_agent_mode_error` itself.
        if agent_instructions_exit:
            first_skip = next(
                (t for t in agent_instructions_targets if t.status.startswith("skipped")),
                None,
            )
            if first_skip is not None:
                # `_init_advance` already routed on this skipped target, so the
                # envelope's rank-1 action *is* this obligation — unless an
                # unresolved human-owned declaration outranks it, in which case
                # that is the honest answer here too.
                _emit_agent_mode_error(
                    "config_already_exists",
                    path=first_skip.path,
                    message=first_skip.message,
                    next_action=routing.legacy_next_action,
                    next_actions=routing.json_actions(),
                    control=routing.envelope.model_dump(mode="json"),
                )

        final_exit = max(manifest_exit, agent_instructions_exit)
        if final_exit:
            raise typer.Exit(final_exit)
