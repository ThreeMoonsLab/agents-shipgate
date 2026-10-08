"""#914: the reviewer-first reading of an application comparison.

``diff --application`` is exact and was long: a hundred rows share three causes,
every effect read ``write (provisional: unknown effect)``, and every row asked
the same question. The comparison now opens with one finding per changed agent,
the first thing not read, one line per shared cause and a question specific to
what changed. These tests hold that reading to the rows it summarizes:

- it is presentation: no row, status, direction, gap or exit code moves, and the
  JSON differs only by the additive ``summary`` block;
- nothing in it is absent from a row, and every statement names its rows;
- a thing not read is never shortened away: a finding with an unread hop is
  ``partial`` and names the first one.
"""

from __future__ import annotations

import gzip
import json
import re
from collections import Counter
from pathlib import Path

import pytest
from test_application_diff import commit, run
from test_application_diff import repo as repo
from typer.testing import CliRunner

import agents_shipgate.cli.application_diff as diff_module
from agents_shipgate.cli.application_summary import (
    DETAIL_ROW_LIMIT,
    build_summary,
    pattern_of,
    summary_lines,
)
from agents_shipgate.cli.main import app

ROOT = Path(__file__).resolve().parents[1]
GOLDENS = ROOT / "benchmark" / "application-q2" / "goldens"

AGENT = '''import os
import requests
from agents import Agent, function_tool


@function_tool
def lookup(query: str) -> str:
    return query

BODY

agent = Agent(name="assistant", tools=[lookup, act])
'''

CALL = '''
@function_tool
def act(city: str) -> dict:
    token = os.environ["WEATHER_TOKEN"]
    return requests.post(
        "https://api.example.com/forecast",
        json={"city": city},
        headers={"Authorization": token},
        timeout=5,
    ).json()
'''

OPAQUE = '''
@function_tool
def act(city: str) -> dict:
    return client.fetch(city)
'''


def _text(repo, base: str, head: str) -> str:
    result = CliRunner().invoke(
        app, ["diff", "--application", "--workspace", str(repo), "--base", base, "--head", head]
    )
    assert result.exit_code == 0, result.output
    return result.output


def _added(repo, body: str) -> tuple[dict, str, str]:
    base = commit(repo, {"agent.py": AGENT.replace("BODY", "").replace(", act", "")})
    head = commit(repo, {"agent.py": AGENT.replace("BODY", body)})
    return run(repo, base, head), base, head


def _strings(value) -> list[str]:
    """Every string a JSON value holds, raw (not JSON-escaped)."""

    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for item in value.values() for s in _strings(item)]
    if isinstance(value, list):
        return [s for item in value for s in _strings(item)]
    return []


def assert_traceable(payload: dict) -> None:
    """Every statement the summary makes is a fact of the rows it names."""

    rows, summary = payload["rows"], payload["summary"]
    assert summary["counts"]["total"] == len(rows)
    for kind in ("added", "removed", "changed", "not_established"):
        assert summary["counts"][kind] == sum(1 for row in rows if row["change"] == kind)
    seen: list[int] = []
    for finding in summary["findings"]:
        seen.extend(finding["rows"])
        assert [t["row"] for t in finding["tools"]] == finding["rows"]
        for tool in finding["tools"]:
            row = rows[tool["row"]]
            assert (row["agent"], row["tool"]) == (finding["agent"], tool["tool"])
            assert (row["change"], row["candidate_change"]) == (tool["change"], tool["candidate_change"])
            blob = "\n".join(_strings(row))
            for fact in tool["facts"]:
                if "at" in fact:
                    assert fact["at"] in blob
                for host in fact.get("host", []):
                    assert host in blob
                for credential in fact.get("credentials", []):
                    for env in re.findall(r"env ([A-Z0-9_]+)", credential):
                        assert env in blob
                for supplied in fact.get("model_supplied", []):
                    assert supplied.split(" ")[0] in blob
            if "unresolved" in tool:
                assert tool["unresolved"]["at"] in blob and tool["unresolved"]["why"] in blob
        for question in finding["questions"]:
            assert question["rows"] and set(question["rows"]) <= set(finding["rows"])
        first = finding["first_unresolved"]
        assert (first is None) == (finding["status"] == "compared")
        if first is not None and first["row"] is not None:
            assert first["row"] in finding["rows"]
            blob = "\n".join(_strings(rows[first["row"]]))
            if first["kind"] == "reach":
                assert first["at"] in blob and first["text"].removeprefix(first["at"] + " ") in blob
            else:
                assert first["text"] in blob
        for index in finding["causes"]:
            cause = summary["causes"][index]
            assert set(cause["rows"]) & set(finding["rows"])
    assert sorted(seen) == list(range(len(rows))), "every row belongs to exactly one finding"
    for cause in summary["causes"]:
        for index in cause["rows"]:
            assert any(cause["example"] == r or pattern_of(r) == cause["pattern"]
                       for reasons in rows[index]["uncertainty"].values() for r in reasons)
    assert ("question" in summary) == bool(summary["counts"]["not_established"])


