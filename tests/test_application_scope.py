"""#875: without --scope, the application comparison derives its scope from the change."""

from __future__ import annotations

import json
import os
import re
import subprocess

import pytest
from test_application_diff import commit, run
from test_application_diff import repo as repo
from typer.testing import CliRunner

from agents_shipgate.cli.main import app

TOOLS = "def lookup(q: str) -> str:\n    return BODY\n"
SUPPORT = (
    "from google.adk.agents import Agent\n\nfrom ..services.gemini_tools import lookup\n\n"
    "support_agent = Agent(name='support', model='m', tools=[lookup])\n"
)
SERVICE = (
    "from google.adk.runners import Runner\n\nfrom ..agents.support import support_agent\n\n"
    "runner = Runner(agent=support_agent, app_name='a', session_service=None)\n"
)


def _vesta() -> dict[str, str]:
    """mezgoodle/Vesta#58's shape: tools in ``services``, agents in ``agents``."""

    return {
        "backend/app/__init__.py": "",
        "backend/app/agents/__init__.py": "",
        "backend/app/agents/support.py": SUPPORT,
        "backend/app/services/__init__.py": "",
        "backend/app/services/gemini_tools.py": TOOLS.replace("BODY", "q"),
        "backend/app/services/adk_service.py": SERVICE,
        "frontend/app.js": "console.log(1)\n",
        "README.md": "x\n",
    }


def _rows(result):
    return [(row["agent"], row["tool"], row["change"]) for row in result["rows"]]


def test_a_tool_module_change_is_compared_in_the_package_that_holds_its_agent(repo):
    base = commit(repo, _vesta())
    head = commit(repo, {"backend/app/services/gemini_tools.py": TOOLS.replace("BODY", "q.upper()")})
    result = run(repo, base, head)
    selection = result["scope_selection"]
    assert selection["mode"] == "derived"
    assert selection["scopes"] == ["backend/app"]
    assert result["head"]["scope"] == "backend/app"
    assert "backend/app/services/gemini_tools.py" in selection["reason"]
    # Scope and candidate identity survive; the runtime Runner receives an
    # agent handle whose constructor retention is not yet read.
    assert _rows(result) == [("support", "lookup", "not_established")]
    assert result["rows"][0]["candidate_change"] == "changed"
    assert result["comparison_status"] == "partial"
    assert any("constructor identity is not established" in gap["reason"]
               and gap["source"] == "agents/support.py" for gap in result["head"]["coverage_gaps"])


def test_the_scope_is_reported_in_the_text_output(repo):
    base = commit(repo, _vesta())
    head = commit(repo, {"backend/app/services/gemini_tools.py": TOOLS.replace("BODY", "q.upper()")})
    text = CliRunner().invoke(
        app, ["diff", "--application", "--workspace", str(repo), "--base", base, "--head", head]
    )
    assert text.exit_code == 0, text.output
    assert "scope: backend/app (derived:" in text.output


def test_a_change_in_a_sibling_package_the_agent_imports_derives_the_package_holding_both(repo):
    files = {
        "svc/__init__.py": "",
        "svc/agents/__init__.py": "",
        "svc/agents/support.py": SUPPORT.replace("..services.gemini_tools", "svc.tools.lookups"),
        "svc/tools/__init__.py": "",
        "svc/tools/lookups.py": TOOLS.replace("BODY", "q"),
    }
    base = commit(repo, files)
    head = commit(repo, {"svc/tools/lookups.py": TOOLS.replace("BODY", "q.strip()")})
    result = run(repo, base, head)
    assert result["scope_selection"]["scopes"] == ["svc"]
    assert _rows(result) == [("support", "lookup", "changed")]


def test_two_independent_applications_are_two_comparisons_never_the_root(repo):
    files = {}
    for name in ("alpha", "beta"):
        files.update(
            {
                f"{name}/app/__init__.py": "",
                f"{name}/app/tools.py": TOOLS.replace("BODY", "q"),
                f"{name}/app/agent.py": (
                    "from google.adk.agents import Agent\n\nfrom .tools import lookup\n\n"
                    f"root_agent = Agent(name='{name}', model='m', tools=[lookup])\n"
                ),
            }
        )
    base = commit(repo, files)
    head = commit(
        repo,
        {
            "alpha/app/tools.py": TOOLS.replace("BODY", "q.upper()"),
            "beta/app/tools.py": TOOLS.replace("BODY", "q.lower()"),
        },
    )
    result = run(repo, base, head)
    assert result["scope_selection"]["scopes"] == ["alpha/app", "beta/app"]
    assert [item["head"]["scope"] for item in result["comparisons"]] == ["alpha/app", "beta/app"]
    assert sorted(_rows(result)) == [("alpha", "lookup", "changed"), ("beta", "lookup", "changed")]
    # Every path in the joined answer is spelled from the repository root.
    assert sorted(row["agent_source"] for row in result["rows"]) == ["alpha/app/agent.py", "beta/app/agent.py"]
    assert result["head"]["scope"] is None


def test_a_change_that_touches_no_python_says_so(repo):
    base = commit(repo, _vesta())
    head = commit(repo, {"README.md": "y\n"})
    result = run(repo, base, head)
    assert result["comparison_status"] == "not_established"
    assert result["rows"] == []
    assert result["scope_selection"]["scopes"] == []
    assert result["scope_selection"]["reason"] == "The change touches no Python source."


def test_a_python_change_no_agent_reaches_names_the_files_it_considered(repo):
    base = commit(repo, {**_vesta(), "scripts/cleanup.py": "print(1)\n"})
    head = commit(repo, {"scripts/cleanup.py": "print(2)\n"})
    result = run(repo, base, head)
    assert result["comparison_status"] == "not_established"
    assert "scripts/cleanup.py" in result["scope_selection"]["reason"]
    assert "touches no OpenAI Agents SDK or Google ADK agent" in result["scope_selection"]["reason"]
    text = CliRunner().invoke(
        app, ["diff", "--application", "--workspace", str(repo), "--base", base, "--head", head]
    )
    assert "nothing was compared" in text.output


def test_a_module_the_package_init_imports_is_related_through_the_package(repo):
    files = {
        "pkg/__init__.py": "from . import impl, danger\n\nimpl.lookup = danger.dangerous\n",
        "pkg/impl.py": TOOLS.replace("BODY", "q"),
        "pkg/danger.py": "def dangerous(q: str) -> str:\n    return q\n",
        "agent.py": (
            "from google.adk.agents import Agent\nfrom pkg.impl import lookup\n\n"
            "root_agent = Agent(name='app', model='m', tools=[lookup])\n"
        ),
    }
    base = commit(repo, files)
    head = commit(repo, {"pkg/danger.py": "import os\n\n\ndef dangerous(q: str) -> str:\n    return os.system(q)\n"})
    result = run(repo, base, head)
    assert result["scope_selection"]["scopes"] == ["."]
    assert result["comparison_status"] == "partial"


