"""Claude Code plugin marketplace package contract.

The repo doubles as a Claude Code plugin marketplace
(``/plugin marketplace add ThreeMoonsLab/agents-shipgate``), the symmetric
counterpart of the Codex marketplace under ``.agents/plugins/``
(tests/test_codex_plugin_launch_package.py). Same discipline: the plugin is
skill-only (workflows, not the scanner binary), its skill and command are
byte-identical copies of the canonical sources, and every version literal
moves with ``__version__`` on release.

The frontmatter tests exist because ``claude plugin validate`` caught the
canonical SKILL.md and /shipgate command shipping YAML frontmatter that
failed to parse (unquoted ``:`` in ``description``) — Claude Code loads such
files with silently-empty metadata, which kills description-based skill
auto-triggering. That must never regress.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from agents_shipgate import __version__

REPO_ROOT = Path(__file__).resolve().parent.parent
MARKETPLACE_PATH = REPO_ROOT / ".claude-plugin" / "marketplace.json"
PLUGIN_ROOT = REPO_ROOT / "plugins" / "claude-code"
CANONICAL_SKILL = REPO_ROOT / "skills" / "agents-shipgate"
CANONICAL_COMMAND = REPO_ROOT / ".claude" / "commands" / "shipgate.md"


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path} has no frontmatter"
    block = text.split("---\n", 2)[1]
    parsed = yaml.safe_load(block)
    assert isinstance(parsed, dict), (
        f"{path} frontmatter failed to parse as a YAML mapping — Claude Code "
        "loads it with silently-empty metadata (the validate finding this "
        "suite exists to prevent)."
    )
    return parsed


def test_marketplace_manifest_is_installable() -> None:
    marketplace = json.loads(MARKETPLACE_PATH.read_text(encoding="utf-8"))
    assert marketplace["name"] == "agents-shipgate"
    assert marketplace["owner"]["name"] == "Three Moons Lab"
    [entry] = marketplace["plugins"]
    assert entry["name"] == "agents-shipgate"
    assert entry["source"] == "./plugins/claude-code"
    assert (REPO_ROOT / "plugins" / "claude-code" / ".claude-plugin" / "plugin.json").is_file()
    assert entry["version"] == __version__, (
        "marketplace plugin entry version must move with __version__ on "
        "every release bump."
    )
    assert entry["strict"] is True


def test_plugin_manifest_is_skill_only_and_versioned() -> None:
    manifest = json.loads(
        (PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    assert manifest["name"] == "agents-shipgate"
    assert manifest["version"] == __version__, (
        "plugin.json version must move with __version__ on every release bump."
    )
    # Skill-only, like the Codex plugin: no hooks, MCP servers, agents, or
    # bin/ shipped from the marketplace. Hooks stay on the explicit
    # install-hooks CLI path where the agents-shipgate binary must exist.
    for key in ("hooks", "mcpServers", "agents", "lspServers"):
        assert key not in manifest, f"plugin must stay skill-only; found {key!r}"
    assert not (PLUGIN_ROOT / "hooks").exists()
    assert not (PLUGIN_ROOT / ".mcp.json").exists()
    assert not (PLUGIN_ROOT / "agents").exists()


def test_plugin_skill_is_byte_identical_to_canonical() -> None:
    plugin_skill = PLUGIN_ROOT / "skills" / "agents-shipgate"
    canonical_files = sorted(
        p.relative_to(CANONICAL_SKILL) for p in CANONICAL_SKILL.rglob("*") if p.is_file()
    )
    plugin_files = sorted(
        p.relative_to(plugin_skill) for p in plugin_skill.rglob("*") if p.is_file()
    )
    assert canonical_files == plugin_files, (
        "plugin skill file set diverged from skills/agents-shipgate/ — "
        "copy the canonical tree wholesale."
    )
    for rel in canonical_files:
        assert (plugin_skill / rel).read_bytes() == (CANONICAL_SKILL / rel).read_bytes(), (
            f"plugins/claude-code/skills/agents-shipgate/{rel} diverged from "
            f"the canonical skills/agents-shipgate/{rel}; sync the copies in "
            "the same commit."
        )


def test_plugin_command_is_byte_identical_to_canonical() -> None:
    plugin_command = PLUGIN_ROOT / "commands" / "shipgate.md"
    assert plugin_command.read_bytes() == CANONICAL_COMMAND.read_bytes(), (
        "plugins/claude-code/commands/shipgate.md diverged from "
        ".claude/commands/shipgate.md (the renderer-pinned canonical source)."
    )


def test_skill_and_command_frontmatter_parse_with_metadata() -> None:
    skill_fm = _frontmatter(PLUGIN_ROOT / "skills" / "agents-shipgate" / "SKILL.md")
    assert skill_fm.get("name") == "agents-shipgate"
    assert isinstance(skill_fm.get("description"), str) and skill_fm["description"], (
        "skill description is the auto-trigger surface; it must survive "
        "YAML parsing."
    )
    command_fm = _frontmatter(PLUGIN_ROOT / "commands" / "shipgate.md")
    assert isinstance(command_fm.get("description"), str) and command_fm["description"]
