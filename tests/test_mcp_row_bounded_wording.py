"""#929 and #933: an MCP row says what the read declaration establishes, and no more.

#929. Replaying bulaofen0036-coder/TIA_Portal_Openness_MCP#46, a pull request
removed `.mcp.json` and moved its one server, unchanged, inline into
`.claude-plugin/plugin.json`'s `mcpServers`, a member this entry does not
read. Coverage named that member (`changed, not read by this entry`, #821),
yet the removal row said "an MCP tool surface is no longer offered to the
agent". The row may say only that the read source no longer declares the
server, while a changed, unread MCP declaration shares its host and plugin
scope; it is never paired with that declaration, which stays unread.

#933. Replaying webiny/webiny-js#5760, `npx -y @webiny/stdlib serve` was noted
as `launch source is mutable`. npm documents that a package named with no
version specifier "will be matched with whatever version exists in the local
project", and installed into the npm cache only when the project does not
depend on it (https://docs.npmjs.com/cli/v11/commands/npm-exec#description).
The note now states the declaration fact (`package spec has no exact
version`) and the launch it leaves open, apart. No `package.json`, lockfile
or cache is read: a project that depends on the package and one that does not
give the same row.

Both are wording only: no row, direction, `expands`, severity, expansion
signal, digest, baseline or `check` decision moves. Nothing is installed,
fetched or run.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.capability_diff_rows import (
    MCP_REMOVED_UNREAD_DECLARATION,
    MCP_REMOVED_WHY,
    NPX_LOCAL_OR_REGISTRY,
    _unread_declaration_may_offer,
    capability_diff_rows,
)
from agents_shipgate.core.host_grants import (
    _mcp_grants,
    build_host_grants_baseline,
    compared_grant,
    host_grant_expansion_signals,
    host_grants_sha256,
    normalized_host_grants,
)
from agents_shipgate.core.mcp_launch_source import (
    LOCAL_PROJECT_OR_REGISTRY,
    launch_source_pin,
    launch_source_resolution,
)
from agents_shipgate.core.unread_inputs import UnreadMcpDeclaration
from agents_shipgate.schemas.host_grants import HostMcpServerGrantV9

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}

UNVERSIONED = f"package spec has no exact version; {NPX_LOCAL_OR_REGISTRY}"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=_GIT_ENV
    ).stdout.strip()


def _write(repo: Path, name: str, value: object) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value) + "\n", encoding="utf-8")


def _two(tmp_path: Path, base: dict[str, object], head: dict[str, object | None]) -> Path:
    """One base commit on `main` and one head commit on `change`; ``None`` deletes."""

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    for name, value in base.items():
        _write(repo, name, value)
    _git(repo, "add", "-A", "-f")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "change")
    for name, value in head.items():
        if value is None:
            (repo / name).unlink()
        else:
            _write(repo, name, value)
    _git(repo, "add", "-A", "-f")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "head")
    return repo


def _invoke(args: list[str], *, exit_code: int | None = 0) -> str:
    result = CliRunner().invoke(app, args, env={"NO_COLOR": "1", "COLUMNS": "400"})
    if exit_code is not None:
        assert result.exit_code == exit_code, result.output
    return result.stdout


def _routes(repo: Path, tmp_path: Path) -> dict[str, object]:
    """Every route that prints or publishes these rows, read once each."""

    command = ["diff", "--workspace", str(repo), "--base", "main"]
    text = _invoke(command)
    diff = json.loads(_invoke([*command, "--json"]))
    out = tmp_path / "out"
    _invoke([
        "verify", "--workspace", str(repo), "--config", "shipgate.yaml", "--ci-mode", "advisory",
        "--out", str(out), "--format", "text", "--base", "main",
        "--head", _git(repo, "rev-parse", "HEAD"),
    ])
    verifier = json.loads((out / "verifier.json").read_text(encoding="utf-8"))
    patch = tmp_path / "change.diff"
    patch.write_text(_git(repo, "diff", "main", "HEAD") + "\n", encoding="utf-8")
    checks = {
        name: json.loads(_invoke(
            ["check", "--workspace", str(repo), *selection, "--format", "agent-boundary-json"],
            exit_code=None,
        ))
        for name, selection in {
            "check": ["--base", "main", "--head", _git(repo, "rev-parse", "HEAD")],
            "check_diff": ["--diff", str(patch)],
        }.items()
    }
    return {
        "text": text,
        "diff": diff,
        "verify": verifier["host_comparison"],
        "comment": (out / "pr-comment.md").read_text(encoding="utf-8"),
        **checks,
    }


def _whys(rows: list[dict]) -> list[str]:
    return [row["why"] for row in rows]


def _decision(result: dict) -> dict:
    return {key: result.get(key) for key in ("decision", "control", "violations")}


# --- #929: a removal beside an unread inline replacement ----------------------

TIA = {
    "command": "${CLAUDE_PLUGIN_ROOT}/runtime/v21/TiaMcpServer.exe",
    "args": ["--tia-major-version", "21"],
}
PLUGIN = ".claude-plugin/plugin.json"


def _tia_move(tmp_path: Path) -> Path:
    """TIA_Portal_Openness_MCP#46: `.mcp.json` removed, the same server moved inline."""

    return _two(
        tmp_path,
        {PLUGIN: {"name": "tia", "mcpServers": "./.mcp.json"}, ".mcp.json": {"mcpServers": {"tia-portal": TIA}}},
        {".mcp.json": None, PLUGIN: {"name": "tia", "mcpServers": {"tia-portal": TIA}}},
    )


