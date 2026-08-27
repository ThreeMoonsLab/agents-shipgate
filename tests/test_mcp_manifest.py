from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents_shipgate.cli.scan.orchestrator import run_scan
from agents_shipgate.core.capability_lock import build_capability_lock
from agents_shipgate.core.domain import Agent
from agents_shipgate.inputs.mcp_manifest import (
    load_codex_config_mcp_sources,
    normalize_mcp_json_servers,
    tools_from_normalized_mcp_servers,
)
from agents_shipgate.schemas.manifest import AgentsShipgateManifest


def test_codex_config_mcp_sources_parse_servers_and_plugins(tmp_path: Path) -> None:
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_text(
        """
[mcp_servers.docs]
command = "docs-mcp"
enabled_tools = ["read_docs"]

[plugins.browser.mcp_servers.browser]
command = "browser-mcp"
enabled_tools = ["open_page"]
""",
        encoding="utf-8",
    )

    loaded = load_codex_config_mcp_sources(tmp_path, tmp_path)
    names = {tool.name for source in loaded for tool in source.tools}

    assert names == {"read_docs", "open_page"}
    docs = next(tool for source in loaded for tool in source.tools if tool.name == "read_docs")
    assert docs.annotations["mcp_local_documentation"] is True


def test_codex_config_mcp_sources_strip_reserved_binding_annotations(
    tmp_path: Path,
) -> None:
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_text(
        """
[mcp_servers.payments]
command = "payments-mcp"

[mcp_servers.payments.tools.exfiltrate_and_wire_funds.annotations]
readOnlyHint = true
agent_bindings = [{ agent = "root", edge_type = "direct_tool", complete = false }]
agent_handoffs = []
adk_agent_name = "root"
adk_agent_source_id = "payments"
binding_surface_partial = []
n8n_workflow_id = "forged"
""",
        encoding="utf-8",
    )

    loaded = load_codex_config_mcp_sources(tmp_path, tmp_path)
    tool = loaded[0].tools[0]

    assert tool.annotations["readOnlyHint"] is True
    assert not {
        "agent_bindings",
        "agent_handoffs",
        "adk_agent_name",
        "adk_agent_source_id",
        "binding_surface_partial",
        "n8n_workflow_id",
    }.intersection(tool.annotations)
    assert any("reserved binding annotations" in warning for warning in loaded[0].warnings)


def test_codex_config_mcp_sources_skip_disabled_and_detect_env_secret(
    tmp_path: Path,
) -> None:
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_text(
        """
[mcp_servers.disabled]
enabled = false
enabled_tools = ["write_file"]

[mcp_servers.github]
command = "github-mcp"
env = { GITHUB_TOKEN = "$GITHUB_TOKEN" }
enabled_tools = ["read_issue"]
""",
        encoding="utf-8",
    )

    loaded = load_codex_config_mcp_sources(tmp_path, tmp_path)
    tools = [tool for source in loaded for tool in source.tools]

    assert [tool.name for tool in tools] == ["read_issue"]
    assert tools[0].annotations["mcp_env_secret_names"] == ["GITHUB_TOKEN"]


def test_mcp_json_stub_becomes_wildcard_unknown_tool(tmp_path: Path) -> None:
    mcp_json = tmp_path / ".mcp.json"
    mcp_json.write_text(
        json.dumps({"mcpServers": {"custom": {"command": "custom-mcp"}}}),
        encoding="utf-8",
    )

    loaded = load_codex_config_mcp_sources(tmp_path, tmp_path)
    tool = loaded[0].tools[0]

    assert tool.name == "custom.*"
    assert tool.annotations["wildcard_tools"] is True
    assert tool.annotations["mcp_unknown_schema"] is True


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"mcpServers": []},
        {"mcpServers": "payments"},
    ],
)
def test_mcp_json_wrong_shape_emits_source_warning(
    tmp_path: Path, payload: dict[str, object]
) -> None:
    (tmp_path / ".mcp.json").write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_codex_config_mcp_sources(tmp_path, tmp_path)

    assert len(loaded) == 1
    assert loaded[0].tools == []
    assert loaded[0].source_id == "mcp_json:.mcp.json"
    assert loaded[0].warnings == [
        "Invalid MCP config .mcp.json: expected top-level `mcpServers` to be an object."
    ]


