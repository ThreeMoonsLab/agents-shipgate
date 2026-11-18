# Framework mix of agent-changing pull requests (#916)

Which agent frameworks do real pull requests that change an agent's tools,
sub-agents or handoffs use? `diff --application` reads the OpenAI Agents SDK
and Google ADK. Every application corpus so far was selected by importing one
of those two, so none of them can say what share of real agent changes the
route can address at all. This directory records two samples, judged by hand
from source at pinned refs: one drawn by searches that name no framework, and
one per framework that judges how readable each framework's bindings are.

**Read the share as a share of pull requests titled as agent tool changes,
not of agent-changing pull requests in general.** The neutral frame searches
titles. Pull requests that change an SDK or ADK agent are almost never titled
that way: 1 of the 49 in the application development corpus is (see [what
the title frame reaches](#what-the-title-frame-reaches)). The frame reaches
repositories whose product is itself an agent, or a coding agent's tooling,
far better than applications built on a framework.

It is the evidence for the
[next-reader decision record](../../docs/engineering/next-application-reader.md).
It is not a market estimate, a success rate or a qualification claim, and it
must not be cited as one.

## Method

**Window.** Pull requests created from `2026-08-19T00:00:00Z` up to
`2026-11-17T00:00:00Z` (the 90 days before the sample was recorded), in
public repositories, merged or still open. Closed-unmerged pull requests are
left out.

**Neutral frame.** GitHub's pull-request search for four title queries that
name no framework: `agent tool`, `agent handoff`, `subagent` and
`"sub-agent"`, each with `in:title`. GitHub stems the words, so `agent tool`
also matches `agents`, `agentic`, `tools` and `tooling`. The window's 2,160
hours were ordered by `sha256("issue-916-framework-mix:<hour>")` and the
first 24 hours of that order were queried, every result of every query in
each hour. Pull requests without a non-test code file were left out, the
remaining 881 ordered by `sha256("issue-916-framework-mix:<url>")`, and the
first 250 of that order walked. Every walked pull request was judged,
including the 25 the prefilter (a changed line of non-test code mentions a
tool, handoff or sub-agent) did not flag, so the prefilter only ordered the
reading and its misses are counted.

**Framework frames: readability.** For each framework, GitHub code search
for its import and tool-binding idiom (up to 300 results per query), the 15
most recently created pull requests of each repository it returned, and the
same exclusions; a pull request already in the neutral frame is left out of
these. Candidates were walked in seeded order
(`sha256("issue-916-framework-mix:<framework>:<url>")`), at most 200 per
framework; a candidate is judged when a changed line of non-test code matches
the framework's binding idiom in a file that imports the framework, with at
most three judged per repository. Judging stopped at 15 relevant pull
requests for a framework; only the TypeScript MCP frame reached that. These
frames find a framework's pull requests so that their bindings can be judged;
their counts say nothing about share. Pull requests found through one
framework's search are counted under the framework their changed agent
actually uses. The import check is a regular expression, so a local package
named `agents` reads as the SDK (`JuanSync7/project-keel#30`); the tag only
selects what is judged, and the judgment reads the code.

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
pull request was fetched. Both are full 40-character SHAs.

**What the judge read.** For each pull request, a dossier: its title, its
changed non-test code files, the import lines of up to 12 touched files (10
in the framework frames) read at the head (or base), and the changed lines
that match a binding pattern. The dossier is capped: at most 16 lines in the
neutral frame (7 per file, 3 when only weak matches) and 30 in the framework
frames (6 per file). When a dossier was unclear the judge opened the full
patch and the files it touches. `collect.py` writes these dossiers.

76 judgments rest on a dossier whose line cap stopped before a later code file
that also had matching lines (`dossier_capped` in `sample.json`). After
review, every such pull request judged not relevant as application code was
re-read from its full patch (`reread_full_patch`); two of them were relevant
and are now counted (`realopen-ai/realopen-ai#56`, `jymchng/agenthicc#12`).
Of the capped relevant ones, the cap can only hide more of a change already
seen: four whose readability is judged were re-read, and the rest are
`custom`, another named framework (neither judged for readability) or
already `runtime`, the hardest level, which hidden lines cannot raise. The capped
coding-agent tooling, samples, framework internals and non-agents are classed
by what the repository is, which the hidden lines do not change.

<!-- summarize:dossiers -->
| Judgment | Judged from a capped dossier | Re-read from the full patch |
| --- | ---: | ---: |
| Not relevant, application code | 21 | 21 |
| Relevant | 26 | 6 |
| Not relevant: coding-agent tooling, sample, framework internals, test or not an agent | 29 | 0 |
| **All judged (379)** | **76** | **27** |
<!-- /summarize:dossiers -->

**Judging.** Relevance is the application corpus's Q0 read across
frameworks: in non-test application code, the pull request adds or removes a
tool, sub-agent or handoff of an agent, creates an agent that binds them,
changes the code that selects them at run time, or adds or removes a tool an
MCP server registers. Not counted:

- a change only to a tool's own body, signature or description ("definition
  only"), and tool definitions nothing registers or binds;
- prompts, UI, tests, samples, tutorials and course work, and the
  frameworks' own library code;
- **coding-agent tooling**: configuration, extensions, plugins, subagents and
  skills for Claude Code, Codex, pi, opencode or similar harnesses; apps that
  run, coordinate or display their sessions; and MCP servers or memory layers
  packaged for them. An MCP server that any client may use counts as an MCP
  server, whoever happens to call it; one built as a coding agent's plugin or
  hook is coding-agent tooling. A coding agent product's own built-in tools
  (a repository that is itself an agent) are counted, as `custom`.

**Stacked pull requests.** Each pull request is a sampled unit, so a change
that two stacked pull requests both carry is counted in each, and the
rationale says so (`jymchng/agenthicc#11` and `#12`, `krasun/notes-mcp#5` and
`#6`, `parshwa0104/sih_26176#13` and `#14`).

The framework is read off the changed agent's construction or the server's
registration, not off the repository.

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
  a feature flag, a role or permission scope, plugin discovery, a registry
  filled at run time or reflection. A member added under such a condition is
  `runtime` even when its name is a literal.

When a pull request changes several bindings, the hardest one decides
(`runtime` over `flow` over `static`).

Readability is not judged for `custom` pull requests or for frameworks #916
does not list: no reader under discussion would read them, so it decides
nothing here.

`diff --application` was not run on any member. The question is what real
pull requests use, judged from source, not how the engine answers them.

## Neutral frame: what pull requests titled as agent tool changes use

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
| **Relevant: changes an agent's tools, sub-agents or handoffs, or an MCP server's tools** | **59** |
| Not relevant: `application` | 84 |
| Not relevant: `coding_agent_extension` | 83 |
| Not relevant: `not_agent` | 11 |
| Not relevant: `framework_internal` | 6 |
| Not relevant: `sample` | 6 |
| Not relevant: `test_only` | 1 |
<!-- /summarize:neutral-funnel -->

<!-- summarize:neutral-share -->
| Framework of the changed agent or server | Relevant PRs | Share | 95% interval (independent draws) | Existing agent | Static | Flow | Runtime | Not judged |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| OpenAI Agents SDK | 0 | 0% | 0–6% | 0 | 0 | 0 | 0 | 0 |
| Google ADK | 0 | 0% | 0–6% | 0 | 0 | 0 | 0 | 0 |
| LangChain / LangGraph | 5 | 8% | 4–18% | 2 | 1 | 1 | 3 | 0 |
| CrewAI | 0 | 0% | 0–6% | 0 | 0 | 0 | 0 | 0 |
| PydanticAI | 1 | 2% | 0–9% | 1 | 0 | 0 | 1 | 0 |
| LlamaIndex | 0 | 0% | 0–6% | 0 | 0 | 0 | 0 | 0 |
| AutoGen / AG2 | 0 | 0% | 0–6% | 0 | 0 | 0 | 0 | 0 |
| smolagents | 0 | 0% | 0–6% | 0 | 0 | 0 | 0 | 0 |
| Agno | 0 | 0% | 0–6% | 0 | 0 | 0 | 0 | 0 |
| Semantic Kernel | 0 | 0% | 0–6% | 0 | 0 | 0 | 0 | 0 |
| MCP server, Python | 1 | 2% | 0–9% | 0 | 0 | 0 | 1 | 0 |
| MCP server, TypeScript/JavaScript | 9 | 15% | 8–27% | 9 | 0 | 6 | 3 | 0 |
| MCP server, other languages | 5 | 8% | 4–18% | 4 | 2 | 3 | 0 | 0 |
| Other named frameworks (mastra 1, pipecat 1, vercel-ai-sdk 3, webmcp 1) | 6 | 10% | 5–20% | 5 | 0 | 0 | 0 | 6 |
| Custom (the repository's own tool registry or loop) | 32 | 54% | 42–66% | 27 | 0 | 0 | 0 | 32 |
| **All relevant** | 59 | 100% | - | 48 | 3 | 10 | 8 | 38 |
<!-- /summarize:neutral-share -->

In counts, among 250 pull requests titled as agent tool, handoff or sub-agent
changes: 59 change an agent's tools, sub-agents or handoffs, or an MCP
server's tools. 32 of the 59 use no framework, the repository's own tool
registry or loop over a model API; this is the share the title frame
inflates most, because it is drawn from repositories that are themselves
agents. 15 change an MCP server's tools. 6 use a framework #916 lists
(LangChain or LangGraph 5, PydanticAI 1) and 6 another named framework. None
uses the OpenAI Agents SDK or Google ADK, which, given what the frame reaches,
says little about how often SDK or ADK agents change. 83 of the 250 were
coding-agent tooling, the host route's territory, and are not counted as
relevant.

## What the title frame reaches

The neutral frame's word rule, applied to other titles
(`summarize.py`'s `title_matches`, which reproduces GitHub's stemmed match):

<!-- summarize:title-reach -->
| Pull requests | Title meets the frame's word rule | 95% interval |
| --- | ---: | ---: |
| The neutral frame's walked pull requests with a recorded title (does the rule reproduce the search?) | 248 of 249 | 98–100% |
| Relevant pull requests the framework frames found | 1 of 59 | 0–9% |
| The application development corpus (SDK and ADK changes, `development.json`) | 1 of 49 | 0–11% |
<!-- /summarize:title-reach -->

The same rule matches 9 of the 124 retrievable titles of the 134-PR SDK/ADK
screening corpus of 2026-09-25 (kept outside the repository). Pull requests
that change a framework application are titled by feature ("Add web search
tool", "feat(agents): …"), not as agent tool changes. So the frame reaches
repositories that are themselves agents far better than it reaches framework
applications, and the share table cannot say how common SDK, ADK or any
other framework's agent changes are among agent-changing pull requests in
general. It can say which frameworks the PRs it does reach use, and the
framework frames measure readability where the neutral frame is blind.

## Framework frames: how readable each framework's bindings are

<!-- summarize:framework-frames -->
| Framework | Code-search files | Candidates | Walked | Flagged | Judged | Relevant | Existing agent | Static | Flow | Runtime |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| OpenAI Agents SDK | 13,168 | 89 | 81 | 11 | 11 | 3 | 2 | 2 | 0 | 1 |
| Google ADK | 40,384 | 40 | 37 | 3 | 3 | 1 | 1 | 1 | 0 | 0 |
| LangChain / LangGraph | 40,320 + 38,016 | 170 | 148 | 23 | 23 | 12 | 9 | 4 | 1 | 7 |
| CrewAI | 23,488 | 103 | 99 | 8 | 8 | 5 | 3 | 4 | 1 | 0 |
| PydanticAI | 6,560 + 5,112 | 665 | 200 | 18 | 18 | 9 | 7 | 2 | 0 | 7 |
| LlamaIndex | 5,048 | 100 | 100 | 6 | 6 | 1 | 1 | 0 | 1 | 0 |
| AutoGen / AG2 | 1,064 + 3,448 | 204 | 200 | 5 | 5 | 1 | 1 | 0 | 1 | 0 |
| smolagents | 5,328 | 68 | 68 | 5 | 5 | 2 | 2 | 1 | 0 | 1 |
| Agno | 20,352 | 34 | 34 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Semantic Kernel | 12,032 + 5,152 | 110 | 110 | 3 | 3 | 1 | 0 | 0 | 1 | 0 |
| MCP server, Python | 155,392 | 151 | 136 | 20 | 20 | 8 | 8 | 3 | 4 | 1 |
| MCP server, TypeScript/JavaScript | 100,992 + 56,960 | 922 | 200 | 36 | 27 | 15 | 15 | 2 | 9 | 4 |
<!-- /summarize:framework-frames -->

Readability of every judged relevant pull request, from both frames:

<!-- summarize:readability -->
| Framework | Relevant PRs, both frames | Static | Flow | Runtime | Static or flow |
| --- | ---: | ---: | ---: | ---: | ---: |
| OpenAI Agents SDK | 3 | 2 | 0 | 1 | 2 of 3 |
| Google ADK | 1 | 1 | 0 | 0 | 1 of 1 |
| LangChain / LangGraph | 17 | 5 | 2 | 10 | 7 of 17 |
| CrewAI | 5 | 4 | 1 | 0 | 5 of 5 |
| PydanticAI | 10 | 2 | 0 | 8 | 2 of 10 |
| LlamaIndex | 1 | 0 | 1 | 0 | 1 of 1 |
| AutoGen / AG2 | 1 | 0 | 1 | 0 | 1 of 1 |
| smolagents | 2 | 1 | 0 | 1 | 1 of 2 |
| Agno | 0 | 0 | 0 | 0 | - |
| Semantic Kernel | 1 | 0 | 1 | 0 | 1 of 1 |
| MCP server, Python | 9 | 3 | 4 | 2 | 7 of 9 |
| MCP server, TypeScript/JavaScript | 24 | 2 | 15 | 7 | 17 of 24 |
| MCP server, other languages | 5 | 2 | 3 | 0 | 5 of 5 |
<!-- /summarize:readability -->

The framework frames found few relevant pull requests for most frameworks:
code search returns many tutorial and personal repositories with no recent
pull request, and the holdout pool removed most of what it returns for the
SDK (194 of 254 repositories) and ADK (172 of 240). Agno has no judged
relevant pull request; ADK, LlamaIndex, AutoGen and Semantic Kernel one each;
smolagents two; the SDK three and CrewAI five. Their rows are shown, but they
measure nothing to any useful precision. Where there are nine or more:
LangChain or LangGraph 7 of 17 static or flow, PydanticAI 2 of 10, MCP
servers in Python 7 of 9 and in TypeScript or JavaScript 17 of 24, most of
the latter `flow` (tool definitions registered in a loop or from a
registration function that receives the server).

## reach, readable and addressable

The decision record's quantities, from this sample. reach is per 100
relevant pull requests of the neutral frame, over all of them and over the
ones that change an agent's binding (MCP server registrations left out, as
the record proposes unless the owner brings them into scope); readable is
the static-or-flow share from both frames; addressable is their product, and
the last column uses the low end of reach's interval:

<!-- summarize:addressable -->
| Framework | Neutral relevant | reach over all relevant | reach over agent-binding PRs | Static or flow, both frames | addressable over all | addressable over agent-binding | …with reach at the low end of its interval |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| OpenAI Agents SDK | 0 | 0.0 of 100 (0 of 59) | 0.0 of 100 (0 of 44) | 2 of 3 | 0.0 | 0.0 | 0.0 |
| Google ADK | 0 | 0.0 of 100 (0 of 59) | 0.0 of 100 (0 of 44) | 1 of 1 | 0.0 | 0.0 | 0.0 |
| LangChain / LangGraph | 5 | 8.5 of 100 (5 of 59) | 11.4 of 100 (5 of 44) | 7 of 17 | 3.5 | 4.7 | 2.0 |
| PydanticAI | 1 | 1.7 of 100 (1 of 59) | 2.3 of 100 (1 of 44) | 2 of 10 | 0.3 | 0.5 | 0.1 |
| MCP server, Python | 1 | 1.7 of 100 (1 of 59) | - | 7 of 9 | 1.3 | - | - |
| MCP server, TypeScript/JavaScript | 9 | 15.3 of 100 (9 of 59) | - | 17 of 24 | 10.8 | - | - |
| MCP server, other languages | 5 | 8.5 of 100 (5 of 59) | - | 5 of 5 | 8.5 | - | - |
<!-- /summarize:addressable -->

## What this can and cannot show

It can show, with counts, which frameworks the pull requests titled as agent
tool changes use, and how the bindings each framework's pull requests change
are written. It cannot show more than that:

- **The title frame barely reaches framework applications.** See [what the
  title frame reaches](#what-the-title-frame-reaches): 1 of 49 development
  corpus titles, 1 of 59 relevant framework-frame titles. No search GitHub
  offers reads a pull request's code, so a frame that samples framework
  applications as they are titled needs another source; this one is stated,
  not corrected.
- **The holdout pool is left out unread.** Nine of the 1,219 pull requests
  the neutral queries returned came from repositories in the holdout's pool,
  which is built from code that imports the SDK or ADK. They were dropped by
  name. In expectation about two of them would have been among the 250
  walked (9 × 250 / 1,210); if both were SDK or ADK changes, that is about 2
  of 61 relevant, up to about 11% at the top of its interval. In the
  framework frames, the pool removed most of what code search returns for
  the SDK and ADK (see `pool_repositories_skipped` in `sample.json`).
- **The intervals are too narrow.** They are Wilson intervals for
  independent draws. The neutral frame is 24 clusters (the hours drawn), and
  some repositories appear more than once, so the real uncertainty is wider.
- **Small counts.** A framework with no relevant pull request here can still
  hold a few percent of what the frame reaches.
- **One judge.** One agent judged every pull request in both frames (Claude
  Code, Opus 5.5), not the two-scorer protocol of the application benchmark.
  An independent review re-read part of the sample; the judgments it found
  wrong are corrected, and the rules it sharpened (coding-agent tooling,
  stacked pull requests, capped dossiers) were applied again to every
  member, not only to the ones it named. The rationale of every judgment is
  in `sample.json`, so a second scorer can check any of them.
- **Dossiers, not always the whole patch.** Most judgments were made from
  the capped dossier `collect.py` writes; see the dossier table under
  [Method](#method) for how many hit the cap and how many were re-read.
- **The prefilters.** In the neutral frame the prefilter only ordered the
  reading: every walked pull request was judged. In the framework frames
  only flagged pull requests were judged, so a binding change written in an
  idiom the framework's pattern does not match is not seen there.
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

Two titles that name a private person are left out of `sample.json`
(`title: null`); their pull requests are excluded samples.

## Files

| File | What it is |
| --- | --- |
| [`sample.json`](sample.json) | The window, the seed, every search request with its date and count, the exclusion counts, and every judged pull request: repository, number, title, state, created date, merge base, head, whether the prefilter flagged it, whether its dossier was capped and whether it was re-read from the full patch, relevance, framework, kind, whether the agent exists at the base, readability and the reason. |
| [`collect.py`](collect.py) | How the sample was recorded: the searches, exclusions, pins, prefilters and the dossiers the judge read. Search results drift, so re-running it does not reproduce the sample. |
| [`summarize.py`](summarize.py) | Counts the tables above from `sample.json`. `tests/test_framework_mix_benchmark.py` holds the README to them. |
