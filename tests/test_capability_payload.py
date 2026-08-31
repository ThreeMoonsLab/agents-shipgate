"""``shipgate.capability_payload/v1`` — the frozen shared capability payload.

The payload exists so the exported delta attestation (#470) and the committed
capability state (#474) serialize one structure instead of two. These tests
hold the four properties that claim is made of:

* **Round trip.** For every shipped sample, internal facts project into the
  payload, serialize, parse back, and still carry the same values — checked
  field by field through the published-field map rather than by spot check.
* **The schema is load-bearing, not decorative.** The published field set is
  closed and declared; an internal field that is not in it does not appear in
  the output, and a new internal field cannot be added without deciding, in
  writing, whether it is published.
* **One subject, one row.** Duplicate subjects, disagreeing summaries, and
  disagreeing subject rollups are rejected rather than repaired — so the
  ``+2``-for-one-tool shape cannot be stated in this schema at all.
* **Determinism.** Two builds of the same static inputs are byte-identical.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import types
import typing
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from pydantic import BaseModel, ValidationError

from agents_shipgate.cli.capability import build_capability_lock_from_config
from agents_shipgate.core.capability_lattice import (
    PERMISSION_CLASS_RANK as LATTICE_PERMISSION_RANK,
)
from agents_shipgate.core.capability_lattice import (
    PermissionClass,
    classify_tool_permission,
)
from agents_shipgate.core.capability_payload import (
    PUBLISHED_FACT_FIELDS,
    UNPUBLISHED_FACT_FIELDS,
    UNPUBLISHED_LOCK_FIELDS,
    CapabilityPayloadError,
    _merge_subject_refs,
    _permission_shift,  # the identity guard has no public caller to reach it through
    project_capability_delta,
    project_capability_record,
    project_capability_state,
)
from agents_shipgate.core.domain import Tool
from agents_shipgate.schemas.capabilities import (
    CapabilityFactV1,
    CapabilityLockFileV1,
)
from agents_shipgate.schemas.capability_payload import (
    CAPABILITY_PAYLOAD_SCHEMA_PATH,
    CAPABILITY_PAYLOAD_SCHEMA_VERSION,
    CapabilityAnalysisCoverage,
    CapabilityDeltaPayloadV1,
    CapabilityDeltaSubject,
    CapabilityDeltaSummary,
    CapabilityPayloadV1,
    CapabilityPermissionClass,
    CapabilityPermissionFacts,
    CapabilityRecord,
    CapabilityRecordTransitionEntry,
    CapabilityStatePayloadV1,
    CapabilityStateSubject,
    canonical_payload_json,
    changed_record_dimensions,
    payload_digest,
    subject_key,
)
from agents_shipgate.schemas.capability_payload import (
    PERMISSION_CLASS_RANK as PAYLOAD_PERMISSION_RANK,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLES = REPO_ROOT / "samples"
REFUND_SAMPLE = SAMPLES / "ai_generated_refund_pr"
PAYLOAD_SCHEMA = json.loads(
    (REPO_ROOT / CAPABILITY_PAYLOAD_SCHEMA_PATH).read_text(encoding="utf-8")
)
STATE_EXAMPLE = REPO_ROOT / "docs/examples/capability-payload.v1.state.example.json"
DELTA_EXAMPLE = REPO_ROOT / "docs/examples/capability-payload.v1.delta.example.json"

SAMPLE_CONFIGS = sorted(SAMPLES.glob("*/shipgate.yaml"))


def _facts(config: Path) -> list[CapabilityFactV1]:
    lock = build_capability_lock_from_config(
        config=config,
        no_plugins=True,
        verbose=False,
    )
    return lock.capabilities


@pytest.fixture(scope="module")
def refund_facts(tmp_path_factory) -> tuple[list[CapabilityFactV1], list[CapabilityFactV1]]:
    """Base and head capability facts of the shipped refund-PR sample.

    The sample keeps its head tool surface under ``_head/`` because the fixture
    runner materializes a two-commit history from it; reproduce that with a
    copy rather than a git checkout, so this stays a static-input test.
    """

    base = _facts(REFUND_SAMPLE / "shipgate.yaml")
    head_root = tmp_path_factory.mktemp("refund_head") / "head"
    shutil.copytree(REFUND_SAMPLE, head_root)
    shutil.copyfile(head_root / "_head" / "tools.json", head_root / "tools.json")
    return base, _facts(head_root / "shipgate.yaml")


def _added_entry(record) -> CapabilityRecordTransitionEntry:
    return CapabilityRecordTransitionEntry(
        transition="added",
        changed_dimensions=(),
        semantic_direction="added",
        semantic_changes=(),
        before=None,
        after=record,
    )


def _removed_entry(record) -> CapabilityRecordTransitionEntry:
    return CapabilityRecordTransitionEntry(
        transition="removed",
        changed_dimensions=(),
        semantic_direction="removed",
        semantic_changes=(),
        before=record,
        after=None,
    )


def _paired_entry_for(
    before,
    after,
    *,
    transition: str,
    direction: str = "unknown",
) -> CapabilityRecordTransitionEntry:
    return CapabilityRecordTransitionEntry(
        transition=transition,
        changed_dimensions=changed_record_dimensions(before, after),
        semantic_direction=direction,
        semantic_changes=(),
        before=before,
        after=after,
    )


# --- Round trip --------------------------------------------------------------


def _resolve(root: Any, path: str) -> Any:
    current = root
    for part in path.split("."):
        current = getattr(current, part)
    return current


@pytest.mark.parametrize("config", SAMPLE_CONFIGS, ids=lambda p: p.parent.name)
def test_state_payload_round_trips_on_shipped_samples(config: Path) -> None:
    """internal facts -> payload -> JSON -> parse -> the same published values.

    The comparison walks ``PUBLISHED_FACT_FIELDS``, so it covers every field the
    payload claims to publish rather than the handful a hand-written assertion
    would name.
    """

    facts = _facts(config)
    payload = project_capability_state(facts, ref=config.parent.name)

    reparsed = CapabilityStatePayloadV1.model_validate_json(payload.model_dump_json())
    assert reparsed == payload, "state payload did not survive a JSON round trip"

    jsonschema.validate(payload.model_dump(mode="json"), PAYLOAD_SCHEMA)

    facts_by_id = {fact.id: fact for fact in facts}
    published_ids = {
        record.capability_id
        for subject in reparsed.subjects
        for record in subject.capabilities
    }
    assert published_ids == set(facts_by_id), (
        "the state payload must carry every capability fact exactly once"
    )

    for subject in reparsed.subjects:
        names = {facts_by_id[record.capability_id].identity.tool_name
                 for record in subject.capabilities}
        assert subject.subject.name == min(names)
        for record in subject.capabilities:
            fact = facts_by_id[record.capability_id]
            for internal_path, payload_path in PUBLISHED_FACT_FIELDS.items():
                expected = _resolve(fact, internal_path)
                if payload_path.startswith("capabilities[]."):
                    actual = _resolve(record, payload_path[len("capabilities[]."):])
                elif payload_path == "subject.name":
                    # Display strings can differ across a group; identity cannot.
                    continue
                else:
                    actual = _resolve(subject, payload_path)
                assert actual == expected, (
                    f"{config.parent.name}: {internal_path} -> {payload_path} "
                    f"published {actual!r}, fact holds {expected!r}"
                )


def test_delta_payload_round_trips_and_validates(refund_facts) -> None:
    base, head = refund_facts
    payload = project_capability_delta(base, head, base_ref="base", head_ref="head")

    reparsed = CapabilityDeltaPayloadV1.model_validate_json(payload.model_dump_json())
    assert reparsed == payload
    jsonschema.validate(payload.model_dump(mode="json"), PAYLOAD_SCHEMA)

    # The union root accepts either view through the same schema file.
    assert isinstance(
        CapabilityPayloadV1.model_validate(payload.model_dump(mode="json")).root,
        CapabilityDeltaPayloadV1,
    )


def test_delta_state_refs_match_the_state_payloads(refund_facts) -> None:
    """A delta and a state payload must be provably about the same state."""

    base, head = refund_facts
    delta = project_capability_delta(base, head)
    assert delta.base == project_capability_state(base).state
    assert delta.head == project_capability_state(head).state


def test_added_tool_is_one_subject_in_the_delta(refund_facts) -> None:
    """The sample's whole PR is one added tool; it must read as one subject."""

    base, head = refund_facts
    delta = project_capability_delta(base, head)
    added = [entry for entry in delta.subjects if entry.transition == "added"]
    assert [entry.subject.name for entry in added] == ["stripe.create_refund"]
    assert delta.summary.added_subjects == 1
    assert delta.summary.subjects == len(delta.subjects)