def test_an_added_tool_leads_with_what_it_reaches_and_what_was_not_read(repo):
    result, base, head = _added(repo, CALL)
    assert_traceable(result)
    [finding] = result["summary"]["findings"]
    assert finding["agent"] == "agent"
    assert finding["counts"]["added"] == 1
    [tool] = finding["tools"]
    [reach] = [fact for fact in tool["facts"] if fact["kind"] == "reaches"]
    assert reach["target"] == "POST https://api.example.com/forecast"
    assert reach["credentials"] == ["env WEATHER_TOKEN → header Authorization"]
    assert reach["model_supplied"] == ["city → field city"]
    [question] = [item["question"] for item in finding["questions"]]
    assert question == (
        "Should agent reach POST https://api.example.com/forecast through act, "
        "sending env WEATHER_TOKEN → header Authorization, with the model controlling city → field city?"
    )
    assert finding["status"] == "compared" and finding["first_unresolved"] is None

    text = _text(repo, base, head)
    lines = text.splitlines()
    first = next(line for line in lines if line.startswith("agent ["))
    assert first == "agent [compared]: +act (new reach: POST https://api.example.com/forecast)"
    before_detail = lines[: lines.index("Detail:")]
    assert len(before_detail) <= 15, text
    # The row-by-row text is still printed, under its own heading.
    assert any(line.startswith("ADDED  agent → act") for line in lines[lines.index("Detail:"):])


def test_a_finding_names_the_first_hop_it_could_not_read_and_is_partial(repo):
    result, base, head = _added(repo, OPAQUE)
    assert_traceable(result)
    assert result["comparison_status"] == "compared"
    [finding] = result["summary"]["findings"]
    assert finding["status"] == "partial"
    first = finding["first_unresolved"]
    assert first["kind"] == "reach"
    assert first["text"].startswith(first["at"]) and "client.fetch" in first["text"]
    [tool] = finding["tools"]
    assert tool["unresolved"]["count"] >= 1
    text = _text(repo, base, head)
    header = next(line for line in text.splitlines() if line.startswith("agent ["))
    assert "[partial]" in header and f"not read beyond {first['at']}" in header
    assert "reach: none established" in header


def test_a_removal_asks_whether_the_loss_is_intended(repo):
    base = commit(repo, {"agent.py": AGENT.replace("BODY", CALL)})
    head = commit(repo, {"agent.py": AGENT.replace("BODY", "").replace(", act", "")})
    result = run(repo, base, head)
    assert_traceable(result)
    [finding] = result["summary"]["findings"]
    assert finding["counts"]["removed"] == 1
    assert [q["question"] for q in finding["questions"]] == [
        "Is it intended that agent no longer holds act?"
    ]
    [fact] = [f for f in finding["tools"][0]["facts"] if f["kind"] == "reaches_dropped"]
    assert fact["text"].startswith("no longer reaches POST https://api.example.com/forecast")


