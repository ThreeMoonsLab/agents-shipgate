# Caller-supplied agent lists

The first #874 increment reads caller-supplied lists in synchronous,
module-level agent builders. It extends the shared `ListExpressions` reader
introduced by #909; it does not evaluate application code.

For example, changing `app.py` from `build([read])` to
`build([read, write])` now reports the added `write` binding when the builder
contains `return Agent(name="Built", tools=tools)`. The row retains the
construction, the caller and the tool definition locations.

## One invocation per construction

`BuilderCalls` finds direct callers in the read scope, including local imports
and repository-local re-exports that the import resolver establishes. It binds
the complete Python call: positional-only and keyword-only arguments and
defaults are supported; missing, duplicate, unknown, excess and unpacked
arguments are limits. Variadic signatures, decorated builders and lambda
callers remain unread. Ordinary model, database and instruction argument values
remain original AST expressions and need not be statically evaluated.

Each invocation supplies all capability fields together. Forwarding builders
keep the parent invocation. Defaults are read in the defining module, supplied
arguments in the caller. Caches include invocation identity. Different callers
cannot borrow one another's tools or handoffs. Equivalent constructions retain
all caller locations; differing constructions follow the existing ambiguity
rules and never silently become one complete union.

List spreads, concatenation, choices and filters keep #909's member conditions.
Unknown caller branches add their own conditions. Statically unreachable
callers are limits. An agent construction under a conditional, loop or handler
is not followed through caller arguments in this increment. The reader reports
static construction and membership evidence, not execution or runtime behavior.

Tools can resolve across module boundaries. Handoffs and ADK sub-agents passed
from a caller in the construction's own module retain their existing identity
rules and now retain caller provenance even when there are no tools. A foreign
caller target remains unread: its name alone cannot establish its actual agent
construction and reachable tool surface. Following those complete target
surfaces belongs to the next increment.

## Mutation and escape limits

A returned agent handle is checked even for literal or omitted fields. In-place
changes, aliases, unknown consumers, container retention, reflective access and
imports of a returned handle into another module keep it incomplete. Exported
handles are conservatively unread even when their importing module makes no
visible change. Class-body callers, coroutine and generator builders are not
followed.

A borrowed module list is checked against a bounded census of its in-scope
importers and re-exports. Mutation through a returned agent reaches every
holder, including a holder in the list's defining module. Shared mutable
defaults likewise retain ownership across callers that omit the argument.
Fresh list copies and direct replacement of a returned field do not mutate
another holder's original list. Unknown sharing never becomes a clean removal
in `diff --application`; unrelated established additions can still be reported.

A framework import alone does not prove a constructor stayed read-only.
Visible constructor replacement through aliases, retained namespaces,
reflective setters and namespace dictionaries keeps borrowed lists incomplete.
Saved namespace dictionaries retain their keyed writes and dictionary mutations;
fresh literal or builtin dictionaries retain their independence. An opaque
producer's imported callee never proves ownership of the returned namespace.
Uncertain exported objects, receivers and computed field names retain that
uncertainty, with a bounded 128-site provenance proof. Constructor implementation
writes likewise withhold the read-only proof. These checks do not infer generic
factory results or execute application code.

Dictionary initialization and class setters retain their write owners too.
Saved bare builtin aliases are resolved at their own lexical sites. Primitive
stability is scoped to its actual namespace: an `operator.vars` field cannot
replace `builtins.vars`. The standard-library `operator` receiver model is used
only when the repository import census finds no local provider; a local,
ambiguous or unread provider leaves its call effects unknown.
Mutation calls with an unpacked receiver remain unread, including literal
payloads that look foreign; this increment does not infer receiver positions
from argument unpacking. An explicit receiver retains its ownership when only
later arguments are unpacked.

