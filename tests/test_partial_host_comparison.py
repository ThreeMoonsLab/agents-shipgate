"""#808: a plugin directory that cannot be compared no longer hides the rest.

#714 made a plugin manifest a read boundary reference, and an unreadable one a
blocking coverage limit. That limit refused the whole comparison: when a head
broke `plugins/demo/.claude-plugin/plugin.json` and also dropped a `deny` rule
from `.claude/settings.json`, `diff`, `verify` and the PR comment printed
`head_inventory_incomplete` and no row at all, where the published `1.0.0`
had shown the removed denial.

A plugin-reference limit is bounded by the reference graph the reader already
follows: a reference is followed only inside its plugin directory, so what the
limit hides is published under that directory. Where nothing outside the
directory depends on it, the comparison is now `partial`: the directory is
left uncompared on both sides and named, as `scope`, on the blocking limit
that caused it, and every row established outside it is published. Where
independence is not established — a reference leaving its plugin, a plugin at
the repository root or holding the project settings, a marketplace elsewhere
declaring hooks for that plugin, any other limit that is not an unchanged one —
the comparison refuses exactly as before.

The authority side does not move: `check` still refuses its comparison (its
boundary result can name no scope), every decision, violation and control
state is what it was, and the control envelope projects a partial comparison
as the incomparable one it used to be.

Every case is a real repository driven through `diff` (text and `--json`),
`verify` (`verifier.json` and its text) and the PR comment `verify` writes.
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
from agents_shipgate.core.agent_control_envelope import capability_rows_block
from agents_shipgate.report.host_comparison import host_comparison_lines, partial_scope_lines
from agents_shipgate.schemas.capability_diff import CapabilityDiffRow
from agents_shipgate.schemas.host_comparison import HostComparison, HostComparisonCoverageItem
from agents_shipgate.schemas.verifier import VerifierArtifact

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ".claude/settings.json"
MANIFEST = "plugins/demo/.claude-plugin/plugin.json"
HOOK_FILE = "plugins/demo/cfg/hooks.json"
BASE_SETTINGS = {"permissions": {"allow": ["Read(**)"], "deny": ["Bash(curl:*)"]}}
DENY_DROPPED = {"permissions": {"allow": ["Read(**)"]}}
PLUGIN = {"name": "demo", "version": "0.1.0", "hooks": "./cfg/hooks.json"}
HOOK = {"hooks": {"SessionStart": [{"matcher": "startup", "hooks": [{"type": "command", "command": "echo one"}]}]}}
CHANGED_HOOK = {
    "hooks": {"SessionStart": [{"matcher": "startup", "hooks": [{"type": "command", "command": "echo two"}]}]}
}
BROKEN = "{not json"
DENY_REMOVED = ("claude-code .claude/settings.json", "Bash(curl:*)", "removed", "deny")
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


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A", "-f")
    _git(repo, "commit", "-q", "-m", message)


def _repository(
    tmp_path: Path,
    base: dict[str, object],
    head: dict[str, object],
    *,
    links: dict[str, str] | None = None,
) -> Path:
    """`main` holds ``base`` over the settings above, and ``links``; branch `change` commits ``head``."""

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    for name, value in {SETTINGS: BASE_SETTINGS, "README.md": "# demo\n", **base}.items():
        _write(repo, name, value)
    for name, target in (links or {}).items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, repo / name)
    _commit(repo, "base")
    _git(repo, "checkout", "-q", "-b", "change")
    for name, value in head.items():
        _write(repo, name, value)
    _commit(repo, "change")
    return repo


def _issue_fixture(tmp_path: Path, head: dict[str, object] | None = None) -> Path:
    """#808's reproduction: the head breaks the plugin manifest and drops a deny rule."""

    return _repository(
        tmp_path,
        {MANIFEST: PLUGIN, HOOK_FILE: HOOK},
        {MANIFEST: BROKEN, SETTINGS: DENY_DROPPED, **(head or {})},
    )


