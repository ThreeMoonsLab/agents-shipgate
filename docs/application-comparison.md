# Application comparison without prior setup

**Availability:** new in 1.2.0, the newest published release; see the
[CHANGELOG entry](../CHANGELOG.md#application-comparison-without-prior-setup).
An older install has no `--application`: `pipx upgrade agents-shipgate`.

For an OpenAI Agents SDK or Google ADK application, compare committed PR refs:

```bash
agents-shipgate diff --application --workspace /path/to/repository \
  --base BASE_SHA --head HEAD_SHA --scope backend
```

No `init`, manifest, saved baseline or authored declaration is required. The
command writes no files in the subject repository and runs no application code.
Fetch the refs first; the command never fetches. Without `--scope` the scope
is derived from the change (below); `--scope .` selects the repository root.
`--head` defaults to committed `HEAD`, excluding dirty edits.
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

## A scope derived from the change

Without `--scope`, the comparison does not read the whole repository, nor only
the directory the change touched. Each changed Python file is related to the
agent files — files discovery scores as OpenAI Agents SDK or Google ADK sources
that construct an agent class (under any name they import it as), subclass
one or copy one with capabilities of its own, and a module that changes an
agent's capabilities after construction (`support.tools.append(x)`) and
imports one of those, or that builds its agent through one's factory
(`root_agent = make("bot", [tool])` with `make` returning an `Agent`); a
module that only defines tools is not one — that
import it, or that it imports, up to six import hops away, on each side of the
change. A module under a directory discovery skips (`build/`, `fixtures/`) is
related like any other, never an agent file. Importing `pkg.impl` runs `pkg/__init__.py`
first, so a package's imports count, and so does a literal
`importlib.import_module("app.tools")`. A file named like a test is related
when an agent imports it; it is never an agent file, and a changed test that
imports an agent does not widen a scope. A changed Python file not related to
a module that builds an agent — no import path, one longer than six hops, only
a module that rewires an agent, a file too large to read — is named in
`scope_selection.limits` with why, and makes the result `partial`, even when a
compared scope holds it: its consumer may be elsewhere. So is a changed link
to a module or a directory, or a changed submodule, whose content is not read,
and so is an agent outside the compared scopes whose imports go past the hop
bound. A module that imports the change — directly, through a package's
re-export, an agent file's, or other modules, up to six levels — and imports
within six hops an agent builder that builds on request — one that
constructs an agent whose tools, handoffs, MCP servers or sub-agents are not a
fixed list of the module's own names (`tools=tools`, `list(REGISTRY.items)`,
`self.tools`, `**config`) or gives it such capabilities afterwards (a
rewire, or a copy such as `BASE.clone(tools=tools)`), or subclasses an agent
class — is named too,
however it builds its agent from them, when it builds, copies
(`clone(tools=...)`, `clone(update={"tools": ...})`) or rewires an agent
itself, calls the repository's code with arguments of its own, or sets the
repository's module state (`factory.TOOLS = [...]`,
`setattr(factory, ...)`), unless
one compared scope holds it, the change and that builder. A module that only
imports another module's agent, or only calls an entry point (`main()`), is
not one. A module that imports the change and imports onward past six hops
without reaching a builder is named as not established, and so is the search
itself when modules outside the compared scopes still import onward after six
levels. A rename's two paths are one file.

```text
scope: backend/app (derived: backend/app: backend/app/services/gemini_tools.py is
related to agent file backend/app/agents/support.py through …)
```

Each changed file is compared in the outermost package holding it and every
agent that reaches it — with what those agents import from the repository and
the path entry a namespace package is imported through (`src` for
`myapp.tools` in `src/myapp/`) — so the reader can follow each chain. A
change to `backend/app/services/gemini_tools.py` that
`backend/app/agents/support.py` imports is compared in `backend/app`. When only
the repository root holds them all — a library module its own agents and an
example app elsewhere both import — the change is compared where its nearest
agents are, the others are named in `outside` and in `scope_selection.limits`,
and the answer is `partial`, never a silent `compared`.

