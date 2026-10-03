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
substitute for upstream policy validation. Semantic expansion/subset review,
Git dependency identity, gate integration, local composition and native proof
are separate implementation stages (#944–#948).

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
