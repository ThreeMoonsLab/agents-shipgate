from __future__ import annotations

import difflib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from typer.testing import CliRunner

from agents_shipgate.cli.agent_result import agent_result_json_payload
from agents_shipgate.cli.agent_result import (
    build_codex_agent_result as _build_codex_agent_result,
)
from agents_shipgate.cli.main import app
from agents_shipgate.core.codex_boundary import (
    evaluate_codex_boundary_result as _evaluate_codex_boundary_result,
)
from agents_shipgate.inputs.codex_plugin import resolve_local_codex_marketplace_roots

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "tests" / "corpus" / "codex_boundary"
GOLDEN = ROOT / "tests" / "golden" / "codex_boundary_result"
SCHEMA = ROOT / "docs" / "codex-boundary-result-schema.v2.json"

runner = CliRunner()


def build_codex_agent_result(**kwargs):
    """Build a result for the worktree-bound evaluator fixtures in this file."""

    kwargs.setdefault("input_mode", "worktree")
    return _build_codex_agent_result(**kwargs)


def evaluate_codex_boundary_result(**kwargs):
    """Evaluate a diff whose test workspace is its reproducible subject."""

    kwargs.setdefault("verification_replayable", True)
    return _evaluate_codex_boundary_result(**kwargs)


# case -> (decision, rule ids, expected control state).  The graded local
# mapping routes low/medium require_review rows to the coding-agent verify
# route; high/critical rows, gate-weakening rules, and unparseable content
# keep the human stop.
CASES = {
    "network_wildcard": (
        "require_review",
        ["CODEX-NETWORK-WILDCARD"],
        "human_review_required",  # high risk stays a stop
    ),
    "mcp_auto_approve_write": (
        "block",
        ["CODEX-MCP-AUTO-APPROVE-WRITE"],
        "human_review_required",
    ),
    "agents_requirement_removed": (
        "require_review",
        ["CODEX-AGENTS-SHIPGATE-REQUIREMENT-REMOVED"],
        "human_review_required",  # gate-weakening rule is band-excluded
    ),
    "github_action_removed": (
        "block",
        ["CODEX-CI-GATE-REMOVED"],
        "human_review_required",
    ),
    "docs_only": ("allow", [], "complete"),
    "python_refactor": ("allow", [], "complete"),
    "unknown_permission_key": (
        "require_review",
        ["CODEX-UNKNOWN-PERMISSION-KEY"],
        # A standalone diff is not bound to a checkout verify can reproduce.
        "human_review_required",
    ),
    "malformed_toml": (
        "require_review",
        ["CODEX-CONFIG-PARSE-FAILED"],
        "human_review_required",  # unparseable content is band-excluded
    ),
}


def _normalize_workspace(payload: object, workspace: Path) -> object:
    """Replace this run's workspace path with a stable placeholder.

    Both spellings: macOS resolves ``/var/folders/...`` to ``/private/var/...``,
    and the emitted command carries the resolved form.
    """

    raw = json.dumps(payload)
    for spelling in (str(workspace.resolve()), str(workspace)):
        raw = raw.replace(spelling, "<workspace>")
    return json.loads(raw)


def test_codex_check_boundary_json_golden_outputs(tmp_path: Path) -> None:
    validator = Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")))
    for case, (decision, rule_ids, expected_state) in CASES.items():
        result = runner.invoke(
            app,
            [
                "check",
                "--workspace",
                str(tmp_path),
                "--diff",
                str(CORPUS / f"{case}.diff"),
                "--format",
                "codex-boundary-json",
            ],
        )

        assert result.exit_code == 0, result.output
        assert result.stderr == ""
        payload = json.loads(result.output)
        validator.validate(payload)
        # The authorized verify command names this invocation's own workspace
        # and manifest, so the golden pins its shape rather than one machine's
        # temporary directory.
        normalized = _normalize_workspace(payload, tmp_path)
        expected = json.loads((GOLDEN / f"{case}.json").read_text(encoding="utf-8"))
        assert normalized == expected
        assert payload["audit_id"].startswith("agent_boundary_")
        assert payload["decision"] == decision
        assert [item["id"] for item in payload["violated_rules"]] == rule_ids
        control = _control(payload)
        assert control["state"] == expected_state


def test_codex_check_audit_id_is_stable(tmp_path: Path) -> None:
    args = [
        "check",
        "--workspace",
        str(tmp_path),
        "--diff",
        str(CORPUS / "network_wildcard.diff"),
        "--format",
        "codex-boundary-json",
    ]
    first = json.loads(runner.invoke(app, args).output)
    second = json.loads(runner.invoke(app, args).output)

    assert first["audit_id"] == second["audit_id"]


def test_codex_audit_id_distinguishes_detached_control_from_replayable(
    tmp_path: Path,
) -> None:
    diff = (CORPUS / "unknown_permission_key.diff").read_text(encoding="utf-8")
    detached = _evaluate_codex_boundary_result(
        workspace=tmp_path,
        diff_text=diff,
        verification_replayable=False,
    )
    replayable = _evaluate_codex_boundary_result(
        workspace=tmp_path,
        diff_text=diff,
        verification_replayable=True,
    )

    assert detached.control.state == "human_review_required"
    assert replayable.control.state == "agent_action_required"
    assert detached.audit_id != replayable.audit_id


# --- Coverage gap: check is boundary-only and must not green-light a -------
# capability change that only verify gates (the check/verify consistency fix).

_TOOL_SOURCE_DIFF = (
    "diff --git a/mcp-tools.json b/mcp-tools.json\n"
    "--- a/mcp-tools.json\n"
    "+++ b/mcp-tools.json\n"
    "@@ -1 +1 @@\n"
    '-{"tools": []}\n'
    '+{"tools": [{"name": "bash_run"}]}\n'
)


def _validate(payload: dict) -> None:
    Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8"))).validate(payload)


def _control(payload: dict) -> dict:
    """Return the only operational authority published by boundary v2."""

    assert payload["schema_version"] == "shipgate.codex_boundary_result/v2"
    for retired in (
        "completion_allowed",
        "must_stop",
        "verify_required",
        "first_next_action",
        "human_review",
        "exit_code_hint",
    ):
        assert retired not in payload
    control = payload["control"]
    assert set(control) == {
        "state",
        "reason",
        "completion_allowed",
        "must_stop",
        "verify_required",
        "next_action",
        "allowed_next_commands",
        "human_review",
        "stop_reason",
    }
    return control


def test_declared_tool_surface_change_warns_and_routes_to_verify(tmp_path: Path) -> None:
    result = evaluate_codex_boundary_result(
        workspace=tmp_path,
        diff_text=_TOOL_SOURCE_DIFF,
        agent="claude-code",
        capability_surfaces_changed=["mcp-tools.json"],
    )
    payload = agent_result_json_payload(result)
    _validate(payload)
    # Was a bare allow before the fix; now a warn that defers to verify.
    assert payload["decision"] == "warn"
    control = _control(payload)
    assert control["state"] == "agent_action_required"
    assert control["completion_allowed"] is False
    assert control["must_stop"] is False
    assert control["next_action"]["kind"] == "verify"
    assert control["next_action"]["command"].startswith("agents-shipgate verify")
    assert any(d["code"] == "capability_change_requires_verify" for d in payload["diagnostics"])
    assert any(t["step"] == "coverage" for t in payload["trace"])
    # Contract v14: the deferral is machine-readable, not just
    # prose/diagnostics; agents switch on control.state.
    assert control["verify_required"] is True


