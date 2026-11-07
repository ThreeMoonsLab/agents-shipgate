"""Tests for the optional read-only MCP adapter."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import sys
import tomllib
import types
from pathlib import Path

import pytest

from agents_shipgate.core.errors import ConfigError
from agents_shipgate.mcp_server import (
    build_server,
    shipgate_capabilities,
    shipgate_check,
    shipgate_explain,
    shipgate_handoff,
    shipgate_preflight,
)
from tests.plugin_marker_fixture import install_marker_adapter_plugin

_HAS_MCP_SDK = importlib.util.find_spec("mcp") is not None


def _has_mcp_server_api() -> bool:
    """Whether the installed SDK carries the 2.x ``MCPServer`` the server needs.

    An SDK 1.x environment has ``mcp`` but not this module. Building the server
    there must fail with a version diagnostic, not register tools.
    """

    try:
        return importlib.util.find_spec("mcp.server.mcpserver") is not None
    except ModuleNotFoundError:
        return False


_HAS_MCP_SERVER_API = _has_mcp_server_api()


def _snapshot(root: Path) -> list[str]:
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())


def test_shipgate_check_returns_boundary_result_without_writes(tmp_path: Path) -> None:
    diff = """diff --git a/.codex/config.toml b/.codex/config.toml
new file mode 100644
index 0000000..1111111
--- /dev/null
+++ b/.codex/config.toml
@@ -0,0 +1,4 @@
+[mcp_servers.filesystem]
+command = "filesystem-mcp"
+enabled_tools = ["write_file"]
+default_tools_approval_mode = "approve"
"""
    before = _snapshot(tmp_path)

    payload = shipgate_check(
        agent="cursor",
        workspace=str(tmp_path),
        diff_text=diff,
    )

    after = _snapshot(tmp_path)
    assert after == before
    assert payload["schema_version"] == "shipgate.agent_boundary_result/v3"
    assert payload["agent"] == "cursor"
    assert payload["actor"] == "cursor"
    assert payload["affected_hosts"] == ["codex"]
    assert payload["input_coverage"] == "complete"
    assert payload["decision"] == "block"
    assert payload["control"]["state"] == "human_review_required"
    assert payload["control"]["next_action"]["kind"] == "stop"
    json.dumps(payload)


def test_mcp_check_never_authorizes_verify_for_detached_diff_text(
    tmp_path: Path,
) -> None:
    payload = shipgate_check(
        workspace=str(tmp_path),
        diff_text=(
            "diff --git a/.codex/config.toml b/.codex/config.toml\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/.codex/config.toml\n"
            "@@ -0,0 +1,3 @@\n"
            "+[permissions.workspace.network]\n"
            "+enabled = true\n"
            '+surprise = "value"\n'
        ),
    )

    assert payload["decision"] == "require_review"
    assert payload["control"]["state"] == "human_review_required"
    assert payload["control"]["allowed_next_commands"] == []
    assert payload["control"]["next_action"]["command"] is None
    assert "not bound to a checkout state" in payload["control"]["stop_reason"]
    assert payload["summary"] == payload["control"]["reason"]
    assert "Re-run check against the intended worktree" in payload["summary"]


def test_mcp_preflight_handler_is_read_only(tmp_path: Path) -> None:
    workspace = tmp_path / "wk"
    shutil.copytree("samples/clean_read_only_agent", workspace)
    before = _snapshot(workspace)

    payload = shipgate_preflight(
        workspace=str(workspace),
        changed_files=["shipgate.yaml"],
        diff_text=(
            "diff --git a/.cursor/rules/agents-shipgate.mdc "
            "b/.cursor/rules/agents-shipgate.mdc\n"
            "--- a/.cursor/rules/agents-shipgate.mdc\n"
            "+++ b/.cursor/rules/agents-shipgate.mdc\n"
        ),
    )

    assert payload["preflight_schema_version"] == "0.6"
    assert payload["requires_human_review"] is True
    assert payload["requires_verify"] is True
    assert payload["control"]["state"] == "human_review_required"
    assert {touch["path"] for touch in payload["protected_surface_touches"]} >= {
        "shipgate.yaml",
        ".cursor/rules/agents-shipgate.mdc",
    }
    assert any(signal["kind"] == "protected_surface_touch" for signal in payload["signals"])
    assert _snapshot(workspace) == before


def test_mcp_preflight_keeps_the_source_side_of_a_rename(tmp_path: Path) -> None:
    workspace = tmp_path / "wk"
    shutil.copytree("samples/clean_read_only_agent", workspace)

    payload = shipgate_preflight(
        workspace=str(workspace),
        diff_text=(
            "diff --git a/shipgate.yaml b/retired.txt\n"
            "similarity index 100%\n"
            "rename from shipgate.yaml\n"
            "rename to retired.txt\n"
        ),
    )

    assert payload["changed_files"] == ["retired.txt", "shipgate.yaml"]
    assert payload["requires_human_review"] is True
    assert any(
        touch["path"] == "shipgate.yaml"
        for touch in payload["protected_surface_touches"]
    )


def test_mcp_preflight_accepts_plan_without_writes(tmp_path: Path) -> None:
    workspace = tmp_path / "wk"
    shutil.copytree("samples/clean_read_only_agent", workspace)
    before = _snapshot(workspace)

    payload = shipgate_preflight(
        workspace=str(workspace),
        plan={
            "schema_version": "preflight_plan_v1",
            "changed_files": ["docs/readme.md"],
            "host_permission_requests": [
                {
                    "host": "claude-code",
                    "surface": "permissions.allow",
                    "operation": "add",
                    "path": ".claude/settings.json",
                    "subject": "Write(*)",
                    "requested_access": {"allow": ["Write(*)"]},
                    "reason": "auto approve write tools",
                }
            ],
        },
    )

    assert payload["preflight_schema_version"] == "0.6"
    assert payload["first_next_action"]["actor"] == "human"
    assert payload["control"]["state"] == "human_review_required"
    assert any(signal["kind"] == "least_privilege" for signal in payload["signals"])
    assert _snapshot(workspace) == before


def test_mcp_preflight_rejects_plan_combined_with_direct_diff(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "wk"
    shutil.copytree("samples/clean_read_only_agent", workspace)

    with pytest.raises(
        ConfigError,
        match=r"plan cannot be combined with diff_text",
    ):
        shipgate_preflight(
            workspace=str(workspace),
            plan={"schema_version": "preflight_plan_v1"},
            diff_text=(
                "diff --git a/shipgate.yaml b/shipgate.yaml\n"
                "--- a/shipgate.yaml\n"
                "+++ b/shipgate.yaml\n"
                "@@ -1 +1 @@\n"
                '-version: "0.1"\n'
                '+version: "0.2"\n'
            ),
        )


def test_mcp_preflight_rejects_mixed_shape_before_validating_direct_input(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "wk"
    shutil.copytree("samples/clean_read_only_agent", workspace)

    with pytest.raises(
        ConfigError,
        match=r"plan cannot be combined with capability_request",
    ):
        shipgate_preflight(
            workspace=str(workspace),
            plan={"schema_version": "preflight_plan_v1"},
            capability_request={"not": "a capability request"},
        )


def test_mcp_explain_handler_returns_check_metadata() -> None:
    payload = shipgate_explain(
        check_id="SHIP-POLICY-APPROVAL-MISSING",
        no_plugins=True,
    )

    assert payload["id"] == "SHIP-POLICY-APPROVAL-MISSING"
    assert payload["category"] == "policy"


def test_mcp_capabilities_handler_does_not_write_reports(tmp_path: Path) -> None:
    workspace = tmp_path / "wk"
    shutil.copytree("samples/clean_read_only_agent", workspace)
    before = _snapshot(workspace)

    payload = shipgate_capabilities(
        config=str(workspace / "shipgate.yaml"),
        no_plugins=True,
    )

    assert payload["capability_lock_schema_version"] == "0.9"
    assert _snapshot(workspace) == before


def test_mcp_capabilities_loads_adapter_plugins_only_on_the_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The read-only `shipgate.capabilities` tool runs no plugin by default.

    Its default `no_plugins=False` reached the lock builder as an explicit
    `plugins_enabled=True`, so an agent host that auto-approves a read-only
    tool imported every installed adapter plugin with the opt-in unset, where
    `scan` and `verify` imported none (#1020).
    """

    marker = install_marker_adapter_plugin(tmp_path, monkeypatch)
    workspace = tmp_path / "wk"
    shutil.copytree("samples/clean_read_only_agent", workspace)

    payload = shipgate_capabilities(config=str(workspace / "shipgate.yaml"))

    assert not marker.exists(), "an adapter plugin was imported without the opt-in"
    assert payload["source"]["plugins_enabled"] is False

    monkeypatch.setenv("AGENTS_SHIPGATE_ENABLE_PLUGINS", "1")
    payload = shipgate_capabilities(config=str(workspace / "shipgate.yaml"), no_plugins=True)
    assert not marker.exists(), "no_plugins must win over the opt-in"
    assert payload["source"]["plugins_enabled"] is False

    payload = shipgate_capabilities(config=str(workspace / "shipgate.yaml"))
    assert marker.exists(), "the fake plugin must be discoverable once opted in"
    assert payload["source"]["plugins_enabled"] is True


