"""Reach regressions: inputs the application reader never follows cannot refuse it.

Measured on 134 open third-party SDK/ADK PRs (2026-09-25). At the default
root scope, 15 of the first 27 exited 2 on a link or submodule unrelated to the
application: ``CLAUDE.md -> AGENTS.md``, a linked skill directory, a ``VERSION``
link that leaves the tree, a vendored gitlink. ``from google.adk import Agent``
read as "no supported agents".
"""

import os

import pytest
from test_application_diff import SDK, commit, git, run
from test_application_diff import repo as repo

LINKS = {
    # dlt-hub/dlt#4417, jaegertracing/jaeger#9636, omnigent-ai/omnigent#6611
    "in_tree_file": ("CLAUDE.md", "AGENTS.md"),
    # asterinas#3834, vllm#57322, kagent-dev/kagent#2788
    "in_tree_directory": (".claude/skills/review", "../../.agents/skills/review"),
    # TencentCloud/CubeSandbox#1508: agent/VERSION -> ../../VERSION
    "leaves_tree": ("agent/VERSION", "../../VERSION"),
    "absolute": (".pylintrc", "/etc/pylintrc"),
    "dangling": ("docs/latest", "missing/latest"),
}


def link(repo, path, target):
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    os.symlink(target, repo / path)
    git(repo, "add", path)


def gitlink(repo, path, commit_id):
    # An unpopulated submodule's empty directory keeps `add -A` from staging
    # the gitlink's deletion.
    (repo / path).mkdir(parents=True, exist_ok=True)
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{commit_id},{path}")


@pytest.mark.parametrize("shape", sorted(LINKS))
def test_root_scope_reads_past_a_link_python_discovery_never_follows(repo, shape):
    path, target = LINKS[shape]
    commit(repo, {"AGENTS.md": "# notes\n", ".agents/skills/review/SKILL.md": "# review\n"})
    link(repo, path, target)
    base = commit(repo, {"agent.py": SDK.replace("TOOLS", "[lookup]")})
    head = commit(repo, {"agent.py": SDK.replace("TOOLS", "[lookup, execute]")})
    result = run(repo, base, head)
    assert result["comparison_status"] == "compared", result["head"]["limits"]
    assert [(r["agent"], r["tool"], r["change"]) for r in result["rows"]] == [
        ("agent", "execute", "added")
    ]
    assert result["base"]["limits"] == result["head"]["limits"] == []


def test_python_link_to_an_input_already_read_is_not_read_twice(repo):
    # Read through, the alias made one agent two ambiguous ones and hid the
    # real file's change: `partial` with no row, as a narrow scope on main did.
    source = SDK.replace("agent = Agent", "helper = Agent").replace('name="assistant"', 'name="helper"')
    commit(repo, {"real/helper.py": source.replace("TOOLS", "[lookup]")})
    link(repo, "helper.py", "real/helper.py")
    base = commit(repo, {})
    head = commit(repo, {"real/helper.py": source.replace("TOOLS", "[lookup, execute]")})
    result = run(repo, base, head)
    assert result["comparison_status"] == "compared", result["head"]["limits"]
    assert [(r["agent_source"], r["tool"], r["change"]) for r in result["rows"]] == [
        ("real/helper.py", "execute", "added")
    ]
    assert result["head"]["sources"] == [{"type": "openai_agents_sdk", "path": "real/helper.py"}]


@pytest.mark.parametrize("target", ["leaves_scope", "not_python", "dangling"])
def test_python_link_to_anything_else_is_a_gap_over_its_path(repo, target):
    # Never read through, and never silence: the link's own path is unread,
    # and only that path. The unrelated addition stays established.
    if target == "leaves_scope":
        commit(repo, {"shared/tools.py": SDK.replace("TOOLS", "[lookup]")})
        link(repo, "app/tools.py", "../shared/tools.py")
    elif target == "not_python":
        commit(repo, {"app/tools_impl": SDK.replace("TOOLS", "[lookup]")})
        link(repo, "app/tools.py", "tools_impl")
    else:
        link(repo, "app/tools.py", "missing.py")
    base = commit(repo, {"app/agent.py": SDK.replace("TOOLS", "[lookup]")})
    head = commit(repo, {"app/agent.py": SDK.replace("TOOLS", "[lookup, execute]")})
    result = run(repo, base, head, "--scope", "app")
    assert result["comparison_status"] == "partial"
    assert [(r["agent_source"], r["tool"], r["change"]) for r in result["rows"]] == [
        ("agent.py", "execute", "added")
    ]
    assert result["head"]["sources"] == [{"type": "openai_agents_sdk", "path": "agent.py"}]
    for side in ("base", "head"):
        assert [(g["source"], g["reason"]) for g in result[side]["coverage_gaps"]] == [
            ("tools.py", "Linked Python input: tools.py")
        ]


