"""#821: a changed input the host entry does not read is named, never left silent.

Before this, a pull request that added a Cursor plugin's `mcp.json`, removed a
guard from `.cursor/hooks.json`, gave a dotfiles package `Bash(*)` or moved a
marketplace plugin's pinned `sha` printed "No static host-grant changes
detected", the same answer a docs-only change gets, and `verify` handed it to
the setup route, which says nothing about it. On a 23-PR public corpus none of
the nine comparable zero-row results that changed such a file named it.

Each of the seven reproduction fixtures from the issue is a real repository
driven through `diff` (text and `--json`), `verify` (`verifier.json` and its
text) and the PR comment `verify` writes. The item is named on every route,
never as a row, a widening or a `check` violation, and never as a claim that a
host loads the file. Negative controls, bounds, redaction, legacy readers and
the "not examined" path are pinned beside them.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core import unread_inputs
from agents_shipgate.core.host_comparison import _coverage_rank, compare_host_inventories
from agents_shipgate.core.host_grants import (
    build_host_boundary_snapshot,
    build_host_grants_baseline,
)
from agents_shipgate.core.unread_inputs import (
    MAX_UNREAD_CANDIDATES,
    ChangedInputs,
    discover_unread_inputs,
)
from agents_shipgate.report.host_comparison import (
    COVERAGE_BOUNDARY,
    COVERAGE_BOUNDARY_WITH_UNREAD,
    UNREAD_NOT_EXAMINED,
    coverage_lines,
    host_comparison_lines,
)
from agents_shipgate.schemas.host_comparison import (
    HostComparison,
    HostComparisonCoverage,
    HostComparisonCoverageItem,
)
from agents_shipgate.schemas.verifier import VerifierArtifact

ROOT = Path(__file__).resolve().parents[1]
HEADING = "What this run established:"
_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}
NOT_LOADED = "no row, and loading is not established"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=_GIT_ENV
    ).stdout.strip()


def _write(repo: Path, name: str, value: object) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")


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


def _verify(repo: Path, out: Path, *, head: bool = True) -> tuple[dict, str, str]:
    args = [
        "verify", "--workspace", str(repo), "--config", "shipgate.yaml", "--ci-mode", "advisory",
        "--out", str(out), "--format", "text", "--base", "main",
    ]
    if head:
        args += ["--head", _git(repo, "rev-parse", "HEAD")]
    text = _invoke(args)
    verifier = json.loads((out / "verifier.json").read_text(encoding="utf-8"))
    return verifier, (out / "pr-comment.md").read_text(encoding="utf-8"), text


def _unread(coverage: dict) -> list[tuple]:
    return [
        (item["source"], item["side"], item["candidate"], item["hosts"])
        for item in coverage["items"]
        if item["status"] == "changed_not_read"
    ]


# --- the seven reproduction fixtures from #821 --------------------------------

PIPX = {"mcpServers": {"demo": {"command": "pipx", "args": [
    "run", "--spec", "git+https://example.invalid/demo-mcp.git", "demo-mcp"]}}}
MARKETPLACE = {"name": "m", "owner": {"name": "x"}, "plugins": [
    {"name": "one", "source": {"source": "github", "repo": "example/one", "sha": "1" * 40}}]}
MOVED_PIN = {**MARKETPLACE, "plugins": [
    {"name": "one", "source": {"source": "github", "repo": "example/one", "sha": "2" * 40}}]}
CODEX_HOOKS = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "node ./hooks/send.js"}]}]}}
CODEX_HOOKS_EDITED = {"hooks": {"Stop": [
    {"hooks": [{"type": "command", "command": "node ./hooks/send.js || true"}]}]}}

FIXTURES = {
    "cursor-plugin-mcp": (
        {"plugins/demo/.cursor-plugin/plugin.json": {"name": "demo"}},
        {"plugins/demo/mcp.json": PIPX},
        ("plugins/demo/mcp.json", "head", "plugin_mcp_config", ["cursor"]),
        "plugins/demo/mcp.json (cursor): added, not read by this entry: "
        f"MCP configuration in a plugin directory; {NOT_LOADED}",
    ),
    "root-mcp-json": (
        {".claude-plugin/plugin.json": {"name": "demo"}},
        {"mcp.json": {"mcpServers": {"demo": {"url": "https://mcp.example.invalid/"}}}},
        ("mcp.json", "head", "plugin_mcp_config", ["claude-code"]),
        "mcp.json (claude-code): added, not read by this entry: "
        f"MCP configuration in a plugin directory; {NOT_LOADED}",
    ),
    "plugin-inline-mcp": (
        {".claude-plugin/plugin.json": {"name": "demo"}},
        {".claude-plugin/plugin.json": {"name": "demo", "mcpServers": {
            "demo": {"type": "http", "url": "https://mcp.example.invalid/mcp"}}}},
        (".claude-plugin/plugin.json#mcpServers", "head", "plugin_manifest_mcp_servers", ["claude-code"]),
        ".claude-plugin/plugin.json#mcpServers (claude-code): added, not read by this "
        f"entry: a plugin manifest's mcpServers; {NOT_LOADED}",
    ),
    "codex-plugin-hooks": (
        {
            "p/.codex-plugin/plugin.json": {"name": "p", "hooks": "./hooks/hooks.json"},
            "p/hooks/hooks.json": CODEX_HOOKS,
        },
        {"p/hooks/hooks.json": CODEX_HOOKS_EDITED},
        ("p/hooks/hooks.json", "both", "plugin_hook_file", ["codex"]),
        "p/hooks/hooks.json (codex): changed, not read by this entry: "
        f"a hook file a plugin manifest's hooks names; {NOT_LOADED}",
    ),
    "cursor-hooks-removed": (
        {".cursor/hooks.json": {"version": 1, "hooks": {
            "beforeShellExecution": [{"command": "./hooks/guard.sh"}]}}},
        {".cursor/hooks.json": {"version": 1, "hooks": {}}},
        (".cursor/hooks.json", "both", "cursor_project_hooks", ["cursor"]),
        f".cursor/hooks.json (cursor): changed, not read by this entry: Cursor project hooks; {NOT_LOADED}",
    ),
    "user-scope-package": (
        {"claude/.claude/settings.json": {"permissions": {"allow": ["Read"]}}},
        {"claude/.claude/settings.json": {
            "permissions": {"allow": ["Read", "Bash(*)"], "defaultMode": "bypassPermissions"}}},
        ("claude/.claude/settings.json", "both", "nested_host_settings", ["claude-code"]),
        "claude/.claude/settings.json (claude-code): changed, not read by this entry: host "
        "settings below the repository root, whose scope (a nested project or a user-scope "
        f"package) is not established; {NOT_LOADED}",
    ),
    "marketplace-external-pin": (
        {".claude-plugin/marketplace.json": MARKETPLACE},
        {".claude-plugin/marketplace.json": MOVED_PIN},
        (".claude-plugin/marketplace.json#plugins.one", "both", "external_plugin_source", ["claude-code"]),
        ".claude-plugin/marketplace.json#plugins.one (claude-code): changed, not read by this "
        f"entry: an external plugin source, now github example/one at {'2' * 40}; no row, and "
        "its content is not fetched",
    ),
}


@pytest.mark.parametrize("name", list(FIXTURES))
def test_each_reproduction_names_its_unread_input_on_every_route(tmp_path: Path, name: str) -> None:
    base, head, expected, line = FIXTURES[name]
    repo = _two(tmp_path, base, head)

    text, payload = _diff(repo)
    verifier, comment, verify_text = _verify(repo, tmp_path / "out")
    comparison = verifier["host_comparison"]

    # A named item, and nothing else moves: no row, no widening, no review.
    assert payload["capability_diff_schema_version"] == "0.4"
    assert payload["comparison_status"] == "comparable"
    assert payload["rows"] == [] and payload["review"]["summary"]["widenings"] == 0
    assert _unread(payload["coverage"]) == [expected]
    assert payload["coverage"]["read_sources_only"] is False
    assert payload["coverage"]["unread_candidates"] == "examined"
    assert "No static host-grant changes detected. No verdict is implied." in text
    assert f"  {line}" in text.splitlines()
    assert f"  {COVERAGE_BOUNDARY_WITH_UNREAD}" in text.splitlines()
    assert COVERAGE_BOUNDARY not in text

    # verify publishes the same object, and its text and the PR comment print it.
    assert verifier["verifier_schema_version"] == "0.21"
    assert comparison is not None, "the setup route would say nothing about this change"
    assert comparison["rows"] == [] and comparison["coverage"] == payload["coverage"]
    assert f"- {line}" in verify_text.splitlines()
    # The PR comment prints repository text as inline code: the path, and an
    # external source's description.
    source, rest = line.split(" (", 1)
    detail = f"github example/one at {'2' * 40}"
    assert f"- ` {source} ` ({rest.replace(detail, f'` {detail} `')}" in comment.splitlines()
    schema = json.loads((ROOT / "docs/verifier-schema.v0.21.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(verifier)


@pytest.mark.parametrize("name", list(FIXTURES))
def test_a_worktree_comparison_names_the_same_input(tmp_path: Path, name: str) -> None:
    """`verify` without `--head` reads the working tree's change set, as `diff` does."""

    base, head, expected, _line = FIXTURES[name]
    repo = _two(tmp_path, base, head)

    verifier, _comment, _text = _verify(repo, repo / "agents-shipgate-reports", head=False)

    assert _unread(verifier["host_comparison"]["coverage"]) == [expected]


