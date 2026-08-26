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
    BUILTIN_EFFECT_OBLIGATIONS,
    ordered_controls,
)
from agents_shipgate.schemas.common import Severity
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
        "Money and identity move under review: every state change is recorded, "
        "financial writes are confirmed, and privileged reads leave a trail."
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
#: the repository's own pack says the effect requires.
CONTROL_PACK_POLICY_ID_PREFIX = "control-pack:"


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


#: Separator joining the effects one pack rule covers inside a ``policy_id``.
#: Effect names never contain it, so the id round-trips.
CONTROL_PACK_EFFECT_SEPARATOR = "+"


def control_pack_policy_id(pack_id: str, effects: Sequence[str]) -> str:
    """``evidence.policy_id`` for one pack rule, written in one place.

    Takes the whole effect group rather than one effect: a pack requiring the
    same controls for a write and a privileged read states *one* rule about an
    action that does both, and the reader has *one* edit to make. Emitting a
    finding per effect would say the same sentence three times about one
    action — the shape #410 exists to remove.
    """

    joined = CONTROL_PACK_EFFECT_SEPARATOR.join(sorted(effects))
    return f"{CONTROL_PACK_POLICY_ID_PREFIX}{pack_id}:{joined}"


def is_control_pack_policy_id(policy_id: object) -> bool:
    """Is ``policy_id`` one this engine minted for a pack obligation?

    Matching the bare prefix would also claim a *user* policy someone happened
    to name ``control-pack:…`` — and this predicate decides whether a finding
    can be waived by ``checks.ignore``, so it should recognise only ids whose
    pack segment is a pack that exists.
    """

    if not isinstance(policy_id, str) or not policy_id.startswith(
        CONTROL_PACK_POLICY_ID_PREFIX
    ):
        return False
    remainder = policy_id[len(CONTROL_PACK_POLICY_ID_PREFIX) :]
    pack_id, _, effects = remainder.partition(":")
    if pack_id not in BUILTIN_CONTROL_PACKS:
        return False
    parts = effects.split(CONTROL_PACK_EFFECT_SEPARATOR)
    return bool(parts) and all(
        part in BUILTIN_CONTROL_PACKS[pack_id].obligations for part in parts
    )


def finding_control_rule(finding) -> tuple[str, tuple[str, ...]] | None:
    """``(pack_id, effects)`` for a built-in control finding, else ``None``.

    The pack id is stamped on the finding rather than looked up from the
    manifest because the only caller that needs it — the human report — is
    handed a report, not a workspace. Effects come from the *shipped* check
    id, or from the pack rule named in ``evidence.policy_id`` where the id is
    the generic action-policy one.
    """

    evidence = getattr(finding, "evidence", None) or {}
    pack_id = evidence.get("control_pack")
    if not isinstance(pack_id, str) or pack_id not in BUILTIN_CONTROL_PACKS:
        return None
    effects = _CHECK_ID_EFFECTS.get(finding.check_id)
    if effects is not None:
        return pack_id, effects
    if finding.check_id != "SHIP-ACTION-POLICY-VIOLATION":
        # The tool-level ``SHIP-POLICY-*`` checks carry the pack but speak
        # about a whole tool rather than one effect family. They are counted
        # by the rule they enforce, not by a rule of their own, so a reader
        # is not shown "approval" twice for the same missing approval.
        return None
    policy_id = evidence.get("policy_id")
    if policy_id == HIGH_IMPACT_APPROVAL_POLICY_ID:
        return pack_id, tuple(sorted(HIGH_IMPACT_EFFECTS))
    if is_control_pack_policy_id(policy_id):
        joined = str(policy_id).rsplit(":", 1)[-1]
        return pack_id, tuple(sorted(joined.split(CONTROL_PACK_EFFECT_SEPARATOR)))
    return None


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
        if getattr(finding, "suppressed", False):
            continue
        rule = finding_control_rule(finding)
        if rule is None:
            continue
        subject = (
            (getattr(finding, "evidence", None) or {}).get("action_id")
            or finding.tool_id
            or finding.tool_name
            or ""
        )
        counted.setdefault(rule, set()).add(str(subject))
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
    "control_pack_policy_id",
    "pack_only_effect_groups",
    "manifest_control_pack_block",
    "control_rule_summaries",
    "finding_control_rule",
    "is_control_pack_policy_id",
    "resolve_control_pack",
]
