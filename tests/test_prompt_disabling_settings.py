"""#827: one rating per Claude Code setting, on every surface that names it.

For settings that disable prompts or approve project MCP servers wholesale,
`audit --host`, `diff` and `check` used to answer differently on the same
input. `enableAllProjectMcpServers: true` was a `critical` grant and row, but
`check` recorded it as `SHIP-HOST-BOUNDARY-CONFIG-PARSE-FAILED`, a key that
"could not be parsed", at `medium`. `defaultMode: dontAsk` and
`bypassPermissions` were `medium` grants and rows, but `check` blocked both at
`critical`. Rows printed `True` or `dontAsk` with no setting name, and
`enabledMcpjsonServers` produced no row at all.

Every surface now reads one table, `core.host_settings`. These tests build the
issue's reproduction as real two-commit repositories and pin, for each setting
alone and for the settings combined:

* the `audit --host` grant's `risk`;
* the `diff` rows, `--json` and text, which name the setting and its value;
* `check` in every maintained format: the violation's check id, action, risk
  and evidence, the rows, the control envelope's rows and the text;
* `verify`: the `host_comparison` rows of the manifest-free route and the
  finding a manifest's verification reports;

and that one rating joins them: a violation's risk is its row's severity is its
grant's risk.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.host_boundary import (
    DEFAULT_RULES,
    HostBoundaryPolicy,
    HostBoundaryRule,
    evaluate_host_boundary,
)
from agents_shipgate.core.host_grants import (
    _claude_grants,
    _claude_precedence_key,
    diff_host_grants,
    host_grant_expansion_signals,
    published_setting_value,
)
from agents_shipgate.core.host_settings import (
    CLAUDE_LIST_SETTINGS,
    CLAUDE_SCALAR_SETTINGS,
    claude_setting_values,
    rate_claude_setting,
    setting_value_text,
)

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}
SETTINGS = ".claude/settings.json"
BASE = {"permissions": {"allow": ["Read"]}}
WILDCARD = "SHIP-HOST-BOUNDARY-PERMISSION-WILDCARD-ALLOW"
EXPANDED = "SHIP-HOST-BOUNDARY-PERMISSION-ALLOW-EXPANDED"
PARSE_FAILED = "SHIP-HOST-BOUNDARY-CONFIG-PARSE-FAILED"
_MANIFEST = (
    'version: "0.1"\n'
    "project:\n  name: demo\n"
    "agent:\n  name: support\n  declared_purpose:\n    - Help customers.\n"
    "environment:\n  target: production_like\n"
    "tool_sources:\n"
    "  - id: mcp\n"
    "    type: mcp\n"
    "    path: mcp-tools.json\n"
)


def _settings(*, mode: str | None = None, **top: object) -> dict:
    permissions: dict = {"allow": ["Read"]}
    if mode is not None:
        permissions["defaultMode"] = mode
    return {"permissions": permissions, **top}


def _violation(check_id: str, risk: str, evidence: dict) -> tuple[str, str, str, str]:
    action = "block" if check_id == WILDCARD else "require_review"
    return (check_id, action, risk, json.dumps(evidence, sort_keys=True))


#: name -> (head settings, rows as (cell, severity), check decision, check
#: control state, violations as (check id, action, risk, evidence)).
CASES = {
    "bypass_permissions": (
        _settings(mode="bypassPermissions"),
        [("defaultMode: bypassPermissions", "critical")],
        "block",
        "human_review_required",
        [_violation(WILDCARD, "critical", {"kind": "permission_mode_expanded", "mode": "bypassPermissions"})],
    ),
    "dont_ask": (
        _settings(mode="dontAsk"),
        [("defaultMode: dontAsk", "medium")],
        "require_review",
        "agent_action_required",
        [_violation(EXPANDED, "medium", {"kind": "permission_mode_changed", "mode": "dontAsk"})],
    ),
    "enable_all_project_mcp_servers": (
        _settings(enableAllProjectMcpServers=True),
        [("enableAllProjectMcpServers: true", "critical")],
        "block",
        "human_review_required",
        [
            _violation(
                WILDCARD, "critical",
                {
                    "kind": "permission_mode_expanded",
                    "setting": "enableAllProjectMcpServers",
                    "value": "true",
                },
            )
        ],
    ),
    "skip_dangerous_mode_permission_prompt": (
        _settings(skipDangerousModePermissionPrompt=True),
        [("skipDangerousModePermissionPrompt: true", "critical")],
        "block",
        "human_review_required",
        [
            _violation(
                WILDCARD, "critical",
                {
                    "kind": "permission_mode_expanded",
                    "setting": "skipDangerousModePermissionPrompt",
                    "value": "true",
                },
            )
        ],
    ),
    "enabled_mcpjson_servers": (
        _settings(enabledMcpjsonServers=["docs"]),
        [("enabledMcpjsonServers: docs", "high")],
        "require_review",
        "review_publishable",
        [
            _violation(
                EXPANDED, "high",
                {"kind": "permission_mode_changed", "setting": "enabledMcpjsonServers", "value": "docs"},
            )
        ],
    ),
    # The issue's reproduction, `allmcp`.
    "dont_ask_and_enable_all": (
        _settings(mode="dontAsk", enableAllProjectMcpServers=True),
        [("enableAllProjectMcpServers: true", "critical"), ("defaultMode: dontAsk", "medium")],
        "block",
        "human_review_required",
        [
            _violation(
                WILDCARD, "critical",
                {
                    "kind": "permission_mode_expanded",
                    "setting": "enableAllProjectMcpServers",
                    "value": "true",
                },
            ),
            _violation(EXPANDED, "medium", {"kind": "permission_mode_changed", "mode": "dontAsk"}),
        ],
    ),
    "all_combined": (
        _settings(
            mode="bypassPermissions",
            enableAllProjectMcpServers=True,
            skipDangerousModePermissionPrompt=True,
            enabledMcpjsonServers=["docs", "db"],
        ),
        [
            ("defaultMode: bypassPermissions", "critical"),
            ("enableAllProjectMcpServers: true", "critical"),
            ("skipDangerousModePermissionPrompt: true", "critical"),
            ("enabledMcpjsonServers: db", "high"),
            ("enabledMcpjsonServers: docs", "high"),
        ],
        "block",
        "human_review_required",
        [
            _violation(WILDCARD, "critical", {"kind": "permission_mode_expanded", "mode": "bypassPermissions"}),
            _violation(
                WILDCARD, "critical",
                {
                    "kind": "permission_mode_expanded",
                    "setting": "enableAllProjectMcpServers",
                    "value": "true",
                },
            ),
            _violation(
                WILDCARD, "critical",
                {
                    "kind": "permission_mode_expanded",
                    "setting": "skipDangerousModePermissionPrompt",
                    "value": "true",
                },
            ),
            _violation(
                EXPANDED, "high",
                {"kind": "permission_mode_changed", "setting": "enabledMcpjsonServers", "value": "db"},
            ),
            _violation(
                EXPANDED, "high",
                {"kind": "permission_mode_changed", "setting": "enabledMcpjsonServers", "value": "docs"},
            ),
        ],
    ),
}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=_GIT_ENV
    ).stdout.strip()


def _run(args: list[str]) -> str:
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    return result.output


def _repository(
    tmp_path: Path, head: dict, *, base: dict = BASE, manifest: bool = False
) -> Path:
    """One base commit on `main` and the settings edit on `change`."""

    repo = tmp_path / "repo"
    (repo / ".claude").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    (repo / ".gitignore").write_text("agents-shipgate-reports/\n.agents-shipgate/\n", encoding="utf-8")
    (repo / SETTINGS).write_text(json.dumps(base), encoding="utf-8")
    if manifest:
        (repo / "shipgate.yaml").write_text(_MANIFEST, encoding="utf-8")
        (repo / "mcp-tools.json").write_text('{"tools":[]}\n', encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "change")
    (repo / SETTINGS).write_text(json.dumps(head), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "head")
    return repo


def _rows(rows: list[dict]) -> list[tuple[str, str]]:
    return sorted((row["after"], row["severity"]) for row in rows)


def _violations(payload: dict) -> list[tuple[str, str, str, str]]:
    return sorted(
        (item["check_id"], item["action"], item["risk_level"], json.dumps(item["evidence"], sort_keys=True))
        for item in payload["violations"]
    )


def _named(evidence: dict) -> str:
    """The row cell a violation's evidence names."""

    if "mode" in evidence:
        return f"defaultMode: {evidence['mode']}"
    return f"{evidence['setting']}: {evidence['value']}"


