"""SHIP-HOST-BOUNDARY-* — host capability governance (diff-aware).

Mirrors the codex boundary harness: synthetic unified diffs evaluated
against a tmp_path workspace through the ``checks.host_boundary`` wrapper.
Covers the three governed host surfaces (project MCP server declarations,
Claude Code settings, GitHub workflow permissions), the
"fires only with a verification context" contract, suppression immunity,
evidence privacy (no env var values), and determinism.
"""

from __future__ import annotations

import json
from pathlib import Path

from agents_shipgate.checks.host_boundary import run as host_boundary_run
from agents_shipgate.config.loader import load_manifest
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.domain import Agent
from agents_shipgate.core.findings.mutations import apply_suppressions
from agents_shipgate.core.host_boundary import evaluate_host_boundary
from agents_shipgate.schemas.manifest import SuppressionConfig
from agents_shipgate.schemas.verification import VerificationContext


def _context(
    tmp_path: Path,
    diff_text: str | None = None,
    *,
    verification: bool = True,
) -> ScanContext:
    manifest = load_manifest(Path("samples/support_refund_agent/shipgate.yaml"))
    vc = (
        VerificationContext(
            diff_text=diff_text,
            diff_text_available=diff_text is not None,
        )
        if verification
        else None
    )
    return ScanContext(
        manifest=manifest,
        agent=Agent(id="agent:test/test", name="test"),
        tools=[],
        config_path=tmp_path / "shipgate.yaml",
        verification=vc,
    )


def _new_file_diff(path: str, text: str) -> str:
    lines = text.splitlines()
    body = "\n".join(f"+{line}" for line in lines)
    return (
        f"diff --git a/{path} b/{path}\n"
        "new file mode 100644\n"
        "index 0000000..1111111\n"
        "--- /dev/null\n"
        f"+++ b/{path}\n"
        f"@@ -0,0 +1,{len(lines)} @@\n"
        f"{body}\n"
    )


def _change_diff(path: str, old_text: str, new_text: str) -> str:
    """Full-file replacement diff; the OLD text must be on disk so the
    evaluator can forward-apply the hunks against the workspace base."""
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    body = "\n".join(
        [f"-{line}" for line in old_lines] + [f"+{line}" for line in new_lines]
    )
    return (
        f"diff --git a/{path} b/{path}\n"
        "index 1111111..2222222 100644\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        f"@@ -1,{len(old_lines)} +1,{len(new_lines)} @@\n"
        f"{body}\n"
    )


def _write(workspace: Path, relative: str, text: str) -> None:
    target = workspace / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


# --- Verification-context gating ---------------------------------------------


def test_plain_scan_without_verification_emits_nothing(tmp_path: Path) -> None:
    assert host_boundary_run(_context(tmp_path, verification=False)) == []


def test_verification_context_without_host_files_emits_nothing(
    tmp_path: Path,
) -> None:
    diff = _new_file_diff("src/app.py", "print('hello')")
    assert host_boundary_run(_context(tmp_path, diff)) == []


# --- MCP server declarations --------------------------------------------------


def test_mcp_server_added_fires(tmp_path: Path) -> None:
    diff = _new_file_diff(
        ".mcp.json",
        json.dumps(
            {
                "mcpServers": {
                    "search": {"command": "npx some-search-server --stdio"}
                }
            },
            indent=2,
        ),
    )

    findings = host_boundary_run(_context(tmp_path, diff))

    assert [f.check_id for f in findings] == ["SHIP-HOST-BOUNDARY-MCP-SERVER-ADDED"]
    finding = findings[0]
    assert finding.category == "host_boundary"
    assert finding.severity == "high"
    assert finding.blocks_release is False
    assert finding.source.type == "changed_file"
    assert finding.source.path == ".mcp.json"
    assert finding.provenance_kind == "static_declaration"
    assert finding.evidence == {
        "kind": "mcp_server_added",
        "server": "search",
        "transport_hint": "stdio",
        "command_or_url": "npx",
    }


def test_mcp_server_added_url_transport_redacts_to_host(tmp_path: Path) -> None:
    diff = _new_file_diff(
        ".vscode/mcp.json",
        json.dumps(
            {
                "servers": {
                    "remote": {"url": "https://user:tok3n@api.example.com/mcp?key=s3cret"}
                }
            },
            indent=2,
        ),
    )

    findings = host_boundary_run(_context(tmp_path, diff))

    assert [f.check_id for f in findings] == ["SHIP-HOST-BOUNDARY-MCP-SERVER-ADDED"]
    evidence = findings[0].evidence
    assert evidence["transport_hint"] == "url"
    assert evidence["command_or_url"] == "api.example.com"
    dumped = json.dumps([f.evidence for f in findings])
    assert "tok3n" not in dumped
    assert "s3cret" not in dumped


