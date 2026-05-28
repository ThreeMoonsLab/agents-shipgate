"""``agents-shipgate trigger`` — evaluate the published trigger catalog.

Promotes the :mod:`agents_shipgate.triggers` evaluator to a first-class
subcommand. It decides whether Shipgate should *run* on a diff; it does
not decide safety (that is ``scan`` / ``verify`` and
``release_decision.decision``).

Inputs, in precedence order:

1. ``--base``/``--head`` — shell out to ``git diff`` for changed paths
   and the unified-diff body. This is the ONLY mode that runs git.
2. ``--changed-files PATH`` / ``--diff PATH`` — read changed paths
   (newline-separated) and a unified-diff body from local files. No
   git, no network.

``--list-rules [--json]`` prints the catalog and exits. The developer
entry point ``python -m agents_shipgate.triggers`` is preserved.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import typer

from agents_shipgate.triggers import (
    _git_diff_context,
    evaluate,
    load_triggers,
)


def trigger(
    workspace: Path = typer.Option(
        Path("."),
        "--workspace",
        help=(
            "Workspace root. Used to detect whether shipgate.yaml is "
            "present (the opt-in that flips force_run) and as the git "
            "working directory for --base/--head."
        ),
    ),
    changed_files: Path | None = typer.Option(
        None,
        "--changed-files",
        help=(
            "Path to a file of newline-separated changed paths "
            "(repo-relative, forward slashes). Ignored when "
            "--base/--head is passed."
        ),
    ),
    diff: Path | None = typer.Option(
        None,
        "--diff",
        help=(
            "Path to a file containing the unified-diff body. Used for "
            "diff_contains predicates (e.g. @function_tool). Ignored "
            "when --base/--head is passed."
        ),
    ),
    base: str | None = typer.Option(
        None,
        "--base",
        help=(
            "Git base ref for a PR-style diff (e.g. origin/main). "
            "Passing --base or --head is the only thing that runs git."
        ),
    ),
    head: str | None = typer.Option(
        None,
        "--head",
        help="Git head ref (defaults to HEAD when --base is given).",
    ),
    manifest_present: bool | None = typer.Option(
        None,
        "--manifest-present/--no-manifest-present",
        help=(
            "Override shipgate.yaml detection. When unset, presence is "
            "auto-detected under --workspace."
        ),
    ),
    user_requested: bool = typer.Option(
        False,
        "--user-requested",
        help="The user explicitly asked for a Shipgate run (suppresses stop_conditions).",
    ),
    list_rules: bool = typer.Option(
        False,
        "--list-rules",
        help="Print the loaded rule catalog and exit.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit JSON output. Default: human-readable summary.",
    ),
) -> None:
    """Decide whether Shipgate should run on a diff (run/skip verdict)."""
    triggers = load_triggers()

    if list_rules:
        if json_output:
            typer.echo(json.dumps(triggers, indent=2))
        else:
            for rule in triggers.get("rules", []):
                typer.echo(
                    f"{rule['id']}\t{rule['action']}\t{rule.get('rationale', '')}"
                )
        return

    use_git = base is not None or head is not None
    if use_git:
        head_ref = head or "HEAD"
        revspec = f"{base}...{head_ref}" if base else head_ref
        try:
            paths, diff_text = _git_diff_context(
                revspec, cwd=workspace.resolve()
            )
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            typer.echo(
                f"--base/--head git diff failed: {exc}. Run inside a git "
                "checkout, or pass --changed-files / --diff instead.",
                err=True,
            )
            raise typer.Exit(2) from exc
    else:
        paths = _read_lines(changed_files)
        diff_text = diff.read_text(encoding="utf-8") if diff is not None else ""

    if manifest_present is None:
        manifest_present_resolved = (workspace / "shipgate.yaml").is_file()
    else:
        manifest_present_resolved = manifest_present

    result = evaluate(
        paths=paths,
        diff_text=diff_text,
        manifest_present=manifest_present_resolved,
        user_requested=user_requested,
        triggers=triggers,
    )

    if json_output:
        typer.echo(json.dumps(result, indent=2))
        return

    verdict = "RUN" if result["should_run"] else "SKIP"
    typer.echo(f"Verdict: {verdict}")
    typer.echo(f"Rationale: {result['rationale']}")
    if result["dry_run_recommended"]:
        typer.echo("Dry-run recommended (advisory scan, no manifest write).")
    if result["matched_rules"]:
        typer.echo("Matched rules:")
        for match in result["matched_rules"]:
            command = f" → {match['command']}" if match.get("command") else ""
            typer.echo(f"  - {match['id']} [{match['action']}]{command}")
    if result["diff_tokens"]:
        typer.echo(f"Diff tokens: {', '.join(result['diff_tokens'])}")
    if result["stop_conditions_fired"]:
        typer.echo("Stop conditions fired (overriding any matched rules).")


def _read_lines(path: Path | None) -> list[str]:
    if path is None:
        return []
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