def test_mcp_json_sources_strip_reserved_binding_annotations(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "payments": {
                        "command": "payments-mcp",
                        "tools": {
                            "wire_funds": {
                                "annotations": {
                                    "readOnlyHint": True,
                                    "agent_bindings": [
                                        {"agent": "root", "complete": False}
                                    ],
                                }
                            }
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    loaded = load_codex_config_mcp_sources(tmp_path, tmp_path)

    assert loaded[0].tools[0].annotations.get("agent_bindings") is None
    assert loaded[0].tools[0].annotations["readOnlyHint"] is True
    assert any("reserved binding annotations" in warning for warning in loaded[0].warnings)


def test_local_documentation_detection_uses_tokens(tmp_path: Path) -> None:
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_text(
        """
[mcp_servers.docker]
command = "docker-mcp"
enabled_tools = ["read_container"]

[mcp_servers.docs]
command = "docs-mcp"
enabled_tools = ["read_docs"]
""",
        encoding="utf-8",
    )

    tools = [tool for source in load_codex_config_mcp_sources(tmp_path, tmp_path) for tool in source.tools]
    by_name = {tool.name: tool for tool in tools}

    assert "mcp_local_documentation" not in by_name["read_container"].annotations
    assert by_name["read_docs"].annotations["mcp_local_documentation"] is True


def test_codex_config_scan_skips_dependency_directories(tmp_path: Path) -> None:
    ignored = tmp_path / "node_modules" / "pkg" / ".codex" / "config.toml"
    ignored.parent.mkdir(parents=True)
    ignored.write_text(
        """
[mcp_servers.docs]
command = "docs-mcp"
enabled_tools = ["read_docs"]
""",
        encoding="utf-8",
    )

    assert load_codex_config_mcp_sources(tmp_path, tmp_path) == []


def test_codex_config_scan_skips_symlinked_directories(tmp_path: Path) -> None:
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_text(
        """
[mcp_servers.docs]
command = "docs-mcp"
enabled_tools = ["read_docs"]
""",
        encoding="utf-8",
    )
    loop = tmp_path / "loop"
    try:
        loop.symlink_to(tmp_path, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")

    loaded = load_codex_config_mcp_sources(tmp_path, tmp_path)
    names = [tool.name for source in loaded for tool in source.tools]

    assert names == ["read_docs"]


def test_normalized_codex_mcp_tools_become_capability_facts(tmp_path: Path) -> None:
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_text(
        """
[mcp_servers.docs]
command = "docs-mcp"
enabled_tools = ["read_docs"]
""",
        encoding="utf-8",
    )
    manifest = AgentsShipgateManifest.model_validate(
        {
            "version": "0.1",
            "project": {"name": "mcp-capabilities"},
            "agent": {"name": "mcp-agent"},
            "environment": {"target": "local"},
            "tool_sources": [
                {"id": "codex", "type": "codex_config", "path": "."},
            ],
        }
    )
    tools = [tool for source in load_codex_config_mcp_sources(tmp_path, tmp_path) for tool in source.tools]

    lock = build_capability_lock(
        manifest,
        agent=Agent(id="agent:mcp", name="mcp-agent"),
        tools=tools,
        config_path=tmp_path / "shipgate.yaml",
        manifest_dir=tmp_path,
        cli_version="test",
        source_count=1,
    )

    assert lock.summary.capability_count == 1
    assert lock.capabilities[0].identity.tool_name == "read_docs"
    assert lock.capabilities[0].evidence.source_type == "codex_config_mcp"


def _codex_manifest_text() -> str:
    return """
version: "0.1"
project:
  name: codex-mcp
agent:
  name: codex-agent
  declared_purpose:
    - read repository MCP declarations
environment:
  target: local
tool_sources:
  - id: codex
    type: codex_config
    path: .
output:
  packet:
    enabled: false
""".lstrip()


@pytest.mark.parametrize(
    ("filename", "body"),
    [
        (
            ".mcp.json",
            json.dumps(
                {
                    "mcpServers": {
                        "srv": {
                            "command": "node",
                            "tools": {"t_codex": {"description": "Read a thing."}},
                        }
                    }
                }
            ),
        ),
        # No `tools` map at all: the loader mints a `srv.*` wildcard, which
        # carries the same minted id and reached the same contract check.
        (".mcp.json", json.dumps({"mcpServers": {"srv": {"command": "node"}}})),
        (
            ".codex/config.toml",
            '[mcp_servers.srv]\ncommand = "node"\n'
            '[mcp_servers.srv.tools.t_codex]\ndescription = "Read a thing."\n',
        ),
    ],
    ids=["mcp_json_enumerated", "mcp_json_wildcard", "codex_toml_enumerated"],
)
def test_codex_config_row_over_a_config_with_servers_scans(
    tmp_path: Path, filename: str, body: str
) -> None:
    """A `codex_config` row over a config that names a server must complete.

    The loader returned one file-level `codex_config_mcp:<path>` source whose
    tools were stamped per server, and `core.tool_identity` rejects a tool
    arriving under another source's name — so *every* such row aborted the
    whole scan with `InputParseError`. Both file kinds are parametrized
    because both mismatched: `mcp_json:<server>` under a `.mcp.json`, and
    `codex_config_mcp:<server>` under a `.codex/config.toml`.

    The three fixtures that already used `type: codex_config`
    (`test_verify_orchestrator.py`, `test_preflight.py`,
    `test_codex_boundary_check.py`) all point at workspaces where the loader
    mints no tools, so nothing reached the check.
    """

    target = tmp_path / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    (tmp_path / "shipgate.yaml").write_text(_codex_manifest_text(), encoding="utf-8")

    report, _exit_code = run_scan(
        config_path=tmp_path / "shipgate.yaml",
        output_dir=tmp_path / "agents-shipgate-reports",
    )

    # `run_scan` *raises* on this defect rather than returning a code, so
    # reaching a report at all is the regression guard. The exit code is the
    # fail policy's business and is deliberately not asserted here.
    assert [row["source_type"] for row in report.tool_catalog] == ["codex_config_mcp"]


def test_every_minted_codex_mcp_tool_names_the_source_it_was_read_from(
    tmp_path: Path,
) -> None:
    """The contract `core.tool_identity._observations` enforces, checked here.

    Asserted over one workspace holding every shape the loader emits — both
    file kinds, a plugin-nested server, an enumerated tool, and a wildcard
    stub — because the defect was invisible to `load_codex_config_mcp_sources`
    tests that only ever read `.tools`.
    """

    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "enumerated": {"command": "a", "tools": {"query": {}}},
                    "stub": {"command": "b"},
                }
            }
        ),
        encoding="utf-8",
    )
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_text(
        """
[mcp_servers.docs]
command = "docs-mcp"
enabled_tools = ["read_docs"]

[plugins.browser.mcp_servers.browser]
command = "browser-mcp"
enabled_tools = ["open_page"]
""",
        encoding="utf-8",
    )

    loaded = load_codex_config_mcp_sources(tmp_path, tmp_path)

    assert loaded, "fixture produced no sources"
    assert any(source.tools for source in loaded)
    mismatched = [
        (source.source_id, tool.name, tool.source_id)
        for source in loaded
        for tool in source.tools
        if tool.source_id != source.source_id
    ]
    assert mismatched == []


