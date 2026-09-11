# Incident shape: a prompt change rides a routine release

AWS reported that malicious code reached version 1.84.0 of the Amazon Q
Developer VS Code extension after a repository credential was abused and the
change was automatically included in a release; AWS removed that version and
published 1.85.0. See the
[AWS repository security advisory](https://github.com/aws/aws-toolkit-vscode/security/advisories/GHSA-7g7f-ff96-5gcw).
[JFrog's public analysis](https://research.jfrog.com/post/amazon-q-vs-code-extension-compromised-with-malicious-code/)
describes the prompt-bearing shape of the injected change.

This fixture models only the review shape. It uses inert, synthetic text: a
prompt under `prompts/release.md` changes beside `package.json` and
`CHANGELOG.md` in a routine patch release. It neither reproduces the vendor's
code nor attempts a destructive action.

Replay it from this checkout:

```bash
./shipgate fixture run prompt_change_rides_release
```

Once a release carries this fixture, replay it with
`uvx agents-shipgate@<that version> fixture run prompt_change_rides_release`. No published release
does yet — the newest, `v0.15.0`, does not carry it — so naming a version here would
fail at install before the fixture ran.

Current engine output:

- changed files: `CHANGELOG.md`, `package.json`, and `prompts/release.md`;
- `SHIP-AGENT-BOUNDARY-PROTECTED-SURFACE-UNCLASSIFIED` and
  `SHIP-VERIFY-TRUST-ROOT-TOUCHED` are both present; the latter names
  `prompts/release.md` and its `**/prompts/**` match;
- `verifier.json.trigger.matched_rules` includes
  `TRIGGER-PROMPTS-OR-POLICIES` with action `run_shipgate`, the top-level
  trigger has `run_shipgate: true`, and `TRIGGER-DOCS-ONLY-NEGATIVE` is absent;
- `report.json.release_decision.decision` is `review_required`;
- `verifier.json.merge_verdict` is `human_review_required`;
- `can_merge_without_human` is `false`.

Agents Shipgate makes no natural-language judgment that the new prompt is
malicious. The deterministic claim is narrower: a protected prompt surface
changed, so release metadata cannot make the change disappear as noise and a
human must review it. Static review does not prove runtime behavior or replace
extension signing, credential controls, or release provenance.
