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
from agents_shipgate.core.boundary_registry import (
    is_claude_plugin_manifest_path,
    is_claude_plugin_marketplace_path,
    is_hook_declaration_file_name,
)
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
    loaded one when no project settings layer registers its marketplace in
    this repository: installation from that marketplace is still external."""

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


#: A host-grants baseline that published `agents-shipgate==1.0.0` saved for
#: the `_self_enabled()` workspace, checked in because no synthesis from this
#: reader's own inventory can hold what 1.0.0 never read. Reproduced with:
#:
#:     python3.12 -m venv v && v/bin/pip install agents-shipgate==1.0.0
#:     # write the `_self_enabled()` files into <repo>, exactly as `_workspace` does
#:     v/bin/agents-shipgate audit --host --workspace <repo> --save-baseline
#:     cp <repo>/.agents-shipgate/host-grants.json tests/fixtures/<this file>
#:
#: `test_the_published_1_0_0_baseline_is_what_1_0_0_read` pins what makes it a
#: 1.0.0 artifact, so a regenerated copy cannot quietly become a current one.
PUBLISHED_1_0_0_BASELINE = (
    Path(__file__).parent / "fixtures" / "host-grants-1.0.0-enabled-plugin.json"
)


def _published_1_0_0_baseline() -> dict:
    return json.loads(PUBLISHED_1_0_0_BASELINE.read_text(encoding="utf-8"))


def _recorded_by_1_0_0(inventory: dict) -> dict:
    """The same inventory as a 1.0.0 baseline recorded it: every hook file
    `execute`/`high`, whatever selected it.

    Faithful only where 1.0.0 read the same files. 1.0.0 read no plugin
    manifest and no marketplace, so an inventory holding one cannot be
    rewritten into a 1.0.0 baseline: the artifacts and observed sources drift
    compares would be this reader's, and the comparison could never fail. Use
    `_published_1_0_0_baseline()` there.
    """

    newly_read = sorted(
        artifact["path"]
        for artifact in inventory["artifacts"]
        if is_claude_plugin_manifest_path(artifact["path"])
        or is_claude_plugin_marketplace_path(artifact["path"])
    )
    assert not newly_read, (
        f"1.0.0 never read {newly_read}; a synthesized baseline would hold artifacts and "
        "observed sources it cannot have had. Use _published_1_0_0_baseline()."
    )
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

    # 1.0.0's pair for every hook file is the pair an enabled plugin's hook
    # carries now. The removal is described from that baseline grant, so it
    # must name no basis at all: neither selection nor enablement.
    assert [hook_loading_basis(grant) for grant in recorded] == ["project_enabled_plugin"]
    assert [(row.direction, row.expands, row.why) for row in rows] == [
        ("removed", False, NEUTRAL_REMOVAL_WHY)
    ]
    assert "plugin" not in rows[0].why
    assert "enable" not in rows[0].why


@pytest.mark.parametrize(
    ("grant", "basis"),
    [
        ({"source": ".claude/settings.json", "access": "execute", "risk": "high"}, "host_configuration"),
        ({"source": "hooks/hooks.json", "access": "execute", "risk": "medium"}, "plugin_selected"),
        ({"source": ".claude/hooks/hooks.json", "access": "unknown", "risk": "unknown"}, "declared_only"),
        # The one pair shared with history: 1.0.0 recorded every hook file as
        # execute/high. Only a current grant's basis is ever read (see
        # test_a_removal_against_a_1_0_0_baseline_claims_no_selection).
        ({"source": ".claude/hooks/hooks.json", "access": "execute", "risk": "high"}, "project_enabled_plugin"),
        ({"source": ".claude/hooks/hooks.json", "access": "execute", "risk": "low"}, "unestablished"),
        ({"source": ".claude/hooks/hooks.json", "access": "read", "risk": "medium"}, "unestablished"),
        (
            {"source": ".claude-plugin/marketplace.json#plugins.demo", "access": "execute", "risk": "medium"},
            "plugin_selected",
        ),
        (
            {"source": ".claude-plugin/marketplace.json#plugins.demo", "access": "execute", "risk": "high"},
            "project_enabled_plugin",
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


def test_diff_does_not_compare_a_newly_malformed_manifest(tmp_path: Path) -> None:
    """Not hidden where a limit can be carried: `diff` never compares past a
    manifest the change itself broke. Since #808 it leaves that plugin's
    directory uncompared and names it, instead of refusing the settings file
    beside it too, so the result is `partial`, never `comparable`."""

    root = _repository(tmp_path, {"README.md": "# base\n"}, {MALFORMED_NESTED_MANIFEST: "{not json"})

    result = runner.invoke(app, ["diff", "--workspace", str(root), "--base", "main", "--json"])
    payload = json.loads(result.output)

    assert payload["comparison_status"] == "partial"
    assert payload["incomparable_reasons"] == ["head_inventory_incomplete"]
    assert payload["rows"] == []
    assert [
        (item["source"], item["status"], item["side"], item["scope"])
        for item in payload["coverage"]["items"]
        if item["status"] == "blocking_limit"
    ] == [(MALFORMED_NESTED_MANIFEST, "blocking_limit", "head", "examples/bad")]


# --- review cycle 2, P1: a plugin the repository itself enables is loaded -------

SELF_MARKETPLACE = {**MARKETPLACE, "plugins": [{"name": "demo", "source": "./"}]}
SELF_PLUGIN = {**PLUGIN, "hooks": "./.claude/hooks/hooks.json"}
IN_REPOSITORY_MARKETPLACE = {"market": {"source": {"source": "directory", "path": "."}}}
LOADED_WHY = "changes what runs around the agent's actions"
ENABLED_WHY = "project settings enable the plugin that selects this hook"


def _self_enabled(
    *,
    enabled: object = True,
    marketplaces: object = None,
    marketplace_name: object = "market",
    plugin_id: str = "demo@market",
    settings_file: str = ".claude/settings.json",
) -> dict[str, object]:
    """The repository is its own marketplace and its settings enable the plugin."""

    layer = {
        "extraKnownMarketplaces": IN_REPOSITORY_MARKETPLACE if marketplaces is None else marketplaces,
        "enabledPlugins": {plugin_id: enabled},
    }
    return {
        ".claude-plugin/marketplace.json": {**SELF_MARKETPLACE, "name": marketplace_name},
        ".claude-plugin/plugin.json": SELF_PLUGIN,
        ".claude/hooks/hooks.json": HOOK,
        settings_file: {**SETTINGS, **layer} if settings_file == ".claude/settings.json" else layer,
    }


@pytest.mark.parametrize("settings_file", [".claude/settings.json", ".claude/settings.local.json"])
def test_a_plugin_the_project_settings_enable_from_the_repository_is_loaded(
    tmp_path: Path, settings_file: str
) -> None:
    root = _workspace(tmp_path, _self_enabled(settings_file=settings_file))
    before = _inventory(root)
    _write(root, ".claude/hooks/hooks.json", CHANGED_HOOK)

    after = _inventory(root)
    grant = _hooks(after)[".claude/hooks/hooks.json#SessionStart"]
    payload, rows = _compare(before, after)

    assert (grant["access"], grant["risk"]) == ("execute", "high")
    assert hook_loading_basis(grant) == "project_enabled_plugin"
    assert payload["expansion_signals"] == ["hook_changed: claude-code:.claude/hooks/hooks.json"]
    assert [(row.subject, row.direction, row.expands, row.severity) for row in rows] == [
        ("claude-code .claude/hooks/hooks.json", "widened", True, "high")
    ]
    assert rows[0].why.startswith(LOADED_WHY)
    assert ENABLED_WHY in rows[0].why
    assert inventory_is_complete(after)
    _never_executed(root)


@pytest.mark.parametrize(
    "source",
    [
        {"source": "directory", "path": "./"},
        {"source": "file", "path": ".claude-plugin/marketplace.json"},
        {"source": "file", "path": "./.claude-plugin/marketplace.json"},
    ],
    ids=["directory-dot-slash", "file", "file-dot-slash"],
)
def test_a_directory_or_file_marketplace_in_the_repository_establishes_enablement(
    tmp_path: Path, source: dict
) -> None:
    root = _workspace(tmp_path, _self_enabled(marketplaces={"market": {"source": source}}))

    grant = _hooks(_inventory(root))[".claude/hooks/hooks.json#SessionStart"]

    assert hook_loading_basis(grant) == "project_enabled_plugin"


@pytest.mark.parametrize(
    "plugin_id",
    ["demo@market", "demo@real-market"],
    ids=["settings-key", "marketplace-name"],
)
def test_either_name_of_a_registered_marketplace_identifies_it(
    tmp_path: Path, plugin_id: str
) -> None:
    """Claude Code's marketplace schema calls the `name` in `marketplace.json`
    the identifier users see after the `@`, and the settings documentation
    only ever shows an `extraKnownMarketplaces` key equal to it. Neither name
    is documented as the one `enabledPlugins` matches, so both are read: as
    with case-insensitive reference matching, that errs toward showing a hook
    the host would load. Published 1.0.0 showed this hook as `execute`/`high`.
    """

    root = _workspace(
        tmp_path, _self_enabled(marketplace_name="real-market", plugin_id=plugin_id)
    )

    grant = _hooks(_inventory(root))[".claude/hooks/hooks.json#SessionStart"]

    assert (grant["access"], grant["risk"]) == ("execute", "high")
    assert hook_loading_basis(grant) == "project_enabled_plugin"
    _never_executed(root)


@pytest.mark.parametrize(
    "variant",
    [
        {"marketplace_name": "real-market", "marketplaces": {}, "plugin_id": "demo@real-market"},
        {
            "marketplace_name": "real-market",
            "marketplaces": {"market": {"source": {"source": "github", "repo": "o/r"}}},
            "plugin_id": "demo@real-market",
        },
        {"marketplace_name": 7, "plugin_id": "demo@7"},
    ],
    ids=["not-registered", "registered-remotely", "non-string-name"],
)
def test_a_marketplace_name_alone_does_not_register_a_marketplace(
    tmp_path: Path, variant: dict
) -> None:
    """The `name` identifies a marketplace the project settings register in
    the repository. It never registers one on its own: otherwise any
    `marketplace.json` in the tree would enable plugins nothing asked for."""

    root = _workspace(tmp_path, _self_enabled(**variant))

    grant = _hooks(_inventory(root))[".claude/hooks/hooks.json#SessionStart"]

    assert (grant["access"], grant["risk"]) == ("execute", "medium")
    assert hook_loading_basis(grant) == "plugin_selected"


@pytest.mark.parametrize(
    "variant",
    [
        {"enabled": False},
        {"enabled": "true"},
        {"plugin_id": "demo@elsewhere"},
        {"marketplaces": {"market": {"source": {"source": "github", "repo": "o/r"}}}},
        {"marketplaces": {"market": {"source": {"source": "git", "url": "https://example.invalid/r.git"}}}},
        {"marketplaces": {"market": {"source": {"source": "url", "url": "https://example.invalid/m.json"}}}},
        {"marketplaces": {"market": {"source": {"source": "directory", "path": "/abs/repo"}}}},
        {"marketplaces": {"market": {"source": {"source": "directory", "path": "../repo"}}}},
        {"marketplaces": {"market": {"source": {"source": "directory", "path": "~/repo"}}}},
        {"marketplaces": {"market": {"source": {"source": "file", "path": "marketplace.json"}}}},
        {
            "marketplaces": {
                "market": {
                    "source": {
                        "source": "settings", "name": "market",
                        "plugins": [{"name": "demo", "source": {"source": "github", "repo": "o/r"}}],
                    }
                }
            }
        },
    ],
    ids=[
        "disabled", "non-boolean", "unknown-marketplace", "github", "git", "url",
        "absolute-directory", "escaping-directory", "home-directory", "file-not-a-marketplace",
        "settings-source",
    ],
)
def test_without_in_repository_enablement_a_plugin_hook_stays_selected_only(
    tmp_path: Path, variant: dict
) -> None:
    root = _workspace(tmp_path, _self_enabled(**variant))
    before = _inventory(root)
    _write(root, ".claude/hooks/hooks.json", CHANGED_HOOK)

    after = _inventory(root)
    grant = _hooks(after)[".claude/hooks/hooks.json#SessionStart"]
    payload, rows = _compare(before, after)

    assert (grant["access"], grant["risk"]) == ("execute", "medium")
    assert hook_loading_basis(grant) == "plugin_selected"
    assert payload["expansion_signals"] == []
    assert [(row.direction, row.expands, row.severity) for row in rows] == [("changed", False, "medium")]


def test_enabling_a_plugin_the_marketplace_does_not_list_selects_nothing(tmp_path: Path) -> None:
    root = _workspace(
        tmp_path,
        {
            ".claude/settings.json": {
                **SETTINGS,
                "extraKnownMarketplaces": IN_REPOSITORY_MARKETPLACE,
                "enabledPlugins": {"ghost@market": True},
            },
            ".claude-plugin/marketplace.json": {
                **MARKETPLACE, "plugins": [{"name": "demo", "source": "./plugins/demo"}],
            },
            ".claude/hooks/hooks.json": HOOK,
            "plugins/demo/hooks/hooks.json": HOOK,
        },
    )

    grants = _hooks(_inventory(root))

    assert {key: hook_loading_basis(grant) for key, grant in grants.items()} == {
        ".claude/hooks/hooks.json#SessionStart": "declared_only",
        "plugins/demo/hooks/hooks.json#SessionStart": "plugin_selected",
    }


def test_only_the_enabled_plugins_hooks_are_loaded(tmp_path: Path) -> None:
    root = _workspace(
        tmp_path,
        {
            ".claude/settings.json": {
                **SETTINGS,
                "extraKnownMarketplaces": IN_REPOSITORY_MARKETPLACE,
                "enabledPlugins": {"demo@market": True, "other@market": False},
            },
            ".claude-plugin/marketplace.json": {
                **MARKETPLACE,
                "plugins": [
                    {"name": "demo", "source": "./plugins/demo", "strict": False, "hooks": CHANGED_HOOK["hooks"]},
                    {"name": "other", "source": "./plugins/other"},
                ],
            },
            "plugins/demo/hooks/hooks.json": HOOK,
            "plugins/other/hooks/hooks.json": HOOK,
        },
    )

    grants = _hooks(_inventory(root))

    assert {key: hook_loading_basis(grant) for key, grant in grants.items()} == {
        ".claude-plugin/marketplace.json#plugins.demo#SessionStart": "project_enabled_plugin",
        "plugins/demo/hooks/hooks.json#SessionStart": "project_enabled_plugin",
        "plugins/other/hooks/hooks.json#SessionStart": "plugin_selected",
    }


def test_the_published_1_0_0_baseline_is_what_1_0_0_read(tmp_path: Path) -> None:
    """What makes the checked-in fixture a 1.0.0 artifact: the schema 1.0.0
    wrote, the hook recorded as `execute`/`high`, and no plugin manifest,
    because 1.0.0 read none. A regenerated copy that lost any of these would
    not be evidence of anything."""

    baseline = _published_1_0_0_baseline()
    inventory = baseline["inventory"]
    hooks = [grant for grant in inventory["grants"] if grant["kind"] == "hook"]

    assert baseline["host_grants_schema_version"] == "0.5"
    assert [(grant["source"], grant["access"], grant["risk"]) for grant in hooks] == [
        (".claude/hooks/hooks.json", "execute", "high")
    ]
    assert [artifact["path"] for artifact in inventory["artifacts"]] == [
        ".claude/hooks/hooks.json",
        ".claude/settings.json",
    ]
    [coverage] = [
        entry for entry in inventory["host_coverage"] if entry["host"] == "claude-code"
    ]
    assert coverage["sources_observed"] == [".claude/hooks/hooks.json", ".claude/settings.json"]
    # The fixture describes the workspace these tests build.
    assert _workspace(tmp_path, _self_enabled()).joinpath(".claude/hooks/hooks.json").exists()


def test_a_1_0_0_baseline_of_an_enabled_plugin_hook_drifts_once_with_no_grant_change(
    tmp_path: Path,
) -> None:
    """Against a baseline published 1.0.0 actually saved, this reader drifts
    once. The hook grant is untouched — 1.0.0's `execute`/`high`, same
    identity, no typed change and no expansion signal — but the plugin
    manifest that selects it is a newly read artifact and a newly observed
    source, so `--fail-on-drift` exits 20 until the baseline is re-saved."""

    root = _workspace(tmp_path, _self_enabled())
    baseline = _published_1_0_0_baseline()
    current = _inventory(root)

    payload = build_host_drift_payload(
        baseline=baseline, inventory=current, baseline_file="b"
    )

    assert payload["comparison_status"] == "comparable"
    assert payload["has_drift"] is True
    assert payload["changes"] == []
    assert payload["expansion_signals"] == []
    assert capability_diff_rows(payload) == []
    assert [
        (change["baseline"], change["current"]["path"]) for change in payload["artifact_changes"]
    ] == [(None, ".claude-plugin/plugin.json")]
    [coverage] = payload["coverage_changes"]
    assert coverage["current"]["host"] == "claude-code"
    assert set(coverage["current"]["sources_observed"]) - set(
        coverage["baseline"]["sources_observed"]
    ) == {".claude-plugin/plugin.json"}
    # The grant the manifest selects is byte-identical on both sides.
    recorded = [grant for grant in baseline["inventory"]["grants"] if grant["kind"] == "hook"]
    read_now = [grant for grant in current["grants"] if grant["kind"] == "hook"]
    assert [(g["grant_id"], g["access"], g["risk"]) for g in read_now] == [
        (g["grant_id"], g["access"], g["risk"]) for g in recorded
    ]
    _never_executed(root)


def test_the_drift_gate_exits_20_once_on_a_published_1_0_0_baseline(tmp_path: Path) -> None:
    """The documented upgrade effect, through the gate a scheduled workflow
    runs: exit 20, zero typed grant changes, and the added artifact and
    observed source to read in `--json`."""

    root = _workspace(tmp_path, _self_enabled())
    _write(root, ".agents-shipgate/host-grants.json", PUBLISHED_1_0_0_BASELINE.read_text("utf-8"))

    gate = runner.invoke(
        app, ["audit", "--host", "--workspace", str(root), "--drift", "--fail-on-drift"]
    )
    data = runner.invoke(app, ["audit", "--host", "--workspace", str(root), "--drift", "--json"])

    assert gate.exit_code == 20, gate.output
    assert "**Drift detected** — 0 typed grant change(s)." in gate.output
    payload = json.loads(data.output)
    assert payload["changes"] == [] and payload["expansion_signals"] == []
    assert [change["current"]["path"] for change in payload["artifact_changes"]] == [
        ".claude-plugin/plugin.json"
    ]
    _never_executed(root)


def test_diff_marks_an_enabled_plugin_hook_change_as_widening_as_1_0_0_did(tmp_path: Path) -> None:
    root = _repository(tmp_path, _self_enabled(), {".claude/hooks/hooks.json": CHANGED_HOOK})

    text = runner.invoke(app, ["diff", "--workspace", str(root), "--base", "main"])
    data = runner.invoke(app, ["diff", "--workspace", str(root), "--base", "main", "--json"])

    assert text.exit_code == 0, text.output
    # Published 1.0.0 printed exactly this line for the same change.
    assert "⚠ high  widened  claude-code .claude/hooks/hooks.json" in text.output
    assert "1 widening what the agent may do" in text.output
    payload = json.loads(data.output)
    assert [
        (row["subject"], row["direction"], row["expands"], row["severity"]) for row in payload["rows"]
    ] == [("claude-code .claude/hooks/hooks.json", "widened", True, "high")]
    _never_executed(root)


def test_verify_publishes_the_enabled_plugin_hook_as_widening(tmp_path: Path) -> None:
    root = _repository(
        tmp_path,
        {**_self_enabled(), ".gitignore": "agents-shipgate-reports/\n"},
        {".claude/hooks/hooks.json": CHANGED_HOOK},
    )

    result = runner.invoke(
        app,
        ["verify", "--workspace", str(root), "--base", "main", "--head", "HEAD", "--format", "text"],
    )

    assert result.exit_code == 0, result.output
    verifier = json.loads((root / "agents-shipgate-reports/verifier.json").read_text())
    assert [
        (row["subject"], row["direction"], row["expands"], row["severity"])
        for row in verifier["host_comparison"]["rows"]
    ] == [("claude-code .claude/hooks/hooks.json", "widened", True, "high")]
    comment = (root / "agents-shipgate-reports/pr-comment.md").read_text()
    assert "claude-code .claude/hooks/hooks.json" in comment
    assert "widened" in comment


@pytest.mark.parametrize("fmt", ["text", "agent-boundary-json", "agent-control-json"])
def test_check_orders_the_enabled_plugin_hook_as_an_expansion(tmp_path: Path, fmt: str) -> None:
    root = _repository(tmp_path, _self_enabled(), {".claude/hooks/hooks.json": CHANGED_HOOK})

    result = _check(root, fmt)

    assert result.exit_code == 0, result.output
    if fmt == "text":
        assert "high / widened — claude-code .claude/hooks/hooks.json" in result.output
        return
    payload = json.loads(result.output)
    assert payload["decision"] == "require_review"
    rows = payload["rows"] if fmt == "agent-boundary-json" else payload["capability_rows"]["rows"]
    assert [(row["direction"], row["expands"], row["severity"]) for row in rows] == [
        ("widened", True, "high")
    ]


# --- review cycle 2, P2: a plugin limit on one side only refuses check's rows ---

PLUGIN_HOOK_FILE_BASE = {
    "plugins/demo/.claude-plugin/plugin.json": {**PLUGIN, "hooks": "./cfg/hooks.json"},
    "plugins/demo/cfg/hooks.json": HOOK,
}


@pytest.mark.parametrize("edit_hook", [False, True], ids=["manifest-only", "manifest-and-hook"])
@pytest.mark.parametrize("fmt", ["text", "agent-boundary-json", "agent-control-json"])
def test_check_refuses_rows_when_the_head_breaks_a_plugin_manifest(
    tmp_path: Path, fmt: str, edit_hook: bool
) -> None:
    """No row is built from a manifest the head cannot be read from. The
    decision stays what 1.0.0 gave, `allow`: `check` routes no plugin file."""

    head: dict[str, object] = {"plugins/demo/.claude-plugin/plugin.json": "{not json"}
    if edit_hook:
        head["plugins/demo/cfg/hooks.json"] = CHANGED_HOOK
    root = _repository(tmp_path, PLUGIN_HOOK_FILE_BASE, head)

    result = _check(root, fmt)

    assert result.exit_code == 0, result.output
    if fmt == "text":
        assert "Host capability comparison unavailable: head_inventory_incomplete" in result.output
        assert "removed" not in result.output
        assert "Control: complete" in result.output
        return
    payload = json.loads(result.output)
    assert payload["decision"] == "allow"
    if fmt == "agent-boundary-json":
        assert payload["violations"] == []
        assert payload["input_coverage"] == "complete"
        comparison = payload
    else:
        assert payload["control_state"] == "complete"
        comparison = payload["capability_rows"]
    assert comparison["comparison_status"] == "incomparable"
    assert comparison["incomparable_reasons"] == ["head_inventory_incomplete"]
    assert comparison["rows"] == []


@pytest.mark.parametrize(
    ("base_manifest", "head_manifest", "reasons"),
    [
        ("{not json", {**PLUGIN, "hooks": "./cfg/hooks.json"}, ["base_inventory_incomplete"]),
        ("{not json", "{still not json", ["base_inventory_incomplete", "head_inventory_incomplete"]),
    ],
    ids=["base-only", "changed-on-both-sides"],
)
def test_check_refuses_rows_unless_both_sides_share_an_untouched_plugin_limit(
    tmp_path: Path, base_manifest: object, head_manifest: object, reasons: list[str]
) -> None:
    root = _repository(
        tmp_path,
        {**PLUGIN_HOOK_FILE_BASE, "plugins/demo/.claude-plugin/plugin.json": base_manifest},
        {"plugins/demo/.claude-plugin/plugin.json": head_manifest},
    )

    payload = json.loads(_check(root, "agent-boundary-json").output)

    assert payload["decision"] == "allow"
    assert payload["comparison_status"] == "incomparable"
    assert payload["incomparable_reasons"] == reasons
    assert payload["rows"] == []


# --- review cycle 2, nits ------------------------------------------------------


def test_a_source_with_a_slash_is_not_a_bare_name_under_plugin_root(tmp_path: Path) -> None:
    root = _workspace(
        tmp_path,
        {
            ".claude-plugin/marketplace.json": {
                **MARKETPLACE,
                "metadata": {"pluginRoot": "./plugins"},
                "plugins": [{"name": "demo", "source": "team/demo"}],
            },
            "plugins/team/demo/hooks/hooks.json": HOOK,
        },
    )

    inventory = _inventory(root)

    assert _hooks(inventory) == {}
    assert inventory["issues"] == []


@pytest.mark.parametrize("plugin_root", ["./..", "../plugins", "/plugins"])
def test_a_plugin_root_outside_the_marketplace_is_named_without_blocking(
    tmp_path: Path, plugin_root: str
) -> None:
    root = _workspace(
        tmp_path,
        {
            ".claude-plugin/marketplace.json": {
                **MARKETPLACE,
                "metadata": {"pluginRoot": plugin_root},
                "plugins": [{"name": "demo", "source": "demo"}],
            },
            "plugins/demo/hooks/hooks.json": HOOK,
        },
    )

    inventory = _inventory(root)

    assert [(issue["source"], issue["blocking"]) for issue in inventory["issues"]] == [
        (".claude-plugin/marketplace.json", False)
    ]
    assert "`metadata.pluginRoot`" in inventory["issues"][0]["message"]
    assert inventory_is_complete(inventory)
    assert _hooks(inventory) == {}


def test_a_reference_through_a_link_leaving_the_workspace_keeps_only_the_link_limit(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "hooks.json").write_text(json.dumps(HOOK), encoding="utf-8")
    root = _workspace(
        tmp_path, {"plugins/demo/.claude-plugin/plugin.json": {**PLUGIN, "hooks": "./cfg/hooks.json"}}
    )
    try:
        (root / "plugins" / "demo" / "cfg").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symbolic links are unavailable")

    inventory = _inventory(root)

    assert {(issue["source"], issue["blocking"]) for issue in inventory["issues"]} == {
        ("plugins/demo/cfg", True)
    }
    assert not any("not in the repository" in issue["message"] for issue in inventory["issues"])
    assert _hooks(inventory) == {}
    _never_executed(root)
