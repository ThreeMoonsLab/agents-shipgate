# Release Runbook

Operational procedure for cutting a tagged release. Covers what each control
proves, the mandatory rehearsal, and the recovery path when publication
partially succeeds.

For the packaging surface and post-release fan-out checks, see
[`distribution.md`](distribution.md).

## The pipeline

A release runs as two jobs with an explicit, content-addressed handoff.

| Job | Workflow | Permissions | What it does |
|---|---|---|---|
| `verify` | `release-verify.yml` (reusable) | `contents: read` | Builds from the tagged source, validates the signed qualification, binds wheel to source, runs the correctness suite, audits dependencies, produces the wheel-scoped SBOM, uploads a candidate bundle |
| `publish` | `release.yml` | `contents: write`, `id-token: write`, `environment: pypi` | Re-derives every digest, signs, drafts the GitHub Release, publishes to PyPI once, validates assets, finalises |

Verification holds no write or OIDC authority, so the expensive read-only work
cannot mutate anything. The `pypi` environment's required-reviewer gate sits on
`publish` alone, which means reviewers approve **after** the readiness summary
exists rather than approving a run whose evidence has not been produced yet.

### Which artifact is authoritative

The **qualified wheel** — the one named by `SAFETY_QUALIFICATION_WHEEL_FILENAME`
and covered by the signed `safety-qualification.json` — is what ships. The wheel
built during verification is never published; it exists only to prove the
qualified wheel came from the tagged commit.

Provenance is established by four bindings, all before `uv publish`:

1. **tag ↔ source** — the tag must equal `v<pyproject version>` at the checkout.
2. **qualification ↔ wheel bytes** — the signed artifact records the wheel's
   SHA-256, and the Sigstore identity is verified before the JSON is parsed.
3. **tag ↔ wheel version** — from the wheel's own `METADATA`.
4. **source ↔ wheel** — `scripts/verify_wheel_provenance.py` rebuilds from the
   tagged checkout and requires byte equality.

Binding 4 is the one that was missing. Without it, any wheel declaring
`Name: agents-shipgate` and the right `Version` satisfied every check, so the
pipeline tested one artifact and published another.

### Build reproducibility

Byte equality is only achievable because the build backend is pinned in
[`constraints/release-build.txt`](../constraints/release-build.txt). Wheels
record `Generator: hatchling <version>` inside `.dist-info/WHEEL`, so an
unpinned backend makes two machines produce different bytes from identical
source.

**The qualification promotion flow must build with the same constraint file:**

```bash
PIP_CONSTRAINT=constraints/release-build.txt python -m build --wheel
```

If a backend bump lands between qualification and release, the provenance gate
fails. The fix is to re-run qualification against a wheel built with the current
pin — not to relax the comparison. `--allow-payload-equivalent` exists as a
pre-approved interim control for genuine reproducibility gaps; using it requires
opening an issue to track the gap, and it still rejects any content difference.

## Before tagging: rehearse

**A rehearsal on the candidate commit is a prerequisite for pushing a tag.**
Before this existed, the verification and failure paths of the release workflow
were first-run at the same moment publication became possible.

Run the **Release Rehearsal** workflow (`workflow_dispatch`) against the
candidate ref. It calls the same reusable verification workflow the release
uses — same build, qualification validation, tests, audit, SBOM, and handoff.

It cannot publish, for three independent reasons: there is no publication job in
the file, `permissions: contents: read` caps the token so tag and release
creation fail, and no `id-token: write` anywhere means Trusted Publishing cannot
mint a token.

Check the run's readiness summary before tagging:

- every control row reads `pass`;
- **Wheel bound to tagged source** reads `identical_bytes` (`identical_payload`
  means the backend pin drifted);
- the wheel SHA-256 matches the wheel you expect to ship.

### Rehearsing a failure path

At least once per release-process change, prove the gate fails closed. The
cheapest deliberate mismatch: point `SAFETY_QUALIFICATION_WHEEL_URL` at a wheel
from a different commit and confirm the rehearsal fails at **Bind the qualified
wheel to the tagged source tree**, naming the differing members. Restore the
variable afterwards.

### Re-deriving the timeout

`release-verify.yml` sets `timeout-minutes: 25`, derived from observed hosted-
runner timings rather than an estimate:

