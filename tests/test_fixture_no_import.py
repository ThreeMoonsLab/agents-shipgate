"""Per-adapter trust-model live-load tests + sample-protection assertion.

Companion to ``tests/test_adapter_static_only.py``:

- That file enforces the no-import invariant *statically* by scanning
  every ``src/agents_shipgate/inputs/*.py`` source for forbidden builtins
  (``exec``/``eval``/``__import__``/``compile``) and dynamic-import
  surfaces (``importlib``/``runpy``/``subprocess``).
- This file enforces the same invariant *at runtime* by driving each
  adapter end-to-end against a fixture that raises ``RuntimeError`` at
  module load and watching for ``sys.modules`` pollution from the
  fixture directory.

Two fixture shapes:

1. **Python-parsing adapters** (LangChain, CrewAI, OpenAI Agents SDK,
   Google ADK) take a Python source as their primary input. The fixture
   *is* a Python file with a module-level ``raise RuntimeError(...)``;
   if the adapter ever ``import``s it, the test fails.

2. **Declarative adapters** (MCP, OpenAPI, Anthropic, OpenAI API, n8n,
   Codex plugin) ingest JSON/YAML/Markdown/plugin manifests. The fixture
   places a sibling ``trap.py`` with the same ``raise`` at module top.
   The adapter must never touch that file even though it sits in the
   workspace.

The strongest cross-cutting assertion is the ``sys.modules`` snapshot:
no module whose ``__file__`` resolves under the workspace root may
appear in ``sys.modules`` after the scan. ``ast.parse`` /
``yaml.safe_load`` / ``json.loads`` must be the only ingestion paths.

If you add a new adapter, add a test here.
"""

from __future__ import annotations

import sys
from pathlib import Path
from textwrap import dedent

import pytest

from agents_shipgate.cli.scan import run_scan

REPO_ROOT = Path(__file__).resolve().parent.parent
TRAP = (
    'raise RuntimeError("agents-shipgate must parse this file without importing")'
)


# --- Helpers ---------------------------------------------------------------


def _user_modules_under(root: Path) -> set[str]:
    """Set of ``sys.modules`` keys whose ``__file__`` resolves under ``root``.

    Filters by resolved path so a fixture inside ``tmp_path`` is not
    confused with the agents-shipgate source tree or stdlib.
    """
    resolved_root = root.resolve()
    out: set[str] = set()
    for name, mod in list(sys.modules.items()):
        file_attr = getattr(mod, "__file__", None)
        if not file_attr:
            continue
        try:
            mod_path = Path(file_attr).resolve()
        except (ValueError, OSError):
            continue
        try:
            if mod_path.is_relative_to(resolved_root):
                out.add(name)
        except ValueError:
            # Different drive on Windows; cannot be under the same root.
            continue
    return out


def _run_and_assert_no_import(workspace: Path) -> object:
    """Run a scan rooted at ``workspace`` and enforce the no-import invariant.

    Returns the parsed report. The universal invariant is no module
    under the workspace ending up in ``sys.modules``. Callers add
    adapter-specific "adapter actually did work" assertions on the
    returned report (most adapters populate ``tool_inventory``;
    framework adapters like n8n populate ``frameworks[<name>]``).
    """
    before = _user_modules_under(workspace)
    report, _exit_code = run_scan(
        config_path=workspace / "shipgate.yaml",
        output_dir=workspace / "_reports",
        formats=["json"],
        ci_mode="advisory",
    )
    after = _user_modules_under(workspace)
    leaked = sorted(after - before)
    assert not leaked, (
        f"Adapter imported user modules from {workspace}:\n  "
        + "\n  ".join(leaked)
        + "\n\nTrust-model invariant violation. Adapters must parse user "
        + "files with ast.parse / yaml.safe_load / json.loads only. See "
        + "STABILITY.md § 'Trust-model invariants' and "
        + "tests/test_adapter_static_only.py for the structural check."
    )
    return report


def _assert_did_work_tool_inventory(report: object, *, minimum: int = 1) -> None:
    """For adapters that surface declarations into ``report.tool_catalog``."""
    count = len(report.tool_catalog)  # type: ignore[attr-defined]
    assert count >= minimum, (
        f"Adapter did not extract enough tools into tool_catalog "
        f"(got {count}, expected ≥ {minimum}). If the adapter changed its "
        "scope, update the fixture or the assertion."
    )


