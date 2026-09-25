"""Review regressions: scoped uncertainty, definition identity and usable recovery."""

import json
import re

import pytest
from test_application_diff import SDK, commit, git, run
from test_application_diff import repo as repo
from typer.testing import CliRunner

from agents_shipgate.cli.main import app


@pytest.mark.parametrize("same_file", [False, True])
@pytest.mark.parametrize("removing", [False, True])
def test_unrelated_dynamic_agent_cannot_hide_a_known_change(repo, same_file, removing):
    other = '\nfrom agents import Agent\nhelper = Agent(name="helper", tools=make_tools())\n'
    before, after = "[lookup]", "[lookup, execute]"
    if removing:
        before, after = after, before
    base = commit(
        repo,
        {
            "agent.py": SDK.replace("TOOLS", before) + (other if same_file else ""),
            **({} if same_file else {"other.py": other}),
        },
    )
    head = commit(repo, {"agent.py": SDK.replace("TOOLS", after) + (other if same_file else "")})
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial"
    assert [(r["tool"], r["change"]) for r in result["rows"]] == [
        ("execute", "removed" if removing else "added")
    ]
    assert all(g["agent"] == "helper" for g in result["base"]["coverage_gaps"])


def test_unrelated_malformed_file_does_not_hide_an_addition(repo):
    base = commit(repo, {"agent.py": SDK.replace("TOOLS", "[lookup]"), "broken.py": "def (\n"})
    head = commit(repo, {"agent.py": SDK.replace("TOOLS", "[lookup, execute]")})
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial"
    assert [(r["tool"], r["change"]) for r in result["rows"]] == [("execute", "added")]


@pytest.mark.parametrize("pattern", ["override", "method"])
def test_reader_definition_identity_detects_body_changes(repo, pattern):
    source = SDK.replace("TOOLS", "[lookup]")
    if pattern == "override":
        source = source.replace(
            "@function_tool\ndef lookup", '@function_tool(name_override="search")\ndef lookup'
        )
    else:
        source += "\nclass Other:\n    def lookup(self, query):\n        return query\n"
    base = commit(repo, {"agent.py": source})
    head = commit(repo, {"agent.py": source.replace("return query\n", "return query.upper()\n", 1)})
    result = run(repo, base, head)
    assert result["comparison_status"] == "compared"
    assert len(result["rows"]) == 1
    row = result["rows"][0]
    assert row["change"] == "changed"
    assert (
        row["before"]["definition"]["implementation_sha256"]
        != row["after"]["definition"]["implementation_sha256"]
    )


def test_unknown_implementation_does_not_hide_an_observed_addition(repo, monkeypatch):
    import agents_shipgate.cli.application_diff as module

    definition = module._definition

    def missing(root, tool):
        value = definition(root, tool)
        if tool.name == "lookup":
            value["implementation_sha256"] = None
        return value

    monkeypatch.setattr(module, "_definition", missing)
    base = commit(repo, {"agent.py": SDK.replace("TOOLS", "[lookup]")})
    head = commit(repo, {"agent.py": SDK.replace("TOOLS", "[lookup, execute]")})
    result = run(repo, base, head)
    assert {r["tool"]: r["change"] for r in result["rows"]} == {
        "lookup": "not_established",
        "execute": "added",
    }
    assert result["base"]["coverage_gaps"][0]["affects"] == "implementation"


def test_uncertainty_belongs_to_base_and_is_not_no_change_text(repo):
    base = commit(repo, {"agent.py": SDK.replace("TOOLS", "make_tools()")})
    head = commit(repo, {"agent.py": SDK.replace("TOOLS", "[lookup]")})
    result = run(repo, base, head)
    assert result["head"]["status"] == "complete"
    assert result["head"]["limits"] == []
    assert list(result["rows"][0]["uncertainty"]) == ["base"]
    text = CliRunner().invoke(
        app, ["diff", "--application", "--workspace", str(repo), "--base", base, "--head", head]
    )
    assert text.exit_code == 0
    assert "NOT_ESTABLISHED" in text.output
    assert "No established" not in text.output