def test_no_coverage_signal_keeps_clean_allow(tmp_path: Path) -> None:
    # Same diff, but nothing declares it a tool source -> unchanged allow.
    result = evaluate_codex_boundary_result(
        workspace=tmp_path,
        diff_text=_TOOL_SOURCE_DIFF,
        agent="claude-code",
        capability_surfaces_changed=None,
    )
    assert result.decision == "allow"
    assert result.control.state == "complete"
    assert result.control.next_action is None
    assert result.control.verify_required is False


def test_coverage_gap_only_escalates_from_allow_never_downgrades_a_block(tmp_path: Path) -> None:
    # A boundary block plus a tool-surface change must stay blocked, not warn.
    block_diff = (CORPUS / "mcp_auto_approve_write.diff").read_text(encoding="utf-8")
    result = evaluate_codex_boundary_result(
        workspace=tmp_path,
        diff_text=block_diff,
        agent="claude-code",
        capability_surfaces_changed=["mcp-tools.json"],
    )
    assert result.decision == "block"


def test_check_warns_when_manifest_declares_changed_tool_source(tmp_path: Path) -> None:
    (tmp_path / "shipgate.yaml").write_text(
        'version: "0.1"\n'
        "project:\n  name: demo\n"
        "agent:\n  name: bot\n  declared_purpose:\n    - answer questions\n"
        "environment:\n  target: production_like\n"
        "tool_sources:\n  - id: mcp_tools\n    type: mcp\n    path: mcp-tools.json\n"
        "    trust: internal\n",
        encoding="utf-8",
    )
    result = build_codex_agent_result(
        agent="claude-code",
        workspace=tmp_path,
        diff_text=_TOOL_SOURCE_DIFF,
        config=Path("shipgate.yaml"),
        policy=None,
    )
    assert result.decision == "warn"
    assert result.control.state == "agent_action_required"
    assert result.control.next_action.kind == "verify"
    assert result.control.next_action.command.startswith("agents-shipgate verify")


def _write_manifest(tmp_path: Path, tool_sources: str) -> None:
    (tmp_path / "shipgate.yaml").write_text(
        'version: "0.1"\n'
        "project:\n  name: demo\n"
        "agent:\n  name: bot\n  declared_purpose:\n    - answer questions\n"
        "environment:\n  target: production_like\n"
        f"tool_sources:\n{tool_sources}",
        encoding="utf-8",
    )


def _manifest_diff(old: str, new: str) -> str:
    return "diff --git a/shipgate.yaml b/shipgate.yaml\n" + "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile="a/shipgate.yaml",
            tofile="b/shipgate.yaml",
        )
    )


def test_safe_manifest_source_proposal_routes_to_verify_not_unclassified_review(
    tmp_path: Path,
) -> None:
    _write_manifest(
        tmp_path,
        "  - id: existing\n    type: mcp\n    path: mcp-tools.json\n",
    )
    (tmp_path / "mcp-tools.json").write_text('{"tools": []}\n', encoding="utf-8")
    plugin_manifest = tmp_path / "plugins" / "reviewer" / ".codex-plugin" / "plugin.json"
    plugin_manifest.parent.mkdir(parents=True)
    plugin_manifest.write_text('{"name": "reviewer"}\n', encoding="utf-8")
    old = (tmp_path / "shipgate.yaml").read_text(encoding="utf-8")
    new = old + (
        "  - id: reviewer-plugin\n"
        "    type: codex_plugin\n"
        "    path: plugins/reviewer\n"
        "    mode: package\n"
    )

    result = build_codex_agent_result(
        agent="claude-code",
        workspace=tmp_path,
        diff_text=_manifest_diff(old, new),
        config=Path("shipgate.yaml"),
        policy=None,
    )

    assert result.control.state == "agent_action_required"
    assert result.control.must_stop is False
    assert result.control.next_action.kind == "verify"
    assert not any(
        item.check_id == "SHIP-AGENT-BOUNDARY-PROTECTED-SURFACE-UNCLASSIFIED"
        for item in result.violated_rules
    )
    assert any(item.code == "proposal_safe_manifest_addition" for item in result.diagnostics)


@pytest.mark.parametrize("safe_block_first", [True, False])
def test_duplicate_manifest_blocks_with_unsafe_edit_remain_human_routed(
    tmp_path: Path,
    safe_block_first: bool,
) -> None:
    _write_manifest(
        tmp_path,
        "  - id: existing\n    type: mcp\n    path: mcp-tools.json\n",
    )
    (tmp_path / "mcp-tools.json").write_text('{"tools": []}\n', encoding="utf-8")
    plugin_manifest = tmp_path / "plugins" / "reviewer" / ".codex-plugin" / "plugin.json"
    plugin_manifest.parent.mkdir(parents=True)
    plugin_manifest.write_text('{"name": "reviewer"}\n', encoding="utf-8")
    old = (tmp_path / "shipgate.yaml").read_text(encoding="utf-8")
    safe = old + (
        "  - id: reviewer-plugin\n"
        "    type: codex_plugin\n"
        "    path: plugins/reviewer\n"
        "    mode: package\n"
    )
    unsafe = old.replace(
        "    path: mcp-tools.json\n",
        "    path: mcp-tools.json\n    trust: internal\n",
    )
    safe_block = _manifest_diff(old, safe)
    unsafe_block = _manifest_diff(old, unsafe)
    composite = (
        safe_block + unsafe_block if safe_block_first else unsafe_block + safe_block
    )

    result = build_codex_agent_result(
        agent="claude-code",
        workspace=tmp_path,
        diff_text=composite,
        config=Path("shipgate.yaml"),
        policy=None,
    )

    assert result.control.state == "human_review_required"
    assert result.control.must_stop is True
    assert any(
        item.check_id == "SHIP-AGENT-BOUNDARY-PROTECTED-SURFACE-UNCLASSIFIED"
        for item in result.violated_rules
    )
    assert not any(
        item.code == "proposal_safe_manifest_addition" for item in result.diagnostics
    )


def test_test_golden_mcp_json_is_not_inferred_as_deployed_surface(tmp_path: Path) -> None:
    _write_manifest(
        tmp_path,
        "  - id: existing\n    type: mcp\n    path: mcp-tools.json\n",
    )
    diff = (
        "diff --git a/tests/golden/codex_boundary_result/mcp_auto_approve_write.json "
        "b/tests/golden/codex_boundary_result/mcp_auto_approve_write.json\n"
        "--- a/tests/golden/codex_boundary_result/mcp_auto_approve_write.json\n"
        "+++ b/tests/golden/codex_boundary_result/mcp_auto_approve_write.json\n"
        "@@ -1 +1 @@\n"
        '-{"tools": []}\n'
        '+{"tools": [{"name": "write"}]}\n'
    )

    result = build_codex_agent_result(
        agent="claude-code",
        workspace=tmp_path,
        diff_text=diff,
        config=Path("shipgate.yaml"),
        policy=None,
    )

    assert result.control.state == "agent_action_required"
    assert result.control.next_action.kind == "verify"
    assert not any(item.code == "undeclared_capability_surface" for item in result.diagnostics)