@pytest.mark.parametrize("direction", ["file_to_link", "link_to_file"])
def test_python_file_replaced_by_a_dangling_link_is_not_a_removal(repo, direction):
    # PR #877 review: discovery drops a dangling link before any census, so
    # replacing `agent.py` with `agent.py -> missing.py` read as a definite
    # removal, `compared`, with no gap.
    source = {"app/agent.py": SDK.replace("TOOLS", "[lookup]")}
    if direction == "file_to_link":
        base = commit(repo, source)
        (repo / "app/agent.py").unlink()
        link(repo, "app/agent.py", "missing.py")
        head = commit(repo, {})
    else:
        link(repo, "app/agent.py", "missing.py")
        base = commit(repo, {})
        (repo / "app/agent.py").unlink()
        head = commit(repo, source)
    result = run(repo, base, head, "--scope", "app")
    assert result["comparison_status"] == "partial"
    candidate = "removed" if direction == "file_to_link" else "added"
    assert [(r["tool"], r["change"], r["candidate_change"]) for r in result["rows"]] == [
        ("lookup", "not_established", candidate)
    ]
    side = "head" if direction == "file_to_link" else "base"
    assert result["rows"][0]["uncertainty"] == {side: ["Linked Python input: agent.py"]}


@pytest.mark.parametrize("direction", ["directory_to_link", "link_to_directory"])
@pytest.mark.parametrize("target", ["/opt/tools", "../../../outside/tools", "missing"])
def test_source_directory_replaced_by_an_unresolved_link_is_not_a_removal(
    repo, direction, target
):
    source = {"app/tools/agent.py": SDK.replace("TOOLS", "[lookup]"), "app/README.md": "x"}
    if direction == "directory_to_link":
        base = commit(repo, source)
        git(repo, "rm", "-rq", "app/tools")
        link(repo, "app/tools", target)
        head = commit(repo, {})
    else:
        link(repo, "app/tools", target)
        base = commit(repo, {"app/README.md": "x"})
        git(repo, "rm", "-q", "app/tools")
        head = commit(repo, source)
    result = run(repo, base, head, "--scope", "app")
    assert result["comparison_status"] == "partial"
    assert [(r["tool"], r["change"]) for r in result["rows"]] == [("lookup", "not_established")]
    side = "head" if direction == "directory_to_link" else "base"
    assert result["rows"][0]["uncertainty"] == {
        side: ["Linked input resolves outside the tree: tools"]
    }


def test_unchanged_unresolved_link_beside_the_application_is_not_a_gap(repo):
    # `agent/VERSION -> ../../VERSION` (CubeSandbox#1508) is the same on both
    # sides and replaced nothing the other side reads.
    link(repo, "app/VERSION", "../../VERSION")
    base = commit(repo, {"app/agent.py": SDK.replace("TOOLS", "[lookup]")})
    head = commit(repo, {"app/agent.py": SDK.replace("TOOLS", "[lookup, execute]")})
    result = run(repo, base, head, "--scope", "app")
    assert result["comparison_status"] == "compared"
    assert result["base"]["limits"] == result["head"]["limits"] == []


def test_directory_link_leaving_the_scope_with_python_is_a_gap(repo):
    commit(repo, {"lib/helpers.py": SDK.replace("TOOLS", "[lookup]")})
    link(repo, "app/lib", "../lib")
    base = commit(repo, {"app/agent.py": SDK.replace("TOOLS", "[lookup]")})
    head = commit(repo, {"app/agent.py": SDK.replace("TOOLS", "[lookup, execute]")})
    result = run(repo, base, head, "--scope", "app")
    assert result["comparison_status"] == "partial"
    assert [(r["tool"], r["change"]) for r in result["rows"]] == [("execute", "added")]
    assert [(g["source"], g["reason"]) for g in result["head"]["coverage_gaps"]] == [
        ("lib", "Linked directory holds Python outside the scope: lib")
    ]