@pytest.mark.parametrize("case", sorted(CASES))
def test_every_surface_gives_a_setting_one_rating(tmp_path: Path, case: str) -> None:
    head, rows, decision, state, violations = CASES[case]
    repo = _repository(tmp_path, head)
    workspace = ["--workspace", str(repo)]
    check = [
        "check", "--agent", "claude-code", *workspace, "--base", "main", "--head", "HEAD", "--format",
    ]

    # audit --host: the grant carries the rating.
    inventory = json.loads(_run(["audit", "--host", *workspace, "--json"]))
    grants = {
        f"{grant['setting']}: {setting_value_text(grant['setting'], published_setting_value(grant))}": grant
        for grant in inventory["grants"]
        if grant["kind"] == "permission_mode"
    }
    assert sorted((cell, grants[cell]["risk"]) for cell in grants) == sorted(rows)

    # diff: one row per value, naming the setting, at the grant's rating, with
    # the table's basis as its reason.
    diff = json.loads(_run(["diff", *workspace, "--base", "main", "--json"]))
    assert diff["comparison_status"] == "comparable"
    assert _rows(diff["rows"]) == sorted(rows)
    for row in diff["rows"]:
        assert row["direction"] == "added"
        assert row["why"] != "changes a permission_mode grant", row
    text = _run(["diff", *workspace, "--base", "main"])
    for cell, severity in rows:
        assert cell in text
        assert f"⚠ {severity}" in text

    # check: the violations, at the same rating, in every maintained format.
    boundary = json.loads(_run([*check, "agent-boundary-json"]))
    assert (boundary["decision"], boundary["control"]["state"]) == (decision, state)
    assert boundary["input_coverage"] == "complete"
    assert _violations(boundary) == sorted(violations)
    assert PARSE_FAILED not in json.dumps(boundary)
    assert _rows(boundary["rows"]) == sorted(rows)
    severity = dict(rows)
    for item in boundary["violations"]:
        assert item["risk_level"] == severity[_named(item["evidence"])], item
    assert boundary["control"]["permissions"]["merge"] is False

    control = json.loads(_run([*check, "agent-control-json"]))
    assert (control["decision"], control["control_state"]) == (decision, state)
    assert _rows(control["capability_rows"]["rows"]) == sorted(rows)
    assert control["input_id"] == boundary["audit_id"]

    assert f"Control: {state}" in _run([*check, "text"])

    # verify, manifest-free: the same rows.
    verify = json.loads(
        _run(["verify", "--preview", *workspace, "--base", "main", "--head", "HEAD", "--json"])
    )
    assert _rows(verify["host_comparison"]["rows"]) == sorted(rows)


