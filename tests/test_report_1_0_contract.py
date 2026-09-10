"""The report 1.0 freeze, held against the runtime rather than against prose.

``docs/report-1-0-contract.md`` is the published claim: which report fields are
stable, which are provisional, what ``1.x`` may change, and what a pre-freeze
artifact may do. A document nothing checks is a document that drifts, and a
compatibility promise that drifts is worse than none -- a consumer acts on it.

Every test here binds one sentence of that document to something executable.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agents_shipgate.schemas.report import ReadinessReport
from agents_shipgate.schemas.report_compatibility import (
    FIRST_FROZEN_REPORT_SCHEMA_VERSION,
    LAST_PRE_FREEZE_REPORT_SCHEMA_VERSION,
    REPORT_CONTRACT_MAJOR,
    ReportSchemaCompatibilityError,
    classify_report_schema_version,
    current_report_schema_version,
    parse_report_schema_version,
    require_supported_report_schema,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS = REPO_ROOT / "docs"
CONTRACT_DOC = DOCS / "report-1-0-contract.md"

CURRENT = current_report_schema_version()
CURRENT_SCHEMA_PATH = DOCS / f"report-schema.v{CURRENT}.json"
PRE_FREEZE_SCHEMA_PATH = DOCS / f"report-schema.v{LAST_PRE_FREEZE_REPORT_SCHEMA_VERSION}.json"


# --------------------------------------------------------------------------
# The freeze itself
# --------------------------------------------------------------------------


def test_the_engine_emits_the_frozen_major() -> None:
    parsed = parse_report_schema_version(CURRENT)
    assert parsed is not None, f"the engine's own version {CURRENT!r} must parse"
    assert parsed[0] == REPORT_CONTRACT_MAJOR, (
        f"the engine emits report schema {CURRENT}, which is not in the frozen "
        f"{REPORT_CONTRACT_MAJOR}.x major. Freezing means the emitted major and "
        "the declared one are the same number."
    )


def test_the_current_schema_document_is_published() -> None:
    assert CURRENT_SCHEMA_PATH.is_file(), (
        f"the engine emits report schema {CURRENT} but {CURRENT_SCHEMA_PATH.name} "
        "is not committed. Run `python scripts/generate_schemas.py`."
    )
    schema = json.loads(CURRENT_SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["properties"]["report_schema_version"]["const"] == CURRENT
    assert schema["$id"].endswith(f"report-schema.v{CURRENT}.json")


def test_the_1_0_schema_is_a_promotion_of_the_last_pre_freeze_schema() -> None:
    """`1.0` renumbers `0.43`; it does not reshape it.

    This is the whole argument for why a *major* bump is not a break here.
    ``STABILITY.md``'s own rule reads "major on breaking", so the claim that
    ``0.43 -> 1.0`` broke nothing has to be a fact somebody can check, not a
    sentence in a migration note.

    It compares the two *frozen documents* rather than "whatever is current",
    so it keeps running after `1.1` lands. An earlier form skipped once a later
    minor became current -- which retired the only byte comparison guarding
    `report-schema.v1.0.json` at exactly the moment that file became a frozen
    published artifact nothing else pinned.
    """

    frozen_1_0 = DOCS / f"report-schema.v{FIRST_FROZEN_REPORT_SCHEMA_VERSION}.json"
    assert frozen_1_0.is_file(), (
        f"{frozen_1_0.name} is the published freeze and may not be deleted"
    )

    def _comparable(path: Path) -> dict:
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.pop("$id", None)
        payload.pop("title", None)
        payload["properties"]["report_schema_version"].pop("const", None)
        return payload

    assert _comparable(frozen_1_0) == _comparable(PRE_FREEZE_SCHEMA_PATH), (
        f"report-schema.v{FIRST_FROZEN_REPORT_SCHEMA_VERSION}.json differs from "
        f"report-schema.v{LAST_PRE_FREEZE_REPORT_SCHEMA_VERSION}.json in more "
        "than $id/title/version. The freeze is published as a promotion of the "
        "pre-freeze shape, and both documents are published artifacts consumers "
        "validate against: a shape change ships as its own minor with its own "
        "migration note, never as an edit to either of these files."
    )


def test_the_last_pre_freeze_schema_stays_published() -> None:
    """Every published schema URL keeps its bytes forever (rule 3)."""

    assert PRE_FREEZE_SCHEMA_PATH.is_file(), (
        f"{PRE_FREEZE_SCHEMA_PATH.name} is the last pre-freeze schema and is "
        "referenced as a frozen compatibility reference; it may not be deleted."
    )


# --------------------------------------------------------------------------
# The stable/provisional inventory
# --------------------------------------------------------------------------

_ROW = re.compile(
    r"^\|\s*`(?P<field>[a-z_]+)`\s*\|\s*(?P<presence>required|optional)\s*\|"
    r"\s*(?P<stability>stable|provisional)\s*\|"
)


def _inventory() -> dict[str, tuple[str, str]]:
    """Parse the report top-level table out of the published contract doc."""

    rows: dict[str, tuple[str, str]] = {}
    for line in CONTRACT_DOC.read_text(encoding="utf-8").splitlines():
        match = _ROW.match(line)
        if match is None:
            continue
        field = match.group("field")
        assert field not in rows, f"{field} is listed twice in the inventory"
        rows[field] = (match.group("presence"), match.group("stability"))
    return rows


def test_the_inventory_covers_every_top_level_field() -> None:
    rows = _inventory()
    model = set(ReadinessReport.model_fields)
    missing = sorted(model - set(rows))
    unknown = sorted(set(rows) - model)
    assert not missing, (
        "these report fields are not in the stable/provisional inventory in "
        f"docs/{CONTRACT_DOC.name}. A field nobody classified is a field a "
        "consumer cannot tell is safe to depend on:\n  - " + "\n  - ".join(missing)
    )
    assert not unknown, (
        f"docs/{CONTRACT_DOC.name} classifies fields the report does not have "
        "(renamed or removed without updating the inventory):\n  - "
        + "\n  - ".join(unknown)
    )


def test_the_inventory_presence_column_matches_the_published_schema() -> None:
    """`required` in the doc means `required` in the artifact, or it means nothing."""

    schema = json.loads(CURRENT_SCHEMA_PATH.read_text(encoding="utf-8"))
    schema_required = set(schema["required"])
    documented_required = {field for field, (presence, _) in _inventory().items() if presence == "required"}

    overstated = sorted(documented_required - schema_required)
    understated = sorted(schema_required - documented_required)
    assert not overstated, (
        f"docs/{CONTRACT_DOC.name} promises these fields are present in every "
        f"report, but report-schema.v{CURRENT}.json does not require them:\n  - "
        + "\n  - ".join(overstated)
    )
    assert not understated, (
        f"report-schema.v{CURRENT}.json requires these fields, but "
        f"docs/{CONTRACT_DOC.name} marks them optional:\n  - "
        + "\n  - ".join(understated)
    )


def _stability_promised_by_stability_md() -> set[str]:
    """Top-level field names STABILITY.md's stable list already promises."""

    text = (REPO_ROOT / "STABILITY.md").read_text(encoding="utf-8")
    start = text.index("### JSON report fields (stable)")
    end = text.index("\n### ", start + 10)
    names = set()
    for line in text[start:end].splitlines():
        match = re.match(r"- `([A-Za-z_][A-Za-z0-9_]*)", line)
        if match:
            names.add(match.group(1))
    return names & set(ReadinessReport.model_fields)