| Phase | Observed |
|---|---|
| Correctness suite (`-n auto`, `not perf`) | 407s |
| Install, lint, compile, schema check, static lint, dependency audit | ~40s |
| Source build, artifact download, signature + qualification verification, isolated SBOM install | ~2 min |

A healthy run lands near 10 minutes, so 25 leaves roughly 2.5x headroom. The
suite dominates; the SBOM step is the second largest because it installs the
wheel's runtime closure into a fresh environment.

After any change that materially grows the suite, read the actual job duration
from a rehearsal run and reset the timeout to roughly 2.5x it. Do not raise it
in response to a single timeout without checking what got slower — a timeout
that appears without a corresponding change in these phases is more likely a
hung step than an undersized budget.

## Cutting the release

1. Confirm `pyproject.toml` has the release version and a rehearsal is green.
2. Push the tag: `git tag v0.16.0 && git push origin v0.16.0`.
3. The `verify` job runs unattended.
4. Approve the `pypi` environment gate on the `publish` job, using the readiness
   summary as the evidence.
5. Confirm the GitHub Release is published (not draft) with all assets, then run
   the fan-out checks in [`distribution.md`](distribution.md).

## Recovery

PyPI uploads are **immutable**. A version can never be replaced, so recovery is
about completing an interrupted transaction, never about retrying it blindly.

The publication job is ordered so that the recoverable state is the likely one:
the draft GitHub Release, carrying every authoritative asset, is created
*before* the PyPI upload.

### Publication succeeded, finalisation failed

This is the case the ordering is designed for. PyPI holds the version and a
**draft** GitHub Release holds the wheel, SBOM, signatures, qualification
artifacts, provenance record, and candidate manifest.

Re-run the `publish` job. It is idempotence-aware:
`scripts/release_publication.py pypi-state` classifies the index as
`published_identical`, the upload step is skipped via its `if:` condition, and
the job proceeds to asset validation and finalisation.

If re-running is not possible, finalise by hand — the draft already has the
authoritative assets:

```bash
gh release edit v0.16.0 --draft=false --latest
```

### Publication failed

Nothing was uploaded. Fix the cause and re-run the `publish` job; the state
check returns `absent` and the upload proceeds normally.

### The index holds different bytes for this version

`pypi-state` exits non-zero with `published_divergent` and publication stops.
This means the version was uploaded from a different artifact — possibly a
partially-completed earlier attempt with a different wheel.

**Do not attempt to republish; PyPI will not accept it.** Cut a new patch or
pre-release version, re-run qualification against the new wheel, and tag again.
Delete or clearly mark the stale draft release so the wrong assets are not
mistaken for the shipped ones.

### Verification failed

Nothing outside the run changed: no tag deletion, no cleanup needed. Fix the
cause on the branch, and either move the tag (only safe while nothing has been
published for it) or cut a new version.

## Required repository configuration

The six qualification variables are read by the **verification** job, which
deliberately runs without an environment so it can run unattended. They must
therefore be available at **repository** scope:

| Variable | Value |
|---|---|
| `SAFETY_QUALIFICATION_WHEEL_URL` | HTTPS URL for the exact qualified wheel |
| `SAFETY_QUALIFICATION_WHEEL_FILENAME` | Safe wheel basename |
| `SAFETY_QUALIFICATION_JSON_URL` | HTTPS URL for the qualified JSON artifact |
| `SAFETY_QUALIFICATION_SIGSTORE_BUNDLE_URL` | HTTPS URL for that artifact's Sigstore bundle |
| `SAFETY_QUALIFICATION_SIGNER_IDENTITY` | Trusted certificate identity for qualification promotion |
| `SAFETY_QUALIFICATION_OIDC_ISSUER` | Trusted OIDC issuer |

These are variables, not secrets — they are URLs and identities, readable by any
workflow in the repository regardless of scope. Environment-scoped values still
override repository ones for the `publish` job.

Moving them to repository scope does weaken *who can change them* relative to
environment-scoped variables. That is an accepted trade, because the control
that mattered is now stronger: a tampered wheel URL no longer reaches PyPI, it
fails the source-binding gate. Variable ACLs were doing work that
content-addressing now does directly.
