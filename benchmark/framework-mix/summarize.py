"""Count the framework-mix sample: which frameworks real agent-changing pull requests use (#916).

Usage:
    python benchmark/framework-mix/summarize.py [--sample benchmark/framework-mix/sample.json]

Prints the Markdown tables the README carries between its ``summarize:``
markers. Every number is counted from ``sample.json``; the judgments in it
(relevance, framework, readability) were made by hand from source at the
pinned refs, and this script only counts them.
"""

from __future__ import annotations

import argparse
import collections
import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
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
        "| Framework of the changed agent or server | Relevant PRs | Share | 95% interval | Existing agent | Static | Flow | Runtime | Not judged |",
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


TABLES = {
    "neutral-funnel": neutral_funnel,
    "neutral-share": neutral_table,
    "framework-frames": framework_table,
    "readability": combined_readability,
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