def test_mcp_handoff_handler_is_read_only(tmp_path: Path) -> None:
    output_dir = tmp_path / "agents-shipgate-reports"
    output_dir.mkdir()
    _write_json(
        output_dir / "verifier.json",
        {
            "workspace": str(tmp_path),
            "config": "shipgate.yaml",
            "execution": "succeeded",
            "head_status": "succeeded",
            "diff_status": {"completeness": "complete"},
            "release_decision": {
                "decision": "passed",
                "reason": "All required static verification passed.",
                "blockers": [],
                "review_items": [],
                "evidence_coverage": {
                    "level": "complete",
                    "human_review_recommended": False,
                    "source_warning_count": 0,
                    "low_confidence_tool_count": 0,
                    "evidence_gaps": [],
                },
                "baseline_delta": {"enabled": False},
                "fail_policy": {
                    "ci_mode": "advisory",
                    "fail_on": [],
                    "new_findings_only": False,
                    "would_fail_ci": False,
                    "exit_code": 0,
                },
            },
            "decision": "passed",
            "merge_verdict": "mergeable",
            "applicability": "verified",
            "can_merge_without_human": True,
                "control": {
                "state": "complete",
                "reason": "All required static verification passed.",
                "completion_allowed": True,
                "must_stop": False,
                "verify_required": False,
                "next_action": None,
                "human_review": {
                    "required": False,
                    "why": None,
                    "required_reviewers": [],
                },
                "stop_reason": None,
                    "allowed_next_commands": [],
                },
                "authorization": {
                    "schema_version": "shipgate.human_authorization_evaluation/v1",
                    "status": "not_requested",
                    "authorization_id": None,
                    "authorization_request_id": None,
                    "trust_policy_id": None,
                    "key_id": None,
                    "provider": None,
                    "principal": None,
                    "operation_id": None,
                    "command": None,
                    "issued_at": None,
                    "expires_at": None,
                    "reason_codes": [],
                },
            "forbidden_file_edits": [],
            "forbidden_actions": [],
            "artifacts": {
                "verifier_json": str(output_dir / "verifier.json"),
                "agent_handoff_json": str(output_dir / "agent-handoff.json"),
            },
        },
    )
    _write_json(output_dir / "verify-run.json", {"run_id": "sha256:" + "b" * 64})
    before = _snapshot(tmp_path)

    payload = shipgate_handoff(verifier_path=str(output_dir / "verifier.json"))

    assert payload["schema_version"] == "shipgate.agent_handoff/v9"
    assert payload["gate"]["merge_verdict"] == "mergeable"
    assert payload["control"]["state"] == "complete"
    assert _snapshot(tmp_path) == before