# --- The schema is load-bearing ----------------------------------------------


def _model_field_paths(model: type[BaseModel], *, stop: set[str], prefix: str = "") -> set[str]:
    """Dotted leaf paths of a Pydantic model, not descending into ``stop``."""

    paths: set[str] = set()
    for name, field in model.model_fields.items():
        path = f"{prefix}{name}"
        if path in stop:
            paths.add(path)
            continue
        nested = _nested_model(field.annotation)
        if nested is not None:
            paths |= _model_field_paths(nested, stop=stop, prefix=f"{path}.")
        else:
            paths.add(path)
    return paths


def _nested_model(annotation: Any) -> type[BaseModel] | None:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        models = [
            arg
            for arg in typing.get_args(annotation)
            if isinstance(arg, type) and issubclass(arg, BaseModel)
        ]
        if len(models) == 1:
            return models[0]
    return None


def test_every_internal_fact_field_is_published_or_excluded_with_a_reason() -> None:
    """The frozen-union guard (#323): a new field forces a written decision.

    Without this, adding a field to ``CapabilityFactV1`` silently drops it from
    both public surfaces — the schema would be documentation rather than a
    contract.
    """

    internal = _model_field_paths(
        CapabilityFactV1,
        stop=set(UNPUBLISHED_FACT_FIELDS),
    )
    declared = set(PUBLISHED_FACT_FIELDS) | set(UNPUBLISHED_FACT_FIELDS)
    assert internal == declared, (
        "CapabilityFactV1 and the capability payload have drifted.\n"
        f"  internal but undeclared: {sorted(internal - declared)}\n"
        f"  declared but not internal: {sorted(declared - internal)}\n"
        "Add each new field to PUBLISHED_FACT_FIELDS (and to the payload "
        "models) or to UNPUBLISHED_FACT_FIELDS with the reason it stays "
        "internal."
    )
    assert not (set(PUBLISHED_FACT_FIELDS) & set(UNPUBLISHED_FACT_FIELDS))


def _internal_annotation(path: str) -> Any:
    model: Any = CapabilityFactV1
    parts = path.split(".")
    for index, part in enumerate(parts):
        field = model.model_fields[part]
        if index == len(parts) - 1:
            return field.annotation
        model = field.annotation


def _payload_annotation(path: str) -> Any:
    if path.startswith("capabilities[]."):
        model: Any = CapabilityRecord
        rest = path[len("capabilities[]."):]
    else:
        model = CapabilityStateSubject
        rest = path
    parts = rest.split(".")
    for index, part in enumerate(parts):
        field = model.model_fields[part]
        if index == len(parts) - 1:
            return field.annotation
        model = field.annotation


def test_every_published_field_keeps_the_internal_type() -> None:
    """Names are not enough: a widened internal type must be a schema decision.

    The frozen-union guard covers field *names*. Without this, adding a value to
    an internal Literal — a new ``reversibility`` state, a new effect — leaves
    CI green and turns into a `ValidationError` raised from inside the
    projection on the first repository that produces it.
    """

    mismatched = [
        (internal_path, payload_path)
        for internal_path, payload_path in PUBLISHED_FACT_FIELDS.items()
        if _internal_annotation(internal_path) != _payload_annotation(payload_path)
    ]
    assert not mismatched, (
        "the payload re-declares these fields with a different type than the "
        f"capability fact: {mismatched}. Either mirror the internal type or "
        "publish a new payload schema version."
    )


def test_the_payload_permission_vocabulary_matches_the_lattice() -> None:
    """The one closed vocabulary the payload re-spells rather than imports.

    ``schemas`` cannot import ``core`` without inverting the layering, so the
    Literal is duplicated. Pin the duplicate: a class added to the lattice and
    not here would crash the projection instead of failing this test.
    """

    assert set(typing.get_args(CapabilityPermissionClass)) == set(
        typing.get_args(PermissionClass)
    ), (
        "core.capability_lattice.PermissionClass and the payload's "
        "CapabilityPermissionClass have diverged"
    )


def test_every_lock_wrapper_field_is_declared() -> None:
    """The lock file is the state artifact this payload supersedes."""

    wrapper = set(CapabilityLockFileV1.model_fields) - {"capabilities"}
    assert wrapper == set(UNPUBLISHED_LOCK_FIELDS), (
        "capability lock wrapper fields and UNPUBLISHED_LOCK_FIELDS have "
        f"drifted: {sorted(wrapper ^ set(UNPUBLISHED_LOCK_FIELDS))}"
    )


def test_every_exclusion_carries_a_reason() -> None:
    for name, reasons in (
        ("UNPUBLISHED_FACT_FIELDS", UNPUBLISHED_FACT_FIELDS),
        ("UNPUBLISHED_LOCK_FIELDS", UNPUBLISHED_LOCK_FIELDS),
    ):
        for field, reason in reasons.items():
            assert len(reason.split()) >= 8, (
                f"{name}[{field!r}] must say why the field stays internal, not "
                "merely that it does"
            )


def _keys(node: Any) -> set[str]:
    if isinstance(node, dict):
        return set(node) | {key for value in node.values() for key in _keys(value)}
    if isinstance(node, list):
        return {key for value in node for key in _keys(value)}
    return set()


def _strings(node: Any) -> set[str]:
    if isinstance(node, dict):
        return {value for item in node.values() for value in _strings(item)}
    if isinstance(node, list):
        return {value for item in node for value in _strings(item)}
    if isinstance(node, str):
        return {node}
    return set()


@pytest.mark.parametrize("config", SAMPLE_CONFIGS, ids=lambda p: p.parent.name)
def test_unpublished_field_names_never_reach_the_payload(config: Path) -> None:
    """Negative control: an internally present field absent from the schema is
    absent from the output — on every shipped sample, not only a crafted one."""

    payload = project_capability_state(_facts(config)).model_dump(mode="json")
    present = _keys(payload)
    forbidden = {
        "source_location",
        "semantic_assessment",
        "risk_score",
        "risk_level",
        "reasons",
        "cli_version",
        "experimental",
        "capability_lock_schema_version",
    }
    assert not (present & forbidden), (
        f"{config.parent.name}: payload leaked internal field(s) "
        f"{sorted(present & forbidden)}"
    )


def test_unpublished_field_values_never_reach_the_payload() -> None:
    """The stronger form: the excluded *value* is nowhere in the output.

    A field can leak by being folded into a published string as easily as by
    keeping its own key, so assert on the value a sample fact carries.
    """

    facts = _facts(REFUND_SAMPLE / "shipgate.yaml")
    marker = "UNPUBLISHED-SOURCE-LOCATION-MARKER"
    fact = facts[0].model_copy(
        update={
            "evidence": facts[0].evidence.model_copy(
                update={"source_location": marker}
            )
        }
    )
    assert fact.evidence.source_location == marker

    payload = project_capability_state([fact]).model_dump(mode="json")
    assert marker not in _strings(payload)
    assert marker not in json.dumps(payload)


def test_payload_models_forbid_unknown_fields() -> None:
    """``extra="forbid"`` is what stops an internal field riding along."""

    payload = project_capability_state(
        _facts(REFUND_SAMPLE / "shipgate.yaml")
    ).model_dump(mode="json")
    payload["subjects"][0]["capabilities"][0]["risk_score"] = 42
    with pytest.raises(ValidationError):
        CapabilityStatePayloadV1.model_validate(payload)


# --- One subject, one row ----------------------------------------------------


def _fact_with(fact: CapabilityFactV1, **identity: Any) -> CapabilityFactV1:
    updated_identity = fact.identity.model_copy(update=identity)
    hashes = fact.hashes.model_copy(
        update={"identity_hash": f"{fact.hashes.identity_hash}x"}
    )
    return fact.model_copy(
        update={
            "identity": updated_identity,
            "hashes": hashes,
            "id": f"cap_{hashes.identity_hash}",
        }
    )


