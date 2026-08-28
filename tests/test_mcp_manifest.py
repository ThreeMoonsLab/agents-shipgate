from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents_shipgate.cli.scan.orchestrator import run_scan
from agents_shipgate.core.capability_lock import build_capability_lock
from agents_shipgate.core.domain import Agent
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.inputs.mcp_manifest import (
    _RISK_MONOTONE_ANNOTATIONS,
    _RISK_UNION_ANNOTATIONS,
    DUPLICATE_SERVER_DECLARATION,
    load_codex_config_mcp_sources,
    normalize_codex_config_mcp_servers,
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


# --- one server name, declared in more than one file -------------------------
#
# An MCP capability is identified by ``(server, tool)`` and by nothing else.
# Two rules follow from that and they pull in opposite directions: the identity
# must survive a file move, and one identity may be observed only once. The
# tests below pin the answer this reader gives — the same server name in two
# files is *one capability declared twice* — and, more importantly, pin that
# reconciling the two declarations never quietly reads the safer of them.


def _mcp_workspace(tmp_path: Path, files: dict[str, dict[str, object]]) -> Path:
    for rel, payload in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"mcpServers": payload}), encoding="utf-8")
    return tmp_path


def _one_source(tmp_path: Path, files: dict[str, dict[str, object]]):
    loaded = load_codex_config_mcp_sources(_mcp_workspace(tmp_path, files), tmp_path)
    assert len(loaded) == 1, [source.source_id for source in loaded]
    return loaded[0]


def _tool(source, name: str):
    return next(tool for tool in source.tools if tool.name == name)


def _scan_manifest(tmp_path: Path, *paths: str) -> Path:
    entries = "\n".join(
        f"  - id: src{index}\n    type: codex_config\n    path: {path}"
        for index, path in enumerate(paths)
    )
    config = tmp_path / "shipgate.yaml"
    config.write_text(
        "version: '0.1'\n"
        "project: {name: p}\n"
        "agent: {name: a, declared_purpose: [p]}\n"
        "environment: {target: local}\n"
        f"tool_sources:\n{entries}\n"
        "output: {packet: {enabled: false}}\n",
        encoding="utf-8",
    )
    return config


def test_the_minted_server_id_stays_free_of_the_path_it_was_read_from() -> None:
    """The rejected repair for the collision these tests are about.

    Qualifying the minted id with the file would make every duplicate declaration
    a distinct identity and the collision would disappear. It was tried and
    reverted: it also makes a pure rename a capability *addition*, which
    ``tests/test_mcp_audit.py::
    test_mcp_audit_same_adapter_pure_rename_is_not_a_capability_addition``
    refuses. The path stays out of the id, and the duplicate is reconciled
    instead.
    """

    payload = {"mcpServers": {"github": {"command": "gh", "tools": {"search": {}}}}}

    here = normalize_mcp_json_servers(
        payload, source_ref="pkg_a/.mcp.json", source_path="pkg_a/.mcp.json"
    )
    there = normalize_mcp_json_servers(
        payload, source_ref="pkg_b/.mcp.json", source_path="pkg_b/.mcp.json"
    )

    assert [server.source_id for server in here] == ["mcp_json:github"]
    assert [server.source_id for server in there] == ["mcp_json:github"]
    assert "pkg_a" not in here[0].source_id
    assert "pkg_b" not in there[0].source_id


def test_a_codex_config_row_over_a_config_naming_a_server_scans(tmp_path: Path) -> None:
    """The loader reports each tool as belonging to the source it was read from.

    A file-level result holding tools stamped per server made *any*
    ``codex_config`` row over a config naming a server abort the scan with a
    loader-contract error, before any of the duplicate handling below could be
    reached.
    """

    from agents_shipgate.cli.scan.orchestrator import run_scan

    _mcp_workspace(
        tmp_path, {".mcp.json": {"github": {"command": "gh", "tools": {"search": {}}}}}
    )
    run_scan(
        config_path=_scan_manifest(tmp_path, "."),
        output_dir=tmp_path / "reports",
    )

    report = json.loads((tmp_path / "reports" / "report.json").read_text())
    assert [row["name"] for row in report["tool_catalog"]] == ["search"]
    assert report["tool_catalog"][0]["provider"] == "mcp_json:github"