@pytest.mark.parametrize("scope", [".", "app"])
def test_unchanged_submodule_is_named_without_refusing(repo, scope):
    # temporalio/sdk-python#1868: temporalio/bridge/sdk-core is a gitlink the
    # PR does not move. The same commit on both sides is the same content, so
    # it cannot carry a change; it is named, not read and not refused.
    vendored = commit(repo, {"README.md": "vendored"})
    gitlink(repo, "app/vendor/core", vendored)
    base = commit(repo, {"app/agent.py": SDK.replace("TOOLS", "[lookup]")})
    head = commit(repo, {"app/agent.py": SDK.replace("TOOLS", "[lookup, execute]")})
    result = run(repo, base, head, "--scope", scope)
    assert result["comparison_status"] == "compared"
    assert [(r["tool"], r["change"]) for r in result["rows"]] == [("execute", "added")]
    vendor = "app/vendor/core" if scope == "." else "vendor/core"
    for side in ("base", "head"):
        assert result[side]["coverage_gaps"] == []
        assert result[side]["limits"] == [
            f"Submodule content is not read (unchanged commit {vendored[:12]}): {vendor}"
        ]


def test_submodule_behind_a_link_out_of_scope_is_not_the_scopes(repo):
    # The scoped materializer brings the target of an in-scope directory link,
    # gitlinks beneath it included. Discovery never walks through that link,
    # so they are not this scope's submodules and name nothing.
    vendored = commit(repo, {"README.md": "vendored"})
    gitlink(repo, "lib/vendor", vendored)
    commit(repo, {"lib/README.md": "shared"})
    link(repo, "app/lib", "../lib")
    base = commit(repo, {"app/agent.py": SDK.replace("TOOLS", "[lookup]")})
    head = commit(repo, {"app/agent.py": SDK.replace("TOOLS", "[lookup, execute]")})
    result = run(repo, base, head, "--scope", "app")
    assert result["comparison_status"] == "compared"
    assert [(r["tool"], r["change"]) for r in result["rows"]] == [("execute", "added")]
    assert result["base"]["limits"] == result["head"]["limits"] == []


@pytest.mark.parametrize("direction", ["submodule_to_directory", "directory_to_submodule"])
def test_submodule_at_the_selected_scope_covers_the_whole_scope(repo, direction):
    # PR #877 review: a gitlink at the scope itself named source ".", which
    # covered no relative binding path, so its unread content let `agent.py`
    # read as a definite addition or removal.
    vendored = commit(repo, {"README.md": "vendored"})
    source = {"app/agent.py": SDK.replace("TOOLS", "[lookup]")}
    if direction == "submodule_to_directory":
        gitlink(repo, "app", vendored)
        base = commit(repo, {})
        git(repo, "rm", "-q", "--cached", "app")
        head = commit(repo, source)
    else:
        base = commit(repo, source)
        git(repo, "rm", "-rq", "app")
        gitlink(repo, "app", vendored)
        head = commit(repo, {})
    result = run(repo, base, head, "--scope", "app")
    assert result["comparison_status"] == "partial"
    assert [(r["tool"], r["change"]) for r in result["rows"]] == [("lookup", "not_established")]
    side = "base" if direction == "submodule_to_directory" else "head"
    reason = f"Submodule content is not read (commit {vendored[:12]}): the selected scope"
    assert result["rows"][0]["uncertainty"] == {side: [reason]}
    assert [(g["source"], g["reason"]) for g in result[side]["coverage_gaps"]] == [(None, reason)]


@pytest.mark.parametrize("move", ["added", "bumped", "removed"])
def test_moved_submodule_is_an_attributed_gap_not_a_refusal(repo, move):
    first = commit(repo, {"README.md": "first"})
    second = commit(repo, {"README.md": "second"})
    if move != "added":
        gitlink(repo, "vendor/core", first)
    base = commit(repo, {"agent.py": SDK.replace("TOOLS", "[lookup]")})
    if move == "bumped":
        gitlink(repo, "vendor/core", second)
    elif move == "removed":
        git(repo, "rm", "-q", "--cached", "vendor/core")
    else:
        gitlink(repo, "vendor/core", second)
    head = commit(repo, {"agent.py": SDK.replace("TOOLS", "[lookup, execute]")})
    result = run(repo, base, head)
    # Not `compared`: whatever moved inside the submodule is unobserved.
    assert result["comparison_status"] == "partial"
    # The known addition elsewhere stays visible (#871 fail-closed granularity).
    assert [(r["tool"], r["change"]) for r in result["rows"]] == [("execute", "added")]
    sides = {"added": ["head"], "bumped": ["base", "head"], "removed": ["base"]}[move]
    for side in ("base", "head"):
        gaps = result[side]["coverage_gaps"]
        if side in sides:
            assert [(g["source"], g["affects"]) for g in gaps] == [
                ("vendor/core", "binding_presence")
            ]
            assert gaps[0]["reason"].startswith("Submodule content is not read")
        else:
            assert gaps == []


