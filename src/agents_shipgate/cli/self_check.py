"""``agents-shipgate self-check`` command: run a small set of bundled
fixtures and report whether the install is in working order. Designed to be
the first thing an agent runs in a fresh environment.
"""

from __future__ import annotations

import json
import platform
import shutil
import sys
import tempfile
from pathlib import Path

import typer

from agents_shipgate import __version__
from agents_shipgate.cli.scan import run_scan
from agents_shipgate.core.errors import AgentsShipgateError, ConfigError, InputParseError
from agents_shipgate.fixtures import (
    FixturesUnavailableError,
    fixtures_root,
    list_fixtures,
)

# Fixtures that should always run during self-check. Picked to exercise
# different input loaders (MCP/OpenAPI/SDK/ADK) when present.
_DEFAULT_FIXTURES = (
    "clean_read_only_agent",
    "support_refund_agent",
    "google_adk_agent",
)


def self_check(
    json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of text."),
) -> None:
    """Verify install + bundled fixtures + CLI surface."""
    payload: dict[str, object] = {
        "version": __version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "fixtures_available": False,
        "fixtures_run": {},
        "cli_surface": {},
        "ready": False,
    }

    try:
        root = fixtures_root()
        payload["fixtures_root"] = str(root)
        payload["fixtures_available"] = True
        available = {fixture["name"] for fixture in list_fixtures()}
    except FixturesUnavailableError as exc:
        payload["fixtures_root_error"] = str(exc)
        available = set()

    fixture_results: dict[str, str] = {}
    for name in _DEFAULT_FIXTURES:
        if name not in available:
            fixture_results[name] = "skipped (not bundled)"
            continue
        fixture_results[name] = _run_fixture(name)
    payload["fixtures_run"] = fixture_results

    payload["cli_surface"] = _probe_cli_surface()

    payload["ready"] = (
        payload["fixtures_available"]
        and all(value == "ok" for value in fixture_results.values() if "skipped" not in value)
        and all(value == "ok" for value in payload["cli_surface"].values())
    )

    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if not payload["ready"]:
            raise typer.Exit(4)
        return

    typer.echo(f"Agents Shipgate {payload['version']} on Python {payload['python']}")
    typer.echo(f"Platform: {payload['platform']}")
    typer.echo("")
    typer.echo("Bundled fixtures:")
    for name, result in fixture_results.items():
        typer.echo(f"  {name:30} {result}")
    typer.echo("")
    typer.echo("CLI surface:")
    for command, status in payload["cli_surface"].items():
        typer.echo(f"  {command:25} {status}")
    typer.echo("")
    typer.echo("Ready: " + ("yes" if payload["ready"] else "no"))
    if not payload["ready"]:
        raise typer.Exit(4)


def _run_fixture(name: str) -> str:
    from agents_shipgate.fixtures import fixture_path

    try:
        src = fixture_path(name)
    except Exception as exc:  # noqa: BLE001 - reporting boundary
        return f"error: {type(exc).__name__}"

    workdir = Path(tempfile.mkdtemp(prefix=f"shipgate-selfcheck-{name}-"))
    target = workdir / name
    try:
        shutil.copytree(src, target)
        run_scan(
            config_path=target / "shipgate.yaml",
            output_dir=target / "reports",
            formats=["json"],
            ci_mode="advisory",
        )
        return "ok"
    except (ConfigError, InputParseError, AgentsShipgateError) as exc:
        return f"error: {type(exc).__name__}: {exc}"
    except Exception as exc:  # noqa: BLE001 - reporting boundary
        return f"error: {type(exc).__name__}: {exc}"
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _probe_cli_surface() -> dict[str, str]:
    """Confirm key CLI entry-point modules import cleanly."""
    results: dict[str, str] = {}
    probes = {
        "scan": "agents_shipgate.cli.scan",
        "init": "agents_shipgate.cli.discovery",
        "doctor": "agents_shipgate.cli.scan",
        "explain": "agents_shipgate.checks.registry",
        "list-checks": "agents_shipgate.checks.registry",
        "baseline.save": "agents_shipgate.core.baseline",
        "fixture": "agents_shipgate.cli.fixture",
        "self-check": "agents_shipgate.cli.self_check",
    }
    for command, module_name in probes.items():
        try:
            __import__(module_name)
            results[command] = "ok"
        except Exception as exc:  # noqa: BLE001 - reporting boundary
            results[command] = f"error: {type(exc).__name__}: {exc}"
    return results


if __name__ == "__main__":
    self_check(json_output="--json" in sys.argv)
