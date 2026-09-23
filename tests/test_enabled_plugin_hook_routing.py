"""#809: an enabled in-repository plugin's hook is routed wherever it lives.

#714 publishes a hook that a plugin selects, where the repository's own project
settings enable that plugin from a marketplace inside the repository, as
`execute`/`high` with an expansion signal: the authority a settings hook
carries. `check` did not route the file, because only registry paths such as
`.claude/hooks/hooks.json` reach its protected-surface review. A row the
engine called a widening therefore sat beside `decision: allow`.

The route takes two facts, and neither is enough alone: the path is one the
plugin hook reader opens (a hook-named file, a plugin manifest or a
marketplace), which its name decides, and the reader found, on a side of the
change, that it declares hooks of a plugin the project settings enable. A
hook a plugin only selects keeps #714's semantics: a row, never an expansion,
and no route. The decision is the existing protected-surface rule's and the
rows are still the host comparison's.

Every case is read statically; no host is started and no hook command runs.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.boundary_registry import (
    is_boundary_surface_path,
    is_claude_plugin_reference_path,
    is_enabled_plugin_hook_source,
)
from agents_shipgate.core.host_grants import (
    HostStaticParseCache,
    build_host_boundary_snapshot,
    hook_loading_basis,
)

runner = CliRunner()

PERMISSIONS = {"permissions": {"allow": ["Read(**)"]}}
ENABLING = {
    **PERMISSIONS,
    "extraKnownMarketplaces": {"market": {"source": {"source": "directory", "path": "."}}},
    "enabledPlugins": {"demo@market": True},
}
#: A command no test may ever run: if a reader executed it, the marker exists.
HOOK = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "touch EXECUTED"}]}]}}
CHANGED_HOOK = {
    "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "touch EXECUTED-2"}]}]}
}
MARKETPLACE = {
    "name": "market",
    "owner": {"name": "owner"},
    "plugins": [{"name": "demo", "source": "./plugins/demo"}],
}
PLUGIN_HOOK = "plugins/demo/cfg/hooks.json"
MANIFEST = "plugins/demo/.claude-plugin/plugin.json"

#: The issue's reproduction: the repository is its own marketplace, its
#: project settings enable the plugin, and the plugin selects a hook file at a
#: path no adapter names.
ENABLED_BASE: dict[str, object] = {
    "README.md": "# base\n",
    ".claude/settings.json": ENABLING,
    ".claude-plugin/marketplace.json": MARKETPLACE,
    MANIFEST: {"name": "demo", "hooks": "./cfg/hooks.json"},
    PLUGIN_HOOK: HOOK,
}
#: The same files without the enablement: the plugin selects the hook, and
#: whether it is installed or enabled is external state.
SELECTED_BASE: dict[str, object] = {**ENABLED_BASE, ".claude/settings.json": PERMISSIONS}
#: The registry-path shape the issue compares against: the same enabled
#: plugin selecting `.claude/hooks/hooks.json`, which `check` always routed.
REGISTRY_HOOK = ".claude/hooks/hooks.json"
REGISTRY_BASE: dict[str, object] = {
    "README.md": "# base\n",
    ".claude/settings.json": ENABLING,
    ".claude-plugin/marketplace.json": {**MARKETPLACE, "plugins": [{"name": "demo", "source": "./"}]},
    ".claude-plugin/plugin.json": {"name": "demo", "hooks": "./.claude/hooks/hooks.json"},
    REGISTRY_HOOK: HOOK,
}

#: The same enabled plugin selecting its hooks from a file whose name the
#: static reader does not follow. The host loads it; the reader names it as a
#: limit and reads nothing from it.
UNREAD_HOOK = "plugins/demo/cfg/lifecycle.json"
SKIPPED_HOOK = "plugins/demo/node_modules/pkg/hooks.json"
UNREAD_BASE: dict[str, object] = {
    "README.md": "# base\n",
    ".claude/settings.json": ENABLING,
    ".claude-plugin/marketplace.json": MARKETPLACE,
    MANIFEST: {"name": "demo", "hooks": "./cfg/lifecycle.json"},
    UNREAD_HOOK: HOOK,
}

PROTECTED = "BOUNDARY-PROTECTED-SURFACE-UNCLASSIFIED"
INCOMPLETE = "BOUNDARY-INPUT-INCOMPLETE"
ROUTED_EVIDENCE = {
    "kind": "protected_surface_unclassified",
    "hook_loading_basis": "project_enabled_plugin",
}


def _write(root: Path, relative: str, data: object) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _repository(
    tmp_path: Path, base: dict[str, object], head: dict[str, object | None]
) -> Path:
    """A two-commit repository; a `None` head value deletes the file."""

    root = tmp_path / "repo"
    for relative, data in base.items():
        _write(root, relative, data)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "T")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    _git(root, "checkout", "-qb", "change")
    for relative, data in head.items():
        if data is None:
            (root / relative).unlink()
        else:
            _write(root, relative, data)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "change", "--allow-empty")
    return root


def _check(root: Path, fmt: str = "agent-boundary-json", *refs: str):
    return runner.invoke(
        app,
        [
            "check", "--agent", "claude-code", "--workspace", str(root),
            *(refs or ("--base", "main")), "--format", fmt,
        ],
    )


def _payload(root: Path, *refs: str) -> dict:
    result = _check(root, "agent-boundary-json", *refs)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _violations(payload: dict) -> list[tuple[str, str, dict]]:
    return [(item["id"], item["path"], item["evidence"]) for item in payload["violations"]]


def _rows(rows: list[dict]) -> list[tuple[str, str, bool, str]]:
    return [(row["subject"], row["direction"], row["expands"], row["severity"]) for row in rows]


def _never_executed(root: Path) -> None:
    assert not list(root.rglob("EXECUTED*")), "a hook command ran during static analysis"


# --- the route's two facts ----------------------------------------------------


def test_the_route_needs_the_name_and_the_loading_evidence() -> None:
    evidence = {PLUGIN_HOOK, "plugins/demo/scripts/run.sh", ".claude-plugin/marketplace.json"}

    # Both facts.
    assert is_enabled_plugin_hook_source(PLUGIN_HOOK, evidence)
    assert is_enabled_plugin_hook_source(".claude-plugin/marketplace.json", evidence)
    # A name the reader opens, with no evidence that an enabled plugin loads it.
    assert is_claude_plugin_reference_path("plugins/other/hooks/hooks.json")
    assert not is_enabled_plugin_hook_source("plugins/other/hooks/hooks.json", evidence)
    # Evidence for a path the plugin hook reader never opens routes nothing.
    assert not is_claude_plugin_reference_path("plugins/demo/scripts/run.sh")
    assert not is_enabled_plugin_hook_source("plugins/demo/scripts/run.sh", evidence)
    # Spelling is compared as the reader matches references: case-insensitively.
    assert is_enabled_plugin_hook_source("./Plugins/Demo/cfg/hooks.json", evidence)
    # Every name the route can match is one a materialized base tree holds.
    assert all(is_boundary_surface_path(path) for path in evidence if is_claude_plugin_reference_path(path))


def test_the_snapshot_names_the_files_an_enabled_plugin_loads_hooks_from(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    files: dict[str, object] = {
        **ENABLED_BASE,
        ".claude-plugin/marketplace.json": {
            **MARKETPLACE,
            "plugins": [
                {"name": "demo", "source": "./plugins/demo", "strict": False, "hooks": HOOK["hooks"]},
                {"name": "other", "source": "./plugins/other"},
            ],
        },
        "plugins/other/hooks/hooks.json": HOOK,
        "config/hooks.json": HOOK,
    }
    for relative, data in files.items():
        _write(root, relative, data)

    snapshot = build_host_boundary_snapshot(root, cache=HostStaticParseCache())
    bases = {
        grant["source"]: hook_loading_basis(grant)
        for grant in snapshot.inventory["grants"]
        if grant["kind"] == "hook"
    }

    # The same selection publishes these hooks as `project_enabled_plugin`;
    # a hook-named file nothing selects outside the registry is not read.
    assert bases == {
        ".claude-plugin/marketplace.json#plugins.demo": "project_enabled_plugin",
        PLUGIN_HOOK: "project_enabled_plugin",
        "plugins/other/hooks/hooks.json": "plugin_selected",
    }
    assert snapshot.enabled_plugin_hook_sources == {".claude-plugin/marketplace.json", PLUGIN_HOOK}
    # Nothing about it is published.
    assert "enabled_plugin_hook_sources" not in json.dumps(snapshot.inventory)
    _never_executed(root)


def test_without_enablement_the_snapshot_names_no_file(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    for relative, data in {
        **SELECTED_BASE, MANIFEST: {"name": "demo", "hooks": ["./cfg/hooks.json", "./cfg/x.json"]}
    }.items():
        _write(root, relative, data)

    snapshot = build_host_boundary_snapshot(root, cache=HostStaticParseCache())

    assert snapshot.enabled_plugin_hook_sources == frozenset()
    assert snapshot.enabled_plugin_unread_hook_files == frozenset()


def test_the_snapshot_names_the_hook_files_an_enabled_plugin_selects_but_the_reader_does_not_open(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    files: dict[str, object] = {
        **UNREAD_BASE,
        MANIFEST: {
            "name": "demo",
            "hooks": ["./cfg/lifecycle.json", "./node_modules/pkg/hooks.json", "./cfg/hooks.json"],
        },
        "plugins/demo/cfg/hooks.json": HOOK,
    }
    for relative, data in files.items():
        _write(root, relative, data)

    snapshot = build_host_boundary_snapshot(root, cache=HostStaticParseCache())

    # A name the reader does not follow, and a directory the walk skips. The
    # hook-named file it reads is a routed source instead.
    assert snapshot.enabled_plugin_unread_hook_files == {
        UNREAD_HOOK, "plugins/demo/node_modules/pkg/hooks.json",
    }
    assert snapshot.enabled_plugin_hook_sources == {"plugins/demo/cfg/hooks.json"}
    # Each one is a named limit in the inventory, and the fact is not published.
    limited = {issue["source"] for issue in snapshot.inventory["issues"]}
    assert snapshot.enabled_plugin_unread_hook_files <= limited
    assert "enabled_plugin_unread_hook_files" not in json.dumps(snapshot.inventory)
    _never_executed(root)


# --- check ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "refs", [(), ("--base", "main", "--head", "HEAD")], ids=["worktree", "git-range"]
)
def test_the_issue_reproduction_routes_to_protected_surface_review(
    tmp_path: Path, refs: tuple[str, ...]
) -> None:
    root = _repository(tmp_path, ENABLED_BASE, {PLUGIN_HOOK: CHANGED_HOOK})

    payload = _payload(root, *refs)

    # The diagnostic decision and the permissions it grants are two answers,
    # and both moved: before #809 this was `allow` with `merge: true`.
    assert payload["decision"] == "require_review"
    assert payload["control"]["state"] == "agent_action_required"
    assert payload["control"]["permissions"]["merge"] is False
    assert payload["control"]["permissions"]["report_complete"] is False
    assert payload["control"]["completion_allowed"] is False
    assert _violations(payload) == [(PROTECTED, PLUGIN_HOOK, ROUTED_EVIDENCE)]
    assert payload["affected_hosts"] == ["claude-code"]
    claude = next(item for item in payload["host_coverage"] if item["adapter"] == "claude_code")
    assert (claude["status"], claude["paths"]) == ("complete", [PLUGIN_HOOK])
    assert payload["input_coverage"] == "complete"
    # The rows are the host comparison's, unchanged by the route.
    assert _rows(payload["rows"]) == [(f"claude-code {PLUGIN_HOOK}", "widened", True, "high")]
    _never_executed(root)


@pytest.mark.parametrize("fmt", ["text", "agent-control-json", "codex-boundary-json"])
def test_every_check_format_carries_the_route(tmp_path: Path, fmt: str) -> None:
    root = _repository(tmp_path, ENABLED_BASE, {PLUGIN_HOOK: CHANGED_HOOK})

    result = _check(root, fmt)

    assert result.exit_code == 0, result.output
    if fmt == "text":
        assert f"high / widened — claude-code {PLUGIN_HOOK}" in result.output
        assert "Control: agent_action_required" in result.output
        assert "You may not: merge, report_complete" in result.output
        assert f"Still owed human review: {PROTECTED}" in result.output
        return
    payload = json.loads(result.output)
    assert payload["decision"] == "require_review"
    if fmt == "agent-control-json":
        assert payload["control_state"] == "agent_action_required"
        assert payload["permissions"]["merge"] is False
        assert _rows(payload["capability_rows"]["rows"]) == [
            (f"claude-code {PLUGIN_HOOK}", "widened", True, "high")
        ]
    else:
        assert [(item["id"], item["path"]) for item in payload["violated_rules"]] == [
            (PROTECTED, PLUGIN_HOOK)
        ]


@pytest.mark.parametrize(
    ("base", "hook"),
    [(REGISTRY_BASE, REGISTRY_HOOK), (ENABLED_BASE, PLUGIN_HOOK)],
    ids=["registry-path", "plugin-path"],
)
@pytest.mark.parametrize("head_content", [CHANGED_HOOK, "{not json"], ids=["changed", "broken"])
def test_an_enabled_plugin_hook_is_routed_as_its_registry_path_is(
    tmp_path: Path, base: dict[str, object], hook: str, head_content: object
) -> None:
    """The issue's comparison: the same enabled hook at `.claude/hooks/hooks.json`
    always reached protected-surface review. At a plugin path it now gets the
    same decision, rules and control, and a head that breaks it is incomplete
    input on a routed file, exactly as at the registry path."""

    root = _repository(tmp_path, base, {hook: head_content})

    payload = _payload(root)

    broken = head_content == "{not json"
    assert payload["decision"] == "require_review"
    assert [(item["id"], item["path"]) for item in payload["violations"]] == [
        *([("BOUNDARY-INPUT-INCOMPLETE", hook)] if broken else []),
        (PROTECTED, hook),
    ]
    assert payload["control"]["state"] == (
        "human_review_required" if broken else "agent_action_required"
    )
    assert payload["control"]["permissions"]["merge"] is False
    assert payload["input_coverage"] == ("partial" if broken else "complete")
    assert payload["comparison_status"] == ("incomparable" if broken else "comparable")


def test_a_plugin_selected_hook_stays_unrouted(tmp_path: Path) -> None:
    """#714's semantics hold: a hook a plugin only selects is a row, never an
    expansion, and `check` does not route it."""

    root = _repository(tmp_path, SELECTED_BASE, {PLUGIN_HOOK: CHANGED_HOOK})

    payload = _payload(root)

    assert payload["decision"] == "allow"
    assert payload["violations"] == []
    assert payload["control"]["state"] == "complete"
    assert payload["control"]["permissions"]["merge"] is True
    assert payload["affected_hosts"] == []
    assert _rows(payload["rows"]) == [(f"claude-code {PLUGIN_HOOK}", "changed", False, "medium")]


@pytest.mark.parametrize(
    "head",
    [
        {"README.md": "# changed\n"},
        # A file in the enabled plugin the hook reader never opens.
        {"plugins/demo/scripts/run.sh": "echo\n"},
        # A hook-named file nothing selects.
        {"config/hooks.json": HOOK},
        # A manifest edit outside `hooks`: a selector, not a declaration.
        {MANIFEST: {"name": "demo", "version": "2.0.0", "hooks": "./cfg/hooks.json"}},
    ],
    ids=["readme", "plugin-script", "unselected-hook-file", "manifest-version"],
)
def test_nothing_else_beside_an_enabled_plugin_is_routed(
    tmp_path: Path, head: dict[str, object]
) -> None:
    root = _repository(tmp_path, ENABLED_BASE, head)

    payload = _payload(root)

    assert payload["decision"] == "allow"
    assert payload["violations"] == []
    assert payload["control"]["permissions"]["merge"] is True


def test_a_deleted_enabled_hook_file_is_routed_from_the_base_tree(tmp_path: Path) -> None:
    """Only the base tree shows the plugin loaded this file: the head removed
    both the file and the manifest's reference to it."""

    root = _repository(
        tmp_path, ENABLED_BASE, {PLUGIN_HOOK: None, MANIFEST: {"name": "demo"}}
    )

    payload = _payload(root)

    assert payload["decision"] == "require_review"
    assert _violations(payload) == [(PROTECTED, PLUGIN_HOOK, ROUTED_EVIDENCE)]
    assert _rows(payload["rows"]) == [(f"claude-code {PLUGIN_HOOK}", "removed", False, "high")]


