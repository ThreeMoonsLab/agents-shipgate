# Distribution Plan

These items require release infrastructure, registry credentials, domains, or GitHub repository settings. They are tracked here so the project has a clear path beyond source installs.

## Package Channels

- `agents-shipgate` is published on PyPI.
- Pinned GitHub Action release tags are published, including `v0.8.0`.
- GitHub Releases attach the wheel, sdist, SBOM, and Sigstore bundles.
- Evaluate a container image later only if it has an exercised build-and-test path.
- Evaluate Homebrew once CLI usage warrants it.

The GitHub Action installs from its tagged source by default. A
`shipgate_version` input is available for release flows that intentionally need
to install a published PyPI version.

## Supply Chain

- Generate SBOMs for release artifacts.
- Sign release artifacts with Sigstore.
- Publish to PyPI through Trusted Publishing from `.github/workflows/release.yml`.
- Keep GitHub Actions pinned by SHA.
- Use Dependabot for Python and GitHub Actions updates.
- Add a lockfile for release and dev dependency builds once packaging workflow is finalized.

PyPI Trusted Publishing is configured for this repository's tag-triggered
release workflow and protected `pypi` environment.

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