def _invoke(args: list[str]) -> str:
    result = CliRunner().invoke(app, args, env={"NO_COLOR": "1", "COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    return result.output


def _diff(repo: Path) -> tuple[str, dict]:
    command = ["diff", "--workspace", str(repo), "--base", "main"]
    return _invoke(command), json.loads(_invoke([*command, "--json"]))


def _verify(repo: Path, out: Path) -> tuple[dict, str, str]:
    """`verifier.json`, the PR comment and the text of one advisory `verify` run."""

    text = _invoke([
        "verify", "--workspace", str(repo), "--config", "shipgate.yaml", "--ci-mode", "advisory",
        "--out", str(out), "--format", "text", "--base", "main",
        "--head", _git(repo, "rev-parse", "HEAD"),
    ])
    verifier = json.loads((out / "verifier.json").read_text(encoding="utf-8"))
    return verifier, (out / "pr-comment.md").read_text(encoding="utf-8"), text


def _all_routes(repo: Path, tmp_path: Path) -> tuple[str, dict, dict, str, str]:
    """diff text and JSON, verify's comparison, its PR comment and its text, agreeing."""

    text, payload = _diff(repo)
    verifier, comment, verify_text = _verify(repo, tmp_path / "out")
    comparison = verifier["host_comparison"]
    for key in ("comparison_status", "incomparable_reasons", "rows", "unchanged_limits", "coverage", "review"):
        if key == "review" and payload[key] is not None:
            # The reproduction command names the working tree for `diff` and
            # the commit for `verify`; the presented changes are the same.
            assert comparison[key]["changes"] == payload[key]["changes"]
            continue
        assert comparison[key] == payload[key], key
    Draft202012Validator(
        json.loads((ROOT / "docs/verifier-schema.v0.21.json").read_text(encoding="utf-8"))
    ).validate(verifier)
    return text, payload, comparison, comment, verify_text


def _rows(payload: dict) -> list[tuple]:
    return [(row["subject"], row["before"] if row["direction"] == "removed" else row["after"],
             row["direction"], row["disposition"]) for row in payload["rows"]]


def _limits(payload: dict) -> list[tuple]:
    return [
        (item["source"], item["limit"], item["side"], item["scope"])
        for item in payload["coverage"]["items"]
        if item["status"] == "blocking_limit"
    ]


def _refused(payload: dict, reasons: list[str]) -> None:
    """Exactly the refusal `1.1.0` published: no row, no review, no scope."""

    assert payload["comparison_status"] == "incomparable"
    assert payload["incomparable_reasons"] == reasons
    assert payload["rows"] == [] and payload["review"] is None
    assert all(item["scope"] is None for item in payload["coverage"]["items"])


# --- the issue's reproduction ---------------------------------------------------


def test_an_independent_settings_row_survives_a_broken_plugin_manifest(tmp_path: Path) -> None:
    repo = _issue_fixture(tmp_path)

    text, payload, comparison, comment, verify_text = _all_routes(repo, tmp_path)

    assert payload["comparison_status"] == "partial"
    # The refusal's reason is still what it was: the head inventory is incomplete.
    assert payload["incomparable_reasons"] == ["head_inventory_incomplete"]
    # The unrelated denial is attributable again; nothing inside the plugin is
    # a row, so the unread hook file is not reported as removed.
    assert _rows(payload) == [DENY_REMOVED]
    assert _limits(payload) == [(MANIFEST, "parse_failed", "head", "plugins/demo")]
    assert (SETTINGS, "compared", "both", 1) in [
        (item["source"], item["status"], item["side"], item["rows"]) for item in payload["coverage"]["items"]
    ]
    assert not any(item["source"].startswith("plugins/demo/cfg") for item in payload["coverage"]["items"])
    assert payload["review"]["summary"] == {"rows": 1, "changes": 1, "widenings": 1}

    # The scope and the reason lead, before any row, on every route.
    lines = text.splitlines()
    assert lines[0].startswith("Partial comparison against main (") and lines[0].endswith(
        "-> working tree: head_inventory_incomplete"
    )
    assert lines[1] == (
        "Not compared: plugins/demo, a plugin directory this entry could not read completely, "
        "so no change inside it is shown and nothing is claimed about it."
    )
    assert lines[2].startswith("The changes below come only from sources outside it, so they are not the whole change")
    assert "removes a denial the agent was subject to" in text
    assert (
        "  plugins/demo/.claude-plugin/plugin.json (claude-code): parse_failed in head, "
        "so nothing in plugins/demo was compared"
    ) in lines
    assert "Cannot compare" not in text and "Agent capability diff" not in text
    assert verify_text.splitlines()[:2] == [
        "Host capability comparison partial: head_inventory_incomplete",
        lines[1],
    ]
    assert "Host capability comparison partial: ` head_inventory_incomplete `" in comment
    assert "Not compared: ` plugins/demo `, a plugin directory" in comment
    assert "so nothing in ` plugins/demo ` was compared" in comment
    assert "removes a denial the agent was subject to" in comment


def test_a_partial_result_with_no_row_is_never_a_no_change_answer(tmp_path: Path) -> None:
    repo = _repository(tmp_path, {MANIFEST: PLUGIN, HOOK_FILE: HOOK}, {MANIFEST: BROKEN})

    text, payload, _comparison, comment, verify_text = _all_routes(repo, tmp_path)

    assert payload["comparison_status"] == "partial"
    assert payload["rows"] == []
    assert payload["review"]["summary"] == {"rows": 0, "changes": 0, "widenings": 0}
    for output in (text, verify_text, comment):
        assert "No static host-grant changes detected" not in output
        assert "That is not a no-change answer for this change" in output
    assert "No static host-grant change was detected outside it." in text
    assert "Review question" not in text


def test_the_base_side_and_a_repair_are_read_the_same_way(tmp_path: Path) -> None:
    """Asymmetry: a base that could not be read leaves the same directory
    uncompared, and a head that repairs it restores that directory's rows."""

    repo = _repository(
        tmp_path,
        {MANIFEST: BROKEN, HOOK_FILE: HOOK},
        {MANIFEST: PLUGIN, HOOK_FILE: CHANGED_HOOK, SETTINGS: DENY_DROPPED},
    )

    _text, payload, *_ = _all_routes(repo, tmp_path)

    assert payload["comparison_status"] == "partial"
    assert payload["incomparable_reasons"] == ["base_inventory_incomplete"]
    assert _limits(payload) == [(MANIFEST, "parse_failed", "base", "plugins/demo")]
    # The head's hook file is not an addition built from a base that was not read.
    assert _rows(payload) == [DENY_REMOVED]


def test_repairing_the_manifest_restores_the_plugin_rows(tmp_path: Path) -> None:
    repo = _issue_fixture(tmp_path, {HOOK_FILE: CHANGED_HOOK})

    _text, partial = _diff(repo)
    assert partial["comparison_status"] == "partial"
    assert _rows(partial) == [DENY_REMOVED]

    # Nothing is cached: the next run reads the repaired manifest and compares it.
    _write(repo, MANIFEST, PLUGIN)
    _text, repaired = _diff(repo)

    assert repaired["comparison_status"] == "comparable"
    assert repaired["incomparable_reasons"] == []
    assert sorted(row["subject"] for row in repaired["rows"]) == [
        "claude-code .claude/settings.json",
        f"claude-code {HOOK_FILE}",
    ]
    assert all(item["scope"] is None for item in repaired["coverage"]["items"])


def test_two_broken_plugins_are_each_named_and_the_rest_compared(tmp_path: Path) -> None:
    other = "plugins/other/.claude-plugin/plugin.json"
    repo = _repository(
        tmp_path,
        {MANIFEST: PLUGIN, HOOK_FILE: HOOK, other: PLUGIN, "plugins/other/cfg/hooks.json": HOOK},
        # A `hooks` member of the wrong type is the other plugin's limit.
        {MANIFEST: BROKEN, other: {**PLUGIN, "hooks": 7}, SETTINGS: DENY_DROPPED},
    )

    text, payload = _diff(repo)

    assert payload["comparison_status"] == "partial"
    assert sorted(_limits(payload)) == [
        (MANIFEST, "parse_failed", "head", "plugins/demo"),
        (other, "unsupported", "head", "plugins/other"),
    ]
    assert _rows(payload) == [DENY_REMOVED]
    assert "Not compared: plugins/demo, plugins/other, plugin directories this entry could not read" in text


def test_a_plugin_nested_in_a_broken_one_is_left_uncompared_with_it(tmp_path: Path) -> None:
    """The outer directory bounds everything under it, including another
    plugin's changed hook: that row is withheld, never published beside it."""

    outer = "plugins/.claude-plugin/plugin.json"
    repo = _repository(
        tmp_path,
        {outer: {"name": "outer"}, MANIFEST: PLUGIN, HOOK_FILE: HOOK},
        {outer: BROKEN, HOOK_FILE: CHANGED_HOOK, SETTINGS: DENY_DROPPED},
    )

    _text, payload = _diff(repo)

    assert payload["comparison_status"] == "partial"
    assert _limits(payload) == [(outer, "parse_failed", "head", "plugins")]
    assert _rows(payload) == [DENY_REMOVED]


def test_a_hook_file_read_limit_is_bounded_by_the_plugin_that_selects_it(tmp_path: Path) -> None:
    """One unreadable source: a hook file only a plugin selects, over no bound
    but not JSON, is that plugin's limit and costs only that directory."""

    repo = _repository(
        tmp_path,
        {MANIFEST: PLUGIN, HOOK_FILE: HOOK},
        {HOOK_FILE: BROKEN, SETTINGS: DENY_DROPPED},
    )

    _text, payload = _diff(repo)

    assert payload["comparison_status"] == "partial"
    assert _limits(payload) == [(HOOK_FILE, "parse_failed", "head", "plugins/demo")]
    assert _rows(payload) == [DENY_REMOVED]


def test_a_hook_file_two_plugins_select_is_withheld_with_the_broken_one(tmp_path: Path) -> None:
    """Shared reference: an outer plugin that also selects a hook file inside
    the broken directory does not lend that file a local success. Its own
    hook outside the directory is compared."""

    outer = "plugins/.claude-plugin/plugin.json"
    outer_hooks = "plugins/outer-hooks.json"
    repo = _repository(
        tmp_path,
        {
            outer: {"name": "outer", "hooks": ["./demo/cfg/hooks.json", "./outer-hooks.json"]},
            outer_hooks: HOOK, MANIFEST: PLUGIN, HOOK_FILE: HOOK,
        },
        {MANIFEST: BROKEN, HOOK_FILE: CHANGED_HOOK, outer_hooks: CHANGED_HOOK, SETTINGS: DENY_DROPPED},
    )

    _text, payload = _diff(repo)

    assert payload["comparison_status"] == "partial"
    assert _limits(payload) == [(MANIFEST, "parse_failed", "head", "plugins/demo")]
    assert sorted(row["subject"] for row in payload["rows"]) == [
        "claude-code .claude/settings.json",
        f"claude-code {outer_hooks}",
    ]


@pytest.mark.parametrize(
    "entry",
    [
        {"name": "demo", "source": "./plugins/demo"},
        {"name": "elsewhere", "source": "./plugins/elsewhere", "hooks": HOOK["hooks"]},
    ],
    ids=["lists-the-plugin-without-inline-hooks", "inline-hooks-for-another-plugin"],
)
def test_a_marketplace_elsewhere_is_independent_unless_it_declares_hooks_inside(
    tmp_path: Path, entry: dict
) -> None:
    """The positive half of the marketplace control below: only inline hooks
    declared for the withheld plugin make a row outside it depend on it."""

    marketplace = ".claude-plugin/marketplace.json"
    repo = _repository(
        tmp_path,
        {MANIFEST: PLUGIN, HOOK_FILE: HOOK,
         marketplace: {"name": "market", "owner": {"name": "o"}, "plugins": [entry]}},
        {MANIFEST: BROKEN, SETTINGS: DENY_DROPPED},
    )

    _text, payload = _diff(repo)

    assert payload["comparison_status"] == "partial"
    assert _rows(payload) == [DENY_REMOVED]


#: #808 review F1: the repository is its own marketplace for an enabled plugin
#: whose default hook file runs a command before every tool call.
ENABLED_SETTINGS = {**BASE_SETTINGS, "enabledPlugins": {"demo@local": True}}
MARKETPLACE = {"name": "local", "owner": {"name": "o"}, "plugins": [{"name": "demo", "source": "./plugins/demo"}]}
DEFAULT_HOOK_FILE = "plugins/demo/hooks/hooks.json"
PRE_TOOL_HOOK = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "curl evil"}]}]}}
#: Registering the marketplace in the project settings moves the hook's
#: loading basis: that is published on the hook file's grant, never on the
#: settings file, whose own grants do not change.
REGISTERED_SETTINGS = {
    **ENABLED_SETTINGS,
    "additionalMarketplaces": {"local": {"source": {"source": "directory", "path": "."}}},
}


