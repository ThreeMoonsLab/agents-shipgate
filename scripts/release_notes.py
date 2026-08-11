#!/usr/bin/env python3
"""Extract the ``CHANGELOG.md`` section a release tag names.

Standard library only, on purpose — see ``scripts/_release_support``. It runs in
the verification job before any toolchain is installed, in the staging job, and
in finalisation, which fetches it by immutable SHA rather than checking out.

Those three jobs extract independently, so verification publishes the digest of
what it approved and the other two pass it back with ``--expected-sha256``. The
body is then reapplied in the same API call that undrafts the release: the
window between staging and finalisation includes the environment approval, and
a release-write actor editing the draft's text in between would otherwise
publish notes nobody reviewed.

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
import hashlib
import re
import sys
from pathlib import Path

if __package__:
    from scripts._release_support import SHA256_PATTERN, ReleaseError
else:  # ``python scripts/release_notes.py``
    from _release_support import SHA256_PATTERN, ReleaseError

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
            elif (
                marker[0] == fence[0]
                and len(marker) >= len(fence)
                # CommonMark allows spaces and tabs — and only those — after a
                # closing fence. Requiring an exactly empty suffix failed the
                # release with "unterminated code fence" on a valid changelog;
                # bare `str.strip()` overshoots the other way and accepts
                # Unicode whitespace such as NBSP, so a line that does *not*
                # close the block would end it and the next heading would be
                # read as content.
                and not fenced.group("info").strip(" \t")
            ):
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


def notes_digest(notes: str) -> str:
    return hashlib.sha256(notes.encode("utf-8")).hexdigest()


def normalize_body(text: str) -> str:
    """A release body as GitHub stores it, comparable to extracted notes.

    GitHub returns bodies with CRLF line endings and no guaranteed trailing
    newline, so a raw comparison reports a difference on every release that has
    none. Only those two representational details are normalised; a single
    changed character still differs.
    """

    return "\n".join(line.rstrip("\r") for line in text.split("\n")).strip("\n")


def assert_body_matches(notes: str, body: str) -> None:
    if normalize_body(body) != normalize_body(notes):
        raise ReleaseError(
            "The published release body is not the changelog section this release "
            "verified. Release notes stay editable after publication, so a re-run must "
            "not certify text nobody reviewed; see docs/release-runbook.md."
        )


def assert_expected_digest(notes: str, expected: str) -> str:
    """Bind extracted notes to the digest verification published.

    Compared whenever it is supplied, including when it is empty or malformed.
    A truthiness test would fail open here in exactly the way
    ``verify-manifest`` documents: an absent or redacted job output arrives as
    ``""`` and the workflow still passes the flag.
    """

    if not SHA256_PATTERN.fullmatch(expected):
        raise ReleaseError(
            "Expected release-notes digest is not a 64-character lowercase SHA-256 "
            f"({expected!r}); the verification job output was missing or redacted."
        )
    actual = notes_digest(notes)
    if actual != expected:
        raise ReleaseError(
            f"Release notes digest {actual} does not match the verified {expected}; the "
            "changelog this job read is not the one verification approved."
        )
    return actual


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract the CHANGELOG section for a release tag.")
    parser.add_argument("--changelog", type=Path, default=Path("CHANGELOG.md"))
    parser.add_argument("--tag", required=True, help="release tag, e.g. v0.16.0")
    parser.add_argument(
        "--output",
        type=Path,
        help="write the notes here; omit to validate the section without writing it",
    )
    parser.add_argument(
        "--expected-sha256",
        help=(
            "digest of the notes verification approved; every job downstream of "
            "verification passes it, so a changelog read from a different tree fails"
        ),
    )
    parser.add_argument(
        "--github-output",
        help="append release_notes_sha256 here",
    )
    parser.add_argument(
        "--published-body",
        type=Path,
        help=(
            "file holding a published release's body; fails unless it is this "
            "section, so a re-run cannot certify text that was edited afterwards"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        notes = extract_release_notes(changelog_path=args.changelog, tag=args.tag)
        digest = (
            assert_expected_digest(notes, args.expected_sha256)
            if args.expected_sha256 is not None
            else notes_digest(notes)
        )
        if args.published_body is not None:
            assert_body_matches(notes, args.published_body.read_text(encoding="utf-8"))
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(notes, encoding="utf-8")
        if args.github_output:
            with Path(args.github_output).open("a", encoding="utf-8") as handle:
                handle.write(f"release_notes_sha256={digest}\n")
    except (ReleaseError, OSError, ValueError) as exc:
        sys.stderr.write(f"Release notes error: {exc}\n")
        return 1
    destination = f" -> {args.output}" if args.output is not None else ""
    sys.stdout.write(
        f"OK: {args.changelog} describes {args.tag} in {notes.count(chr(10))} lines "
        f"({len(notes)} characters, sha256 {digest}){destination}.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
