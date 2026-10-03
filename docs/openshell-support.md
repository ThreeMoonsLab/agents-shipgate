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
references across the workspace, including repeated selections. Paths select exact
filenames; glob expansion is unavailable. URLs, absolute paths and `..` are rejected. A policy is read only when a registration selects
it. The detection census recognizes the registration filename without reading
the policy. It never classifies every YAML file as an OpenShell policy.

`authored` identifies a proposed sandbox policy. `effective_snapshot` identifies
a locally supplied export in the same policy YAML/JSON shape. Both are static
documents reviewed independently. An export's presence establishes no live
freshness or enforcement. Version 2 selections additionally describe explicit local composition. The runtime pin `0.1.2`, OpenShell policy schema `1`, registration schemas
`1`/`2` and host inventory schema `0.9` are separate version axes.

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
substitute for upstream policy validation. Native containment is a separate opt-in verifier stage.

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
read session. Historical v0.1–v0.8 host schemas remain frozen and readable;
current inventories/baselines/drift use v0.9.

The checked-in [example](../samples/openshell/sandbox.yaml) is selected by
`samples/openshell/.shipgate/openshell.json` when auditing this repository root.
For another workspace, copy the policy and register its new local path.

## Selected input identity

Each comparison reads registrations and selected policies independently from
its base and head tree. Arbitrary policy filenames, selection-only edits,
deletions and in-tree link chains use the same bounded archive closure as
other host dependencies. An unread selected policy makes the comparison
incomplete; it never means an empty policy.

Verification binds regular document bytes, link-target text, and named missing
inputs to the existing plan and receipt lifecycle. Ignored policies participate
in currency checks. Retargeting a link, replacing its type or changing a consumed
policy invalidates current control, even when Git reports no changed files.
The existing `input_script_blobs` field carries these host dependencies too;
`source: generated` identifies derived UTF-8 link-target text, while
`source: worktree` identifies regular bytes. Plan dependency provenance records
links separately. Old plans without a links collection remain readable.

Credential-shaped input paths cannot identify published bytes after redaction.
They become named unsupported, unconfirmable inputs, without publishing raw
paths or link-target digests. Static identity establishes which documents were
read, not whether an effective export is fresh or enforced by a running sandbox.

## Local control and verifier routing

`check` evaluates selected OpenShell inputs for Codex, Claude Code and Cursor
callers alike. It reads both compared trees, including selection-only edits,
deleted registrations, arbitrary filenames and link targets. The same policy
comparator supplies `audit --host --drift`, `diff`, `check` and verifier rows.

A proved expansion uses `SHIP-HOST-BOUNDARY-PERMISSION-ALLOW-EXPANDED`. Mixed
changes keep proven expansions. Unknown authority uses the existing protected
surface review rule. Unread/malformed/unsupported inputs use the existing
incomplete-input route, so absent grant rows never authorize completion.
Neutral or proved narrowings can clear only their exact selected document's
obligation; other trust-root, manifest, policy and instruction edits still apply.
Selection edits and changed link identities retain separate review obligations.

Trigger evaluation takes exact selected paths as explicit context, so even a
selected `README.md` overrides a docs-only skip. Preflight protects selected
inputs as exact host trust roots, including ignored files; the trust graph
binds their captured identity without treating filenames as globs.

With a configured application gate, verifier findings project to the normal
release decision, control permissions, Markdown and SARIF. Without one,
`verify` remains an advisory host comparison and routes to `audit --host`;
it requires no invented purpose, action effects, authority or agent bindings,
and establishes no application merge verdict. Preview never grants completion.

For an end-to-end exercise, register the sample policy, commit it as the base,
then remove `enforcement: enforce` from its REST endpoint. Run:

```bash
shipgate audit --host --workspace . --json
shipgate diff --workspace . --base main --json
shipgate check --agent codex --workspace . --base main --format agent-boundary-json
shipgate verify --workspace . --base main --json
shipgate agent control --workspace . --reports-dir agents-shipgate-reports
```

The change widens well-formed REST request authority by restoring audit mode.
Review the existing control route and its permissions. A subsequent selected
policy edit invalidates the receipt/current control, including ignored paths.
The route-parity fixtures exercise this transition for all three callers,
configured verification and SARIF, plus malformed input and Git-tree isolation.
Validation is pinned to OpenShell v0.1.2/schema 1 and the supported comparison
subset above. Gateway state, live enforcement, credentials, provider/global
composition and native containment remain outside this static MVP.

