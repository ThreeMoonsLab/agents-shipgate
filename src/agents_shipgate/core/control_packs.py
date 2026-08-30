"""Named control packs: which controls each effect obliges, chosen once.

Issue #410 §F. Shipgate has always had a rule saying *a financial write
needs approval, an audit log, and idempotency*. It was written four times —
once as :data:`~agents_shipgate.core.action_semantics.BUILTIN_EFFECT_OBLIGATIONS`,
once as literals inside the branches of ``_current_action_policy_findings``,
and twice more as the effect sets in ``subject_requires_approval_review`` /
``subject_requires_confirmation_review`` — and it was not selectable, not
named, and not stated anywhere the adopter reads. Seven findings said
"lacks a declared approval policy" and none of them said *which rule* wanted
one.

A control pack is that rule set, named and versioned. It is chosen once in
the manifest (``policies.control_pack``) rather than answered once per tool,
which is the whole point: an organisation's control requirements are a
property of the organisation, not of the twelfth tool someone happened to
add.

Two properties are load-bearing, and each has a test.

**Every pack obliges at least what ``default`` obliges.** Pack selection can
only tighten the gate, never loosen it, so answering ``init``'s one question
is safe in a way that a free-form rule file would not be — a wrong answer
costs work, never coverage. It also means a report that passes under any
pack would also have passed under ``default``, which is why selecting a pack
does not need to be republished as a contract field before a verdict can be
trusted. :func:`_assert_packs_extend_default` checks it at import time so a
pack cannot be added that violates it.

**A pack does not decide what a declaration means.** The obligation lattice
the declaration comparator reads
(:func:`~agents_shipgate.core.action_semantics.builtin_obligations`) stays
the built-in one: it encodes what effects intrinsically *are*, and a pack
that obliged every effect identically would collapse it and let a declared
``write`` discharge an inferred ``external_communication`` — the #413
fail-open with the table swapped underneath it. Packs decide which control
findings fire; they never decide which declarations are accepted.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from agents_shipgate.core.action_semantics import (
    ACTION_EFFECT_RANK,
    BUILTIN_EFFECT_OBLIGATIONS,
    ordered_controls,
)
from agents_shipgate.schemas.common import Severity
from agents_shipgate.schemas.manifest.action_surface import (
    CONTROL_PACK_POLICY_ID_PREFIX as _RESERVED_POLICY_ID_PREFIX,
)
from agents_shipgate.schemas.surfaces import ActionEffect

#: The id written to ``policies.control_pack`` when the manifest is silent.
#: Silence has to mean today's rules, or adding this field would change every
#: existing manifest's verdict.
DEFAULT_CONTROL_PACK_ID = "default"

#: Comment column the generated manifest wraps pack prose at.
_MANIFEST_COMMENT_WIDTH = 78


@dataclass(frozen=True)
class ControlPack:
    """One named effect → required-controls rule set."""

    id: str
    name: str
    version: str
    summary: str
    obligations: Mapping[ActionEffect, frozenset[str]]

    def obligations_for(self, effect: str) -> frozenset[str]:
        """The controls ``effect`` obliges under this pack — empty for none."""

        return self.obligations.get(effect, frozenset())  # type: ignore[arg-type]

    def run_identity(self) -> dict[str, object]:
        """The pack as run identity: what it is called *and* what it requires.

        Hashed into ``run_id`` (#410 §F review). Two manifests differing only
        in their pack enforce different policy, so they are different runs —
        but before this they could hash identically whenever neither produced
        a control finding, which is exactly the clean-surface case a reviewer
        would most want to tell apart.

        The obligations are included rather than only the id and version: a
        Shipgate release that changes what ``default`` requires changes what
        the run means, and an id alone would hide that behind a name that did
        not move.
        """

        return {
            "id": self.id,
            "version": self.version,
            "obligations": {
                effect: sorted(controls)
                for effect, controls in sorted(self.obligations.items())
            },
        }

    def effects_obliging(self, control: str) -> frozenset[str]:
        """Every effect this pack obliges ``control`` for.

        The tool-level ``SHIP-POLICY-*`` checks used to carry their own effect
        sets — ``{financial_write, destructive, production_operation,
        code_execution}`` for approval — which were exactly this projection of
        the built-in table, maintained by hand beside it.
        """

        return frozenset(
            effect for effect, controls in self.obligations.items() if control in controls
        )


#: ``default`` is the built-in table itself, not a copy of it. Spelling the
#: rows out again here would be a second place for them to be wrong.
DEFAULT_CONTROL_PACK = ControlPack(
    id=DEFAULT_CONTROL_PACK_ID,
    name="Shipgate default controls",
    version="1",
    summary=(
        "Shipgate's built-in requirements: money, destruction, production "
        "operations, code execution, and outbound communication carry controls."
    ),
    obligations=dict(BUILTIN_EFFECT_OBLIGATIONS),
)


#: For teams whose gate exists because of what the agent can move. Adds the
#: two effects ``default`` leaves uncontrolled but a finance-facing review
#: asks about anyway — a plain write and a look at privileged data — and
#: raises the bar on the ones it already covers.
FINANCIAL_STRICT_CONTROL_PACK = ControlPack(
    id="financial-strict",
    name="Financial strict controls",
    version="1",
    summary=(
        "Recoverability and record: every state change is logged and "
        "retry-safe, production operations are reversible, financial writes "
        "are confirmed, and privileged reads leave a trail."
    ),
    obligations={
        "write": frozenset({"safeguards.audit_log", "safeguards.idempotency"}),
        "privileged_data_access": frozenset({"safeguards.audit_log"}),
        "identity_access": frozenset({"approval.required", "safeguards.audit_log"}),
        "external_communication": frozenset(
            {"safeguards.audit_log", "confirmation.required", "approval.required"}
        ),
        "financial_write": frozenset(
            {
                "approval.required",
                "safeguards.audit_log",
                "safeguards.idempotency",
                "confirmation.required",
            }
        ),
        "production_operation": frozenset(
            {"approval.required", "safeguards.audit_log", "safeguards.rollback"}
        ),
        "code_execution": frozenset({"approval.required", "safeguards.audit_log"}),
        "destructive": frozenset(
            {
                "approval.required",
                "safeguards.rollback",
                "confirmation.required",
                "safeguards.audit_log",
            }
        ),
    },
)


#: For an agent whose job is to answer questions. Nothing forbids a write —
#: this is a static scanner, and "forbid" is not a thing it can enforce — but
#: every state change becomes an exception a human has to have signed for,
#: which is the enforceable form of the same intent. An agent that genuinely
#: needs one write declares the controls and passes; an agent that grew a
#: write nobody meant to add stops the gate.
READ_ONLY_AGENT_CONTROL_PACK = ControlPack(
    id="read-only-agent",
    name="Read-only agent controls",
    version="1",
    summary=(
        "This agent reads. Any state change, outbound message, or privileged "
        "read is an exception that carries approval and an audit trail."
    ),
    obligations={
        "write": frozenset({"approval.required", "safeguards.audit_log"}),
        "privileged_data_access": frozenset(
            {"approval.required", "safeguards.audit_log"}
        ),
        "identity_access": frozenset({"approval.required", "safeguards.audit_log"}),
        "external_communication": frozenset(
            {"safeguards.audit_log", "confirmation.required", "approval.required"}
        ),
        "financial_write": frozenset(
            {
                "approval.required",
                "safeguards.audit_log",
                "safeguards.idempotency",
                "confirmation.required",
            }
        ),
        "production_operation": frozenset(
            {"approval.required", "safeguards.audit_log"}
        ),
        "code_execution": frozenset({"approval.required", "safeguards.audit_log"}),
        "destructive": frozenset(
            {
                "approval.required",
                "safeguards.rollback",
                "confirmation.required",
                "safeguards.audit_log",
            }
        ),
    },
)


BUILTIN_CONTROL_PACKS: dict[str, ControlPack] = {
    pack.id: pack
    for pack in (
        DEFAULT_CONTROL_PACK,
        FINANCIAL_STRICT_CONTROL_PACK,
        READ_ONLY_AGENT_CONTROL_PACK,
    )
}

#: Declaration order, which is also the order ``init`` lists them in: the
#: default first, then the two ways of being stricter than it.
CONTROL_PACK_IDS: tuple[str, ...] = tuple(BUILTIN_CONTROL_PACKS)

#: ``evidence.policy_id`` for the built-in approval rule over production
#: operations and code execution. Named rather than spelled inline because
#: the release decision reads it to keep that blocker non-waivable, and a
#: literal in two files is a rule that can be renamed in one of them.
HIGH_IMPACT_APPROVAL_POLICY_ID = "builtin-high-impact-approval"

#: ``evidence.policy_id`` prefix for an obligation that exists only because
#: a pack was selected. Same non-waivable treatment as the rule above: a
#: suppression explains accepted noise, and it cannot stand in for a control
#: the repository's own pack says the effect requires. Re-exported from the
#: manifest schema, which reserves it against user-authored policy ids.
CONTROL_PACK_POLICY_ID_PREFIX = _RESERVED_POLICY_ID_PREFIX


def _assert_packs_extend_default() -> None:
    """A built-in pack may add obligations; it may never drop one.

    Checked at import rather than only in the suite because the consequence
    of getting it wrong is a manifest field that quietly turns a blocker off,
    and a pack table is exactly the kind of thing edited without reading the
    module docstring above it.
    """

    for pack in BUILTIN_CONTROL_PACKS.values():
        if pack.id == DEFAULT_CONTROL_PACK_ID:
            continue
        for effect, required in DEFAULT_CONTROL_PACK.obligations.items():
            weaker = required - pack.obligations_for(effect)
            if weaker:
                raise AssertionError(
                    f"control pack {pack.id!r} drops {sorted(weaker)} for "
                    f"effect {effect!r}; a pack may only add obligations"
                )


_assert_packs_extend_default()


#: Effects with a control check of their own: the effect, its check id, that
#: check's severity, and how a title spells the effect. All four are *shipped*
#: facts — a check id may be deprecated over a minor cycle but never quietly
#: repointed (``STABILITY.md``) — so this table is fixed while the packs above
#: vary. Ordered as the branches it replaced were, so a scan emits its
#: findings in the order it always has.
DEDICATED_CONTROL_CHECKS: tuple[tuple[str, str, Severity, str], ...] = (
    (
        "financial_write",
        "SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING",
        "critical",
        "financial write",
    ),
    (
        "external_communication",
        "SHIP-ACTION-EXTERNAL-COMMUNICATION-AUDIT-MISSING",
        "high",
        "external communication",
    ),
    (
        "destructive",
        "SHIP-ACTION-DESTRUCTIVE-ROLLBACK-MISSING",
        "critical",
        "destructive",
    ),
)

#: Two effects, one finding: an action that both operates on production and
#: executes code owes one set of controls, not two overlapping lists.
HIGH_IMPACT_EFFECTS = frozenset({"production_operation", "code_execution"})

#: Everything the two dedicated routes cover, so the pack-only route can be
#: defined as *the rest* rather than by listing effects a second time.
EFFECTS_WITH_DEDICATED_CONTROL_CHECK = (
    frozenset(effect for effect, _, _, _ in DEDICATED_CONTROL_CHECKS)
    | HIGH_IMPACT_EFFECTS
)

_CHECK_ID_EFFECTS: dict[str, tuple[str, ...]] = {
    check_id: (effect,) for effect, check_id, _, _ in DEDICATED_CONTROL_CHECKS
}


@dataclass(frozen=True)
class ControlRuleSummary:
    """One pack rule, and how many actions are still short of it."""

    pack: ControlPack
    effects: tuple[str, ...]
    controls: tuple[str, ...]
    action_count: int


#: Built-in current-surface controls whose finding a ``checks.ignore`` entry
#: records but does not waive. Four shipped check ids plus, through
#: :func:`is_control_pack_finding`, every obligation a selected pack states
#: about an effect with no check of its own.
#:
#: Lives here rather than in the release decision because two consumers read
#: it: the decision, to keep the blocker, and the human Control Pack section,
#: to keep explaining a blocker the decision kept. They disagreed, and a
#: report said BLOCKED while naming nothing that blocked it.
MANDATORY_CURRENT_CONTROL_CHECKS = frozenset(
    {
        "SHIP-ACTION-WILDCARD-SCOPE",
        "SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING",
        "SHIP-ACTION-EXTERNAL-COMMUNICATION-AUDIT-MISSING",
        "SHIP-ACTION-DESTRUCTIVE-ROLLBACK-MISSING",
    }
)


def is_mandatory_current_control(finding) -> bool:
    """Is ``finding`` a control a suppression cannot waive?"""

    if finding.check_id in MANDATORY_CURRENT_CONTROL_CHECKS:
        return True
    if finding.check_id != "SHIP-ACTION-POLICY-VIOLATION":
        return False
    return is_control_pack_finding(finding)


#: Separator joining the effects one pack rule covers inside a ``policy_id``.
#: Effect names never contain it, so the id round-trips.
CONTROL_PACK_EFFECT_SEPARATOR = "+"


def control_pack_policy_id(effects: Sequence[str]) -> str:
    """``evidence.policy_id`` for one pack rule, written in one place.

    Takes the whole effect group rather than one effect: a pack requiring the
    same controls for a write and a privileged read states *one* rule about an
    action that does both, and the reader has *one* edit to make. Emitting a
    finding per effect would say the same sentence three times about one
    action — the shape #410 exists to remove.

    The **pack name is deliberately absent** (#410 §F review). ``policy_id``
    is a fingerprint input, and two packs that require the same controls for
    the same effect state the same rule — naming the pack would re-fingerprint
    an identical finding on an identical action just because the manifest
    switched between them, silently dropping the baseline entry that accepted
    it. Which pack is in force is ``evidence.control_pack``, which is excluded
    from the fingerprint precisely because it is context rather than identity.
    Where two packs require *different* controls the ``missing`` rows differ,
    so the fingerprint still moves — for the reason that actually changed.
    """

    joined = CONTROL_PACK_EFFECT_SEPARATOR.join(sorted(effects))
    return f"{CONTROL_PACK_POLICY_ID_PREFIX}{joined}"


def is_control_pack_finding(finding) -> bool:
    """Did *this engine* raise ``finding`` for a pack obligation?

    #410 §F review. Deciding this from the ``policy_id`` string alone read a
    user-authored ``action_surface.policies[].id`` that happened to match the
    grammar as engine-minted — which would have made that user's own rule
    non-waivable, and (worse) turned their ``checks.ignore`` entry into a
    blocker. Provenance is ``evidence.control_pack``, which only this engine
    writes; the id grammar is checked too, so a stray ``control_pack`` on some
    other action-policy finding cannot claim the route either.

    ``action_surface.policies[].id`` additionally rejects the reserved prefix
    at manifest load, so the collision is refused before it can be raised.
    Both layers are kept: the validator is the guarantee, and this predicate
    is what a report loaded from disk — written by any build — is judged on.
    """

    evidence = getattr(finding, "evidence", None) or {}
    if evidence.get("control_pack") not in BUILTIN_CONTROL_PACKS:
        return False
    policy_id = evidence.get("policy_id")
    if policy_id == HIGH_IMPACT_APPROVAL_POLICY_ID:
        return True
    if not isinstance(policy_id, str) or not policy_id.startswith(
        CONTROL_PACK_POLICY_ID_PREFIX
    ):
        return False
    effects = policy_id[len(CONTROL_PACK_POLICY_ID_PREFIX) :].split(
        CONTROL_PACK_EFFECT_SEPARATOR
    )
    return bool(effects) and all(part in ACTION_EFFECT_RANK for part in effects)


def finding_control_rule(finding) -> tuple[str, str, tuple[str, ...]] | None:
    """``(pack_id, action_id, effects)`` for one control-rule row, else ``None``.

    Every field is *stamped by the emitter*, not reconstructed. The first
    draft recovered the effects from the check id, and from the pack rule
    named in ``policy_id`` — which was right for the two routes that carry the
    effect in their identity and wrong for the third: the built-in high-impact
    rule shares one id between ``production_operation`` and ``code_execution``,
    so an action doing only one of them was reported as doing both, and the
    rule row then unioned the obligations of both and demanded a rollback the
    actual rule never asked for (#410 §F review). Read what the finding says
    about itself.

    A rule row is about an **action**, so the discriminator is an
    ``action_id``. The tool-level ``SHIP-POLICY-APPROVAL-MISSING`` carries the
    pack and speaks about the same missing approval, but about a whole tool:
    counting it too would report two actions short where one is. Excluding it
    by naming its check id would be a guard scoped to one id shape — vacuous
    for the next check that carries a pack. The structural test is the one
    that stays true.
    """

    evidence = getattr(finding, "evidence", None) or {}
    pack_id = evidence.get("control_pack")
    if not isinstance(pack_id, str) or pack_id not in BUILTIN_CONTROL_PACKS:
        return None
    action_id = evidence.get("action_id")
    if not isinstance(action_id, str) or not action_id:
        return None
    effects = evidence.get("control_effects")
    if not isinstance(effects, list) or not effects:
        return None
    return pack_id, action_id, tuple(sorted(str(effect) for effect in effects))


def control_rule_summaries(findings) -> list[ControlRuleSummary]:
    """The pack rules some scanned action is still short of, most first.

    #410 §F: seven findings saying "lacks a declared approval policy" never
    said *which rule* wanted one. This is that sentence — one row per rule
    rather than one per tool — and it is one function so the console summary
    and ``report.md`` cannot render different counts from the same report.

    Counted by *action*, because one action missing three controls is one
    thing to go and fix.
    """

    counted: dict[tuple[str, tuple[str, ...]], set[str]] = {}
    for finding in findings:
        # A suppression explains accepted noise, and for a mandatory
        # current-surface control it does not waive the blocker — the release
        # decision keeps it in ``blockers[]``. Dropping those rows here left a
        # report that says BLOCKED with no Control Pack section and no console
        # line naming the rule doing the blocking (#410 §F review). One
        # predicate, shared with the release decision, decides both.
        if getattr(finding, "suppressed", False) and not is_mandatory_current_control(
            finding
        ):
            continue
        rule = finding_control_rule(finding)
        if rule is None:
            continue
        pack_id, action_id, effects = rule
        counted.setdefault((pack_id, effects), set()).add(action_id)
    summaries: list[ControlRuleSummary] = []
    for (pack_id, effects), subjects in counted.items():
        pack = BUILTIN_CONTROL_PACKS[pack_id]
        controls = frozenset().union(
            *(pack.obligations_for(effect) for effect in effects)
        )
        summaries.append(
            ControlRuleSummary(
                pack=pack,
                effects=effects,
                controls=tuple(ordered_controls(controls)),
                action_count=len(subjects),
            )
        )
    summaries.sort(key=lambda row: (-row.action_count, row.effects))
    return summaries


def manifest_control_pack_block(selected: str) -> list[str]:
    """The one question ``init`` asks, written into the manifest as one line.

    #410 §F. The alternatives and what each means are rendered from
    :data:`~agents_shipgate.core.control_packs.BUILTIN_CONTROL_PACKS` rather
    than typed out here: a glossary that is a copy of the table is a glossary
    that goes stale, and the missing glossary is half of what made the old
    per-tool declarations unanswerable.

    ``default`` is written explicitly even though omitting the key means the
    same thing. A line you can see is a question you can answer; an absent
    key is a question nobody knows was asked.
    """

    lines = [
        "  # Which controls each action effect requires — one answer for the",
        "  # repository instead of one per tool. Every pack requires at least",
        "  # what `default` requires, so changing this can only tighten the gate.",
    ]
    width = max(len(pack_id) for pack_id in CONTROL_PACK_IDS)
    for pack_id in CONTROL_PACK_IDS:
        summary = BUILTIN_CONTROL_PACKS[pack_id].summary
        first, *rest = _wrap_comment(summary, width)
        lines.append(f"  #   {pack_id.ljust(width)} — {first}")
        for continuation in rest:
            lines.append(f"  #   {' ' * width}   {continuation}")
    lines.append(f"  control_pack: {selected}")
    return lines


def _wrap_comment(text: str, indent_width: int) -> list[str]:
    """Wrap one pack summary to the manifest's comment width."""

    budget = max(_MANIFEST_COMMENT_WIDTH - indent_width - 8, 24)
    words = text.split()
    rows: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > budget:
            rows.append(current)
            current = word
        else:
            current = candidate
    if current:
        rows.append(current)
    return rows or [""]


def pack_only_effect_groups(
    pack: ControlPack, effects: Iterable[str]
) -> list[tuple[tuple[str, ...], frozenset[str]]]:
    """Group ``effects`` with no dedicated check by the controls they oblige.

    One rule, one finding, one edit. Ordered by the group's effects so a scan
    emits them the same way every time.
    """

    grouped: dict[frozenset[str], list[str]] = {}
    for effect in effects:
        if effect in EFFECTS_WITH_DEDICATED_CONTROL_CHECK:
            continue
        controls = pack.obligations_for(effect)
        if not controls:
            continue
        grouped.setdefault(controls, []).append(effect)
    return sorted(
        ((tuple(sorted(members)), controls) for controls, members in grouped.items()),
        key=lambda row: row[0],
    )


def weakened_pack_obligations(
    base_pack_id: str | None,
    head_pack_id: str | None,
) -> list[tuple[str, list[str]]]:
    """Effects the head pack requires less of than the base pack did.

    ``None`` on either side means "the field is absent", which is by
    construction the ``default`` rule set — a build that could not load a
    manifest naming a pack. It is resolved to ``default`` and compared rather
    than skipped, so the "no pack is weaker than default" invariant is enforced
    here instead of assumed: if a weaker pack were ever added, this comparison
    would report it rather than stay silent.

    An id this build cannot resolve returns ``[]`` — "cannot compare" is not
    "nothing changed", and every caller has to route that fail-safe itself
    because what to do about it differs: ``verify_policy`` raises a finding
    that says the direction is unprovable, while a setup route hands the
    manifest to a person.

    It lives here rather than in the check that first needed it because the
    setup routes need the same answer, and this is the module that owns what a
    pack requires. Two implementations of "did this get weaker?" is how one of
    them ends up not seeing a downgrade (#410 §F).
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


def control_pack_by_id(pack_id: str | None) -> ControlPack:
    """The pack ``pack_id`` names, or ``default`` when the manifest is silent.

    Unknown ids cannot reach here: the manifest schema restricts the field to
    :data:`CONTROL_PACK_IDS`, so a typo is a config error at load time rather
    than a silent fallback to the loosest pack.
    """

    if not pack_id:
        return DEFAULT_CONTROL_PACK
    return BUILTIN_CONTROL_PACKS[pack_id]


def resolve_control_pack(manifest) -> ControlPack:
    """The one place a manifest becomes a pack.

    Every caller resolves through here rather than reading
    ``manifest.policies.control_pack`` itself, so the rules the action lens
    applies and the rules the tool-level checks apply cannot come from two
    readings of the same field.
    """

    policies = getattr(manifest, "policies", None)
    return control_pack_by_id(getattr(policies, "control_pack", None))


__all__ = [
    "BUILTIN_CONTROL_PACKS",
    "DEDICATED_CONTROL_CHECKS",
    "EFFECTS_WITH_DEDICATED_CONTROL_CHECK",
    "HIGH_IMPACT_EFFECTS",
    "CONTROL_PACK_IDS",
    "CONTROL_PACK_POLICY_ID_PREFIX",
    "DEFAULT_CONTROL_PACK",
    "DEFAULT_CONTROL_PACK_ID",
    "FINANCIAL_STRICT_CONTROL_PACK",
    "HIGH_IMPACT_APPROVAL_POLICY_ID",
    "READ_ONLY_AGENT_CONTROL_PACK",
    "ControlPack",
    "ControlRuleSummary",
    "control_pack_by_id",
    "MANDATORY_CURRENT_CONTROL_CHECKS",
    "control_pack_policy_id",
    "pack_only_effect_groups",
    "manifest_control_pack_block",
    "control_rule_summaries",
    "finding_control_rule",
    "is_control_pack_finding",
    "is_mandatory_current_control",
    "resolve_control_pack",
    "weakened_pack_obligations",
]
