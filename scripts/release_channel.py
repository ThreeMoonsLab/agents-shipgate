#!/usr/bin/env python3
"""Which release line a version belongs to, declared in reviewed code (#648).

A ``v*`` tag used to mean one thing: a qualified release, gated on signed
qualification evidence. The advisory-1.0 decision gives the advisory line a
``v*`` release too, so the tag alone no longer says which line it is on.

The answer must not come from anything a release run can discover or lose.
Choosing the qualified path "by the presence of the qualification artifact"
fails open on intent: a qualified release whose qualification download is
missing would silently publish as advisory, and PyPI never gives the version
back. So each version's channel is declared in a committed, reviewed file,
for the same reason the qualification trust roots are
(``.github/release-trust-roots.json``): whoever can push a tag cannot choose
its channel without a reviewed diff.

Everything here fails closed. A missing or malformed declaration, an unknown
channel, or a tag that is not ``v<version>`` stops the release before anything
is built, and ``select`` refuses any verification outcome other than exactly
the declared channel's.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

if __package__:
    from scripts._release_support import is_release_version
else:  # ``python scripts/release_channel.py``
    from _release_support import is_release_version

DECLARATION_PATH = Path(".github/release-channels.json")
SCHEMA = "shipgate.release_channels/v1"
ADVISORY = "advisory"
QUALIFIED = "qualified"
CHANNELS = frozenset({ADVISORY, QUALIFIED})
VERIFY_RESULTS = frozenset({"success", "failure", "cancelled", "skipped"})


class ChannelError(ValueError):
    """The release channel cannot be established; the release must stop."""


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise ChannelError(f"duplicate key {key!r} in the release channel declaration")
        document[key] = value
    return document


def load_declaration(path: Path = DECLARATION_PATH) -> dict[str, str]:
    """The reviewed version-to-channel map, validated in full before use."""

    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ChannelError(f"release channel declaration not found: {path}") from exc
    return parse_declaration(raw)


def parse_declaration(raw: str) -> dict[str, str]:
    """Validate declaration text. Shared by the release run and the cadence
    reader, which reads the declaration from each tag's own committed tree."""

    try:
        document = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise ChannelError(f"release channel declaration is not valid JSON: {exc}") from exc
    if not isinstance(document, dict) or set(document) != {"schema", "channels"}:
        raise ChannelError(
            "release channel declaration must contain exactly 'schema' and 'channels'"
        )
    if document["schema"] != SCHEMA:
        raise ChannelError(
            f"unsupported release channel schema {document['schema']!r}; expected {SCHEMA!r}"
        )
    channels = document["channels"]
    if not isinstance(channels, dict):
        raise ChannelError("'channels' must map release versions to a channel")
    for version, channel in channels.items():
        # A local segment is refused by every public index, so no v* release
        # can carry one; that is the preview channel's property, not this one.
        if not is_release_version(version) or "+" in version:
            raise ChannelError(f"{version!r} is not a complete public release version")
        if channel not in CHANNELS:
            raise ChannelError(
                f"version {version} declares unknown channel {channel!r}; "
                f"expected one of {sorted(CHANNELS)}"
            )
    return dict(channels)


def resolve(version: str, *, path: Path = DECLARATION_PATH) -> str:
    """The declared channel for ``version``; no declaration is no release."""

    channels = load_declaration(path)
    if version not in channels:
        raise ChannelError(
            f"no reviewed release channel is declared for {version}; add it to "
            f"{DECLARATION_PATH} in a reviewed change before tagging"
        )
    return channels[version]


def resolve_tag(tag: str, *, path: Path = DECLARATION_PATH) -> str:
    """The declared channel for a ``v<version>`` release tag."""

    # ``is_release_version`` accepts a local segment, and no public index
    # accepts one, so a ``v*`` release can never carry it.
    if not tag.startswith("v") or not is_release_version(tag[1:]) or "+" in tag:
        raise ChannelError(f"release tag {tag!r} is not v<complete release version>")
    return resolve(tag[1:], path=path)


def select(channel: str, *, qualified_result: str, advisory_result: str) -> str:
    """Confirm exactly the declared channel's verification ran and succeeded.

    ``release.yml`` gates two verification jobs on the declared channel, and
    publication depends on this check rather than on either job directly. The
    declared job must have succeeded and the other must have been skipped.
    Anything else is refused — both ran, neither ran, the undeclared one ran,
    or a cancellation — so publication can never proceed on the wrong line's
    evidence.
    """

    if channel not in CHANNELS:
        raise ChannelError(f"unknown channel {channel!r}")
    for name, result in (("qualified", qualified_result), ("advisory", advisory_result)):
        if result not in VERIFY_RESULTS:
            raise ChannelError(f"unexpected {name} verification result {result!r}")
    declared, other = (
        (qualified_result, advisory_result)
        if channel == QUALIFIED
        else (advisory_result, qualified_result)
    )
    if declared != "success":
        raise ChannelError(
            f"the declared {channel} verification did not succeed (result: {declared})"
        )
    if other != "skipped":
        raise ChannelError(
            f"the undeclared channel's verification did not stay skipped (result: {other})"
        )
    return channel


def _write_github_output(values: dict[str, str]) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    if not target:
        return
    with open(target, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve and confirm a release's channel.")
    commands = parser.add_subparsers(dest="command", required=True)
    resolver = commands.add_parser("resolve", help="declared channel for a v<version> tag")
    resolver.add_argument("--tag", required=True)
    resolver.add_argument("--declaration", type=Path, default=DECLARATION_PATH)
    selector = commands.add_parser("select", help="confirm the declared verification ran alone")
    selector.add_argument("--channel", required=True)
    selector.add_argument("--qualified-result", required=True)
    selector.add_argument("--advisory-result", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "resolve":
            channel = resolve_tag(args.tag, path=args.declaration)
        else:
            channel = select(
                args.channel,
                qualified_result=args.qualified_result,
                advisory_result=args.advisory_result,
            )
    except ChannelError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    _write_github_output({"channel": channel})
    print(channel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
