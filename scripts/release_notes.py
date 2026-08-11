#!/usr/bin/env python3
"""Extract the ``CHANGELOG.md`` section a release tag names.

Standard library only, on purpose — see ``scripts/_release_support``. This runs
in the staging job, which holds `contents: write`, and in the verification job
before any toolchain is installed.

The release used to publish ``--notes "Agents Shipgate v0.16.0"`` while
``CHANGELOG.md`` held the real entry, so the one artifact users actually read on
the release page said nothing. The notes are extracted rather than
hand-transcribed at tag time so the published body is the reviewed text, byte
for byte, from the commit verification approved.

Absence is a failure, not an empty body. A release whose changelog section was
never written is a release nobody described, and finding that out after an
immutable PyPI upload leaves nothing to fix it with — so verification requires
the section before a tag can publish, which means a rehearsal catches it while
the tag does not yet exist.

Section matching is deliberately literal. ``v0.16.0`` matches a ``##`` heading
whose first token is ``0.16.0``, ``v0.16.0``, or ``[0.16.0]``, with anything
after it (conventionally a date) ignored. ``## Unreleased`` therefore never
matches a tag: promoting that heading to the released version is part of
cutting a release, and this is what enforces it.

Run from the repo root:

    python scripts/release_notes.py --tag v0.16.0 --output release-notes.md
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

if __package__:
    from scripts._release_support import ReleaseError
else:  # ``python scripts/release_notes.py``
    from _release_support import ReleaseError

# GitHub rejects a release body longer than this with a 422 — after the tag
# exists, which is the expensive moment to discover it. The measured 0.16.0
# development section is already ~75,000 characters, so this bound is a live
# constraint on this project's changelog rather than a theoretical one.
MAX_BODY_CHARACTERS = 125_000

# ATX heading, exactly level two: `### Something` is content inside a section.
_SECTION_HEADING = re.compile(r"^##(?!#)[ \t]+(.+?)[ \t]*$")
# Fenced code block, per CommonMark: up to three leading spaces, then three or
# more backticks or tildes.
_FENCE = re.compile(r"^ {0,3}(?P<marker>`{3,}|~{3,})(?P<info>.*)$")
_LINE_BREAK = re.compile(r"\r\n|\r|\n")


def _split_lines(text: str) -> list[str]:
    """Split on real line terminators only.

    ``str.splitlines`` also breaks on form feed and the Unicode line separators,
    which would silently rewrite a changelog that contains one inside a code
    block. Faithful reproduction is the whole point of extracting rather than
    retyping the notes.
    """

    return _LINE_BREAK.split(text)


def _section_headings(lines: list[str]) -> list[tuple[int, str]]:
    """Every level-two heading outside a fenced code block, with its line index."""

    headings: list[tuple[int, str]] = []
    fence: str | None = None
    for index, line in enumerate(lines):
        fenced = _FENCE.match(line)
        if fenced:
            marker = fenced.group("marker")
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence) and not fenced.group("info"):
                fence = None
            continue
        if fence is not None:
            continue
        heading = _SECTION_HEADING.match(line)
        if heading:
            headings.append((index, heading.group(1)))
    if fence is not None:
        raise ReleaseError(
            "Changelog has an unterminated code fence; section boundaries cannot be "
            "determined and the extracted notes would be wrong."
        )
    return headings


def heading_version(label: str) -> str:
    """The version a ``##`` heading declares, or ``""`` for headings like ``Unreleased``.

    Accepts ``0.16.0``, ``v0.16.0`` and the Keep a Changelog ``[0.16.0]`` form;
    everything after the first token (conventionally ``- 2026-08-11``) is
    ignored.
    """

    tokens = label.split()
    if not tokens:
        return ""
    token = tokens[0].strip("[]")
    if token[:1] in {"v", "V"}:
        token = token[1:]
    # A version starts with a digit. `Unreleased`, `Unreleased - next` and
    # `Notes` all fall out here rather than matching some tag by accident.
    return token if token[:1].isdigit() else ""


def version_from_tag(tag: str) -> str:
    version = tag[1:] if tag[:1] in {"v", "V"} else tag
    if not version:
        raise ReleaseError(f"Release tag {tag!r} carries no version.")
    return version


def extract_release_notes(*, changelog_path: Path, tag: str) -> str:
    """Return the changelog body for ``tag``, exactly as written."""

    if not changelog_path.is_file():
        raise ReleaseError(f"Changelog not found: {changelog_path}")
    try:
        text = changelog_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ReleaseError(f"Unable to read {changelog_path}: {exc}") from exc

    version = version_from_tag(tag)
    lines = _split_lines(text)
    headings = _section_headings(lines)
    matches = [
        (position, index)
        for position, (index, label) in enumerate(headings)
        if heading_version(label) == version
    ]

    if not matches:
        known = [label for _, label in headings if heading_version(label)]
        raise ReleaseError(
            f"{changelog_path} has no section for {tag}. Promote the section that "
            f"describes this release to a '## {version} - <date>' heading before tagging. "
            f"Sections present: {', '.join(known[:5]) if known else 'none'}."
        )
    if len(matches) > 1:
        at = ", ".join(str(headings[position][0] + 1) for position, _ in matches)
        raise ReleaseError(
            f"{changelog_path} has {len(matches)} sections for {tag} (lines {at}); "
            "which one describes the release is ambiguous."
        )

    position, start = matches[0]
    end = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
    body = lines[start + 1 : end]
    while body and not body[0].strip():
        body.pop(0)
    while body and not body[-1].strip():
        body.pop()
    if not body:
        raise ReleaseError(
            f"{changelog_path} section for {tag} is empty; a release with no described "
            "changes must not be published."
        )

    notes = "\n".join(body) + "\n"
    if len(notes) > MAX_BODY_CHARACTERS:
        raise ReleaseError(
            f"Release notes for {tag} are {len(notes)} characters; GitHub rejects a body "
            f"over {MAX_BODY_CHARACTERS}. Split the section or move detail into the docs."
        )
    return notes


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract the CHANGELOG section for a release tag.")
    parser.add_argument("--changelog", type=Path, default=Path("CHANGELOG.md"))
    parser.add_argument("--tag", required=True, help="release tag, e.g. v0.16.0")
    parser.add_argument(
        "--output",
        type=Path,
        help="write the notes here; omit to validate the section without writing it",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        notes = extract_release_notes(changelog_path=args.changelog, tag=args.tag)
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(notes, encoding="utf-8")
    except (ReleaseError, OSError, ValueError) as exc:
        sys.stderr.write(f"Release notes error: {exc}\n")
        return 1
    destination = f" -> {args.output}" if args.output is not None else ""
    sys.stdout.write(
        f"OK: {args.changelog} describes {args.tag} in {notes.count(chr(10))} lines "
        f"({len(notes)} characters){destination}.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