def test_an_enabled_plugins_inline_marketplace_hooks_are_routed(tmp_path: Path) -> None:
    inline = {
        **MARKETPLACE,
        "plugins": [
            {"name": "demo", "source": "./plugins/demo", "strict": False, "hooks": HOOK["hooks"]}
        ],
    }
    base = {**ENABLED_BASE, ".claude-plugin/marketplace.json": inline, MANIFEST: {"name": "demo"}}
    del base[PLUGIN_HOOK]
    root = _repository(
        tmp_path,
        base,
        {".claude-plugin/marketplace.json": {**inline, "plugins": [
            {**inline["plugins"][0], "hooks": CHANGED_HOOK["hooks"]}
        ]}},
    )

    payload = _payload(root)

    assert payload["decision"] == "require_review"
    assert _violations(payload) == [(PROTECTED, ".claude-plugin/marketplace.json", ROUTED_EVIDENCE)]
    assert _rows(payload["rows"]) == [
        ("claude-code .claude-plugin/marketplace.json#plugins.demo", "widened", True, "high")
    ]


def test_a_change_only_to_what_a_manifest_selects_is_not_routed(tmp_path: Path) -> None:
    """The documented limit: a manifest that only references hook files is a
    selector, not a declaration, so retargeting it to a file that did not
    change is not routed. The rows still show both hooks."""

    root = _repository(
        tmp_path,
        {**ENABLED_BASE, "plugins/demo/cfg/other-hooks.json": CHANGED_HOOK},
        {MANIFEST: {"name": "demo", "hooks": "./cfg/other-hooks.json"}},
    )

    payload = _payload(root)

    assert payload["decision"] == "allow"
    assert payload["violations"] == []
    assert sorted(_rows(payload["rows"])) == [
        ("claude-code plugins/demo/cfg/hooks.json", "removed", False, "high"),
        ("claude-code plugins/demo/cfg/other-hooks.json", "added", True, "high"),
    ]