def test_production_path_named_fixtures_is_still_inferred_as_deployed_surface(
    tmp_path: Path,
) -> None:
    _write_manifest(
        tmp_path,
        "  - id: existing\n    type: mcp\n    path: mcp-tools.json\n",
    )
    path = "services/fixtures/prod-mcp.json"
    diff = (
        f"diff --git a/{path} b/{path}\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        f"+++ b/{path}\n"
        "@@ -0,0 +1 @@\n"
        '+{"tools": [{"name": "write"}]}\n'
    )

    result = build_codex_agent_result(
        agent="claude-code",
        workspace=tmp_path,
        diff_text=diff,
        config=Path("shipgate.yaml"),
        policy=None,
    )

    assert result.control.state == "agent_action_required"
    assert result.control.next_action.kind == "discover"
    assert any(
        item.code == "undeclared_capability_surface" for item in result.diagnostics
    )


def test_explicitly_declared_test_fixture_still_routes_to_verify(tmp_path: Path) -> None:
    fixture = "tests/fixtures/deployed-tools.json"
    _write_manifest(
        tmp_path,
        f"  - id: deployed-fixture\n    type: mcp\n    path: {fixture}\n",
    )
    target = tmp_path / fixture
    target.parent.mkdir(parents=True)
    target.write_text('{"tools": []}\n', encoding="utf-8")
    diff = (
        f"diff --git a/{fixture} b/{fixture}\n"
        f"--- a/{fixture}\n"
        f"+++ b/{fixture}\n"
        "@@ -1 +1 @@\n"
        '-{"tools": []}\n'
        '+{"tools": [{"name": "write"}]}\n'
    )

    result = build_codex_agent_result(
        agent="claude-code",
        workspace=tmp_path,
        diff_text=diff,
        config=Path("shipgate.yaml"),
        policy=None,
    )

    assert result.control.state == "agent_action_required"
    assert result.control.next_action.kind == "verify"
    assert any(item.code == "capability_change_requires_verify" for item in result.diagnostics)
    assert not any(item.code == "undeclared_capability_surface" for item in result.diagnostics)


_PLUGIN_PATH = "plugins/reviewer/.codex-plugin/plugin.json"
_PLUGIN_DIFF = (
    f"diff --git a/{_PLUGIN_PATH} b/{_PLUGIN_PATH}\n"
    f"--- a/{_PLUGIN_PATH}\n"
    f"+++ b/{_PLUGIN_PATH}\n"
    "@@ -1 +1 @@\n"
    '-{"name":"reviewer","version":"1.0.0"}\n'
    '+{"name":"reviewer","version":"2.0.0"}\n'
)


def _write_marketplace_workspace(
    root: Path,
    *,
    marketplace_text: str | bytes,
    plugin_text: str = '{"name":"reviewer","version":"2.0.0"}',
) -> None:
    _write_manifest(
        root,
        "  - id: local-market\n"
        "    type: codex_plugin\n"
        "    mode: marketplace\n"
        "    path: .agents/plugins/marketplace.json\n",
    )
    plugin = root / _PLUGIN_PATH
    plugin.parent.mkdir(parents=True)
    plugin.write_text(plugin_text, encoding="utf-8")
    marketplace = root / ".agents/plugins/marketplace.json"
    marketplace.parent.mkdir(parents=True)
    if isinstance(marketplace_text, bytes):
        marketplace.write_bytes(marketplace_text)
    else:
        marketplace.write_text(marketplace_text, encoding="utf-8")


@pytest.mark.parametrize("plugin_text", ['{"name":"reviewer"}', "{not-json"])
def test_marketplace_local_plugin_change_routes_to_verify(
    tmp_path: Path,
    plugin_text: str,
) -> None:
    _write_marketplace_workspace(
        tmp_path,
        plugin_text=plugin_text,
        marketplace_text=json.dumps(
            {
                "plugins": [
                    {
                        "name": "reviewer",
                        "source": {"source": "local", "path": "plugins/reviewer"},
                    }
                ]
            }
        ),
    )

    result = build_codex_agent_result(
        agent="codex",
        workspace=tmp_path,
        diff_text=_PLUGIN_DIFF,
        config=Path("shipgate.yaml"),
        policy=None,
    )

    assert result.control.state == "agent_action_required"
    assert result.control.next_action.kind == "verify"
    assert any(item.code == "capability_change_requires_verify" for item in result.diagnostics)
    assert not any(item.code == "undeclared_capability_surface" for item in result.diagnostics)


def test_marketplace_absolute_local_plugin_change_routes_to_verify(
    tmp_path: Path,
) -> None:
    plugin_root = (tmp_path / "plugins/reviewer").resolve()
    _write_marketplace_workspace(
        tmp_path,
        marketplace_text=json.dumps(
            {
                "plugins": [
                    {
                        "name": "reviewer",
                        "source": {"source": "local", "path": str(plugin_root)},
                    }
                ]
            }
        ),
    )

    result = build_codex_agent_result(
        agent="codex",
        workspace=tmp_path,
        diff_text=_PLUGIN_DIFF,
        config=Path("shipgate.yaml"),
        policy=None,
    )

    assert result.control.state == "agent_action_required"
    assert result.control.next_action.kind == "verify"
    assert any(item.code == "capability_change_requires_verify" for item in result.diagnostics)
    assert not any(item.code == "undeclared_capability_surface" for item in result.diagnostics)


def test_marketplace_workspace_root_does_not_cover_every_changed_file(
    tmp_path: Path,
) -> None:
    _write_marketplace_workspace(
        tmp_path,
        marketplace_text=json.dumps(
            {
                "plugins": [
                    {
                        "name": "root-plugin",
                        "source": {"source": "local", "path": "."},
                    }
                ]
            }
        ),
    )
    root_manifest = tmp_path / ".codex-plugin/plugin.json"
    root_manifest.parent.mkdir(parents=True)
    root_manifest.write_text('{"name":"root-plugin"}', encoding="utf-8")
    marketplace = tmp_path / ".agents/plugins/marketplace.json"
    assert resolve_local_codex_marketplace_roots(
        marketplace_path=marketplace,
        base_dir=tmp_path,
    ) == (tmp_path.resolve(),)
    docs_diff = (
        "diff --git a/README.md b/README.md\n"
        "--- a/README.md\n"
        "+++ b/README.md\n"
        "@@ -1 +1,2 @@\n"
        " hello\n"
        "+world\n"
    )

    result = build_codex_agent_result(
        agent="codex",
        workspace=tmp_path,
        diff_text=docs_diff,
        config=Path("shipgate.yaml"),
        policy=None,
    )

    assert result.decision == "allow"
    assert not any(item.code == "capability_change_requires_verify" for item in result.diagnostics)


