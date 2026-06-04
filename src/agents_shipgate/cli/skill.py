from __future__ import annotations

from pathlib import Path

import typer

from agents_shipgate import __version__
from agents_shipgate.cli._helpers import _parse_fail_on, _parse_formats
from agents_shipgate.core.errors import AgentsShipgateError, ConfigError, InputParseError
from agents_shipgate.core.logging import configure_logging
from agents_shipgate.skill.models import SkillCommand
from agents_shipgate.skill.runner import run_skill_review

skill_app = typer.Typer(
    help="Lint and security-review agent skill and instruction artifacts.",
    no_args_is_help=True,
)


@skill_app.command("lint")
def lint(
    paths: list[Path] = typer.Argument(None, help="Optional files or directories to scan."),
    config: Path = typer.Option(
        Path(".shipgate/skill-review.yml"),
        "--config",
        "-c",
        help="Optional skill review config path.",
    ),
    out: Path | None = typer.Option(None, "--out", help="Output directory for reports."),
    formats: str = typer.Option(
        "markdown,json",
        "--format",
        help="Comma-separated report formats: markdown,json,sarif.",
    ),
    ci_mode: str = typer.Option("advisory", "--ci-mode", help="advisory or strict."),
    fail_on: str | None = typer.Option(
        None,
        "--fail-on",
        help="Comma-separated severities that fail CI.",
    ),
    changed_files: Path | None = typer.Option(
        None,
        "--changed-files",
        help="Path to newline-separated changed repo-relative files.",
    ),
    verbose: bool = typer.Option(False, "--verbose", help="Show debug output."),
) -> None:
    _run(
        "lint",
        paths=paths,
        config=config,
        out=out,
        formats=formats,
        ci_mode=ci_mode,
        fail_on=fail_on,
        changed_files=changed_files,
        verbose=verbose,
    )


@skill_app.command("security")
def security(
    paths: list[Path] = typer.Argument(None, help="Optional files or directories to scan."),
    config: Path = typer.Option(
        Path(".shipgate/skill-review.yml"),
        "--config",
        "-c",
        help="Optional skill review config path.",
    ),
    out: Path | None = typer.Option(None, "--out", help="Output directory for reports."),
    formats: str = typer.Option(
        "markdown,json",
        "--format",
        help="Comma-separated report formats: markdown,json,sarif.",
    ),
    ci_mode: str = typer.Option("advisory", "--ci-mode", help="advisory or strict."),
    fail_on: str | None = typer.Option(
        None,
        "--fail-on",
        help="Comma-separated severities that fail CI.",
    ),
    changed_files: Path | None = typer.Option(
        None,
        "--changed-files",
        help="Path to newline-separated changed repo-relative files.",
    ),
    verbose: bool = typer.Option(False, "--verbose", help="Show debug output."),
) -> None:
    _run(
        "security",
        paths=paths,
        config=config,
        out=out,
        formats=formats,
        ci_mode=ci_mode,
        fail_on=fail_on,
        changed_files=changed_files,
        verbose=verbose,
    )


@skill_app.command("review")
def review(
    paths: list[Path] = typer.Argument(None, help="Optional files or directories to scan."),
    config: Path = typer.Option(
        Path(".shipgate/skill-review.yml"),
        "--config",
        "-c",
        help="Optional skill review config path.",
    ),
    out: Path | None = typer.Option(None, "--out", help="Output directory for reports."),
    formats: str = typer.Option(
        "markdown,json",
        "--format",
        help="Comma-separated report formats: markdown,json,sarif.",
    ),
    ci_mode: str = typer.Option("advisory", "--ci-mode", help="advisory or strict."),
    fail_on: str | None = typer.Option(
        None,
        "--fail-on",
        help="Comma-separated severities that fail CI.",
    ),
    changed_files: Path | None = typer.Option(
        None,
        "--changed-files",
        help="Path to newline-separated changed repo-relative files.",
    ),
    verbose: bool = typer.Option(False, "--verbose", help="Show debug output."),
) -> None:
    _run(
        "review",
        paths=paths,
        config=config,
        out=out,
        formats=formats,
        ci_mode=ci_mode,
        fail_on=fail_on,
        changed_files=changed_files,
        verbose=verbose,
    )


def _run(
    command: SkillCommand,
    *,
    paths: list[Path] | None,
    config: Path,
    out: Path | None,
    formats: str,
    ci_mode: str,
    fail_on: str | None,
    changed_files: Path | None,
    verbose: bool,
) -> None:
    try:
        configure_logging(verbose=verbose)
        parsed_formats = _parse_formats(formats)
        parsed_fail_on = _parse_fail_on(fail_on)
        report, exit_code = run_skill_review(
            command=command,
            paths=list(paths or []),
            config_path=config,
            formats=parsed_formats,
            output_dir=out,
            ci_mode=ci_mode,
            fail_on=parsed_fail_on,
            changed_files=changed_files,
        )
    except ConfigError as exc:
        typer.echo(f"Config error: {exc}", err=True)
        raise typer.Exit(2) from exc
    except InputParseError as exc:
        typer.echo(f"Input parsing error: {exc}", err=True)
        raise typer.Exit(3) from exc
    except AgentsShipgateError as exc:
        typer.echo(f"Agents Shipgate error: {exc}", err=True)
        raise typer.Exit(4) from exc
    except Exception as exc:  # noqa: BLE001 - CLI should map unexpected failures.
        typer.echo(f"Internal error: {exc}", err=True)
        raise typer.Exit(4) from exc

    typer.echo(f"Agents Shipgate Skill {command.title()} {__version__}")
    typer.echo(f"Verdict: {report.summary.verdict}")
    typer.echo(f"Artifacts: {report.summary.artifact_count}")
    typer.echo(f"Findings: {report.summary.finding_count}")
    if report.generated_reports:
        for name, path in sorted(report.generated_reports.items()):
            typer.echo(f"{name}: {path}")
    typer.echo(f"Exit code: {exit_code}")
    raise typer.Exit(exit_code)
