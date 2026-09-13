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
is built. ``candidate`` refuses any verification outcome other than exactly the
declared channel's, and forwards only that channel's values to publication,
together with the channel-keyed names publication needs — which rehearsal to
require, which assets to upload, which to sign — so the release workflow itself
spells no channel's asset set.

Two further subcommands serve the advisory verification. ``exercised`` confirms
that a Release Engine Smoke run installed and ran the exact candidate through
both the CLI and the Action (#570). ``statement`` writes the signed
``advisory-statement.json`` from fields fixed in this file, never from build
output, so what the release claims about itself is reviewed text.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

if __package__:
    from scripts._release_support import ReleaseError, inspect_wheel, is_release_version
    from scripts.release_publication import CHANNEL_ASSETS, CHANNEL_SIGNED_ASSETS
else:  # ``python scripts/release_channel.py``
    from _release_support import ReleaseError, inspect_wheel, is_release_version
    from release_publication import CHANNEL_ASSETS, CHANNEL_SIGNED_ASSETS

DECLARATION_PATH = Path(".github/release-channels.json")
SCHEMA = "shipgate.release_channels/v1"
ADVISORY = "advisory"
QUALIFIED = "qualified"
CHANNELS = frozenset({ADVISORY, QUALIFIED})
VERIFY_RESULTS = frozenset({"success", "failure", "cancelled", "skipped"})

#: The values publication reads from verification, by name. Both reusable
#: verification workflows export at least these; ``candidate`` forwards exactly
#: these, from the declared channel only.
CANDIDATE_OUTPUTS = (
    "version",
    "release_tag",
    "source_sha",
    "wheel_filename",
    "wheel_sha256",
    "manifest_sha256",
    "release_notes_sha256",
    "artifact_name",
)

#: The rehearsal ``stage`` requires for the exact candidate, by workflow file.
#: Separate files, so neither line's rehearsal can satisfy the other's.
REHEARSAL_WORKFLOWS = {
    QUALIFIED: "release-rehearsal.yml",
    ADVISORY: "release-advisory-rehearsal.yml",
}

STATEMENT_FILENAME = "advisory-statement.json"
STATEMENT_SCHEMA = "shipgate.advisory_release_statement/v1"
EXERCISE_WORKFLOW = ".github/workflows/release-engine-smoke.yml"
READINESS_RECORD = "docs/engineering/v1-release-readiness.md"
GOVERNING_POLICY = "docs/release-evidence-policy-decision.md, Amendment 5"

_FULL_SHA = re.compile(r"[0-9a-f]{40}")
_RUN_ID = re.compile(r"[1-9][0-9]{0,19}")
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


class ChannelError(ValueError):
    """The release channel cannot be established; the release must stop."""


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise ChannelError(f"duplicate key {key!r}")
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


def assert_history_preserved(declared: dict[str, str], tagged: dict[str, str]) -> None:
    """A version's channel is fixed once a tag for it exists.

    ``tagged`` maps each tagged version to the channel its *own* tree declared.
    Changing or removing that entry later would let the declaration say one
    thing about a release whose tag, and whose published bytes, said another —
    and would let a version that shipped advisory be re-declared qualified, so
    that one version carried both claims.
    """

    changed = sorted(
        f"{version} (tagged {channel}, now {declared.get(version, 'undeclared')})"
        for version, channel in tagged.items()
        if declared.get(version) != channel
    )
    if changed:
        raise ChannelError(
            "the channel of an already-tagged version cannot change: " + ", ".join(changed)
        )


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


def _parse_outputs(name: str, raw: str) -> dict[str, str]:
    try:
        outputs = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise ChannelError(f"the {name} verification outputs are not valid JSON: {exc}") from exc
    if not isinstance(outputs, dict) or not all(
        isinstance(value, str) for value in outputs.values()
    ):
        raise ChannelError(f"the {name} verification outputs must map names to strings")
    return outputs


def candidate(
    channel: str,
    *,
    tag: str,
    qualified_result: str,
    advisory_result: str,
    qualified_outputs: str,
    advisory_outputs: str,
) -> dict[str, str]:
    """Everything publication reads, from the declared channel's verification only.

    The undeclared job must have exported nothing, the declared one every value
    publication consumes, and it must have verified the tag that was pushed.
    Values reach ``GITHUB_OUTPUT`` one per line, so a value spanning lines is
    refused rather than allowed to write a second key.
    """

    select(channel, qualified_result=qualified_result, advisory_result=advisory_result)
    other_channel = ADVISORY if channel == QUALIFIED else QUALIFIED
    raw = {QUALIFIED: qualified_outputs, ADVISORY: advisory_outputs}
    declared = _parse_outputs(channel, raw[channel])
    other = _parse_outputs(other_channel, raw[other_channel])
    if any(other.values()):
        raise ChannelError(
            f"the undeclared {other_channel} verification exported values "
            f"({', '.join(sorted(key for key, value in other.items() if value))})"
        )
    missing = [key for key in CANDIDATE_OUTPUTS if not declared.get(key)]
    if missing:
        raise ChannelError(
            f"the {channel} verification did not export {', '.join(missing)}"
        )
    values = {key: declared[key] for key in CANDIDATE_OUTPUTS}
    if values["release_tag"] != tag:
        raise ChannelError(
            f"the {channel} verification bound {values['release_tag']!r}, "
            f"not the pushed tag {tag!r}"
        )
    values.update(
        channel=channel,
        rehearsal_workflow=REHEARSAL_WORKFLOWS[channel],
        release_assets=" ".join(sorted(CHANNEL_ASSETS[channel])),
        signed_assets=" ".join(CHANNEL_SIGNED_ASSETS[channel]),
    )
    for key, value in values.items():
        if "\n" in value or "\r" in value:
            raise ChannelError(f"the {key} value spans lines")
    return values


def confirm_exercised(evidence_path: Path, *, source_commit: str, wheel_path: Path) -> str:
    """Require smoke evidence that exercised exactly this wheel from this commit.

    Reads the ``distribution-evidence.json`` that
    ``scripts/release_engine_smoke.py compare`` writes only after the installed
    CLI and the composite Action agreed. The run's success is not taken as the
    claim: the record must name this commit and these bytes, say the two halves
    agreed, and still say the build is unqualified.
    """

    if not _FULL_SHA.fullmatch(source_commit):
        raise ChannelError("source commit must be a full lowercase 40-character SHA")
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ChannelError(f"unreadable smoke evidence {evidence_path}: {exc}") from exc
    if not isinstance(evidence, dict):
        raise ChannelError(f"smoke evidence {evidence_path} is not a JSON object")
    _, _, digest = inspect_wheel(wheel_path)
    problems = []
    if evidence.get("local_and_action_agree") is not True:
        problems.append("it does not record the installed CLI and the Action agreeing")
    if evidence.get("qualified") is not False:
        problems.append("it does not record the build as unqualified")
    if evidence.get("source_commit") != source_commit:
        problems.append(f"it exercised source {evidence.get('source_commit')!r}, not {source_commit}")
    if evidence.get("wheel_sha256") != digest:
        problems.append(f"it exercised wheel {evidence.get('wheel_sha256')!r}, not {digest}")
    if problems:
        raise ChannelError(
            "the Release Engine Smoke evidence does not cover this candidate: "
            + "; ".join(problems)
        )
    return digest


def advisory_statement(
    *,
    tag: str,
    source_commit: str,
    wheel_path: Path,
    smoke_run_id: str,
    repository: str,
    declaration: Path = DECLARATION_PATH,
) -> dict[str, object]:
    """What an advisory release says about itself, from reviewed fields.

    Written only for a version the declaration makes advisory. It names no
    qualification tier — there is no key a reader could find one under — and
    states the non-claims rather than leaving them to the absence of a file.
    """

    if resolve_tag(tag, path=declaration) != ADVISORY:
        raise ChannelError(f"{tag} is not declared advisory; it gets no advisory statement")
    if not _FULL_SHA.fullmatch(source_commit):
        raise ChannelError("source commit must be a full lowercase 40-character SHA")
    if not _RUN_ID.fullmatch(smoke_run_id):
        raise ChannelError(f"smoke run id {smoke_run_id!r} is not a workflow run id")
    if not _REPOSITORY.fullmatch(repository):
        raise ChannelError(f"repository {repository!r} is not owner/name")
    distribution, version, digest = inspect_wheel(wheel_path)
    if tag != f"v{version}":
        raise ChannelError(f"release tag {tag} does not match wheel version {version}")
    return {
        "schema": STATEMENT_SCHEMA,
        "channel": ADVISORY,
        "release_tag": tag,
        "distribution": distribution,
        "version": version,
        "source_commit": source_commit,
        "wheel_filename": wheel_path.name,
        "wheel_sha256": digest,
        "claims": {
            "qualification": "none",
            "default_ci_mode": "advisory",
            "blocking": "opt_in",
        },
        "not_claimed": [
            "No safety qualification was verified for this build, and no "
            "qualification artifact exists for it.",
            "A blocking result from this build is not a qualified verdict. Blocking "
            "is opt-in, through ci_mode: strict or a fail_on list, and no evidence "
            "bar stands behind it.",
        ],
        "channel_declaration": {"path": str(DECLARATION_PATH), "commit": source_commit},
        "exact_candidate_evidence": {
            "workflow": EXERCISE_WORKFLOW,
            "run_id": int(smoke_run_id),
            "run_url": f"https://github.com/{repository}/actions/runs/{smoke_run_id}",
            "wheel_sha256": digest,
        },
        "readiness_record": {"path": READINESS_RECORD, "commit": source_commit},
        "governing_policy": GOVERNING_POLICY,
    }


def _write_github_output(values: dict[str, str]) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    if not target:
        return
    with open(target, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Resolve and confirm a release's channel.")
    commands = parser.add_subparsers(dest="command", required=True)
    resolver = commands.add_parser("resolve", help="declared channel for a v<version> tag")
    resolver.add_argument("--tag", required=True)
    resolver.add_argument("--declaration", type=Path, default=DECLARATION_PATH)
    selector = commands.add_parser("select", help="confirm the declared verification ran alone")
    selector.add_argument("--channel", required=True)
    selector.add_argument("--qualified-result", required=True)
    selector.add_argument("--advisory-result", required=True)
    chooser = commands.add_parser(
        "candidate", help="forward the declared verification's values to publication"
    )
    chooser.add_argument("--channel", required=True)
    chooser.add_argument("--tag", required=True)
    chooser.add_argument("--qualified-result", required=True)
    chooser.add_argument("--advisory-result", required=True)
    chooser.add_argument("--qualified-outputs", required=True)
    chooser.add_argument("--advisory-outputs", required=True)
    exercised = commands.add_parser(
        "exercised", help="confirm smoke evidence covers this exact candidate"
    )
    exercised.add_argument("--evidence", type=Path, required=True)
    exercised.add_argument("--wheel", type=Path, required=True)
    exercised.add_argument("--source-commit", required=True)
    statement = commands.add_parser("statement", help="write the advisory release statement")
    statement.add_argument("--tag", required=True)
    statement.add_argument("--source-commit", required=True)
    statement.add_argument("--wheel", type=Path, required=True)
    statement.add_argument("--smoke-run-id", required=True)
    statement.add_argument("--repository", required=True)
    statement.add_argument("--output", type=Path, required=True)
    statement.add_argument("--declaration", type=Path, default=DECLARATION_PATH)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "resolve":
            channel = resolve_tag(args.tag, path=args.declaration)
            _write_github_output({"channel": channel})
            print(channel)
        elif args.command == "select":
            channel = select(
                args.channel,
                qualified_result=args.qualified_result,
                advisory_result=args.advisory_result,
            )
            _write_github_output({"channel": channel})
            print(channel)
        elif args.command == "candidate":
            values = candidate(
                args.channel,
                tag=args.tag,
                qualified_result=args.qualified_result,
                advisory_result=args.advisory_result,
                qualified_outputs=args.qualified_outputs,
                advisory_outputs=args.advisory_outputs,
            )
            _write_github_output(values)
            print(json.dumps(values, indent=2, sort_keys=True))
        elif args.command == "exercised":
            digest = confirm_exercised(
                args.evidence, source_commit=args.source_commit, wheel_path=args.wheel
            )
            print(f"OK: the smoke exercised {args.wheel.name} ({digest}) from {args.source_commit}.")
        else:
            document = advisory_statement(
                tag=args.tag,
                source_commit=args.source_commit,
                wheel_path=args.wheel,
                smoke_run_id=args.smoke_run_id,
                repository=args.repository,
                declaration=args.declaration,
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", "utf-8")
            print(f"OK: wrote the advisory statement for {args.tag} to {args.output}.")
    except (ChannelError, ReleaseError) as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