def test_an_uncommitted_and_untracked_candidate_is_named(tmp_path: Path) -> None:
    repo = _two(tmp_path, {"plugins/demo/.cursor-plugin/plugin.json": {"name": "demo"}}, {})
    _write(repo, "plugins/demo/mcp.json", PIPX)  # untracked, never added

    _text, payload = _diff(repo)

    assert _unread(payload["coverage"]) == [
        ("plugins/demo/mcp.json", "head", "plugin_mcp_config", ["cursor"])
    ]


def test_nothing_is_fetched_or_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The external source and the MCP launch are text; a hook command is never run."""

    marker = tmp_path / "ran"
    repo = _two(
        tmp_path,
        {
            ".claude-plugin/marketplace.json": MARKETPLACE,
            "p/.codex-plugin/plugin.json": {"name": "p", "hooks": "./hooks/hooks.json"},
            "p/hooks/hooks.json": CODEX_HOOKS,
            "plugins/demo/.cursor-plugin/plugin.json": {"name": "demo"},
        },
        {
            ".claude-plugin/marketplace.json": MOVED_PIN,
            "p/hooks/hooks.json": {"hooks": {"Stop": [{"hooks": [
                {"type": "command", "command": f"touch {marker}"}]}]}},
            "plugins/demo/mcp.json": {"mcpServers": {"demo": {"command": "touch", "args": [str(marker)]}}},
        },
    )

    def refuse(*_args, **_kwargs):
        raise AssertionError("no network access")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)

    _text, payload = _diff(repo)
    _verify(repo, tmp_path / "out")

    assert {item[2] for item in _unread(payload["coverage"])} == {
        "external_plugin_source", "plugin_hook_file", "plugin_mcp_config",
    }
    assert not marker.exists()


def test_check_and_a_provided_diff_never_look_for_unread_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`check`'s result carries no coverage, so no candidate can become a violation there."""

    import agents_shipgate.core.host_comparison as comparator

    def refuse(*_args, **_kwargs):
        raise AssertionError("check looked for unread inputs")

    monkeypatch.setattr(comparator, "discover_unread_inputs", refuse)
    base, head, _expected, _line = FIXTURES["cursor-hooks-removed"]
    repo = _two(tmp_path, base, head)
    selection = ["--workspace", str(repo), "--base", "main", "--head", "HEAD"]

    payload = json.loads(
        _invoke(["check", *selection, "--format", "agent-boundary-json"], exit_code=None)
    )

    assert "changed_not_read" not in json.dumps(payload) and "coverage" not in payload
    from agents_shipgate.core.host_diff_comparison import compare_host_diff

    assert compare_host_diff(repo, _git(repo, "diff", "main", "HEAD") + "\n").coverage is None


