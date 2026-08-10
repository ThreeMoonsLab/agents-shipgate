#!/usr/bin/env python3
"""Content-addressed handoff and idempotence guard for the publication jobs.

Standard library only, on purpose — see ``scripts/_release_support``. The jobs
that run this are the ones able to mint a PyPI token, so they install no
project code.

Verification and publication run as separate jobs so that expensive, read-only
checking cannot hold write or OIDC permissions, and so an immutable PyPI upload
is never entangled with the steps that decide whether it should happen. That
split introduces a new obligation: the publication job must prove it is
shipping *exactly* the bytes verification approved, not merely an artifact with
the same name.

``manifest`` records every candidate asset with its SHA-256. The verification
job emits the manifest's own digest as a job output; job outputs travel through
GitHub's trusted channel rather than the artifact store, so
``verify-manifest --expected-sha256`` closes the loop:

    job output digest -> manifest bytes -> per-asset digests -> asset bytes

The check is closed-world. Verifying only the *listed* assets would leave an
intact manifest sitting beside an unlisted sdist or executable that a
subsequent ``dist/*`` upload would happily publish, so the directory contents
must equal the manifest exactly, and every entry must be a regular file.

``pypi-state`` answers the question a retry must ask before re-uploading an
immutable version. PyPI uploads cannot be replaced, so "just re-run the job" is
not a recovery procedure — it either fails confusingly or, worse, succeeds
against a version that already holds different bytes. The three states are:

``absent``
    Version not on the index. Publication proceeds.
``published_identical``
    The index holds exactly one file for this version: an unyanked wheel with
    the expected filename and digest. The upload already succeeded; a re-run is
    completing an interrupted transaction, so the publish step is skipped and
    finalisation continues.
``published_divergent``
    Anything else. Always fatal.

That last classification is deliberately strict about *the whole file set*, not
just "our digest appears somewhere". A release that also carries a divergent
sdist, a second wheel, a renamed file, or a yanked record is not the release
this pipeline verified, and treating it as identical would skip the upload and
finalise over it.

Run from the repo root:

    python scripts/release_publication.py manifest --tag v0.16.0 \\
        --source-commit "$SOURCE_SHA" --wheel dist/agents_shipgate-0.16.0-py3-none-any.whl \\
        --asset dist/agents-shipgate-sbom.json --output dist/candidate-manifest.json
    python scripts/release_publication.py verify-manifest \\
        --manifest dist/candidate-manifest.json --expected-sha256 "$DIGEST"
    python scripts/release_publication.py pypi-state \\
        --wheel dist/agents_shipgate-0.16.0-py3-none-any.whl
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

if __package__:
    from scripts._release_support import (
        SHA256_PATTERN,
        ReleaseError,
        inspect_wheel,
        sha256_file,
    )
else:  # ``python scripts/release_publication.py``
    from _release_support import (
        SHA256_PATTERN,
        ReleaseError,
        inspect_wheel,
        sha256_file,
    )

DEFAULT_INDEX = "https://pypi.org/pypi"
_NETWORK_TIMEOUT_SECONDS = 30


def build_manifest(
    *,
    tag: str,
    source_commit: str,
    wheel_path: Path,
    asset_paths: list[Path],
    output_path: Path,
) -> dict[str, Any]:
    """Write a content-addressed record of every asset the release will ship."""

    wheel_name, wheel_version, wheel_sha256 = inspect_wheel(wheel_path)
    if tag != f"v{wheel_version}":
        raise ReleaseError(f"Release tag {tag} does not match wheel version {wheel_version}")

    assets = []
    for path in sorted({*asset_paths, wheel_path}, key=lambda item: item.name):
        if not path.is_file():
            raise ReleaseError(f"Candidate asset not found: {path}")
        assets.append({"filename": path.name, "sha256": sha256_file(path)})

    manifest = {
        "release_tag": tag,
        "source_commit": source_commit,
        "distribution": wheel_name,
        "version": wheel_version,
        "wheel_filename": wheel_path.name,
        "wheel_sha256": wheel_sha256,
        "assets": assets,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def _assert_closed_world(
    manifest_path: Path,
    base: Path,
    expected: set[str],
    allowed_extra: set[str],
    required_extra: set[str],
) -> None:
    """Reject anything in the candidate directory the manifest does not name.

    ``allowed_extra`` permits a file; ``required_extra`` demands it. The
    distinction matters at the end of the transaction: the signature bundles
    are produced *after* the manifest is sealed, so they cannot be listed in
    it, but a finished release that is missing one — or that carries arbitrary
    bytes under the expected name — is not complete. Permitting without
    requiring let exactly that pass.
    """

    allowed = expected | {manifest_path.name} | allowed_extra | required_extra
    present: set[str] = set()
    for entry in sorted(base.rglob("*")):
        if entry.is_dir():
            continue
        relative = entry.relative_to(base).as_posix()
        if entry.is_symlink() or not entry.is_file():
            raise ReleaseError(f"Candidate handoff contains a non-regular entry: {relative}")
        present.add(relative)

    unexpected = sorted(present - allowed)
    if unexpected:
        raise ReleaseError(
            "Candidate handoff contains files the manifest does not list "
            f"({', '.join(unexpected)}); publication would upload unverified bytes."
        )
    absent = sorted(required_extra - present)
    if absent:
        raise ReleaseError(
            f"Release is missing required assets ({', '.join(absent)}); "
            "the transaction is not complete."
        )


def verify_manifest(
    *,
    manifest_path: Path,
    expected_sha256: str | None = None,
    directory: Path | None = None,
    allowed_extra: set[str] | None = None,
    required_extra: set[str] | None = None,
) -> dict[str, Any]:
    """Re-derive every digest the verification job recorded.

    ``expected_sha256`` is compared whenever it is supplied, including when it
    is an empty or malformed string. A truthiness test here would fail open:
    a missing or redacted job output arrives as ``""`` and the workflow still
    passes ``--expected-sha256 ""``, silently disabling the one binding that
    does not travel through the artifact store.
    """

    if not manifest_path.is_file():
        raise ReleaseError(f"Candidate manifest not found: {manifest_path}")

    if expected_sha256 is not None:
        if not SHA256_PATTERN.fullmatch(expected_sha256):
            raise ReleaseError(
                "Expected manifest digest is not a 64-character lowercase SHA-256 "
                f"({expected_sha256!r}); the verification job output was missing or redacted."
            )
        actual_sha256 = sha256_file(manifest_path)
        if actual_sha256 != expected_sha256:
            raise ReleaseError(
                "Candidate manifest digest does not match the verification job output "
                f"(expected {expected_sha256}, got {actual_sha256}); the artifact handoff was "
                "modified between verification and publication."
            )

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"Invalid candidate manifest {manifest_path}: {exc}") from exc

    base = directory or manifest_path.parent
    errors: list[str] = []
    listed: set[str] = set()
    for asset in manifest.get("assets", []):
        filename = str(asset.get("filename", ""))
        listed.add(filename)
        path = base / filename
        if not path.is_file():
            errors.append(f"missing asset {filename}")
            continue
        digest = sha256_file(path)
        if digest != asset.get("sha256"):
            errors.append(
                f"{filename} digest {digest} does not match the verified {asset.get('sha256')}"
            )
    if errors:
        raise ReleaseError("Candidate handoff rejected: " + "; ".join(errors))

    _assert_closed_world(
        manifest_path, base, listed, allowed_extra or set(), required_extra or set()
    )
    return manifest


def _fetch_release_files(distribution: str, version: str, index: str) -> list[dict[str, Any]]:
    url = f"{index.rstrip('/')}/{distribution}/{version}/json"
    if not url.startswith("https://"):
        raise ReleaseError(f"Index URL must use HTTPS: {url}")
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(  # noqa: S310 - scheme asserted https above
            request, timeout=_NETWORK_TIMEOUT_SECONDS
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return []
        raise ReleaseError(f"Unable to query {url}: HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeError) as exc:
        # Deliberately not treated as "absent": an unreachable index must not
        # be read as permission to upload.
        raise ReleaseError(f"Unable to query {url}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReleaseError(f"Malformed index response for {url}")
    files = payload.get("urls", [])
    if not isinstance(files, list) or not all(isinstance(item, dict) for item in files):
        raise ReleaseError(f"Malformed index file list for {url}")
    return files


def _classify_published(
    files: list[dict[str, Any]], *, wheel_filename: str, wheel_sha256: str
) -> str:
    """Require the index to hold exactly the one file this pipeline publishes."""

    if len(files) != 1:
        return "published_divergent"
    record = files[0]
    digests = record.get("digests")
    if not isinstance(digests, dict):
        return "published_divergent"
    matches = (
        str(record.get("filename", "")) == wheel_filename
        and str(record.get("packagetype", "")) == "bdist_wheel"
        and str(digests.get("sha256", "")) == wheel_sha256
        and record.get("yanked") is not True
    )
    return "published_identical" if matches else "published_divergent"


def pypi_state(*, wheel_path: Path, index: str = DEFAULT_INDEX) -> dict[str, Any]:
    """Classify whether this exact wheel — and nothing else — is on the index."""

    distribution, version, wheel_sha256 = inspect_wheel(wheel_path)
    files = _fetch_release_files(distribution, version, index)
    if not files:
        state = "absent"
    else:
        state = _classify_published(
            files, wheel_filename=wheel_path.name, wheel_sha256=wheel_sha256
        )

    if state == "published_divergent":
        raise ReleaseError(
            f"{distribution} {version} is already on the index, but not as the single "
            f"unyanked wheel {wheel_path.name} with digest {wheel_sha256}. PyPI uploads are "
            "immutable, so this tag cannot be republished. Cut a new version; see "
            "docs/release-runbook.md for the recovery procedure."
        )
    return {
        "state": state,
        "distribution": distribution,
        "version": version,
        "wheel_sha256": wheel_sha256,
        "should_publish": state == "absent",
    }


def _emit_github_output(values: dict[str, Any], output_path: str | None) -> None:
    if not output_path:
        return
    with Path(output_path).open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Content-addressed release handoff and publication idempotence guard."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    manifest = subparsers.add_parser("manifest", help="record candidate assets and digests")
    manifest.add_argument("--tag", required=True)
    manifest.add_argument("--source-commit", required=True)
    manifest.add_argument("--wheel", type=Path, required=True)
    manifest.add_argument("--asset", type=Path, action="append", default=[])
    manifest.add_argument("--output", type=Path, required=True)

    verify = subparsers.add_parser("verify-manifest", help="re-derive the handoff digests")
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument(
        "--expected-sha256",
        required=True,
        help=(
            "manifest digest from the verification job output; required so a missing "
            "or redacted value cannot silently skip the binding"
        ),
    )
    verify.add_argument("--directory", type=Path)
    verify.add_argument(
        "--allow",
        action="append",
        default=[],
        metavar="FILENAME",
        help=(
            "additionally permit this filename in the directory; for signature "
            "bundles produced after the manifest was sealed"
        ),
    )
    verify.add_argument(
        "--require",
        action="append",
        default=[],
        metavar="FILENAME",
        help=(
            "additionally require this filename to be present; use for the "
            "final asset set, where a missing signature bundle means the "
            "transaction did not complete"
        ),
    )

    state = subparsers.add_parser("pypi-state", help="classify the index state for this wheel")
    state.add_argument("--wheel", type=Path, required=True)
    state.add_argument("--index", default=DEFAULT_INDEX)
    state.add_argument("--github-output", help="append should_publish/state here")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "manifest":
            manifest = build_manifest(
                tag=args.tag,
                source_commit=args.source_commit,
                wheel_path=args.wheel,
                asset_paths=list(args.asset),
                output_path=args.output,
            )
            digest = sha256_file(args.output)
            sys.stdout.write(
                f"OK: candidate manifest for {manifest['release_tag']} lists "
                f"{len(manifest['assets'])} assets; manifest sha256 {digest}.\n"
            )
        elif args.command == "verify-manifest":
            manifest = verify_manifest(
                manifest_path=args.manifest,
                expected_sha256=args.expected_sha256,
                directory=args.directory,
                allowed_extra=set(args.allow),
                required_extra=set(args.require),
            )
            sys.stdout.write(
                f"OK: all {len(manifest['assets'])} candidate assets match the verified digests.\n"
            )
        else:
            result = pypi_state(wheel_path=args.wheel, index=args.index)
            _emit_github_output(
                {
                    "state": result["state"],
                    "should_publish": str(result["should_publish"]).lower(),
                },
                args.github_output,
            )
            sys.stdout.write(
                f"OK: {result['distribution']} {result['version']} index state "
                f"is {result['state']}; should_publish={result['should_publish']}.\n"
            )
    except (ReleaseError, OSError, ValueError) as exc:
        sys.stderr.write(f"Release publication error: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
