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
| Python `@mcp.tool` (FastMCP decorator) | 5 | `awslabs/mcp` (488) | **no — next increment** |
| Go `Tool{Name: "…"}` | 3 | `github/github-mcp-server` (135) | yes (`go_tool_struct`) |
| TS `.tool(` / `.registerTool("…"` | 3 | `cloudflare/mcp-server-cloudflare` (85) | yes (`ts_sdk_register_tool`) |
| Go `NewTool("…"` | 2 | `hashicorp/terraform-mcp-server` (63) | yes (`go_new_tool`) |
| Go `MustTool("…"` | 1 | `grafana/mcp-grafana` (132) | yes (`go_must_tool`) |
| TS `static toolName = "…"` | 1 | `mongodb-js/mongodb-mcp-server` (77) | yes (`ts_static_tool_name`) |

The five shipped idioms cover every surveyed server whose tools are declared in
TypeScript or Go, including all three walked vendors. Measured against the
current heads of the three vendor repositories, the reader finds 61, 114, and
110 tool names respectively, with 3, 1, and 3 registrations whose names are
built at runtime — each recorded as an unenumerated subject rather than
dropped.

One shape was measured and deliberately rejected: an object literal carrying
`name:` and `description:` keys, which appears in 14 of the surveyed
repositories. It matches any object with those two keys, which is most
configuration in a TypeScript codebase, and a reader resting on it would invent
tools rather than find them.

## Why Python FastMCP is not in this increment

It is the largest single shape in the survey, and it is a **different
extraction mechanism**: the name defaults to the decorated function's, the
schema comes from the signature, and the provenance gate is an import binding
rather than a declared dependency. Per [#393](https://github.com/ThreeMoonsLab/agents-shipgate/issues/393),
the mechanism is reusable but each one needs its own adversarial probe list
before it can claim anything — the lexical reader shipped here had eight
fail-open constructs in its first draft, all found by writing that list. Python
gets its own increment, its own probes, and a real AST rather than a masking
lexer — filed as
[#484](https://github.com/ThreeMoonsLab/agents-shipgate/issues/484).

## What the reader does and does not read

It reads a name, an operation-class literal where the idiom defines one, and a
description literal where one sits beside the name. It runs no code, evaluates
no schema library (Zod included), infers no type, and reads no annotation the
source declares about itself.

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

- **Test files** — `*_test.go`, `*.test.ts`, `*.spec.ts`, and files under
  `test/`, `tests/`, `__tests__/`, `testdata/`, `fixtures/`. A test's fake tool
  is not the published surface. Excluding them drops 14 phantom tools from the
  MongoDB repository and 8 from Grafana.
- **Vendored and built code** — `node_modules/`, `vendor/`, `dist/`, `build/`.
  Somebody else's registrations are not this repository's surface.
- **JSX** (`.tsx`, `.jsx`) — JSX puts prose in code position, so an apostrophe
  in `<p>don't</p>` opens a string that never closes and the file reads as
  unreadable. That is the fail-closed direction, which is exactly why it is
  unaffordable: it would hold a whole repository's surface at `partial` over a
  contraction in a React component. No measured server registers a tool from a
  JSX module.

## Confidence, and where an export still wins

A literal at a registration site is `medium`. A committed export stays `high`
and remains the better route wherever one exists: it is the server's own
published contract and it carries the input schemas this input does not read.
`detect` therefore withholds the source route when a committed export **names
every tool the source route resolved**, and records the withheld route in
`excluded_sources` with the export that displaced it — named and visible, never
silently dropped.

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

The zero-install detector (`tools/shipgate-detect.py`) does not read registration
sites at all, so it still answers "not an agent project" for these repositories
while the installed CLI does not. That divergence is named in the script's own
"intentional simplifications" list, pinned by a test, and filed as
[#485](https://github.com/ThreeMoonsLab/agents-shipgate/issues/485).

## Routing a diff

`TRIGGER-MCP-TOOL-REGISTRATION-SOURCE` routes on the registry's published
`diff_tokens`. Those are deliberately narrower than what the reader matches: a
matched capability rule overrides the workspace stop condition, so a router
firing on `.tool(` or a bare `Tool{` would turn "this is not an agent project"
into "run" for any diff containing that substring. The router's job is to stop
the *silent* miss; the reader's is to read the surface once the repository is
adopted.

## Adding an idiom

Idioms live in `agents_shipgate.inputs.mcp_idioms`. Adding one means:

1. Evidence that a real workflow needs it — a repository whose surface is
   invisible without it, in the survey's terms.
2. An entry in `IDIOMS` with its `diff_tokens`, and a positive sample in
   `tests/test_mcp_idioms.py` (the sample is what proves the tokens are real).
3. Adversarial cases in the same file for every way the shape can be faked or
   built at runtime.
4. A bump of `IDIOM_REGISTRY_VERSION`, because the trigger catalog's token list
   is derived from this registry.