# --- negative controls --------------------------------------------------------


def test_documentation_an_unrelated_json_and_an_unchanged_candidate_name_nothing(
    tmp_path: Path,
) -> None:
    repo = _two(
        tmp_path,
        {
            ".claude/settings.json": {"permissions": {"allow": ["Read"]}},
            ".claude-plugin/plugin.json": {"name": "demo"},
            "plugins/demo/.cursor-plugin/plugin.json": {"name": "demo"},
            "plugins/demo/mcp.json": {"mcpServers": {}},
            ".cursor/hooks.json": {"version": 1, "hooks": {}},
            "claude/.claude/settings.json": {"permissions": {"allow": ["Read"]}},
        },
        {
            "README.md": "# mcp.json, hooks.json and .claude/settings.json explained\n",
            "docs/mcp.json.md": "How mcp.json works.\n",
            "docs/hooks.json/notes.md": "Not a hooks file.\n",
            "config/other.json": {"mcpServers": {"x": {"command": "npx"}}},
            "package.json": {"name": "x"},
            # A plugin manifest edit that leaves `mcpServers` and `hooks` alone.
            ".claude-plugin/plugin.json": {"name": "demo", "version": "1.0.1"},
        },
    )

    text, payload = _diff(repo)
    verifier, comment, _verify_text = _verify(repo, tmp_path / "out")

    assert _unread(payload["coverage"]) == []
    assert payload["coverage"]["read_sources_only"] is True
    assert payload["coverage"]["unread_candidates"] == "examined"
    assert f"  {COVERAGE_BOUNDARY}" in text.splitlines()
    assert "not read by this entry" not in text + comment
    assert verifier["host_comparison"]["coverage"] == payload["coverage"]