def _assert_did_work_framework(
    report: object, framework: str, *, key: str, minimum: int = 1
) -> None:
    """For framework adapters that surface their work into ``report.frameworks``."""
    summary = report.frameworks.get(framework)  # type: ignore[attr-defined]
    assert summary is not None, (
        f"Adapter did not populate report.frameworks[{framework!r}]. "
        f"frameworks keys: {sorted(report.frameworks)}"  # type: ignore[attr-defined]
    )
    actual = summary.get(key, 0)
    assert actual >= minimum, (
        f"report.frameworks[{framework!r}][{key!r}] = {actual}, "
        f"expected ≥ {minimum}. If the adapter changed its scope, update "
        "the fixture or the assertion."
    )


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(content).lstrip("\n"), encoding="utf-8")


# --- Python-parsing adapters -----------------------------------------------


def test_langchain_adapter_does_not_import_user_module(tmp_path: Path) -> None:
    """LangChain adapter must parse ``agent.py`` via ast.parse only.

    The fixture's module-level ``raise`` would fire on any real import;
    we additionally watch ``sys.modules`` for any pollution.
    """
    workspace = tmp_path / "lc"
    _write(
        workspace / "agent.py",
        f"""
        from langchain.agents import create_agent
        from langchain.tools import tool
        from langchain_core.tools import StructuredTool
        from pydantic import BaseModel, Field

        {TRAP}


        class LookupInput(BaseModel):
            case_id: str = Field(..., description="Support case identifier.")


        @tool(args_schema=LookupInput)
        def lookup_case(case_id: str) -> dict:
            \"\"\"Look up read-only case metadata.\"\"\"
            return {{"case_id": case_id}}


        def summarize_case(case_id: str) -> dict:
            \"\"\"Summarize read-only case metadata.\"\"\"
            return {{"case_id": case_id, "summary": ""}}


        summary_tool = StructuredTool.from_function(
            func=summarize_case,
            name="summarize_case",
            description="Summarize read-only case metadata.",
        )

        agent = create_agent(model=None, tools=[lookup_case, summary_tool])
        """,
    )
    _write(
        workspace / "shipgate.yaml",
        """
        version: "0.1"
        project:
          name: lc-no-import
        agent:
          name: lc-agent
          declared_purpose:
            - read case metadata
        environment:
          target: local
        tool_sources:
          - id: lc
            type: langchain
            path: agent.py
        """,
    )
    report = _run_and_assert_no_import(workspace)
    _assert_did_work_tool_inventory(report, minimum=2)


def test_crewai_adapter_does_not_import_user_module(tmp_path: Path) -> None:
    """CrewAI adapter must parse ``crew.py`` via ast.parse only."""
    workspace = tmp_path / "crew"
    _write(
        workspace / "crew.py",
        f"""
        from crewai import Agent, Crew
        from crewai.tools import BaseTool, tool
        from crewai_tools import FileReadTool
        from pydantic import BaseModel, Field

        {TRAP}


        class LookupInput(BaseModel):
            case_id: str = Field(..., description="Support case identifier.")


        @tool("summarize_case")
        def summarize_case(case_id: str) -> dict:
            \"\"\"Summarize read-only case metadata.\"\"\"
            return {{"case_id": case_id}}


        class LookupTool(BaseTool):
            name: str = "lookup_case"
            description: str = "Look up read-only case metadata."
            args_schema = LookupInput

            def _run(self, case_id: str) -> dict:
                return {{"case_id": case_id}}


        file_tool = FileReadTool()
        lookup_tool = LookupTool()

        researcher = Agent(
            role="reader",
            goal="read case metadata",
            backstory="reads cases",
            tools=[summarize_case, lookup_tool, file_tool],
        )
        crew = Crew(agents=[researcher])
        """,
    )
    _write(
        workspace / "shipgate.yaml",
        """
        version: "0.1"
        project:
          name: crewai-no-import
        agent:
          name: crew-agent
          declared_purpose:
            - read case metadata
        environment:
          target: local
        tool_sources:
          - id: crewai
            type: crewai
            path: crew.py
        """,
    )
    report = _run_and_assert_no_import(workspace)
    _assert_did_work_tool_inventory(report, minimum=2)