Plain capability-field replacement on an established direct Agent instance,
including its plain assignment aliases, does not patch the constructor class
or another holder's original list. This role requires the existing lexical
constructor proof and captured absence of a repository-local import provider.
The same distinction preserves a bound source-local synchronous builder whose
single direct return is an Agent construction. Its exact lexical import,
provider absence and constructor mutation census must also be established.
That census includes source-local imports and their package initializers from
both the defining and calling modules, bounded to 128 modules. Unread imports,
dynamic import machinery, patches above the read scope and ambiguous exported
object writes remain limits; an import path alone never proves a foreign class
cannot retain the canonical constructor. Ordinary constructor reads require
captured absence of a repository-local provider of their canonical import root,
as well as this import census. A local `agents`, `openai_agents` or `google`
provider cannot acquire an external SDK or ADK list-consumption role merely
through its import spelling. The census includes every outer caller retained by a forwarded
invocation. Its dependency cache distinguishes that complete caller chain.
Read-only helper proofs retain their own actual invocation, including a bounded
parent chain when the helper call belongs to that parent's function. A census
of another importer starts its own context; cached helper proofs distinguish
these invocations, so a clean caller cannot hide another caller's import effects.
The bound construction checks its constructor independently of list-expression
resolution, so literal, empty and omitted fields do not skip the census.
Caller-supplied lists on SDK generic Agent syntax retain the existing read limit;
these checks do not establish a new constructor role for generic callees.
The existing SDK `clone()` list-consumption role retains the original holder's
list only when the receiver is one directly constructed SDK Agent and both its
constructor and canonical method pass the same caller and dependency census.
That instance role also captures the absence of a repository-local SDK provider;
an imported name alone cannot identify its constructor or method as the SDK's.
Instance method replacement, class method replacement and unread namespace
writes retain their limits. The cloned agent's own bindings remain unread.
The SDK's existing `dataclasses.replace()` and `copy.replace()` list-consumption
roles likewise require one positional receiver name bound to a single direct
SDK Agent construction. Its provider absence and complete constructor census,
then the copying function's own provider absence and dependency census, must
all be established. An arbitrary dataclass or replacement protocol can change
the list through its initialization hooks or `__replace__` method. Opaque,
returned, aliased and unpacked receivers retain a named read limit in this
increment; the replaced agent's own binding graph remains unread.
These borrowing roles require an import-resolver module in the read scope.
The SDK and ADK file readers enforce that requirement when recognizing a
consumer. Pure-AST users of the shared reader retain their explicit consumer
predicate; it makes no claim about a file's framework provider or imports.
A standalone directory load may read a linked file outside that scope, but
its import spelling cannot establish provider absence or the dependency census;
borrowed lists remain incomplete. Verification snapshots reject linked inputs
before extraction. No application or replacement protocol is executed.
Unknown writes to constructor fields include supplied
keywords, the recognized SDK/ADK fields initialized or inspected when omitted,
private attributes (including name-mangled construction hooks) and Pydantic
model hooks. Installing a descriptor or
lifecycle hook through an unresolved class owner cannot prove a constructor
leaves borrowed lists alone; unrelated namespace metadata retains its existing
literal-field distinction. This is static evidence, not execution of a
particular installed framework's constructor.
A saved `delattr` primitive remains an explicit constructor-read limit,
including when its receiver is a different source module. Deleting a function
binding is not a same-function write: it can release defaults, annotations,
function metadata or weak-reference callbacks. This reader has no deletion
ownership proof and does not treat a foreign receiver as one. Supported source
slot writes and mapping mutations do not imply deletion support.
Class returns, conditional or multiple returns, opaque producers and unknown
receiver writes to any capability field withhold constructor reads. Reflective
access and ambiguous aliases retain their limits. No generic factory result
or getter is inferred.

Alias expansion uses import locations. Same-named schema modules and unrelated
named function imports do not widen the census. A namespace import can retain
an already-loaded child module, including through a parent package, a renamed
namespace or an ambiguous local import. Computed access and unknown consumers
of those retained namespaces stay unread; a direct, established builder call
through the namespace remains readable. Importing a namespace that retains a
returned agent handle also keeps that handle incomplete.