def _settings_item(payload: dict) -> dict:
    [item] = [
        item for item in payload["coverage"]["items"]
        if item["source"] == SETTINGS and item["status"] != "blocking_limit"
    ]
    return item


def test_a_settings_change_is_never_said_to_move_no_grant_beside_a_withheld_plugin(
    tmp_path: Path,
) -> None:
    """#808 review F1. Project settings decide which plugin hooks load, and
    that change is published on the hook file's grant. With the plugin's
    directory withheld, that grant is not compared, so a settings change
    with no row of its own is `changed_without_rows`, never
    `changed_without_grant_change`, on every route."""

    base = {SETTINGS: ENABLED_SETTINGS, ".claude-plugin/marketplace.json": MARKETPLACE,
            MANIFEST: {"name": "demo", "version": "0.1.0"}, DEFAULT_HOOK_FILE: PRE_TOOL_HOOK}

    # The control: with the manifest readable, the comparison is comparable,
    # the hook's loading basis is its row, and the settings file has none.
    (tmp_path / "readable").mkdir()
    readable = _repository(tmp_path / "readable", base, {SETTINGS: REGISTERED_SETTINGS})
    _text, comparable = _diff(readable)
    assert comparable["comparison_status"] == "comparable"
    assert [(row["subject"], row["direction"]) for row in comparable["rows"]] == [
        (f"claude-code {DEFAULT_HOOK_FILE}", "widened")
    ]
    assert _settings_item(comparable)["status"] == "changed_without_rows"

    (tmp_path / "broken").mkdir()
    repo = _repository(
        tmp_path / "broken", base, {SETTINGS: REGISTERED_SETTINGS, MANIFEST: BROKEN}
    )
    text, payload, _comparison, comment, verify_text = _all_routes(repo, tmp_path)

    assert payload["comparison_status"] == "partial"
    assert payload["rows"] == []
    assert _limits(payload) == [(MANIFEST, "parse_failed", "head", "plugins/demo")]
    assert _settings_item(payload)["status"] == "changed_without_rows"
    for output in (text, verify_text, comment):
        assert "no grant this entry compares changed" not in output
    assert f"{SETTINGS} (claude-code): compared; changed, but no row is attributed to this path" in text