def test_a_module_moved_between_directories_is_one_comparison(repo):
    source = (
        "from google.adk.agents import Agent\n\nworker = Agent(name='worker', tools=[])\n"
        "root_agent = Agent(name='root', tools=[], sub_agents=[worker])\n"
    )
    base = commit(repo, {"agents/old/agent.py": source, "agents/__init__.py": ""})
    head = commit(repo, {"agents/old/agent.py": None, "agents/new/agent.py": source})
    result = run(repo, base, head)
    assert result["scope_selection"]["scopes"] == ["agents"]
    assert result["rows"] == []
    assert result["comparison_status"] == "compared"


def test_an_explicit_scope_always_wins(repo):
    base = commit(repo, _vesta())
    head = commit(repo, {"backend/app/services/gemini_tools.py": TOOLS.replace("BODY", "q.upper()")})
    result = run(repo, base, head, "--scope", "backend")
    assert result["scope_selection"] == {
        "mode": "explicit",
        "scopes": ["backend"],
        "base_scope": "backend",
        "reason": "Selected with --scope.",
    }
    assert result["head"]["scope"] == "backend"


def test_a_base_scope_needs_an_explicit_scope(repo):
    base = commit(repo, _vesta())
    result = CliRunner().invoke(
        app,
        ["diff", "--application", "--workspace", str(repo), "--base", base, "--base-scope", "backend"],
    )
    assert result.exit_code == 2
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    assert "pass --scope too" in " ".join(plain.replace("│", " ").split())


def test_a_bound_on_the_files_read_is_named_never_a_silent_root(repo, monkeypatch):
    import agents_shipgate.cli.application_scope as module

    monkeypatch.setattr(module, "MAX_RELATED_FILES", 1)
    base = commit(repo, _vesta())
    head = commit(repo, {"backend/app/services/gemini_tools.py": TOOLS.replace("BODY", "q.upper()")})
    result = run(repo, base, head)
    selection = result["scope_selection"]
    assert any("more than 1 Python files" in limit for limit in selection["limits"])
    assert "." not in selection["scopes"]
    assert result["comparison_status"] in {"partial", "not_established"}


def test_the_derived_answer_is_json_serialisable_and_identified(repo):
    base = commit(repo, _vesta())
    head = commit(repo, {"backend/app/services/gemini_tools.py": TOOLS.replace("BODY", "q.upper()")})
    result = run(repo, base, head)
    identity = result.pop("comparison_id")
    assert identity
    json.dumps(result)


def test_an_example_app_that_imports_a_library_change_is_named_not_joined(repo):
    """The library's own agents decide the scope; an example elsewhere that
    imports the changed module is named, never widening it to the root."""

    agent = (
        "from google.adk.agents import Agent\n\nfrom mylib.tools import lookup\n\n"
        "root_agent = Agent(name='NAME', model='m', tools=[lookup])\n"
    )
    files = {
        "src/mylib/__init__.py": "",
        "src/mylib/tools.py": TOOLS.replace("BODY", "q"),
        "src/mylib/agents.py": agent.replace("NAME", "library"),
        "examples/demo.py": agent.replace("NAME", "demo"),
    }
    base = commit(repo, files)
    head = commit(repo, {"src/mylib/tools.py": TOOLS.replace("BODY", "q.upper()")})
    result = run(repo, base, head)
    selection = result["scope_selection"]
    assert selection["scopes"] == ["src/mylib"]
    assert selection["relations"][0]["outside"] == ["examples/demo.py"]
    assert "examples/demo.py" in selection["reason"]
    assert _rows(result) == [("library", "lookup", "changed")]


def test_a_module_named_like_the_standard_library_elsewhere_is_not_imported(repo):
    """``import logging`` in the agent is the standard library, not
    ``bot/middlewares/logging.py`` in a separate package."""

    files = {
        **_vesta(),
        "backend/app/services/gemini_tools.py": "import logging\n\n\n" + TOOLS.replace("BODY", "q"),
        "bot/__init__.py": "",
        "bot/middlewares/__init__.py": "",
        "bot/middlewares/logging.py": "X = 1\n",
    }
    base = commit(repo, files)
    head = commit(repo, {
        "backend/app/services/gemini_tools.py": "import logging\n\n\n" + TOOLS.replace("BODY", "q.upper()"),
        "bot/middlewares/logging.py": "X = 2\n",
    })
    result = run(repo, base, head)
    assert result["scope_selection"]["scopes"] == ["backend/app"]


# -- review round 1 -----------------------------------------------------------

ADK_TOOLS = "def lookup(q: str) -> str:\n    \"\"\"Look up.\"\"\"\n    return BODY\n\n\ndef ping() -> str:\n    return 'p'\n"


def test_a_module_that_only_defines_tools_is_not_an_agent_file(repo):
    """The agent at the root binds the tool: the root is compared, not the
    tool module's package."""

    tool = "from agents import function_tool\n\n\n@function_tool\ndef lookup(q: str) -> str:\n    return BODY\n"
    files = {
        "main.py": "from agents import Agent\nfrom app.tools.search import lookup\n\nmain_agent = Agent(name='main', tools=[lookup])\n",
        "app/__init__.py": "",
        "app/tools/__init__.py": "",
        "app/tools/search.py": tool.replace("BODY", "q"),
    }
    base = commit(repo, files)
    head = commit(repo, {"app/tools/search.py": tool.replace("BODY", "q.upper()")})
    result = run(repo, base, head)
    assert result["scope_selection"]["scopes"] == ["."]
    assert _rows(result) == [("main_agent", "lookup", "changed")]


def test_an_agent_left_outside_the_nearest_scope_makes_the_answer_partial(repo):
    files = {
        "app/__init__.py": "",
        "app/tools.py": ADK_TOOLS.replace("BODY", "q"),
        "app/health_agent.py": "from google.adk.agents import Agent\nfrom .tools import ping\n\nroot_agent = Agent(name='health', model='m', tools=[ping])\n",
        "server.py": "from google.adk.agents import Agent\nfrom app.tools import lookup\n\nroot_agent = Agent(name='main', model='m', tools=[lookup])\n",
    }
    base = commit(repo, files)
    head = commit(repo, {"app/tools.py": ADK_TOOLS.replace("BODY", "q.upper()")})
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial"
    assert result["scope_selection"]["relations"][0]["outside"] == ["server.py"]
    assert any("server.py" in limit for limit in result["scope_selection"]["limits"])


def test_an_adk_module_imported_as_from_google_import_adk_is_an_agent_file(repo):
    agent = "from google import adk\nfrom svc.tools import lookup, ping\n\nroot_agent = adk.Agent(name='svc', model='m', tools=[TOOLS])\n"
    base = commit(repo, {"svc/agent.py": agent.replace("TOOLS", "lookup"), "svc/tools.py": ADK_TOOLS.replace("BODY", "q")})
    head = commit(repo, {"svc/agent.py": agent.replace("TOOLS", "lookup, ping")})
    result = run(repo, base, head)
    assert result["comparison_status"] == "compared"
    assert _rows(result) == [("svc", "ping", "added")]