def test_openai_agents_sdk_adapter_does_not_import_user_module(
    tmp_path: Path,
) -> None:
    """OpenAI Agents SDK static loader must parse via ast.parse only."""
    workspace = tmp_path / "openai_sdk"
    _write(
        workspace / "agent.py",
        f"""
        from agents import Agent, function_tool

        {TRAP}


        @function_tool
        def send_email_preview(recipient: str, subject: str, body: str) -> str:
            \"\"\"Render a customer email preview without sending it.\"\"\"
            return f"To: {{recipient}}"


        refund_agent = Agent(
            name="refund-assistant",
            instructions="Answer refund policy questions.",
            tools=[send_email_preview],
        )
        """,
    )
    _write(
        workspace / "shipgate.yaml",
        """
        version: "0.1"
        project:
          name: openai-sdk-no-import
        agent:
          name: refund-assistant
          sdk:
            type: openai-agents
            language: python
            entrypoint: agent.py
            object: refund_agent
            static_extract: true
            deep_import: false
          declared_purpose:
            - prepare refund requests for human review
        environment:
          target: local
        tool_sources:
          - id: sdk
            type: openai_agents_sdk
            path: agent.py
            mode: static
        """,
    )
    report = _run_and_assert_no_import(workspace)
    _assert_did_work_tool_inventory(report, minimum=1)


def test_google_adk_adapter_does_not_import_user_module(tmp_path: Path) -> None:
    """Google ADK static extractor must parse via ast.parse only.

    A separate end-to-end ADK test lives in tests/test_google_adk.py;
    this one adds the ``sys.modules`` snapshot assertion uniformly with
    the other adapters so a future regression in either dimension is
    caught here.
    """
    workspace = tmp_path / "adk"
    _write(
        workspace / "agent.py",
        f"""
        from google.adk.agents import LlmAgent
        from google.adk.tools import FunctionTool

        {TRAP}


        def lookup(case_id: str) -> dict:
            \"\"\"Look up read-only case metadata.\"\"\"
            return {{"case_id": case_id}}


        lookup_tool = FunctionTool(func=lookup)

        root_agent = LlmAgent(
            name="root_agent",
            instruction="Read case metadata.",
            tools=[lookup_tool],
        )
        """,
    )
    _write(
        workspace / "shipgate.yaml",
        """
        version: "0.1"
        project:
          name: adk-no-import
        agent:
          name: root-agent
          declared_purpose:
            - read case metadata
        environment:
          target: local
        tool_sources:
          - id: adk
            type: google_adk
            path: agent.py
        """,
    )
    report = _run_and_assert_no_import(workspace)
    _assert_did_work_framework(
        report, "google_adk", key="function_tool_count", minimum=1
    )


# --- Declarative adapters: sibling-trap pattern ----------------------------


def _write_sibling_trap(workspace: Path) -> None:
    """Plant a load-trap Python file in the workspace next to the manifest.

    A declarative adapter must never touch ``trap.py`` — it has no
    business reading Python sources. We assert that via the
    ``sys.modules`` snapshot in the helper.
    """
    _write(workspace / "trap.py", TRAP + "\n")


def _write_codex_plugin_package(root: Path) -> None:
    """Write a minimal Codex plugin package with executable-looking stubs.

    The scanner may parse these JSON/Markdown files, but it must never execute
    the hook command or launch the MCP server command.
    """
    _write(
        root / ".codex-plugin/plugin.json",
        """
        {
          "name": "browserish",
          "version": "1.0.0",
          "description": "Review browser automation from static Codex plugin files.",
          "skills": "./skills/",
          "mcpServers": "./.mcp.json",
          "hooks": "./hooks.json"
        }
        """,
    )
    _write(
        root / "skills/browser/SKILL.md",
        """
        ---
        name: browser
        description: Use browser automation for local UI inspection.
        ---

        # Browser
        """,
    )
    _write(
        root / ".mcp.json",
        """
        {
          "mcpServers": {
            "browser": {
              "command": "python",
              "args": ["-c", "raise SystemExit('must not execute')"]
            }
          }
        }
        """,
    )
    _write(
        root / "hooks.json",
        """
        {
          "preRun": {
            "command": "touch should-never-run"
          }
        }
        """,
    )


