"""Base-vs-head policy weakening and its no-base fail-safe (§5.1 Tier B).

This module emits two distinct reason codes.

``SHIP-VERIFY-POLICY-WEAKENED`` is a *base-relative* claim. It compares the
normalized effective-policy snapshot of the base report (via ``--diff-from``)
against the head manifest and emits one finding per detected weakening:

- ``ci_mode_weakened`` — CI gate moved strict -> advisory.
- ``fail_on_loosened`` — the fail-on severity set lost a tier
  (head ⊊ base), i.e. fewer severities now fail CI.
- ``severity_override_lowered`` — a check's applied severity dropped
  across a tier boundary versus the base.
- ``control_pack_weakened`` — the manifest moved to a control pack that
  requires less of some effect than the base's did (#410 §F).

``SHIP-VERIFY-POLICY-BASE-ABSENT`` is the fail-safe for when there is no base
policy to compare against at all. It carries no weakening claim in either
direction — it says the comparison could not be made and routes the change to
a human (§5.3: ambiguous direction -> review_required, never silent pass). It
emits only when the PR also touched a policy/manifest trust root, under one of
two evidence kinds:

- ``manifest_introduced`` — the base is proven to carry no manifest at all, so
  this diff adopts the gate rather than modifying one. Adoption is still a
  human decision, but nothing existed to weaken and the finding says so.
- ``base_snapshot_unavailable`` — no base snapshot was obtainable (no diff
  reference, a pre-v0.22 base, or a base whose scan did not produce one).

Splitting the fail-safe out of ``SHIP-VERIFY-POLICY-WEAKENED`` is what keeps
"the policy was weakened relative to a base" readable as the fact it claims:
on a first adoption the old shared reason code reported a weakening that
definitionally could not have happened. The split is reason-code and copy
only — severity, category, human-acknowledgement requirement, and the
``policy_weakened`` fail-safe flag are all unchanged (see
``core/findings/verifier_blocks`` and ``cli/verify/capability_review``).

Weakening is defined as movement toward less review / less blocking. A
strengthening change (stricter mode, more fail-on severities, raised
override) emits nothing.
"""

from __future__ import annotations

from functools import lru_cache

from agents_shipgate.checks._metadata_loader import load_check_metadata
from agents_shipgate.checks._verify_common import (
    SEVERITY_RANK,
    base_effective_policy,
    changed_files,
    head_effective_policy,
    touched,
    verification_active,
    verify_finding,
)
from agents_shipgate.core.action_semantics import (
    control_phrase,
    effect_phrase,
    join_phrases,
    ordered_controls,
)
from agents_shipgate.core.check_ids import (
    SPLIT_CHECK_ID_ALIASES,
    expands_to_check_id,
)
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.control_packs import (
    BUILTIN_CONTROL_PACKS,
    DEFAULT_CONTROL_PACK_ID,
)
from agents_shipgate.core.policy_reason_codes import (
    POLICY_BASE_ABSENT_CHECK_ID,
    POLICY_WEAKENED_CHECK_ID,
)
from agents_shipgate.core.trust_roots import is_context_configured_manifest
from agents_shipgate.schemas.report import Finding

CHECK_ID = POLICY_WEAKENED_CHECK_ID
# The no-base fail-safe. A separate reason code because it makes the opposite
# claim from CHECK_ID: not "the gate got weaker" but "there is no base gate to
# compare against". Consumers that read a reason code as a fact — reviewer
# routing, the registry, the gate-bypass alarm — could not tell those apart
# while both shared one id. Configuration written against CHECK_ID still
# reaches this one: see ``core.check_ids.SPLIT_CHECK_ID_ALIASES``.
BASE_ABSENT_CHECK_ID = POLICY_BASE_ABSENT_CHECK_ID

# Strength of a CI mode — higher blocks more. Unknown modes rank -1 so an
# unrecognized head mode never reads as "stronger" than a known base.
_CI_MODE_RANK = {"advisory": 0, "warn": 0, "strict": 1, "block": 1}