def test_subject_key_ignores_subject_kind() -> None:
    """The tool and the action are one subject — the #439 class, structurally.

    Two facts about one tool that differ only in ``subject_kind`` must land in
    one row. Keying on the kind is exactly what produced ``+2`` for one added
    tool.
    """

    base = _facts(REFUND_SAMPLE / "shipgate.yaml")[0]
    tool_view = _fact_with(base, subject_kind="tool")
    assert tool_view.id != base.id

    payload = project_capability_state([base, tool_view])
    assert len(payload.subjects) == 1
    assert len(payload.subjects[0].capabilities) == 2
    assert payload.state.subject_count == 1
    assert payload.state.capability_count == 2

    delta = project_capability_delta([], [base, tool_view])
    assert delta.summary.subjects == 1
    assert delta.summary.added_subjects == 1
    assert delta.summary.capability_changes == 2


def test_subject_key_separates_same_named_tools_from_two_providers() -> None:
    """Identity, not the display name — two providers are two subjects."""

    base = _facts(REFUND_SAMPLE / "shipgate.yaml")[0]
    other = _fact_with(base, provider="other_provider", tool_id="tool_v2_other")
    payload = project_capability_state([base, other])
    assert len(payload.subjects) == 2
    assert {entry.subject.name for entry in payload.subjects} == {base.identity.tool_name}


def test_a_key_collision_between_two_identities_fails_closed() -> None:
    """The identity guard must not be skipped when the display names agree.

    ``subject.key`` is a truncated digest, so equal keys are near-certainly the
    same subject but not provably so. A same-named collision is the one shape
    where a name-equality shortcut would merge two tools silently.
    """

    subject = project_capability_state(
        _facts(REFUND_SAMPLE / "shipgate.yaml")
    ).subjects[0].subject
    collided = subject.model_copy(update={"tool_id": "tool_v2_a_different_tool"})
    assert collided.key == subject.key and collided.name == subject.name

    with pytest.raises(CapabilityPayloadError, match="two different identities"):
        _merge_subject_refs(subject, collided)

    # A genuine rename under one identity still reconciles to a stable spelling.
    renamed = subject.model_copy(update={"name": "aaa_renamed"})
    assert _merge_subject_refs(subject, renamed).name == "aaa_renamed"


def test_an_empty_delta_cannot_name_two_different_states(refund_facts) -> None:
    """"Nothing changed" has to be supported by the payload's own digests."""

    base, head = refund_facts
    payload = project_capability_delta(base, base).model_dump(mode="json")
    assert payload["subjects"] == []

    payload["head"] = project_capability_state(head).state.model_dump(mode="json")
    with pytest.raises(ValidationError, match="same state, but their digests differ"):
        CapabilityDeltaPayloadV1.model_validate(payload)


def test_canonical_json_refuses_a_value_it_cannot_serialize() -> None:
    """A verifiable digest must not be computable over a `str()` rendering."""

    with pytest.raises(TypeError):
        canonical_payload_json({"path": Path("a/b")})
    # The two values a `default=str` fallback would have collapsed together.
    assert canonical_payload_json({"path": "a/b"}) == '{"path":"a/b"}'


def test_duplicate_subject_rows_are_rejected() -> None:
    payload = project_capability_state(
        _facts(REFUND_SAMPLE / "shipgate.yaml")
    ).model_dump(mode="json")
    payload["subjects"].append(payload["subjects"][0])
    payload["state"]["subject_count"] = 2
    payload["state"]["capability_count"] = 2
    with pytest.raises(ValidationError, match="duplicate subject key"):
        CapabilityStatePayloadV1.model_validate(payload)


def _reidentify_subject(row: dict, *, tool_id: str, name: str) -> dict:
    """Give a copied subject row a genuinely different, self-consistent identity.

    The key is not a free label any more, so a duplicate has to be built the way
    a producer would build it — otherwise the test only proves the key validator
    fires, not the invariant it is aiming at.
    """

    duplicate = json.loads(json.dumps(row))
    duplicate["subject"]["tool_id"] = tool_id
    duplicate["subject"]["name"] = name
    duplicate["subject"]["key"] = subject_key(
        agent=duplicate["subject"]["agent"],
        provider=duplicate["subject"]["provider"],
        tool_id=tool_id,
    )
    return duplicate


def test_state_digests_that_do_not_describe_the_rows_are_rejected(
    refund_facts,
) -> None:
    """The spec promises the digests are recomputable, so the payload checks."""

    base, head = refund_facts
    payload = project_capability_state(base).model_dump(mode="json")
    # Rows from head, digests still from base: exactly the shape a stale ref or
    # a hand-edit produces, and the one a digest-comparing consumer misses.
    head_payload = project_capability_state(head).model_dump(mode="json")
    payload["subjects"] = head_payload["subjects"]
    payload["state"]["subject_count"] = head_payload["state"]["subject_count"]
    payload["state"]["capability_count"] = head_payload["state"]["capability_count"]
    with pytest.raises(ValidationError, match="do not describe what this payload carries"):
        CapabilityStatePayloadV1.model_validate(payload)


def test_one_capability_cannot_appear_under_two_subjects() -> None:
    """Global, not per-row: the evidence digest is keyed by capability id.

    A repeat would silently drop one capability's provenance from
    ``evidence_set_digest``, so two states with different provenance would
    digest alike.
    """

    facts = _facts(REFUND_SAMPLE / "shipgate.yaml")
    payload = project_capability_state(facts).model_dump(mode="json")
    payload["subjects"].append(
        _reidentify_subject(
            payload["subjects"][0],
            tool_id="tool_v2_a_second_tool",
            name="zzz_second_tool",
        )
    )
    payload["state"]["subject_count"] = 2
    payload["state"]["capability_count"] = 2
    with pytest.raises(ValidationError, match="duplicate capability_id across subjects"):
        CapabilityStatePayloadV1.model_validate(payload)


def test_one_capability_cannot_appear_under_two_delta_subjects(refund_facts) -> None:
    base, head = refund_facts
    payload = project_capability_delta(base, head).model_dump(mode="json")
    borrowed = _reidentify_subject(
        payload["subjects"][0],
        tool_id="tool_v2_a_second_tool",
        name="zzz_second_tool",
    )
    payload["subjects"].append(borrowed)
    payload["summary"]["subjects"] += 1
    payload["summary"][f"{borrowed['transition']}_subjects"] += 1
    payload["summary"]["capability_changes"] += len(borrowed["changes"])
    with pytest.raises(ValidationError, match="duplicate capability_id across subjects"):
        CapabilityDeltaPayloadV1.model_validate(payload)


def test_a_summary_that_disagrees_with_the_rows_is_rejected(refund_facts) -> None:
    """A tampered count must fail, not be silently corrected."""

    base, head = refund_facts
    payload = project_capability_delta(base, head).model_dump(mode="json")
    # Move a subject between directions: the partition still sums, so the row
    # comparison is what has to catch it.
    payload["summary"]["added_subjects"] += 1
    payload["summary"]["modified_subjects"] -= 1
    with pytest.raises(ValidationError, match="does not describe its rows"):
        CapabilityDeltaPayloadV1.model_validate(payload)


def test_a_tool_that_loses_one_operation_is_modified_not_removed() -> None:
    """`transition` is about the subject, never about the kinds of its changes.

    A delta row carries only the capabilities that moved, so a tool that keeps
    one operation and loses another looks — from its changes alone — exactly
    like a tool that went away. Reading `removed` off the change kinds tells a
    reviewer the agent lost a tool it still has.
    """

    base_fact = _facts(REFUND_SAMPLE / "shipgate.yaml")[0]
    second = _fact_with(base_fact, operation="second_operation")

    lost_one = project_capability_delta([base_fact, second], [base_fact])
    assert [entry.transition for entry in lost_one.subjects] == ["modified"]
    assert lost_one.summary.removed_subjects == 0
    assert lost_one.summary.modified_subjects == 1
    row = lost_one.subjects[0]
    assert (row.present_in_base, row.present_in_head) == (True, True)
    assert [change.transition for change in row.changes] == ["removed"]

    # The mirror case: gaining an operation is not gaining a tool.
    gained_one = project_capability_delta([base_fact], [base_fact, second])
    assert [entry.transition for entry in gained_one.subjects] == ["modified"]
    assert gained_one.summary.added_subjects == 0

    # And a subject that really does go away is still `removed`.
    gone = project_capability_delta([base_fact, second], [])
    assert [entry.transition for entry in gone.subjects] == ["removed"]
    assert gone.summary.removed_subjects == 1


