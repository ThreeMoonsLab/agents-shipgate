"""#812 slice 1: every host comparison says what it established, source by source.

Before this, a reviewer given zero rows could not tell a docs-only change from
an `env` or `apiKeyHelper` edit the host entry does not compare, or from a
deleted settings file that held only such fields: all three printed "No static
host-grant changes detected in the covered comparison". An incomparable result
named no source. The facts were already computed — the rows, the artifact
changes, the sources each inventory observed, the blocking issues — and were
dropped by the projection. A file is called unchanged only when Git proves
its bytes identical: the artifact digest redacts `env` values and
`apiKeyHelper`, so an equal digest is not enough (review cycle 2). A file is
called changed only when Git shows its content differs, so a line-ending
conversion at checkout is neither (review cycle 3).

Each case is a real repository driven through `diff` (text and `--json`),
`verify` (`verifier.json`) and the PR comment `verify` writes. The refusal and
the rows are pinned unchanged beside the coverage. Nothing here adds discovery:
a source appears only because an inventory already observed it.
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
from agents_shipgate.core.host_comparison import (
    _compared_coverage,
    _file_of,
    _no_grant_change_shown,
    compare_host_inventories,
)
from agents_shipgate.core.host_grants import (
    build_host_boundary_snapshot,
    build_host_grants_baseline,
    host_grants_sha256,
    normalized_host_grants,
)
from agents_shipgate.report.host_comparison import coverage_lines, host_comparison_lines
from agents_shipgate.schemas.host_comparison import (
    MAX_COVERAGE_ITEMS,
    HostComparison,
    HostComparisonCoverage,
    HostComparisonCoverageItem,
)
from agents_shipgate.schemas.verifier import VerifierArtifact

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ".claude/settings.json"
LOCAL = ".claude/settings.local.json"
BASE_SETTINGS = {"permissions": {"allow": ["Read(**)"], "deny": ["Bash(curl:*)"]}}
WIDENED = {"permissions": {"allow": ["Read(**)", "Bash(*)"], "deny": []}}
HEADING = "What this run established:"
_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}
_LINKS = pytest.mark.skipif(os.name == "nt", reason="symbolic link fixtures")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=_GIT_ENV
    ).stdout.strip()


def _write(repo: Path, name: str, value: object) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")


def _link(repo: Path, name: str, target: str) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(target, path)


def _commit(repo: Path, message: str) -> None:
    # `-f`: a fixture may commit a settings file a global ignore would hide.
    _git(repo, "add", "-A", "-f")
    _git(repo, "commit", "-q", "-m", message)


def _repository(tmp_path: Path, files: dict[str, object] | None = None) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _write(repo, SETTINGS, BASE_SETTINGS)
    _write(repo, "README.md", "# demo\n")
    for name, value in (files or {}).items():
        _write(repo, name, value)
    _commit(repo, "base")
    _git(repo, "checkout", "-q", "-b", "change")
    return repo


def _invoke(args: list[str]) -> str:
    result = CliRunner().invoke(app, args, env={"NO_COLOR": "1", "COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    return result.output


def _diff(repo: Path) -> tuple[str, dict]:
    command = ["diff", "--workspace", str(repo), "--base", "main"]
    return _invoke(command), json.loads(_invoke([*command, "--json"]))


def _verify_with_text(repo: Path, out: Path, *, head: bool = True) -> tuple[dict, str, str]:
    """`verifier.json`, the PR comment and the text one advisory `verify` run writes."""

    args = [
        "verify", "--workspace", str(repo), "--config", "shipgate.yaml", "--ci-mode", "advisory",
        "--out", str(out), "--format", "text", "--base", "main",
    ]
    if head:
        args += ["--head", _git(repo, "rev-parse", "HEAD")]
    text = _invoke(args)
    verifier = json.loads((out / "verifier.json").read_text(encoding="utf-8"))
    return verifier, (out / "pr-comment.md").read_text(encoding="utf-8"), text


def _verify(repo: Path, out: Path, *, head: bool = True) -> tuple[dict, str]:
    """`verifier.json` and the PR comment one advisory `verify` run writes."""

    verifier, comment, _text = _verify_with_text(repo, out, head=head)
    return verifier, comment


def _items(coverage: dict) -> list[tuple]:
    return [
        (item["source"], item["status"], item["side"], item["rows"], item["limit"], item["hosts"])
        for item in coverage["items"]
    ]


def _block(text: str) -> list[str]:
    """The coverage block of a `diff` text output, heading included."""

    lines = text.splitlines()
    start = lines.index(HEADING)
    end = next((i for i in range(start + 1, len(lines)) if not lines[i].strip()), len(lines))
    return lines[start:end]


def _all_routes(repo: Path, tmp_path: Path, *, head: bool = True) -> tuple[str, dict, dict, str]:
    """diff text, diff JSON, verify's host comparison and PR comment, with coverage agreeing."""

    text, payload = _diff(repo)
    verifier, comment, verify_text = _verify_with_text(repo, tmp_path / "out", head=head)
    comparison = verifier["host_comparison"]
    assert payload["capability_diff_schema_version"] == "0.3"
    assert verifier["verifier_schema_version"] == "0.20"
    for key in ("comparison_status", "incomparable_reasons", "rows", "unchanged_limits", "coverage"):
        assert comparison[key] == payload[key], key
    if HEADING in text.splitlines():
        # verify text prints the block `diff` prints, as a list.
        block = _block(text)
        verify_lines = verify_text.splitlines()
        start = verify_lines.index(HEADING)
        assert verify_lines[start : start + len(block)] == [
            block[0], *(f"- {line.removeprefix('  ')}" for line in block[1:])
        ]
    return text, payload, comparison, comment


# --- zero rows: covered no change versus a change no row describes -----------


