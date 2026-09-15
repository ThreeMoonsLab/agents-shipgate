"""#714: a parsed hook file is not proof that a host loads it.

Three facts a static reader can meet, and the first proves neither of the
others: a parsed hook declaration, a supported reference in the repository
that selects it, and installation or session state that selects the plugin.
None of them proves a hook ran.

* A hook a settings layer (or Codex's `hooks.json`) declares is what the host
  documents loading: `execute`/`high`, an expansion. Unchanged.
* A hook a plugin manifest selects keeps its event and every command change,
  but whether the plugin is installed or enabled is external: a row, not an
  expansion.
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
        assert "no settings file or plugin manifest" in rows[0].why
        assert "not established" in rows[0].why
        assert payload["expansion_signals"] == []

    payload, rows = _compare(changed, empty)
    assert [(row.direction, row.expands) for row in rows] == [("removed", False)]
    assert rows[0].why.startswith("removes a hook declaration")


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

    assert (grant["access"], grant["risk"]) == ("execute", "high")
    assert hook_loading_basis(grant) == "plugin_manifest"
    assert payload["expansion_signals"] == [], "plugin enablement is not in the repository"
    assert [(row.subject, row.direction, row.expands) for row in rows] == [
        (f"claude-code {plugin_root}hooks/hooks.json", "added", False)
    ]
    assert "installed or enabled is not established" in rows[0].why
    _never_executed(root)


def test_a_default_hook_file_without_a_manifest_is_not_read(tmp_path: Path) -> None:
    """A plugin root is recognised by its manifest. Without one — a
    marketplace entry, say — the file is not followed; that limit is on the
    support page."""

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

    assert hook_loading_basis(grant) == "plugin_manifest"
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
        "config/extra-hooks.json#Stop": "plugin_manifest",
        "hooks/hooks.json#SessionStart": "plugin_manifest",
    }


def test_inline_manifest_hooks_are_selected(tmp_path: Path) -> None:
    root = _workspace(tmp_path, {".claude-plugin/plugin.json": {**PLUGIN, "hooks": HOOK["hooks"]}})

    grant = _hooks(_inventory(root))[".claude-plugin/plugin.json#SessionStart"]

    assert hook_loading_basis(grant) == "plugin_manifest"
    assert grant["access"] == "execute"


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
    assert "plugin manifest" in rows[0].why
    assert payload["expansion_signals"] == []


def test_a_manifest_edit_outside_hooks_is_not_drift(tmp_path: Path) -> None:
    root = _workspace(tmp_path, {".claude-plugin/plugin.json": PLUGIN, "hooks/hooks.json": HOOK})
    before = _inventory(root)
    _write(root, ".claude-plugin/plugin.json", {**PLUGIN, "version": "0.2.0"})

    payload, _rows = _compare(before, _inventory(root))

    assert payload["has_drift"] is False


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

    assert hook_loading_basis(grant) == "plugin_manifest"


@pytest.mark.parametrize(
    ("manifest", "source"),
    [
        ({**PLUGIN, "hooks": 42}, ".claude-plugin/plugin.json"),
        ({**PLUGIN, "hooks": ["./hooks/extra-hooks.json", 7]}, ".claude-plugin/plugin.json"),
        ({**PLUGIN, "hooks": "config/hooks.json"}, ".claude-plugin/plugin.json"),
        ({**PLUGIN, "hooks": "./../outside/hooks.json"}, ".claude-plugin/plugin.json"),
        ({**PLUGIN, "hooks": "./"}, ".claude-plugin/plugin.json"),
        ({**PLUGIN, "hooks": "./config/security.json"}, "config/security.json"),
        (["not", "an", "object"], ".claude-plugin/plugin.json"),
        ("{not json", ".claude-plugin/plugin.json"),
    ],
)
def test_a_malformed_reference_is_a_blocking_limit(
    tmp_path: Path, manifest: object, source: str
) -> None:
    root = _workspace(
        tmp_path,
        {
            ".claude-plugin/plugin.json": manifest,
            "config/security.json": HOOK,
            ".claude/hooks/hooks.json": HOOK,
        },
    )

    inventory = _inventory(root)
    blocking = [issue for issue in inventory["issues"] if issue["blocking"]]

    assert [(issue["host"], issue["source"]) for issue in blocking] == [("claude-code", source)]
    assert not inventory_is_complete(inventory)
    # The file nothing selected stays declared-only; nothing is invented.
    assert hook_loading_basis(_hooks(inventory)[".claude/hooks/hooks.json#SessionStart"]) == (
        "declared_only"
    )
    _never_executed(root)


def test_a_reference_to_a_missing_file_selects_nothing(tmp_path: Path) -> None:
    root = _workspace(tmp_path, {".claude-plugin/plugin.json": {**PLUGIN, "hooks": "./absent/hooks.json"}})

    inventory = _inventory(root)

    assert [
        (issue["source"], issue["blocking"]) for issue in inventory["issues"]
    ] == [("absent/hooks.json", False)]
    assert inventory_is_complete(inventory)
    assert _hooks(inventory) == {}


# --- saved baselines ----------------------------------------------------------


def test_a_baseline_that_recorded_execute_is_a_changed_row_not_a_widening(
    tmp_path: Path,
) -> None:
    """A baseline saved by 1.0.0 recorded this unselected file as
    `execute`/`high`. Re-reading it is one changed row that names the basis:
    not hidden, and not a widening."""

    root = _workspace(tmp_path, {".claude/hooks/hooks.json": HOOK})
    current = _inventory(root)
    recorded = json.loads(json.dumps(normalized_host_grants(current)))
    for grant in recorded["grants"]:
        if grant["kind"] == "hook":
            grant.update(access="execute", risk="high")
    baseline = {**build_host_grants_baseline(current), "inventory": recorded}

    payload = build_host_drift_payload(baseline=baseline, inventory=current, baseline_file="b")
    rows = capability_diff_rows(payload)

    assert payload["has_drift"] is True
    assert payload["expansion_signals"] == []
    assert [(row.direction, row.before, row.after, row.expands, row.severity) for row in rows] == [
        ("changed", "SessionStart", "SessionStart", False, "unknown")
    ]


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