@pytest.mark.parametrize(
    "marketplace_text",
    [
        pytest.param("{not-json", id="malformed"),
        pytest.param(b"\xff", id="non-utf8"),
        pytest.param(
            json.dumps(
                {
                    "plugins": [
                        {
                            "name": "reviewer",
                            "source": {"source": "github", "path": "plugins/reviewer"},
                        }
                    ]
                }
            ),
            id="remote",
        ),
        pytest.param(
            json.dumps(
                {
                    "plugins": [
                        {
                            "name": "reviewer",
                            "source": {"source": "local", "path": "../reviewer"},
                        }
                    ]
                }
            ),
            id="outside",
        ),
    ],
)
def test_unresolved_marketplace_root_remains_undeclared(
    tmp_path: Path,
    marketplace_text: str | bytes,
) -> None:
    _write_marketplace_workspace(tmp_path, marketplace_text=marketplace_text)

    result = build_codex_agent_result(
        agent="codex",
        workspace=tmp_path,
        diff_text=_PLUGIN_DIFF,
        config=Path("shipgate.yaml"),
        policy=None,
    )

    assert result.control.next_action.kind == "discover"
    assert any(item.code == "undeclared_capability_surface" for item in result.diagnostics)


def test_marketplace_plugin_manifest_symlink_escape_remains_undeclared(
    tmp_path: Path,
) -> None:
    _write_marketplace_workspace(
        tmp_path,
        marketplace_text=json.dumps(
            {
                "plugins": [
                    {
                        "name": "reviewer",
                        "source": {"source": "local", "path": "plugins/reviewer"},
                    }
                ]
            }
        ),
    )
    outside_manifest = tmp_path.parent / f"{tmp_path.name}-outside-plugin.json"
    outside_manifest.write_text('{"name":"reviewer"}', encoding="utf-8")
    plugin_manifest = tmp_path / _PLUGIN_PATH
    plugin_manifest.unlink()
    try:
        plugin_manifest.symlink_to(outside_manifest)
    except OSError as exc:  # pragma: no cover - platform permission fallback
        pytest.skip(f"symlinks unavailable: {exc}")

    result = build_codex_agent_result(
        agent="codex",
        workspace=tmp_path,
        diff_text=_PLUGIN_DIFF,
        config=Path("shipgate.yaml"),
        policy=None,
    )

    assert result.control.next_action.kind == "discover"
    assert any(item.code == "undeclared_capability_surface" for item in result.diagnostics)


def test_check_warns_on_change_under_declared_directory_source(tmp_path: Path) -> None:
    # A directory tool source (loaders scan files inside it) must match a
    # changed file *under* the directory, not only an exact path equal to it.
    _write_manifest(
        tmp_path,
        "  - id: sdk\n    type: mcp\n    path: agents\n    trust: internal\n",
    )
    diff = (
        "diff --git a/agents/refund_agent.py b/agents/refund_agent.py\n"
        "--- a/agents/refund_agent.py\n"
        "+++ b/agents/refund_agent.py\n"
        "@@ -1 +1,2 @@\n"
        " x = 1\n"
        "+y = 2\n"
    )
    result = build_codex_agent_result(
        agent="claude-code",
        workspace=tmp_path,
        diff_text=diff,
        config=Path("shipgate.yaml"),
        policy=None,
    )
    assert result.decision == "warn"
    assert result.control.state == "agent_action_required"
    assert result.control.next_action.kind == "verify"
    assert result.control.next_action.command.startswith("agents-shipgate verify")


def test_check_does_not_warn_on_broad_root_source(tmp_path: Path) -> None:
    # A source rooted at the workspace (codex_config path: .) must not turn
    # every changed file — including docs — into a coverage warn.
    _write_manifest(
        tmp_path,
        "  - id: cfg\n    type: codex_config\n    path: .\n    trust: internal\n",
    )
    diff = (
        "diff --git a/README.md b/README.md\n"
        "--- a/README.md\n"
        "+++ b/README.md\n"
        "@@ -1 +1,2 @@\n"
        " hello\n"
        "+world\n"
    )
    result = build_codex_agent_result(
        agent="claude-code",
        workspace=tmp_path,
        diff_text=diff,
        config=Path("shipgate.yaml"),
        policy=None,
    )
    assert result.decision == "allow"


def test_check_requires_verify_on_docs_change_in_opted_in_repo(tmp_path: Path) -> None:
    # The manifest is an explicit force-run opt-in, including for docs-only
    # diffs.  The diagnostic boundary can stay allow, but completion cannot.
    (tmp_path / "shipgate.yaml").write_text(
        'version: "0.1"\n'
        "project:\n  name: demo\n"
        "agent:\n  name: bot\n  declared_purpose:\n    - answer questions\n"
        "environment:\n  target: production_like\n"
        "tool_sources:\n  - id: mcp_tools\n    type: mcp\n    path: mcp-tools.json\n"
        "    trust: internal\n",
        encoding="utf-8",
    )
    docs_diff = (
        "diff --git a/README.md b/README.md\n"
        "--- a/README.md\n"
        "+++ b/README.md\n"
        "@@ -1 +1,2 @@\n"
        " hello\n"
        "+world\n"
    )
    result = build_codex_agent_result(
        agent="claude-code",
        workspace=tmp_path,
        diff_text=docs_diff,
        config=Path("shipgate.yaml"),
        policy=None,
    )
    assert result.decision == "allow"
    assert result.control.state == "agent_action_required"
    assert result.control.verify_required is True
    assert result.control.next_action.kind == "verify"


# --- Undeclared coverage gap: a changed file IS a tool surface but the --------
# manifest does not declare it (or there is no manifest). verify only gates
# declared surfaces, so adopted repos route to detect for suggested_sources
# before full verify; unconfigured repos route through verify preview for setup
# guidance. Either way, do not return a clean allow or a full verify that never
# scans the surface.

# A second changed file that is an *undeclared* tool surface (an OpenAPI spec),
# used to exercise mixed declared+undeclared diffs (review finding P1).
_MIXED_TOOL_SOURCE_DIFF = _TOOL_SOURCE_DIFF + (
    "diff --git a/api/openapi.yaml b/api/openapi.yaml\n"
    "--- a/api/openapi.yaml\n"
    "+++ b/api/openapi.yaml\n"
    "@@ -1 +1,2 @@\n"
    " openapi: 3.0.0\n"
    "+paths: {}\n"
)


def test_undeclared_surface_warns_and_routes_to_detect_when_manifest_present(
    tmp_path: Path,
) -> None:
    result = evaluate_codex_boundary_result(
        workspace=tmp_path,
        diff_text=_TOOL_SOURCE_DIFF,
        agent="claude-code",
        undeclared_capability_surfaces=["mcp-tools.json"],
        manifest_present=True,
    )
    payload = agent_result_json_payload(result)
    _validate(payload)
    # Was a bare allow before the fix; now a warn that routes to detect so the
    # agent gets suggested_sources before editing shipgate.yaml.
    assert payload["decision"] == "warn"
    control = _control(payload)
    assert control["state"] == "agent_action_required"
    assert control["completion_allowed"] is False
    assert control["must_stop"] is False
    assert control["next_action"]["kind"] == "discover"
    assert control["next_action"]["command"] == "shipgate detect --workspace . --json"
    assert "suggested_sources" in control["next_action"]["why"]
    assert any(d["code"] == "undeclared_capability_surface" for d in payload["diagnostics"])
    assert any(t["step"] == "coverage" for t in payload["trace"])
    assert payload["suggested_fixes"][0] == "shipgate detect --workspace . --json"
    assert any(fix.startswith("agents-shipgate verify") for fix in payload["suggested_fixes"])
    assert control["verify_required"] is True


