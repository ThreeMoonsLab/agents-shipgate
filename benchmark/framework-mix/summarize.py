"""Count the framework-mix sample: which frameworks real agent-changing pull requests use (#916).

Usage:
    python benchmark/framework-mix/summarize.py [--sample benchmark/framework-mix/sample.json]

Prints the Markdown tables the README carries between its ``summarize:``
markers. Every number is counted from ``sample.json`` (and, for the title
frame's reach, from the application corpus's ``development.json``); the
judgments in it (relevance, framework, readability) were made by hand from
source at the pinned refs, and this script only counts them.

The intervals are 95% Wilson intervals for independent draws. The neutral
frame is not that: it is 24 clusters (the hours drawn), and a repository can
appear more than once, so the true uncertainty is wider than they show.
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEVELOPMENT = HERE.parent / "application-q2" / "development.json"
_WORD = re.compile(r"[a-z0-9]+")
#: The frameworks #916 names, in its order, then the MCP server buckets.
LISTED = (
    ("openai_agents_sdk", "OpenAI Agents SDK"),
    ("google_adk", "Google ADK"),
    ("langchain_langgraph", "LangChain / LangGraph"),
    ("crewai", "CrewAI"),
    ("pydantic_ai", "PydanticAI"),
    ("llamaindex", "LlamaIndex"),
    ("autogen_ag2", "AutoGen / AG2"),
    ("smolagents", "smolagents"),
    ("agno", "Agno"),
    ("semantic_kernel", "Semantic Kernel"),
)
MCP = (
    ("mcp_server_python", "MCP server, Python"),
    ("mcp_server_typescript", "MCP server, TypeScript/JavaScript"),
    ("mcp_server_other", "MCP server, other languages"),
)
READABILITY = ("static", "flow", "runtime")
MCP_KEYS = frozenset(key for key, _ in MCP)


def title_matches(title: str) -> bool:
    """Whether a title meets the neutral frame's word rule.

    The frame's four searches, read as GitHub reads ``in:title`` words with
    stemming: a word stemmed from "agent" (agents, agentic) with one from
    "tool" (tools, tooling) or "handoff", or a sub-agent word. GitHub's own
    matching differs at the edges, so this reproduces the frame for
    comparison, not exactly.
    """

    words = _WORD.findall(title.lower())

    def has(stem: str) -> bool:
        return any(word.startswith(stem) for word in words)

    return (
        (has("agent") and (has("tool") or has("handoff")))
        or has("subagent")
        or re.search(r"\bsub-agents?\b", title.lower()) is not None
    )


def wilson(k: int, n: int) -> tuple[float, float]:
    """The 95% Wilson interval of k successes in n, as percentages."""

    if n == 0:
        return (0.0, 0.0)
    z = 1.959963984540054
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (100 * max(0.0, centre - half), 100 * min(1.0, centre + half))


def _row(label: str, members: list[dict], total: int, *, interval: bool = True) -> str:
    k = len(members)
    low, high = wilson(k, total)
    read = collections.Counter(m["readability"] for m in members)
    existing = sum(1 for m in members if m["existing_agent"])
    share = f"{100 * k / total:.0f}%" if total else "-"
    cells = [label, str(k), share, f"{low:.0f}–{high:.0f}%" if interval else "-", str(existing)]
    cells += [str(read.get(level, 0)) for level in READABILITY]
    cells.append(str(read.get(None, 0)))
    return "| " + " | ".join(cells) + " |"


def neutral_table(sample: dict) -> str:
    members = [m for m in sample["members"] if m["frame"] == "neutral"]
    relevant = [m for m in members if m["relevant"]]
    total = len(relevant)
    by = collections.defaultdict(list)
    for m in relevant:
        by[m["framework"]].append(m)
    lines = [
        "| Framework of the changed agent or server | Relevant PRs | Share | 95% interval (independent draws) | Existing agent | Static | Flow | Runtime | Not judged |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for key, label in (*LISTED, *MCP):
        lines.append(_row(label, by.pop(key, []), total))
    others = sorted(key for key in by if key.startswith("other:"))
    named = [m for key in others for m in by.pop(key)]
    names = ", ".join(f"{key[6:]} {sum(1 for m in named if m['framework'] == key)}" for key in others)
    lines.append(_row(f"Other named frameworks ({names})" if names else "Other named frameworks", named, total))
    lines.append(_row("Custom (the repository's own tool registry or loop)", by.pop("custom", []), total))
    assert not by, f"unbucketed frameworks: {sorted(by)}"
    lines.append(_row("**All relevant**", relevant, total, interval=False))
    return "\n".join(lines)


def neutral_funnel(sample: dict) -> str:
    frame = sample["frames"]["neutral"]
    members = [m for m in sample["members"] if m["frame"] == "neutral"]
    kinds = collections.Counter(m["kind"] for m in members if not m["relevant"])
    lines = [
        "| Step | Pull requests |",
        "| --- | ---: |",
        f"| Returned by the title queries in {len(frame['hours_walked'])} random hours (unique) | {frame['returned_unique']} |",
    ]
    for reason, count in sorted(frame["excluded"].items()):
        lines.append(f"| Excluded: `{reason}` | {count} |")
    lines += [
        f"| Candidates, in seeded random order | {frame['candidates']} |",
        f"| Walked and judged from source | {len(members)} |",
        f"| …the binding prefilter flagged | {sum(1 for m in members if m['prefilter'])} |",
        f"| **Relevant: changes an agent's tools, sub-agents or handoffs, or an MCP server's tools** | **{sum(1 for m in members if m['relevant'])}** |",
    ]
    for kind, count in sorted(kinds.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"| Not relevant: `{kind}` | {count} |")
    return "\n".join(lines)


def framework_table(sample: dict) -> str:
    lines = [
        "| Framework | Code-search files | Candidates | Walked | Flagged | Judged | Relevant | Existing agent | Static | Flow | Runtime |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for key, label in (*LISTED, *MCP[:2]):
        frame = sample["frames"].get(f"framework:{key}")
        if frame is None:
            continue
        members = [m for m in sample["members"] if m["frame"] == f"framework:{key}"]
        relevant = [m for m in members if m["relevant"] and m["framework"] == key]
        read = collections.Counter(m["readability"] for m in relevant)
        totals = dict(sorted({(e["query"], e["total_count"]) for e in frame["code_search"]}))
        files = " + ".join(f"{total:,}" for total in totals.values())
        cells = [label, files, str(frame["candidates"]), str(frame["walked"]), str(frame["flagged"]),
                 str(len(members)), str(len(relevant)), str(sum(1 for m in relevant if m["existing_agent"]))]
        cells += [str(read.get(level, 0)) for level in READABILITY]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def combined_readability(sample: dict) -> str:
    """Readability per framework across both frames: every judged relevant PR of that framework."""

    lines = [
        "| Framework | Relevant PRs, both frames | Static | Flow | Runtime | Static or flow |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for key, label in (*LISTED, *MCP):
        relevant = [m for m in sample["members"] if m["relevant"] and m["framework"] == key]
        if not relevant:
            lines.append(f"| {label} | 0 | 0 | 0 | 0 | - |")
            continue
        read = collections.Counter(m["readability"] for m in relevant)
        readable = read["static"] + read["flow"]
        cells = [label, str(len(relevant))] + [str(read.get(level, 0)) for level in READABILITY]
        cells.append(f"{readable} of {len(relevant)}")
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def title_reach(sample: dict) -> str:
    """How often PRs that change agent bindings are titled the way the neutral frame searches."""

    def row(label: str, titles: list[str]) -> str:
        k = sum(1 for title in titles if title_matches(title))
        low, high = wilson(k, len(titles))
        return f"| {label} | {k} of {len(titles)} | {low:.0f}–{high:.0f}% |"

    neutral = [m["title"] for m in sample["members"] if m["frame"] == "neutral" and m["title"]]
    framework = [m["title"] for m in sample["members"]
                 if m["frame"] != "neutral" and m["relevant"] and m["title"]]
    development = json.loads(DEVELOPMENT.read_text(encoding="utf-8"))["members"]
    return "\n".join([
        "| Pull requests | Title meets the frame's word rule | 95% interval |",
        "| --- | ---: | ---: |",
        row("The neutral frame's walked pull requests with a recorded title (does the rule reproduce the search?)", neutral),
        row("Relevant pull requests the framework frames found", framework),
        row("The application development corpus (SDK and ADK changes, `development.json`)",
            [m["title"] for m in development]),
    ])


def addressable(sample: dict) -> str:
    """reach(F), readable(F) and addressable(F) for the frameworks the neutral frame saw."""

    neutral = [m for m in sample["members"] if m["frame"] == "neutral" and m["relevant"]]
    agent_binding = [m for m in neutral if m["framework"] not in MCP_KEYS]
    lines = [
        "| Framework | Neutral relevant | reach over all relevant | reach over agent-binding PRs | Static or flow, both frames | addressable over all | addressable over agent-binding | …with reach at the low end of its interval |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for key, label in (LISTED[0], LISTED[1], LISTED[2], LISTED[4], *MCP):
        k = sum(1 for m in neutral if m["framework"] == key)
        judged = [m for m in sample["members"] if m["relevant"] and m["framework"] == key]
        readable = sum(1 for m in judged if m["readability"] in ("static", "flow"))
        share = readable / len(judged) if judged else 0.0
        reach_all = 100 * k / len(neutral)
        cells = [label, str(k), f"{reach_all:.1f} of 100 ({k} of {len(neutral)})"]
        if key in MCP_KEYS:
            cells.append("-")
        else:
            cells.append(f"{100 * k / len(agent_binding):.1f} of 100 ({k} of {len(agent_binding)})")
        cells.append(f"{readable} of {len(judged)}" if judged else "-")
        cells.append(f"{reach_all * share:.1f}")
        if key in MCP_KEYS:
            cells += ["-", "-"]
        else:
            reach_agent = 100 * k / len(agent_binding)
            low, _ = wilson(k, len(agent_binding))
            cells += [f"{reach_agent * share:.1f}", f"{low * share:.1f}"]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def dossier_table(sample: dict) -> str:
    """How many judgments rest on a dossier whose line cap hid whole files, and which were re-read."""

    def group(m: dict) -> str:
        if m["relevant"]:
            return "Relevant"
        if m["kind"] == "application":
            return "Not relevant, application code"
        return "Not relevant: coding-agent tooling, sample, framework internals, test or not an agent"

    order = ("Not relevant, application code", "Relevant",
             "Not relevant: coding-agent tooling, sample, framework internals, test or not an agent")
    capped = [m for m in sample["members"] if m["dossier_capped"]]
    lines = [
        "| Judgment | Judged from a capped dossier | Re-read from the full patch |",
        "| --- | ---: | ---: |",
    ]
    for name in order:
        rows = [m for m in capped if group(m) == name]
        lines.append(f"| {name} | {len(rows)} | {sum(1 for m in rows if m['reread_full_patch'])} |")
    lines.append(f"| **All judged ({len(sample['members'])})** | **{len(capped)}** | "
                 f"**{sum(1 for m in capped if m['reread_full_patch'])}** |")
    return "\n".join(lines)


TABLES = {
    "neutral-funnel": neutral_funnel,
    "neutral-share": neutral_table,
    "title-reach": title_reach,
    "framework-frames": framework_table,
    "readability": combined_readability,
    "addressable": addressable,
    "dossiers": dossier_table,
}


def render(sample: dict) -> dict[str, str]:
    return {name: build(sample) for name, build in TABLES.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--sample", type=Path, default=HERE / "sample.json")
    args = parser.parse_args(argv)
    sample = json.loads(args.sample.read_text(encoding="utf-8"))
    for name, table in render(sample).items():
        print(f"<!-- summarize:{name} -->\n{table}\n<!-- /summarize:{name} -->\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
