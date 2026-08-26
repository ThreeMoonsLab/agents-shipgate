from __future__ import annotations

from collections.abc import Iterable, Sequence

from agents_shipgate.schemas.surfaces import ActionEffect

ACTION_EFFECT_RANK: dict[ActionEffect, int] = {
    "read": 0,
    "privileged_data_access": 1,
    "write": 2,
    "external_communication": 3,
    "financial_write": 4,
    "production_operation": 4,
    "identity_access": 4,
    "code_execution": 4,
    "destructive": 5,
}

#: The built-in controls each effect obliges, as the dot paths
#: ``_current_action_policy_findings`` requires plus the ``confirmation``
#: policy it looks up separately. Effects absent from this table oblige no
#: built-in control.
#:
#: Effects are **not** totally ordered by obligation. ``financial_write``
#: outranks ``external_communication`` on risk while requiring no confirmation,
#: which is exactly what communicating outward requires — so a declaration
#: cannot discharge one category by naming a higher-risk different one. The
#: control evaluator has always read effects as a *set* for this reason
#: (``_control_effects``); this table is the same fact in a form the
#: declaration comparator can read.
#:
#: ``test_the_builtin_obligation_table_matches_the_controls_that_fire`` walks
#: every entry through a real scan, so the table cannot drift from the branches
#: it mirrors.
BUILTIN_EFFECT_OBLIGATIONS: dict[ActionEffect, frozenset[str]] = {
    "financial_write": frozenset(
        {"approval.required", "safeguards.audit_log", "safeguards.idempotency"}
    ),
    "external_communication": frozenset({"safeguards.audit_log", "confirmation.required"}),
    "destructive": frozenset(
        {"approval.required", "safeguards.rollback", "confirmation.required"}
    ),
    "production_operation": frozenset({"approval.required"}),
    "code_execution": frozenset({"approval.required"}),
}


#: How each built-in control path is spelled in a sentence an adopter reads.
#:
#: Four of the five are the manifest key itself, under
#: ``action_surface.actions[]`` — naming them any other way would send the
#: reader looking for a field that does not exist.  ``confirmation.required``
#: is the exception and the reason this table is not ``{path: path}``: no
#: action row carries it.  It is satisfied from
#: ``policies.require_confirmation_for_tools``, so the sentence names the
#: policy rather than a key nobody can write.
_CONTROL_PHRASES: dict[str, str] = {
    "approval.required": "approval.required",
    "confirmation.required": "confirmation policy",
    "safeguards.audit_log": "safeguards.audit_log",
    "safeguards.idempotency": "safeguards.idempotency",
    "safeguards.rollback": "safeguards.rollback",
    "safeguards.dry_run": "safeguards.dry_run",
}

#: Reading order for a control list, so one set of missing controls has one
#: sentence regardless of which branch collected it or in what order.  The
#: order is the order a reviewer decides in — who authorises, who confirms,
#: then what the system records and undoes.
_CONTROL_ORDER: tuple[str, ...] = (
    "approval.required",
    "confirmation.required",
    "safeguards.audit_log",
    "safeguards.idempotency",
    "safeguards.rollback",
    "safeguards.dry_run",
)


def ordered_controls(paths: Iterable[str]) -> list[str]:
    """``paths`` in the order a reviewer decides in, unknown paths last.

    Exported so the sentence telling a reader what to declare and the control
    pack's own rule listing put the same set of controls in the same order.
    Sorting alphabetically instead reads "confirmation policy and
    safeguards.audit_log" in one surface and "safeguards.audit_log and
    confirmation policy" in another, for one rule.
    """

    wanted = {str(path) for path in paths}
    known = [path for path in _CONTROL_ORDER if path in wanted]
    return known + sorted(wanted - set(_CONTROL_ORDER))


def builtin_obligations(effect: ActionEffect) -> frozenset[str]:
    """The built-in controls ``effect`` obliges — empty for effects with none."""

    return BUILTIN_EFFECT_OBLIGATIONS.get(effect, frozenset())


def control_phrase(path: str) -> str:
    """How ``path`` is spelled in a sentence, or the path itself.

    Exported because the reader of the table has to be the same as its writer.
    ``report.md`` drops a recommendation that only repeats the row it sits
    under, and deciding that by looking for the raw *path* in the sentence
    misses every control this table renames — ``confirmation.required`` is
    written "confirmation policy", so the check said "not the same fact" about
    a sentence built from exactly that fact.
    """

    return _CONTROL_PHRASES.get(path, path)


def effect_phrase(effect: str) -> str:
    """``financial_write`` as a reader says it: ``financial write``."""

    return str(effect).replace("_", " ")


def join_phrases(phrases: Sequence[str]) -> str:
    """``a``, ``a and b``, ``a, b, and c`` — the serial comma this repo uses."""

    items = list(phrases)
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return f"{', '.join(items[:-1])}, and {items[-1]}"


def missing_control_recommendation(
    effects: Sequence[str],
    missing: Sequence[str],
) -> str:
    """The sentence telling a reader what is left to declare — and only that.

    Issue #364: this used to be a per-check literal naming every control the
    effect obliges, which is a different list from the one the finding fired
    on.  An action that already declared ``approval.required`` was told to
    declare it again, in the same finding whose ``evidence.missing`` says it
    is present — so following the sentence costs a round and returns the
    reader to the same finding.

    Deriving the sentence from ``missing`` is not enough on its own; the
    caller could still pass a different list to ``evidence``.  The two are
    built from one value at one call site (``_builtin_control_finding``),
    which is what makes them agree by construction rather than by review.

    Unknown paths are rendered verbatim.  A control this table has not
    learned to spell is still a control the reader has to declare, and
    dropping it silently would put the sentence back in the business of
    disagreeing with the evidence.

    An empty ``missing`` cannot happen — every branch that calls this is
    inside ``if missing:`` — so it is a wiring mistake rather than a state,
    and the answer to it is the pre-#364 sentence rather than ``Declare  for
    this … action.``  A hole in a sentence is the one outcome that helps
    nobody, and raising here would abort a scan over a rendering detail.
    """

    wanted = set(missing) or set().union(
        *(builtin_obligations(effect) for effect in effects), set()
    )
    phrases = [control_phrase(path) for path in ordered_controls(wanted)]
    subject = join_phrases([effect_phrase(effect) for effect in effects])
    return f"Declare {join_phrases(phrases)} for this {subject} action."


def normalize_declared_strings(values: Iterable[str]) -> list[str]:
    """Declared token lists as every surface compares them: stripped, deduped, sorted.

    One rule for the two lists an ``action_surface.actions`` row can carry —
    ``scopes`` and ``risk_tags`` — because comparing them is what decides
    whether a declaration matches, broadens, or narrows.
    """

    return sorted({str(value).strip() for value in values if str(value).strip()})


__all__ = [
    "ACTION_EFFECT_RANK",
    "BUILTIN_EFFECT_OBLIGATIONS",
    "builtin_obligations",
    "control_phrase",
    "effect_phrase",
    "join_phrases",
    "missing_control_recommendation",
    "normalize_declared_strings",
    "ordered_controls",
]