def test_a_removal_beside_an_unread_inline_replacement_is_bounded_on_every_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _tia_move(tmp_path)
    routes = _routes(repo, tmp_path)

    (row,) = routes["diff"]["rows"]
    assert row["direction"] == "removed" and row["subject"] == "claude-code .mcp.json"
    assert row["why"] == MCP_REMOVED_UNREAD_DECLARATION
    assert row["severity"] == "high" and row["expands"] is False
    assert routes["verify"]["rows"] == routes["diff"]["rows"]
    assert _whys(routes["check"]["rows"]) == _whys(routes["check_diff"]["rows"]) == [row["why"]]
    for printed in (routes["text"], routes["comment"]):
        assert MCP_REMOVED_UNREAD_DECLARATION in printed
        assert MCP_REMOVED_WHY not in printed
    # The #821 coverage item still names the unread member; nothing pairs with it.
    (item,) = [
        entry for entry in routes["diff"]["coverage"]["items"] if entry["status"] == "changed_not_read"
    ]
    assert item["source"] == f"{PLUGIN}#mcpServers"
    assert item["candidate"] == "plugin_manifest_mcp_servers" and item["rows"] == 0
    assert routes["verify"]["coverage"] == routes["diff"]["coverage"]
    assert f"{PLUGIN}#mcpServers (claude-code): changed, not read by this entry" in routes["text"]
    # `check` decides exactly as it would with the old wording.
    monkeypatch.setattr(
        "agents_shipgate.core.host_comparison._removes_mcp_server", lambda payload: False
    )
    for name, selection in {
        "check": ["--base", "main", "--head", _git(repo, "rev-parse", "HEAD")],
        "check_diff": ["--diff", str(tmp_path / "change.diff")],
    }.items():
        original = json.loads(_invoke(
            ["check", "--workspace", str(repo), *selection, "--format", "agent-boundary-json"],
            exit_code=None,
        ))
        assert _whys(original["rows"]) == [MCP_REMOVED_WHY]
        assert _decision(routes[name]) == _decision(original)
        assert [
            {key: value for key, value in entry.items() if key != "why"} for entry in routes[name]["rows"]
        ] == [{key: value for key, value in entry.items() if key != "why"} for entry in original["rows"]]


