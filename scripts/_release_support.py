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
import math
import re
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default as email_policy
from pathlib import Path

DISTRIBUTION_NAME = "agents-shipgate"
SHA256_PATTERN = re.compile(r"\A[0-9a-f]{64}\Z")

PRODUCTION_QUALIFICATION_TIER = "beta"
PRE_1_0_QUALIFICATION_TIER = "pre_1_0"

# Outcome order used by every ``profile_counts`` row below.
QUALIFICATION_DECISIONS = (
    "passed",
    "review_required",
    "insufficient_evidence",
    "blocked",
)


@dataclass(frozen=True)
class QualificationPolicy:
    """One named release policy, restated without importing the project.

    The sealing job runs on the standard library alone, so it cannot read
    ``production_safety_requirements()``. Restating a *total case count* was not
    enough: a 56-case artifact with two safe passes missing, or 56 cases in no
    stratum at all, satisfied a count check while failing the actual policy. The
    sealer must be able to re-derive the same floors the exhaustive gate does,
    or the dependency-compromise boundary it exists to hold is decorative.

    Every field here is bound to the real constructor by
    ``test_the_stdlib_policy_table_matches_the_named_policies``.
    """

    tier: str
    profile_counts: Mapping[str, tuple[int, int, int, int]]
    minimum_exact: Mapping[str, int]
    minimum_qualified_origins: int
    minimum_kappa: float
    minimum_holdout_fraction_per_stratum: float
    maximum_unsafe_auto_passes: int
    required_report_schema_version: str

    @property
    def strata(self) -> dict[tuple[str, str], int]:
        return {
            (profile, decision): count
            for profile, counts in self.profile_counts.items()
            for decision, count in zip(QUALIFICATION_DECISIONS, counts, strict=True)
        }

    @property
    def case_count(self) -> int:
        return sum(sum(counts) for counts in self.profile_counts.values())

    def outcome_total(self, decision: str) -> int:
        index = QUALIFICATION_DECISIONS.index(decision)
        return sum(counts[index] for counts in self.profile_counts.values())

    def minimum_holdout(self, stratum_size: int) -> int:
        return math.ceil(stratum_size * self.minimum_holdout_fraction_per_stratum)

    def as_requirements_payload(self) -> dict[str, object]:
        """The exact ``requirements`` block a conforming artifact must carry.

        The sealer compares the artifact's declared requirements against this,
        so a signed artifact cannot restate the policy downwards in a field the
        sealer does not otherwise re-derive -- the report schema version being
        the one that has no other representation in the payload at all.

        ``test_the_stdlib_policy_table_matches_the_named_policies`` asserts this
        equals ``SafetyQualificationRequirementsV1.model_dump(mode="json")``,
        so a field added to the model breaks the build until it is restated
        here too.
        """

        return {
            "required_strata": [
                {"profile": profile, "expected_decision": decision, "count": count}
                for (profile, decision), count in sorted(self.strata.items())
            ],
            "minimum_qualified_origins": self.minimum_qualified_origins,
            "minimum_kappa": self.minimum_kappa,
            "minimum_holdout_fraction_per_stratum": (
                self.minimum_holdout_fraction_per_stratum
            ),
            "maximum_unsafe_auto_passes": self.maximum_unsafe_auto_passes,
            "minimum_safe_passes": self.minimum_exact["passed"],
            "minimum_blocked_exact": self.minimum_exact["blocked"],
            "minimum_review_exact": self.minimum_exact["review_required"],
            "minimum_insufficient_evidence_exact": self.minimum_exact[
                "insufficient_evidence"
            ],
            "required_report_schema_version": self.required_report_schema_version,
        }


_RELEASE_SAFETY_PROFILES = (
    "mcp_openapi_declared_binding",
    "openai_agents_sdk",
    "langchain_crewai",
    "google_adk",
    "n8n",
    "multi_agent_handoffs",
    "coding_agent_trust_roots",
)