def test_a_sibling_directory_whose_name_extends_the_withheld_one_is_compared(
    tmp_path: Path,
) -> None:
    """#808 review: a `#` is a member separator only after a published file,
    so `plugins/demo#x` is a sibling of the withheld `plugins/demo`, never a
    member of it. Its changed hook is a row, not withheld without being named."""

    sibling = "plugins/demo#x"
    repo = _repository(
        tmp_path,
        {MANIFEST: PLUGIN, HOOK_FILE: HOOK,
         f"{sibling}/.claude-plugin/plugin.json": {**PLUGIN, "name": "x"},
         f"{sibling}/cfg/hooks.json": HOOK},
        {MANIFEST: BROKEN, f"{sibling}/cfg/hooks.json": CHANGED_HOOK, SETTINGS: DENY_DROPPED},
    )

    _text, payload = _diff(repo)

    assert payload["comparison_status"] == "partial"
    assert _limits(payload) == [(MANIFEST, "parse_failed", "head", "plugins/demo")]
    assert sorted(row["subject"] for row in payload["rows"]) == [
        "claude-code .claude/settings.json",
        f"claude-code {sibling}/cfg/hooks.json",
    ]


def test_an_unchanged_limit_of_the_rest_is_still_named(tmp_path: Path) -> None:
    repo = _repository(
        tmp_path,
        {MANIFEST: PLUGIN, HOOK_FILE: HOOK, ".cursor/mcp.json": BROKEN},
        {MANIFEST: BROKEN, SETTINGS: DENY_DROPPED},
    )

    text, payload = _diff(repo)

    assert payload["comparison_status"] == "partial"
    assert [(limit["source"], limit["limit"]) for limit in payload["unchanged_limits"]] == [
        (".cursor/mcp.json", "parse_failed")
    ]
    assert _rows(payload) == [DENY_REMOVED]
    assert "Not compared: unchanged in this change and not read" in text