@pytest.mark.parametrize(
    "base,head",
    [
        # No unread input changed: the plugin keeps naming the read file.
        pytest.param(
            {PLUGIN: {"name": "tia", "mcpServers": "./.mcp.json"}, ".mcp.json": {"mcpServers": {"tia-portal": TIA}}},
            {".mcp.json": {"mcpServers": {}}},
            id="no-unread-input",
        ),
        # The unread declaration is in another plugin's directory.
        pytest.param(
            {".mcp.json": {"mcpServers": {"tia-portal": TIA}}, f"plugins/other/{PLUGIN}": {"name": "other"}},
            {".mcp.json": None, f"plugins/other/{PLUGIN}": {"name": "other", "mcpServers": {"tia-portal": TIA}}},
            id="other-plugin-scope",
        ),
        # The unread declaration is another host's: a Codex manifest's inline
        # member, while the removed file is one no Codex manifest selected.
        pytest.param(
            {".mcp.json": {"mcpServers": {"tia-portal": TIA}}, ".codex-plugin/plugin.json": {"name": "tia", "mcpServers": "./other/.mcp.json"}},
            {".mcp.json": None, ".codex-plugin/plugin.json": {"name": "tia", "mcpServers": {"tia-portal": TIA}}},
            id="other-host",
        ),
        # The unread member is removed in the head: nothing there can offer it.
        pytest.param(
            {PLUGIN: {"name": "tia", "mcpServers": {"x": TIA}}, ".mcp.json": {"mcpServers": {"tia-portal": TIA}}},
            {".mcp.json": None, PLUGIN: {"name": "tia"}},
            id="member-removed",
        ),
        # A changed input this entry reads is no reason to bound it.
        pytest.param(
            {".mcp.json": {"mcpServers": {"tia-portal": TIA}}, ".claude/settings.json": {}},
            {".mcp.json": None, ".claude/settings.json": {"permissions": {"allow": ["Read"]}}},
            id="read-input",
        ),
    ],
)
def test_a_removal_without_a_relevant_unread_declaration_keeps_its_wording(
    tmp_path: Path, base: dict, head: dict
) -> None:
    repo = _two(tmp_path, base, head)
    routes = _routes(repo, tmp_path)
    removed = [row for row in routes["diff"]["rows"] if row["direction"] == "removed"]
    assert removed and all(row["why"].startswith(MCP_REMOVED_WHY) for row in removed)
    assert routes["verify"]["rows"] == routes["diff"]["rows"]
    for name in ("check", "check_diff"):
        assert [
            row["why"] for row in routes[name]["rows"] if row["direction"] == "removed"
        ] == [row["why"] for row in removed]
    assert MCP_REMOVED_UNREAD_DECLARATION not in routes["text"] + routes["comment"]


def test_an_unattributed_removal_beside_a_changed_unread_plugin_declaration_is_bounded(
    tmp_path: Path,
) -> None:
    """#936's `unknown` host: a Cursor plugin's `mcp.json` beside it changed, unread."""

    root = "plugins/demo"
    repo = _two(
        tmp_path,
        {
            f"{root}/.cursor-plugin/plugin.json": {"name": "demo"},
            f"{root}/.mcp.json": {"mcpServers": {"demo": {"url": "https://mcp.example.invalid/"}}},
            f"{root}/mcp.json": {"mcpServers": {}},
        },
        {
            f"{root}/.mcp.json": None,
            f"{root}/mcp.json": {"mcpServers": {"demo": {"url": "https://mcp.example.invalid/"}}},
        },
    )
    routes = _routes(repo, tmp_path)
    (row,) = routes["diff"]["rows"]
    assert row["subject"] == f"{root}/.mcp.json" and row["direction"] == "removed"
    assert row["why"].startswith(f"{MCP_REMOVED_UNREAD_DECLARATION}; host not established")
    assert routes["verify"]["rows"] == routes["diff"]["rows"]
    assert _whys(routes["check"]["rows"]) == [row["why"]]
    # A provided diff states nothing about the unchanged manifest beside the
    # file, so it neither attributes the file to no host (#936) nor names the
    # plugin's `mcp.json` as a plugin's: its row keeps both of today's wordings.
    assert _whys(routes["check_diff"]["rows"]) == [MCP_REMOVED_WHY]