def test_an_uncommitted_hook_file_an_enabled_plugin_selects_is_routed(tmp_path: Path) -> None:
    root = _repository(tmp_path, ENABLED_BASE, {})
    _write(root, MANIFEST, {"name": "demo", "hooks": ["./cfg/hooks.json", "./cfg/extra-hooks.json"]})
    _write(root, "plugins/demo/cfg/extra-hooks.json", CHANGED_HOOK)

    payload = _payload(root)

    assert payload["decision"] == "require_review"
    assert _violations(payload) == [
        (PROTECTED, "plugins/demo/cfg/extra-hooks.json", ROUTED_EVIDENCE)
    ]


def test_a_provided_diff_is_routed_from_the_workspace_tree(tmp_path: Path) -> None:
    root = _repository(tmp_path, ENABLED_BASE, {PLUGIN_HOOK: CHANGED_HOOK})
    diff = subprocess.run(
        ["git", "-C", str(root), "diff", "main", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout
    diff_file = tmp_path / "change.diff"
    diff_file.write_text(diff, encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "check", "--agent", "claude-code", "--workspace", str(root),
            "--diff", str(diff_file), "--format", "agent-boundary-json",
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0, result.output
    assert payload["decision"] == "require_review"
    assert _violations(payload) == [(PROTECTED, PLUGIN_HOOK, ROUTED_EVIDENCE)]


def test_an_unreadable_compared_commit_is_incomplete_input_not_a_guess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No compared commit can be materialized: not for the route's evidence,
    and not for the host comparison either, which reads trees the same way.
    Whether an enabled plugin loads the changed hook file is then unknown."""

    import agents_shipgate.cli.verify.host_comparison as host_comparison

    root = _repository(tmp_path, SELECTED_BASE, {PLUGIN_HOOK: CHANGED_HOOK})

    def refuse(*_args, **_kwargs):
        raise RuntimeError("archive refused")

    monkeypatch.setattr(host_comparison, "archive_tree", refuse)
    payload = _payload(root)

    assert payload["decision"] == "require_review"
    assert payload["control"]["state"] == "human_review_required"
    assert [
        (item["id"], item["path"], item["evidence"].get("code")) for item in payload["violations"]
    ] == [("BOUNDARY-INPUT-INCOMPLETE", PLUGIN_HOOK, "host_inventory_unreadable")]
    assert payload["input_coverage"] == "partial"
    assert payload["comparison_status"] == "incomparable"
    assert payload["rows"] == []


# --- a hook file the reader does not open -----------------------------------------


@pytest.mark.parametrize(
    ("base", "head"),
    [
        (UNREAD_BASE, {UNREAD_HOOK: CHANGED_HOOK}),
        # Selected from the marketplace entry, whose limits never block.
        (
            {
                **UNREAD_BASE,
                ".claude-plugin/marketplace.json": {
                    **MARKETPLACE,
                    "plugins": [
                        {"name": "demo", "source": "./plugins/demo", "hooks": "./cfg/lifecycle.json"}
                    ],
                },
                MANIFEST: {"name": "demo"},
            },
            {UNREAD_HOOK: CHANGED_HOOK},
        ),
        # Deleted with its reference: only the base shows the plugin loaded it.
        (UNREAD_BASE, {UNREAD_HOOK: None, MANIFEST: {"name": "demo"}}),
        # A hook-named file inside a directory the walk skips.
        (
            {
                **UNREAD_BASE,
                MANIFEST: {"name": "demo", "hooks": "./node_modules/pkg/hooks.json"},
                SKIPPED_HOOK: HOOK,
            },
            {SKIPPED_HOOK: CHANGED_HOOK},
        ),
    ],
    ids=["manifest-selected", "marketplace-selected", "deleted-with-reference", "skipped-directory"],
)
def test_a_changed_hook_file_an_enabled_plugin_loads_but_the_reader_cannot_is_incomplete_input(
    tmp_path: Path, base: dict[str, object], head: dict[str, object | None]
) -> None:
    """The host loads hooks from this file and the reader read none of them,
    so a change to it is input `check` could not read, not one it may allow."""

    root = _repository(tmp_path, base, head)
    (unread,) = [path for path in head if path != MANIFEST]

    payload = _payload(root)

    assert payload["decision"] == "require_review"
    assert payload["control"]["state"] == "human_review_required"
    assert payload["control"]["permissions"]["merge"] is False
    assert [
        (item["id"], item["path"], item["evidence"].get("code")) for item in payload["violations"]
    ] == [(INCOMPLETE, unread, "host_inventory_unsupported")]
    assert payload["input_coverage"] == "partial"
    assert payload["affected_hosts"] == ["claude-code"]
    claude = next(item for item in payload["host_coverage"] if item["adapter"] == "claude_code")
    assert (claude["status"], claude["paths"]) == ("partial", [unread])
    assert payload["rows"] == []
    _never_executed(root)


def test_such_a_file_a_plugin_only_selects_stays_as_it_was(tmp_path: Path) -> None:
    """Without the enablement, whether the plugin loads anything is external
    state (#714): the limit stays out of the check decision."""

    root = _repository(
        tmp_path,
        {**UNREAD_BASE, ".claude/settings.json": PERMISSIONS},
        {UNREAD_HOOK: CHANGED_HOOK},
    )

    payload = _payload(root)

    assert payload["decision"] == "allow"
    assert payload["violations"] == []
    assert payload["input_coverage"] == "complete"


def test_a_marketplace_the_head_makes_unparseable_is_routed_with_a_non_blocking_limit(
    tmp_path: Path,
) -> None:
    """The documented edge: an enabled plugin's inline hooks live in the
    marketplace, and the head breaks it. A marketplace limit never blocks, so
    the input stays complete; the route still requires review, and the
    comparison shows the unread inline hooks as removed."""

    inline = {
        **MARKETPLACE,
        "plugins": [
            {"name": "demo", "source": "./plugins/demo", "strict": False, "hooks": HOOK["hooks"]}
        ],
    }
    base = {**ENABLED_BASE, ".claude-plugin/marketplace.json": inline, MANIFEST: {"name": "demo"}}
    del base[PLUGIN_HOOK]
    root = _repository(tmp_path, base, {".claude-plugin/marketplace.json": "{not json"})

    payload = _payload(root)

    assert payload["decision"] == "require_review"
    assert payload["control"]["state"] == "agent_action_required"
    assert [(item["id"], item["path"]) for item in payload["violations"]] == [
        (PROTECTED, ".claude-plugin/marketplace.json")
    ]
    assert payload["input_coverage"] == "complete"
    assert _rows(payload["rows"]) == [
        ("claude-code .claude-plugin/marketplace.json#plugins.demo", "removed", False, "high")
    ]


# --- every surface --------------------------------------------------------------


def test_inventory_diff_check_verify_and_the_pr_comment_agree(tmp_path: Path) -> None:
    root = _repository(
        tmp_path,
        {**ENABLED_BASE, ".gitignore": "agents-shipgate-reports/\n"},
        {PLUGIN_HOOK: CHANGED_HOOK},
    )
    subject = f"claude-code {PLUGIN_HOOK}"
    widened = (subject, "widened", True, "high")

    # Inventory: the hook is one the host loads for this project.
    audit = runner.invoke(app, ["audit", "--host", "--workspace", str(root), "--json"])
    assert audit.exit_code == 0, audit.output
    (grant,) = [
        grant for grant in json.loads(audit.output)["grants"]
        if grant["kind"] == "hook" and grant["source"] == PLUGIN_HOOK
    ]
    assert (grant["access"], grant["risk"]) == ("execute", "high")
    assert hook_loading_basis(grant) == "project_enabled_plugin"

    # diff: the change widens what runs around the agent.
    diff = runner.invoke(app, ["diff", "--workspace", str(root), "--base", "main", "--json"])
    assert diff.exit_code == 0, diff.output
    assert _rows(json.loads(diff.output)["rows"]) == [widened]

    # check: the same row, no longer under `allow`.
    payload = _payload(root)
    assert _rows(payload["rows"]) == [widened]
    assert payload["decision"] == "require_review"
    assert payload["control"]["permissions"]["merge"] is False

    # verify and the PR comment: the same row, and no merge without a human.
    result = runner.invoke(
        app,
        ["verify", "--workspace", str(root), "--base", "main", "--head", "HEAD", "--format", "text"],
    )
    assert result.exit_code == 0, result.output
    verifier = json.loads((root / "agents-shipgate-reports/verifier.json").read_text())
    assert _rows(verifier["host_comparison"]["rows"]) == [widened]
    assert verifier["can_merge_without_human"] is False
    assert verifier["control"]["permissions"]["merge"] is False
    comment = (root / "agents-shipgate-reports/pr-comment.md").read_text()
    assert f"` {subject} `" in comment
    assert "` widened `" in comment
    _never_executed(root)


#: A gate whose own application surface is clean: one read-only MCP tool. On a
#: change beside it, `verify` passes, so any other verdict below comes from
#: the plugin hook.
GATE: dict[str, object] = {
    ".gitignore": "agents-shipgate-reports/\n",
    "tools.json": {
        "tools": [
            {
                "name": "docs.lookup",
                "description": "Look up internal documentation metadata.",
                "annotations": {"readOnlyHint": True},
                "auth": {"mode": "none"},
            }
        ]
    },
    "shipgate.yaml": """
version: "0.1"
project:
  name: plugin-hooks
agent:
  name: plugin-hooks-agent
  declared_purpose:
    - read documentation
environment:
  target: local
tool_sources:
  - id: tools
    type: mcp
    path: tools.json
agent_bindings:
  declarations:
    - agent: root
      complete: true
      tools:
        - tool: docs.lookup
          source_id: tools
      handoffs: []
      reason: reviewed binding
action_surface:
  actions:
    - tool: docs.lookup
      source_id: tools
      effect: read
      authority:
        mode: none
""",
}


@pytest.mark.parametrize(
    ("base", "head", "finding"),
    [
        (ENABLED_BASE, {"README.md": "# changed\n"}, None),
        (ENABLED_BASE, {PLUGIN_HOOK: CHANGED_HOOK}, (PROTECTED, PLUGIN_HOOK)),
        # Routed from the base tree: the head removed the file and its reference.
        (ENABLED_BASE, {PLUGIN_HOOK: None, MANIFEST: {"name": "demo"}}, (PROTECTED, PLUGIN_HOOK)),
        (SELECTED_BASE, {PLUGIN_HOOK: CHANGED_HOOK}, None),
        (UNREAD_BASE, {UNREAD_HOOK: CHANGED_HOOK}, (INCOMPLETE, UNREAD_HOOK)),
    ],
    ids=["readme", "changed", "deleted", "plugin-selected", "unread"],
)
def test_a_gated_verify_decides_as_check_does(
    tmp_path: Path,
    base: dict[str, object],
    head: dict[str, object | None],
    finding: tuple[str, str] | None,
) -> None:
    """With a manifest, `verify` is the release gate CI reads, and its PR
    comment is what a reviewer sees. It decides the plugin hook from the same
    two-sided evidence `check` does, so the two never disagree on merge."""

    root = _repository(tmp_path, {**base, **GATE}, head)

    payload = _payload(root)
    result = runner.invoke(
        app,
        [
            "verify", "--workspace", str(root), "--config", "shipgate.yaml",
            "--base", "main", "--head", "HEAD", "--format", "text",
        ],
    )

    assert result.exit_code in {0, 1}, result.output
    reports = root / "agents-shipgate-reports"
    verifier = json.loads((reports / "verifier.json").read_text())
    report = json.loads((reports / "report.json").read_text())
    boundary = [
        (item["check_id"], item["source"]["path"])
        for item in report["findings"]
        if item["check_id"].startswith("SHIP-AGENT-BOUNDARY-")
    ]
    comment = (reports / "pr-comment.md").read_text()
    if finding is None:
        assert payload["violations"] == []
        assert (verifier["decision"], verifier["merge_verdict"]) == ("passed", "mergeable")
        assert verifier["can_merge_without_human"] is True
        assert boundary == []
        assert "Release gate: `passed`" in comment
    else:
        assert [(item["id"], item["path"]) for item in payload["violations"]] == [finding]
        assert payload["control"]["permissions"]["merge"] is False
        assert (verifier["decision"], verifier["merge_verdict"]) == (
            "review_required", "human_review_required"
        )
        assert verifier["can_merge_without_human"] is False
        assert boundary == [(f"SHIP-AGENT-{finding[0]}", finding[1])]
        assert "Release gate: `review_required`" in comment
    _never_executed(root)