def test_a_change_names_what_changed_and_what_it_still_reaches(repo):
    base = commit(repo, {"agent.py": AGENT.replace("BODY", CALL)})
    changed = CALL.replace("city: str", "city: str, units: str").replace(
        '{"city": city}', '{"city": city, "units": units}'
    )
    head = commit(repo, {"agent.py": AGENT.replace("BODY", changed)})
    result = run(repo, base, head)
    assert_traceable(result)
    [finding] = result["summary"]["findings"]
    [tool] = finding["tools"]
    changes = [fact["text"] for fact in tool["facts"] if fact["kind"] == "change"]
    assert changes == ["implementation changed", "arguments +units"]
    # The call is the same; what it sends is what changed.
    [reach] = [fact for fact in tool["facts"] if fact["kind"] == "reaches_changed"]
    assert reach["model_supplied"] == ["units → field units"] and "credentials" not in reach
    assert finding["changed_capabilities"] == ["POST https://api.example.com/forecast"]
    assert [q["question"] for q in finding["questions"]] == [
        "Should act now let the model control units → field units in its call to POST https://api.example.com/forecast?",
        "Do the changes to act (implementation, arguments) still match what agent should be able to do?",
    ]


def test_one_line_per_cause_however_many_rows_share_it(repo):
    # Twelve tools leave and twelve arrive while one undecorated function leaves
    # the agent's list unread on both sides: 24 candidates, one cause each side.
    def source(names: list[str]) -> str:
        tools = "\n".join(f"@function_tool\ndef {name}(x: str) -> str:\n    return x\n" for name in names)
        return (
            "from agents import Agent, function_tool\n"
            + tools
            + "\ndef plain(x):\n    return x\n\n"
            + f"agent = Agent(name='assistant', tools=[{', '.join([*names, 'plain'])}])\n"
        )

    old = [f"old_{i}" for i in range(12)]
    new = [f"new_{i}" for i in range(12)]
    base = commit(repo, {"agent.py": source(old)})
    head = commit(repo, {"agent.py": source(new)})
    result = run(repo, base, head)
    assert_traceable(result)
    rows = result["rows"]
    assert len(rows) > DETAIL_ROW_LIMIT
    summary = result["summary"]
    assert summary["counts"]["not_established"] == len(rows) == 24
    # The grouping is by what the reasons share; every reason stays in its row.
    assert 1 <= len(summary["causes"]) <= 4
    assert sum(len(cause["rows"]) for cause in summary["causes"]) >= len(rows)
    text = _text(repo, base, head)
    lines = text.splitlines()
    assert len(lines) <= 20, text
    assert any(line.startswith("Detail: 24 rows are not printed here.") for line in lines)
    assert "Not established: 24 rows share" in text
    assert "ADDED " not in text and "NOT_ESTABLISHED " not in text
    # Nothing was dropped from the data the line points at.
    assert all(row["uncertainty"] for row in rows)


def test_the_json_differs_only_by_the_additive_summary(repo, monkeypatch):
    result, base, head = _added(repo, CALL)
    assert list(result)[:3] == ["application_comparison_schema_version", "comparison_status", "summary"]
    assert result["application_comparison_schema_version"] == "0.4"
    assert result["summary"] == build_summary(result)
    identity = result.pop("comparison_id")
    assert identity == diff_module._digest(result)

    monkeypatch.setattr(diff_module, "build_summary", lambda payload: {"stub": True})
    stubbed = run(repo, base, head)
    for payload in (result, stubbed):
        payload.pop("summary")
        payload.pop("comparison_id", None)
    assert stubbed == result


def test_exit_code_and_status_do_not_depend_on_the_summary(repo, monkeypatch):
    _, base, head = _added(repo, OPAQUE)
    first = CliRunner().invoke(
        app, ["diff", "--application", "--workspace", str(repo), "--base", base, "--head", head, "--json"]
    )
    monkeypatch.setattr(diff_module, "summary_lines", lambda summary: [])
    second = CliRunner().invoke(
        app, ["diff", "--application", "--workspace", str(repo), "--base", base, "--head", head]
    )
    assert first.exit_code == second.exit_code == 0


def test_pattern_groups_what_varies_and_nothing_else():
    a = "Agent 'x' at a.py:3 binds unresolved tool 'f': it resolves to 'g' at b/c.py:9, which is not decorated with the SDK's @function_tool."
    b = "Agent 'y' at a.py:7 binds unresolved tool 'h': it resolves to 'k' at b/d.py:2, which is not decorated with the SDK's @function_tool."
    c = "Agent 'x' at a.py:3 binds unresolved tool 'f': resolving it would read more than 64 modules."
    assert pattern_of(a) == pattern_of(b) != pattern_of(c)
    assert pattern_of("no names here") == "no names here"


