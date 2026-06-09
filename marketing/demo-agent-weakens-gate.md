# Demo Script: "The Agent Deletes the Gate" (90 seconds)

The one demo that explains the moat in a single take. Record as a
terminal screencast (asciinema or screen capture); no slides.

## Setup (before recording)

```bash
pipx install agents-shipgate   # or: uv tool install agents-shipgate
agents-shipgate --version      # >= 0.12
```

Terminal at 100×30, large font, dark theme.

## Script

**[0:00–0:15] — The premise.** (voiceover while terminal is empty)

> "Your coding agent's PR fails the release gate. What's the cheapest way
> for it to pass? Delete the gate. Here's what happens when it tries."

**[0:15–0:35] — Run the fixture.**

```bash
agents-shipgate fixture run agent_weakens_gate
```

Let the output land; it prints within a few seconds:

```
Fixture: agent_weakens_gate
Mode: verify
Merge verdict: blocked
Decision: blocked
Can merge without human: false
```

> "Shipgate built a real git history: a clean docs agent with an advisory
> Shipgate workflow, and a head commit — written by the 'agent' — that
> deletes `.github/workflows/agents-shipgate.yml`. Nothing else changed."

**[0:35–1:00] — Show why.** Open the report it printed the path to:

```bash
cat <reports-path>/pr-comment.md
```

Point at the two blockers:

- `SHIP-VERIFY-CI-GATE-REMOVED` — *Shipgate CI gate removed*
- `SHIP-CODEX-BOUNDARY-CI-GATE-REMOVED` — *the workflow no longer
  invokes the gate*

> "Both checks are suppression-immune: the manifest's `checks.ignore`
> cannot silence them, severity floors stop downgrades, and
> `can_merge_without_human` is pinned to false. The agent cannot
> approve its own boundary change."

**[1:00–1:30] — The kicker.**

> "This isn't a special case — `shipgate.yaml`, policies, baselines,
> agent instructions, `.mcp.json`, Claude Code permission rules, and
> workflow permissions are all protected surfaces. Editing any of them
> routes to a human. The cheapest reward-hack is also the most visible
> one. That's the whole point of a deterministic merge gate."

Close with the README one-liner on screen:

> **Your coding agent changed what your AI agent can do — Agents
> Shipgate tells you whether it can merge.**

## Variants

- **30-second cut**: 0:15–0:35 only, ending on `Can merge without human:
  false`.
- **Refund cut**: same structure with
  `fixture run ai_generated_refund_pr` for the capability-addition story
  (blocked on missing approval policy + idempotency evidence).
- **Live-wire cut** (advanced): run `agents-shipgate install-hooks
  --target claude-code --write` in a sandbox repo, ask Claude Code to
  "remove the Shipgate workflow," and capture the PreToolUse `ask`
  prompt appearing *before the edit happens*.
