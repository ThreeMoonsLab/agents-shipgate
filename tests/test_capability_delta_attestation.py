"""``shipgate.capability_delta_attestation/v1`` — the exported delta (#470).

The claim this surface makes is that a consumer can read *what an agent can do
after a change* without running Agents Shipgate. These tests hold the five
properties that claim is made of:

* **One projection.** The attestation is exactly the projection of the two
  capability locks the same run publishes, and the number it reports is the
  number the PR comment prints. The delta is computed once (#433).
* **The envelope binds.** The attested subject and the payload name the same
  tree, and an attestation that says otherwise is rejected — so a valid
  statement cannot be relabelled onto another commit.
* **The reference verifier is real.** It accepts the shipped example and a live
  emission, rejects every tampering we can construct, and its independent
  re-implementation of the payload's semantics agrees with the package's.
* **Nothing is silently absent.** A tool added but never bound reaches the
  attestation through ``analysis_coverage.newly_outside_analysis`` (#437), and
  a side the run could not establish says ``unavailable`` rather than nothing.
* **The format is discoverable.** The predicate type, both schema versions and
  the artifact are on the published contract, and the docs and the reference
  verifier agree about what a passing run establishes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from agents_shipgate.cli.capability import build_capability_lock_from_config
from agents_shipgate.cli.fixture import materialize_git_pr_fixture
from agents_shipgate.cli.verify.orchestrator import run_verify
from agents_shipgate.core.capability_attestation import (
    ObservedSubject,
    coverage_from_scan,
    project_capability_delta_attestation,
)
from agents_shipgate.core.capability_lock import diff_capability_locks
from agents_shipgate.core.capability_payload import project_capability_delta
from agents_shipgate.schemas.capabilities import CapabilityLockFileV1
from agents_shipgate.schemas.capability_attestation import (
    CAPABILITY_DELTA_ATTESTATION_ARTIFACT_KEY,
    CAPABILITY_DELTA_ATTESTATION_FILENAME,
    CAPABILITY_DELTA_ATTESTATION_SCHEMA_PATH,
    CAPABILITY_DELTA_ATTESTATION_SCHEMA_VERSION,
    CAPABILITY_DELTA_ATTESTATION_SPEC_PATH,
    CAPABILITY_DELTA_PREDICATE_TYPE,
    IN_TOTO_STATEMENT_TYPE,
    CapabilityDeltaAttestationV1,
    CapabilityDeltaVerificationRef,
    attestation_json,
    render_attestation_json,
)
from agents_shipgate.schemas.capability_payload import (
    ACTION_EFFECT_RANK,
    CAPABILITY_DIGEST_DIMENSIONS,
    CAPABILITY_PAYLOAD_SCHEMA_VERSION,
    PERMISSION_CLASS_RANK,
    REVERSIBILITY_RANK,
    CapabilityAnalysisCoverage,
    CapabilityChangeKind,
    published_semantic_shift,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLES = REPO_ROOT / "samples"
REFUND_SAMPLE = SAMPLES / "ai_generated_refund_pr"
EXAMPLE = REPO_ROOT / "docs/examples/capability-delta-attestation.v1.example.json"
VERIFIER_SCRIPT = REPO_ROOT / "tools/verify-capability-delta.py"
SPEC_PAGE = REPO_ROOT / CAPABILITY_DELTA_ATTESTATION_SPEC_PATH

TREE_A = "a" * 40
TREE_B = "b" * 40


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _facts(config: Path) -> list[Any]:
    return build_capability_lock_from_config(
        config=config, no_plugins=True, verbose=False
    ).capabilities


def _refund_fact_pair(tmp_path: Path) -> tuple[list[Any], list[Any]]:
    base = _facts(REFUND_SAMPLE / "shipgate.yaml")
    head_root = tmp_path / "head"
    shutil.copytree(REFUND_SAMPLE, head_root)
    shutil.copyfile(head_root / "_head" / "tools.json", head_root / "tools.json")
    return base, _facts(head_root / "shipgate.yaml")


def _attestation(tmp_path: Path, **overrides: Any) -> CapabilityDeltaAttestationV1:
    base, head = _refund_fact_pair(tmp_path)
    kwargs: dict[str, Any] = {
        "subject_name": "samples/ai_generated_refund_pr",
        "base_tree_sha": TREE_A,
        "head_tree_sha": TREE_B,
        "head_commit_sha": None,
    }
    kwargs.update(overrides)
    return project_capability_delta_attestation(base, head, **kwargs)


def _run_reference_verifier(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VERIFIER_SCRIPT), str(path), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _verify_refund_pr(tmp_path: Path, *, archive_head: bool = True) -> Path:
    """Run the shipped refund-PR fixture end to end; return its reports dir."""

    target = tmp_path / "refund_pr"
    shutil.copytree(REFUND_SAMPLE, target)
    head_tools = (target / "_head" / "tools.json").read_text(encoding="utf-8")
    shutil.rmtree(target / "_head")
    materialize_git_pr_fixture(
        target,
        head_files={"tools.json": head_tools},
        user_email="fixture@example.com",
        user_name="Agents Shipgate Fixture",
        base_commit_message="base",
        head_commit_message="head adds a refund tool",
    )
    out = target / "reports"
    run_verify(
        workspace=target,
        config=Path("shipgate.yaml"),
        base="origin/main",
        head="HEAD",
        archive_head=archive_head,
        out=out,
        ci_mode="advisory",
        fail_on=None,
        baseline=None,
        baseline_mode="new-findings",
        diff_from=None,
        policy_packs=None,
        plugins_enabled=False,
        strict_plugins=False,
        suggest_patches=False,
        no_heuristics=False,
        verbose=False,
        pr_comment_style="capability-review",
    )
    return out


@pytest.fixture(autouse=True)
def _neutral_cli_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the variables that retarget emitted commands and identity."""

    for name in (
        "AGENTS_SHIPGATE_CLI",
        "AGENTS_SHIPGATE_ENABLE_PLUGINS",
        "CLAUDECODE",
        "CURSOR_TRACE_ID",
        "GITHUB_ACTIONS",
        "GITHUB_EVENT_NAME",
        "EVENT_NAME",
        "EVALUATED_HEAD_SHA",
    ):
        monkeypatch.delenv(name, raising=False)