@pytest.mark.parametrize(
    ("files", "scope"),
    [
        (
            {
                "src/myapp/agent.py": "from google.adk.agents import Agent\nfrom myapp.tools.search import lookup\n\n"
                "root_agent = Agent(name='s', model='m', tools=[lookup])\n",
                "src/myapp/tools/search.py": ADK_TOOLS.replace("BODY", "q"),
            },
            "src",
        ),
        (
            {
                "app/__init__.py": "",
                "app/agent.py": "from google.adk.agents import Agent\nfrom app.tools import lookup\nfrom lib.other import ping\n\n"
                "root_agent = Agent(name='s', model='m', tools=[lookup, ping])\n",
                "app/tools.py": ADK_TOOLS.replace("BODY", "q"),
                "lib/__init__.py": "",
                "lib/other.py": "def ping() -> str:\n    return 'p'\n",
            },
            ".",
        ),
    ],
    ids=["namespace-package-under-src", "an-import-outside-the-package"],
)
def test_the_scope_holds_what_the_agent_imports(repo, files, scope):
    changed = next(path for path in files if path.endswith(("search.py", "app/tools.py")))
    base = commit(repo, files)
    head = commit(repo, {changed: ADK_TOOLS.replace("BODY", "q.upper()")})
    result = run(repo, base, head)
    assert result["scope_selection"]["scopes"] == [scope]
    assert result["comparison_status"] == "compared", result["head"]["limits"]
    assert _rows(result) == [("s", "lookup", "changed")]


def test_a_renamed_non_python_file_never_joins_applications(repo):
    files = {}
    for name in ("alpha", "beta"):
        files.update(
            {
                f"{name}/app/__init__.py": "",
                f"{name}/app/tools.py": TOOLS.replace("BODY", "q"),
                f"{name}/app/agent.py": "from google.adk.agents import Agent\nfrom .tools import lookup\n\n"
                f"root_agent = Agent(name='{name}', model='m', tools=[lookup])\n",
            }
        )
    notes = "# notes\n" * 20
    base = commit(repo, {**files, "alpha/app/NOTES.md": notes})
    head = commit(
        repo,
        {
            "alpha/app/tools.py": TOOLS.replace("BODY", "q.upper()"),
            "beta/app/tools.py": TOOLS.replace("BODY", "q.lower()"),
            "alpha/app/NOTES.md": None,
            "docs/NOTES.md": notes,
        },
    )
    assert run(repo, base, head)["scope_selection"]["scopes"] == ["alpha/app", "beta/app"]


def test_an_application_directory_moved_whole_is_one_relocation(repo):
    agent = "from google.adk.agents import Agent\nfrom .tools import lookup\n\nroot_agent = Agent(name='foo', model='m', tools=[lookup])\n"
    base = commit(repo, {"apps/foo/__init__.py": "", "apps/foo/agent.py": agent, "apps/foo/tools.py": TOOLS.replace("BODY", "q")})
    head = commit(
        repo,
        {
            "apps/foo/__init__.py": None, "apps/foo/agent.py": None, "apps/foo/tools.py": None,
            "services/foo/__init__.py": "", "services/foo/agent.py": agent,
            "services/foo/tools.py": TOOLS.replace("BODY", "q"),
        },
    )
    result = run(repo, base, head)
    assert result["base"]["scope"] == "apps/foo"
    assert result["head"]["scope"] == "services/foo"
    assert result["comparison_status"] == "compared"
    assert result["rows"] == []


@pytest.mark.parametrize(
    ("files", "changed"),
    [
        (
            {
                "app/__init__.py": "",
                "app/agent.py": "from google.adk.agents import Agent\nfrom .a import lookup\n\nroot_agent = Agent(name='deep', model='m', tools=[lookup])\n",
                "app/a.py": "from .b import lookup  # noqa: F401\n",
                "app/b.py": "from .c import lookup  # noqa: F401\n",
                "app/c.py": "from .d import lookup  # noqa: F401\n",
                "app/d.py": TOOLS.replace("BODY", "q"),
            },
            "app/d.py",
        ),
        (
            {
                "app/__init__.py": "",
                "app/agent.py": "import importlib\n\nfrom google.adk.agents import Agent\n\n"
                "lookup = importlib.import_module('app.tools').lookup\nroot_agent = Agent(name='deep', model='m', tools=[lookup])\n",
                "app/tools.py": TOOLS.replace("BODY", "q"),
            },
            "app/tools.py",
        ),
        (
            {
                "app/__init__.py": "",
                "app/agent.py": "from google.adk.agents import Agent\nfrom .test_runner import lookup\n\nroot_agent = Agent(name='deep', model='m', tools=[lookup])\n",
                "app/test_runner.py": TOOLS.replace("BODY", "q"),
            },
            "app/test_runner.py",
        ),
    ],
    ids=["four-hops", "literal-dynamic-import", "module-named-like-a-test"],
)
def test_a_change_an_agent_reaches_is_related(repo, files, changed):
    base = commit(repo, files)
    head = commit(repo, {changed: TOOLS.replace("BODY", "q.upper()")})
    assert run(repo, base, head)["scope_selection"]["scopes"] == ["app"]


def test_no_agent_found_within_a_bound_is_partial_not_no_agent(repo, monkeypatch):
    import agents_shipgate.cli.application_scope as module

    monkeypatch.setattr(module, "MAX_AGENT_CANDIDATES", 1)
    files = {**_vesta(), "aaa/notes.py": "agents = 1\n", "aab/more.py": "agents = 2\n"}
    base = commit(repo, files)
    head = commit(repo, {"backend/app/services/gemini_tools.py": TOOLS.replace("BODY", "q.upper()")})
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial"
    assert result["scope_selection"]["limits"]


def test_a_file_that_copies_an_agent_with_its_own_tools_is_an_agent_file(repo):
    """A copy passing its own capabilities is an agent to the readers (#876), so
    a change to it is compared, not answered as having no agent in reach."""

    body = (
        "from agents import Agent, function_tool\nfrom shared import base_agent\n\n\n"
        "@function_tool\ndef quote(item: str) -> str:\n    return item\n\n\n"
        "copy = base_agent.clone(tools=[quote])\n"
    )
    base = commit(repo, {"app/agent.py": body})
    head = commit(repo, {"app/agent.py": body + "# touched\n"})
    result = run(repo, base, head)
    assert result["scope_selection"]["scopes"] == ["app"]
    assert result["comparison_status"] == "partial"


# ---------------------------------------------------------------------------
# #875 review, round 2: a changed file related to no agent is named beside the
# other scopes; an aliased agent class, and a module that rewires an agent,
# are agent files; a changed test does not widen a scope; a relocation holds
# the scopes inside it.

_ADK_TOOL = 'def lookup(q: str) -> str:\n    """Look up."""\n    return BODY\n'


