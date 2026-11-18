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
it: a frame whose searches name no framework, for which frameworks appear,
and a frame per framework for how readable each one's bindings are, every
pull request judged by hand from source at pinned refs. Its README states
the method, the query log and the limits; this record uses its tables.

## What the sample can and cannot say

The neutral frame searches pull-request titles: it samples **pull requests
titled as agent tool, handoff or sub-agent changes**. Framework applications
are rarely titled that way. The frame's word rule matches 1 of the 49 titles
in the application development corpus (SDK and ADK changes), 1 of the 59
relevant pull requests the framework frames found, and 9 of the 124
retrievable titles of the 134-PR SDK/ADK screening corpus. So the frame
reaches repositories whose product is an agent far better than it reaches
applications built on a framework, and its shares are shares among the pull
requests it reaches, not among agent-changing pull requests in general. The
share it most inflates is `custom`, the repository's own tool loop.

It can still say which frameworks the pull requests it reaches use, and the
framework frames, which search code, measure readability where the title
frame is blind.

## What the sample shows

**Which frameworks, among PRs titled as agent tool changes.** 250 such pull
requests, drawn at random from 24 hours of the 90-day window and judged one
by one; 59 change an agent's tools, sub-agents or handoffs, or an MCP
server's tools. 44 of the 59 change an agent's binding; 15 change an MCP
server's registered tools.

| Framework of the changed agent or server | Relevant PRs | Of 59 | Of the 44 agent-binding PRs |
| --- | ---: | ---: | ---: |
| No framework: the repository's own tool registry or loop | 32 | 54% | 73% |
| An MCP server's registered tools (TypeScript or JavaScript 9, other languages 5, Python 1) | 15 | 25% | - |
| A framework #916 lists: LangChain or LangGraph 5, PydanticAI 1 | 6 | 10% | 14% |
| Another named framework (Vercel AI SDK 3; Mastra, Pipecat, WebMCP 1 each) | 6 | 10% | 14% |
| OpenAI Agents SDK or Google ADK | 0 | 0% (95% interval to 6% each) | 0% (to 8%) |
| CrewAI, LlamaIndex, AutoGen or AG2, smolagents, Agno, Semantic Kernel | 0 | 0% | 0% |

The intervals assume independent draws; the sample is 24 hour-clusters with
some repositories repeated, so the real intervals are wider. The holdout
pool's nine pull requests were dropped unread; about two of them would be
expected among the 250 walked (9 × 250 / 1,210). If both were SDK or ADK
changes, that is 2 of 61 relevant (to 11% at the top of its interval) or 2
of 46 agent-binding (to 15%).

**Readability, from both frames.** Of every judged relevant pull request of a
framework, how many change a binding a static reader could follow (`static`
or `flow`). Nine or more were found only for these:

| Framework | Static or flow | Runtime |
| --- | ---: | ---: |
| LangChain / LangGraph | 7 of 17 | 10 |
| PydanticAI | 2 of 10 | 8 |
| MCP server, TypeScript or JavaScript | 17 of 24 (15 `flow`) | 7 |
| MCP server, Python | 7 of 9 | 2 |

The SDK (2 of 3), CrewAI (5 of 5), and ADK, LlamaIndex, AutoGen, smolagents
and Semantic Kernel (one or two each) have too few judged pull requests to
measure. Agno has none.

**reach, readable and addressable.** reach is per 100 relevant pull requests
of the neutral frame. The record proposes the agent-binding denominator
(MCP server registrations left out) unless the owner brings MCP servers into
the route's scope; both are shown:

| Framework | reach over all 59 | reach over 44 agent-binding | addressable over all | addressable over agent-binding | …with reach at the low end of its interval |
| --- | ---: | ---: | ---: | ---: | ---: |
| LangChain / LangGraph | 8.5 (5) | 11.4 (5; interval 5.0–24.0) | 3.5 | 4.7 | 2.0 |
| PydanticAI | 1.7 (1) | 2.3 (1) | 0.3 | 0.5 | 0.1 |
| OpenAI Agents SDK, Google ADK | 0 | 0 | 0 | 0 | 0 |
| MCP servers, all languages | 25.4 (15) | - | 20.6 | - | - |

Four things follow, each within what the frame reaches:

1. **No SDK or ADK change appears among the PRs titled as agent tool
   changes**, which says little about how often SDK or ADK agents change:
   the frame reaches 1 of 49 of the development corpus's SDK and ADK
   changes.
2. **Most relevant pull requests here use no framework.** This is the share
   the title frame inflates most, so it is an upper bound on what custom
   loops are in general, but no framework reader reaches any of them.
