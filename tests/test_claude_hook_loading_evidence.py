"""#714: a parsed hook file is not proof that a host loads it.

Three facts a static reader can meet, and the first proves neither of the
others: a parsed hook declaration, a supported reference in the repository
that selects it, and installation or session state that selects the plugin.
None of them proves a hook ran.

* A hook a settings layer (or Codex's `hooks.json`) declares is what the host
  documents loading: `execute`/`high`, an expansion. Unchanged.
* A hook a plugin manifest or marketplace entry selects keeps its event and
  every command change as `execute`/`medium`, but whether the plugin is
  installed or enabled is external: a row, not an expansion.
* A hook file nothing selects stays visible with `access: unknown`.

Every case is read statically; no host is started and no hook command runs.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.boundary_registry import is_hook_declaration_file_name
from agents_shipgate.core.capability_diff_rows import capability_diff_rows
from agents_shipgate.core.host_grants import (
    HostStaticParseCache,
    build_host_boundary_snapshot,
    build_host_drift_payload,
    build_host_grants_baseline,
    hook_loading_basis,
    inventory_is_complete,
    normalized_host_grants,
)

runner = CliRunner()

SETTINGS = {"permissions": {"allow": ["Read(**)"]}}
#: A command no test may ever run: if a reader executed it, the marker exists.
HOOK = {
    "hooks": {
        "SessionStart": [
            {"matcher": "startup", "hooks": [{"type": "command", "command": "touch EXECUTED"}]}
        ]
    }
}
CHANGED_HOOK = {
    "hooks": {
        "SessionStart": [
            {"matcher": "startup", "hooks": [{"type": "command", "command": "touch EXECUTED-2"}]}
        ]
    }
}
PLUGIN = {"name": "demo", "version": "0.1.0"}
MARKETPLACE = {"name": "market", "owner": {"name": "owner"}}
DECLARED_ONLY_WHY = "no settings file, plugin manifest or marketplace entry"
NEUTRAL_REMOVAL_WHY = (
    "removes a hook declared in this file; whether a host loaded it is not established"
)


def _write(root: Path, relative: str, data: object) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


def _workspace(tmp_path: Path, files: dict[str, object]) -> Path:
    root = tmp_path / "repo"
    _write(root, ".claude/settings.json", SETTINGS)
    for relative, data in files.items():
        _write(root, relative, data)
    return root


def _inventory(root: Path) -> dict:
    return build_host_boundary_snapshot(root, cache=HostStaticParseCache()).inventory


def _hooks(inventory: dict) -> dict[str, dict]:
    return {
        f"{grant['source']}#{grant['event']}": grant
        for grant in inventory["grants"]
        if grant["kind"] == "hook"
    }


def _compare(before: dict, after: dict) -> tuple[dict, list]:
    payload = build_host_drift_payload(
        baseline=build_host_grants_baseline(before), inventory=after, baseline_file="b"
    )
    return payload, capability_diff_rows(payload)


def _never_executed(root: Path) -> None:
    assert not list(root.rglob("EXECUTED*")), "a hook command ran during static analysis"


# --- unreferenced artifacts ---------------------------------------------------


@pytest.mark.parametrize(
    "relative", [".claude/hooks/hooks.json", "examples/demo/.claude/hooks/hooks.json"]
)
def test_an_unreferenced_hook_file_is_declared_only(tmp_path: Path, relative: str) -> None:
    root = _workspace(tmp_path, {relative: HOOK})

    inventory = _inventory(root)
    grant = _hooks(inventory)[f"{relative}#SessionStart"]

    assert (grant["access"], grant["risk"]) == ("unknown", "unknown")
    assert hook_loading_basis(grant) == "declared_only"
    assert inventory_is_complete(inventory), "unknown loading is not missing evidence"
    _never_executed(root)


@pytest.mark.parametrize(
    "relative", [".claude/hooks/hooks.json", "examples/demo/.claude/hooks/hooks.json"]
)
def test_adding_or_changing_an_unreferenced_hook_is_a_row_never_an_expansion(
    tmp_path: Path, relative: str
) -> None:
    root = _workspace(tmp_path, {})
    empty = _inventory(root)
    _write(root, relative, HOOK)
    added = _inventory(root)
    _write(root, relative, CHANGED_HOOK)
    changed = _inventory(root)

    for before, after, direction in ((empty, added, "added"), (added, changed, "changed")):
        payload, rows = _compare(before, after)
        assert [(row.subject, row.direction, row.expands, row.severity) for row in rows] == [
            (f"claude-code {relative}", direction, False, "unknown")
        ]
        assert DECLARED_ONLY_WHY in rows[0].why
        assert "not established" in rows[0].why
        assert payload["expansion_signals"] == []

    payload, rows = _compare(changed, empty)
    assert [(row.direction, row.expands, row.why) for row in rows] == [
        ("removed", False, NEUTRAL_REMOVAL_WHY)
    ]


def test_a_hook_file_is_not_read_as_a_settings_file(tmp_path: Path) -> None:
    """Claude Code reads permissions from settings, never from a hook file.
    Reading one as settings published a wildcard allow from a path alone."""

    root = _workspace(
        tmp_path,
        {".claude/hooks/hooks.json": {**HOOK, "permissions": {"allow": ["Bash(*)"]}}},
    )

    sources = {
        (grant["kind"], grant["source"]) for grant in _inventory(root)["grants"]
    }

    assert ("hook", ".claude/hooks/hooks.json") in sources
    assert ("permission_rule", ".claude/hooks/hooks.json") not in sources


# --- supported references -----------------------------------------------------


def test_a_settings_hook_keeps_its_expansion(tmp_path: Path) -> None:
    root = _workspace(tmp_path, {})
    before = _inventory(root)
    _write(root, ".claude/settings.json", {**SETTINGS, **HOOK})

    after = _inventory(root)
    grant = _hooks(after)[".claude/settings.json#SessionStart"]
    payload, rows = _compare(before, after)

    assert (grant["access"], grant["risk"]) == ("execute", "high")
    assert hook_loading_basis(grant) == "host_configuration"
    assert payload["expansion_signals"] == ["hook_added: claude-code:.claude/settings.json"]
    assert [(row.direction, row.expands, row.why) for row in rows] == [
        ("added", True, "changes what runs around the agent's actions")
    ]


def test_the_codex_hook_file_keeps_its_expansion(tmp_path: Path) -> None:
    root = _workspace(tmp_path, {})
    before = _inventory(root)
    _write(root, ".codex/hooks.json", HOOK)

    payload, rows = _compare(before, _inventory(root))

    assert payload["expansion_signals"] == ["hook_added: codex:.codex/hooks.json"]
    assert [row.expands for row in rows] == [True]


@pytest.mark.parametrize("plugin_root", ["", "plugins/demo/"])
def test_a_plugin_default_hook_file_is_selected_but_not_loaded(
    tmp_path: Path, plugin_root: str
) -> None:
    root = _workspace(tmp_path, {f"{plugin_root}.claude-plugin/plugin.json": PLUGIN})
    before = _inventory(root)
    _write(root, f"{plugin_root}hooks/hooks.json", HOOK)

    after = _inventory(root)
    grant = _hooks(after)[f"{plugin_root}hooks/hooks.json#SessionStart"]
    payload, rows = _compare(before, after)

    assert (grant["access"], grant["risk"]) == ("execute", "medium")
    assert hook_loading_basis(grant) == "plugin_selected"
    assert payload["expansion_signals"] == [], "plugin enablement is not in the repository"
    assert [(row.subject, row.direction, row.expands, row.severity) for row in rows] == [
        (f"claude-code {plugin_root}hooks/hooks.json", "added", False, "medium")
    ]
    assert "installed or enabled is not established" in rows[0].why
    _never_executed(root)


def test_a_default_hook_file_with_no_plugin_root_is_not_read(tmp_path: Path) -> None:
    """Neither a manifest nor a marketplace entry makes this directory a
    plugin root, so the file is not followed."""

    root = _workspace(tmp_path, {"hooks/hooks.json": HOOK})

    assert _hooks(_inventory(root)) == {}


@pytest.mark.parametrize(
    "reference", ["./.claude/hooks/hooks.json", ["./.claude/hooks/hooks.json"]]
)
def test_a_custom_manifest_path_selects_the_named_file(tmp_path: Path, reference: object) -> None:
    root = _workspace(
        tmp_path,
        {
            ".claude-plugin/plugin.json": {**PLUGIN, "hooks": reference},
            ".claude/hooks/hooks.json": HOOK,
        },
    )

    inventory = _inventory(root)
    grant = _hooks(inventory)[".claude/hooks/hooks.json#SessionStart"]
    manifests = [a for a in inventory["artifacts"] if a["path"] == ".claude-plugin/plugin.json"]

    assert hook_loading_basis(grant) == "plugin_selected"
    assert [(a["kind"], a["parse_status"]) for a in manifests] == [("config", "parsed")]
    assert inventory_is_complete(inventory)


def test_a_custom_path_adds_to_the_default(tmp_path: Path) -> None:
    root = _workspace(
        tmp_path,
        {
            ".claude-plugin/plugin.json": {**PLUGIN, "hooks": "./config/extra-hooks.json"},
            "hooks/hooks.json": HOOK,
            "config/extra-hooks.json": {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "true"}]}]}},
        },
    )

    grants = _hooks(_inventory(root))

    assert {key: hook_loading_basis(grant) for key, grant in grants.items()} == {
        "config/extra-hooks.json#Stop": "plugin_selected",
        "hooks/hooks.json#SessionStart": "plugin_selected",
    }


def test_inline_manifest_hooks_are_selected(tmp_path: Path) -> None:
    root = _workspace(tmp_path, {".claude-plugin/plugin.json": {**PLUGIN, "hooks": HOOK["hooks"]}})

    grant = _hooks(_inventory(root))[".claude-plugin/plugin.json#SessionStart"]

    assert hook_loading_basis(grant) == "plugin_selected"
    assert (grant["access"], grant["risk"]) == ("execute", "medium")


def test_selecting_a_declared_file_changes_its_basis_without_claiming_expansion(
    tmp_path: Path,
) -> None:
    root = _workspace(
        tmp_path,
        {".claude-plugin/plugin.json": PLUGIN, ".claude/hooks/hooks.json": HOOK},
    )
    before = _inventory(root)
    _write(root, ".claude-plugin/plugin.json", {**PLUGIN, "hooks": "./.claude/hooks/hooks.json"})

    payload, rows = _compare(before, _inventory(root))

    assert [(row.subject, row.direction, row.expands) for row in rows] == [
        ("claude-code .claude/hooks/hooks.json", "changed", False)
    ]
    assert "a plugin in this repository selects" in rows[0].why
    assert payload["expansion_signals"] == []


def test_a_manifest_edit_outside_hooks_is_not_drift(tmp_path: Path) -> None:
    root = _workspace(tmp_path, {".claude-plugin/plugin.json": PLUGIN, "hooks/hooks.json": HOOK})
    before = _inventory(root)
    _write(root, ".claude-plugin/plugin.json", {**PLUGIN, "version": "0.2.0"})

    payload, _rows = _compare(before, _inventory(root))

    assert payload["has_drift"] is False


# --- marketplace entries --------------------------------------------------------


def test_a_marketplace_entry_hooks_path_selects_the_file(tmp_path: Path) -> None:
    root = _workspace(
        tmp_path,
        {
            ".claude-plugin/marketplace.json": {
                **MARKETPLACE,
                "plugins": [
                    {"name": "demo", "source": "./plugins/demo", "hooks": "./config/hooks.json"}
                ],
            },
            "plugins/demo/config/hooks.json": HOOK,
        },
    )

    inventory = _inventory(root)
    grant = _hooks(inventory)["plugins/demo/config/hooks.json#SessionStart"]

    assert hook_loading_basis(grant) == "plugin_selected"
    assert [
        (a["kind"], a["parse_status"])
        for a in inventory["artifacts"]
        if a["path"] == ".claude-plugin/marketplace.json"
    ] == [("config", "parsed")]
    assert inventory_is_complete(inventory)


def test_a_non_strict_marketplace_entry_with_inline_hooks_is_selected(tmp_path: Path) -> None:
    root = _workspace(
        tmp_path,
        {
            ".claude-plugin/marketplace.json": {
                **MARKETPLACE,
                "plugins": [
                    {"name": "demo", "source": "./plugins/demo", "strict": False, "hooks": HOOK["hooks"]}
                ],
            },
        },
    )

    grant = _hooks(_inventory(root))[".claude-plugin/marketplace.json#plugins.demo#SessionStart"]

    assert hook_loading_basis(grant) == "plugin_selected"


@pytest.mark.parametrize(
    "marketplace",
    [
        {**MARKETPLACE, "plugins": [{"name": "demo", "source": "./plugins/demo"}]},
        {
            **MARKETPLACE,
            "metadata": {"pluginRoot": "./plugins"},
            "plugins": [{"name": "demo", "source": "demo"}],
        },
    ],
    ids=["relative-source", "plugin-root"],
)
def test_a_manifestless_marketplace_plugin_selects_its_default_hooks(
    tmp_path: Path, marketplace: dict
) -> None:
    root = _workspace(
        tmp_path,
        {".claude-plugin/marketplace.json": marketplace, "plugins/demo/hooks/hooks.json": HOOK},
    )

    grant = _hooks(_inventory(root))["plugins/demo/hooks/hooks.json#SessionStart"]

    assert hook_loading_basis(grant) == "plugin_selected"


def test_a_remote_marketplace_source_selects_nothing_in_the_repository(tmp_path: Path) -> None:
    root = _workspace(
        tmp_path,
        {
            ".claude-plugin/marketplace.json": {
                **MARKETPLACE,
                "plugins": [{"name": "demo", "source": {"source": "github", "repo": "o/r"}}],
            },
            "plugins/demo/hooks/hooks.json": HOOK,
        },
    )

    assert _hooks(_inventory(root)) == {}


def test_a_loopkit_shaped_marketplace_leaves_the_hook_file_declared_only(tmp_path: Path) -> None:
    """The historical wiring: a `./` marketplace entry with no `hooks` or
    `strict`, a manifest with no `hooks`, and a `hooks/` holding only a git
    hook script."""

    root = _workspace(
        tmp_path,
        {
            ".claude-plugin/marketplace.json": {
                **MARKETPLACE, "plugins": [{"name": "loopkit", "source": "./"}],
            },
            ".claude-plugin/plugin.json": {"name": "loopkit"},
            ".claude/hooks/hooks.json": HOOK,
            "hooks/pre-commit": "#!/bin/sh\n",
        },
    )

    grants = _hooks(_inventory(root))

    assert list(grants) == [".claude/hooks/hooks.json#SessionStart"]
    assert hook_loading_basis(grants[".claude/hooks/hooks.json#SessionStart"]) == "declared_only"


@pytest.mark.parametrize(
    ("marketplace", "source"),
    [
        ("{not json", ".claude-plugin/marketplace.json"),
        ({**MARKETPLACE, "plugins": "nope"}, ".claude-plugin/marketplace.json"),
        (
            {**MARKETPLACE, "plugins": [{"name": "demo", "source": "./../elsewhere"}]},
            ".claude-plugin/marketplace.json",
        ),
        (
            {**MARKETPLACE, "plugins": [{"name": "demo", "source": "./plugins/demo", "hooks": 42}]},
            ".claude-plugin/marketplace.json",
        ),
        (
            {
                **MARKETPLACE,
                "plugins": [{"name": "demo", "source": "./plugins/demo", "hooks": "./config/other.json"}],
            },
            "plugins/demo/config/other.json",
        ),
    ],
)
def test_a_malformed_marketplace_reference_is_named_without_blocking(
    tmp_path: Path, marketplace: object, source: str
) -> None:
    root = _workspace(
        tmp_path,
        {
            ".claude-plugin/marketplace.json": marketplace,
            "plugins/demo/config/other.json": HOOK,
            ".claude/hooks/hooks.json": HOOK,
        },
    )

    inventory = _inventory(root)

    assert [(issue["source"], issue["blocking"]) for issue in inventory["issues"]] == [
        (source, False)
    ]
    assert inventory_is_complete(inventory)
    assert hook_loading_basis(_hooks(inventory)[".claude/hooks/hooks.json#SessionStart"]) == (
        "declared_only"
    )


# --- unavailable activation and malformed references --------------------------


def test_activation_state_is_never_read_as_proof(tmp_path: Path) -> None:
    """Enabling the plugin in project settings does not make its hook a
    loaded one: installation from the marketplace is still external."""

    root = _workspace(
        tmp_path,
        {
            ".claude-plugin/plugin.json": PLUGIN,
            "hooks/hooks.json": HOOK,
            ".claude/settings.json": {**SETTINGS, "enabledPlugins": {"demo@local": True}},
        },
    )

    grant = _hooks(_inventory(root))["hooks/hooks.json#SessionStart"]

    assert hook_loading_basis(grant) == "plugin_selected"


@pytest.mark.parametrize(
    ("manifest", "source"),
    [
        ({**PLUGIN, "hooks": 42}, ".claude-plugin/plugin.json"),
        ({**PLUGIN, "hooks": ["./hooks/extra-hooks.json", 7]}, ".claude-plugin/plugin.json"),
        ({**PLUGIN, "hooks": "config/hooks.json"}, ".claude-plugin/plugin.json"),
        ({**PLUGIN, "hooks": "./../outside/hooks.json"}, ".claude-plugin/plugin.json"),
        ({**PLUGIN, "hooks": "./"}, ".claude-plugin/plugin.json"),
        ({**PLUGIN, "hooks": "./config/security.json"}, "config/security.json"),
        ({**PLUGIN, "hooks": "./config/webhooks.json"}, "config/webhooks.json"),
        (["not", "an", "object"], ".claude-plugin/plugin.json"),
        ("{not json", ".claude-plugin/plugin.json"),
    ],
)
def test_a_malformed_manifest_reference_is_a_blocking_limit(
    tmp_path: Path, manifest: object, source: str
) -> None:
    root = _workspace(
        tmp_path,
        {
            ".claude-plugin/plugin.json": manifest,
            "config/security.json": HOOK,
            "config/webhooks.json": HOOK,
            ".claude/hooks/hooks.json": HOOK,
        },
    )

    snapshot = build_host_boundary_snapshot(root, cache=HostStaticParseCache())
    blocking = [issue for issue in snapshot.inventory["issues"] if issue["blocking"]]

    assert [(issue["host"], issue["source"]) for issue in blocking] == [("claude-code", source)]
    assert not inventory_is_complete(snapshot.inventory)
    assert {issue["issue_id"] for issue in blocking} <= snapshot.plugin_reference_issue_ids
    # The file nothing selected stays declared-only; nothing is invented.
    assert hook_loading_basis(
        _hooks(snapshot.inventory)[".claude/hooks/hooks.json#SessionStart"]
    ) == "declared_only"
    _never_executed(root)


def test_hook_declaration_names_start_at_a_boundary() -> None:
    assert is_hook_declaration_file_name("hooks/hooks.json")
    assert is_hook_declaration_file_name("config/security-hooks.json")
    assert is_hook_declaration_file_name("config/team_hooks.json")
    assert is_hook_declaration_file_name("config/claude.hooks.json")
    assert not is_hook_declaration_file_name("config/webhooks.json")
    assert not is_hook_declaration_file_name("config/hooks.json.bak")


def test_a_reference_to_a_missing_file_selects_nothing(tmp_path: Path) -> None:
    root = _workspace(tmp_path, {".claude-plugin/plugin.json": {**PLUGIN, "hooks": "./absent/hooks.json"}})

    inventory = _inventory(root)

    assert [
        (issue["source"], issue["blocking"]) for issue in inventory["issues"]
    ] == [("absent/hooks.json", False)]
    assert inventory_is_complete(inventory)
    assert _hooks(inventory) == {}


@pytest.mark.parametrize("skipped", ["node_modules", ".venv"])
def test_a_reference_into_a_skipped_directory_is_named_as_unread(
    tmp_path: Path, skipped: str
) -> None:
    root = _workspace(
        tmp_path,
        {
            ".claude-plugin/plugin.json": {**PLUGIN, "hooks": f"./{skipped}/pkg/hooks.json"},
            f"{skipped}/pkg/hooks.json": HOOK,
        },
    )

    inventory = _inventory(root)

    assert [(issue["source"], issue["blocking"]) for issue in inventory["issues"]] == [
        (f"{skipped}/pkg/hooks.json", False)
    ]
    message = inventory["issues"][0]["message"]
    assert f"`{skipped}`" in message and "does not walk" in message
    assert "not in the repository" not in message
    assert inventory_is_complete(inventory)
    assert _hooks(inventory) == {}


@pytest.mark.parametrize("content", [{"description": "no events"}, {"hooks": []}])
def test_a_selected_file_without_a_hooks_object_is_named(tmp_path: Path, content: dict) -> None:
    root = _workspace(tmp_path, {".claude-plugin/plugin.json": PLUGIN, "hooks/hooks.json": content})

    inventory = _inventory(root)

    assert [(issue["source"], issue["blocking"]) for issue in inventory["issues"]] == [
        ("hooks/hooks.json", False)
    ]
    assert "no `hooks` object" in inventory["issues"][0]["message"]
    assert _hooks(inventory) == {}


# --- saved baselines ----------------------------------------------------------


def _recorded_by_1_0_0(inventory: dict) -> dict:
    """The same inventory as a 1.0.0 baseline recorded it: every hook file
    `execute`/`high`, whatever selected it."""

    recorded = json.loads(json.dumps(normalized_host_grants(inventory)))
    for grant in recorded["grants"]:
        if grant["kind"] == "hook":
            grant.update(access="execute", risk="high")
    return {**build_host_grants_baseline(inventory), "inventory": recorded}


def test_a_baseline_that_recorded_execute_is_a_changed_row_not_a_widening(
    tmp_path: Path,
) -> None:
    """A baseline saved by 1.0.0 recorded this unselected file as
    `execute`/`high`. Re-reading it is one changed row that names the basis:
    not hidden, and not a widening."""

    root = _workspace(tmp_path, {".claude/hooks/hooks.json": HOOK})
    current = _inventory(root)

    payload = build_host_drift_payload(
        baseline=_recorded_by_1_0_0(current), inventory=current, baseline_file="b"
    )
    rows = capability_diff_rows(payload)

    assert payload["has_drift"] is True
    assert payload["expansion_signals"] == []
    assert [(row.direction, row.before, row.after, row.expands, row.severity) for row in rows] == [
        ("changed", "SessionStart", "SessionStart", False, "unknown")
    ]
    assert DECLARED_ONLY_WHY in rows[0].why


def test_a_removal_against_a_1_0_0_baseline_claims_no_selection(tmp_path: Path) -> None:
    root = _workspace(tmp_path, {".claude/hooks/hooks.json": HOOK})
    baseline = _recorded_by_1_0_0(_inventory(root))
    (root / ".claude" / "hooks" / "hooks.json").unlink()

    payload = build_host_drift_payload(baseline=baseline, inventory=_inventory(root), baseline_file="b")
    rows = capability_diff_rows(payload)
    recorded = [g for g in baseline["inventory"]["grants"] if g["kind"] == "hook"]

    assert [hook_loading_basis(grant) for grant in recorded] == ["unestablished"]
    assert [(row.direction, row.expands, row.why) for row in rows] == [
        ("removed", False, NEUTRAL_REMOVAL_WHY)
    ]
    assert "plugin" not in rows[0].why


@pytest.mark.parametrize(
    ("grant", "basis"),
    [
        ({"source": ".claude/settings.json", "access": "execute", "risk": "high"}, "host_configuration"),
        ({"source": "hooks/hooks.json", "access": "execute", "risk": "medium"}, "plugin_selected"),
        ({"source": ".claude/hooks/hooks.json", "access": "unknown", "risk": "unknown"}, "declared_only"),
        ({"source": ".claude/hooks/hooks.json", "access": "execute", "risk": "high"}, "unestablished"),
        (
            {"source": ".claude-plugin/marketplace.json#plugins.demo", "access": "execute", "risk": "medium"},
            "plugin_selected",
        ),
    ],
)
def test_the_basis_is_read_only_from_the_published_signature(grant: dict, basis: str) -> None:
    assert hook_loading_basis({"host": "claude-code", "kind": "hook", **grant}) == basis


# --- CLI surfaces ---------------------------------------------------------------


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _repository(tmp_path: Path, base: dict[str, object], head: dict[str, object]) -> Path:
    root = _workspace(tmp_path, base)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "T")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    _git(root, "checkout", "-qb", "change")
    for relative, data in head.items():
        _write(root, relative, data)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "change")
    return root


def test_diff_reads_a_plugin_hook_on_both_sides(tmp_path: Path) -> None:
    """The base tree is materialized from paths alone, so a selected plugin
    hook must exist there too, or a changed command reads as an addition."""

    root = _repository(
        tmp_path,
        {"plugins/demo/.claude-plugin/plugin.json": PLUGIN, "plugins/demo/hooks/hooks.json": HOOK},
        {"plugins/demo/hooks/hooks.json": CHANGED_HOOK},
    )

    result = runner.invoke(app, ["diff", "--workspace", str(root), "--base", "main", "--json"])
    payload = json.loads(result.output)

    assert result.exit_code == 0, result.output
    assert [
        (row["subject"], row["direction"], row["expands"]) for row in payload["rows"]
    ] == [("claude-code plugins/demo/hooks/hooks.json", "changed", False)]
    _never_executed(root)


def test_diff_reads_a_marketplace_selected_hook_on_both_sides(tmp_path: Path) -> None:
    marketplace = {**MARKETPLACE, "plugins": [{"name": "demo", "source": "./plugins/demo"}]}
    root = _repository(
        tmp_path,
        {".claude-plugin/marketplace.json": marketplace, "plugins/demo/hooks/hooks.json": HOOK},
        {"plugins/demo/hooks/hooks.json": CHANGED_HOOK},
    )

    result = runner.invoke(app, ["diff", "--workspace", str(root), "--base", "main", "--json"])
    payload = json.loads(result.output)

    assert [(row["direction"], row["expands"]) for row in payload["rows"]] == [("changed", False)]


def test_diff_text_does_not_mark_a_declared_only_hook_as_widening(tmp_path: Path) -> None:
    root = _repository(tmp_path, {}, {".claude/hooks/hooks.json": HOOK})

    result = runner.invoke(app, ["diff", "--workspace", str(root), "--base", "main"])

    assert result.exit_code == 0, result.output
    assert "⚠" not in result.output
    assert "widening" not in result.output
    assert "whether a host loads it is not established" in result.output


def test_check_publishes_the_basis_and_keeps_its_review_route(tmp_path: Path) -> None:
    """`check`'s decision is path-based and unchanged: a changed
    `.claude/hooks/hooks.json` still routes to protected-surface review. Its
    rows now say the file is declared only."""

    root = _repository(tmp_path, {}, {".claude/hooks/hooks.json": HOOK})

    result = runner.invoke(
        app,
        [
            "check", "--workspace", str(root), "--base", "main", "--head", "HEAD",
            "--format", "agent-boundary-json",
        ],
    )
    payload = json.loads(result.output)

    assert payload["decision"] == "require_review"
    assert [item["id"] for item in payload["violations"]] == [
        "BOUNDARY-PROTECTED-SURFACE-UNCLASSIFIED"
    ]
    assert [(row["subject"], row["expands"], row["severity"]) for row in payload["rows"]] == [
        ("claude-code .claude/hooks/hooks.json", False, "unknown")
    ]
    assert "not established" in payload["rows"][0]["why"]


# --- review cycle 1, P1: an untouched plugin limit leaves check as 1.0.0 had it ---

MALFORMED_NESTED_MANIFEST = "examples/bad/.claude-plugin/plugin.json"
DENY_SETTINGS = {"permissions": {"allow": ["Read(**)"], "deny": ["Bash(rm -rf:*)"]}}


def _untouched_malformed_manifest(tmp_path: Path, head: dict[str, object]) -> Path:
    base = {
        ".claude/settings.json": DENY_SETTINGS,
        MALFORMED_NESTED_MANIFEST: "{not json",
        "README.md": "# base\n",
    }
    root = _repository(tmp_path, base, head)
    # The witness: without check's exclusion this blocking limit is what
    # refused the comparison and required review.
    snapshot = build_host_boundary_snapshot(root, cache=HostStaticParseCache())
    limits = [
        issue for issue in snapshot.inventory["issues"]
        if issue["blocking"] and issue["source"] == MALFORMED_NESTED_MANIFEST
    ]
    assert limits and {issue["issue_id"] for issue in limits} <= snapshot.plugin_reference_issue_ids
    return root


def _check(root: Path, fmt: str):
    return runner.invoke(
        app,
        ["check", "--workspace", str(root), "--base", "main", "--head", "HEAD", "--format", fmt],
    )


@pytest.mark.parametrize("fmt", ["text", "agent-boundary-json", "agent-control-json"])
def test_check_allows_a_readme_change_beside_an_untouched_malformed_manifest(
    tmp_path: Path, fmt: str
) -> None:
    root = _untouched_malformed_manifest(tmp_path, {"README.md": "# changed\n"})

    result = _check(root, fmt)

    assert result.exit_code == 0, result.output
    if fmt == "text":
        assert "Control: complete" in result.output
        assert "No static host-grant changes detected" in result.output
        return
    payload = json.loads(result.output)
    assert payload["decision"] == "allow"
    if fmt == "agent-boundary-json":
        assert payload["violations"] == []
        assert payload["input_coverage"] == "complete"
        assert payload["comparison_status"] == "comparable"
    else:
        assert payload["control_state"] == "complete"
        assert payload["capability_rows"]["comparison_status"] == "comparable"


@pytest.mark.parametrize("fmt", ["text", "agent-boundary-json", "agent-control-json"])
def test_check_keeps_a_deny_rule_row_beside_an_untouched_malformed_manifest(
    tmp_path: Path, fmt: str
) -> None:
    root = _untouched_malformed_manifest(
        tmp_path, {".claude/settings.json": {"permissions": {"allow": ["Read(**)"]}}}
    )

    result = _check(root, fmt)

    assert result.exit_code == 0, result.output
    if fmt == "text":
        assert "removes a denial the agent was subject to" in result.output
        return
    payload = json.loads(result.output)
    assert payload["decision"] == "require_review"
    if fmt == "agent-boundary-json":
        assert [item["id"] for item in payload["violations"]] == ["HOST-PERMISSION-DENY-REMOVED"]
        assert payload["comparison_status"] == "comparable"
        rows = payload["rows"]
    else:
        assert payload["capability_rows"]["comparison_status"] == "comparable"
        rows = payload["capability_rows"]["rows"]
    assert [(row["direction"], row["why"]) for row in rows] == [
        ("removed", "removes a denial the agent was subject to")
    ]


def test_diff_names_the_untouched_malformed_manifest_as_a_limit(tmp_path: Path) -> None:
    root = _untouched_malformed_manifest(tmp_path, {"README.md": "# changed\n"})

    result = runner.invoke(app, ["diff", "--workspace", str(root), "--base", "main", "--json"])
    payload = json.loads(result.output)

    assert payload["comparison_status"] == "comparable"
    assert [limit["source"] for limit in payload["unchanged_limits"]] == [MALFORMED_NESTED_MANIFEST]


def test_diff_refuses_a_newly_malformed_manifest(tmp_path: Path) -> None:
    """Not hidden where a limit can be carried: `diff` refuses a manifest the
    change itself broke."""

    root = _repository(tmp_path, {"README.md": "# base\n"}, {MALFORMED_NESTED_MANIFEST: "{not json"})

    result = runner.invoke(app, ["diff", "--workspace", str(root), "--base", "main", "--json"])
    payload = json.loads(result.output)

    assert payload["comparison_status"] == "incomparable"
    assert "head_inventory_incomplete" in payload["incomparable_reasons"]
