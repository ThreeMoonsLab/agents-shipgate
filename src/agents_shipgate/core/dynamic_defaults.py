from __future__ import annotations

from agents_shipgate.inputs.policy_packs import LoadedPolicyPacks
from agents_shipgate.schemas.checks import CheckMetadata
from agents_shipgate.schemas.common import Severity
from agents_shipgate.schemas.manifest import AgentsShipgateManifest

_SEVERITY_RANK_FOR_MAX = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "info": 4,
}


def strongest_action_policy_severity(
    manifest: AgentsShipgateManifest,
) -> Severity | None:
    """Return the strongest severity declared across action-surface policies."""
    policies = manifest.action_surface.policies
    if not policies:
        return None
    return min(
        (policy.severity for policy in policies),
        key=lambda severity: _SEVERITY_RANK_FOR_MAX[severity],
    )


def dynamic_check_defaults(
    manifest: AgentsShipgateManifest,
    policy_packs: LoadedPolicyPacks,
    *,
    catalog: list[CheckMetadata],
) -> dict[str, Severity]:
    """Map dynamic-severity check IDs to manifest-effective defaults.

    Always returns an entry for every catalog check with
    ``dynamic_default=True``. The seed step keeps the severity override
    resolver's consistency guard from false-positive failures on
    manifests that override a dynamic-default check without declaring
    the corresponding manifest section. Overlay branches then replace
    the static seed with the manifest-effective value where present.
    """
    defaults: dict[str, Severity] = {}
    for entry in catalog:
        if entry.dynamic_default:
            defaults[entry.id] = entry.default_severity

    action_policy_max = strongest_action_policy_severity(manifest)
    if action_policy_max is not None:
        defaults["SHIP-ACTION-POLICY-VIOLATION"] = action_policy_max

    for resolved in policy_packs.rules:
        defaults[resolved.rule.id] = resolved.rule.severity
    return defaults