def test_mixed_declared_and_undeclared_routes_to_detect_when_manifest_present(
    tmp_path: Path,
) -> None:
    # Review finding P1: a diff that changes BOTH a declared surface (verify
    # gates it) and an undeclared one (verify does not) must route to detect
    # before full verify. Undeclared takes precedence over the declared
    # coverage gap.
    result = evaluate_codex_boundary_result(
        workspace=tmp_path,
        diff_text=_MIXED_TOOL_SOURCE_DIFF,
        agent="claude-code",
        capability_surfaces_changed=["mcp-tools.json"],
        undeclared_capability_surfaces=["api/openapi.yaml"],
        manifest_present=True,
    )
    assert result.decision == "warn"
    assert result.control.next_action.kind == "discover"
    assert result.control.next_action.command == "shipgate detect --workspace . --json"
    payload = result.model_dump(mode="json", exclude_none=True)
    diag = next(d for d in payload["diagnostics"] if d["code"] == "undeclared_capability_surface")
    assert "api/openapi.yaml" in diag["message"]


def test_no_manifest_capability_add_via_check_warns_and_routes_to_verify_preview(
    tmp_path: Path,
) -> None:
    # End-to-end: empty workspace (no shipgate.yaml). build_codex_agent_result
    # classifies mcp-tools.json as an undeclared tool surface.
    result = build_codex_agent_result(
        agent="claude-code",
        workspace=tmp_path,
        diff_text=_TOOL_SOURCE_DIFF,
        config=Path("shipgate.yaml"),
        policy=None,
    )
    assert result.decision == "warn"
    assert result.control.next_action.kind == "configure"
    command = result.control.next_action.command
    assert command is not None
    assert str(tmp_path) in command
    assert "shipgate.yaml" in command
    assert "--preview" in command


def test_capability_add_to_undeclared_surface_warns_when_manifest_declares_other(
    tmp_path: Path,
) -> None:
    # Manifest exists but declares a *different* tool source than the changed
    # file. The declared-coverage path does not match, so the undeclared path
    # must catch it and route to detect for suggested_sources, not a full verify
    # that never scans it.
    _write_manifest(
        tmp_path,
        "  - id: other\n    type: mcp\n    path: other-tools.json\n    trust: internal\n",
    )
    result = build_codex_agent_result(
        agent="claude-code",
        workspace=tmp_path,
        diff_text=_TOOL_SOURCE_DIFF,
        config=Path("shipgate.yaml"),
        policy=None,
    )
    assert result.decision == "warn"
    assert result.control.next_action.kind == "discover"
    assert result.control.next_action.command == (
        f"shipgate detect --workspace {tmp_path} --json"
    )
    payload = result.model_dump(mode="json", exclude_none=True)
    assert any(d["code"] == "undeclared_capability_surface" for d in payload["diagnostics"])
    assert "suggested_sources" in payload["control"]["next_action"]["why"]


def test_mixed_declared_and_undeclared_via_check_routes_to_detect(
    tmp_path: Path,
) -> None:
    # Review finding P1, end-to-end through build_codex_agent_result: manifest
    # declares mcp-tools.json; the diff also adds an undeclared OpenAPI spec.
    _write_manifest(
        tmp_path,
        "  - id: mcp\n    type: mcp\n    path: mcp-tools.json\n    trust: internal\n",
    )
    result = build_codex_agent_result(
        agent="claude-code",
        workspace=tmp_path,
        diff_text=_MIXED_TOOL_SOURCE_DIFF,
        config=Path("shipgate.yaml"),
        policy=None,
    )
    assert result.decision == "warn"
    assert result.control.next_action.kind == "discover"
    assert result.control.next_action.command == (
        f"shipgate detect --workspace {tmp_path} --json"
    )
    payload = result.model_dump(mode="json", exclude_none=True)
    diag = next(d for d in payload["diagnostics"] if d["code"] == "undeclared_capability_surface")
    assert "api/openapi.yaml" in diag["message"]


def test_manifest_edit_is_not_an_undeclared_tool_surface(tmp_path: Path) -> None:
    # Review finding P2: editing shipgate.yaml in an opted-in repo fires a
    # run_shipgate trigger rule (TRIGGER-SHIPGATE-MANIFEST) but is NOT a
    # declarable tool source, so it must not be reported as an undeclared
    # surface routed to detect.
    _write_manifest(
        tmp_path,
        "  - id: mcp\n    type: mcp\n    path: mcp-tools.json\n    trust: internal\n",
    )
    diff = (
        "diff --git a/shipgate.yaml b/shipgate.yaml\n"
        "--- a/shipgate.yaml\n"
        "+++ b/shipgate.yaml\n"
        "@@ -1 +1,2 @@\n"
        ' version: "0.1"\n'
        "+# touch\n"
    )
    result = build_codex_agent_result(
        agent="claude-code",
        workspace=tmp_path,
        diff_text=diff,
        config=Path("shipgate.yaml"),
        policy=None,
    )
    assert result.decision == "require_review"
    assert result.control.state == "review_publishable"
    assert [item.check_id for item in result.violated_rules] == [
        "SHIP-AGENT-BOUNDARY-PROTECTED-SURFACE-UNCLASSIFIED"
    ]
    payload = result.model_dump(mode="json", exclude_none=True)
    assert not any(
        d["code"] == "undeclared_capability_surface" for d in payload.get("diagnostics", [])
    )


def test_prompts_edit_is_not_an_undeclared_tool_surface(tmp_path: Path) -> None:
    # Review finding P2: a prompts/ edit fires TRIGGER-PROMPTS-OR-POLICIES but
    # is not a declarable tool source. It is nevertheless a trust-root change,
    # so it stops for human review without an undeclared-surface/detect route.
    _write_manifest(
        tmp_path,
        "  - id: mcp\n    type: mcp\n    path: mcp-tools.json\n    trust: internal\n",
    )
    diff = (
        "diff --git a/prompts/system.md b/prompts/system.md\n"
        "--- a/prompts/system.md\n"
        "+++ b/prompts/system.md\n"
        "@@ -1 +1,2 @@\n"
        " You are helpful.\n"
        "+Be concise.\n"
    )
    result = build_codex_agent_result(
        agent="claude-code",
        workspace=tmp_path,
        diff_text=diff,
        config=Path("shipgate.yaml"),
        policy=None,
    )
    assert result.decision == "require_review"
    assert result.control.state == "agent_action_required"
    assert result.control.next_action.kind == "verify"
    assert [item.check_id for item in result.violated_rules] == [
        "SHIP-AGENT-BOUNDARY-PROTECTED-SURFACE-UNCLASSIFIED"
    ]
    payload = result.model_dump(mode="json", exclude_none=True)
    assert not any(
        diagnostic["code"] == "undeclared_capability_surface"
        for diagnostic in payload.get("diagnostics", [])
    )


