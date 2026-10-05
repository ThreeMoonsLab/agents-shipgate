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
and repository-local re-exports that the import resolver establishes. The read
scope is the census only when it is the application's root: the repository
root, or a root declared with `--scope`. A scope that `diff --application`
derives from the change (#875) holds the changed files' agents, not every
caller of a builder they share, so below the repository root its census is a
named limit for each construction it would have followed. It binds
the complete Python call: positional-only and keyword-only arguments and
defaults are supported; missing, duplicate, unknown, excess and unpacked
arguments are limits. Variadic signatures, decorated builders and lambda
callers remain unread. Ordinary model, database and instruction argument values
remain original AST expressions and need not be statically evaluated.

Each invocation supplies all capability fields together. Forwarding builders
keep the parent invocation. Defaults are read where Python evaluates them: in
the scope around the `def` of the builder or forwarding function, never in its
body, which may bind the same name to something else; a name rebound after the
`def` stays unresolved. Supplied arguments are read in the caller. Caches
include invocation identity. Different callers
cannot borrow one another's tools or handoffs. Equivalent constructions retain
all caller locations and every caller's caveat on a binding, whatever order the
callers are read in; differing constructions follow the existing ambiguity
rules and never silently become one complete union.

List spreads, concatenation, choices and filters keep #909's member conditions.
Unknown caller branches add their own conditions: an `if`, loop or handler, a
conditional expression, a short-circuited `and`/`or` operand or a comprehension
around the call, out through the `def` of every function it is made in.
Statically unreachable callers — `if False:`, `while False:`, the `else` of
`while True:`, `x if False else y`, `False and x`, or a function defined only
in such a branch — are limits. An agent construction under a conditional
statement or expression, a short-circuited operand, a loop, a comprehension or
a handler is not followed through caller arguments in this increment. The
reader reports static construction and membership evidence, not execution or
runtime behavior.

Tools can resolve across module boundaries. Handoffs and ADK sub-agents passed
from a caller in the construction's own module retain their existing identity
rules and now retain caller provenance even when there are no tools. The
nearest scope that binds an element's name decides its identity: an agent
constructed there, or the name a function's own `from` import imports. A
parameter — forwarded element by element or holding a default — a loop
variable or any other local binding is not followed through the invocation;
its spelling never names a module-level agent, so it is a named limit (an
unresolved sub-agent for ADK), as an element-level tool parameter already is.
A foreign caller target remains unread: its name alone cannot establish its
actual agent construction and reachable tool surface. Following those complete
target surfaces belongs to the next increment.

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

Alias expansion uses import locations. Same-named schema modules and unrelated
named function imports do not widen the census. A namespace import can retain
an already-loaded child module, including through a parent package, a renamed
namespace or an ambiguous local import. Computed access and unknown consumers
of those retained namespaces stay unread; a direct, established builder call
through the namespace remains readable. Importing a namespace that retains a
returned agent handle also keeps that handle incomplete.

Computed access, ambiguous imports and dynamic import machinery are explicit
limits, and so is any access through a receiver the reader cannot name
(`sys.modules["builders"].build`). A store or `del` through a namespace that
may hold the builder — `builders.build = alt`, also inside a function or after
a local import — rebinds it for every caller and is a limit; a store to another
library's module or to a local object (`self.build`) is not. Known direct
callers remain partial evidence beside unresolved callers.
Foreign caller-local tool closures, list/dictionary-returning factories and
`self.tools` remain unread for subsequent #874 increments. ADK's followed-list
observations retain the existing distinction between comparable application
bindings and trusted scan coverage; this change creates no authority, effect or
agent-binding declarations.

## Read bounds and evidence

One index has at most 2,000 Python files, 100,000 directory entries, 32 MiB of
source text and 32 candidate modules. Alias expansion has four rounds,
forwarding four caller levels and construction expansion 128 contexts. Exceeding
any bound produces a named limit.

The census reads file bytes and directory listings through the static input
snapshot. Noncandidate Python files are evidence too: changing one can introduce
a caller. New files, removed files, selected import dependencies and virtualenv
exclusion-marker presence or absence are captured. Tests, hidden directories,
`__pycache__`, `node_modules`, `site-packages` and recognized virtualenvs are
outside this caller scope; ordinary `cache/` and `vendor/` are inventoried. Links are
not followed. No application import, agent execution, tool call, LLM call or
network request is used to establish these bindings.
