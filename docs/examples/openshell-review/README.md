# OpenShell document-review acceptance — #942

This is a constructed local example, pinned to OpenShell v0.1.2 and policy
schema 1. It changes the selected `worker-policy.yaml` REST endpoint from
`enforcement: enforce` to `enforcement: audit`. That restores audit-only
request restrictions and widens the supported declared permission relation.
No application, OpenShell process, gateway or endpoint was executed. The
application manifest and tool export are copied unchanged from the existing
reviewed `samples/clean_read_only_agent` fixture.

[The patch](change.diff), [exact Git history](history.git-export) and
[captured evidence](acceptance.json) identify the source and result. The capture
records the engine distribution digest, not merely its package version:
an unreleased source checkout can report the same version as a released wheel.
These fixture identities are historical evidence and grant no authority in
another workspace. A replay generates its own receipt and current control.

## Reproduce from the exact history

Import the data stream into an empty temporary directory; it is Git data,
not a script. Use this repository's absolute `shipgate` launcher path when
validating the development source. For an installed build, use
`agents-shipgate` and record that build's own engine identity.

```sh
git init
git fast-import < /absolute/path/to/history.git-export
git checkout main
/absolute/path/to/shipgate audit --host --workspace . --save-baseline --baseline-file /tmp/openshell-base.json --json
git checkout audit-only
/absolute/path/to/shipgate audit --host --workspace . --drift --baseline-file /tmp/openshell-base.json --json
/absolute/path/to/shipgate diff --workspace . --base main --json
/absolute/path/to/shipgate check --agent codex --workspace . --base main --head HEAD --format agent-boundary-json
/absolute/path/to/shipgate check --agent claude-code --workspace . --base main --head HEAD --format agent-boundary-json
/absolute/path/to/shipgate check --agent cursor --workspace . --base main --head HEAD --format agent-boundary-json
/absolute/path/to/shipgate verify --workspace . --config shipgate.yaml --base main --head HEAD --json
/absolute/path/to/shipgate agent control --workspace . --reports-dir agents-shipgate-reports
```

Audit drift names the expansion; `diff` gives the before/after row and no
verdict. Each caller's boundary result reports the same policy widening.
Configured verification produces `review_required` and current control is
`review_publishable`: commit, push and PR updates are allowed, while merge
and reporting completion remain denied. The receipt binds the fixture's
committed base/head and evaluated source. Changing an input requires a fresh
verification identity. Neither advisory exit zero nor this captured receipt
approves a future change.

Without `shipgate.yaml`, verification follows the existing advisory host
comparison route and establishes no application release verdict. It requires
no invented purpose, effect, authority or agent-binding declarations.

## Acceptance matrix

| Criterion | Source evidence |
| --- | --- |
| Explicit selected inputs, typed facts and v0.1.2/schema 1 limits | `test_openshell_inventory.py`; supported-version documentation |
| Enforced restrictions become audit-only, with exact source and route | This committed fixture history and capture; `test_audit_diff_check_and_configured_verifier_agree[widened]` |
| Equivalent/narrowed changes have no false expansion | The equivalent/narrowed cases of that same CLI parity test |
| Reference changes retain every established expansion | `test_link_retarget_keeps_reference_review_and_every_supported_expansion`: linked and independent policy changes, all three callers, configured verification |
| Every rejected policy in the inventory conformance corpus denies completion | Shared `REJECTED_POLICIES`, exercised by `test_conformance_rejections_deny_boundary_and_configured_completion`; missing-policy case is separate |
| Audit drift, verdict-free diff, check and configured verification agree | `test_audit_diff_check_and_configured_verifier_agree`; existing preview/manifest-free parity tests |
| Policy, selecting-reference and link edits invalidate current evidence | `test_openshell_inputs.py`: ignored policy edits, identical-byte selection changes and link retargets in configured/manifest-free workspaces |
| Committed policy links retain bounded snapshot support | `test_verification_git_snapshot.py`: contained chains, malformed bytes, mixed-case registrations, 7/8/9-hop boundary, unresolved and unselected links; generic refusal remains tested |
| Portable preparation retains unchanged selected dependencies | `test_portable_prepare_binds_unchanged_nested_selection_and_worker_rejects_drift`: README-only diffs bind nested registration, consumed policy and every link hop; worker replay rejects a retarget or policy edit |
| Default operations stay local/static | `test_default_routes_do_not_execute_openshell_connect_or_retrieve_credentials`: scan/audit/diff/check/preview/verify reject native execution, network connections and nonlocal Git/process calls; fixture credential is not published |
| Authored, composed and observed evidence remain distinct | `docs/openshell-support.md`; composition/native tests remain separate opt-in evidence |

The six original OpenShell test modules also exercise composition, protected
inputs, receipts and the optional native wrapper. Wrapper fixtures establish
that wrapper's contract; they do not prove upstream native compatibility or
deployed enforcement. This acceptance evidence does not publish a package,
claim runtime freshness, or establish external adoption.
