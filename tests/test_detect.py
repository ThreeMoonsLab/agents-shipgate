"""Tests for ``shipgate detect`` and ``signals.detect_workspace``."""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from agents_shipgate.cli.discovery.signals import detect_workspace, select_agent_name
from agents_shipgate.cli.discovery.template import render_auto_manifest
from agents_shipgate.schemas.detect import DetectResult

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
FIXTURE_SKIP_DIR_NAMES = (
    "fixtures",
    "_fixtures",
    "__fixtures__",
    "testdata",
    "test_data",
    "test-fixtures",
    "test_fixtures",
    "golden",
    "goldens",
)


def _write_skipped_fixture_signals(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "agent.py").write_text(
        "from langchain.tools import tool\n\n@tool\ndef lookup():\n    return 'x'\n",
        encoding="utf-8",
    )
    tools = root / "tools"
    tools.mkdir()
    (tools / "payments-mcp.json").write_text(
        '{"tools": [{"name": "create_payment_link", "description": "Create link."}]}',
        encoding="utf-8",
    )
    (root / "broken-mcp.json").write_text("{not json", encoding="utf-8")
    specs = root / "specs"
    specs.mkdir()
    (specs / "support.openapi.yaml").write_text(
        "openapi: 3.1.0\ninfo:\n  title: T\n  version: '1'\npaths: {}\n",
        encoding="utf-8",
    )
    plugin = root / "plugin" / ".codex-plugin"
    plugin.mkdir(parents=True)
    (plugin / "plugin.json").write_text("{}", encoding="utf-8")


def _write_plugin_marketplace(
    root: Path,
    *,
    marketplace: object | str | bytes,
    plugin_text: str = '{"name":"reviewer"}',
) -> Path:
    workspace = root / "workspace"
    plugin = workspace / "plugins/reviewer/.codex-plugin/plugin.json"
    plugin.parent.mkdir(parents=True)
    plugin.write_text(plugin_text, encoding="utf-8")
    path = workspace / ".agents/plugins/marketplace.json"
    path.parent.mkdir(parents=True)
    if isinstance(marketplace, bytes):
        path.write_bytes(marketplace)
    else:
        path.write_text(
            marketplace if isinstance(marketplace, str) else json.dumps(marketplace),
            encoding="utf-8",
        )
    return workspace


def _local_marketplace(source: str = "local") -> dict[str, object]:
    return {
        "plugins": [
            {
                "name": "reviewer",
                "source": {"source": source, "path": "plugins/reviewer"},
            }
        ]
    }


@pytest.mark.parametrize("plugin_text", ['{"name":"reviewer"}', "{not-json"])
def test_detect_deduplicates_marketplace_covered_package(
    tmp_path: Path,
    plugin_text: str,
) -> None:
    workspace = _write_plugin_marketplace(
        tmp_path,
        marketplace=_local_marketplace(),
        plugin_text=plugin_text,
    )

    result = detect_workspace(workspace)

    assert [(item.mode, item.path) for item in result.codex_plugin_candidates] == [
        ("marketplace", ".agents/plugins/marketplace.json")
    ]
    rendered = render_auto_manifest(workspace, result).text
    assert rendered.count("type: codex_plugin") == 1
    assert "path: plugins/reviewer" not in rendered


@pytest.mark.parametrize(
    "marketplace",
    [
        pytest.param("{not-json", id="malformed"),
        pytest.param(b"\xff", id="non-utf8"),
        pytest.param(_local_marketplace("github"), id="remote"),
    ],
)
def test_detect_keeps_package_for_unresolved_marketplace(
    tmp_path: Path,
    marketplace: object | str | bytes,
) -> None:
    workspace = _write_plugin_marketplace(tmp_path, marketplace=marketplace)

    result = detect_workspace(workspace)

    assert {(item.mode, item.path) for item in result.codex_plugin_candidates} == {
        ("marketplace", ".agents/plugins/marketplace.json"),
        ("package", "plugins/reviewer"),
    }


def test_detects_langchain_sample() -> None:
    result = detect_workspace(SAMPLES / "simple_langchain_agent")
    assert result.is_agent_project is True
    assert {fw.type for fw in result.frameworks} == {"langchain"}
    langchain = next(fw for fw in result.frameworks if fw.type == "langchain")
    assert langchain.confidence == "high"
    assert any("langchain import" in ev for ev in langchain.evidence)


def test_detects_crewai_sample() -> None:
    result = detect_workspace(SAMPLES / "simple_crewai_agent")
    assert result.is_agent_project is True
    assert {fw.type for fw in result.frameworks} == {"crewai"}


def test_detects_google_adk_sample_and_extracts_agent_name_literal() -> None:
    result = detect_workspace(SAMPLES / "google_adk_agent")
    assert result.is_agent_project is True
    assert any(fw.type == "google_adk" for fw in result.frameworks)
    # ADK sample defines `Agent(name="adk_support_agent", ...)` — must beat
    # the workspace dir name in the ranking.
    assert result.agent_name_candidates[0].source == "Agent_name_literal"
    assert result.agent_name_candidates[0].value == "adk_support_agent"


def test_detects_artifact_only_anthropic_sample() -> None:
    """Anthropic projects ship only artifacts (tools/anthropic-tools.json,
    policies/anthropic-policy.yaml). They have no .py imports and would be
    missed without the strong artifact-anchor rule (per C12)."""
    result = detect_workspace(SAMPLES / "simple_anthropic_agent")
    assert result.is_agent_project is True
    assert {fw.type for fw in result.frameworks} == {"anthropic"}
    anthropic = next(fw for fw in result.frameworks if fw.type == "anthropic")
    assert any("anthropic-tools.json" in ev for ev in anthropic.evidence)
    assert any("anthropic-policy.yaml" in ev for ev in anthropic.evidence)


def test_detects_openai_api_sample_as_openai_api_not_sdk() -> None:
    """OpenAI API artifact projects (openai-config.json + tools/policies/...)
    must classify as ``openai_api`` (artifact-based Messages API surface),
    NOT ``openai_agents_sdk`` (the Python @function_tool surface).

    Per v0.6 reviewer feedback: openai_api and openai_agents_sdk are
    distinct things in the manifest schema (manifest.openai_api block vs
    tool_sources[*].type == 'openai_agents_sdk') and detection must
    reflect that.
    """
    result = detect_workspace(SAMPLES / "simple_openai_api_agent")
    assert result.is_agent_project is True
    assert any(fw.type == "openai_api" for fw in result.frameworks)
    # Must NOT have been mislabeled as the SDK adapter.
    assert not any(fw.type == "openai_agents_sdk" for fw in result.frameworks)


def test_detects_artifact_only_openai_api_workspace(tmp_path: Path) -> None:
    """A workspace with only prompts/ and tools/openai-tools.json must
    register as an agent project so the canonical agent flow doesn't
    skip a repo that init can onboard. Regression for v0.6 reviewer
    feedback."""
    (tmp_path / "prompts").mkdir()
    (tmp_path / "tools").mkdir()
    (tmp_path / "prompts" / "support.md").write_text("you are helpful", encoding="utf-8")
    (tmp_path / "tools" / "openai-tools.json").write_text("[]", encoding="utf-8")
    result = detect_workspace(tmp_path)
    assert result.is_agent_project is True
    assert any(fw.type == "openai_api" for fw in result.frameworks)
    assert result.next_action.startswith("agents-shipgate init")


def test_clean_read_only_workspace_is_not_agent_project() -> None:
    """clean_read_only_agent has only a manifest + a tools.json file; that
    is a tool surface, not enough to say the *project* is an agent project."""
    result = detect_workspace(SAMPLES / "clean_read_only_agent")
    assert result.is_agent_project is False


def test_negative_workspace_detects_nothing(tmp_path: Path) -> None:
    """A repo with random Python that imports nothing framework-specific must
    not register as an agent project."""
    (tmp_path / "main.py").write_text(
        textwrap.dedent(
            """
            import json

            def main() -> None:
                print(json.dumps({"hi": "there"}))
            """
        ).strip(),
        encoding="utf-8",
    )
    result = detect_workspace(tmp_path)
    assert result.is_agent_project is False
    assert result.frameworks == []
    assert any(c.value == tmp_path.name for c in result.agent_name_candidates)


def test_detect_ignores_local_private_and_virtualenv_fixtures(tmp_path: Path) -> None:
    """Local agent state and package fixture installs must not pollute detect."""
    claude_agent = tmp_path / ".claude" / "worktrees" / "fixture" / "agent.py"
    claude_agent.parent.mkdir(parents=True)
    claude_agent.write_text(
        "from langchain.tools import tool\n\n@tool\ndef lookup():\n    return 'x'\n",
        encoding="utf-8",
    )

    private_agent = tmp_path / ".agents-private" / "copy" / "crew.py"
    private_agent.parent.mkdir(parents=True)
    private_agent.write_text(
        "from crewai import Agent\n\nAgent(role='support', goal='help')\n",
        encoding="utf-8",
    )

    venv_tools = (
        tmp_path
        / ".venv-py312"
        / "lib"
        / "python3.12"
        / "site-packages"
        / "agents_shipgate"
        / "_fixtures"
        / "simple_openai_api_agent"
        / "tools"
        / "openai-tools.json"
    )
    venv_tools.parent.mkdir(parents=True)
    venv_tools.write_text("[]", encoding="utf-8")

    generated_report = tmp_path / "agents-shipgate-reports" / "report.json"
    generated_report.parent.mkdir()
    generated_report.write_text('{"report_schema_version": "0.8"}', encoding="utf-8")

    result = detect_workspace(tmp_path)

    assert result.is_agent_project is False
    assert result.frameworks == []
    assert result.suggested_sources == []