# --------------------------------------------------------------------------
# The envelope
# --------------------------------------------------------------------------


def test_the_wire_constants_are_the_ones_the_models_pin() -> None:
    """A ``Literal`` and the constant beside it are two spellings of one value.

    They drift silently: nothing in Python compares them, and a typo in either
    ships a payload no consumer can switch on.
    """

    fields = CapabilityDeltaAttestationV1.model_fields
    assert get_args(fields["statement_type"].annotation) == (IN_TOTO_STATEMENT_TYPE,)
    assert get_args(fields["predicate_type"].annotation) == (CAPABILITY_DELTA_PREDICATE_TYPE,)
    predicate = fields["predicate"].annotation.model_fields
    assert get_args(predicate["predicate_schema_version"].annotation) == (
        CAPABILITY_DELTA_ATTESTATION_SCHEMA_VERSION,
    )
    assert get_args(predicate["capability_payload_schema_version"].annotation) == (
        CAPABILITY_PAYLOAD_SCHEMA_VERSION,
    )


def test_the_attested_subject_must_be_the_state_the_delta_describes(tmp_path: Path) -> None:
    """Relabelling the subject is the cheapest attack on an unsigned statement.

    Four characters of ``subject[0].digest.gitTree`` would otherwise move a
    valid delta onto a commit it never reviewed.
    """

    attestation = _attestation(tmp_path)
    payload = attestation_json(attestation)
    payload["subject"][0]["digest"]["gitTree"] = "c" * 40
    with pytest.raises(ValidationError, match="not the state the delta describes"):
        CapabilityDeltaAttestationV1.model_validate(payload)


def test_a_statement_names_exactly_one_subject(tmp_path: Path) -> None:
    attestation = _attestation(tmp_path)
    payload = attestation_json(attestation)
    payload["subject"] = [*payload["subject"], payload["subject"][0]]
    with pytest.raises(ValidationError, match="exactly one subject"):
        CapabilityDeltaAttestationV1.model_validate(payload)
    payload["subject"] = []
    with pytest.raises(ValidationError, match="exactly one subject"):
        CapabilityDeltaAttestationV1.model_validate(payload)


def test_both_refs_must_be_git_object_ids(tmp_path: Path) -> None:
    """The payload allows any opaque label; this surface does not.

    ``ref`` is documented as a caller label — a path, a commit — so nothing in
    the payload stops an attestation naming ``"my-branch"`` as the state it
    attests. A consumer cannot fetch that.
    """

    attestation = _attestation(tmp_path)
    payload = attestation_json(attestation)
    payload["predicate"]["delta"]["base"]["ref"] = "origin/main"
    with pytest.raises(ValidationError, match="must be the git tree object id"):
        CapabilityDeltaAttestationV1.model_validate(payload)


def test_two_refs_naming_one_tree_must_carry_an_empty_delta(tmp_path: Path) -> None:
    """A populated delta over one tree is a relabelled delta.

    Reviewing the same content twice is legitimate — a branch that reverts
    itself — but nothing can have moved, so the combination that says otherwise
    is refused rather than published.
    """

    base, head = _refund_fact_pair(tmp_path)
    with pytest.raises(ValidationError, match="must be empty"):
        project_capability_delta_attestation(
            base,
            head,
            subject_name="s",
            base_tree_sha=TREE_A,
            head_tree_sha=TREE_A,
            head_commit_sha=None,
        )
    # The empty case over one tree is accepted, because it states nothing false.
    same = project_capability_delta_attestation(
        base,
        base,
        subject_name="s",
        base_tree_sha=TREE_A,
        head_tree_sha=TREE_A,
        head_commit_sha=None,
    )
    assert same.predicate.delta.subjects == ()