# Trust roots whose modification, when the base snapshot is missing,
# warrants a fail-safe review-required finding.
_POLICY_SURFACES = (
    "**/shipgate.yaml",
    "**/policies/**",
    "**/.agents-shipgate/**",
)


def run(context: ScanContext) -> list[Finding]:
    if not verification_active(context):
        return []
    base = base_effective_policy(context)
    head = head_effective_policy(context)
    if base is None:
        return _fail_safe(context)
    return _compare(context, base, head)


def _compare(context: ScanContext, base, head) -> list[Finding]:
    findings: list[Finding] = []

    # 1. ci.mode weakened (strict -> advisory).
    base_rank = _CI_MODE_RANK.get(base.ci_mode or "", 0)
    head_rank = _CI_MODE_RANK.get(head.ci_mode or "", 0)
    if head_rank < base_rank:
        findings.append(
            verify_finding(
                context,
                check_id=CHECK_ID,
                title=f"CI mode weakened: {base.ci_mode} -> {head.ci_mode}",
                severity="high",
                evidence={
                    "kind": "ci_mode_weakened",
                    "base_ci_mode": base.ci_mode,
                    "head_ci_mode": head.ci_mode,
                },
                recommendation=(
                    "This PR weakens the CI gate mode. A human must review "
                    "and approve the change; do not weaken the gate to make "
                    "CI pass."
                ),
            )
        )

    # 2. fail_on loosened (head is a proper subset of base).
    base_fail = set(base.fail_on)
    head_fail = set(head.fail_on)
    dropped = base_fail - head_fail
    if dropped:
        findings.append(
            verify_finding(
                context,
                check_id=CHECK_ID,
                title="Fail-on severity set loosened",
                severity="high",
                evidence={
                    "kind": "fail_on_loosened",
                    "removed_severities": sorted(
                        dropped, key=lambda s: (SEVERITY_RANK.get(s, -1), s)
                    ),
                    "base_fail_on": list(base.fail_on),
                    "head_fail_on": list(head.fail_on),
                },
                recommendation=(
                    "This PR removes severities from the CI fail-on set, so "
                    "fewer findings block release. A human must approve the "
                    "reduced gate."
                ),
            )
        )

    # 3. effective severity lowered across a tier (per check_id).
    # Compare effective applied severity, not just explicit override text:
    # adding a downgrade (default high -> override medium), lowering an
    # existing override, and removing a hardening override (override critical
    # -> default medium) are all policy weakening.
    defaults = _catalog_default_severities()
    lowered = []
    # An override written against a check that has since split configures both
    # halves (``SPLIT_CHECK_ID_ALIASES``), so the comparison must be made for
    # every check either side's configuration *reaches* — not only for the
    # literal keys. Comparing keys alone reports both kinds of wrong answer: a
    # head that adds an explicit override for the new id lowers the applied
    # severity with no key change on the old one (missed), and a head that
    # drops a redundant explicit override changes no applied severity at all
    # (falsely reported).
    check_ids = _comparable_check_ids(base.severity_overrides, head.severity_overrides)
    for check_id in sorted(check_ids):
        base_sev = _effective_severity(
            check_id,
            base.severity_overrides,
            defaults,
        )
        head_sev = _effective_severity(
            check_id,
            head.severity_overrides,
            defaults,
        )
        if base_sev is None or head_sev is None:
            continue
        if SEVERITY_RANK.get(head_sev, -1) < SEVERITY_RANK.get(base_sev, -1):
            lowered.append((check_id, base_sev, head_sev))
    for check_id, base_sev, head_sev in lowered:
        findings.append(
            verify_finding(
                context,
                check_id=CHECK_ID,
                title=f"Severity override lowered for {check_id}",
                severity="high",
                evidence={
                    "kind": "severity_override_lowered",
                    "target_check_id": check_id,
                    "base_severity": base_sev,
                    "head_severity": head_sev,
                },
                recommendation=(
                    f"This PR lowers the severity of {check_id} from "
                    f"{base_sev} to {head_sev}. A human must approve the "
                    "downgrade with a documented reason."
                ),
            )
        )

    # 4. control pack moved to one that requires less of some effect.
    #
    # One edit, one finding — the shape ``fail_on_loosened`` already uses,
    # and the reason it matters here: a single changed line drops obligations
    # across up to nine effects, and a finding per effect would repeat one
    # sentence nine times about one edit. The per-effect detail is evidence,
    # not nine rows to read.
    removed = _weakened_pack_rules(base.control_pack, head.control_pack)
    if removed:
        base_pack = base.control_pack or DEFAULT_CONTROL_PACK_ID
        head_pack = head.control_pack or DEFAULT_CONTROL_PACK_ID
        noun = "effect" if len(removed) == 1 else "effects"
        findings.append(
            verify_finding(
                context,
                check_id=CHECK_ID,
                title=(
                    f"Control pack weakened: {base_pack} -> {head_pack} "
                    f"({len(removed)} {noun} require less)"
                ),
                severity="high",
                evidence={
                    "kind": "control_pack_weakened",
                    "base_control_pack": base_pack,
                    "head_control_pack": head_pack,
                    "removed_controls": [
                        {"effect": effect, "controls": controls}
                        for effect, controls in removed
                    ],
                },
                recommendation=(
                    f"This PR moves policies.control_pack from {base_pack} to "
                    f"{head_pack}, so {len(removed)} action {noun} no longer "
                    "require controls they required on the base "
                    f"({_removed_summary(removed)}). A human must approve the "
                    "reduced gate; do not change the pack to make a scan pass."
                ),
            )
        )

    return findings