def test_a_scope_escalation_is_one_reidentified_change_on_one_subject() -> None:
    """The reviewer-relevant case: a tool whose scope widened.

    Scope is part of capability identity, so widening it changes the id. The
    engine pairs the two facts within one lineage instead of reporting an
    unrelated add and remove, and the payload must carry that pairing — a
    published `+1 added, -1 removed` for one broadened scope would read as a
    new tool and a lost one.
    """

    before = _facts(REFUND_SAMPLE / "shipgate.yaml")[0]
    after = _fact_with(
        before, scope=("support:kb:read", "support:kb:write")
    )

    delta = project_capability_delta([before], [after])
    assert delta.summary.subjects == 1
    assert (delta.summary.added_subjects, delta.summary.removed_subjects) == (0, 0)
    row = delta.subjects[0]
    assert row.transition == "modified"
    assert [change.transition for change in row.changes] == ["reidentified"]
    change = row.changes[0]
    assert change.semantic_direction == "broadened"
    assert change.before is not None and change.after is not None
    assert change.before.capability_id != change.after.capability_id
    assert change.after.scope == ("support:kb:read", "support:kb:write")
    jsonschema.validate(delta.model_dump(mode="json"), PAYLOAD_SCHEMA)


def test_rows_emitted_out_of_order_are_rejected() -> None:
    """Determinism is a published guarantee, so the format enforces the order."""

    facts = _facts(SAMPLES / "support_refund_agent" / "shipgate.yaml")
    payload = project_capability_state(facts).model_dump(mode="json")
    assert len(payload["subjects"]) > 1
    payload["subjects"].reverse()
    with pytest.raises(ValidationError, match="must be emitted in sorted order"):
        CapabilityStatePayloadV1.model_validate(payload)


def test_a_transition_that_disagrees_with_presence_is_rejected(refund_facts) -> None:
    base, head = refund_facts
    payload = project_capability_delta(base, head).model_dump(mode="json")
    for entry in payload["subjects"]:
        if entry["transition"] == "added":
            entry["present_in_base"] = True
            break
    else:  # pragma: no cover - the sample always has an added subject
        pytest.fail("sample delta lost its added subject")
    with pytest.raises(ValidationError, match="make it 'modified'"):
        CapabilityDeltaPayloadV1.model_validate(payload)


def test_a_subject_absent_from_a_side_cannot_carry_impossible_changes(
    refund_facts,
) -> None:
    """Presence bounds the changes: base never had it, so nothing can have moved."""

    base, head = refund_facts
    payload = project_capability_delta(base, head).model_dump(mode="json")
    for entry in payload["subjects"]:
        if entry["transition"] == "added":
            change = entry["changes"][0]
            change["transition"] = "reidentified"
            # A distinct 'before' so the identity rules are satisfied and the
            # presence rule is the one left to fire.
            before = json.loads(json.dumps(change["after"]))
            before["capability_id"] = "cap_0000000000000000"
            before["digests"]["identity_hash"] = "0000000000000000"
            change["before"] = before
            change["changed_dimensions"] = ["identity_hash"]
            change["semantic_direction"] = "unknown"
            break
    else:  # pragma: no cover
        pytest.fail("sample delta lost its added subject")
    with pytest.raises(ValidationError, match="absent from base but carries"):
        CapabilityDeltaPayloadV1.model_validate(payload)


def test_a_subject_present_on_neither_side_is_rejected() -> None:
    facts = _facts(REFUND_SAMPLE / "shipgate.yaml")
    subject = project_capability_state(facts).subjects[0].subject
    with pytest.raises(ValidationError, match="present on neither side"):
        CapabilityDeltaSubject(
            subject=subject,
            present_in_base=False,
            present_in_head=False,
            transition="modified",
            changes=(_added_entry(project_capability_record(facts[0])),),
        )


def test_one_capability_cannot_appear_under_two_rows_of_a_subject() -> None:
    """Uniqueness covers both sides of a transition, not only the survivor.

    A ``changed`` entry names one capability twice (before and after) and a
    ``reidentified`` entry names two. Checking only the surviving side would let
    a removal and a reidentification publish the same capability under two rows.
    """

    facts = _facts(REFUND_SAMPLE / "shipgate.yaml")
    before = project_capability_record(facts[0])
    after = project_capability_record(
        _fact_with(facts[0], operation="renamed_operation")
    )
    subject = project_capability_state(facts).subjects[0].subject

    # Legitimate: the reidentified pair names two distinct capabilities.
    CapabilityDeltaSubject(
        subject=subject,
        present_in_base=True,
        present_in_head=True,
        transition="modified",
        changes=(_paired_entry_for(before, after, transition="reidentified"),),
    )

    with pytest.raises(ValidationError, match="duplicate capability_id"):
        CapabilityDeltaSubject(
            subject=subject,
            present_in_base=True,
            present_in_head=True,
            transition="modified",
            changes=tuple(
                sorted(
                    (
                        _paired_entry_for(before, after, transition="reidentified"),
                        _removed_entry(before),
                    ),
                    key=lambda entry: (
                        entry.record.subject_kind,
                        entry.record.operation,
                        entry.record.capability_id,
                    ),
                )
            ),
        )


def test_summary_directional_counts_must_partition_the_subjects() -> None:
    with pytest.raises(ValidationError, match="must sum to subjects"):
        CapabilityDeltaSummary(
            subjects=2,
            added_subjects=1,
            removed_subjects=0,
            modified_subjects=0,
            capability_changes=2,
        )


def test_summary_cannot_report_fewer_changes_than_subjects() -> None:
    with pytest.raises(ValidationError, match="cannot be fewer than subjects"):
        CapabilityDeltaSummary(
            subjects=2,
            added_subjects=2,
            removed_subjects=0,
            modified_subjects=0,
            capability_changes=1,
        )


def test_transition_and_sides_must_agree() -> None:
    facts = _facts(REFUND_SAMPLE / "shipgate.yaml")
    record = project_capability_record(facts[0])

    with pytest.raises(ValidationError, match="forbids a 'before' record"):
        CapabilityRecordTransitionEntry(
            transition="added",
            changed_dimensions=(),
            semantic_direction="added",
            semantic_changes=(),
            before=record,
            after=record,
        )
    with pytest.raises(ValidationError, match="requires an 'after' record"):
        CapabilityRecordTransitionEntry(
            transition="changed",
            changed_dimensions=("effect_hash",),
            semantic_direction="unknown",
            semantic_changes=(),
            before=record,
            after=None,
        )
    with pytest.raises(ValidationError, match="cannot name changed dimensions"):
        CapabilityRecordTransitionEntry(
            transition="added",
            changed_dimensions=("effect_hash",),
            semantic_direction="added",
            semantic_changes=(),
            before=None,
            after=record,
        )


def test_a_membership_change_cannot_claim_the_other_direction() -> None:
    """An `added` row that says `removed` is a contradiction, not a nuance."""

    record = project_capability_record(_facts(REFUND_SAMPLE / "shipgate.yaml")[0])
    with pytest.raises(ValidationError, match="must carry semantic_direction 'added'"):
        CapabilityRecordTransitionEntry(
            transition="added",
            changed_dimensions=(),
            semantic_direction="removed",
            semantic_changes=(),
            before=None,
            after=record,
        )
    with pytest.raises(ValidationError, match="cannot claim membership direction"):
        _paired_entry_for(record, record, transition="changed", direction="added")


def test_two_identical_records_are_not_a_change() -> None:
    record = project_capability_record(_facts(REFUND_SAMPLE / "shipgate.yaml")[0])
    with pytest.raises(ValidationError, match="two identical records"):
        _paired_entry_for(record, record, transition="changed")