def test_mcp_adapter_does_not_import_sibling_python(tmp_path: Path) -> None:
    """MCP adapter loads JSON only — sibling trap.py must remain untouched."""
    workspace = tmp_path / "mcp"
    _write(
        workspace / "mcp-tools.json",
        """
        {
          "tools": [
            {
              "name": "support.search_kb",
              "description": "Search support knowledge base.",
              "annotations": {"readOnlyHint": true}
            }
          ]
        }
        """,
    )
    _write_sibling_trap(workspace)
    _write(
        workspace / "shipgate.yaml",
        """
        version: "0.1"
        project:
          name: mcp-no-import
        agent:
          name: mcp-agent
          declared_purpose:
            - search support knowledge base
        environment:
          target: local
        tool_sources:
          - id: mcp
            type: mcp
            path: mcp-tools.json
        """,
    )
    report = _run_and_assert_no_import(workspace)
    _assert_did_work_tool_inventory(report, minimum=1)


def test_codex_plugin_adapter_does_not_import_sibling_python(
    tmp_path: Path,
) -> None:
    """Codex plugin adapter loads package files only.

    The sibling trap.py stays untouched.
    """
    workspace = tmp_path / "codex_plugin"
    _write_codex_plugin_package(workspace / "plugins/browserish")
    _write_sibling_trap(workspace)
    marker = workspace / "should-never-run"
    _write(
        workspace / "shipgate.yaml",
        """
        version: "0.1"
        project:
          name: codex-plugin-no-import
        agent:
          name: codex-plugin-agent
          declared_purpose:
            - review a plugin package
        environment:
          target: local
        tool_sources:
          - id: browserish
            type: codex_plugin
            mode: package
            path: plugins/browserish
        """,
    )
    report = _run_and_assert_no_import(workspace)
    assert marker.exists() is False, "Codex plugin hook command must not run."
    assert report.codex_plugin_surface is not None
    assert report.codex_plugin_surface.plugin_count == 1
    assert report.codex_plugin_surface.skill_count == 1
    assert report.codex_plugin_surface.mcp_server_stub_count == 1
    assert report.codex_plugin_surface.hook_stub_count == 1


def test_openapi_adapter_does_not_import_sibling_python(tmp_path: Path) -> None:
    """OpenAPI adapter loads YAML only — sibling trap.py must remain untouched."""
    workspace = tmp_path / "openapi"
    _write(
        workspace / "support.openapi.yaml",
        """
        openapi: 3.1.0
        info:
          title: Support
          version: "1.0"
        paths:
          /records:
            get:
              operationId: support.lookup_record
              responses:
                "200":
                  description: ok
        """,
    )
    _write_sibling_trap(workspace)
    _write(
        workspace / "shipgate.yaml",
        """
        version: "0.1"
        project:
          name: openapi-no-import
        agent:
          name: openapi-agent
          declared_purpose:
            - look up support records
        environment:
          target: local
        tool_sources:
          - id: openapi
            type: openapi
            path: support.openapi.yaml
        """,
    )
    report = _run_and_assert_no_import(workspace)
    _assert_did_work_tool_inventory(report, minimum=1)


def test_anthropic_adapter_does_not_import_sibling_python(
    tmp_path: Path,
) -> None:
    """Anthropic adapter loads JSON tools + Markdown prompts only."""
    workspace = tmp_path / "anthropic"
    _write(
        workspace / "tools/anthropic-tools.json",
        """
        {
          "tools": [
            {
              "name": "get_help_article",
              "description": "Look up a help center article.",
              "input_schema": {
                "type": "object",
                "properties": {
                  "article_id": {"type": "string"}
                },
                "required": ["article_id"]
              }
            }
          ]
        }
        """,
    )
    _write(
        workspace / "prompts/support.md",
        """
        # Support assistant

        Look up help center articles only. Do not change customer records.
        """,
    )
    _write_sibling_trap(workspace)
    _write(
        workspace / "shipgate.yaml",
        """
        version: "0.1"
        project:
          name: anthropic-no-import
        agent:
          name: anthropic-agent
          prohibited_actions:
            - change customer records
        environment:
          target: local
        anthropic:
          prompt_files:
            - prompts/support.md
          tools:
            - path: tools/anthropic-tools.json
        """,
    )
    report = _run_and_assert_no_import(workspace)
    _assert_did_work_tool_inventory(report, minimum=1)


