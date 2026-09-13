"""The report ``1.0`` compatibility boundary (#569).

``STABILITY.md`` promised that a ``1.0`` product line would not begin until the
report schema reached ``1.0`` and held without a breaking change. Freezing the
number is the easy half. The half that makes the promise mean something is
this module: one place that decides whether a report payload is something this
engine is entitled to interpret, and refuses -- by name, with a route -- when
it is not.

Three properties this boundary exists to hold:

**No silent reinterpretation.** ``ReadinessReport`` is ``extra="allow"`` and
almost every field carries a default, so ``model_validate`` will happily accept
a ``0.9`` payload and hand back an object whose thirty-four newer blocks are
filled with this model's defaults. Nothing in that object says which values
were read and which were invented. A pre-freeze payload is therefore refused
here, *before* validation, rather than normalized into a shape it never had.

**No authority upgrade.** Refusal is the only outcome for an unsupported
artifact. This module converts nothing and restamps nothing: there is no path
by which an artifact written under an older contract acquires the standing of
one written under the current engine. The qualification gate holds the same
line from the other side -- ``required_report_schema_version`` is compared for
exact equality, so an older receipt cannot satisfy a ``1.0`` requirement by
being relabelled.

**Fail-closed on the unknown.** A version this cannot parse is refused. So, by
default, is a version from a *newer* engine: the additive-only 1.x rule is a
promise to consumers reading our output, and it is not a licence to *compare*
today's evidence against evidence written under a contract we do not have.

That last rule is off by default and relaxed explicitly, not the other way
round, because the two directions fail differently. A projection reader that
is wrongly strict refuses an artifact it could have read -- annoying, safe. An
evidence comparison that is wrongly lenient diffs against blocks it cannot
interpret -- silent, and wrong in the direction that matters. So the strict
rule is what a new caller gets by forgetting, and ``accept_newer_minor=True``
is a claim the caller makes about itself: "I only project what is in the
payload; a field I have never heard of changes nothing I output."
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

#: The frozen major. Every ``1.x`` report is additive over ``1.0``: no field is
#: renamed, retyped, removed, or given a new meaning within the major. A change
#: that cannot be expressed that way needs ``2.0`` -- see
#: ``docs/report-1-0-contract.md``.
REPORT_CONTRACT_MAJOR = 1

#: The first frozen version. Reports below it were written under the pre-1.0
#: additive-versioned line, which carried no compatibility promise.
FIRST_FROZEN_REPORT_SCHEMA_VERSION = "1.0"

#: The last pre-freeze version this engine ever emitted. Named so the refusal
#: can tell a reader *which* line their artifact belongs to instead of only
#: that it is old.
LAST_PRE_FREEZE_REPORT_SCHEMA_VERSION = "0.43"

ReportSchemaStatus = Literal[
    "supported",
    "missing",
    "malformed",
    "pre_freeze",
    "newer_than_engine",
    "future_major",
]

#: The command that regenerates a report from its own workspace. A refusal
#: that does not name this is a dead end: the artifact cannot be converted, so
#: the only route forward is a fresh scan of the source the artifact described.
REGENERATE_REPORT_COMMAND = "agents-shipgate scan -c shipgate.yaml --format json"


class ReportSchemaCompatibilityError(ValueError):
    """A report payload this engine must not interpret.

    Carries a stable ``reason_code`` so a caller can route on the cause
    without matching prose, and so the same cause reads the same way at every
    boundary that raises it.
    """

    def __init__(self, message: str, *, reason_code: str, version: Any) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.version = version


@dataclass(frozen=True)
class ReportSchemaSupport:
    """What this engine can do with one declared report schema version."""

    status: ReportSchemaStatus
    version: Any
    #: Populated for every status except ``supported``.
    reason: str = ""

    @property
    def supported(self) -> bool:
        return self.status == "supported"


def current_report_schema_version() -> str:
    """The version this build emits.

    Read from the model rather than restated. A second literal is how the
    qualification gate ended up demanding a schema no build produced (#416).
    """

    from agents_shipgate.schemas.report import ReadinessReport

    return str(ReadinessReport.model_fields["report_schema_version"].default)


def parse_report_schema_version(value: Any) -> tuple[int, ...] | None:
    """``"1.2"`` -> ``(1, 2)``; anything else -> ``None``.

    Deliberately strict. ``"1.0.0"`` and ``"1"`` are not versions this engine
    ever emitted, and guessing what a caller meant is exactly the silent
    reinterpretation this module exists to prevent.
    """

    if not isinstance(value, str):
        return None
    parts = value.split(".")
    if len(parts) != 2:
        return None
    try:
        parsed = tuple(int(part) for part in parts)
    except ValueError:
        return None
    if any(part < 0 for part in parsed):
        return None
    # ``int()`` accepts "+1", " 1" and non-ASCII digits; a version string that
    # is not exactly what we emit is not one we recognise.
    if any(part != str(number) for part, number in zip(parts, parsed, strict=True)):
        return None
    return parsed


def classify_report_schema_version(value: Any) -> ReportSchemaSupport:
    """Decide what this engine may do with ``value``, and say why.

    Returns a verdict for every *input*: nothing about the payload raises.
    The one exception is about **this build**, not the payload -- if the
    engine's own declared schema version cannot be parsed there is no baseline
    to compare a minor against, and that is a broken install rather than a
    classification, so it is raised.
    """

    if value is None:
        return ReportSchemaSupport(
            status="missing",
            version=value,
            reason=(
                "the payload declares no `report_schema_version`, so it cannot "
                "be identified as an Agents Shipgate report"
            ),
        )

    parsed = parse_report_schema_version(value)
    if parsed is None:
        return ReportSchemaSupport(
            status="malformed",
            version=value,
            reason=(
                f"`report_schema_version` is {value!r}, which is not a "
                "MAJOR.MINOR version this engine emits"
            ),
        )

    major, minor = parsed
    if major < REPORT_CONTRACT_MAJOR:
        return ReportSchemaSupport(
            status="pre_freeze",
            version=value,
            reason=(
                f"report schema {value} predates the {FIRST_FROZEN_REPORT_SCHEMA_VERSION} "
                f"freeze (the pre-freeze line ended at "
                f"{LAST_PRE_FREEZE_REPORT_SCHEMA_VERSION}). Pre-freeze reports carried no "
                "compatibility promise, and reading one under the current model would "
                "fill blocks it never recorded with this build's defaults"
            ),
        )
    if major > REPORT_CONTRACT_MAJOR:
        return ReportSchemaSupport(
            status="future_major",
            version=value,
            reason=(
                f"report schema {value} belongs to a later contract major than this "
                f"engine's ({REPORT_CONTRACT_MAJOR}.x), so its fields may mean "
                "something this build does not implement"
            ),
        )

    current_text = current_report_schema_version()
    current = parse_report_schema_version(current_text)
    if current is None:
        # Not an assert: `python -O` strips those, and the stripped form
        # indexes `None` and raises `TypeError` out of the one function whose
        # whole job is to fail cleanly. Callers wrap
        # `ReportSchemaCompatibilityError`/`ValueError`, not `TypeError`.
        code = "report_schema_engine_version_unreadable"
        raise ReportSchemaCompatibilityError(
            f"[{code}] this build declares report schema {current_text!r}, which "
            "is not a MAJOR.MINOR version it can compare against. The install is "
            "broken; run `agents-shipgate doctor --json` and read the "
            "`environment` block.",
            reason_code=code,
            version=current_text,
        )
    if minor > current[1]:
        return ReportSchemaSupport(
            status="newer_than_engine",
            version=value,
            reason=(
                f"report schema {value} was written by a newer engine than this one "
                f"(which emits {current_text}). The 1.x additive rule "
                "keeps *your* parser working across minors; it does not let this build "
                "compare evidence recorded under a contract it does not have"
            ),
        )
    return ReportSchemaSupport(status="supported", version=value)


def _route(status: ReportSchemaStatus, regenerate_command: str) -> str:
    """The next step that actually resolves ``status``."""

    if status == "newer_than_engine":
        return (
            "Upgrade the CLI to at least the version that wrote it "
            "(`pipx upgrade agents-shipgate`), or regenerate the artifact with "
            f"this build: `{regenerate_command}`."
        )
    if status == "future_major":
        return (
            "This artifact is not convertible: a later contract major may have "
            "changed what its fields mean. Upgrade the CLI, or regenerate the "
            f"artifact from its own workspace with `{regenerate_command}`."
        )
    if status == "pre_freeze":
        return (
            "Regenerate it from the workspace it described, with "
            f"`{regenerate_command}`. There is no conversion: a pre-freeze report "
            "does not record what the current contract needs, and re-labelling one "
            "would assert evidence nothing measured. Re-verify anything derived "
            "from it (baselines with `agents-shipgate baseline save`, receipts by "
            "re-running the verification that produced them) against the "
            "regenerated report."
        )
    return f"Regenerate the artifact with `{regenerate_command}`."


def require_supported_report_schema(
    value: Any,
    *,
    subject: str = "report.json",
    regenerate_command: str = REGENERATE_REPORT_COMMAND,
    accept_newer_minor: bool = False,
) -> str:
    """Return ``value`` when this engine may interpret it, else refuse.

    ``subject`` names the artifact in the message, because the same refusal is
    raised about a ``--diff-from`` base, a packet input and a findings input,
    and "which file" is the first thing a reader needs.

    ``accept_newer_minor`` is for a reader that only *projects* the payload it
    was handed. Within the frozen major every later minor is additive, so a
    field such a reader has never heard of cannot change what it outputs -- and
    refusing the artifact would break the additive promise in exactly the
    direction it is supposed to hold. An evidence *comparison* must not set it.
    """

    support = classify_report_schema_version(value)
    if support.supported:
        return str(value)
    if accept_newer_minor and support.status == "newer_than_engine":
        return str(value)
    reason_code = f"report_schema_{support.status}"
    raise ReportSchemaCompatibilityError(
        f"{subject}: [{reason_code}] {support.reason}. "
        f"{_route(support.status, regenerate_command)}",
        reason_code=reason_code,
        version=value,
    )


#: The marker a refusal carries so a downstream classifier can recognise it.
#: Matched on the *bracket*, not on a list of known codes: an enumerated list
#: is a second copy of the producer's vocabulary, and the first version of this
#: function was exactly that -- it walked ``ReportSchemaStatus``, so the
#: engine-unreadable refusal below (whose code is not an input status) came out
#: unrecognised and would have routed an incomparable base to
#: ``review_required`` instead of withholding the verdict, in the one branch
#: that fires when the install itself is broken.
_REFUSAL_MARKER = re.compile(r"\[(report_schema_[a-z0-9_]+)\]")


def report_schema_refusal_code(text: str) -> str | None:
    """The reason code carried by one of this module's refusals, or ``None``.

    Every refusal this module raises embeds its own ``reason_code`` in brackets
    so that a downstream classifier can recognise it *structurally*.

    This exists because the previous classifier matched three substrings of the
    old prose (``"predates report schema"``, ``"semantic evidence"``,
    ``"not comparable with --diff-from"``). Rewording the message -- which the
    1.0 freeze did -- silently dropped an incomparable ``--diff-from`` base out
    of ``insufficient_evidence`` and into ``review_required``: the run stopped
    withholding a verdict it had no evidence for, and nothing failed except one
    unrelated-looking assertion. A code the producer emits and the consumer
    reads back cannot drift that way.
    """

    match = _REFUSAL_MARKER.search(text)
    return match.group(1) if match else None


__all__ = [
    "FIRST_FROZEN_REPORT_SCHEMA_VERSION",
    "LAST_PRE_FREEZE_REPORT_SCHEMA_VERSION",
    "REGENERATE_REPORT_COMMAND",
    "REPORT_CONTRACT_MAJOR",
    "ReportSchemaCompatibilityError",
    "ReportSchemaStatus",
    "ReportSchemaSupport",
    "classify_report_schema_version",
    "report_schema_refusal_code",
    "current_report_schema_version",
    "parse_report_schema_version",
    "require_supported_report_schema",
]