def test_a_repository_that_touches_no_candidate_keeps_the_setup_route(tmp_path: Path) -> None:
    """Only a named unread input moves `verify` onto the host route; a docs change does not."""

    repo = _two(tmp_path, {"README.md": "# demo\n"}, {"README.md": "# demo\nmore\n"})
    args = [
        "verify", "--workspace", str(repo), "--config", "shipgate.yaml", "--ci-mode", "advisory",
        "--out", str(tmp_path / "out"), "--format", "json", "--base", "main",
    ]
    result = CliRunner().invoke(app, args, env={"NO_COLOR": "1"})
    verifier = json.loads((tmp_path / "out" / "verifier.json").read_text(encoding="utf-8"))

    assert result.exit_code == 2, result.output
    assert verifier["host_comparison"] is None


def test_a_file_a_reader_reads_is_its_own_item_and_never_unread(tmp_path: Path) -> None:
    """Root settings, `.cursor/mcp.json` and a hook file a Claude plugin selects are read."""

    repo = _two(
        tmp_path,
        {
            ".claude/settings.json": {"permissions": {"allow": ["Read"]}},
            ".cursor/mcp.json": {"mcpServers": {}},
            "plug/.claude-plugin/plugin.json": {"name": "plug"},
            "plug/hooks/hooks.json": CODEX_HOOKS,
        },
        {
            ".claude/settings.json": {"permissions": {"allow": ["Read", "Bash(*)"]}},
            ".cursor/mcp.json": {"mcpServers": {"x": {"command": "npx"}}},
            "plug/hooks/hooks.json": CODEX_HOOKS_EDITED,
        },
    )

    _text, payload = _diff(repo)
    sources = {item["source"] for item in payload["coverage"]["items"]}

    assert _unread(payload["coverage"]) == []
    assert {".claude/settings.json", ".cursor/mcp.json", "plug/hooks/hooks.json"} <= sources
    assert payload["rows"]


def test_a_member_is_named_even_where_a_reader_reads_the_file(tmp_path: Path) -> None:
    """A Claude Code manifest's `hooks` is read; its `mcpServers` is not, so it is named."""

    hooks = {"PreToolUse": [{"hooks": [{"type": "command", "command": "echo"}]}]}
    repo = _two(
        tmp_path,
        {"plug/.claude-plugin/plugin.json": {"name": "plug", "hooks": hooks}},
        {"plug/.claude-plugin/plugin.json": {
            "name": "plug", "hooks": hooks, "mcpServers": "./servers.json"}},
    )

    _text, payload = _diff(repo)
    statuses = {(item["source"], item["status"]) for item in payload["coverage"]["items"]}

    assert ("plug/.claude-plugin/plugin.json", "changed_without_rows") in statuses
    assert _unread(payload["coverage"]) == [
        ("plug/.claude-plugin/plugin.json#mcpServers", "head", "plugin_manifest_mcp_servers",
         ["claude-code"]),
    ]


