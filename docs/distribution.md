# Distribution Plan

These items require release infrastructure, registry credentials, domains, or GitHub repository settings. They are tracked here so the project has a clear path beyond source installs.

## Package Channels

- Publish `agents-shipgate` to PyPI.
- Publish a pinned GitHub Action release tag such as `v0.3.0`.
- Evaluate a container image later only if it has an exercised build-and-test path.
- Evaluate Homebrew once CLI usage warrants it.

The GitHub Action can install from its tagged source before PyPI publication. A
`shipgate_version` input is available for release flows that intentionally
install a published PyPI version.

## Supply Chain

- Generate SBOMs for release artifacts.
- Sign release artifacts with Sigstore.
- Publish to PyPI through Trusted Publishing from `.github/workflows/release.yml`.
- Keep GitHub Actions pinned by SHA.
- Use Dependabot for Python and GitHub Actions updates.
- Add a lockfile for release and dev dependency builds once packaging workflow is finalized.

PyPI release prerequisite: configure the `agents-shipgate` PyPI project with a
Trusted Publisher for this repository, workflow `.github/workflows/release.yml`,
environment `pypi`, and the tag-triggered release job before pushing a public
release tag.

## Marketplace And Site

- Submit `ThreeMoonsLab/agents-shipgate` to GitHub Marketplace after the first tagged release.
- Create a small landing page with install instructions, trust model, and findings gallery.
- Consider a local-only playground later; do not accept private customer manifests into a hosted service without a separate privacy review.

## Marketplace Repository Notes

The repository keeps a root `action.yml` for GitHub Marketplace publication and
a minimal `.github/workflows/ci.yml` for project validation plus a tag-triggered
release workflow. The action remains a composite action; there is no Docker
action entrypoint in v0.3.
