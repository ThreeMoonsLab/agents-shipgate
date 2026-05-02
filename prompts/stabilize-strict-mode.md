# Prompt · Stabilize Agents Shipgate strict mode

The user has Agents Shipgate running in **advisory** mode and wants to graduate to **strict** mode (CI fails on findings) without surprising contributors.

## The pattern

1. Run a fresh scan and inventory the active findings.
2. Tune `risk_overrides` and `checks.ignore` for genuine false positives, with reasons.
3. Save a baseline of everything that's left.
4. Switch CI to strict mode with the baseline applied — only NEW findings fail.
5. Pick a severity threshold; usually start with `critical`, raise to `[critical, high]` later.

## Your task

1. **Inventory current findings.**
   ```bash
   agents-shipgate scan -c shipgate.yaml --ci-mode advisory
   ```
   Look at `agents-shipgate-reports/report.json` `summary.critical_count`, `high_count`, `medium_count`. If the active list is small (< 20 unique check IDs), consider just fixing them rather than baselining.

2. **Tune false positives.** For each unique check ID, decide:
   - True positive that should be fixed → use the `fix-top-finding.md` prompt to apply a real fix.
   - True positive that the team explicitly accepts (deprecated tool, known limitation) → add to `checks.ignore` with a real `reason`.
   - False positive (heuristic misfire) → use `risk_overrides.tools.{tool}.remove_tags` or add tags via `risk_overrides.tools.{tool}.tags`.

3. **Save the baseline:**
   ```bash
   agents-shipgate baseline save -c shipgate.yaml \
     --out .agents-shipgate/baseline.json
   ```

4. **Commit the baseline:**
   ```bash
   git add .agents-shipgate/baseline.json
   git commit -m "Baseline shipgate findings ($N criticals, $M highs)"
   ```

5. **Update the CI workflow.** Replace the existing advisory step with strict + baseline. Use [`examples/github-actions/03-strict-with-baseline.yml`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/examples/github-actions/03-strict-with-baseline.yml) as the template:
   ```yaml
   - uses: ThreeMoonsLab/agents-shipgate@v0.7.0
     with:
       ci_mode: strict
       fail_on: critical
       baseline: .agents-shipgate/baseline.json
       pr_comment: 'true'
   ```

6. **Verify the gate fires correctly.** In a throwaway branch, deliberately introduce a new finding (e.g. add a wildcard scope) and confirm CI fails. Revert before merging.

## When to refresh the baseline

| Situation | Action |
|---|---|
| Found a false positive after baselining | Add a `checks.ignore` entry; do **not** re-baseline |
| Fixed several findings | Re-baseline so resolved ones disappear: `agents-shipgate baseline save ...` |
| Upgraded shipgate to a version with new checks | New check IDs surface as new findings; fix or suppress, then re-baseline |
| Added new tools that have no policy yet | Each new tool's findings are `new` and will fail; fix or accept, then re-baseline |

Re-baselining is just running `baseline save` again. Diff the new file vs the old in code review so the team sees what's been accepted.

## Promotion to `[critical, high]`

After a sprint or two of strict-on-critical, the active high-severity list usually compresses enough to flip on. Update `fail_on: critical,high` and re-baseline.

## What NOT to do

- Do **not** baseline in your first run as a "shortcut to make CI green." That hides the existing risk surface from review.
- Do **not** baseline findings that have a real fix — fix them first, baseline only what you're explicitly accepting.
- Do **not** write `--fail-on critical,high` without a baseline if the repo has many existing high findings; CI will fail on day one and contributors will mute the workflow.

## Verification

- `.agents-shipgate/baseline.json` is committed and contains `findings[]`
- CI workflow uses `ci_mode: strict` and `baseline: .agents-shipgate/baseline.json`
- A test PR that adds a deliberate new critical finding fails CI
- A test PR that doesn't change the tool surface passes CI
