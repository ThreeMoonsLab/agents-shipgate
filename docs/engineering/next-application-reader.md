# Decision record: the next application reader (#916)

Status: **proposed**. The owner accepts or rejects it; nothing in it is
implemented, and no reader is added under
[#916](https://github.com/ThreeMoonsLab/agents-shipgate/issues/916).
Recorded 2026-11-17 against `main` `fc8cbb6707`. Part of
[#868](https://github.com/ThreeMoonsLab/agents-shipgate/issues/868).

## Question

`diff --application` reads two frameworks: the OpenAI Agents SDK and Google
ADK. Which agent frameworks do real agent-changing pull requests use, and
which reader, if any, should come after those two?

Every application corpus so far was selected by importing the SDK or ADK, so
none of them could answer this. The
[framework-mix sample](../../benchmark/framework-mix/README.md) was drawn for
it: a framework-neutral frame for the share, and a frame per framework for
how readable each one's bindings are, every pull request judged by hand from
source at pinned refs. Its README states the method, the query log and the
limits; this record uses its tables.

## What the sample shows

**Share, from the neutral frame.** 250 pull requests whose titles name an
agent and a tool, handoff or sub-agent, drawn at random from 24 hours of the
90-day window and judged one by one; 61 change an agent's tools, sub-agents
or handoffs, or an MCP server's tools.

| Framework of the changed agent or server | Relevant PRs | Of 61 |
| --- | ---: | ---: |
| No framework: the repository's own tool registry or loop | 31 | 51% |
| An MCP server's registered tools (TypeScript or JavaScript 10, other languages 6, Python 2) | 18 | 30% |
| A framework #916 lists: LangChain or LangGraph 5, PydanticAI 1 | 6 | 10% |
| Another named framework (Vercel AI SDK 3, Mastra, Pipecat, WebMCP 1 each) | 6 | 10% |
| OpenAI Agents SDK or Google ADK | 0 | 0% (95% interval up to 6% each) |
| CrewAI, LlamaIndex, AutoGen or AG2, smolagents, Agno, Semantic Kernel | 0 | 0% |

**Readability, from both frames.** Of every judged relevant pull request of a
framework, how many change a binding a static reader could follow (`static`
or `flow`). Ten or more were found only for these:

| Framework | Static or flow | reach(F) per 100 | addressable(F) per 100 |
| --- | ---: | ---: | ---: |
| LangChain / LangGraph | 8 of 17 | 8.2 (5 of 61) | 3.9 |
| PydanticAI | 2 of 10 | 1.6 (1 of 61) | 0.3 |
| MCP server, TypeScript or JavaScript | 20 of 25 | 16.4 (10 of 61) | 13.1 |
| MCP server, Python | 11 of 13 | 3.3 (2 of 61) | 2.8 |

The SDK (3 of 4), CrewAI (5 of 5), and ADK, LlamaIndex, AutoGen, smolagents
and Semantic Kernel (one or two each) have too few judged pull requests to
measure, and none of them appears in the neutral frame. Agno has none.

Four things follow:

1. **The SDK and ADK are not where agent-changing pull requests are, in this
   frame.** 0 of 61. The application corpora are selected by those imports,
   so they could not show this; the holdout pool exclusion (9 of 1,219
   results, dropped unread) can raise it to at most 9.
2. **Half the relevant pull requests use no framework.** No framework reader,
   built now or later, reaches them.
3. **Among the frameworks #916 lists, only LangChain or LangGraph recurs**, and
   9 of its 17 judged changes select tools at run time: a filter on a role or
   a message type, a condition on configuration, tools listed from an MCP
   server.
4. **MCP server tool registrations are the largest bucket a static reader
   could read** (18 of 61; 37 of 44 judged `static` or `flow`), but in
   TypeScript, where most of them are, 17 of 25 are `flow`: definitions
   registered in a loop, or registered by a function that receives the
   server. And a server's tool is not an agent's binding.

The sample's limits are in its README: a title frame likely under-counts
framework applications, one judge, small counts. They are reasons to read
these as orders of magnitude, not to reverse them.

## Decision (proposed)

**No new framework reader now.** No framework #916 lists meets the criterion
below: LangChain or LangGraph is the closest, at about 4 addressable pull
requests per 100 against a bar of 5, and is the first candidate when the
criterion is measured again. Reader work stays on the SDK and ADK readers'
lists and gates (#1147, #874, #1149), where Q1 is 0 of 49 today: a third
reader built on the same machinery would add reach and no Q1.

**MCP server tool registrations are a scope question, not a framework
adapter.** On this sample they are well above the reach bar (about 25
addressable per 100, 13 of them TypeScript or JavaScript), and the
repository already reads their literal registrations for `scan`
(`mcp_idioms.py`, `mcp_server_source.py`). Whether `diff --application` should compare a
server's registered tools as well as an agent's bindings is the owner's
decision, as #1150 asks for instructions and models. This record does not
make it; if the owner wants it, it is its own issue with its own corpus.

## The criterion, in headline-metric terms

The application route's headline metric is #868's: Q1 and Q2 cases on the
development corpus and the holdout, toward ten Q2 cases. A reader moves it
only through pull requests it can reach (Q0) and read (Q1). For a candidate
framework `F`, from a neutral sample:

- **reach(F)**: relevant pull requests that change an `F` agent, per 100
  relevant agent-changing pull requests;
- **readable(F)**: the share of `F`'s judged relevant pull requests whose
  changed binding is `static` or `flow`: determinable from source, the way
  the SDK and ADK readers' shared machinery determines a Python tools list;
- **addressable(F) = reach(F) × readable(F)**.

Build a reader for `F` when all three hold:

1. **Reach.** addressable(F) is at least **5 per 100** relevant agent-changing
   pull requests, on a neutral sample of at least 100 relevant pull requests,
   and the lower end of reach(F)'s 95% interval is above zero.
2. **The shared machinery yields rows.** Q1 on the development corpus is
   above its best recorded value, 4 of 49 (the 2026-10-05 ledger), on `main`.
   A third reader inherits the same list reading and the same gates. While
   the two readers it would join establish no row (0 of 49 on the 2026-11-08
   ledger), a new one adds Q0 reach and no Q1.
3. **A frozen corpus first.** Before the reader merges, a pinned corpus of at
   least 20 relevant `F` pull requests, chosen by a rule committed before the
   reader exists, as #908 did for the SDK and ADK. The reader is then held to
   a Q1 rate on it at least the SDK and ADK readers' rate on their development
   corpus.

Stated as one line: **a reader is worth building when it adds at least 5
statically readable, relevant pull requests per 100 agent-changing pull
requests, at a Q1 rate at least the SDK and ADK readers' own.**

This sample has 61 relevant pull requests, below the 100 that item 1 asks
for, so a framework above the bar here would still need a second sample. No
framework #916 lists is above it.

## The ROADMAP non-goal

[`ROADMAP.md` § Explicit non-goals](../../ROADMAP.md#explicit-non-goals)
says more framework adapters is not the roadmap: a new adapter is accepted
only when a real workflow needs one, and #1147 cites that non-goal for new
framework readers. This recommendation keeps it: no reader is proposed now.

If the owner accepts the criterion above, it becomes the measurable form of
"a real workflow needs one" for application readers. Then the non-goal's text
should say so, for example: *a new application reader is accepted when it
meets the reach-and-readability criterion in
`docs/engineering/next-application-reader.md`*. Changing the ROADMAP is the
owner's decision, and this record does not edit it. Until the owner decides,
the non-goal stands as written.

## Reader cost

Estimates, not measurements. The SDK reader is `openai_sdk_static.py`, 2,509
lines; the ADK reader, `google_adk.py`, is 5,372 with its `scan` artifacts.
Both stand on shared machinery that any Python framework whose agents are
built by a call with a tools list could reuse: `python_imports.py` (5,562),
`list_expressions.py` (4,279), `builder_calls.py` (4,727), `object_tools.py`
(1,065), `tool_reach.py` (7,268) and `agent_construction_identity.py`.

| Candidate | What it could reuse | What is new | Size |
| --- | --- | --- | --- |
| LangChain / LangGraph | The shared machinery; `langchain.py` (441 lines, `scan`) already recognises `@tool`, `StructuredTool`, `create_agent`, `create_react_agent`, `ToolNode` and `bind_tools` | A constructor table with `create_react_agent`'s positional tools; LangGraph graph topology (`add_node` sub-agents, `Command` handoffs), which no reader models; tools listed from an MCP client at run time | About the SDK reader's size for `create_agent` and `create_react_agent`; graphs beyond that |
| PydanticAI | The shared machinery; `object_tools.py` for MCP toolsets | A decorator that binds to an agent instance (`@agent.tool`); `FunctionToolset` objects | Medium, for 2 readable of 10 judged |
| CrewAI | The shared machinery; `crewai.py` (575 lines, `scan`) | `BaseTool` classes, `Crew(agents=[...])` membership, `@agent` builder methods, agents configured in YAML | Medium, for no neutral reach |
| MCP server registrations | `mcp_idioms.py` (3,794) and `mcp_server_source.py` (767), which read a literal name at a registration site whose server they can prove | A comparison of two catalogs; the TypeScript `flow` shapes (arrays, loops, registration functions), for which the repository has no TypeScript reader beyond literal idioms | Low for literal Python and Go sites; high for the TypeScript shapes that dominate |
| LlamaIndex, AutoGen or AG2, smolagents, Agno | The shared machinery | A constructor table each | Medium each, no measured reach |
| Semantic Kernel | Nothing for C# | A C# reader | High |

## Consequences

- No public surface is added, and the ROADMAP and `CONTRIBUTING.md`'s surface
  discipline are unchanged.
- The application route's claims stay scoped to the SDK and ADK, and its
  corpora measure those two frameworks only. This sample says most
  agent-changing pull requests in a framework-neutral frame are neither.
- 80 of the 250 judged pull requests were coding-agent tooling:
  configuration, extensions, and apps that run or display coding-agent
  sessions. They are the host route's territory, not this one's, and the two
  routes see different pull requests.
- #1149's option B starts from the changed lines and asks whether a hunk is a
  tool's body or an element of a list that reaches a binding. That question
  depends less on a framework's constructor table than option A does.
  Whether it would extend past the SDK and ADK is untested here.

## What would change this decision

- A second neutral sample, with at least 100 relevant pull requests, in which
  LangChain or LangGraph reaches 5 addressable per 100; or a frame that reads
  code rather than titles and shows the title frame under-counts framework
  applications.
- Q1 on the development corpus above 4 of 49 (criterion 2).
- An owner decision that MCP server registrations are in the application
  route's scope.
- A design partner or pilot whose real workflow uses a framework the readers
  do not read: the ROADMAP's own test.
