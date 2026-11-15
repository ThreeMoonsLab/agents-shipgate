"""#1146: a binding that reads the same on both sides is not a candidate change.

On the 49-PR development corpus 555 of 868 rows were candidate ``changed`` rows
whose compared meaning was equal on both sides: every tool of an agent whose
rows were withheld was listed as changed, with the fact "binding details
changed". Such a binding is not a row. What is not established about it is
said once, for its agent (``read_same``), and the comparison stays
``partial``. These tests hold that:

- the OpenAI Agents SDK README shape yields one row, the added tool, and one
  agent-level statement naming the two bindings that read the same and why;
- the one test of a difference is ``_differs`` (the engine's ``_meaning``),
  and every row the engine emits for a change says what changed;
- a callable read on neither side does not read the same, so it stays a row.
"""

from __future__ import annotations

import copy
import gzip
import json
from pathlib import Path

import pytest
from test_application_diff import SDK, commit, run
from test_application_diff import repo as repo
from typer.testing import CliRunner

from agents_shipgate.cli.application_diff import _differs, _meaning, _reads_the_same
from agents_shipgate.cli.application_summary import _what_changed, build_summary, summary_lines
from agents_shipgate.cli.main import app

ROOT = Path(__file__).resolve().parents[1]
GOLDENS = ROOT / "benchmark" / "application-q2" / "goldens"

README = '''import asyncio

from agents import Agent, Runner, function_tool


@function_tool
def lookup_order(order_id: str) -> str:
    """Look up an order."""
    return f"order {order_id}"


@function_tool
def track_shipment(order_id: str) -> str:
    """Track a shipment."""
    return f"tracking {order_id}"


@function_tool
def issue_refund(order_id: str, amount: float) -> str:
    """Refund an order."""
    return f"refunded {amount} on {order_id}"


support_agent = Agent(
    name="support",
    instructions="Help customers with orders.",
    tools=TOOLS,
)


async def main() -> None:
    result = await Runner.run(support_agent, "where is order 42?")
    print(result.final_output)


if __name__ == "__main__":
    asyncio.run(main())
'''
BASE_TOOLS = "[lookup_order, track_shipment]"
HEAD_TOOLS = "[lookup_order, track_shipment, issue_refund]"


def _text(repo, base: str, head: str) -> str:
    result = CliRunner().invoke(
        app, ["diff", "--application", "--workspace", str(repo), "--base", base, "--head", head]
    )
    assert result.exit_code == 0, result.output
    return result.output


def assert_every_change_says_what(payload: dict) -> None:
    """A row for a change, established or candidate, names what changed."""

    for row in payload["rows"]:
        kind = row["candidate_change"] if row["change"] == "not_established" else row["change"]
        if kind != "changed":
            continue
        assert row["before"] is not None and row["after"] is not None, row
        assert _what_changed(row["before"], row["after"]), (row["agent"], row["tool"])


def test_the_readme_shape_is_one_row_and_one_statement_about_the_agent(repo):
    base = commit(repo, {"app.py": README.replace("TOOLS", BASE_TOOLS)})
    head = commit(repo, {"app.py": README.replace("TOOLS", HEAD_TOOLS)})
    result = run(repo, base, head)

    assert result["comparison_status"] == "partial"
    assert [(r["tool"], r["change"], r["candidate_change"]) for r in result["rows"]] == [
        ("issue_refund", "not_established", "added")
    ]
    [same] = result["read_same"]
    assert (same["agent"], same["agent_source"], same["tools"]) == (
        "support_agent",
        "app.py",
        ["lookup_order", "track_shipment"],
    )
    assert sorted(same["uncertainty"]) == ["base", "head"]
    reasons = [reason for items in same["uncertainty"].values() for reason in items]
    assert reasons and all("app.py:32" in reason for reason in reasons)

    [finding] = result["summary"]["findings"]
    assert finding["status"] == "partial"
    assert [tool["tool"] for tool in finding["tools"]] == ["issue_refund"]
    assert finding["read_same"]["count"] == 2
    assert finding["read_same"]["tools"] == ["lookup_order", "track_shipment"]
    assert finding["read_same"]["at"] == "app.py:32"
    assert "read_same" not in result["summary"]
    assert result["summary"]["counts"]["total"] == 1
    assert_every_change_says_what(result)

    text = _text(repo, base, head)
    assert "Findings: 1 changed agent, 1 row (1 not established)." in text
    assert "support_agent [partial]: ?+issue_refund (" in text
    assert (
        "  2 bindings read the same on both sides (lookup_order, track_shipment); whether "
        "support_agent holds them unchanged is not established: the constructor identity is not "
        "established: a framework constructor or namespace is retained or used opaquely in app.py:32\n"
    ) in text
    # The statement is made once, and no unchanged tool is a row or a candidate.
    assert text.count("read the same on both sides") == 1
    assert "lookup_order (candidate" not in text and "binding details changed" not in text
    assert "NOT_ESTABLISHED  support_agent → lookup_order" not in text


