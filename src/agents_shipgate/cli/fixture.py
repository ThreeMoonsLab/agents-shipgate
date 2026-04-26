"""``agents-shipgate fixture`` subcommand: list, run, copy, and verify the
bundled fixtures so an agent can validate install + report shape with one
command, without authoring a manifest.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import typer

from agents_shipgate.cli.scan import run_scan
from agents_shipgate.core.errors import AgentsShipgateError, ConfigError, InputParseError
from agents_shipgate.fixtures import (
    FixtureNotFoundError,
    FixturesUnavailableError,
    fixture_path,
    list_fixtures,
)

fixture_app = typer.Typer(
    help="Run, copy, list, or verify bundled sample fixtures.",
    no_args_is_help=True,
)


@fixture_app.command("list")
def fixture_list(
    json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of text."),
) -> None:
    """List the bundled fixtures."""
    try:
        fixtures = list_fixtures()
    except FixturesUnavailableError as exc:
        typer.echo(f"Fixtures unavailable: {exc}", err=True)
        raise typer.Exit(4) from exc

    if json_output:
        typer.echo(json.dumps(fixtures, indent=2))
        return

    if not fixtures:
        typer.echo("No bundled fixtures available.")
        return
    for fixture in fixtures:
        line = f"{fixture['name']}"
        if fixture.get("description"):
            line += f"\t{fixture['description']}"
        typer.echo(line)


@fixture_app.command("run")
def fixture_run(
    name: str = typer.Argument(..., help="Fixture name; see `fixture list`."),
    out: Path | None = typer.Option(
        None,
        "--out",
        help="Output directory for the report. Defaults to a temp location next to the fixture copy.",
    ),
    ci_mode: str | None = typer.Option(
        None,
        "--ci-mode",
        help="advisory or strict; defaults to advisory for fixture runs.",
    ),
    keep: bool = typer.Option(
        False,
        "--keep",
        help="Keep the fixture copy in a tempdir after the run (otherwise discard).",
    ),
) -> None:
    """Copy a fixture to a tempdir and scan it."""
    src = _resolve_fixture(name)

    import tempfile

    workdir = Path(tempfile.mkdtemp(prefix=f"shipgate-fixture-{name}-"))
    target = workdir / name
    shutil.copytree(src, target)

    out_dir = out or (target / "reports")

    try:
        report, exit_code = run_scan(
            config_path=target / "shipgate.yaml",
            output_dir=out_dir,
            formats=["markdown", "json"],
            ci_mode=ci_mode or "advisory",
        )
    except (ConfigError, InputParseError, AgentsShipgateError) as exc:
        typer.echo(f"Fixture {name!r} scan failed: {exc}", err=True)
        raise typer.Exit(4) from exc

    typer.echo(f"Fixture: {name}")
    typer.echo(f"Status:  {report.summary.status}")
    typer.echo(
        f"Counts:  critical={report.summary.critical_count} "
        f"high={report.summary.high_count} medium={report.summary.medium_count}"
    )
    typer.echo(f"Reports: {out_dir}")
    if not keep:
        typer.echo(f"Fixture copy at {target}; pass --keep to retain after the run.")
    raise typer.Exit(exit_code)


@fixture_app.command("copy")
def fixture_copy(
    name: str = typer.Argument(..., help="Fixture name."),
    to: Path = typer.Option(..., "--to", help="Destination directory (created if missing)."),
) -> None:
    """Copy a fixture into a user-provided directory.

    The destination is always ``<to>/<fixture-name>``; ``<to>`` is created if
    it does not exist. The fixture is copied as a self-contained subdirectory
    so multiple fixtures can be staged side-by-side.
    """
    src = _resolve_fixture(name)

    to.mkdir(parents=True, exist_ok=True)
    target = to / name
    if target.exists():
        typer.echo(f"Destination already exists: {target}", err=True)
        raise typer.Exit(2)

    shutil.copytree(src, target)
    typer.echo(f"Copied fixture {name!r} to {target}")


@fixture_app.command("verify")
def fixture_verify(
    name: str = typer.Argument(..., help="Fixture name."),
) -> None:
    """Scan a fixture and (when ``expected/`` is present) confirm the JSON
    summary matches the golden snapshot."""
    src = _resolve_fixture(name)

    import tempfile

    workdir = Path(tempfile.mkdtemp(prefix=f"shipgate-fixture-verify-{name}-"))
    target = workdir / name
    shutil.copytree(src, target)
    out_dir = target / "reports"

    try:
        report, _ = run_scan(
            config_path=target / "shipgate.yaml",
            output_dir=out_dir,
            formats=["json"],
            ci_mode="advisory",
        )
    except (ConfigError, InputParseError, AgentsShipgateError) as exc:
        typer.echo(f"Fixture {name!r} scan failed: {exc}", err=True)
        raise typer.Exit(4) from exc

    expected_dir = src / "expected"
    if not expected_dir.is_dir():
        typer.echo(
            f"Fixture {name!r} has no expected/ directory; "
            "verification skipped (scan succeeded).",
        )
        raise typer.Exit(0)

    summary = {
        "status": report.summary.status,
        "critical_count": report.summary.critical_count,
        "high_count": report.summary.high_count,
        "medium_count": report.summary.medium_count,
    }
    expected_summary_file = expected_dir / "summary.json"
    if expected_summary_file.is_file():
        expected = json.loads(expected_summary_file.read_text(encoding="utf-8"))
        if summary == expected:
            typer.echo(f"Fixture {name!r}: summary matches expected/summary.json")
            raise typer.Exit(0)
        typer.echo("Fixture summary diverged from expected:", err=True)
        typer.echo(f"  expected: {expected}", err=True)
        typer.echo(f"  actual:   {summary}", err=True)
        raise typer.Exit(20)

    typer.echo(
        f"Fixture {name!r}: no expected/summary.json; "
        f"actual summary = {json.dumps(summary)}",
    )


def _resolve_fixture(name: str) -> Path:
    try:
        return fixture_path(name)
    except FixturesUnavailableError as exc:
        typer.echo(f"Fixtures unavailable: {exc}", err=True)
        raise typer.Exit(4) from exc
    except FixtureNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