Computed access, ambiguous imports and dynamic import machinery are explicit
limits. Known direct callers remain partial evidence beside unresolved callers.
Foreign caller-local tool closures and `self.tools` remain unread for
subsequent #874 increments. ADK's followed-list
observations retain the existing distinction between comparable application
bindings and trusted scan coverage; this change creates no authority, effect or
agent-binding declarations.

## Returned tool lists and dictionaries

The next increment follows synchronous repository-local factories with one
final return. It uses the same argument binding and caller census as agent
builders, and the same list-expression membership reader. The supported prelude
is limited to imports, a docstring and `pass`; executable statements, nested
definitions and unresolved returned expressions remain unread. Literal list/tuple
returns of direct names and literal-string-key projections of returned literal dictionaries
are supported, including `groups["calendar"] + groups["email"]` and
`groups.get("memory")`. A missing subscription key is a limit, not an empty
list. Dictionary spreads, nonliteral keys and duplicate keys are limits.

A `.get` default is evaluated eagerly even when the key exists. This entry
accepts only `None`, literal lists/tuples of established function references,
or an established function reference as that default; executable and unresolved
defaults remain unread. A selected caller default retains its caller scope.
Factory invocations contribute their own source locations and membership
conditions; inherited caller conditions are recorded once.

This bounded entry proves ownership of fresh returned containers. It checks the
factory callable census, all eagerly evaluated member-name bindings, canonical
returned-callable identities across their
bounded borrower modules, and every retained container use, including projected
values at every established invocation. An opaque result from a different call
may retain the same functions and their globals, so it makes the selected
call incomplete too. The factory's own namespace is checked even when the
selected projection is empty: an opaque helper or class carrying its globals
cannot establish that a removed binding stays absent. Clean callers keep their own provenance. Caller and sibling aliases and retaining bridge/package namespaces
remain subject to the same ownership checks. Functions and class methods in a retaining module also carry its globals, so
opaque uses of those namespace holders stay unread through bounded re-exports.
Class values and constructors remain unread by this entry. This namespace mode has its own census cache; the
ordinary shared-list census keeps its narrower import rules. Executable text and
builtin execution machinery in a relevant borrower's import dependencies stay
unread, using the resolver's existing module and import-step evidence. Patching or escaping the factory, renaming or
handing on a returned callable, mutation, aliases, opaque consumers, dictionary
views/copies/unions, starred calls and helper identity changes retain named
limits. Strict helper proofs follow projections and wrappers instead of ending
at indexing. Copies retain callable objects even when their list membership is
fresh, so unknown uses of copied members remain incomplete. Equality, ordering,
membership tests, opaque addition operands and comparator arguments are unread;
identity tests and additions with established literal container shapes remain
readable. A retained dictionary projection can supply an addition operand only
from the same binding already under that ownership walk; unrelated retained
dictionaries stay unread. Fresh factory shapes require the same callable census
and whole-call binding proof. A builtin-shaped list's zero-argument `.copy()`
follows every use of the new container; copied members retain their namespace
obligations. Unshadowed `print(*TOOLS)` supports one starred container and no
other arguments. Arbitrary starred consumers remain unread. Other builtin
readers accept only a single container argument, so custom
`isinstance` metaclass dispatch cannot acquire the list through this route.

Imported dictionaries and module-owned literal dictionary projections remain
unread. Returned members must be direct names in the factory's lexical module;
foreign returned-list members, dotted/wrapper members, nested tool closures,
foreign target-agent surfaces and runtime runner retention remain subsequent
increments. This entry does not establish Vesta acceptance or complete #874.

## Constructor identity and retained namespaces

