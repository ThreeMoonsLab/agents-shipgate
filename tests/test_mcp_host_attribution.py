"""#936 (with #970): a `.mcp.json` belongs to the host whose declaration selects it.

The boundary registry finds `.mcp.json` by its file name at any depth, and the
name alone made every one a Claude Code declaration. A static replay of
firecrawl/firecrawl-mcp-server#468 printed `claude-code
plugins/codex/firecrawl/.mcp.json` although only a Codex plugin manifest
selects it, and one of xai-org/plugin-marketplace#1205 printed `claude-code
external_plugins/ceraph/.mcp.json` beside a Grok plugin manifest no Claude
Code file names.

Both shapes are static fixtures here, driven through `diff` (text and
`--json`), `verify`, `check` and `audit --host`, beside the controls that keep
`claude-code`: a root `.mcp.json`, one a Claude Code plugin selects, one two
hosts select, and an unselected nested copy, whose existing attribution is
preserved. Nothing is installed, fetched or run.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.boundary_registry import (
    is_boundary_surface_path,
    plugin_manifest_root,
)
from agents_shipgate.core.host_grants import build_host_boundary_snapshot
from agents_shipgate.core.mcp_host_selection import (
    NOT_AN_OBJECT,
    NOT_SELECTED,
    UNATTRIBUTED_MCP_HOST,
    UNREAD_FORMAT,
    followed_mcp_member,
    select_mcp_hosts,
)

ROOT = Path(__file__).resolve().parents[1]
_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=_GIT_ENV
    ).stdout.strip()


def _write(repo: Path, name: str, value: object) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    # Written as a repository file usually is, with a final newline: `check`
    # compares the diff's text with the working tree's.
    path.write_text(value if isinstance(value, str) else json.dumps(value) + "\n", encoding="utf-8")


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A", "-f")
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)


def _two(tmp_path: Path, base: dict[str, object], head: dict[str, object | None]) -> Path:
    """One base commit on `main` and one head commit on `change`; ``None`` deletes."""

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    for name, value in base.items():
        _write(repo, name, value)
    _commit(repo, "base")
    _git(repo, "checkout", "-q", "-b", "change")
    for name, value in head.items():
        if value is None:
            (repo / name).unlink()
        else:
            _write(repo, name, value)
    _commit(repo, "head")
    return repo


def _invoke(args: list[str], *, exit_code: int | None = 0) -> str:
    result = CliRunner().invoke(app, args, env={"NO_COLOR": "1", "COLUMNS": "200"})
    if exit_code is not None:
        assert result.exit_code == exit_code, result.output
    return result.output


def _diff(repo: Path) -> tuple[str, dict]:
    command = ["diff", "--workspace", str(repo), "--base", "main"]
    return _invoke(command), json.loads(_invoke([*command, "--json"]))


def _check(repo: Path) -> dict:
    return json.loads(_invoke(
        ["check", "--workspace", str(repo), "--base", "main", "--format", "agent-boundary-json"],
        exit_code=None,
    ))


def _verify(repo: Path, out: Path) -> tuple[dict, str]:
    _invoke([
        "verify", "--workspace", str(repo), "--config", "shipgate.yaml", "--ci-mode", "advisory",
        "--out", str(out), "--format", "text", "--base", "main",
        "--head", _git(repo, "rev-parse", "HEAD"),
    ])
    verifier = json.loads((out / "verifier.json").read_text(encoding="utf-8"))
    return verifier, (out / "pr-comment.md").read_text(encoding="utf-8")


def _audit(repo: Path) -> dict:
    return json.loads(_invoke(["audit", "--host", "--workspace", str(repo), "--json"], exit_code=None))


# --- the two issue shapes and their controls ----------------------------------

FIRECRAWL = {"mcpServers": {"firecrawl": {"url": "https://mcp.firecrawl.dev/fc-key/v2/mcp"}}}
CERAPH = {"mcpServers": {"react-native-mcp": {
    "command": "npx", "args": ["-y", "@ceraph/react-native-mcp@latest"]}}}
DEMO = {"mcpServers": {"demo": {"command": "npx", "args": ["-y", "demo-mcp@latest"]}}}

#: name: (base, head, {subject: host} of the added rows, the coverage line)
SHAPES: dict[str, tuple[dict, dict, dict[str, str], str]] = {
    # firecrawl/firecrawl-mcp-server#468: only a Codex plugin manifest selects it.
    "codex_plugin": (
        {"README.md": "firecrawl\n"},
        {
            "plugins/codex/firecrawl/.codex-plugin/plugin.json": {
                "name": "firecrawl", "description": "Firecrawl for Codex",
                "mcpServers": "./.mcp.json",
            },
            "plugins/codex/firecrawl/.mcp.json": FIRECRAWL,
        },
        {"codex plugins/codex/firecrawl/.mcp.json": "codex"},
        "plugins/codex/firecrawl/.mcp.json (codex): read in head only; 1 row",
    ),
    # xai-org/plugin-marketplace#1205: registered only in Grok plugin manifests.
    "grok_plugin": (
        {".grok-plugin/marketplace.json": {"name": "m", "plugins": []}},
        {
            ".grok-plugin/marketplace.json": {"name": "m", "plugins": [
                {"name": "ceraph", "source": "./external_plugins/ceraph"}]},
            "external_plugins/ceraph/.grok-plugin/plugin.json": {"name": "ceraph"},
            "external_plugins/ceraph/.mcp.json": CERAPH,
        },
        {"external_plugins/ceraph/.mcp.json": UNATTRIBUTED_MCP_HOST},
        "external_plugins/ceraph/.mcp.json (host not established): read in head only; 1 row",
    ),
    "root_control": (
        {"README.md": "x\n"},
        {".mcp.json": DEMO},
        {"claude-code .mcp.json": "claude-code"},
        ".mcp.json (claude-code): read in head only; 1 row",
    ),
    "claude_plugin_control": (
        {"README.md": "x\n"},
        {"plugins/demo/.claude-plugin/plugin.json": {"name": "demo"}, "plugins/demo/.mcp.json": DEMO},
        {"claude-code plugins/demo/.mcp.json": "claude-code"},
        "plugins/demo/.mcp.json (claude-code): read in head only; 1 row",
    ),
    "claude_and_codex_plugin": (
        {"README.md": "x\n"},
        {
            "plugins/demo/.claude-plugin/plugin.json": {"name": "demo"},
            "plugins/demo/.codex-plugin/plugin.json": {"name": "demo", "mcpServers": "./.mcp.json"},
            "plugins/demo/.mcp.json": DEMO,
        },
        {"claude-code plugins/demo/.mcp.json": "claude-code", "codex plugins/demo/.mcp.json": "codex"},
        "plugins/demo/.mcp.json (claude-code, codex): read in head only; 2 rows",
    ),
    # No declaration selects it and no plugin manifest sits beside it: the
    # registry's attribution, a nested copy kept under Claude Code review, is
    # unchanged.
    "unselected_nested": (
        {"README.md": "x\n"},
        {"examples/demo/.mcp.json": DEMO},
        {"claude-code examples/demo/.mcp.json": "claude-code"},
        "examples/demo/.mcp.json (claude-code): read in head only; 1 row",
    ),
}


@pytest.mark.parametrize("name", list(SHAPES))
def test_each_shape_names_the_selecting_host_in_diff_text_and_json(tmp_path: Path, name: str) -> None:
    base, head, rows, line = SHAPES[name]
    repo = _two(tmp_path, base, head)

    text, payload = _diff(repo)

    assert payload["comparison_status"] == "comparable"
    assert {row["subject"]: row["direction"] for row in payload["rows"]} == dict.fromkeys(rows, "added")
    # The declaration facts are kept on every host: server, launch detail, `⚠`.
    assert all(row["expands"] and row["severity"] == "high" for row in payload["rows"])
    assert f"  {line}" in text.splitlines()
    for subject in rows:
        assert f"added  {subject}\n" in text
    if "claude-code" not in rows.values():
        assert "claude-code" not in text
    # A manifest member this entry followed to a file it read is not unread.
    assert not [item for item in payload["coverage"]["items"] if item["status"] == "changed_not_read"]


def test_the_codex_shape_keeps_its_facts_and_names_codex(tmp_path: Path) -> None:
    base, head, _rows, _line = SHAPES["codex_plugin"]
    text, payload = _diff(_two(tmp_path, base, head))

    assert "⚠ high  added  codex plugins/codex/firecrawl/.mcp.json" in text
    assert "firecrawl (url https://mcp.firecrawl.dev/<redacted-path>)" in text
    assert "claude-code" not in text
    assert [item["hosts"] for item in payload["coverage"]["items"]] == [["codex"]]


def test_the_grok_shape_keeps_its_facts_under_a_host_neutral_subject(tmp_path: Path) -> None:
    base, head, _rows, _line = SHAPES["grok_plugin"]
    text, payload = _diff(_two(tmp_path, base, head))

    assert "⚠ high  added  external_plugins/ceraph/.mcp.json\n" in text
    assert "react-native-mcp (command name npx; package @ceraph/react-native-mcp@latest)" in text
    (row,) = payload["rows"]
    assert row["why"] == (
        "an MCP tool surface the agent may call has changed; host not established: no "
        "declaration this entry reads selects this file, and another host's plugin manifest "
        "sits beside it; launch source is mutable (@ceraph/react-native-mcp@latest)"
    )
    assert "claude-code" not in text
    assert [item["hosts"] for item in payload["coverage"]["items"]] == [[UNATTRIBUTED_MCP_HOST]]


@pytest.mark.parametrize("name", ["codex_plugin", "grok_plugin"])
def test_verify_and_the_pr_comment_carry_the_same_rows(tmp_path: Path, name: str) -> None:
    base, head, rows, line = SHAPES[name]
    repo = _two(tmp_path, base, head)
    _text, payload = _diff(repo)

    verifier, comment = _verify(repo, tmp_path / "out")

    comparison = verifier["host_comparison"]
    assert [row["subject"] for row in comparison["rows"]] == [row["subject"] for row in payload["rows"]]
    assert [row["why"] for row in comparison["rows"]] == [row["why"] for row in payload["rows"]]
    assert comparison["coverage"]["items"] == payload["coverage"]["items"]
    assert "claude-code" not in comment
    source, hosts = line.split(":", 1)[0].split(" (", 1)
    assert f"` {source} ` ({hosts}" in comment


#: name: affected hosts `check` names. Its decision is the registry's, unchanged.
CHECK_HOSTS = {
    "codex_plugin": ["codex"],
    "grok_plugin": [],
    "root_control": ["claude-code"],
    "claude_plugin_control": ["claude-code"],
    "claude_and_codex_plugin": ["claude-code", "codex"],
    "unselected_nested": ["claude-code"],
}


@pytest.mark.parametrize("name", list(SHAPES))
def test_check_names_the_same_hosts_and_decides_as_before(tmp_path: Path, name: str) -> None:
    base, head, rows, _line = SHAPES[name]
    repo = _two(tmp_path, base, head)

    result = _check(repo)

    assert result["affected_hosts"] == CHECK_HOSTS[name]
    assert sorted(row["subject"] for row in result["rows"]) == sorted(rows)
    # Routing stays the registry's: every `.mcp.json` still routes to review,
    # the root one through its MCP rule, a nested one as a protected surface.
    assert result["decision"] == "require_review"
    mcp = [item for item in result["violations"] if item["path"].endswith(".mcp.json")]
    expected = (
        "SHIP-HOST-BOUNDARY-MCP-SERVER-ADDED" if name == "root_control"
        else "SHIP-AGENT-BOUNDARY-PROTECTED-SURFACE-UNCLASSIFIED"
    )
    assert [item["check_id"] for item in mcp] == [expected]
    covered = {
        item["adapter"]: item["paths"] for item in result["host_coverage"] if item["paths"]
    }
    mcp_path = next(path for path in head if path.endswith(".mcp.json"))
    expected_adapters = {
        {"claude-code": "claude_code", "codex": "codex"}[host]
        for host in CHECK_HOSTS[name]
    }
    assert {adapter for adapter, paths in covered.items() if mcp_path in paths} == expected_adapters


@pytest.mark.parametrize("name", ["codex_plugin", "grok_plugin"])
def test_audit_publishes_the_host_and_names_why_none_is_established(tmp_path: Path, name: str) -> None:
    base, head, rows, _line = SHAPES[name]
    repo = _two(tmp_path, base, head)

    inventory = _audit(repo)

    Draft202012Validator(
        json.loads((ROOT / "docs/host-grants-inventory-schema.v0.9.json").read_text())
    ).validate(inventory)
    mcp_path = next(path for path in head if path.endswith(".mcp.json"))
    assert {(grant["host"], grant["source"]) for grant in inventory["grants"]} == {
        (host, mcp_path) for host in rows.values()
    }
    issues = [issue for issue in inventory["issues"] if issue["source"] == mcp_path]
    if name == "codex_plugin":
        assert issues == []
        return
    (issue,) = issues
    assert issue["host"] == UNATTRIBUTED_MCP_HOST and issue["blocking"] is False
    assert "external_plugins/ceraph/.grok-plugin/plugin.json" in issue["message"]
    # No host's coverage is partial for it: it is not established as any host's.
    assert all(item["status"] == "complete" for item in inventory["host_coverage"])


def test_drift_reports_a_reattributed_declaration_once(tmp_path: Path) -> None:
    """A baseline that recorded the file under Claude Code sees one removal and one addition."""

    _write(tmp_path, "plugins/x/.mcp.json", DEMO)
    baseline = tmp_path / "saved" / "host-grants.json"
    audit = ["audit", "--host", "--workspace", str(tmp_path), "--baseline-file", str(baseline)]
    _invoke([*audit, "--save-baseline"], exit_code=None)
    assert baseline.is_file()
    _write(tmp_path, "plugins/x/.grok-plugin/plugin.json", {"name": "x"})

    drift = json.loads(_invoke([*audit, "--drift", "--json"], exit_code=None))

    Draft202012Validator(
        json.loads((ROOT / "docs/host-grants-drift-schema.v0.9.json").read_text())
    ).validate(drift)
    assert drift["has_drift"] is True
    assert {
        ((change["baseline"] or {}).get("host"), (change["current"] or {}).get("host"))
        for change in drift["changes"]
    } == {("claude-code", None), (None, UNATTRIBUTED_MCP_HOST)}
    assert len(drift["changes"]) == 2


def test_a_removed_codex_plugin_is_attributed_from_the_base_tree(tmp_path: Path) -> None:
    """The base is read from a scoped archive, which must hold the manifest."""

    _base, head, _rows, _line = SHAPES["codex_plugin"]
    repo = _two(tmp_path, head, dict.fromkeys(head))

    _text, payload = _diff(repo)

    assert [(row["subject"], row["direction"]) for row in payload["rows"]] == [
        ("codex plugins/codex/firecrawl/.mcp.json", "removed")
    ]


def test_moving_selection_between_hosts_is_a_removal_and_an_addition(tmp_path: Path) -> None:
    manifest = "plugins/x/.codex-plugin/plugin.json"
    repo = _two(
        tmp_path,
        {manifest: {"name": "x"}, "plugins/x/.mcp.json": DEMO},
        {manifest: None, "plugins/x/.claude-plugin/plugin.json": {"name": "x"}},
    )

    _text, payload = _diff(repo)

    assert sorted((row["subject"], row["direction"]) for row in payload["rows"]) == [
        ("claude-code plugins/x/.mcp.json", "added"),
        ("codex plugins/x/.mcp.json", "removed"),
    ]


def test_an_inline_codex_mcp_servers_member_is_still_named_unread(tmp_path: Path) -> None:
    manifest = "p/.codex-plugin/plugin.json"
    repo = _two(
        tmp_path,
        {"README.md": "x\n"},
        {
            manifest: {"name": "p", "mcpServers": {"inline": {"command": "node"}}},
            "p/.mcp.json": DEMO,
        },
    )

    _text, payload = _diff(repo)

    # The Codex reader of `scan` falls back to `.mcp.json` when the member
    # names no path, and the host attribution follows the same rule.
    assert [row["subject"] for row in payload["rows"]] == ["codex p/.mcp.json"]
    assert [
        (item["source"], item["candidate"])
        for item in payload["coverage"]["items"]
        if item["status"] == "changed_not_read"
    ] == [(f"{manifest}#mcpServers", "plugin_manifest_mcp_servers")]


# --- the rules, one by one ----------------------------------------------------


def _hosts(sources: list[str], manifests: dict[str, object], **kwargs) -> dict[str, tuple]:
    return {
        source: (selection.hosts, selection.unattributed_by)
        for source, selection in select_mcp_hosts(
            sources=sources, manifests=manifests, **kwargs
        ).items()
    }


def test_plugin_manifests_of_every_format_are_recognised_by_name() -> None:
    assert plugin_manifest_root(".claude-plugin/plugin.json") == ("", "claude-code")
    assert plugin_manifest_root("a/b/.Codex-Plugin/plugin.json") == ("a/b", "codex")
    assert plugin_manifest_root("x/.grok-plugin/plugin.json") == ("x", "other")
    assert plugin_manifest_root("x/.cursor-plugin/plugin.json") == ("x", "other")
    assert plugin_manifest_root("x/.github/plugin/plugin.json") == ("x", "other")
    assert plugin_manifest_root(".grok-plugin/marketplace.json") is None
    assert plugin_manifest_root("x/plugin.json") is None
    assert plugin_manifest_root("x/my-plugin/plugin.json") is None
    # A base tree is scoped by the same predicate, so it holds them.
    assert is_boundary_surface_path("x/.grok-plugin/plugin.json")
    assert is_boundary_surface_path("x/.codex-plugin/plugin.json")


def test_the_root_file_is_claude_codes_whatever_sits_beside_it() -> None:
    assert _hosts([".mcp.json"], {".grok-plugin/plugin.json": None}) == {
        ".mcp.json": (("claude-code",), ()),
    }


def test_a_codex_manifest_selects_its_default_and_what_it_names() -> None:
    manifests = {
        "a/.codex-plugin/plugin.json": {"name": "a"},
        "b/.codex-plugin/plugin.json": {"name": "b", "mcpServers": ["./cfg/.mcp.json"]},
        "c/.codex-plugin/plugin.json": {"name": "c", "mcpServers": "../a/.mcp.json"},
    }
    assert _hosts(["a/.mcp.json", "b/cfg/.mcp.json", "b/.mcp.json"], manifests) == {
        "a/.mcp.json": (("codex",), ()),
        "b/cfg/.mcp.json": (("codex",), ()),
        # `b` names another file, so the one at its root is not selected.
        "b/.mcp.json": ((UNATTRIBUTED_MCP_HOST,), (("b/.codex-plugin/plugin.json", NOT_SELECTED),)),
    }


def test_a_reference_outside_the_plugin_or_absolute_is_not_followed() -> None:
    manifests = {
        "c/.codex-plugin/plugin.json": {"name": "c", "mcpServers": ["../a/.mcp.json", "/a/.mcp.json"]},
    }
    assert _hosts(["a/.mcp.json"], manifests) == {"a/.mcp.json": (("claude-code",), ())}


def test_another_hosts_manifest_beside_it_leaves_the_host_not_established() -> None:
    manifests = {
        "g/.grok-plugin/plugin.json": None,
        "u/.cursor-plugin/plugin.json": None,
        "p/.github/plugin/plugin.json": None,
        "broken/.codex-plugin/plugin.json": None,
    }
    assert _hosts(["g/.mcp.json", "u/.mcp.json", "p/.mcp.json", "broken/.mcp.json"], manifests) == {
        "g/.mcp.json": ((UNATTRIBUTED_MCP_HOST,), (("g/.grok-plugin/plugin.json", UNREAD_FORMAT),)),
        "u/.mcp.json": ((UNATTRIBUTED_MCP_HOST,), (("u/.cursor-plugin/plugin.json", UNREAD_FORMAT),)),
        "p/.mcp.json": ((UNATTRIBUTED_MCP_HOST,), (("p/.github/plugin/plugin.json", UNREAD_FORMAT),)),
        "broken/.mcp.json": (
            (UNATTRIBUTED_MCP_HOST,), (("broken/.codex-plugin/plugin.json", NOT_AN_OBJECT),)
        ),
    }


def test_claude_code_selection_wins_over_an_unread_manifest_beside_it() -> None:
    manifests = {
        "a/.claude-plugin/plugin.json": None,
        "a/.grok-plugin/plugin.json": None,
        "m/.grok-plugin/plugin.json": None,
        "r/.grok-plugin/plugin.json": None,
    }
    assert _hosts(
        ["a/.mcp.json", "m/.mcp.json", "r/cfg/.mcp.json", "r/.mcp.json"], manifests,
        claude_roots={"m"}, claude_references={"R/cfg/.MCP.json"},
    ) == {
        "a/.mcp.json": (("claude-code",), ()),
        "m/.mcp.json": (("claude-code",), ()),
        "r/cfg/.mcp.json": (("claude-code",), ()),
        "r/.mcp.json": ((UNATTRIBUTED_MCP_HOST,), (("r/.grok-plugin/plugin.json", UNREAD_FORMAT),)),
    }


def test_a_claude_marketplace_plugin_root_keeps_claude_code_through_the_reader(tmp_path: Path) -> None:
    _write(tmp_path, ".claude-plugin/marketplace.json", {
        "name": "m", "owner": {"name": "x"},
        "plugins": [{"name": "x", "source": "./plugins/x"}, {"name": "y", "source": "./plugins/y",
                     "mcpServers": "./servers/.mcp.json"}],
    })
    _write(tmp_path, "plugins/x/.grok-plugin/plugin.json", {"name": "x"})
    _write(tmp_path, "plugins/x/.mcp.json", DEMO)
    _write(tmp_path, "plugins/y/.grok-plugin/plugin.json", {"name": "y"})
    _write(tmp_path, "plugins/y/servers/.mcp.json", DEMO)

    inventory = build_host_boundary_snapshot(tmp_path).inventory

    assert sorted((grant["host"], grant["source"]) for grant in inventory["grants"]) == [
        ("claude-code", "plugins/x/.mcp.json"),
        ("claude-code", "plugins/y/servers/.mcp.json"),
    ]


def test_only_a_member_of_mcp_json_references_is_followed() -> None:
    manifest = "p/.codex-plugin/plugin.json"
    assert followed_mcp_member(manifest, {"name": "p"}) == []
    assert followed_mcp_member(manifest, {"mcpServers": "./.mcp.json"}) == ["p/.mcp.json"]
    assert followed_mcp_member(manifest, {"mcpServers": ["./a/.mcp.json", 3]}) is None
    assert followed_mcp_member(manifest, {"mcpServers": "./servers.json"}) is None
    assert followed_mcp_member(manifest, {"mcpServers": {"x": {}}}) is None
    # Claude Code documents `./` paths only.
    assert followed_mcp_member("p/.claude-plugin/plugin.json", {"mcpServers": ".mcp.json"}) is None
    assert followed_mcp_member("p/.grok-plugin/plugin.json", {"mcpServers": "./.mcp.json"}) is None


def test_the_support_page_documents_the_rules() -> None:
    page = (ROOT / "docs/host-boundary-support.md").read_text(encoding="utf-8")
    assert "### Which host a `.mcp.json` belongs to" in page
    for phrase in ("host not established", "`unknown`", ".codex-plugin/plugin.json", "#936"):
        assert phrase in page