Independent applications of a monorepo are separate comparisons, never the
repository root; `--json` then holds each one under `comparisons`, with every
path spelled from the repository root, and `comparison_status` is `compared`
only when each one is. A Python module moved inside one package is one
comparison of that package; an application directory moved whole — its old
path gone from the head, its new one absent from the base — is one relocation,
compared old path to new path as `--base-scope`/`--scope` would, with every
scope inside it; a renamed
non-Python file joins nothing. A change that touches no supported agent — a
README, or a script no agent imports — is `not_established` with the reason
naming the files considered; nothing is compared, and that is an answer, not a
failure. An absolute import is found where the importer's own path would find
it, or at one path entry holding a package of that name (`libs/shared`); a
standard-library name is the standard library unless a module on the importer's
path shadows it.

Everything is read from the two commits' objects, never run. The reading is
bounded per side: six import hops, 2000 files read to relate the change, and
5000 files or 64 MB read to find the agent files. A bound reached is named in
`scope_selection.limits` and makes the result `partial` — so does a
no-agent answer whose import following stopped at the hop bound — and it never
falls back to the root. `--json` records `scope_selection`: `mode` (`derived`
or `explicit`), `scopes`, the `reason`, the changed files considered and each
scope's relations (`base_scope` when it moved, `outside` agents). An explicit
`--scope` always wins, and `--base-scope` needs it.

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

