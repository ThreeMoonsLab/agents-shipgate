"""No terminal or publication authority from input Shipgate did not evaluate."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator
from pydantic import TypeAdapter, ValidationError

from agents_shipgate.cli.mcp import _agent_result_from_audit, build_mcp_audit
from agents_shipgate.cli.verify.orchestrator import _derive_verifier_control
from agents_shipgate.core.agent_control import AgentControlConsistencyError, derive_agent_control
from agents_shipgate.core.codex_boundary import evaluate_codex_boundary_result
from agents_shipgate.schemas.agent_control import AGENT_CONTROL_ADAPTER, PERMISSION_FIELDS
from agents_shipgate.schemas.current_control import CurrentControlProjection
from agents_shipgate.schemas.verifier import VerifierDiffStatus


@pytest.mark.parametrize("publication_allowed", [False, True])
def test_no_obligations_do_not_prove_an_evaluated_subject(publication_allowed: bool) -> None:
    with pytest.raises(AgentControlConsistencyError, match="evaluated subject"):
        derive_agent_control(reason="Nothing was read.", publication_allowed=publication_allowed)


@pytest.mark.parametrize("decision", ["allow", "warn", "require_review", "block"])
def test_unreadable_mcp_input_never_authorizes_any_action(tmp_path: Path, decision: str) -> None:
    diff = (
        "diff --git a/.mcp.json b/.mcp.json\n"
        "new file mode 100644\n--- /dev/null\n+++ b/.mcp.json\n"
        '@@ -0,0 +1 @@\n+{"mcpServers": {\n'
    )
    audit = build_mcp_audit(workspace=tmp_path, diff_text=diff)
    assert audit["input_complete"] is False
    # The input invariant must hold independently of the policy's aggregate decision.
    audit["decision"] = decision
    result = _agent_result_from_audit(audit)
    assert result.control.state == "human_review_required"
    assert not result.control.permissions.authorizes_anything


@pytest.mark.parametrize(
    "diff",
    [
        "",
        "diff --git a/README.md b/README.md\n"
        "--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-old\n+new\n",
    ],
)
def test_audit_without_a_recognized_subject_grants_no_authority(tmp_path: Path, diff: str) -> None:
    result = _agent_result_from_audit(build_mcp_audit(workspace=tmp_path, diff_text=diff))
    assert result.control.state == "human_review_required"
    assert not result.control.permissions.authorizes_anything


def test_audit_can_evaluate_an_empty_server_map(tmp_path: Path) -> None:
    diff = (
        "diff --git a/.mcp.json b/.mcp.json\n"
        "new file mode 100644\n--- /dev/null\n+++ b/.mcp.json\n"
        '@@ -0,0 +1 @@\n+{"mcpServers": {}}\n'
    )
    result = _agent_result_from_audit(build_mcp_audit(workspace=tmp_path, diff_text=diff))
    assert result.control.state == "complete"


@pytest.mark.parametrize(
    "path,body", [(".codex/config.toml", "[mcp_servers."), (".mcp.json", '{"mcpServers": {')]
)
def test_malformed_source_never_grants_terminal_authority(
    tmp_path: Path, path: str, body: str
) -> None:
    diff = (
        f"diff --git a/{path} b/{path}\nnew file mode 100644\n"
        f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1 @@\n+{body}\n"
    )
    result = _agent_result_from_audit(build_mcp_audit(workspace=tmp_path, diff_text=diff))
    assert not result.control.permissions.authorizes_anything


def test_missing_policy_denies_authority_on_a_readable_source(tmp_path: Path) -> None:
    diff = (
        "diff --git a/.mcp.json b/.mcp.json\nnew file mode 100644\n"
        '--- /dev/null\n+++ b/.mcp.json\n@@ -0,0 +1 @@\n+{"mcpServers": {}}\n'
    )
    audit = build_mcp_audit(workspace=tmp_path, diff_text=diff, policy=Path("missing.yaml"))
    assert audit["decision"] == "allow"
    assert not _agent_result_from_audit(audit).control.permissions.authorizes_anything


@pytest.mark.parametrize(
    "body", ["[]", "false", "- invalid_policy_root", "rules: {}", "rules: [false]"]
)
def test_invalid_policy_structure_is_not_complete_input(tmp_path: Path, body: str) -> None:
    (tmp_path / "policy.yaml").write_text(body)
    diff = (
        "diff --git a/.mcp.json b/.mcp.json\nnew file mode 100644\n"
        '--- /dev/null\n+++ b/.mcp.json\n@@ -0,0 +1 @@\n+{"mcpServers": {}}\n'
    )
    audit = build_mcp_audit(workspace=tmp_path, diff_text=diff, policy=Path("policy.yaml"))
    assert not audit["input_complete"]
    assert not _agent_result_from_audit(audit).control.permissions.authorizes_anything


@pytest.mark.parametrize(
    "body", ['{"mcpServers": "unreadable"}', '{"mcpServers": {"broken": false}}']
)
def test_invalid_server_mapping_is_not_complete_input(tmp_path: Path, body: str) -> None:
    diff = (
        "diff --git a/.mcp.json b/.mcp.json\nnew file mode 100644\n"
        f"--- /dev/null\n+++ b/.mcp.json\n@@ -0,0 +1 @@\n+{body}\n"
    )
    audit = build_mcp_audit(workspace=tmp_path, diff_text=diff)
    assert not audit["input_complete"]
    assert not _agent_result_from_audit(audit).control.permissions.authorizes_anything


def test_valid_empty_toml_is_an_evaluated_source(tmp_path: Path) -> None:
    diff = "diff --git a/.codex/config.toml b/.codex/config.toml\nnew file mode 100644\n"
    audit = build_mcp_audit(workspace=tmp_path, diff_text=diff)
    assert audit["subject_evaluated"]
    assert _agent_result_from_audit(audit).control.state == "complete"


@pytest.mark.parametrize(
    "body",
    [
        'plugins = "unreadable"',
        "[plugins]\nbroken = false",
        '[plugins.broken]\nmcp_servers = "unreadable"',
    ],
)
def test_invalid_plugin_container_is_not_complete_input(tmp_path: Path, body: str) -> None:
    lines = body.splitlines()
    diff = (
        "diff --git a/.codex/config.toml b/.codex/config.toml\nnew file mode 100644\n"
        f"--- /dev/null\n+++ b/.codex/config.toml\n@@ -0,0 +1,{len(lines)} @@\n"
        + "".join(f"+{line}\n" for line in lines)
    )
    audit = build_mcp_audit(workspace=tmp_path, diff_text=diff)
    assert not audit["input_complete"]
    assert not _agent_result_from_audit(audit).control.permissions.authorizes_anything


@pytest.mark.parametrize("missing", [None, *PERMISSION_FIELDS])
@pytest.mark.parametrize(
    "state",
    [
        "complete",
        "agent_action_required",
        "review_publishable",
        "human_review_required",
        "unavailable",
    ],
)
def test_current_pointer_projection_rejects_partial_permissions(state, missing):
    adapter = TypeAdapter(CurrentControlProjection)
    payload = {
        "state": state,
        "reason": "Evaluated or recovering.",
        "completion_allowed": state == "complete",
        "must_stop": state in {"human_review_required", "unavailable"},
        "permissions": {
            field: state == "complete"
            or (
                state in {"agent_action_required", "review_publishable"}
                and field not in {"merge", "report_complete"}
            )
            for field in PERMISSION_FIELDS
        },
    }
    assert adapter.validate_python(payload)
    if missing is None:
        payload["permissions"] = {}
    else:
        del payload["permissions"][missing]
    assert not Draft202012Validator(adapter.json_schema()).is_valid(payload)
    with pytest.raises(ValidationError, match="permission"):
        adapter.validate_python(payload)


@pytest.mark.parametrize(
    "execution,decision,complete,skip_evaluated",
    [
        ("skipped", None, True, False),
        ("succeeded", "passed", False, False),
        ("failed", "passed", True, False),
    ],
)
def test_verifier_terminal_routes_require_evaluated_input(
    execution, decision, complete, skip_evaluated
):
    with pytest.raises(AgentControlConsistencyError, match="evaluated subject"):
        _derive_verifier_control(
            execution=execution,
            merge_verdict="mergeable",
            release_decision=SimpleNamespace(decision=decision, reason="Passed.")
            if decision
            else None,
            fix_task=None,
            capability_review=None,
            headline=None,
            first_next_action_override=None,
            base_status="not_requested",
            base_ref=None,
            diff_status=VerifierDiffStatus() if complete else VerifierDiffStatus.unknown(),
            skip_subject_evaluated=skip_evaluated,
        )


@pytest.mark.parametrize(
    "diff",
    [
        "",
        "diff --git a/README.md b/README.md\n"
        "--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-old\n+new\n",
    ],
)
def test_detached_check_does_not_authorize_completion(tmp_path: Path, diff: str) -> None:
    result = evaluate_codex_boundary_result(
        workspace=tmp_path,
        diff_text=diff,
        verification_replayable=False,
    )
    assert result.control.state == "human_review_required"
    assert not result.control.permissions.authorizes_anything


def test_replayable_clean_check_remains_an_evaluated_result(tmp_path: Path) -> None:
    result = evaluate_codex_boundary_result(
        workspace=tmp_path,
        diff_text="",
        verification_replayable=True,
    )
    assert result.control.state == "complete"


@pytest.mark.parametrize("missing", [None, *PERMISSION_FIELDS])
@pytest.mark.parametrize(
    "route", ["complete", "agent_action_required", "review_publishable", "human_review_required"]
)
def test_explicit_incomplete_permissions_are_rejected(route: str, missing: str | None) -> None:
    if route == "complete":
        # Direct construction is allowed for trusted internal callers.
        from agents_shipgate.schemas.agent_control import CompleteAgentControl

        control = CompleteAgentControl(state="complete", reason="An evaluated subject passed.")
    else:
        kwargs = {
            "human_review_required": route != "agent_action_required",
            "publication_allowed": route != "human_review_required",
        }
        if route == "agent_action_required":
            kwargs["next_action"] = {
                "actor": "coding_agent",
                "kind": "verify",
                "command": "shipgate verify",
                "expects": None,
                "why": "Verify.",
            }
        control = derive_agent_control(reason="Review or verify the evaluated input.", **kwargs)
    payload = deepcopy(control.model_dump(mode="json"))
    if missing is None:
        payload["permissions"] = {}
    else:
        del payload["permissions"][missing]
    assert not Draft202012Validator(AGENT_CONTROL_ADAPTER.json_schema()).is_valid(payload)
    with pytest.raises(ValidationError, match="permission"):
        AGENT_CONTROL_ADAPTER.validate_python(payload)