@pytest.mark.parametrize("case", sorted(CASES))
def test_verify_reports_the_violation_as_a_finding_of_the_same_severity(
    tmp_path: Path, case: str
) -> None:
    head, _rows_, _decision, _state, violations = CASES[case]
    repo = _repository(tmp_path, head, manifest=True)

    _run(
        [
            "verify", "--workspace", str(repo), "--config", "shipgate.yaml", "--base", "main",
            "--head", "HEAD", "--ci-mode", "advisory", "--format", "json",
        ]
    )
    report = json.loads((repo / "agents-shipgate-reports" / "report.json").read_text())
    findings = sorted(
        (
            item["check_id"],
            "block" if item["blocks_release"] else "require_review",
            item["severity"],
            json.dumps(item["evidence"], sort_keys=True),
        )
        for item in report["findings"]
        if item["check_id"].startswith("SHIP-HOST-BOUNDARY-")
    )
    assert findings == sorted(violations)
    # A critical value blocks the release, as its rating says; any other is a
    # review item at its own severity. (This fixture declares no tools, so a
    # release nothing blocks reads `insufficient_evidence`, never `passed`.)
    decision = report["release_decision"]
    blockers = sorted(
        (item["check_id"], item["severity"])
        for item in decision["blockers"]
        if item["check_id"].startswith("SHIP-HOST-BOUNDARY-")
    )
    reviews = sorted(
        (item["check_id"], item["severity"])
        for item in decision["review_items"]
        if item["check_id"].startswith("SHIP-HOST-BOUNDARY-")
    )
    assert blockers == sorted((check_id, risk) for check_id, _, risk, _ in violations if risk == "critical")
    assert reviews == sorted((check_id, risk) for check_id, _, risk, _ in violations if risk != "critical")
    if blockers:
        assert decision["decision"] == "blocked"
    else:
        assert decision["decision"] in {"review_required", "insufficient_evidence"}


