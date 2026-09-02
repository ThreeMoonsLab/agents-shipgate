from __future__ import annotations

import difflib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.cli.preflight import _read_plan
from agents_shipgate.cli.scan import run_scan
from agents_shipgate.core import preflight as preflight_module
from agents_shipgate.core import trust_roots as trust_roots_module
from agents_shipgate.core.boundary_registry import BOUNDARY_ADAPTERS
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.core.host_grants import build_host_grants_baseline, host_audit_inventory
from agents_shipgate.core.preflight import (
    build_preflight_result,
    build_trust_root_graph,
    classify_protected_touches,
    forbidden_file_edits,
    required_evidence_for_capability_request,
)
from agents_shipgate.schemas.preflight import (
    CapabilityRequestControls,
    CapabilityRequestV1,
    PreflightResultV1,
    PreflightResultV2,
    PreflightResultV4,
)

runner = CliRunner()


def _assert_verify_command(command: str, workspace: Path, config: str) -> None:
    """The emitted verify command must target the preflight's own request."""

    assert command.startswith("agents-shipgate verify ")
    assert f"--workspace {shlex.quote(str(workspace))}" in command
    assert command.endswith("--ci-mode advisory --json")
    rendered = command.split("--config ", 1)[1].split(" ", 1)[0]
    assert Path(rendered).name == Path(config).name
    if not Path(rendered).is_absolute():
        assert rendered == config


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: preflight-test
agent:
  name: support-agent
  declared_purpose:
    - answer support questions
environment:
  target: local
tool_sources:
  - id: tools
    type: mcp
    path: tools.json