def test_changed_dimensions_are_derived_from_the_records() -> None:
    """A dimension list is a fact about the two rows, not a free annotation."""

    facts = _facts(REFUND_SAMPLE / "shipgate.yaml")
    before = project_capability_record(facts[0])
    after = project_capability_record(
        facts[0].model_copy(
            update={
                "hashes": facts[0].hashes.model_copy(
                    update={"effect_hash": "9" * 16, "risk_hash": "8" * 16}
                )
            }
        )
    )
    assert changed_record_dimensions(before, after) == ("effect_hash", "risk_hash")

    with pytest.raises(ValidationError, match="must be exactly the digests that differ"):
        CapabilityRecordTransitionEntry(
            transition="changed",
            changed_dimensions=("authority_hash",),
            semantic_direction="unknown",
            semantic_changes=(),
            before=before,
            after=after,
        )
    with pytest.raises(ValidationError, match="must be exactly the digests that differ"):
        CapabilityRecordTransitionEntry(
            transition="changed",
            changed_dimensions=("effect_hash",),
            semantic_direction="unknown",
            semantic_changes=(),
            before=before,
            after=after,
        )


def test_changed_and_reidentified_are_distinguished_by_identity() -> None:
    facts = _facts(REFUND_SAMPLE / "shipgate.yaml")
    before = project_capability_record(facts[0])
    renamed = project_capability_record(_fact_with(facts[0], operation="renamed"))
    same_id_moved = project_capability_record(
        facts[0].model_copy(
            update={
                "hashes": facts[0].hashes.model_copy(update={"effect_hash": "7" * 16})
            }
        )
    )

    # 'changed' is one capability moving; 'reidentified' is its identity moving.
    _paired_entry_for(before, same_id_moved, transition="changed")
    _paired_entry_for(before, renamed, transition="reidentified")

    with pytest.raises(ValidationError, match="transition 'changed' is one capability"):
        _paired_entry_for(before, renamed, transition="changed")
    with pytest.raises(ValidationError, match="transition 'reidentified' is an identity"):
        _paired_entry_for(before, same_id_moved, transition="reidentified")


# --- The subjects outside the analysed surface (#437) ------------------------


def test_analysis_coverage_defaults_to_not_requested(refund_facts) -> None:
    """Absence of the axis must never read as "nothing was left out"."""

    base, head = refund_facts
    state = project_capability_state(base)
    assert state.analysis_coverage.status == "not_requested"
    assert state.analysis_coverage.subjects_outside_analysis == ()

    delta = project_capability_delta(base, head)
    assert delta.analysis_coverage.status == "not_requested"
    assert delta.analysis_coverage.newly_outside_analysis == ()
    assert delta.analysis_coverage.no_longer_outside_analysis == ()


def test_naming_outside_subjects_requires_a_completed_analysis() -> None:
    """"We did not look" cannot be written in the shape of "we found none"."""

    subject = (
        project_capability_state(_facts(REFUND_SAMPLE / "shipgate.yaml"))
        .subjects[0]
        .subject
    )
    for status in ("not_requested", "unavailable"):
        with pytest.raises(ValidationError, match="requires having looked"):
            CapabilityAnalysisCoverage(
                status=status,
                subjects_outside_analysis=(subject,),
            )


def test_duplicate_outside_subjects_are_rejected() -> None:
    subject = (
        project_capability_state(_facts(REFUND_SAMPLE / "shipgate.yaml"))
        .subjects[0]
        .subject
    )
    with pytest.raises(ValidationError, match="duplicate subject key"):
        CapabilityAnalysisCoverage(
            status="complete",
            subjects_outside_analysis=(subject, subject),
        )


def test_a_subject_outside_analysis_is_carried_and_validates(refund_facts) -> None:
    """The #437 class has a place in the frozen payload, on both views.

    An added-but-unbound tool produces no capability fact, so it cannot appear
    in ``subjects[]``. If the schema had no room for it, the first surface that
    needed to report it would have had to invent a second payload shape — which
    is what freezing this schema exists to prevent.
    """

    base, head = refund_facts
    unbound = project_capability_state(head).subjects[-1].subject
    coverage = CapabilityAnalysisCoverage(
        status="complete",
        subjects_outside_analysis=(unbound,),
    )

    state = project_capability_state(base, analysis_coverage=coverage)
    assert state.analysis_coverage.subjects_outside_analysis == (unbound,)
    jsonschema.validate(state.model_dump(mode="json"), PAYLOAD_SCHEMA)
    assert (
        CapabilityPayloadV1.model_validate(
            state.model_dump(mode="json")
        ).root.analysis_coverage
        == coverage
    )

    delta = project_capability_delta(
        base,
        head,
        base_analysis_coverage=CapabilityAnalysisCoverage(
            status="complete", subjects_outside_analysis=()
        ),
        head_analysis_coverage=coverage,
    )
    assert delta.analysis_coverage.head == coverage
    assert delta.analysis_coverage.newly_outside_analysis == (unbound,)
    jsonschema.validate(delta.model_dump(mode="json"), PAYLOAD_SCHEMA)


def test_a_delta_distinguishes_newly_unbound_from_already_unbound() -> None:
    """One snapshot cannot answer #437's question; two sides can.

    "A tool was added and is unbound" and "a tool has been unbound since before
    this change" are different facts, and only the first is something a reviewer
    of this diff must act on. With a single coverage block the two produced
    byte-identical payloads.
    """

    facts = _facts(REFUND_SAMPLE / "shipgate.yaml")
    subject = project_capability_state(facts).subjects[0].subject
    clean = CapabilityAnalysisCoverage(status="complete", subjects_outside_analysis=())
    named = CapabilityAnalysisCoverage(
        status="complete", subjects_outside_analysis=(subject,)
    )

    newly = project_capability_delta(
        [], [], base_analysis_coverage=clean, head_analysis_coverage=named
    )
    already = project_capability_delta(
        [], [], base_analysis_coverage=named, head_analysis_coverage=named
    )
    resolved = project_capability_delta(
        [], [], base_analysis_coverage=named, head_analysis_coverage=clean
    )

    assert newly.analysis_coverage.newly_outside_analysis == (subject,)
    assert already.analysis_coverage.newly_outside_analysis == ()
    assert resolved.analysis_coverage.no_longer_outside_analysis == (subject,)
    assert len({payload.model_dump_json() for payload in (newly, already, resolved)}) == 3

    for payload in (newly, already, resolved):
        jsonschema.validate(payload.model_dump(mode="json"), PAYLOAD_SCHEMA)


def test_a_coverage_comparison_is_only_as_established_as_its_weaker_side() -> None:
    facts = _facts(REFUND_SAMPLE / "shipgate.yaml")
    subject = project_capability_state(facts).subjects[0].subject
    named = CapabilityAnalysisCoverage(
        status="complete", subjects_outside_analysis=(subject,)
    )
    unknown = CapabilityAnalysisCoverage.not_requested()
    failed = CapabilityAnalysisCoverage(status="unavailable", subjects_outside_analysis=())

    half = project_capability_delta(
        [], [], base_analysis_coverage=unknown, head_analysis_coverage=named
    )
    assert half.analysis_coverage.status == "not_requested"
    # The head does name a subject, but the transition is not established: with
    # no base to compare against, "newly" is not something the payload knows.
    assert half.analysis_coverage.newly_outside_analysis == ()
    assert half.analysis_coverage.head.subjects_outside_analysis == (subject,)

    # `unavailable` outranks `not_requested`: it is the fail-open shape made
    # visible, and a consumer must be able to tell it from never having asked.
    broken = project_capability_delta(
        [], [], base_analysis_coverage=failed, head_analysis_coverage=named
    )
    assert broken.analysis_coverage.status == "unavailable"


def test_coverage_is_bound_into_each_state_identity() -> None:
    """Two states differing only in coverage must not share a state ref."""

    facts = _facts(REFUND_SAMPLE / "shipgate.yaml")
    subject = project_capability_state(facts).subjects[0].subject
    clean = CapabilityAnalysisCoverage(status="complete", subjects_outside_analysis=())
    named = CapabilityAnalysisCoverage(
        status="complete", subjects_outside_analysis=(subject,)
    )

    a = project_capability_state(facts, analysis_coverage=clean).state
    b = project_capability_state(facts, analysis_coverage=named).state
    assert a.capability_set_digest == b.capability_set_digest
    assert a.analysis_coverage_digest != b.analysis_coverage_digest
    assert a != b


