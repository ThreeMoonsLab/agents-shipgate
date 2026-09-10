# Declared operation attribution

An unchanged missing-approval finding and a narrower operation are different
facts. Agents Shipgate reports them separately in the existing
`tool_surface_facts.operation_attributions[]` and
`tool_surface_diff.operation_comparisons[]` blocks (#607). These optional
diagnostics do not change a finding, fingerprint, support classification,
baseline, exit code or release decision. Every exclusion flag remains false.

For example, an OpenAPI DELETE operation whose declared document IDs change
from `[alpha, beta]` to `[alpha]` has a **narrowed declared target domain**.
If the agent still has no declared approval policy, its approval predicate is
still a **standing weakness**. The report never calls that narrowing approval,
nor does it claim the server enforces the enum. An actual supplied approval
declaration can resolve the predicate; a caller parameter named `approved`
cannot. Declaration changes continue through the existing release review.

## What the first profile establishes

`openapi_delete/v1` reads a bounded declared HTTP request surface directly from
the captured OpenAPI bytes, before parameter normalization loses locations or
merges request-body properties. The manifest must select one OpenAPI source,
without auxiliary framework blocks or an SDK entrypoint: other files can
change selector/binding resolution or contribute effective approval. This
first profile refuses that wider dependency problem rather than silently
ignoring those files, including optional policy artifacts. It supports:

- OpenAPI **3.0.3**, literal DELETE and explicit `operationId`;
- one effective literal HTTPS server, including operation/path/root overrides;
- a fixed path template with up to four required scalar string path parameters,
  simple serialization, at most 32 literal enum values per parameter, and
  operation-level parameter overrides identified by `(in, name)`;
- explicit security alternatives, preserving their OR/AND structure, with
  literal HTTP basic/bearer or header API-key scheme declarations;
- at most 256 declared URLs, with a conservative 16 KiB expansion budget.

Duplicate keys, aliases/merges, ambiguous parameters, dynamic servers, unknown
configuration, request bodies, callbacks, query/header/cookie parameters,
references and unsupported schema composition cannot complete this profile.
The existing reader still exposes its ordinary supported inventory; this is
an attribution refusal, not a new parser error or a weaker gate. Other HTTP
methods receive no operation attribution in this version.

Each observed row joins the source observation to exactly one canonical tool
and the existing capability-policy subject, then to
`SHIP-POLICY-APPROVAL-MISSING`. It retains the source and manifest byte digests,
operation pointer, canonical/capability IDs, structural `openapi_method` claim,
the effective control pack's full identity, actual approval predicate, and
supplied static binding evidence. Approval provenance includes the manifest
policy list and any selected action-level approval declaration. This is an
explanatory projection of the same check inputs, not new `Finding.support`.

An unbound catalog operation has no in-scope action-policy subject to join.
Agents Shipgate does not generate a binding or an effect/authority declaration
to create one. Mixed artifact families, semantic conflicts and ambiguous
canonical membership remain unresolved. Overall dependency coverage stays
`incomplete`; **deployed reachability and runtime behavior remain unknown**.

## Which comparisons are trustworthy

Committed `verify` reconstructs the base from its Git tree with the current
reader. OpenAPI bases are rescanned even when a report cache and its adjacent
checksum agree: that checksum establishes byte consistency, not provenance.
The reconstruction is carried privately in memory; a public report cannot
set a flag to acquire it. Other source families keep their report cache after
the archived manifest establishes that they contain no OpenAPI source.

The head's captured operation and manifest bytes also enter the verification
dependency record, so an ignored source or policy edit can invalidate the current-control read.
The ordinary receipt and live-workspace checks remain required before using
any report. Raw hashes in an old report are not a substitute for those checks.

`scan --diff-from` and `verify --diff-from` accept report comparison inputs,
but **never grant reconstructed operation provenance**. Their operation
comparison remains unresolved. No/missing base, one-sided operation records,
changed operation/capability or dependency identity, changed security/control
configuration, changed binding evidence, or redacted evidence also prevent a
complete comparison. In particular, a reader can omit a referenced Path Item;
absence of its operation row does not prove an addition or removal.

For two supported, consistently identified operations, the report compares
literal target sets independently from the missing-approval predicate. A
predicate may remain missing, remain declared, become missing, or be resolved
by a supplied declaration. New deployed reachability is never inferred.

## Release boundary and validation

The regression inputs are isolated synthetic fixtures, not deployed wiring,
human qualification labels, runtime observations or release certification.
Paired scans and committed-repository tests exercise narrowing/widening,
declaration provenance, unsupported inputs, forged cache bytes, supplied
reports, redaction and current-control drift. Existing native findings and
their gate behavior are asserted unchanged by the attribution projection.

#557 still owns broader reader dependency closure. #515 owns any future
decision consumption, including its TypeScript MongoDB `cal-1` case; #563/#312
still require independently reviewed historical evidence. This OpenAPI
profile does not satisfy those acceptance bars or freeze report 1.0 (#569).