def test_the_digest_set_follows_in_toto_rather_than_this_format(tmp_path: Path) -> None:
    """``DigestSet`` is in-toto's type: absent, never ``null``.

    Every other absence in this schema is spelled as a value, deliberately. This
    one object is the exception because the type is not ours — in-toto types it
    ``map<string, string>``, and a consumer that reads it as such chokes on a
    ``null``. The published schema has to agree, or an external validator would
    accept a document this format never produces.
    """

    import jsonschema

    attestation = _attestation(tmp_path, head_commit_sha=None)
    payload = attestation_json(attestation)
    assert payload["subject"][0]["digest"] == {"gitTree": TREE_B}

    with_commit = attestation_json(_attestation(tmp_path / "c", head_commit_sha="e" * 40))
    assert with_commit["subject"][0]["digest"] == {
        "gitTree": TREE_B,
        "gitCommit": "e" * 40,
    }

    schema = json.loads(
        (REPO_ROOT / CAPABILITY_DELTA_ATTESTATION_SCHEMA_PATH).read_text(encoding="utf-8")
    )
    validator = jsonschema.Draft202012Validator(schema)
    validator.validate(payload)
    nulled = json.loads(json.dumps(payload))
    nulled["subject"][0]["digest"]["gitCommit"] = None
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(nulled)


def test_a_stale_attestation_does_not_survive_a_later_run(tmp_path: Path) -> None:
    """It names an ``input_set_id``, so a stale one offers a dead chain.

    A later run into the same reports directory must clear it with the rest of
    the identity-bearing set, or a consumer finds an attestation for one review
    beside another review's receipt and has no way to tell.
    """

    from agents_shipgate.cli._artifact_lifecycle import (
        VERIFIER_ROUTE_ARTIFACT_NAMES,
        clear_verifier_route_artifacts,
    )

    assert CAPABILITY_DELTA_ATTESTATION_FILENAME in VERIFIER_ROUTE_ARTIFACT_NAMES
    out = tmp_path / "reports"
    out.mkdir()
    stale = out / CAPABILITY_DELTA_ATTESTATION_FILENAME
    stale.write_text("{}", encoding="utf-8")
    clear_verifier_route_artifacts(out)
    assert not stale.exists()


@pytest.mark.parametrize(
    ("status", "input_set_id", "subject_id"),
    [
        ("bound", None, "sha256:" + "0" * 64),
        ("bound", "sha256:" + "0" * 64, None),
        ("unbound", "sha256:" + "0" * 64, None),
        ("unbound", None, "sha256:" + "0" * 64),
    ],
)
def test_a_receipt_binding_is_all_or_nothing(
    status: str, input_set_id: str | None, subject_id: str | None
) -> None:
    """A partial chain is one a consumer cannot follow, so it cannot be stated."""

    with pytest.raises(ValidationError):
        CapabilityDeltaVerificationRef(
            status=status,  # type: ignore[arg-type]
            input_set_id=input_set_id,
            subject_id=subject_id,
        )


def test_the_predicate_carries_the_payload_unchanged(tmp_path: Path) -> None:
    """No second payload shape: the predicate embeds the frozen delta verbatim."""

    base, head = _refund_fact_pair(tmp_path)
    attestation = project_capability_delta_attestation(
        base,
        head,
        subject_name="s",
        base_tree_sha=TREE_A,
        head_tree_sha=TREE_B,
        head_commit_sha=None,
    )
    direct = project_capability_delta(base, head, base_ref=TREE_A, head_ref=TREE_B)
    assert attestation.predicate.delta == direct


# --------------------------------------------------------------------------
# Coverage — the #437 axis
# --------------------------------------------------------------------------


def test_a_tool_that_is_observed_but_never_analysed_is_named(tmp_path: Path) -> None:
    """The #437 row: an added-but-unbound tool must not be silently absent.

    It produces no capability fact, so a delta built from facts alone reports
    that nothing changed. Coverage is the axis that says otherwise, and it
    names the subject rather than counting it (#433).
    """

    base, head = _refund_fact_pair(tmp_path)
    agent_id = base[0].identity.agent_id
    observed_base = [
        ObservedSubject(
            tool_id=fact.identity.tool_id,
            name=fact.identity.tool_name,
            provider=fact.identity.provider,
        )
        for fact in base
    ]
    arrived = ObservedSubject(
        tool_id="tool_v2_unbound", name="delete_repository", provider="github"
    )
    base_coverage = coverage_from_scan(
        agent_id=agent_id, observed=observed_base, analysed=base
    )
    head_coverage = coverage_from_scan(
        agent_id=agent_id,
        observed=[
            *(
                ObservedSubject(
                    tool_id=fact.identity.tool_id,
                    name=fact.identity.tool_name,
                    provider=fact.identity.provider,
                )
                for fact in head
            ),
            arrived,
        ],
        analysed=head,
    )
    assert base_coverage.subjects_outside_analysis == ()
    assert [row.name for row in head_coverage.subjects_outside_analysis] == [
        "delete_repository"
    ]

    attestation = project_capability_delta_attestation(
        base,
        head,
        subject_name="s",
        base_tree_sha=TREE_A,
        head_tree_sha=TREE_B,
        head_commit_sha=None,
        base_analysis_coverage=base_coverage,
        head_analysis_coverage=head_coverage,
    )
    coverage = attestation.predicate.delta.analysis_coverage
    assert coverage.status == "complete"
    assert [row.name for row in coverage.newly_outside_analysis] == ["delete_repository"]
    assert coverage.no_longer_outside_analysis == ()


