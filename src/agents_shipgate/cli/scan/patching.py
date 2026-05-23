from __future__ import annotations

from pathlib import Path


def _check_metadata_lookup(
    *, plugins_enabled: bool | None
) -> dict:
    """Build a {check_id: CheckMetadata} lookup honoring the scan's
    actual plugin setting. Used by ``annotate_remediation`` so the
    serialized report's per-finding remediation fields reflect the
    catalog the scan was run against.

    Avoids the late-stage plugin-loading hazard: by passing the lookup
    *into* annotation, we never call ``check_catalog()`` at write time
    where ``AGENTS_SHIPGATE_ENABLE_PLUGINS=1`` could re-load plugins
    even for ``--no-plugins`` scans.
    """
    from agents_shipgate.checks.registry import check_catalog

    return {
        check.id: check
        for check in check_catalog(plugins_enabled=plugins_enabled)
    }


def _attach_patches(
    findings: list,
    manifest,
    config_path: Path,
    *,
    plugins_enabled: bool | None,
) -> None:
    """Attach Patch objects to unsuppressed findings (per v0.6 plan §3).

    Suppressed findings are intentionally skipped — apply-patches must
    not mutate entries the user marked ignored.

    Coverage rule: every active finding gets ≥ 1 patch (non-manual when
    a generator exists, ManualPatch otherwise). Findings without
    --suggest-patches keep ``patches=None`` (per C4) and are filtered
    out of the JSON by ``report_json_payload``.

    Per the v0.7 PR 3 review: ``plugins_enabled`` is forwarded into
    ``check_catalog`` so the recommendation lookup honors the scan's
    explicit ``--no-plugins`` flag even when ``AGENTS_SHIPGATE_ENABLE_PLUGINS=1``
    is set in the environment. Without this, the patch-attachment path
    would load third-party plugin entry points before
    ``annotate_remediation`` ran with its plugin-safe lookup.
    """
    from agents_shipgate.checks.patches import (
        PatchContext,
        generate_patches_for_finding,
    )
    from agents_shipgate.checks.registry import check_catalog

    recommendation_lookup = {
        check.id: check.recommendation
        for check in check_catalog(plugins_enabled=plugins_enabled)
        if check.recommendation
    }
    context = PatchContext(
        manifest=manifest,
        manifest_path=config_path,
        recommendation_lookup=recommendation_lookup,
    )
    for finding in findings:
        if finding.suppressed:
            continue
        finding.patches = generate_patches_for_finding(context, finding)
