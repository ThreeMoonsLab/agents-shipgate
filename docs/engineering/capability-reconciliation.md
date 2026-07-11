# Capability Reconciliation Prototype

Status: **experimental, local-only, non-gating**.

This design reconciles four different claims about an agent capability:

1. what reviewed policy intends;
2. what the static capability surface declares;
3. what a sandbox grants; and
4. what an eval or sandbox run attempted.

The prototype consumes local artifacts and emits a deterministic audit artifact.
It does not run an agent, connect to a sandbox, call a tool, update an allowlist,
or affect `report.json.release_decision.decision`.

## Why this is not an extension of trace learning

Existing trace ingestion normalizes selected tool-call scalars and
`CapabilityRuntimeEvidence` links them to `CapabilityFactV1`. That block is
deliberately audit-only and excludes raw prompts, arguments, and outputs. It
does not represent a complete sandbox grant set, resource selectors, explicit
sandbox enforcement outcomes, or principal bindings.

A learning run is especially unsuitable as policy authority. It can capture an
accidental or adversarial attempt, and it can miss rare legitimate paths. This
prototype therefore keeps reviewed intent and untrusted observations in
different artifacts and never promotes observations into an approved policy.

## Crosswalk

| Set | Current Shipgate substrate | Prototype source | Absence is meaningful when |
|---|---|---|---|
| Intended (`I`) | `action_surface.actions[]`, policies, prohibited actions, and prose lenses | Human-owned capability-intent sidecar | The target kind is `closed_world`, or an explicit deny selector matches |
| Declared (`D`) | Capability lock v0.3 and `CapabilityFactV1` | Existing exported capability lock | Static extraction is complete for tool/MCP identity |
| Granted (`G`) | No product-agent runtime grant inventory; host grants are a different boundary | Effective grants in the observation bundle | Coverage is `complete` for the same target kind and policy cohort |
| Observed (`O`) | Normalized trace evidence | Attempt/allow/deny rows in the observation bundle | Never; a missing row means only “not observed in supplied runs” |

`CapabilityIdentity.resource` is derived from static permission/scope evidence.
It must not be equated heuristically with a runtime hostname or file class.
Consequently, v0.1 can definitively reconcile resource intent, grants, and
observations while static declaration membership for resource-only targets
remains `unknown`.

## Trust model

- The capability-intent sidecar is a reviewed, human-owned trust input. It
  requires an owner and reason.
- Sandbox and eval artifacts always declare `evidence_trust: "untrusted"`.
- A source run hash and sandbox policy hash bind bytes/equality. They do not
  authenticate the producer or prove correct collection.
- A sandbox grant snapshot supports negative conclusions only for target kinds
  whose coverage is explicitly `complete`.
- Observation collection can be complete for one run, but behavioral coverage
  is always `sampled`.
- Imported evidence may reveal risk. It never establishes safety, closes static
  semantic evidence gaps, or narrows the declared capability envelope.
- Candidate changes are reviewer actions, never executable patches.

## Experimental contracts

### Reviewed intent

`capability-intent-schema.v0.1.json` contains:

- subject (`agent_id`, optional environment);
- human `owner` and `reason`;
- per-target-kind coverage (`closed_world` or `partial`); and
- exact allow/deny rules with optional expected principal binding.

Conflicting overlapping rules are invalid. `closed_world` means that a target
not covered by an allow rule is not intended. `partial` means that unmatched
intent is unknown, not denied.

### Untrusted observations

`capability-observation-schema.v0.1.json` contains one or more sources and their
effective grants/observations. Each source records:

- kind (`sandbox_policy`, `sandbox_run`, or `eval_run`);
- vendor and producer version;
- collection mode (`learning`, `enforced`, or `evaluation`);
- run ID and `sha256:<hex>` run hash;
- sandbox policy hash, or `null` when unavailable;
- grant inventory coverage by target kind; and
- observation collection coverage.

Observation outcome means the sandbox enforcement result:

- `attempted`: the enforcement result was not captured;
- `allowed`: the sandbox authorized the attempt; and
- `denied`: the sandbox rejected the attempt.

Tool success, downstream API failure, and sandbox denial are different events.
Adapters must not infer `allowed` or `denied` from a success/error field.

### Atomic target grammar

Each row has exactly one target. Multi-resource activity is split into atomic
rows connected by an optional correlation ID.

| Kind | Required identity | Safe resource detail |
|---|---|---|
| `tool` | tool name; optional capability ID/provider/operation | none |
| `mcp` | MCP server and tool name; optional capability ID/operation | none |
| `network` | normalized hostname-only domain | optional scheme/port, resource class, opaque exact-locator hash |
| `filesystem` | path class | resource class and optional opaque exact-locator hash |
| `resource` | vendor-neutral resource class | optional provider and opaque exact-locator hash |