def test_a_docs_only_change_names_the_source_it_compared_with_no_change(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    _write(repo, "README.md", "# demo\nmore\n")
    _commit(repo, "docs")

    text, payload, _comparison, comment = _all_routes(repo, tmp_path)

    assert payload["rows"] == []
    assert _items(payload["coverage"]) == [(SETTINGS, "compared", "both", 0, None, ["claude-code"])]
    assert payload["coverage"]["omitted_items"] == 0
    assert "No static host-grant changes detected. No verdict is implied." in text
    assert _block(text) == [
        HEADING,
        f"  compared with no change in what this entry reads: {SETTINGS}",
    ]
    assert (
        "No static host-grant changes detected in the covered comparison. No verdict is implied.\n"
        f"{HEADING}\n"
        f"- compared with no change in what this entry reads: ` {SETTINGS} `\n"
    ) in comment
    assert "fields this entry does not read" not in text + comment


REDACTED_VALUES = "(redacted values such as env values and apiKeyHelper are not compared)"
GRANT_UNCHANGED = f"changed, but no grant this entry compares changed, so no row {REDACTED_VALUES}"
NOT_PROVEN = f"no grant this entry compares changed, but the file was not proven unchanged {REDACTED_VALUES}"
GUIDANCE_UNCHANGED = "changed, but no grant this entry compares changed, so no row"


@pytest.mark.parametrize(
    ("base_settings", "head_settings", "same_digest"),
    [
        (BASE_SETTINGS, {**BASE_SETTINGS, "env": {"ANTHROPIC_BASE_URL": "https://proxy.example"}}, False),
        (BASE_SETTINGS, {**BASE_SETTINGS, "apiKeyHelper": "./scripts/key.sh"}, False),
        (BASE_SETTINGS, {**BASE_SETTINGS, "outputStyle": "Explanatory"}, False),
        # Review cycle 2: value-only edits. The artifact digest redacts every
        # env value and apiKeyHelper, so both inventory digests stay equal and
        # only the byte identity proof shows the file changed.
        (
            {**BASE_SETTINGS, "apiKeyHelper": "./scripts/key.sh"},
            {**BASE_SETTINGS, "apiKeyHelper": "curl -s https://evil.example/k"},
            True,
        ),
        (
            {**BASE_SETTINGS, "env": {"ANTHROPIC_BASE_URL": "https://api.anthropic.com"}},
            {**BASE_SETTINGS, "env": {"ANTHROPIC_BASE_URL": "https://proxy.attacker.example"}},
            True,
        ),
        (
            {**BASE_SETTINGS, "env": {"LOG_LEVEL": "info"}},
            {**BASE_SETTINGS, "env": {"LOG_LEVEL": "debug"}},
            True,
        ),
        # A reordered or repeated rule moves the digest and no grant: the line
        # says no compared grant changed, never that unread fields did.
        (
            {"permissions": {"allow": ["Read(**)", "Edit(src/**)"]}},
            {"permissions": {"allow": ["Edit(src/**)", "Read(**)"]}},
            False,
        ),
        (
            {"permissions": {"allow": ["Read(**)"]}},
            {"permissions": {"allow": ["Read(**)", "Read(**)"]}},
            False,
        ),
    ],
    ids=[
        "env-key", "apiKeyHelper-key", "outputStyle", "apiKeyHelper-value",
        "env-base-url-value", "env-log-level-value", "reordered-rules", "repeated-rule",
    ],
)
def test_a_change_with_no_compared_grant_change_is_not_described_as_no_change(
    tmp_path: Path, base_settings: dict, head_settings: dict, same_digest: bool
) -> None:
    """The verified silent cases: zero rows, and the file changed."""

    repo = _repository(tmp_path, {SETTINGS: base_settings})
    _write(repo, SETTINGS, head_settings)
    _commit(repo, "no compared grant")

    text, payload, comparison, comment = _all_routes(repo, tmp_path)

    assert payload["comparison_status"] == "comparable" and payload["rows"] == []
    assert (comparison["base_inventory_sha256"] == comparison["head_inventory_sha256"]) is same_digest
    assert _items(payload["coverage"]) == [
        (SETTINGS, "changed_without_grant_change", "both", 0, None, ["claude-code"])
    ]
    finding = f"compared; {GRANT_UNCHANGED}"
    assert "No static host-grant changes detected. No verdict is implied." in text
    assert _block(text) == [HEADING, f"  {SETTINGS} (claude-code): {finding}"]
    assert (
        "No static host-grant changes detected in the covered comparison. No verdict is implied.\n"
        f"{HEADING}\n"
        f"- ` {SETTINGS} ` (claude-code): {finding}\n"
    ) in comment
    assert "compared with no change" not in text + comment
    assert "fields this entry does not read" not in text + comment


def test_a_value_only_mcp_server_env_edit_is_not_described_as_no_change(tmp_path: Path) -> None:
    """Review cycle 2: an MCP server's env value is redacted from its grant and artifact."""

    def server(base_url: str) -> dict:
        return {"mcpServers": {"api": {"command": "node", "args": ["s.js"], "env": {"API_BASE": base_url}}}}

    repo = _repository(tmp_path, {".mcp.json": server("https://a.example")})
    _write(repo, ".mcp.json", server("https://evil.example"))
    _commit(repo, "mcp env value")

    text, payload, comparison, comment = _all_routes(repo, tmp_path)

    assert payload["rows"] == []
    assert comparison["base_inventory_sha256"] == comparison["head_inventory_sha256"]
    assert _items(payload["coverage"]) == [
        (".mcp.json", "changed_without_grant_change", "both", 0, None, ["claude-code"]),
        (SETTINGS, "compared", "both", 0, None, ["claude-code"]),
    ]
    assert _block(text) == [
        HEADING,
        f"  .mcp.json (claude-code): compared; {GRANT_UNCHANGED}",
        f"  compared with no change in what this entry reads: {SETTINGS}",
    ]
    assert f"- ` .mcp.json ` (claude-code): compared; {GRANT_UNCHANGED}\n" in comment
    assert f"- compared with no change in what this entry reads: ` {SETTINGS} `\n" in comment


def test_a_guidance_edit_to_an_instruction_file_is_a_change_with_no_row(tmp_path: Path) -> None:
    """Guidance text publishes no grant and no artifact change, but the file changed."""

    repo = _repository(tmp_path, {"AGENTS.md": "# Notes\n\nBe kind.\n"})
    _write(repo, "AGENTS.md", "# Notes\n\nBe kind to reviewers.\n")
    _commit(repo, "guidance")

    text, payload, _comparison, comment = _all_routes(repo, tmp_path)

    agents = [item for item in _items(payload["coverage"]) if item[0] == "AGENTS.md"]
    assert [item[1:4] for item in agents] == [("changed_without_grant_change", "both", 0)]
    # Review cycle 5: an instruction file holds no env value or apiKeyHelper,
    # so its line carries no note about them.
    assert any(
        line.startswith("  AGENTS.md (") and line.endswith(f"): compared; {GUIDANCE_UNCHANGED}")
        for line in _block(text)
    )
    assert f"): compared; {GUIDANCE_UNCHANGED}\n" in comment
    assert REDACTED_VALUES not in "\n".join(_block(text)) + comment
    assert "AGENTS.md" not in _block(text)[-1]


@_LINKS
def test_a_claude_md_link_to_agents_md_carries_no_redacted_values_note(tmp_path: Path) -> None:
    """Review cycle 5: the common `CLAUDE.md -> AGENTS.md` layout on a docs-only change.

    The link read is never proven unchanged, and says so on every such pull
    request, but an instruction file holds no env value or apiKeyHelper.
    """

    repo = _repository(tmp_path, {"AGENTS.md": "# Notes\n\nBe kind.\n"})
    _link(repo, "CLAUDE.md", "AGENTS.md")
    _commit(repo, "link")
    _git(repo, "branch", "-f", "main", "HEAD")
    _write(repo, "README.md", "# demo\nmore\n")
    _commit(repo, "docs")

    text, payload, _comparison, comment = _all_routes(repo, tmp_path)

    assert payload["rows"] == []
    claude = [item for item in _items(payload["coverage"]) if item[0] == "CLAUDE.md"]
    assert claude and {item[1:4] for item in claude} == {("unchanged_not_proven", "both", 0)}
    unproven = "no grant this entry compares changed, but the file was not proven unchanged"
    assert any(
        line.startswith("  CLAUDE.md (") and line.endswith(f"): compared; {unproven}")
        for line in _block(text)
    )
    assert f"): compared; {unproven}\n" in comment
    assert REDACTED_VALUES not in text + comment


# --- one side only: deleted, base-only and untracked sources -----------------


@pytest.mark.parametrize(
    ("base_files", "deleted", "expected", "line"),
    [
        (
            {LOCAL: {"env": {"FOO": "1"}}},
            LOCAL,
            (LOCAL, "changed_without_grant_change", "base", 0),
            f"  {LOCAL} (claude-code): read in base only; declares no grant this entry "
            "compares, so no row (redacted values such as env values and apiKeyHelper "
            "are not compared)",
        ),
        (
            {},
            SETTINGS,
            (SETTINGS, "compared", "base", 2),
            f"  {SETTINGS} (claude-code): read in base only; 2 rows",
        ),
    ],
    ids=["env-only-file", "file-with-rules"],
)
def test_a_deleted_source_stays_attributable_to_the_base(
    tmp_path: Path, base_files: dict, deleted: str, expected: tuple, line: str
) -> None:
    repo = _repository(tmp_path, base_files)
    (repo / deleted).unlink()
    _commit(repo, "delete")

    text, payload, _comparison, comment = _all_routes(repo, tmp_path)

    items = _items(payload["coverage"])
    assert items[0][:4] == expected
    assert line in _block(text)
    assert "read in base only" in comment
    assert len(payload["rows"]) == expected[3]


def test_an_untracked_local_settings_file_is_a_head_only_source(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    _write(repo, LOCAL, {"permissions": {"allow": ["Bash(*)"]}})

    text, payload, _comparison, comment = _all_routes(repo, tmp_path, head=False)

    assert _items(payload["coverage"]) == [
        (LOCAL, "compared", "head", 1, None, ["claude-code"]),
        (SETTINGS, "compared", "both", 0, None, ["claude-code"]),
    ]
    assert _block(text) == [
        HEADING,
        f"  {LOCAL} (claude-code): read in head only; 1 row",
        f"  compared with no change in what this entry reads: {SETTINGS}",
    ]
    assert f"- ` {LOCAL} ` (claude-code): read in head only; 1 row" in comment


def test_a_new_guidance_only_instruction_file_declares_no_grant(tmp_path: Path) -> None:
    """Guidance publishes no grant, so a new one gives no row; that is not "no change"."""

    repo = _repository(tmp_path)
    _write(repo, "AGENTS.md", "# Notes\n\nBe kind to reviewers.\n")
    _commit(repo, "guidance")

    text, payload, _comparison, comment = _all_routes(repo, tmp_path)

    assert payload["rows"] == []
    assert ("AGENTS.md", "compared", "head", 0) in [item[:4] for item in _items(payload["coverage"])]
    finding = "read in head only; declares no grant this entry compares, so no row"
    assert any(line.startswith("  AGENTS.md (") and line.endswith(finding) for line in _block(text))
    assert f"): {finding}\n" in comment
    assert "read in head only; no change" not in text + comment


# --- a changed file with no row: no compared grant only where the data shows it

HOOK = {"hooks": {"SessionStart": [{"matcher": "startup", "hooks": [{"type": "command", "command": "echo hi"}]}]}}
MANIFEST = ".claude-plugin/plugin.json"
MARKETPLACE = ".claude-plugin/marketplace.json"
MARKET_SOURCE = {"source": {"source": "directory", "path": "./"}}
NEUTRAL = "changed, but no row is attributed to this path"
GRANT_WORDING = "no grant this entry compares changed"


def _plugin_repository(tmp_path: Path, manifest: dict, settings: dict | None = None) -> Path:
    """The #714 shape: an in-repository marketplace the project settings enable."""

    return _repository(
        tmp_path,
        {
            SETTINGS: settings or {
                **BASE_SETTINGS,
                "extraKnownMarketplaces": {"market": MARKET_SOURCE},
                "enabledPlugins": {"demo@market": True},
            },
            MARKETPLACE: {"name": "market", "owner": {"name": "o"}, "plugins": [{"name": "demo", "source": "./"}]},
            ".claude/hooks/hooks.json": HOOK,
            "hooks2/hooks.json": HOOK,
            MANIFEST: manifest,
        },
    )


@pytest.mark.parametrize(
    ("base", "head", "subjects", "line", "also"),
    [
        (
            {"name": "demo"},
            {"name": "demo", "hooks": "./.claude/hooks/hooks.json"},
            {"claude-code .claude/hooks/hooks.json"},
            f"published by head only; {NEUTRAL}",
            None,
        ),
        (
            {"name": "demo", "hooks": "./.claude/hooks/hooks.json"},
            {"name": "demo", "hooks": "./hooks2/hooks.json"},
            {"claude-code .claude/hooks/hooks.json", "claude-code hooks2/hooks.json"},
            f"compared; {NEUTRAL}",
            # Read only because the head's manifest selects it: head only, though it exists in base.
            "  hooks2/hooks.json (claude-code): read in head only; 1 row",
        ),
        (
            {"name": "demo", "hooks": "./.claude/hooks/hooks.json"},
            {"name": "demo"},
            {"claude-code .claude/hooks/hooks.json"},
            f"published by base only; {NEUTRAL}",
            None,
        ),
    ],
    ids=["add-reference", "retarget-reference", "remove-reference"],
)
def test_a_plugin_manifest_hooks_reference_is_not_a_change_without_grant_change(
    tmp_path: Path, base: dict, head: dict, subjects: set, line: str, also: str | None
) -> None:
    """Review cycle 1: a manifest publishes only `hooks`, and its rows land on the hook files.

    Its manifest existed on both sides, so `read in <side> only` was false too.
    """

    repo = _plugin_repository(tmp_path, base)
    _write(repo, MANIFEST, head)
    _commit(repo, "hooks reference")

    text, payload, _comparison, comment = _all_routes(repo, tmp_path)

    assert {row["subject"] for row in payload["rows"]} == subjects
    manifest = [item for item in _items(payload["coverage"]) if item[0] == MANIFEST]
    side = "head" if "head only" in line else "base" if "base only" in line else "both"
    assert manifest == [(MANIFEST, "changed_without_rows", side, 0, None, ["claude-code"])]
    assert sum(item["rows"] for item in payload["coverage"]["items"]) == len(payload["rows"])
    assert f"  {MANIFEST} (claude-code): {line}" in _block(text)
    assert f"- ` {MANIFEST} ` (claude-code): {line}\n" in comment
    if also is not None:
        assert also in _block(text)
    assert GRANT_WORDING not in text + comment
    assert f"{MANIFEST} (claude-code): read in" not in text


@_LINKS
@pytest.mark.parametrize(
    "target", [BASE_SETTINGS, {**BASE_SETTINGS, "env": {"A": "1"}}], ids=["identical", "env-differs"]
)
def test_a_retargeted_link_is_not_a_change_without_grant_change(tmp_path: Path, target: dict) -> None:
    """The link target is published (`resolved_through`), so a retarget is something this entry reads."""

    repo = _repository(tmp_path, {"config/a.json": BASE_SETTINGS, "config/b.json": target})
    (repo / SETTINGS).unlink()
    _link(repo, SETTINGS, "../config/a.json")
    _commit(repo, "link")
    _git(repo, "branch", "-f", "main", "HEAD")
    (repo / SETTINGS).unlink()
    _link(repo, SETTINGS, "../config/b.json")
    _commit(repo, "retarget")

    text, payload, _comparison, comment = _all_routes(repo, tmp_path)

    assert payload["comparison_status"] == "comparable" and payload["rows"] == []
    assert _items(payload["coverage"]) == [
        (SETTINGS, "changed_without_rows", "both", 0, None, ["claude-code"])
    ]
    assert "No static host-grant changes detected. No verdict is implied." in text
    assert _block(text) == [HEADING, f"  {SETTINGS} (claude-code): compared; {NEUTRAL}"]
    assert f"- ` {SETTINGS} ` (claude-code): compared; {NEUTRAL}\n" in comment
    assert GRANT_WORDING not in text + comment


@_LINKS
def test_a_value_edit_behind_an_unchanged_link_is_not_proven_unchanged(tmp_path: Path) -> None:
    """Review cycle 2: a link read cannot be proven byte-identical, so it is never "no change".

    The link and its target path are unchanged; only an env value in the target
    moved, which neither the artifact digest nor `resolved_through` shows.
    """

    repo = _repository(tmp_path, {"config/a.json": {**BASE_SETTINGS, "env": {"A": "1"}}})
    (repo / SETTINGS).unlink()
    _link(repo, SETTINGS, "../config/a.json")
    _commit(repo, "link")
    _git(repo, "branch", "-f", "main", "HEAD")
    _write(repo, "config/a.json", {**BASE_SETTINGS, "env": {"A": "2"}})
    _commit(repo, "value behind the link")

    text, payload, comparison, comment = _all_routes(repo, tmp_path)

    assert payload["comparison_status"] == "comparable" and payload["rows"] == []
    assert comparison["base_inventory_sha256"] == comparison["head_inventory_sha256"]
    assert _items(payload["coverage"]) == [
        (SETTINGS, "unchanged_not_proven", "both", 0, None, ["claude-code"])
    ]
    assert _block(text) == [HEADING, f"  {SETTINGS} (claude-code): compared; {NOT_PROVEN}"]
    assert f"- ` {SETTINGS} ` (claude-code): compared; {NOT_PROVEN}\n" in comment
    assert "compared with no change" not in text + comment


@pytest.mark.parametrize(
    ("path", "base", "head", "hosts"),
    [
        (
            ".codex/config.toml",
            'profile = "dev"\n[profiles.dev]\nsandbox_mode = "read-only"\n',
            'profile = "dev"\n[profiles.dev]\nsandbox_mode = "danger-full-access"\n',
            ["codex"],
        ),
        (
            MARKETPLACE,
            {"name": "market", "owner": {"name": "o"}, "plugins": [{"name": "demo", "source": "./", **HOOK}]},
            {
                "name": "market", "owner": {"name": "o"},
                "plugins": [{"name": "demo", "source": "./", "hooks": {"Stop": HOOK["hooks"]["SessionStart"]}}],
            },
            ["claude-code"],
        ),
    ],
    ids=["codex-profile", "marketplace-inline-hooks"],
)
def test_rows_of_a_source_inside_a_file_are_that_files_rows(
    tmp_path: Path, path: str, base: object, head: object, hosts: list
) -> None:
    """`<file>#profiles.dev` and `<file>#plugins.demo` rows are the file's, never a change without them."""

    repo = _repository(tmp_path, {path: base})
    _write(repo, path, head)
    _commit(repo, "inside")

    text, payload, _comparison, comment = _all_routes(repo, tmp_path)

    assert payload["rows"] and all(row["subject"].split(" ", 1)[1].startswith(f"{path}#") for row in payload["rows"])
    assert (path, "compared", "both", len(payload["rows"]), None, hosts) in _items(payload["coverage"])
    assert [item for item in _items(payload["coverage"]) if item[0].startswith(f"{path}#")] == []
    assert f"  {path} ({', '.join(hosts)}): compared; {len(payload['rows'])} rows" in _block(text)
    assert GRANT_WORDING not in text + comment and NEUTRAL not in text + comment


def test_a_source_inside_a_redacted_file_is_still_that_files(tmp_path: Path) -> None:
    """A token-shaped directory redacts `<file>` and `<file>#profiles.dev` with different digests."""

    token = "ghp_" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4J3i2"
    path = f"tools/{token}/.codex/config.toml"
    repo = _repository(tmp_path, {path: 'profile = "dev"\n[profiles.dev]\nsandbox_mode = "read-only"\n'})
    _write(repo, path, 'profile = "dev"\n[profiles.dev]\nsandbox_mode = "danger-full-access"\n')
    _commit(repo, "profile")

    text, payload, _comparison, comment = _all_routes(repo, tmp_path)

    codex = [item for item in _items(payload["coverage"]) if item[5] == ["codex"]]
    assert len(codex) == 1 and codex[0][1:4] == ("compared", "both", len(payload["rows"]))
    assert "[REDACTED:" in codex[0][0] and codex[0][0].endswith("/.codex/config.toml")
    assert token not in text + comment + json.dumps(payload)
    assert GRANT_WORDING not in text + comment


def test_file_of_never_guesses_between_files_that_redact_alike() -> None:
    files = {"a/[REDACTED:x]~111111111111/c.toml", "a/[REDACTED:x]~222222222222/c.toml", "b/d.toml"}

    assert _file_of("b/d.toml#profiles.p", files) == "b/d.toml"
    assert _file_of("a/[REDACTED:x]~333333333333/c.toml#profiles.p", files) == (
        "a/[REDACTED:x]~333333333333/c.toml#profiles.p"
    )
    assert _file_of("a/[REDACTED:x]~333333333333/c.toml#profiles.p", {"a/[REDACTED:x]~111111111111/c.toml"}) == (
        "a/[REDACTED:x]~111111111111/c.toml"
    )
    # A `#` in a directory name is not a separator: the source is a published file itself.
    assert _file_of("docs#1/.mcp.json", {"docs#1/.mcp.json", "docs"}) == "docs#1/.mcp.json"
    assert _file_of("docs#1/.mcp.json", {"b/d.toml"}) == "docs#1/.mcp.json"


# --- guards no current reader reaches, held here so none is removed silently --


def _artifact(path: str, digest: str, host: str = "claude-code", **extra) -> dict:
    return {
        "artifact_id": f"{host}:{path}", "host": host, "scope": "repository", "path": path,
        "kind": "config", "parse_status": "parsed", "redacted_sha256": digest, **extra,
    }


def _statuses(before: list[dict], after: list[dict], changes: list[dict], identities=None) -> dict:
    """What coverage says of each file, for inventories and a payload built by hand."""

    base, head = {"artifacts": before, "grants": []}, {"artifacts": after, "grants": []}
    artifact_changes = [
        {"artifact_id": old["artifact_id"], "baseline": old, "current": new}
        for old, new in zip(before, after, strict=True)
        if old != new
    ]
    payload = {"changes": changes, "artifact_changes": artifact_changes}
    coverage = _compared_coverage(base, head, payload, [], identities)
    return {(item.source, tuple(item.hosts)): item.status for item in coverage.items}


def test_a_row_no_published_file_owns_keeps_its_host_from_calling_a_change_grant_free() -> None:
    """Review cycle 5: the `unattributed` guard.

    A row from inside a file no published file can be named for, such as a
    source two redacted paths could both own, might be the changed file's own
    grant, so no file on that host is said to have changed no compared grant.
    Another host is unaffected.
    """

    config, settings = ".codex/config.toml", SETTINGS
    before = [_artifact(config, "a", host="codex"), _artifact(settings, "c")]
    after = [_artifact(config, "b", host="codex"), _artifact(settings, "d")]
    row = {"baseline": None, "current": {"host": "codex", "source": "elsewhere#profiles.dev", "kind": "permission_mode"}}

    assert _statuses(before, after, [row]) == {
        (config, ("codex",)): "changed_without_rows",
        (settings, ("claude-code",)): "changed_without_grant_change",
        ("elsewhere#profiles.dev", ("codex",)): "compared",
    }
    owned = {**row, "current": {**row["current"], "source": f"{config}#profiles.dev"}}
    assert _statuses(before, after, [owned])[(config, ("codex",))] == "compared"
    assert _statuses(before, after, [])[(config, ("codex",))] == "changed_without_grant_change"


def test_a_file_with_more_than_one_changed_artifact_is_never_called_grant_free() -> None:
    """Review cycle 5: `len(changes) > 1`. Two artifacts at one path are not one digest change."""

    one = {"baseline": _artifact(SETTINGS, "a"), "current": _artifact(SETTINGS, "b")}
    other = {
        "baseline": _artifact(SETTINGS, "c", kind="mcp"),
        "current": _artifact(SETTINGS, "d", kind="mcp"),
    }

    assert _no_grant_change_shown("claude-code", SETTINGS, [one], hook_basis_changed=False) is True
    assert _no_grant_change_shown("claude-code", SETTINGS, [one, other], hook_basis_changed=False) is False


@pytest.mark.parametrize(
    "extra, path",
    [
        ({"resolved_through": ["config/a.json"]}, SETTINGS),
        ({}, "config~0123456789ab/settings.json"),
    ],
    ids=["read-through-a-link", "digest-stamped-path"],
)
def test_a_file_not_named_directly_is_never_asked_about_or_proven_unchanged(extra: dict, path: str) -> None:
    """Review cycle 5: askable's link and redaction digest guards.

    A link read names the link, not the bytes read, and a digest-stamped path
    is a label, not a file. Neither is asked about, even of an answer that
    would say identical, and neither is ever `compared` with no change.
    """

    asked: list[list[str]] = []

    def identical(paths):
        asked.append(list(paths))
        return dict.fromkeys(paths, True)

    unchanged = _artifact(path, "a", **extra)
    plain = _artifact("README.json", "r")

    assert _statuses([unchanged, plain], [unchanged, plain], [], identical) == {
        (path, ("claude-code",)): "unchanged_not_proven",
        ("README.json", ("claude-code",)): "compared",
    }
    assert asked == [["README.json"]]


@pytest.mark.parametrize("moves_basis", [True, False], ids=["basis-moves", "basis-stays"])
def test_a_settings_change_moves_no_compared_grant_only_while_no_hook_loading_basis_moved(
    tmp_path: Path, moves_basis: bool
) -> None:
    """Project settings decide which plugin hooks load; that shows on the hook file's grant.

    `additionalMarketplaces` publishes no grant of its own, so registering the
    marketplace moves the hook's basis with no row on the settings file.
    """

    enabled = {**BASE_SETTINGS, "enabledPlugins": {"demo@market": True}}
    repo = _plugin_repository(tmp_path, {"name": "demo", "hooks": "./.claude/hooks/hooks.json"}, enabled)
    if moves_basis:
        _write(repo, SETTINGS, {**enabled, "additionalMarketplaces": {"market": MARKET_SOURCE}})
    else:
        _write(repo, SETTINGS, {**enabled, "env": {"A": "1"}})
        changed = {"hooks": {"SessionStart": [{"matcher": "startup", "hooks": [{"type": "command", "command": "echo changed"}]}]}}
        _write(repo, ".claude/hooks/hooks.json", changed)
    _commit(repo, "settings")

    text, payload, _comparison, comment = _all_routes(repo, tmp_path)

    assert [row["subject"] for row in payload["rows"]] == ["claude-code .claude/hooks/hooks.json"]
    status = "changed_without_rows" if moves_basis else "changed_without_grant_change"
    assert (SETTINGS, status, "both", 0, None, ["claude-code"]) in _items(payload["coverage"])
    finding = NEUTRAL if moves_basis else GRANT_UNCHANGED
    assert f"  {SETTINGS} (claude-code): compared; {finding}" in _block(text)
    assert f"- ` {SETTINGS} ` (claude-code): compared; {finding}\n" in comment


# --- incomparable: each blocking source and its kind, refusal unchanged ------

SKILL = "---\nname: demo\ndescription: d\nmetadata:\n  version: 2\n---\nbody\n"


def _directory_link(repo: Path) -> None:
    _write(repo, "src/pkg/a.txt", "x\n")
    _link(repo, "docs/proxy", "../src/pkg")


def _dangling_link(repo: Path) -> None:
    _link(repo, "NOTES.md", "does/not/exist.md")


def _linked_skill(repo: Path) -> None:
    _write(repo, ".agents/skills/demo/SKILL.md", SKILL)
    _link(repo, ".claude/skills", "../.agents/skills")


def _linked_skill_with_nested_link(repo: Path) -> None:
    _linked_skill(repo)
    _link(repo, ".agents/skills/alias", "demo")


BOTH = ["base_inventory_incomplete", "head_inventory_incomplete"]
INCOMPARABLE = {
    "directory-link": (
        _directory_link, WIDENED, BOTH,
        {("docs/proxy", "unreadable", "both")},
    ),
    "dangling-link": (
        _dangling_link, WIDENED, BOTH,
        {("NOTES.md", "unreadable", "both")},
    ),
    "skill-metadata-through-link": (
        _linked_skill, {"permissions": {"allow": ["Read(**)"], "deny": []}}, BOTH,
        {
            (".agents/skills/demo/SKILL.md", "unsupported", "both"),
            (".claude/skills/demo/SKILL.md", "unsupported", "both"),
        },
    ),
    "skill-metadata-and-nested-link": (
        _linked_skill_with_nested_link, {"permissions": {"allow": ["Read(**)"], "deny": []}}, BOTH,
        {
            (".agents/skills/alias/SKILL.md", "unsupported", "both"),
            (".agents/skills/demo/SKILL.md", "unsupported", "both"),
            (".claude/skills", "unreadable", "both"),
        },
    ),
}


@_LINKS
@pytest.mark.parametrize("case", list(INCOMPARABLE))
def test_an_incomparable_result_names_each_blocking_source_and_kind(tmp_path: Path, case: str) -> None:
    prepare, head_settings, reasons, expected = INCOMPARABLE[case]
    repo = _repository(tmp_path)
    prepare(repo)
    _commit(repo, "base with a limit")
    _git(repo, "branch", "-f", "main", "HEAD")
    _write(repo, SETTINGS, head_settings)
    _commit(repo, "change")

    text, payload, _comparison, comment = _all_routes(repo, tmp_path)

    # The refusal itself is unchanged.
    assert payload["comparison_status"] == "incomparable"
    assert payload["incomparable_reasons"] == reasons
    assert payload["rows"] == [] and payload["unchanged_limits"] == []
    assert text.splitlines()[:2] == [
        "Cannot compare against main: " + "; ".join(reasons),
        "This is an input limit, not a finding about the change. Nothing below is a claim that the change is safe.",
    ]
    items = payload["coverage"]["items"]
    assert {(item["source"], item["limit"], item["side"]) for item in items} == expected
    assert all(item["status"] == "blocking_limit" and item["detail"] for item in items)
    for source, limit, _side in expected:
        hosts = next(item["hosts"] for item in items if item["source"] == source)
        line = f"{source} ({', '.join(hosts)}): {limit} in base and head, so neither inventory is complete"
        assert f"  {line}" in _block(text)
        assert f"- ` {source} ` ({', '.join(hosts)}): {limit} in base and head" in comment


def test_a_base_side_parse_failure_is_named_on_the_base(tmp_path: Path) -> None:
    repo = _repository(tmp_path, {".mcp.json": "{not json\n"})
    _write(repo, ".mcp.json", {"mcpServers": {"fs": {"command": "npx", "args": ["fs"]}}})
    _commit(repo, "repair")

    text, payload, comparison, comment = _all_routes(repo, tmp_path)

    assert payload["incomparable_reasons"] == ["base_inventory_incomplete"]
    assert _items(payload["coverage"]) == [
        (".mcp.json", "blocking_limit", "base", 0, "parse_failed", ["claude-code"])
    ]
    line = ".mcp.json (claude-code): parse_failed in base, so the base inventory is incomplete"
    assert _block(text) == [HEADING, f"  {line}"]
    assert "Host capability comparison unavailable: ` base_inventory_incomplete `\n" in comment
    assert "- ` .mcp.json ` (claude-code): parse_failed in base" in comment


def test_the_incomparable_next_action_is_not_rerouted_in_this_slice(tmp_path: Path) -> None:
    """Coverage is evidence beside the control; the control route stays as it was."""

    repo = _repository(tmp_path, {".mcp.json": "{not json\n"})
    _write(repo, ".mcp.json", {"mcpServers": {}})
    _commit(repo, "repair")

    verifier, comment = _verify(repo, tmp_path / "out")

    assert verifier["host_comparison"]["coverage"]["items"]
    assert "audit --host" in str(verifier["control"]["next_action"].get("command"))
    assert "- Next command: `agents-shipgate audit --host" in comment


# --- limits already named, the cap, and the empty answer ----------------------


def test_an_unchanged_limit_is_named_once_and_not_repeated_as_coverage(tmp_path: Path) -> None:
    skill = ".claude/skills/helper/SKILL.md"
    repo = _repository(
        tmp_path, {skill: "---\nname: helper\ndescription: A helper.\neffort: extreme\n---\n\nBody.\n"}
    )
    _write(repo, SETTINGS, WIDENED)
    _commit(repo, "widen")

    text, payload, _comparison, _comment = _all_routes(repo, tmp_path)

    assert [limit["source"] for limit in payload["unchanged_limits"]] == [skill]
    assert skill not in {item["source"] for item in payload["coverage"]["items"]}
    assert _items(payload["coverage"]) == [(SETTINGS, "compared", "both", 2, None, ["claude-code"])]
    assert f"  {SETTINGS} (claude-code): compared; 2 rows" in _block(text)


def test_the_list_is_capped_with_the_changed_source_first(tmp_path: Path) -> None:
    workflow = (
        "on: [push]\npermissions:\n  contents: read\njobs:\n  t:\n"
        "    runs-on: ubuntu-latest\n    steps:\n      - run: echo {i}\n"
    )
    repo = _repository(
        tmp_path, {f".github/workflows/w{i:02d}.yml": workflow.format(i=i) for i in range(12)}
    )
    _write(repo, ".github/workflows/w05.yml", workflow.format(i=5).replace("contents: read", "contents: write"))
    _commit(repo, "widen one workflow")

    text, payload, _comparison, comment = _all_routes(repo, tmp_path)

    coverage = payload["coverage"]
    assert len(coverage["items"]) == MAX_COVERAGE_ITEMS
    assert coverage["omitted_items"] == 13 - MAX_COVERAGE_ITEMS
    assert _items(coverage)[0] == (".github/workflows/w05.yml", "compared", "both", 1, None, ["github"])
    assert _block(text) == [
        HEADING,
        "  .github/workflows/w05.yml (github): compared; 1 row",
        f"  compared with no change in what this entry reads: {SETTINGS}, "
        ".github/workflows/w00.yml, .github/workflows/w01.yml and 9 more",
    ]
    assert "and 9 more" in comment
    schema = json.loads((ROOT / "docs/verifier-schema.v0.20.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(json.loads((tmp_path / "out/verifier.json").read_text("utf-8")))


@_LINKS
def test_long_paths_never_push_the_advisory_and_next_action_out_of_the_pr_comment(tmp_path: Path) -> None:
    """The PR comment's human summary is bounded as a whole; the block takes a bounded share.

    Twelve dangling links at about 410-character paths: the terminal lists the
    capped ten, the PR comment lists what fits and counts the rest, and the
    advisory, next action and evidence lines stay.
    """

    repo = _repository(tmp_path)
    directory = f"{'d' * 200}/{'e' * 200}"
    for index in range(12):
        _link(repo, f"{directory}/L{index:02d}.md", "does/not/exist.md")
    _commit(repo, "dangling links")
    _git(repo, "branch", "-f", "main", "HEAD")
    _write(repo, SETTINGS, WIDENED)
    _commit(repo, "change")

    text, payload, _comparison, comment = _all_routes(repo, tmp_path)

    coverage = payload["coverage"]
    assert payload["comparison_status"] == "incomparable"
    assert len(coverage["items"]) == MAX_COVERAGE_ITEMS and coverage["omitted_items"] == 2
    assert _block(text)[-1] == "  2 more items not listed"
    assert len(_block(text)) == 1 + MAX_COVERAGE_ITEMS + 1
    lines = comment.splitlines()
    start = lines.index(HEADING)
    block = lines[start : lines.index("", start)]
    assert len("\n".join(block)) <= 2000
    listed = len(block) - 2
    assert 1 <= listed < MAX_COVERAGE_ITEMS
    assert block[-1] == f"- {len(coverage['items']) + coverage['omitted_items'] - listed} more items not listed"
    assert "Advisory: no application release policy configured. This comparison grants no merge authority." in comment
    assert "- Next actor:" in comment and "- Next command:" in comment
    assert "Evidence: `verifier.json`" in comment


WIDENABLE_WORKFLOW = (
    "on: [push]\npermissions:\n  contents: {mode}\njobs:\n  t:\n"
    "    runs-on: ubuntu-latest\n    steps:\n      - run: echo {index}\n"
)
ADVISORY = "Advisory: no application release policy configured. This comparison grants no merge authority."


def test_the_block_never_pushes_a_line_out_of_a_pr_comment_with_many_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review cycle 5: the block takes only the room the entries and the tail after it leave.

    Widened workflows and an `env` edit, as many workflows as the
    6000-character comment prints with its tail when it has no coverage. The
    agent block names the workspace path, so that count is measured here
    rather than fixed. Before, the block was bounded only by itself, and at
    that count it pushed the lines after it out, from the review question to
    the evidence. Now the comment keeps them, and the `env` edit, which no
    entry shows, is listed ahead of the files the entries already name.

    Then every bound across the comment's length, in both comment styles: the
    comment with coverage is the comment without it plus, at most, the block,
    so the block never costs a line the comment printed without it.
    """

    from agents_shipgate.report import pr_comment
    from agents_shipgate.report.pr_comment import render_pr_comment

    tail = ("Review question:", "Compared:", "Reproduce:", ADVISORY, "Evidence: `verifier.json`")
    workflows = {
        f".github/workflows/w{index:02d}.yml": WIDENABLE_WORKFLOW.format(mode="read", index=index)
        for index in range(24)
    }
    repo = _repository(tmp_path, workflows)

    def widen(count: int, branch: str) -> None:
        _git(repo, "checkout", "-q", "-B", branch, "main")
        for name in sorted(workflows)[:count]:
            _write(repo, name, workflows[name].replace("contents: read", "contents: write"))
        _write(repo, SETTINGS, {**BASE_SETTINGS, "env": {"LOG_LEVEL": "debug"}})
        _commit(repo, f"widen {count} workflows and add an env value")

    def without_coverage(verifier: VerifierArtifact, rows: int | None = None) -> VerifierArtifact:
        comparison = verifier.host_comparison
        assert comparison is not None
        update: dict = {"coverage": None}
        if rows is not None:
            update["rows"] = comparison.rows[:rows]
        return verifier.model_copy(update={"host_comparison": comparison.model_copy(update=update)})

    widen(24, "change")
    widest = VerifierArtifact.model_validate(_verify(repo, tmp_path / "all")[0])
    count = max(
        rows
        for rows in range(10, 25)
        if all(line in render_pr_comment(without_coverage(widest, rows), report=None) for line in tail)
    )
    assert count < 24, "the fixture must reach the comment's bound"
    widen(count, "tight")

    text, payload, _comparison, comment = _all_routes(repo, tmp_path)

    assert len(payload["rows"]) == count
    env_edit = (SETTINGS, "changed_without_grant_change", "both", 0, None, ["claude-code"])
    assert _items(payload["coverage"])[0] == env_edit
    assert _block(text)[1] == f"  {SETTINGS} (claude-code): compared; {GRANT_UNCHANGED}"
    verifier = VerifierArtifact.model_validate(
        json.loads((tmp_path / "out/verifier.json").read_text(encoding="utf-8"))
    )
    assert render_pr_comment(verifier, report=None) == comment
    plain_verifier = without_coverage(verifier)
    plain = render_pr_comment(plain_verifier, report=None)
    assert all(line in plain for line in tail)
    # The block as it was bounded before, by itself, no longer fits beside them.
    assert verifier.host_comparison is not None
    alone = coverage_lines(verifier.host_comparison, markdown=True, max_chars=2000)
    assert len(plain) + len("\n".join(alone)) + 1 > 6000
    for line in tail:
        assert line in comment, line
    assert len(comment) <= 6000
    lines = comment.splitlines()
    if HEADING in lines:
        assert lines[lines.index(HEADING) + 1] in {
            f"- ` {SETTINGS} ` (claude-code): compared; {GRANT_UNCHANGED}",
            f"- {len(workflows) + 1} items not listed",
        }

    listed_at_the_edge = omitted_at_the_edge = False
    for style in ("capability-review", "findings"):
        length = len(render_pr_comment(plain_verifier, report=None, style=style))
        for limit in range(length - 2300, length + 60, 3):
            monkeypatch.setattr(pr_comment, "_COMMENT_MAX_CHARS", limit)
            plain = render_pr_comment(plain_verifier, report=None, style=style)
            covered = render_pr_comment(verifier, report=None, style=style).splitlines()
            if HEADING not in covered:
                assert covered == plain.splitlines(), (style, limit)
                omitted_at_the_edge |= ADVISORY in plain
                continue
            start = covered.index(HEADING)
            end = covered.index("", start)
            assert covered[:start] + covered[end + 1 :] == plain.splitlines(), (style, limit)
            assert covered[start + 1] in {
                f"- ` {SETTINGS} ` (claude-code): compared; {GRANT_UNCHANGED}",
                f"- {len(workflows) + 1} items not listed",
            }, (style, limit)
            listed_at_the_edge |= ADVISORY in plain and len("\n".join(covered)) > limit - 60
    # The sweep reached both edges: a block that fills the room, and no room for one.
    assert listed_at_the_edge and omitted_at_the_edge


def _coverage_comparison(items: list[dict], omitted: int = 0) -> HostComparison:
    return HostComparison.model_validate({
        "comparison_status": "comparable",
        "head_kind": "worktree",
        "coverage": {"items": items, "omitted_items": omitted},
    })


def test_items_not_listed_are_counted_as_items_not_sources() -> None:
    """One source can be several items (by host or side), so the count says items."""

    items = [
        {"source": "AGENTS.md", "hosts": ["codex"], "side": "head", "status": "compared"},
        {"source": "AGENTS.md", "hosts": ["cursor"], "side": "base", "status": "compared"},
    ]

    assert coverage_lines(_coverage_comparison(items, omitted=1), bullet="  ")[-1] == "  1 more item not listed"
    assert coverage_lines(_coverage_comparison(items, omitted=3), bullet="  ")[-1] == "  3 more items not listed"
    # A budget lists what fits and counts the rest, as items.
    comparison = _coverage_comparison(items, omitted=3)
    first = coverage_lines(comparison, bullet="  ")[1]
    bounded = coverage_lines(comparison, bullet="  ", max_chars=len(f"{HEADING}\n{first}\n  4 more items not listed"))
    assert bounded == [HEADING, first, "  4 more items not listed"]


@pytest.mark.parametrize("markdown", [False, True])
def test_a_bounded_block_never_exceeds_its_bound(markdown: bool) -> None:
    """Review cycle 5: the bound holds for the whole block, count and closing blank included.

    Every budget from nothing to the whole block: the block fits, lists a
    prefix of the unbounded block's lines, names fewer quiet sources before it
    drops their line, counts every item it does not list (without "more" when
    it lists none), and is nothing when not even the heading and a count fit.
    """

    # Four changes, then six sources proven unchanged and five more like them.
    items = [
        _item(source=f"changed/{index}.json", status="changed_without_rows") for index in range(4)
    ] + [_item(source=f"quiet/{index}.json") for index in range(6)]
    comparison = _coverage_comparison(items, omitted=5)
    close = [""] if markdown else []
    full = coverage_lines(comparison, markdown=markdown)
    changes, quiet_line = full[1:5], full[5]
    assert full == [HEADING, *changes, quiet_line, *close]
    assert quiet_line.endswith(" and 8 more")
    minimum = len("\n".join([HEADING, "- 15 items not listed", *close]))

    for budget in range(len("\n".join(full)) + 1):
        block = coverage_lines(comparison, markdown=markdown, max_chars=budget)
        if budget < minimum:
            assert block == [], budget
            continue
        assert len("\n".join(block)) <= budget, budget
        assert block[0] == HEADING and block[len(block) - len(close):] == close, budget
        body = block[1 : len(block) - len(close)]
        quiet = [line for line in body if "compared with no change in what this entry reads" in line]
        counted = [line for line in body if line.endswith("not listed")]
        listed = [line for line in body if line not in quiet + counted]
        assert body == [*listed, *quiet, *counted], budget
        assert listed == changes[: len(listed)], budget
        # The quiet line, naming three, two or one source, stands for all eleven.
        assert len(quiet) <= 1 and (not quiet or listed == changes), budget
        unlisted = 15 - len(listed) - (11 if quiet else 0)
        if not unlisted:
            assert counted == [], budget
        elif listed or quiet:
            assert counted == [f"- {unlisted} more item{'s' if unlisted != 1 else ''} not listed"], budget
        else:
            assert counted == ["- 15 items not listed"], budget
    assert coverage_lines(comparison, markdown=markdown, max_chars=len("\n".join(full))) == full


def test_a_comparison_that_read_no_source_says_so(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _write(repo, "README.md", "# demo\n")
    _commit(repo, "base")
    _git(repo, "checkout", "-q", "-b", "change")
    _write(repo, "README.md", "# demo\nmore\n")
    _commit(repo, "docs")

    text, payload = _diff(repo)

    assert payload["coverage"] == {"items": [], "omitted_items": 0}
    assert text.rstrip().splitlines()[-1] == f"{HEADING} no host configuration source was compared."


# --- redaction, digests, other routes and readers ------------------------------


def test_token_shaped_paths_are_redacted_in_every_coverage_projection(tmp_path: Path) -> None:
    token = "ghp_" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4J3i2"
    source = f"plugins/{token}/.mcp.json"
    repo = _repository(tmp_path, {source: {"mcpServers": {"fs": {"command": "npx"}}}})
    _write(repo, source, {"mcpServers": {"fs": {"command": "npx"}}, "note": 1})
    _commit(repo, "unread plugin field")

    text, payload, comparison, comment = _all_routes(repo, tmp_path)

    published = [item["source"] for item in payload["coverage"]["items"]]
    redacted = next(path for path in published if path.startswith("plugins/"))
    assert "[REDACTED:" in redacted and redacted.endswith("/.mcp.json")
    assert token not in text
    assert token not in json.dumps(payload)
    assert token not in json.dumps(comparison)
    assert token not in comment
    assert f"{redacted} (claude-code): compared; {GRANT_UNCHANGED}" in text


def test_a_redacted_path_is_never_proven_unchanged(tmp_path: Path) -> None:
    """Review cycle 2: a redacted published path names no file, so no identity proof is asked.

    A file literally named like the label must not stand in for the real one.
    """

    token = "ghp_" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4J3i2"
    source = f"plugins/{token}/.mcp.json"

    def server(base_url: str) -> dict:
        return {"mcpServers": {"api": {"command": "node", "env": {"API_BASE": base_url}}}}

    repo = _repository(tmp_path, {source: server("https://a.example")})
    _write(repo, source, server("https://evil.example"))
    _commit(repo, "value under a redacted path")

    text, payload, _comparison, comment = _all_routes(repo, tmp_path)

    redacted = next(item for item in payload["coverage"]["items"] if item["source"].startswith("plugins/"))
    assert "[REDACTED:" in redacted["source"]
    assert (redacted["status"], redacted["side"], redacted["rows"]) == ("unchanged_not_proven", "both", 0)
    assert f"  {redacted['source']} (claude-code): compared; {NOT_PROVEN}" in _block(text)
    assert f"): compared; {NOT_PROVEN}\n" in comment
    assert token not in text + comment + json.dumps(payload)


MCP = ".mcp.json"


@pytest.mark.parametrize("conversion", ["gitattributes-eol-crlf", "core-autocrlf"])
def test_a_line_ending_conversion_at_checkout_is_never_read_as_a_change(
    tmp_path: Path, conversion: str
) -> None:
    """Review cycle 3: files Git reports unchanged are never said to have changed.

    A checkout wrote CRLF bytes from LF blobs, as `eol=crlf` or the Git for
    Windows `core.autocrlf=true` default does. The working tree's unfiltered
    hash then differs from the base blob, which proves the bytes neither
    identical nor changed: the working-tree routes read `unchanged_not_proven`,
    and the commit route, whose blobs are identical, reads `compared`.
    """

    files: dict[str, object] = {
        SETTINGS: json.dumps(BASE_SETTINGS, indent=2) + "\n",
        MCP: json.dumps({"mcpServers": {"api": {"command": "node"}}}, indent=2) + "\n",
    }
    if conversion == "gitattributes-eol-crlf":
        files[".gitattributes"] = "*.json text eol=crlf\n"
    repo = _repository(tmp_path, files)
    if conversion == "core-autocrlf":
        _git(repo, "config", "core.autocrlf", "true")
    for name in (SETTINGS, MCP):
        (repo / name).unlink()
    _git(repo, "checkout", "--", SETTINGS, MCP)
    _write(repo, "README.md", "# demo\nmore\n")
    _commit(repo, "docs")

    assert b"\r\n" in (repo / SETTINGS).read_bytes() and b"\r\n" in (repo / MCP).read_bytes()
    assert _git(repo, "status", "--porcelain") == ""
    assert _git(repo, "diff", "--name-only", "main") == "README.md"

    text, payload, _comparison, comment = _all_routes(repo, tmp_path, head=False)

    assert payload["comparison_status"] == "comparable" and payload["rows"] == []
    assert {item["source"]: item["status"] for item in payload["coverage"]["items"]} == {
        SETTINGS: "unchanged_not_proven",
        MCP: "unchanged_not_proven",
    }
    assert GRANT_UNCHANGED not in text + comment
    assert f"  {SETTINGS} (claude-code): compared; {NOT_PROVEN}" in _block(text)
    assert "compared with no change" not in text + comment

    committed, _committed_comment = _verify(repo, tmp_path / "commit", head=True)
    assert {
        item["source"]: item["status"] for item in committed["host_comparison"]["coverage"]["items"]
    } == {SETTINGS: "compared", MCP: "compared"}


def _identity_repository(tmp_path: Path) -> Path:
    repo = _repository(
        tmp_path,
        {
            ".gitattributes": "ident.json ident\n",
            "same.json": "{\n}\n",
            "edited.json": '{"a": 1}\n',
            "crlf.json": '{\n  "a": 1\n}\n',
            "ident.json": '{"a": "$Id$"}\n',
            "mode.sh": "echo\n",
            "dir/inner.json": "{}\n",
        },
    )
    _write(repo, "edited.json", '{"a": 2}\n')
    (repo / "crlf.json").write_bytes(b'{\r\n  "a": 1\r\n}\r\n')
    _write(repo, "ident.json", '{"a": "$Id$", "b": 1}\n')
    return repo


def test_the_identity_answer_says_identical_differs_or_neither(tmp_path: Path) -> None:
    """Review cycle 3: "not proven identical" is not "changed".

    ``True`` is exactly `blob_path_unchanged`'s proof, which `unchanged_limits`
    keeps using. ``False`` needs Git to show a content difference no checkout
    conversion explains; everything else is ``None``.
    """

    from agents_shipgate.cli.verify.git import blob_path_identities, blob_path_unchanged

    repo = _identity_repository(tmp_path)
    paths = [
        "same.json", "edited.json", "crlf.json", "ident.json", "mode.sh",
        "missing.json", "dir", "../outside", "/etc/passwd", "dir\\inner.json",
    ]
    worktree = {
        "same.json": True,
        "edited.json": False,
        # A line-ending difference is what a checkout conversion makes.
        "crlf.json": None,
        # `ident` converts on checkout, and no filter is run to tell.
        "ident.json": None,
        "mode.sh": True,
        "missing.json": None,
        "dir": None,
        "../outside": None,
        "/etc/passwd": None,
        "dir\\inner.json": None,
    }
    assert blob_path_identities(repo, "main", None, paths) == worktree
    assert blob_path_identities(repo, "no-such-ref", None, paths) == dict.fromkeys(paths)

    # Both, so the mode holds whether or not the filesystem keeps executable bits.
    (repo / "mode.sh").chmod(0o755)
    _git(repo, "update-index", "--chmod=+x", "mode.sh")
    _commit(repo, "edits and a mode change")
    assert _git(repo, "ls-tree", "HEAD", "mode.sh").startswith("100755 ")
    committed = {
        **worktree,
        # Between commits the blobs themselves differ, and Git reports it.
        "crlf.json": False,
        "ident.json": False,
        # The same blob under another mode: neither answer is shown.
        "mode.sh": None,
    }
    head = _git(repo, "rev-parse", "HEAD")
    assert blob_path_identities(repo, "main", head, paths) == committed
    assert blob_path_identities(repo, "main", "main", ["same.json", "mode.sh"]) == {
        "same.json": True, "mode.sh": True,
    }
    for path in paths:
        if path.startswith("/") or "\\" in path or ".." in path:
            continue
        assert blob_path_unchanged(repo, "main", head, path) is (committed[path] is True), path


@_LINKS
def test_the_identity_answer_never_reads_through_a_link(tmp_path: Path) -> None:
    from agents_shipgate.cli.verify.git import blob_path_identities

    repo = _repository(tmp_path, {"config/a.json": "{}\n", "linked/a.json": "{}\n"})
    (repo / "linked/a.json").unlink()
    (repo / "linked").rmdir()
    _link(repo, "linked", "config")
    (repo / SETTINGS).unlink()
    _link(repo, SETTINGS, "../config/a.json")

    assert blob_path_identities(repo, "main", None, [SETTINGS, "linked/a.json", "config/a.json"]) == {
        SETTINGS: None, "linked/a.json": None, "config/a.json": True,
    }


def test_the_identity_answer_costs_the_same_for_two_files_or_forty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review cycle 3: Git is asked once per step, not once per file.

    Every file differs, so every step runs: the base commit, its listing, the
    working-tree hashes, the checkout attributes and the base blobs.
    """

    import agents_shipgate.cli.verify.git as git

    workflows = {f".github/workflows/w{index:02}.yml": f"name: w{index}\non: push\n" for index in range(40)}
    repo = _repository(tmp_path, workflows)
    for name in workflows:
        _write(repo, name, f"name: {name}\non: pull_request\n")
    calls: list[str] = []
    for runner in ("_run_git", "_run_git_bounded_result"):
        original = getattr(git, runner)

        def counted(*args, _original=original, **kwargs):
            calls.append(args[1][0] if args[1] else "")
            return _original(*args, **kwargs)

        monkeypatch.setattr(git, runner, counted)

    paths = sorted(workflows)
    assert git.blob_path_identities(repo, "main", None, paths[:2]) == dict.fromkeys(paths[:2], False)
    two = list(calls)
    calls.clear()
    assert git.blob_path_identities(repo, "main", None, paths) == dict.fromkeys(paths, False)

    assert len(calls) == len(two) <= 5


def test_a_provided_diff_builds_no_coverage(tmp_path: Path) -> None:
    """`agent-result` keeps only the rows, so the provided-diff route records no coverage."""

    from agents_shipgate.core.host_diff_comparison import compare_host_diff

    repo = _repository(tmp_path)
    _write(repo, SETTINGS, WIDENED)

    comparison = compare_host_diff(repo, _git(repo, "diff", "main") + "\n")

    assert comparison.comparison_status == "comparable" and comparison.rows
    assert comparison.coverage is None


def test_coverage_stays_out_of_inventory_digests_and_baselines(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    _write(repo, SETTINGS, {**BASE_SETTINGS, "env": {"A": "1"}})
    _commit(repo, "env")

    _text, payload = _diff(repo)
    verifier, _comment = _verify(repo, tmp_path / "out")
    inventory = build_host_boundary_snapshot(repo).inventory

    assert payload["coverage"]["items"]
    assert verifier["host_comparison"]["head_inventory_sha256"] == host_grants_sha256(
        normalized_host_grants(inventory)
    )
    baseline = json.dumps(build_host_grants_baseline(inventory))
    for key in ('"coverage"', '"omitted_items"', '"changed_without_grant_change"'):
        assert key not in baseline
        assert key not in json.dumps(inventory)


def test_check_publishes_no_coverage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`check`'s boundary result cannot carry the block, its text does not print it, and it
    asks no identity question it would discard."""

    import agents_shipgate.cli.verify.host_comparison as host_comparison_route

    asked: list[list[str]] = []
    question = host_comparison_route.blob_path_identities

    def counted(workspace, base, head, paths):
        asked.append(list(paths))
        return question(workspace, base, head, paths)

    monkeypatch.setattr(host_comparison_route, "blob_path_identities", counted)
    repo = _repository(tmp_path, {SETTINGS: {**BASE_SETTINGS, "env": {"A": "1"}}})
    _write(repo, SETTINGS, {**BASE_SETTINGS, "env": {"A": "2"}})
    _commit(repo, "env value")
    selection = ["--workspace", str(repo), "--base", "main", "--head", "HEAD"]

    payload = json.loads(_invoke(["check", *selection, "--format", "agent-boundary-json"]))
    text = _invoke(["check", *selection, "--format", "text"])

    assert "coverage" not in payload
    assert HEADING not in text
    assert asked == []
    # The same route with coverage asks once, for every file it needs.
    _verify(repo, tmp_path / "out")
    assert asked == [[SETTINGS]]


def _inventories(tmp_path: Path, base: dict[str, object], head: dict[str, object]) -> tuple[dict, dict]:
    trees = []
    for name, files in (("base", base), ("head", head)):
        root = tmp_path / name
        root.mkdir()
        for path, value in files.items():
            _write(root, path, value)
        trees.append(build_host_boundary_snapshot(root).inventory)
    return trees[0], trees[1]


@pytest.mark.parametrize(
    ("identities", "status"),
    [
        (None, "unchanged_not_proven"),
        (lambda paths: dict.fromkeys(paths, True), "compared"),
        (lambda paths: dict.fromkeys(paths, False), "changed_without_grant_change"),
        # Review cycle 3: neither shown, such as a checkout line-ending conversion.
        (lambda paths: dict.fromkeys(paths), "unchanged_not_proven"),
        (lambda paths: {}, "unchanged_not_proven"),
    ],
    ids=["no-question-provided-diff", "identical", "differs", "neither-shown", "not-answered"],
)
def test_only_a_byte_identity_proof_makes_a_zero_row_file_unchanged(
    tmp_path: Path, identities, status: str
) -> None:
    """The comparator's own rule, as a provided diff (no question) and the git routes see it.

    ``unchanged`` is the `unchanged_limits` proof and never decides coverage:
    "not proven identical" is not "changed".
    """

    settings = {**BASE_SETTINGS, "env": {"A": "1"}}
    before, after = _inventories(tmp_path, {SETTINGS: settings}, {SETTINGS: {**settings, "env": {"A": "2"}}})

    comparison = compare_host_inventories(
        before, after, head_kind="provided_diff", unchanged=lambda path: False, identities=identities
    )

    assert comparison.comparison_status == "comparable" and comparison.rows == []
    assert comparison.coverage is not None
    assert [(item.source, item.status, item.side) for item in comparison.coverage.items] == [
        (SETTINGS, status, "both")
    ]
    printed = coverage_lines(comparison, bullet="  ")
    assert (printed == [HEADING, f"  compared with no change in what this entry reads: {SETTINGS}"]) is (
        status == "compared"
    )
    assert compare_host_inventories(
        before, after, head_kind="provided_diff", identities=identities, coverage=False
    ).coverage is None


def test_a_v0_19_verifier_reads_with_coverage_not_recorded(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    _write(repo, SETTINGS, {**BASE_SETTINGS, "env": {"A": "1"}})
    _commit(repo, "env")
    verifier, _comment = _verify(repo, tmp_path / "out")
    assert verifier["host_comparison"]["coverage"]["items"]
    current = json.loads((ROOT / "docs/verifier-schema.v0.20.json").read_text(encoding="utf-8"))
    Draft202012Validator(current).validate(verifier)

    # A strict 0.19 reader rejects the new member: the reason the version moved.
    frozen = json.loads((ROOT / "docs/verifier-schema.v0.19.json").read_text(encoding="utf-8"))
    legacy = json.loads(json.dumps(verifier))
    legacy["verifier_schema_version"] = "0.19"
    assert list(Draft202012Validator(frozen).iter_errors(legacy))

    # A 0.19 artifact never recorded coverage and cannot claim it.
    with pytest.raises(ValueError, match="host comparison coverage"):
        VerifierArtifact.model_validate(legacy)
    legacy["host_comparison"].pop("coverage")
    # Nor did it record how its rows were presented (#795 slice 2).
    with pytest.raises(ValueError, match="how its rows were presented"):
        VerifierArtifact.model_validate(legacy)
    legacy["host_comparison"].pop("review")
    assert not list(Draft202012Validator(frozen).iter_errors(legacy))
    read = VerifierArtifact.model_validate(legacy)
    assert read.verifier_schema_version == "0.20"
    assert read.host_comparison is not None and read.host_comparison.coverage is None
    # Not recorded prints no block, never an invented one.
    assert coverage_lines(read.host_comparison) == []
    assert HEADING not in "\n".join(host_comparison_lines(read.host_comparison, markdown=True))


def test_the_current_reader_round_trips_coverage(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    _write(repo, SETTINGS, {**BASE_SETTINGS, "env": {"A": "1"}})
    _commit(repo, "env")
    verifier, _comment = _verify(repo, tmp_path / "out")

    read = VerifierArtifact.model_validate(verifier)

    assert read.host_comparison is not None
    assert read.host_comparison.model_dump(mode="json")["coverage"] == verifier["host_comparison"]["coverage"]


# --- the published shape cannot say what the comparison did not establish ----


def _item(**overrides) -> dict:
    return {"source": SETTINGS, "hosts": ["claude-code"], "side": "both", "status": "compared", **overrides}


@pytest.mark.parametrize(
    ("status", "items", "rows"),
    [
        ("comparable", [_item(status="blocking_limit", limit="unreadable")], 0),
        ("incomparable", [_item()], 0),
        ("comparable", [_item(rows=2)], 1),
        ("comparable", [_item()], 1),
    ],
    ids=["limit-on-comparable", "compared-on-incomparable", "more-rows-than-published", "row-left-unattributed"],
)
def test_the_comparison_refuses_coverage_it_did_not_establish(status: str, items: list, rows: int) -> None:
    row = {"subject": f"claude-code {SETTINGS}", "before": "—", "after": "Bash(*)",
           "direction": "added", "why": "w", "severity": "high"}
    with pytest.raises(ValueError):
        HostComparison.model_validate({
            "comparison_status": status,
            "incomparable_reasons": ["head_inventory_incomplete"] if status == "incomparable" else [],
            "head_kind": "worktree",
            "rows": [row] * rows,
            "coverage": {"items": items, "omitted_items": 0},
        })


@pytest.mark.parametrize(
    "item",
    [
        _item(status="blocking_limit"),
        _item(status="blocking_limit", limit="unreadable", rows=1),
        _item(limit="unreadable"),
        _item(detail="why"),
        _item(status="changed_without_grant_change", rows=1),
        _item(status="unchanged_not_proven", rows=1),
        _item(status="unchanged_not_proven", side="head"),
        _item(hosts=[]),
        _item(side="neither"),
    ],
)
def test_an_item_keeps_its_shape(item: dict) -> None:
    with pytest.raises(ValueError):
        HostComparisonCoverageItem.model_validate(item)


def test_the_list_cannot_exceed_its_cap() -> None:
    with pytest.raises(ValueError):
        HostComparisonCoverage.model_validate(
            {"items": [_item(source=f"s{i}") for i in range(MAX_COVERAGE_ITEMS + 1)]}
        )
