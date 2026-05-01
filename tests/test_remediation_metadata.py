"""Population + anchor-resolution tests for v0.7 CheckMetadata fields.

Per the v0.7 plan §2:
- Every entry in `CHECK_METADATA` carries non-None `docs_url` plus the
  three remediation policy fields (`autofix_safe`,
  `requires_human_review`, `suggested_patch_kind`).
- Default policy is the safe-closed shape (`autofix_safe=False`,
  `requires_human_review=True`, `suggested_patch_kind="manual"`).

Catalog-vs-Finding contract (key safety invariant):
- The catalog-level `autofix_safe` and `requires_human_review` describe
  the *worst-case* per-check outcome. Checks whose generator USUALLY
  yields a safe non-manual patch but falls back to `ManualPatch` in
  edge cases (e.g. ambiguous duplicate matches in stale-manifest
  generators) MUST keep the safe-closed defaults at this level.
- `suggested_patch_kind` is informational — it documents the kind the
  generator *targets* when conditions are clean.
- The mirror Finding-level fields (PR 3) read the actual emitted
  patches and tell the truth for each finding instance. Agents acting
  on a specific finding should consult those, not the catalog.

Trace approval/confirmation findings stay at the safe-closed default
permanently — flipping the trace patches the evidence, not the agent.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from agents_shipgate.checks.registry import check_catalog

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECKS_MD = REPO_ROOT / "docs" / "checks.md"


def _builtin_checks():
    """Built-in checks only — exclude any third-party plugin output to
    keep the test deterministic regardless of `AGENTS_SHIPGATE_ENABLE_PLUGINS`."""
    return check_catalog(plugins_enabled=False)


def test_every_check_has_docs_url():
    for check in _builtin_checks():
        assert check.docs_url, f"{check.id} missing docs_url"
        assert check.docs_url.startswith(
            "https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/checks.md#"
        ), f"{check.id} docs_url does not point at the canonical anchor base"


def test_every_check_has_remediation_policy_fields():
    for check in _builtin_checks():
        # Pydantic defaults populate these even on un-overridden checks,
        # so the model fields must always be set.
        assert isinstance(check.autofix_safe, bool), (
            f"{check.id}.autofix_safe missing/wrong type"
        )
        assert isinstance(check.requires_human_review, bool), (
            f"{check.id}.requires_human_review missing/wrong type"
        )
        assert check.suggested_patch_kind in {
            "manual",
            "remove_pointer",
            "append_pointer",
            "set_pointer",
            "none",
        }, f"{check.id}.suggested_patch_kind out of enum"


def test_default_remediation_policy_is_safe_closed():
    """A check with no entry in `_REMEDIATION_OVERRIDES` should default
    to the safe-closed shape — manual, requires review, never
    auto-fixable."""
    documentation_check = next(
        c for c in _builtin_checks() if c.id == "SHIP-DOC-MISSING-DESCRIPTION"
    )
    assert documentation_check.autofix_safe is False
    assert documentation_check.requires_human_review is True
    assert documentation_check.suggested_patch_kind == "manual"


@pytest.mark.parametrize(
    "check_id",
    [
        "SHIP-MANIFEST-STALE-SUPPRESSION",
        "SHIP-MANIFEST-STALE-POLICY",
        "SHIP-MANIFEST-STALE-RISK-OVERRIDE",
    ],
)
def test_stale_manifest_checks_target_remove_pointer_but_stay_conservative(check_id):
    """Stale-manifest checks declare ``suggested_patch_kind="remove_pointer"``
    so agents know the generator's target shape, but the catalog-level
    ``autofix_safe`` / ``requires_human_review`` stay at the safe-closed
    default. Per ``checks/patches.py``: the generator falls back to
    ``ManualPatch`` when ≥ 2 manifest entries match the same evidence
    (ambiguous removal). The per-Finding fields (PR 3) tell the truth
    for each instance — the catalog must not over-promise."""
    check = next(c for c in _builtin_checks() if c.id == check_id)
    assert check.suggested_patch_kind == "remove_pointer"
    # Catalog stays conservative — even though most instances ARE
    # safely auto-applicable, duplicate-match edge cases hand off to
    # ManualPatch. An agent reading only `list-checks --json` must
    # default to "needs review."
    assert check.autofix_safe is False, (
        f"{check_id} catalog-level autofix_safe must NOT be True — the "
        "generator can fall back to ManualPatch on ambiguous duplicates."
    )
    assert check.requires_human_review is True


def test_scope_coverage_is_medium_append_not_default_applied():
    check = next(
        c for c in _builtin_checks() if c.id == "SHIP-AUTH-SCOPE-COVERAGE-MISSING"
    )
    assert check.suggested_patch_kind == "append_pointer"
    # Critical: must NOT be auto-applied at default --confidence high.
    # Adding scopes can encode policy choices.
    assert check.autofix_safe is False
    assert check.requires_human_review is True


@pytest.mark.parametrize(
    "check_id",
    [
        "SHIP-API-TRACE-APPROVAL-MISSING",
        "SHIP-API-TRACE-CONFIRMATION-MISSING",
    ],
)
def test_trace_findings_are_permanent_manual(check_id):
    """Per the v0.6 C6 rule: flipping `approved`/`confirmed` in a trace
    patches the *evidence*, not the agent's runtime behavior. These
    checks must always produce ManualPatch and never auto-apply."""
    check = next(c for c in _builtin_checks() if c.id == check_id)
    assert check.autofix_safe is False
    assert check.requires_human_review is True
    assert check.suggested_patch_kind == "manual"


def test_every_docs_url_anchor_resolves_in_checks_md():
    """The docs_url anchor (lower-kebab of the check ID) must match a
    real `### SHIP-...` heading in `docs/checks.md`. Fails the moment
    a check ID is renamed without updating the docs anchor — or vice
    versa.

    GitHub-flavored anchor rule: lowercase the heading text, replace
    spaces with hyphens, strip non-alphanumerics. Our headings are
    already `### SHIP-XXX-YYY`, which produce `#ship-xxx-yyy`.
    """
    text = CHECKS_MD.read_text(encoding="utf-8")
    heading_anchors: set[str] = set()
    for line in text.splitlines():
        match = re.match(r"^### (SHIP-[A-Z0-9-]+)\s*$", line)
        if match:
            heading_anchors.add(match.group(1).lower())

    for check in _builtin_checks():
        assert check.docs_url is not None
        fragment = check.docs_url.split("#", 1)[1]
        assert fragment in heading_anchors, (
            f"{check.id}.docs_url fragment '{fragment}' has no matching "
            f"`### SHIP-...` heading in docs/checks.md. Either add the "
            f"section or update the check ID."
        )


def test_list_checks_json_carries_new_fields():
    """`agents-shipgate list-checks --json` is the contract for
    downstream consumers (CI dashboards, agent prompts). Ensure the new
    keys appear in the per-check JSON dump."""
    for check in _builtin_checks():
        payload = check.model_dump(mode="json")
        for key in ("autofix_safe", "requires_human_review", "suggested_patch_kind", "docs_url"):
            assert key in payload, f"{check.id} JSON dump missing {key!r}"


def test_regenerated_checks_json_includes_new_fields():
    """`docs/checks.json` is the machine-readable catalog regenerated by
    `scripts/generate_schemas.py`. Confirm a sampled check carries the
    new fields after regeneration. Catalog `autofix_safe` stays
    conservative (False) per the catalog-vs-Finding contract."""
    import json

    catalog = json.loads((REPO_ROOT / "docs" / "checks.json").read_text(encoding="utf-8"))
    by_id = {entry["id"]: entry for entry in catalog["checks"]}
    stale = by_id["SHIP-MANIFEST-STALE-SUPPRESSION"]
    assert stale["autofix_safe"] is False
    assert stale["requires_human_review"] is True
    assert stale["suggested_patch_kind"] == "remove_pointer"
    assert stale["docs_url"].endswith("#ship-manifest-stale-suppression")