def test_detect_excludes_common_fixture_dirs_by_default(tmp_path: Path) -> None:
    """Fixture corpora should not make an otherwise empty workspace look agentic."""
    for dirname in FIXTURE_SKIP_DIR_NAMES:
        _write_skipped_fixture_signals(tmp_path / dirname)

    result = detect_workspace(tmp_path)

    assert result.is_agent_project is False
    assert result.frameworks == []
    assert result.suggested_sources == []
    assert result.excluded_sources == []
    assert result.codex_plugin_candidates == []
    assert result.workspace_signals.python_file_count == 0


def test_detect_does_not_skip_workspace_because_parent_is_skipped_name(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / ".claude" / "worktrees" / "agent-review"
    workspace.mkdir(parents=True)
    (workspace / "agent.py").write_text(
        "from langchain.tools import tool\n\n@tool\ndef lookup():\n    return 'x'\n",
        encoding="utf-8",
    )

    result = detect_workspace(workspace)

    assert result.is_agent_project is True
    langchain = next(fw for fw in result.frameworks if fw.type == "langchain")
    assert langchain.candidate_files == ["agent.py"]


def test_detect_does_not_skip_workspace_named_fixtures(tmp_path: Path) -> None:
    workspace = tmp_path / "fixtures"
    workspace.mkdir()
    (workspace / "agent.py").write_text(
        "from langchain.tools import tool\n\n@tool\ndef lookup():\n    return 'x'\n",
        encoding="utf-8",
    )

    result = detect_workspace(workspace)

    assert result.is_agent_project is True
    langchain = next(fw for fw in result.frameworks if fw.type == "langchain")
    assert langchain.candidate_files == ["agent.py"]
    assert result.workspace_signals.python_file_count == 1


def test_detect_respects_gitignored_nested_agent_artifacts(tmp_path: Path) -> None:
    if not shutil.which("git"):
        pytest.skip("git is required for git-aware discovery regression coverage")

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text("ignored-agent/\n", encoding="utf-8")

    ignored_agent = tmp_path / "ignored-agent" / "agent.py"
    ignored_agent.parent.mkdir()
    ignored_agent.write_text(
        "from agents import Agent, function_tool\n\n"
        "@function_tool\n"
        "def refund_user():\n"
        "    return None\n\n"
        "Agent(name='ignored')\n",
        encoding="utf-8",
    )
    (tmp_path / "ignored-agent" / "openapi.yaml").write_text(
        "openapi: 3.1.0\ninfo:\n  title: ignored\n  version: '1.0'\npaths: {}\n",
        encoding="utf-8",
    )

    result = detect_workspace(tmp_path)

    assert result.is_agent_project is False
    assert result.frameworks == []
    assert result.suggested_sources == []


def test_detect_excludes_common_fixture_dirs_with_git_candidates(
    tmp_path: Path,
) -> None:
    if not shutil.which("git"):
        pytest.skip("git is required for git-aware discovery regression coverage")

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    _write_skipped_fixture_signals(tmp_path / "fixtures")

    result = detect_workspace(tmp_path)

    assert result.is_agent_project is False
    assert result.frameworks == []
    assert result.suggested_sources == []
    assert result.excluded_sources == []
    assert result.codex_plugin_candidates == []
    assert result.workspace_signals.python_file_count == 0


def test_pyproject_seeds_project_name_not_agent_name(tmp_path: Path) -> None:
    """pyproject [project].name → project_name_candidates, NOT
    agent_name_candidates (post-review correction)."""
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent(
            """
            [project]
            name = "shipgate-demo"
            version = "0.1.0"
            """
        ).strip(),
        encoding="utf-8",
    )
    result = detect_workspace(tmp_path)
    project_sources = {c.source for c in result.project_name_candidates}
    agent_sources = {c.source for c in result.agent_name_candidates}
    assert "pyproject" in project_sources
    assert "pyproject" not in agent_sources


def test_emits_next_action_for_detected_project() -> None:
    result = detect_workspace(SAMPLES / "simple_langchain_agent")
    assert result.next_action.startswith("agents-shipgate init")


def test_max_python_files_caps_walk(tmp_path: Path) -> None:
    """Cap defends large monorepos from unbounded AST parses."""
    for i in range(50):
        (tmp_path / f"m{i}.py").write_text("x = 1\n", encoding="utf-8")
    # cap below the file count: must not raise
    result = detect_workspace(tmp_path, max_python_files=5)
    assert isinstance(result, DetectResult)


def test_detect_result_serializes_cleanly() -> None:
    result = detect_workspace(SAMPLES / "simple_langchain_agent")
    payload = result.model_dump(mode="json")
    assert payload["is_agent_project"] is True
    assert isinstance(payload["frameworks"], list)
    assert isinstance(payload["agent_name_candidates"], list)


def test_symlink_loop_in_git_workspace_does_not_crash_detect(tmp_path: Path) -> None:
    """A symlink loop must skip the entry, never crash discovery.

    Found mining real history: stripe/ai ships a looping symlink
    (llm/ai-sdk/LICENSE), and ``Path.resolve()`` surfaces ELOOP as
    ``RuntimeError`` on CPython — which crashed ``detect`` (and therefore
    ``init`` cold-start) with a traceback. The git candidate path
    (``git ls-files -co``) is the one that lists symlinks.
    """
    if shutil.which("git") is None:
        pytest.skip("git unavailable")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    (tmp_path / "agent.py").write_text(
        textwrap.dedent(
            """
            from langchain_core.tools import tool

            @tool
            def lookup(query: str) -> str:
                \"\"\"Look up a document.\"\"\"
                return query
            """
        ),
        encoding="utf-8",
    )
    (tmp_path / "loop").symlink_to(tmp_path / "loop")

    result = detect_workspace(tmp_path)

    assert isinstance(result, DetectResult)
    assert result.is_agent_project is True


# --- Suggested-source parse probe ------------------------------------------
# Suggestion rules are filename globs and filenames lie: detect must only
# suggest files the real input adapters accept, and report the rest as
# excluded_sources with a reason. Regression context: a Cursor plugin
# mcp.json (mcpServers-style host config) matched `*mcp*.json`, got written
# by `init --write`, and made the very next `scan` exit 3.


def test_mcpservers_config_is_excluded_not_suggested(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "providers" / "cursor" / "plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "mcp.json").write_text(
        '{"mcpServers": {"stripe": {"command": "npx"}}}', encoding="utf-8"
    )
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "payments-mcp.json").write_text(
        '{"tools": [{"name": "create_payment_link", "description": '
        '"Create a payment link for checkout."}]}',
        encoding="utf-8",
    )
    result = detect_workspace(tmp_path)
    assert result.suggested_sources == [
        {"type": "mcp", "path": "tools/payments-mcp.json"}
    ]
    assert len(result.excluded_sources) == 1
    excluded = result.excluded_sources[0]
    assert excluded["type"] == "mcp"
    assert excluded["path"] == "providers/cursor/plugin/mcp.json"
    assert "mcpServers" in excluded["reason"]


def test_dot_mcp_json_stays_silently_skipped(tmp_path: Path) -> None:
    """The literal `.mcp.json` (Claude Code host config) was always skipped
    by name; it must not start showing up as excluded-source noise in every
    repo that has one."""
    (tmp_path / ".mcp.json").write_text(
        '{"mcpServers": {"docs": {"command": "npx"}}}', encoding="utf-8"
    )
    result = detect_workspace(tmp_path)
    assert result.suggested_sources == []
    assert result.excluded_sources == []


def test_corrupt_mcp_json_is_excluded_with_parse_reason(tmp_path: Path) -> None:
    (tmp_path / "notes-mcp.json").write_text("{not json", encoding="utf-8")
    result = detect_workspace(tmp_path)
    assert result.suggested_sources == []
    assert len(result.excluded_sources) == 1
    assert "Unable to parse input file" in result.excluded_sources[0]["reason"]
    # Reasons are workspace-relative so manifests/JSON stay deterministic.
    assert "notes-mcp.json" in result.excluded_sources[0]["reason"]


def test_swagger2_doc_is_excluded_from_openapi_suggestions(tmp_path: Path) -> None:
    """Swagger 2.0 documents match the `*swagger*` glob but the openapi
    adapter only accepts OpenAPI 3.x (`openapi:` version key) — same
    poison-manifest failure mode as the mcpServers case."""
    (tmp_path / "legacy-swagger.json").write_text(
        '{"swagger": "2.0", "info": {"title": "t", "version": "1"}, "paths": {}}',
        encoding="utf-8",
    )
    result = detect_workspace(tmp_path)
    assert result.suggested_sources == []
    assert len(result.excluded_sources) == 1
    excluded = result.excluded_sources[0]
    assert excluded["type"] == "openapi"
    assert "openapi" in excluded["reason"]