def test_the_issue_reproduction_before_and_after(tmp_path: Path) -> None:
    """What #827 observed on `9df307ab`, and what each surface says now."""

    head, *_ = CASES["dont_ask_and_enable_all"]
    repo = _repository(tmp_path, head)
    text = _run(["diff", "--workspace", str(repo), "--base", "main"])

    # Was: `⚠ critical added True — changes a permission_mode grant`.
    assert "enableAllProjectMcpServers: true" in text
    assert "approves every MCP server the project's .mcp.json declares, without a prompt" in text
    # Was: `⚠ medium added dontAsk`, beside a blocking `critical` in `check`.
    assert "defaultMode: dontAsk" in text
    assert "denies every tool call no allow rule already permits, instead of prompting" in text
    assert "changes a permission_mode grant" not in text


def test_enabled_mcpjson_servers_is_one_grant_per_server(tmp_path: Path) -> None:
    """Adding one server is one row and one violation; removing one raises nothing."""

    repo = _repository(
        tmp_path,
        _settings(enabledMcpjsonServers=["docs", "db"]),
        base=_settings(enabledMcpjsonServers=["docs", "logs"]),
    )
    diff = json.loads(_run(["diff", "--workspace", str(repo), "--base", "main", "--json"]))
    assert sorted((row["direction"], row["before"], row["after"], row["expands"]) for row in diff["rows"]) == [
        ("added", "—", "enabledMcpjsonServers: db", True),
        ("removed", "enabledMcpjsonServers: logs", "—", False),
    ]
    boundary = json.loads(
        _run(
            [
                "check", "--agent", "claude-code", "--workspace", str(repo), "--base", "main",
                "--head", "HEAD", "--format", "agent-boundary-json",
            ]
        )
    )
    assert _violations(boundary) == [
        _violation(
            EXPANDED, "high",
            {"kind": "permission_mode_changed", "setting": "enabledMcpjsonServers", "value": "db"},
        )
    ]