The pre-commit hook recognizes OpenShell selection files. Its static filename
filter cannot identify an arbitrary selected policy path on its own. Run
`shipgate check` or `agents-shipgate verify` for those changes, or set
`always_run: true` on the local hook. The GitHub verifier reads the explicit
selection and evaluates its dependencies.

## Reproducible local composition

Version 2 selections can describe a composed view without executing OpenShell:

```json
{
  "version": 2,
  "runtime_version": "0.1.2",
  "compositions": [{
    "name": "worker",
    "workspace": "team-a",
    "global_policy": {"state": "absent"},
    "saved_policy": {"state": "selected", "path": "configs/base.yaml"},
    "image_policy": {"state": "absent"},
    "catalog_mode": "imported",
    "profile_catalog": [
      {"path": "profiles/github.yaml", "scope": "platform"}
    ],
    "providers": [
      {"name": "work-github", "profile_id": "github", "endpoint_resolution": "profile"}
    ]
  }]
}
```

Supply the selected policy and profile files locally. A profile uses the pinned
upstream shape: `id`, `endpoints`, `binaries`, and optional credential metadata,
resource version and annotations. This bundle describes a proposed local
resolution; it does not attest which profile a gateway resolved. Familiar
profile IDs are mutable content, with their scope, workspace, normalized content
digest and contributing rule keys recorded beside the composed policy.

Selection follows global override, saved sandbox policy, then image policy.
An active global policy replaces the effective policy and suppresses provider
network layers. Removing it restores the saved/image policy and provider layers.
All declared inputs, including suppressed policies and unused catalog entries,
participate in Git comparison and receipt currency. At least a saved or image
policy must be supplied: runtime driver defaults are unresolved. Explicit absent
selection states prevent a missing context from becoming an inferred default.

Without a global override, provider network rules are concatenated with the base.
Reserved `_provider_*` keys are rejected in authored composition inputs. Provider
names use upstream ASCII sanitization, with numeric suffixes for collisions;
no layer overwrites another. Workspace profiles override platform profiles of
the same ID only in their named workspace. Duplicate scope/ID pairs and
interceptor/imported ID collisions are incomplete inputs. `catalog_mode` must
name imported, interceptor or combined local inputs. Remote discovery, base-URL
overrides, endpointless profiles and unresolved provider context are unsupported.
`endpoint_resolution: "profile"` explicitly selects the supplied endpoint set.

Network endpoint/binary reach and credential placement are separate facts.
Profiles may supply credential names, environment-variable names and placement
metadata, but never values, refresh tokens or runtime authorization claims.
Changes to credential placement produce an unproven authorization limitation
while retaining any independently established network expansion.

