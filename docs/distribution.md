# Distribution Plan

These items require release infrastructure, registry credentials, domains, or GitHub repository settings. They are tracked here so the project has a clear path beyond source installs.

## Package Channels

- `agents-shipgate` is published on PyPI.
- Pinned GitHub Action release tags are published, including `v0.5.1`.
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

## Marketplace Repository Notes

The repository keeps a root `action.yml` for GitHub Marketplace publication and
a minimal `.github/workflows/ci.yml` for project validation plus a tag-triggered
release workflow. The action remains a composite action; there is no Docker
action entrypoint in v0.5.
