from __future__ import annotations

from typing import Any

from agents_shipgate.schemas.manifest._artifacts import ArtifactPathConfig


class PolicyPackConfig(ArtifactPathConfig):
    id: str | None = None
    source: str | None = None
    # v0.2 (org distribution): optional content pin. When set, the pack
    # file's SHA-256 must match or the scan fails with a config error
    # (optional packs degrade to a warning). Pin shared/org packs that are
    # vendored or synced from another repo so a tampered pack cannot
    # silently change the release policy.
    sha256: str | None = None


def _parse_policy_pack_entries(value: Any) -> list[PolicyPackConfig]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError("policy_packs must be a list")
    entries: list[PolicyPackConfig] = []
    for item in value:
        if isinstance(item, PolicyPackConfig):
            entries.append(item)
        elif isinstance(item, str):
            entries.append(PolicyPackConfig(path=item))
        elif isinstance(item, dict):
            entries.append(PolicyPackConfig.model_validate(item))
        else:
            raise TypeError("policy_packs entries must be strings or objects")
    return entries