def _adk(name: str, imports: str, tools: str) -> str:
    return f"from google.adk.agents import Agent\n{imports}\n\nroot_agent = Agent(name='{name}', model='m', tools=[{tools}])\n"


_OTHER = {
    "other/__init__.py": "",
    "other/agent.py": _adk("other", "from .tools import lookup", "lookup"),
    "other/tools.py": _ADK_TOOL.replace("BODY", "q"),
}
_OTHER_TOUCHED = {"other/agent.py": "# note\n" + _adk("other", "from .tools import lookup", "lookup")}


def _chain(depth: int) -> dict[str, str]:
    files = {"deep/__init__.py": "", "deep/agent.py": _adk("deep", "from deep.m0 import lookup", "lookup")}
    for index in range(depth):
        files[f"deep/m{index}.py"] = f"from deep.m{index + 1} import lookup\n"
    files[f"deep/m{depth}.py"] = _ADK_TOOL.replace("BODY", "q")
    return files


@pytest.mark.parametrize(
    ("files", "changed", "named"),
    [
        (
            {
                "app/__init__.py": "",
                "app/db.py": "def connect():\n    return 1\n",
                "scripts/migrate.py": "from app.db import connect\n\nconnect()\n",
            },
            {"app/db.py": "def connect():\n    return 2\n"},
            "app/db.py (no agent file imports it",
        ),
        (_chain(7), {"deep/m7.py": _ADK_TOOL.replace("BODY", "q.upper()")}, "deep/m7.py (no agent file reaches it within 6"),
    ],
    ids=["imported-by-a-script", "beyond-the-hop-bound"],
)
def test_a_changed_file_related_to_no_agent_is_named_beside_other_scopes(repo, files, changed, named):
    base = commit(repo, {**files, **_OTHER})
    head = commit(repo, {**changed, **_OTHER_TOUCHED})
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial", result["scope_selection"]
    assert any(named in limit for limit in result["scope_selection"]["limits"]), result["scope_selection"]


@pytest.mark.parametrize(
    "agent",
    [
        "from agents import Agent as SdkAgent, function_tool\n\n\n@function_tool\ndef lookup(q: str) -> str:\n"
        "    return q\n\n\n@function_tool\ndef ping() -> str:\n    return 'p'\n\n\nbot = SdkAgent(name='bot', tools=[TOOLS])\n",
    ],
    ids=["sdk-alias"],
)
def test_an_aliased_agent_class_is_an_agent_file(repo, agent):
    base = commit(repo, {"svc/__init__.py": "", "svc/agent.py": agent.replace("TOOLS", "lookup")})
    head = commit(repo, {"svc/agent.py": agent.replace("TOOLS", "lookup, ping")})
    result = run(repo, base, head)
    assert result["comparison_status"] == "compared", result["scope_selection"]
    assert [(row["agent"], row["tool"], row["change"]) for row in result["rows"]] == [("bot", "ping", "added")]


def test_a_module_that_rewires_an_agent_is_an_agent_file(repo):
    """``server.py`` appends a tool to an app's agent: a change to that tool is
    related through it, never "touches no agent"."""

    files = {
        "app/__init__.py": "",
        "app/agents/__init__.py": "",
        "app/agents/support.py": _adk("support", "from app.tools import lookup", "lookup").replace(
            "root_agent", "support_agent"
        ),
        "app/tools.py": _ADK_TOOL.replace("BODY", "q"),
        "app/danger.py": 'def danger(cmd: str) -> str:\n    """D."""\n    return cmd\n',
        "server.py": "from app.agents.support import support_agent\nfrom app.danger import danger\n\n"
        "support_agent.tools.append(danger)\n",
    }
    base = commit(repo, files)
    head = commit(repo, {"app/danger.py": 'def danger(cmd: str) -> str:\n    """D."""\n    return cmd.upper()\n'})
    result = run(repo, base, head)
    assert result["scope_selection"]["scopes"] == ["."], result["scope_selection"]
    assert result["comparison_status"] == "partial"


def test_a_relocation_holds_the_scopes_inside_it(repo):
    def app(top: str, body: str) -> dict[str, str | None]:
        return {
            f"{top}/foo/zagent.py": _adk("top", "from .ztools import lookup", "lookup"),
            f"{top}/foo/ztools.py": _ADK_TOOL.replace("BODY", "q"),
            f"{top}/foo/sub/aagent.py": _adk("sub", "from .atools import lookup", "lookup"),
            f"{top}/foo/sub/atools.py": _ADK_TOOL.replace("BODY", body),
        }

    base = commit(repo, {**app("apps", "q"), "keep/x.txt": "x\n"})
    head = commit(repo, {**dict.fromkeys(app("apps", "q")), **app("services", "q.upper()")})
    result = run(repo, base, head)
    assert result["scope_selection"]["scopes"] == ["services/foo"], result["scope_selection"]
    assert [(row["agent"], row["tool"], row["change"]) for row in result["rows"]] == [("sub", "lookup", "changed")]


def test_a_changed_test_that_imports_an_agent_does_not_widen_the_scope(repo):
    files = {
        f"{name}/app/{item}": content
        for name in ("alpha", "beta")
        for item, content in (
            ("__init__.py", ""),
            ("agent.py", _adk("assistant", "from .tools import lookup", "lookup")),
            ("tools.py", _ADK_TOOL.replace("BODY", "q")),
        )
    }
    test = "from alpha.app.agent import root_agent\n\n\ndef test_agent():\n    assert root_agent.name{extra}\n"
    base = commit(repo, {**files, "tests/test_alpha.py": test.format(extra=" == 'assistant'")})
    head = commit(
        repo,
        {"beta/app/tools.py": _ADK_TOOL.replace("BODY", "q.lower()"), "tests/test_alpha.py": test.format(extra="")},
    )
    result = run(repo, base, head)
    assert result["scope_selection"]["scopes"] == ["beta/app"], result["scope_selection"]


# ---------------------------------------------------------------------------
# #875 review, round 3: a changed file is exempt only when related to a file
# that builds an agent — not for being inside a compared scope; a changed
# link or submodule is named; an agent's tools under a directory discovery
# skips are related.

def _git(root, *args):
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=T", "-c", "user.email=t@e.com", "-c", "commit.gpgsign=false", *args],
        check=True,
        capture_output=True,
    )


def _head(root) -> str:
    return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()


def test_a_changed_file_inside_a_compared_scope_whose_consumer_is_outside_is_named(repo):
    files = {
        "app/__init__.py": "",
        "app/agent.py": _adk("app", "from .tools import lookup", "lookup"),
        "app/tools.py": _ADK_TOOL.replace("BODY", "q"),
        "app/shared.py": _ADK_TOOL.replace("lookup", "shared_tool").replace("BODY", "q"),
        "lib/__init__.py": "",
        "lib/factory.py": "from google.adk.agents import Agent\n\n\ndef make(name, tools):\n"
        "    return Agent(name=name, model='m', tools=tools)\n",
        "server.py": "from lib.factory import make\nfrom app.shared import shared_tool\n\nroot_agent = make('bot', [shared_tool])\n",
    }
    base = commit(repo, files)
    head = commit(
        repo,
        {
            "app/shared.py": _ADK_TOOL.replace("lookup", "shared_tool").replace("BODY", "q.upper()"),
            "app/agent.py": "# note\n" + _adk("app", "from .tools import lookup", "lookup"),
        },
    )
    result = run(repo, base, head)
    # ``server.py`` builds its agent through ``lib/factory.make``: it is an
    # agent file, so the change is compared where it and the change meet.
    assert result["comparison_status"] == "partial", result["scope_selection"]
    assert result["scope_selection"]["scopes"] == ["."], result["scope_selection"]


