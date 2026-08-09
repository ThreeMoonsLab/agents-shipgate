#!/usr/bin/env python3
"""Bind the qualified/published wheel to a wheel built from the tagged source.

The release pipeline previously established three bindings — tag to
``pyproject.toml`` version, qualification payload to wheel bytes, and tag to
the wheel's own ``METADATA`` version — but nothing tied the shipped bytes back
to the tagged commit. Any wheel declaring ``Name: agents-shipgate`` and the
right ``Version`` satisfied every check regardless of what source produced it.

This module closes that gap. The release job builds a wheel from the tagged
checkout and this verifier requires it to match the qualified wheel:

``identical_bytes``
    SHA-256 of both archives is equal. This is the preferred outcome and the
    only one accepted by default: it makes every release a reproducible-build
    check as a side effect.

``identical_payload``
    Archive bytes differ, but every member name, permission mode, and member
    content digest is equal — the difference is zip container metadata only
    (entry ordering, timestamps, compression level). This is the explicitly
    approved *interim* control for the case where wheel archives are not yet
    bit-for-bit reproducible. It is rejected unless
    ``--allow-payload-equivalent`` is passed, so the weaker bar can never be
    taken silently.

``mismatch``
    Member sets or member contents differ. Always fatal.

A ``Generator:`` change in ``.dist-info/WHEEL`` (a build-backend version bump)
is a *content* difference and therefore a mismatch, not a tolerated container
difference. That is deliberate: the fix is to align the pinned build backend
(``constraints/release-build.txt``), not to widen the comparison. The report
names the differing member so the operator can see that immediately.

Run from the repo root:

    python scripts/verify_wheel_provenance.py \\
        --built dist-build/agents_shipgate-0.16.0-py3-none-any.whl \\
        --qualified qualified-dist/agents_shipgate-0.16.0-py3-none-any.whl \\
        --report provenance.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path
from typing import Literal

from agents_shipgate.core.errors import ConfigError

ProvenanceMode = Literal["identical_bytes", "identical_payload", "mismatch"]

# Number of differing members named in the failure message. A full listing of a
# 500-member wheel buries the signal in CI logs; the first few are enough to
# tell "backend version bump" from "different source tree" apart.
_MAX_REPORTED_DIFFERENCES = 20


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _wheel_payload(path: Path) -> dict[str, tuple[str, str]]:
    """Return ``member name -> (content sha256, permission mode)``.

    Directory entries are skipped: they carry no payload and their presence
    varies between zip writers. Permission bits are normalised to the low 12
    bits because some writers store the file-type bits in ``external_attr``
    and some do not, which is a container detail rather than a payload one.
    """

    if not path.is_file() or path.suffix != ".whl":
        raise ConfigError(f"Wheel not found or not a .whl file: {path}")
    payload: dict[str, tuple[str, str]] = {}
    try:
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                mode = oct((info.external_attr >> 16) & 0o7777)
                payload[info.filename] = (
                    hashlib.sha256(archive.read(info.filename)).hexdigest(),
                    mode,
                )
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        raise ConfigError(f"Invalid wheel {path}: {exc}") from exc
    if not payload:
        raise ConfigError(f"Wheel contains no files: {path}")
    return payload


def _describe_differences(
    built: dict[str, tuple[str, str]],
    qualified: dict[str, tuple[str, str]],
) -> list[str]:
    differences: list[str] = []
    for name in sorted(set(built) - set(qualified)):
        differences.append(f"only in built wheel: {name}")
    for name in sorted(set(qualified) - set(built)):
        differences.append(f"only in qualified wheel: {name}")
    for name in sorted(set(built) & set(qualified)):
        built_digest, built_mode = built[name]
        qualified_digest, qualified_mode = qualified[name]
        if built_digest != qualified_digest:
            differences.append(
                f"content differs: {name} "
                f"(built {built_digest[:12]}, qualified {qualified_digest[:12]})"
            )
        elif built_mode != qualified_mode:
            differences.append(
                f"mode differs: {name} (built {built_mode}, qualified {qualified_mode})"
            )
    return differences


def compare_wheels(built_path: Path, qualified_path: Path) -> tuple[ProvenanceMode, list[str]]:
    """Classify how a wheel built from source relates to the qualified wheel."""

    built_sha256 = _sha256_file(built_path)
    qualified_sha256 = _sha256_file(qualified_path)
    if built_sha256 == qualified_sha256:
        return "identical_bytes", []

    differences = _describe_differences(_wheel_payload(built_path), _wheel_payload(qualified_path))
    if not differences:
        return "identical_payload", []
    return "mismatch", differences


def verify_wheel_provenance(
    *,
    built_path: Path,
    qualified_path: Path,
    allow_payload_equivalent: bool = False,
    source_commit: str | None = None,
) -> dict[str, object]:
    """Return a provenance record, or raise ``ConfigError`` if unpublishable.

    Raising is the whole point: the caller runs before ``uv publish``, so an
    exception here is what keeps an unbound artifact off PyPI.
    """

    mode, differences = compare_wheels(built_path, qualified_path)
    if mode == "mismatch":
        shown = differences[:_MAX_REPORTED_DIFFERENCES]
        omitted = len(differences) - len(shown)
        detail = "; ".join(shown)
        if omitted > 0:
            detail += f"; (+{omitted} more)"
        raise ConfigError(
            "Wheel built from the tagged source does not match the qualified wheel. "
            "The qualified wheel was not produced by this source tree, or the build "
            "backend pin drifted. Differences: " + detail
        )
    if mode == "identical_payload" and not allow_payload_equivalent:
        raise ConfigError(
            "Wheel archives are not byte-identical. Member contents match, so this is a "
            "build-reproducibility gap rather than a source mismatch, but the stronger bar "
            "is required by default. Align the build backend pin in "
            "constraints/release-build.txt, or re-run with --allow-payload-equivalent "
            "as an explicit, tracked interim control."
        )

    return {
        "provenance_mode": mode,
        "built_wheel": built_path.name,
        "built_wheel_sha256": _sha256_file(built_path),
        "qualified_wheel": qualified_path.name,
        "qualified_wheel_sha256": _sha256_file(qualified_path),
        "source_commit": source_commit,
        "byte_reproducible": mode == "identical_bytes",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify the qualified wheel was produced by the tagged source tree."
    )
    parser.add_argument("--built", type=Path, required=True, help="wheel built from the checkout")
    parser.add_argument(
        "--qualified", type=Path, required=True, help="signed, qualified wheel to be published"
    )
    parser.add_argument("--report", type=Path, help="write a JSON provenance record here")
    parser.add_argument("--source-commit", help="commit SHA the built wheel came from")
    parser.add_argument(
        "--allow-payload-equivalent",
        action="store_true",
        help=(
            "accept container-metadata-only differences as an explicit interim control; "
            "the remaining reproducibility gap must be tracked separately"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        record = verify_wheel_provenance(
            built_path=args.built,
            qualified_path=args.qualified,
            allow_payload_equivalent=args.allow_payload_equivalent,
            source_commit=args.source_commit,
        )
        if args.report:
            args.report.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", "utf-8")
    except (ConfigError, OSError, ValueError) as exc:
        sys.stderr.write(f"Wheel provenance error: {exc}\n")
        return 1
    sys.stdout.write(
        f"OK: qualified wheel is bound to the tagged source ({record['provenance_mode']}); "
        f"sha256 {record['qualified_wheel_sha256']}.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