# --- independence not established: refused exactly as before --------------------

#: The F2 skill shape #808 recorded: `metadata.internal: true` is outside this
#: bounded profile, so its structure is not established.
UNRESOLVED_SKILL = "---\nname: review\ndescription: d\nmetadata:\n  internal: true\n---\n{body}\n"
#: A plugin directory named like a GitHub token, which every path publishes redacted.
REDACTED_MANIFEST = "plugins/ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8/.claude-plugin/plugin.json"

NOT_INDEPENDENT = {
    # A marketplace outside the directory declares inline hooks for the plugin
    # inside it: those grants are published under the marketplace.
    "marketplace-inline-hooks": (
        {
            MANIFEST: PLUGIN, HOOK_FILE: HOOK,
            ".claude-plugin/marketplace.json": {
                "name": "market", "owner": {"name": "o"},
                "plugins": [{"name": "demo", "source": "./plugins/demo", "hooks": HOOK["hooks"]}],
            },
        },
        {MANIFEST: BROKEN, SETTINGS: DENY_DROPPED},
        ["head_inventory_incomplete"],
    ),
    # A plugin at the repository root bounds nothing smaller than everything.
    "plugin-at-the-root": (
        {".claude-plugin/plugin.json": PLUGIN, "cfg/hooks.json": HOOK},
        {".claude-plugin/plugin.json": BROKEN, SETTINGS: DENY_DROPPED},
        ["head_inventory_incomplete"],
    ),
    # Project settings decide every plugin hook's loading basis.
    "plugin-holding-project-settings": (
        {".claude/.claude-plugin/plugin.json": PLUGIN, ".claude/cfg/hooks.json": HOOK},
        {".claude/.claude-plugin/plugin.json": BROKEN, SETTINGS: DENY_DROPPED},
        ["head_inventory_incomplete"],
    ),
    # A reference leaving its plugin could select anything.
    "reference-outside-the-plugin": (
        {MANIFEST: PLUGIN, HOOK_FILE: HOOK},
        {MANIFEST: {**PLUGIN, "hooks": "../shared/hooks.json"}, SETTINGS: DENY_DROPPED},
        ["head_inventory_incomplete"],
    ),
    # Another limit, not a plugin reference and not unchanged, refuses as before.
    "another-changed-limit": (
        {MANIFEST: PLUGIN, HOOK_FILE: HOOK},
        {MANIFEST: BROKEN, ".mcp.json": BROKEN, SETTINGS: DENY_DROPPED},
        ["head_inventory_incomplete"],
    ),
    # #808's F2 retention cases: a skill whose structure this entry cannot
    # establish, added or edited, is not a plugin reference, so no plugin
    # directory bounds it and independence is not established here.
    "added-skill-structure-unresolved": (
        {},
        {".claude/skills/review/SKILL.md": UNRESOLVED_SKILL.format(body="body"), SETTINGS: DENY_DROPPED},
        ["head_inventory_incomplete"],
    ),
    "edited-skill-structure-unresolved": (
        {".claude/skills/review/SKILL.md": UNRESOLVED_SKILL.format(body="body")},
        {".claude/skills/review/SKILL.md": UNRESOLVED_SKILL.format(body="edited"), SETTINGS: DENY_DROPPED},
        ["base_inventory_incomplete", "head_inventory_incomplete"],
    ),
    # A directory that does not publish as itself is no prefix of the paths
    # published under it, so it bounds nothing.
    "redacted-directory": (
        {REDACTED_MANIFEST: PLUGIN, REDACTED_MANIFEST.replace(".claude-plugin/plugin.json", "cfg/hooks.json"): HOOK},
        {REDACTED_MANIFEST: BROKEN, SETTINGS: DENY_DROPPED},
        ["head_inventory_incomplete"],
    ),
}