def test_a_pre_existing_unbound_tool_is_not_a_newly_outside_row(tmp_path: Path) -> None:
    """"Added and unbound" and "unbound since before" are different facts.

    Only the first is something a reviewer of *this* change must act on, and a
    single coverage snapshot could not tell them apart.
    """

    base, head = _refund_fact_pair(tmp_path)
    agent_id = base[0].identity.agent_id
    standing = ObservedSubject(
        tool_id="tool_v2_standing", name="legacy_export", provider="github"
    )

    def observed(facts: list[Any]) -> list[ObservedSubject]:
        return [
            *(
                ObservedSubject(
                    tool_id=fact.identity.tool_id,
                    name=fact.identity.tool_name,
                    provider=fact.identity.provider,
                )
                for fact in facts
            ),
            standing,
        ]

    attestation = project_capability_delta_attestation(
        base,
        head,
        subject_name="s",
        base_tree_sha=TREE_A,
        head_tree_sha=TREE_B,
        head_commit_sha=None,
        base_analysis_coverage=coverage_from_scan(
            agent_id=agent_id, observed=observed(base), analysed=base
        ),
        head_analysis_coverage=coverage_from_scan(
            agent_id=agent_id, observed=observed(head), analysed=head
        ),
    )
    coverage = attestation.predicate.delta.analysis_coverage
    assert [row.name for row in coverage.head.subjects_outside_analysis] == ["legacy_export"]
    assert coverage.newly_outside_analysis == ()


def test_an_unestablished_side_is_unavailable_and_names_nothing() -> None:
    """"We did not look" must not be writable as "we looked and found none"."""

    from agents_shipgate.schemas.capability_payload import (
        CapabilityCoverageDelta,
        CapabilitySubjectRef,
        subject_key,
    )

    unavailable = CapabilityAnalysisCoverage(
        status="unavailable", subjects_outside_analysis=()
    )
    complete = CapabilityAnalysisCoverage(status="complete", subjects_outside_analysis=())
    delta = CapabilityCoverageDelta.of(unavailable, complete)
    assert delta.status == "unavailable"
    assert delta.newly_outside_analysis == ()
    assert delta.no_longer_outside_analysis == ()

    named = CapabilitySubjectRef(
        key=subject_key(agent="a", provider="p", tool_id="t"),
        name="delete_repository",
        agent="a",
        provider="p",
        tool_id="t",
    )
    with pytest.raises(ValidationError, match="requires having looked"):
        CapabilityAnalysisCoverage(
            status="unavailable", subjects_outside_analysis=(named,)
        )
    # And the transition stays empty when only one side was established, so a
    # consumer never reads "nothing newly arrived unanalysed" off half a
    # comparison.
    one_sided = CapabilityCoverageDelta.of(
        unavailable,
        CapabilityAnalysisCoverage(
            status="complete", subjects_outside_analysis=(named,)
        ),
    )
    assert one_sided.status == "unavailable"
    assert one_sided.newly_outside_analysis == ()


def test_a_catalog_provider_is_keyed_the_way_a_capability_subject_is() -> None:
    """``my api`` in the catalog is ``my_api`` at the capability layer.

    Keying a coverage row off the catalog spelling would put a *fully analysed*
    tool in ``subjects_outside_analysis`` under a key nothing else uses — the
    two-spellings-of-one-subject defect ``catalog_label_index`` exists to stop.
    """

    from agents_shipgate.core.lenses.action_surface import _provider
    from agents_shipgate.core.surface_exclusions import provider_token

    class _Tool:
        provider = ""
        source_id = "my api"
        source_type = "mcp"

    assert _provider(_Tool(), None) == provider_token("my api") == "my_api"


# --------------------------------------------------------------------------
# The reference verifier
# --------------------------------------------------------------------------


def test_the_reference_verifier_accepts_the_shipped_example() -> None:
    result = _run_reference_verifier(EXAMPLE)
    assert result.returncode == 0, result.stderr
    assert "VALID" in result.stdout
    assert "Unsigned" in result.stdout


def test_the_reference_verifier_imports_nothing_of_ours() -> None:
    """A verifier that needed our package would not be an interchange format.

    Checked on the source rather than by running it: a run under this repo's
    interpreter would import successfully whatever the script says.
    """

    source = VERIFIER_SCRIPT.read_text(encoding="utf-8")
    assert "agents_shipgate" not in source.replace("agents-shipgate", "")
    imports = set(re.findall(r"^\s*(?:from|import)\s+([A-Za-z_][\w.]*)", source, re.M))
    third_party = imports - {
        "__future__",
        "argparse",
        "hashlib",
        "json",
        "sys",
        "pathlib",
        "typing",
        # Optional, and only to run JSON Schema stage one when it is installed.
        "jsonschema",
    }
    assert not third_party, f"the reference verifier imports {sorted(third_party)}"


@pytest.mark.parametrize(
    ("name", "pointer", "value", "rule"),
    [
        ("subject relabelled", ("subject", 0, "digest", "gitTree"), "f" * 40, "E7"),
        (
            "summary inflated",
            ("predicate", "delta", "summary", "added_subjects"),
            5,
            "P4",
        ),
        (
            "subject key forged",
            ("predicate", "delta", "subjects", 0, "subject", "key"),
            "capsubj_0123456789abcdef",
            "P1",
        ),
        (
            "presence flipped",
            ("predicate", "delta", "subjects", 0, "present_in_base"),
            True,
            "P3",
        ),
        (
            "coverage status downgraded",
            ("predicate", "delta", "analysis_coverage", "status"),
            "complete",
            "P7",
        ),
        (
            "binding forged",
            ("predicate", "verification", "status"),
            "bound",
            "E9",
        ),
    ],
)
def test_the_reference_verifier_rejects_a_tampered_payload(
    tmp_path: Path,
    name: str,
    pointer: tuple[Any, ...],
    value: Any,
    rule: str,
) -> None:
    """Every derived value is recomputed, so a single edit cannot stand alone."""

    document = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    cursor: Any = document
    for step in pointer[:-1]:
        cursor = cursor[step]
    cursor[pointer[-1]] = value
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    result = _run_reference_verifier(path)
    assert result.returncode == 1, f"{name} was accepted"
    assert f"[{rule}]" in result.stderr, result.stderr


