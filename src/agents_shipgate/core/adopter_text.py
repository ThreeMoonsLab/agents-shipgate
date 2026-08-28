"""The vocabulary rule for text an adopter is expected to read and act on.

Invariant 5 of the adoption walk (#327), filed as #329: *proceeding must not
require understanding the internal identity model*. The clearest violation was
the failure #321 reported, printed verbatim at someone running the tool on
their own repository for the first time::

    Duplicate tool observation identity: source_type='google_adk_function',
    source_id='google_adk:agent.py',
    native_locator='agent.py#map_salesforce_account_to_sap_bp'

Three internal concepts, none of which appears in the manifest that person
wrote, two of them derived — and the one recoverable fact, that a file was read
twice, is the one thing the message does not say.

The distinction this module enforces is *not* "internal identifiers are bad".
They are the identity model and they are supposed to be precise wherever
tooling reads them: ``report.json`` evidence blocks, the tool catalog, and the
verification artifacts keep them. The rule applies to the other set — the
strings whose whole purpose is to tell a person what to do next:

* console output (failures, and the next-action hints printed beside them),
* the agent-mode error envelope and its ``next_action`` / ``next_actions[]``,
* ``agent-handoff.json`` prose, including ``fix_task.instructions[]``,
* PR comment text,
* and the evidence-gap rows those four render.

Two categories, because the terms are not equally unlocatable.

:data:`INTERNAL_ONLY_TERMS` name things that exist nowhere an adopter can open.
There is no ``native_locator`` key in any manifest, no observation id in any
file they wrote. These are refused outright.

:data:`MANIFEST_SPELLED_TERMS` are the awkward ones: ``source_id`` and
``source_type`` really are manifest keys — ``tool_identity.bindings[].members[]``
accepts both, ``tool_inventories[].source_id`` and ``agent_bindings.root.source_id``
accept the first — and ``fingerprint`` really is a published ``findings[]`` field.
Spelled with the surface they belong to, they are locatable; spelled bare, they
are the internal model leaking. So they are allowed only in a message that also
names where to find them (:data:`LOCATABLE_ANCHORS`).

The rule is checked on *rendered* messages, not on fragments, because the
rendered message is what the person reads: an ``accepted_values`` list of
selector keys is fine next to ``at shipgate.yaml#tool_identity.bindings`` and
meaningless on its own.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

__all__ = [
    "AGENT_ID_PATTERN",
    "DUPLICATE_ACROSS_ARTIFACTS",
    "DUPLICATE_IN_SOURCE_ARTIFACT",
    "DUPLICATE_TOOL_IN_SOURCE",
    "FINGERPRINT_PATTERN",
    "INTERNAL_ID_SHAPES",
    "INTERNAL_ONLY_TERMS",
    "LOCATABLE_ANCHORS",
    "MANIFEST_SPELLED_TERMS",
    "OBSERVATION_ID_PATTERN",
    "REPEATED_SOURCE_ENTRY",
    "TOOL_ID_PATTERN",
    "TOOL_SOURCE_MISMATCH",
    "duplicate_tool_observation_message",
    "internal_vocabulary",
    "names_a_locatable_anchor",
    "overlapping_binding_message",
    "source_label",
]

#: ``AgentsShipgateError.details["failure"]`` for a tool read twice from one
#: source. Routing on a typed key rather than on the message text is what lets
#: the sentence be rewritten for a human without breaking the recovery route.
DUPLICATE_TOOL_IN_SOURCE = "duplicate_tool_in_source"

#: ``details["failure"]`` for a loader that reported a tool as belonging to a
#: source other than the one it was read from. Alone among the input failures
#: this one is not in the adopter's repository at all: it is a defect in
#: adapter code, so no ``path`` is routable and the recovery is to repair or
#: upgrade the adapter. It carries a typed key for the same reason the
#: duplicate does — the sentence is for the reader, ``details`` is for the
#: caller, and an untyped failure falls through to the generic review action
#: with no route at all.
TOOL_SOURCE_MISMATCH = "tool_source_mismatch"

#: ``details["cause"]``. Two different mistakes reach the duplicate check and
#: they have opposite repairs, so the check reports which one it saw rather
#: than naming both and letting the reader pick. Guessing here is not free:
#: the structured action carries one ``path``, and a consumer routing on it
#: would delete a source declaration when the real fix was inside the file.
#:
#: ``REPEATED_SOURCE_ENTRY`` — the same artifact was read twice for one source
#: id, which is a repeated entry in the manifest.
#: ``DUPLICATE_IN_SOURCE_ARTIFACT`` — one read of one artifact produced the
#: same tool twice, which is a duplicate definition inside that artifact.
#: ``DUPLICATE_ACROSS_ARTIFACTS`` — two *different* files declared one
#: capability under one name. Neither of the other two sentences is true of it:
#: the manifest names no artifact twice, and no artifact defines the tool twice.
#: Reporting it as a repeated entry told the reader to delete a manifest line
#: that does not exist.
REPEATED_SOURCE_ENTRY = "repeated_source_entry"
DUPLICATE_IN_SOURCE_ARTIFACT = "duplicate_in_source_artifact"
DUPLICATE_ACROSS_ARTIFACTS = "duplicate_across_artifacts"

#: Identity-model terms with no counterpart in anything an adopter writes or
#: opens. No anchor rescues these: there is nowhere to send the reader.
INTERNAL_ONLY_TERMS: tuple[str, ...] = (
    "native_locator",
    "observation_id",
    "observation identity",
)

#: Nothing may precede a derived id but a boundary. Agent names, tool names and
#: source labels are adopter-controlled strings, and an unbounded search called
#: an agent legitimately named ``customer_agent_v1_deadbeef`` a derived id —
#: which, once that agent has an evidence gap, aborts an otherwise valid scan
#: through the conservation invariant. The reserved shapes only ever appear at
#: the start of a token or inside a bracketed label.
_NOT_A_WORD_CHAR_BEFORE = r"(?<![0-9A-Za-z_])"

#: And nothing may follow one either. A leading boundary alone still matched
#: the *prefix* of an adopter identifier: `tool_v2_deadbeef_helper` is a legal
#: tool name, and matching `tool_v2_deadbeef` inside it aborts validation on a
#: gap that labels that tool. The digest is greedy, so a real 64-hex id is
#: unaffected — the character after it is a bracket, a space, or the end.
_NOT_A_WORD_CHAR_AFTER = r"(?![0-9A-Za-z_])"

#: The producer's exact separator, not a permissive class. ``agent_v1:`` is the
#: only form ``core.agent_bindings`` emits, so accepting ``agent_v1_`` bought
#: nothing and matched adopter identifiers that merely read like one. Tools keep
#: ``[_:]``: that pattern predates this module and guards a shipped invariant.
TOOL_ID_PATTERN = re.compile(
    _NOT_A_WORD_CHAR_BEFORE + r"tool_v[0-9]+[_:][0-9a-f]{8,}" + _NOT_A_WORD_CHAR_AFTER
)
AGENT_ID_PATTERN = re.compile(
    _NOT_A_WORD_CHAR_BEFORE + r"agent_v[0-9]+:[0-9a-f]{8,}" + _NOT_A_WORD_CHAR_AFTER
)
OBSERVATION_ID_PATTERN = re.compile(
    _NOT_A_WORD_CHAR_BEFORE + r"obs_v[0-9]+_[0-9a-f]{8,}" + _NOT_A_WORD_CHAR_AFTER
)
FINGERPRINT_PATTERN = re.compile(
    _NOT_A_WORD_CHAR_BEFORE + r"fp_[0-9a-f]{16,}" + _NOT_A_WORD_CHAR_AFTER
)

#: Derived identifiers, matched by *shape* rather than by the field name that
#: carries them. A message can print an observation id or a canonical tool id
#: without ever saying "observation_id", and the digest is the part the reader
#: cannot do anything with — the lesson the gap-subject conservation rule
#: already learned: match the shape, not the spelling.
#:
#: These are refused like :data:`INTERNAL_ONLY_TERMS`, with one deliberate
#: exception that is not in the swept set: a command whose *argument* is one of
#: these identifiers — ``explain-finding <FINGERPRINT>`` — may show an example
#: in its own ``--help``, because there the identifier is the input the reader
#: supplies, and the help says which report field to copy it from.
#:
#: ``core.surface_exclusions`` builds the report's conservation invariant from
#: the same patterns. One definition, because the two rules are one rule read
#: from opposite ends: a subject a reader cannot open, and a sentence that
#: names one.
INTERNAL_ID_SHAPES: dict[str, re.Pattern[str]] = {
    "observation": OBSERVATION_ID_PATTERN,
    "tool": TOOL_ID_PATTERN,
    "agent": AGENT_ID_PATTERN,
    "finding": FINGERPRINT_PATTERN,
}

#: Internal spellings that are *also* real keys on a surface the adopter owns.
#: Locatable when the message names that surface, internal vocabulary when not.
MANIFEST_SPELLED_TERMS: tuple[str, ...] = (
    "source_type",
    "source_id",
    "fingerprint",
)

#: Surfaces an adopter can open. Naming one of these anchors the terms in
#: :data:`MANIFEST_SPELLED_TERMS`: the reader is told which file, and which key
#: in it, rather than being expected to already know the field exists.
#:
#: ``baseline`` is the loosest entry and is deliberate: the baseline is a file
#: the adopter owns and regenerates, and the messages about it explain
#: fingerprints as a property of that file rather than asking the reader to
#: know what one is. Everything else here is a filename or a manifest key path.
LOCATABLE_ANCHORS: tuple[str, ...] = (
    "shipgate.yaml",
    "report.json",
    "findings[]",
    "tool_sources",
    "tool_inventories",
    "tool_identity",
    "agent_bindings",
    "action_surface",
    "baseline",
)


def names_a_locatable_anchor(text: str) -> bool:
    """Whether ``text`` names a file or manifest key the adopter can open."""

    lowered = text.lower()
    return any(anchor in lowered for anchor in LOCATABLE_ANCHORS)


def internal_vocabulary(
    text: str, *, given_id_kinds: Iterable[str] = ()
) -> tuple[str, ...]:
    """Internal identity terms ``text`` uses without making them locatable.

    Empty for text that satisfies the rule. Sorted and de-duplicated so a
    failure message reads the same on every run.

    ``given_id_kinds`` names the id kinds a surface is allowed to carry,
    because a derived id is not always a demand on the reader:

    * a generated declaration template writes a **tool** id into the block for
      them to paste, so a selector still resolves when two tools share a name
      (#388); dropping it would put the ambiguity back;
    * ``explain-finding`` echoes the **finding** fingerprint the reader just
      supplied, with the report field to copy the right one from.

    Per kind, not per surface. A blanket exemption forgave every shape on a
    surface that had a reason for exactly one, so an agent id in the
    `explain-finding` prose — which the reader was never given — passed
    unnoticed (#329 review 3). The term rules apply either way: a template or
    an echo may carry an id, not a sentence about one.
    """

    allowed = set(given_id_kinds)
    unknown = allowed - set(INTERNAL_ID_SHAPES)
    if unknown:
        raise ValueError(f"unknown derived id kinds: {sorted(unknown)}")
    offenders = {term for term in INTERNAL_ONLY_TERMS if term in text}
    offenders.update(
        match.group(0)
        for kind, shape in INTERNAL_ID_SHAPES.items()
        if kind not in allowed
        for match in shape.finditer(text)
    )
    if not names_a_locatable_anchor(text):
        offenders.update(term for term in MANIFEST_SPELLED_TERMS if term in text)
    return tuple(sorted(offenders))


def source_label(*, file_path: str | None, source_id: str) -> str:
    """Name a tool source the way the adopter can find it.

    A file wins whenever one is known: every framework entrypoint and every
    declared artifact is a path the adopter listed in ``shipgate.yaml`` and can
    open. Only a source whose loader recorded no path at all falls back to the
    id, which for those is the ``tool_sources[].id`` the adopter wrote — still
    something they can find, just one indirection further away.
    """

    return repr(file_path) if file_path else repr(source_id)


def duplicate_tool_observation_message(
    *,
    tool_name: str,
    file_path: str | None,
    source_id: str,
    cause: str,
    other_file_path: str | None = None,
) -> str:
    """The adopter-facing form of a duplicate tool inside one tool source.

    Three mistakes reach the duplicate check with different repairs, and
    ``cause`` says which one the check actually saw (see
    :data:`REPEATED_SOURCE_ENTRY`). Naming them all and hedging was the first
    draft of this message; it reads as helpful and is not, because the
    structured action beside it can only carry one target.

    ``other_file_path`` is the file the *first* read of this identity came
    from, and it is what distinguishes two files declaring one capability from
    one file read twice. Without it the message asserted a repeated manifest
    entry for a manifest that names each file once.

    The identity triple that detected it — ``source_type``, ``source_id``,
    ``native_locator`` — stays in the error's ``details`` for a bug report.
    """

    where = source_label(file_path=file_path, source_id=source_id)
    if cause == DUPLICATE_ACROSS_ARTIFACTS:
        other = source_label(file_path=other_file_path, source_id=source_id)
        return (
            f"The tool {tool_name!r} was read from both {other} and {where}, "
            "which declare it under one name, so one capability arrived twice. "
            "Give the two declarations distinct names, or bring both files "
            "under a single shipgate.yaml tool_sources entry so they are "
            "reconciled as one declaration, then re-run the scan."
        )
    if cause == REPEATED_SOURCE_ENTRY:
        return (
            f"{where} was read twice as one tool source, so the tool "
            f"{tool_name!r} arrived twice. Remove the repeated shipgate.yaml "
            f"entry naming {where}, then re-run the scan."
        )
    return (
        f"{where} defines the tool {tool_name!r} more than once, so one tool "
        f"source produced it twice. Remove the duplicate definition from "
        f"{where}, then re-run the scan."
    )


def overlapping_binding_message(
    *, tool_name: str, file_path: str | None, source_id: str, binding_ids: Iterable[str]
) -> str:
    """The adopter-facing form of one tool claimed by several bindings.

    Previously written as ``Tool observation obs_v1_<64 hex> appears in
    multiple bindings`` — an id that appears in no file the adopter has,
    naming a tool they could otherwise have opened.
    """

    where = source_label(file_path=file_path, source_id=source_id)
    ids = ", ".join(repr(binding_id) for binding_id in sorted(binding_ids))
    return (
        f"Tool {tool_name!r} from {where} is claimed by more than one "
        f"tool_identity.bindings entry: {ids}. A tool may be joined by at most "
        "one binding — leave it in a single entry, then re-run the scan."
    )
