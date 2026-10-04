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
call incomplete too. Clean callers keep their own provenance. Caller and sibling aliases and retaining bridge/package namespaces
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
and whole-call binding proof. Builtin readers accept only a single container argument, so custom
`isinstance` metaclass dispatch cannot acquire the list through this route.

Imported dictionaries and module-owned literal dictionary projections remain
unread. Returned members must be direct names in the factory's lexical module;
foreign returned-list members, dotted/wrapper members, nested tool closures,
foreign target-agent surfaces and runtime runner retention remain subsequent
increments. This entry does not establish Vesta acceptance or complete #874.

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