def test_an_agent_whose_bindings_all_read_the_same_has_no_row_and_is_not_a_no_change_result(repo):
    # cuppibla/adk_tutorial#7's shape: the change edits nothing the agent binds.
    base = commit(repo, {"app.py": README.replace("TOOLS", BASE_TOOLS)})
    head = commit(
        repo,
        {"app.py": README.replace("TOOLS", BASE_TOOLS).replace("Help customers", "Help our customers")},
    )
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial"
    assert result["rows"] == []
    assert [(e["agent"], e["tools"]) for e in result["read_same"]] == [
        ("support_agent", ["lookup_order", "track_shipment"])
    ]
    summary = result["summary"]
    assert summary["findings"] == []
    [item] = summary["read_same"]
    assert (item["agent"], item["agent_source"], item["count"]) == ("support_agent", "app.py", 2)
    text = _text(repo, base, head)
    assert "Read the same, not established: 2 bindings on 1 agent with no row." in text
    assert "  2 bindings read the same on both sides (lookup_order, track_shipment); whether support_agent" in text
    assert "Incomplete comparison; this is not a no-change result." in text
    assert "Findings:" not in text


def test_a_binding_that_reads_the_same_with_nothing_unread_is_not_named(repo):
    base = commit(repo, {"agent.py": SDK.replace("TOOLS", "[lookup]")})
    head = commit(repo, {"agent.py": SDK.replace("TOOLS", "[lookup, execute]")})
    result = run(repo, base, head)
    assert result["comparison_status"] == "compared", result["head"]["limits"]
    assert [(r["tool"], r["change"]) for r in result["rows"]] == [("execute", "added")]
    assert "read_same" not in result
    assert "read_same" not in result["summary"]
    assert "read_same" not in result["summary"]["findings"][0]


def test_each_application_names_what_reads_the_same_from_the_repository_root(repo):
    files = {}
    for name in ("alpha", "beta"):
        files[f"{name}/app.py"] = README.replace("TOOLS", BASE_TOOLS)
    base = commit(repo, files)
    head = commit(repo, {f"{name}/app.py": README.replace("TOOLS", HEAD_TOOLS) for name in ("alpha", "beta")})
    result = run(repo, base, head)
    assert [item["head"]["scope"] for item in result["comparisons"]] == ["alpha", "beta"]
    assert sorted((r["agent_source"], r["tool"]) for r in result["rows"]) == [
        ("alpha/app.py", "issue_refund"),
        ("beta/app.py", "issue_refund"),
    ]
    assert [(e["agent_source"], e["tools"]) for e in result["read_same"]] == [
        ("alpha/app.py", ["lookup_order", "track_shipment"]),
        ("beta/app.py", ["lookup_order", "track_shipment"]),
    ]
    # Each comparison keeps its own, spelled from its scope.
    assert [item["read_same"][0]["agent_source"] for item in result["comparisons"]] == ["app.py", "app.py"]
    findings = result["summary"]["findings"]
    assert [(f["agent_source"], f["read_same"]["count"]) for f in findings] == [
        ("alpha/app.py", 2),
        ("beta/app.py", 2),
    ]


def _binding(**extra) -> dict:
    """A binding with every field the engine records on one side."""

    return {
        "agent": "a",
        "agent_source": "app.py",
        "tool": "t",
        "binding_location": "app.py:9",
        "edge_type": "direct_tool",
        "definition": {"source": "app.py", "line": 3, "implementation_sha256": "a" * 64},
        "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
        "output_schema": {"type": "string"},
        "signature": "t(q) -> str",
        "evidence_basis": "ast_extraction",
        "import_path": [],
        "bound_when": ["DEBUG"],
        "construction_sites": ["app.py:9"],
        "reach": {"calls": [], "effects": [], "limits": [], "effect_claims": []},
        "effect_evidence": {"conservative_effect": "write", "status": "unknown", "claims": []},
        "object": {"kind": "mcp_server", "transport": "stdio"},
        "object_evidence": {},
        "agent_settings": {"tool_use_behavior": "run_llm_again"},
        "target_source": "worker.py",
        **extra,
    }