def test_the_reference_verifier_rejects_an_escalated_effect(tmp_path: Path) -> None:
    """The strongest tamper: change what the capability *is*, not what it says.

    Nothing in the envelope notices; the derived direction does, because the
    payload publishes both records and the direction is recomputed from them.
    """

    document = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    entry = document["predicate"]["delta"]["subjects"][1]["changes"][0]
    entry["after"]["effect"]["effect"] = "destructive"
    path = tmp_path / "escalated.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    result = _run_reference_verifier(path)
    assert result.returncode == 1
    assert "[P6]" in result.stderr


def test_the_reference_verifier_rejects_a_mismatched_subject(tmp_path: Path) -> None:
    """The consumer's own question: is this attestation about *my* commit?"""

    ok = _run_reference_verifier(EXAMPLE, "--expect-tree", _example_tree())
    assert ok.returncode == 0, ok.stderr

    mismatched = _run_reference_verifier(EXAMPLE, "--expect-tree", "d" * 40)
    assert mismatched.returncode == 1
    assert "[expect-tree]" in mismatched.stderr


def test_the_reference_verifier_can_require_a_receipt_binding() -> None:
    """The shipped example is honestly ``unbound``; a consumer may refuse that."""

    result = _run_reference_verifier(EXAMPLE, "--require-receipt-binding")
    assert result.returncode == 1
    assert "[require-receipt-binding]" in result.stderr


@pytest.mark.parametrize(
    "document",
    [
        5,
        [],
        {"subject": 5},
        {"subject": ["not-a-descriptor"]},
        {"subject": [{"digest": "not-a-map"}]},
        {"predicate": []},
    ],
    ids=lambda value: repr(value)[:24],
)
def test_the_reference_verifier_reports_malformed_input_as_rules(
    tmp_path: Path, document: object
) -> None:
    """A published verifier must never answer a bad file with a traceback.

    ``subject: 5`` did: the consumer-supplied ``--expect-*`` checks indexed the
    subject list after ``verify`` had already collected the real problems, so a
    ``TypeError`` replaced the rule list the caller was going to read.
    """

    path = tmp_path / "malformed.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    result = _run_reference_verifier(
        path, "--expect-tree", "a" * 40, "--require-receipt-binding", "--json"
    )
    assert result.returncode == 1
    assert "Traceback" not in result.stderr, result.stderr
    payload = json.loads(result.stdout)
    assert payload["valid"] is False
    assert payload["problems"]


def test_the_reference_verifier_runs_json_schema_stage_one() -> None:
    """Stage one is optional but must actually run when it is available."""

    result = _run_reference_verifier(
        EXAMPLE, "--schema", str(REPO_ROOT / CAPABILITY_DELTA_ATTESTATION_SCHEMA_PATH), "--json"
    )
    payload = json.loads(result.stdout)
    assert payload["valid"] is True
    assert payload["stage_one"] == "ran", payload
    assert result.returncode == 0


def _example_tree() -> str:
    document = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    return document["subject"][0]["digest"]["gitTree"]


