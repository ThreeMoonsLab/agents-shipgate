from __future__ import annotations

from agents_shipgate.schemas.checks import CheckMetadata
from agents_shipgate.schemas.common import AgentAction
from agents_shipgate.schemas.patches import ManualPatch
from agents_shipgate.schemas.report import Finding

# v0.7: safe-closed default for findings whose check_id isn't in the
# loaded catalog — policy-pack rules, third-party plugins, or any check
# emitted outside the built-in set. The static catalog is silent for
# these, so we default-close: human review required, no auto-fix kind
# claimed.
_REMEDIATION_FALLBACK = {
    "autofix_safe": False,
    "requires_human_review": True,
    "suggested_patch_kind": "manual",
    "docs_url": None,
}

def annotate_remediation(
    findings: list[Finding],
    check_metadata_lookup: dict[str, CheckMetadata],
) -> list[Finding]:
    """Populate the v0.7 per-finding remediation fields in place.

    Strict derivation policy:

    - When ``finding.patches`` is non-empty, the safety bools are derived
      from the actual emitted patches:
      * ``autofix_safe=True`` iff EVERY patch is non-manual AND has
        ``confidence == "high"``. Mixed-state (e.g. one safe + one
        manual, one high + one medium) → ``autofix_safe=False``.
      * ``requires_human_review`` is the inverse of ``autofix_safe``.
      * ``suggested_patch_kind`` = kind of the first non-manual patch,
        or ``"manual"`` when all are manual, or ``"none"`` when the
        list is empty.
    - When ``finding.patches`` is None (scan ran without
      ``--suggest-patches``), the safety bools and
      ``suggested_patch_kind`` come from the matching ``CheckMetadata``
      entry, with the safe-closed fallback for unknown check IDs.
    - ``docs_url`` is always sourced from CheckMetadata (or None for
      unknown check IDs). Patches don't carry per-instance doc URLs.

    Caller (`scan.run_scan`) builds the metadata lookup from the
    catalog with the scan's actual ``plugins_enabled`` setting, so this
    function never triggers plugin loading at serialization time.
    """
    for finding in findings:
        meta = check_metadata_lookup.get(finding.check_id)
        catalog_doc_url = meta.docs_url if meta is not None else None

        # Three states, treated distinctly:
        # 1. `patches is None`  → scan ran without --suggest-patches.
        #    Seed from CheckMetadata (or safe-closed fallback for
        #    unknown check IDs).
        # 2. `patches == []`    → scan ran WITH --suggest-patches but
        #    the generator emitted nothing for this finding. Treat as
        #    safe-closed with `suggested_patch_kind="none"` — falling
        #    back to the catalog would misleadingly report a patch
        #    kind that the report doesn't actually carry.
        # 3. `patches` non-empty → derive from the actual patches
        #    via the strict rule below.
        if finding.patches is None:
            if meta is not None:
                autofix_safe = meta.autofix_safe
                requires_human_review = meta.requires_human_review
                suggested_patch_kind = meta.suggested_patch_kind
            else:
                autofix_safe = bool(_REMEDIATION_FALLBACK["autofix_safe"])
                requires_human_review = bool(
                    _REMEDIATION_FALLBACK["requires_human_review"]
                )
                suggested_patch_kind = str(
                    _REMEDIATION_FALLBACK["suggested_patch_kind"]
                )
        else:
            (
                autofix_safe,
                requires_human_review,
                suggested_patch_kind,
            ) = _derive_from_patches(finding.patches)

        # Reviewer-grade escalation: when the catalog flags this check
        # as requiring human review regardless of the per-patch state
        # (approval/confirmation/idempotency, broad-scope,
        # prohibited-action, runtime-trace, HITL evidence), force
        # safe-closed values BEFORE assigning. Setting them here keeps
        # `finding.autofix_safe`, `finding.requires_human_review`, and
        # `finding.agent_action` (derived below) in agreement: the
        # existing `auto_apply` early-return in `derive_agent_action`
        # tests `finding.autofix_safe`, so flipping it to False naturally
        # routes the verdict to `propose_patch_for_review` whenever
        # patches are present, and to `escalate_to_human` otherwise.
        if meta is not None and meta.requires_human_review_regardless_of_patch:
            autofix_safe = False
            requires_human_review = True

        finding.autofix_safe = autofix_safe
        finding.requires_human_review = requires_human_review
        finding.suggested_patch_kind = suggested_patch_kind
        finding.docs_url = catalog_doc_url
        finding.agent_action = derive_agent_action(finding)
        # v0.14: ensure every emitted finding carries a real
        # `provenance_kind`. Built-in checks set it via the required
        # `tool_finding`/`agent_finding` kwarg. Third-party plugin
        # checks may still construct `Finding(...)` directly without
        # the field; coerce None → "static_declaration" so the wire
        # schema's required + non-nullable enum is satisfied. Plugins
        # that want a more accurate label should set the field
        # themselves; this fallback is the conservative declarative
        # label rather than a sentinel.
        if finding.provenance_kind is None:
            finding.provenance_kind = "static_declaration"
    return findings