def test_wildcard_mcp_export_stays_suggested(tmp_path: Path) -> None:
    """Wildcard exposure (`tools: "*"`) is a shape the mcp adapter accepts;
    the probe must not tighten the suggestion rules beyond what scan parses."""
    (tmp_path / "everything-mcp.json").write_text('{"tools": "*"}', encoding="utf-8")
    result = detect_workspace(tmp_path)
    assert result.suggested_sources == [
        {"type": "mcp", "path": "everything-mcp.json"}
    ]
    assert result.excluded_sources == []


def test_mongodb_typescript_static_tool_idiom_is_a_named_mcp_source(
    tmp_path: Path,
) -> None:
    (tmp_path / "package.json").write_text(
        '{"name":"mongodb-mcp-server"}', encoding="utf-8"
    )
    tools = tmp_path / "src" / "tools"
    tools.mkdir(parents=True)
    (tools / "dropDatabase.ts").write_text(
        """
export class DropDatabaseTool extends MongoDBToolBase {
  static toolName = "drop-database";
  static operationType: OperationType = "delete";
}
""",
        encoding="utf-8",
    )
    (tools / "deleteMany.ts").write_text(
        """
export class DeleteManyTool extends MongoDBToolBase {
  static toolName = "delete-many";
  static operationType: OperationType = "delete";
}
""",
        encoding="utf-8",
    )

    result = detect_workspace(tmp_path)

    assert result.is_agent_project is True
    assert {framework.type for framework in result.frameworks} == {"mcp_server"}
    assert result.suggested_sources == [
        {
            "type": "mcp",
            "path": "src/tools",
            "idiom": "typescript_static_tool_v1",
        }
    ]
    evidence = result.frameworks[0].evidence
    assert any("2 literal tool registration(s)" in row for row in evidence)


def test_grafana_go_musttool_idiom_is_detected(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text(
        "module github.com/grafana/mcp-grafana\n", encoding="utf-8"
    )
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "incident.go").write_text(
        """
package tools
var UpdateIncident = mcpgrafana.MustTool(
  "update_incident", "Update an incident", updateIncident,
)
func RegisterTools(mcp *server.MCPServer) {
  UpdateIncident.Register(mcp)
}
""",
        encoding="utf-8",
    )

    result = detect_workspace(tmp_path)

    assert result.is_agent_project is True
    assert result.suggested_sources == [
        {"type": "mcp", "path": "tools/incident.go", "idiom": "go_musttool_v1"}
    ]


def test_generic_typescript_sdk_registration_is_detected(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"dependencies":{"@modelcontextprotocol/sdk":"^1.0.0"}}',
        encoding="utf-8",
    )
    (tmp_path / "server.ts").write_text(
        'server.registerTool("lookup", {description: "Look up"}, lookup);\n'
        'server.tool("legacy_lookup", "Look up", legacyLookup);\n',
        encoding="utf-8",
    )

    result = detect_workspace(tmp_path)

    assert result.is_agent_project is True
    assert result.suggested_sources == [
        {
            "type": "mcp",
            "path": "server.ts",
            "idiom": "typescript_mcp_sdk_v1",
        }
    ]


def test_mcp_source_import_is_an_independent_discovery_marker(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"name":"ordinary-package-name"}', encoding="utf-8"
    )
    (tmp_path / "server.ts").write_text(
        """
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
const server = new McpServer({name: "catalog", version: "1"});
server.registerTool("lookup", {}, lookup);
""",
        encoding="utf-8",
    )

    result = detect_workspace(tmp_path)

    assert result.suggested_sources == [
        {
            "type": "mcp",
            "path": "server.ts",
            "idiom": "typescript_mcp_sdk_v1",
        }
    ]


def test_dynamic_only_mcp_registration_is_detected_and_routed_to_ledger(
    tmp_path: Path,
) -> None:
    (tmp_path / "package.json").write_text(
        '{"dependencies":{"@modelcontextprotocol/sdk":"^1.0.0"}}',
        encoding="utf-8",
    )
    (tmp_path / "server.ts").write_text(
        "server.registerTool(toolNameFromConfig(), {}, handler);\n",
        encoding="utf-8",
    )

    result = detect_workspace(tmp_path)

    assert result.is_agent_project is True
    assert result.suggested_sources == [
        {
            "type": "mcp",
            "path": "server.ts",
            "idiom": "typescript_mcp_sdk_v1",
        }
    ]
    mcp = next(item for item in result.frameworks if item.type == "mcp_server")
    assert any("1 runtime-built name(s) omitted" in row for row in mcp.evidence)


def test_checked_in_mcp_export_is_preferred_over_code_in_same_project(
    tmp_path: Path,
) -> None:
    (tmp_path / "package.json").write_text(
        '{"name":"published-mcp"}', encoding="utf-8"
    )
    (tmp_path / "server.ts").write_text(
        'server.registerTool("lookup", {}, lookup);', encoding="utf-8"
    )
    (tmp_path / "published-mcp.json").write_text(
        '{"tools":[{"name":"lookup","annotations":{"readOnlyHint":true}}]}',
        encoding="utf-8",
    )

    result = detect_workspace(tmp_path)

    assert result.is_agent_project is True
    assert result.suggested_sources == [
        {"type": "mcp", "path": "published-mcp.json"}
    ]
    mcp = next(item for item in result.frameworks if item.type == "mcp_server")
    assert any("preferred source" in row for row in mcp.evidence)


def test_checked_in_mcp_export_does_not_hide_a_distinct_code_server(
    tmp_path: Path,
) -> None:
    (tmp_path / "package.json").write_text(
        '{"name":"two-mcp-servers"}', encoding="utf-8"
    )
    (tmp_path / "server.ts").write_text(
        'server.registerTool("source-only", {}, handler);', encoding="utf-8"
    )
    (tmp_path / "published-mcp.json").write_text(
        '{"tools":[{"name":"export-only"}]}', encoding="utf-8"
    )

    result = detect_workspace(tmp_path)

    assert result.suggested_sources == [
        {"type": "mcp", "path": "published-mcp.json"},
        {
            "type": "mcp",
            "path": "server.ts",
            "idiom": "typescript_mcp_sdk_v1",
        },
    ]


def test_github_per_tool_snapshots_are_high_confidence_and_preferred(
    tmp_path: Path,
) -> None:
    (tmp_path / "go.mod").write_text(
        "module github.com/github/github-mcp-server\n", encoding="utf-8"
    )
    snapshots = tmp_path / "pkg" / "github" / "__toolsnaps__"
    snapshots.mkdir(parents=True)
    (snapshots / "delete_repository.snap").write_text(
        '{"name":"delete_repository","inputSchema":{"type":"object"}}',
        encoding="utf-8",
    )
    (tmp_path / "server.ts").write_text(
        'server.registerTool("delete_repository", {}, handler);', encoding="utf-8"
    )

    result = detect_workspace(tmp_path)

    assert result.suggested_sources == [
        {
            "type": "mcp",
            "path": "pkg/github/__toolsnaps__",
            "idiom": "mcp_tool_snapshot_v1",
        }
    ]
    mcp = next(item for item in result.frameworks if item.type == "mcp_server")
    assert mcp.confidence == "high"
    assert any("checked-in per-tool MCP snapshot" in row for row in mcp.evidence)


def test_typescript_regex_registration_text_does_not_activate_detect(
    tmp_path: Path,
) -> None:
    (tmp_path / "package.json").write_text(
        '{"dependencies":{"@modelcontextprotocol/sdk":"^1"}}', encoding="utf-8"
    )
    (tmp_path / "server.ts").write_text(
        r'const pattern = /server\.registerTool\("phantom", \{\}, handler\)/;',
        encoding="utf-8",
    )

    result = detect_workspace(tmp_path)

    assert result.is_agent_project is False
    assert result.suggested_sources == []


def test_unbalanced_mcp_source_is_detected_as_an_incomplete_idiom(
    tmp_path: Path,
) -> None:
    (tmp_path / "package.json").write_text(
        '{"dependencies":{"@modelcontextprotocol/sdk":"^1"}}', encoding="utf-8"
    )
    (tmp_path / "server.ts").write_text(
        'server.registerTool("lost", {}, handler;', encoding="utf-8"
    )

    result = detect_workspace(tmp_path)

    assert result.suggested_sources == [
        {
            "type": "mcp",
            "path": "server.ts",
            "idiom": "typescript_mcp_sdk_v1",
        }
    ]
    mcp = next(item for item in result.frameworks if item.type == "mcp_server")
    assert any("static recognition gap" in row for row in mcp.evidence)