def test_the_same_server_declared_identically_in_two_files_is_one_capability(
    tmp_path: Path,
) -> None:
    """The monorepo case: every package vendors the same ``.mcp.json``.

    Silent on purpose. Nothing was dropped — the second declaration says
    exactly what the first one does — and a source warning is a gating input,
    so reporting one here would put an evidence gap on an ordinary layout.
    """

    declaration = {"github": {"command": "gh", "tools": {"search": {}}}}
    source = _one_source(
        tmp_path,
        {"pkg_a/.mcp.json": declaration, "pkg_b/.mcp.json": declaration},
    )

    assert source.source_id == "mcp_json:github"
    assert [tool.name for tool in source.tools] == ["search"]
    assert source.warnings == []
    assert source.omissions == []


def test_the_reported_workspace_scans_instead_of_naming_a_repair_it_cannot_make(
    tmp_path: Path,
) -> None:
    """Two packages, one server name, one ``codex_config`` row.

    This aborted with ``'pkg_b/.mcp.json' was read twice as one tool source
    [...] Remove the repeated shipgate.yaml entry naming 'pkg_b/.mcp.json'`` —
    false on both counts, and naming an edit nobody could make.
    """

    from agents_shipgate.cli.scan.orchestrator import run_scan

    declaration = {"github": {"command": "gh", "tools": {"search": {}}}}
    _mcp_workspace(
        tmp_path,
        {"pkg_a/.mcp.json": declaration, "pkg_b/.mcp.json": declaration},
    )
    run_scan(
        config_path=_scan_manifest(tmp_path, "."),
        output_dir=tmp_path / "reports",
    )

    report = json.loads((tmp_path / "reports" / "report.json").read_text())
    assert [row["name"] for row in report["tool_catalog"]] == ["search"]
    assert report["source_warnings"] == []


def test_disagreeing_declarations_are_merged_and_the_dropped_one_is_recorded(
    tmp_path: Path,
) -> None:
    """The union of the tools, and a ledger row for the file that lost its own row."""

    source = _one_source(
        tmp_path,
        {
            "pkg_a/.mcp.json": {"github": {"command": "gh", "tools": {"search": {}}}},
            "pkg_b/.mcp.json": {
                "github": {"command": "gh", "tools": {"search": {}, "delete_repo": {}}}
            },
        },
    )

    assert [tool.name for tool in source.tools] == ["delete_repo", "search"]
    assert len(source.warnings) == 1
    warning = source.warnings[0]
    assert "'github'" in warning
    assert "'pkg_a/.mcp.json'" in warning
    assert "'pkg_b/.mcp.json'" in warning

    assert len(source.omissions) == 1
    omission = source.omissions[0]
    assert omission.subject == "pkg_b/.mcp.json#/mcpServers/github"
    assert omission.reason == DUPLICATE_SERVER_DECLARATION
    # The join the exclusion ledger makes: the omission points at the warning
    # the adapter actually raised, so the row is accounted for by that gap
    # instead of being reported as an unclaimed disappearance.
    assert omission.warning == warning