@pytest.mark.parametrize(
    ("base", "head", "expected"),
    [
        (
            {"p/.codex-plugin/plugin.json": {"name": "p", "mcpServers": "./.mcp.json"}},
            {"p/.codex-plugin/plugin.json": {"name": "p"}},
            [("p/.codex-plugin/plugin.json#mcpServers", "base", "plugin_manifest_mcp_servers", ["codex"])],
        ),
        (
            {"p/.codex-plugin/plugin.json": {"name": "p", "hooks": "./hooks/a-hooks.json"}},
            {"p/.codex-plugin/plugin.json": {"name": "p", "hooks": "./hooks/b-hooks.json"}},
            [("p/.codex-plugin/plugin.json#hooks", "both", "plugin_manifest_hooks", ["codex"])],
        ),
        (
            {".github/plugin/plugin.json": {"name": "p"}},
            {".github/plugin/plugin.json": {"name": "p", "hooks": ["hooks.json"]}, "hooks.json": {}},
            [
                (".github/plugin/plugin.json#hooks", "head", "plugin_manifest_hooks", ["copilot"]),
                ("hooks.json", "head", "plugin_hook_file", ["copilot"]),
            ],
        ),
        (
            {"plugins/x/.cursor-plugin/plugin.json": {"name": "x"}},
            {"plugins/x/.cursor-plugin/plugin.json": "{not json"},
            [("plugins/x/.cursor-plugin/plugin.json", "both", "unparsed_plugin_manifest", ["cursor"])],
        ),
        (
            {".claude-plugin/marketplace.json": MARKETPLACE},
            {".claude-plugin/marketplace.json": {**MARKETPLACE, "plugins": []}},
            [(".claude-plugin/marketplace.json#plugins.one", "base", "external_plugin_source", ["claude-code"])],
        ),
        (
            {".claude-plugin/marketplace.json": MARKETPLACE},
            # Reordered keys and a new local entry: the external source's text is unchanged.
            {".claude-plugin/marketplace.json": {"plugins": [
                {"source": {"sha": "1" * 40, "repo": "example/one", "source": "github"}, "name": "one"},
                {"name": "two", "source": "./two"}], "owner": {"name": "x"}, "name": "m"}},
            [],
        ),
    ],
    ids=["mcp-servers-removed", "codex-hooks-retargeted", "copilot-hooks-added",
         "unparsed-cursor-manifest", "external-entry-removed", "external-source-unchanged"],
)
def test_each_member_rule_names_only_a_member_whose_text_differs(
    tmp_path: Path, base: dict, head: dict, expected: list
) -> None:
    repo = _two(tmp_path, base, head)

    _text, payload = _diff(repo)

    assert _unread(payload["coverage"]) == expected


def test_a_removed_external_source_names_what_it_was(tmp_path: Path) -> None:
    repo = _two(
        tmp_path,
        {".claude-plugin/marketplace.json": MARKETPLACE},
        {".claude-plugin/marketplace.json": {**MARKETPLACE, "plugins": []}},
    )

    text, _payload = _diff(repo)

    assert (
        "  .claude-plugin/marketplace.json#plugins.one (claude-code): removed, not read by "
        f"this entry: an external plugin source, was github example/one at {'1' * 40}; no row, "
        "and its content is not fetched"
    ) in text.splitlines()


def test_an_external_source_is_published_redacted(tmp_path: Path) -> None:
    token = "ghp_" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4J3i2"
    url = f"https://user:{token}@git.example.invalid/team/plugin.git"
    marketplace = {"name": "m", "owner": {"name": "x"}, "plugins": [
        {"name": "one", "source": {"source": "url", "url": url, "ref": "main"}}]}
    repo = _two(tmp_path, {".claude-plugin/marketplace.json": {**marketplace, "plugins": []}},
                {".claude-plugin/marketplace.json": marketplace})

    text, payload = _diff(repo)
    verifier, comment, verify_text = _verify(repo, tmp_path / "out")

    for output in (text, json.dumps(payload), json.dumps(verifier), comment, verify_text):
        assert token not in output and "user:" not in output
    (item,) = [item for item in payload["coverage"]["items"] if item["status"] == "changed_not_read"]
    # The URL is published only in the engine's sanitized form, as an MCP URL is.
    assert item["detail"] == "url <redacted-url> at main"


# --- bounds, ranking and the not-examined answer ------------------------------


