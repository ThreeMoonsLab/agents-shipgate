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

- `compared`: the selected supported source observations were compared. An
  unchanged submodule may still be named in `limits` (see below); it cannot
  carry a change, so it does not make the comparison `partial`.
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
subclass passing `tools` through `super().__init__`, a factory,
`Agent[Context](...)`, `.clone()`), that side records a gap for the agent and
its rows are `not_established`, never `removed` or `added`. An agent referenced
only in another agent's `handoffs` is not an observed construction.

Framework identity follows the import, not the spelling. `Agent` and
`function_tool` are the OpenAI Agents SDK's only when imported from the absolute
`agents`/`openai_agents` package or not imported at all, resolved in the scope
that uses them, so an import in another function does not decide it; LiveKit's
`livekit.agents` exports the same names and is not read as the SDK, and a
relative `.agents` import is the project's own package.

Discovery is bounded by `--max-python-files` (default 1000) and a 2 MB per-Python
file limit. Partial discovery remains visible. The readers follow tools imported
from other modules inside the selected scope (next section); dynamic factories,
built-ins and imports they cannot follow remain explicit reader limitations. It
does not support other application frameworks yet. Indirect helper effects,
runtime loading, deployed reachability and business authority are outside this
comparison. It grants no release or merge permission and cannot stand in for a
reviewed verifier base or qualification evidence.

## Tools imported from other modules

An agent often binds a function defined elsewhere in the repository:

```python
from . import memory_bank
from ..tools.shop import add_to_cart

root_agent = Agent(name="orchestrator", tools=[memory_bank.remember_firm_finding])
checkout_agent = Agent(name="checkout", tools=[add_to_cart])
```

Both readers follow such a reference to its definition: a name imported from a
sibling module or re-exported by a package's `__init__.py`, a module-qualified
`module.function`, a plain `alias = function`, and, for Google ADK,
`FunctionTool(imported_function)` / `LongRunningFunctionTool(...)`, including a
wrapper built in the imported module. The row then
shows the definition's own signature, location and implementation digest, and
adds `import_path`: each module read, the line of the binding followed, and that
module's SHA-256. `import_path` is evidence, not compared meaning — moving an
import is not a change. An OpenAI Agents SDK definition must carry the SDK's
`@function_tool`.

The boundary is narrow. Only regular `.py` files inside the selected scope are
read, parsed with `ast` and never imported or run. Symbolic links are not
followed, and a module name must match a file's exact spelling. An absolute
module name is looked up from the importing file's directory and each parent up
to the scope, and — when the scope is itself a package — from the scope's
parent for names starting with the scope's own package name. A name has to be
bound exactly once, directly in the module body, in every module on the way; a
package's own `from . import submodule`, even under `if TYPE_CHECKING:`, names
that submodule, and a module-level `__getattr__` is not evaluated — a tool
reached past one is named but, for `scan`, not counted as proven.

`import a.b` followed by `a.b.f` reads the submodule `a/b.py`, which is what
the import system guarantees after `a/__init__.py` runs, even when the package
binds a `b` of its own; several `import a.x` statements bind one package and
are not a rebinding. A name the function building the agent binds for itself —
a local `from support import lookup`, a parameter, a local assignment — is not
the module's binding of that name, so it is never resolved at module scope; a
function defined inside the builder is that nested definition.

A reference that does not reach one definition stays an unresolved tool, named
with its reason (`Not resolved because …` in the gap) and scoped to each agent
that lists it: a module the scope does not contain, a relative import above the
scope, more than one matching module location, a name bound twice or only
inside an `if`/`try`, a wildcard import, an import cycle, a class or other
value, a name bound by the enclosing function, a symbolic link, a module that
does not parse, or more than 64 modules read. Two agents binding same-named
functions from different modules keep two tools. One agent binding two
different functions under one name binds neither, whatever their order in the
list; its rows for that name are `not_established`. For `scan`, a definition an
import reaches and another configured source also reads is one catalog tool.

A row compares a definition's signature and implementation digest, not the
module it lives in: moving a function is not a change. So retargeting a binding
between two functions whose definitions are the same text in different modules
shows no row, even when the modules differ in what the function body refers to.

## Evidence identity and recovery

`coverage_gaps` records each gap's source/agent/tool and whether it affects
binding presence or only the implementation. Locations are relative to that
side's selected scope. A partial result with no rows is not a no-change answer.
Reader gaps are scoped to their input file unless typed agent evidence narrows
them further; this does not infer cross-module bindings by matching names — a
cross-module binding exists only where an import chain reaches its definition.

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
