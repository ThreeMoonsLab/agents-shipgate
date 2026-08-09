# Distribution Plan

These items require release infrastructure, registry credentials, domains, or GitHub repository settings. They are tracked here so the project has a clear path beyond source installs.

## Package Channels

- `agents-shipgate` is published on PyPI.
- Pinned GitHub Action release tags are published, including `v0.15.0`.
- GitHub Releases attach the independently qualified wheel, SBOM,
  `safety-qualification.json`, and their Sigstore bundles. The tag workflow
  does not rebuild or publish an unqualified sdist.
- Evaluate a container image later only if it has an exercised build-and-test path.
- Evaluate Homebrew once CLI usage warrants it.

The GitHub Action installs from its tagged source by default. A
`shipgate_version` input is available for release flows that intentionally need
to install a published PyPI version.

## Supply Chain

- Generate a wheel-scoped SBOM from an isolated runtime-only install of the
  published wheel, bound to its SHA-256.
- Sign release artifacts with Sigstore.
- Publish to PyPI through Trusted Publishing from `.github/workflows/release.yml`.
- Keep GitHub Actions pinned by SHA.
- Pin the build backend in `constraints/release-build.txt` so release wheels are
  byte-reproducible.
- Use Dependabot for Python and GitHub Actions updates.
- Add a lockfile for release and dev dependency builds once packaging workflow is finalized.

PyPI Trusted Publishing is configured for this repository's tag-triggered
release workflow and protected `pypi` environment.

The operational procedure — mandatory rehearsal, the two-job publication
transaction, and the recovery path when PyPI succeeds but release finalization
fails — is in [`release-runbook.md`](release-runbook.md).

### Protected qualification inputs

Every tag release fails closed unless all six variables below are configured.
They are read by the read-only verification job, which runs without an
environment so it can run unattended and produce the evidence the `pypi`
reviewers approve against — so the values must exist at **repository** scope.
Environment-scoped values still override them for the publication job. Values
must be populated by the independent benchmark-owner promotion flow after it
runs the frozen corpus against the exact wheel and signs
`safety-qualification.json`:

| Variable | Required value |
|---|---|
| `SAFETY_QUALIFICATION_WHEEL_URL` | HTTPS URL for the exact qualified wheel |
| `SAFETY_QUALIFICATION_WHEEL_FILENAME` | Safe wheel basename, for example `agents_shipgate-0.16.0b6-py3-none-any.whl` |
| `SAFETY_QUALIFICATION_JSON_URL` | HTTPS URL for the production-qualified JSON artifact |
| `SAFETY_QUALIFICATION_SIGSTORE_BUNDLE_URL` | HTTPS URL for that JSON artifact's Sigstore bundle |
| `SAFETY_QUALIFICATION_SIGNER_IDENTITY` | Exact trusted certificate identity configured for qualification promotion |
| `SAFETY_QUALIFICATION_OIDC_ISSUER` | Trusted OIDC issuer, normally `https://token.actions.githubusercontent.com` for GitHub Actions |

The verification job checks the signature identity first, then validates the
artifact's production policy, 100-case invariants, tag/version, and wheel
SHA-256. It then rebuilds a wheel from the tagged checkout and requires byte
equality with the qualified wheel, which is the binding that ties the published
artifact to the tagged commit. The rebuilt wheel is only ever a comparison
reference: the artifact published to PyPI remains the exact qualified wheel.

Missing variables, non-HTTPS URLs, an unsafe filename, an invalid signature, a
non-production result, or any binding mismatch stops before PyPI publication.

Because a tampered wheel URL now fails the source-binding gate rather than
reaching PyPI, variable ACLs are no longer the primary control over what gets
published. The required-reviewer gate on the `pypi` environment protects the
publication step itself.

This is a configured trust root, not proof of organizational independence.
The promotion job trusts the signed qualification summary and does not replay
its underlying verifier receipts. The four-week, three-design-partner rollout
is likewise an external beta stop condition; the machine gate enforces only
the qualification artifact's combined minimum of 40 real-history,
rejected/reverted, or design-partner origins.

## Marketplace And Site

- `ThreeMoonsLab/agents-shipgate` is listed on GitHub Marketplace.
- Create a small landing page with install instructions, trust model, and findings gallery.
- Consider a local-only playground later; do not accept private customer manifests into a hosted service without a separate privacy review.

## Release Fan-Out Checklist

The tag-triggered release workflow publishes PyPI artifacts and creates the
GitHub release. After each release tag, verify the external surfaces that live
outside this repository:

- PyPI shows the new package version.
- GitHub Marketplace shows the new release tag as Latest.
- The website header, footer, `/llms.txt`, and
  `/.well-known/agents-shipgate.json` show the new version.
- Website discovery metadata points at the current report schema and GitHub
  Action pin.
- `/sitemap.xml` resolves to the current sitemap or redirects to
  `/sitemap-index.xml`.

## Marketplace Repository Notes

The repository keeps a root `action.yml` for GitHub Marketplace publication and
a minimal `.github/workflows/ci.yml` for project validation plus a tag-triggered
release workflow. The action remains a composite action; there is no Docker
action entrypoint in the current release.