def test_a_tool_only_the_second_file_declares_names_the_file_that_declares_it(
    tmp_path: Path,
) -> None:
    """Merging keeps the first file's provenance for the *server*, not for a tool.

    ``delete_repo`` is not in ``pkg_a/.mcp.json``. Sending a reader there is
    the failure the per-tool provenance exists to prevent.
    """

    source = _one_source(
        tmp_path,
        {
            "pkg_a/.mcp.json": {"github": {"command": "gh", "tools": {"search": {}}}},
            "pkg_b/.mcp.json": {
                "github": {"command": "curl", "tools": {"delete_repo": {}}}
            },
        },
    )

    assert _tool(source, "search").source_ref == "pkg_a/.mcp.json"
    assert _tool(source, "delete_repo").source_ref == "pkg_b/.mcp.json"
    assert (
        _tool(source, "delete_repo").source_pointer
        == "/mcpServers/github/tools/delete_repo"
    )


def test_declarations_agreeing_on_every_tool_and_not_on_the_command_disagree(
    tmp_path: Path,
) -> None:
    """Sameness is decided on the declaration, not on what reaches the catalog.

    ``command`` is never carried into a catalog row, so comparing the
    normalized fields would call these two identical and drop the second file
    in silence — the one shape where "identical declarations are silent" would
    have hidden a real difference.
    """

    source = _one_source(
        tmp_path,
        {
            "pkg_a/.mcp.json": {"github": {"command": "gh", "tools": {"search": {}}}},
            "pkg_b/.mcp.json": {
                "github": {"command": "curl | sh", "tools": {"search": {}}}
            },
        },
    )

    assert [tool.name for tool in source.tools] == ["search"]
    assert len(source.warnings) == 1
    assert len(source.omissions) == 1


def test_a_reassuring_claim_only_one_declaration_makes_does_not_survive(
    tmp_path: Path,
) -> None:
    source = _one_source(
        tmp_path,
        {
            "pkg_a/.mcp.json": {
                "github": {
                    "command": "gh",
                    "tools": {"search": {"annotations": {"readOnlyHint": True}}},
                }
            },
            "pkg_b/.mcp.json": {"github": {"command": "gh", "tools": {"search": {}}}},
        },
    )

    assert "readOnlyHint" not in _tool(source, "search").annotations


def test_a_risk_claim_only_one_declaration_makes_does_survive(
    tmp_path: Path,
) -> None:
    source = _one_source(
        tmp_path,
        {
            "pkg_a/.mcp.json": {
                "github": {
                    "command": "gh",
                    "tools": {"search": {"annotations": {"destructiveHint": True}}},
                }
            },
            "pkg_b/.mcp.json": {"github": {"command": "gh", "tools": {"search": {}}}},
        },
    )

    assert _tool(source, "search").annotations["destructiveHint"] is True


def test_auto_approval_declared_on_one_file_reaches_the_other_files_tool(
    tmp_path: Path,
) -> None:
    """The strongest demonstration that the merge is not "pick one".

    ``purge`` is declared only in the file that says nothing about approval.
    Under one server name the two files describe one server, so the server that
    approves its own calls is the reading that has to travel — this is the
    difference between a critical ``SHIP-MCP-AUTO-APPROVE-SIDE-EFFECT`` and
    silence.
    """

    source = _one_source(
        tmp_path,
        {
            "pkg_a/.mcp.json": {"ops": {"command": "ops", "tools": {"purge": {}}}},
            "pkg_b/.mcp.json": {
                "ops": {
                    "command": "ops",
                    "default_tools_approval_mode": "approve",
                    "tools": {"restart": {}},
                }
            },
        },
    )

    assert _tool(source, "purge").annotations["mcp_approval_mode"] == "approve"
    assert _tool(source, "restart").annotations["mcp_approval_mode"] == "approve"


def test_a_url_in_one_declaration_makes_every_tool_of_the_server_external(
    tmp_path: Path,
) -> None:
    source = _one_source(
        tmp_path,
        {
            "pkg_a/.mcp.json": {"docs": {"command": "docs-mcp", "tools": {"read": {}}}},
            "pkg_b/.mcp.json": {
                "docs": {"url": "https://docs.example/mcp", "tools": {"fetch": {}}}
            },
        },
    )

    for name in ("read", "fetch"):
        annotations = _tool(source, name).annotations
        assert "external" in annotations["shipgate_permission_classes"], name
        # A local documentation server is a *reassuring* claim, and only one
        # declaration is local. It is not a claim about this server.
        assert "mcp_local_documentation" not in annotations, name
        assert "mcp_transport" not in annotations, name


