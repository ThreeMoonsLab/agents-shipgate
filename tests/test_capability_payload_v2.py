"""``shipgate.capability_payload/v2``: v1 plus each tool's definition (#1117).

Capability standard 0.6 put a tool's description, output schema and annotations
into the capability lock. ``v1`` is closed, so it could not carry them, and a
rewritten tool description was not a row in the exported delta. ``v2``
publishes the three digests; ``v1`` is unchanged and still emitted.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError

from agents_shipgate.core.capability_attestation import project_capability_delta_attestation
from agents_shipgate.core.capability_payload import (
    PUBLISHED_FACT_FIELDS_V2,
    UNPUBLISHED_FACT_FIELDS_V2,
    project_capability_delta,
    project_capability_record,
    project_capability_state,
)
from agents_shipgate.schemas.capabilities import CapabilityFactV1
from agents_shipgate.schemas.capability_attestation import (
    CAPABILITY_DELTA_ATTESTATION_ARTIFACT_KEY,
    CAPABILITY_DELTA_ATTESTATION_FILENAME,
    attestation_json,
    render_attestation_json,
)
from agents_shipgate.schemas.capability_attestation_v2 import (
    CAPABILITY_DELTA_ATTESTATION_V2_ARTIFACT_KEY,
    CAPABILITY_DELTA_ATTESTATION_V2_FILENAME,
    CAPABILITY_DELTA_PREDICATE_TYPE_V2,
    CapabilityDeltaAttestationV2,
)
from agents_shipgate.schemas.capability_payload import (
    CAPABILITY_DIGEST_DIMENSIONS,
    CapabilityPayloadV1,
)
from agents_shipgate.schemas.capability_payload_v2 import (
    CAPABILITY_DIGEST_DIMENSIONS_V2,
    DEFINITION_DIGEST_DIMENSIONS,
    CapabilityDigestsV2,
    CapabilityPayloadV2,
)
from agents_shipgate.schemas.contract import build_contract_payload
from tests.test_capability_definition import POISON, _variant
from tests.test_capability_delta_attestation import (  # noqa: F401 - autouse fixture
    _neutral_cli_environment,
    _run_reference_verifier,
    _verify_refund_pr,
)
from tests.test_capability_lock import _lock
from tests.test_capability_payload import _model_field_paths

REPO_ROOT = Path(__file__).resolve().parents[1]
PAYLOAD_SCHEMA_V2 = json.loads(
    (REPO_ROOT / "docs/capability-payload-schema.v2.json").read_text(encoding="utf-8")
)
ATTESTATION_SCHEMA_V2 = REPO_ROOT / "docs/capability-delta-attestation-schema.v2.json"
BASE_TREE = "a" * 40
HEAD_TREE = "b" * 40


def _facts(tool) -> list[CapabilityFactV1]:
    return list(_lock([tool]).capabilities)


def _pair(**head_updates: object) -> tuple[list[CapabilityFactV1], list[CapabilityFactV1]]:
    return _facts(_variant()), _facts(_variant(**head_updates))


def _round_trip_v2(payload) -> None:
    dumped = json.loads(payload.model_dump_json())
    CapabilityPayloadV2.model_validate(dumped)
    jsonschema.validate(dumped, PAYLOAD_SCHEMA_V2)


@pytest.mark.parametrize(
    ("update", "dimensions"),
    [
        ({"description": POISON}, ("description_hash",)),
        (
            {"output_schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}}},
            ("output_schema_hash",),
        ),
        ({"annotations": {"title": "Case lookup"}}, ("annotations_hash",)),
        (
            {"description": POISON, "annotations": {"title": "Case lookup"}},
            ("annotations_hash", "description_hash"),
        ),
    ],
)
def test_a_definition_only_change_is_a_v2_row_and_not_a_v1_row(update, dimensions) -> None:
    base, head = _pair(**update)

    v1 = project_capability_delta(base, head)
    v2 = project_capability_delta(base, head, version=2)

    # v1 is frozen and cannot see it; it still validates as an empty delta.
    assert v1.subjects == ()
    CapabilityPayloadV1.model_validate(json.loads(v1.model_dump_json()))
    (subject,) = v2.subjects
    assert subject.transition == "modified"
    (entry,) = subject.changes
    assert entry.transition == "changed"
    assert entry.changed_dimensions == dimensions
    # The payload sees that the text moved, not which way the capability did.
    assert entry.semantic_direction == "unknown"
    assert entry.semantic_changes == ()
    assert v2.summary.capability_changes == 1
    _round_trip_v2(v2)


def test_a_semantic_change_and_a_definition_change_are_one_row() -> None:
    base, head = _pair(
        description=POISON,
        input_schema={"type": "object", "properties": {"case_id": {"type": "integer"}}},
    )

    v1 = project_capability_delta(base, head)
    v2 = project_capability_delta(base, head, version=2)

    (v1_entry,) = v1.subjects[0].changes
    (v2_entry,) = v2.subjects[0].changes
    assert "description_hash" not in v1_entry.changed_dimensions
    assert v2_entry.changed_dimensions == tuple(
        sorted((*v1_entry.changed_dimensions, "description_hash"))
    )
    assert v2_entry.semantic_direction == v1_entry.semantic_direction
    assert v2_entry.semantic_changes == v1_entry.semantic_changes
    _round_trip_v2(v2)


def test_v1_output_is_unchanged_and_v2_adds_only_the_three_digests() -> None:
    facts = _facts(_variant())

    v1 = project_capability_state(facts)
    v2 = project_capability_state(facts, version=2)

    v1_record = json.loads(v1.model_dump_json())["subjects"][0]["capabilities"][0]
    v2_record = json.loads(v2.model_dump_json())["subjects"][0]["capabilities"][0]
    assert set(v1_record["digests"]) == set(CAPABILITY_DIGEST_DIMENSIONS)
    assert set(v2_record["digests"]) == set(CAPABILITY_DIGEST_DIMENSIONS_V2)
    assert set(v2_record["digests"]) - set(v1_record["digests"]) == set(
        DEFINITION_DIGEST_DIMENSIONS
    )
    for record in (v1_record, v2_record):
        record.pop("digests")
    assert v1_record == v2_record
    definition = facts[0].definition
    assert definition is not None
    assert v2.subjects[0].capabilities[0].digests.description_hash == definition.description_hash
    _round_trip_v2(v2)


def test_the_definition_is_inside_the_capability_set_digest() -> None:
    """Two states that differ only in a description share a v1 ref, not a v2 one."""

    base, head = _pair(description=POISON)

    assert (
        project_capability_state(base).state.capability_set_digest
        == project_capability_state(head).state.capability_set_digest
    )
    assert (
        project_capability_state(base, version=2).state.capability_set_digest
        != project_capability_state(head, version=2).state.capability_set_digest
    )
    assert (
        project_capability_state(base, version=2).state.evidence_set_digest
        == project_capability_state(head, version=2).state.evidence_set_digest
    )


def test_a_fact_without_a_definition_publishes_three_nulls() -> None:
    (fact,) = _facts(_variant())
    bare = fact.model_copy(update={"definition": None})

    record = project_capability_record(bare, version=2)

    assert all(getattr(record.digests, name) is None for name in DEFINITION_DIGEST_DIMENSIONS)
    _round_trip_v2(project_capability_state([bare], version=2))


def test_a_side_that_states_a_definition_and_one_that_does_not_still_differ() -> None:
    """The fact diff skips such a pair; the published records do not agree, so v2 rows it."""

    (fact,) = _facts(_variant())
    bare = fact.model_copy(update={"definition": None})

    v2 = project_capability_delta([bare], [fact], version=2)

    (entry,) = v2.subjects[0].changes
    assert entry.changed_dimensions == DEFINITION_DIGEST_DIMENSIONS
    _round_trip_v2(v2)


def test_a_definition_is_stated_whole_or_not_at_all() -> None:
    record = project_capability_record(_facts(_variant())[0], version=2)
    digests = record.digests.model_dump()
    digests["output_schema_hash"] = None

    with pytest.raises(ValidationError, match="null together"):
        CapabilityDigestsV2.model_validate(digests)


def test_a_v2_delta_that_hides_a_moved_definition_is_rejected() -> None:
    base, head = _pair(description=POISON)
    dumped = json.loads(project_capability_delta(base, head, version=2).model_dump_json())
    dumped["subjects"][0]["changes"][0]["changed_dimensions"] = []

    with pytest.raises(ValidationError, match="changed_dimensions"):
        CapabilityPayloadV2.model_validate(dumped)


def test_the_two_versions_do_not_read_each_other() -> None:
    base, head = _pair(description=POISON)
    v1 = json.loads(project_capability_delta(base, head).model_dump_json())
    v2 = json.loads(project_capability_delta(base, head, version=2).model_dump_json())

    with pytest.raises(ValidationError):
        CapabilityPayloadV1.model_validate(v2)
    with pytest.raises(ValidationError):
        CapabilityPayloadV2.model_validate(v1)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(v1, PAYLOAD_SCHEMA_V2)


def test_every_internal_fact_field_is_published_or_excluded_in_v2() -> None:
    internal = _model_field_paths(CapabilityFactV1, stop=set(UNPUBLISHED_FACT_FIELDS_V2))
    declared = set(PUBLISHED_FACT_FIELDS_V2) | set(UNPUBLISHED_FACT_FIELDS_V2)

    assert internal == declared, (
        f"internal but undeclared: {sorted(internal - declared)}; "
        f"declared but not internal: {sorted(declared - internal)}"
    )
    assert "definition" not in UNPUBLISHED_FACT_FIELDS_V2


def test_the_v2_attestation_round_trips_through_the_reference_verifier(tmp_path: Path) -> None:
    base, head = _pair(description=POISON)
    attestation = project_capability_delta_attestation(
        base,
        head,
        subject_name="acme/agent",
        base_tree_sha=BASE_TREE,
        head_tree_sha=HEAD_TREE,
        head_commit_sha=None,
        version=2,
    )
    assert isinstance(attestation, CapabilityDeltaAttestationV2)
    path = tmp_path / CAPABILITY_DELTA_ATTESTATION_V2_FILENAME
    path.write_text(render_attestation_json(attestation), encoding="utf-8")

    accepted = _run_reference_verifier(path, "--schema", str(ATTESTATION_SCHEMA_V2), "--json")
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert json.loads(accepted.stdout)["valid"] is True

    document = attestation_json(attestation)
    document["predicate"]["delta"]["subjects"][0]["changes"][0]["changed_dimensions"] = []
    path.write_text(json.dumps(document), encoding="utf-8")
    rejected = _run_reference_verifier(path, "--json")
    assert rejected.returncode == 1
    assert "P5" in rejected.stdout

    # A v2 body under the v1 predicate type is refused by name.
    document = attestation_json(attestation)
    document["predicateType"] = "https://threemoonslab.com/agents-shipgate/capability-delta/v1"
    path.write_text(json.dumps(document), encoding="utf-8")
    assert _run_reference_verifier(path).returncode == 1


def test_the_contract_and_well_known_name_both_versions() -> None:
    payload = build_contract_payload()
    well_known = json.loads(
        (REPO_ROOT / ".well-known/agents-shipgate.json").read_text(encoding="utf-8")
    )

    assert payload.capability_delta_predicate_type_v2 == CAPABILITY_DELTA_PREDICATE_TYPE_V2
    assert payload.capability_delta_attestation_v2_artifact.endswith(
        CAPABILITY_DELTA_ATTESTATION_V2_FILENAME
    )
    for field in (
        "capability_payload_v2_schema_version",
        "capability_payload_v2_schema_path",
        "capability_delta_attestation_v2_schema_version",
        "capability_delta_attestation_v2_schema_path",
        "capability_delta_predicate_type_v2",
        "capability_delta_attestation_v2_artifact",
    ):
        assert well_known[field] == getattr(payload, field), field
    for path in (
        payload.capability_payload_v2_schema_path,
        payload.capability_delta_attestation_v2_schema_path,
    ):
        assert (REPO_ROOT / path).is_file(), path


def test_verify_writes_both_versions_from_one_run(tmp_path: Path) -> None:
    """One verify run: both files, both bound, both verified against the receipt."""

    from agents_shipgate.cli.authorization import _AUTHORIZATION_RECEIPT_ARTIFACTS

    out = _verify_refund_pr(tmp_path)
    v1_path = out / CAPABILITY_DELTA_ATTESTATION_FILENAME
    v2_path = out / CAPABILITY_DELTA_ATTESTATION_V2_FILENAME
    assert v1_path.is_file() and v2_path.is_file()
    v1 = json.loads(v1_path.read_text(encoding="utf-8"))
    v2 = json.loads(v2_path.read_text(encoding="utf-8"))

    assert v2["predicateType"] == CAPABILITY_DELTA_PREDICATE_TYPE_V2
    assert v2["subject"] == v1["subject"]
    assert v2["predicate"]["verification"] == v1["predicate"]["verification"]
    # The refund fixture adds a tool and changes no definition in place, so the
    # two versions agree on every row and count; only the digests widen.
    assert v2["predicate"]["delta"]["summary"] == v1["predicate"]["delta"]["summary"]
    assert [row["subject"] for row in v2["predicate"]["delta"]["subjects"]] == [
        row["subject"] for row in v1["predicate"]["delta"]["subjects"]
    ]

    verifier = json.loads((out / "verifier.json").read_text(encoding="utf-8"))
    assert verifier["artifacts"][CAPABILITY_DELTA_ATTESTATION_ARTIFACT_KEY].endswith(
        CAPABILITY_DELTA_ATTESTATION_FILENAME
    )
    assert verifier["artifacts"][CAPABILITY_DELTA_ATTESTATION_V2_ARTIFACT_KEY].endswith(
        CAPABILITY_DELTA_ATTESTATION_V2_FILENAME
    )
    # An authorized push over a receipt that binds the v2 file is not refused.
    assert CAPABILITY_DELTA_ATTESTATION_V2_ARTIFACT_KEY in _AUTHORIZATION_RECEIPT_ARTIFACTS

    receipt = out / "verification-receipt.json"
    for path, schema in (
        (v1_path, REPO_ROOT / "docs/capability-delta-attestation-schema.v1.json"),
        (v2_path, ATTESTATION_SCHEMA_V2),
    ):
        result = _run_reference_verifier(
            path, "--receipt", str(receipt), "--schema", str(schema), "--json"
        )
        assert result.returncode == 0, path.name + result.stdout + result.stderr