def test_mcp_server_changed_reports_changed_keys_only(tmp_path: Path) -> None:
    old_text = json.dumps(
        {
            "mcpServers": {
                "db": {
                    "command": "uvx db-server",
                    "env": {"DB_TOKEN": "old-secret-value"},
                },
                "untouched": {"command": "uvx other-server"},
            }
        },
        indent=2,
    )
    new_text = json.dumps(
        {
            "mcpServers": {
                "db": {
                    "command": "uvx db-server",
                    "env": {"DB_TOKEN": "abc123xyz", "SECRET_TOKEN": "abc123xyz"},
                },
                "untouched": {"command": "uvx other-server"},
            }
        },
        indent=2,
    )
    _write(tmp_path, ".mcp.json", old_text)
    diff = _change_diff(".mcp.json", old_text, new_text)

    findings = host_boundary_run(_context(tmp_path, diff))

    assert [f.check_id for f in findings] == ["SHIP-HOST-BOUNDARY-MCP-SERVER-CHANGED"]
    assert findings[0].evidence == {
        "kind": "mcp_server_changed",
        "server": "db",
        "changed_keys": ["env"],
    }
    # Env var VALUES must never appear in evidence — keys only.
    dumped = json.dumps([f.evidence for f in findings])
    assert "abc123xyz" not in dumped
    assert "old-secret-value" not in dumped


def test_cursor_mcp_json_is_governed(tmp_path: Path) -> None:
    diff = _new_file_diff(
        ".cursor/mcp.json",
        json.dumps({"mcpServers": {"local": {"command": "node server.js"}}}),
    )

    findings = host_boundary_run(_context(tmp_path, diff))

    assert [f.check_id for f in findings] == ["SHIP-HOST-BOUNDARY-MCP-SERVER-ADDED"]
    assert findings[0].evidence["server"] == "local"


def test_malformed_mcp_json_fails_closed(tmp_path: Path) -> None:
    diff = _new_file_diff(".mcp.json", '{"mcpServers": {not valid json')

    findings = host_boundary_run(_context(tmp_path, diff))

    assert [f.check_id for f in findings] == [
        "SHIP-HOST-BOUNDARY-CONFIG-PARSE-FAILED"
    ]
    assert findings[0].severity == "medium"
    assert findings[0].blocks_release is False
    assert findings[0].evidence["kind"] == "json_parse_failed"


# --- Claude Code settings ------------------------------------------------------


def test_wildcard_allow_blocks_release(tmp_path: Path) -> None:
    old_text = json.dumps({"permissions": {"allow": []}}, indent=2)
    new_text = json.dumps({"permissions": {"allow": ["Bash(*)"]}}, indent=2)
    _write(tmp_path, ".claude/settings.json", old_text)
    diff = _change_diff(".claude/settings.json", old_text, new_text)

    findings = host_boundary_run(_context(tmp_path, diff))

    assert [f.check_id for f in findings] == [
        "SHIP-HOST-BOUNDARY-PERMISSION-WILDCARD-ALLOW"
    ]
    finding = findings[0]
    assert finding.severity == "critical"
    assert finding.blocks_release is True
    assert finding.evidence == {
        "kind": "permission_wildcard_allow",
        "rule": "Bash(*)",
    }


def test_wildcard_shapes_classify_as_wildcard(tmp_path: Path) -> None:
    """'*', bare tool names, '(*)', '(*:*)', and ':*)' shapes are all
    wildcard-shaped; scoped rules are not."""
    settings = json.dumps(
        {
            "permissions": {
                "allow": [
                    "*",
                    "WebFetch",
                    "Bash(*)",
                    "Bash(*:*)",
                    "mcp__db(query:*)",
                    "Bash(npm run build)",
                ]
            }
        }
    )
    diff = _new_file_diff(".claude/settings.local.json", settings)

    violations, _diagnostics = evaluate_host_boundary(
        workspace=tmp_path, diff_text=diff
    )

    by_rule = {item.evidence["rule"]: item.id for item in violations}
    assert by_rule == {
        "*": "HOST-PERMISSION-WILDCARD-ALLOW",
        "WebFetch": "HOST-PERMISSION-WILDCARD-ALLOW",
        "Bash(*)": "HOST-PERMISSION-WILDCARD-ALLOW",
        "Bash(*:*)": "HOST-PERMISSION-WILDCARD-ALLOW",
        # Scoped prefix rules keep an explicit tool + argument prefix; the
        # trailing `*` widens only within that prefix, so they expand the
        # allowlist (review) without being wildcard-shaped (block).
        "mcp__db(query:*)": "HOST-PERMISSION-ALLOW-EXPANDED",
        "Bash(npm run build)": "HOST-PERMISSION-ALLOW-EXPANDED",
    }


