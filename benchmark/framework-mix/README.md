# Framework mix of agent-changing pull requests (#916)

Which agent frameworks do real pull requests that change an agent's tools,
sub-agents or handoffs use? `diff --application` reads the OpenAI Agents SDK
and Google ADK. Every application corpus so far was selected by importing one
of those two, so none of them can say what share of real agent changes the
route can address at all. This directory records a sample that is not
selected by framework, judged by hand from source, and the per-framework
samples that judge how readable each framework's bindings are.

It is the evidence for the
[next-reader decision record](../../docs/engineering/next-application-reader.md).
It is not a market estimate, a success rate or a qualification claim, and it
must not be cited as one.

## Method

**Window.** Pull requests created from `2026-08-19T00:00:00Z` up to
`2026-11-17T00:00:00Z` (the 90 days before the sample was recorded), in
public repositories, merged or still open. Closed-unmerged pull requests are
left out.

**Neutral frame: the share.** GitHub's pull-request search for four title
queries that name no framework: `agent tool`, `agent handoff`, `subagent`
and `"sub-agent"`, each with `in:title`. GitHub stems the words, so
`agent tool` also matches `agents` and `tools`. The window's 2,160 hours were
ordered by `sha256("issue-916-framework-mix:<hour>")` and the first 24 hours
of that order were queried, every result of every query in each hour. Pull
requests without a non-test code file were left out, the remaining 881
ordered by `sha256("issue-916-framework-mix:<url>")`, and the first 250 of
that order walked. Every walked pull request was judged, including the 25 the
prefilter (a changed line of non-test code mentions a tool, handoff or
sub-agent) did not flag, so the prefilter only ordered the reading and its
one miss is counted.

**Framework frames: readability.** For each framework, GitHub code search
for its import and tool-binding idiom (up to 300 results per query), the 15
most recently created pull requests of each repository it returned, and the
same exclusions; a pull request already in the neutral frame is left out of
these. Candidates were walked in seeded order, at most 200 per framework; a
candidate is judged when a changed line of non-test code matches the
framework's binding idiom in a file that imports the framework, with at most
three judged per repository. Judging stopped at 15 relevant pull requests for
a framework; only the TypeScript MCP frame reached that. These frames find a
framework's pull requests so that their bindings can be judged; their counts
say nothing about share. Pull requests found through one framework's search
are counted under the framework their changed agent actually uses.

**Exclusions, before anything was read.**

- Every repository of the application holdout's recorded pool
  ([`pool.json`](../application-q2/pool.json)) and every pull request of the
  [development corpus](../application-q2/development.json). The holdout
  ([#1158](https://github.com/ThreeMoonsLab/agents-shipgate/issues/1158)) is
  drawn from the pool, and its candidates are not used for anything else, so
  pool repositories were dropped by name: in the neutral frame as soon as
  the search returned them, before any patch was fetched; in the framework
  frames before their pull requests were listed. None was judged, and only
  their count is recorded.
- The frameworks' own repositories in the framework frames (for example
  `langchain-ai/langchain`, `pydantic/pydantic-ai`).

**Pins.** `merge_base` is the merge base GitHub's compare API reports for the
pull request's base and head commits; `head` is the head commit when the
pull request was fetched. Both are full 40-character SHAs. Every judgment
was made from the patch between those two commits and the head (or base)
text of the files it touches.

**Judging.** Relevance is the application corpus's Q0 read across
frameworks: in non-test application code, the pull request adds or removes a
tool, sub-agent or handoff of an agent, creates an agent that binds them,
changes the code that selects them at run time, or adds or removes a tool an
MCP server registers. A change only to a tool's own body, signature or
description is not counted ("definition only"); nor are prompts, UI, tests,
coding-agent tooling (configuration, extensions, subagents and skills for
Claude Code, Codex, pi or opencode, and apps that run or display their
sessions), samples, tutorials and course work, and the frameworks' own
library code. A coding agent product's own built-in tools (a repository that
is itself an agent) are counted, as `custom`. The framework is read off the
changed agent's construction or the server's registration, not off the
repository.

Readability is judged for the binding the pull request changes, as a static
reader would have to read it at both refs:

- `static`: a literal at the constructor or registration site, a decorator, a
  class in a literal list, or a name bound once to such a literal, including
  one imported from another module of the repository, where the agent or
  server it binds to is created in the same module.