def test_removing_a_setting_raises_nothing_of_its_own(tmp_path: Path) -> None:
    """As removing `defaultMode` never did; the settings file is still reviewed."""

    repo = _repository(
        tmp_path, BASE, base=_settings(mode="bypassPermissions", enableAllProjectMcpServers=True)
    )
    diff = json.loads(_run(["diff", "--workspace", str(repo), "--base", "main", "--json"]))
    assert sorted((row["direction"], row["before"], row["severity"], row["expands"]) for row in diff["rows"]) == [
        ("removed", "defaultMode: bypassPermissions", "critical", False),
        ("removed", "enableAllProjectMcpServers: true", "critical", False),
    ]
    assert all(row["why"].startswith("removes a setting that ") for row in diff["rows"])
    boundary = json.loads(
        _run(
            [
                "check", "--agent", "claude-code", "--workspace", str(repo), "--base", "main",
                "--head", "HEAD", "--format", "agent-boundary-json",
            ]
        )
    )
    assert [item["id"] for item in boundary["violations"]] == ["BOUNDARY-PROTECTED-SURFACE-UNCLASSIFIED"]
    assert boundary["decision"] == "require_review"


# --- the table and the readers that share it ----------------------------------


@pytest.mark.parametrize(
    ("setting", "value", "access", "risk"),
    [
        ("defaultMode", "bypassPermissions", "admin", "critical"),
        ("defaultMode", "auto", "admin", "high"),
        ("defaultMode", "acceptEdits", "write", "high"),
        ("defaultMode", "dontAsk", "unknown", "medium"),
        ("defaultMode", "plan", "unknown", "medium"),
        ("defaultMode", "default", "unknown", "medium"),
        ("defaultMode", "askAlways", "unknown", "high"),
        ("defaultMode", True, "unknown", "high"),
        ("disableBypassPermissionsMode", "disable", "unknown", "medium"),
        ("allowManagedPermissionRulesOnly", True, "unknown", "medium"),
        ("allowManagedHooksOnly", True, "unknown", "medium"),
        ("skipDangerousModePermissionPrompt", True, "admin", "critical"),
        ("skipDangerousModePermissionPrompt", False, "unknown", "medium"),
        ("enableAllProjectMcpServers", True, "admin", "critical"),
        ("enableAllProjectMcpServers", False, "unknown", "medium"),
        # The reader has always read a switch with `bool`, so an undocumented
        # value is rated as the value it resembles, never below it.
        ("enableAllProjectMcpServers", "yes", "admin", "critical"),
        ("disableAllHooks", True, "unknown", "medium"),
        ("enabledMcpjsonServers", "docs", "external", "high"),
    ],
)
def test_the_table_rates_each_value_and_the_grant_carries_it(
    setting: str, value: object, access: str, risk: str
) -> None:
    rating = rate_claude_setting(setting, value)
    assert (rating.access, rating.risk) == (access, risk)

    data = {**BASE, setting: [value] if setting in CLAUDE_LIST_SETTINGS else value}
    [grant] = [
        grant
        for grant in _claude_grants(data, scope="repository", source=SETTINGS)
        if grant["kind"] == "permission_mode"
    ]
    assert (grant["setting"], grant["access"], grant["risk"]) == (setting, access, risk)


def test_every_documented_value_has_a_basis() -> None:
    documented = [
        *(("defaultMode", mode) for mode in ("bypassPermissions", "auto", "acceptEdits", "dontAsk", "plan", "default")),
        ("disableBypassPermissionsMode", "disable"),
        *((setting, flag) for setting in CLAUDE_SCALAR_SETTINGS[2:] for flag in (True, False)),
        ("enabledMcpjsonServers", "docs"),
    ]
    for setting, value in documented:
        assert rate_claude_setting(setting, value).basis, (setting, value)
    # A value Claude Code does not document names no behaviour it may not have.
    assert rate_claude_setting("enableAllProjectMcpServers", "yes").basis is None
    assert rate_claude_setting("disableBypassPermissionsMode", "enable").basis is None