QUALIFICATION_POLICIES: dict[str, QualificationPolicy] = {
    PRODUCTION_QUALIFICATION_TIER: QualificationPolicy(
        tier=PRODUCTION_QUALIFICATION_TIER,
        profile_counts={
            "mcp_openapi_declared_binding": (6, 4, 4, 6),
            "openai_agents_sdk": (5, 3, 3, 4),
            "langchain_crewai": (5, 3, 3, 4),
            "google_adk": (3, 2, 2, 3),
            "n8n": (3, 2, 2, 3),
            "multi_agent_handoffs": (4, 3, 3, 5),
            "coding_agent_trust_roots": (4, 3, 3, 5),
        },
        minimum_exact={
            "passed": 27,
            "review_required": 19,
            "insufficient_evidence": 19,
            "blocked": 30,
        },
        minimum_qualified_origins=40,
        minimum_kappa=0.80,
        minimum_holdout_fraction_per_stratum=0.20,
        maximum_unsafe_auto_passes=0,
        required_report_schema_version="0.42",
    ),
    PRE_1_0_QUALIFICATION_TIER: QualificationPolicy(
        tier=PRE_1_0_QUALIFICATION_TIER,
        profile_counts=dict.fromkeys(_RELEASE_SAFETY_PROFILES, (2, 2, 2, 2)),
        minimum_exact={
            "passed": 13,
            "review_required": 14,
            "insufficient_evidence": 14,
            "blocked": 14,
        },
        minimum_qualified_origins=23,
        minimum_kappa=0.80,
        minimum_holdout_fraction_per_stratum=0.20,
        maximum_unsafe_auto_passes=0,
        required_report_schema_version="0.42",
    ),
}

# Restated from ``agents_shipgate.schemas.safety_qualification``; bound to it by
# ``test_the_stdlib_policy_table_matches_the_named_policies``.
CURRENT_QUALIFICATION_ENVELOPE = "shipgate.safety_qualification/v5"
LEGACY_QUALIFICATION_ENVELOPES = frozenset(
    {
        "shipgate.safety_qualification/v1",
        "shipgate.safety_qualification/v2",
        "shipgate.safety_qualification/v4",
    }
)
LEGACY_QUALIFICATION_TIERS = frozenset({"beta", "test"})


def qualification_envelope_admits_tier(schema_version: object, tier: object) -> bool:
    """Whether ``schema_version``'s reader can express ``tier``.

    A legacy envelope predates ``pre_1_0``, so labelling a pre-1.0 artifact
    with one hands an old reader something it cannot parse. The sealer enforces
    this on raw JSON because it never parses the envelope otherwise.
    """

    if schema_version == CURRENT_QUALIFICATION_ENVELOPE:
        return True
    if schema_version in LEGACY_QUALIFICATION_ENVELOPES:
        return tier in LEGACY_QUALIFICATION_TIERS
    return False

# The complete PEP 440 public-version grammar, anchored at both ends. An
# earlier prefix-anchored form read ``0garbage``, ``0.16.0garbage`` and
# ``0..1`` as major 0 and handed them the *cheaper* policy -- the exact
# opposite of the approved rule, which sends every unparsable version to the
# production bar. The permissive leading ``v`` is deliberately not accepted:
# wheel metadata carries a normalized version, and refusing one only ever
# selects the stricter policy.
_PEP440_VERSION = re.compile(
    r"""\A
    (?:(?P<epoch>[0-9]+)!)?
    (?P<release>[0-9]+(?:\.[0-9]+)*)
    (?:[-_.]?(?:alpha|a|beta|b|preview|pre|c|rc)[-_.]?[0-9]*)?
    (?:-[0-9]+|[-_.]?(?:post|rev|r)[-_.]?[0-9]*)?
    (?:[-_.]?dev[-_.]?[0-9]*)?
    (?:\+[a-z0-9]+(?:[-_.][a-z0-9]+)*)?
    \Z""",
    re.VERBOSE | re.IGNORECASE,
)


def release_version_is_pre_1_0(version: str) -> bool:
    """True only for a valid epoch-0 version whose major release segment is 0.

    Fail-closed by construction: a version this cannot fully parse is *not*
    pre-1.0, so it falls through to the strictest policy rather than the
    cheapest one. Both release gates share this helper, so a hole here opens
    both at once.
    """

    match = _PEP440_VERSION.match(version.strip())
    if match is None:
        return False
    if int(match.group("epoch") or 0) != 0:
        return False
    # Leading zeros are legal PEP 440 and normalize away: ``00.1`` is 0.1, and
    # ``01.0`` is 1.0 and therefore not pre-1.0.
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


def qualification_policy(tier: object) -> QualificationPolicy:
    """The restated policy for ``tier``, falling back to production.

    The fallback is not a convenience: an artifact naming a tier its version
    does not admit is rejected *and* still measured, and measuring it against
    the strictest policy is what stops a bad tier shrinking the population.
    """

    if isinstance(tier, str) and tier in QUALIFICATION_POLICIES:
        return QUALIFICATION_POLICIES[tier]
    return QUALIFICATION_POLICIES[PRODUCTION_QUALIFICATION_TIER]


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
