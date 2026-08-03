"""Fail-closed recognition of prose-preserving version-literal synchronization.

This module does not approve an agent-instruction change.  It recognizes one
narrow shape that a static evaluator *can* prove is not an instruction
weakening: a diff over an agent-instruction **prose document** in which every
changed line is byte-identical to its counterpart except at version-literal
tokens, and every new literal is a version this CLI itself publishes.

Why that shape is safe to author without stopping the turn
----------------------------------------------------------
``SHIP-VERIFY-AGENT-INSTRUCTIONS-WEAKENED`` routes agent-instruction edits to a
human because Shipgate is static and cannot prove from text that an instruction
was not softened.  That reasoning is sound for a general edit.  It is *not*
needed for a diff whose prose is provably preserved: when the removed and added
lines pair 1:1 and are equal after masking version literals, no instruction can
have been removed, added, reordered, or reworded.  The only thing that moved is
a number that this CLI is itself the source of truth for.

The residual risk is a version literal that is load-bearing inside an
instruction (for example "read this artifact only when the contract is >= N").
That is why a new literal must match :func:`build_contract_payload` — the exact
values ``agents-shipgate contract --json`` publishes — rather than any number
the diff likes.  A fabricated threshold is therefore rejected, and the
synchronized value is by construction the one the installed CLI actually
implements.

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

# A bare integer is only treated as a version literal on a line that says so.
# Without this, "require at least 2 approvals" would be maskable and a
# threshold could be rewritten under the exception.
_VERSION_CONTEXT_TERMS = ("version", "contract", "schema")

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
# Placeholder cannot occur in a diff line: control characters are rejected as
# repository content upstream and would not survive a Markdown document.
_MASK = "\x00v\x00"
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
    """Recognize a prose-preserving version-literal synchronization.

    Safety is structural: the diff must change no line count, every changed
    line must be identical to its counterpart once version literals are masked,
    at least one literal must actually move, and every new literal must be a
    version this CLI publishes.

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

    synchronized: list[tuple[str, str]] = []
    for hunk in diff_file.hunks:
        removed = [text for kind, text in hunk.lines if kind == "-"]
        added = [text for kind, text in hunk.lines if kind == "+"]
        if len(removed) != len(added):
            return _unsafe("a hunk adds or removes instruction lines")
        for old_line, new_line in zip(removed, added, strict=True):
            reason = _assess_line_pair(old_line, new_line, synchronized)
            if reason is not None:
                return _unsafe(reason)

    if not synchronized:
        return _unsafe("change moves no version literal")
    return InstructionSyncAssessment(
        sync_safe=True,
        reason=(
            "every changed line is identical except at version literals this "
            "CLI publishes; the concrete diff still requires verification and "
            "reviewer sign-off"
        ),
        synchronized_literals=tuple(synchronized),
    )


def _assess_line_pair(
    old_line: str,
    new_line: str,
    synchronized: list[tuple[str, str]],
) -> str | None:
    """Return a refusal reason, or ``None`` when the pair only syncs versions."""

    # Context terms are read from the pair itself, and the masked texts must
    # match, so both sides agree on which pattern applied.
    pattern = (
        _ANY_TOKEN_RE
        if _has_version_context(old_line) and _has_version_context(new_line)
        else _TAGGED_TOKEN_RE
    )
    old_masked, old_tokens = _mask(old_line, pattern)
    new_masked, new_tokens = _mask(new_line, pattern)
    if old_masked != new_masked:
        return "an edited line changes prose outside its version literals"
    if len(old_tokens) != len(new_tokens):
        return "an edited line changes how many version literals it carries"

    authoritative = _authoritative_version_literals()
    for old_token, new_token in zip(old_tokens, new_tokens, strict=True):
        if old_token == new_token:
            continue
        if new_token not in authoritative:
            return (
                f"{new_token!r} is not a version this CLI publishes; a coding "
                "agent must not invent a contract, schema, or release number"
            )
        synchronized.append((old_token, new_token))
    return None


def _has_version_context(line: str) -> bool:
    folded = line.casefold()
    return any(term in folded for term in _VERSION_CONTEXT_TERMS)


def _mask(line: str, pattern: re.Pattern[str]) -> tuple[str, list[str]]:
    """Replace version literals with a fixed placeholder, keeping their order."""

    tokens: list[str] = []

    def capture(match: re.Match[str]) -> str:
        tokens.append(match.group(0))
        return _MASK

    # A pre-existing placeholder would let two different lines mask to the same
    # text. Control characters are not legal repository content here, so their
    # presence is refused rather than normalized.
    if _MASK in line:
        return "\x00unmaskable\x00", []
    return pattern.sub(capture, line), tokens


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