def test_non_wildcard_allow_requires_review(tmp_path: Path) -> None:
    old_text = json.dumps({"permissions": {"allow": ["Bash(git status)"]}}, indent=2)
    new_text = json.dumps(
        {"permissions": {"allow": ["Bash(git status)", "Bash(npm run build)"]}},
        indent=2,
    )
    _write(tmp_path, ".claude/settings.json", old_text)
    diff = _change_diff(".claude/settings.json", old_text, new_text)

    findings = host_boundary_run(_context(tmp_path, diff))

    assert [f.check_id for f in findings] == [
        "SHIP-HOST-BOUNDARY-PERMISSION-ALLOW-EXPANDED"
    ]
    finding = findings[0]
    assert finding.severity == "high"
    assert finding.blocks_release is False
    assert finding.evidence == {
        "kind": "permission_allow_expanded",
        "rule": "Bash(npm run build)",
    }


def test_deny_removal_requires_review(tmp_path: Path) -> None:
    old_text = json.dumps(
        {"permissions": {"deny": ["WebFetch", "Bash(curl:*)"]}}, indent=2
    )
    new_text = json.dumps({"permissions": {"deny": ["Bash(curl:*)"]}}, indent=2)
    _write(tmp_path, ".claude/settings.json", old_text)
    diff = _change_diff(".claude/settings.json", old_text, new_text)

    findings = host_boundary_run(_context(tmp_path, diff))

    assert [f.check_id for f in findings] == [
        "SHIP-HOST-BOUNDARY-PERMISSION-DENY-REMOVED"
    ]
    assert findings[0].evidence == {
        "kind": "permission_deny_removed",
        "rule": "WebFetch",
    }


def test_hooks_change_requires_review(tmp_path: Path) -> None:
    old_text = json.dumps({"hooks": {}}, indent=2)
    new_text = json.dumps(
        {
            "hooks": {
                "PostToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [{"type": "command", "command": "./audit.sh"}],
                    }
                ]
            }
        },
        indent=2,
    )
    _write(tmp_path, ".claude/settings.json", old_text)
    diff = _change_diff(".claude/settings.json", old_text, new_text)

    findings = host_boundary_run(_context(tmp_path, diff))

    assert [f.check_id for f in findings] == ["SHIP-HOST-BOUNDARY-HOOK-CHANGED"]
    assert findings[0].evidence == {
        "kind": "hook_changed",
        "events": ["PostToolUse"],
    }


def test_unchanged_settings_keys_emit_nothing(tmp_path: Path) -> None:
    """Delta-scoped: pre-existing allow rules, deny rules, and hooks do not
    fire when an unrelated key changes."""
    old_text = json.dumps(
        {
            "permissions": {"allow": ["Bash(*)"], "deny": ["WebFetch"]},
            "hooks": {"PreToolUse": [{"type": "command", "command": "./pre.sh"}]},
            "model": "old",
        },
        indent=2,
    )
    new_text = json.dumps(
        {
            "permissions": {"allow": ["Bash(*)"], "deny": ["WebFetch"]},
            "hooks": {"PreToolUse": [{"type": "command", "command": "./pre.sh"}]},
            "model": "new",
        },
        indent=2,
    )
    _write(tmp_path, ".claude/settings.json", old_text)
    diff = _change_diff(".claude/settings.json", old_text, new_text)

    assert host_boundary_run(_context(tmp_path, diff)) == []


# --- GitHub workflows -----------------------------------------------------------


def test_workflow_read_to_write_expansion(tmp_path: Path) -> None:
    old_text = (
        "name: CI\n"
        "on: push\n"
        "permissions:\n"
        "  contents: read\n"
        "jobs:\n"
        "  build:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - run: make test\n"
    )
    new_text = old_text.replace("contents: read", "contents: write")
    _write(tmp_path, ".github/workflows/ci.yml", old_text)
    diff = _change_diff(".github/workflows/ci.yml", old_text, new_text)

    findings = host_boundary_run(_context(tmp_path, diff))

    assert [f.check_id for f in findings] == [
        "SHIP-HOST-BOUNDARY-WORKFLOW-PERMISSIONS-EXPANDED"
    ]
    finding = findings[0]
    assert finding.severity == "high"
    assert finding.blocks_release is False
    assert finding.evidence == {
        "kind": "workflow_permissions_expanded",
        "job": "<top-level>",
        "scope": "contents",
        "old": "read",
        "new": "write",
    }


