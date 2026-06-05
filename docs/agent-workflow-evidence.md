# Agent Workflow Evidence

Agent Workflow Evidence is the local, opt-in evidence bundle for turning a real
AI-generated agent PR into replayable verifier input. It complements
`verifier.json`, `report.json`, and `attestation.json`; it does not gate and it
does not change the default static scan path.

## Boundary

Agents Shipgate does not collect this evidence automatically. A user or wrapper
may attach it after a local run, but the default `scan`, `verify`, and GitHub
Action flows still do not execute agents, call tools, connect to MCP servers,
call LLMs, make network calls, or upload telemetry.

The bundle must not record private chain-of-thought. Record observable actions,
commands, tool-call metadata, file/resource effects, verifier artifacts, and
human decisions.

## AgentTraceEvent v0.1

The event schema lives at
[`agent-trace-event-schema.v0.1.json`](agent-trace-event-schema.v0.1.json).
Every event has:

- `event_id`: stable event identifier within the bundle.
- `run_id`: stable run identifier.
- `sequence`: monotonic integer order; no wall-clock timestamp is required.
- `event_type`: one of `task`, `agent_message`, `tool_call`, `command`,
  `file_change`, `verification`, `human_decision`, or `artifact`.
- `actor`: observable actor such as `human`, `coding_agent`, `ci`, or a named
  tool wrapper.
- `summary`: reviewer-safe description of the observable event.
- `resource_effects[]`: normalized read/write/delete/execute/network/deploy
  effects when known.
- `artifact_refs[]`: local artifact paths or content-addressed references.

Optional fields may include redacted arguments, exit codes, policy checks, and
source references. Raw prompts, transcripts, and command output should be
stored as separate artifacts and referenced by hash/path rather than copied
into every event.

## Replay Bundle Shape

The bundle schema lives at
[`agent-workflow-evidence-bundle-schema.v0.1.json`](agent-workflow-evidence-bundle-schema.v0.1.json).
A replayable governance scenario should include:

- base and head refs or a sanitized patch.
- `shipgate.yaml` and local tool-source artifacts.
- `verifier.json`, `report.json`, and `pr-comment.md` from the first run.
- optional `agent-trace.jsonl` containing AgentTraceEvent records.
- optional human-review note: expected reviewer action and rationale.
- redaction note listing which paths or values were removed before sharing.

The governance benchmark consumes these bundles as evidence. It must not treat
an LLM-generated summary as proof unless the summary points back to deterministic
events, artifacts, or verifier fields.