3. **Among the frameworks #916 lists, only LangChain or LangGraph recurs**,
   and 10 of its 17 judged changes select tools at run time: a filter on a
   role or a message type, a condition on configuration, tools listed from
   an MCP server. It is at the bar below, within noise: 4.7 addressable per
   100 agent-binding pull requests, 2.0 with reach at the low end of its
   interval, 3.5 over all relevant pull requests.
4. **MCP server tool registrations are the largest bucket a static reader
   could read** (15 of 59; 29 of 38 judged `static` or `flow` across both
   frames), but in TypeScript, where most of them are, 15 of 24 are `flow`:
   definitions registered in a loop, or by a function that receives the
   server. And a server's tool is not an agent's binding.

## Decision (proposed)

**No new framework reader now**, for two reasons that do not depend on the
share:

- **Q1 is 0 of 49** on the development corpus (the 2026-11-08 ledger). A
  third reader would stand on the same list reading and the same gates as
  the SDK and ADK readers, which establish no row today; it would add reach
  and no Q1 (criterion 2).
- **This sample cannot show any framework above the bar.** It has 44
  agent-binding relevant pull requests, below the 100 criterion 1 asks for,
  and its frame reaches framework applications 1 time in 49. LangChain or
  LangGraph is at the bar within noise and is the first candidate when the
  criterion is measured on a frame that reaches framework applications.

Reader work stays on the SDK and ADK readers' lists and gates (#1147, #874,
#1149).

**MCP server tool registrations are a scope question, not a framework
adapter.** In this frame they are well above the bar (about 21 addressable
per 100 relevant pull requests, 11 of them TypeScript or JavaScript), and the
repository already reads their literal registrations for `scan`
(`mcp_idioms.py`, `mcp_server_source.py`). Whether `diff --application`
should compare a server's registered tools as well as an agent's bindings is
the owner's decision, as #1150 asks for instructions and models. This record
does not make it; until the owner does, reach is counted over agent-binding
pull requests. If the owner wants MCP servers in scope, that is its own
issue with its own corpus.

## The criterion, in headline-metric terms

The application route's headline metric is #868's: Q1 and Q2 cases on the
development corpus and the holdout, toward ten Q2 cases. A reader moves it
only through pull requests it can reach (Q0) and read (Q1). For a candidate
framework `F`, from a sample whose selection names no framework:

- **reach(F)**: relevant pull requests that change an `F` agent, per 100
  relevant pull requests that change an agent's binding (MCP server
  registrations excluded unless the owner brings them into scope);
- **readable(F)**: the share of `F`'s judged relevant pull requests whose
  changed binding is `static` or `flow`: determinable from source, the way
  the SDK and ADK readers' shared machinery determines a Python tools list;
- **addressable(F) = reach(F) × readable(F)**.

Build a reader for `F` when all three hold:

1. **Reach.** addressable(F), computed with reach(F) at the low end of its
   95% interval, is at least **5 per 100**, on a sample of at least 100
   relevant agent-binding pull requests, drawn by a rule that reaches
   framework applications: it selects at least half of a framework-selected
   corpus's agent-changing pull requests (this sample's title rule: 1 of
   49).
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

Stated as one line: **a reader is worth building when, even at the low end
of its reach, it adds at least 5 statically readable, relevant pull requests
per 100 agent-changing pull requests, at a Q1 rate at least the SDK and ADK
readers' own.**

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
  corpora measure those two frameworks only. This sample does not measure
  how much of all agent-changing work that leaves out; it measures what the
  pull requests titled as agent tool changes use.
- 83 of the 250 judged pull requests were coding-agent tooling:
  configuration, extensions, plugins, memory layers and MCP servers built
  for coding agents, and apps that run or display their sessions. They are
  the host route's territory, not this one's, and the two routes see
  different pull requests.
- #1149's option B starts from the changed lines and asks whether a hunk is a
  tool's body or an element of a list that reaches a binding. That question
  depends less on a framework's constructor table than option A does.
  Whether it would extend past the SDK and ADK is untested here.

## What would change this decision

- A sample drawn by a rule that reaches framework applications (criterion
  1), with at least 100 relevant agent-binding pull requests, in which
  LangChain or LangGraph, or another framework, clears 5 addressable per 100
  at the low end of its reach.
- Q1 on the development corpus above 4 of 49 (criterion 2).
- An owner decision that MCP server registrations are in the application
  route's scope.
- A design partner or pilot whose real workflow uses a framework the readers
  do not read: the ROADMAP's own test.