#: What a changed value of each part of the compared meaning looks like.
_CHANGED = {
    "edge_type": "toolset",
    "input_schema": {"type": "object", "properties": {"q": {"type": "string"}, "n": {"type": "integer"}}},
    "output_schema": {"type": "integer"},
    "signature": "t(q, n) -> str",
    "bound_when": ["PROD"],
    "object": {"kind": "mcp_server", "transport": "sse"},
    "agent_settings": {"tool_use_behavior": "stop_on_first_tool"},
}
#: The binding's identity: one key, never a difference between two sides of it.
_IDENTITY = {"agent", "tool", "target_source"}


def test_the_compared_meaning_is_the_one_listed_here():
    # A part added to the compared meaning needs a way to say it changed.
    assert set(_meaning(_binding())) == {*_CHANGED, *_IDENTITY, "implementation_sha256"}


@pytest.mark.parametrize("part", sorted({*_CHANGED, "implementation_sha256"}))
def test_every_part_of_the_compared_meaning_that_differs_says_what_changed(part):
    before = _binding()
    after = copy.deepcopy(before)
    if part == "implementation_sha256":
        after["definition"]["implementation_sha256"] = "b" * 64
    else:
        after[part] = _CHANGED[part]
    assert _differs(before, after)
    assert _what_changed(before, after), part


def test_only_evidence_differs_is_not_a_difference():
    before = _binding()
    after = _binding(
        binding_location="app.py:40",
        definition={"source": "tools.py", "line": 12, "implementation_sha256": "a" * 64},
        construction_sites=["app.py:40", "app.py:52"],
        evidence_basis="structural",
        reach={"calls": [{"method": "GET", "url": "https://x", "at": "t.py:1", "via": []}]},
    )
    assert not _differs(before, after)
    assert _what_changed(before, after) == []


def test_an_implementation_read_on_neither_side_does_not_read_the_same():
    # Equal meanings, but no digest on either side is no reading: the body
    # may have changed unseen, so the binding stays a candidate row.
    unread = {"source": "app.py", "line": None, "implementation_sha256": None}
    before, after = _binding(definition=unread), _binding(definition=dict(unread))
    assert not _differs(before, after)
    assert not _reads_the_same(before, after)
    assert _what_changed(before, after) == [
        "implementation not read on either side; whether it changed is not established"
    ]
    # A handoff has no definition to read; equal is the same.
    handoff = {key: value for key, value in _binding().items() if key != "definition"}
    assert _reads_the_same(handoff, copy.deepcopy(handoff))


def test_a_handoff_target_moved_by_the_change_is_not_a_difference():
    before = _binding(target_source="old/worker.py")
    after = _binding(target_source="new/worker.py")
    assert _differs(before, after)
    assert not _differs(before, after, {"old/worker.py": "new/worker.py"})


def test_a_row_with_nothing_to_say_is_caught():
    # The guard the corpus checks rely on: the old fallback text is gone, so a
    # row whose sides read the same has nothing to say and fails it.
    same = _binding()
    row = {
        "agent": "a",
        "agent_source": "app.py",
        "tool": "t",
        "change": "not_established",
        "candidate_change": "changed",
        "uncertainty": {"head": ["Agent 'a' is built twice."]},
        "before": same,
        "after": copy.deepcopy(same),
    }
    with pytest.raises(AssertionError):
        assert_every_change_says_what({"rows": [row]})
    [tool] = build_summary({"rows": [row], "base": {}, "head": {}})["findings"][0]["tools"]
    assert not [fact for fact in tool["facts"] if fact["kind"] == "change"]


@pytest.mark.parametrize("path", sorted(GOLDENS.glob("*.input.json.gz")), ids=lambda p: p.name.split(".")[0])
def test_every_golden_change_row_says_what_changed(path):
    payload = json.loads(gzip.decompress(path.read_bytes()))
    assert_every_change_says_what(payload)
    for line in summary_lines(payload["summary"]):
        assert "binding details changed" not in line