- `flow`: determinable from source, but only by following data: a parameter
  (including a registration function that receives the server or agent as
  one), an attribute set elsewhere, a comprehension or concatenation over a
  static list, a factory that returns tools, an array of tool definitions
  registered in a loop, a local wrapper around the framework's decorator.
- `runtime`: depends on configuration, environment, a database, user input,
  a feature flag or role, plugin discovery, a registry filled at run time or
  reflection. A member added under such a condition is `runtime` even when
  its name is a literal.

When a pull request changes several bindings, the hardest one decides
(`runtime` over `flow` over `static`).

Readability is not judged for `custom` pull requests or for frameworks #916
does not list: no reader under discussion would read them, so it decides
nothing here.

`diff --application` was not run on any member. The question is what real
pull requests use, judged from source, not how the engine answers them.

## Neutral frame: what agent-changing pull requests use

<!-- summarize:neutral-funnel -->
| Step | Pull requests |
| --- | ---: |
| Returned by the title queries in 24 random hours (unique) | 1219 |
| Excluded: `closed_unmerged` | 62 |
| Excluded: `holdout_pool_repository` | 9 |
| Excluded: `no_non_test_code_file` | 267 |
| Candidates, in seeded random order | 881 |
| Walked and judged from source | 250 |
| …the binding prefilter flagged | 225 |
| **Relevant: changes an agent's tools, sub-agents or handoffs, or an MCP server's tools** | **61** |
| Not relevant: `application` | 85 |
| Not relevant: `coding_agent_extension` | 80 |
| Not relevant: `not_agent` | 11 |
| Not relevant: `framework_internal` | 6 |
| Not relevant: `sample` | 6 |
| Not relevant: `test_only` | 1 |
<!-- /summarize:neutral-funnel -->

<!-- summarize:neutral-share -->
| Framework of the changed agent or server | Relevant PRs | Share | 95% interval | Existing agent | Static | Flow | Runtime | Not judged |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| OpenAI Agents SDK | 0 | 0% | 0–6% | 0 | 0 | 0 | 0 | 0 |
| Google ADK | 0 | 0% | 0–6% | 0 | 0 | 0 | 0 | 0 |
| LangChain / LangGraph | 5 | 8% | 4–18% | 2 | 1 | 2 | 2 | 0 |
| CrewAI | 0 | 0% | 0–6% | 0 | 0 | 0 | 0 | 0 |
| PydanticAI | 1 | 2% | 0–9% | 1 | 0 | 0 | 1 | 0 |
| LlamaIndex | 0 | 0% | 0–6% | 0 | 0 | 0 | 0 | 0 |
| AutoGen / AG2 | 0 | 0% | 0–6% | 0 | 0 | 0 | 0 | 0 |
| smolagents | 0 | 0% | 0–6% | 0 | 0 | 0 | 0 | 0 |
| Agno | 0 | 0% | 0–6% | 0 | 0 | 0 | 0 | 0 |
| Semantic Kernel | 0 | 0% | 0–6% | 0 | 0 | 0 | 0 | 0 |
| MCP server, Python | 2 | 3% | 1–11% | 1 | 0 | 1 | 1 | 0 |
| MCP server, TypeScript/JavaScript | 10 | 16% | 9–28% | 10 | 0 | 9 | 1 | 0 |
| MCP server, other languages | 6 | 10% | 5–20% | 5 | 3 | 3 | 0 | 0 |
| Other named frameworks (mastra 1, pipecat 1, vercel-ai-sdk 3, webmcp 1) | 6 | 10% | 5–20% | 5 | 0 | 0 | 0 | 6 |
| Custom (the repository's own tool registry or loop) | 31 | 51% | 39–63% | 26 | 0 | 0 | 0 | 31 |
| **All relevant** | 61 | 100% | - | 50 | 4 | 15 | 5 | 37 |
<!-- /summarize:neutral-share -->

In counts: of 250 agent-titled pull requests judged, 61 change an agent's
tools, sub-agents or handoffs, or an MCP server's tools. 31 of the 61 use no
framework: the repository's own tool registry or loop over a model API. 18
change an MCP server's tools. 6 use a framework #916 lists (LangChain or
LangGraph 5, PydanticAI 1) and 6 another named framework. None uses the
OpenAI Agents SDK or Google ADK, the two `diff --application` reads; with 61
relevant pull requests that is consistent with a share up to about 6% each.
80 of the 250 were coding-agent tooling (configuration, extensions, and apps
that run or display coding-agent sessions), the host route's territory, and
are not counted as relevant.

## Framework frames: how readable each framework's bindings are

