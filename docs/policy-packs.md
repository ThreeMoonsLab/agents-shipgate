# Policy Packs

Policy packs are local YAML files for organization-specific release rules. They
are declarative data, not Python plugins, and are enabled by default only when
declared in `shipgate.yaml` or passed on the CLI.

```yaml
checks:
  policy_packs:
    - id: org-release
      path: policies/org-release.yaml
      optional: false
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
- `block`: optional boolean; when `true`, matching findings set
  `findings[].blocks_release` and block strict CI even if severity is below
  the configured `fail_on` threshold.
- `confidence`: optional `low`, `medium`, or `high`; defaults to `medium`.
- `recommendation`: required remediation text.
- `match`: required static predicate object.

Supported match fields:

- `risk_tags`: fires when the tool has any listed medium-or-higher risk tag.
- `source_types`: fires only for matching normalized tool source types.
- `environment_targets`: fires only for matching manifest environment targets.
- `missing_owner`, `missing_auth_scopes`, `missing_approval_policy`,
  `missing_confirmation_policy`, `missing_idempotency_policy`: boolean
  requirements over the normalized tool and manifest/API policies.
- `parameters`: list of parameter predicates. Each predicate must match at
  least one parameter.

Parameter predicates support `name`, `names`, `types`, `missing_maximum`, and
`required`.

## Trust Model

Policy packs are parsed as YAML through the same local file-size and
path-containment protections as other inputs. They cannot import code, connect
to services, call models, or call tools. Python plugins remain separate and
must still be explicitly enabled with `AGENTS_SHIPGATE_ENABLE_PLUGINS=1`.

Reports include `loaded_policy_packs` with pack name, version, path, and rule
count. Policy-pack findings support suppressions, severity overrides,
release-blocking `block: true`, baselines, Markdown, JSON, and SARIF like
built-in findings.

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