def test_adk_package_root_agent_import_is_an_agent(repo):
    # TencentCloud/CubeSandbox#1508 imports the constructor from the package
    # root, which re-exports google.adk.agents.llm_agent.Agent.
    source = '''from google.adk import Agent

def lookup(query: str) -> str:
    return query

def execute(code: str) -> str:
    return code

root_agent = Agent(name="helper", tools=TOOLS)
'''
    base = commit(repo, {"agent.py": source.replace("TOOLS", "[lookup]")})
    head = commit(repo, {"agent.py": source.replace("TOOLS", "[lookup, execute]")})
    result = run(repo, base, head)
    assert result["comparison_status"] == "compared"
    assert [(r["agent"], r["tool"], r["change"]) for r in result["rows"]] == [
        ("helper", "execute", "added")
    ]


def test_adk_package_module_alias_is_an_agent(repo):
    source = '''import google.adk as adk

def lookup(query: str) -> str:
    return query

root_agent = adk.Agent(name="helper", tools=TOOLS)
'''
    base = commit(repo, {"agent.py": source.replace("TOOLS", "[]")})
    head = commit(repo, {"agent.py": source.replace("TOOLS", "[lookup]")})
    result = run(repo, base, head)
    assert [(r["agent"], r["tool"], r["change"]) for r in result["rows"]] == [
        ("helper", "lookup", "added")
    ]


def test_adk_package_root_agent_with_sibling_tool_is_compared(repo):
    # The CubeSandbox#1508 shape itself: the tool comes from a sibling module.
    # Before #864 it was a named gap on an established agent; the reader now
    # follows the import, so the binding is an established addition.
    tool = "def run_python_in_cube(code: str) -> str:\n    return code\n"
    agent = (
        "from google.adk import Agent\n"
        "from cube_code_tool import run_python_in_cube\n"
        'root_agent = Agent(name="cube_code_agent", tools=[run_python_in_cube])\n'
    )
    base = commit(repo, {"README.md": "examples"})
    head = commit(
        repo, {"examples/adk/agent.py": agent, "examples/adk/cube_code_tool.py": tool}
    )
    result = run(repo, base, head)
    assert result["comparison_status"] == "compared"
    assert [a["name"] for a in result["head"]["agents"]] == ["cube_code_agent"]
    assert result["head"]["coverage_gaps"] == []
    (row,) = result["rows"]
    assert (row["agent"], row["tool"], row["change"]) == (
        "cube_code_agent",
        "run_python_in_cube",
        "added",
    )
    assert row["after"]["definition"]["source"] == "examples/adk/cube_code_tool.py"


def test_recording_gitlinks_is_opt_in_at_the_materializer(repo, tmp_path_factory):
    # Only the application route opts in; every other archive caller still
    # refuses a gitlink it would have to read through.
    from agents_shipgate.cli.verify.git import archive_tree
    from agents_shipgate.core.errors import ConfigError

    vendored = commit(repo, {"README.md": "vendored"})
    gitlink(repo, "vendor/core", vendored)
    ref = commit(repo, {"agent.py": SDK.replace("TOOLS", "[lookup]")})
    with pytest.raises(ConfigError, match="unsupported external binding at vendor/core"):
        archive_tree(repo, ref, tmp_path_factory.mktemp("refused"), scope=lambda _: True)
    destination = tmp_path_factory.mktemp("recorded")
    recorded = archive_tree(
        repo, ref, destination, scope=lambda _: True, record_gitlinks=True
    )
    assert recorded == {"vendor/core": vendored}
    assert (destination / "vendor/core").is_dir()
    assert not any((destination / "vendor/core").iterdir())
    assert (destination / "agent.py").read_text() == SDK.replace("TOOLS", "[lookup]")