def test_mcp_code_shapes_without_independent_mcp_marker_do_not_activate_detect(
    tmp_path: Path,
) -> None:
    (tmp_path / "package.json").write_text(
        '{"name":"ordinary-widget-service"}', encoding="utf-8"
    )
    (tmp_path / "widget.ts").write_text(
        """
// MCP may be evaluated someday; prose is not an import/module marker.
const protocolName = "mcp";
class Widget { static toolName = "format-widget"; }
server.tool("format-widget", formatWidget);
""",
        encoding="utf-8",
    )
    (tmp_path / "registry.go").write_text(
        'package widgets\nfunc mount() { registry.AddTool(&Widget{Name: "format"}) }',
        encoding="utf-8",
    )

    result = detect_workspace(tmp_path)

    assert result.is_agent_project is False
    assert result.suggested_sources == []
    assert all(item.type != "mcp_server" for item in result.frameworks)


def test_mcp_code_discovery_aggregate_byte_cap_is_routed_and_accounted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agents_shipgate.cli.discovery import signals

    (tmp_path / "package.json").write_text(
        '{"name":"bounded-mcp-server"}', encoding="utf-8"
    )
    (tmp_path / "server.ts").write_text(
        'server.registerTool("lookup", {}, lookup);', encoding="utf-8"
    )
    monkeypatch.setattr(signals, "MAX_MCP_CODE_TOTAL_BYTES", 16)

    result = detect_workspace(tmp_path)

    assert result.is_agent_project is False
    assert result.suggested_sources == []
    cap = next(
        item for item in result.excluded_sources if item.get("reason_code") == "walk_capped"
    )
    assert cap["path"] == "."
    assert "aggregate" in cap["reason"]
    assert "project rather than the enclosing monorepo" in result.next_action
    row = next(item for item in result.surface_exclusions.entries if item.reason == "walk_capped")
    assert row.accounting == "route_blocked"


def test_mcp_code_discovery_per_file_byte_cap_is_routed_and_accounted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agents_shipgate.cli.discovery import signals

    (tmp_path / "package.json").write_text(
        '{"name":"bounded-mcp-server"}', encoding="utf-8"
    )
    (tmp_path / "server.ts").write_text(
        'server.registerTool("lookup", {}, lookup);', encoding="utf-8"
    )
    monkeypatch.setattr(signals, "MAX_MCP_CODE_FILE_BYTES", 16)

    result = detect_workspace(tmp_path)

    assert result.is_agent_project is False
    assert result.suggested_sources == []
    cap = next(
        item for item in result.excluded_sources if item.get("reason_code") == "walk_capped"
    )
    assert "per-file limit" in cap["reason"]
    assert "project rather than the enclosing monorepo" in result.next_action


# --- Agent-name candidate ranking -------------------------------------------
#
# Two bugs, one cause: candidates used to be emitted in file-then-AST order
# with no ranking, and every consumer took the first one. #320 is the
# candidate-*quality* face of that (a one-character test literal became a
# repository's declared identity); #324 is the candidate-*hierarchy* face
# (a Salesforce worker outranked the coordinator that owns it). Both are
# pinned here against the shape of the repository that reported them.


