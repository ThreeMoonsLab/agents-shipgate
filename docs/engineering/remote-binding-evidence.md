# Design note: agent → remote binding evidence

Status: **shipped** for the Google ADK `McpToolset` reader (#538). The
carriage, the identity rule and the capability projection are framework-neutral
and are meant to be reused when a second adapter learns to read a remote
binding; the reader itself is ADK-only today.

## The defect

An ADK agent mounts a remote MCP server:

```python
McpToolset(
    connection_params=StreamableHTTPConnectionParams(
        url="https://readonly.example/mcp",
        headers={"Authorization": os.environ["READ_KEY"]},
    ),
    tool_filter=["search"],
)
```

Change the endpoint to `https://admin.example/mcp` and the reference to
`ADMIN_KEY`, preserving every source line and the filter. Before #538 the two
workspaces produced **byte-identical reports**: the reader recorded whether a
local inventory path was present and discarded the connection, so
`tool_surface_facts` was empty, `capability_facts` was empty, and the related
finding retained only the toolset kind, the source line and one agent name.

That is lost evidence, not a demonstrated unsafe pass. `ADMIN_KEY` proves no
privilege level and nothing here claims it does. What *is* established is that
the endpoint and the credential reference changed, and a reviewer never saw it.

## The claim being made

A remote binding is a capability in its own right, independent of the leaves
behind it:

> This agent will call whatever `https://admin.example/mcp` advertises, under
> `ADMIN_KEY`, restricted to `search`.

That statement is complete, citable and comparable whether or not the tools the
endpoint advertises were ever enumerated. **Leaf coverage is a separate claim.**
Supplying a reviewed inventory answers the leaf question and must not be able to
answer, or conceal, the connection question.

## Three layers

### 1. Read (`inputs/google_adk.py`)

`_read_mcp_connection` parses the `connection_params=` argument with `ast`. It
never imports the module, constructs the object, resolves the endpoint or reads
the process environment; every fixture in `tests/test_adk_remote_bindings.py`
carries a module-level `raise RuntimeError("must never execute")`.

Each axis carries a `RemoteBindingStatus` beside its value so three different
answers stay apart: `absent` (the argument is not there), `unresolved` (it is
there and is not statically readable), and `not_read` (this reader does not look
at it — a statement about the reader, which keeps a coverage gap visible instead
of reading as a connection that carries nothing).

| Axis | Read as |
| --- | --- |
| `transport` | the connection-params constructor, matched on its final dotted segment |
| `endpoint` | a literal URL, or the *name* of the `os.environ` / `os.getenv` key it is read from |
| `credential_refs` | one `"<where>=<what>"` entry per credential-bearing key in `headers=` / `env=` — including `StdioConnectionParams`' nested `server_params` |
| `tool_filter` | the literal filter list |

The credential axis lists **every** entry, not only the environment
references: a value that was read and withheld renders as
`<literal credential withheld>` and one that could not be read as
`<not statically readable>`. Recording those only as limitations left the
carried summary and hash unchanged, so adding a hardcoded credential beside an
existing reference produced no delta at all. The markers carry presence and
completeness without carrying a byte of the value.

Per-binding limitation codes (`shadowed_connection_constructor`,
`rebound_connection_reference`, `dynamic_endpoint_expression`,
`literal_credential_value`, …) name what could not be established, so a reviewer
never has to infer a silence.

### 2. Carry (`core/remote_bindings.py`)

The base side of a comparison is only available through the serialized base
`report.json`, so each binding rides as four `ToolSurfacePolicyFact` rows inside
`tool_surface_facts.policies` — one per axis. `ToolSurfacePolicyFact.kind` is a
free string, so this is additive and needs **no `report_schema_version` bump**.
It is the same carriage `core/toolkit_scope.py` uses for dynamically-loaded
toolkit bounds.

Four rows rather than one is the point: the axes then diff independently through
the ordinary `_diff_policies` set comparison, which is what makes an
endpoint-only, credential-reference-only or filter-only change land as its own
named delta. `inventory_path` is deliberately not an axis, which is why "a
supplied inventory cannot conceal an independent connection change" holds by
construction rather than by convention.

**Identity** is `key = "<agent>:<source_id>:<slot>"`, every component escaped.
`slot` is the module-level variable the toolset was assigned to, else `#<n>` —
the order of inline constructions within that agent's tool list. No line
number, no formatting and no comment text enters the key or any hash, so moving
or reflowing the call produces no delta. The agent is part of the key, so two
agents mounting the same endpoint stay two bindings, and one toolset shared by
two agents produces one fact per binding agent. The configured source id is
part of it because two sources may each declare an agent of the same name, and
a two-component key silently dropped one of their bindings.

The **capability member's subject** repeats the *whole* identity for the same
reason the key carries it: the member id is hashed from that string, so a
subject missing any component collapses two bindings into one member and
discards one binding's evidence in `_dedup_members`. Two ways that happened —
dropping the `#<n>` ordinal merged two inline bindings of one agent, and
dropping the source id merged same-named agents declared by two configured
sources. The source is spelled `agent [source_id]`, the same form the
release-decision reason text uses, so it stays a `tool_sources[].id` an adopter
can open.

### 3. Project (`core/findings/verifier_blocks.py`)

Carried drift is projected into the existing `capability_change` block, which
never gates. `tool` stays empty — no leaf tool is claimed to exist, because
inventing one would be exactly the synthesized remote tool this must not
produce.

| Movement | Direction | Why |
| --- | --- | --- |
| the endpoint row appears / disappears | `added` / `removed` | the agent gained or lost a remote binding; the endpoint row is the presence anchor, so this is one member rather than one per axis |
| endpoint, credential reference or transport changed | `broadened`, confidence `medium` | the block's documented opaque-direction bucket; the rationale says in words that the direction is not established and that a host or variable name proves no privilege level |
| filter values gained / lost | `broadened` / `narrowed` | set membership, the one axis where the direction *is* established |
| a filter was added where there was none | `narrowed` | an unbounded binding is now bounded |
| a filter was removed | `broadened` | every tool the endpoint advertises is reachable again |
| both sides unbounded | *no member* | the source text moved and the authority did not |
| either side of a filter is `unresolved` | `broadened`, confidence `medium` | membership cannot be compared across it, so no direction is claimed |

**An empty `tool_filter` is not a filter of nothing — it is no filter.** ADK's
`BaseToolset._is_tool_selected` returns `True` for any falsy filter
(adk-python 2.8.0, `base_toolset.py`), and `McpToolset.get_tools` uses that
predicate, so `tool_filter=[]` exposes every advertised tool. The carriage
still renders it distinctly from an absent argument — they are different
source text and the edit is still named — but the projection treats both as
unbounded, because whether an empty list *bounds* anything is the consuming
framework's semantics and not a set-theory question.

## Secrets

`core/privacy.redact_url_credentials` strips URL userinfo and sensitive query
values at the reader, before the value reaches any artifact — the generic
patterns do not catch them, because `SECRET_PATTERNS`'s `database_url` rule
covers only database schemes and `LABELED_SECRET_PATTERN` only fires on values
long enough to look like a key. A literal value under a credential-bearing
header key is reported as present and withheld (`credential_status: redacted`),
never published.

Query keys are classified **after** percent-decoding, because `api%5Fkey` is
`api_key` to every server that reads it, and against
`privacy.is_credential_key` rather than the exact `SENSITIVE_VALUE_KEYS`
vocabulary, because `access_token` and `x-api-key` are ordinary spellings no
fixed list enumerates. That predicate is the exact vocabulary plus a suffix
rule (`…token`, `…secret`, `…password`, `…apikey`, `…signature`, …), and it
deliberately excludes a bare `key` so `sort_key` and `partition_key` stay
ordinary parameters — claiming a credential where there is none is its own
false statement. The same predicate classifies connection header and `env`
keys.

`value_hash` is computed over the *published* value, so a withheld secret is
never hashed either. The consequence is stated rather than hidden: **a change
confined to redacted bytes is not named.** The axis still flips status when
literal credential material appears or disappears, and the binding carries
`endpoint_credentials_redacted` / `literal_credential_value` while it is there.
Referencing a credential through an environment variable is what makes the
change comparable.

## Known limits

- **The Python constructor only.** An ADK YAML agent config records the binding
  with `not_read` statuses and a `connection_not_read_from_agent_config`
  limitation; it reads a literal `connection_params.url` and nothing else.
- **Nested parameters are resolved one level, and only for a recognized
  constructor.** `StdioConnectionParams(server_params=StdioServerParameters(…))`
  is read; `server_params=build_params()` reports `unresolved` with an
  `unresolved_nested_server_params` limitation rather than the false `absent`
  that reading only the outer call produced.
- **A renamed slot is a replacement, not a rename.** The diff is a set
  comparison on `(kind, key)`, so renaming the toolset variable reports the old
  binding removed and the new one added. Both members carry their own endpoint,
  so a rename can never hide a connection change — it only declines to call the
  two bindings the same one.
- **No privilege is inferred.** A host name, an environment variable name and a
  tool name are never promoted to risk evidence, no remote leaf is synthesized,
  and no reviewed authority, effect or binding declaration is written.
- **The release enum and the qualification thresholds do not move.** These facts
  are evidence, not a second gate; `release_decision.decision` remains the
  release gate and no new check id was added.

## Coordination with #515

`#515` will introduce a canonical subject for an agent → binding edge. The only
subject-bearing strings here are the policy `key` and the capability member's
`scope`; everything else — pairing, per-axis hashing, the four rows — is
subject-agnostic, so those two are the swap points.