The local subset is pinned to [upstream composition at v0.1.2](https://github.com/NVIDIA/OpenShell/blob/v0.1.2/crates/openshell-policy/src/compose.rs),
[policy selection](https://docs.nvidia.com/openshell/how-it-works/policies/overview)
and [profile resolution](https://docs.nvidia.com/openshell/how-it-works/providers/profiles).
A registration supports up to 16 compositions, 32 catalog entries and 32
attachments per composition, with the shared 64-reference read budget and
1 MiB derived-input bound. Unsupported fields remain named coverage limits.

Effective snapshots can carry optional metadata in a version 2 policy reference:

```json
{"path": "exports/effective.yaml", "role": "effective_snapshot",
 "snapshot": {"source": "operator export", "revision": "sandbox-42"}}
```

Metadata is supplied evidence, with `runtime_freshness_verified: false`.
Filesystem and Landlock fields are startup-bound, process identity is fixed
at sandbox creation, and network policy/middleware fields may update
dynamically. A composed proposal and an effective snapshot retain distinct roles.
Neither establishes installed policy or live workload behavior.

## Optional native containment

A default `scan`, `check`, preview or flagless verifier never executes or installs
OpenShell or its prover. Native execution requires an operator-owned external
trust configuration, passed explicitly to configured `verify`:

```bash
agents-shipgate verify --workspace . --config shipgate.yaml --base origin/main \
  --openshell-proof-config /operator/openshell/trust.json --json
```

The operator supplies the pinned executable and maximum boundary independently
of the candidate PR. All three trust inputs must be absolute, outside the
candidate workspace, without symlink traversal or hard links, owned by the
running user or root, and not writable by group or others. Output directories
cannot overlap them. External location is a containment boundary, not proof of
human approval: the operator or trusted CI must independently approve and
protect these files. Never copy trust configuration or executables from the
candidate checkout to manufacture this provenance. A trusted CI job must load
its trust inputs and invocation from operator-controlled infrastructure rather
than candidate-authored workflow code.

Example trust configuration ([schema v1](openshell-native-trust-schema.v1.json)):

```json
{
  "version": 1,
  "required": true,
  "runtime_version": "0.1.2",
  "prover_version": "0.1.2",
  "executable": {"path": "/operator/openshell/openshell-prover", "sha256": "sha256:<approved-executable-digest>"},
  "boundary": {"path": "/operator/openshell/maximum.yaml", "sha256": "sha256:<approved-boundary-digest>"},
  "candidate": {"registration": ".shipgate/openshell.json", "path": "configs/worker-policy.yaml"},
  "required_domains": ["filesystem", "network_l4", "network_rest", "process", "landlock"],
  "timeout_seconds": 10
}
```

Replace digest placeholders with the independently approved SHA-256 identities.
Select either a registered document `path`, or a named local `composition` from
a version 2 registration. Composed candidates retain contributor provenance and
reconstruct the selected authored fields, preserving omitted defaults. The
result must normalize to the same policy as the static composer before it runs.
Every original dependency
remains bound by the static read session. A selected effective snapshot still
has unverified deployment freshness. No credential values are read.

The supported contract is [OpenShell v0.1.2 JSON schema 1](https://github.com/NVIDIA/OpenShell/blob/v0.1.2/crates/openshell-prover-cli/src/main.rs).
All five [modeled domains](https://github.com/NVIDIA/OpenShell/blob/v0.1.2/crates/openshell-prover/src/containment.rs)
are required. The wrapper checks schema/version, input names, actual process
exit status, domain coverage, outcome consistency and stable reason identifiers.
It supplies captured immutable bytes to a copied, hash-pinned executable in a
private directory, with no shell, repository executable discovery or inherited
credential/loader environment. The installation's approved runtime and shared
libraries remain part of the operator's execution trust; the executable hash
does not attest an entire operating system. The executable must remain runnable
after copying into the private directory without loader environment overrides.
Use a release-stamped prover reporting `0.1.2`; an unstamped source build inherits
the upstream development Cargo version `0.0.0` and is rejected by this contract.

Execution supports POSIX descriptor reads and process groups. Bounds include
1–30 seconds of solver budget plus two seconds of wall-time overhead, CPU time,
256 KiB per output stream, file size, descriptor count and input size. Linux
also applies a 2 GiB address-space limit. Darwin does not support that limit;
its time/output/file bounds still apply. Unsupported platforms cannot produce
passing execution evidence. The complete process group is terminated at the
end, including timeout, output overflow and cancellation.

`openshell-native.json` ([evidence schema v1](openshell-native-evidence-schema.v1.json))
records the observation, immutable candidate hash, trusted config/boundary/prover
identities, invocation options, actual exit status, available raw validated JSON,
composition provenance and the verifier's Git subject/request identity. The
same observation is bound in plan options and the existing terminal receipt.
Current-control reads revalidate every external origin as well as candidate
inputs; a change to ignored policy/profile bytes, trust configuration, boundary
or prover makes that receipt stale. A local receipt is not a portable signature
of approved execution. Repository-authored result JSON has no import route;
workers and assembly refuse native-proof plans and require a fresh trusted run.

`within_boundary` establishes only modeled containment of those inputs. It
clears no static host expansion, review, purpose, effect, authority or binding
obligation. `exceeds_boundary` produces a critical, suppression-immune verify
finding. Unsupported, inconclusive, error, invalid, cancelled and timeout
outcomes remain distinct observations. Required non-success also adds an
unsuppressible evidence gap through the existing release decision, so it cannot
complete. If optional proof is missing, its observation is `absent`, with
`provenance: "not_executed"`; it is never described as successful containment.
Use `--openshell-proof-required` to require proof even when no configuration
is available. Required status is scoped to that verification request, and
recovery commands preserve both proof flags. Native execution is refused on
preview and never supplies a manifest-free release verdict.