def _write_adk_root_agent_project(root: Path) -> None:
    """The google/adk-samples#1745 shape: two literal sub-agents, a
    coordinator whose name comes from an adjacent config module, and an
    explicit ``App(root_agent=…)`` binding."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.py").write_text(
        textwrap.dedent(
            """
            import os

            AGENT_NAME = os.environ.get("AGENT_NAME", "SmartCloserAgent")
            """
        ).lstrip(),
        encoding="utf-8",
    )
    (root / "agent.py").write_text(
        textwrap.dedent(
            """
            from config import AGENT_NAME
            from google.adk.agents import LlmAgent
            from google.adk.apps import App
            from google.adk.tools import FunctionTool

            salesforce_agent = LlmAgent(name="SalesforceAgent")
            sap_agent = LlmAgent(name="SapAgent")

            root_agent = LlmAgent(
                name=AGENT_NAME,
                sub_agents=[salesforce_agent, sap_agent],
                tools=[FunctionTool(func=lambda: None)],
            )

            app = App(name="smart_closer_app", root_agent=root_agent)
            """
        ).lstrip(),
        encoding="utf-8",
    )


def test_adk_app_root_agent_outranks_first_sub_agent(tmp_path: Path) -> None:
    """#324: the application root wins over the sub-agent the walk reaches
    first. Both worker names are plausible agent names — that is exactly why
    picking one is the failure that survives review."""
    _write_adk_root_agent_project(tmp_path / "smart_closer")
    result = detect_workspace(tmp_path / "smart_closer")

    selected = select_agent_name(result.agent_name_candidates)
    assert selected is not None
    assert selected.value == "SmartCloserAgent"
    assert selected.role == "root_agent"
    # The workers stay in the list — an agent may still override the choice —
    # but they rank below the coordinator that declares them.
    values = [c.value for c in result.agent_name_candidates]
    assert values[0] == "SmartCloserAgent"
    assert {"SalesforceAgent", "SapAgent"} <= set(values)
    roles = {c.value: c.role for c in result.agent_name_candidates}
    assert roles["SalesforceAgent"] == "sub_agent"
    assert roles["SapAgent"] == "sub_agent"


def test_adk_root_name_resolves_one_hop_through_adjacent_config(
    tmp_path: Path,
) -> None:
    """#324 step 3: ``name=AGENT_NAME`` imported from a sibling module
    resolves statically. The rendered manifest must carry the resolved name,
    and the rationale must say the value came from an environment default so
    a reviewer knows it can be overridden at runtime."""
    _write_adk_root_agent_project(tmp_path / "smart_closer")
    result = detect_workspace(tmp_path / "smart_closer")

    root = result.agent_name_candidates[0]
    assert root.value == "SmartCloserAgent"
    assert any("config.py" in reason for reason in root.rationale)
    assert any("overridable at runtime" in reason for reason in root.rationale)
    assert (
        "agent:\n  name: SmartCloserAgent"
        in render_auto_manifest(tmp_path / "smart_closer", result).text
    )


def test_unresolvable_root_name_blocks_selection_entirely(tmp_path: Path) -> None:
    """#324's fail-closed criterion, and the sharpest case for it.

    An f-string root name cannot be resolved without running user code. The
    tempting behaviour is to drop the root and let the remaining candidates
    rank — but everything remaining is, by construction, *not* the root, so
    that silently declares a worker as the reviewed identity. When a
    declared root cannot be resolved, nothing is selectable.
    """
    project = tmp_path / "dynamic"
    project.mkdir()
    (project / "agent.py").write_text(
        textwrap.dedent(
            """
            import os
            from google.adk.agents import LlmAgent
            from google.adk.apps import App

            worker = LlmAgent(name="WorkerAgent")
            root_agent = LlmAgent(
                name=f"{os.environ['TIER']}-coordinator",
                sub_agents=[worker],
            )
            app = App(name="dynamic_app", root_agent=root_agent)
            """
        ).lstrip(),
        encoding="utf-8",
    )
    result = detect_workspace(project)
    values = [c.value for c in result.agent_name_candidates]
    assert not any("coordinator" in value for value in values)
    assert select_agent_name(result.agent_name_candidates) is None
    worker = next(c for c in result.agent_name_candidates if c.value == "WorkerAgent")
    assert worker.selectable is False
    assert any("application root" in reason for reason in worker.rationale)
    assert "name: CHANGE_ME" in render_auto_manifest(project, result).text


def test_root_reference_resolves_to_the_reaching_assignment(tmp_path: Path) -> None:
    """`App(root_agent=agent)` names the binding live at that point, not
    every construction that ever used the identifier. Classifying both as
    roots put source order back in charge of the tie and picked the
    overwritten one."""
    project = tmp_path / "rebound"
    project.mkdir()
    (project / "agent.py").write_text(
        textwrap.dedent(
            """
            from google.adk.agents import Agent
            from google.adk.apps import App

            agent = Agent(name="OldWorker")
            agent = Agent(name="ActualRoot")
            app = App(name="a", root_agent=agent)
            """
        ).lstrip(),
        encoding="utf-8",
    )
    result = detect_workspace(project)
    roles = {c.value: c.role for c in result.agent_name_candidates}
    assert roles["ActualRoot"] == "root_agent"
    assert roles["OldWorker"] == "agent"
    selected = select_agent_name(result.agent_name_candidates)
    assert selected is not None and selected.value == "ActualRoot"


def test_function_local_root_agent_is_not_an_application_root(tmp_path: Path) -> None:
    """The ADK convention is about the *module* symbol `adk run` imports. A
    local variable that happens to be spelled `root_agent` is just a local,
    and promoting it outranks every real module-level agent."""
    project = tmp_path / "local"
    project.mkdir()
    (project / "agent.py").write_text(
        textwrap.dedent(
            """
            from google.adk.agents import Agent

            def build():
                root_agent = Agent(name="LocalHelper")
                return root_agent

            top = Agent(name="RealTopLevel")
            """
        ).lstrip(),
        encoding="utf-8",
    )
    result = detect_workspace(project)
    roles = {c.value: c.role for c in result.agent_name_candidates}
    assert roles["LocalHelper"] == "agent"
    assert roles["RealTopLevel"] == "agent"
    assert not any(c.role == "root_agent" for c in result.agent_name_candidates)


def test_root_reference_to_an_undefined_symbol_fails_closed(tmp_path: Path) -> None:
    """A root bound to something no single construction defines is still a
    declared root. Selection declines rather than falling through."""
    project = tmp_path / "dangling"
    project.mkdir()
    (project / "agent.py").write_text(
        textwrap.dedent(
            """
            from google.adk.agents import Agent
            from google.adk.apps import App
            from factory import build_root

            worker = Agent(name="WorkerAgent")
            app = App(name="a", root_agent=build_root())
            """
        ).lstrip(),
        encoding="utf-8",
    )
    result = detect_workspace(project)
    assert select_agent_name(result.agent_name_candidates) is None


def test_rebound_constant_is_never_resolved(tmp_path: Path) -> None:
    """`NAME = "Old"` then `NAME = "Current"` passes `Current` at runtime.
    Taking the first static assignment asserts a value Python never uses, so
    any second binding of the symbol leaves it unresolved."""
    project = tmp_path / "rebound_const"
    project.mkdir()
    (project / "agent.py").write_text(
        textwrap.dedent(
            """
            from google.adk.agents import Agent

            NAME = "OldAgent"
            NAME = "CurrentAgent"
            root_agent = Agent(name=NAME)
            """
        ).lstrip(),
        encoding="utf-8",
    )
    result = detect_workspace(project)
    values = {c.value for c in result.agent_name_candidates}
    assert "OldAgent" not in values and "CurrentAgent" not in values
    assert select_agent_name(result.agent_name_candidates) is None


def test_conditionally_rebound_constant_is_never_resolved(tmp_path: Path) -> None:
    """The same rule covers the write a module-body-only scan cannot see: a
    conditional reassignment is a second binding, so the top-level literal
    stops being authoritative."""
    project = tmp_path / "conditional_const"
    project.mkdir()
    (project / "agent.py").write_text(
        textwrap.dedent(
            """
            import os
            from google.adk.agents import Agent

            NAME = "DefaultAgent"
            if os.environ.get("TIER"):
                NAME = "TierAgent"
            root_agent = Agent(name=NAME)
            """
        ).lstrip(),
        encoding="utf-8",
    )
    result = detect_workspace(project)
    assert "DefaultAgent" not in {c.value for c in result.agent_name_candidates}
    assert select_agent_name(result.agent_name_candidates) is None


def test_child_keywords_only_count_on_agent_constructors(tmp_path: Path) -> None:
    """`sub_agents=`/`handoffs=` mean "these are my children" only when the
    surrounding call builds an agent. Reading them off any call let an
    unrelated helper demote a coordinator and hand the identity to a
    worker."""
    project = tmp_path / "unrelated"
    project.mkdir()
    (project / "agent.py").write_text(
        textwrap.dedent(
            """
            from agents import Agent

            coordinator = Agent(name="CoordinatorAgent")
            worker = Agent(name="SomeWorkerAgent")
            configure(handoffs=[coordinator])
            """
        ).lstrip(),
        encoding="utf-8",
    )
    result = detect_workspace(project)
    roles = {c.value: c.role for c in result.agent_name_candidates}
    assert roles["CoordinatorAgent"] == "agent"
    assert roles["SomeWorkerAgent"] == "agent"


def test_helper_local_import_does_not_resolve_a_module_level_name(
    tmp_path: Path,
) -> None:
    """Import bindings are per scope. A helper importing `AGENT_NAME` from
    somewhere else must not supply the value a module-level construction
    reads from a different module."""
    project = tmp_path / "scoped_import"
    project.mkdir()
    (project / "other.py").write_text('AGENT_NAME = "HelperAgent"\n', encoding="utf-8")
    (project / "agent.py").write_text(
        textwrap.dedent(
            """
            from google.adk.agents import Agent

            def helper():
                from other import AGENT_NAME
                return AGENT_NAME

            root_agent = Agent(name=AGENT_NAME)
            """
        ).lstrip(),
        encoding="utf-8",
    )
    result = detect_workspace(project)
    assert "HelperAgent" not in {c.value for c in result.agent_name_candidates}
    assert select_agent_name(result.agent_name_candidates) is None


def test_aliased_import_resolves_the_imported_name(tmp_path: Path) -> None:
    """`from config import AGENT_NAME as NAME` defines `AGENT_NAME` in the
    target module; looking up the alias there finds nothing."""
    project = tmp_path / "aliased"
    project.mkdir()
    (project / "config.py").write_text('AGENT_NAME = "AliasedAgent"\n', encoding="utf-8")
    (project / "agent.py").write_text(
        textwrap.dedent(
            """
            from config import AGENT_NAME as NAME
            from google.adk.agents import Agent

            root_agent = Agent(name=NAME)
            """
        ).lstrip(),
        encoding="utf-8",
    )
    result = detect_workspace(project)
    selected = select_agent_name(result.agent_name_candidates)
    assert selected is not None and selected.value == "AliasedAgent"


def test_conflicting_import_roots_leave_the_name_unresolved(tmp_path: Path) -> None:
    """`from config import AGENT_NAME` resolves against the agent directory
    or the workspace root depending on `sys.path`. When both exist in the
    workspace and disagree, which one Python picks is not ours to assume."""
    project = tmp_path / "ambiguous"
    (project / "pkg").mkdir(parents=True)
    (project / "config.py").write_text('AGENT_NAME = "WorkspaceRoot"\n', encoding="utf-8")
    (project / "pkg" / "config.py").write_text(
        'AGENT_NAME = "SiblingRoot"\n', encoding="utf-8"
    )
    (project / "pkg" / "agent.py").write_text(
        textwrap.dedent(
            """
            from config import AGENT_NAME
            from google.adk.agents import Agent

            root_agent = Agent(name=AGENT_NAME)
            """
        ).lstrip(),
        encoding="utf-8",
    )
    result = detect_workspace(project)
    values = {c.value for c in result.agent_name_candidates}
    assert "SiblingRoot" not in values and "WorkspaceRoot" not in values
    assert select_agent_name(result.agent_name_candidates) is None


def test_one_character_literal_never_becomes_the_agent_name(tmp_path: Path) -> None:
    """#320, in the shape usestrix/strix reported it: every ``Agent(name=…)``
    literal in the repository lives in a test, and the one the walk reaches
    first is a single character. ``t`` must be unselectable, and the name the
    project name independently corroborates must win."""
    project = tmp_path / "strix"
    tests_dir = project / "tests"
    tests_dir.mkdir(parents=True)
    (project / "runner.py").write_text(
        "from agents import Agent\n", encoding="utf-8"
    )
    # File order matters: `t` is encountered first, which used to be the
    # entire selection policy.
    (tests_dir / "test_aaa_streaming.py").write_text(
        'from agents import Agent\n\nagent = Agent(name="t")\n', encoding="utf-8"
    )
    (tests_dir / "test_zzz_recovery.py").write_text(
        'from agents import Agent\n\nagent = Agent(name="Strix")\n', encoding="utf-8"
    )

    result = detect_workspace(project)
    by_value = {c.value: c for c in result.agent_name_candidates}
    assert by_value["t"].selectable is False
    assert any("context-poor" in reason for reason in by_value["t"].rationale)

    selected = select_agent_name(result.agent_name_candidates)
    assert selected is not None and selected.value == "Strix"
    assert any("corroborated" in reason for reason in selected.rationale)


def test_generic_placeholder_name_fails_closed_to_change_me(tmp_path: Path) -> None:
    """When the only literal is scaffolding, ``init`` must leave the
    CHANGE_ME placeholder and ask for review rather than declare
    ``agent`` as the reviewed identity."""
    project = tmp_path / "scaffold"
    project.mkdir()
    (project / "main.py").write_text(
        'from agents import Agent, function_tool\n\n'
        '@function_tool\ndef ping() -> str:\n    return "pong"\n\n'
        'agent = Agent(name="agent", tools=[ping])\n',
        encoding="utf-8",
    )
    result = detect_workspace(project)
    assert select_agent_name(result.agent_name_candidates) is None
    assert all(not c.selectable for c in result.agent_name_candidates)
    assert "name: CHANGE_ME" in render_auto_manifest(project, result).text


def test_test_declared_name_ranks_below_product_declared_name(tmp_path: Path) -> None:
    """A name that only a test declares is a fixture name. It stays a
    candidate — often it is the real one — but product code outranks it."""
    project = tmp_path / "svc"
    (project / "tests").mkdir(parents=True)
    (project / "service.py").write_text(
        'from agents import Agent\n\nagent = Agent(name="billing-assistant")\n',
        encoding="utf-8",
    )
    (project / "tests" / "test_service.py").write_text(
        'from agents import Agent\n\nfixture = Agent(name="alpha-fixture")\n',
        encoding="utf-8",
    )
    result = detect_workspace(project)
    assert [c.value for c in result.agent_name_candidates][:2] == [
        "billing-assistant",
        "alpha-fixture",
    ]
    fixture = next(c for c in result.agent_name_candidates if c.value == "alpha-fixture")
    assert any("test code" in reason for reason in fixture.rationale)


def test_workspace_dir_candidate_is_never_selectable(tmp_path: Path) -> None:
    """Reported for reference only. ``init`` writing the directory name as
    ``agent.name`` would be asserting an identity nothing declared."""
    project = tmp_path / "some_workspace"
    project.mkdir()
    result = detect_workspace(project)
    assert [c.value for c in result.agent_name_candidates] == ["some_workspace"]
    assert result.agent_name_candidates[0].selectable is False
    assert result.agent_name_candidates[0].role == "workspace_dir"
    assert select_agent_name(result.agent_name_candidates) is None


def test_constant_resolution_does_not_chain_across_modules(tmp_path: Path) -> None:
    """One hop, not a graph walk. A constant that is itself a name in the
    imported module stays unresolved — following it is the first step toward
    partially evaluating user code."""
    project = tmp_path / "chained"
    project.mkdir()
    (project / "base.py").write_text('REAL_NAME = "DeepAgent"\n', encoding="utf-8")
    (project / "config.py").write_text(
        "from base import REAL_NAME\n\nAGENT_NAME = REAL_NAME\n", encoding="utf-8"
    )
    (project / "agent.py").write_text(
        textwrap.dedent(
            """
            from config import AGENT_NAME
            from google.adk.agents import Agent

            root_agent = Agent(name=AGENT_NAME)
            """
        ).lstrip(),
        encoding="utf-8",
    )
    result = detect_workspace(project)
    assert "DeepAgent" not in {c.value for c in result.agent_name_candidates}
    assert select_agent_name(result.agent_name_candidates) is None


def test_constant_lookup_stays_inside_the_workspace(tmp_path: Path) -> None:
    """A module resolved outside the selected workspace is never read, even
    when the import would resolve there on ``sys.path``."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "config.py").write_text('AGENT_NAME = "OutsideAgent"\n', encoding="utf-8")
    project = tmp_path / "inside"
    project.mkdir()
    (project / "agent.py").write_text(
        textwrap.dedent(
            """
            from config import AGENT_NAME
            from google.adk.agents import Agent

            root_agent = Agent(name=AGENT_NAME)
            """
        ).lstrip(),
        encoding="utf-8",
    )
    result = detect_workspace(project)
    assert "OutsideAgent" not in {c.value for c in result.agent_name_candidates}


