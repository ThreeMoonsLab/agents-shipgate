"""The root CLI imports a command only when it is resolved (#661).

Importing every command module cost 0.67 s before any command ran, against
0.19 s for `diff` alone, and the Stop hook runs `diff` once per change with a
1.5 s budget. Help, `--help-all`, completion and typo suggestions still see
every command.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import typer
from typer.main import get_command
from typer.testing import CliRunner

from agents_shipgate.cli.main import app

SRC = Path(__file__).resolve().parents[1] / "src"
UNRELATED = (
    "agents_shipgate.cli._helpers",
    "agents_shipgate.cli._register_baseline",
    "agents_shipgate.cli._register_scan",
    "agents_shipgate.cli.scan.orchestrator",
    "agents_shipgate.cli.verify.command",
    "agents_shipgate.cli.verify.orchestrator",
    "agents_shipgate.checks.registry",
    "agents_shipgate.ci.release_decision",
)


def _modules_after(code: str) -> set[str]:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(SRC), os.environ.get("PYTHONPATH")]))}
    completed = subprocess.run(
        [sys.executable, "-c", f"{code}\nimport json, sys\nprint(json.dumps(sorted(sys.modules)))"],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    return set(json.loads(completed.stdout.splitlines()[-1]))


def test_importing_the_cli_imports_no_command() -> None:
    modules = _modules_after("import agents_shipgate.cli.main")

    commands = sorted(
        name
        for name in modules
        if name.startswith("agents_shipgate.cli.") and name != "agents_shipgate.cli.main"
    )
    assert commands == []


def test_running_diff_imports_nothing_unrelated(tmp_path: Path) -> None:
    """The Stop hook's route: the whole `diff` run, not just its resolution."""

    modules = _modules_after(
        "import subprocess, sys\n"
        f"workspace = {str(tmp_path)!r}\n"
        "subprocess.run(['git', 'init', '-q', workspace], check=True)\n"
        "subprocess.run(['git', '-C', workspace, '-c', 'user.name=t', '-c', 'user.email=t@t', "
        "'commit', '-q', '--allow-empty', '-m', 'base'], check=True)\n"
        "from agents_shipgate.cli.main import app\n"
        "sys.argv = ['agents-shipgate', 'diff', '--workspace', workspace, '--base', 'HEAD', '--json']\n"
        "try:\n"
        "    app(standalone_mode=False)\n"
        "except SystemExit:\n"
        "    pass\n"
    )

    assert "agents_shipgate.cli.diff" in modules
    assert [name for name in UNRELATED if name in modules] == []


def test_the_verify_package_still_exports_its_command_and_orchestrator() -> None:
    from agents_shipgate.cli import verify as package
    from agents_shipgate.cli.verify.command import verify
    from agents_shipgate.cli.verify.orchestrator import run_verify

    assert package.verify is verify
    assert package.run_verify is run_verify


def test_resolving_diff_imports_diff_and_nothing_unrelated() -> None:
    modules = _modules_after(
        "import typer\n"
        "from typer.main import get_command\n"
        "from agents_shipgate.cli.main import app\n"
        "root = get_command(app)\n"
        "assert root.get_command(typer.Context(root), 'diff').name == 'diff'\n"
    )

    assert "agents_shipgate.cli.diff" in modules
    assert [name for name in UNRELATED if name in modules] == []


def test_every_listed_command_resolves_to_itself() -> None:
    root = get_command(app)
    ctx = typer.Context(root)
    names = root.list_commands(ctx)

    assert len(names) == len(set(names)) > 25
    for name in names:
        command = root.get_command(ctx, name)
        assert command is not None and command.name == name, name
    visible = [name for name in names if not root.get_command(ctx, name).hidden]
    assert visible == ["diff", "check", "verify", "audit", "init", "doctor"]
    assert root.get_command(ctx, "not-a-command") is None


def test_a_typo_is_still_offered_the_command_it_meant() -> None:
    result = CliRunner().invoke(app, ["dif"], env={"CI": "true", "TERM": "dumb", "COLUMNS": "200"})

    assert result.exit_code == 2
    assert "Did you mean 'diff'?" in result.output