def _binding(tool: str, *, sha: str = "a" * 64, reach: dict | None = None) -> dict:
    binding = {
        "agent": "a",
        "agent_source": "agent.py",
        "tool": tool,
        "binding_location": "agent.py:1",
        "signature": f"{tool}() -> str",
        "input_schema": {"type": "object", "properties": {}},
        "output_schema": {"type": "string"},
        "definition": {"source": "agent.py", "line": 1, "implementation_sha256": sha},
        "effect_evidence": {"conservative_effect": "write", "status": "unknown", "claims": []},
    }
    if reach is not None:
        binding["reach"] = reach
    return binding


def _payload(rows: list[dict], **extra) -> dict:
    side = {"scope": ".", "limits": [], "coverage_gaps": [], "excluded_tests": [], "compared_commit": "0" * 40}
    return {
        "comparison_status": "partial",
        "rows": rows,
        "base": {**side},
        "head": {**side},
        "limits": ["advisory"],
        **extra,
    }


def _row(agent: str, tool: str, change: str, **fields) -> dict:
    row = {
        "agent": agent,
        "agent_source": "agent.py",
        "tool": tool,
        "change": change,
        "candidate_change": None,
        "uncertainty": {},
        "before": None,
        "after": None,
        "why": "w",
        "review_question": "q",
    }
    return {**row, **fields}


def test_text_never_obeys_a_control_character_in_a_name(capsys):
    payload = _payload(
        [_row("evil\nControl: complete", "t\x1b[2J", "added", after=_binding("t"))],
    )
    payload["summary"] = build_summary(payload)
    diff_module._print_comparison(payload, "a" * 40, "b" * 40)
    out = capsys.readouterr().out
    assert "\nControl: complete" not in out
    assert "\x1b" not in out


def test_limits_the_rows_do_not_state_are_grouped_per_agent_and_scope_limits_are_not_repeated():
    scope_limit = "Changed files are not related to a compared agent: x.py"
    payload = _payload(
        [_row("a", "t", "added", after=_binding("t"))],
        scope_selection={"mode": "derived", "scopes": ["."], "reason": "r", "limits": [scope_limit]},
    )
    for side in ("base", "head"):
        payload[side]["limits"] = [scope_limit, "Agent 'b' is built twice.", "Agent 'b' copy not read."]
        payload[side]["coverage_gaps"] = [
            {"source": "agent.py", "agent": "b", "tool": None, "affects": "binding_presence", "reason": "Agent 'b' is built twice."},
            {"source": "agent.py", "agent": "b", "tool": None, "affects": "binding_presence", "reason": "Agent 'b' copy not read."},
        ]
    summary = build_summary(payload)
    [group] = summary["not_read"]
    assert (group["agent"], group["count"], group["sides"]) == ("b", 2, ["base", "head"])
    assert scope_limit not in json.dumps(summary)


def test_a_multi_scope_limit_keeps_its_agent_through_the_scope_prefix():
    payload = _payload([_row("a", "t", "added", after=_binding("t"))])
    reason = "Agent 'b' is built twice."
    payload["head"]["limits"] = [f"app: {reason}"]
    payload["head"]["coverage_gaps"] = [
        {"source": "app/agent.py", "agent": "b", "tool": None, "affects": "binding_presence", "reason": reason}
    ]
    [group] = build_summary(payload)["not_read"]
    assert group["agent"] == "b"


def test_an_agent_the_rows_do_not_cover_still_makes_the_answer_partial_in_text(capsys):
    payload = _payload([_row("a", "t", "added", after=_binding("t"))])
    payload["head"]["limits"] = ["Agent 'b' is built twice."]
    payload["head"]["coverage_gaps"] = [
        {"source": "agent.py", "agent": "b", "tool": None, "affects": "binding_presence", "reason": "Agent 'b' is built twice."}
    ]
    payload["summary"] = build_summary(payload)
    diff_module._print_comparison(payload, "a" * 40, "b" * 40)
    out = capsys.readouterr().out
    assert "Also not read, and not tied to a row above: 1 limit on b" in out


