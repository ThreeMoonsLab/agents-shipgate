# Direction replay for #820

This offline replay compares main `1b1f6463e5ae48f447f9427959f797d5cc48f754`
with the #820 candidate, using the same oracle and scorer for both.
The JSON records in [results/2026-09-27-direction-820](results/2026-09-27-direction-820/)
bind each engine's distribution digest and retain every case's counts.

| Measure | Before | After |
| --- | ---: | ---: |
| Frozen population cases | 50 | 50 |
| Scored / unclassified / incomparable cases | 45 / 5 / 0 | 45 / 5 / 0 |
| Rows | 84 | 84 |
| Marked rows, all cases | 69 | 62 |
| Marked rows, scored cases | 65 | 58 |
| False marked rows, scored cases | 7 | 0 |
| Expected widenings found by marked rows | 60 / 61 | 60 / 61 |
| Control cases / rows | 12 / 16 | 12 / 16 |
| False marked control rows | 6 | 0 |
| Expected control widenings found | 6 / 6 | 6 / 6 |

The six negative controls are the issue's hook guard, MCP read-only argument,
plugin disablement, newly disabled plugin, permission-mode tightening and
sandbox enablement. Six positive controls reverse enablement, bypass mode and
sandbox direction, add a hook and an MCP server, and add a second handler to an
event that already had one. Every edit remains visible; an edit whose
direction is not established is named as such in its row, not marked.
The frozen population's one missed widening (`Krilliac__DuetOS__pr229`)
remains a limitation, not a new success claim. Unclassified cases receive no credit as correct negatives.

A first candidate of this change also dropped two widenings the oracle
expects: the myplanet marketplace addition (`extraKnownMarketplaces`, #720)
and the second-handler control. Scored with this oracle it found 59 / 61 and
5 / 6; the recorded candidate restores both.

`direction_counts` counts a marked row as false when it matches no expected
widening. Recall requires an actual marked row; merely naming the change is
insufficient. It reuses the existing row matcher, including its weaker
file-level workflow attribution. Historical row-presence metrics are unchanged.
These figures measure selected-file replay, not live repository reach or all
possible false positives. The oracle was amended after engine-output exposure,
by the same coding agent; there is no independent human labeling or approval.

The oracle now distinguishes plugin booleans, sandbox enablement/disablement,
Codex approval from sandbox authority, and one more handler on an existing
hook event. It still calls a marketplace addition a widening, as it did
before #820, so the myplanet recorded replay keeps its two expected widenings.
It does not classify the sandbox keys the engine newly ranks
(`excludedCommands`, `autoAllowBashIfSandboxed`, `enableWeakerNestedSandbox`,
`network.allowedDomains`, Codex `writable_roots` and `web_search`); no frozen
case contains one, so they are covered by unit tests, not by this replay.
Other historical runs are preserved.

Reproduce with the candidate checkout's harness and either engine source:

```sh
PYTHONPATH=/path/to/base/src python benchmark/host-config/direction_replay.py /tmp/before.json
PYTHONPATH=/path/to/candidate/src python benchmark/host-config/direction_replay.py /tmp/after.json
```

Both source trees must include their bundled data (samples, policies, docs/checks,
adoption kits and other package force-includes), because the engine digest covers
those inputs too. The runner rejects an engine whose digest changes during replay.
The base and candidate use this checkout's oracle, frozen cases and twelve controls.
See [STABILITY](../../STABILITY.md#grant-direction-820) for the documented rules and
host references. No live MCP servers, hooks or agents are executed.