def test_n8n_adapter_does_not_import_sibling_python(tmp_path: Path) -> None:
    """n8n adapter loads workflow JSON only — sibling trap.py must remain untouched."""
    workspace = tmp_path / "n8n"
    _write(
        workspace / "workflow.json",
        """
        {
          "id": "workflow-1",
          "name": "Support assistant",
          "nodes": [
            {
              "id": "webhook",
              "name": "Webhook",
              "type": "n8n-nodes-base.webhook",
              "parameters": {"path": "support", "httpMethod": "POST"}
            },
            {
              "id": "http-tool",
              "name": "Lookup Article",
              "type": "n8n-nodes-langchain.toolHttpRequest",
              "parameters": {
                "url": "https://help.example.com/api/articles/{{ $json.article_id }}",
                "method": "GET",
                "toolDescription": "Look up a help center article."
              }
            }
          ],
          "connections": {
            "Webhook": {
              "main": [[{"node": "Lookup Article", "type": "main", "index": 0}]]
            }
          }
        }
        """,
    )
    _write_sibling_trap(workspace)
    _write(
        workspace / "shipgate.yaml",
        """
        version: "0.1"
        project:
          name: n8n-no-import
        agent:
          name: n8n-agent
          declared_purpose:
            - look up help center articles
        environment:
          target: local
        n8n:
          workflows:
            - path: workflow.json
        """,
    )
    report = _run_and_assert_no_import(workspace)
    _assert_did_work_framework(report, "n8n", key="workflow_count", minimum=1)


def test_openai_api_adapter_does_not_import_sibling_python(
    tmp_path: Path,
) -> None:
    """OpenAI Chat Completions API adapter loads JSON + Markdown only."""
    workspace = tmp_path / "openai_api"
    _write(
        workspace / "tools/openai-tools.json",
        """
        {
          "tools": [
            {
              "type": "function",
              "function": {
                "name": "get_help_article",
                "description": "Look up a help center article.",
                "parameters": {
                  "type": "object",
                  "properties": {
                    "article_id": {"type": "string"}
                  },
                  "required": ["article_id"]
                }
              }
            }
          ]
        }
        """,
    )
    _write(
        workspace / "prompts/support.md",
        """
        # Support assistant

        Look up help center articles only.
        """,
    )
    _write_sibling_trap(workspace)
    _write(
        workspace / "shipgate.yaml",
        """
        version: "0.1"
        project:
          name: openai-api-no-import
        agent:
          name: openai-api-agent
          prohibited_actions:
            - change customer records
        environment:
          target: local
        openai_api:
          prompt_files:
            - prompts/support.md
          tools:
            - path: tools/openai-tools.json
        """,
    )
    report = _run_and_assert_no_import(workspace)
    _assert_did_work_tool_inventory(report, minimum=1)


# --- Sample-protection assertion -------------------------------------------

# Each sample under samples/ that is a Python load-trap demonstrator must
# keep its module-level ``raise`` intact. Without it, the sample stops
# being a faithful no-import demonstrator for adopters reading the repo.

PROTECTED_SAMPLES = (
    REPO_ROOT / "samples/simple_langchain_agent/agent.py",
    REPO_ROOT / "samples/simple_crewai_agent/crew.py",
)


@pytest.mark.parametrize(
    "sample_path",
    PROTECTED_SAMPLES,
    ids=lambda p: str(p.relative_to(REPO_ROOT)),
)
def test_published_sample_keeps_module_level_raise(sample_path: Path) -> None:
    """Published samples must keep their module-level RuntimeError trap.

    These files are referenced by quickstart docs and ``fixture run`` as
    proof points that the scanner does not import user code. Stripping
    the ``raise`` line silently turns them into innocuous examples;
    block that at the contract level.
    """
    text = sample_path.read_text(encoding="utf-8")
    assert "raise RuntimeError(" in text, (
        f"{sample_path.relative_to(REPO_ROOT)} no longer raises at module load. "
        "Restore the load-trap so this sample remains a faithful "
        "no-import demonstrator (see STABILITY.md § 'Trust-model invariants')."
    )
    # And the trap must be at module level — not inside an `if False:`
    # block or a function body. Quick check: line must not be indented.
    raise_lines = [
        ln for ln in text.splitlines() if "raise RuntimeError(" in ln
    ]
    assert any(
        ln.startswith("raise RuntimeError(") for ln in raise_lines
    ), (
        f"{sample_path.relative_to(REPO_ROOT)} has a RuntimeError but not "
        "at module level (every match is indented). The trap must fire on "
        "any unguarded import."
    )
