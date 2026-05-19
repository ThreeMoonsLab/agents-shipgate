# Privacy And Redaction

Agents Shipgate is local-first. A scan reads local manifests and tool-surface
artifacts, then writes local reports under the configured output directory. The
CLI does not upload tool schemas, prompts, reports, credentials, or telemetry.

Public scan artifacts are redacted by default before they are written:

- `report.json`, `report.md`, and `report.sarif`
- Release Evidence Packet outputs (`packet.json`, `packet.md`, `packet.html`,
  and `packet.pdf` when PDF support is installed)
- GitHub step summaries
- `explain-finding` output loaded from an existing `report.json`
- JSON logs under `AGENTS_SHIPGATE_LOG_FORMAT=json`

Redaction is best-effort and deterministic. It uses known secret patterns
(OpenAI-style API keys, AWS access keys and STS-like IDs, GitHub classic and
fine-grained tokens, Stripe API and webhook secrets, Slack tokens, JWTs,
common database URLs, bearer tokens, and labeled secret values) plus obvious
sensitive leaf keys such as `password`, `token`, `secret`, and `authorization`.
It is not a replacement for a full secret scanner.

Every emitted v0.18+ report includes `privacy_audit`. The audit confirms that
the redaction layer ran, names the redaction rules and sensitive-field inventory
versions, lists output surfaces covered by the scan, and records aggregate
redaction counts by structural path. The audit never includes original values or
hashes/verifiers of original values.

Audit paths are structural summaries, not exact JSONPath selectors. Simple object
keys are preserved, while complex dotted or colon-separated map keys may be
collapsed to `<key>` and secret-bearing keys are shown as `<redacted>`.

The sensitive-field inventory is machine-readable at
[`report-sensitive-fields.json`](report-sensitive-fields.json). It classifies
top-level report fields and packet-derived sections as `secret_value`,
`credential_metadata`, `free_text`, `path_metadata`, `schema_metadata`,
`hash_only`, or `public_control_metadata`.