#: Effects named in the recommendation before it says how many it is not
#: naming. Three is what fits a console line beside the rest of the sentence;
#: the full list is always in ``evidence.removed_controls``.
_NAMED_WEAKENED_EFFECTS = 3


def _removed_summary(removed: list[tuple[str, list[str]]]) -> str:
    """``write loses approval.required; …, and 5 more`` — truncation stated."""

    shown = removed[:_NAMED_WEAKENED_EFFECTS]
    parts = [
        f"{effect_phrase(effect)} loses "
        f"{join_phrases([control_phrase(path) for path in controls])}"
        for effect, controls in shown
    ]
    hidden = len(removed) - len(shown)
    if hidden:
        noun = "effect" if hidden == 1 else "effects"
        parts.append(f"and {hidden} more {noun}")
    return "; ".join(parts)


def _weakened_pack_rules(
    base_pack_id: str | None,
    head_pack_id: str | None,
) -> list[tuple[str, list[str]]]:
    """Effects the head pack requires less of than the base pack did.

    ``None`` on the base side means the snapshot predates the field, which is
    by construction the ``default`` rule set — that build could not have
    loaded a manifest naming a pack. It is resolved to ``default`` and
    compared rather than skipped, so the "no pack is weaker than default"
    invariant is enforced here instead of assumed: if a weaker pack were ever
    added, this comparison would report it rather than stay silent.

    An id neither side's build knows cannot be compared in either direction,
    and a base report from a newer build already fails validation upstream, so
    the diff is disabled long before this runs.
    """

    base_pack = BUILTIN_CONTROL_PACKS.get(base_pack_id or DEFAULT_CONTROL_PACK_ID)
    head_pack = BUILTIN_CONTROL_PACKS.get(head_pack_id or DEFAULT_CONTROL_PACK_ID)
    if base_pack is None or head_pack is None or base_pack.id == head_pack.id:
        return []
    weakened: list[tuple[str, list[str]]] = []
    for effect in sorted(base_pack.obligations):
        dropped = base_pack.obligations_for(effect) - head_pack.obligations_for(effect)
        if dropped:
            weakened.append((effect, ordered_controls(dropped)))
    return weakened


@lru_cache(maxsize=1)
def _catalog_default_severities() -> dict[str, str]:
    return {entry.id: entry.default_severity for entry in load_check_metadata()}