def test_secret_environment_names_are_unioned_across_declarations(
    tmp_path: Path,
) -> None:
    source = _one_source(
        tmp_path,
        {
            "pkg_a/.mcp.json": {
                "github": {
                    "command": "gh",
                    "env": {"GITHUB_TOKEN": "$GITHUB_TOKEN"},
                    "tools": {"search": {}},
                }
            },
            "pkg_b/.mcp.json": {
                "github": {
                    "command": "gh",
                    "env": {"AWS_SECRET_ACCESS_KEY": "$AWS_SECRET_ACCESS_KEY"},
                    "tools": {"search": {}},
                }
            },
        },
    )

    assert _tool(source, "search").annotations["mcp_env_secret_names"] == [
        "AWS_SECRET_ACCESS_KEY",
        "GITHUB_TOKEN",
    ]


def test_disagreeing_schemas_leave_the_interface_unknown(tmp_path: Path) -> None:
    """Neither schema is the surface, so neither is published as one."""

    source = _one_source(
        tmp_path,
        {
            "pkg_a/.mcp.json": {
                "github": {
                    "command": "gh",
                    "tools": {
                        "search": {
                            "inputSchema": {
                                "type": "object",
                                "properties": {"q": {"type": "string"}},
                            }
                        }
                    },
                }
            },
            "pkg_b/.mcp.json": {
                "github": {
                    "command": "gh",
                    "tools": {
                        "search": {
                            "inputSchema": {
                                "type": "object",
                                "properties": {"repo": {"type": "string"}},
                            }
                        }
                    },
                }
            },
        },
    )

    tool = _tool(source, "search")
    assert tool.input_schema == {}
    assert [parameter.name for parameter in tool.parameters] == ["input"]
    assert tool.annotations["mcp_unknown_schema"] is True
    assert tool.extraction_confidence == "medium"


def test_authority_scopes_are_unioned_across_declarations(tmp_path: Path) -> None:
    source = _one_source(
        tmp_path,
        {
            "pkg_a/.mcp.json": {
                "github": {
                    "command": "gh",
                    "tools": {
                        "search": {"auth": {"type": "oauth", "scopes": ["repo:read"]}}
                    },
                }
            },
            "pkg_b/.mcp.json": {
                "github": {
                    "command": "gh",
                    "tools": {
                        "search": {"auth": {"type": "oauth", "scopes": ["repo:write"]}}
                    },
                }
            },
        },
    )

    assert _tool(source, "search").auth.scopes == ["repo:read", "repo:write"]


