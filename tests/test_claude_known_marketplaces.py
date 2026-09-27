"""#720: a Claude Code plugin marketplace is a grant, so changing one produces a row.

`extraKnownMarketplaces` decides which plugin marketplaces a project trusts, and
`enabledPlugins` entries install from them. The adapter read only the plugins,
so the #660 cold start saw `open-learning-exchange/myplanet` add a marketplace
beside its plugin and named only the plugin.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from agents_shipgate.cli.main import app

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}
TEAM = {"source": {"source": "github", "repo": "acme/claude-plugins"}}
FORK = {"source": {"source": "github", "repo": "someone-else/claude-plugins"}}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=_GIT_ENV
    ).stdout.strip()


def _rows(tmp_path: Path, before: dict, after: dict) -> list[tuple[str, str, str, bool]]:
    repo = tmp_path / "repo"
    (repo / ".claude").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    settings = repo / ".claude" / "settings.json"
    settings.write_text(json.dumps(before), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    settings.write_text(json.dumps(after), encoding="utf-8")

    result = CliRunner().invoke(app, ["diff", "--workspace", str(repo), "--base", base, "--json"])
    payload = json.loads(result.stdout)
    assert payload["comparison_status"] == "comparable", payload
    return [(row["direction"], row["before"], row["after"], row["expands"]) for row in payload["rows"]]


def test_an_added_marketplace_is_named_and_expands(tmp_path: Path) -> None:
    assert _rows(tmp_path, {}, {"extraKnownMarketplaces": {"acme": TEAM}}) == [
        ("added", "—", "marketplace:acme", True)
    ]


def test_a_removed_marketplace_is_named_and_does_not_expand(tmp_path: Path) -> None:
    assert _rows(tmp_path, {"extraKnownMarketplaces": {"acme": TEAM}}, {}) == [
        ("removed", "marketplace:acme", "—", False)
    ]


def test_a_repointed_marketplace_is_one_widening_row(tmp_path: Path) -> None:
    """The name stays and the source moves: what the enabled plugins run changed."""

    assert _rows(
        tmp_path,
        {"extraKnownMarketplaces": {"acme": TEAM}},
        {"extraKnownMarketplaces": {"acme": FORK}},
    ) == [("widened", "marketplace:acme", "marketplace:acme", True)]


def test_an_unchanged_marketplace_is_quiet(tmp_path: Path) -> None:
    settings = {"extraKnownMarketplaces": {"acme": TEAM}}

    assert _rows(tmp_path, settings, {**settings, "model": "claude-sonnet-5"}) == []


def test_a_plugin_and_a_marketplace_of_one_name_are_two_grants(tmp_path: Path) -> None:
    rows = _rows(tmp_path, {}, {"enabledPlugins": {"acme": True}, "extraKnownMarketplaces": {"acme": TEAM}})

    assert sorted(row[2] for row in rows) == ["acme", "marketplace:acme"]