def test_a_state_ref_that_disagrees_with_its_coverage_is_rejected(refund_facts) -> None:
    base, head = refund_facts
    subject = project_capability_state(head).subjects[-1].subject
    payload = project_capability_delta(
        base,
        head,
        base_analysis_coverage=CapabilityAnalysisCoverage(
            status="complete", subjects_outside_analysis=()
        ),
        head_analysis_coverage=CapabilityAnalysisCoverage(
            status="complete", subjects_outside_analysis=(subject,)
        ),
    ).model_dump(mode="json")
    payload["head"]["analysis_coverage_digest"] = payload["base"]["analysis_coverage_digest"]
    with pytest.raises(ValidationError, match="analysis_coverage_digest does not describe"):
        CapabilityDeltaPayloadV1.model_validate(payload)


def test_a_coverage_transition_cannot_be_asserted(refund_facts) -> None:
    """The directional lists are recomputed, so they cannot be fabricated."""

    base, head = refund_facts
    subject = project_capability_state(head).subjects[-1].subject
    named = CapabilityAnalysisCoverage(
        status="complete", subjects_outside_analysis=(subject,)
    )
    payload = project_capability_delta(
        base,
        head,
        base_analysis_coverage=named,
        head_analysis_coverage=named,
    ).model_dump(mode="json")
    payload["analysis_coverage"]["newly_outside_analysis"] = [
        payload["analysis_coverage"]["head"]["subjects_outside_analysis"][0]
    ]
    with pytest.raises(ValidationError, match="newly_outside_analysis must be exactly"):
        CapabilityDeltaPayloadV1.model_validate(payload)


def test_outside_analysis_and_delta_rows_are_separate_axes(refund_facts) -> None:
    """A tool that lost its binding is both removed and newly outside.

    The two axes answer different questions, so the schema must not force them
    to be disjoint.
    """

    base, head = refund_facts
    removed = project_capability_delta(head, base)
    assert removed.summary.removed_subjects == 1
    gone = next(
        entry.subject for entry in removed.subjects if entry.transition == "removed"
    )
    payload = project_capability_delta(
        head,
        base,
        base_analysis_coverage=CapabilityAnalysisCoverage(
            status="complete", subjects_outside_analysis=()
        ),
        head_analysis_coverage=CapabilityAnalysisCoverage(
            status="complete", subjects_outside_analysis=(gone,)
        ),
    )
    assert payload.analysis_coverage.newly_outside_analysis == (gone,)
    assert gone.key in {entry.subject.key for entry in payload.subjects}
    jsonschema.validate(payload.model_dump(mode="json"), PAYLOAD_SCHEMA)


# --- Digests, permission, determinism ---------------------------------------


def test_evidence_only_change_moves_only_the_evidence_digest() -> None:
    """The two digests answer two different questions, and must stay separable."""

    fact = _facts(REFUND_SAMPLE / "shipgate.yaml")[0]
    moved = fact.model_copy(
        update={
            "evidence": fact.evidence.model_copy(
                update={"source_path": "moved/tools.json"}
            ),
            "hashes": fact.hashes.model_copy(
                update={"evidence_hash": "0" * len(fact.hashes.evidence_hash)}
            ),
        }
    )
    before = project_capability_state([fact]).state
    after = project_capability_state([moved]).state
    assert before.capability_set_digest == after.capability_set_digest
    assert before.evidence_set_digest != after.evidence_set_digest


def test_semantic_change_moves_the_capability_digest() -> None:
    fact = _facts(REFUND_SAMPLE / "shipgate.yaml")[0]
    moved = fact.model_copy(
        update={"risk_tags": (*fact.risk_tags, "newly_tagged")}
    )
    before = project_capability_state([fact]).state
    after = project_capability_state([moved]).state
    assert before.capability_set_digest != after.capability_set_digest


def test_published_permission_matches_the_audit_classifier() -> None:
    """One classifier: the payload cannot disagree with ``mcp audit``."""

    facts = _facts(SAMPLES / "support_refund_agent" / "shipgate.yaml")
    checked = 0
    for fact in facts:
        assert fact.semantic_assessment is not None
        record = project_capability_record(fact)
        tool = Tool(
            id=fact.identity.tool_id,
            name=fact.identity.tool_name,
            source_type=fact.evidence.source_type,
            semantic_assessment=None,
        )
        tool = tool.model_copy(
            update={"semantic_assessment": _as_domain_assessment(fact)}
        )
        assert record.permission.status == "measured"
        assert record.permission.classes == classify_tool_permission(tool).classes
        checked += 1
    assert checked, "sample produced no facts to compare"


def _as_domain_assessment(fact: CapabilityFactV1) -> Any:
    from agents_shipgate.core.domain import ToolSemanticAssessment

    assert fact.semantic_assessment is not None
    return ToolSemanticAssessment.model_validate(
        fact.semantic_assessment.model_dump(mode="python")
    )


def test_permission_classes_are_ordered_the_same_in_every_process() -> None:
    """`financial` and `production` share a rank, so a rank-only sort is not total.

    A stable sort with a tied key preserves input order, and the input was a
    `set`, whose iteration order is hash-randomized per process. That reached
    the published bytes and the state digest: the same repository produced two
    different payloads on two runs. Run it under several hash seeds.
    """

    program = (
        "import sys; sys.path.insert(0, 'src')\n"
        "from agents_shipgate.core.capability_lattice import "
        "classify_semantic_permission, _normalize_classes, PERMISSION_CLASS_RANK\n"
        "classes = _normalize_classes({'financial', 'production'})\n"
        "print(tuple(sorted(classes, key=lambda i: (PERMISSION_CLASS_RANK[i], i))))\n"
    )
    seen = {
        subprocess.run(
            [sys.executable, "-c", program],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
        ).stdout.strip()
        for seed in ("0", "1", "4", "7", "11", "random")
    }
    assert seen == {"('financial', 'production')"}, (
        f"permission class order is not process-independent: {sorted(seen)}"
    )


def test_the_payload_permission_ranks_match_the_lattice() -> None:
    """The tie-break only works if both spellings agree on the ranks too."""

    assert PAYLOAD_PERMISSION_RANK == LATTICE_PERMISSION_RANK


def test_a_permission_shape_the_classifier_cannot_produce_is_rejected() -> None:
    """A combination the lattice never emits is a claim with no meaning."""

    cases = (
        ("must name at least one class", {"status": "measured", "classes": (), "side_effect_unknown": False}),
        ("cannot name classes", {"status": "unavailable", "classes": ("read",), "side_effect_unknown": True}),
        ("cannot pair 'read'", {"status": "measured", "classes": ("read", "write"), "side_effect_unknown": False}),
        ("must also carry 'write'", {"status": "measured", "classes": ("destructive",), "side_effect_unknown": False}),
        ("must carry the 'unknown' class", {"status": "measured", "classes": ("write",), "side_effect_unknown": True}),
        ("ordered by (rank, name)", {"status": "measured", "classes": ("production", "financial", "unknown"), "side_effect_unknown": True}),
    )
    for expected, kwargs in cases:
        with pytest.raises(ValidationError, match=re.escape(expected)):
            CapabilityPermissionFacts(**kwargs)

    # And the shapes the lattice does produce still round-trip.
    CapabilityPermissionFacts(
        status="measured", classes=("write", "financial", "unknown"), side_effect_unknown=True
    )
    CapabilityPermissionFacts(status="measured", classes=("read",), side_effect_unknown=False)