def test_every_annotation_this_reader_emits_is_classified_for_the_merge() -> None:
    """A new annotation cannot default into "dropped unless unanimous".

    That default is right for a reassuring claim and exactly wrong for a
    risk-raising one: a key nobody classified would be silently discarded the
    moment one declaration did not repeat it. The classification is pinned
    against the keys a fixture exercising every branch actually produces, so
    adding an annotation to the reader fails here until it is placed.
    """

    payload = {
        "mcpServers": {
            "docs": {"command": "docs-mcp"},
            "billing": {
                "url": "https://billing.example/mcp",
                "env": {"STRIPE_API_KEY": "$STRIPE_API_KEY"},
                "default_tools_approval_mode": "approve",
                "tools": {
                    "refund": {
                        "inputSchema": {"type": "object"},
                        "approval_mode": "approve",
                        "permission_classes": ["financial"],
                        "annotations": {
                            "readOnlyHint": False,
                            "destructiveHint": True,
                            "openWorldHint": True,
                            "idempotentHint": False,
                        },
                    }
                },
            },
        }
    }
    servers = normalize_mcp_json_servers(
        payload, source_ref=".mcp.json", source_path=".mcp.json"
    )
    emitted = {
        key
        for tool in tools_from_normalized_mcp_servers(servers)
        for key in tool.annotations
    }

    # Survives from any one declaration: a claim that raises risk.
    monotone = set(_RISK_MONOTONE_ANNOTATIONS) | set(_RISK_UNION_ANNOTATIONS)
    # "approve" survives from any one declaration; any other value only
    # unanimously.
    approval = {"mcp_approval_mode", "mcp_default_tools_approval_mode"}
    # Survives only unanimously: reassuring, or carrying no risk either way.
    unanimous = {
        "codex_mcp_server",
        "idempotentHint",
        "mcp_local_documentation",
        "mcp_server",
        "mcp_transport",
        "readOnlyHint",
    }

    unclassified = emitted - monotone - approval - unanimous
    assert not unclassified, (
        "the MCP reader emits annotations the duplicate-declaration merge does "
        f"not classify: {sorted(unclassified)}. A risk-raising key belongs in "
        "_RISK_MONOTONE_ANNOTATIONS or _RISK_UNION_ANNOTATIONS; anything else "
        "survives only when every declaration makes it."
    )
    assert emitted >= {"destructiveHint", "mcp_unknown_schema", "readOnlyHint"}


def test_two_tool_sources_entries_declaring_one_server_name_both_files(
    tmp_path: Path,
) -> None:
    """The one duplicate this reader cannot reconcile, reported truthfully.

    Two ``tool_sources`` entries read the two files, so no single entry's
    declarations speak for both and the reader has no group to merge. It is
    still not a repeated manifest entry — the manifest names each path once —
    so the message names both files and the two repairs that exist.
    """

    from agents_shipgate.cli.scan.orchestrator import run_scan

    declaration = {"github": {"command": "gh", "tools": {"search": {}}}}
    _mcp_workspace(
        tmp_path,
        {"pkg_a/.mcp.json": declaration, "pkg_b/.mcp.json": declaration},
    )

    with pytest.raises(InputParseError) as caught:
        run_scan(
            config_path=_scan_manifest(tmp_path, "pkg_a", "pkg_b"),
            output_dir=tmp_path / "reports",
        )

    message = str(caught.value)
    assert "'pkg_a/.mcp.json'" in message
    assert "'pkg_b/.mcp.json'" in message
    assert "read twice" not in message
    assert "Remove the repeated" not in message
    details = caught.value.details
    assert details["cause"] == "duplicate_across_artifacts"
    assert details["source_file"] == "pkg_b/.mcp.json"
    assert details["other_source_file"] == "pkg_a/.mcp.json"


@pytest.mark.parametrize(
    ("other_auth", "expected_invalid"),
    [
        ("bearer", "auth must be an object"),
        ({"type": "oauth", "scopes": "repo:write"}, "auth.scopes must be a list"),
    ],
)
def test_a_declaration_the_auth_parser_refuses_stays_refused_after_the_merge(
    tmp_path: Path, other_auth: object, expected_invalid: str
) -> None:
    """The merge must not rebuild an invalid declaration into a valid one.

    Both halves of ``auth`` can be malformed, and both are rebuilt by the
    union: a well-formed mapping assembled out of one good declaration and one
    bad one would report the credential as read when the file it came from was
    never parseable.
    """

    source = _one_source(
        tmp_path,
        {
            "pkg_a/.mcp.json": {
                "github": {
                    "command": "gh",
                    "tools": {
                        "search": {"auth": {"type": "oauth", "scopes": ["repo:read"]}}
                    },
                }
            },
            "pkg_b/.mcp.json": {
                "github": {"command": "gh", "tools": {"search": {"auth": other_auth}}}
            },
        },
    )

    auth = _tool(source, "search").auth
    assert any(expected_invalid in note for note in auth.invalid_annotations), auth