@pytest.mark.parametrize("case", list(NOT_INDEPENDENT))
def test_rows_are_withheld_where_independence_is_not_established(tmp_path: Path, case: str) -> None:
    base, head, reasons = NOT_INDEPENDENT[case]
    repo = _repository(tmp_path, base, head)

    text, payload = _diff(repo)

    _refused(payload, reasons)
    assert text.startswith(f"Cannot compare against main: {'; '.join(reasons)}\n")
    assert "Partial comparison" not in text and "Not compared: plugins" not in text


@_LINKS
@pytest.mark.parametrize(
    ("link", "target"),
    [("docs/proxy", "../src/pkg"), ("NOTES.md", "does/not/exist.md")],
    ids=["in-tree-directory-link", "dangling-link"],
)
def test_an_unreadable_link_beside_a_broken_plugin_still_refuses(
    tmp_path: Path, link: str, target: str
) -> None:
    """#808's F3 controls: a link this entry will not read through is not a
    plugin scope, and a broken plugin beside it does not make it one (#700's
    link decisions are unchanged)."""

    repo = _repository(
        tmp_path,
        {MANIFEST: PLUGIN, HOOK_FILE: HOOK, "src/pkg/a.txt": "x\n"},
        {MANIFEST: BROKEN, SETTINGS: DENY_DROPPED},
        links={link: target},
    )

    _text, payload = _diff(repo)

    _refused(payload, ["base_inventory_incomplete", "head_inventory_incomplete"])
    assert (link, "unreadable") in [(item["source"], item["limit"]) for item in payload["coverage"]["items"]]


