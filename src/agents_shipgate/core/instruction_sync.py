"""Fail-closed recognition of managed-field version synchronization.

This module does not approve an agent-instruction change.  It recognizes one
narrow shape that a static evaluator *can* prove is not an instruction
weakening: a diff over an agent-instruction **prose document** whose old and
new line sequences are identical position for position except at
**managed-field values**, where each new value is the published value of the
exact contract field naming it.

Why recognition is positive rather than exclusionary
-----------------------------------------------------
An earlier form of this exception masked any version-shaped token and refused a
list of conditional phrasings (``>=``, "at least", "or later").  That cannot be
made sound.  Prose encodes constraints in unbounded ways — "Allow contract
versions **through** v14", "Reject contract versions **after** v14" — and each
of those changes meaning when the number moves, while no blacklist can
enumerate them.  Deciding which sentences are rules and which are facts is
precisely the semantic judgement Shipgate does not make (Principle 3: prompts
are not controls).

So nothing is recognized unless it matches a template whose meaning is fixed by
structure rather than by English:

* the token is inside a code span whose entire content is
  ``<field><separator><value>`` — a quoted machine literal, e.g. "…report
  ``minimum_control_contract_version: 14``."; or
* the whole line, trimmed of list markers and trailing punctuation, is exactly
  ``<field><separator><value>`` — a fenced-block or YAML/JSON snippet line.

``<field>`` must be an exact key of :func:`build_contract_payload`, the values
``agents-shipgate contract --json`` publishes, and the new value must be the
published value **of that field**.  Binding to the field is what keeps a real
version from being written where it does not belong: ``report_schema_version``
cannot take the contract's number merely because that number is published
somewhere.

Because the field name sits outside the value span, it is compared as prose —
renaming the field is a prose change and is refused.

Consequences worth stating plainly: a version cited in ordinary prose
("Contract v14 publishes these boundaries") is **not** recognized and keeps the
standing human route.  That is deliberate.  A monotonicity rule is likewise
absent and would be wrong here: correcting a document that claims
``contract_version: 99`` down to the published ``19`` is a valid
synchronization, and field binding already proves the new value is the true
one.

Deliberately out of scope, all of which keep the standing whole-file route:

* every non-prose agent-instruction surface (``.claude/settings.json``,
  ``.mcp.json``, hook scripts) — machine-consumed configuration where a number
  is not documentation;
* every other whole-file trust-root class (``ci_gate``, ``policy``,
  ``host_boundary``);
* added, deleted, or renamed files, and any line-count change.

A safe result downgrades *authoring* routing only.  The concrete diff still
carries its verify-mode trust-root findings into human PR review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from agents_shipgate.core.boundary_diff import DiffFile
from agents_shipgate.core.trust_roots import trust_root_class_for
from agents_shipgate.schemas.contract import build_contract_payload

# Agent-instruction surfaces that are prose documents rather than
# machine-consumed configuration. A version literal in a document is a factual
# claim about the CLI; in a settings file it is behaviour.
_PROSE_SUFFIXES = frozenset({".md", ".mdc", ".markdown"})

# One managed-field assignment and nothing else: an optional list marker, an
# optionally quoted field name, a ``:``/``=`` separator, an optionally quoted
# version value, and at most trailing sentence punctuation. The value group is
# located by span so it can be masked in place.
_ASSIGNMENT_RE = re.compile(
    r"""^\s*(?:[-*+]\s+)?["']?(?P<field>[A-Za-z_][A-Za-z0-9_]*)["']?\s*[:=]\s*
        ["']?(?P<value>v?\d+(?:\.\d+)*(?:[A-Za-z]+\d*)?)["']?
        \s*[.,;:)\]]?\s*$""",
    re.VERBOSE,
)
_VERSION_VALUE_RE = re.compile(r"\d+(?:\.\d+)*(?:[A-Za-z]+\d*)?")


@dataclass(frozen=True)
class InstructionSyncAssessment:
    """Semantic result for one proposed agent-instruction document change."""

    sync_safe: bool
    reason: str
    synchronized_literals: tuple[tuple[str, str], ...] = ()


def is_instruction_prose_document(path: str) -> bool:
    """Whether ``path`` is an agent-instruction trust root made of prose."""

    normalized = path.replace("\\", "/")
    if trust_root_class_for(normalized) != "agent_instructions":
        return False
    return PurePosixPath(normalized).suffix.casefold() in _PROSE_SUFFIXES


def assess_version_literal_sync(
    *,
    diff_file: DiffFile,
) -> InstructionSyncAssessment:
    """Recognize a managed-field version synchronization.

    Safety is structural: the masked old and new line sequences of every hunk
    must match position for position with context included, at least one
    managed-field value must actually move, and every moved value must be the
    published value of the exact field that names it.

    The assessment reads only the diff, so it never depends on workspace state
    and cannot be widened by a file that changes underneath it.
    """

    path = diff_file.path.replace("\\", "/")
    if not path:
        return _unsafe("change has no resolvable repository path")
    if not is_instruction_prose_document(path):
        return _unsafe(
            "managed-field version synchronization is recognized only for "
            "agent-instruction prose documents"
        )
    if diff_file.is_new or diff_file.is_deleted or diff_file.is_rename:
        return _unsafe("added, deleted, and renamed instruction files stay human-routed")
    if not diff_file.hunks:
        return _unsafe("change carries no hunks to evaluate")
    if len(diff_file.added_lines) != len(diff_file.removed_lines):
        return _unsafe("change adds or removes instruction lines")
    if not diff_file.added_lines:
        return _unsafe("change contains no edited instruction lines")
    # A ``+``/``-`` line outside every hunk is still collected at file level but
    # would never reach the positional comparison below, so it could carry an
    # arbitrary instruction past the exception. Require the hunks to account
    # for the whole change.
    hunk_added = sum(1 for hunk in diff_file.hunks for kind, _ in hunk.lines if kind == "+")
    hunk_removed = sum(1 for hunk in diff_file.hunks for kind, _ in hunk.lines if kind == "-")
    if hunk_added != len(diff_file.added_lines) or hunk_removed != len(
        diff_file.removed_lines
    ):
        return _unsafe("change carries edited lines outside its hunks")

    synchronized: list[tuple[str, str]] = []
    for hunk in diff_file.hunks:
        # Context is part of the comparison: a line that keeps its text but
        # changes position relative to its neighbours has changed which
        # instruction it qualifies.
        old_lines = [text for kind, text in hunk.lines if kind in {" ", "-"}]
        new_lines = [text for kind, text in hunk.lines if kind in {" ", "+"}]
        if len(old_lines) != len(new_lines):
            return _unsafe("a hunk adds or removes instruction lines")
        for old_line, new_line in zip(old_lines, new_lines, strict=True):
            reason = _assess_line_pair(old_line, new_line, synchronized)
            if reason is not None:
                return _unsafe(reason)

    if not synchronized:
        return _unsafe("change moves no managed-field version value")
    return InstructionSyncAssessment(
        sync_safe=True,
        reason=(
            "every line keeps its position and prose except at managed-field "
            "values this CLI publishes for those exact fields; the concrete "
            "diff still requires verification and reviewer sign-off"
        ),
        synchronized_literals=tuple(synchronized),
    )


def _assess_line_pair(
    old_line: str,
    new_line: str,
    synchronized: list[tuple[str, str]],
) -> str | None:
    """Return a refusal reason, or ``None`` when the pair only syncs versions."""

    if _has_disallowed_control(old_line) or _has_disallowed_control(new_line):
        return "an edited line carries a control character and cannot be compared"

    # Segments are compared as a tuple rather than joined around a placeholder:
    # a placeholder that can also occur in the text would let two different
    # lines mask to the same string. Field names live in the segments, so a
    # renamed field is a prose change.
    old_segments, old_values = _mask(old_line)
    new_segments, new_values = _mask(new_line)
    if old_segments != new_segments:
        return (
            "an edited line changes text outside a managed-field value; only a "
            "published value of a named contract field may move"
        )

    # Equal segment tuples imply equal value counts.
    published = _published_field_values()
    changed = [
        (old_field, old_value, new_value)
        for (old_field, old_value), (_new_field, new_value) in zip(
            old_values, new_values, strict=True
        )
        if old_value != new_value
    ]
    for field, old_value, new_value in changed:
        allowed = published.get(field.casefold())
        if allowed is None:
            return (
                f"{field!r} is not a contract field this CLI publishes, so its "
                "value cannot be synchronized automatically"
            )
        if new_value not in allowed:
            return (
                f"{new_value!r} is not the published value of {field!r}; a "
                "coding agent must not write one field's version into another"
            )
        synchronized.append((old_value, new_value))
    return None


def _has_disallowed_control(line: str) -> bool:
    """Whether a line carries a control character that prose should not hold."""

    return any(character < " " and character != "\t" for character in line)


def _code_spans(line: str) -> list[tuple[int, int]]:
    """Interior spans of backtick-delimited code runs, outermost first."""

    spans: list[tuple[int, int]] = []
    index = 0
    length = len(line)
    while index < length:
        if line[index] != "`":
            index += 1
            continue
        fence_end = index
        while fence_end < length and line[fence_end] == "`":
            fence_end += 1
        fence = line[index:fence_end]
        closing = line.find(fence, fence_end)
        if closing == -1:
            break
        spans.append((fence_end, closing))
        index = closing + len(fence)
    return spans


def _managed_field_values(line: str) -> list[tuple[int, int, str, str]]:
    """Locate ``(start, end, field, value)`` for each managed-field assignment.

    Only two containers are recognized, both of which fix the meaning of the
    number structurally: a code span holding nothing but the assignment, and a
    line that is nothing but the assignment. A version mentioned in ordinary
    prose is not a managed field and is left in the compared text.
    """

    found: dict[tuple[int, int], tuple[str, str]] = {}
    candidates = [(start, line[start:end]) for start, end in _code_spans(line)]
    candidates.append((0, line))
    for offset, text in candidates:
        match = _ASSIGNMENT_RE.match(text)
        if match is None:
            continue
        found[(offset + match.start("value"), offset + match.end("value"))] = (
            match.group("field"),
            match.group("value"),
        )
    return [(start, end, field, value) for (start, end), (field, value) in found.items()]


def _mask(line: str) -> tuple[tuple[str, ...], list[tuple[str, str]]]:
    """Split a line into literal segments and its managed-field values."""

    segments: list[str] = []
    values: list[tuple[str, str]] = []
    cursor = 0
    for start, end, field, value in sorted(_managed_field_values(line)):
        if start < cursor:
            # Overlapping recognitions cannot be masked unambiguously.
            return (line,), []
        segments.append(line[cursor:start])
        values.append((field, value))
        cursor = end
    segments.append(line[cursor:])
    return tuple(segments), values


def _published_field_values() -> dict[str, frozenset[str]]:
    """Published value spellings for each contract field, keyed by field name.

    Sourced from the contract payload rather than a hand-kept list so a
    contract or schema bump cannot leave this exception recognizing stale
    numbers — the authority and the published surface move together.
    """

    out: dict[str, frozenset[str]] = {}
    for name, value in build_contract_payload().model_dump().items():
        if not name.endswith("_version") or not isinstance(value, str):
            continue
        forms = _version_forms(value)
        if forms:
            out[name.casefold()] = frozenset(forms)
    return out


def _version_forms(value: str) -> set[str]:
    """Bare and ``v``-prefixed spellings of one published version value."""

    out: set[str] = set()
    # "shipgate.agent_handoff/v6" documents as "v6"; "0.34" documents as
    # "0.34" and, in schema filenames, as "v0.34".
    for candidate in {value, value.rsplit("/", 1)[-1]}:
        text = candidate.strip()
        if text[:1] in {"v", "V"} and text[1:2].isdigit():
            text = text[1:]
        if text and _VERSION_VALUE_RE.fullmatch(text):
            out.add(text)
            out.add(f"v{text}")
    return out


def _unsafe(reason: str) -> InstructionSyncAssessment:
    return InstructionSyncAssessment(sync_safe=False, reason=reason)


__all__ = [
    "InstructionSyncAssessment",
    "assess_version_literal_sync",
    "is_instruction_prose_document",
]