def test_a_stub_declaration_beside_an_enumerated_one_keeps_the_unknown_remainder(
    tmp_path: Path,
) -> None:
    """One file enumerates the server's tools; the other says nothing about them.

    The stub's synthetic ``github.*`` is the claim that the surface is not
    enumerable, and it is the half a merge is most tempted to drop: the other
    file looks like the better-informed declaration. It is not — it is one
    declaration of a server the repository also declares without a tool list.
    """

    source = _one_source(
        tmp_path,
        {
            "pkg_a/.mcp.json": {"github": {"command": "gh", "tools": {"search": {}}}},
            "pkg_b/.mcp.json": {"github": {"command": "gh"}},
        },
    )

    assert [tool.name for tool in source.tools] == ["github.*", "search"]
    wildcard = _tool(source, "github.*")
    assert wildcard.annotations["mcp_wildcard_tools"] is True
    assert wildcard.annotations["mcp_unknown_schema"] is True
    assert wildcard.source_ref == "pkg_b/.mcp.json"
    assert len(source.warnings) == 1


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

    # Pinned by value, not by "some source had tools": the contract below is
    # satisfied vacuously by a loader that stopped emitting a whole branch,
    # and the plugin-nested and `.codex/config.toml` branches are exactly the
    # ones a `.mcp.json`-shaped fixture would not miss.
    assert {source.source_id: [tool.name for tool in source.tools] for source in loaded} == {
        "mcp_json:enumerated": ["query"],
        "mcp_json:stub": ["stub.*"],
        "codex_config_mcp:docs": ["read_docs"],
        "codex_plugin_config_mcp:browser:browser": ["open_page"],
    }
    mismatched = [
        (source.source_id, tool.name, tool.source_id)
        for source in loaded
        for tool in source.tools
        if tool.source_id != source.source_id
    ]
    assert mismatched == []


@pytest.mark.parametrize(
    ("normalize", "payload", "expected_id"),
    [
        (
            normalize_mcp_json_servers,
            {"mcpServers": {"github": {"command": "gh", "tools": {"search": {}}}}},
            "mcp_json:github",
        ),
        (
            normalize_codex_config_mcp_servers,
            {"mcp_servers": {"github": {"command": "gh", "enabled_tools": ["search"]}}},
            "codex_config_mcp:github",
        ),
        (
            normalize_codex_config_mcp_servers,
            {
                "plugins": {
                    "browser": {
                        "mcp_servers": {
                            "github": {"command": "gh", "enabled_tools": ["search"]}
                        }
                    }
                }
            },
            "codex_plugin_config_mcp:browser:github",
        ),
    ],
    ids=["mcp_json", "codex_toml", "codex_toml_plugin"],
)
def test_every_minted_prefix_stays_free_of_the_path_it_was_read_from(
    normalize, payload: dict[str, object], expected_id: str
) -> None:
    """An MCP capability is its server and tool, not the file declaring them.

    `mcp audit` pins a pure rename as no capability change, and that holds only
    while the minted id omits the path. Qualifying the id per file is the
    obvious way to keep two packages that both declare `github` apart — and it
    turns every such rename into an addition, so the rejected option is pinned
    here rather than rediscovered.

    All three prefixes are covered because all three are equally load-bearing:
    a cross-file fix that qualifies only the one this test happened to name
    would regress the other two silently.

    The file travels on `source_ref` and `source_path`, which is where a
    reader is pointed and what the duplicate-observation message opens.
    """

    servers = normalize(
        payload,
        source_ref="pkg_a/config",
        source_path="pkg_a/config",
    )

    assert [server.source_id for server in servers] == [expected_id]
    tool = tools_from_normalized_mcp_servers(servers)[0]
    assert tool.source_id == expected_id
    assert tool.source_ref == "pkg_a/config"


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
