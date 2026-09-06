# Release Runbook

Operational procedure for cutting a tagged release. Covers what each control
proves, the mandatory rehearsal, and the recovery path when publication
partially succeeds.

For the packaging surface and post-release fan-out checks, see
[`distribution.md`](distribution.md).

## The pipeline

A release runs as five jobs with an explicit, content-addressed handoff.

| Job | Permissions | What it does |
|---|---|---|
| `verify` → `tests` | `contents: read` | Installs the locked closure, checks the locks against the declarations, lints, compiles, schema check, the correctness suite, dependency audit |
| `verify` → `artifact` | `contents: read` | Requires a changelog section for the tag, builds from the tagged source, validates the signed qualification, binds wheel to source, produces the wheel-scoped SBOM, seals and uploads the candidate bundle |
| `stage` | `contents: write`, `actions: read` | Re-peels the tag, requires a matching rehearsal, re-derives every digest, extracts the release notes, classifies the index, creates or repairs the **draft** release |
| `publish` | `id-token: write`, `environment: pypi` | Signs and uploads to PyPI once |
| `finalize` | `contents: write` | Attaches signatures, re-verifies the remote bytes, reapplies the notes and undrafts in one call |

The sealing job installs no project code — only the hash-locked toolchain in
`constraints/release-seal.txt` — and executes nothing from the wheel's runtime
closure. The SBOM is produced by reading installed `.dist-info` metadata rather
than launching the environment's interpreter, because interpreter startup runs
`site` processing, which executes any `.pth` file the closure installed. The
wheel itself is built with `--no-isolation` so the locked backend is the code
that runs.

Verification is two jobs for a specific reason: **the job that seals the handoff
never runs the candidate's tests.** In a combined job the qualified wheel stayed
writable on disk — with its path exported through `GITHUB_ENV` — while pytest,
its plugins, and `conftest` code executed. A test could therefore replace the
wheel *after* the source-to-wheel equality check and before the handoff was
sealed, and the provenance report would still have claimed equality. The
`artifact` job runs no suite and no dependency audit, and re-asserts the binding
on the exact bytes it seals.

The split is about which capabilities are ever held together. `publish` can
mint a PyPI Trusted Publishing token, so it holds **no repository write**,
checks out **no project code**, and installs only the hash-locked toolchain in
`constraints/release-publish.txt` with `--require-hashes`. Conversely `stage`
and `finalize` can write to the repository but cannot mint a token. A
dependency compromised in any single job therefore cannot reach both registries.

"No project code" is exact rather than approximate: `publish` and `finalize`
check out `.github/actions/install-release-toolchain` and nothing else, with
`sparse-checkout-cone-mode: false` because cone mode would also materialise
every file at the repository root. That checkout is not optional — a local
`./.github/actions/...` action is loaded from the workspace, so a job that uses
one without checking out fails while *preparing* the action, before any step
runs. The file it makes available is the one the job already executes as an
action, at the verified commit, with no credentials persisted.

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

Because a tag can still move (or be deleted) *after* verification, it is
re-peeled against the remote immediately before every irreversible step — in
`stage`, in `publish` before the upload, in `finalize` before touching the
release, and once more immediately before undrafting. Undrafting is the moment
the release becomes public, so the binding is confirmed as late as possible: by
then PyPI already holds the bytes for source A, and a tag moved to B would make
GitHub's source archives resolve to different code than the index serves. The
draft is created with `gh release create --verify-tag`.

The tag is re-peeled again inside the upload step itself, immediately before
`uv publish`, because everything between the previous check and the upload —
artifact download, digest verification, signing, the index query — is window.

