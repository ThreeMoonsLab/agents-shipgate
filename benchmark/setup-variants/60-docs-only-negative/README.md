# 60-docs-only-negative

A composable negative overlay. It appends a docs-only footnote to the
archetype's `README.md`. Use it together with the `04-docs-only-negative`
prompt to test that the agent does not propose Shipgate on a pure docs PR.

This overlay composes on top of variants `00`–`30` and `50`. It is **not**
composed with `40-shipgate-yaml` in the v1 matrix because
[`docs/triggers.json`](../../../docs/triggers.json) defines `force_run` when
`shipgate.yaml` is present — the agent should reasonably run scan even on a
docs-only PR in an opted-in repo, which is a different behaviour from the
"un-adopted, do not propose Shipgate" criterion.

In `scorer/rules.py` the `discovers_relevance` detector inverts its expected
value when the cell has both `negative_overlay == 60-docs-only-negative` and
`variant ∈ {00, 10, 20, 30, 50}`.