<!-- summarize:framework-frames -->
| Framework | Code-search files | Candidates | Walked | Flagged | Judged | Relevant | Existing agent | Static | Flow | Runtime |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| OpenAI Agents SDK | 13,168 | 89 | 81 | 11 | 11 | 4 | 2 | 2 | 1 | 1 |
| Google ADK | 40,384 | 40 | 37 | 3 | 3 | 1 | 1 | 1 | 0 | 0 |
| LangChain / LangGraph | 40,320 + 38,016 | 170 | 148 | 23 | 23 | 12 | 9 | 4 | 1 | 7 |
| CrewAI | 23,488 | 103 | 99 | 8 | 8 | 5 | 3 | 4 | 1 | 0 |
| PydanticAI | 6,560 + 5,112 | 665 | 200 | 18 | 18 | 9 | 6 | 2 | 0 | 7 |
| LlamaIndex | 5,048 | 100 | 100 | 6 | 6 | 1 | 1 | 0 | 1 | 0 |
| AutoGen / AG2 | 1,064 + 3,448 | 204 | 200 | 5 | 5 | 1 | 1 | 0 | 1 | 0 |
| smolagents | 5,328 | 68 | 68 | 5 | 5 | 2 | 2 | 1 | 0 | 1 |
| Agno | 20,352 | 34 | 34 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Semantic Kernel | 12,032 + 5,152 | 110 | 110 | 3 | 3 | 1 | 0 | 0 | 1 | 0 |
| MCP server, Python | 155,392 | 151 | 136 | 20 | 20 | 11 | 11 | 6 | 4 | 1 |
| MCP server, TypeScript/JavaScript | 100,992 + 56,960 | 922 | 200 | 36 | 23 | 15 | 15 | 3 | 8 | 4 |
<!-- /summarize:framework-frames -->

Readability of every judged relevant pull request, from both frames:

<!-- summarize:readability -->
| Framework | Relevant PRs, both frames | Static | Flow | Runtime | Static or flow |
| --- | ---: | ---: | ---: | ---: | ---: |
| OpenAI Agents SDK | 4 | 2 | 1 | 1 | 3 of 4 |
| Google ADK | 1 | 1 | 0 | 0 | 1 of 1 |
| LangChain / LangGraph | 17 | 5 | 3 | 9 | 8 of 17 |
| CrewAI | 5 | 4 | 1 | 0 | 5 of 5 |
| PydanticAI | 10 | 2 | 0 | 8 | 2 of 10 |
| LlamaIndex | 1 | 0 | 1 | 0 | 1 of 1 |
| AutoGen / AG2 | 1 | 0 | 1 | 0 | 1 of 1 |
| smolagents | 2 | 1 | 0 | 1 | 1 of 2 |
| Agno | 0 | 0 | 0 | 0 | - |
| Semantic Kernel | 1 | 0 | 1 | 0 | 1 of 1 |
| MCP server, Python | 13 | 6 | 5 | 2 | 11 of 13 |
| MCP server, TypeScript/JavaScript | 25 | 3 | 17 | 5 | 20 of 25 |
| MCP server, other languages | 6 | 3 | 3 | 0 | 6 of 6 |
<!-- /summarize:readability -->

The framework frames found few relevant pull requests for most frameworks:
code search returns many tutorial and personal repositories with no recent
pull request, and the holdout pool removed most of what it returns for the
SDK (194 of 254 repositories) and ADK (172 of 240). Agno has no judged
relevant pull request; ADK, LlamaIndex, AutoGen and Semantic Kernel one each;
smolagents two; the SDK four and CrewAI five. Their rows are shown, but they
measure nothing to any useful precision. Where there are ten or more: LangChain or LangGraph 8 of 17 static or flow, PydanticAI 2
of 10, MCP servers in Python 11 of 13 and in TypeScript or JavaScript 20 of
25, most of the latter `flow` (tool definitions registered in a loop or from
a registration function that receives the server). Some judged pull requests
in the same repository repeat one change (stacked branches); each is judged
as its own pull request.

## What this can and cannot show

It can show, with counts, which frameworks the agent-changing pull requests
of one framework-neutral frame use, and how the bindings each framework's
pull requests change are written. It cannot show more than that:

- **The frame is titles, not code.** It finds pull requests whose authors
  wrote "agent" with "tool", "handoff" or "sub-agent" in the title. Products
  whose noun is "agent" (coding agents, assistant platforms) title their
  changes that way more often than an application that adds a tool to a
  framework agent ("Add web search tool"), so the frame likely over-counts
  repositories with their own tool registry and under-counts framework
  applications. No frame that GitHub's search offers reads a pull request's
  code, so this bias cannot be removed with the API alone; it is stated, not
  corrected.