def test_an_assessment_only_change_that_moves_permission_is_not_evidence_only() -> None:
    """The payload must classify what *it* publishes.

    The fact layer folds the whole semantic assessment into `evidence_hash`, and
    this payload publishes a `permission` block derived from that assessment. So
    an assessment change can widen a published permission while the only fact
    hash that moved is the evidence one — and inheriting the engine's verdict
    there published a permission expansion as provenance-only, in the artifact
    whose whole purpose is to be trusted without re-deriving anything.
    """

    fact = _facts(REFUND_SAMPLE / "shipgate.yaml")[0]
    assessment = fact.semantic_assessment
    assert assessment is not None
    widened = fact.model_copy(
        update={
            "semantic_assessment": assessment.model_copy(
                update={"effect": assessment.effect.model_copy(update={"status": "inferred"})}
            ),
            "hashes": fact.hashes.model_copy(update={"evidence_hash": "f" * 16}),
        }
    )

    delta = project_capability_delta([fact], [widened])
    change = delta.subjects[0].changes[0]
    assert change.changed_dimensions == ("evidence_hash",)
    assert change.before is not None and change.after is not None
    assert not change.before.permission.side_effect_unknown
    assert change.after.permission.side_effect_unknown
    assert change.semantic_direction == "broadened", (
        "a published permission expansion must not be labelled provenance-only"
    )
    assert "permission_changed" in {entry.kind for entry in change.semantic_changes}
    jsonschema.validate(delta.model_dump(mode="json"), PAYLOAD_SCHEMA)

    # The reverse move narrows, and a genuine provenance-only change still reads
    # as one.
    assert (
        project_capability_delta([widened], [fact]).subjects[0].changes[0].semantic_direction
        == "narrowed"
    )
    moved_file = fact.model_copy(
        update={
            "evidence": fact.evidence.model_copy(update={"source_path": "moved.json"}),
            "hashes": fact.hashes.model_copy(update={"evidence_hash": "e" * 16}),
        }
    )
    assert (
        project_capability_delta([fact], [moved_file]).subjects[0].changes[0].semantic_direction
        == "evidence_only"
    )


def test_a_permission_move_the_payload_cannot_rank_is_reported_unknown() -> None:
    """`unknown` is the honest answer; `evidence_only` would be the wrong one.

    Losing the measurement is not a narrowing and regaining it is not a
    broadening, so a status change gets no direction. What matters is that none
    of these paths falls back to "only provenance moved".
    """

    fact = _facts(REFUND_SAMPLE / "shipgate.yaml")[0]
    unmeasured = fact.model_copy(
        update={
            "semantic_assessment": None,
            "hashes": fact.hashes.model_copy(update={"evidence_hash": "d" * 16}),
        }
    )
    lost = project_capability_delta([fact], [unmeasured]).subjects[0].changes[0]
    assert lost.before is not None and lost.after is not None
    assert lost.before.permission.status == "measured"
    assert lost.after.permission.status == "unavailable"
    assert lost.semantic_direction == "unknown"
    assert "permission_changed" in {entry.kind for entry in lost.semantic_changes}

    regained = project_capability_delta([unmeasured], [fact]).subjects[0].changes[0]
    assert regained.semantic_direction == "unknown"


def test_a_permission_move_in_both_directions_is_mixed() -> None:
    """One class gained and another lost is neither broadened nor narrowed."""

    fact = _facts(REFUND_SAMPLE / "shipgate.yaml")[0]
    before = project_capability_record(fact)
    assert before.permission.classes == ("financial",)

    direction, changes = _permission_shift(
        before.permission,
        CapabilityPermissionFacts(
            status="measured",
            classes=("write", "external"),
            side_effect_unknown=False,
        ),
    )
    assert direction == "mixed"
    assert [entry.kind for entry in changes] == ["permission_changed"]

    # And a move that is neither a gain nor a loss has no direction to give.
    same_classes, _ = _permission_shift(before.permission, before.permission)
    assert same_classes == "unknown"


def test_evidence_only_cannot_be_claimed_over_differing_semantics() -> None:
    """The same rule, enforced on a payload the parser did not produce."""

    fact = _facts(REFUND_SAMPLE / "shipgate.yaml")[0]
    assessment = fact.semantic_assessment
    assert assessment is not None
    widened = fact.model_copy(
        update={
            "semantic_assessment": assessment.model_copy(
                update={"effect": assessment.effect.model_copy(update={"status": "inferred"})}
            ),
            "hashes": fact.hashes.model_copy(update={"evidence_hash": "f" * 16}),
        }
    )
    with pytest.raises(ValidationError, match="differ in semantic content"):
        _paired_entry_for(
            project_capability_record(fact),
            project_capability_record(widened),
            transition="changed",
            direction="evidence_only",
        )


def test_a_renamed_tool_is_published_under_its_head_name() -> None:
    """A reviewer of this diff has to be able to find the name in head.

    Reducing the two sides with a lexical-minimum tie-break published whichever
    spelling sorted first, which for a rename is as likely to be the name that
    no longer exists.
    """

    facts = _facts(REFUND_SAMPLE / "shipgate.yaml")

    def renamed(name: str, digest: str) -> CapabilityFactV1:
        # A rename moves the tool name, which is part of capability identity —
        # so it moves the identity hash and the id with it, exactly as a real
        # scan would. The tool id does not move, so it is one subject.
        return facts[0].model_copy(
            update={
                "identity": facts[0].identity.model_copy(update={"tool_name": name}),
                "hashes": facts[0].hashes.model_copy(update={"identity_hash": digest}),
                "id": f"cap_{digest}",
            }
        )

    before = renamed("zzz_old_name", "aaaa0000aaaa0000")
    after = renamed("aaa_new_name", "bbbb1111bbbb1111")
    # Same subject either way: the display name is not identity.
    assert (
        project_capability_state([before]).subjects[0].subject.key
        == project_capability_state([after]).subjects[0].subject.key
    )

    forward = project_capability_delta([before], [after])
    assert forward.subjects[0].subject.name == "aaa_new_name"
    backward = project_capability_delta([after], [before])
    assert backward.subjects[0].subject.name == "zzz_old_name"

    # A subject only in base keeps the only name it has.
    gone = project_capability_delta([before], [])
    assert gone.subjects[0].subject.name == "zzz_old_name"


def test_permission_without_a_semantic_assessment_is_fail_closed() -> None:
    """Unmeasured is unknown, not read-only."""

    fact = _facts(REFUND_SAMPLE / "shipgate.yaml")[0]
    legacy = fact.model_copy(update={"semantic_assessment": None})
    record = project_capability_record(legacy)
    assert record.permission.status == "unavailable"
    assert record.permission.classes == ()
    assert record.permission.side_effect_unknown is True


def test_permission_unavailable_cannot_claim_known_side_effects() -> None:
    with pytest.raises(ValidationError, match="not side-effect free"):
        CapabilityPermissionFacts(
            status="unavailable", classes=(), side_effect_unknown=False
        )


@pytest.mark.parametrize("config", SAMPLE_CONFIGS, ids=lambda p: p.parent.name)
def test_projection_is_byte_stable(config: Path) -> None:
    facts = _facts(config)
    first = project_capability_state(facts).model_dump_json()
    second = project_capability_state(list(reversed(facts))).model_dump_json()
    assert first == second, (
        "the payload must not depend on the order facts arrive in; two builds "
        "of the same inputs have to serialize byte-identically"
    )