Membership evidence is comparable only when the framework constructor is still
established. The SDK and ADK readers check its exact lexical framework import.
Repeated unconditional imports in that lexical body are accepted only when
every binding establishes the same supported canonical path and provider.
They follow repository-local re-exports and the import dependencies of its
callers to discover patches and retained namespaces; constructing through an
arbitrary re-export is not a supported positive. A local
module or regular package providing any prefix of the framework import cannot
prove the external class. An exact external SDK root import does not borrow
an application's namespace-only `agents/` or `openai_agents/` folder; local
providers and explicit child imports keep their ordinary checks.
Conditional imports, shadowing, class or namespace
patches, saved aliases, reflective access and unread import dependencies retain
named limits. The constructor's metadata, `__init__` and `__new__` belong to the
same proof. SDK `function_tool` identity is checked too.

The protected identity set also covers inherited construction machinery:
ADK's BaseAgent, BaseNode and BaseTool, SDK's AgentBase and Generic, and the
Pydantic/ABC classes and schema helpers used by supported construction routes.
Known external reexports and repository-local carriers keep the same mutation,
opaque-retention and module-table checks. Attribute writes beneath the current
framework or its dependency roots remain unread even for an unenumerated helper.
Local dependency providers are checked in every bounded caller and import
context, even without an application-side import; the framework itself imports
those dependencies. A sibling provider beside a caller cannot borrow a
repository-root builder's provider proof. Startup-preloaded standard
modules keep their existing provider boundary.
Unused imports retain their existing boundary. These dependencies never
become supported Agent or tool receiving classes. The dependency policy is
conservative across known framework namespaces, including empty constructors; refusing a
dependency change does not assert that this particular call executed it.
An enclosing package or its transitive import closure above the selected scope
that imports a protected canonical namespace retains a named constructor
ownership limit, including unused imports and ancestor namespaces bound by a
dotted import without an alias. Its repository-relative AST cannot borrow an
in-scope ownership proof. Empty initialization and ordinary unrelated imports
keep their existing boundary.
Pydantic argument-model instance construction during a later tool call is not
reported as eager decorator execution. See the pinned
[ADK inheritance source](https://github.com/google/adk-python/blob/v2.4.0/src/google/adk/agents/llm_agent.py)
and [SDK base classes](https://github.com/openai/openai-agents-python/blob/v0.17.2/src/agents/agent.py).

Protected namespaces include all known SDK and ADK framework roots;
the canonical Pydantic, typing, typing-extensions, abc and `_collections_abc`
namespaces, whose functions and classes carry shared ABC metaclass state; and ADK's Google
Gen AI namespace. These namespaces expose mutable class and schema-reader
aliases through public and private modules, so the boundary covers those
carriers without assuming a list of spelling aliases is exhaustive.
Annotation readers and SDK Field metadata use the
same boundary, including their real reexports and SDK Griffe documentation
parsers. An opaque consumer of such a
helper cannot establish the original name or schema. Unrecognized calls through
these internal modules remain unread, including internal `cast` calls and eager
framework-export annotations. Exact canonical public typing values such as
`typing.Any`, `typing.List` and `typing.Literal` retain their annotation-only
role; this permits no call, retained alias, metadata read or opaque export of
the type. Tested deferred type-value annotations and
public ADK `pydantic.Field(default=None)` / `pydantic.fields.Field(default=None)`
calls keep their existing behavior when the result is unused, discarded or only
compared by identity. A returned result must retain that ownership through every
known caller. Exporting the result, reading metadata, invoking its methods or
passing it to another consumer remains unread, as does retaining the Field
function or supplying a callback. Other Field-returning helper calls can retain
a named helper limit. Eager typing-extensions annotations retain the namespace limit;
deferred annotations retain their existing behavior. Supported exact
Agent, wrapper and SDK decorator paths retain only their existing owner routes.
A decorator from a different SDK root cannot borrow the selected constructor's
owner route, even when both imports use supported framework spellings.

Two ADK value roles preserve the existing static extraction routes. An exact
canonical `ToolContext` from `google.adk.tools` or
`google.adk.tools.tool_context` may be the complete parameter annotation of an
ADK tool; calls, aliases, metadata, containers and foreign SDK annotations do
not gain that role. Exact MCP and OpenAPI toolset calls accept only the finite
literal configuration and local artifact forms the reader understands. Their
results may enter a literal list or tuple supplied to the actual ADK Agent's
`tools` field, with the complete owner-use census still required. They gain no
method, getter, index, callback, export or receiving-class permission. Static
artifact hints identify files to read; they do not validate a runtime ADK
constructor signature. The exact `Path("literal").read_text()` spec form
requires the canonical Path provider and grants no other Path result API.
ADK provider checks also include `mcp`, `json` and `yaml` in every bounded
caller and import context: local providers can run before toolset construction
and alter the shared construction dependencies.

Only an actual direct `if TYPE_CHECKING:` or `if typing.TYPE_CHECKING:` guard
can establish a runtime-false body. The guard must resolve in its evaluation
scope to one unconditional canonical `typing` or `typing_extensions` import,
with its provider proved. Parameters, local/class/closure/nonlocal shadows,
mixed or conditional imports, wildcard imports and flag mutations retain the
executable import edges. Negated or compared flags grant no false-body role.
Import edges, recorded attribute/table stores, caller/reflection checks and
the primary constructor namespace walks use the same proved-dead node set; lexical
binding censuses keep every binding, because even a dead-branch assignment can
make a name local. The false boolean role grants no typing-object retention.
Above a scoped read, a canonical typing import can carry only these proved
boolean uses. An unused, shadowed or mixed import, or any retained typing
namespace, keeps the protected-import limit.

A separate finite primary-stdlib table protects direct ABC reexports and
source-proved ABC ancestry, including `collections.abc`, `contextlib.abc`,
`numbers.ABCMeta`, `io.abc`, `dataclasses.abc`, `os.abc`, and their ancestor
namespace handles. It also covers the listed classes in contextlib, collections,
io, selectors and the other named primary modules. Ordinary sibling calls such
as `os.getenv`, `os.path.join`, `io.StringIO`, `pathlib.Path` and builtin-backed
collections containers retain their existing boundary; the table grants no
receiving-class or getter-result authority.

An unread external imported handle used as a value or mutation target retains
a named ownership limit. Its private tag claims no canonical object identity,
and the obligation follows local reexports, namespace carriers and aliases.
Actual imported candidates remain obligations through conditional imports,
mixed bindings and a saved alias whose original name was later rebound. This
does not give the ambiguous name an identity or replace an ordinary lexical
data binding with a same-named outer import.
For example, handing `os.getenv` to an opaque consumer cannot borrow the
ordinary call's boundary: the function can carry shared ABC globals. Deferred
annotations keep their existing treatment. Ordinary external callees are not
executed or inspected, and their returned object graphs are outside this proof.
A plain synchronous source-local helper can preserve that ordinary call-result
boundary through an exact stable absolute-import callee, or a bounded sequence
of method calls directly on those call expressions. The full projected name
must remain outside the protected construction/dependency paths; local
providers, relative imports, mixed/rebound imports and stopped resolutions do
not gain the role. Saved results, properties, indexing, callable results,
parameters, generators, asynchronous helpers and reflective method calls do
not gain it either. Every
argument, namespace and other use remains checked. This permits the call only;
it neither proves the result graph nor registers a receiving owner route.
The enclosing-package check evaluates the same handle roles against its
repository snapshot in a separate bounded path namespace. Its reads never fall
back to the live checkout. A constructor, container or decorator ownership route
there remains a named limit: the in-scope list reader cannot finish that separate
ownership census. Plain local data and ordinary external calls retain their
existing boundary.
Every expanded import candidate is checked, including alternate providers,
links, unread blobs and package search-path changes. Discovered executable
modules inside the selected scope return to the original scope's import and
ownership census. A namespace portion alone cannot verify an otherwise unread
external imported handle; an exact literal in a known repository child module
keeps its ordinary data role.
This finite source/import boundary does not establish exhaustive stdlib heap
ownership, arbitrary third-party wiring or runtime safety.
Missing above-scope relative modules retain a named ownership limit even under
an ImportError guard. Readable literal version metadata can close that limit;
code that changes a shared constructor keeps it open. Reflective imports,
class-construction hooks and retained generators remain distinct limits.
Likewise, a reviewed inventory retires unresolved-symbol warnings, but cannot
prove the ownership of an unread imported object. That constructor warning and
binding evidence gap remain separate from the inventory repair. The ownership
gap names the actual constructor source and requests readable supported source
or removal of the opaque route. It offers no binding declaration template:
repeating a complete binding declaration cannot resolve that gap.

A foreign framework's functions, classes and modules can carry the same mutable
dependencies in their globals or ancestry. Opaque consumers remain unread even
when the active agent belongs to another framework, and mixed framework
constructions can retain that conservative limit. The narrow unrelated-write
exception covers only a single ordinary attribute assignment, with a stable
module-level import explicitly naming the framework and resolving the exact foreign class: SDK AgentBase's
`__new__` / `__setattr__` for an ADK read, or ADK BaseAgent's `__init__` /
`__new__` for an SDK read. Its imports must also prove the foreign dependency
providers. Ancestor imports such as `import google`, local reexported class
imports, deletion, augmented assignment, aliases, inherited method-object
mutation, metadata reads and opaque retention do not gain that exception; the
assigned value is checked independently.

Every constructed agent retains its class, including agents with empty tools.
Functions in a retaining module carry its globals. An unknown consumer of an
agent, a local callback, class method, returned container or projected member
can therefore invalidate another construction in that namespace. The reader
checks the complete bounded owner census before accepting any binding. A
constructor gap makes candidate tools incomplete and marks their binding
evidence uncertain; it cannot establish an addition, implementation change or
removal. Sub-agent and handoff targets whose constructors are uncertain are
unresolved too.

Readable members preserve their names, definitions and candidate directions
beside the constructor limit. An unresolved mutable named list is not enumerated
by this entry and can instead publish only the list and constructor gaps.
Original callable/import diagnostics remain beside constructor diagnostics.
An unresolved repository-local plain function can keep its binding gap scoped
to its receiving agent. An unread external object handed to an Agent or
FunctionTool retains a constructor ownership limit for other constructions in
the same namespace: its callable globals, ancestry and metadata getters have
not been read. The other agents' readable candidate changes and definitions
remain visible beside that limit.
Free-form output findings remain available for readable possible bindings,
with unknown binding evidence; they do not put those tools in the reachable
inventory or establish their action authority.

Frame-inspection namespaces, including an inspector handed to an opaque
consumer, remain unread. A lexically bound data name `stack` is not
`inspect.stack`, and a function-local or formal `vars` shadows the builtin;
actual inspector imports, method aliases and saved builtin references keep
their refusal. The exact immediate `score = FunctionTool(func=score)` following
an undecorated same-module definition reads the prior function, then follows
every use of the actual wrapper handle. Aliases, intervening statements and
opaque wrapper consumers do not get that exception.

Eager defaults, annotations and a comprehension's first iterable use their
outer evaluation scope. The binding census also records eager-header stores
and comprehension walruses in that scope, as uncertain bindings rather than
plain assignments. Comprehension iteration targets, lambda bodies and ordinary
local/class cells keep their lexical scope. A generic definition's type-parameter
cell can shadow an imported constructor, including in a method. Constructor
calls in generic annotations, lazy type-parameter bounds and constraints, or
deferred type-alias values remain unread. Postponed annotations and unevaluated
function-local variable annotations also cannot establish constructors;
defaults and decorators still use their outer scope. See
[Python's annotation scopes](https://docs.python.org/3.12/reference/executionmodel.html#annotation-scopes)
[assignment-expression scope](https://peps.python.org/pep-0572/#scope-of-the-target),
and [variable annotations](https://docs.python.org/3.12/reference/simple_stmts.html#annotated-assignment-statements).
Opaque decorators receive functions implicitly. The
unchanged SDK `function_tool` decorator, bare or with constant keyword values,
is supported. Its named `is_enabled` callback must resolve to an undecorated
function, and every use of the resulting tool handle must remain owned. The
six named ADK before/after agent, model and tool callbacks use the same actual
Agent receiving-handle proof. Other decorators remain unread. Class decorators, metaclasses,
base-class hooks and descriptor `__set_name__` calls can receive methods with
the same globals. Plain undecorated classes with no base or an unshadowed
builtin `object` base remain readable when their bodies contain only plain
method definitions, literal assignments, docstrings and `pass`, and their later
uses do not escape. Other class construction shapes retain a named limit.

Canonical SDK execution consumers remain a precision limit in this increment:
`await Runner.run(agent, ...)` and `Runner.run_sync(agent, ...)`, including an
`asyncio.run(main())` dispatcher, do not establish constructor ownership. The
reader has no separate proof of source-declared wiring before the execution
entry and does not assume that Runner leaves retained capability handles
unchanged. Readable tool candidates and handoff edges remain in the report;
an incomplete comparison must not publish their disappearance as a removal.
This is a known unsupported form, not evidence that the application is unsafe.

This deliberately narrows several former static positives. Anonymous lambda
and generator carriers, eager framework-class annotations, imported wrapper
instances, opaque handoff wrappers and unproved list mutations carrying callbacks
stay unread until their retained handles are followed explicitly. Readable
candidate names and membership conditions remain evidence beside that limit; their
presence does not make the binding established. Dynamic import or builtin
execution machinery in a relevant dependency also prevents constructor proof.
Replacing the module's `__builtins__` namespace prevents lexical builtin
proofs too, including imports, definitions or pattern captures bound under that
special name and a function's explicit global rebinding. Ordinary local and
class bindings keep their lexical scope.
No application code is imported or run to recover these cases.

Constructor identity and list membership are separate proofs. Discarded
`append`, `insert`, `extend`, `clear` and `reverse` calls on a proven builtin
list, or replacement of an owned capability field with another such list, can
preserve contained callable objects. The receiver's source, replacement
history and every downstream handle use must be read; method spelling alone
proves nothing. Ordinary membership evidence still names the changed list as
incomplete. Imported literal lists use the bounded shared-list borrower census
and strict member ownership, including namespace, function and class carriers,
reexports and their forward dependencies. Indexing, iteration, opaque consumers,
custom receivers, metadata and unfinished ownership still retain named limits.

## Read bounds and evidence

One index has at most 2,000 Python files, 100,000 directory entries, 32 MiB of
source text and 32 candidate modules. Alias expansion has four rounds,
forwarding four caller levels and construction expansion 128 contexts. Exceeding
any bound produces a named limit.

The constructor owner census has at most 1,024 owners, 128 active retained
edges and 4,096 ownership traversals. Completed proofs are memoized within that
census; adding an owner invalidates the completed-owner cache. A recursive or
unfinished proof never becomes complete through a cache hit.

The census reads file bytes and directory listings through the static input
snapshot. Noncandidate Python files are evidence too: changing one can introduce
a caller. New files, removed files, selected import dependencies and virtualenv
exclusion-marker presence or absence are captured. Tests, hidden directories,
`__pycache__`, `node_modules`, `site-packages` and recognized virtualenvs are
outside this caller scope; ordinary `cache/` and `vendor/` are inventoried. Links are
not followed. No application import, agent execution, tool call, LLM call or
network request is used to establish these bindings.
