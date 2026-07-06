"""SHIP-CAP-CONFIG-BINDING-* — config-bound dynamic-toolkit authority.

Implements the detection half of
docs/engineering/config-bound-capability-detection.md. A dynamic toolkit
factory (``*toolkit.get_tools()``) whose authority-bearing constructor
argument is bound from a *config read* hides its effective tool surface from
static enumeration on BOTH sides of a verify diff — so a PR that changes or
removes the binding produces no capability change in the generic diff at all.
The removal case is the dangerous one: deleting a restrictive config line
*expands* authority while looking like cleanup.

Two detections, both on factory sites present on both sides of the diff
(paired the same way as ``SHIP-VERIFY-CAPABILITY-SCOPE-BROADENED``):

- ``config-bound → absent`` ⇒ ``SHIP-CAP-CONFIG-BINDING-REMOVED``
  (severity ``high``): the restriction-bearing config binding was removed
  from the factory call; the effective tool surface may have expanded to
  the toolkit's defaults.
- ``config-bound → config-bound`` with the referenced config file in the
  PR's ``changed_files`` ⇒ ``SHIP-CAP-CONFIG-BINDING-CHANGED`` (severity
  ``medium``, a review item): the config that binds this toolkit's
  authority changed; static analysis cannot diff the effective tool list.

Calibration guards (the design doc's false-positive warning):

- A head binding of ``unknown`` (present but not statically readable) NEVER
  fires the removal check — it degrades to the extractor's source warning.
- A head that moves to a *literal* allowlist is the safe direction (the
  authority became statically visible); nothing fires.
- Everything asserts "authority may have changed invisibly", never
  "authority is X": confidence stays explicit and the findings flow into
  the one decision engine (category ``verify``: suppression-immune,
  floor-protected, no second verdict).
"""

from __future__ import annotations

from agents_shipgate.checks._verify_common import (
    base_toolkit_bounds,
    changed_files,
    head_toolkit_bounds,
    pair_toolkit_bounds,
    verification_active,
    verify_finding,
)
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.domain import ToolkitScopeBound
from agents_shipgate.schemas.report import Finding

CHECK_ID_REMOVED = "SHIP-CAP-CONFIG-BINDING-REMOVED"
CHECK_ID_CHANGED = "SHIP-CAP-CONFIG-BINDING-CHANGED"


def run(context: ScanContext) -> list[Finding]:
    if not verification_active(context):
        return []
    base_bounds = base_toolkit_bounds(context)
    if not base_bounds:
        # No base reference (or a pre-feature base with no carried bounds):
        # degrade safely — the dynamic-toolkit surface still reads as
        # low-confidence evidence elsewhere, never a silent pass.
        return []
    head_bounds = head_toolkit_bounds(context)
    files = changed_files(context)
    findings: list[Finding] = []
    for base_bound, head_bound in pair_toolkit_bounds(base_bounds, head_bounds):
        if base_bound.config_binding != "config":
            continue
        if head_bound.config_binding == "absent":
            findings.append(_removed_finding(context, base_bound, head_bound))
        elif head_bound.config_binding == "config":
            hit = _changed_config_file(
                head_bound.config_path or base_bound.config_path, files
            )
            if hit is not None:
                findings.append(
                    _changed_finding(context, base_bound, head_bound, hit)
                )
        # head "unknown": never fire the removal check on an unreadable
        # binding (design-doc guard); the extractor's source warning routes
        # it to review. head "literal": authority became statically visible
        # — the safe direction; the normal scope-bound checks own it.
    return findings


def _changed_config_file(config_path: str | None, files: list[str]) -> str | None:
    """The changed file matching the factory's bound config path, if any.

    Conservative: an exact repo-relative match, or a changed file whose
    path ends with ``/<config_path>`` (the AST literal may be relative to a
    subdirectory). ``None`` when the binding carried no literal path (env /
    settings reads) — never guess a match.
    """
    if not config_path:
        return None
    normalized = config_path.lstrip("./")
    for changed in files:
        if changed == normalized or changed.endswith(f"/{normalized}"):
            return changed
    return None


def _site(bound: ToolkitScopeBound) -> str:
    if bound.source_ref and bound.source_line is not None:
        return f"{bound.source_ref}:{bound.source_line}"
    return bound.source_ref or "the factory call site"


def _removed_finding(
    context: ScanContext,
    base_bound: ToolkitScopeBound,
    head_bound: ToolkitScopeBound,
) -> Finding:
    provider = head_bound.provider or base_bound.provider
    site = _site(head_bound)
    base_source = (
        f"configuration read from {base_bound.config_path}"
        if base_bound.config_path
        else "a configuration read the base bound statically traced"
    )
    return verify_finding(
        context,
        check_id=CHECK_ID_REMOVED,
        title=f"{provider} toolkit config binding removed",
        severity="high",
        evidence={
            "kind": "config_binding_removed",
            "provider": provider,
            "constructor": head_bound.constructor or base_bound.constructor,
            "binding": head_bound.binding or base_bound.binding or "",
            "base_config_path": base_bound.config_path or "",
            "source_ref": head_bound.source_ref or "",
        },
        recommendation=(
            f"The base bound this {provider} toolkit's authority to "
            f"{base_source}; the head constructs it at {site} without that "
            "binding, so the toolkit may mount its everything-enabled "
            "defaults — the effective tool surface can expand invisibly. A "
            "human must review: restore the config binding (or an explicit "
            "literal allowlist), or declare an explicit local tool inventory "
            f"for the toolkit constructed at {site} so the mounted surface "
            "is statically enumerable."
        ),
    )


def _changed_finding(
    context: ScanContext,
    base_bound: ToolkitScopeBound,
    head_bound: ToolkitScopeBound,
    changed_file: str,
) -> Finding:
    provider = head_bound.provider or base_bound.provider
    site = _site(head_bound)
    return verify_finding(
        context,
        check_id=CHECK_ID_CHANGED,
        title=f"{provider} toolkit authority config changed",
        severity="medium",
        evidence={
            "kind": "config_binding_changed",
            "provider": provider,
            "constructor": head_bound.constructor or base_bound.constructor,
            "binding": head_bound.binding or "",
            "config_path": head_bound.config_path or base_bound.config_path or "",
            "changed_file": changed_file,
            "source_ref": head_bound.source_ref or "",
        },
        recommendation=(
            f"This PR changes {changed_file}, the config that binds the "
            f"{provider} toolkit's authority at {site}. Static analysis "
            "cannot diff the effective tool list — review the config delta "
            "for added capabilities, or declare an explicit local tool "
            f"inventory for the toolkit constructed at {site} so future "
            "changes are statically comparable."
        ),
    )