def test_a_changed_link_to_a_module_is_named(repo):
    files = {
        "app/__init__.py": "",
        "app/agent.py": _adk("app", "from .tools import lookup", "lookup"),
        "shared/safe.py": _ADK_TOOL.replace("BODY", "q"),
        "shared/danger.py": _ADK_TOOL.replace("BODY", "q.upper()"),
        **_OTHER,
    }
    for path, content in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(content)
    os.symlink("../shared/safe.py", repo / "app/tools.py")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    base = _head(repo)
    (repo / "app/tools.py").unlink()
    os.symlink("../shared/danger.py", repo / "app/tools.py")
    head = commit(repo, _OTHER_TOUCHED)
    assert base != head
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial", result["scope_selection"]
    assert any("app/tools.py (it is a link" in limit for limit in result["scope_selection"]["limits"])


def test_a_changed_submodule_is_named(repo):
    sub = repo / "vendor/tools"
    sub.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(sub)], check=True, capture_output=True)
    (sub / "tools.py").write_text(_ADK_TOOL.replace("BODY", "q"))
    _git(sub, "add", "-A")
    _git(sub, "commit", "-qm", "one")
    base = commit(repo, _OTHER)
    (sub / "tools.py").write_text(_ADK_TOOL.replace("BODY", "q.upper()"))
    _git(sub, "commit", "-qam", "two")
    head = commit(repo, _OTHER_TOUCHED)
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial", result["scope_selection"]
    assert any("vendor/tools (it is a submodule" in limit for limit in result["scope_selection"]["limits"])


def test_an_agents_tools_under_a_skipped_directory_are_related(repo):
    files = {
        "app/__init__.py": "",
        "app/build/__init__.py": "",
        "app/build/tools.py": _ADK_TOOL.replace("BODY", "q"),
        "app/agent.py": _adk("app", "from app.build.tools import lookup", "lookup"),
    }
    base = commit(repo, files)
    head = commit(repo, {"app/build/tools.py": _ADK_TOOL.replace("BODY", "q.upper()")})
    result = run(repo, base, head)
    assert result["comparison_status"] == "compared", result["scope_selection"]
    assert [(row["agent"], row["tool"], row["change"]) for row in result["rows"]] == [("app", "lookup", "changed")]


# ---------------------------------------------------------------------------
# #875 review, round 4: a changed link to a directory is named; a module that
# builds its agent through a builder's factory is an agent file; an agent
# outside the compared scopes whose imports go past the hop bound is named.


def test_a_changed_link_to_a_directory_is_named(repo):
    files = {
        "app/__init__.py": "",
        "app/agent.py": _adk("app", "from app.lib.tools import lookup", "lookup"),
        "shared_v1/__init__.py": "",
        "shared_v1/tools.py": _ADK_TOOL.replace("BODY", "q"),
        "shared_v2/__init__.py": "",
        "shared_v2/tools.py": _ADK_TOOL.replace("BODY", "q.upper()"),
        **_OTHER,
    }
    for path, content in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(content)
    os.symlink("../shared_v1", repo / "app/lib")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    base = _head(repo)
    (repo / "app/lib").unlink()
    os.symlink("../shared_v2", repo / "app/lib")
    head = commit(repo, _OTHER_TOUCHED)
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial", result["scope_selection"]
    assert any("app/lib (it is a link" in limit for limit in result["scope_selection"]["limits"])


def test_a_module_building_its_agent_through_a_factory_is_an_agent_file(repo):
    files = {
        "app/__init__.py": "",
        "app/agent.py": _adk("app", "from .tools import lookup\nfrom . import shared  # noqa: F401", "lookup"),
        "app/tools.py": _ADK_TOOL.replace("BODY", "q"),
        "app/shared.py": _ADK_TOOL.replace("lookup", "shared_tool").replace("BODY", "q"),
        "lib/__init__.py": "",
        "lib/factory.py": "from google.adk.agents import Agent\n\n\ndef make(name, tools):\n"
        "    return Agent(name=name, model='m', tools=tools)\n",
        "server.py": "from lib.factory import make\nfrom app.shared import shared_tool\n\nroot_agent = make('bot', [shared_tool])\n",
    }
    base = commit(repo, files)
    head = commit(repo, {"app/shared.py": _ADK_TOOL.replace("lookup", "shared_tool").replace("BODY", "q.upper()")})
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial", result["scope_selection"]
    assert any(
        "outside app also import the change" in limit and "server.py" in limit
        for limit in result["scope_selection"]["limits"]
    ), result["scope_selection"]


def test_an_agent_outside_the_scopes_importing_past_the_bound_is_named(repo):
    files = {"app/__init__.py": "", "app/agent.py": _adk("app", "from .g import lookup", "lookup")}
    files.update(
        {
            "deep/__init__.py": "",
            "deep/agent.py": _adk("deep", "from deep.m0 import lookup", "lookup"),
            **{f"deep/m{index}.py": f"from deep.m{index + 1} import lookup\n" for index in range(6)},
            "deep/m6.py": "from app.g import lookup\n",
            "app/g.py": _ADK_TOOL.replace("BODY", "q"),
        }
    )
    base = commit(repo, files)
    head = commit(repo, {"app/g.py": _ADK_TOOL.replace("BODY", "q.upper()")})
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial", result["scope_selection"]
    assert any("deep/agent.py" in limit for limit in result["scope_selection"]["limits"])


# ---------------------------------------------------------------------------
# #875 review, round 5: a module outside the compared scopes that imports the
# change and an agent builder is named however it builds its agent.

_FACTORY_BASE = {
    "app/__init__.py": "",
    "app/agent.py": _adk("app", "from .tools import lookup\nfrom . import shared  # noqa: F401", "lookup"),
    "app/tools.py": _ADK_TOOL.replace("BODY", "q"),
    "app/shared.py": _ADK_TOOL.replace("lookup", "shared_tool").replace("BODY", "q"),
    "lib/__init__.py": "",
}


