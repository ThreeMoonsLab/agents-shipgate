# Direction replay for #820

This offline replay compares main `1b1f6463e5ae48f447f9427959f797d5cc48f754`
with the #820 candidate, using the same corrected oracle and scorer for both.
The JSON records in [results/2026-09-27-direction-820](results/2026-09-27-direction-820/)
bind each engine's distribution digest and retain every case's counts.

| Measure | Before | After |
| --- | ---: | ---: |
| Frozen population cases | 50 | 50 |
| Scored / unclassified / incomparable cases | 45 / 5 / 0 | 45 / 5 / 0 |
| Rows | 84 | 84 |
| Marked rows, all cases | 69 | 61 |
| Marked rows, scored cases | 65 | 57 |
| False marked rows, scored cases | 8 | 0 |
| Expected widenings found by marked rows | 59 / 60 | 59 / 60 |
| Control cases / rows | 11 / 15 | 11 / 15 |
| False marked control rows | 6 | 0 |
| Expected control widenings found | 5 / 5 | 5 / 5 |

The six negative controls are the issue's hook guard, MCP read-only argument,
plugin disablement, newly disabled plugin, permission-mode tightening and
sandbox enablement. Five positive controls reverse enablement, bypass mode and
sandbox direction, and add a hook and an MCP server. Every edit remains visible.
The frozen population's one missed widening (`Krilliac__DuetOS__pr229`)
remains a limitation, not a new success claim. Unclassified cases receive no credit as correct negatives.

`direction_counts` counts a marked row as false when it matches no expected
widening. Recall requires an actual marked row; merely naming the change is
insufficient. It reuses the existing row matcher, including its weaker
file-level workflow attribution. Historical row-presence metrics are unchanged.
These figures measure selected-file replay, not live repository reach or all
possible false positives. The oracle was amended after engine-output exposure,
by the same coding agent; there is no independent human labeling or approval.

The oracle now distinguishes plugin booleans, sandbox enablement/disablement,
and Codex approval from sandbox authority. The myplanet recorded replay changes
from two expected widenings to one for that oracle correction; its two rows
remain. Other historical runs are preserved.

Reproduce with the candidate checkout's harness and either engine source:

```sh
PYTHONPATH=/path/to/base/src python benchmark/host-config/direction_replay.py /tmp/before.json
PYTHONPATH=/path/to/candidate/src python benchmark/host-config/direction_replay.py /tmp/after.json
```

Both source trees must include their bundled data (samples, policies, docs/checks,
adoption kits and other package force-includes), because the engine digest covers
those inputs too. The runner rejects an engine whose digest changes during replay.
The base and candidate use this checkout's oracle, frozen cases and eleven controls.
See [STABILITY](../../STABILITY.md#grant-direction-820) for the documented rules and
host references. No live MCP servers, hooks or agents are executed.
