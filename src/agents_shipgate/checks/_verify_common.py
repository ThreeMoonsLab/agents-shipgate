"""Shared helpers for the Tier B SHIP-VERIFY-* trust-root weakening checks.

Tier B (docs/engineering/ai-coding-workflow-verifier.md §5.1) detects
*semantic* weakening of the release gate — policy weakened, waiver/baseline
expanded, CI gate removed — as opposed to Tier A's pure path classification
(``SHIP-VERIFY-TRUST-ROOT-TOUCHED``).

All Tier B checks share these invariants, enforced here so each check
module stays small and consistent:

- They emit **only** when a ``VerificationContext`` is present
  (``context.verification is not None``). Plain ``scan`` emits nothing.
- The comparison checks (policy-weakened, baseline/waiver-expanded) read
  the BASE effective-policy snapshot from the ``--diff-from`` reference
  report's ``effective_policy`` block. When that base is unavailable
  (no diff reference, or a pre-v0.22 base that predates the block), they
  FAIL SAFE — never silently pass (§5.3).
- Findings are ordinary ``Finding``s in category ``verify`` so they are
  suppression-immune and floor-protected and flow through the one
  decision engine; they never set ``blocks_release`` directly.

No I/O beyond reading the already-loaded diff reference's ``raw`` dict; no
network; deterministic (sorted outputs).
"""

from __future__ import annotations

from collections import defaultdict

from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.domain import ToolkitScopeBound
from agents_shipgate.core.globbing import glob_match
from agents_shipgate.core.lenses.effective_policy import (
    build_effective_policy_snapshot,
)
from agents_shipgate.core.toolkit_scope import bound_from_policy_fact, policy_key_for
from agents_shipgate.schemas.capability_change import EffectivePolicy
from agents_shipgate.schemas.common import (
    SourceReference,
    parse_confidence,
    parse_severity,
)
from agents_shipgate.schemas.report import Finding


def verification_active(context: ScanContext) -> bool:
    """True when a verification context is present (verify mode)."""
    return context.verification is not None


def changed_files(context: ScanContext) -> list[str]:
    """Normalized, de-duplicated changed-file list from the context.

    Returns ``[]`` when no verification context is present. Paths are
    forward-slash-normalized without stripping: leading and trailing
    whitespace are legal, distinct Git path bytes.
    """
    verification = context.verification
    if verification is None:
        return []
    seen: list[str] = []
    out: set[str] = set()
    for raw in getattr(verification, "changed_files", []) or []:
        path = str(raw).replace("\\", "/")
        if path and path not in out:
            out.add(path)
            seen.append(path)
    return seen


def touched(globs: tuple[str, ...], files: list[str]) -> list[str]:
    """Sorted unique changed files matching any of ``globs``."""
    hits = {f for f in files for pattern in globs if glob_match(pattern, f)}
    return sorted(hits)


def head_effective_policy(context: ScanContext) -> EffectivePolicy:
    """Build the HEAD effective-policy snapshot from the live manifest.

    Built directly from the manifest (not the in-progress report) because
    checks run before the report is assembled. ``baseline_fingerprints``
    is intentionally left empty at check time — findings are not yet
    baseline-matched — so comparisons use only the manifest-derived fields.
    """
    return build_effective_policy_snapshot(context.manifest)


def base_effective_policy(context: ScanContext) -> EffectivePolicy | None:
    """Read the BASE effective-policy snapshot from the diff reference.

    Returns ``None`` (degrade safely) when:
    - no ``--diff-from`` reference was supplied,
    - the reference is a baseline (no policy snapshot), or
    - the reference predates v0.22 and has no ``effective_policy`` block.

    The reference's ``effective_policy`` was already validated when the
    base report was loaded, so this is a plain attribute read — it never
    raises, honoring the §6 contract that base failure disables enrichment
    only and never fails the head scan.
    """
    reference = context.diff_reference
    if reference is None:
        return None
    return getattr(reference, "effective_policy", None)


