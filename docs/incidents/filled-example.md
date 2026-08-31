# Filled example: the capability change hidden in a routine release

> Status: example, not an incident claim · Sources checked: 2026-08-31

## What is publicly established

The
[AWS security advisory](https://github.com/aws/aws-toolkit-vscode/security/advisories/GHSA-7g7f-ff96-5gcw)
states that malicious code entered the Amazon Q Developer VS Code extension
repository through an inappropriately scoped token and was automatically
included in version 1.84.0. AWS reports that the code did not execute because
of a syntax error, removed 1.84.0 from distribution, and released 1.85.0.
[JFrog's public analysis](https://research.jfrog.com/post/amazon-q-vs-code-extension-compromised-with-malicious-code/)
documents that the injected change carried a prompt directed at the coding
assistant.

## The incident shape

A prompt-bearing capability change can look like one file among ordinary
release edits. The public incident and this constructed fixture are separate:
the fixture uses only inert synthetic text and generic release metadata.

## Replay

```bash
uvx agents-shipgate fixture run capability_change_rides_release
```

Fresh output from the fixture contract:

- decision: `review_required`;
- merge verdict: `human_review_required`;
- `can_merge_without_human`: `false`;
- signal: `SHIP-VERIFY-TRUST-ROOT-TOUCHED` on `prompts/release.md`.

## Detection boundary

The verifier establishes that a protected prompt path changed beside
`package.json` and `CHANGELOG.md`, then routes the PR to a human. It does not
classify the prose as malicious or claim it would execute. It also does not
replace credential scoping, signed releases, extension-store review, or runtime
controls.

Related replays:
[`agent_weakens_gate`](agent-weakens-gate.md) and
[`governed_edits_governance`](governed-edits-governance.md).
