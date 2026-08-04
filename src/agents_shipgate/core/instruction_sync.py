"""Fail-closed recognition of prose-preserving version-literal synchronization.

This module does not approve an agent-instruction change.  It recognizes one
narrow shape that a static evaluator *can* prove is not an instruction
weakening: a diff over an agent-instruction **prose document** in which the
complete old and new line sequences are identical once version literals are
masked, and every moved literal is a non-decreasing published version on a line
that states no version condition.

Why that shape is safe to author without stopping the turn
----------------------------------------------------------
``SHIP-VERIFY-AGENT-INSTRUCTIONS-WEAKENED`` routes agent-instruction edits to a
human because Shipgate is static and cannot prove from text that an instruction
was not softened.  That reasoning is sound for a general edit.  It is *not*
needed for a diff whose prose is provably preserved: when the masked old and
new sequences match **position for position, context lines included**, no
instruction can have been removed, added, reordered, moved relative to its
neighbours, or reworded.

Preserving position is load-bearing, not incidental.  Comparing removed lines
against added lines while ignoring context accepts a *move*: relocating
"Contract vN applies to the next rule" from above an instruction to below it
rebinds "the next rule" to a different instruction without changing a single
character of prose.

Four separate guards keep a moved literal from carrying meaning with it:

* the literal must be a version this CLI publishes (:func:`build_contract_payload`,
  the exact values ``agents-shipgate contract --json` emits), so a fabricated
  number is refused and the value is the one the installed CLI implements;
* it must not *decrease*, because a published value is not automatically a safe
  replacement for another published value — lowering "contract version >= 19"
  to ">= 14" moves a real threshold using two legitimate numbers;
* a line stating a version condition (``>=``, "at least", "or later", …) is
  refused outright, since its literal is a threshold rather than a fact; and
* a bare integer counts as a version only on a line naming ``version`` /
  ``contract`` / ``schema`` at an identifier boundary, so "2 approvals for
  conversion" neither matches on the substring ``version`` nor becomes
  rewritable.

Deliberately out of scope, all of which keep the standing whole-file route:

* every non-prose agent-instruction surface (``.claude/settings.json``,
  ``.mcp.json``, hook scripts) — those are machine-consumed configuration where
  a number is not documentation;
* every other whole-file trust-root class (``ci_gate``, ``policy``,
  ``host_boundary``) — a version literal there changes machine behaviour;
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

# A bare integer is a version literal only when a version *field* introduces it
# directly ("...contract_version: 14", "contract 14"), not merely because the
# line mentions a version somewhere. Line-level context would make every number
# on the line rewritable: "Use subversion 2 for schema" qualifies on "schema"
# while its 2 is a count.
#
# Matching respects identifier boundaries in both directions. Plain word
# boundaries reject ``minimum_control_contract_version`` (``_`` is a word
# character), while substring matching accepts ``conversion`` and
# ``subversion``. Underscores and other non-alphanumerics separate; letters and
# digits do not.
_VERSION_FIELD_SUFFIX_RE = re.compile(
    r"(?<![a-z0-9])(?:version|contract|schema)s?$"
)
# Separators that may sit between a version field and its value.
_FIELD_VALUE_SEPARATORS = " \t:=`\"'([,"

# A line stating a version *condition* carries a threshold, not a fact, so a
# changed literal moves the condition even when both values are published.
_VERSION_CONDITION_RE = re.compile(
    r"(?:>=|<=|=>|=<|>|<|≥|≤)"
    r"|(?<![a-z0-9])(?:"
    r"at\s+least|at\s+most|no\s+(?:lower|higher|older|newer|earlier|later)\s+than"
    r"|or\s+(?:later|newer|higher|above|greater|earlier|older|lower|below)"
    r"|(?:newer|older|greater|later|earlier|lower|higher)\s+than"
    r")(?![a-z0-9])"
)

# Dotted/suffixed releases ("0.34", "0.16.0b7", "v0.34") and plain "vN" tags.
# Ordered so the dotted form wins before the short ``v\d+`` alternative.
_DOTTED_OR_TAGGED = r"v?\d+(?:\.\d+)+(?:[A-Za-z]+\d*)?|v\d+"
_BARE_INTEGER = r"\d+"
# ``.`` and ``/`` are intentionally allowed on the left so a literal embedded in
# a filename or namespace ("report-schema.v0.34.json",
# "shipgate.agent_handoff/v6") is recognized as one token.
_LEFT_BOUNDARY = r"(?<![0-9A-Za-z_-])"
_RIGHT_BOUNDARY = r"(?![0-9A-Za-z])"
_TAGGED_TOKEN_RE = re.compile(
    rf"{_LEFT_BOUNDARY}(?:{_DOTTED_OR_TAGGED}){_RIGHT_BOUNDARY}"
)
_ANY_TOKEN_RE = re.compile(
    rf"{_LEFT_BOUNDARY}(?:{_DOTTED_OR_TAGGED}|{_BARE_INTEGER}){_RIGHT_BOUNDARY}"
)
_VERSION_VALUE_RE = re.compile(r"\d+(?:\.\d+)*(?:[A-Za-z]+\d*)?")
_NUMERIC_COMPONENT_RE = re.compile(r"^(\d+)([A-Za-z]+\d*)?$")


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
    """Recognize a prose-preserving version-literal synchronization.

    Safety is structural: the masked old and new line sequences of every hunk
    must match position for position with context included, at least one
    literal must actually move, and every moved literal must be a
    non-decreasing published version on a line that states no version
    condition.

    The assessment reads only the diff, so it never depends on workspace state
    and cannot be widened by a file that changes underneath it.
    """

    path = diff_file.path.replace("\\", "/")
    if not path:
        return _unsafe("change has no resolvable repository path")
    if not is_instruction_prose_document(path):
        return _unsafe(
            "version-literal synchronization is recognized only for "
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
        return _unsafe("change moves no version literal")
    return InstructionSyncAssessment(
        sync_safe=True,
        reason=(
            "every line keeps its position and prose except at version literals "
            "this CLI publishes; the concrete diff still requires verification "
            "and reviewer sign-off"
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
    # lines mask to the same string. Tokenization is a pure function of each
    # line, so a number the two sides classify differently shows up as a
    # segment mismatch and is refused.
    old_segments, old_tokens = _mask(old_line)
    new_segments, new_tokens = _mask(new_line)
    if old_segments != new_segments:
        return "an edited line changes prose outside its version literals"

    # Equal segment tuples imply equal token counts.
    changed = [
        (old_token, new_token)
        for old_token, new_token in zip(old_tokens, new_tokens, strict=True)
        if old_token != new_token
    ]
    if not changed:
        return None
    if _states_version_condition(old_line) or _states_version_condition(new_line):
        return (
            "an edited line states a version condition, so a changed literal "
            "would move a threshold rather than restate a fact"
        )

    authoritative = _authoritative_version_literals()
    for old_token, new_token in changed:
        if new_token not in authoritative:
            return (
                f"{new_token!r} is not a version this CLI publishes; a coding "
                "agent must not invent a contract, schema, or release number"
            )
        if not _is_non_decreasing(old_token, new_token):
            return (
                f"{old_token!r} to {new_token!r} moves a documented version "
                "backwards; one published value is not a safe replacement for "
                "another"
            )
        synchronized.append((old_token, new_token))
    return None


def _states_version_condition(line: str) -> bool:
    return _VERSION_CONDITION_RE.search(line.casefold()) is not None


def _version_field_introduces(line: str, index: int) -> bool:
    """Whether a version field name sits immediately before ``index``."""

    prefix = line[:index].casefold().rstrip(_FIELD_VALUE_SEPARATORS)
    return _VERSION_FIELD_SUFFIX_RE.search(prefix) is not None


def _has_disallowed_control(line: str) -> bool:
    """Whether a line carries a control character that prose should not hold."""

    return any(character < " " and character != "\t" for character in line)


def _mask(line: str) -> tuple[tuple[str, ...], list[str]]:
    """Split a line into literal segments and the version tokens between them.

    A ``vN``/dotted token is self-identifying. A bare integer is only a version
    when a version field introduces it; otherwise it stays inside a segment and
    therefore has to match the other side exactly.
    """

    segments: list[str] = []
    tokens: list[str] = []
    cursor = 0
    for match in _ANY_TOKEN_RE.finditer(line):
        token = match.group(0)
        if _TAGGED_TOKEN_RE.fullmatch(token) is None and not _version_field_introduces(
            line, match.start()
        ):
            continue
        segments.append(line[cursor : match.start()])
        tokens.append(token)
        cursor = match.end()
    segments.append(line[cursor:])
    return tuple(segments), tokens


def _is_non_decreasing(old_token: str, new_token: str) -> bool:
    """Whether ``new_token`` orders at or above ``old_token``.

    A published value is not automatically a safe replacement for another
    published value: "contract version 19" and "14" are both real, and swapping
    them lowers whatever the sentence asserts. Only a forward move can be a
    synchronization. Anything this cannot order — a pre-release suffix, an
    unparsable shape — fails closed.
    """

    old_key = _numeric_key(old_token)
    new_key = _numeric_key(new_token)
    if old_key is None or new_key is None:
        return False
    old_numbers, old_suffix = old_key
    new_numbers, new_suffix = new_key
    width = max(len(old_numbers), len(new_numbers))
    old_padded = old_numbers + (0,) * (width - len(old_numbers))
    new_padded = new_numbers + (0,) * (width - len(new_numbers))
    if new_padded != old_padded:
        return new_padded > old_padded
    # Identical numbers: only a pure spelling change ("14" to "v14") remains,
    # and pre-release suffixes have no reliable order.
    return old_suffix == new_suffix


def _numeric_key(token: str) -> tuple[tuple[int, ...], str] | None:
    text = token[1:] if token[:1] in {"v", "V"} else token
    if not text:
        return None
    numbers: list[int] = []
    suffix = ""
    parts = text.split(".")
    for index, part in enumerate(parts):
        match = _NUMERIC_COMPONENT_RE.match(part)
        if match is None:
            return None
        trailing = match.group(2)
        if trailing and index != len(parts) - 1:
            return None
        numbers.append(int(match.group(1)))
        suffix = trailing or ""
    return tuple(numbers), suffix


def _authoritative_version_literals() -> frozenset[str]:
    """Every version string this CLI publishes, in the spellings docs use.

    Sourced from the contract payload rather than a hand-kept list so a
    contract or schema bump cannot leave this exception recognizing stale
    numbers — the authority and the published surface move together.
    """

    values: set[str] = set()
    for name, value in build_contract_payload().model_dump().items():
        if not name.endswith("_version") or not isinstance(value, str):
            continue
        values |= _version_forms(value)
    return frozenset(values)


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
