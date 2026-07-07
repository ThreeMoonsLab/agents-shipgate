# Prompt · Upgrade Agents Shipgate version

Bump the agents-shipgate version pinned in CI and the development environment.

## Your task

1. **Read the changelog** for the gap between the current and target version:
   - https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/CHANGELOG.md
   - Specifically look for entries under "Breaking changes" and "New checks added".

2. **Update the pin in three places** (in this order):

   a. **`pyproject.toml`** (if the project depends on shipgate as a dev dep):
      ```toml
      [project.optional-dependencies]
      dev = ["agents-shipgate==<NEW>", ...]
      ```

   b. **CI workflow** at `.github/workflows/shipgate.yml`:
      ```yaml
      - uses: ThreeMoonsLab/agents-shipgate@v<NEW>
        with:
          shipgate_version: '<NEW>'
      ```

   c. **Pre-commit config** at `.pre-commit-config.yaml` (if present):
      ```yaml
      repos:
        - repo: https://github.com/ThreeMoonsLab/agents-shipgate
          rev: v<NEW>
      ```

3. **Run a local scan** with the new version:
   ```bash
   pipx upgrade agents-shipgate
   agents-shipgate --version    # confirm the new version is in PATH
   agents-shipgate scan -c shipgate.yaml --ci-mode advisory
   ```

4. **Compare the new finding count to the baseline.** If `report.json` shows new finding fingerprints (any with `"baseline_status": "new"`):
   - These are usually new checks added in the upgrade. Read the changelog "New checks added" section.
   - For each new check ID, decide: fix, override, or suppress (see [`triage-false-positive.md`](triage-false-positive.md)).

5. **Re-baseline if the new findings are accepted:**
   ```bash
   agents-shipgate baseline save -c shipgate.yaml \
     --out .agents-shipgate/baseline.json
   ```

6. **Commit** the version bumps + the new baseline (if regenerated) in one PR. Title: `Upgrade agents-shipgate v<OLD> → v<NEW>`.

## Stability guarantees

Per [`STABILITY.md`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/STABILITY.md), within `0.x`:

- Existing check IDs do not change names or fingerprint algorithms.
- Existing CLI flags do not break.
- The JSON report's stable fields persist.

So a `0.2.x → 0.3.x` upgrade should not silently break existing suppressions or baselines. If it does, that's a stability bug — file an issue.

## What may legitimately change

- Risk-classifier keyword sets (false-positive tuning). Use `risk_overrides` to pin specific behavior.
- New checks fire (additive). Triage with the prompts above.
- Markdown report layout (parse `report.json` instead).

## Verification

- `agents-shipgate --version` reflects the new version
- CI workflow uses the new version
- A scan completes without error
- The baseline file (if used) is up to date