def test_discovery_is_bounded_and_says_what_it_did_not_examine(tmp_path: Path) -> None:
    count = MAX_UNREAD_CANDIDATES + 3
    repo = _two(
        tmp_path,
        {},
        {f"pkg{index:02d}/.cursor/hooks.json": {"version": 1} for index in range(count)},
    )

    text, payload = _diff(repo)
    coverage = payload["coverage"]

    assert coverage["unread_candidates_not_examined"] == 3
    assert len(coverage["items"]) == 10
    assert coverage["omitted_items"] == MAX_UNREAD_CANDIDATES - 10
    assert "  3 more changed candidate inputs not examined, past the discovery bound" in text.splitlines()
    assert f"  {MAX_UNREAD_CANDIDATES - 10} more items not listed, each ranked below those above" in (
        text.splitlines()
    )


def _fake(paths, files: dict[str, dict[str, bytes]], calls: list | None = None) -> ChangedInputs:
    def present(side, wanted):
        if calls is not None:
            calls.append(("present", side, list(wanted)))
        return {path for path in wanted if path in files[side]}

    def read(side, wanted):
        if calls is not None:
            calls.append(("read", side, list(wanted)))
        return {path: files[side][path] for path in wanted if path in files[side]}

    return ChangedInputs(paths=tuple(paths), present=present, read=read)


def test_discovery_asks_one_presence_question_and_reads_only_manifests() -> None:
    calls: list = []
    manifest = b'{"name": "p", "hooks": "./hooks/hooks.json"}'
    cursor = "x/.cursor-plugin/plugin.json"
    files = {
        "base": {"p/.codex-plugin/plugin.json": manifest, "p/hooks/hooks.json": b"{}",
                 "notes/hooks.json": b"{}", cursor: b"{}"},
        "head": {"p/.codex-plugin/plugin.json": manifest, "p/hooks/hooks.json": b"{ }",
                 "notes/hooks.json": b"{ }", ".cursor/hooks.json": b"{}", cursor: b"{}",
                 "x/mcp.json": b"{}"},
    }

    result = discover_unread_inputs(
        _fake(
            ["p/hooks/hooks.json", "notes/hooks.json", ".cursor/hooks.json", "x/mcp.json", "README.md"],
            files,
            calls,
        ),
        read_by_entry=lambda _path: False,
    )

    assert [call[:2] for call in calls] == [
        ("present", "base"), ("present", "head"), ("read", "base"), ("read", "head"),
    ]
    # Only the manifest a hook file's reference could come from is read; the
    # one beside `mcp.json` is only asked about, and no candidate file itself is read.
    assert [call[2] for call in calls if call[0] == "read"] == [
        ["p/.codex-plugin/plugin.json"], ["p/.codex-plugin/plugin.json"],
    ]
    assert sorted((fact["source"], fact["candidate"]) for fact in result.facts) == [
        (".cursor/hooks.json", "cursor_project_hooks"),
        ("p/hooks/hooks.json", "plugin_hook_file"),
        ("x/mcp.json", "plugin_mcp_config"),
    ]


def test_a_manifest_that_could_not_be_read_is_counted_not_guessed() -> None:
    files = {
        "base": {"p/.codex-plugin/plugin.json": b"", "p/hooks/hooks.json": b"{}"},
        "head": {"p/.codex-plugin/plugin.json": b"", "p/hooks/hooks.json": b"{ }"},
    }

    def present(side, wanted):
        return {path for path in wanted if path in files[side]}

    result = discover_unread_inputs(
        # Present on both sides, but the read returns nothing: past its bound.
        ChangedInputs(paths=("p/hooks/hooks.json",), present=present, read=lambda _s, _p: {}),
        read_by_entry=lambda _path: False,
    )

    assert result.facts == [] and result.not_examined == 1