See [Deployment prerequisites](#deployment-prerequisites): tag protection is
what actually closes this, and the re-peels are detection, not prevention.

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

### Which evidence bar the tag must meet

Two named qualification policies exist, and the **version decides which one
applies** — nothing in the artifact does:

| Version | Accepted qualification | Cases |
|---|---|---|
| `0.x` (epoch 0, major 0) | `pre_1_0`, or the stronger `beta` | 56, or 100 |
| `1.0` and later | `beta` only | 100 |
| unparsable | `beta` only | 100 |

The `pre_1_0` policy was approved for issue #341 and is recorded, with its
thresholds and rationale, in
[`release-evidence-policy-decision.md`](release-evidence-policy-decision.md). It
buys a smaller corpus, not a laxer judgement: zero unsafe auto-passes, a
per-case verifier receipt, the 20% holdout fraction and the κ floor are
identical to the production policy, and every exact-match floor is the
production rate rounded up.

Both gates apply the rule independently — the exhaustive
`scripts/verify_safety_qualification_release.py` in the `tests` job, and the
standard-library `scripts/verify_qualification_binding.py` in the sealing job.
The sealing gate cannot import the project, so it *restates* each tier's
strata, exact-match floors, per-stratum holdout, and origin and κ floors, and
re-derives them from the raw cases; a case count alone would let 56 identical
rows through. An artifact naming a tier the version does not admit is rejected
by both, and is then measured against the *production* policy, so a bad tier
can never shrink what is checked.

"Unparsable" is decided by a complete PEP 440 parse, not a prefix: `0garbage`
is not a `0.x` version and gets the production bar.

To produce the artifact, `scripts/run_safety_qualification.py` selects the same
way. `--policy-tier production` opts up to the 80-case `beta` bar on a `0.x` wheel;
`--policy-tier pre-1.0` is refused for a `1.0`-or-later wheel, at the point of
production rather than at the gate.

### Build reproducibility

Byte equality is only achievable because the build backend is pinned in
[`constraints/release-build.txt`](../constraints/release-build.txt). Wheels
record `Generator: hatchling <version>` inside `.dist-info/WHEEL`, so an
unpinned backend makes two machines produce different bytes from identical
source.

**The qualification promotion flow must build with the same backend:**

```bash
python -m pip install --require-hashes -r constraints/build-backend.txt
python -m build --wheel --no-isolation
```

Install the backend closure, then build without isolation. Setting
`PIP_CONSTRAINT` alone does **not** pin it: current pip does not apply
constraints to an isolated build environment, so a constrained build still
resolves whatever the index offers — verified by constraining hatchling to a
version that does not exist and watching the build succeed.

If a backend bump lands between qualification and release, the provenance gate
fails. The fix is to re-run qualification against a wheel built with the current
pin — not to relax the comparison. `--allow-payload-equivalent` exists as a
pre-approved interim control for genuine reproducibility gaps; using it requires
opening an issue to track the gap, and it still rejects any content difference.

### The environment is locked, and it is CI's

`pip install -e ".[dev]"` resolves fresh every time it runs, so the release
tested the candidate against a different set of packages than the CI run that
approved the commit — a ruff, pytest or plugin release landing in between was
enough. Both now install the same hash-locked closure, with the identical
command:

```bash
python -m pip install --require-hashes --requirement constraints/dev.txt
python -m pip install --require-hashes --requirement constraints/build-backend.txt
python -m pip install -e . --no-deps --no-build-isolation
python -m pip check
```

The project is installed separately because an editable install cannot be
hashed, and `pip check` is what proves the locked closure actually satisfies the
project's declared dependencies.

`--no-build-isolation` is load-bearing. `--no-deps` does not disable PEP 517
build isolation, and current pip does not apply `PIP_CONSTRAINT` to an isolated
build environment, so an editable install resolves its backend — and the
backend's own dependencies — from the index unless the closure is already
present. That is why the backend has a lock of its own.

| Lock | Installed by | Contains |
|---|---|---|
| [`constraints/dev.txt`](../constraints/dev.txt) | CI and `verify` → `tests` | The development closure: runtime dependencies plus the `dev` extra |
| [`constraints/build-backend.txt`](../constraints/build-backend.txt) | CI, `verify` → `tests`, qualification promotion | The build backend's closure, so builds need no isolation |
| [`constraints/release-seal.txt`](../constraints/release-seal.txt) | `verify` → `artifact` | `build`, `hatchling`, `sigstore` |
| [`constraints/release-publish.txt`](../constraints/release-publish.txt) | `stage`, `publish`, `finalize` | `uv`, `sigstore` |
| [`constraints/release-build.txt`](../constraints/release-build.txt) | — | Not a lock: the one hand-maintained backend pin the closure above resolves |

Locks installed into the same environment must pin every shared distribution to
the same version, or the second `pip install` moves part of the first one's
closure; that is checked too.

Updating a dependency is two commands, and the second one is the gate:

```bash
python scripts/update_locks.py            # or a single lock path
python scripts/verify_dependency_lock.py
```

`scripts/update_locks.py` recompiles with `uv` and restores each lock's header,
which the compiler would otherwise overwrite — the reason regenerating used to
be a per-file ritual nobody wanted to perform.

`scripts/verify_dependency_lock.py` runs in CI **and** in release verification,
and fails when a lock stops describing its declarations. Each lock records the
normalized PEP 508 requirements it was compiled from:

```
#   declares: pytest<10,>=9.1.1
```

so a declaration that grows an extra, moves behind a marker, or becomes a direct
URL invalidates the lock even though every name and every range still matches —
a name-and-range comparison accepts all three silently. On top of that it fails
on a declared requirement with no pin, a pin outside the declared range, a
direct requirement the declarations no longer contain, and a pin without a hash.

Pins are matched **per environment**, not by comparing marker text. Markers are
evaluated over CPython 3.12–3.14 × linux/darwin/win32 × x86-64/aarch64, and for
every environment a declaration selects there must be exactly one applicable
pin, satisfying the declared range or naming the declared URL. That is what
distinguishes a conditional requirement that is legitimately unpinned (no
supported environment selects it) from one that is simply missing, catches a pin
whose own marker excludes the platform that needs it, and rejects two branches
whose markers differ as strings but both apply somewhere. The same comparison
runs across locks installed together, so a universal fork split between two
locks is fine while a genuine disagreement inside one environment is not.

`[build-system]` is bound too: the backend `pyproject.toml` actually builds with
must be the one `constraints/build-backend.txt` pins. Otherwise a raised floor
or a switch to another backend leaves the hand-maintained pin, its closure, and
this gate perfectly consistent with each other and wrong about the wheel.

It deliberately does not re-resolve against the index — "stale" means
*inconsistent with the declarations*, not *older than the newest release on
PyPI*, or every unrelated upload would turn the build red.

### The release notes are the changelog

The release body is the `CHANGELOG.md` section matching the tag, extracted from
the checkout — which is pinned to the verified commit, not to the tag — so the
published notes are the reviewed text rather than something retyped at tag time.
Earlier releases published `Agents Shipgate v0.15.0` as their entire body.

A tag matches a `##` heading whose first token is the version, with or without a
`v`, in `[brackets]` or not, and anything after it (conventionally the date) is
ignored. **`## Unreleased` never matches**, which is what makes "promote the
heading" a step the pipeline enforces rather than one an operator remembers.
Verification requires the section, so a rehearsal fails on a missing one while
the tag still does not exist. A body over GitHub's 125,000-character limit is
also refused there rather than by a 422 from `gh release create`.

Three jobs extract the notes — verification, `stage`, and `finalize` — so
verification publishes their SHA-256 as a job output and the other two pass it
back with `--expected-sha256`. `finalize` re-derives them from `CHANGELOG.md`
fetched at the verified commit and reapplies the body **in the same API call
that undrafts**, because everything between staging and finalisation, the
environment approval window included, is time in which a release-write actor
can edit the draft's text; asset digests are re-derived there but the body was
not. Each job writes the file under `$RUNNER_TEMP`, never into the checkout, so
a candidate that commits its own `release-notes.md` cannot decide where the
write lands — and cannot pass the rehearsal only to fail after the tag exists.

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

Each verification job is bounded separately, from observed hosted-runner
timings rather than an estimate:

| Job | Phase | Observed | Timeout |
|---|---|---|---|
| `tests` | correctness suite (`-n auto`, `not perf`) | 407s | 20 min |
| `tests` | install, lint, compile, schema check, static lint, audit | ~40s | |
| `artifact` | source build, downloads, signature + qualification + provenance | ~30s | 15 min |
| `artifact` | isolated SBOM install | ~1–2 min | |

Each leaves roughly 2.5–3.5x headroom. The suite dominates its job; the SBOM
step dominates the other, because it installs the wheel's whole runtime closure
into a fresh environment.

After any change that materially grows the suite, read the actual job duration
from a rehearsal run and reset the timeout to roughly 2.5x it. Do not raise it
in response to a single timeout without checking what got slower — a timeout
that appears without a corresponding change in these phases is more likely a
hung step than an undersized budget.

## Cadence

**Approved 2026-09-02 for [#491](https://github.com/ThreeMoonsLab/agents-shipgate/issues/491).**

Releases are cut **on a schedule, not on a feature**. The schedule is the
policy; everything below is how it is kept and what happens when it is not.

| | |
|---|---|
| Interval | **30 days** |
| Defect threshold | **45 days** |
| Who tags | The release owner — Pengfei Hu (`pengfei-threemoonslab`), the same owner as [`release-evidence-policy-decision.md`](release-evidence-policy-decision.md) |
| Where the number is read | The **Release cadence** step in every CI run: a job-summary block, and a warning annotation once the interval lapses |

Why a schedule at all: at the time this was written, 5,039 of `CHANGELOG.md`'s
6,242 lines were under `## Unreleased` — 81% of the project's shipped work,
installable by nobody, because releases were gated on a single expensive
artifact and therefore happened when that artifact happened, which was never.
The cost compounds: the longer the section grows, the larger and riskier the
eventual release note, and the more migration notes land on adopters at once.
It had already grown past GitHub's 125,000-character release-body limit, so the
backlog had become a *blocker* on its own.

### Keeping it

```bash
python scripts/release_cadence.py --json
```

Counts days since the newest **release tag** — `v` followed by a complete PEP
440 version, so `preview-*`, `wip-sectiond` and `m3-pre-rebase` are correctly
not releases. It reads the repository's own tags and needs no network, so an
operator gets the same answer a runner does. Three states:

| Status | Meaning | Action |
|---|---|---|
| `current` | under 30 days | none |
| `due` | 30–45 days | cut a release, or record on the tracking issue why not |
| `overdue` | over 45 days | **a defect to be triaged**, not a state to be tolerated |

The CI step deliberately **warns and does not fail**. A red check here would
fail whichever unrelated pull request happened to arrive after the interval
lapsed, and that author is not the actor who can fix it. `--fail-when-overdue`
gives the non-zero exit to an operator or a scheduled job that wants one;
nothing in `ci.yml` passes it.

### When the interval is missed

1. **Name the blocker.** If the release cannot be cut, something is blocking
   it, and it is an issue with a number. At the time of writing that is
   [#456](https://github.com/ThreeMoonsLab/agents-shipgate/issues/456): no
   signed qualification artifact exists, so the pipeline fails closed at
   **Verify production qualification and exact wheel binding**.
2. **Ship what can be shipped.** Publish an [unqualified
   preview](#the-unqualified-preview-channel) so merged work is installable
   while the blocker is worked. A preview is not a release and does not reset
   this metric — deliberately.
3. **Keep the note readable.** See below; this is the part that rots silently.

### Release-note hygiene is continuous, not a release-day task

`CHANGELOG.md`'s `## Unreleased` section **is** the next release body, and
verification refuses a body over 125,000 characters. At 338,932 characters it
was 2.7× that limit — a tag could not have been cut from it even with every
other gate green.

The shape that holds:

- **`CHANGELOG.md` is the release note.** One line per change: the bold
  headline sentence and its issue references. A section stays a section
  somebody will read end to end.
- **`docs/changelog/<version>.md` is the record.** The full reviewed prose for
  every entry — design rationale, the trap each fix closed, the invariants —
  moves there when the section is promoted. Nothing is deleted; the release
  note links to it.
- **Write the one-liner when the change lands**, not 165 entries later. The
  headline sentence already exists in every entry; curation is only expensive
  when it is deferred.

## Cutting the release

1. Promote the `## Unreleased` heading in `CHANGELOG.md` to
   `## <version> - <date>`, move the full prose to
   `docs/changelog/<version>.md`, and leave a one-line-per-change section
   behind. This is the release body; verification refuses a tag with no
   matching section, and refuses one over 125,000 characters.
2. Stamp `<version>` on `STABILITY.md`'s title and on every
   `## Migration Note: unreleased` heading.
3. Confirm `pyproject.toml` has the release version and a rehearsal is green.
4. Push the tag: `git tag v0.16.0 && git push origin v0.16.0`.
5. The `verify` job runs unattended.
6. Approve the `pypi` environment gate on the `publish` job, using the readiness
   summary as the evidence.
7. Confirm the GitHub Release is published (not draft) with all assets and the
   changelog section as its body, then run the fan-out checks in
   [`distribution.md`](distribution.md).
8. **Only now, move the published-release constants.**
   `LATEST_PUBLISHED_VERSION` and `LATEST_PUBLISHED_CONTRACT_VERSION` in
   [`src/agents_shipgate/published_release.py`](../src/agents_shipgate/published_release.py),
   then `.well-known`'s `release_status.latest_release` and every other surface
   that names the newest release — `tests/test_public_surface_contract.py`
   fails once per file until each does, so the suite enumerates them for you.

   These two are what `init` writes into an adopter's repository: the `uses:`
   pin in the workflow it generates, the runner pins in the bundled adoption
   prompts, the `shipgate_version` the bundled CI recipe installs. Moving them
   *before* the tag exists is the failure they guard against (#506) — a
   generated workflow that names a ref GitHub cannot resolve fails the
   adopter's job before any step runs. Moving them late costs nothing: the
   previous release still resolves.

   `LATEST_PUBLISHED_CONTRACT_VERSION` is the `CONTRACT_VERSION` the new tag
   emits, not the tree's. When it lands below
   `MINIMUM_CONTROL_CONTRACT_VERSION`, the adoption prompts say so in the same
   breath as the pin rather than quietly promising a floor the pinned build
   does not report.

## The unqualified preview channel

**Admissibility finding: `release-evidence-policy-decision.md`
[§ Amendment 2](release-evidence-policy-decision.md#amendment-2--the-unqualified-preview-channel),
approved 2026-09-02.** Read it before touching this: it records the five
conditions the channel is admissible under, and the cases that would have made
it inadmissible.

A preview exists so merged work is installable while a release is blocked. It
is **not a release**, and every property below is load-bearing rather than
descriptive:

| | |
|---|---|
| Workflow | `release-preview.yml`, `workflow_dispatch` only, with a mandatory `reason` |
| Precondition | a successful `ci.yml` run for the exact `github.sha` |
| Version | `<pyproject version>+preview.<date>.g<short sha>` — a PEP 440 **local** segment, which public indexes must refuse |
| Ref | `preview-<version>`, outside the `v*` trigger namespace |
| Assets | the wheel, and nothing else |
| Marked | `--prerelease`, so it is never `Latest` |
| Authority | `contents: write` in the job that runs no candidate code; `contents: read` in the job that builds. No `id-token` anywhere |

To cut one, dispatch **Release Preview (unqualified)** against the commit and
give the reason. It appears under the newest qualified release, labelled as not
one.

**It does not reset the cadence metric, and that is deliberate** —
`release_cadence.py` counts `v*` release tags only. A channel that suppressed
the signal which justified its existence would be worse than not having one.

Three things a preview can never become:

- **Release evidence.** `stage` locates the mandatory rehearsal by workflow
  file (`release-rehearsal.yml`), so a preview run is invisible to it.
- **A PyPI upload.** The local version segment is refused by the index at the
  API, and no job in the file holds `id-token: write`.
- **A qualified artifact.** It ships no `safety-qualification.json`, and
  `verify_safety_qualification_release.py` rejects it on both the missing
  artifact and `tag == v<version>`. The negative controls are in
  `tests/test_release_pipeline.py` § #491.

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

A re-run also never mutates an already-published GitHub Release. `stage`
downloads the published assets, proves they are the verified ones, records
`release_state=published`, and **`publish` and `finalize` do not run at all**.
Re-signing would mint fresh, non-reproducible Sigstore bundles and replace the
public attestations for no benefit; clobbering assets would replace public bytes
that immutable PyPI can no longer be made to match.

Because that declaration makes both signature-verifying jobs skip, the **body**
is checked there too, not only the assets. Release notes stay editable after
publication and nothing else re-reads them, so a re-run over a release whose
text was changed afterwards would otherwise certify it. A divergent body fails
the run rather than being rewritten — mutating a published release is exactly
what this branch refuses to do — and the fix is to restore the changelog section
by hand, or to investigate who edited it.

The index is also reclassified *inside* the publish attempt rather than reusing
the decision `stage` made before environment approval. A stale `absent` would
otherwise make "Re-run failed jobs" retry an immutable version and never reach
recovery.

Before undrafting, `finalize` downloads every remote asset and re-derives it
against the trusted manifest digest — closed-world apart from the two signature
bundles, which are themselves verified against the release workflow's Sigstore
identity. Asset *names* are not evidence: draft repair clobbers expected names
but leaves unlisted ones behind, and an asset can be replaced during the
approval window.

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

## Deployment prerequisites

Some windows in this pipeline cannot be closed by code in this repository, and
the workflow does not pretend otherwise. Each item below is a **repository or
organisation setting**; without them the corresponding check is detection after
the fact rather than prevention.

| Prerequisite | What it closes | Residual without it |
|---|---|---|
| Ruleset on `v*` forbidding tag **updates and deletions** | A tag moving between verification and any later step | The re-peels detect a moved tag, but only at the next checkpoint. Between the last peel and `uv publish`, a move publishes immutable candidate A while the public tag resolves to B |
| **Immutable releases** enabled | Post-publication mutation of release assets | A `contents: write` actor can replace assets after finalisation, and nothing in this workflow runs again to notice |
| **Restricted release-write authority** (few actors, protected environment) | Concurrent mutation during finalisation | Remote verification and undrafting are two API calls. Another writer can replace an asset or add one in between, and the undraft publishes the changed server-side set |
| Protected `.github/workflows/**` and `.github/release-trust-roots.json` (CODEOWNERS or ruleset) | Changes to the pipeline and its trust roots landing unreviewed | Workflow logic is candidate-controlled at the tag, so review is the control that makes it trustworthy |
| `pypi` environment reviewers, independent of the release initiator | Unattended publication | Approval becomes a formality |

### The limit worth stating plainly

The workflow that runs for a tag is **the workflow at that tag** — it is part of
the candidate. The publication job is hardened as far as this repository can
harden it: it holds `id-token: write` and nothing else, checks out no
repository code, installs only a hash-locked closure with `--require-hashes`,
and classifies the index with `curl` and `jq` rather than a fetched helper.
That removes candidate *code* from the token-bearing job. It does not make the
job's own YAML a separate trust root, and no arrangement of files in this
repository can. Branch/tag protection and review of `.github/**` are what
supply that boundary.

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