def test_detect_human_output_reports_what_init_will_write(tmp_path: Path) -> None:
    """`detect`'s human line and `init`'s manifest must not disagree.
    Printing candidate zero regardless of selectability told a reader the
    agent was named `t` while `init` wrote CHANGE_ME."""
    from typer.testing import CliRunner

    from agents_shipgate.cli.main import app

    project = tmp_path / "scaffold"
    project.mkdir()
    (project / "main.py").write_text(
        "from agents import Agent, function_tool\n\n"
        "@function_tool\ndef ping() -> str:\n    return 'pong'\n\n"
        'agent = Agent(name="t", tools=[ping])\n',
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["detect", "--workspace", str(project)])
    assert result.exit_code == 0, result.output
    assert "Agent name candidate: t " not in result.output
    assert "init will write CHANGE_ME" in result.output
    assert "context-poor" in result.output


# --- Round-2 review: reading Python name binding, or declining ---------------
#
# The first ranking read binding with a flattened walk and first-write-wins
# heuristics. Each test below is a case where that produced a confident,
# wrong identity. The shared rule they enforce: resolve it the way Python
# does, or say you cannot.


def _adk(root: Path, body: str, *, name: str = "agent.py") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    return root


def test_root_name_symbol_that_fails_resolution_blocks_selection(
    tmp_path: Path,
) -> None:
    """A root whose name is a symbol that never resolves is as unresolved as
    one whose name is an f-string. Dropping the root quietly and letting the
    remaining worker rank is the #324 failure wearing a different hat."""
    project = _adk(
        tmp_path / "sym",
        """
        from google.adk.agents import Agent

        NAME = "Old"
        NAME = "Current"
        worker = Agent(name="WorkerAgent")
        root_agent = Agent(name=NAME, sub_agents=[worker])
        """,
    )
    result = detect_workspace(project)
    assert select_agent_name(result.agent_name_candidates) is None
    worker = next(c for c in result.agent_name_candidates if c.value == "WorkerAgent")
    assert worker.selectable is False


def test_conditionally_assigned_root_is_ambiguous(tmp_path: Path) -> None:
    """Either arm of an `if/else` can execute. Taking the lexically later
    one is a guess dressed as an answer, so the identity fails closed."""
    project = _adk(
        tmp_path / "branch",
        """
        import os
        from google.adk.agents import Agent
        from google.adk.apps import App

        if os.environ.get("TIER"):
            root = Agent(name="BranchOne")
        else:
            root = Agent(name="BranchTwo")
        app = App(name="a", root_agent=root)
        """,
    )
    result = detect_workspace(project)
    assert select_agent_name(result.agent_name_candidates) is None
    assert not any(c.role == "root_agent" for c in result.agent_name_candidates)


def test_closure_reference_resolves_to_the_enclosing_binding(tmp_path: Path) -> None:
    """A free name in a nested function resolves against the enclosing
    function before the module. Falling straight through to the module
    binding reads a different object than the code does."""
    project = _adk(
        tmp_path / "closure",
        """
        from google.adk.agents import Agent
        from google.adk.apps import App

        root = Agent(name="ModuleLevelAgent")

        def outer():
            root = Agent(name="EnclosingAgent")

            def inner():
                return App(name="a", root_agent=root)

            return inner
        """,
    )
    result = detect_workspace(project)
    roles = {c.value: c.role for c in result.agent_name_candidates}
    assert roles["EnclosingAgent"] == "root_agent"
    assert roles["ModuleLevelAgent"] == "agent"


def test_later_non_agent_write_retires_the_previous_root(tmp_path: Path) -> None:
    """`root_agent = build_root()` after `root_agent = Agent(...)` means the
    earlier construction is no longer the runtime root. A model that only
    records agent constructions cannot see that it stopped being one."""
    project = _adk(
        tmp_path / "retired",
        """
        from google.adk.agents import Agent
        from factory import build_root

        root_agent = Agent(name="StaleRoot")
        root_agent = build_root()
        """,
    )
    result = detect_workspace(project)
    assert select_agent_name(result.agent_name_candidates) is None
    stale = next(c for c in result.agent_name_candidates if c.value == "StaleRoot")
    assert stale.role != "root_agent"
    assert stale.selectable is False


def test_shadowed_getenv_cannot_fabricate_an_identity(tmp_path: Path) -> None:
    """Matching the callee spelling is not proof of provenance. A local
    `getenv` returning something else would have its fallback lifted out as
    the agent identity."""
    project = _adk(
        tmp_path / "shadowed",
        """
        from google.adk.agents import Agent

        def getenv(key, fallback):
            return "RuntimeAgent"

        AGENT_NAME = getenv("AGENT_NAME", "FabricatedAgent")
        root_agent = Agent(name=AGENT_NAME)
        """,
    )
    result = detect_workspace(project)
    assert "FabricatedAgent" not in {c.value for c in result.agent_name_candidates}
    assert select_agent_name(result.agent_name_candidates) is None


def test_stdlib_getenv_alias_still_resolves(tmp_path: Path) -> None:
    """Proving provenance must not mean only recognising one spelling:
    `import os as operating_system` is still the stdlib module."""
    project = _adk(
        tmp_path / "aliased_os",
        """
        import os as operating_system
        from google.adk.agents import Agent

        AGENT_NAME = operating_system.getenv("AGENT_NAME", "AliasedOsAgent")
        root_agent = Agent(name=AGENT_NAME)
        """,
    )
    result = detect_workspace(project)
    selected = select_agent_name(result.agent_name_candidates)
    assert selected is not None and selected.value == "AliasedOsAgent"


def test_product_origin_outranks_every_test_hierarchy_bonus(tmp_path: Path) -> None:
    """The documented contract is that product code outranks test code —
    full stop, not "unless the test one happens to be an App root"."""
    project = tmp_path / "origin"
    (project / "tests").mkdir(parents=True)
    (project / "service.py").write_text(
        "from google.adk.agents import Agent\n\n"
        'helper = Agent(name="ProductionAgent")\n',
        encoding="utf-8",
    )
    (project / "tests" / "test_fixture.py").write_text(
        "from google.adk.agents import Agent\n"
        "from google.adk.apps import App\n\n"
        'root_agent = Agent(name="TestFixtureRoot")\n'
        'app = App(name="a", root_agent=root_agent)\n',
        encoding="utf-8",
    )
    result = detect_workspace(project)
    selected = select_agent_name(result.agent_name_candidates)
    assert selected is not None and selected.value == "ProductionAgent"


def test_origin_penalty_dominates_the_other_signals_by_construction(
    tmp_path: Path,
) -> None:
    """Pins the arithmetic the contract rests on. If a future signal widens
    the spread past ORIGIN_TEST_PENALTY, a test fixture can outrank product
    code again and only this assertion would notice."""
    from agents_shipgate.cli.discovery import signals

    best_test_score = (
        1.0
        + signals.ROOT_AGENT_BONUS
        + signals.CORROBORATION_BONUS
        - signals.ORIGIN_TEST_PENALTY
    )
    worst_product_score = 1.0 - signals.SUB_AGENT_PENALTY
    assert best_test_score < worst_product_score, (
        "ORIGIN_TEST_PENALTY must exceed the whole spread of the hierarchy "
        "and corroboration signals, or 'product code outranks test code' "
        "stops being true for the best-placed fixture."
    )


