# Host-config precision harness

A scripted measurement of whether `shipgate diff` names the right host-capability changes on real merged pull requests (#659). It measures the comparison on a frozen population of public repositories. It does not measure adoption, a person's comprehension, or anything about the repositories listed, and it implies no endorsement by any of them.

## What counts

Each case is one merged PR that changed one host-configuration file. The comparison runs from the merge commit against its first parent.

| Metric | Definition | Bar (#659) |
|---|---|---|
| Row precision | Rows on the case file that name an expected change, over all rows on the file | ≥ 0.95 |
| Widening recall | Expected changes that widen authority and are named by a row, over all expected widenings | ≥ 0.90 |
| Benign zero-row rate | Supported cases whose file made no capability change and that produced no row on it, over such cases | ≥ 95% |

An incomparable, failed, timed-out or unparsed comparison names nothing: it counts as missed for recall and never raises precision or the benign rate. Workflow files are scored at file level, because the engine renders one authority row per workflow; workflow precision is therefore weaker evidence than the other kinds'.

## The pieces

| File | Role |
|---|---|
| `select.py` → `selection.json` | Freezes the population: 50 merged public PRs, one per repository, with fixed quotas per file kind. Every candidate considered is recorded with the reason for any rejection. |
| `expected.py` | Derives each case's expected changes, with a direction, from the two file versions and the hosts' documentation, never from `agents_shipgate`. It extends `../cold-start/expected.py`. |
| `run.py` | The live driver. Per case it fetches the merge commit and its parent, runs `shipgate diff --workspace <checkout> --base <parent> --json`, and, for an incomparable comparison, `audit --host --json` at both commits so the refusal can be mapped to its cause. |
| `score.py` | Scores runs against the expectations and writes the CSV. It reuses `../cold-start/score.py`'s row matcher. |
| `vendor.py` → `cases/` | Stores each case's two file versions, redacted, with their source links, for offline replay. |
| `replay.py` | Rebuilds a vendored case offline and scores it; `tests/test_host_config_replay.py` runs it in CI. |
| `results/` | One directory per candidate run, named by date and candidate. |

## Boundaries

- **Selection rule.** `selection.json` records it: GitHub code search per kind, exact path, public, not a fork or archived, one PR per repository, the newest commit touching the path in the 365 days before selection that belongs to a merged PR. For workflows, the PR's patch must add or remove a `permissions` line or a scope level. The population is not re-chosen after a run, and refused cases stay in the denominator.
- **Expected changes cover the selected file only.** Rows on other files the PR touched are not scored.
- **Coding-agent co-authorship** is recorded as metadata, never as a selection or risk signal.

## Replaying, and what replay does not measure

`tests/test_host_config_replay.py` rebuilds each vendored case as a two-commit repository that holds **only the selected file**, runs the same comparison offline, and fails whenever a case's outcome differs from its committed `replay.json`. It also re-scores the run of record, so the published rates stay reproducible from `runs.json`.

Replay isolates one question: is the comparison of that file correct? It deliberately does not reproduce reach. Most live refusals came from something *else* in the repository, such as a symlink or a skill's frontmatter, and none of that is vendored. The population rates come only from `run.py`, and each `results/` README maps every refusal to its cause.

To measure a candidate live, pass absolute paths:

```bash
python benchmark/host-config/run.py benchmark/host-config/selection.json ./agents_shipgate-<version>-py3-none-any.whl /tmp/host-config benchmark/host-config/results/<date>-<candidate>
python benchmark/host-config/score.py benchmark/host-config/results/<date>-<candidate>/runs.json benchmark/host-config/results/<date>-<candidate>/scores.csv
```

After an engine change that moves a replayed outcome, re-record in the same change:

```bash
python benchmark/host-config/replay.py --record
```
