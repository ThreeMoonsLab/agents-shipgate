# Application comparison without prior setup

**Availability:** see the [CHANGELOG entry](../CHANGELOG.md#application-comparison-without-prior-setup).
While that entry is under Unreleased, run the source checkout's `./shipgate`
or a build containing the feature.

For an OpenAI Agents SDK or Google ADK application, compare committed PR refs:

```bash
agents-shipgate diff --application --workspace /path/to/repository \
  --base BASE_SHA --head HEAD_SHA --scope backend
```

No `init`, manifest, saved baseline or authored declaration is required. The
command writes no files in the subject repository and runs no application code.
Fetch the refs first; the command never fetches. `--scope` defaults to the
repository root; `--head` defaults to committed `HEAD`, excluding dirty edits.
The base is the requested base ref's merge base with the selected head.

The result identifies each observed agent object and its added, removed or
changed tool bindings. It shows before/after signatures, binding and function
locations, and implementation digests. Existing reader observations supply the
binding edges; unrelated tool definitions are not agent capabilities. A missing
deployment-root selection does not hide known per-agent edges.

```text
ADDED  synthesizer_agent → python_exec
  before: no observed binding
  after: python_exec(code, dataset_json) -> str at backend/app/agents/synthesizer.py:38
```

A reviewer can inspect the new callable and decide whether that agent should
receive it. A body change is a request to review the implementation; it does not
establish widening, narrowing, business impact or runtime behavior.

## Scopes, moves and incomplete inputs

For an application directory moved by the PR, select its old location separately:

```bash
agents-shipgate diff --application --base BASE_SHA --head HEAD_SHA \
  --base-scope old/application --scope new/application --json
```

The two scopes are discovered independently. Exact Git blob renames supply file
correspondence, preserving original evidence locations; file identity is not a
claim about deployed agent identity. An absent directory is absence at that
Git path, not proof that no agent exists anywhere else. Missing manifests do not
supply an empty base.

An absent side names the missing scope and suggests `--base-scope`/`--scope`
for relocation. If neither selected directory exists, the command refuses with
exit 2. A removal describes the selected source path, not the entire repository.

`--json` emits `application_comparison_schema_version: "0.1"`, engine identity,
requested and compared refs/tree IDs, per-side scope/coverage, rows, source
correspondence, and a deterministic `comparison_id`. This is a separate advisory
artifact from the existing host diff JSON and verifier receipt.

- `compared`: the selected supported source observations were compared, and
  every agent construction in the scope was established. An unchanged
  submodule or excluded test code may still be named in `limits` (see below);
  neither can carry an application change, so it does not make the comparison
  `partial`.
- `partial`: a parse/discovery/binding gap remains. Gaps identify their source
  and agent where known. Only affected candidates become `change: not_established`
  rows, carrying `candidate_change` and per-side `uncertainty`; independent known
  additions/removals remain visible. Unknown implementation evidence cannot hide
  an observed binding addition. Unattributed discovery bounds still cover the scope.
- `not_established`: neither side established a supported application agent.
- Exit 2: refs/materialization/input could not be read. This is not no change.

An agent one side observes is absent from the other only when that side's
file no longer names it. If the file still assigns or imports the agent's name
(`from factory import agent`), or passes it as `name=`, through a construction the reader does not support (an `Agent`
subclass passing `tools` through `super().__init__`, a factory's result,
`Agent[Context](...)`, `.clone()`), that side records a gap for the agent and
its rows are `not_established`, never `removed` or `added`. An agent referenced
only in another agent's `handoffs` is not an observed construction.

### Agent constructions

An unobserved agent is not "no change". The OpenAI Agents SDK reader
establishes two forms of `Agent(...)`:

- `name = Agent(...)`, keyed by the name it is assigned to;
- `return Agent(name="Quote", ...)` in a function or method, keyed by its
  literal `name=`, with the same location, binding and implementation evidence
  an assigned agent has. A builder's `tools=` parameter is supplied by its
  caller, so it is a dynamic tools expression, never a module list that shares
  its name.

Either form's tools are not established when it unpacks arguments
(`Agent(name="x", **config)`, `Agent(*args)`) without spelling `tools=`: the
unpacked mapping may set them, so it is named, never read as an empty list.

Every other construction the reader recognises is a coverage gap over its file,
named with its line, so the comparison is `partial`:

```text
OpenAI Agents SDK agent construction at agents.py:12 is not read (not assigned
to a single name or returned); its agent and tool bindings are not established.
```

Those are an agent passed inline (`handoffs=[Agent(...)]`, a list or dict of
agents, a call argument), assigned to an attribute or to several names, a
`lambda` or `yield`, `return Agent(name=label, ...)` without a literal name, a
parameterized `Agent[Context](...)`, a `.clone()` of an agent the module
assigns, and a class deriving from `Agent`, whose instances are not followed.
The Google ADK reader reads every `Agent(...)`/`LlmAgent(...)` call; a class
deriving from one is named the same way. `compared` therefore means every
construction site in the scope was established; none is unaccounted for.

One identity constructed at two sites in a file — two builders returning
`Agent(name="Assistant", ...)`, one name assigned twice, or two ADK agents
sharing a `name=` — is one agent binding the union of their tools, as before,
and now also a gap on that agent naming both lines:

```text
Agent 'Assistant' is constructed at agent.py:12, agent.py:20; which
construction binds which tool is not established.
```

Each union binding is true of some construction, so an added or removed
binding is still reported, but the result is `partial`: a tool moved from one
variant to the other leaves the union unchanged and is never read as no
change.

### Test code

Test code does not establish the application. A file is test code when its
path relative to the selected scope is in a `test/` or `tests/` directory, or
is named `test_*.py`, `*_test.py`, `conftest.py`, `test.py` or `tests.py`; this
is the same rule discovery uses to rank agent names. Test code is never read
as a source, so a test double cannot make a scope `compared`, and nothing in it
(an unsupported framework, a parse failure, a link, a tool defined twice) is a
gap. Each side names what it left out in `limits`, without making the result
`partial`:

```text
Test code is not read as the application (1 file(s)): tests/test_turn.py
```

A scope selected inside a test directory (`--scope tests/fixtures/app`) is read
in full: selecting it is the request to read it. With test code excluded, a
scope whose only agents were test doubles is `not_established`.

### Tools defined twice

A tool name defined twice in one application file — two nested
`@function_tool def _tool(...)` in different builders — is a coverage gap on
that name in that file (`examples/agent.py defines the tool '_tool' more than
once; which definition an agent binds is not established.`). No agent binds
either definition, so a binding of that name in that file is `not_established`
rather than added or removed, and every other file and tool is compared. It
never refuses the comparison.

Framework identity follows the import, not the spelling. `Agent` and
`function_tool` are the OpenAI Agents SDK's only when imported from the absolute
`agents`/`openai_agents` package or not imported at all, resolved in the scope
that uses them, so an import in another function does not decide it; LiveKit's
`livekit.agents` exports the same names and is not read as the SDK, and a
relative `.agents` import is the project's own package.

Discovery is bounded by `--max-python-files` (default 1000) and a 2 MB per-Python
file limit. Partial discovery remains visible. The first increment uses existing
SDK/ADK readers; unresolved imports, dynamic factories and built-ins remain
explicit reader limitations. It does not support other application frameworks
yet. Indirect helper effects, runtime loading, deployed reachability and business
authority are outside this comparison. It grants no release or merge permission
and cannot stand in for a reviewed verifier base or qualification evidence.

## Evidence identity and recovery

`coverage_gaps` records each gap's source/agent/tool and whether it affects
binding presence or only the implementation. Locations are relative to that
side's selected scope. A partial result with no rows is not a no-change answer.
Reader gaps are scoped to their input file unless typed agent evidence narrows
them further; this does not infer cross-module bindings by matching names.

Implementation digests hash the resolved function's AST, including defaults
and decorators, excluding source positions and its leading docstring. Empty
AST fields are retained on Python 3.13+ to match 3.12. Digests are qualified by
the emitted engine/Python identity, not promised across arbitrary future AST
schema changes. `comparison_id` is SHA-256 over the **sanitized** published
object with that key removed, encoded as UTF-8 JSON with sorted keys,
`separators=(",", ":")` and `ensure_ascii=True`; readers can recompute it.

Each side materializes only its selected scope through the existing verified
Git materializer, which retains symlinks and containment checks. The default
root scope goes through the same scoped materializer, so it packs the tree
rather than the history.

### Links and submodules

A symlink anywhere in the tree is recreated as the link it is, never refused
and never read through. Every link under the scope is censused, including one
that resolves to nothing, and what it can hide decides what it is:

- A link Python discovery would not read changes nothing, as in a checkout:
  `CLAUDE.md -> AGENTS.md`, or a linked `.claude/skills/…` directory whose
  target the scope already reads at its own path.
- A `*.py` link whose target is a Python input the scope already reads is
  compared at that target's path. Any other `*.py` link — dangling, leaving
  the scope or the repository, or landing on something that is not a Python
  input — is a coverage gap over the link's own path
  (`Linked Python input: agent.py`), so replacing `agent.py` with a dangling
  link is `not_established`, never a removal.
- A link to a directory outside the scope that holds Python is a gap over the
  link's path (`Linked directory holds Python outside the scope: lib`).
- A link that resolves to nothing in the repository (dangling, absolute, or
  leaving the tree) is a gap only where the other side reads source at or
  beneath its path (`Linked input resolves outside the tree: tools`). An
  unchanged `agent/VERSION -> ../../VERSION` beside the application changes
  nothing.

A selected scope that is itself a link is refused (exit 2).

A submodule's content is in another repository. The comparison never fetches
it and materializes its gitlink as the empty directory a checkout without
`--recurse-submodules` leaves. The same gitlink commit on both sides is the
same content, so it is named in both sides' `limits` —
`Submodule content is not read (unchanged commit 85b71d7ecd4f): vendor/core` —
and nothing more. An added, removed or moved gitlink is a coverage gap over its
path on each side that has it, so the comparison is `partial`, and a binding
the other side holds at that path is `not_established` rather than added or
removed. A gitlink at the selected scope itself covers the whole scope
(`…: the selected scope`).

Partial clones
with unfetched objects exit 2 with `objects_missing`, name the affected side,
and provide the existing `git fetch --refetch --no-filter <remote>` recovery.
The comparison never runs that fetch. Other configuration/materialization errors
use `config_error`; malformed reader inputs use `input_parse_error` in agent mode.
