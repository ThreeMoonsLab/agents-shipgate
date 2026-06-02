"""SHIP-VERIFY-CAPABILITY-SCOPE-BROADENED — toolkit least-privilege weakening.

A Tier B (docs/engineering/ai-coding-workflow-verifier.md §5.1) check for a
trust concern the generic tool/scope diff cannot see: an agent toolkit that
loads its tools through a runtime factory (``*toolkit.get_tools()``) the
static extractor cannot enumerate, but whose *constructor* declared an
explicit least-privilege allowlist.

The extractor records that allowlist as a
:class:`~agents_shipgate.core.domain.ToolkitScopeBound` (HEAD side on
``context.toolkit_bounds``); the base report carries it as a
``toolkit_scope_bound`` policy fact (see ``core.toolkit_scope``). This check
diffs the two and fires when the head *removes* the bound (mounting the full
toolkit surface) or *broadens* it (granting permissions the base allowlist
did not). Either way the agent silently gained capability — e.g. a
support agent that gains refund / cancel / dispute tools — which the
opaque dynamic factory would otherwise hide behind ``insufficient_evidence``.

The finding is ``critical`` so it is a blocker in the one decision engine
(``ci/release_decision.py``): the verdict becomes ``blocked`` rather than
``insufficient_evidence``. Like the other Tier B checks it is category
``verify`` (suppression-immune + floor-protected), emits nothing without a
``VerificationContext``, and fails safe (no base bound → no finding, never a
silent contradiction — the dynamic-toolkit path still degrades to
``insufficient_evidence``). A *narrowing* (bound added or tightened) emits
nothing.
"""

from __future__ import annotations

from collections import defaultdict

from agents_shipgate.checks._verify_common import verification_active, verify_finding
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.domain import ToolkitScopeBound
from agents_shipgate.core.toolkit_scope import bound_from_policy_fact, policy_key_for
from agents_shipgate.schemas.report import Finding

CHECK_ID = "SHIP-VERIFY-CAPABILITY-SCOPE-BROADENED"


def run(context: ScanContext) -> list[Finding]:
    if not verification_active(context):
        return []
    base_bounds = _base_bounds(context)
    if not base_bounds:
        # No base toolkit bound to compare against (no diff reference, a
        # pre-feature base, or a base with no recognized toolkit). Degrade
        # safely: the dynamic-toolkit surface still reads as low-confidence
        # evidence elsewhere, so this is never a silent pass.
        return []
    head_bounds = _head_bounds(context)
    findings: list[Finding] = []
    for base_bound, head_bound in _pair_bounds(base_bounds, head_bounds):
        weakening = _classify(base_bound, head_bound)
        if weakening is not None:
            findings.append(_finding(context, base_bound, head_bound, weakening))
    return findings


def _base_bounds(context: ScanContext) -> dict[str, ToolkitScopeBound]:
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


def _head_bounds(context: ScanContext) -> dict[str, ToolkitScopeBound]:
    return {policy_key_for(bound): bound for bound in context.toolkit_bounds}


def _pair_bounds(
    base: dict[str, ToolkitScopeBound], head: dict[str, ToolkitScopeBound]
) -> list[tuple[ToolkitScopeBound, ToolkitScopeBound]]:
    """Pair base↔head bounds for comparison, keeping instances distinct.

    First by exact ``provider:binding`` key, so multiple toolkit instances of a
    provider do not collapse. Then a leftover fallback: for a provider with
    exactly one unmatched bound on each side, pair them — this re-pairs a
    *renamed* single toolkit so a rename-plus-broaden cannot slip through.
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


def _classify(base: ToolkitScopeBound, head: ToolkitScopeBound) -> dict | None:
    """Return weakening evidence, or ``None`` for no-change / narrowing.

    Only a base that was *bounded* can be weakened — an already-unbounded
    base cannot broaden further (a bounded head would be a narrowing).
    """
    if not base.bounded:
        return None
    base_scopes = sorted(set(base.scopes))
    if not head.bounded:
        return {
            "kind": "scope_bound_removed",
            "base_scopes": base_scopes,
            "head_scopes": [],
            "added_scopes": [],
        }
    added = sorted(set(head.scopes) - set(base.scopes))
    if added:
        return {
            "kind": "scope_bound_broadened",
            "base_scopes": base_scopes,
            "head_scopes": sorted(set(head.scopes)),
            "added_scopes": added,
        }
    return None


def _finding(
    context: ScanContext,
    base_bound: ToolkitScopeBound,
    head_bound: ToolkitScopeBound,
    weakening: dict,
) -> Finding:
    provider = head_bound.provider or base_bound.provider
    label = provider[:1].upper() + provider[1:] if provider else "Toolkit"
    evidence: dict = {
        "provider": provider,
        **weakening,
    }
    if head_bound.constructor:
        evidence["constructor"] = head_bound.constructor
    if head_bound.binding:
        evidence["binding"] = head_bound.binding
    if head_bound.source_ref:
        evidence["source_ref"] = head_bound.source_ref
    if head_bound.source_line is not None:
        evidence["source_line"] = head_bound.source_line
    if weakening["kind"] == "scope_bound_removed":
        title = f"{label} toolkit least-privilege bound removed"
        recommendation = (
            "The base restricted this toolkit to an explicit allowlist "
            f"({', '.join(weakening['base_scopes']) or 'no actions'}); the head "
            "removes the configuration, mounting the full toolkit surface "
            "(including high-risk write tools such as refund / cancel / "
            "dispute). A coding agent cannot self-approve a capability "
            "broadening — a human must review and re-apply a least-privilege "
            "configuration or explicitly approve the expanded surface."
        )
    else:
        title = f"{label} toolkit scope bound broadened"
        recommendation = (
            "The head grants toolkit permissions the base allowlist did not "
            f"(added: {', '.join(weakening['added_scopes'])}). A coding agent "
            "cannot self-approve a capability broadening — a human must review "
            "and approve the expanded toolkit scope."
        )
    return verify_finding(
        context,
        check_id=CHECK_ID,
        title=title,
        severity="critical",
        evidence=evidence,
        recommendation=recommendation,
    )