def test_job_level_permission_grant_reports_job_name(tmp_path: Path) -> None:
    old_text = (
        "name: CI\n"
        "on: push\n"
        "jobs:\n"
        "  release:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - run: make release\n"
    )
    new_text = (
        "name: CI\n"
        "on: push\n"
        "jobs:\n"
        "  release:\n"
        "    runs-on: ubuntu-latest\n"
        "    permissions:\n"
        "      id-token: write\n"
        "    steps:\n"
        "      - run: make release\n"
    )
    _write(tmp_path, ".github/workflows/ci.yml", old_text)
    diff = _change_diff(".github/workflows/ci.yml", old_text, new_text)

    findings = host_boundary_run(_context(tmp_path, diff))

    assert [f.check_id for f in findings] == [
        "SHIP-HOST-BOUNDARY-WORKFLOW-PERMISSIONS-EXPANDED"
    ]
    assert findings[0].evidence == {
        "kind": "workflow_permissions_expanded",
        "job": "release",
        "scope": "id-token",
        "old": None,
        "new": "write",
    }


def test_workflow_write_all_blocks(tmp_path: Path) -> None:
    old_text = (
        "name: CI\n"
        "on: push\n"
        "permissions:\n"
        "  contents: read\n"
        "jobs:\n"
        "  build:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - run: make test\n"
    )
    new_text = old_text.replace(
        "permissions:\n  contents: read", "permissions: write-all"
    )
    _write(tmp_path, ".github/workflows/ci.yml", old_text)
    diff = _change_diff(".github/workflows/ci.yml", old_text, new_text)

    findings = host_boundary_run(_context(tmp_path, diff))

    assert [f.check_id for f in findings] == [
        "SHIP-HOST-BOUNDARY-WORKFLOW-WRITE-ALL"
    ]
    finding = findings[0]
    assert finding.severity == "critical"
    assert finding.blocks_release is True
    assert finding.evidence == {"kind": "workflow_write_all", "job": "<top-level>"}


def test_pull_request_target_added(tmp_path: Path) -> None:
    """Also exercises the YAML 1.1 normalization: a bare ``on:`` key parses
    as boolean True and must still be read as the trigger map."""
    new_text = (
        "name: Auto label\n"
        "on:\n"
        "  pull_request_target:\n"
        "    types: [opened]\n"
        "jobs:\n"
        "  label:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - run: echo hi\n"
    )
    diff = _new_file_diff(".github/workflows/label.yaml", new_text)

    findings = host_boundary_run(_context(tmp_path, diff))

    assert [f.check_id for f in findings] == [
        "SHIP-HOST-BOUNDARY-PULL-REQUEST-TARGET-ADDED"
    ]
    finding = findings[0]
    assert finding.severity == "critical"
    assert finding.blocks_release is False
    assert finding.evidence == {"kind": "workflow_pull_request_target_added"}


def test_deleted_workflow_is_skipped(tmp_path: Path) -> None:
    """Gate removal is covered by SHIP-VERIFY-CI-GATE-REMOVED — the host
    boundary check must not duplicate it."""
    diff = (
        "diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml\n"
        "deleted file mode 100644\n"
        "index 1111111..0000000\n"
        "--- a/.github/workflows/ci.yml\n"
        "+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n"
        "-name: CI\n"
        "-on: push\n"
    )

    assert host_boundary_run(_context(tmp_path, diff)) == []


# --- Reward-hacking guard: suppression immunity ---------------------------------


def test_manifest_ignore_cannot_suppress_host_boundary_findings(
    tmp_path: Path,
) -> None:
    """Unit: a checks.ignore entry targeting a host boundary check must NOT
    flip ``suppressed`` — otherwise the PR that grants itself a wildcard
    allow could also suppress the check that flags it."""
    old_text = json.dumps({"permissions": {"allow": []}}, indent=2)
    new_text = json.dumps({"permissions": {"allow": ["Bash(*)"]}}, indent=2)
    _write(tmp_path, ".claude/settings.json", old_text)
    diff = _change_diff(".claude/settings.json", old_text, new_text)
    findings = host_boundary_run(_context(tmp_path, diff))
    assert len(findings) == 1

    apply_suppressions(
        findings,
        [
            SuppressionConfig(
                check_id="SHIP-HOST-BOUNDARY-PERMISSION-WILDCARD-ALLOW",
                reason="make CI green",
            )
        ],
    )

    assert findings[0].suppressed is False
    assert findings[0].blocks_release is True


# --- Determinism -----------------------------------------------------------------


def test_same_diff_twice_produces_identical_findings(tmp_path: Path) -> None:
    diff = _new_file_diff(
        ".mcp.json",
        json.dumps(
            {
                "mcpServers": {
                    "b-server": {"command": "uvx b"},
                    "a-server": {"url": "https://a.example.com/mcp"},
                }
            },
            indent=2,
        ),
    ) + _new_file_diff(
        ".claude/settings.json",
        json.dumps({"permissions": {"allow": ["Bash(*)", "Bash(git status)"]}}),
    )

    first = host_boundary_run(_context(tmp_path, diff))
    second = host_boundary_run(_context(tmp_path, diff))

    assert [f.model_dump(mode="json") for f in first] == [
        f.model_dump(mode="json") for f in second
    ]
    assert len(first) == 4