@pytest.mark.parametrize(
    ("factory", "server"),
    [
        (
            "from google.adk.agents import Agent\n\n\nclass CustomAgent:\n    def __init__(self, name, tools):\n"
            "        self.name, self.tools = name, tools\n\n    def build(self):\n"
            "        return Agent(name=self.name, model='m', tools=self.tools)\n",
            "from lib.factory import CustomAgent\nfrom app.shared import shared_tool\n\n"
            "root_agent = CustomAgent('bot', [shared_tool]).build()\n",
        ),
        (
            "from google.adk.agents import Agent\n\n\ndef make(name, tools):\n    return Agent(name=name, model='m', tools=tools)\n",
            "from lib import factory\nfrom app.shared import shared_tool\n\nroot_agent = factory.make('bot', [shared_tool])\n",
        ),
        (
            "from google.adk.agents import Agent\n\n\nclass Factory:\n    @staticmethod\n    def make(name, tools):\n"
            "        return Agent(name=name, model='m', tools=tools)\n",
            "from lib.factory import Factory\nfrom app.shared import shared_tool\n\nroot_agent = Factory.make('bot', [shared_tool])\n",
        ),
        (
            "from google.adk.agents import Agent\n\n\ndef _build(name, tools):\n    return Agent(name=name, model='m', tools=tools)\n\n\n"
            "def make(name, tools):\n    return _build(name, tools)\n",
            "from lib.factory import make\nfrom app.shared import shared_tool\n\nroot_agent = make('bot', [shared_tool])\n",
        ),
    ],
    ids=["wrapper-class", "module-attribute", "static-method", "helper-hop"],
)
def test_a_consumer_outside_the_scopes_is_named_however_it_builds(repo, factory, server):
    base = commit(repo, {**_FACTORY_BASE, "lib/factory.py": factory, "server.py": server})
    head = commit(repo, {"app/shared.py": _ADK_TOOL.replace("lookup", "shared_tool").replace("BODY", "q.upper()")})
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial", result["scope_selection"]
    assert any("server.py" in limit for limit in result["scope_selection"]["limits"]), result["scope_selection"]


# ---------------------------------------------------------------------------
# #875 review, round 6: the consumer is found however far the change travels
# to it, and whatever scope it sits in; the bounds it hits are named.

_WRAPPER = (
    "from google.adk.agents import Agent\n\n\nclass CustomAgent:\n    def __init__(self, name, tools):\n"
    "        self.name, self.tools = name, list(tools)\n\n    def build(self):\n"
    "        return Agent(name=self.name, model='m', tools=self.tools)\n"
)
_SHARED_HEAD = {"app/shared.py": _ADK_TOOL.replace("lookup", "shared_tool").replace("BODY", "q.upper()")}


def _consumer_limits(result):
    return [limit for limit in result["scope_selection"]["limits"] if "import the change" in limit]


@pytest.mark.parametrize(
    "extra",
    [
        {
            "svc/__init__.py": "",
            "svc/c.py": "from lib.factory import CustomAgent\n\n\ndef build_c(tools):\n    return CustomAgent('bot', tools).build()\n",
            "svc/b.py": "from svc.c import build_c\n\n\ndef build_b(tools):\n    return build_c(tools)\n",
            "svc/a.py": "from svc.b import build_b\n\n\ndef build_a(tools):\n    return build_b(tools)\n",
            "server.py": "from svc.a import build_a\nfrom app.shared import shared_tool\n\nroot_agent = build_a([shared_tool])\n",
        },
        {
            "app/__init__.py": "from .shared import shared_tool  # noqa: F401\n",
            "server.py": "from lib.factory import CustomAgent\nfrom app import shared_tool\n\n"
            "root_agent = CustomAgent('bot', [shared_tool]).build()\n",
        },
        {
            "bundles/__init__.py": "",
            "bundles/tools.py": "from app.shared import shared_tool\n\nTOOLS = [shared_tool]\n",
            "server.py": "from lib.factory import CustomAgent\nfrom bundles.tools import TOOLS\n\n"
            "root_agent = CustomAgent('bot', TOOLS).build()\n",
        },
    ],
    ids=["builder-four-hops-away", "package-re-export", "through-another-module"],
)
def test_a_consumer_the_change_reaches_indirectly_is_named(repo, extra):
    base = commit(repo, {**_FACTORY_BASE, "lib/factory.py": _WRAPPER, **extra})
    head = commit(repo, _SHARED_HEAD)
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial", result["scope_selection"]
    assert any("server.py" in limit for limit in _consumer_limits(result)), result["scope_selection"]


def test_a_consumer_inside_another_compared_scope_is_named(repo):
    base = commit(
        repo,
        {
            **_FACTORY_BASE,
            "lib/factory.py": _WRAPPER,
            **_OTHER,
            "other/server.py": "from lib.factory import CustomAgent\nfrom app.shared import shared_tool\n\n"
            "bot_agent = CustomAgent('bot', [shared_tool]).build()\n",
        },
    )
    head = commit(repo, {**_SHARED_HEAD, "other/agent.py": "# note\n" + _OTHER["other/agent.py"]})
    result = run(repo, base, head)
    assert result["scope_selection"]["scopes"] == ["app", "other"]
    assert result["comparison_status"] == "partial", result["scope_selection"]
    assert any("other/server.py" in limit for limit in _consumer_limits(result)), result["scope_selection"]


def test_a_consumer_inside_the_scope_holding_the_change_and_its_builder_is_compared(repo):
    base = commit(
        repo,
        {
            **_FACTORY_BASE,
            "app/factory.py": _WRAPPER,
            "app/server.py": "from .factory import CustomAgent\nfrom .shared import shared_tool\n\n"
            "bot_agent = CustomAgent('bot', [shared_tool]).build()\n",
        },
    )
    head = commit(repo, _SHARED_HEAD)
    result = run(repo, base, head)
    assert _consumer_limits(result) == [], result["scope_selection"]


def test_the_bound_the_consumer_search_hits_is_named(repo, monkeypatch):
    from agents_shipgate.cli import application_scope

    # Relating the change reads less than the bound; searching for its
    # consumers — past modules that mention it — reads more.
    monkeypatch.setattr(application_scope, "MAX_RELATED_FILES", 20)
    wide = {f"wide/mods/m{index}.py": "VALUE = 1\n" for index in range(10)}
    noise = {f"aa_noise/n{index}.py": "# shared\nVALUE = 1\n" for index in range(15)}
    base = commit(
        repo,
        {
            **_FACTORY_BASE,
            **noise,
            "lib/factory.py": _WRAPPER,
            "wide/__init__.py": "",
            "wide/mods/__init__.py": "",
            **wide,
            "wide/agent.py": "from google.adk.agents import Agent\n"
            + "".join(f"import wide.mods.m{index}\n" for index in range(10))
            + "\nroot_agent = Agent(name='wide', model='m', tools=[])\n",
            "server.py": "from lib.factory import CustomAgent\nfrom app.shared import shared_tool\n\n"
            "root_agent = CustomAgent('bot', [shared_tool]).build()\n",
        },
    )
    head = commit(repo, _SHARED_HEAD)
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial", result["scope_selection"]
    assert any("files past the bound were not read" in limit for limit in result["scope_selection"]["limits"])


