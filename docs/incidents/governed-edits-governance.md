# Incident shape: the governed edits the governance file

GitHub custom-agent files live under `.github/agents/`. A
[public GitHub community discussion](https://github.com/orgs/community/discussions/187679)
documents Copilot refusing edits in that directory and discusses the risk of an
agent changing the configuration that controls its own behavior and access.

The constructed replay starts with conservative
`.github/agents/release-reviewer.agent.md` instructions. The synthetic PR
changes only that file to remove the separate-review boundary. It does not copy
GitHub's instructions or any vendor vulnerability.

```bash
uvx agents-shipgate fixture run governed_edits_governance
```

Current engine output is intentionally an **expected-fail**:

- desired verdict: `human_review_required`;
- observed `report.json` decision: `passed`;
- observed `verifier.json` verdict: `mergeable`;
- missing path-level signal: `SHIP-VERIFY-TRUST-ROOT-TOUCHED` for
  `.github/agents/**`.

The command prints the expected and observed verdicts plus the known-gap link.
It exits successfully only while that exact gap is reproduced; if the engine
starts returning the desired review verdict, the replay exits 20 so the
fixture must be converted from expected-fail to an ordinary passing contract.

The missing repository-configurable path-level surface belongs to the
[committed capability-state RFC, #474](https://github.com/ThreeMoonsLab/agents-shipgate/issues/474).
This fixture records the gap; it does not smuggle the detection change into the
demo PR.