- **The holdout pool is left out unread.** Nine of the pull requests the
  neutral queries returned came from repositories in the holdout's pool, which
  is built from code that imports the SDK or ADK. They were dropped by name,
  so an SDK or ADK change among them is not counted. If all nine were relevant
  SDK or ADK changes, and all had been walked, the SDK and ADK would have nine.
  In the framework frames, the pool removed most of what code search returns
  for the SDK and ADK (see `pool_repositories_skipped` in `sample.json`), so
  those two frames hold the repositories the pool never recorded.
- **Small counts.** The share table gives each count with its 95% Wilson
  interval. A framework with no relevant pull request in this sample can
  still hold a few percent of the population.
- **One judge.** One agent judged every pull request in both frames (Claude Code,
  Opus 5.5), not the two-scorer protocol of the application benchmark. The
  rationale of every judgment is in `sample.json`, so a second scorer can
  check any of them.
- **The prefilters.** In the neutral frame the prefilter only ordered the
  reading: every walked pull request was judged, and one relevant pull
  request it missed is counted. In the framework frames only flagged pull
  requests were judged, so a binding change written in an idiom the
  framework's pattern does not match is not seen there.
- **Readability is the judge's reading of the changed binding, not a
  reader's result.** `static` and `flow` say a static reader could follow the
  binding; they do not say the existing readers do. `diff --application` was
  not run on any member.
- **Wiring, not every binding change.** A change only to a tool's own body is
  a binding change under the application benchmark's Definitions but is not
  counted here, for every framework alike.

## Query log

All searches ran on 2026-11-17 through the GitHub API with the `gh` CLI,
read-only. `sample.json` records every request with its time, the query
string, GitHub's total count and how many results it returned.

- **Neutral frame**: GraphQL `search(type: ISSUE)` with
  `is:pr is:public created:<hour>..<hour+59:59> <query>`, for each of the four
  title queries in each walked hour (`frames.neutral.requests`). The hours
  walked are listed in `frames.neutral.hours_walked`.
- **Framework frames**: REST `search/code`, up to three pages of 100 per
  query (`frames["framework:<name>"].code_search`), then GraphQL for the 15
  most recently created pull requests of each repository, kept when created
  in the window.

| Framework frame | Code search queries |
| --- | --- |
| OpenAI Agents SDK | `"from agents import" "function_tool" "tools=" language:Python` |
| Google ADK | `"from google.adk" "tools=" language:Python` |
| LangChain / LangGraph | `"create_react_agent" "tools" language:Python`; `"from langchain" "create_agent" "tools=" language:Python` |
| CrewAI | `"from crewai import" "tools=" language:Python` |
| PydanticAI | `"from pydantic_ai import" "tools=" language:Python`; `"from pydantic_ai" "@agent.tool" language:Python` |
| LlamaIndex | `"from llama_index" "FunctionTool" language:Python` |
| AutoGen / AG2 | `"from autogen_agentchat" "tools=" language:Python`; `"from autogen import" "register_function" language:Python` |
| smolagents | `"from smolagents import" "tools=" language:Python` |
| Agno | `"from agno.agent import Agent" "tools=" language:Python` |
| Semantic Kernel | `"semantic_kernel" "kernel_function" language:Python`; `"Microsoft.SemanticKernel" "KernelFunction" language:C#` |
| MCP server, Python | `"FastMCP" "@mcp.tool" language:Python` |
| MCP server, TypeScript | `"@modelcontextprotocol/sdk/server" "registerTool" language:TypeScript`; `"@modelcontextprotocol/sdk/server" "server.tool(" language:TypeScript` |

## Files

| File | What it is |
| --- | --- |
| [`sample.json`](sample.json) | The window, the seed, every search request with its date and count, the exclusion counts, and every judged pull request: repository, number, title, state, created date, merge base, head, whether the prefilter flagged it, relevance, framework, kind, whether the agent exists at the base, readability and the reason. |
| [`collect.py`](collect.py) | How the sample was recorded: the searches, exclusions, pins, prefilters and the dossiers the judge read. Search results drift, so re-running it does not reproduce the sample. |
| [`summarize.py`](summarize.py) | Counts the tables above from `sample.json`. `tests/test_framework_mix_benchmark.py` holds the README to them. |