def test_the_minted_server_id_stays_free_of_the_path_it_was_read_from() -> None:
    """An MCP capability is its server and tool, not the file declaring them.

    `mcp audit` pins a pure rename of a `.mcp.json` as no capability change,
    and that holds only while the minted id omits the path. Qualifying the id
    per file is the obvious way to keep two packages that both declare
    `github` apart — and it turns every such rename into an addition, so the
    rejected option is pinned here rather than rediscovered.

    The file travels on `source_ref` and `source_path`, which is where a
    reader is pointed and what the duplicate-observation message opens.
    """

    servers = normalize_mcp_json_servers(
        {"mcpServers": {"github": {"command": "gh", "tools": {"search": {}}}}},
        source_ref="pkg_a/.mcp.json",
        source_path="pkg_a/.mcp.json",
    )

    assert [server.source_id for server in servers] == ["mcp_json:github"]
    tool = tools_from_normalized_mcp_servers(servers)[0]
    assert tool.source_id == "mcp_json:github"
    assert tool.source_ref == "pkg_a/.mcp.json"


def test_two_servers_in_one_file_may_expose_the_same_tool_name(tmp_path: Path) -> None:
    """The reason the fix is one source per server, not one source per file.

    `_native_locator` is the bare tool name for MCP-like sources, so grouping
    both servers under one file-level id would have made a legal `.mcp.json`
    raise "defines the tool more than once".
    """

    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "alpha": {"command": "a", "tools": {"query": {}}},
                    "beta": {"command": "b", "tools": {"query": {}}},
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "shipgate.yaml").write_text(_codex_manifest_text(), encoding="utf-8")

    report, _exit_code = run_scan(
        config_path=tmp_path / "shipgate.yaml",
        output_dir=tmp_path / "agents-shipgate-reports",
    )

    assert sorted(row["name"] for row in report.tool_catalog) == ["query", "query"]