def derive_agent_action(finding: Finding) -> AgentAction:
    """Project ``finding`` to a single ``AgentAction`` enum value.

    Deterministic projection of (``blocks_release``, ``patches``,
    ``autofix_safe``, ``requires_human_review``). A release-blocking
    finding always escalates to a human unless it is suppressed.
    Order-invariant: the result depends on the SET of patches, not on
    their list ordering. The first
    non-manual patch's confidence drives the verdict, mirroring
    :func:`_derive_from_patches` (which derives ``suggested_patch_kind``
    from the first non-manual patch). Earlier this function used
    ``patches[0]`` directly, so a finding with
    ``[ManualPatch, medium SetPointerPatch]`` mapped to
    ``escalate_to_human`` while
    ``[medium SetPointerPatch, ManualPatch]`` mapped to
    ``propose_patch_for_review`` despite identical patch content
    (#57 review P2).

    The strategy proposal in ``docs/agent-adoption-strategy.md`` §7
    G10 sketched an algorithm that ordered ``requires_human_review``
    before the medium/low confidence check, but that mapped non-manual
    medium-confidence patches to ``escalate_to_human`` even though the
    value's defined semantic ("no machine-applicable patch; needs
    human judgment") excludes that case. We deviate by checking
    confidence on the first non-manual patch BEFORE falling through
    to escalate, keeping the value definitions consistent with the
    projection.

    The ``suppress_with_reason`` value is reserved for future check
    classes that explicitly mark themselves as suppressible. The
    built-in projection does not emit it.
    """
    if finding.suppressed:
        return "informational"
    if finding.blocks_release:
        return "escalate_to_human"

    patches = finding.patches

    # No patch list (no --suggest-patches) or empty patch list:
    # nothing machine-applicable. Route on the catalog flags.
    if not patches:
        if finding.requires_human_review:
            return "escalate_to_human"
        return "informational"

    # Pick the first non-manual patch (order-invariant: every patch
    # generator produces a stable order, but the agent_action verdict
    # should depend on the set, not on which manual patch happened to
    # land first). All-manual lists fall through to escalate.
    non_manual = [p for p in patches if p.kind != "manual"]
    if not non_manual:
        return "escalate_to_human"

    first = non_manual[0]
    first_confidence = getattr(first, "confidence", None)
    if first_confidence == "high" and finding.autofix_safe:
        return "auto_apply"

    # Any non-manual patch with declared confidence (high, medium, or
    # low) is machine-applicable, so the verdict is propose-for-review
    # — including high-confidence patches in mixed lists where a
    # ManualPatch sibling disqualified `autofix_safe`. The enum's
    # `escalate_to_human` definition is "no machine-applicable patch",
    # which doesn't fit this case; routing it to escalate would
    # contradict the documented semantics (#57 review P3).
    if first_confidence in {"high", "medium", "low"}:
        return "propose_patch_for_review"

    # Rare: non-manual patch carries no confidence. Conservative escalate.
    if finding.requires_human_review:
        return "escalate_to_human"
    return "informational"


def _derive_from_patches(patches: list) -> tuple[bool, bool, str]:
    """Strict derivation: ``autofix_safe`` is True only when EVERY
    emitted patch is non-manual AND high-confidence. Mixed states fall
    to safe-closed."""
    if not patches:
        return (False, True, "none")

    has_manual = any(isinstance(p, ManualPatch) for p in patches)
    non_manual = [p for p in patches if not isinstance(p, ManualPatch)]
    all_high_confidence_non_manual = (
        not has_manual
        and bool(non_manual)
        and all(getattr(p, "confidence", None) == "high" for p in non_manual)
    )

    # Per the plan §2 derivation rule: kind of the FIRST non-manual
    # patch takes priority (even when ManualPatches are also present).
    # All-manual → "manual". Empty list → "none" (handled above).
    if non_manual:
        suggested_patch_kind = non_manual[0].kind
    else:
        suggested_patch_kind = "manual"

    autofix_safe = all_high_confidence_non_manual
    requires_human_review = not autofix_safe
    return (autofix_safe, requires_human_review, suggested_patch_kind)
