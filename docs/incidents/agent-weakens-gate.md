# Incident shape: an agent weakens its own gate

Public reports show why a merge gate cannot rely only on instructions telling
the governed agent to preserve it. In
[Claude Code issue #40117](https://github.com/anthropics/claude-code/issues/40117),
the reporter documented commits made while hooks were bypassed despite explicit
project instructions. A separate
[GitHub community discussion](https://github.com/orgs/community/discussions/187679)
records users encountering a restriction on agent edits under
`.github/agents/`, with participants describing the self-modification trust
boundary.

This fixture does not reproduce either product or its implementation. It uses
the existing `samples/agent_weakens_gate` repository: the base contains a clean
read-only docs agent and the Agents Shipgate workflow; the synthetic head
deletes only that workflow.

Replay it from an installed release:

```bash
uvx agents-shipgate fixture run agent_weakens_gate
```

Current engine output:

- `SHIP-VERIFY-CI-GATE-REMOVED` is critical and suppression-immune.
- `report.json.release_decision.decision` is `blocked`.
- `verifier.json.merge_verdict` is `blocked`.
- `can_merge_without_human` is `false`.

The result establishes a static fact about the PR diff: the opted-in gate file
was removed. It does not prove why the author removed it, whether an agent
authored the change, or what would happen at runtime.