def test_class_body_binding_is_not_visible_to_a_method(tmp_path: Path) -> None:
    """Class bodies bind names but are not in the closure lookup chain — a
    method referencing one raises `NameError`. Walking through them would
    resolve the root to an object Python never supplies."""
    project = _adk(
        tmp_path / "classscope",
        """
        from google.adk.agents import Agent
        from google.adk.apps import App

        class Registry:
            root = Agent(name="ClassBodyAgent")

            def build(self):
                return App(name="a", root_agent=root)
        """,
    )
    result = detect_workspace(project)
    roles = {c.value: c.role for c in result.agent_name_candidates}
    assert roles["ClassBodyAgent"] == "agent", (
        "a class-body binding must not satisfy a method's free-name "
        "reference; Python raises NameError there"
    )
    assert select_agent_name(result.agent_name_candidates) is None


# --- Round-3 review: the binding table is the answer, or there isn't one ----
#
# Each case below produced a confident, wrong identity because recognition
# trusted a spelling, or because a binding form was invisible to the model.


def test_shadowed_constructor_does_not_fabricate_an_identity(tmp_path: Path) -> None:
    """A spelling is not provenance. With `Agent` bound to a local function
    and the real constructors imported under aliases, trusting the terminal
    name reads the decoy as the agent and misses the actual root."""
    project = _adk(
        tmp_path / "shadowed_ctor",
        """
        from google.adk.agents import LlmAgent as RealAgent
        from google.adk.apps import App as RealApp

        def Agent(name):
            return object()

        fake = Agent(name="FabricatedRoot")
        app = RealApp(name="a", root_agent=RealAgent(name="ActualRoot"))
        """,
    )
    result = detect_workspace(project)
    values = [c.value for c in result.agent_name_candidates]
    assert "FabricatedRoot" not in values
    selected = select_agent_name(result.agent_name_candidates)
    assert selected is not None and selected.value == "ActualRoot"
    assert selected.role == "root_agent"


def test_dotted_and_reexported_constructors_stay_recognised(tmp_path: Path) -> None:
    """Proving provenance must not shrink to "one spelling". An unbound
    dotted access and a third-party re-export have nothing in the file
    contradicting the terminal name, so both still count."""
    project = _adk(
        tmp_path / "reexport",
        """
        import google.adk.agents as adk
        agent = adk.LlmAgent(name="DottedAgent")
        """,
    )
    result = detect_workspace(project)
    assert "DottedAgent" in [c.value for c in result.agent_name_candidates]

    other = _adk(
        tmp_path / "wrapper",
        """
        from google.adk.agents import Agent
        from mypkg.agents import Agent as Wrapped
        agent = Wrapped(name="WrappedAgent")
        """,
    )
    result = detect_workspace(other)
    assert "WrappedAgent" not in [c.value for c in result.agent_name_candidates], (
        "an import that renames something else must not inherit Agent's meaning"
    )


def test_enclosing_writes_are_not_ordered_by_the_nested_reference(
    tmp_path: Path,
) -> None:
    """A function body does not execute where it is written. A module-level
    rebinding below a nested reference still happens before the call, so
    comparing enclosing writes against the nested node's line reads the
    stale value as the live one."""
    project = _adk(
        tmp_path / "late_global",
        """
        from google.adk.agents import Agent
        from google.adk.apps import App

        root = Agent(name="StaleGlobalRoot")

        def make_app():
            return App(name="a", root_agent=root)

        root = Agent(name="ActualGlobalRoot")
        app = make_app()
        """,
    )
    result = detect_workspace(project)
    assert select_agent_name(result.agent_name_candidates) is None
    assert not any(c.role == "root_agent" for c in result.agent_name_candidates)


def test_conditional_inline_roots_are_unresolved(tmp_path: Path) -> None:
    """Which branch built the app decides which agent is the root, and that
    is a runtime fact. Both constructions were selectable roots and the
    first one seen won."""
    project = _adk(
        tmp_path / "inline_branch",
        """
        import os
        from google.adk.agents import Agent
        from google.adk.apps import App

        if os.getenv("TIER"):
            app = App(name="a", root_agent=Agent(name="BranchOne"))
        else:
            app = App(name="a", root_agent=Agent(name="BranchTwo"))
        """,
    )
    result = detect_workspace(project)
    assert select_agent_name(result.agent_name_candidates) is None
    assert not any(c.role == "root_agent" for c in result.agent_name_candidates)


@pytest.mark.parametrize(
    ("label", "body"),
    [
        ("del", "root_agent = Agent(name='StaleRoot')\ndel root_agent\n"),
        ("class", "root_agent = Agent(name='StaleRoot')\nclass root_agent:\n    pass\n"),
        (
            "except",
            "root_agent = Agent(name='StaleRoot')\n"
            "try:\n    pass\nexcept ValueError as root_agent:\n    pass\n",
        ),
        (
            "match",
            "root_agent = Agent(name='StaleRoot')\n"
            "match object():\n    case root_agent:\n        pass\n",
        ),
    ],
)
def test_binding_forms_without_a_store_name_retire_the_root(
    tmp_path: Path, label: str, body: str
) -> None:
    """`del`, `class`, `except … as`, and `case` all rebind or unbind the
    module symbol without producing a Store `Name`. A model that only sees
    assignments keeps a root that no longer exists."""
    project = _adk(
        tmp_path / f"form_{label}", "from google.adk.agents import Agent\n" + body
    )
    result = detect_workspace(project)
    assert select_agent_name(result.agent_name_candidates) is None


def test_wildcard_import_makes_bindings_unprovable(tmp_path: Path) -> None:
    """`from x import *` binds an unknowable set of names, so nothing in the
    file can be shown to still hold what it was assigned."""
    project = _adk(
        tmp_path / "star",
        """
        from google.adk.agents import Agent
        from replacement import *

        root_agent = Agent(name="StaleRoot")
        """,
    )
    result = detect_workspace(project)
    assert select_agent_name(result.agent_name_candidates) is None


def test_global_declaration_routes_the_write_to_the_module(tmp_path: Path) -> None:
    """`global root_agent` means a store in that function rebinds the module
    symbol. Recorded as a local write it was invisible, and a stale
    module-level root kept the role while the runtime had moved on."""
    project = _adk(
        tmp_path / "global_decl",
        """
        from google.adk.agents import Agent

        root_agent = Agent(name="OldRoot")

        def install():
            global root_agent
            root_agent = Agent(name="NewRoot")

        install()
        """,
    )
    result = detect_workspace(project)
    assert select_agent_name(result.agent_name_candidates) is None
    assert not any(c.role == "root_agent" for c in result.agent_name_candidates)


def test_nonlocal_declaration_routes_the_write_to_the_enclosing_scope(
    tmp_path: Path,
) -> None:
    """Same rule one scope in: a `nonlocal` store rebinds the enclosing
    function's name, not a fresh local."""
    project = _adk(
        tmp_path / "nonlocal_decl",
        """
        from google.adk.agents import Agent
        from google.adk.apps import App

        def outer():
            root = Agent(name="EnclosingRoot")

            def swap():
                nonlocal root
                root = Agent(name="SwappedRoot")

            swap()
            return App(name="a", root_agent=root)
        """,
    )
    result = detect_workspace(project)
    assert select_agent_name(result.agent_name_candidates) is None


def test_top_level_test_modules_count_as_test_code(tmp_path: Path) -> None:
    """`tests.py` and `test.py` are conventional test modules with no
    `test_` prefix; missing them let a fixture root outrank product code."""
    project = tmp_path / "toplevel"
    project.mkdir()
    (project / "service.py").write_text(
        'from google.adk.agents import Agent\n\nhelper = Agent(name="ProductionAgent")\n',
        encoding="utf-8",
    )
    (project / "tests.py").write_text(
        "from google.adk.agents import Agent\n"
        "from google.adk.apps import App\n\n"
        'root_agent = Agent(name="TestFixtureRoot")\n'
        'app = App(name="a", root_agent=root_agent)\n',
        encoding="utf-8",
    )
    result = detect_workspace(project)
    selected = select_agent_name(result.agent_name_candidates)
    assert selected is not None and selected.value == "ProductionAgent"


