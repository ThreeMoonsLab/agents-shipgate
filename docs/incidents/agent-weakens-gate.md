# Incident shape: an agent weakens its own gate

Public reports show why a merge gate cannot rely only on instructions telling
the governed agent to preserve it. In
[Claude Code issue #40117](https://github.com/anthropics/claude-code/issues/40117),
the reporter documented commits made while hooks were bypassed despite explicit
project instructions; the report was closed as `not_planned`. A separate,
currently unanswered
[GitHub community discussion](https://github.com/orgs/community/discussions/187679)
records users encountering a restriction on agent edits under
`.github/agents/`. Participants describe the self-modification trust boundary,
but GitHub has not published an official rationale in that thread.

This fixture does not reproduce either product or its implementation. It uses
the existing `samples/agent_weakens_gate` repository: the base contains a clean
read-only docs agent and the Agents Shipgate workflow; the synthetic head
deletes only that workflow.

Replay it from the v0.18.0 release:

```bash
uvx agents-shipgate@0.18.0 fixture run agent_weakens_gate
```

Before v0.18.0 is published, use
`./shipgate fixture run agent_weakens_gate` from this checkout.

Current engine output:

- `SHIP-VERIFY-CI-GATE-REMOVED` is critical and suppression-immune.
- `report.json.release_decision.decision` is `blocked`.
- `verifier.json.merge_verdict` is `blocked`.
- `can_merge_without_human` is `false`.

The result establishes a static fact about the PR diff: the opted-in gate file
was removed. It does not prove why the author removed it, whether an agent
authored the change, or what would happen at runtime.
