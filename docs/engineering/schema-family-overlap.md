# Schema family overlap

The [generated index](../schemas.md) lists the schema documents built by the
checkout. Compare their root objects and `$defs` together when proposing a
consolidation. A shared definition establishes a reusable part of the contract.
The root object determines which artifact a consumer can validate.

| Families | Shared payload or definitions | Recommendation |
| --- | --- | --- |
| Capability payload and capability delta attestation | The state and delta views share one versioned capability payload. The in-toto statement embeds its corresponding delta view under `predicate.delta`. | Reuse the capability payload for both views. Keep the attestation envelope for consumers that validate its subject and predicate. |
| Capability lock, capability lock diff and readiness report | The same authority, binding and effect evidence models appear in several places. The lock describes a snapshot, the diff describes transitions, and the report carries a release decision. | Keep each root. Reuse evidence definitions as they evolve. |
| Readiness report, evidence packet and verifier | They share release-decision evidence, including coverage and declaration review definitions. The packet selects reviewer evidence and the verifier records execution and comparison context. | Keep the three roots and reuse the release-decision model. |
| Agent boundary result, agent result, handoff, preflight, agent control, current control, verifier and verify run | They share permission or action definitions. Preflight projects a plan, the boundary result explains a local check, and the compact control envelope routes another action. Current control is a pointer to the bound artifacts. | Keep the published roots. A planning response or pointer needs a different validation boundary from a verdict or routing envelope. |
| Verification plan, unit result, artifact manifest, receipt and verify run | The records describe successive stages of one verification. The plan carries input and subject identity, worker results describe evaluated units, and the receipt binds the assembled outputs. | Keep separate stage contracts so consumers can validate exactly the artifact they received. |
| Human authorization, human review request and human review decision | They reuse review-item identities across unsigned questions, signed responses and evaluations. The authorization protocol binds a requested operation. | Keep the review and operation-authorization contracts distinct. |
| Host grants inventory, baseline and drift | All three models use host grant artifact and unknown-setting definitions. The baseline adds reviewed snapshot context and drift carries a comparison. | Keep the three roots and reuse the grant definitions. |
| Attestation, organization status, organization bundle and registry | The organization and registry views project local evidence and may refer to or collect attestations. The per-run attestation binds the verifier and report. | Keep the per-run evidence and aggregate views separate. |
| Manifest and policy pack | Both can be input to a scan. The manifest defines the project and sources, while the pack declares policy checks and routing metadata. | Keep separate configuration schemas. |
| OpenShell native trust and native evidence | The trust file configures accepted issuer and prover settings. Evidence binds a proof to a verification subject, which shares Git subject definitions with the plan and verify run. | Keep configuration and evidence separate. |
| Governance benchmark catalog and result | Results refer to the cases defined by the catalog and add run observations and metrics. | Keep input and output validation separate. |

Use a successor when consolidation would change a published schema. See
[Schema Changes](../../CONTRIBUTING.md#schema-changes) for the release procedure.
