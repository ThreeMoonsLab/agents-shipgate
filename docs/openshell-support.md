# OpenShell static policy inventory

Agents Shipgate reads explicitly selected, repository-local OpenShell policy
documents with `shipgate audit --host --workspace . --json`. The inventory
describes document facts. It does not execute OpenShell, query a gateway,
resolve DNS, inspect a running sandbox, or assert tool effects or approvals.

Register policies in `.shipgate/openshell.json`:

```json
{
  "version": 1,
  "runtime_version": "0.1.2",
  "policies": [
    {"path": "configs/worker-policy.yaml", "role": "authored"}
  ]
}
```

This registration is an Agents Shipgate format. OpenShell does not discover
policies from this filename. Paths are normalized, relative to the audited
workspace root, even when a registration is nested. Arbitrary filenames are
accepted. Each registration selects 1–64 distinct paths, with at most 64 policy
references across the workspace, including repeated selections. Globs, URLs, absolute
paths and `..` are rejected. A policy is read only when a registration selects
it. The detection census recognizes the registration filename without reading
the policy. It never classifies every YAML file as an OpenShell policy.

`authored` identifies a proposed sandbox policy. `effective_snapshot` identifies
a locally supplied export in the same policy YAML/JSON shape. Both are static
documents reviewed independently. An export's presence establishes no live
freshness or enforcement. Provider/global composition is outside this reader's
scope. The runtime pin `0.1.2`, OpenShell policy schema `1`, registration schema
`1` and host inventory schema `0.8` are separate version axes.

The `openshell_policy` grant contains typed filesystem, Landlock, process and
network facts. Endpoint and binary lists remain together under their rule;
they are not flattened into independent grants. `field_paths` and
`defaulted_fields` are JSON pointers. For example, an omitted endpoint
`enforcement` is recorded as `audit`; an explicitly supplied filesystem section
defaults `include_workdir` to `false`. When the entire filesystem section is
omitted, the recorded omission means workdir inclusion defaults to `true`.
Driver-selected process identity and runtime-added filesystem baseline paths
are named unresolved runtime context. Access/risk remain `unknown`: an HTTP
method or MCP tool name does not declare a business action's authority.

Defaults follow the pinned [OpenShell v0.1.2 authored schema](https://github.com/NVIDIA/OpenShell/blob/v0.1.2/crates/openshell-policy-schema/src/lib.rs)
and [conversion code](https://github.com/NVIDIA/OpenShell/blob/v0.1.2/crates/openshell-policy/src/lib.rs).
The [upstream schema reference](https://docs.nvidia.com/openshell/how-it-works/policies/schema)
describes runtime constraints beyond document inventory. This reader is not a
substitute for upstream policy validation. Git dependency identity, gate
integration, local composition and native proof are separate implementation
stages (#945–#948).

## Conservative declared-authority comparison

The shared host comparator reads normalized policy facts. It describes
declared authority, without establishing runtime enforcement or whether a
named file, executable or destination exists. JSON and Markdown use the same
before/after rows, expansion signals and explanations. `diff` publishes no
merge verdict. Mixed changes retain their proven expansions and narrowings;
their single overall direction remains unknown.

| Surface | Supported direction proof | Unproven cases |
|---|---|---|
| Filesystem | Exact canonical absolute read/write path sets; read-write also grants read; duplicates are neutral | Omitted filesystem, changed workdir inclusion, ancestor overlap, noncanonical/wildcard paths, edits to runtime-baseline paths while network policy is present |
| Landlock | `hard_requirement` to explicit/omitted `best_effort` weakens the compatibility requirement; reverse strengthens it | Actual kernel support or applied rules |
| Process | Unchanged identity is neutral | Changed identity, image user/group resolution and driver defaults |
| Network L4 | Exact binary × hostname × port selection without address/transport/request options | Wildcards, DNS/IP reach, executable resolution, credential options and endpoint path routing |
| REST | Exact method/path matchers; method `*`; read-only/read-write/full presets; compatible overlapping allows with deny precedence | Request-path globs or omitted paths, query constraints, encoded-slash changes and protocol/credential options |
| Other protocols | Unchanged normalized facts are neutral | Changes to MCP, GraphQL, WebSocket, JSON-RPC or middleware semantics |

For REST, a finite partition includes every literal method/path on either side
and an additional class for all other values. It evaluates the effective union
of matching allows minus matching denials, keeping each binary/destination
relationship. An unchanged covering grant makes an added grant redundant.
Each possible combination of up to eight exact binary selectors is evaluated
per destination, because a process may match both its own path and ancestor
paths. A denial or inspected rule can therefore affect grants from another
matching binary selector.
Removing one of two covering denials does not expand the allowed set. A matching
inspected rule suppresses request access from uninspected rules. Conflicting
enforcement modes in overlapping inspected endpoints are unproven.

Removing `enforcement: enforce` restores upstream `audit`, which permits
well-formed requests that violate request rules. Malformed requests and runtime
transport checks are outside this comparison. A full-access endpoint without
denials already permits the compared request domain, so that transition alone
is neutral. Named rules, collection order, duplicates and explicit spelling of
an unchanged default are not authority. Adding/removing an entire selected
document remains unknown because it establishes no replacement runtime policy.

Comparison stops with a named unknown result beyond eight exact binary selectors
per destination or 100,000 network reference
or request-partition cells. Unsupported semantics never become inferred safe
narrowing. An HTTP method or MCP tool name still supplies no business effect,
approval, argument restriction or deployed agent binding.

## Read limits and coverage

The shared identity-bound host reader limits individual files to 1 MiB, along
with its aggregate byte/entry limits. OpenShell's own file limit is larger.
This adapter additionally limits YAML to 100,000 parser events and nesting depth
48, rejecting anchors, aliases, merge keys, duplicate/non-string mapping keys,
explicit YAML tags, multiple documents, explicit nulls and control characters.
Booleans use YAML 1.2 spelling; date-like MCP revisions remain strings. YAML and
JSON policy files use the same validation. Registration files must be JSON.

Unknown fields/types, unsupported versions, malformed documents, missing or
unreadable paths, and unsupported middleware produce named blocking coverage
issues. They cannot become an empty complete inventory. `network_middlewares`
is recognized but not interpreted; its free-form configuration is never
published. The reader supports bounded in-tree file symlinks through the
shared read session; escaping, unresolved and directory links are coverage
limits. It never reads outside the workspace.

Reports publish redacted evidence digests and sanitized errors, never parser
excerpts or credential values. If a credential-shaped authority label would
change under redaction, that document becomes unsupported instead of letting
different labels compare as equal. Exact file identity remains internal to the
read session. Historical v0.1–v0.7 host schemas remain frozen and readable;
current inventories/baselines/drift use v0.8.

The checked-in [example](../samples/openshell/sandbox.yaml) is selected by
`samples/openshell/.shipgate/openshell.json` when auditing this repository root.
For another workspace, copy the policy and register its new local path.