`--json` emits `application_comparison_schema_version: "0.3"`, engine identity,
requested and compared refs/tree IDs, per-side scope/coverage, rows, source
correspondence, `scope_selection`, `comparisons` when a derived change spans
more than one application, and a deterministic `comparison_id`. Version 0.2 adds
`reach`, `effect_evidence` and `construction_sites` to a row's sides (see
[What a bound tool reaches](#what-a-bound-tool-reaches)); version 0.3 adds
`bound_when` (see [Tools lists built by an expression](#tools-lists-built-by-an-expression)).
This is a separate advisory
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
  merge them, so which construction binds which tool is not established;
  constructions that bind exactly the same tools and handoffs are one agent.
  A tool every such construction binds identically is still a row of that
  agent, beside the limit. The same holds for a Google ADK agent name: its
  constructions are one agent only when each binds the same tool definitions
  and handoffs and was read cleanly (no toolset, no `**`, no warning, no
  unresolved or non-literal `sub_agents`), as `root_agent` and a builder
  returning its twin do;
- a construction with `**` keyword unpacking or positional arguments after its
  name, which can carry `tools` or `handoffs`;
- an agent whose `tools`, `handoffs` or `mcp_servers` are changed after
  construction: assigned, extended or sliced (`agent.tools.append(...)`,
  `agent.tools = [...]`, `agent.tools[:] = ...`), `setattr`/`delattr` by name,
  or a handle taken on them (`t = agent.tools`) that is itself changed later —
  a handle only read, like `len(request.tools)`, is nothing. The changed value is read in
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

The Google ADK reader reads each `Agent(...)` / `LlmAgent(...)` call, not a
subclass's constructor. A class deriving from an ADK agent class (`class
Helper(LlmAgent)`, its base imported from `google.adk`, or a class deriving
from that one) is therefore a named limit where it is used: on the module that
defines it when that module names it again — a call, `functools.partial`, any
other reference, or a decorator that could build it — and that module's tool
surface is then not reported as enumerated to `scan` either; and on every other
module in the scope that imports it, as for an SDK subclass. A subclass nothing
uses, even one another unused subclass derives from, is not a limit. `scan`
reads one declared module, so a subclass used only in another module does not
affect it.

The census of copies, changes and subclasses runs only when some side's
discovery found an OpenAI Agents SDK or Google ADK source. Without one, no
agent is read on either side, and the result stays `not_established` rather
than turning `partial` over the repository's own `.tools` (a report builder's
`artifacts.tools.append(record)`). Every module is still parsed, and one that
cannot be is still a gap.

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

A file that defines one tool name twice is a named limit on that name in that
file: no agent binds either definition, so every binding of the name there is
`not_established`, while the file's other tools, and every other file in the
scope, are still compared.

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

A Google ADK tool built by a factory is the function the factory wraps. A
factory call — `tool = create_tool()` bound once and unconditionally in the
agent's function or at a module's top level, or `tools=[create_tool()]` — is
read, never run, to the factory's one unconditional `return`:
`FunctionTool(inner)` / `LongRunningFunctionTool(inner)`, a plain function, a
name bound once to one of those, or another factory's call, up to four
factories deep. The function is one nested in the factory and defined before
the `return`, or one the factory's module binds; `import_path` records the
factory as a step, and the factory's code and the values each of this
agent's calls gives its parameters — what the tool's closure holds — are
part of its implementation digest: a literal, or a name or module attribute
bound once to one (a list, dict or set — at any depth, or through another
name — only when nothing else in its scope uses it, never a module's), a
function by its own code (not the helpers it calls, as for any tool), or a
parameter of the factory the call is made in, with the factory's defaults for
the rest. So
`make_sql_tool(readonly=False)` in place of `readonly=True` is a changed
tool, while spelling the same call otherwise (an alias, a keyword, the
default written out), a docstring, or another agent's call is not. A value
this read cannot name — a builder's parameter, a computed value — is named
as a limit on that agent's tool, with the arguments and where they are
given; a row appears, `not_established`, only when the calling module or the
factory changed — a value set from another module leaves only the limit. A
module constant the factory's own body reads is not part of it, as for any
function. A factory that returns from more than one place or
under a condition, calls itself, is decorated, a generator or `async`, a
wrapped function that is decorated, a parameter, defined more than once in its
file (a tool is known by its name there), or changed or handed on in the
factory (`inner.__name__ = ...` renames the tool, for a module function,
`impl.search` or a function-local import too), a tool changed or handed on
where it is bound (reading `tool.name`, listing it in a tools list, or
wrapping it in `FunctionTool(func=...)` is not), and a factory the repository
holds outside the scope stay named with why; any other call — a third-party
package, a class, a name bound twice — is what it was. A tools list built in
the agent's function — `tools = [a, b]` with `tools.append(c)`,
`.extend([...])`, `.insert(i, c)` or `tools += [...]` — is read member by
member up to the statement that builds the agent, which copies it; an addition
under a condition or in a loop before it is named on the agent, never read as
bound. A list used any other way is read as
[a tools list built by an expression](#tools-lists-built-by-an-expression) is.

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
once, unconditionally, to a list [the expression reader](#tools-lists-built-by-an-expression)
reads, and every use of that binding in the module that binds it only reads
it: iterated, indexed, compared, tested, formatted, spread (`[*TOOLS, x]`),
handed to a read-only builtin, a standard-library reader (`json.dumps`) or a
logger's method — each proven by its binding, so a `print` imported from the
application or an `.info()` on its own object is not one — to an agent's (or a copy's)
own `tools=`, or to a function whose every use of that parameter is such a
read. A list method, `+=`, a `global` or `nonlocal` rebinding, a subscript
store, a second name (also through `x or y`), a tuple, a return, `*args`, or anything
in the module that reaches its names without spelling them (`globals()`,
`vars()`, `sys.modules`, importing the module by `__name__`) leaves it unread,
named with why. The names in a module-level list are read at module level, whatever
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

## Tools lists built by an expression

An agent's tools are often not written out in its construction:
`tools=[*FINANCE_TOOLS, *([prepare_handoff] if want_handoff else [])]`,
`tools=base_tools + (extra_tools or [])`, or a filter over another module's
list. An OpenAI Agents SDK or Google ADK agent's `tools=`, an SDK agent's
`handoffs=` and an ADK agent's `sub_agents=` are read member by member when
built from (#909):

- a list or tuple literal, each `*` spread spliced in;
- `a + b`;
- `a if c else b` and `a or b`: every branch that can be the value, each
  member *conditional* on the condition that selects it, or only the branch a
  constant condition, or an operand known to be empty or not, selects;
- a comprehension that keeps its elements (`[t for t in TOOLS if keep(t)]`)
  and `filter(f, TOOLS)`: the members of `TOOLS`, each conditional on the
  filter, which is not evaluated, so this over-approximates and says so;
- `list(...)`, `tuple(...)` and `sorted(...)` of one of these;
- a name bound once, unconditionally, to one of these — in the builder or at
  module level, and through a repository-local import to the module that builds
  the list, whose members are then read by that module's names.

A name is read only while nothing in the module that binds it can change the
list after it is built, by the rule for `tools=NAME` above; a change made to it
from another module is not looked for. Anything else is named where it is, on
the agent it belongs to, with why — a call (`get_tools()`), a parameter of the
builder (its value comes from a caller), `self.tools`, a comprehension that
builds new elements, a name bound twice or changed in place, nesting deeper
than 16 levels:

```text
OpenAI Agents SDK agent 'finance' at agent.py:4 has a tools list it reads only in part;
its binding graph is incomplete. Not read: a call to `plugin_tools`, whose result is not
read (agent.py:4).
```

The agent stays incomplete, so the answer is never `compared`, but the members
that were read are still compared: a tool both sides bind keeps its `changed`
row, and nothing a part not read holds is reported as removed. Nothing is
imported or run. A Google ADK list read this way is not counted by `scan` as a
proven surface, because only its own module is checked for changes to it.

A member held only under a condition carries it on its row's side as
`bound_when`, one entry per way it gets in, each the condition's source text:

```text
ADDED  finance → prepare_finance_handoff
  before: no observed binding
  after: prepare_finance_handoff(note) at app/Agent/financeAgent.py:100
    bound only when `want_handoff_tool`
```

A conditional member is never shown as unconditional, and a tool the list also
holds unconditionally has no `bound_when`. When only the condition changed
(`if handoffs` → `if want_handoff_tool`, or a conditional member made
unconditional), the row is `changed`, and its `why` names both conditions: they
are read, never evaluated, so whether the agent now holds the tool more or less
often is not established.

## What a bound tool reaches

A signature says what the model may pass, not what the call does. Each
`before`/`after` side of a function tool also carries `reach`: the outbound
HTTP calls the tool's own code makes. It is read statically from the function
and the repository helpers it calls, up to three helper calls deep.

```text
ADDED  tensorflow_pr_review_agent → submit_pr_code_review
  after: submit_pr_code_review(pr_number, overall_assessment, summary_comment, inline_comments) -> dict[str, Any] at pr_review_agent/agent/agent.py:591
    implementation: pr_review_agent/agent/agent.py:140 (f4919a519638)
    reaches: POST https://api.github.com/repos/{env OWNER}/{env REPO}/pulls/{pr_number}/reviews at pr_review_agent/agent/utils.py:172 via pr_review_agent/agent/agent.py:190 post_pull_request_review, and 1 more call site
      field event ∈ {APPROVE, COMMENT, REQUEST_CHANGES}, decided by overall_assessment
      model-supplied: inline_comments → field comments; overall_assessment → field event; pr_number → url; summary_comment → field body; inline_comments → field body
      credential: env GITHUB_TOKEN → header Authorization
    effect: write (structural evidence: outbound call at pr_review_agent/agent/utils.py:172)
    reach limit: pr_review_agent/agent/agent.py:165 calls ….get ('_PREFETCHED_PR_DETAILS' is bound more than once in agent.py (lines 51, 55)), which is not read
```

What each call records:

- **The method and the URL.** Calls through `requests`, `httpx`, an `aiohttp`
  session and `urllib.request.urlopen` are read, including a
  `requests.Session()` or `httpx.Client(base_url=...)` built in the function or
  at module level. The URL is a template. Each part is a literal, a tool
  parameter (`{pr_number}`), an environment variable (`{env OWNER}`), or `{…}`
  for a part the read cannot name. Module constants are followed through
  imports.
- **Request fields** (`json=`, `data=`). A field is one of:
  - a literal value, printed only when it is a number or a word without
    digits and the field's name does not suggest a secret (`pass`, `token`,
    `key`, …); any other literal is `literal: true`;
  - the literals it is chosen from, with the parameters that decide
    (`field event ∈ {…}, decided by overall_assessment`);
  - the parameters or environment variables it is made from.
- **`model_supplied`.** Which parameters the model supplies flow into the URL,
  method, query, fields or headers. A parameter the framework injects, such as
  a tool or run context, is not model input, and neither is a parameter the
  function overwrites before reading it (`pr_number = int(os.environ[...])`).
- **`credential_sources`.** For a credential-named header (`Authorization`,
  `X-Api-Key`, `Cookie`, …), `auth=`, a secret-named query key or field, or a
  value spliced into a URL's userinfo (names are matched by whole word, so
  `max_tokens` and `Idempotency-Key` are not credentials), these are the environment variables its
  value is made from, by name only. A hard-coded credential, or a literal
  default (`os.getenv("KEY", "sk-test")`), is reported as `literal: true`, and
  its value is never printed. A value fetched at run time is `computed`. In a
  printed URL, a query value is shown only when it is a short lowercase word
  or a number (or a date, or a list of words). A token-shaped path piece after
  the host is withheld: a run of 20 characters, or of 7 with letters and at
  least two digits, so `us-central1` stays readable. So is the whole path of a
  webhook or bot URL except its method name
  (`hooks.slack.com/services/[REDACTED:sensitive_field]/…`). These are shapes,
  not proof: a secret spelled like an ordinary word in an ordinary place can
  still print, as it already appears in the source.
- **GraphQL.** For a call to a GraphQL endpoint (the URL, or the variable it
  comes from, says `graphql`), this records whether the literal `query`
  document defines a query or a mutation. Transport is not effect: a query
  sent over POST reads. A `query` field elsewhere (SQL, LogQL) is a plain POST.

Each side also carries `effect_evidence`: the existing semantic assessment of
the tool (see [effect evidence](effect-evidence.md)), with the reach as one
more structural source, `source_http_call`. It gives the conservative effect,
the evidence status and the claims.

- **Write or destructive.** A POST, PUT or PATCH call, or a GraphQL mutation,
  supports `write`; a DELETE supports `destructive`. It does so whatever else
  the tool does.
- **Read.** A tool is said to read only when all of these hold:
  - it makes at least one outbound call, and every outbound call reads (GET,
    HEAD, OPTIONS or a GraphQL query);
  - every call the tool makes was followed, and none of them is a limit.
- **Limits.** The read passes over only calls with no effect outside the
  process:
  - a closed list of builtins, and functions such as `json`, `re`, `os.path`
    and `logging`;
  - read-only methods (`get`, `strip`, `json`, …) on plain data (an
    environment value, a model argument with a JSON type, or a list or dict
    holding only those) or on an object one of those libraries returned (an
    HTTP response, a regex match, a date). What such a method returns is plain
    data only when what it reads is: `CLIENTS.values()` hands back clients;
  - list and dict changes (`append`, `update`, …) on the function's own
    containers or on plain data.

  Every other call is a `reach.limits[]` entry with its location, and the tool
  is then not said to read. That includes:
  - a library function, `open`, and a method on an object the read cannot
    name (a repository class's `get` may POST);
  - a helper beyond the bound or outside the scope;
  - a decorator the read cannot see into;
  - a store into an object it cannot name (`cache[key] = value` on a redis
    client), or an attribute set on something the function did not build
    (`r.method = "DELETE"` on a request passed in).

  A repository function handed to another call (`sorted(items, key=helper)`)
  is read as called. So is one set as a request's or client's hook or auth
  (`hooks={"response": [audit]}`, httpx `event_hooks`, `session.auth = sign`),
  since it runs on every request. Any other function that a passed-over call
  runs is a limit: `map(es.delete, ids)`, a `key=` that is not a builtin,
  `iter(queue.pop, None)`. A request's changes after it is built are read: its
  `data`, its headers, and a method set on it, which leaves the method unread.
  Setting anything on a client other than its transport settings (headers,
  auth, timeouts, proxies, …) — `session.request = send` — is a limit.
  Options spread into a call (`requests.get(url, **opts)`) are read one by one.

  Five rules bound what `read` can rest on:
  - **A call through a client or request built elsewhere is a limit.** A
    module-level, imported or factory-made client (`SESSION.get(...)`), or a
    module-level `urllib` request (`urlopen(PURGE)`), can be configured
    anywhere. So only a direct library call (`requests.get`), or a client or
    request the same function constructs, can support `read`. Its hooks and
    auth are still read, so a write they make is still found.
  - **A value read out of a module-level dict is never taken as written.**
    Any code may change such a dict, through routes a static read cannot
    bound: a helper two calls away, `*args`, an accessor, a loop. So
    `CONFIG["method"]`, `QUERIES["viewer"]` or `HANDLERS["drop"]` is unknown,
    and cannot support `read`. So is a value read out of a copy of one
    (`{**DEFAULTS}`, `config.update(DEFAULTS)`). A dict the tool's own
    function builds from literals is read as written.
  - **A module-level constant or function rebound elsewhere is unknown.**
    Every attribute and `setattr` name the scope stores under is collected
    (`agent_config.METHOD = "DELETE"`, `helpers.fetch = purge`), whatever the
    store goes through: an import in a function, a dotted import, an alias or
    a parameter. So is every module whose namespace is changed under names
    the read cannot see. That covers a computed `setattr`, `vars(m)`,
    `m.__dict__`, `globals()`, `exec` or `eval`, and `sys.modules[...]`
    (replaced, or its `__class__` swapped). It also covers a namespace
    handed to a call that is not a known reader
    (`code.interact(local=vars(m))`). What the changed object may be is
    followed:
    - through names, closures, `global` and `nonlocal`;
    - through parameters and their defaults, however many helpers deep;
    - through what a function returns, and through displays, loops and
      `.values()`;
    - back to an `import`, `sys.modules[...]`, `importlib.import_module`,
      `__import__` or `globals()`.

    Any other object is taken as not a module: a call's result, an ORM row,
    an instance attribute. So `setattr(user, field, value)` withholds
    nothing. A module is also tracked when it is kept where it is not
    followed, in a container, an attribute, a class attribute, or a call the
    scope does not define (`Holder(config)`, `SimpleNamespace(m=config)`).
    If any namespace change in the scope goes to an object the scan does
    not follow, every module kept that way is unknown. Every value from
    module scope is unknown in these cases:
    - a namespace change the scan cannot tie to one module, such as a
      computed `sys.modules[name]` store, or a frame's `f_globals`;
    - a lambda, or a function passed on as a value (a callback,
      `functools.partial`, an import hook), that changes its argument's
      namespace. A library may hand it any module. A function used only as
      a decorator (`@tag`, or returned by a factory used only as
      `@tag(...)`) is handed definitions, never a module;
    - a file the scan cannot read, or a scope past its 2000-file bound.

    A module-level name another file imports is followed to what it holds:
    an alias (`import config as settings_module`, `CONFIG = config`, a tuple)
    is that module, and so is a name a star import may bind. A module held in
    a container there (`SETTINGS_MODULES = [config]`), or returned by a
    module's `__getattr__`, is also kept. Any other imported name that is no
    module file of the scope is an object the scan does not follow. `del sys.modules[name]` only makes the next import read the module
    again. A module registered lazily under its own name and spec
    (`module_from_spec(find_spec(name))`) is itself. A builtin rebound
    anywhere in the scope (`builtins.print = send_log`,
    `builtins.__dict__["print"] = …`) is not read as the builtin. A library
    function the read takes as pure and replaced anywhere in the scope
    (`json.dumps = audited`, `logging.Logger.info = …`, `setattr(re, "sub", …)`)
    is a limit where the tool calls it, however the module was reached
    (`sys.modules["json"]`, `import_module("json")`, an alias, a helper's
    parameter). So is a method replaced on a library class
    (`pathlib.Path.read_text = …`) where the tool calls it on such an object.

    A value the tool takes from module scope under such a name, or from such
    a module, is unknown. Stores through a method's own `self` change an
    instance and are not collected. Two unrelated uses of one name only ever
    withhold a value.
  - **State a closure shares is not taken as written.** A dict the tool reads
    from an enclosing function (an ADK factory's `state = {...}`) outlives
    one call, so what is read out of it is unknown. A name a function
    nested in that enclosing function rebinds (`nonlocal method`) or stores
    into (`state["method"] = "DELETE"`) is unknown too, whichever of its
    closures is the tool.
  - **A patch to an HTTP library anywhere in the scope is a limit** on every
    tool that sends: a store into `requests`, `httpx`, `urllib3`, `http`,
    `socket` or the like (`requests.get = logged_get`,
    `setattr(requests, …)`, `sys.modules["requests"].get = …`, or through an
    alias or a helper's parameter, a client class's method included), and a
    call that installs or instruments the
    stack (`install_opener`, `patch_all`, `ddtrace.patch(…)`,
    `instrument_requests()`), in any file outside tests, inside a function or
    not. A setting stored as a plain value (`DEFAULT_RETRIES = 3`, a class's
    `timeout` or `max_retries`) and a retrying transport or adapter are not
    patches. A class's other attributes are, even as a plain value
    (`urllib.request.Request.method = "DELETE"`). So is any attribute but a
    tuning one stored on a class reached through an object or its bases
    (`req.__class__.method = …`, `type(req)`, `Sub.__mro__[1]`, through an
    alias, a loop or a helper too), unless it is the method's own class
    (`type(self)`). A patch is followed however its library was reached:
    through another module's attribute or a class's (`agent.requests.get = …`,
    `clients.Http.lib.get = …`, an import in a class body, re-exported
    relatively or with `*`), kept
    on an object, `self` or one built around it and patched by name
    (`http.module.get = …`, `self.http.get = …`,
    `Holder(urllib.request).module.Request.method = …`, a method the stack
    sends through such as `Session.prepare_request`, one of its classes
    however reached), kept in a container, under another attribute or a
    property, set with `setattr` or `__dict__`, or passed through a call
    (`typing.cast(type, requests.Session).request = …`), or through
    `mock.patch.multiple`, `patch.object` under any alias and `wrapt`'s
    wrappers. A function of the stack handed along
    (`asyncio.to_thread(requests.get, url)`), a constant and an exception
    are not the stack kept. A chain of re-exports too long to follow counts
    as any module. A file the scan cannot
    read is named. A patch outside the compared scope is not seen, nor is code run
    from a string (`exec`, `eval`): 13 of the 99 corpus scopes run an interpreter
    or a calculator that way, so failing closed there would limit most of them. A recursive call with other arguments is followed twice as called,
  then once with every parameter unnamed. Past 24 outbound calls, the rest
  are not listed but still count for the effect.
- **Unread method or document.** When a request method or a GraphQL document is
  not a literal, it is a limit, and that call supports no effect.

`reach` and `effect_evidence` are evidence, not compared meaning. A helper's
changed endpoint does not make a binding `changed` on its own, and neither
field is a verdict or a risk score. The read never runs the code or fetches, so
a URL built from a response stays `{…}`.

When one Google ADK agent name is constructed more than once in a module, each
row's `binding_location` names the first construction, in line order, that
lists the tool, and `construction_sites` lists every construction that does.
When the constructions differ, which one runs is not established, so the agent
stays a named limit; identical constructions are one agent.

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