def _comparable_check_ids(
    base_overrides: dict[str, str],
    head_overrides: dict[str, str],
) -> set[str]:
    """Every check whose applied severity either side's configuration reaches.

    A configured key is itself comparable, and so is each check it expands to
    through a split alias — otherwise a configured id that no longer names the
    check it governs drops out of the comparison entirely.
    """

    configured = set(base_overrides) | set(head_overrides)
    return configured | {
        expansion
        for check_id in configured
        for expansion in SPLIT_CHECK_ID_ALIASES.get(check_id, ())
    }


def _effective_severity(
    check_id: str,
    overrides: dict[str, str],
    defaults: dict[str, str],
) -> str | None:
    """The severity ``check_id`` is actually applied at, under ``overrides``.

    Resolution mirrors the runtime applier
    (``core.findings.mutations._severity_override_for_check``) exactly: an
    override written against this check wins, and only then does an override
    written against a pre-split umbrella id apply. Reading the literal key
    alone made the comparator describe a policy the run does not enforce.
    """

    direct = overrides.get(check_id)
    if direct:
        return direct
    for configured_check_id, override in sorted(overrides.items()):
        if override and expands_to_check_id(configured_check_id, check_id):
            return override
    return defaults.get(check_id)


def _touched_policy_surfaces(context: ScanContext) -> list[str]:
    """Changed policy trust roots, including a non-default manifest name.

    ``_POLICY_SURFACES`` only knows ``**/shipgate.yaml``. A repository whose
    gate is ``new-gate.yml`` was therefore invisible to this fail-safe: its
    manifest could be introduced or rewritten with no base snapshot and this
    check emitted nothing at all.
    """

    files = changed_files(context)
    hits = set(touched(_POLICY_SURFACES, files))
    hits.update(path for path in files if is_context_configured_manifest(context, path))
    return sorted(hits)


def _fail_safe(context: ScanContext) -> list[Finding]:
    """No base snapshot: emit review-required iff a policy root was touched."""
    hit = _touched_policy_surfaces(context)
    if not hit:
        return []
    verification = context.verification
    introducing = verification is not None and verification.manifest_introduced
    # An adoption that *also* edits an existing policy pack or baseline is not
    # covered by "nothing existed to weaken": those files were already there.
    # The friendlier wording is only correct when every touched policy surface
    # is the manifest being introduced.
    if introducing and all(
        is_context_configured_manifest(context, path) for path in hit
    ):
        configured_manifest = verification.configured_manifest_path or hit[0]
        # A base with no manifest at all cannot have been weakened. This still
        # emits — at the same check id and severity, so the verdict and every
        # fail-closed consumer are unchanged — because the human decision
        # (adopt this policy) is real. Only the claim about what happened
        # changes. The orchestrator proves the base carries no manifest under
        # any name, so a moved-and-loosened manifest does not reach here.
        return [
            verify_finding(
                context,
                check_id=BASE_ABSENT_CHECK_ID,
                title="Initial Shipgate adoption: the base carries no policy",
                severity="medium",
                evidence={
                    "kind": "manifest_introduced",
                    "changed_policy_files": hit,
                },
                recommendation=(
                    "This PR introduces the Shipgate manifest rather than "
                    "changing an existing one, so no prior gate was weakened. "
                    "Adopting a release policy is a human decision: review the "
                    f"configured manifest {configured_manifest!r}; a human "
                    "must decide whether to adopt it."
                ),
            )
        ]
    return [
        verify_finding(
            context,
            check_id=BASE_ABSENT_CHECK_ID,
            title="Policy change cannot be proven safe (no base snapshot)",
            severity="medium",
            evidence={
                "kind": "base_snapshot_unavailable",
                "changed_policy_files": hit,
            },
            recommendation=(
                "This PR changes a policy trust root but no base report was "
                "available to prove the change does not weaken the gate. A "
                "human must review the effective-policy change directly."
            ),
        )
    ]
