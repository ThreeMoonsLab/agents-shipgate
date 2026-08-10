# Release Runbook

Operational procedure for cutting a tagged release. Covers what each control
proves, the mandatory rehearsal, and the recovery path when publication
partially succeeds.

For the packaging surface and post-release fan-out checks, see
[`distribution.md`](distribution.md).

## The pipeline

A release runs as four jobs with an explicit, content-addressed handoff.

| Job | Permissions | What it does |
|---|---|---|
| `verify` | `contents: read` | Builds from the tagged source, validates the signed qualification, binds wheel to source, runs the correctness suite, audits dependencies, produces the wheel-scoped SBOM, uploads a candidate bundle |
| `stage` | `contents: write`, `actions: read` | Re-peels the tag, requires a matching rehearsal, re-derives every digest, classifies the index, creates or repairs the **draft** release |
| `publish` | `id-token: write`, `environment: pypi` | Signs and uploads to PyPI once |
| `finalize` | `contents: write` | Attaches signatures, confirms the index, validates assets, undrafts |

The split is about which capabilities are ever held together. `publish` can
mint a PyPI Trusted Publishing token, so it holds **no repository write**,
checks out **no project code**, and installs only the hash-locked toolchain in
`constraints/release-publish.txt` with `--require-hashes`. Conversely `stage`
and `finalize` can write to the repository but cannot mint a token. A
dependency compromised in any single job therefore cannot reach both registries.

Verification holds no write or OIDC authority at all, so the expensive
read-only work cannot mutate anything. The `pypi` environment's
required-reviewer gate sits on `publish` alone, so reviewers approve **after**
the readiness summary exists rather than approving a run whose evidence has not
been produced yet.

### The candidate is pinned to a commit, not a tag

`release.yml` passes `github.sha` — never `github.ref` — into verification. A
symbolic ref is re-resolved by the checkout action, so a tag moved between the
push event and the checkout would build one commit while provenance recorded
another. Every downstream binding is keyed to the SHA the verification job
actually resolved with `git rev-parse HEAD`.

Because a tag can still move (or be deleted) *after* verification, both `stage`
and `publish` re-peel it against the remote immediately before acting, and the
draft is created with `gh release create --verify-tag`.

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

**A rehearsal on the candidate commit is a prerequisite for pushing a tag, and
the pipeline enforces it.** Before this existed, the verification and failure
paths of the release workflow were first-run at the same moment publication
became possible.

Run the **Release Rehearsal** workflow (`workflow_dispatch`) against the
candidate ref. It calls the same reusable verification workflow the release
uses — same build, qualification validation, tests, audit, SBOM, and handoff.

`stage` refuses to proceed without a successful rehearsal run whose `head_sha`
equals the verified commit — which binds the workflow revision too, since both
live in the same tree — and whose candidate manifest is byte-identical to the
tagged one. That second check binds candidate *identity*: a qualification
artifact swapped between the rehearsal and the tag is caught even though the
source did not change.

It cannot publish, for three independent reasons: there is no publication job in
the file, `permissions: contents: read` caps the token so tag and release
creation fail, and no `id-token: write` anywhere means Trusted Publishing cannot
mint a token.

Check the run's readiness summary before tagging:

- every control row reads `pass`;
- **Wheel bound to tagged source** reads `identical_bytes` (`identical_payload`
  means the backend pin drifted);
- the wheel SHA-256 matches the wheel you expect to ship.

### The failure path is rehearsed automatically

Every rehearsal runs a fault-injection drill: it corrupts a *copy* of the
qualified wheel and asserts the provenance gate rejects it, failing the
rehearsal if the tampered artifact is accepted. The deliberate-mismatch
exercise is therefore executed on every run rather than left to operator
discipline, and the rejection message appears in the log.

The drill runs only in rehearsal mode — a real release must not spend its
budget on drills.

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

Re-run the workflow. It is idempotence-aware:
`scripts/release_publication.py pypi-state` classifies the index as
`published_identical`, the upload step is skipped via its `if:` condition, and
the run proceeds to asset validation and finalisation.

`published_identical` is deliberately strict — it requires the index to hold
*exactly one* unyanked wheel with the expected filename and digest. A version
that also carries a divergent sdist, a second wheel, a renamed file, or a
yanked record is **not** treated as identical, because skipping the upload and
finalising over it would ship a release this pipeline never verified.

A re-run also never mutates an already-published GitHub Release. It downloads
the published assets, proves they are the verified ones, and leaves them alone;
only drafts are repaired. Clobbering a published release's assets would replace
public bytes that immutable PyPI can no longer be made to match.

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

## Required configuration

Qualification configuration is split by trust level: **what authenticates the
evidence** lives in reviewed code, and only **where the evidence lives** is
mutable.

### Trust roots — reviewed code

`.github/release-trust-roots.json` holds the two values that authenticate the
signed qualification artifact:

| Field | Value |
|---|---|
| `signer_identity` | Exact Sigstore certificate identity of the qualification promotion job |
| `oidc_issuer` | Trusted OIDC issuer, normally `https://token.actions.githubusercontent.com` |

These must **not** be variables. An actor able to set variables could otherwise
substitute fabricated qualification evidence *and* replace the identity that
vouches for it, in a single step with no diff to review. Source-to-wheel
binding does not compensate: that attack reuses the legitimate wheel and forges
only the safety claims about it.

Both ship as `CHANGE_ME` until the promotion flow exists. The release **fails
closed** while either is unset rather than defaulting to something permissive.
Changing either is a trust-root change and is reviewed as one.

### Artifact locations — repository variables

| Variable | Value |
|---|---|
| `SAFETY_QUALIFICATION_WHEEL_URL` | HTTPS URL for the exact qualified wheel |
| `SAFETY_QUALIFICATION_WHEEL_FILENAME` | Safe wheel basename |
| `SAFETY_QUALIFICATION_JSON_URL` | HTTPS URL for the qualified JSON artifact |
| `SAFETY_QUALIFICATION_SIGSTORE_BUNDLE_URL` | HTTPS URL for that artifact's Sigstore bundle |

These are read by the verification job, which deliberately runs without an
environment so it can run unattended, so they live at **repository** scope.
Leaving them mutable is safe precisely because they are only *locations*:
pointing one somewhere else fails either the signature check against the
committed trust root or the source-to-wheel provenance gate.

None of the four are currently set. Until they are, the release stops at
**Require configured qualification artifact locations**.