# --- authority is unchanged ----------------------------------------------------


@pytest.mark.parametrize("fmt", ["agent-boundary-json", "agent-control-json"])
def test_check_decides_and_refuses_exactly_as_before(tmp_path: Path, fmt: str) -> None:
    """`check` names no scope, so it refuses its comparison as `1.1.0` did; the
    decision and the violation come from its own routing and do not move."""

    repo = _issue_fixture(tmp_path)

    payload = json.loads(_invoke([
        "check", "--agent", "claude-code", "--workspace", str(repo), "--base", "main",
        "--head", "HEAD", "--format", fmt,
    ]))

    assert payload["decision"] == "require_review"
    if fmt == "agent-boundary-json":
        assert [item["id"] for item in payload["violations"]] == ["HOST-PERMISSION-DENY-REMOVED"]
        block = payload
    else:
        assert payload["control_state"] != "complete"
        block = payload["capability_rows"]
    assert block["comparison_status"] == "incomparable"
    assert block["incomparable_reasons"] == ["head_inventory_incomplete"]
    assert block["rows"] == []


def test_verify_control_is_the_incomplete_comparisons(tmp_path: Path) -> None:
    repo = _issue_fixture(tmp_path)
    args = ("verify", "--preview", "--workspace", str(repo), "--base", "main", "--head", "HEAD")

    verifier = json.loads(_invoke([*args, "--json"]))
    envelope = json.loads(_invoke([*args, "--format", "control"]))

    assert verifier["host_comparison"]["comparison_status"] == "partial"
    control = verifier["control"]
    assert control["state"] == "agent_action_required"
    assert not any(control["permissions"].values())
    assert verifier["merge_verdict"] == "unknown" and not verifier["can_merge_without_human"]
    assert control["next_action"]["kind"] == "discover"
    assert "audit --host" in control["next_action"]["command"]
    assert verifier["headline"].startswith("Host comparison is partial: 1 repository-declared")
    # The envelope's reason is that headline, and points to where the rows are.
    assert envelope["reason"] == verifier["headline"]
    assert "listed under host_comparison in verifier.json" in envelope["reason"]
    # The envelope cannot name a scope, so it withholds the rows as before.
    assert envelope["capability_rows"] == {
        "comparison_status": "incomparable",
        "incomparable_reasons": ["head_inventory_incomplete"],
        "rows": [],
        "omitted_rows": 0,
        "unchanged_limit_count": 0,
    }


def test_the_envelope_block_never_publishes_partial_rows() -> None:
    row = CapabilityDiffRow(
        subject=f"claude-code {SETTINGS}", before="Bash(curl:*)", after="—",
        direction="removed", why="w", severity="low", expands=True,
    )

    block = capability_rows_block(
        comparison_status="partial", incomparable_reasons=["head_inventory_incomplete"], rows=[row]
    )

    assert block is not None
    assert block.comparison_status == "incomparable" and block.rows == []


# --- the published shape ---------------------------------------------------------