def test_findings_beyond_the_third_keep_their_header_line():
    rows = [_row(f"agent_{i}", "t", "added", after=_binding("t")) for i in range(5)]
    lines = summary_lines(build_summary(_payload(rows)))
    headers = [line for line in lines if line.startswith("agent_")]
    assert len(headers) == 5
    assert any(line.startswith("2 more agents: tool lines and questions") for line in lines)


def test_duplicate_agent_names_are_told_apart_by_their_source():
    rows = [
        _row("orion", "t", "added", agent_source=source, after={**_binding("t"), "agent_source": source})
        for source in ("api.py", "main.py")
    ]
    headers = [line for line in summary_lines(build_summary(_payload(rows))) if line.startswith("orion")]
    assert [line.split(" [")[0] for line in headers] == ["orion (api.py)", "orion (main.py)"]


def test_a_tool_object_is_held_not_reached_and_its_credentials_are_asked_about():
    identity = {
        "kind": "mcp_server",
        "class": "McpToolset",
        "transport": "streamable_http",
        "host": ["search.example.com"],
        "command": None,
        "credential_sources": [{"header": "Authorization", "env": ["SEARCH_API_KEY"]}],
        "tool_filter": None,
        "endpoint_sha256": "e" * 64,
    }
    row = _row("a", "search()", "added", after={**_binding("search()"), "object": identity})
    [finding] = build_summary(_payload([row]))["findings"]
    assert finding["capabilities"] == ["MCP server (McpToolset, streamable_http)"]
    [question] = [item["question"] for item in finding["questions"]]
    assert question == (
        "Should a hold MCP server (McpToolset, streamable_http), "
        "sending env SEARCH_API_KEY → header Authorization?"
    )


def test_what_changed_is_named_by_kind():
    base = _binding("t")
    head = {
        **_binding("t", sha="b" * 64),
        "bound_when": ["DEBUG"],
        "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
        "signature": "t(q) -> str",
    }
    row = _row("a", "t", "changed", before=base, after=head)
    [tool] = build_summary(_payload([row]))["findings"][0]["tools"]
    assert [f["text"] for f in tool["facts"] if f["kind"] == "change"] == [
        "implementation changed",
        "arguments +q",
        "bound unconditionally → only when DEBUG",
    ]


def test_a_finding_of_only_candidates_shows_what_each_would_be():
    reach = {
        "calls": [{"method": "GET", "url": "https://x.example/a", "at": "t.py:3", "via": []}],
        "effects": [],
        "limits": [],
        "effect_claims": [],
    }
    row = _row(
        "a",
        "t",
        "not_established",
        candidate_change="added",
        after=_binding("t", reach=reach),
        uncertainty={"head": ["Agent 'a' at x.py:1 is built twice."]},
    )
    lines = summary_lines(build_summary(_payload([row])))
    assert any(
        line.startswith("  ?+t (candidate, not established): reaches GET https://x.example/a at t.py:3")
        for line in lines
    )
    # Beside an established row the same candidate is counted, not listed.
    established = _row("a", "u", "added", after=_binding("u"))
    mixed = summary_lines(build_summary(_payload([row, established])))
    assert not any(line.startswith("  ?+t") for line in mixed)


def test_each_scope_keeps_its_status_when_the_rows_are_not_printed(capsys):
    rows = [_row("a", f"t{i}", "added", after=_binding(f"t{i}")) for i in range(DETAIL_ROW_LIMIT + 1)]
    comparisons = [
        {"head": {"scope": "app"}, "comparison_status": "partial"},
        {"head": {"scope": "lib"}, "comparison_status": "compared"},
    ]
    payload = _payload(rows, comparisons=comparisons)
    payload["summary"] = build_summary(payload)
    diff_module._print_comparison(payload, "a" * 40, "b" * 40)
    out = capsys.readouterr().out
    assert "Scope app: partial" in out and "Scope lib: compared" in out
    assert f"Detail: {len(rows)} rows are not printed here." in out