def test_a_file_two_hosts_select_is_bounded_only_for_the_host_whose_declaration_is_unread(
    tmp_path: Path,
) -> None:
    """A root `.mcp.json` is Claude Code's and, by a Codex manifest's default, Codex's (#936)."""

    repo = _two(
        tmp_path,
        {".mcp.json": {"mcpServers": {"tia-portal": TIA}}, ".codex-plugin/plugin.json": {"name": "tia"}},
        {".mcp.json": None, ".codex-plugin/plugin.json": {"name": "tia", "mcpServers": {"tia-portal": TIA}}},
    )
    routes = _routes(repo, tmp_path)
    whys = {row["subject"]: row["why"] for row in routes["diff"]["rows"]}
    assert whys == {
        "claude-code .mcp.json": MCP_REMOVED_WHY,
        "codex .mcp.json": MCP_REMOVED_UNREAD_DECLARATION,
    }
    assert routes["verify"]["rows"] == routes["diff"]["rows"]
    assert {row["subject"]: row["why"] for row in routes["check"]["rows"]} == whys


@pytest.mark.parametrize(
    "grant,declaration,bounded",
    [
        ({"host": "claude-code", "source": ".mcp.json"}, ("", {"claude-code"}), True),
        ({"host": "claude-code", "source": "p/.mcp.json"}, ("p", {"claude-code"}), True),
        ({"host": "claude-code", "source": "p/q/.mcp.json"}, ("p", {"claude-code"}), True),
        ({"host": "claude-code", "source": "P/.mcp.json"}, ("p", {"claude-code"}), True),
        ({"host": "claude-code", "source": "pq/.mcp.json"}, ("p", {"claude-code"}), False),
        ({"host": "claude-code", "source": ".mcp.json"}, ("p", {"claude-code"}), False),
        ({"host": "claude-code", "source": ".mcp.json"}, ("", {"codex"}), False),
        ({"host": "codex", "source": ".codex/config.toml#profiles.x"}, ("", {"codex"}), True),
        ({"host": "unknown", "source": "p/.mcp.json"}, ("p", {"cursor"}), True),
        ({"host": "unknown", "source": "p/.mcp.json"}, ("p", set()), True),
        ({"host": "claude-code", "source": "p/.mcp.json"}, ("p", set()), False),
    ],
)
def test_relevance_is_one_plugin_scope_and_one_host(grant, declaration, bounded) -> None:
    root, hosts = declaration
    assert _unread_declaration_may_offer(
        grant, [UnreadMcpDeclaration(root=root, hosts=frozenset(hosts))]
    ) is bounded


def test_only_a_removal_is_bounded() -> None:
    """An added or changed server keeps its wording beside the same declaration."""

    declarations = [UnreadMcpDeclaration(root="", hosts=frozenset({"claude-code"}))]

    def grant(args: list[str]) -> dict:
        return _mcp_grants(
            {"mcpServers": {"docs": {"command": "node", "args": args}}},
            host="claude-code", scope="repository", source=".mcp.json",
        )[0]

    for change in (
        {"baseline": grant(["a.js"]), "current": None},
        {"baseline": None, "current": grant(["a.js"])},
        {"baseline": grant(["a.js"]), "current": grant(["b.js"])},
    ):
        payload = {"changes": [change], "expansion_signals": host_grant_expansion_signals([change])}
        (plain,) = capability_diff_rows(payload)
        (bounded,) = capability_diff_rows(payload, unread_mcp_declarations=declarations)
        same = {key: value for key, value in plain.as_dict().items() if key != "why"}
        assert {key: value for key, value in bounded.as_dict().items() if key != "why"} == same
        if change["current"] is None:
            assert (plain.why, bounded.why) == (MCP_REMOVED_WHY, MCP_REMOVED_UNREAD_DECLARATION)
        else:
            assert bounded.why == plain.why


# --- #933: an unversioned npx package -----------------------------------------