@pytest.mark.parametrize("failure", ["unlisted", "unreadable"])
def test_a_change_set_that_cannot_be_looked_at_is_said_to_be_not_examined(
    tmp_path: Path, failure: str
) -> None:
    from agents_shipgate.core.errors import ConfigError

    def broken(_side, _paths):
        raise ConfigError("listing failed")

    changed = (
        ChangedInputs(paths=None)
        if failure == "unlisted"
        else ChangedInputs(paths=(".cursor/hooks.json",), present=broken, read=broken)
    )
    empty = build_host_boundary_snapshot(tmp_path).inventory
    comparison = compare_host_inventories(
        empty, empty, head_kind="worktree", base_commit="a" * 40,
        head_commit="b" * 40, changed_inputs=changed,
    )

    assert comparison.coverage is not None
    assert comparison.coverage.unread_candidates == "not_examined"
    assert comparison.coverage.read_sources_only is True
    lines = coverage_lines(comparison, bullet="  ")
    assert lines[1:3] == [f"  {COVERAGE_BOUNDARY}", f"  {UNREAD_NOT_EXAMINED}"]


def test_an_unread_input_ranks_after_the_limits_and_before_every_other_item() -> None:
    def item(status: str, **extra) -> dict:
        return {"source": "a", "hosts": {"claude-code"}, "side": "both", "status": status,
                "rows": 0, **extra}

    ordered = [
        item("blocking_limit", limit="unsupported", source="z"),
        item("changed_not_read", candidate="cursor_project_hooks", source="y"),
        item("changed_without_rows", source="b"),
        item("compared", side="head"),
        item("unchanged_not_proven"),
        item("compared", rows=2),
        item("compared"),
    ]

    assert sorted(reversed(ordered), key=_coverage_rank) == ordered


def test_a_refused_comparison_names_its_limits_first_and_the_unread_input_beside_them(
    tmp_path: Path,
) -> None:
    repo = _two(
        tmp_path,
        {".mcp.json": {"mcpServers": {}}, ".cursor/hooks.json": {"version": 1, "hooks": {}}},
        {".mcp.json": "{not json", ".cursor/hooks.json": {"version": 1, "hooks": {"stop": []}}},
    )

    text, payload = _diff(repo)
    verifier, comment, _verify_text = _verify(repo, tmp_path / "out")

    assert payload["comparison_status"] == "incomparable"
    statuses = [item["status"] for item in payload["coverage"]["items"]]
    assert statuses == ["blocking_limit", "changed_not_read"]
    assert "  .cursor/hooks.json (cursor): changed, not read by this entry: Cursor project hooks; " \
        f"{NOT_LOADED}" in text.splitlines()
    assert verifier["host_comparison"]["coverage"] == payload["coverage"]
    assert "Cursor project hooks" in comment


# --- the published shape ------------------------------------------------------


def test_coverage_stays_out_of_digests_baselines_and_rows(tmp_path: Path) -> None:
    base, head, _expected, _line = FIXTURES["cursor-hooks-removed"]
    repo = _two(tmp_path, {**base, ".claude/settings.json": {"permissions": {"allow": ["Read"]}}}, head)
    inventory = build_host_boundary_snapshot(repo).inventory
    changed = ChangedInputs(
        paths=(".cursor/hooks.json",),
        present=lambda _side, paths: set(paths),
        read=lambda _side, _paths: {},
    )

    named = compare_host_inventories(
        inventory, inventory, head_kind="worktree", base_commit="a" * 40, head_commit="b" * 40,
        changed_inputs=changed,
    )
    plain = compare_host_inventories(
        inventory, inventory, head_kind="worktree", base_commit="a" * 40, head_commit="b" * 40,
    )

    assert not named.coverage.read_sources_only
    for field in ("rows", "review", "base_inventory_sha256", "head_inventory_sha256", "paths"):
        assert getattr(named, field) == getattr(plain, field), field
    assert "changed_not_read" not in json.dumps(build_host_grants_baseline(inventory))


@pytest.mark.parametrize(
    "item",
    [
        {"status": "changed_not_read"},
        {"status": "changed_not_read", "candidate": "cursor_project_hooks", "rows": 1},
        {"status": "changed_not_read", "candidate": "cursor_project_hooks", "limit": "unsupported"},
        {"status": "compared", "candidate": "cursor_project_hooks"},
        {"status": "blocking_limit", "limit": "unsupported", "candidate": "cursor_project_hooks"},
    ],
    ids=["no-candidate", "rows", "limit", "candidate-on-compared", "candidate-on-limit"],
)
def test_an_unread_item_keeps_its_shape(item: dict) -> None:
    with pytest.raises(ValueError):
        HostComparisonCoverageItem(source=".cursor/hooks.json", hosts=["cursor"], side="both", **item)


