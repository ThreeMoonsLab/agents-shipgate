# Autofix policy

Which Agents Shipgate findings are safe to apply automatically, which
need human review, and how the per-finding metadata in `report.json`
maps to `apply-patches --confidence` flag semantics.

> **Audience.** AI coding agents driving the canonical 4-call flow
> (see [`agent-recipes.md`](agent-recipes.md)) and CI integrators
> deciding what to gate on.

---

## The four classes

Every active finding falls into one of four classes. The class is
encoded by the `autofix_safe` and `requires_human_review` fields on
each Finding, plus the `kind` and `confidence` fields on each
attached Patch.

| Class | Finding fields | Patch shape | v0.7 examples |
|---|---|---|---|
| **Safe auto-fix** | `autofix_safe: true`, `requires_human_review: false` | All patches non-manual AND high confidence | The 3 stale-manifest removals (`SHIP-MANIFEST-STALE-{SUPPRESSION,POLICY,RISK-OVERRIDE}`) when the match is unique |
| **Medium-confidence config fix** | `autofix_safe: false`, `requires_human_review: true`, `suggested_patch_kind: append_pointer/set_pointer` | Non-manual patch but at `medium` confidence | `SHIP-AUTH-SCOPE-COVERAGE-MISSING` scope appends |
| **Manual source/policy fix** | `autofix_safe: false`, `requires_human_review: true`, `suggested_patch_kind: manual` | `ManualPatch` with curated `instructions` | All other ~30 active checks (documentation, schema bounds, owner gaps, ADK/LangChain/CrewAI metadata, …) |
| **Never auto-fix** | `autofix_safe: false`, `requires_human_review: true`, `suggested_patch_kind: manual` | `ManualPatch` with explicit anti-pattern language | `SHIP-API-TRACE-{APPROVAL,CONFIRMATION}-MISSING` (flipping the trace patches the *evidence*, not the agent's runtime gate) |

Class four is a deliberate subset of class three — the distinction is
that an agent must NEVER attempt to "auto-fix" a trace finding by
editing the trace recording, even if the user asks. The
`ManualPatch.instructions` for these checks spell out the
anti-pattern in prose so even a curious operator gets the message.

---

## Catalog vs. Finding (the dual-source contract)

Two sources describe per-check remediation policy, and they answer
different questions:

| Source | Endpoint | What it answers |
|---|---|---|
| **CheckMetadata** | `agents-shipgate list-checks --json`, `agents-shipgate explain <ID> --json`, `docs/checks.json` | What an agent should *assume* when it has only the catalog and no scan output. Conservative across the board. |
| **Finding** | `agents-shipgate-reports/report.json` (per-finding) | What this *specific* instance produced. Can be more permissive than the catalog when the generator emitted clean high-confidence patches. |

**Catalog `autofix_safe` and `requires_human_review` describe the
worst-case per-check outcome.** A check whose generator USUALLY emits
a safe non-manual patch but falls back to `ManualPatch` in edge
cases (e.g. ambiguous duplicate matches in the stale-manifest
generators) keeps the safe-closed defaults at the catalog level. The
per-Finding fields tell the truth for that instance.

`suggested_patch_kind` at the catalog level is **informational** —
it documents the kind the generator *targets* when conditions are
clean, not what the report carries. An agent that sees
`suggested_patch_kind: "remove_pointer"` in `list-checks --json`
should still consult `Finding.patches` (or the per-Finding
`suggested_patch_kind`) to know whether this particular instance
actually produced one.

When in doubt, **trust the per-Finding fields over the catalog**
for any specific finding. The catalog is for static planning
("which check IDs *might* yield safe fixes"); the report is for
acting on a specific scan.

---

## Strict derivation rule

When a scan runs with `--suggest-patches`, every active finding
gets one or more attached patches and the four per-Finding fields
are derived from those patches with this rule:

```text
autofix_safe = True iff EVERY patch is non-manual AND has confidence == "high"
```

That is: a single `ManualPatch` mixed in, or a single `medium`/`low`
confidence patch mixed in, drops the entire finding to safe-closed.
The earlier "at least one safe patch wins" rule was unsafe — it
would have marked a `[high_remove, manual]` combination
auto-fixable while a ManualPatch still required review.

`suggested_patch_kind` is the kind of the **first non-manual patch**
even when ManualPatches are also present. (If ALL patches are
manual: `"manual"`. If the patches list is empty: `"none"`.)

`requires_human_review` is always the inverse of `autofix_safe`.

`docs_url` always comes from `CheckMetadata.docs_url`. Patches
don't carry per-instance documentation URLs.

### Three patch states

| `Finding.patches` | Source of derived fields |
|---|---|
| `None` (scan ran without `--suggest-patches`) | CheckMetadata, with safe-closed fallback for unknown check IDs |
| `[]` (scan ran WITH `--suggest-patches` but generator emitted nothing) | Safe-closed shape, `suggested_patch_kind: "none"`. Does NOT fall back to catalog — the report carries no patches, so reporting a catalog-level kind would mislead. |
| Non-empty | Strict derivation rule above |

### Unknown check IDs (policy packs and third-party plugins)

A finding whose `check_id` isn't in the loaded catalog (a policy
pack rule, a third-party plugin emitted while plugins are disabled)
gets the safe-closed fallback when patches are absent:

```text
autofix_safe: false
requires_human_review: true
suggested_patch_kind: "manual"
docs_url: null
```

The fallback only applies when patches are absent. A high-confidence
non-manual patch from a policy pack still derives correctly.

---

## How `apply-patches --confidence` filters

`apply-patches` reads the report, filters patches by `--confidence`
and `--kinds`, and applies the survivors. Default flags:

```bash
agents-shipgate apply-patches \
    --from agents-shipgate-reports/report.json \
    --confidence high \
    --kinds set_pointer,append_pointer,remove_pointer \
    --apply
```

| Flag | Default | What it accepts |
|---|---|---|
| `--confidence` | `high` | Minimum patch confidence. Patches below this are skipped. |
| `--kinds` | `set_pointer,append_pointer,remove_pointer` | Patch kinds to include. ManualPatch is filtered out unconditionally — even with `--kinds manual`. |
| `--apply` | (off) | Without this, dry-run only. Always preview before mutating. |

So in v0.7 with the default flags:

- The 3 stale-manifest removals (when unambiguous) auto-apply.
- `SHIP-AUTH-SCOPE-COVERAGE-MISSING` scope appends are **skipped**
  (medium confidence). Pass `--confidence medium` to opt in — but
  read the appended scopes before merging, since adding scopes can
  encode policy choices.
- Trace approval/confirmation findings are **never** applied —
  ManualPatch is filtered out.
- Everything else with a ManualPatch is **never** applied.

`apply-patches` enforces a **containment check**: every patch's
`target_file` must resolve under `report.manifest_dir`. Anything
outside aborts with exit code 5 before any SHA verification.

---

## Decision tree for agents

When walking `findings[]` from a `--suggest-patches` report:

```text
for finding in active_findings:
    if finding.suggested_patch_kind == "manual":
        # Manual source/policy fix or never-auto-fix.
        # Read finding.patches[0].instructions and surface to user.
        # Do NOT attempt to auto-edit, especially for trace findings.
        surface_to_user(finding)
        continue

    if finding.suggested_patch_kind == "none":
        # Scan ran with --suggest-patches but the generator emitted
        # nothing for this finding (empty patches list — see "Three
        # patch states" above). There's nothing to apply via
        # apply-patches at any confidence level. Surface for human
        # triage instead.
        surface_to_user(finding)
        continue

    if finding.autofix_safe is True:
        # Safe to include in the next `apply-patches --confidence high`.
        plan_to_apply(finding)
        continue

    # Medium-confidence non-manual patch (e.g. scope coverage).
    # Surface as "review and run apply-patches --confidence medium"
    # but do not auto-apply on the high-confidence path.
    surface_for_medium_review(finding)
```

After running `apply-patches --apply`, re-run `scan` to confirm the
fixed findings are gone. The `run_id` will only change if the
manifest or tool surface actually changed — patches are excluded
from the hash so toggling `--suggest-patches` doesn't shift it.

---

## See also

- [`agent-autofix-boundary.md`](agent-autofix-boundary.md) — the
  *behavioral* counterpart to this *mechanical* page. What an agent may
  assert in a PR comment or review summary, beyond which patches
  `apply-patches` will run.
- [`agent-recipes.md`](agent-recipes.md) — copy-pasteable AI-agent
  workflows, including the soft-stop rule for `detect`.
- [`report-reading-for-agents.md`](report-reading-for-agents.md) —
  reader's primer for `report.json`.
- [`checks.md`](checks.md) — full check catalog with rationale.
- [`minimal-real-configs.md`](minimal-real-configs.md) — per-framework
  minimal manifests to build from.
- [`report-schema.v0.12.json`](report-schema.v0.12.json) — current JSON
  Schema for `report.json`.
- [`AGENTS.md`](../AGENTS.md) — top-level agent instructions, install,
  trigger table.
- [`STABILITY.md`](../STABILITY.md) — what won't break across `0.x`.
