"""The framework-mix sample stays pinned, apart from the holdout, and says what it counts (#916).

`benchmark/framework-mix/` records which agent frameworks real agent-changing
pull requests use, judged by hand from source. These tests hold the files to
what the README states: every judged pull request is pinned to full commits,
none is a member of the application development corpus or comes from a
repository of the holdout's recorded pool, every judgment has the fields its
level needs, the hours and the candidates walked are the first of their seeded
orders, and the README's tables are the ones `summarize.py` counts from the sample. They
fetch nothing and run no engine.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark" / "framework-mix"
Q2 = ROOT / "benchmark" / "application-q2"
SHA = re.compile(r"^[0-9a-f]{40}$")
SAMPLE = json.loads((BENCH / "sample.json").read_text(encoding="utf-8"))
README = (BENCH / "README.md").read_text(encoding="utf-8")
KINDS = {
    "application", "coding_agent_extension", "framework_internal", "sample", "test_only", "not_agent", "undecided",
}
BUCKETS = {
    "openai_agents_sdk", "google_adk", "langchain_langgraph", "crewai", "pydantic_ai", "llamaindex",
    "autogen_ag2", "smolagents", "agno", "semantic_kernel", "mcp_server_python", "mcp_server_typescript",
    "mcp_server_other", "custom",
}


def _seeded(*parts: str) -> str:
    return hashlib.sha256(":".join((SAMPLE["seed"], *parts)).encode()).hexdigest()


def _summarize():
    spec = importlib.util.spec_from_file_location("framework_mix_summarize", BENCH / "summarize.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_judged_pull_request_is_pinned_and_unique() -> None:
    members = SAMPLE["members"]
    assert members
    urls = [member["url"] for member in members]
    assert len(set(urls)) == len(urls)
    start, end = SAMPLE["window"]["created_from"], SAMPLE["window"]["created_before"]
    for member in members:
        assert SHA.match(member["merge_base"]), member["url"]
        assert SHA.match(member["head"]), member["url"]
        assert member["merge_base"] != member["head"], member["url"]
        assert member["state"] in {"merged", "open"}, member["url"]
        assert member["url"] == f"https://github.com/{member['repository']}/pull/{member['number']}"
        assert start <= member["created_at"] < end, member["url"]
        assert member["frame"] in SAMPLE["frames"], member["url"]
        assert member["title"] is None or member["title"].strip(), member["url"]
        assert isinstance(member["dossier_capped"], bool), member["url"]
        assert isinstance(member["reread_full_patch"], bool), member["url"]


def test_no_member_touches_the_application_corpus_or_the_holdout_pool() -> None:
    development = json.loads((Q2 / "development.json").read_text(encoding="utf-8"))
    pool = json.loads((Q2 / "pool.json").read_text(encoding="utf-8"))
    development_urls = {member["url"].lower() for member in development["members"]}
    pool_repositories = {repository.lower() for repository in pool["repositories"]}
    for member in SAMPLE["members"]:
        assert member["url"].lower() not in development_urls, member["url"]
        assert member["repository"].lower() not in pool_repositories, member["url"]


def test_each_judgment_has_what_its_level_needs() -> None:
    for member in SAMPLE["members"]:
        assert member["kind"] in KINDS, member["url"]
        assert member["rationale"].strip() and "|" not in member["rationale"], member["url"]
        framework = member["framework"]
        assert framework is None or framework in BUCKETS or framework.startswith("other:"), member["url"]
        if member["relevant"]:
            assert member["kind"] == "application", member["url"]
            assert framework is not None, member["url"]
            assert isinstance(member["existing_agent"], bool), member["url"]
            assert member["readability"] in {"static", "flow", "runtime", None}, member["url"]
            if framework in BUCKETS - {"custom"}:
                # Readability is judged for every listed framework and MCP server.
                assert member["readability"] is not None, member["url"]
        else:
            assert member["existing_agent"] is None, member["url"]
            assert member["readability"] is None, member["url"]


def test_the_neutral_frame_is_a_prefix_of_its_seeded_walk() -> None:
    frame = SAMPLE["frames"]["neutral"]
    members = [member for member in SAMPLE["members"] if member["frame"] == "neutral"]
    assert sorted(member["order"] for member in members) == list(range(frame["walked"]))
    assert frame["candidates"] + sum(frame["excluded"].values()) == frame["returned_unique"]
    assert len(frame["hours_walked"]) == len(set(frame["hours_walked"]))
    queried = {request["hour"] for request in frame["requests"]}
    assert queried == set(frame["hours_walked"])


def test_the_hours_and_candidates_walked_are_the_first_of_their_seeded_orders() -> None:
    window = SAMPLE["window"]
    start = datetime.strptime(window["created_from"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    end = datetime.strptime(window["created_before"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    hours = [
        (start + timedelta(hours=offset)).strftime("%Y-%m-%dT%H:00:00Z")
        for offset in range(int((end - start).total_seconds() // 3600))
    ]
    assert len(hours) == 2160
    order = sorted(hours, key=lambda hour: _seeded(hour))
    walked_hours = SAMPLE["frames"]["neutral"]["hours_walked"]
    assert walked_hours == order[: len(walked_hours)]

    # Within each frame, a member's place in the walk follows its seeded key.
    for name in SAMPLE["frames"]:
        members = sorted(
            (member for member in SAMPLE["members"] if member["frame"] == name),
            key=lambda member: member["order"],
        )
        prefix = () if name == "neutral" else (name.removeprefix("framework:"),)
        keys = [_seeded(*prefix, member["url"].lower()) for member in members]
        assert keys == sorted(keys) and len(set(keys)) == len(keys), name


def test_each_framework_frame_counts_its_members() -> None:
    for name, frame in SAMPLE["frames"].items():
        if not name.startswith("framework:"):
            continue
        members = [member for member in SAMPLE["members"] if member["frame"] == name]
        assert frame["judged"] == len(members), name
        assert frame["judged"] <= frame["flagged"] <= frame["walked"] <= frame["candidates"], name
        assert frame["code_search"], name


def test_the_readme_carries_the_tables_the_sample_counts() -> None:
    for name, table in _summarize().render(SAMPLE).items():
        block = f"<!-- summarize:{name} -->\n{table}\n<!-- /summarize:{name} -->"
        assert block in README, f"README table {name!r} is not what summarize.py counts"