Resource selectors support only `class` and `exact` granularity. Arbitrary
wildcard, glob, regex, URL path, and raw file path semantics are out of scope
for v0.1. Class selectors contain exact selectors of the same normalized class.

### Principal binding

A binding contains principal kind, credential mode, optional auth type,
authority source/mode, scopes, and an optional opaque principal-reference hash.
Change detection compares the authority binding and excludes the opaque
instance reference so two equivalent user/eval instances do not create noise.

Raw emails, usernames, role ARNs, credentials, tokens, prompts, arguments,
outputs, headers, query strings, fragments, and absolute file paths are not
valid fields in the observation contract.

## Matching and cohorts

Evidence is partitioned by `(agent, environment, policy_sha256)`. Sources with
no policy hash remain in separate unbound cohorts and cannot be unioned.

Tool/MCP matching is deterministic:

1. exact valid capability ID;
2. exact provider/operation/tool identity;
3. exact MCP server/tool identity;
4. unique tool-name fallback; and
5. otherwise ambiguous or unmapped evidence.

There is no fuzzy matching. Resource comparison uses only the typed exact/class
grammar above. Different policy hashes are never merged into one effective
grant or observation set.

Membership is three-valued: `present`, `absent`, or `unknown`. Incomplete
evidence suppresses only absence-based conclusions; positive observations and
grants remain visible.

## Deterministic result rules

| Signal | Condition | Required handling |
|---|---|---|
| `excess_authority` | `G` present and `I` authoritatively absent | Review removal or narrowing; never expand intent automatically |
| `policy_unclassified_grant` | `G` present and `I` unknown | Review intent coverage; do not call it excess |
| `behavioral_drift` | `O` present and `D` authoritatively absent | Investigate the route; do not add a declaration or grant automatically |
| `denied_attempt` | Any denied observation | Investigate; subtype is visible through four-set membership |
| `declared_ungranted` | `D` present and `G` absent under complete grant coverage | Treat as broken configuration only when `I` is also present |
| `granted_unobserved` | `G` present but no matching supplied observation | Informational only; never infer removal safety |
| `allowed_ungranted` | Allowed observation contradicts a complete grant snapshot | Review enforcement integrity |
| `intended_undeclared` | `I` present and `D` authoritatively absent | Review static declaration/configuration |
| `declared_unintended` | `D` present and `I` authoritatively absent | Review intent or remove the declaration |
| `principal_changed` | Current complete grant binding differs from an optional complete base snapshot | Authority delta with unknown direction; human review required |
| `principal_mismatch` | Current evidence differs from reviewed/static authority without historical proof | Human review; do not label it changed |

One target can emit multiple orthogonal signals. The primary signal uses this
order: evidence integrity, principal delta, enforcement contradiction, excess
authority, undeclared behavior, missing grants, policy mismatch/denials,
unobserved grants, then alignment.

Evidence gaps include missing policy hashes, ambiguous/unmapped targets,
incomplete intent/static/grant inventories, conflicting complete grant
snapshots, and resource comparisons the static capability standard cannot yet
prove.

## Candidate policy changes

Every candidate is hard-coded to:

```json
{
  "actor": "human",
  "approval_state": "unreviewed",
  "auto_apply": false,
  "safe_to_attempt": false,
  "requires_human_review": true
}
```

An observation can propose investigation, never allowlisting. A
`consider_minimal_grant` proposal is emitted only when reviewed intent and a
static declaration independently exist and a complete grant snapshot proves
the grant is absent. A reviewer must supply the updated policy separately and
rerun reconciliation.

## Prototype command

Export the static capability lock, then invoke the developer-only script:

```bash
agents-shipgate capability export \
  --config shipgate.yaml \
  --out agents-shipgate-reports/capabilities.lock.json

python scripts/prototype_capability_reconciliation.py \
  --workspace . \
  --lock agents-shipgate-reports/capabilities.lock.json \
  --intent reconciliation/intended-policy.json \
  --evidence reconciliation/sandbox-grants.json \
  --evidence reconciliation/eval-run.json \
  --base-evidence reconciliation/previous-sandbox.json \
  --out agents-shipgate-reports/capability-reconciliation.json \
  --json
```

The script containment-checks and size-limits local inputs. Reconciliation
signals exit `0`; nonzero exits are reserved for flags, malformed/missing
inputs, or internal/I/O failures.

## Adapter and promotion gate

An AgentBeach adapter is explicitly out of scope. It may be proposed only
after at least two independently sanitized exports map into these contracts
without schema changes, definitive absence claims have no reviewer-confirmed
false positives, and no learning-mode observation produces an automatic grant
proposal.

If the experiment graduates, expose the engine as the supporting
`agents-shipgate capability reconcile` command. If selected signals later
affect releases, convert them into ordinary findings that feed the existing
`release_decision.decision`. Never add a reconciliation-specific verdict.