def test_unpaired_agent_move_does_not_hide_another_agents_addition(repo):
    base = commit(
        repo,
        {
            "old.py": SDK.replace("TOOLS", "[lookup]"),
            "stable.py": SDK.replace("TOOLS", "[lookup]").replace("agent =", "worker ="),
        },
    )
    head = commit(
        repo,
        {
            "old.py": None,
            "new.py": "# moved\n" + SDK.replace("TOOLS", "[lookup]"),
            "stable.py": SDK.replace("TOOLS", "[lookup, execute]").replace("agent =", "worker ="),
        },
    )
    rows = run(repo, base, head)["rows"]
    assert [(r["agent"], r["tool"]) for r in rows if r["change"] == "added"] == [
        ("worker", "execute")
    ]
    assert all(r["change"] == "not_established" for r in rows if r["agent"] == "agent")


def test_scoped_archive_materializes_only_selected_blobs(repo, monkeypatch):
    import agents_shipgate.cli.application_diff as module

    archive = module.archive_fetched_tree
    seen = []

    def scoped(workspace, ref, destination, **kwargs):
        archive(workspace, ref, destination, **kwargs)
        assert not (destination / "unrelated.txt").exists()
        seen.append(
            sorted(p.relative_to(destination).as_posix() for p in destination.rglob("*.py"))
        )

    monkeypatch.setattr(module, "archive_fetched_tree", scoped)
    source = SDK.replace("TOOLS", "[lookup]")
    base = commit(repo, {"old/agent.py": source, "unrelated.txt": "large unrelated artifact"})
    head = commit(repo, {"old/agent.py": None, "new/agent.py": source})
    assert run(repo, base, head, "--base-scope", "old", "--scope", "new")["rows"] == []
    assert seen == [["old/agent.py"], ["new/agent.py"]]