def _reference_module() -> Any:
    import importlib.util

    spec = importlib.util.spec_from_file_location("_ref_verifier", VERIFIER_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_reference_verifier_restates_the_published_tables_exactly() -> None:
    """An independent implementation is the point; a *divergent* one is a bug.

    The script cannot import these, so it carries copies. Nothing but this
    compares them, and a rank table that drifts makes the verifier reject
    honest attestations — the worst failure mode a published verifier has.
    """

    module = _reference_module()
    assert module.PERMISSION_CLASS_RANK == PERMISSION_CLASS_RANK
    assert module.ACTION_EFFECT_RANK == ACTION_EFFECT_RANK
    assert module.REVERSIBILITY_RANK == REVERSIBILITY_RANK
    assert tuple(module.DIGEST_DIMENSIONS) == CAPABILITY_DIGEST_DIMENSIONS
    assert module.IN_TOTO_STATEMENT_TYPE == IN_TOTO_STATEMENT_TYPE
    assert module.PREDICATE_TYPE == CAPABILITY_DELTA_PREDICATE_TYPE
    assert module.PREDICATE_SCHEMA_VERSION == CAPABILITY_DELTA_ATTESTATION_SCHEMA_VERSION
    assert module.PAYLOAD_SCHEMA_VERSION == CAPABILITY_PAYLOAD_SCHEMA_VERSION

    emitted_kinds = {kind for kind, *_ in module.SET_DIMENSIONS}
    emitted_kinds |= {kind for kind, *_ in module.OPAQUE_DIMENSIONS}
    emitted_kinds |= {
        "effect_changed",
        "effect_flag_changed",
        "reversibility_changed",
        "idempotency_evidence_changed",
        "permission_changed",
        "control_changed",
    }
    assert emitted_kinds == set(get_args(CapabilityChangeKind))


def test_the_reference_verifier_derives_what_the_package_derives(tmp_path: Path) -> None:
    """Two implementations of one specification, compared on real records.

    Restating the semantics in another file is only safe if something proves
    the two agree, and comparing them on the *unperturbed* pair proves almost
    nothing — so every published dimension is moved in turn and both sides are
    asked again.
    """

    module = _reference_module()
    base, head = _refund_fact_pair(tmp_path)
    delta = project_capability_delta(base, head, base_ref=TREE_A, head_ref=TREE_B)
    record = None
    for row in delta.subjects:
        for entry in row.changes:
            if entry.before is not None and entry.after is not None:
                record = entry.before
                break
    assert record is not None, "the refund sample must carry a paired change"

    perturbations: list[tuple[str, dict[str, Any]]] = [
        ("scope", {"scope": (*record.scope, "extra:scope")}),
        ("resource", {"resource": ()}),
        ("risk_tags", {"risk_tags": (*record.risk_tags, "financial_action")}),
        (
            "effect",
            {"effect": record.effect.model_copy(update={"effect": "destructive"})},
        ),
        (
            "reversibility",
            {"effect": record.effect.model_copy(update={"reversibility": "irreversible"})},
        ),
        (
            "flag",
            {"effect": record.effect.model_copy(update={"financial": True})},
        ),
        (
            "idempotency",
            {"effect": record.effect.model_copy(update={"idempotency_known": True})},
        ),
        (
            "control",
            {
                "controls": record.controls.model_copy(
                    update={"approval_required": True}
                )
            },
        ),
        (
            "authority",
            {
                "authority": record.authority.model_copy(
                    update={"scopes": ("payments:write",), "auth_type": "oauth"}
                )
            },
        ),
        ("operation", {"operation": f"{record.operation}.v2"}),
    ]
    for name, update in perturbations:
        after = record.model_copy(update=update)
        expected_direction, expected_changes = published_semantic_shift(record, after)
        got_direction, got_changes = module.semantic_shift(
            record.model_dump(mode="json"), after.model_dump(mode="json")
        )
        assert got_direction == expected_direction, name
        assert got_changes == [
            change.model_dump(mode="json") for change in expected_changes
        ], name

    # And the unperturbed pair, which must be `evidence_only` on both sides.
    assert module.semantic_shift(
        record.model_dump(mode="json"), record.model_dump(mode="json")
    ) == ("evidence_only", [])


def test_the_published_rule_table_matches_the_reference_verifier() -> None:
    """The docs page says what a passing run establishes; the script decides it.

    A rule dropped from the script and left on the page is a promise nothing
    keeps.
    """

    module = _reference_module()
    page = SPEC_PAGE.read_text(encoding="utf-8")
    for rule, statement in module.CHECKS:
        assert f"| `{rule}` |" in page, f"{rule} is not published on the spec page"
        assert statement, rule
    published = set(re.findall(r"^\| `([EP]\d+)` \|", page, re.M))
    assert published == {rule for rule, _ in module.CHECKS}
    assert f"{len(module.CHECKS)} rules checked" in page


# --------------------------------------------------------------------------
# The emitter
# --------------------------------------------------------------------------


def test_verify_writes_the_attestation_and_binds_it_to_the_receipt(tmp_path: Path) -> None:
    out = _verify_refund_pr(tmp_path)
    path = out / CAPABILITY_DELTA_ATTESTATION_FILENAME
    assert path.is_file(), "verify did not write the attestation"

    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["_type"] == IN_TOTO_STATEMENT_TYPE
    assert document["predicateType"] == CAPABILITY_DELTA_PREDICATE_TYPE

    plan = json.loads((out / "verification-plan.json").read_text(encoding="utf-8"))
    verification = document["predicate"]["verification"]
    assert verification["status"] == "bound"
    assert verification["input_set_id"] == plan["inputs"]["input_set_id"]
    assert verification["subject_id"] == plan["subject"]["subject_id"]

    git = plan["subject"]["git"]
    assert document["subject"][0]["digest"]["gitTree"] == git["head_tree_sha"]
    assert document["subject"][0]["digest"]["gitCommit"] == git["head_commit_sha"]
    assert document["subject"][0]["name"] == git["repository_id"]
    assert document["predicate"]["delta"]["base"]["ref"] == git["base_tree_sha"]

    # It is a first-class artifact, so the run's own manifest covers it.
    verifier = json.loads((out / "verifier.json").read_text(encoding="utf-8"))
    assert CAPABILITY_DELTA_ATTESTATION_ARTIFACT_KEY in verifier["artifacts"]
    manifest = json.loads((out / "verification-artifacts.json").read_text(encoding="utf-8"))
    entry = manifest["artifacts"][CAPABILITY_DELTA_ATTESTATION_ARTIFACT_KEY]
    assert entry["path"] == CAPABILITY_DELTA_ATTESTATION_FILENAME
    assert entry["sha256"] == "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()

    result = _run_reference_verifier(
        path, "--expect-tree", git["head_tree_sha"], "--require-receipt-binding"
    )
    assert result.returncode == 0, result.stderr


def test_every_artifact_a_verify_run_binds_is_inside_the_authorization_closure(
    tmp_path: Path,
) -> None:
    """A new verify artifact breaks every authorized push until it is allowed.

    ``_AUTHORIZATION_RECEIPT_ARTIFACTS`` is deny-by-default, which is right for
    a security boundary and makes it a second copy of what ``verify`` writes.
    Adding the attestation to the receipt manifest without adding it there made
    ``authorization execute`` exit 3 on a valid grant — a failure whose message
    named the new artifact but whose cause was two lists that do not co-vary.
    Nothing compared them, so nothing failed until an unrelated test did.
    """

    from agents_shipgate.cli.authorization import _AUTHORIZATION_RECEIPT_ARTIFACTS

    out = _verify_refund_pr(tmp_path)
    receipt = json.loads((out / "verification-receipt.json").read_text(encoding="utf-8"))
    bound = set(receipt["artifact_manifest"]["artifacts"])
    assert CAPABILITY_DELTA_ATTESTATION_ARTIFACT_KEY in bound
    outside = sorted(bound - _AUTHORIZATION_RECEIPT_ARTIFACTS)
    assert not outside, (
        "verify binds artifacts the authorization execution closure does not "
        f"allow: {outside}. Add them to _AUTHORIZATION_RECEIPT_ARTIFACTS or "
        "stop binding them."
    )


def test_a_worktree_run_writes_no_attestation_and_says_why(tmp_path: Path) -> None:
    """A worktree snapshot scanned bytes that are in no tree object.

    Publishing its head tree id as "what was reviewed" would attest content
    nobody can fetch, so the run withholds the artifact — and says so, rather
    than leaving a consumer to wonder whether the delta was empty.
    """

    out = _verify_refund_pr(tmp_path, archive_head=False)
    assert not (out / CAPABILITY_DELTA_ATTESTATION_FILENAME).exists()
    verifier = json.loads((out / "verifier.json").read_text(encoding="utf-8"))
    assert any(
        "Capability delta attestation not written" in note
        for note in verifier["base_notes"]
    ), verifier["base_notes"]
    assert CAPABILITY_DELTA_ATTESTATION_ARTIFACT_KEY not in verifier["artifacts"]


def test_verify_populates_coverage_on_both_sides(tmp_path: Path) -> None:
    """``not_requested`` in a real emission would make #437 unanswerable."""

    out = _verify_refund_pr(tmp_path)
    document = json.loads(
        (out / CAPABILITY_DELTA_ATTESTATION_FILENAME).read_text(encoding="utf-8")
    )
    coverage = document["predicate"]["delta"]["analysis_coverage"]
    assert coverage["base"]["status"] == "complete"
    assert coverage["head"]["status"] == "complete"
    assert coverage["status"] == "complete"


def test_the_attestation_is_deterministic_for_one_input(tmp_path: Path) -> None:
    """Two exports of the same static inputs are byte-identical.

    The format carries no wall clock and no engine identity for exactly this
    reason: a consumer diffing two attestations must see only what moved.
    """

    first = render_attestation_json(_attestation(tmp_path / "a"))
    second = render_attestation_json(_attestation(tmp_path / "b"))
    assert first == second


# --------------------------------------------------------------------------
# One projection
# --------------------------------------------------------------------------


def test_the_attested_delta_is_the_projection_of_the_published_locks(
    tmp_path: Path,
) -> None:
    """The attestation is not a second computation of the delta.

    Re-projected from the two capability locks the same run publishes, byte for
    byte — so a reviewer reading ``capabilities.lock.json`` and a gateway
    reading the attestation are looking at one computation (#433).
    """

    out = _verify_refund_pr(tmp_path)
    document = json.loads(
        (out / CAPABILITY_DELTA_ATTESTATION_FILENAME).read_text(encoding="utf-8")
    )
    base_lock = CapabilityLockFileV1.model_validate_json(
        (out / "base.capabilities.lock.json").read_text(encoding="utf-8")
    )
    head_lock = CapabilityLockFileV1.model_validate_json(
        (out / "capabilities.lock.json").read_text(encoding="utf-8")
    )
    delta = document["predicate"]["delta"]
    reprojected = project_capability_delta(
        base_lock.capabilities,
        head_lock.capabilities,
        base_ref=delta["base"]["ref"],
        head_ref=delta["head"]["ref"],
    )
    projected = reprojected.model_dump(mode="json")
    # Coverage is supplied by the run, not derivable from facts, so compare
    # everything else and the coverage separately.
    for side in ("base", "head"):
        projected[side]["analysis_coverage_digest"] = delta[side][
            "analysis_coverage_digest"
        ]
    projected["analysis_coverage"] = delta["analysis_coverage"]
    assert projected == delta


def test_the_attested_delta_equals_the_delta_the_pr_comment_prints(
    tmp_path: Path,
) -> None:
    """One value, two surfaces (#433).

    ``diff_capability_locks`` and ``project_capability_delta`` are the same
    engine, so the capability-change count the PR comment prints and the one
    the attestation publishes are the same number by construction — and this
    fails if either surface ever grows its own arithmetic.
    """

    out = _verify_refund_pr(tmp_path)
    document = json.loads(
        (out / CAPABILITY_DELTA_ATTESTATION_FILENAME).read_text(encoding="utf-8")
    )
    lock_diff = diff_capability_locks(
        CapabilityLockFileV1.model_validate_json(
            (out / "base.capabilities.lock.json").read_text(encoding="utf-8")
        ),
        CapabilityLockFileV1.model_validate_json(
            (out / "capabilities.lock.json").read_text(encoding="utf-8")
        ),
    )
    summary = lock_diff.summary
    moved = (
        summary.added
        + summary.removed
        + summary.reidentified
        + summary.changed
        + summary.evidence_changed
    )
    assert document["predicate"]["delta"]["summary"]["capability_changes"] == moved

    comment = (out / "pr-comment.md").read_text(encoding="utf-8")
    assert (
        f"Capability lock diff: +{summary.added}, -{summary.removed}, "
        f"{summary.changed} changed" in comment
    ), comment

    # The reader-facing subject total is the same number on both surfaces.
    match = re.search(r"Capability delta \(analysed surface\): (\d+) subjects", comment)
    assert match is not None, comment
    assert int(match.group(1)) == document["predicate"]["delta"]["summary"]["subjects"]


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


def test_the_contract_advertises_the_format() -> None:
    """Shipping an artifact agent-facing discovery does not mention is half a surface."""

    from agents_shipgate.schemas.contract import ARTIFACTS, build_contract_payload

    payload = build_contract_payload()
    assert payload.capability_payload_schema_version == CAPABILITY_PAYLOAD_SCHEMA_VERSION
    assert (
        payload.capability_delta_attestation_schema_version
        == CAPABILITY_DELTA_ATTESTATION_SCHEMA_VERSION
    )
    assert payload.capability_delta_predicate_type == CAPABILITY_DELTA_PREDICATE_TYPE
    assert payload.capability_delta_attestation_schema_path == (
        CAPABILITY_DELTA_ATTESTATION_SCHEMA_PATH
    )
    assert payload.capability_delta_attestation_artifact == ARTIFACTS[
        "capability_delta_attestation"
    ]
    assert ARTIFACTS["capability_delta_attestation"].endswith(
        CAPABILITY_DELTA_ATTESTATION_FILENAME
    )
    assert "capability_delta_attestation" in payload.external_integration_surfaces
    assert "capability_payload" in payload.external_integration_surfaces


def test_the_well_known_document_agrees_with_the_contract() -> None:
    well_known = json.loads(
        (REPO_ROOT / ".well-known/agents-shipgate.json").read_text(encoding="utf-8")
    )
    from agents_shipgate.schemas.contract import build_contract_payload

    payload = build_contract_payload()
    for field in (
        "capability_payload_schema_version",
        "capability_payload_schema_path",
        "capability_delta_attestation_schema_version",
        "capability_delta_attestation_schema_path",
        "capability_delta_predicate_type",
        "capability_delta_attestation_artifact",
        "contract_version",
    ):
        assert well_known[field] == getattr(payload, field), field
    assert well_known["schemas"]["capability_delta_attestation"].endswith(
        "capability-delta-attestation-schema.v1.json"
    )
    assert (
        well_known["artifacts"]["capability_delta_attestation"]
        == payload.capability_delta_attestation_artifact
    )


def test_the_emitter_needs_nothing_from_a_source_checkout(tmp_path: Path) -> None:
    """A tagged release install has no ``docs/`` and no ``samples/``.

    The attestation is produced from code and the run's own inputs, so it has
    to emit with the repository's non-packaged directories out of reach. Run in
    a subprocess with the current working directory somewhere else and the
    schema/spec files hidden, so a hidden read of either fails loudly.
    """

    out = _verify_refund_pr(tmp_path)
    document = json.loads(
        (out / CAPABILITY_DELTA_ATTESTATION_FILENAME).read_text(encoding="utf-8")
    )

    script = tmp_path / "emit.py"
    script.write_text(
        "\n".join(
            [
                "import json, sys",
                "from pathlib import Path",
                "from agents_shipgate.core.capability_attestation import (",
                "    project_capability_delta_attestation,",
                ")",
                "from agents_shipgate.schemas.capability_attestation import (",
                "    render_attestation_json,",
                ")",
                "from agents_shipgate.schemas.capabilities import CapabilityLockFileV1",
                "base = CapabilityLockFileV1.model_validate_json(",
                "    Path(sys.argv[1]).read_text(encoding='utf-8'))",
                "head = CapabilityLockFileV1.model_validate_json(",
                "    Path(sys.argv[2]).read_text(encoding='utf-8'))",
                "print(render_attestation_json(project_capability_delta_attestation(",
                "    base.capabilities, head.capabilities,",
                "    subject_name=sys.argv[3], base_tree_sha=sys.argv[4],",
                "    head_tree_sha=sys.argv[5], head_commit_sha=sys.argv[6] or None,",
                ")), end='')",
            ]
        ),
        encoding="utf-8",
    )
    delta = document["predicate"]["delta"]
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            str(out / "base.capabilities.lock.json"),
            str(out / "capabilities.lock.json"),
            document["subject"][0]["name"],
            delta["base"]["ref"],
            delta["head"]["ref"],
            document["subject"][0]["digest"]["gitCommit"] or "",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
    )
    assert result.returncode == 0, result.stderr
    emitted = json.loads(result.stdout)
    assert emitted["predicateType"] == CAPABILITY_DELTA_PREDICATE_TYPE
    assert emitted["predicate"]["delta"]["summary"] == delta["summary"]
