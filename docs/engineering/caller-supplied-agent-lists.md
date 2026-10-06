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