""",
        encoding="utf-8",
    )
    (root / "tools.json").write_text('{"tools": []}\n', encoding="utf-8")
    (root / "AGENTS.md").write_text("Run Shipgate.\n", encoding="utf-8")
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "agents-shipgate.yml").write_text(
        "name: Agents Shipgate\n",
        encoding="utf-8",
    )
    (root / ".codex").mkdir()
    (root / ".codex" / "config.toml").write_text("[profiles.default]\n", encoding="utf-8")
    return root


def _write(root: Path, path: str, text: str = "x\n") -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _manifest_diff(old: str, new: str, path: str = "shipgate.yaml") -> str:
    return f"diff --git a/{path} b/{path}\n" + "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )


def test_preflight_routes_protected_surface_touches_to_human(tmp_path: Path) -> None:
    root = _workspace(tmp_path)

    result = build_preflight_result(
        workspace=root,
        changed_files=[
            "shipgate.yaml",
            ".github/workflows/agents-shipgate.yml",
            ".codex/config.toml",
            "src/agent.py",
        ],
    )

    assert result.requires_human_review is True
    assert result.first_next_action.actor == "human"
    assert result.control.state == "human_review_required"
    assert result.control.completion_allowed is False
    assert result.control.must_stop is True
    assert result.control.next_action.kind == "stop"
    by_path = {touch.path: touch for touch in result.protected_surface_touches}
    assert by_path["shipgate.yaml"].kind == "manifest"
    assert by_path[".github/workflows/agents-shipgate.yml"].kind == "ci_gate"
    assert by_path[".codex/config.toml"].kind == "codex_config"
    assert "**/shipgate.yaml" not in forbidden_file_edits()
    assert any("AGENTS.md" in pattern for pattern in result.forbidden_file_edits)
    assert any(".codex/config.toml" in pattern for pattern in result.forbidden_file_edits)


def test_preflight_routes_a_planned_symlink_path_to_human(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    alias = root / "edit-me"
    alias.symlink_to(root / "AGENTS.md")

    result = build_preflight_result(workspace=root, changed_files=[alias.name])

    touch = next(item for item in result.protected_surface_touches if item.path == alias.name)
    assert touch.kind == "path_identity"
    assert touch.scope_type == "whole_file"
    assert result.control.state == "human_review_required"


def test_preflight_routes_a_planned_hardlink_path_to_human(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    alias = root / "edit-me"
    alias.hardlink_to(root / "AGENTS.md")

    result = build_preflight_result(workspace=root, changed_files=[alias.name])

    touch = next(item for item in result.protected_surface_touches if item.path == alias.name)
    assert touch.kind == "path_identity"
    assert touch.scope_type == "whole_file"
    assert result.control.state == "human_review_required"


def test_preflight_treats_stored_case_variants_as_host_trust_roots(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    (root / "AGENTS.md").rename(root / "agents.md")

    result = build_preflight_result(workspace=root, changed_files=["agents.md"])

    touch = next(
        item for item in result.protected_surface_touches if item.path == "agents.md"
    )
    assert touch.kind == "agent_instructions"
    assert result.control.state == "human_review_required"


def test_preflight_nested_verify_command_targets_the_nested_manifest(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    nested = root / "services" / "api"
    nested.mkdir(parents=True)
    (nested / "shipgate.yaml").write_text(
        (root / "shipgate.yaml")
        .read_text(encoding="utf-8")
        .replace("name: preflight-test", "name: nested-preflight-test", 1),
        encoding="utf-8",
    )
    (nested / "tools.json").write_text('{"tools": []}\n', encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)

    result = build_preflight_result(
        workspace=nested,
        config=Path("shipgate.yaml"),
        changed_files=["README.md"],
    )

    command = result.control.allowed_next_commands[0]
    argv = shlex.split(command)
    assert Path(argv[argv.index("--workspace") + 1]).resolve() == nested.resolve()
    assert argv[argv.index("--config") + 1] == "services/api/shipgate.yaml"
    assert (root / argv[argv.index("--config") + 1]).resolve() == (
        nested / "shipgate.yaml"
    ).resolve()


def test_preflight_rejects_a_symlinked_config_instead_of_authorizing_its_target(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    target = root / "new-gate.yml"
    (root / "shipgate.yaml").rename(target)
    link = root / "gate.yml"
    link.symlink_to(target.name)

    with pytest.raises(
        ConfigError,
        match=r"--config must not contain symlink components: gate\.yml",
    ):
        build_preflight_result(
            workspace=root,
            config=Path("gate.yml"),
            changed_files=["gate.yml"],
        )


def test_preflight_accepts_absolute_config_under_external_workspace_alias(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    alias = tmp_path / "repo-alias"
    alias.symlink_to(root, target_is_directory=True)

    result = build_preflight_result(
        workspace=alias,
        config=alias / "shipgate.yaml",
        changed_files=["shipgate.yaml"],
    )

    touch = next(
        item for item in result.protected_surface_touches
        if item.path == "shipgate.yaml"
    )
    assert touch.kind == "manifest"
    assert result.control.state == "human_review_required"


def test_preflight_rejects_a_filesystem_resolved_config_alias(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _workspace(tmp_path)
    actual = root / "new-gate.yml"
    (root / "shipgate.yaml").rename(actual)
    alias = root / "NEW-GATE.yml"
    real_lstat = Path.lstat

    def aliased_lstat(path: Path, *args, **kwargs):
        if path == alias:
            return real_lstat(actual, *args, **kwargs)
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", aliased_lstat)

    with pytest.raises(
        ConfigError,
        match=(
            r"--config must use the exact filesystem spelling: "
            r"NEW-GATE\.yml resolves to new-gate\.yml"
        ),
    ):
        build_preflight_result(
            workspace=root,
            config=Path(alias.name),
            changed_files=[actual.name],
        )


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="exercises configured-manifest aliases on macOS filesystems",
)
def test_preflight_matches_real_case_drift_to_configured_manifest(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    indexed = root / "Gate.gate"
    (root / "shipgate.yaml").rename(indexed)
    configured = root / "gate.gate"
    indexed.rename(configured)
    stored_names = {entry.name for entry in root.iterdir()}
    if configured.name not in stored_names or indexed.name in stored_names:
        pytest.skip("filesystem did not retain the case-only rename spelling")
    try:
        if not os.path.samestat(indexed.lstat(), configured.lstat()):
            pytest.skip("filesystem does not alias case variants")
    except OSError:
        pytest.skip("filesystem does not alias case variants")

    result = build_preflight_result(
        workspace=root,
        config=Path(configured.name),
        changed_files=[indexed.name],
    )

    touch = next(
        item for item in result.protected_surface_touches if item.path == configured.name
    )
    assert touch.kind == "manifest"
    assert result.control.state == "human_review_required"


def test_preflight_classifies_both_sides_of_a_custom_manifest_rename(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    (root / "shipgate.yaml").rename(root / "shipgate-self.yaml")
    diff = (
        "diff --git a/shipgate-self.yaml b/renamed-gate.yml\n"
        "similarity index 100%\n"
        "rename from shipgate-self.yaml\n"
        "rename to renamed-gate.yml\n"
    )

    result = build_preflight_result(
        workspace=root,
        config=Path("shipgate-self.yaml"),
        diff_text=diff,
    )

    assert result.changed_files == ["renamed-gate.yml", "shipgate-self.yaml"]
    assert [
        (touch.path, touch.kind) for touch in result.protected_surface_touches
    ] == [("shipgate-self.yaml", "manifest")]
    assert result.requires_human_review is True
    assert result.control.state == "human_review_required"


def test_preflight_allows_exact_append_only_builtin_source_proposal(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    plugin = root / "plugins" / "reviewer"
    _write(plugin, ".codex-plugin/plugin.json", '{"name": "reviewer"}\n')
    old = (root / "shipgate.yaml").read_text(encoding="utf-8")
    new = old + (
        "  - id: reviewer-plugin\n"
        "    type: codex_plugin\n"
        "    path: plugins/reviewer\n"
        "    mode: package\n"
    )

    result = build_preflight_result(
        workspace=root,
        diff_text=_manifest_diff(old, new),
    )

    assert result.changed_files == ["shipgate.yaml"]
    assert result.requires_human_review is False
    assert result.protected_surface_touches[0].requires_human_review is False
    assert result.control.state == "agent_action_required"
    assert result.control.must_stop is False
    assert result.control.next_action.kind == "verify"
    # The command names the request's own target, so a run against another
    # checkout or a non-default manifest does not hand the reader a command
    # pointing at a different gate. The config is rendered the way `verify`
    # resolves it — relative to the repository root, absolute when there is no
    # repository — so its exact spelling depends on the fixture.
    _assert_verify_command(result.control.allowed_next_commands[0], root, "shipgate.yaml")
    signal = next(item for item in result.signals if item.kind == "protected_surface_touch")
    assert signal.actor == "coding_agent"
    assert "not approved" in signal.reason


@pytest.mark.parametrize(
    ("manifest_relative_source_exists", "proposal_safe"),
    [(False, False), (True, True)],
)
def test_preflight_resolves_proposed_sources_from_custom_manifest_directory(
    tmp_path: Path,
    manifest_relative_source_exists: bool,
    proposal_safe: bool,
) -> None:
    root = _workspace(tmp_path)
    nested = root / "config"
    nested.mkdir()
    manifest = nested / "gate.yml"
    old = (root / "shipgate.yaml").read_text(encoding="utf-8")
    manifest.write_text(old, encoding="utf-8")
    (nested / "tools.json").write_text('{"tools": []}\n', encoding="utf-8")
    # A repository-root decoy must not satisfy `config/gate.yml`'s
    # manifest-relative `decoy.json` declaration.
    (root / "decoy.json").write_text('{"tools": []}\n', encoding="utf-8")
    if manifest_relative_source_exists:
        (nested / "decoy.json").write_text('{"tools": []}\n', encoding="utf-8")
    new = old + (
        "  - id: decoy\n"
        "    type: mcp\n"
        "    path: decoy.json\n"
    )

    result = build_preflight_result(
        workspace=root,
        config=Path("config/gate.yml"),
        diff_text=_manifest_diff(old, new, "config/gate.yml"),
    )

    assert result.changed_files == ["config/gate.yml"]
    assert result.protected_surface_touches[0].requires_human_review is (
        not proposal_safe
    )
    assert result.requires_human_review is (not proposal_safe)
    assert result.control.state == (
        "agent_action_required" if proposal_safe else "human_review_required"
    )


@pytest.mark.parametrize("safe_block_first", [True, False])
def test_preflight_rejects_duplicate_manifest_blocks_when_one_is_unsafe(
    tmp_path: Path,
    safe_block_first: bool,
) -> None:
    root = _workspace(tmp_path)
    plugin = root / "plugins" / "reviewer"
    _write(plugin, ".codex-plugin/plugin.json", '{"name": "reviewer"}\n')
    old = (root / "shipgate.yaml").read_text(encoding="utf-8")
    safe = old + (
        "  - id: reviewer-plugin\n"
        "    type: codex_plugin\n"
        "    path: plugins/reviewer\n"
        "    mode: package\n"
    )
    unsafe = old.replace(
        "    path: tools.json\n",
        "    path: tools.json\n    trust: internal\n",
    )
    safe_block = _manifest_diff(old, safe)
    unsafe_block = _manifest_diff(old, unsafe)
    composite = (
        safe_block + unsafe_block if safe_block_first else unsafe_block + safe_block
    )

    result = build_preflight_result(workspace=root, diff_text=composite)

    assert result.requires_human_review is True
    assert result.protected_surface_touches[0].requires_human_review is True
    assert result.control.state == "human_review_required"
    assert result.control.must_stop is True


@pytest.mark.parametrize(
    "addition",
    [
        (
            "  - id: reviewer-plugin\n"
            "    type: codex_plugin\n"
            "    path: plugins/reviewer\n"
            "    mode: package\n"
            "    trust: internal\n"
        ),
        (
            "  - id: reviewer-plugin\n"
            "    type: codex_plugin\n"
            "    path: plugins/reviewer\n"
            "    mode: package\n"
            "    optional: true\n"
        ),
        ("  - id: custom-source\n    type: vendor_custom\n    path: plugins/reviewer\n"),
        (
            "  - id: reviewer-plugin\n"
            "    type: codex_plugin\n"
            "    path: plugins/reviewer\n"
        ),
        "  - id: root-config\n    type: codex_config\n    path: .\n",
        "  - id: missing\n    type: mcp\n    path: missing-tools.json\n",
        "  - id: directory\n    type: mcp\n    path: plugins/reviewer\n",
        (
            "  - id: marketplace\n"
            "    type: codex_plugin\n"
            "    path: plugins/reviewer\n"
            "    mode: marketplace\n"
        ),
    ],
)
def test_preflight_rejects_unsafe_source_proposal(
    tmp_path: Path,
    addition: str,
) -> None:
    root = _workspace(tmp_path)
    plugin = root / "plugins" / "reviewer"
    _write(plugin, ".codex-plugin/plugin.json", '{"name": "reviewer"}\n')
    old = (root / "shipgate.yaml").read_text(encoding="utf-8")

    result = build_preflight_result(
        workspace=root,
        diff_text=_manifest_diff(old, old + addition),
    )

    assert result.requires_human_review is True
    assert result.protected_surface_touches[0].requires_human_review is True
    assert result.control.state == "human_review_required"
    assert result.control.must_stop is True


def test_preflight_rejects_source_addition_mixed_with_other_manifest_change(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    plugin = root / "plugins" / "reviewer"
    _write(plugin, ".codex-plugin/plugin.json", '{"name": "reviewer"}\n')
    old = (root / "shipgate.yaml").read_text(encoding="utf-8")
    new = old.replace("name: preflight-test", "name: renamed") + (
        "  - id: reviewer-plugin\n"
        "    type: codex_plugin\n"
        "    path: plugins/reviewer\n"
        "    mode: package\n"
    )

    result = build_preflight_result(
        workspace=root,
        diff_text=_manifest_diff(old, new),
    )

    assert result.requires_human_review is True
    assert result.control.state == "human_review_required"


def _protected_signal(result, path: str):
    return next(
        item
        for item in result.signals
        if item.kind == "protected_surface_touch" and item.path == path
    )


def test_preflight_human_route_rules_out_conversational_approval(
    tmp_path: Path,
) -> None:
    """The stop must not read as "ask the operator to confirm".

    Conversational acknowledgement never changes control state, so an agent
    that asks for it either stalls or learns to proceed past the gate once the
    operator says yes.
    """

    root = _workspace(tmp_path)

    result = build_preflight_result(
        workspace=root,
        changed_files=["AGENTS.md", ".github/workflows/agents-shipgate.yml"],
    )

    assert result.control.state == "human_review_required"
    for path in ("AGENTS.md", ".github/workflows/agents-shipgate.yml"):
        recommendation = _protected_signal(result, path).recommendation
        assert "pull request" in recommendation
        assert "does not clear this signal" in recommendation
        assert "do not ask the operator" in recommendation


def test_preflight_rejects_source_addition_mixed_with_comment_claim(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    plugin = root / "plugins" / "reviewer"
    _write(plugin, ".codex-plugin/plugin.json", '{"name": "reviewer"}\n')
    old = (root / "shipgate.yaml").read_text(encoding="utf-8")
    new = old.replace('version: "0.1"', '# human-approved\nversion: "0.1"') + (
        "  - id: reviewer-plugin\n"
        "    type: codex_plugin\n"
        "    path: plugins/reviewer\n"
        "    mode: package\n"
    )

    result = build_preflight_result(
        workspace=root,
        diff_text=_manifest_diff(old, new),
    )

    assert result.requires_human_review is True
    assert result.control.state == "human_review_required"


def test_preflight_rejects_plugin_source_with_symlinked_manifest(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    plugin_manifest = root / "plugins" / "reviewer" / ".codex-plugin" / "plugin.json"
    plugin_manifest.parent.mkdir(parents=True)
    outside = tmp_path / "outside-plugin.json"
    outside.write_text('{"name": "reviewer"}\n', encoding="utf-8")
    plugin_manifest.symlink_to(outside)
    old = (root / "shipgate.yaml").read_text(encoding="utf-8")
    new = old + (
        "  - id: reviewer-plugin\n"
        "    type: codex_plugin\n"
        "    path: plugins/reviewer\n"
        "    mode: package\n"
    )

    result = build_preflight_result(
        workspace=root,
        diff_text=_manifest_diff(old, new),
    )

    assert result.requires_human_review is True
    assert result.control.state == "human_review_required"


@pytest.mark.parametrize(
    "path,expected_kind,expected_scope_type",
    [
        ("shipgate.yaml", "manifest", "key_level"),
        (".github/workflows/agents-shipgate.yml", "ci_gate", "whole_file"),
        ("AGENTS.md", "agent_instructions", "whole_file"),
        ("CLAUDE.md", "agent_instructions", "whole_file"),
        (".cursor/rules/agents-shipgate.mdc", "agent_instructions", "whole_file"),
        ("policies/refund.yaml", "policy", "whole_file"),
        (".agents-shipgate/baseline.json", "shipgate_state", "key_level"),
        (".agents-shipgate/waivers.json", "shipgate_state", "key_level"),
        (".codex/config.toml", "codex_config", "whole_file"),
        (".codex/hooks/preflight.sh", "codex_hooks", "whole_file"),
        (".cursor/cli.json", "host_boundary", "whole_file"),
        ("AGENTS.override.md", "agent_instructions", "whole_file"),
        (".github/workflows/deploy.yml", "host_boundary", "whole_file"),
        (".codex-plugin/plugin.json", "codex_plugin", "capability_surface"),
        ("servers/refund/.mcp.json", "tool_surface_decl", "capability_surface"),
        ("plugins/refund/.app.json", "tool_surface_decl", "capability_surface"),
        ("skills/refund/SKILL.md", "tool_surface_decl", "capability_surface"),
    ],
)
def test_preflight_protected_surface_coverage(
    tmp_path: Path,
    path: str,
    expected_kind: str,
    expected_scope_type: str,
) -> None:
    root = _workspace(tmp_path)

    result = build_preflight_result(workspace=root, changed_files=[path])

    assert result.requires_human_review is True
    assert result.protected_surface_touches[0].path == path
    assert result.protected_surface_touches[0].kind == expected_kind
    assert result.protected_surface_touches[0].scope_type == expected_scope_type


def test_every_registered_repository_boundary_path_is_preflight_protected() -> None:
    candidates: list[str] = []
    for adapter in BOUNDARY_ADAPTERS:
        candidates.extend(adapter.exact_paths)
        candidates.extend(
            pattern.replace("**", "nested").replace("*", "item")
            for pattern in adapter.globs
        )

    touches = classify_protected_touches(candidates)

    assert {touch.path for touch in touches} == set(candidates)


def test_capability_request_review_requires_evidence_for_financial_write() -> None:
    request = CapabilityRequestV1(
        tool_name="refund_customer",
        provider="stripe",
        operation="refund_customer",
        effect="financial_write",
        risk_tags=["financial_action"],
    )

    evidence = required_evidence_for_capability_request(request)
    missing = {item.id for item in evidence if not item.satisfied}

    assert {"approval_policy", "idempotency", "auth_scopes", "owner"} <= missing
    assert any(item.severity == "critical" for item in evidence)


def test_capability_request_required_evidence_sorts_by_severity() -> None:
    request = CapabilityRequestV1(
        tool_name="deploy_service",
        effect="production_operation",
        risk_tags=["production_operation"],
    )

    evidence = required_evidence_for_capability_request(request)

    severities = [item.severity for item in evidence]
    assert severities[0] == "critical"
    assert severities[-1] == "medium"


def test_read_only_capability_request_has_no_required_evidence() -> None:
    request = CapabilityRequestV1(tool_name="lookup_case", effect="read")

    assert required_evidence_for_capability_request(request) == []


@pytest.mark.parametrize(
    "request_key,request_payload",
    [
        (
            "capability_requests",
            [{"tool_name": " \t ", "effect": "read"}],
        ),
        (
            "host_permission_requests",
            [
                {
                    "host": "\n ",
                    "surface": "permissions.allow",
                    "operation": "add",
                    "subject": "Bash(git status)",
                }
            ],
        ),
        (
            "host_permission_requests",
            [
                {
                    "host": "claude-code",
                    "surface": " \t",
                    "operation": "add",
                    "subject": "Bash(git status)",
                }
            ],
        ),
        (
            "host_permission_requests",
            [
                {
                    "host": "claude-code",
                    "surface": "permissions.allow",
                    "operation": " ",
                    "subject": "Bash(git status)",
                }
            ],
        ),
        (
            "host_permission_requests",
            [
                {
                    "host": "claude-code",
                    "surface": "permissions.allow",
                    "operation": "add",
                    "subject": "\t",
                }
            ],
        ),
    ],
)
def test_preflight_plan_rejects_blank_capability_and_host_request_fields(
    tmp_path: Path,
    request_key: str,
    request_payload: list[dict[str, object]],
) -> None:
    root = _workspace(tmp_path)

    with pytest.raises(ConfigError, match="must not be blank"):
        build_preflight_result(
            workspace=root,
            plan={
                "schema_version": "preflight_plan_v1",
                request_key: request_payload,
            },
        )


def test_capability_request_normalizes_and_deduplicates_risk_tag_case() -> None:
    request = CapabilityRequestV1(
        tool_name="lookup_customer",
        effect="read",
        risk_tags=[
            " Financial_Action ",
            "financial_action",
            "PRODUCTION_OPERATION",
        ],
    )

    assert request.risk_tags == ["financial_action", "production_operation"]
    assert required_evidence_for_capability_request(request)


def test_external_communication_requires_explicit_confirmation_evidence() -> None:
    missing = CapabilityRequestV1(
        tool_name="send_customer_email",
        effect="external_communication",
    )
    confirmed = CapabilityRequestV1(
        tool_name="send_customer_email",
        effect="external_communication",
        controls=CapabilityRequestControls(confirmation_required=True),
    )

    missing_confirmation = next(
        item
        for item in required_evidence_for_capability_request(missing)
        if item.id == "confirmation"
    )
    confirmed_confirmation = next(
        item
        for item in required_evidence_for_capability_request(confirmed)
        if item.id == "confirmation"
    )
    assert missing_confirmation.satisfied is False
    assert missing_confirmation.field == "controls.confirmation_required"
    assert confirmed_confirmation.satisfied is True


def test_policy_and_trust_root_hashes_are_deterministic(tmp_path: Path) -> None:
    root = _workspace(tmp_path)

    first = build_preflight_result(workspace=root)
    second = build_preflight_result(workspace=root)

    assert first.policy_snapshot_hash == second.policy_snapshot_hash
    assert first.trust_root_graph_hash == second.trust_root_graph_hash
    assert build_trust_root_graph(root).graph_hash == first.trust_root_graph_hash


def test_trust_root_graph_entry_budget_is_aggregate_across_inventory_and_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _workspace(tmp_path)
    budget = trust_roots_module.IdentityReadBudget(
        max_entries=10_000,
        max_total_bytes=1024 * 1024,
    )
    reader = trust_roots_module.IdentityBoundReadSession(root, budget=budget)
    _paths, inventory_entries = preflight_module._walk_trust_root_files(
        root,
        reader=reader,
        max_entries=10_000,
    )
    assert inventory_entries > 0
    monkeypatch.setattr(
        preflight_module,
        "_MAX_TRUST_ROOT_GRAPH_ENTRIES",
        inventory_entries,
    )

    with pytest.raises(ConfigError, match="aggregate static resource bound"):
        build_trust_root_graph(root)


def test_trust_root_graph_byte_budget_is_aggregate_across_unique_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _workspace(tmp_path)
    protected = [
        root / "shipgate.yaml",
        root / "AGENTS.md",
        root / ".codex" / "config.toml",
        root / ".github" / "workflows" / "agents-shipgate.yml",
    ]
    individual_sizes = [path.stat().st_size for path in protected]
    aggregate_limit = max(individual_sizes)
    assert sum(individual_sizes) > aggregate_limit
    monkeypatch.setattr(
        preflight_module,
        "_MAX_TRUST_ROOT_GRAPH_BYTES",
        aggregate_limit,
    )

    with pytest.raises(ConfigError, match="aggregate static resource bound"):
        build_trust_root_graph(root)


def test_trust_root_graph_scans_each_protected_directory_constant_times(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _workspace(tmp_path)
    policy_directory = root / "policies"
    for index in range(50):
        _write(root, f"policies/policy-{index:02d}.yaml", f"index: {index}\n")

    real_scandir = os.scandir
    calls: dict[str, int] = {}

    def counting_scandir(path: os.PathLike[str] | str | int):
        if not isinstance(path, int):
            key = os.path.abspath(os.fspath(path))
            calls[key] = calls.get(key, 0) + 1
        return real_scandir(path)

    monkeypatch.setattr(trust_roots_module.os, "scandir", counting_scandir)

    graph = build_trust_root_graph(root)

    node = next(item for item in graph.nodes if item.pattern == "**/policies/**")
    assert len(node.file_hashes) == 50
    assert calls[os.path.abspath(os.fspath(policy_directory))] == 2


def test_bounded_trust_root_graph_keeps_hardlinks_fail_closed(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    original = root / "policies" / "primary.yaml"
    _write(root, "policies/primary.yaml", "approval: required\n")
    alias = root / "policies" / "alias.yaml"
    alias.hardlink_to(original)

    result = build_preflight_result(workspace=root)

    node = next(
        item for item in result.trust_root_graph.nodes
        if item.pattern == "**/policies/**"
    )
    assert node.file_hashes["policies/primary.yaml"] == "unavailable:path_identity"
    assert node.file_hashes["policies/alias.yaml"] == "unavailable:path_identity"
    assert {
        item.path
        for item in result.protected_surface_touches
        if item.kind == "path_identity"
    } >= {"policies/primary.yaml", "policies/alias.yaml"}
    assert result.control.state == "human_review_required"


def test_trust_root_graph_rejects_protected_addition_after_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _workspace(tmp_path)
    (root / "docs").mkdir()
    added = root / "docs" / "AGENTS.md"
    real_finish = trust_roots_module.IdentityBoundReadSession.finish
    injected = False

    def finish_after_addition(
        reader: trust_roots_module.IdentityBoundReadSession,
    ) -> None:
        nonlocal injected
        if reader.root == root and not injected:
            injected = True
            added.write_text("late protected instruction\n", encoding="utf-8")
        real_finish(reader)

    monkeypatch.setattr(
        trust_roots_module.IdentityBoundReadSession,
        "finish",
        finish_after_addition,
    )

    with pytest.raises(ConfigError, match="changed identity"):
        build_trust_root_graph(root)


def test_exact_custom_manifest_keeps_literal_glob_characters(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    manifest_name = "gate[production]*?.yml"
    (root / "shipgate.yaml").rename(root / manifest_name)

    result = build_preflight_result(
        workspace=root,
        config=Path(manifest_name),
        changed_files=[manifest_name],
    )

    node = next(
        item
        for item in result.trust_root_graph.nodes
        if item.kind == "manifest" and item.pattern == manifest_name
    )
    assert node.present_paths == [manifest_name]
    assert node.file_hashes[manifest_name].startswith("sha256:")
    touch = next(
        item for item in result.protected_surface_touches if item.path == manifest_name
    )
    assert touch.kind == "manifest"
    assert result.control.state == "human_review_required"


def test_preflight_rejects_a_hardlinked_config_manifest(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    alias = root / "custom-gate.yml"
    alias.hardlink_to(root / "shipgate.yaml")

    with pytest.raises(ConfigError, match="hardlinked manifest refused"):
        build_preflight_result(
            workspace=root,
            config=Path(alias.name),
            changed_files=[alias.name],
        )


def test_trust_root_globstar_fails_closed_on_a_symlink_ancestor(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    outside = tmp_path / "outside"
    (outside / "policies").mkdir(parents=True)
    secret = "OUTSIDE-POLICY-SECRET"
    (outside / "policies" / "review.yaml").write_text(secret, encoding="utf-8")
    (root / "vendor").symlink_to(outside, target_is_directory=True)

    result = build_preflight_result(workspace=root)

    node = next(
        item
        for item in result.trust_root_graph.nodes
        if item.pattern == "**/policies/**"
    )
    assert "vendor" in node.present_paths
    assert node.file_hashes["vendor"] == "unavailable:path_identity"
    assert secret not in result.model_dump_json()
    assert any(
        item.path == "vendor" and item.kind == "path_identity"
        for item in result.protected_surface_touches
    )
    assert result.control.state == "human_review_required"


@pytest.mark.parametrize(
    "pattern,path",
    [
        ("**/.agents-shipgate/**", ".agents-shipgate/baseline.json"),
        ("**/policies/**", "policies/refund.yaml"),
        ("**/prompts/**", "prompts/refund.md"),
        ("**/.claude/**", ".claude/settings.json"),
        ("**/.cursor/rules/**", ".cursor/rules/agents-shipgate.mdc"),
        ("**/.agents/skills/**", ".agents/skills/agents-shipgate/SKILL.md"),
        ("**/.codex/**", ".codex/config.toml"),
        ("**/.codex/hooks/**", ".codex/hooks/preflight.sh"),
        ("**/.codex-plugin/**", ".codex-plugin/plugin.json"),
    ],
)
def test_trust_root_graph_records_recursive_pattern_files(
    tmp_path: Path,
    pattern: str,
    path: str,
) -> None:
    root = _workspace(tmp_path)
    if not (root / path).exists():
        _write(root, path)

    graph = build_trust_root_graph(root)

    node = next(node for node in graph.nodes if node.pattern == pattern)
    assert path in node.present_paths
    assert node.file_hashes[path].startswith("sha256:")


def test_base_preflight_reports_trust_root_graph_drift(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    base = build_preflight_result(workspace=root)
    (root / "AGENTS.md").write_text("Run Shipgate before completion.\n", encoding="utf-8")

    head = build_preflight_result(workspace=root, base_preflight=base)

    assert head.trust_root_graph_diff is not None
    assert head.trust_root_graph_diff.changed is True
    assert head.policy_drift is not None


def test_base_preflight_reports_recursive_trust_root_graph_drift(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    _write(root, "policies/refund.yaml", "limit: 100\n")
    _write(root, ".codex/hooks/preflight.sh", "echo OK\n")
    base = build_preflight_result(workspace=root)

    _write(root, "policies/refund.yaml", "limit: 999999\n")
    _write(root, ".codex/hooks/preflight.sh", "echo HACKED\n")
    head = build_preflight_result(workspace=root, base_preflight=base)

    assert head.trust_root_graph_diff is not None
    assert head.trust_root_graph_diff.changed is True
    modified = set(head.trust_root_graph_diff.modified)
    changed_patterns = {node.pattern for node in head.trust_root_graph.nodes if node.id in modified}
    assert "**/policies/**" in changed_patterns
    assert "**/.codex/hooks/**" in changed_patterns


def test_base_preflight_accepts_legacy_v1_payload(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    base = build_preflight_result(workspace=root)
    base_payload = {
        field: value
        for field, value in base.model_dump(mode="json").items()
        if field in PreflightResultV1.model_fields
    }
    base_payload["preflight_schema_version"] = "0.1"
    legacy_base = PreflightResultV1.model_validate(base_payload)

    (root / "AGENTS.md").write_text("Run Shipgate before completion.\n", encoding="utf-8")
    head = build_preflight_result(workspace=root, base_preflight=legacy_base)

    assert head.preflight_schema_version == "0.4"
    assert head.trust_root_graph_diff is not None
    assert head.trust_root_graph_diff.changed is True


def test_preflight_plan_routes_multiple_capability_and_host_requests(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)

    result = build_preflight_result(
        workspace=root,
        plan={
            "schema_version": "preflight_plan_v1",
            "changed_files": ["docs/readme.md"],
            "capability_requests": [
                {"tool_name": "lookup_case", "effect": "read"},
                {
                    "tool_name": "refund_customer",
                    "provider": "stripe",
                    "effect": "financial_write",
                    "risk_tags": ["financial_action"],
                    "scopes": ["*"],
                },
            ],
            "host_permission_requests": [
                {
                    "host": "claude-code",
                    "surface": "permissions.allow",
                    "operation": "add",
                    "path": ".claude/settings.json",
                    "subject": "Bash(*)",
                    "requested_access": {"allow": ["Bash(*)"]},
                    "reason": "let the agent run any shell command",
                }
            ],
            "context": {"agent": "codex", "task": "add refund support"},
        },
    )

    assert result.preflight_schema_version == "0.4"
    assert result.requires_human_review is True
    assert result.requires_verify is True
    assert result.plan_summary["capability_request_count"] == 2
    assert result.plan_summary["host_permission_request_count"] == 1
    assert result.first_next_action.actor == "human"
    assert result.control.state == "human_review_required"
    assert {signal.kind for signal in result.signals} >= {
        "least_privilege",
        "missing_evidence",
        "verify_required",
    }


def test_cli_preflight_json_changed_files_and_diff(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    changed = tmp_path / "changed.txt"
    changed.write_text("shipgate.yaml\n", encoding="utf-8")
    diff = tmp_path / "change.diff"
    diff.write_text(
        "diff --git a/.codex/config.toml b/.codex/config.toml\n"
        "--- a/.codex/config.toml\n"
        "+++ b/.codex/config.toml\n"
        "@@ -1 +1 @@\n"
        "-[profiles.default]\n"
        "+[profiles.default]\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "preflight",
            "--workspace",
            str(root),
            "--changed-files",
            str(changed),
            "--diff",
            str(diff),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["preflight_schema_version"] == "0.4"
    assert payload["requires_human_review"] is True
    assert payload["requires_verify"] is True
    assert payload["control"]["state"] == "human_review_required"
    assert payload["control"]["must_stop"] is True
    assert {touch["path"] for touch in payload["protected_surface_touches"]} == {
        ".codex/config.toml",
        "shipgate.yaml",
    }
    assert any(signal["kind"] == "protected_surface_touch" for signal in payload["signals"])


def test_cli_preflight_uses_diff_semantics_for_safe_manifest_proposal(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    plugin = root / "plugins" / "reviewer"
    _write(plugin, ".codex-plugin/plugin.json", '{"name": "reviewer"}\n')
    old = (root / "shipgate.yaml").read_text(encoding="utf-8")
    new = old + (
        "  - id: reviewer-plugin\n"
        "    type: codex_plugin\n"
        "    path: plugins/reviewer\n"
        "    mode: package\n"
    )
    diff = tmp_path / "manifest.diff"
    diff.write_text(_manifest_diff(old, new), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "preflight",
            "--workspace",
            str(root),
            "--diff",
            str(diff),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["control"]["state"] == "agent_action_required"
    assert payload["control"]["must_stop"] is False
    assert payload["protected_surface_touches"] == [
        {
            "path": "shipgate.yaml",
            "kind": "manifest",
            "pattern": "**/shipgate.yaml",
            "scope_type": "key_level",
            "requires_human_review": False,
        }
    ]


def test_cli_preflight_capability_request(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {
                "tool_name": "refund_customer",
                "effect": "financial_write",
                "risk_tags": ["financial_action"],
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "preflight",
            "--workspace",
            str(root),
            "--capability-request",
            str(request),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["first_next_action"]["kind"] == "gather_evidence"
    assert any(
        item["id"].endswith(":approval_policy")
        for item in payload["required_evidence"]
        if not item["satisfied"]
    )


def test_cli_preflight_plan_stdin_routes_clean_docs_to_verify(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    plan = {
        "schema_version": "preflight_plan_v1",
        "changed_files": ["docs/readme.md"],
        "context": {"agent": "codex", "task": "update docs"},
    }

    result = runner.invoke(
        app,
        [
            "preflight",
            "--workspace",
            str(root),
            "--plan",
            "-",
            "--json",
        ],
        input=json.dumps(plan),
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["preflight_schema_version"] == "0.4"
    assert payload["requires_human_review"] is False
    assert payload["first_next_action"]["kind"] == "verify"
    _assert_verify_command(payload["allowed_next_commands"][0], root, "shipgate.yaml")
    assert payload["control"]["state"] == "agent_action_required"
    assert payload["control"]["completion_allowed"] is False
    assert payload["control"]["must_stop"] is False
    assert payload["control"]["next_action"]["kind"] == "verify"


def test_cli_preflight_plan_empty_stdin_is_empty_plan(tmp_path: Path) -> None:
    root = _workspace(tmp_path)

    result = runner.invoke(
        app,
        [
            "preflight",
            "--workspace",
            str(root),
            "--plan",
            "-",
            "--json",
        ],
        input="",
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["preflight_schema_version"] == "0.4"
    assert payload["changed_files"] == []
    assert payload["requires_human_review"] is False
    assert payload["requires_verify"] is False
    assert payload["first_next_action"]["kind"] == "continue"
    assert payload["control"]["state"] == "complete"
    assert payload["control"]["completion_allowed"] is True
    assert payload["control"]["must_stop"] is False


def test_base_preflight_accepts_frozen_v2_payload(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    current = build_preflight_result(workspace=root)
    payload = {
        field: value
        for field, value in current.model_dump(mode="json").items()
        if field in PreflightResultV2.model_fields
    }
    payload["preflight_schema_version"] = "0.2"
    legacy = PreflightResultV2.model_validate(payload)

    head = build_preflight_result(
        workspace=root,
        changed_files=["docs/readme.md"],
        base_preflight=legacy,
    )

    assert isinstance(head, PreflightResultV4)
    assert head.preflight_schema_version == "0.4"
    assert head.control.state == "agent_action_required"


def test_preflight_legacy_projection_cannot_contradict_control_in_model_or_schema(
    tmp_path: Path,
) -> None:
    payload = build_preflight_result(workspace=_workspace(tmp_path)).model_dump(mode="json")
    payload["first_next_action"] = {
        "actor": "coding_agent",
        "kind": "verify",
        "command": "agents-shipgate verify --json",
        "why": "Contradict complete control.",
    }
    with pytest.raises(ValidationError):
        PreflightResultV4.model_validate(payload)
    schema = json.loads(
        (Path(__file__).resolve().parent.parent / "docs/preflight-schema.v0.4.json").read_text(
            encoding="utf-8"
        )
    )
    assert list(Draft202012Validator(schema).iter_errors(payload))


def test_read_plan_tty_stdin_is_empty_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    class TtyStdin:
        def isatty(self) -> bool:
            return True

        def read(self) -> str:
            raise AssertionError("TTY plan stdin should not be read")

    monkeypatch.setattr(sys, "stdin", TtyStdin())

    plan = _read_plan(Path("-"))

    assert plan.changed_files == []
    assert plan.capability_requests == []
    assert plan.host_permission_requests == []


def test_cli_preflight_plan_file_rejects_legacy_flag_mix(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    plan = tmp_path / "plan.json"
    changed = tmp_path / "changed.txt"
    plan.write_text('{"schema_version": "preflight_plan_v1"}\n', encoding="utf-8")
    changed.write_text("shipgate.yaml\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "preflight",
            "--workspace",
            str(root),
            "--plan",
            str(plan),
            "--changed-files",
            str(changed),
            "--json",
        ],
    )

    assert result.exit_code == 2
    assert "--plan cannot be combined with --changed-files" in result.output


def test_cli_preflight_reports_host_grant_drift_when_baseline_present(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    baseline = build_host_grants_baseline(host_audit_inventory(root))
    baseline_path = root / ".agents-shipgate" / "host-grants.json"
    baseline_path.parent.mkdir(parents=True)
    baseline_path.write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n")
    _write(
        root,
        ".claude/settings.json",
        json.dumps({"permissions": {"allow": ["Bash(*)"]}}),
    )

    result = runner.invoke(
        app,
        [
            "preflight",
            "--workspace",
            str(root),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["host_grant_drift"]["has_drift"] is True
    assert payload["first_next_action"]["actor"] == "human"
    assert any(signal["kind"] == "host_grant_drift" for signal in payload["signals"])


def test_cli_preflight_routes_incomparable_legacy_host_baseline_to_human(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    baseline_path = root / ".agents-shipgate" / "host-grants.json"
    baseline_path.parent.mkdir(parents=True)
    baseline_path.write_text(
        json.dumps(
            {
                "host_grants_schema_version": "0.1",
                "inventory_sha256": "legacy",
                "inventory": {"mcp_servers": []},
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["preflight", "--workspace", str(root), "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["host_grant_drift"]["comparison_status"] == "incomparable"
    assert payload["host_grant_drift"]["has_drift"] is None
    assert payload["host_grant_drift"]["next_action"] is None
    assert payload["first_next_action"]["actor"] == "human"
    signal = next(
        item for item in payload["signals"] if item["kind"] == "host_grant_drift"
    )
    assert "could not be compared completely" in signal["reason"]
    assert signal["related_command"] is None
    assert "Review the existing baseline" in signal["recommendation"]
    assert "--save-baseline" not in json.dumps(payload)


def test_cli_preflight_default_corrupt_host_baseline_fails_closed(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    baseline_path = root / ".agents-shipgate" / "host-grants.json"
    baseline_path.parent.mkdir(parents=True)
    baseline_path.write_text("{", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "preflight",
            "--workspace",
            str(root),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["host_grant_drift"]["comparison_status"] == "incomparable"
    assert payload["requires_human_review"] is True
    assert payload["control"]["state"] == "human_review_required"
    assert any("requires review" in note for note in payload["notes"])
    assert any(signal["kind"] == "host_grant_drift" for signal in payload["signals"])
    assert "--save-baseline" not in json.dumps(payload)


@pytest.mark.parametrize("broken", [False, True])
def test_cli_preflight_default_symlinked_host_baseline_fails_closed(
    tmp_path: Path,
    broken: bool,
) -> None:
    root = _workspace(tmp_path)
    baseline_path = root / ".agents-shipgate" / "host-grants.json"
    baseline_path.parent.mkdir(parents=True)
    external = tmp_path / "external-baseline.json"
    if not broken:
        external.write_text(
            json.dumps(build_host_grants_baseline(host_audit_inventory(root))),
            encoding="utf-8",
        )
    baseline_path.symlink_to(external)

    result = runner.invoke(
        app,
        ["preflight", "--workspace", str(root), "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["host_grant_drift"]["comparison_status"] == "incomparable"
    assert payload["control"]["state"] == "human_review_required"
    assert payload["control"]["allowed_next_commands"] == []
    assert "--save-baseline" not in json.dumps(payload)


def test_cli_preflight_explicit_missing_or_corrupt_host_baseline_fails(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    missing = tmp_path / "missing-baseline.json"
    result = runner.invoke(
        app,
        [
            "preflight",
            "--workspace",
            str(root),
            "--host-baseline",
            str(missing),
            "--json",
        ],
    )
    assert result.exit_code == 2
    assert "No readable host-grants baseline" in result.output

    corrupt = tmp_path / "corrupt-baseline.json"
    corrupt.write_text("{", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "preflight",
            "--workspace",
            str(root),
            "--host-baseline",
            str(corrupt),
            "--json",
        ],
    )
    assert result.exit_code == 2
    assert "not valid JSON" in result.output


def test_high_risk_capability_without_evidence_does_not_pass(tmp_path: Path) -> None:
    report, _exit_code = run_scan(
        config_path=Path("samples/support_refund_agent/shipgate.yaml"),
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )

    assert report.release_decision is not None
    assert report.release_decision.decision in {"blocked", "insufficient_evidence"}
    active_check_ids = {finding.check_id for finding in report.findings if not finding.suppressed}
    assert {
        "SHIP-POLICY-APPROVAL-MISSING",
        "SHIP-SIDEFX-IDEMPOTENCY-MISSING",
    } & active_check_ids