def test_nothing_stability_md_already_promised_is_marked_provisional() -> None:
    """The freeze may not quietly demote a field 0.x already promised.

    A deprecation cycle is counted in shipped releases. Marking an
    already-promised field provisional in the same commit that freezes the
    schema is a removal with no cycle at all, dressed as an inventory entry.
    """

    rows = _inventory()
    promised = _stability_promised_by_stability_md()
    assert promised, "failed to parse STABILITY.md's stable field list"
    demoted = sorted(
        field for field in promised if rows.get(field, ("", ""))[1] == "provisional"
    )
    assert not demoted, (
        "STABILITY.md's 'JSON report fields (stable)' section already promises "
        f"these, but docs/{CONTRACT_DOC.name} marks them provisional. Demoting "
        "a shipped promise needs a deprecation cycle, not an inventory edit:\n  - "
        + "\n  - ".join(demoted)
    )


def test_a_provisional_field_is_never_a_release_gate_input() -> None:
    """`release_decision` is the gate, and the gate is stable.

    Stated as a test because the inventory's own rule -- "a provisional block
    is never a gate input" -- is only meaningful if the gate itself cannot be
    provisional.
    """

    rows = _inventory()
    assert rows["release_decision"] == ("required", "stable")
    assert rows["findings"][1] == "stable"
    assert rows["report_schema_version"] == ("required", "stable")