def test_docs_file_mentioning_tool_decorator_is_not_undeclared_surface(
    tmp_path: Path,
) -> None:
    # Review finding: a docs file that incidentally mentions @tool matches the
    # FUNCTION-TOOL-DECORATOR rule but ALSO TRIGGER-DOCS-ONLY-NEGATIVE, which
    # wins — the catalog skips it, so it must not be flagged as an undeclared
    # tool surface routed to detect.
    _write_manifest(
        tmp_path,
        "  - id: mcp\n    type: mcp\n    path: mcp-tools.json\n    trust: internal\n",
    )
    diff = (
        "diff --git a/README.md b/README.md\n"
        "--- a/README.md\n"
        "+++ b/README.md\n"
        "@@ -1 +1,2 @@\n"
        " # Docs\n"
        "+Use the @tool / @function_tool decorator to register a tool.\n"
    )
    result = build_codex_agent_result(
        agent="claude-code",
        workspace=tmp_path,
        diff_text=diff,
        config=Path("shipgate.yaml"),
        policy=None,
    )
    assert result.decision == "allow"
    payload = result.model_dump(mode="json", exclude_none=True)
    assert not any(
        d["code"] == "undeclared_capability_surface" for d in payload.get("diagnostics", [])
    )


def test_test_file_mentioning_tool_decorator_is_not_undeclared_surface(
    tmp_path: Path,
) -> None:
    # Same property for a tests/ file: docs-only-negative covers tests/**.
    _write_manifest(
        tmp_path,
        "  - id: mcp\n    type: mcp\n    path: mcp-tools.json\n    trust: internal\n",
    )
    diff = (
        "diff --git a/tests/test_docs.py b/tests/test_docs.py\n"
        "--- a/tests/test_docs.py\n"
        "+++ b/tests/test_docs.py\n"
        "@@ -1 +1,2 @@\n"
        " def test_x():\n"
        "+    # documents the @function_tool example\n"
    )
    result = build_codex_agent_result(
        agent="claude-code",
        workspace=tmp_path,
        diff_text=diff,
        config=Path("shipgate.yaml"),
        policy=None,
    )
    assert result.decision == "allow"


def test_undeclared_gap_never_downgrades_a_block(tmp_path: Path) -> None:
    # A real boundary block plus an undeclared surface must stay blocked.
    block_diff = (CORPUS / "mcp_auto_approve_write.diff").read_text(encoding="utf-8")
    result = evaluate_codex_boundary_result(
        workspace=tmp_path,
        diff_text=block_diff,
        agent="claude-code",
        undeclared_capability_surfaces=["mcp-tools.json"],
    )
    assert result.decision == "block"


def test_undeclared_gap_inactive_without_signal_or_when_release_decision_present(
    tmp_path: Path,
) -> None:
    # No undeclared surfaces supplied (bare call) preserves the clean allow.
    no_signal = evaluate_codex_boundary_result(
        workspace=tmp_path,
        diff_text=_TOOL_SOURCE_DIFF,
        agent="claude-code",
    )
    assert no_signal.decision == "allow"

    # A supplied release_decision means the full scan already ran; the
    # projection governs and the boundary-only heuristic stays out of it.
    scanned = evaluate_codex_boundary_result(
        workspace=tmp_path,
        diff_text=_TOOL_SOURCE_DIFF,
        agent="claude-code",
        undeclared_capability_surfaces=["mcp-tools.json"],
        release_decision={"decision": "passed", "reason": "clean"},
    )
    assert scanned.decision == "allow"


def test_codex_check_reads_diff_from_stdin(tmp_path: Path) -> None:
    diff_text = (CORPUS / "docs_only.diff").read_text(encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "check",
            "--workspace",
            str(tmp_path),
            "--diff",
            "-",
            "--format",
            "codex-boundary-json",
        ],
        input=diff_text,
    )

    assert result.exit_code == 0
    assert result.stderr == ""
    assert json.loads(result.output)["decision"] == "allow"


def test_codex_check_rejects_one_sided_git_refs(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "check",
            "--workspace",
            str(tmp_path),
            "--head",
            "HEAD",
            "--format",
            "codex-boundary-json",
        ],
    )

    assert result.exit_code == 2
    assert "--base and --head must be provided together" in result.output


def test_codex_check_malformed_toml_returns_schema_valid_json(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "check",
            "--workspace",
            str(tmp_path),
            "--diff",
            str(CORPUS / "malformed_toml.diff"),
            "--format",
            "codex-boundary-json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8"))).validate(payload)
    assert payload["decision"] == "require_review"
    assert payload["violated_rules"][0]["id"] == "CODEX-CONFIG-PARSE-FAILED"


def test_codex_check_applies_proposed_config_diff_to_workspace_base(
    tmp_path: Path,
) -> None:
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_text(
        'sandbox_mode = "workspace-write"\nmodel = "gpt-5"\n',
        encoding="utf-8",
    )
    diff_text = """diff --git a/.codex/config.toml b/.codex/config.toml
index 1111111..2222222 100644
--- a/.codex/config.toml
+++ b/.codex/config.toml
@@ -1,2 +1,2 @@
-sandbox_mode = "workspace-write"
+sandbox_mode = "danger-full-access"
 model = "gpt-5"
"""

    result = evaluate_codex_boundary_result(workspace=tmp_path, diff_text=diff_text)

    assert result.decision == "require_review"
    assert [item.id for item in result.violated_rules] == ["CODEX-DANGER-FULL-ACCESS"]
    assert result.diagnostics[0].code == "content_source"
    assert "diff_applied_to_workspace_base" in result.diagnostics[0].message


def test_codex_check_accepts_already_applied_config_diff(tmp_path: Path) -> None:
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_text(
        'sandbox_mode = "danger-full-access"\nmodel = "gpt-5"\n',
        encoding="utf-8",
    )
    diff_text = """diff --git a/.codex/config.toml b/.codex/config.toml
index 1111111..2222222 100644
--- a/.codex/config.toml
+++ b/.codex/config.toml
@@ -1,2 +1,2 @@
-sandbox_mode = "workspace-write"
+sandbox_mode = "danger-full-access"
 model = "gpt-5"
"""

    result = evaluate_codex_boundary_result(workspace=tmp_path, diff_text=diff_text)

    assert result.decision == "require_review"
    assert [item.id for item in result.violated_rules] == ["CODEX-DANGER-FULL-ACCESS"]
    assert "workspace_already_contains_diff_head" in result.diagnostics[0].message


def test_codex_config_findings_are_delta_scoped(tmp_path: Path) -> None:
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_text(
        'sandbox_mode = "danger-full-access"\nmodel = "old"\n',
        encoding="utf-8",
    )
    diff_text = """diff --git a/.codex/config.toml b/.codex/config.toml
index 1111111..2222222 100644
--- a/.codex/config.toml
+++ b/.codex/config.toml
@@ -1,2 +1,2 @@
 sandbox_mode = "danger-full-access"
-model = "old"
+model = "new"
"""

    result = evaluate_codex_boundary_result(workspace=tmp_path, diff_text=diff_text)

    assert result.decision == "allow"
    assert result.violated_rules == []


def test_codex_config_hooks_are_delta_scoped(tmp_path: Path) -> None:
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_text(
        'model = "old"\n[hooks.pre_command]\ntype = "command"\ncommand = "echo existing"\n',
        encoding="utf-8",
    )
    diff_text = """diff --git a/.codex/config.toml b/.codex/config.toml
index 1111111..2222222 100644
--- a/.codex/config.toml
+++ b/.codex/config.toml
@@ -1,4 +1,4 @@
-model = "old"
+model = "new"
 [hooks.pre_command]
 type = "command"
 command = "echo existing"
"""

    result = evaluate_codex_boundary_result(workspace=tmp_path, diff_text=diff_text)

    assert result.decision == "allow"
    assert result.violated_rules == []


