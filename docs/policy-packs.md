# Policy Packs

> **Not to be confused with the built-in *control pack*.**
> `policies.control_pack` selects which controls each action *effect* requires
> — one repository-wide answer that parameterizes the built-in checks. A
> *policy pack* on this page is a local YAML file adding rules of its own. See
> [Control Packs](manifest-v0.1.md#control-packs).

Policy packs are local YAML files for organization-specific release rules. They
are declarative data, not Python plugins, and are enabled by default only when
declared in `shipgate.yaml` or passed on the CLI. The machine-readable schema is
[`policy-pack-schema.v0.4.json`](policy-pack-schema.v0.4.json) (v0.3 and older
stay frozen for older builds); policy-pack files do not need to declare a
schema-version key.

```yaml
checks:
  policy_packs:
    - id: org-release
      path: policies/org-release.yaml
      optional: false
      # v0.2 (optional): content pin for shared/org packs. Mismatch fails
      # the scan with a config error so a tampered pack cannot silently
      # change the release policy. Compute with `shasum -a 256 <pack>`.
      sha256: "<sha256-of-the-pack-file>"
```

```bash
agents-shipgate scan --config shipgate.yaml --policy-pack policies/org-release.yaml
```

External rule IDs must not start with `SHIP-`; that namespace is reserved for
built-in checks. Use an organization namespace such as `ORG-*`.

## Pack Format

```yaml
name: Org Release Policy
version: "1.0"
rules:
  - id: ORG-HIGH-RISK-OWNER-MISSING
    title: High-risk production tool has no org owner
    category: org_policy
    severity: high
    block: true
    confidence: high
    recommendation: Assign an owning team before production release.
    match:
      risk_tags: [financial_action]
      source_types: [openapi]
      environment_targets: [production_like, production]
      missing_owner: true
```

Supported rule fields:

- `id`: required unique non-`SHIP-*` rule ID.
- `title`: optional finding title; defaults to `description` or a generic rule-match title.
- `description`: optional fallback finding title when `title` is omitted.
- `category`: optional finding category; defaults to `policy_pack`.
- `severity`: required `info`, `low`, `medium`, `high`, or `critical`.
- `block`: optional boolean; when `true`, authoritatively matched findings set
  `findings[].blocks_release` and block strict CI even if severity is below
  the configured `fail_on` threshold.
- `confidence`: optional `low`, `medium`, or `high`; defaults to `medium`.
  This is a ceiling requested by the rule, never an upgrade over predicate
  evidence confidence.
- `recommendation`: required remediation text.
- `match`: required static predicate object.
- `owner`, `reviewers`, `approval`: optional reviewer/audit routing metadata.
  They are emitted as `findings[].policy_routing`, not `Finding.evidence`, and
  do not affect fingerprints, suppressions, baselines, `blocks_release`, or
  `release_decision`.

Supported legacy match fields:

- `risk_tags`: fires only when a listed tag is backed by policy-eligible
  semantic claims. Keyword/regex candidates remain visible as indeterminate
  applicability and do not create a finding.
- `source_types`: fires only for matching normalized tool source types.
- `environment_targets`: fires only for matching manifest environment targets.
- `missing_owner`, `missing_auth_scopes`, `missing_approval_policy`,
  `missing_confirmation_policy`, `missing_idempotency_policy`: boolean
  requirements over the normalized tool and manifest/API policies.
- `parameters`: list of parameter predicates. Each predicate must match at
  least one parameter.

Parameter predicates support `name`, `names`, `types`, `missing_maximum`, and
`required`.

Agents Shipgate evaluates both legacy match fields and built-in policy checks
through capability-policy subjects backed by deterministic `CapabilityFactV1`
objects.
Existing pack behavior is preserved: top-level fields are ANDed together, list
values are ORed within one field, and every parameter predicate must match at
least one parameter.

Compatibility note: `missing_approval_policy`,
`missing_confirmation_policy`, and `missing_idempotency_policy` evaluate the
same effective controls used by built-in checks. That includes manifest policy
lists, OpenAI API policy artifacts, Anthropic policy artifacts, and
capability/action control facts. A rule that previously matched an Anthropic
tool because only manifest/OpenAI policy inputs were considered may now see
`missing_*: false` when the Anthropic policy artifact explicitly covers the
tool; this aligns policy-pack evidence with the built-in policy checks.

Policy packs can also use capability-native selectors under `match.capability`:

```yaml
rules:
  - id: ORG-FINANCIAL-WRITE-MISSING-CONTROLS
    title: Financial write capability lacks release controls
    category: org_policy
    severity: critical
    block: true
    confidence: high
    recommendation: Add explicit approval, confirmation, and idempotency evidence.
    match:
      capability:
        providers: [api]
        effects: [financial_write]
        risk_tags: [financial_action]
        financial: true
        high_risk: true
        missing_approval_policy: true
        missing_confirmation_policy: true
        missing_idempotency_policy: true
```

Supported `match.capability` fields:

- `tool_names`, `providers`, `operations`, `source_types`, `effects`,
  `risk_tags`, and `scopes`: list selectors. A capability matches when the
  actual value intersects the list.
- `broad_scope`, `externally_visible`, `handles_sensitive_data`, `financial`,
  `code_execution`, and `high_risk`: boolean selectors over normalized
  capability authority/effect fields.
- `auth_types` and `credential_modes`: list selectors over normalized
  capability authority.
- `missing_owner`, `missing_auth_scopes`, `missing_approval_policy`,
  `missing_confirmation_policy`, and `missing_idempotency_policy`: boolean
  selectors over effective controls.
- `parameters`: the same parameter predicate list used by legacy rules.

When a built-in policy check or policy-pack rule matches a capability, the
report finding carries `capability_refs[]` and may carry
`capability_policy_evidence`. These fields are reviewer/audit metadata only.
They are not included in finding fingerprints, and `release_decision.decision`
remains the only gate.

## Trust Model

Policy packs are parsed as YAML through the same local file-size and
path-containment protections as other inputs. They cannot import code, connect
to services, call models, or call tools. Python plugins remain separate and
must still be explicitly enabled with `AGENTS_SHIPGATE_ENABLE_PLUGINS=1`.

Reports include `loaded_policy_packs` with pack name, version, path, and rule
count. Policy-pack findings support suppressions, severity overrides,
release-blocking `block: true`, baselines, Markdown, JSON, and SARIF like
built-in findings.

Each emitted policy finding carries `support`, including predicate status,
effective confidence, evidence bases, contributing claim IDs, and a stable
`support_hash`. A rule emits a finding only for an authoritative `matched`
result. `indeterminate` or `conflicting` applicability is emitted through
`policy_evidence_gaps[]` and release-decision `evidence_gaps[]`, outside the
Finding model, so suppressions, severity overrides, baselines,
acknowledgements, and `--no-heuristics` cannot hide it. Baseline matching also
requires the support hash; changing the evidence basis makes the finding new.

Routing metadata is non-enforcing. Shipgate validates declared team names
against `organization.teams` when present, but it does not call GitHub or any
external approval system to verify approvals. Use deterministic `match`
predicates for rule firing and `block: true` for release gating.

## Testing And Explanation

Policy packs are release rules, so they need tests just like code. The
recommended test harness is a pair of tiny static fixtures per rule:

- a positive fixture whose local tool source should match the rule.
- a negative fixture that is close but should not match.
- a release-decision assertion for `release_decision.decision`,
  `release_decision.blockers[]`, and `release_decision.review_items[]`.
- an explanation assertion against `release_decision.contribution_rules[]`.

`contribution_rules[]` is the deterministic explanation layer for policy-pack
findings. A matching `block: true` rule should appear as a blocker with rule
`policy_block_new`; a non-blocking medium-or-higher rule should appear as a
review item with rule `review_required`; a suppressed policy-pack finding should
appear as `excluded` with rule `suppressed`.

Use ordinary scan calls in tests:

```bash
agents-shipgate scan -c shipgate.yaml \
  --policy-pack policies/org-release.yaml \
  --format json
```

Do not use an LLM to decide whether a policy pack passed. LLMs may help draft
candidate rules or fixture names, but the accepted policy pack must be proven by
deterministic scans and contribution-rule assertions.


## v0.2: Conditional Composition

Flat match fields are implicitly ANDed (v0.1 behavior, unchanged). v0.2
adds explicit combinators — each branch is a complete nested `match`
evaluated against the same subject:

- `all_of: [<match>, ...]` — every branch must match.
- `any_of: [<match>, ...]` — at least one branch must match with
  policy-eligible evidence; the finding records the first authoritative branch
  that established applicability (`{"index": N, "matched": {...}}`).
- `none_of: [<match>, ...]` — no branch may match.

Absence of a risk tag is not authoritative negative evidence. Therefore a
`none_of` branch based only on `risk_tags` remains indeterminate unless the
underlying semantic assessment can prove the predicate from typed evidence;
it produces a non-waivable policy-evidence gap rather than a finding or pass.

Parameter predicates gain declared-bound comparisons:
`maximum_above: <number>` (declared `maximum` exceeds the threshold) and
`minimum_below: <number>`. These compare the *declared* schema bounds —
static evidence, not runtime values.

The canonical example — "a financial action whose `amount` is unbounded
or allowed above 1000 must declare an approval policy":

```yaml
rules:
  - id: ORG-LARGE-FINANCIAL-NEEDS-APPROVAL
    title: Large or unbounded financial action requires declared approval
    severity: critical
    block: true
    recommendation: Declare an approval policy or bound the amount below 1000.
    match:
      all_of:
        - risk_tags: [financial_action]
        - missing_approval_policy: true
        - any_of:
            - parameters:
                - name: amount
                  maximum_above: 1000
            - parameters:
                - name: amount
                  missing_maximum: true
```

Determinism: branches evaluate in declaration order; `any_of` records the
first policy-eligible matching branch. An empty branch (`{}`) has no evidence
and is indeterminate — always give branches at least one predicate.

## Distributing Org Packs

Treat shared packs like dependencies: vendor or sync them into the repo,
then pin the content with `sha256:` in `checks.policy_packs`. A scan
against a pack whose hash no longer matches fails closed with a config
error naming both hashes, so a policy change always shows up as an
explicit pin update in the PR diff — reviewable like a lockfile bump.