# --------------------------------------------------------------------------
# The compatibility boundary
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1.0", "supported"),
        (CURRENT, "supported"),
        ("0.43", "pre_freeze"),
        ("0.1", "pre_freeze"),
        ("0.9", "pre_freeze"),
        ("2.0", "future_major"),
        ("1.999", "newer_than_engine"),
        ("1.0.0", "malformed"),
        ("1", "malformed"),
        ("01.0", "malformed"),
        (" 1.0", "malformed"),
        ("1.0 ", "malformed"),
        ("v1.0", "malformed"),
        ("1.-1", "malformed"),
        ("", "malformed"),
        (1.0, "malformed"),
        (None, "missing"),
    ],
)
def test_version_classification(value: object, expected: str) -> None:
    assert classify_report_schema_version(value).status == expected


def test_a_refusal_names_the_artifact_the_cause_and_a_route() -> None:
    """A refusal a reader cannot act on is a dead end, not a boundary."""

    with pytest.raises(ReportSchemaCompatibilityError) as excinfo:
        require_supported_report_schema("0.28", subject="base report.json")
    message = str(excinfo.value)
    assert excinfo.value.reason_code == "report_schema_pre_freeze"
    assert "base report.json" in message
    assert "0.28" in message
    assert "agents-shipgate scan" in message, "the refusal must name a command"
    # No conversion is offered, at any boundary.
    assert "conversion" in message.lower()


def test_a_newer_minor_is_refused_with_an_upgrade_route() -> None:
    with pytest.raises(ReportSchemaCompatibilityError) as excinfo:
        require_supported_report_schema("1.999")
    assert excinfo.value.reason_code == "report_schema_newer_than_engine"
    assert "upgrade" in str(excinfo.value).lower()


def test_every_refusal_carries_a_code_the_classifier_can_look_up() -> None:
    """The producer emits the code; the consumer looks it up. No prose match.

    The release decision routes an incomparable ``--diff-from`` base to
    ``insufficient_evidence`` only when its ``source_warning`` gap carries
    ``next_action.kind == "provide_source"``. That classification used to be
    three substrings of one refusal's wording, so rewording the refusal
    downgraded the verdict to ``review_required`` -- silently, because nothing
    that named the decision failed.
    """

    from agents_shipgate.schemas.report_compatibility import report_schema_refusal_code

    for version, expected in (
        ("0.28", "report_schema_pre_freeze"),
        ("2.0", "report_schema_future_major"),
        ("1.999", "report_schema_newer_than_engine"),
        ("nonsense", "report_schema_malformed"),
        (None, "report_schema_missing"),
    ):
        with pytest.raises(ReportSchemaCompatibilityError) as excinfo:
            require_supported_report_schema(version)
        assert excinfo.value.reason_code == expected
        assert report_schema_refusal_code(str(excinfo.value)) == expected, (
            "a refusal the classifier cannot recognise routes an incomparable "
            "base to review_required instead of withholding the verdict"
        )

    # And ordinary text is not mistaken for one.
    assert report_schema_refusal_code("tools.json could not be read") is None


def test_a_projection_reader_may_accept_a_newer_minor_and_a_comparison_may_not() -> None:
    """The additive promise holds forward for projections, not for comparisons."""

    newer = "1.999"
    assert (
        require_supported_report_schema(newer, accept_newer_minor=True) == newer
    )
    with pytest.raises(ReportSchemaCompatibilityError):
        require_supported_report_schema(newer)
    # The relaxation is scoped to that one status; it never admits a pre-freeze
    # report, whose absent blocks a projection would render as this build's
    # defaults.
    for refused in ("0.43", "2.0", "nope"):
        with pytest.raises(ReportSchemaCompatibilityError):
            require_supported_report_schema(refused, accept_newer_minor=True)


def test_the_engines_own_output_passes_its_own_boundary() -> None:
    """A gate the engine's own artifact fails rejects everything (#416's lesson)."""

    assert require_supported_report_schema(CURRENT) == CURRENT
