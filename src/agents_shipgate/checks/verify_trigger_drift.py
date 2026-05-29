"""SHIP-VERIFY-TRIGGER-CATALOG-DRIFT (§5.1 Tier B).

The trigger catalog (``docs/triggers.json`` / ``.agents-shipgate``
trigger config) decides WHEN Shipgate runs on a diff. Editing it can
silently carve out paths so the gate stops firing on exactly the changes
that matter — a reward hack one level up from suppressing findings.

Fires when, in verify mode, a trigger-catalog file appears in the PR's
changed files. Deterministic changed-file membership; routes to human
review (medium) so a human confirms the trigger change does not create a
gate-evasion hole. Detectable with or without a base snapshot.
"""

from __future__ import annotations

from agents_shipgate.checks._verify_common import (
    changed_files,
    touched,
    verification_active,
    verify_finding,
)
from agents_shipgate.core.context import ScanContext
from agents_shipgate.schemas.report import Finding

CHECK_ID = "SHIP-VERIFY-TRIGGER-CATALOG-DRIFT"

_TRIGGER_CATALOG_SURFACES = (
    "**/docs/triggers.json",
    "**/.agents-shipgate/triggers.json",
    "**/.agents-shipgate/triggers*.json",
)


def run(context: ScanContext) -> list[Finding]:
    if not verification_active(context):
        return []
    hit = touched(_TRIGGER_CATALOG_SURFACES, changed_files(context))
    if not hit:
        return []
    return [
        verify_finding(
            context,
            check_id=CHECK_ID,
            title="Trigger catalog changed",
            severity="medium",
            evidence={
                "kind": "trigger_catalog_drift",
                "changed_trigger_files": hit,
            },
            recommendation=(
                "This PR changes the trigger catalog that decides when "
                "Shipgate runs. A human must confirm the change does not "
                "create a path that evades the release gate."
            ),
        )
    ]