@pytest.mark.parametrize(
    ("setting", "value", "rule"),
    [
        ("defaultMode", "bypassPermissions", "HOST-PERMISSION-WILDCARD-ALLOW"),
        ("enableAllProjectMcpServers", True, "HOST-PERMISSION-WILDCARD-ALLOW"),
        ("skipDangerousModePermissionPrompt", True, "HOST-PERMISSION-WILDCARD-ALLOW"),
        ("defaultMode", "acceptEdits", "HOST-PERMISSION-ALLOW-EXPANDED"),
        ("defaultMode", "dontAsk", "HOST-PERMISSION-ALLOW-EXPANDED"),
        ("disableAllHooks", True, "HOST-PERMISSION-ALLOW-EXPANDED"),
        ("enableAllProjectMcpServers", False, "HOST-PERMISSION-ALLOW-EXPANDED"),
    ],
)
def test_check_raises_each_value_at_the_table_rating(
    tmp_path: Path, setting: str, value: object, rule: str
) -> None:
    new = {"permissions": {"allow": ["Read"], setting: value}} if setting == "defaultMode" else {**BASE, setting: value}
    [violation] = _evaluate(tmp_path, new)
    rating = rate_claude_setting(setting, value)
    assert (violation.id, violation.risk_level) == (rule, rating.risk)
    assert violation.action == DEFAULT_RULES[rule].action


def _settings_diff(old: dict, new: dict) -> str:
    old_lines = json.dumps(old, indent=2).splitlines()
    new_lines = json.dumps(new, indent=2).splitlines()
    body = "\n".join([f"-{line}" for line in old_lines] + [f"+{line}" for line in new_lines])
    return (
        f"diff --git a/{SETTINGS} b/{SETTINGS}\n"
        "index 1111111..2222222 100644\n"
        f"--- a/{SETTINGS}\n+++ b/{SETTINGS}\n"
        f"@@ -1,{len(old_lines)} +1,{len(new_lines)} @@\n{body}\n"
    )


def _evaluate(
    tmp_path: Path, new: dict, policy: HostBoundaryPolicy | None = None, *, old: dict = BASE
) -> list:
    (tmp_path / ".claude").mkdir(exist_ok=True)
    (tmp_path / SETTINGS).write_text(json.dumps(old, indent=2), encoding="utf-8")
    violations, _ = evaluate_host_boundary(
        workspace=tmp_path, diff_text=_settings_diff(old, new), policy_override=policy
    )
    return violations


def test_a_policy_that_raises_the_rule_raises_the_rating_and_nothing_lowers_it(tmp_path: Path) -> None:
    [default] = _evaluate(tmp_path, _settings(mode="dontAsk"))
    assert (default.id, default.risk_level) == ("HOST-PERMISSION-ALLOW-EXPANDED", "medium")

    raised = dict(DEFAULT_RULES)
    base = raised["HOST-PERMISSION-ALLOW-EXPANDED"]
    raised["HOST-PERMISSION-ALLOW-EXPANDED"] = HostBoundaryRule(
        id=base.id, check_id=base.check_id, title=base.title, action=base.action,
        risk_level="critical", recommendation=base.recommendation,
    )
    policy = HostBoundaryPolicy(id="raised", version="1", rules=raised)
    [strict] = _evaluate(tmp_path, _settings(mode="dontAsk"), policy)
    assert strict.risk_level == "critical"
    # A raise is a floor, not a replacement: a `high` value under it is critical too.
    [scoped] = _evaluate(tmp_path, _settings(enabledMcpjsonServers=["docs"]), policy)
    assert scoped.risk_level == "critical"


def test_a_setting_shadowed_by_permissions_stays_an_unknown_key(tmp_path: Path) -> None:
    """The reader reads a setting from `permissions` when it is set there, and so does `check`."""

    new = {"permissions": {"allow": ["Read"], "defaultMode": "plan"}, "defaultMode": "bypassPermissions"}
    assert [(item.setting, item.value, item.container) for item in claude_setting_values(new)] == [
        ("defaultMode", "plan", "permissions")
    ]
    violations = _evaluate(tmp_path, new)
    assert sorted((item.id, item.risk_level, json.dumps(item.evidence, sort_keys=True)) for item in violations) == [
        ("HOST-CONFIG-PARSE-FAILED", "medium", json.dumps({"key": "defaultMode", "kind": "unknown_host_config_key"})),
        ("HOST-PERMISSION-ALLOW-EXPANDED", "medium", json.dumps({"kind": "permission_mode_changed", "mode": "plan"})),
    ]


