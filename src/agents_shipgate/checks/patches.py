"""Patch generators per check ID for ``scan --suggest-patches``.

v0.6 ships generators that emit only manifest-target patches (per the
plan §3 + C5/C6/C7/C9/C10):

- ``RemovePointerPatch`` for the 3 stale-manifest checks (high
  confidence; auto-applied at default ``apply-patches --confidence high``).
- ``AppendPointerPatch`` for ``SHIP-AUTH-SCOPE-COVERAGE-MISSING``
  (medium confidence; requires explicit ``--confidence medium``).

Every other active (unsuppressed) finding receives a ``ManualPatch``
populated from ``CheckMetadata.recommendation`` so the JSON contract is
predictable: with ``--suggest-patches``, every finding has at least one
patch.

Stale-finding pointer rederivation (per C10): generators receive
``manifest`` and rederive the pointer by matching evidence fields. If
the manifest contains ≥ 2 entries that match the same evidence (a
duplicate), the generator emits a ``ManualPatch`` instead — auto-removal
would be ambiguous.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

from agents_shipgate.config.schema import AgentsShipgateManifest
from agents_shipgate.core.models import Finding
from agents_shipgate.core.patches import (
    AppendPointerPatch,
    ManualPatch,
    Patch,
    RemovePointerPatch,
)

GeneratorFn = Callable[
    ["PatchContext", Finding],
    list[Patch],
]


class PatchContext:
    """State shared by every generator invocation in a single scan run."""

    def __init__(
        self,
        manifest: AgentsShipgateManifest,
        manifest_path: Path,
        recommendation_lookup: dict[str, str],
    ) -> None:
        self.manifest = manifest
        # ``apply-patches`` containment check expects an absolute path.
        self.manifest_path = manifest_path.resolve()
        self.manifest_sha = _sha256(self.manifest_path)
        self.recommendation_lookup = recommendation_lookup

    @property
    def target_file(self) -> str:
        return str(self.manifest_path)


def generate_patches_for_finding(
    context: PatchContext, finding: Finding
) -> list[Patch]:
    """Return the patches a check ID's generator produces, or a single
    ``ManualPatch`` when no specific generator exists.

    Caller is responsible for filtering suppressed findings (per the v4
    plan, generators run only on unsuppressed findings).
    """
    generator = _GENERATORS.get(finding.check_id)
    if generator is not None:
        patches = generator(context, finding)
        if patches:
            return patches
        # Generator declined (e.g., ambiguous duplicate). Fall through to
        # the manual fallback so the contract holds: every active finding
        # has at least one patch.
    return [_default_manual(context, finding)]


# ---------------------------------------------------------------------------
# Stale-manifest removals (high confidence; manifest-target).
# ---------------------------------------------------------------------------


def _gen_stale_suppression(context: PatchContext, finding: Finding) -> list[Patch]:
    check_id = finding.evidence.get("check_id")
    tool = finding.evidence.get("tool")
    matches = [
        i
        for i, suppression in enumerate(context.manifest.checks.ignore)
        if suppression.check_id == check_id and suppression.tool == tool
    ]
    if len(matches) != 1:
        # Zero matches → evidence drift; ≥2 matches → ambiguous which to
        # remove. Either way, hand off to manual.
        return []
    index = matches[0]
    return [
        RemovePointerPatch(
            target_file=context.target_file,
            pointer=f"/checks/ignore/{index}",
            target_format="yaml",
            confidence="high",
            rationale=(
                "Suppression references a check_id or tool that is no "
                "longer present in the loaded surface; safe to remove."
            ),
            target_sha256=context.manifest_sha,
        )
    ]


def _gen_stale_policy(context: PatchContext, finding: Finding) -> list[Patch]:
    policy = finding.evidence.get("policy")
    tool = finding.evidence.get("tool")
    field_for = {
        "approval": "require_approval_for_tools",
        "confirmation": "require_confirmation_for_tools",
        "idempotency": "require_idempotency_for_tools",
    }
    field = field_for.get(policy)
    if field is None:
        return []
    entries = getattr(context.manifest.policies, field)
    matches = [i for i, entry in enumerate(entries) if entry.tool == tool]
    if len(matches) != 1:
        return []
    index = matches[0]
    return [
        RemovePointerPatch(
            target_file=context.target_file,
            pointer=f"/policies/{field}/{index}",
            target_format="yaml",
            confidence="high",
            rationale=(
                f"{policy} policy entry references a tool that is no longer "
                "loaded; safe to remove."
            ),
            target_sha256=context.manifest_sha,
        )
    ]


def _gen_stale_risk_override(context: PatchContext, finding: Finding) -> list[Patch]:
    tool = finding.evidence.get("tool")
    if tool not in context.manifest.risk_overrides.tools:
        return []
    # ``risk_overrides.tools`` is a dict keyed by tool name. JSON-pointer
    # tokens that contain ``/`` or ``~`` need escaping per RFC 6901.
    escaped = tool.replace("~", "~0").replace("/", "~1")
    return [
        RemovePointerPatch(
            target_file=context.target_file,
            pointer=f"/risk_overrides/tools/{escaped}",
            target_format="yaml",
            confidence="high",
            rationale=(
                "Risk override references a tool that is no longer loaded; "
                "safe to remove."
            ),
            target_sha256=context.manifest_sha,
        )
    ]


# ---------------------------------------------------------------------------
# Scope coverage (medium confidence; manifest-target).
# ---------------------------------------------------------------------------


def _gen_auth_scope_coverage(context: PatchContext, finding: Finding) -> list[Patch]:
    missing_scopes = finding.evidence.get("missing_scopes")
    if not isinstance(missing_scopes, list) or not missing_scopes:
        return []
    patches: list[Patch] = []
    for scope in missing_scopes:
        if not isinstance(scope, str):
            continue
        patches.append(
            AppendPointerPatch(
                target_file=context.target_file,
                pointer="/permissions/scopes",
                value=scope,
                target_format="yaml",
                # Medium (not high): adding scopes can encode policy
                # choices; default `apply-patches --confidence high`
                # deliberately skips this.
                confidence="medium",
                rationale=(
                    f"Tool requires scope '{scope}' which is not declared "
                    "in permissions.scopes."
                ),
                target_sha256=context.manifest_sha,
            )
        )
    return patches


# ---------------------------------------------------------------------------
# Manual fallback.
# ---------------------------------------------------------------------------


_TRACE_FLIP_PROHIBITION = (
    "This finding indicates the runtime did not enforce the policy. "
    "Implement the runtime gate; do not edit the trace recording."
)


def _default_manual(context: PatchContext, finding: Finding) -> ManualPatch:
    """Fallback ManualPatch — uses CheckMetadata.recommendation when
    available, with extra prohibition language for trace flips
    (per C6: trace approvals/confirmations stay manual permanently)."""
    recommendation = context.recommendation_lookup.get(
        finding.check_id, finding.recommendation
    )
    if finding.check_id in {
        "SHIP-API-TRACE-APPROVAL-MISSING",
        "SHIP-API-TRACE-CONFIRMATION-MISSING",
    }:
        instructions = f"{recommendation} {_TRACE_FLIP_PROHIBITION}"
    else:
        instructions = recommendation or finding.recommendation
    return ManualPatch(instructions=instructions)


# ---------------------------------------------------------------------------
# Registry.
# ---------------------------------------------------------------------------


_GENERATORS: dict[str, GeneratorFn] = {
    "SHIP-MANIFEST-STALE-SUPPRESSION": _gen_stale_suppression,
    "SHIP-MANIFEST-STALE-POLICY": _gen_stale_policy,
    "SHIP-MANIFEST-STALE-RISK-OVERRIDE": _gen_stale_risk_override,
    "SHIP-AUTH-SCOPE-COVERAGE-MISSING": _gen_auth_scope_coverage,
}


def _sha256(path: Path) -> str:
    if not path.exists():
        return ""
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()