def test_subject_key_matches_the_derivation_the_spec_publishes() -> None:
    """Recompute the key the way the spec tells an external consumer to.

    Asserting ``key == subject_key(...)`` would only check the implementation
    against itself; a change to the digest input or the truncation length would
    move both sides together and leave the published recipe silently wrong.
    This redoes it from stdlib, from the fields the payload carries.
    """

    payload = project_capability_state(_facts(REFUND_SAMPLE / "shipgate.yaml"))
    assert payload.subjects, "sample produced no subjects to check"
    for entry in payload.subjects:
        subject = entry.subject
        material = json.dumps(
            {
                "agent": subject.agent,
                "provider": subject.provider,
                "tool_id": subject.tool_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        expected = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
        assert subject.key == f"capsubj_{expected}"
        # And the exported helper agrees, so a caller can use either.
        assert subject.key == subject_key(
            agent=subject.agent,
            provider=subject.provider,
            tool_id=subject.tool_id,
        )


def test_state_digests_match_the_recipe_the_spec_publishes() -> None:
    """Recompute all three digests from the serialized payload, stdlib only.

    Same reason as the subject key: comparing against ``state_digests`` would
    only check the implementation against itself, and the spec tells external
    consumers these are recomputable from the payload alone. Non-ASCII content
    is included on purpose — the canonicalization is UTF-8 and unescaped, which
    is the part a JavaScript implementation would otherwise get wrong.
    """

    facts = _facts(SAMPLES / "support_refund_agent" / "shipgate.yaml")
    renamed = [
        facts[0].model_copy(
            update={
                "identity": facts[0].identity.model_copy(
                    update={"tool_name": "café_tool"}
                )
            }
        ),
        *facts[1:],
    ]
    outside = project_capability_state(facts).subjects[0].subject
    payload = project_capability_state(
        renamed,
        analysis_coverage=CapabilityAnalysisCoverage(
            status="complete", subjects_outside_analysis=(outside,)
        ),
    ).model_dump(mode="json")
    assert payload["subjects"], "sample produced no subjects to digest"
    assert any("café_tool" == entry["subject"]["name"] for entry in payload["subjects"])

    def sha256_of(value: Any) -> str:
        material = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    semantic_rows = []
    evidence_by_id = {}
    for row in payload["subjects"]:
        records = []
        for record in row["capabilities"]:
            evidence_by_id[record["capability_id"]] = {
                "evidence": record["evidence"],
                "evidence_hash": record["digests"]["evidence_hash"],
            }
            stripped = {key: value for key, value in record.items() if key != "evidence"}
            stripped["digests"] = {
                name: value
                for name, value in record["digests"].items()
                if name != "evidence_hash"
            }
            records.append(stripped)
        semantic_rows.append({"subject": row["subject"], "capabilities": records})

    assert payload["state"]["capability_set_digest"] == sha256_of(semantic_rows)
    assert payload["state"]["evidence_set_digest"] == sha256_of(evidence_by_id)
    assert payload["state"]["analysis_coverage_digest"] == sha256_of(
        payload["analysis_coverage"]
    )


def test_canonical_json_is_utf8_and_unescaped() -> None:
    """A cross-language test vector for the one part languages disagree on.

    ``json.dumps`` escapes non-ASCII by default, so a Python producer would hash
    ``caf\\u00e9`` where a JavaScript consumer hashes ``café``: same identity,
    two digests, in a format whose whole purpose is independent consumers.
    """

    assert canonical_payload_json({"name": "café"}) == '{"name":"café"}'
    assert (
        payload_digest({"name": "café"})
        == hashlib.sha256('{"name":"café"}'.encode()).hexdigest()
    )
    # Sorted keys, compact separators, and no float ambiguity to resolve.
    assert canonical_payload_json({"b": 1, "a": [1, 2]}) == '{"a":[1,2],"b":1}'
    with pytest.raises(ValueError):
        canonical_payload_json({"n": float("nan")})


def test_subject_key_survives_a_non_ascii_identity() -> None:
    material = '{"agent":"agént","provider":"p","tool_id":"tool_✓"}'
    expected = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    assert subject_key(agent="agént", provider="p", tool_id="tool_✓") == (
        f"capsubj_{expected}"
    )


def test_spec_page_publishes_the_digest_recipe() -> None:
    spec = (REPO_ROOT / "docs/capability-payload.md").read_text(encoding="utf-8")
    for fragment in (
        "capability_set_digest",
        "evidence_set_digest",
        "digests.evidence_hash",
        "sorted keys and compact separators",
    ):
        assert fragment in spec, (
            f"docs/capability-payload.md must publish the digest recipe; "
            f"missing {fragment!r}"
        )


def test_spec_page_publishes_the_subject_key_recipe() -> None:
    """The formula above is only reproducible if the spec still states it."""

    spec = (REPO_ROOT / "docs/capability-payload.md").read_text(encoding="utf-8")
    for fragment in ('"capsubj_" + sha256(', "[:16]", "canonical_json"):
        assert fragment in spec, (
            f"docs/capability-payload.md must publish the subject-key "
            f"derivation; missing {fragment!r}"
        )


# --- Published documents -----------------------------------------------------


@pytest.mark.parametrize("path", [STATE_EXAMPLE, DELTA_EXAMPLE])
def test_committed_examples_validate_against_the_committed_schema(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    jsonschema.validate(payload, PAYLOAD_SCHEMA)
    assert (
        payload["capability_payload_schema_version"]
        == CAPABILITY_PAYLOAD_SCHEMA_VERSION
    )
    CapabilityPayloadV1.model_validate(payload)


# The stage-one rules, each with a mutation that must break it. These run
# against the committed artifact an external consumer actually validates with,
# not against the Pydantic models — the gap between the two is the finding this
# table exists to close.
_SCHEMA_REJECTS: tuple[tuple[str, Any], ...] = (
    ("missing schema version", lambda p: p.pop("capability_payload_schema_version")),
    ("missing view discriminator", lambda p: p.pop("view")),
    ("missing analysis_coverage", lambda p: p.pop("analysis_coverage")),
    ("missing subjects", lambda p: p.pop("subjects")),
    (
        "'added' carrying a before record",
        lambda p: _first_added(p)["changes"][0].__setitem__(
            "before", _first_added(p)["changes"][0]["after"]
        ),
    ),
    (
        "'added' claiming direction 'removed'",
        lambda p: _first_added(p)["changes"][0].__setitem__(
            "semantic_direction", "removed"
        ),
    ),
    (
        "'added' naming changed dimensions",
        lambda p: _first_added(p)["changes"][0].__setitem__(
            "changed_dimensions", ["effect_hash"]
        ),
    ),
    (
        "transition disagreeing with presence",
        lambda p: _first_added(p).__setitem__("present_in_base", True),
    ),
    (
        "present on neither side",
        lambda p: p["subjects"][0].update(
            {"present_in_base": False, "present_in_head": False}
        ),
    ),
    (
        "malformed subject key",
        lambda p: p["subjects"][0]["subject"].__setitem__("key", "not-a-key"),
    ),
    (
        "malformed digest",
        lambda p: p["base"].__setitem__("capability_set_digest", "short"),
    ),
    ("negative subject count", lambda p: p["summary"].__setitem__("subjects", -1)),
    ("empty changes list", lambda p: p["subjects"][0].__setitem__("changes", [])),
    (
        "naming subjects without a complete analysis",
        lambda p: p["analysis_coverage"]["base"].__setitem__(
            "subjects_outside_analysis", [p["subjects"][0]["subject"]]
        ),
    ),
)


def _first_added(payload: dict) -> dict:
    for entry in payload["subjects"]:
        if entry["transition"] == "added":
            return entry
    raise AssertionError("sample delta lost its added subject")


@pytest.mark.parametrize(
    ("label", "mutate"), _SCHEMA_REJECTS, ids=[label for label, _ in _SCHEMA_REJECTS]
)
def test_the_committed_schema_rejects_stage_one_violations(label, mutate) -> None:
    """The artifact external consumers validate with must earn its description.

    Pydantic `model_validator` rules do not reach `model_json_schema()`, so a
    constraint that lives only in a validator is one an external tool never
    sees. Everything JSON Schema *can* express is pushed into the published
    file, and this asserts it — against the file, not the models.
    """

    payload = json.loads(DELTA_EXAMPLE.read_text(encoding="utf-8"))
    jsonschema.validate(payload, PAYLOAD_SCHEMA)
    mutate(payload)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, PAYLOAD_SCHEMA)


def test_the_schema_says_it_is_only_stage_one() -> None:
    """A consumer must be told what validating this file does not buy them.

    Recomputation rules — the digests, the rollups, the derived dimensions —
    cannot be expressed in JSON Schema at all. Publishing the file without
    saying so would let a tool believe it had checked them.
    """

    description = PAYLOAD_SCHEMA["description"]
    assert "two stages" in description.lower()
    assert "docs/capability-payload.md" in description
    spec = (REPO_ROOT / "docs/capability-payload.md").read_text(encoding="utf-8")
    assert "Stage one" in spec and "Stage two" in spec
    for rule in (
        "subject-key derivation",
        "state-digest verification",
        "cross-row uniqueness",
    ):
        assert rule in description, f"the schema description must name {rule!r}"


def test_spec_page_documents_every_exclusion() -> None:
    """An exclusion nobody can read about is not a documented exclusion."""

    spec = (REPO_ROOT / "docs/capability-payload.md").read_text(encoding="utf-8")
    for field in (*UNPUBLISHED_FACT_FIELDS, *UNPUBLISHED_LOCK_FIELDS):
        assert field in spec, (
            f"docs/capability-payload.md must say why {field!r} is not published"
        )


def test_spec_page_names_the_schema_and_both_consumers() -> None:
    spec = (REPO_ROOT / "docs/capability-payload.md").read_text(encoding="utf-8")
    assert CAPABILITY_PAYLOAD_SCHEMA_VERSION in spec
    assert "capability-payload-schema.v1.json" in spec
    for issue in ("issues/470", "issues/474"):
        assert issue in spec, (
            "the spec must name both surfaces that consume this payload, so "
            "neither can define a second payload shape unnoticed"
        )