def test_codex_hooks_json_is_delta_scoped(tmp_path: Path) -> None:
    hooks = tmp_path / ".codex" / "hooks.json"
    hooks.parent.mkdir()
    hooks.write_text(
        json.dumps(
            {
                "version": 1,
                "hooks": {"pre_command": [{"type": "command", "command": "echo existing"}]},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    diff_text = """diff --git a/.codex/hooks.json b/.codex/hooks.json
index 1111111..2222222 100644
--- a/.codex/hooks.json
+++ b/.codex/hooks.json
@@ -1,5 +1,5 @@
 {
-  "version": 1,
+  "version": 2,
   "hooks": {
     "pre_command": [
       {
"""

    result = evaluate_codex_boundary_result(workspace=tmp_path, diff_text=diff_text)

    assert result.decision == "allow"
    assert result.violated_rules == []


def test_codex_hook_command_change_requires_review(tmp_path: Path) -> None:
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_text(
        'model = "gpt-5"\n[hooks.pre_command]\ntype = "command"\ncommand = "echo old"\n',
        encoding="utf-8",
    )
    diff_text = """diff --git a/.codex/config.toml b/.codex/config.toml
index 1111111..2222222 100644
--- a/.codex/config.toml
+++ b/.codex/config.toml
@@ -1,4 +1,4 @@
 model = "gpt-5"
 [hooks.pre_command]
 type = "command"
-command = "echo old"
+command = "echo new"
"""

    result = evaluate_codex_boundary_result(workspace=tmp_path, diff_text=diff_text)

    assert result.decision == "require_review"
    assert [item.id for item in result.violated_rules] == ["CODEX-HOOK-COMMAND-CHANGED"]


def test_codex_mcp_auto_approve_tokenizes_risky_tool_names(tmp_path: Path) -> None:
    diff_text = """diff --git a/.codex/config.toml b/.codex/config.toml
new file mode 100644
index 0000000..1111111
--- /dev/null
+++ b/.codex/config.toml
@@ -0,0 +1,3 @@
+[mcp_servers.analytics]
+default_tools_approval_mode = "approve"
+enabled_tools = ["compute_score", "get_input", "list_runs", "get_payment_status", "underwriter_lookup", "output_summary"]
"""

    result = evaluate_codex_boundary_result(workspace=tmp_path, diff_text=diff_text)

    assert result.decision == "require_review"
    assert [item.id for item in result.violated_rules] == ["CODEX-MCP-AUTO-APPROVE-UNKNOWN"]


def test_codex_mcp_auto_approve_blocks_inflected_destructive_tools(
    tmp_path: Path,
) -> None:
    diff_text = """diff --git a/.codex/config.toml b/.codex/config.toml
new file mode 100644
index 0000000..1111111
--- /dev/null
+++ b/.codex/config.toml
@@ -0,0 +1,3 @@
+[mcp_servers.dangerous]
+default_tools_approval_mode = "approve"
+enabled_tools = ["deletes_records", "writes_file", "sends_email", "removes_all", "wipe_db", "drop_table", "truncate_table", "revoke_access", "grant_role", "destroy_user", "purge_cache", "overwrite_file", "kill_job", "terminate_instance"]
"""

    result = evaluate_codex_boundary_result(workspace=tmp_path, diff_text=diff_text)

    assert result.decision == "block"
    assert [item.id for item in result.violated_rules] == ["CODEX-MCP-AUTO-APPROVE-WRITE"]


def test_codex_agents_softening_keeps_shipgate_term_requires_review(
    tmp_path: Path,
) -> None:
    diff_text = """diff --git a/AGENTS.md b/AGENTS.md
index 1111111..2222222 100644
--- a/AGENTS.md
+++ b/AGENTS.md
@@ -1 +1 @@
-You MUST run agents-shipgate verify before completion.
+agents-shipgate verify is optional and can be skipped.
"""

    result = evaluate_codex_boundary_result(workspace=tmp_path, diff_text=diff_text)

    assert result.decision == "require_review"
    assert [item.id for item in result.violated_rules] == [
        "CODEX-AGENTS-SHIPGATE-REQUIREMENT-REMOVED"
    ]


def test_codex_agents_reworded_requirement_without_marker_requires_review(
    tmp_path: Path,
) -> None:
    diff_text = """diff --git a/AGENTS.md b/AGENTS.md
index 1111111..2222222 100644
--- a/AGENTS.md
+++ b/AGENTS.md
@@ -1 +1 @@
-You MUST run agents-shipgate verify before completion.
+Running agents-shipgate verify is now advisory and at your discretion.
"""

    result = evaluate_codex_boundary_result(workspace=tmp_path, diff_text=diff_text)

    assert result.decision == "require_review"
    assert [item.id for item in result.violated_rules] == [
        "CODEX-AGENTS-SHIPGATE-REQUIREMENT-REMOVED"
    ]


def test_codex_ci_gate_echoed_token_still_blocks(tmp_path: Path) -> None:
    workflow = tmp_path / ".github" / "workflows" / "agents-shipgate.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        "name: Agents Shipgate\n"
        "jobs:\n"
        "  verify:\n"
        "    steps:\n"
        "      - run: agents-shipgate verify --workspace . --config shipgate.yaml\n",
        encoding="utf-8",
    )
    diff_text = """diff --git a/.github/workflows/agents-shipgate.yml b/.github/workflows/agents-shipgate.yml
index 1111111..2222222 100644
--- a/.github/workflows/agents-shipgate.yml
+++ b/.github/workflows/agents-shipgate.yml
@@ -5 +5 @@
-      - run: agents-shipgate verify --workspace . --config shipgate.yaml
+      - run: echo "agents-shipgate gate disabled for now"
"""

    result = evaluate_codex_boundary_result(workspace=tmp_path, diff_text=diff_text)

    assert result.decision == "block"
    assert [item.id for item in result.violated_rules] == ["CODEX-CI-GATE-REMOVED"]


def test_codex_shipgate_workflow_accepts_repo_local_action_with_policy_input(
    tmp_path: Path,
) -> None:
    (tmp_path / "action.yml").write_text(
        "name: Agents Shipgate\n"
        "runs:\n"
        "  using: composite\n"
        "  steps:\n"
        "    - shell: bash\n"
        '      run: agents-shipgate "${args[@]}"\n',
        encoding="utf-8",
    )
    workflow = tmp_path / ".github" / "workflows" / "agents-shipgate.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        "name: Agents Shipgate\n"
        "jobs:\n"
        "  verify:\n"
        "    steps:\n"
        "      - uses: ./\n"
        "        with:\n"
        "          config: shipgate.yaml\n"
        "          ci_mode: advisory\n",
        encoding="utf-8",
    )
    diff_text = """diff --git a/.github/workflows/agents-shipgate.yml b/.github/workflows/agents-shipgate.yml
index 1111111..2222222 100644
--- a/.github/workflows/agents-shipgate.yml
+++ b/.github/workflows/agents-shipgate.yml
@@ -7,2 +7,3 @@
           config: shipgate.yaml
           ci_mode: advisory
+          fail_on_merge_verdicts: blocked
"""

    result = evaluate_codex_boundary_result(workspace=tmp_path, diff_text=diff_text)

    assert result.decision == "allow"
    assert result.violated_rules == []


def test_codex_shipgate_workflow_rejects_spoofed_local_action_name(
    tmp_path: Path,
) -> None:
    (tmp_path / "action.yml").write_text(
        "name: Agents Shipgate\n"
        "runs:\n"
        "  using: composite\n"
        "  steps:\n"
        "    - shell: bash\n"
        '      run: echo "agents-shipgate gate disabled"\n',
        encoding="utf-8",
    )
    workflow = tmp_path / ".github" / "workflows" / "agents-shipgate.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        "name: Agents Shipgate\njobs:\n  verify:\n    steps:\n      - uses: ./\n",
        encoding="utf-8",
    )
    diff_text = """diff --git a/.github/workflows/agents-shipgate.yml b/.github/workflows/agents-shipgate.yml
index 1111111..2222222 100644
--- a/.github/workflows/agents-shipgate.yml
+++ b/.github/workflows/agents-shipgate.yml
@@ -5 +5 @@
-      - run: agents-shipgate verify --workspace . --config shipgate.yaml
+      - uses: ./
"""

    result = evaluate_codex_boundary_result(workspace=tmp_path, diff_text=diff_text)

    assert result.decision == "block"
    assert [item.id for item in result.violated_rules] == ["CODEX-CI-GATE-REMOVED"]