@pytest.mark.skipif(_HAS_MCP_SDK, reason="mcp extra installed; error path n/a")
def test_build_server_without_sdk_raises_config_error() -> None:
    with pytest.raises(ConfigError, match=r"agents-shipgate\[mcp\]"):
        build_server()


def test_an_sdk_without_mcpserver_is_named_as_a_version_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#713: reinstalling the extra cannot repair an SDK that lacks the API.

    ``mcp`` imports but ``mcp.server.mcpserver`` does not, which is the shape of
    an SDK 1.x environment. The diagnostic names the version problem and the
    required range instead of repeating the install instruction.
    """

    if not _HAS_MCP_SDK:
        monkeypatch.setitem(sys.modules, "mcp", types.ModuleType("mcp"))
    monkeypatch.setitem(sys.modules, "mcp.server.mcpserver", None)

    with pytest.raises(ConfigError) as caught:
        build_server()

    message = str(caught.value)
    assert "mcp>=2.0.0,<3" in message
    assert "MCPServer" in message
    assert 'pip install --upgrade "agents-shipgate[mcp]"' in message
    assert "requires the optional [mcp] extra" not in message


def test_the_sdk_range_the_server_names_is_the_range_the_extra_declares() -> None:
    from agents_shipgate.mcp_server.server import MCP_SDK_REQUIREMENT

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    extra = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"][
        "optional-dependencies"
    ]["mcp"]

    assert extra == [MCP_SDK_REQUIREMENT]


@pytest.mark.skipif(
    not _HAS_MCP_SERVER_API, reason="requires the optional mcp extra (SDK 2.x)"
)
def test_build_server_registers_read_only_tools() -> None:
    server = build_server()

    import asyncio

    listed = asyncio.run(server.list_tools())
    assert {tool.name for tool in listed} == {
        "shipgate.capabilities",
        "shipgate.check",
        "shipgate.explain",
        "shipgate.handoff",
        "shipgate.preflight",
    }
    # Every tool advertises its read-only, closed-world nature in the
    # machine-readable contract, not just the server prose instructions.
    for tool in listed:
        assert tool.annotations is not None, tool.name
        assert tool.annotations.read_only_hint is True, tool.name
        assert tool.annotations.open_world_hint is False, tool.name


#: Each tool's inputs as registered before #800 gave the tools descriptions:
#: JSON types and default per input, and none required. #800 changed what
#: ``tools/list`` says about a tool, never what it accepts. The SDK's cosmetic
#: ``title`` keys are left out so an SDK release that retitles them does not
#: read as an interface change.
_REGISTERED_INPUTS = {
    "shipgate.check": {
        "agent": (("string",), "codex"),
        "workspace": (("string",), "."),
        "diff_text": (("string",), ""),
        "config": (("string",), "shipgate.yaml"),
        "policy": (("null", "string"), None),
    },
    "shipgate.preflight": {
        "workspace": (("string",), "."),
        "config": (("string",), "shipgate.yaml"),
        "changed_files": (("array[string]", "null"), None),
        "diff_text": (("null", "string"), None),
        "capability_request": (("null", "object"), None),
        "plan": (("null", "object"), None),
        "base_preflight": (("null", "object"), None),
    },
    "shipgate.explain": {
        "check_id": (("null", "string"), None),
        "fingerprint": (("null", "string"), None),
        "report_path": (("null", "string"), None),
        "no_plugins": (("boolean",), False),
    },
    "shipgate.capabilities": {
        "config": (("string",), "shipgate.yaml"),
        "base_lock": (("null", "string"), None),
        "head_lock": (("null", "string"), None),
        "no_plugins": (("boolean",), False),
    },
    "shipgate.handoff": {
        "verifier_path": (("string",), "agents-shipgate-reports/verifier.json"),
        "report_path": (("null", "string"), None),
        "verify_run_path": (("null", "string"), None),
    },
}

#: What each description must say besides naming every input (#800). The first
#: entry is the task it opens with, so a host that shows only the start still
#: shows when to call it; the rest are the input alternatives and boundaries its
#: handler actually has. ``check`` and ``capabilities`` both compare something,
#: so both say they are not the CLI's Git-backed host comparison.
_REQUIRED_STATEMENTS = {
    "shipgate.check": (
        "Check a coding agent's change",
        "from a unified diff you already have",
        "this tool runs no git and does not obtain a base or head ref",
        "not the CLI's Git-backed host comparison (`agents-shipgate diff --base <ref>`)",
        "every result stops for human routing",
        "`human_review_required`",
        "authorizes no verify command and grants no permission",
        "runs no scan or verify and is not a release or merge verdict",
    ),
    "shipgate.preflight": (
        "Before editing, route a planned change",
        "Pass either `plan`",
        "never both: a mixed request is rejected",
        "Projection only: it is not a release verdict",
        "every permission in its `control` is false",
        "names a CLI `verify` command but runs nothing",
        "runs no git, host comparison, scan or verify",
    ),
    "shipgate.explain": (
        "Explain an Agents Shipgate check, or one finding in an existing report",
        "Pass `check_id`",
        "Or pass `fingerprint` with `report_path`",
        "`fingerprint` requires `report_path`",
        "produces no findings and runs no scan, verify, git or host comparison",
    ),
    "shipgate.capabilities": (
        "Export or compare static capability locks",
        "Pass `config`",
        "or both `base_lock` and `head_lock` (existing capability lock JSON files",
        "one lock alone is rejected",
        "neither is a release gate",
        "this tool runs no git and checks out no base ref",
        "not the CLI's Git-backed host comparison (`agents-shipgate diff --base <ref>`)",
    ),
    "shipgate.handoff": (
        "Read the compact agent handoff of a `verify` run that already happened",
        "which must exist",
        "grants nothing the verifier did not",
        "runs no scan, verify, git or host comparison",
        "does not check that the artifacts still match the working tree",
        "re-run `agents-shipgate verify` after any change",
    ),
}


def _listed_tools() -> dict:
    import asyncio

    return {tool.name: tool for tool in asyncio.run(build_server().list_tools())}


def _input_contract(schema: dict) -> tuple[list[str], dict]:
    """Each input's JSON types and default, without the SDK's titles."""

    def types(prop: dict) -> tuple[str, ...]:
        return tuple(
            sorted(
                branch["type"]
                + (f"[{branch['items']['type']}]" if "items" in branch else "")
                for branch in prop.get("anyOf", [prop])
            )
        )

    return sorted(schema.get("required", [])), {
        name: (types(prop), prop.get("default"))
        for name, prop in schema["properties"].items()
    }


@pytest.mark.skipif(
    not _HAS_MCP_SERVER_API, reason="requires the optional mcp extra (SDK 2.x)"
)
def test_every_tool_tells_an_agent_its_task_inputs_and_boundary() -> None:
    """#800: ``tools/list`` advertised five tools with empty descriptions.

    A connected agent choosing between a provided-diff check, a plan
    projection, an explanation, a lock comparison and an artifact read saw
    only names. Each description now opens with the task, names every input
    the tool accepts, and states what the tool cannot do: none runs git or the
    CLI's Git-backed host comparison, scan or verify, or grants an authority.
    """

    listed = _listed_tools()
    assert set(listed) == set(_REQUIRED_STATEMENTS)
    descriptions = {name: tool.description or "" for name, tool in listed.items()}
    for name, description in descriptions.items():
        assert description.strip(), f"{name} is listed with an empty description"
        # Some model APIs cap a function description at 1024 characters, and a
        # host forwarding these tools to one would cut off or refuse a longer
        # one, losing the boundary statements that close each description.
        assert len(description) <= 1024, (name, len(description))
        statements = _REQUIRED_STATEMENTS[name]
        assert description.startswith(statements[0]), (name, description)
        missing = [statement for statement in statements if statement not in description]
        assert not missing, f"{name} no longer says {missing}"
        unnamed = [
            argument
            for argument in listed[name].input_schema["properties"]
            if f"`{argument}`" not in description
        ]
        assert not unnamed, f"{name} does not tell an agent about inputs {unnamed}"
        assert re.search(r"runs no [^.:;]*\bgit\b", description), (
            f"{name} must say it runs no git"
        )
    assert len(set(descriptions.values())) == len(descriptions), (
        "two tools share a description, so an agent cannot tell their jobs apart"
    )


@pytest.mark.skipif(
    not _HAS_MCP_SERVER_API, reason="requires the optional mcp extra (SDK 2.x)"
)
def test_descriptions_left_every_tool_name_and_input_unchanged() -> None:
    listed = _listed_tools()

    assert set(listed) == set(_REGISTERED_INPUTS)
    for name, tool in listed.items():
        required, inputs = _input_contract(tool.input_schema)
        assert required == [], name
        assert inputs == _REGISTERED_INPUTS[name], name
        assert tool.annotations is not None, name
        assert tool.annotations.read_only_hint is True, name
        assert tool.annotations.open_world_hint is False, name


@pytest.mark.skipif(
    not _HAS_MCP_SERVER_API, reason="requires the optional mcp extra (SDK 2.x)"
)
def test_stdio_tools_list_carries_the_descriptions_and_a_tool_still_answers(
    tmp_path: Path,
) -> None:
    """The protocol response, not just the in-process registry (#800).

    ``mcp-serve`` runs as a real stdio server in a scratch directory and is
    asked over JSON-RPC what ``tools/list`` returns, then one existing tool is
    called, so the descriptions an agent host receives are the registered ones
    and a call behaves as before.
    """

    import asyncio

    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    async def over_stdio(errlog):
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "agents_shipgate", "mcp-serve"],
            # The whole environment, so a run against a source tree on
            # PYTHONPATH serves that tree rather than an installed copy.
            env=dict(os.environ),
            cwd=str(tmp_path),
        )
        async with stdio_client(parameters, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
                called = await session.call_tool(
                    "shipgate.explain",
                    {"check_id": "SHIP-DOC-MISSING-DESCRIPTION", "no_plugins": True},
                )
                return tools, called

    with (tmp_path / "server-stderr.log").open("w", encoding="utf-8") as errlog:
        tools, called = asyncio.run(asyncio.wait_for(over_stdio(errlog), timeout=120))

    registered = _listed_tools()
    served = {tool.name: tool for tool in tools}
    assert set(served) == set(registered)
    for name, tool in served.items():
        assert tool.description, f"{name} reached the client without a description"
        assert tool.description == registered[name].description, name
        assert tool.input_schema == registered[name].input_schema, name
        assert tool.annotations == registered[name].annotations, name
    assert called.is_error is False
    assert (called.structured_content or {}).get("id") == "SHIP-DOC-MISSING-DESCRIPTION"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["server-stderr.log"]


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")