def base_toolkit_bounds(context: ScanContext) -> dict[str, ToolkitScopeBound]:
    """BASE-side toolkit scope bounds, decoded from the ``--diff-from``
    reference report's carried ``toolkit_scope_bound`` policy facts.

    Empty when there is no diff reference or the base carried no bounds —
    callers degrade safely (no base, no comparison).
    """
    reference = context.diff_reference
    facts = getattr(reference, "facts", None) if reference is not None else None
    if facts is None:
        return {}
    bounds: dict[str, ToolkitScopeBound] = {}
    for fact in facts.policies:
        bound = bound_from_policy_fact(fact)
        if bound is not None:
            bounds[policy_key_for(bound)] = bound
    return bounds


def head_toolkit_bounds(context: ScanContext) -> dict[str, ToolkitScopeBound]:
    """HEAD-side toolkit scope bounds from the live extraction."""
    return {policy_key_for(bound): bound for bound in context.toolkit_bounds}


def pair_toolkit_bounds(
    base: dict[str, ToolkitScopeBound], head: dict[str, ToolkitScopeBound]
) -> list[tuple[ToolkitScopeBound, ToolkitScopeBound]]:
    """Pair base↔head bounds for comparison, keeping instances distinct.

    First by exact ``provider:binding`` key, so multiple toolkit instances of a
    provider do not collapse. Then a leftover fallback: for a provider with
    exactly one unmatched bound on each side, pair them — this re-pairs a
    *renamed* single toolkit so a rename-plus-weaken cannot slip through.
    Ambiguous leftovers (more than one unmatched per side) are left unpaired
    (fail safe).
    """
    pairs: list[tuple[ToolkitScopeBound, ToolkitScopeBound]] = []
    matched_head: set[str] = set()
    base_left: list[ToolkitScopeBound] = []
    for key in sorted(base):
        if key in head:
            pairs.append((base[key], head[key]))
            matched_head.add(key)
        else:
            base_left.append(base[key])
    head_left = [head[key] for key in sorted(head) if key not in matched_head]

    base_by_provider: dict[str, list[ToolkitScopeBound]] = defaultdict(list)
    head_by_provider: dict[str, list[ToolkitScopeBound]] = defaultdict(list)
    for bound in base_left:
        base_by_provider[bound.provider].append(bound)
    for bound in head_left:
        head_by_provider[bound.provider].append(bound)
    for provider in sorted(base_by_provider):
        bl = base_by_provider[provider]
        hl = head_by_provider.get(provider, [])
        if len(bl) == 1 and len(hl) == 1:
            pairs.append((bl[0], hl[0]))
    return pairs


def verify_finding(
    context: ScanContext,
    *,
    check_id: str,
    title: str,
    severity: str,
    evidence: dict,
    recommendation: str,
) -> Finding:
    """Construct a category-``verify`` finding (mirrors checks/verify.py).

    ``blocks_release`` stays ``False``: the verify category is
    suppression-immune + floor-protected, and the one decision engine
    gates on severity + ``fail_on``. The finding never carries a second
    verdict.
    """
    return Finding(
        check_id=check_id,
        title=title,
        severity=parse_severity(severity),
        category="verify",
        agent_id=getattr(context.agent, "id", None),
        evidence=evidence,
        confidence=parse_confidence("high"),
        provenance_kind="static_declaration",
        source=SourceReference(type="verification_context"),
        recommendation=recommendation,
    )


# Severity tier rank for "lowered across a tier" comparisons. Mirrors the
# snapshot's SEVERITY_RANK; duplicated as a tuple-free import to avoid a
# cycle is unnecessary — we import the canonical map.
from agents_shipgate.schemas.capability_change import SEVERITY_RANK  # noqa: E402

__all__ = [
    "SEVERITY_RANK",
    "base_effective_policy",
    "base_toolkit_bounds",
    "changed_files",
    "head_effective_policy",
    "head_toolkit_bounds",
    "pair_toolkit_bounds",
    "touched",
    "verification_active",
    "verify_finding",
]