@pytest.mark.parametrize(
    ("module", "named"),
    [
        (
            # Importing a module's agent is not building one.
            "from app.agent import root_agent\nfrom app.shared import shared_tool  # noqa: F401\n\n\n"
            "def get_agent():\n    return root_agent\n",
            False,
        ),
        (
            # Copying it with the changed tool is.
            "from app.agent import root_agent\nfrom app.shared import shared_tool\n\n"
            "bot_agent = root_agent.clone(tools=[shared_tool])\n",
            True,
        ),
    ],
    ids=["uses-the-agent", "copies-the-agent"],
)
def test_a_module_using_an_agent_is_named_only_when_it_builds_one(repo, module, named):
    base = commit(repo, {**_FACTORY_BASE, "web/__init__.py": "", "web/deps.py": module})
    head = commit(repo, _SHARED_HEAD)
    result = run(repo, base, head)
    assert any("web/deps.py" in limit for limit in result["scope_selection"]["limits"]) is named, result[
        "scope_selection"
    ]


# ---------------------------------------------------------------------------
# #875 review, round 7: the search walks through agent files — one may
# re-export the change, or hand on an agent a consumer copies — while a
# module that only runs what it imports builds nothing.


@pytest.mark.parametrize(
    "server",
    [
        "from lib.factory import CustomAgent\nfrom app.agent import shared_tool\n\nbot = CustomAgent('bot', [shared_tool]).build()\n",
        "from lib.factory import CustomAgent\nfrom app.agent import root_agent\n\nbot = CustomAgent('bot', list(root_agent.tools)).build()\n",
        "from app.agent import root_agent, shared_tool\n\nbot = root_agent.clone(update={'name': 'bot', 'tools': [shared_tool]})\n",
    ],
    ids=["re-exported-by-the-agent-file", "copies-the-agents-tools", "clones-the-agent"],
)
def test_a_consumer_reaching_the_change_through_an_agent_file_is_named(repo, server):
    agent = _adk("app", "from .tools import lookup\nfrom .shared import shared_tool", "lookup, shared_tool")
    base = commit(repo, {**_FACTORY_BASE, "app/agent.py": agent, "lib/factory.py": _WRAPPER, "server.py": server})
    head = commit(repo, _SHARED_HEAD)
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial", result["scope_selection"]
    assert any("server.py" in limit for limit in _consumer_limits(result)), result["scope_selection"]


@pytest.mark.parametrize(
    ("extra", "agent"),
    [
        (
            {"web/__init__.py": "", "web/routes.py": "from app.tools import lookup\nfrom web.deps import get_agent\n\n\n"
             "def index():\n    return lookup('q'), get_agent().name\n",
             "web/deps.py": "from app.agent import root_agent\n\n\ndef get_agent():\n    return root_agent\n"},
            "from google.adk.agents import Agent\nfrom .tools import lookup\n\n\ndef create():\n"
            "    return Agent(name='app', model='m', tools=[lookup])\n\n\nroot_agent = create()\n",
        ),
        (
            {"main.py": "from app.run import main\n\nif __name__ == '__main__':\n    main()\n",
             "app/run.py": "from app.agent import build\n\n\ndef main():\n    return build('bot')\n"},
            "from google.adk.agents import Agent\nfrom .tools import lookup\n\n\ndef build(name):\n"
            "    return Agent(name=name, model='m', tools=[lookup])\n",
        ),
    ],
    ids=["import-time-factory", "entry-script"],
)
def test_a_module_that_only_runs_the_compared_agents_is_not_a_consumer(repo, extra, agent):
    base = commit(
        repo,
        {"app/__init__.py": "", "app/agent.py": agent, "app/tools.py": _ADK_TOOL.replace("BODY", "q"), **extra},
    )
    head = commit(repo, {"app/tools.py": _ADK_TOOL.replace("BODY", "q.upper()")})
    result = run(repo, base, head)
    assert _consumer_limits(result) == [], result["scope_selection"]


# ---------------------------------------------------------------------------
# #875 review, round 8: module state is something to build with, and setting
# it is giving the builder something; a settings model's copy is no agent's.

_STATE_FACTORY = (
    "from google.adk.agents import Agent\n\nTOOLS = []\n\n\ndef register(tool):\n    TOOLS.append(tool)\n\n\n"
    "def _build():\n    return Agent(name='bot', model='m', tools=list(TOOLS))\n\n\ndef make():\n    return _build()\n"
)


@pytest.mark.parametrize(
    ("factory", "server"),
    [
        (_STATE_FACTORY, "from lib.factory import make, register\nfrom app.shared import shared_tool\n\nregister(shared_tool)\nbot = make()\n"),
        (_STATE_FACTORY, "import lib.factory as factory\nfrom app.shared import shared_tool\n\nfactory.TOOLS = [shared_tool]\nbot = factory.make()\n"),
        (
            "from google.adk.agents import Agent\n\nTOOLS = []\n\n\ndef _build(tools):\n"
            "    return Agent(name='bot', model='m', tools=tools)\n\n\ndef make(tools=None):\n    return _build(tools or list(TOOLS))\n",
            "import lib.factory as factory\nfrom app.shared import shared_tool\n\nfactory.TOOLS = [shared_tool]\nbot = factory.make()\n",
        ),
    ],
    ids=["registry", "module-state", "state-read-by-a-factory-taking-arguments"],
)
def test_a_consumer_giving_a_builder_its_module_state_is_named(repo, factory, server):
    base = commit(repo, {**_FACTORY_BASE, "lib/factory.py": factory, "server.py": server})
    head = commit(repo, _SHARED_HEAD)
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial", result["scope_selection"]
    assert any("server.py" in limit for limit in _consumer_limits(result)), result["scope_selection"]


def test_a_settings_models_copy_is_not_an_agents(repo):
    files = {
        "app/__init__.py": "",
        "app/agent.py": _adk("app", "from .tools import lookup", "lookup"),
        "app/tools.py": "PAGE_SIZE = 5\n\n\n" + _ADK_TOOL.replace("BODY", "q"),
        "web/__init__.py": "",
        "web/deps.py": "from app.agent import root_agent\n\n\ndef get_agent():\n    return root_agent\n",
        "web/settings.py": "from pydantic import BaseModel\n\n\nclass Settings(BaseModel):\n    port: int = 8000\n\n\nBASE = Settings()\n",
        "web/routes.py": "from app.tools import PAGE_SIZE\nfrom web.deps import get_agent\nfrom web.settings import BASE\n\n\n"
        "def index(overrides):\n    cfg = BASE.model_copy(update=overrides)\n    return PAGE_SIZE, cfg, get_agent().name\n",
    }
    base = commit(repo, files)
    head = commit(repo, {"app/tools.py": "PAGE_SIZE = 5\n\n\n" + _ADK_TOOL.replace("BODY", "q.upper()")})
    result = run(repo, base, head)
    assert _consumer_limits(result) == [], result["scope_selection"]