# The three cases #914 names, rendered from the answers of the frozen
# development corpus (benchmark/application-q2/goldens.py regenerates them).
GOLDEN = {
    "jpka_attest-3": 15,
    "Tiendat2703_MIS_TALENT-7": 15,
    "VidulaWickramasinghe_O.R.I.O.N-126": 40,
}


@pytest.mark.parametrize("slug", sorted(GOLDEN))
def test_the_corpus_goldens_render_from_their_inputs(slug, capsys):
    payload = json.loads(gzip.decompress((GOLDENS / f"{slug}.input.json.gz").read_bytes()))
    run_summary = payload.pop("summary")
    assert build_summary(payload) == run_summary
    payload["summary"] = run_summary
    diff_module._print_comparison(payload, payload["base"]["compared_commit"], payload["head"]["compared_commit"])
    out = capsys.readouterr().out
    assert out == (GOLDENS / f"{slug}.txt").read_text(encoding="utf-8")
    lines = out.splitlines()
    summary_part = lines[: lines.index("Detail:")] if "Detail:" in lines else lines
    assert len(summary_part) <= GOLDEN[slug], out
    assert_traceable(payload)
    # Never a clean answer where something was not read.
    assert payload["comparison_status"] != "compared" or all(
        finding["status"] in {"compared", "partial"} for finding in payload["summary"]["findings"]
    )
    for finding in payload["summary"]["findings"]:
        if finding["counts"]["not_established"] or any("unresolved" in t for t in finding["tools"]):
            assert finding["status"] == "partial"


def test_the_goldens_answer_what_the_change_lets_each_agent_do():
    """The first finding line of each golden says it, checked against the hand-scored ledger (#908)."""

    first = {}
    for slug in GOLDEN:
        lines = (GOLDENS / f"{slug}.txt").read_text(encoding="utf-8").splitlines()
        first[slug] = next(line for line in lines if re.match(r"^\S.*\[(partial|compared)\]:", line))
    attest = first["jpka_attest-3"]
    assert attest.startswith("attest_orchestrator [partial]: +recall_firm_memory, +remember_firm_finding")
    assert "memory_bank.py:531" in attest
    mis = first["Tiendat2703_MIS_TALENT-7"]
    assert "+load_service_catalog" in mis and "~load_and_validate" in mis and "database read" in mis
    orion = first["VidulaWickramasinghe_O.R.I.O.N-126"]
    assert "58" in orion or "changed" in orion and "not established" in orion


def test_summary_counts_match_a_counter_of_rows():
    rows = [
        _row("a", "x", "added", after=_binding("x")),
        _row("a", "y", "removed", before=_binding("y")),
        _row("a", "z", "changed", before=_binding("z"), after=_binding("z", sha="b" * 64)),
    ]
    summary = build_summary(_payload(rows))
    assert summary["counts"] == {"total": 3, **Counter(r["change"] for r in rows), "not_established": 0}


def test_the_docs_quote_lines_the_goldens_print():
    docs = (ROOT / "docs" / "application-comparison.md").read_text(encoding="utf-8")
    section = docs.split("## Findings first", 1)[1].split("\n## ", 1)[0]
    printed = {
        line
        for golden in GOLDENS.glob("*.txt")
        for line in golden.read_text(encoding="utf-8").splitlines()
    }
    quoted = [
        line
        for block in re.findall(r"```text\n(.*?)```", section, re.S)
        for line in block.splitlines()
        if line.strip()
    ]
    assert quoted
    assert [line for line in quoted if line not in printed] == []


def test_the_summary_does_not_depend_on_the_order_of_a_rows_sides():
    def row(tool: str, uncertainty: dict[str, list[str]]) -> dict:
        return _row("a", tool, "not_established", candidate_change="added", after=_binding(tool), uncertainty=uncertainty)

    sides = {"head": ["Head cause for 'x' at a.py:1."], "base": ["Base cause for 'y' at b.py:2."]}
    forward = _payload([row("t", sides)])
    backward = _payload([row("t", dict(reversed(list(sides.items()))))])
    assert list(backward["rows"][0]["uncertainty"]) != list(forward["rows"][0]["uncertainty"])
    assert build_summary(backward) == build_summary(forward)
