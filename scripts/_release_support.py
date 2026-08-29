"""Standard-library-only helpers for the publication side of a release.

Everything here is deliberately import-free beyond the standard library.

The jobs that hold `id-token: write` can mint a PyPI Trusted Publishing token,
so any code they execute is inside the blast radius of a compromised
dependency. Installing the editable project plus its ranged dev extras into
such a job would put a build backend and a dozen transitive packages in that
position — before the handoff has even been verified.

Keeping the publication-side scripts on the standard library means those jobs
install nothing but the single pinned tool they need (`uv` to upload,
`sigstore` to sign), and never execute project code at all.

The read-only verification job has no such constraint and may use the full
project; `scripts/verify_wheel_provenance.py` runs only there.
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from email.parser import BytesParser
from email.policy import default as email_policy
from pathlib import Path

DISTRIBUTION_NAME = "agents-shipgate"
SHA256_PATTERN = re.compile(r"\A[0-9a-f]{64}\Z")

PRODUCTION_QUALIFICATION_TIER = "beta"
PRE_1_0_QUALIFICATION_TIER = "pre_1_0"

# Case count per named qualification policy. Restated here because the sealing
# job may not import the project's pydantic schemas, and bound to the real
# constructors by ``test_the_stdlib_case_counts_match_the_named_policies`` so
# the two copies cannot drift.
QUALIFICATION_CASE_COUNTS = {
    PRODUCTION_QUALIFICATION_TIER: 100,
    PRE_1_0_QUALIFICATION_TIER: 56,
}

# PEP 440 leading ``[N!]N(.N)*``. Only the epoch and the first release segment
# decide which policy governs a tag, so nothing after them is parsed.
_VERSION_PREFIX = re.compile(r"\A(?:(?P<epoch>\d+)!)?(?P<release>\d+(?:\.\d+)*)")


def release_version_is_pre_1_0(version: str) -> bool:
    """True only for an epoch-0 version whose major release segment is 0.

    Fail-closed by construction: an unparsable version is *not* pre-1.0, so it
    falls through to the strictest policy rather than the cheapest one.
    """

    match = _VERSION_PREFIX.match(version.strip())
    if match is None:
        return False
    if int(match.group("epoch") or 0) != 0:
        return False
    return int(match.group("release").split(".")[0]) == 0


def accepted_qualification_tiers(version: str) -> tuple[str, ...]:
    """Qualification tiers whose evidence may publish ``version``.

    ``0.x`` accepts the pre-1.0 policy approved for issue #341 *and* the
    stronger production one -- a release is never rejected for carrying more
    evidence than its tag requires. Everything else accepts production only.
    """

    if release_version_is_pre_1_0(version):
        return (PRODUCTION_QUALIFICATION_TIER, PRE_1_0_QUALIFICATION_TIER)
    return (PRODUCTION_QUALIFICATION_TIER,)


def describe_accepted_tiers(accepted: tuple[str, ...]) -> str:
    """Render the accepted set for an error message."""

    if len(accepted) == 1:
        return accepted[0]
    return "one of " + ", ".join(accepted)


class ReleaseError(RuntimeError):
    """A release precondition failed and publication must not proceed."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonicalize_name(name: str) -> str:
    """Canonical distribution name per PEP 503.

    Reimplemented rather than imported from ``packaging`` so this module stays
    dependency-free; the rule is one regex and is stable.
    """

    return re.sub(r"[-_.]+", "-", name).lower()


def parse_wheel_filename(filename: str) -> tuple[str, str, str | None, frozenset[str]]:
    """Return ``(distribution, version, build tag, compatibility tags)``.

    A stdlib stand-in for ``packaging.utils.parse_wheel_filename`` so the
    sealing job needs no third-party import. Per PEP 427 a wheel name is
    ``{distribution}-{version}(-{build})?-{python}-{abi}-{platform}.whl``, and
    the compressed tag fields expand on ``.`` into their cross product.
    """

    if not filename.endswith(".whl"):
        raise ReleaseError(f"Not a wheel filename: {filename}")
    parts = filename[: -len(".whl")].split("-")
    if len(parts) not in (5, 6):
        raise ReleaseError(f"Unparsable wheel filename: {filename}")
    distribution = canonicalize_name(parts[0])
    version = parts[1]
    build = parts[2] if len(parts) == 6 else None
    pythons, abis, platforms = parts[-3:]
    tags = frozenset(
        f"{python}-{abi}-{platform}"
        for python in pythons.split(".")
        for abi in abis.split(".")
        for platform in platforms.split(".")
    )
    if not version or not tags:
        raise ReleaseError(f"Unparsable wheel filename: {filename}")
    return distribution, version, build, tags


def inspect_wheel(path: Path) -> tuple[str, str, str]:
    """Return canonical distribution name, version, and content digest."""

    if not path.is_file() or path.suffix != ".whl":
        raise ReleaseError(f"Wheel not found or not a .whl file: {path}")
    try:
        with zipfile.ZipFile(path) as archive:
            metadata_names = [
                name
                for name in archive.namelist()
                if name.endswith(".dist-info/METADATA") and "/" in name
            ]
            if len(metadata_names) != 1:
                raise ReleaseError(
                    f"Wheel must contain exactly one .dist-info/METADATA file: {path}"
                )
            metadata = BytesParser(policy=email_policy).parsebytes(archive.read(metadata_names[0]))
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        raise ReleaseError(f"Invalid wheel {path}: {exc}") from exc

    name = str(metadata.get("Name", "")).strip()
    version = str(metadata.get("Version", "")).strip()
    canonical_name = canonicalize_name(name)
    if canonical_name != DISTRIBUTION_NAME or not version:
        raise ReleaseError(
            f"Release requires an {DISTRIBUTION_NAME} wheel with Name and Version: {path}"
        )
    return canonical_name, version, sha256_file(path)
