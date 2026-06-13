"""Merged-PR history miner — real-world verdict evidence for Shipgate.

Mines the merged-PR history of public agent repos, replays each
capability-touching PR through the real engine (trigger → check → init →
scan → capability diff), and emits one metadata row per PR. The rows feed
three artifacts at once:

1. **Demand receipts** — real merged PRs whose authority changes shipped
   without a gate.
2. **The labeled verdict-accuracy corpus** — rows are the candidate pool
   for human safe/unsafe labeling.
3. **Extraction coverage** — the `insufficient_evidence` rate on real
   repos, measured instead of assumed.

This is a maintainer benchmark tool: it may use the network (`gh`, `git
clone`) and subprocesses, which the scanner under ``src/agents_shipgate/``
never may. It is not part of the wheel and adds no CLI surface.
"""