def test_moved_scope_names_absence_and_recovery(repo):
    source = SDK.replace("TOOLS", "[lookup]")
    base = commit(repo, {"backend/agent.py": source})
    head = commit(repo, {"backend/agent.py": None, "server/agent.py": "# moved\n" + source})
    result = CliRunner().invoke(
        app,
        [
            "diff",
            "--application",
            "--workspace",
            str(repo),
            "--base",
            base,
            "--head",
            head,
            "--scope",
            "backend",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Scope 'backend' is absent" in result.output
    assert "--base-scope" in result.output


def test_misspelled_scope_is_an_input_error(repo):
    ref = commit(repo, {"backend/agent.py": SDK.replace("TOOLS", "[lookup]")})
    result = CliRunner().invoke(
        app,
        ["diff", "--application", "--workspace", str(repo), "--base", ref, "--scope", "bakend"],
        env={"AGENTS_SHIPGATE_AGENT_MODE": "1"},
    )
    assert result.exit_code == 2
    assert "Neither comparison tree contains" in result.output
    assert "config_error" in result.output


@pytest.mark.parametrize("bound", ["5", "1000"])
def test_explicit_application_bound_requires_application_mode(repo, bound):
    ref = commit(repo, {"README.md": "x"})
    result = CliRunner().invoke(
        app, ["diff", "--workspace", str(repo), "--base", ref, "--max-python-files", bound],
        env={"FORCE_COLOR": "1"},
    )
    assert result.exit_code == 2
    output = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", result.output)
    assert "--application" in output and "--max-python-files" in output


def test_redacted_comparison_identity_is_recomputable(repo, monkeypatch):
    import agents_shipgate.cli.application_diff as module

    ref = commit(repo, {"README.md": "x"})
    monkeypatch.setattr(
        module,
        "observe",
        lambda *a, **kw: module.Observations(
            ".", status="partial", limits=["sk-privacyaaaaaaaaaaaaaaaa"]
        ),
    )
    result = run(repo, ref, ref)
    identity = result.pop("comparison_id")
    assert "sk-privacyaaaaaaaaaaaaaaaa" not in json.dumps(result)
    assert identity == module._digest(result)


@pytest.mark.parametrize("filter_spec", ["blob:none", "tree:0"])
@pytest.mark.parametrize("missing_side", ["base", "head"])
def test_partial_clone_names_side_and_hydration(repo, filter_spec, missing_side):
    from test_capability_diff_partial_clone import _missing_objects, _object_store, _partial_clone

    git(repo, "config", "uploadpack.allowFilter", "true")
    base = commit(repo, {"agent.py": SDK.replace("TOOLS", "[lookup]")})
    head = commit(repo, {"agent.py": SDK.replace("TOOLS", "[lookup, execute]")})
    # Check out only one side before cloning, leaving the other commit's objects promised.
    if missing_side == "head":
        git(repo, "branch", "feature", head)
        git(repo, "reset", "--hard", base)
    clone = _partial_clone(repo, repo.name + "-partial", filter_spec)
    missing_ref = base if missing_side == "base" else head
    assert _missing_objects(clone, missing_ref)
    before = _object_store(clone)
    result = CliRunner().invoke(
        app,
        [
            "diff",
            "--application",
            "--workspace",
            str(clone),
            "--base",
            base,
            "--head",
            head,
            "--json",
        ],
        env={"AGENTS_SHIPGATE_AGENT_MODE": "1"},
    )
    assert result.exit_code == 2, result.output
    assert f"The {missing_side} side" in result.output
    assert "objects_missing" in result.output
    assert "--refetch --no-filter" in result.output
    assert "Traceback" not in result.output
    assert before == _object_store(clone)


def test_imported_tool_gap_is_explicit_and_not_counted_as_complete(repo):
    source = SDK.replace("TOOLS", "[lookup]")
    tool_source = source[: source.index("agent =")]
    agent_source = 'from agents import Agent\nfrom tools import lookup, execute\nagent = Agent(name="app", tools=TOOLS)\n'
    base = commit(
        repo, {"tools.py": tool_source, "agent.py": agent_source.replace("TOOLS", "[lookup]")}
    )
    head = commit(repo, {"agent.py": agent_source.replace("TOOLS", "[lookup, execute]")})
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial"
    assert any("unresolved tool" in reason for reason in result["head"]["limits"])
    assert result["rows"] == []
    text = CliRunner().invoke(
        app, ["diff", "--application", "--workspace", str(repo), "--base", base, "--head", head]
    )
    assert "not a no-change result" in text.output


def test_name_override_does_not_hide_newly_bound_execution_tool(repo):
    source = SDK.replace(
        "@function_tool\ndef lookup", '@function_tool(name_override="search")\ndef lookup'
    )
    base = commit(repo, {"agent.py": source.replace("TOOLS", "[lookup]")})
    head = commit(repo, {"agent.py": source.replace("TOOLS", "[lookup, execute]")})
    result = run(repo, base, head)
    assert result["comparison_status"] == "compared"
    assert [(r["tool"], r["change"]) for r in result["rows"]] == [("execute", "added")]


def test_excluded_unrelated_artifact_does_not_hide_application_change(repo, monkeypatch):
    import agents_shipgate.cli.application_diff as module

    detect = module.detect_workspace

    def with_exclusion(*args, **kwargs):
        detected = detect(*args, **kwargs)
        detected.excluded_sources.append(
            {"path": "unrelated.json", "type": "mcp", "reason": "malformed export"}
        )
        return detected

    monkeypatch.setattr(module, "detect_workspace", with_exclusion)
    base = commit(repo, {"agent.py": SDK.replace("TOOLS", "[lookup]")})
    head = commit(repo, {"agent.py": SDK.replace("TOOLS", "[lookup, execute]")})
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial"
    assert [(r["tool"], r["change"]) for r in result["rows"]] == [("execute", "added")]


def test_scoped_materialization_keeps_link_refusal(repo):
    ref = commit(repo, {"real/agent.py": SDK.replace("TOOLS", "[lookup]")})
    (repo / "alias").symlink_to("real")
    git(repo, "add", "alias")
    ref = commit(repo, {})
    result = CliRunner().invoke(
        app, ["diff", "--application", "--workspace", str(repo), "--base", ref, "--scope", "alias"]
    )
    assert result.exit_code == 2
    assert "not a regular directory" in result.output



def test_non_promisor_git_error_is_normalized_at_materialization_boundary(repo, monkeypatch):
    import subprocess

    from agents_shipgate.cli.verify import git as module

    ref = commit(repo, {"agent.py": SDK.replace("TOOLS", "[lookup]")})

    def broken(*args, **kwargs):
        raise subprocess.CalledProcessError(128, ["git", "rev-parse"])

    monkeypatch.setattr(module, "archive_tree", broken)
    monkeypatch.setattr(module, "promised_objects_missing", lambda *a: False)
    result = CliRunner().invoke(app, ["diff", "--application", "--workspace", str(repo),
                                     "--base", ref, "--json"],
                                env={"AGENTS_SHIPGATE_AGENT_MODE": "1"})
    assert result.exit_code == 2
    assert "config_error" in result.output
    assert "Traceback" not in result.output
