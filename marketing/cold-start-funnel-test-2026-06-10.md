# Cold-Start Funnel Test — 2026-06-10

Method: simulate a cold user on a clean temp dir, installing only from
public channels (PyPI via `uvx`), walking the funnel in
`marketing/gtm-strategy.md` § 5. This is the internal dry run; the launch
gate still requires two engineers outside the project to repeat it
(`marketing/launch-kit.md`).

## Results

| Funnel step | Result | Measurement |
|---|---|---|
| Install (`uvx --refresh agents-shipgate --version`) | ✅ PASS | 3.3 s, resolves 0.12.0 (current — the stale-pipx-0.8.0 issue from the 2026-06-01 pilot is gone) |
| Demo verdict (`uvx agents-shipgate fixture run ai_generated_refund_pr`) | ✅ PASS | 1.3 s to `Merge verdict: blocked`; README's "5-minute demo" promise is actually ~5 seconds |
| PR-comment artifact quality | ✅ PASS | capability table + "Required before merge" human-authority list; now also the README moneyshot source |
| `verify --preview` on a non-matching repo | ✅ PASS | clean skip with rationale and structured `next_action: none` |
| `scan` on an unconfigured repo (human mode) | ❌ FAIL → **fixed same day** | bare `Config error: No shipgate.yaml files matched`, no next step. Now prints `next: agents-shipgate detect …` + why |
| `init --write` → `scan` placeholder path | ❌ FAIL → **fixed same day** | `Input file not found: CHANGE_ME.yaml` dead end. Now routes to the placeholder fix (`next: Edit shipgate.yaml` + doctor pointer) in both human and agent mode |
| `verify` config errors (human + agent mode) | ❌ GAP → **fixed same day** | verify previously printed bare errors with no diagnostics and no agent-mode JSON; now at parity with scan (split flag-parse vs run-phase handlers) |
| `detect` on a minimal dynamic-toolkit repo | ❌ OPEN | a 1-file repo calling `client.responses.create(..., tools=build_toolkit())` yields `is_agent_project: false → "No action"` — a confident wrong answer on the exact shape the Stripe pilot hit. Tracked as the `insufficient_evidence` P1 (config-bound removal / dynamic-factory detection). **Launch remains gated on this.** |

Fixes shipped 2026-06-10 (this branch): `is_agent_mode()` helper;
`_echo_next_action_hint()` printing the rank-1 recovery step for humans
(suppressed in agent mode to keep the `docs/errors.json` single-JSON-line
contract); scan/doctor/verify wired; CHANGE_ME-aware InputParseError
routing; verify gains agent-mode structured errors. Regression tests added
in `tests/test_cli.py` and `tests/test_verify.py`; full suite green.

## Conclusion

The zero-install demo path is launch-quality. The "my own repo" path is the
remaining gap, and it is exactly the gap the GTM plan predicted: dynamic
tool surfaces produce either a wrong "not an agent project" answer (detect)
or weak evidence (scan). Until the P1 detection work lands, every outreach
message and doc should lead with the fixture demo, and design-partner pilots
should ask the "how are tools registered?" question (outreach kit Q5) before
the run, so `insufficient_evidence` arrives as a predicted finding, not a
disappointment.