def test_the_list_says_whether_it_names_an_unread_input() -> None:
    unread = HostComparisonCoverageItem(
        source=".cursor/hooks.json", hosts=["cursor"], side="both", status="changed_not_read",
        candidate="cursor_project_hooks",
    )
    with pytest.raises(ValueError, match="says so"):
        HostComparisonCoverage(items=[unread], unread_candidates="examined")
    with pytest.raises(ValueError, match="read sources only"):
        HostComparisonCoverage(items=[], read_sources_only=False, unread_candidates="examined")
    with pytest.raises(ValueError, match="examined changed-file set"):
        HostComparisonCoverage(items=[unread], read_sources_only=False, unread_candidates="not_examined")
    with pytest.raises(ValueError, match="examined changed-file set"):
        HostComparisonCoverage(unread_candidates_not_examined=2)
    # A refused comparison may name one beside its limits; a comparable one no limit.
    HostComparison(
        comparison_status="incomparable", incomparable_reasons=["head_inventory_incomplete"],
        head_kind="worktree",
        coverage=HostComparisonCoverage(
            items=[unread], read_sources_only=False, unread_candidates="examined"
        ),
    )


def test_a_v0_20_verifier_reads_with_the_search_not_recorded(tmp_path: Path) -> None:
    base, head, _expected, _line = FIXTURES["cursor-hooks-removed"]
    repo = _two(tmp_path, {**base, ".claude/settings.json": {"permissions": {"allow": ["Read"]}}}, {
        **head, ".claude/settings.json": {"permissions": {"allow": ["Read", "Bash(*)"]}}})
    verifier, _comment, _text = _verify(repo, tmp_path / "out")

    legacy = json.loads(json.dumps(verifier))
    legacy["verifier_schema_version"] = "0.20"
    frozen = json.loads((ROOT / "docs/verifier-schema.v0.20.json").read_text(encoding="utf-8"))
    # A strict 0.20 reader rejects the new members: the reason the version moved.
    assert list(Draft202012Validator(frozen).iter_errors(legacy))
    with pytest.raises(ValueError, match="unread changed inputs"):
        VerifierArtifact.model_validate(legacy)

    coverage = legacy["host_comparison"]["coverage"]
    coverage["items"] = [
        {key: value for key, value in item.items() if key != "candidate"}
        for item in coverage["items"]
        if item["status"] != "changed_not_read"
    ]
    coverage["read_sources_only"] = True
    del coverage["unread_candidates"], coverage["unread_candidates_not_examined"]
    assert not list(Draft202012Validator(frozen).iter_errors(legacy))
    read = VerifierArtifact.model_validate(legacy)

    assert read.verifier_schema_version == "0.21"
    assert read.host_comparison.coverage.unread_candidates is None
    # Not recorded prints neither the unread boundary nor the not-examined line.
    lines = host_comparison_lines(read.host_comparison)
    assert f"- {COVERAGE_BOUNDARY}" in lines and f"- {UNREAD_NOT_EXAMINED}" not in lines


def test_the_candidate_rules_are_the_ones_documented() -> None:
    """The support page lists every rule the module applies, by its published name."""

    page = (ROOT / "docs/host-boundary-support.md").read_text(encoding="utf-8")
    from typing import get_args

    from agents_shipgate.schemas.host_comparison import UnreadCandidateKind

    for kind in get_args(UnreadCandidateKind):
        assert f"(`{kind}`)" in page, kind
    for suffix, _host in (*unread_inputs.PLUGIN_MANIFESTS, *unread_inputs.NESTED_HOST_SETTINGS):
        assert suffix in page, suffix
    assert f"At most {MAX_UNREAD_CANDIDATES} candidate paths" in page


def test_the_entry_pages_quote_what_diff_prints() -> None:
    """The quickstart's example line and boundary are the ones the fixture run prints."""

    def flat(path: str) -> str:
        return " ".join((ROOT / path).read_text(encoding="utf-8").split())

    quickstart = flat("docs/quickstart.md")
    assert FIXTURES["cursor-plugin-mcp"][3] in quickstart
    assert COVERAGE_BOUNDARY_WITH_UNREAD in quickstart
    assert FIXTURES["cursor-plugin-mcp"][3] in flat("STABILITY.md")
