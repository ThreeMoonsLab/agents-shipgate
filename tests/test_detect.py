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
    rendered = render_auto_manifest(workspace, result)
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
    assert "agent:\n  name: SmartCloserAgent" in render_auto_manifest(
        tmp_path / "smart_closer", result
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
    assert "name: CHANGE_ME" in render_auto_manifest(project, result)


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
    assert "name: CHANGE_ME" in render_auto_manifest(project, result)


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