def test_detect_result_still_accepts_plain_name_candidates() -> None:
    """`NameCandidate` is a public export and was the declared element type
    before ranking existed. Narrowing the annotation turned working calls
    into a ValidationError; a legacy dict parsed but silently landed on
    `selectable=False`, changing which name `init` writes."""
    from agents_shipgate.schemas.detect import AgentNameCandidate, NameCandidate

    result = DetectResult(
        is_agent_project=True,
        agent_name_candidates=[
            NameCandidate(value="GoodAgent", source="Agent_name_literal"),
            NameCandidate(value="ws", source="workspace_dir"),
        ],
    )
    assert all(isinstance(c, AgentNameCandidate) for c in result.agent_name_candidates)
    selected = select_agent_name(result.agent_name_candidates)
    assert selected is not None and selected.value == "GoodAgent"

    parsed = DetectResult.model_validate(
        {
            "is_agent_project": True,
            "agent_name_candidates": [
                {"value": "GoodAgent", "source": "Agent_name_literal"},
                {"value": "ws", "source": "workspace_dir"},
            ],
        }
    )
    assert [(c.value, c.selectable) for c in parsed.agent_name_candidates] == [
        ("GoodAgent", True),
        ("ws", False),
    ]


# --- Round-4 review: the binding must reach the call site -------------------
#
# Provenance is a question about a *location*, not about a file. Each case
# below found a binding that exists somewhere and used it as if it applied
# here.


def test_a_later_import_cannot_validate_an_earlier_call(tmp_path: Path) -> None:
    """A framework import at the bottom of the file does not reach a call
    above it. Taking the highest-line binding let it retroactively bless a
    local decoy."""
    project = _adk(
        tmp_path / "late_import",
        """
        def Agent(*, name):
            return object()

        root_agent = Agent(name="FabricatedRoot")

        from google.adk.agents import Agent
        """,
    )
    result = detect_workspace(project)
    assert "FabricatedRoot" not in {c.value for c in result.agent_name_candidates}
    assert select_agent_name(result.agent_name_candidates) is None


def test_a_conditional_import_is_not_a_proven_constructor(tmp_path: Path) -> None:
    """An import inside an `if` may not have run, so the spelling it binds
    is not proof of anything at the call site."""
    project = _adk(
        tmp_path / "cond_import",
        """
        import os

        if os.getenv("USE"):
            from google.adk.agents import Agent as A

        root_agent = A(name="MaybeRoot")
        """,
    )
    result = detect_workspace(project)
    assert select_agent_name(result.agent_name_candidates) is None


def test_dotted_constructors_prove_their_head(tmp_path: Path) -> None:
    """`fake.Agent(...)` borrowed the terminal name because provenance was
    only checked for bare spellings. The head has to prove a framework
    module, whatever the tail says."""
    project = _adk(
        tmp_path / "dotted_fake",
        """
        from google.adk.agents import Agent as _Real

        class fake:
            class Agent:
                def __init__(self, name):
                    pass

        root_agent = fake.Agent(name="FabricatedRoot")
        """,
    )
    result = detect_workspace(project)
    assert "FabricatedRoot" not in {c.value for c in result.agent_name_candidates}

    genuine = _adk(
        tmp_path / "dotted_real",
        """
        import google.adk.agents as adk
        root_agent = adk.LlmAgent(name="DottedRoot")
        """,
    )
    result = detect_workspace(genuine)
    assert "DottedRoot" in {c.value for c in result.agent_name_candidates}

    unaliased = _adk(
        tmp_path / "dotted_full",
        """
        import google.adk.agents
        root_agent = google.adk.agents.LlmAgent(name="FullPathRoot")
        """,
    )
    result = detect_workspace(unaliased)
    assert "FullPathRoot" in {c.value for c in result.agent_name_candidates}


def test_attribute_rebinding_retires_constructor_provenance(tmp_path: Path) -> None:
    """`adk.Agent = fake` replaces the constructor through the module object,
    which binds no name at all. The import stays, and looked like proof."""
    project = _adk(
        tmp_path / "attr_ctor",
        """
        import google.adk.agents as adk

        def fake(**kw):
            return object()

        adk.Agent = fake
        root_agent = adk.Agent(name="FabricatedRoot")
        """,
    )
    result = detect_workspace(project)
    assert "FabricatedRoot" not in {c.value for c in result.agent_name_candidates}


def test_attribute_rebinding_retires_environment_defaults(tmp_path: Path) -> None:
    """Same gap, applied to `os.getenv`: the default is only the value the
    call returns while the call is still the stdlib one."""
    project = _adk(
        tmp_path / "attr_env",
        """
        import os
        from google.adk.agents import Agent

        def fake(a, b):
            return "Runtime"

        os.getenv = fake
        NAME = os.getenv("NAME", "FabricatedRoot")
        root_agent = Agent(name=NAME)
        """,
    )
    result = detect_workspace(project)
    assert "FabricatedRoot" not in {c.value for c in result.agent_name_candidates}
    assert select_agent_name(result.agent_name_candidates) is None


def test_comprehension_targets_do_not_bind_the_enclosing_scope(
    tmp_path: Path,
) -> None:
    """Python 3 comprehensions have their own scope: `[App for App in ()]`
    leaves the module's `App` alone. Recording the target as a module write
    shadowed the import and lost the App/root edge entirely."""
    project = _adk(
        tmp_path / "comprehension",
        """
        from google.adk.agents import Agent
        from google.adk.apps import App

        worker = Agent(name="WorkerAgent")
        _ = [App for App in ()]
        app = App(name="a", root_agent=Agent(name="ActualRoot"))
        """,
    )
    result = detect_workspace(project)
    selected = select_agent_name(result.agent_name_candidates)
    assert selected is not None and selected.value == "ActualRoot"
    assert selected.role == "root_agent"


def test_definition_headers_are_evaluated_in_the_enclosing_scope(
    tmp_path: Path,
) -> None:
    """A default value runs before the parameter it initialises exists, and
    in the enclosing scope. Walking headers as part of the body let the
    parameter shadow the constructor its own default had just used."""
    project = _adk(
        tmp_path / "default_header",
        """
        from google.adk.agents import Agent
        from google.adk.apps import App

        worker = Agent(name="WorkerAgent")

        def configure(App=App(name="a", root_agent=Agent(name="ActualRoot"))):
            return App

        app = configure()
        """,
    )
    result = detect_workspace(project)
    selected = select_agent_name(result.agent_name_candidates)
    assert selected is not None and selected.value == "ActualRoot"


def test_roots_inside_conditionally_defined_functions_are_unresolved(
    tmp_path: Path,
) -> None:
    """A body is straight-line relative to itself, but if the `def` only runs
    in one arm the identity it declares is contingent on that arm."""
    project = _adk(
        tmp_path / "branch_def",
        """
        import os
        from google.adk.agents import Agent
        from google.adk.apps import App

        USE = os.getenv("USE")
        if USE:
            def build():
                return App(name="a", root_agent=Agent(name="BranchOne"))
        else:
            def build():
                return App(name="a", root_agent=Agent(name="BranchTwo"))

        app = build()
        """,
    )
    result = detect_workspace(project)
    assert select_agent_name(result.agent_name_candidates) is None
    assert not any(c.role == "root_agent" for c in result.agent_name_candidates)


def test_wildcard_import_retires_constructor_provenance(tmp_path: Path) -> None:
    """`from replacement import *` may legally replace both constructors, so
    an import above it stops being proof at a call below it."""
    project = _adk(
        tmp_path / "star_ctor",
        """
        from google.adk.agents import Agent
        from google.adk.apps import App
        from replacement import *

        app = App(name="a", root_agent=Agent(name="FabricatedRoot"))
        """,
    )
    result = detect_workspace(project)
    assert "FabricatedRoot" not in {c.value for c in result.agent_name_candidates}


def test_a_binding_after_a_wildcard_import_restores_provenance(
    tmp_path: Path,
) -> None:
    """The wildcard rule is source-ordered, not a permanent verdict: an
    explicit import below it re-establishes what the spelling means."""
    project = _adk(
        tmp_path / "star_then_import",
        """
        from replacement import *
        from google.adk.agents import Agent
        from google.adk.apps import App

        app = App(name="a", root_agent=Agent(name="RestoredRoot"))
        """,
    )
    result = detect_workspace(project)
    selected = select_agent_name(result.agent_name_candidates)
    assert selected is not None and selected.value == "RestoredRoot"


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param([{"value": "G", "source": "Agent_name_literal", "extra": 1}], id="extra-key"),
        pytest.param([{"source": "Agent_name_literal"}], id="missing-value"),
        pytest.param([{"value": 5, "source": "Agent_name_literal"}], id="wrong-type"),
    ],
)
def test_malformed_legacy_candidates_are_rejected(payload: list[dict]) -> None:
    """The upgrade shim built the ranked model directly, so it stringified
    missing values and accepted keys `extra="forbid"` exists to reject —
    turning a malformed payload into a well-formed lie."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        DetectResult(is_agent_project=True, agent_name_candidates=payload)


def test_legacy_candidates_accept_every_sequence_form() -> None:
    """The old `list[NameCandidate]` field accepted tuples. Normalising only
    `list` left a tuple of instances raising and a tuple of legacy dicts
    silently landing on `selectable=False`."""
    from agents_shipgate.schemas.detect import NameCandidate

    for payload in (
        (NameCandidate(value="GoodAgent", source="Agent_name_literal"),),
        ({"value": "GoodAgent", "source": "Agent_name_literal"},),
    ):
        result = DetectResult(is_agent_project=True, agent_name_candidates=payload)
        selected = select_agent_name(result.agent_name_candidates)
        assert selected is not None and selected.value == "GoodAgent"
