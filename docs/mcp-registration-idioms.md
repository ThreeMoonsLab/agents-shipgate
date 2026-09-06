# MCP registration idioms: the survey behind the built-in registry

Most MCP servers never emit their tool surface. Three first-party vendor
servers were walked before this input existed, and `detect` reported two of
them as *not an agent project*:

| repo | language | how a tool is named | seen before #431 |
|---|---|---|---|
| `mongodb-js/mongodb-mcp-server` | TypeScript | `static toolName = "aggregate"` | no |
| `grafana/mcp-grafana` | Go | `mcpgrafana.MustTool("update_incident", …)` | no |
| `github/github-mcp-server` | Go | `mcp.Tool{Name: "issue_read"}`, plus committed `__toolsnaps__/*.snap` | no |

The discriminator was never the language — two of the three are Go. It is
whether the repository happens to *emit* its tool list as a committed artifact,
and only one of the three does. What all three do is write the tool's name as a
**string literal at a registration site**.

Issue [#431](https://github.com/ThreeMoonsLab/agents-shipgate/issues/431)
settled the shape of the answer — a built-in, versioned registry of *named
registration idioms*, never a user-configurable pattern — and required a survey
before choosing which idioms to ship: *"count registration idioms across the
top ~30 registry servers, then ship the smallest set that covers the beachhead
and the measured majority. Anything below the survey's coverage line waits for
evidence."*

## Method

Thirty MCP servers and the three official SDKs were cloned at `--depth 1` and
scanned for candidate registration shapes. Counts are call sites, not tools;
test files are included in these raw counts and excluded by the shipped reader
(see *What the reader excludes*). Two listed repositories (`pulumi/mcp-server`,
`influxdata/influxdb-mcp-server`) could not be cloned and are not counted.

## Result

| idiom | repositories | largest single repo | shipped |
|---|---|---|---|
| Python `@mcp.tool` (server decorator) | 5 | `awslabs/mcp` (488) | yes (`py_fastmcp_decorator`) |
| Go `Tool{Name: "…"}` | 3 | `github/github-mcp-server` (135) | yes (`go_tool_struct`) |
| TS `.tool(` / `.registerTool("…"` | 3 | `cloudflare/mcp-server-cloudflare` (85) | yes (`ts_sdk_register_tool`) |
| Go `NewTool("…"` | 2 | `hashicorp/terraform-mcp-server` (63) | yes (`go_new_tool`) |
| Go `MustTool("…"` | 1 | `grafana/mcp-grafana` (132) | yes (`go_must_tool`) |
| TS `static toolName = "…"` | 1 | `mongodb-js/mongodb-mcp-server` (77) | yes (`ts_static_tool_name`) |

The six shipped idioms cover every surveyed server. Re-measured against the
current heads of the three walked vendor repositories when #484 shipped, the
reader finds 61, 115 and 114 tool names, with 3, 0 and 3 registrations whose
names are built at runtime — each recorded as an unenumerated subject rather
than dropped. (#431 published 61/114/110 with 3/1/3 against the heads of the
day; the same clones give the same numbers to the reader before and after
#484, so the difference is those servers gaining tools, not this reader
changing its mind.)

One shape was measured and deliberately rejected: an object literal carrying
`name:` and `description:` keys, which appears in 14 of the surveyed
repositories. It matches any object with those two keys, which is most
configuration in a TypeScript codebase, and a reader resting on it would invent
tools rather than find them.

## Python: a different mechanism, not a sixth pattern

Python was deliberately held back from the first increment and shipped in
[#484](https://github.com/ThreeMoonsLab/agents-shipgate/issues/484) with its
own adversarial probe list, because per
[#393](https://github.com/ThreeMoonsLab/agents-shipgate/issues/393) an
extraction mechanism can only claim what its probes have been run against. The
lexical reader had eight fail-open constructs in its first draft, all found by
writing that list.

Three facts make it a mechanism rather than a pattern, and each was measured
before it was designed for:

- **The name is usually not written down.** `@mcp.tool()` on `def dbsize()`
  registers `dbsize`. All 53 of `redis/mcp-redis`'s registrations are the bare
  form, so a reader looking for a literal beside the call would find none of
  them. The name is the `name=` literal where one is given and the decorated
  function's otherwise.
- **The schema is the signature.** The server builds the tool's input schema
  from the annotated parameters, so this idiom publishes one where the
  TypeScript and Go idioms genuinely have nothing to publish. The request
  `Context` parameter is dropped, because the server drops it too.
- **Whether it registers anything is a binding fact.** `@app.tool()` registers
  an MCP tool when `app` is a server and is somebody else's decorator
  otherwise. The reader follows the decorated name back to a server
  construction and refuses out loud when it cannot: `awslabs/mcp` writes
  `@self.mcp.tool()` on a server passed in as a constructor argument, and
  `app = create_server()` on the result of a factory. Both are recorded as
  unenumerated subjects — 107 of them across that monorepo, against 334 tools
  it does read.

The binding is followed **across modules**, because the population requires it:
`redis/mcp-redis` constructs its server in `src/common/server.py` and applies
every decorator in `src/tools/*.py`. An import is resolved by matching path
segments against the modules the walk read, and only when exactly one module
matches — the scanned root can sit anywhere along the import path, so neither
end is anchored, and two candidates mean the reader cannot tell which module
was imported.

### Three server classes, because all three are live

The class whose instance carries `.tool` has been spelled three ways, and the
reader knows the `(module, class)` pairs rather than one name:

| import | shipped in |
|---|---|
| `from fastmcp import FastMCP` | the standalone `fastmcp` package (2.x) |
| `from mcp.server.fastmcp import FastMCP` | the official Python SDK, v1 |
| `from mcp.server.mcpserver import MCPServer` | the official Python SDK, v2 |

The v2 rename is not a detail. `mcp.server.fastmcp` in the v2 SDK is a module
that exists only to raise `ModuleNotFoundError` with a migration hint, and 41
of `awslabs/mcp`'s servers had already moved when this shipped. A reader that
knew only `FastMCP` reported every one of their registrations as unenumerable —
105 tools read where there are 334.

### A route for a server whose tool names are all dynamic

The lexical idioms need a **resolved name** before discovery offers a route,
because they match a spelling and a spelling in a client repository is a
coincidence. The Python idiom does not: its site is only emitted after the
decorator has been followed back to a server construction, and a client does
not construct a server.

That distinction is load-bearing rather than theoretical.
`neo4j-contrib/mcp-neo4j` registers 40 tools and writes every single name as
`name=namespace_prefix + "…"`. Requiring a readable name would report the
repository as *not an agent project* precisely because its tool names are
built at run time, which is the silent miss this whole input exists to end.
`detect` offers the route, the evidence says "40 registration(s), none of which
this reader can name", and every one of them reaches the exclusion ledger.

For the same reason a committed export cannot displace such a route:
containment is the test, and an export contains an empty set of names
vacuously.

## What the reader does and does not read

It reads a name, an operation-class literal where the idiom defines one, and a
description literal where one sits beside the name — plus, for the Python
idiom, the decorated function's docstring and signature, because that is where
the server itself takes them from. It runs no code, evaluates no schema library
(Zod and Pydantic included), resolves no type, and reads no annotation the
source declares about itself: a parameter's annotation is mapped to a JSON
Schema type by spelling, never by import resolution.

Escape sequences are decoded with **each language's own grammar**, and anything
either grammar does not define is refused rather than guessed. Go writes an
octal escape as three digits — `MustTool("delete\137all", …)` registers
`delete_all` — and one decoder shared between the two languages read that as
`delete137all`: the real action missing from the catalog with an action id
nobody serves standing in its place. A refusal becomes a recorded omission,
which is the only affordable outcome for a *name*.

The name is checkable against the registration site that carries it. Effect and
authority still come from the declaration questionnaire, which is where they
came from for an exported surface too. A `static operationType = "delete"`
literal arrives as a low-confidence inferred hint that can *contradict* a
reviewer who declares the tool read-only, and can never make an action
pass-eligible on its own; `read` and `metadata` are deliberately unmapped,
because a tool server asserting its own harmlessness is the one claim an
untrusted source has an incentive to make.

### What the reader excludes

- **Test files** — `*_test.go`, `*.test.ts`, `*.spec.ts`, `test_*.py`,
  `*_test.py`, `conftest.py`, and files under `test/`, `tests/`, `__tests__/`,
  `testdata/`, `fixtures/`. A test's fake tool is not the published surface.
  Excluding them drops 14 phantom tools from the MongoDB repository and 8 from
  Grafana. Python is the reason the prefix rule exists at all: `pytest`
  collects `test_*.py`, so a suffix-only rule read `test_server.py` as the
  server.
- **Vendored and built code** — `node_modules/`, `vendor/`, `dist/`, `build/`,
  `.venv/`, `site-packages/`, `.tox/`. Somebody else's registrations are not
  this repository's surface.
- **JSX** (`.tsx`, `.jsx`) — JSX puts prose in code position, so an apostrophe
  in `<p>don't</p>` opens a string that never closes and the file reads as
  unreadable. That is the fail-closed direction, which is exactly why it is
  unaffordable: it would hold a whole repository's surface at `partial` over a
  contraction in a React component. No measured server registers a tool from a
  JSX module.

## Confidence, and where an export still wins

A registration read out of source is `medium`, including the Python one that
carries a signature: a signature is what the author wrote, not what the server
publishes, and the ceiling is about the route rather than about how much of it
was read. A committed export stays `high` and remains the better route wherever
one exists, because it is the server's own contract in the shape a client
receives it. `detect` therefore withholds the source route when a committed
export **names every tool the source route resolved**, and records the withheld
route in `excluded_sources` with the export that displaced it — named and
visible, never silently dropped.

Containment is the test, not location and not mere existence. "Any export in
the workspace wins" deleted real actions in two ways: in a repository holding
two servers, an export committed for one suppressed every source-only
registration of the other; and a partial export suppressed the rest of a single
server's surface. Where an export exists but does not contain the source
surface, both routes are suggested and the shortfall is named in the evidence —
two sources describing one server are reconciled by a reviewed `tool_identity`
binding, never by dropping one of them. A wildcard export claims a surface
without enumerating it, so it can never be shown to contain anything.

## Known limitation: a monorepo publishing several servers

`detect` offers **one** route per workspace, at the deepest directory
containing every registration it resolved. For a repository publishing several
independent servers — `modelcontextprotocol/servers` (7 under `src/`),
`cloudflare/mcp-server-cloudflare` (~15 under `apps/`) — that route is
over-broad: it names them all as one tool surface.

Splitting on which package declares the MCP dependency was implemented and
rejected on measurement. In `mongodb-js/mongodb-mcp-server` the SDK is declared
by an eval-harness package and, as a dev dependency, by three packages that
only support the one published server, so the split returned four scopes and
`detect` withheld the route entirely — from the repository this input exists to
reach. The over-broad route is visible in the manifest `init` writes and an
adopter narrows it in one line; the withheld one leaves them where they
started, which is the state #431 was filed about.

The zero-install detector (`tools/shipgate-detect.py`) carries a port of this
reader, so it answers these repositories the same way the installed CLI does —
[#485](https://github.com/ThreeMoonsLab/agents-shipgate/issues/485) closed the
divergence, and `tests/mcp_idiom_corpus.py` is what keeps the two from becoming
different implementations: every case either reader has ever been asked about
lives there once and both are driven through all of it, site by site and span
by span.

## Routing a diff

`TRIGGER-MCP-TOOL-REGISTRATION-SOURCE` routes on the registry's published
`diff_tokens`. Those are deliberately narrower than what the reader matches: a
matched capability rule overrides the workspace stop condition, so a router
firing on `.tool(` or a bare `Tool{` would turn "this is not an agent project"
into "run" for any diff containing that substring. `@mcp.tool` is the Python
token for the same reason: it is the spelling four of the five surveyed servers
use and 329 of `awslabs/mcp`'s call sites, while `@app.tool` — which the reader
does read — is a decorator name a non-MCP repository can plausibly carry. The
router's job is to stop the *silent* miss; the reader's is to read the surface
once the repository is adopted.

## Adding an idiom

Idioms live in `agents_shipgate.inputs.mcp_idioms`. Adding one means:

1. Evidence that a real workflow needs it — a repository whose surface is
   invisible without it, in the survey's terms.
2. An entry in `IDIOMS` with its `diff_tokens`, and a positive sample in
   `tests/test_mcp_idioms.py` (the sample is what proves the tokens are real).
3. Adversarial cases in `tests/mcp_idiom_corpus.py` for every way the shape can
   be faked or built at runtime — the corpus, not one test file, because the
   zero-install detector is driven through the same cases.
4. A bump of `IDIOM_REGISTRY_VERSION`, because the trigger catalog's token list
   is derived from this registry.