@pytest.mark.parametrize(
    "args,pin,resolution",
    [
        (["@webiny/stdlib", "serve"], "mutable", LOCAL_PROJECT_OR_REGISTRY),
        (["-y", "@webiny/stdlib", "serve"], "mutable", LOCAL_PROJECT_OR_REGISTRY),
        (["--yes", "pkg"], "mutable", LOCAL_PROJECT_OR_REGISTRY),
        # A workspace package's executable is spelled like any package name:
        # this reader does not tell them apart, and reads no workspace.
        (["my-workspace-bin"], "mutable", LOCAL_PROJECT_OR_REGISTRY),
        # Any specifier keeps #825's reading.
        (["pkg@latest"], "mutable", None),
        (["-y", "pkg@next"], "mutable", None),
        (["pkg@^1.2.3"], "mutable", None),
        (["pkg@1.2"], "mutable", None),
        (["pkg@1.2.3"], "pinned", None),
        (["--yes", "@scope/pkg@1.2.3"], "pinned", None),
    ],
)
def test_npx_publishes_where_an_unversioned_package_is_resolved(args, pin, resolution) -> None:
    fact = launch_source_pin("npx", args)
    assert fact is not None and fact[0] == pin
    assert launch_source_resolution("npx", args, fact[1]) == resolution
    (grant,) = _mcp_grants(
        {"mcpServers": {"docs": {"command": "npx", "args": args}}},
        host="claude-code", scope="repository", source=".mcp.json",
    )
    assert grant["launch_source"]["pin"] == pin
    assert grant["launch_source"].get("resolution") == resolution
    HostMcpServerGrantV9.model_validate(grant)


@pytest.mark.parametrize(
    "command,args",
    [
        ("bunx", ["pkg"]),
        ("pnpm", ["dlx", "pkg"]),
        ("uvx", ["pkg"]),
        ("docker", ["run", "image"]),
    ],
)
def test_other_launchers_keep_the_mutable_note(command, args) -> None:
    (grant,) = _mcp_grants(
        {"mcpServers": {"docs": {"command": command, "args": args}}},
        host="claude-code", scope="repository", source=".mcp.json",
    )
    assert "resolution" not in grant["launch_source"]
    change = {"baseline": None, "current": grant}
    (row,) = capability_diff_rows({"changes": [change], "expansion_signals": host_grant_expansion_signals([change])})
    assert row.why.endswith("; launch source is mutable")


def _npx(args: list[str]) -> dict:
    return _mcp_grants(
        {"mcpServers": {"docs": {"command": "npx", "args": args}}},
        host="claude-code", scope="repository", source=".mcp.json",
    )[0]


@pytest.mark.parametrize(
    "before,after,note",
    [
        (None, ["-y", "@webiny/stdlib", "serve"], UNVERSIONED),
        (None, ["--yes", "pkg"], UNVERSIONED),
        (["pkg@1.2.3"], ["pkg"], f"launch source moved from pinned (pkg@1.2.3) to a package spec with no exact version; {NPX_LOCAL_OR_REGISTRY}"),
        (["pkg@latest"], ["pkg"], UNVERSIONED),
        (["pkg"], ["pkg", "--verbose"], UNVERSIONED),
        (["pkg"], ["pkg@latest"], "launch source is mutable (pkg@latest)"),
        (None, ["pkg@latest"], "launch source is mutable (pkg@latest)"),
        (["pkg@1.2.3"], ["pkg@latest"], "launch source moved from pinned (pkg@1.2.3) to mutable (pkg@latest)"),
        (["pkg"], ["pkg@1.2.3"], None),
        (["pkg"], None, None),
    ],
)
def test_the_note_separates_the_declaration_from_its_resolution(before, after, note) -> None:
    old = _npx(before) if before is not None else None
    new = _npx(after) if after is not None else None
    change = {"baseline": old, "current": new}
    (row,) = capability_diff_rows({"changes": [change], "expansion_signals": host_grant_expansion_signals([change])})

    def without(grant: dict | None) -> dict | None:
        if grant is None or "resolution" not in (grant.get("launch_source") or {}):
            return grant
        source = {key: value for key, value in grant["launch_source"].items() if key != "resolution"}
        return {**grant, "launch_source": source}

    bare = {"baseline": without(old), "current": without(new)}
    (original,) = capability_diff_rows({"changes": [bare], "expansion_signals": host_grant_expansion_signals([bare])})
    assert {k: v for k, v in row.as_dict().items() if k != "why"} == {
        k: v for k, v in original.as_dict().items() if k != "why"
    }
    assert host_grant_expansion_signals([change]) == host_grant_expansion_signals([bare])
    if note is None:
        assert "launch source" not in row.why and "package spec" not in row.why
    else:
        assert row.why.endswith(note)
        if "resolution" in new["launch_source"]:
            assert "mutable" not in row.why


