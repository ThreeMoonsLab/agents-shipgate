# Review a workflow token-permission change

Constructed configuration review example. Agents Shipgate. The deterministic merge gate for AI-generated agent capability changes.

First task: compare this declaration change against its original base and decide whether it is intended.

Evidence: released wheel 1.1.0, contract 40; base `54c0a9578668acbc2cc447fb65ab1de108c0679f`, head `9dda36470fcb06801b7cae9bf1dccc6ed4135a1e`. The run reads the clean working tree at that head. This is static declaration evidence, not runtime-effective authority or incident prevention.

[Base input](base.txt) · [Head input](head.txt) · [Git diff](change.diff) · [Captured text](output.txt) · [Captured JSON](output.json) · [Exact history](history.git-export) · [Package provenance](../provenance.json)

## Exact configuration change

```diff
diff --git a/.github/workflows/example.yml b/.github/workflows/example.yml
index c1aa10e..54c0fab 100644
--- a/.github/workflows/example.yml
+++ b/.github/workflows/example.yml
@@ -1,7 +1,7 @@
 name: Example
 on: [pull_request]
 permissions:
-  contents: read
+  contents: write
 jobs:
   inspect:
     runs-on: ubuntu-latest
```

## Captured output (unmodified)

```text
Agent capability diff  54c0a9578668acbc2cc447fb65ab1de108c0679f (54c0a957) -> working tree

⚠ high  widened  github .github/workflows/example.yml
                read, inspect: contents: read, on: pull_request → write, inspect: contents: write, on: pull_request
                grants write permissions to workflow jobs

1 change(s), 1 widening what the agent may do (⚠).
Static configuration only: this is what the files permit, not what the agent did. No verdict is implied.

What this run established:
  only sources this entry read or tried to read are listed, so this is not the whole change: a changed file it does not read is absent
  .github/workflows/example.yml (github): compared; 1 row

Review question: Does the team intend this declared capability change?
Compared: base 54c0a957 → working tree at HEAD 9dda3647, agents-shipgate 1.1.0.
Reproduce in that working tree: agents-shipgate diff --base 54c0a9578668acbc2cc447fb65ab1de108c0679f
```

The leading `read`/`write` is the workflow's aggregate token access; #859 tracks labelling it.

## Review question

Does this workflow need a write-capable contents token? The owner can explain intentional scope in the existing PR discussion or choose a revised declaration and compare it again. Neither this question nor a PR note grants approval or merge authority.

## Evidence limit

The workflow declares a broader token permission. Repository policy, fork restrictions and actual token use are not established. This workflow is never uploaded or executed. Coverage is limited to the supported sources named by this run; absence of a row is not a repository-wide no-change claim. A [malformed-source control](../shell/malformed-output.txt) is included in the asset bundle and must not be confused with a covered no-change answer.

## Replay

These captures use released 1.1.0, contract 40; another build may interpret or render the same inputs differently. At this example's head, with the base commit available locally:

```sh
agents-shipgate diff --base 54c0a9578668acbc2cc447fb65ab1de108c0679f
```

For your own checked-out PR branch, use its available original base ref:

```sh
agents-shipgate diff --base <available-original-base-ref>
```

The CLI must already be installed. No manifest, authored policy, saved baseline, skill, CI or account is needed for this task. Git history must be available; the engine never fetches it. The published wheel was freshly installed into a temporary target with existing environment dependencies, not a clean dependency-closure trial. See [package provenance](../provenance.json) for the wheel hash and imported module path.

Prepared asset only. No publication, recruited attempt, user outcome or independent human approval is claimed.