# ---------------------------------------------------------------------------
# #875 review, round 9: a builder builds on request when an agent's
# capabilities are not a fixed list of the module's own names, however the
# state that feeds them is spelled; a fixed one takes nothing from a caller.

_DYNAMIC = "from google.adk.agents import Agent\nSTATE\n\n\ndef _build():\n    return Agent(name='bot', model='m', tools=TOOLS)\n\n\ndef make():\n    return _build()\n"


@pytest.mark.parametrize(
    ("factory", "server", "extra"),
    [
        (
            _DYNAMIC.replace("STATE", "\n\nclass Config:\n    tool_list = ()\n\n\nCONFIG = Config()").replace("TOOLS", "list(CONFIG.tool_list)"),
            "import lib.factory as factory\nfrom app.shared import shared_tool\n\nfactory.CONFIG.tool_list = (shared_tool,)\nbot = factory.make()\n",
            {},
        ),
        (
            _DYNAMIC.replace("STATE", "from lib.registry import TOOLS").replace("TOOLS)", "list(TOOLS))"),
            "from lib.factory import make\nfrom lib.registry import TOOLS\nfrom app.shared import shared_tool\n\nTOOLS.append(shared_tool)\nbot = make()\n",
            {"lib/registry.py": "TOOLS = []\n"},
        ),
        (
            _DYNAMIC.replace("STATE", "\nTOOLS = []").replace("TOOLS)", "list(TOOLS))"),
            "import lib.factory as factory\nfrom app.shared import shared_tool\n\nsetattr(factory, 'TOOLS', [shared_tool])\nbot = factory.make()\n",
            {},
        ),
    ],
    ids=["config-object", "imported-state", "setattr"],
)
def test_a_builder_fed_through_module_state_however_spelled_is_named(repo, factory, server, extra):
    base = commit(repo, {**_FACTORY_BASE, "lib/factory.py": factory, "server.py": server, **extra})
    head = commit(repo, _SHARED_HEAD)
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial", result["scope_selection"]
    assert any("server.py" in limit for limit in _consumer_limits(result)), result["scope_selection"]


@pytest.mark.parametrize(
    "route",
    [
        "from app.tools import PAGE_SIZE\nfrom app.agent import get_agent, render\n\n\ndef index(x):\n    return PAGE_SIZE, render(x), get_agent().name\n",
        "from app.tools import PAGE_SIZE\nfrom app import settings\nfrom app.agent import get_agent\n\nsettings.DEBUG = True\n\n\n"
        "def index():\n    return PAGE_SIZE, get_agent().name\n",
    ],
    ids=["cached-builder", "settings-assignment"],
)
def test_a_builder_with_fixed_tools_takes_nothing_from_a_caller(repo, route):
    agent = (
        "from google.adk.agents import Agent\nfrom .tools import lookup\n\n_CACHE = {}\n\n\ndef get_agent():\n"
        "    if 'agent' not in _CACHE:\n        _CACHE['agent'] = Agent(name='app', model='m', tools=[lookup])\n"
        "    return _CACHE['agent']\n\n\ndef render(x):\n    return str(x)\n"
    )
    files = {
        "app/__init__.py": "",
        "app/agent.py": agent,
        "app/settings.py": "DEBUG = False\n",
        "app/tools.py": "PAGE_SIZE = 5\n\n\n" + _ADK_TOOL.replace("BODY", "q"),
        "web/__init__.py": "",
        "web/routes.py": route,
    }
    base = commit(repo, files)
    head = commit(repo, {"app/tools.py": "PAGE_SIZE = 5\n\n\n" + _ADK_TOOL.replace("BODY", "q.upper()")})
    result = run(repo, base, head)
    assert _consumer_limits(result) == [], result["scope_selection"]


# ---------------------------------------------------------------------------
# #875 review, round 10: a builder that gives its agent the caller's
# capabilities after building it — a rewire or a copy — builds on request.


@pytest.mark.parametrize(
    "factory",
    [
        "from google.adk.agents import Agent\n\n\ndef _build(extra):\n    agent = Agent(name='bot', model='m', tools=[])\n"
        "    agent.tools.append(extra)\n    return agent\n\n\ndef make(extra):\n    return _build(extra)\n",
        "from google.adk.agents import Agent\n\n\ndef _build(extra):\n    agent = Agent(name='bot', model='m')\n"
        "    agent.tools = [extra]\n    return agent\n\n\ndef make(extra):\n    return _build(extra)\n",
        "from google.adk.agents import Agent\n\nBASE = Agent(name='base', model='m', tools=[])\n\n\n"
        "def _build(tools):\n    return BASE.clone(update={'name': 'bot', 'tools': tools})\n\n\ndef make(extra):\n    return _build([extra])\n",
    ],
    ids=["appended", "assigned", "cloned"],
)
def test_a_builder_giving_its_agent_the_callers_capabilities_later_is_on_request(repo, factory):
    server = "from lib.factory import make\nfrom app.shared import shared_tool\n\nbot = make(shared_tool)\n"
    base = commit(repo, {**_FACTORY_BASE, "lib/factory.py": factory, "server.py": server})
    head = commit(repo, _SHARED_HEAD)
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial", result["scope_selection"]
    assert any("server.py" in limit for limit in _consumer_limits(result)), result["scope_selection"]


# ---------------------------------------------------------------------------
# #875 review, round 11: a capability change is what the reader counts as one
# — a slice store, a handle changed later — in the builder or its consumer.

_FIXED_FACTORY = (
    "from google.adk.agents import Agent\n\n\ndef _build():\n    return Agent(name='bot', model='m', tools=[])\n\n\n"
    "def make():\n    return _build()\n"
)


@pytest.mark.parametrize(
    ("factory", "server"),
    [
        (
            "from google.adk.agents import Agent\n\n\ndef _build(extra):\n    agent = Agent(name='bot', model='m', tools=[])\n"
            "    agent.tools[:] = [extra]\n    return agent\n\n\ndef make(extra):\n    return _build(extra)\n",
            "from lib.factory import make\nfrom app.shared import shared_tool\n\nbot = make(shared_tool)\n",
        ),
        (
            _FIXED_FACTORY,
            "from lib.factory import make\nfrom app.shared import shared_tool\n\nbot = make()\nbot.tools[:] = [shared_tool]\n",
        ),
        (
            _FIXED_FACTORY,
            "from lib.factory import make\nfrom app.shared import shared_tool\n\nbot = make()\nhandle = bot.tools\nhandle.append(shared_tool)\n",
        ),
    ],
    ids=["slice-store-in-the-builder", "slice-store-by-the-consumer", "handle-changed-by-the-consumer"],
)
def test_a_capability_change_the_reader_counts_counts_for_the_scope(repo, factory, server):
    base = commit(repo, {**_FACTORY_BASE, "lib/factory.py": factory, "server.py": server})
    head = commit(repo, _SHARED_HEAD)
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial", result["scope_selection"]
    assert any("server.py" in limit for limit in result["scope_selection"]["limits"]), result["scope_selection"]
