# Review an added remote MCP server

Constructed configuration review example. Agents Shipgate. The deterministic merge gate for AI-generated agent capability changes.

First task: compare this declaration change against its original base and decide whether it is intended.

Evidence: released wheel 1.1.0, contract 40; base `f68ee4d7d46650ea13cfd3448fd65aca08ffcac0`, head `fbb92d6a3891a5c8e16ee02c3be6facdc739ca11`. The run reads the clean working tree at that head. This is static declaration evidence, not runtime-effective authority or incident prevention.

[Base input](base.txt) · [Head input](head.txt) · [Git diff](change.diff) · [Captured text](output.txt) · [Captured JSON](output.json) · [Exact history](history.git-export) · [Package provenance](../provenance.json)

## Exact configuration change

```diff
diff --git a/.mcp.json b/.mcp.json
index 8c3bf0d..9fa91e1 100644
--- a/.mcp.json
+++ b/.mcp.json
@@ -1 +1 @@
-{"mcpServers":{}}
+{"mcpServers":{"docs":{"type":"http","url":"https://docs.example.invalid/mcp"}}}
```

## Captured output (unmodified)

```text
Agent capability diff  f68ee4d7d46650ea13cfd3448fd65aca08ffcac0 (f68ee4d7) -> working tree

⚠ high  added  claude-code .mcp.json
              docs (url https://docs.example.invalid/<redacted-path>)
              an MCP tool surface the agent may call has changed

1 change(s), 1 widening what the agent may do (⚠).
Static configuration only: this is what the files permit, not what the agent did. No verdict is implied.

What this run established:
  only sources this entry read or tried to read are listed, so this is not the whole change: a changed file it does not read is absent
  .mcp.json (claude-code): compared; 1 row

Review question: Does the team intend this declared capability change?
Compared: base f68ee4d7 → working tree at HEAD fbb92d6a, agents-shipgate 1.1.0.
Reproduce in that working tree: agents-shipgate diff --base f68ee4d7d46650ea13cfd3448fd65aca08ffcac0
```

## Review question

Should this repository declare a connection to this MCP endpoint? The owner can explain intentional scope in the existing PR discussion or choose a revised declaration and compare it again. Neither this question nor a PR note grants approval or merge authority.

## Evidence limit

The endpoint is declared in configuration. No server is contacted; tools, credentials and remote behavior are unknown. The example.invalid address is deliberately non-operational. Coverage is limited to the supported sources named by this run; absence of a row is not a repository-wide no-change claim. A [malformed-source control](../shell/malformed-output.txt) is included in the asset bundle and must not be confused with a covered no-change answer.

## Replay

These captures use released 1.1.0, contract 40; another build may interpret or render the same inputs differently. At this example's head, with the base commit available locally:

```sh
agents-shipgate diff --base f68ee4d7d46650ea13cfd3448fd65aca08ffcac0
```

For your own checked-out PR branch, use its available original base ref:

```sh
agents-shipgate diff --base <available-original-base-ref>
```

The CLI must already be installed. No manifest, authored policy, saved baseline, skill, CI or account is needed for this task. Git history must be available; the engine never fetches it. The published wheel was freshly installed into a temporary target with existing environment dependencies, not a clean dependency-closure trial. See [package provenance](../provenance.json) for the wheel hash and imported module path.

Prepared asset only. No publication, recruited attempt, user outcome or independent human approval is claimed.
