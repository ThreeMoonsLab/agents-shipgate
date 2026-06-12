"""`init --claude-code` — the one-shot Claude Code setup.

One command wires the full surface: the CLAUDE.md managed block, the
`.claude/skills/agents-shipgate/` skill bundle, the Claude Code hooks
(PostToolUse trigger + Stop verifier), and a conventional
`agents-shipgate verify --json` alias in Makefile / package.json when those
files exist. Everything is idempotent and dry-run without --write.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agents_shipgate.cli.main import app

runner = CliRunner()


def _run_init(workspace: Path, *extra: str):
    return runner.invoke(
        app,
        ["init", "--workspace", str(workspace), "--claude-code", *extra],
    )


def test_claude_code_write_installs_full_surface(tmp_path: Path) -> None:
    (tmp_path / "Makefile").write_text("all:\n\techo hi\n", encoding="utf-8")
    (tmp_path / "package.json").write_text(
        '{\n  "name": "demo",\n  "scripts": {\n    "test": "jest"\n  }\n}\n',
        encoding="utf-8",
    )

    result = _run_init(tmp_path, "--write", "--json")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)

    # Implied agent-instruction targets.
    targets = {t["name"] for t in payload["agent_instructions"]["targets"]}
    assert targets == {"claude-md", "claude-code-skill"}
    assert (tmp_path / "CLAUDE.md").is_file()
    assert (tmp_path / ".claude/skills/agents-shipgate/SKILL.md").is_file()

    # Hooks.
    hooks = payload["claude_code"]["hooks"]
    assert (tmp_path / ".claude/settings.json").is_file()
    assert (tmp_path / ".claude/hooks/agents-shipgate.py").is_file()
    assert "settings_status" in hooks and "script_status" in hooks

    # Verify aliases.
    alias = payload["claude_code"]["verify_alias"]
    assert alias["makefile"]["status"] == "appended"
    assert alias["package_json"]["status"] == "appended"
    makefile = (tmp_path / "Makefile").read_text(encoding="utf-8")
    assert "shipgate-verify:" in makefile
    assert "agents-shipgate verify --json" in makefile
    package = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
    assert package["scripts"]["shipgate:verify"] == "agents-shipgate verify --json"
    # The existing script and key order survive the round-trip.
    assert package["scripts"]["test"] == "jest"
    assert list(package)[0] == "name"


def test_claude_code_write_is_idempotent(tmp_path: Path) -> None:
    (tmp_path / "Makefile").write_text("all:\n\techo hi\n", encoding="utf-8")

    first = _run_init(tmp_path, "--write", "--json")
    assert first.exit_code == 0, first.output

    second = _run_init(tmp_path, "--write", "--json")
    assert second.exit_code == 0, second.output
    payload = json.loads(second.output)
    assert payload["claude_code"]["verify_alias"]["makefile"]["status"] == "unchanged"
    makefile = (tmp_path / "Makefile").read_text(encoding="utf-8")
    assert makefile.count("shipgate-verify:") == 1


def test_claude_code_dry_run_writes_nothing(tmp_path: Path) -> None:
    (tmp_path / "Makefile").write_text("all:\n\techo hi\n", encoding="utf-8")

    result = _run_init(tmp_path, "--json")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["claude_code"]["verify_alias"]["makefile"]["status"] == "planned"
    assert not (tmp_path / "CLAUDE.md").exists()
    assert not (tmp_path / ".claude/settings.json").exists()
    assert "shipgate-verify" not in (tmp_path / "Makefile").read_text(encoding="utf-8")


def test_claude_code_skips_missing_alias_hosts(tmp_path: Path) -> None:
    result = _run_init(tmp_path, "--write", "--json")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    alias = payload["claude_code"]["verify_alias"]
    assert alias["makefile"]["status"] == "skipped_missing"
    assert alias["package_json"]["status"] == "skipped_missing"


def test_claude_code_reports_invalid_package_json(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{not json", encoding="utf-8")

    result = _run_init(tmp_path, "--write", "--json")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["claude_code"]["verify_alias"]["package_json"]["status"] == (
        "skipped_invalid"
    )
    # The broken file is left untouched.
    assert (tmp_path / "package.json").read_text(encoding="utf-8") == "{not json"


def test_explicit_agent_instructions_override_implied_targets(
    tmp_path: Path,
) -> None:
    result = _run_init(
        tmp_path, "--write", "--json", "--agent-instructions=claude-md"
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    targets = {t["name"] for t in payload["agent_instructions"]["targets"]}
    assert targets == {"claude-md"}
    # Hooks and aliases still install — the flag controls the whole surface.
    assert "claude_code" in payload
    assert (tmp_path / ".claude/hooks/agents-shipgate.py").is_file()