def test_resolution_is_display_only() -> None:
    grant = _npx(["-y", "pkg"])
    assert grant["launch_source"] == {"pin": "mutable", "package": None, "resolution": LOCAL_PROJECT_OR_REGISTRY}
    stripped = {**grant, "launch_source": {"pin": "mutable", "package": None}}
    assert compared_grant(grant) == compared_grant(stripped)
    inventory = {
        "scope": "repository", "grants": [grant], "artifacts": [], "issues": [], "host_coverage": [],
    }
    assert host_grants_sha256(normalized_host_grants(inventory)) == host_grants_sha256(
        normalized_host_grants({**inventory, "grants": [stripped]})
    )
    assert "launch_source" not in json.dumps(build_host_grants_baseline(inventory))
    # The 0.9 grant model accepts it, and one without it publishes no key.
    assert HostMcpServerGrantV9.model_validate(grant).model_dump()["launch_source"]["resolution"] == LOCAL_PROJECT_OR_REGISTRY
    assert "resolution" not in HostMcpServerGrantV9.model_validate(_npx(["pkg@latest"])).model_dump()["launch_source"]


@pytest.mark.parametrize("dependency", [True, False], ids=["project-dependency", "no-dependency"])
def test_webiny_shape_reads_alike_whatever_the_project_declares(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dependency: bool
) -> None:
    """webiny-js#5760: no package.json or lockfile decides the row, on any route."""

    project = {"name": "root", "private": True}
    if dependency:
        project["dependencies"] = {"@webiny/stdlib": "6.0.0"}
    base: dict[str, object] = {"package.json": project}
    if dependency:
        base["yarn.lock"] = '"@webiny/stdlib@6.0.0":\n  version "6.0.0"\n'
    repo = _two(
        tmp_path,
        base,
        {".mcp.json": {"mcpServers": {"stdlib": {"command": "npx", "args": ["-y", "@webiny/stdlib", "serve"]}}}},
    )
    routes = _routes(repo, tmp_path)
    (row,) = routes["diff"]["rows"]
    assert row["why"] == f"an MCP tool surface the agent may call has changed; {UNVERSIONED}"
    assert row["direction"] == "added" and row["expands"] is True and row["severity"] == "high"
    assert routes["verify"]["rows"] == routes["diff"]["rows"]
    assert _whys(routes["check"]["rows"]) == _whys(routes["check_diff"]["rows"]) == [row["why"]]
    for printed in (routes["text"], routes["comment"]):
        assert UNVERSIONED in printed and "launch source is mutable" not in printed
    # `check` decides exactly as without the resolution fact.
    monkeypatch.setattr(
        "agents_shipgate.core.host_grants.launch_source_resolution", lambda *args: None
    )
    original = json.loads(_invoke(
        ["check", "--workspace", str(repo), "--base", "main", "--head", _git(repo, "rev-parse", "HEAD"),
         "--format", "agent-boundary-json"],
        exit_code=None,
    ))
    assert _whys(original["rows"]) == ["an MCP tool surface the agent may call has changed; launch source is mutable"]
    assert _decision(routes["check"]) == _decision(original)