def test_emptying_the_approved_server_list_is_a_removal_not_an_unknown_key(tmp_path: Path) -> None:
    violations = _evaluate(
        tmp_path, _settings(enabledMcpjsonServers=[]), old=_settings(enabledMcpjsonServers=["docs"])
    )
    assert [item.id for item in violations] == []


def test_disabled_mcpjson_servers_is_still_an_unknown_key(tmp_path: Path) -> None:
    """Recorded choice: only `enabledMcpjsonServers` became a grant."""

    [violation] = _evaluate(tmp_path, _settings(disabledMcpjsonServers=["docs"]))
    assert (violation.id, violation.evidence) == (
        "HOST-CONFIG-PARSE-FAILED",
        {"kind": "unknown_host_config_key", "key": "disabledMcpjsonServers"},
    )


def test_a_setting_under_permissions_is_rated_not_a_boundary_change(tmp_path: Path) -> None:
    [violation] = _evaluate(
        tmp_path, {"permissions": {"allow": ["Read"], "disableBypassPermissionsMode": "disable"}}
    )
    assert (violation.id, violation.risk_level, violation.evidence) == (
        "HOST-PERMISSION-ALLOW-EXPANDED",
        "medium",
        {"kind": "permission_mode_changed", "setting": "disableBypassPermissionsMode", "value": "disable"},
    )


def test_a_rerated_baseline_grant_is_a_change_but_not_an_expansion() -> None:
    """A baseline saved before #827 rated `bypassPermissions` `medium`."""

    [current] = [
        grant
        for grant in _claude_grants(_settings(mode="bypassPermissions"), scope="repository", source=SETTINGS)
        if grant["kind"] == "permission_mode"
    ]
    saved = {**current, "access": "unknown", "risk": "medium"}
    changes = diff_host_grants({"grants": [saved]}, {"grants": [current]})

    assert [(change["baseline"]["risk"], change["current"]["risk"]) for change in changes] == [
        ("medium", "critical")
    ]
    assert host_grant_expansion_signals(changes) == []
    # A value that really changed is still an expansion.
    added = diff_host_grants({"grants": []}, {"grants": [current]})
    assert host_grant_expansion_signals(added) == [f"permission_mode_added: claude-code:{SETTINGS}"]


def test_a_row_spells_the_value_as_the_file_does() -> None:
    def cell(value: object) -> str:
        [grant] = [
            grant
            for grant in _claude_grants(
                {**BASE, "enableAllProjectMcpServers": value}, scope="repository", source=SETTINGS
            )
            if grant["kind"] == "permission_mode"
        ]
        return setting_value_text(grant["setting"], published_setting_value(grant))

    # A JSON `true` and the string "True" both publish `True`; the digest tells them apart.
    assert cell(True) == "true"
    assert cell(False) == "false"
    assert cell("True") == '"True"'
    assert cell(1) == "1"


def test_each_approved_server_is_its_own_precedence_key() -> None:
    """Whether Claude Code merges `enabledMcpjsonServers` across layers is not
    documented, so one layer's list never hides a server another approves."""

    grants = [
        grant
        for grant in _claude_grants(
            {"enabledMcpjsonServers": ["docs", "db"]}, scope="local_static", source=SETTINGS
        )
        if grant["kind"] == "permission_mode"
    ]
    assert sorted(_claude_precedence_key(grant) for grant in grants) == [
        ("permission_mode", "enabledMcpjsonServers:db"),
        ("permission_mode", "enabledMcpjsonServers:docs"),
    ]