def test_codex_shipgate_workflow_rejects_unrelated_repo_local_action(
    tmp_path: Path,
) -> None:
    (tmp_path / "action.yml").write_text(
        "name: Not Shipgate\nruns:\n  using: composite\n",
        encoding="utf-8",
    )
    workflow = tmp_path / ".github" / "workflows" / "agents-shipgate.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        "name: Agents Shipgate\njobs:\n  verify:\n    steps:\n      - uses: ./\n",
        encoding="utf-8",
    )
    diff_text = """diff --git a/.github/workflows/agents-shipgate.yml b/.github/workflows/agents-shipgate.yml
index 1111111..2222222 100644
--- a/.github/workflows/agents-shipgate.yml
+++ b/.github/workflows/agents-shipgate.yml
@@ -5 +5 @@
-      - run: agents-shipgate verify --workspace . --config shipgate.yaml
+      - uses: ./
"""

    result = evaluate_codex_boundary_result(workspace=tmp_path, diff_text=diff_text)

    assert result.decision == "block"
    assert [item.id for item in result.violated_rules] == ["CODEX-CI-GATE-REMOVED"]


def test_codex_audit_id_reflects_evaluated_content(tmp_path: Path) -> None:
    diff_text = """diff --git a/.codex/config.toml b/.codex/config.toml
index 1111111..2222222 100644
--- a/.codex/config.toml
+++ b/.codex/config.toml
@@ -2 +2 @@
-model = "old"
+model = "new"
"""
    safe = tmp_path / "safe"
    risky = tmp_path / "risky"
    for workspace, sandbox_mode in (
        (safe, "workspace-write"),
        (risky, "danger-full-access"),
    ):
        config = workspace / ".codex" / "config.toml"
        config.parent.mkdir(parents=True)
        config.write_text(
            f'sandbox_mode = "{sandbox_mode}"\nmodel = "old"\n',
            encoding="utf-8",
        )

    safe_result = evaluate_codex_boundary_result(workspace=safe, diff_text=diff_text)
    risky_result = evaluate_codex_boundary_result(workspace=risky, diff_text=diff_text)

    assert safe_result.decision == "allow"
    assert risky_result.decision == "allow"
    assert safe_result.audit_id != risky_result.audit_id


def test_codex_check_mismatched_workspace_content_fails_closed(
    tmp_path: Path,
) -> None:
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_text('model = "unexpected"\n', encoding="utf-8")
    diff_text = """diff --git a/.codex/config.toml b/.codex/config.toml
index 1111111..2222222 100644
--- a/.codex/config.toml
+++ b/.codex/config.toml
@@ -1 +1 @@
-model = "old"
+model = "new"
"""

    result = evaluate_codex_boundary_result(workspace=tmp_path, diff_text=diff_text)

    assert result.decision == "require_review"
    assert result.diagnostics[0].code == "content_source"
    assert "diff_workspace_mismatch" in result.diagnostics[0].message
    assert [item.id for item in result.violated_rules] == ["CODEX-CONFIG-PARSE-FAILED"]


def test_codex_check_accepts_already_applied_insertion_diff(
    tmp_path: Path,
) -> None:
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_text(
        'model = "gpt-5"\n[sandbox_workspace_write]\nnetwork_access = true\n',
        encoding="utf-8",
    )
    diff_text = """diff --git a/.codex/config.toml b/.codex/config.toml
index 1111111..2222222 100644
--- a/.codex/config.toml
+++ b/.codex/config.toml
@@ -1 +1,3 @@
 model = "gpt-5"
+[sandbox_workspace_write]
+network_access = true
"""

    result = evaluate_codex_boundary_result(workspace=tmp_path, diff_text=diff_text)

    assert result.decision == "require_review"
    assert "workspace_already_contains_diff_head" in result.diagnostics[0].message
    assert [item.id for item in result.violated_rules] == ["CODEX-NETWORK-EXPANDED"]


def test_codex_boundary_result_never_contradicts_release_decision(tmp_path: Path) -> None:
    result = evaluate_codex_boundary_result(
        workspace=tmp_path,
        diff_text=(CORPUS / "docs_only.diff").read_text(encoding="utf-8"),
        release_decision={"decision": "blocked", "reason": "release blocked"},
    )

    assert result.decision == "block"
    assert result.control.state == "human_review_required"
    assert result.control.next_action.kind == "stop"


def test_frozen_v2_shape_holds_on_every_serialization_path(tmp_path: Path) -> None:
    """The freeze is a property of the model, not of one emit site.

    ``CodexBoundaryResultV2`` shares ``control`` with the current union, so a
    helper applied at a single serializer would leave ``model_dump_json()``
    exposing the new state and the ``permissions`` object its published schema
    forbids.
    """

    target = tmp_path / ".codex"
    target.mkdir()
    (target / "config.toml").write_text('model = "safe"\n', encoding="utf-8")
    diff = (
        "diff --git a/.codex/config.toml b/.codex/config.toml\n"
        "--- a/.codex/config.toml\n+++ b/.codex/config.toml\n"
        "@@ -1,1 +1,2 @@\n model = \"safe\"\n+sandbox_mode = \"danger-full-access\"\n"
    )
    result = build_codex_agent_result(
        agent="codex",
        workspace=tmp_path,
        diff_text=diff,
        config=Path("shipgate.yaml"),
        policy=None,
        verification_replayable=True,
    )
    # The evaluated, bound subject really is publishable on the current format.
    assert result.control.state == "review_publishable"

    for payload in (
        json.loads(result.model_dump_json()),
        result.model_dump(mode="json"),
        agent_result_json_payload(result),
    ):
        _validate(payload)
        control = payload["control"]
        assert control["state"] == "human_review_required"
        assert control["must_stop"] is True
        assert control["allowed_next_commands"] == []
        assert "permissions" not in control