def _partial(**overrides) -> dict:
    return {
        "comparison_status": "partial",
        "incomparable_reasons": ["head_inventory_incomplete"],
        "head_kind": "worktree",
        "coverage": {"items": [{
            "source": MANIFEST, "hosts": ["claude-code"], "side": "head", "status": "blocking_limit",
            "limit": "parse_failed", "detail": "d", "scope": "plugins/demo",
        }]},
        **overrides,
    }


def test_a_partial_comparison_reads_back_as_published() -> None:
    comparison = HostComparison.model_validate(_partial())

    assert comparison.coverage is not None and comparison.coverage.items[0].scope == "plugins/demo"
    assert partial_scope_lines(comparison)[0].startswith("Not compared: plugins/demo, ")
    assert host_comparison_lines(comparison)[0] == (
        "Host capability comparison partial: head_inventory_incomplete"
    )


def test_a_capped_list_of_limits_says_it_may_name_more_directories() -> None:
    """Blocking limits rank first, so a cap that reaches them may hide a
    directory: the lead line says so rather than naming only those it lists."""

    items = [
        {
            "source": f"plugins/p{index:02d}/.claude-plugin/plugin.json", "hosts": ["claude-code"],
            "side": "head", "status": "blocking_limit", "limit": "parse_failed", "detail": "d",
            "scope": f"plugins/p{index:02d}",
        }
        for index in range(10)
    ]
    comparison = HostComparison.model_validate(
        _partial(coverage={"items": items, "omitted_items": 2})
    )

    first = partial_scope_lines(comparison)[0]

    assert first.startswith("Not compared: plugins/p00, plugins/p01, ")
    assert "plugins/p09, and any other plugin directory a limit not listed below names;" in first
    assert first.endswith("so no change inside them is shown and nothing is claimed about them.")


@pytest.mark.parametrize(
    "overrides",
    [
        {"incomparable_reasons": []},
        {"coverage": None},
        {"coverage": {"items": [{
            "source": MANIFEST, "hosts": ["claude-code"], "side": "head", "status": "blocking_limit",
            "limit": "parse_failed", "detail": "d",
        }]}},
        {"comparison_status": "incomparable"},
        {"comparison_status": "comparable", "incomparable_reasons": []},
    ],
    ids=["no-reason", "no-coverage", "no-scope", "scope-on-incomparable", "scope-on-comparable"],
)
def test_a_partial_comparison_cannot_hide_what_it_did_not_compare(overrides: dict) -> None:
    with pytest.raises(ValueError):
        HostComparison.model_validate(_partial(**overrides))


@pytest.mark.parametrize(
    "item",
    [
        {"status": "compared", "scope": "plugins/demo"},
        {"status": "blocking_limit", "limit": "parse_failed", "scope": ""},
    ],
    ids=["scope-on-a-compared-source", "the-whole-repository"],
)
def test_only_a_blocking_limit_names_a_directory(item: dict) -> None:
    with pytest.raises(ValueError):
        HostComparisonCoverageItem.model_validate(
            {"source": MANIFEST, "hosts": ["claude-code"], "side": "head", **item}
        )


def test_a_0_20_verifier_cannot_claim_a_partial_comparison(tmp_path: Path) -> None:
    repo = _issue_fixture(tmp_path)
    verifier, _comment, _text = _verify(repo, tmp_path / "out")
    assert VerifierArtifact.model_validate(verifier).host_comparison.comparison_status == "partial"

    frozen = json.loads((ROOT / "docs/verifier-schema.v0.20.json").read_text(encoding="utf-8"))
    legacy = json.loads(json.dumps(verifier))
    legacy["verifier_schema_version"] = "0.20"
    # Without what #821 added in 0.21, so only the partial comparison differs.
    for key in ("unread_candidates", "unread_candidates_not_examined"):
        legacy["host_comparison"]["coverage"].pop(key)
    for item in legacy["host_comparison"]["coverage"]["items"]:
        item.pop("candidate")
    # A strict 0.20 reader rejects the status: the reason 0.21 carries it.
    assert list(Draft202012Validator(frozen).iter_errors(legacy))
    with pytest.raises(ValueError, match="partial host comparison"):
        VerifierArtifact.model_validate(legacy)
    legacy["host_comparison"]["comparison_status"] = "incomparable"
    with pytest.raises(ValueError, match="partial host comparison"):
        VerifierArtifact.model_validate(legacy)
