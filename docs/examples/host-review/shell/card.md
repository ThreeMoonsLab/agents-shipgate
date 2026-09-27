# Review a Claude Code allowlist widening

Constructed configuration review example. Agents Shipgate. The deterministic merge gate for AI-generated agent capability changes.

First task: compare this declaration change against its original base and decide whether it is intended.

Evidence: released wheel 1.1.0, contract 40; base `7a401ae9c08d66dc72755890fbd3535ddcb50918`, head `90b944c6de46ebc25ba3361d898efaabd6b1dd8a`. The run reads the clean working tree at that head. This is static declaration evidence, not runtime-effective authority or incident prevention.

[Base input](base.txt) · [Head input](head.txt) · [Git diff](change.diff) · [Captured text](output.txt) · [Captured JSON](output.json) · [Exact history](history.git-export) · [Package provenance](../provenance.json)

## Exact configuration change

```diff
diff --git a/.claude/settings.json b/.claude/settings.json
index f1d7719..583de56 100644
--- a/.claude/settings.json
+++ b/.claude/settings.json
@@ -1 +1 @@
-{"permissions":{"allow":["Bash(npm test *)"]}}
+{"permissions":{"allow":["Bash(npm *)"]}}
```

## Captured output (unmodified)

```text
Agent capability diff  7a401ae9c08d66dc72755890fbd3535ddcb50918 (7a401ae9) -> working tree

⚠ medium  widened  claude-code .claude/settings.json
                  allow: Bash(npm test *) → allow: Bash(npm *)
                  the new rule matches everything the old rule matched; runs without a prompt

1 change(s) from 2 rows, 1 widening what the agent may do (⚠).
Static configuration only: this is what the files permit, not what the agent did. No verdict is implied.

What this run established:
  only sources this entry read or tried to read are listed, so this is not the whole change: a changed file it does not read is absent
  .claude/settings.json (claude-code): compared; 2 rows

Review question: Does the team intend this declared capability change (from 2 rows)?
Compared: base 7a401ae9 → working tree at HEAD 90b944c6, agents-shipgate 1.1.0.
Reproduce in that working tree: agents-shipgate diff --base 7a401ae9c08d66dc72755890fbd3535ddcb50918
```

## Review question

Does this task need npm commands beyond tests? The owner can explain intentional scope in the existing PR discussion or choose a revised declaration and compare it again. Neither this question nor a PR note grants approval or merge authority.

## Evidence limit

The declared allow matcher broadens. Other permission layers and runtime behavior are not established. Coverage is limited to the supported sources named by this run; absence of a row is not a repository-wide no-change claim. A [malformed-source control](../shell/malformed-output.txt) is included in the asset bundle and must not be confused with a covered no-change answer.

## Replay

These captures use released 1.1.0, contract 40; another build may interpret or render the same inputs differently. At this example's head, with the base commit available locally:

```sh
agents-shipgate diff --base 7a401ae9c08d66dc72755890fbd3535ddcb50918
```

For your own checked-out PR branch, use its available original base ref:

```sh
agents-shipgate diff --base <available-original-base-ref>
```

The CLI must already be installed. No manifest, authored policy, saved baseline, skill, CI or account is needed for this task. Git history must be available; the engine never fetches it. The published wheel was freshly installed into a temporary target with existing environment dependencies, not a clean dependency-closure trial. See [package provenance](../provenance.json) for the wheel hash and imported module path.

Prepared asset only. No publication, recruited attempt, user outcome or independent human approval is claimed.
