# Agents Shipgate Codex Plugin Terms

Agents Shipgate is provided under the Apache-2.0 license in this repository.
The Codex plugin package is a skill-only companion that teaches Codex how to
install, run, and summarize the local `agents-shipgate` CLI workflows for
Tool-Use Readiness review.

Using the Codex plugin requires a local `agents-shipgate` CLI installation. The
plugin does not bundle the scanner binary, provide a hosted service, connect to
MCP servers, or execute agent tools. Users remain responsible for the commands
they ask Codex to run and for reviewing any merge, release, suppression,
baseline, waiver, action-effect, action-authority, approval, confirmation,
idempotency, or policy decision.

Agents Shipgate reports are static analysis artifacts. They can help identify
declared tool-use release risks, but they do not prove runtime behavior, model
correctness, prompt robustness, or adversarial resistance. Treat blocked or
human-review verdicts as release signals that require the appropriate code,
configuration, or human-review follow-up.

Privacy and redaction behavior is documented in
[`docs/privacy.md`](privacy.md). Security disclosures should follow the
repository's [`SECURITY.md`](../SECURITY.md).
