"""No terminal or publication authority from input Shipgate did not evaluate."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator
from pydantic import TypeAdapter, ValidationError

from agents_shipgate.cli.agent_result import build_agent_boundary_result
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
    # The record `check` itself writes for an empty untracked file.
    diff = (
        "diff --git a/.codex/config.toml b/.codex/config.toml\nnew file mode 100644\n"
        "--- /dev/null\n+++ b/.codex/config.toml\n@@ -0,0 +0,0 @@\n"
    )
    audit = build_mcp_audit(workspace=tmp_path, diff_text=diff)
    assert audit["subject_evaluated"]
    assert _agent_result_from_audit(audit).control.state == "complete"


_SHELL_SERVER = '{"mcpServers": {"shell": {"command": "bash", "autoApprove": ["*"]}}}'
_ADD_SHELL_SERVER = (
    "diff --git a/.mcp.json b/.mcp.json\nnew file mode 100644\n"
    f"--- /dev/null\n+++ b/.mcp.json\n@@ -0,0 +1 @@\n+{_SHELL_SERVER}\n"
)
# case -> (diff, the issue `check` reports for it, whether the workspace holds
# the edited `.mcp.json`, so that the resolver alone would read text for it).
_UNPROVEN_SOURCE_RECORDS = {
    # What `git diff` prints for a real edit under `.mcp.json binary`.
    "binary_record": (
        "diff --git a/.mcp.json b/.mcp.json\nindex 1111111..2222222 100644\n"
        "Binary files a/.mcp.json and b/.mcp.json differ\n",
        "boundary_diff_content_missing",
        True,
    ),
    "header_only_record": (
        "diff --git a/.mcp.json b/.mcp.json\nindex 1111111..2222222 100644\n"
        "--- a/.mcp.json\n+++ b/.mcp.json\n",
        "boundary_diff_content_missing",
        True,
    ),
    "new_binary_toml": (
        "diff --git a/.codex/config.toml b/.codex/config.toml\nnew file mode 100644\n"
        "index 0000000..2222222\nBinary files /dev/null and b/.codex/config.toml differ\n",
        "boundary_diff_shape_invalid",
        False,
    ),
    "new_header_only_toml": (
        "diff --git a/.codex/config.toml b/.codex/config.toml\nnew file mode 100644\n",
        "boundary_diff_shape_invalid",
        False,
    ),
    "added_then_deleted": (
        _ADD_SHELL_SERVER
        + "diff --git a/.mcp.json b/.mcp.json\ndeleted file mode 100644\n"
        f"--- a/.mcp.json\n+++ /dev/null\n@@ -1 +0,0 @@\n-{_SHELL_SERVER}\n",
        "boundary_diff_shape_invalid",
        False,
    ),
}


@pytest.mark.parametrize("case", sorted(_UNPROVEN_SOURCE_RECORDS))
def test_mcp_audit_refuses_a_source_record_check_cannot_prove(tmp_path: Path, case: str) -> None:
    diff, code, edited_in_workspace = _UNPROVEN_SOURCE_RECORDS[case]
    if edited_in_workspace:
        (tmp_path / ".mcp.json").write_text(_SHELL_SERVER + "\n")
    check = build_agent_boundary_result(
        agent="codex",
        workspace=tmp_path,
        diff_text=diff,
        config=Path("shipgate.yaml"),
        policy=None,
        input_mode="provided_diff",
    )
    audit = build_mcp_audit(workspace=tmp_path, diff_text=diff)

    # One structural validation: the audit refuses each record `check` refuses.
    assert check.issues == [code]
    assert ("warning", code) in {(item["level"], item["code"]) for item in audit["diagnostics"]}
    assert audit["input_complete"] is False
    assert audit["subject_evaluated"] is False
    result = _agent_result_from_audit(audit)
    assert result.control.state == "human_review_required"
    assert not result.control.permissions.authorizes_anything


@pytest.mark.parametrize(
    "zero,empty",
    [
        ("0" * 40, "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"),
        ("0" * 64, "473a0f4c3be8a93681a267e3b1e9a7dcda1185436fe141f7749120a303721813"),
    ],
    ids=["sha1", "sha256"],
)
@pytest.mark.parametrize("mode", ["new", "deleted"])
@pytest.mark.parametrize("path", [".mcp.json", ".codex/config.toml"])
def test_empty_file_records_follow_check(
    tmp_path: Path, zero: str, empty: str, mode: str, path: str
) -> None:
    # Git prints an empty file's addition or deletion without `---`/`+++` lines
    # or hunks. `check` refuses that record whatever its blob id says, so the
    # audit does too; the record `check` writes for an empty file is evaluated.
    index = f"{zero}..{empty}" if mode == "new" else f"{empty}..{zero}"
    diff = f"diff --git a/{path} b/{path}\n{mode} file mode 100644\nindex {index}\n"
    check = build_agent_boundary_result(
        agent="codex",
        workspace=tmp_path,
        diff_text=diff,
        config=Path("shipgate.yaml"),
        policy=None,
        input_mode="provided_diff",
    )
    audit = build_mcp_audit(workspace=tmp_path, diff_text=diff)

    assert check.issues == ["boundary_diff_shape_invalid"]
    assert audit["input_complete"] is False
    assert not _agent_result_from_audit(audit).control.permissions.authorizes_anything


def test_mcp_audit_still_evaluates_a_deletion_and_a_rename_out(tmp_path: Path) -> None:
    deletion = (
        "diff --git a/.mcp.json b/.mcp.json\ndeleted file mode 100644\n"
        f"--- a/.mcp.json\n+++ /dev/null\n@@ -1 +0,0 @@\n-{_SHELL_SERVER}\n"
    )
    (tmp_path / "retired.txt").write_text(_SHELL_SERVER + "\n")
    rename_out = (
        "diff --git a/.mcp.json b/retired.txt\nsimilarity index 100%\n"
        "rename from .mcp.json\nrename to retired.txt\n"
    )
    for diff in (deletion, rename_out):
        audit = build_mcp_audit(workspace=tmp_path, diff_text=diff)
        assert audit["capability_delta"]["removed"]
        assert audit["subject_evaluated"] is True
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
