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

Every OpenAI Agents SDK `Agent(...)` construction in a file the scope reads is
either an observed agent or a named limit. An agent assigned to a plain name
is identified by that name (`assistant = Agent(...)` is `assistant`), as
before, so renaming its `name=` or moving it into a builder changes nothing.
Only a variable name assigned in more than one function or class body — two
builders' local `agent` — gives way to each agent's literal `name`, and a handoff to such a
variable is named by the agent it holds. Any other construction is identified
by its literal `name`: `return Agent(name="Quote", ...)` inside a factory
function, `self.agent = Agent(name="Held", ...)`, an agent inline in a list,
or `Agent("Positional")`. `Agent[Context](...)` is the same construction.

What the reader cannot read is a named limit on the agent it concerns, so other
agents' rows in the file stand:

- one identity constructed at more than one site in a file with different
  tools or handoffs (two `return Agent(name="Quote", ...)` branches, or a
  literal `name` equal to another agent's variable) — the binding graph would
  merge them, so the tools are attributed to neither; constructions that bind
  exactly the same tools and handoffs are one agent. The same holds for a
  Google ADK agent name;
- a construction with `**` keyword unpacking or positional arguments after its
  name, which can carry `tools` or `handoffs`;
- an agent whose `tools`, `handoffs` or `mcp_servers` are changed after
  construction: assigned, extended or sliced (`agent.tools.append(...)`,
  `agent.tools = [...]`, `agent.tools[:] = ...`), `setattr`/`delattr` by name,
  or a handle taken on them (`t = agent.tools`, `payload["tools"] =
  agent.tools`, `self.tools = agent.tools`) that is changed through it later —
  a list method, `+=` or an item store on it or on an alias of it. For an agent
  the file builds (or, in another module, one imported from the scope), the
  list handed out of view counts too: to a call that is not read-only, into a
  container or attribute, returned, unpacked, or bound by `for`, `with` or
  walrus, directly or through a handle. On any other value — a request object,
  another library's model — a handle only read or handed on, like
  `payload["tools"] = request.tools`, is nothing. The changed value is read in
  the scope of the change (a change in place reaches every agent built from the
  same list object): an agent the reader constructed — directly, through
  `self.agent`, or through a module function that returns it — carries the
  limit; a value proven not to be an agent (`Settings()`, a literal,
  `type(...)()`, the instance's own `self.tools`) carries nothing; anything
  else is a limit on the file;
- a copy that passes its own `tools`, `handoffs` or `mcp_servers`
  (`agent.clone(tools=...)`, `dataclasses.replace(agent, tools=...)`,
  `copy.replace(...)`), wherever the original came from, unless the original
  is proven not to be an agent. A copy passing only `**` is a limit only on a
  value known to be an agent. A copy that passes none of them keeps the
  original's tools, which the original's rows already compare, and is not a
  limit;
- an agent built from a subclass of the SDK's `Agent` defined in the same
  module, whose tools arrive through its constructor, including one made with
  `type("X", (Agent,), {})`.

A construction whose `name` is not a literal is a limit on its file.

A module that is not read as an SDK source is still read for what can change
an agent without it: a capability-passing copy, and — once the module imports
anything from the scope, or is a Google ADK source, whose reader does not
follow a change after construction — a change to any object's tools, handoffs, MCP
servers or sub-agents, reached by an alias, a loop, a parameter or a call's
result as in an SDK file, unless the object is plainly not an agent (a literal,
or an instance of a class the scope defines that is not an `Agent` subclass).
A module that imports nothing from the scope has another library's `.tools`.
In any module, importing an SDK `Agent` subclass from the scope — `from core
import Assistant`, `core.Assistant` after `import core`, or through a package
that re-exports it with `from app.core import *` — is a limit; a subclass
nothing imports, a vendor class of the same name, or a name that only appears
in a string is not. Each is a limit on the module where it appears. The scope's
own package path counts as the scope (`from svc.app.x import …` under
`--scope svc/app`).

Not read at all: an `Agent` re-exported through a project module,
`functools.partial(Agent, ...)`, or a subclass defined outside the scope.

An agent one side observes is absent from the other only when that side's
file no longer names it. If the file still assigns or imports the agent's name
(`from factory import agent`, `agent = build()`), or passes it as `name=`,
through a construction the reader does not read (an `Agent` subclass, a
`.clone()`), that side records a gap for the agent and its rows are
`not_established`, never `removed` or `added`. A factory's own `return
Agent(name="assistant", ...)` is read under that literal name in the module
that builds it; it is a construction site of its own, not evidence about the
name the factory's result is assigned to. An agent referenced only in another
agent's `handoffs` is not an observed construction.

Test files never establish the application. A file under a `test` or `tests`
directory, a `test_*.py` or `*_test.py` module, `conftest.py`, `test.py` or
`tests.py` — matched case-sensitively and relative to the selected scope, the
convention discovery uses — is listed in each side's `excluded_tests`, printed
in the text output, and not read as an agent source: a test double's agent is
not the application's agent, so it cannot make a scope count as established,
and a test file's own defects (a tool defined twice, an unsupported framework,
a parse failure) are not the application's gaps. A product module that only
looks like a test is excluded too, which the printed list makes visible; the
import resolver still follows a tool the application imports from such a file.

A file that defines one tool name twice is a named limit on that file, and the
agents it constructs are not compared; every other file in the scope still is.

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
that submodule. A module-level `__getattr__` is not evaluated: a tool reached
past one is, for `scan`, not counted as proven. The comparison takes the
submodule only when the hook is undecorated, never rebinds its parameter, and
every `return` it can reach for that name (one under `if name == "other":`
cannot) gives `importlib.import_module(f".{name}", __name__)` — with
`importlib` bound only by importing it — or the package's own `from . import
<name>`: the lazy-loading idioms. Otherwise the tool stays named with its row
`not_established`.

`import a.b` followed by `a.b.f` reads the submodule `a/b.py`, which is what
the import system guarantees after `a/__init__.py` runs, even when the package
binds a `b` of its own; several `import a.x` statements bind one package and
are not a rebinding. A reference is read where it is used: a function that
builds the agent and imports the name itself (`from support import lookup`
inside the builder) is followed through that import, like a module-level one,
and `nonlocal` follows the outer function's binding. A function defined inside
the builder, when it is the only definition of its name, is that nested
definition. A module-level agent binds what the module binds at top level (its
`def`, its import, its wrapper assignment), never a same-named `def` or wrapper
nested in some function. A parameter, another local assignment, or a name the
builder binds more than once is a named stop, never the module's binding. A
factory's own `toolset = McpToolset(...)` or `tool = FunctionTool(...)` is read
like a module-level one. Code that runs before the name is used is checked
for a reassignment: every module on the chain (the agent's own file included),
the `__init__.py` of every package enclosing one of them, and every in-scope
module those import (`import patches` in the agent's file, `from . import
impl` in a package). `tools.lookup = tools.dangerous`, `setattr(tools, ...)`,
or a reassignment there of an attribute named like a step of the chain on an
imported module makes that reference a named stop, and so does such a module
that cannot be read (a link, or a missing relative module not imported under
`except ImportError`). The defining module's own `registry.lookup = lookup`
hands the definition on and does not count (a reassignment through its own
import still does); an import under `typing`'s `if TYPE_CHECKING:` never runs
and is skipped; every location an ambiguous import could mean is read. Code
there that runs but is not read — a relative import above the scope, an
absolute import of a module the repository holds outside the scope (`from
svc.patches import …` or `from common.patches import …` with scope
`svc/app`), a relative module no file provides — keeps the tool named, with its
row `not_established` and that import in the reason. Whether an absolute import
is the repository's own code is read from the compared commit's tree (for
`scan`, from the checkout, or from the three directories above the scope when
there is none): a module or regular package at the repository root, under
`src/`, or under any directory between the root and the scope (`backend/common`
for scope `backend/app`) — except a module the interpreter loads before any
application code (`os`, `sys`), a standard-library name at a root that is
itself a regular package (a plain directory on the path does shadow it:
`backend/calendar.py` is the `calendar` that scope `backend/app` imports), and
the scope's own package on the way to it, which counts only when it holds the name imported
(`from agents import Agent` beside `app/agents/support` is the SDK) — a linked
or submodule entry the import spells, or a
directory without `__init__.py` that holds the submodule named — `agents/support/`
holding SDK apps is not the `agents` that `from agents import Agent` imports. The
scope spelled from one of those roots through a regular package
(`svc.app.tools` with scope `svc/app`) is read inside the scope. The
`__init__.py` of every package between the repository root and the scope, and
the modules each imports — every package on the way to one (`import
svc.lib.util` runs `svc/lib/__init__.py`) and each submodule named (`from
.hooks import patches`) — run before the scope's modules and are read for the
same reassignments. There a reassignment counts unless it sets one attribute
of another module file outside the scope (`registry.tools = []` with
`registry` a submodule its package binds nothing else under); a longer path
(`registry.tools.lookup`), a name imported from a module, or an alias of a
module (`_t = tools`) still counts. A module of the scope one of them imports
(`from .app import bootstrap`) is read like the chain's own. A change to
`__path__` there is a caveat, except `pkgutil.extend_path`. One of those
modules that cannot be read — a link included — is a caveat unless its import
is guarded or generated, and past 1024 modules the rest are one caveat. What
they import in turn, or import by name at run time
(`importlib.import_module("svc.patches")`), is not followed, and neither is a
change to `sys.path` or `sys.meta_path` that makes a same-named module elsewhere
the one imported. A module file
wins over a directory without `__init__.py` of the same name, as the import
system prefers it. `sys.modules` and
`globals()` are read by allow-list: a subscript, `get`, a membership test, a
comparison, iteration, a spread (`[*globals()]`, `**globals()`), a read-only
builtin, `pkgutil.iter_modules(__path__)` or a namespace keyword
(`get_type_hints(fn, globalns=globals())`) reads them; a store (`[...] =`,
`setdefault`, `update`, `patch.dict`, `setitem`, an attribute of
`sys.modules[...]`) is a named stop when its key names a module on the chain, a package above one, or
the framework's own modules — `__name__` plus a literal is that module's own
name — and nothing when it names another module; any other use (`mods =
sys.modules`, `|=`, `operator.setitem`, a computed key) is a caveat. A
module rebinding its own name through `globals()` or its own module object is
a reassignment of that name, whichever way it spells the object
(`sys.modules[__name__]`, `sys.modules.get(__name__)`,
`importlib.import_module(__name__)`, an alias of one, `globals` under another
name) or the store (an attribute, `__dict__[...]`, `vars(...)[...]`); a
computed `setattr`, the module object anywhere but an attribute, a plain alias,
a comparison or a reader (a container, a return, an annotated or conditional
alias, a walrus, a function), a store through `__dict__.update`, a frame's
`f_globals`, `builtins.globals` under another name, a function of the module's
own named like a reader, and a change to `__path__` are caveats. A local named
`globals` or `vars` is a variable, unless it rebinds the builtin itself. A package
hook is not trusted when the package rebinds `__name__`, `__getattr__` or
`__path__`, patches `importlib`, or stores into `sys.modules` or `globals()`
other than the idiom's own cache. A generated
`*_pb2` or `_version` module, an optional import, and an absolute import the
repository does not hold (a third-party package) are the read's boundary, and a
rebinding in a module none of these import is not looked for.
When the module binds a name more than once or only inside an `if`, the Google
ADK reader still names the same-named `def` or wrapper it found, for `scan`,
but that binding is never established: its row is `not_established` on
whichever side it is present, and so is the row of every tool the module's
bindings of that name could give the agent instead, each followed to its
definition (a function imported from outside the scope by its imported name) —
every one of the agent's rows when one of those cannot be followed or a
wildcard import could bind the name. `x = FunctionTool(func=x)` right after
`def x` wraps that `def`; it is not a guess.

An OpenAI Agents SDK `tools=NAME` or `handoffs=NAME` is read through the scope
that binds `NAME` where the agent is constructed: a builder's own list, a class
body's own list, or the module's. It is read only when that scope binds it
once, to a literal list, and every use of that binding in the file only reads
it: iterated, indexed, compared, tested, formatted, spread (`[*TOOLS, x]`),
handed to a read-only builtin, a standard-library reader (`json.dumps`) or a
logger's method — each proven by its binding, so a `print` imported from the
application or an `.info()` on its own object is not one — to an agent's (or a copy's)
own `tools=`, or to a function whose every use of that parameter is such a
read. A list method, `+=`, a `global` or `nonlocal` rebinding, a subscript
store, a second name (also through `x or y`), a tuple, a return, `*args`, or anything
in the module that reaches its names without spelling them (`globals()`,
`vars()`, `sys.modules`, importing the module by `__name__`) makes it a dynamic
tools expression. The names in a module-level list are read at module level, whatever
the function that builds the agent imports.

A reference that does not reach one definition stays an unresolved tool, named
with its reason (`Not resolved because …` in the gap) and scoped to each agent
that lists it: a module the scope does not contain, a relative import above the
scope, more than one matching module location, a name bound twice or only
inside an `if`/`try`, a wildcard import, an import cycle, a class or other
value, a parameter or local assignment of the enclosing scope, a symbolic link, a module that
does not parse, or more than 64 modules read. Two agents binding same-named
functions from different modules keep two tools. One agent binding two
different functions under one name binds neither, whatever their order in the
list; its rows for that name are `not_established`. For `scan`, a definition an
import reaches and another configured source also reads is one catalog tool,
when both spell the module's path the same way; a Google ADK source configured
as a directory records no file for its tools and is not matched. A source an
inventory completes keeps its own observation, so the completion joins it.

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
