"""#410 §D — the coding-agent declaration loop.

Three surfaces, one rule. ``authorable_by`` says who may write the first draft
of an answer; the ``declare_action`` patch is that draft in machine-applicable
form; ``next_action.kind: confirm_declarations`` is the route that hands it to
an agent and names, by subject, what it must still ask a person for.

The rule is about content, never about who is running: an agent may propose
what the evidence supports, and only a human may assert against it. So every
test here is about what the scan filled in, and the adversarial ones are about
what happens when something tries to write past that.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.cli.scan import run_scan
from agents_shipgate.schemas.report import (
    REVIEW_REQUIRED_SENTINEL,
    EvidenceGapAction,
    ReadinessReport,
    template_is_complete,
)

runner = CliRunner()

_AGENT_SOURCE = '''
from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool


def send_email(to: str, body: str) -> dict:
    """Send an email."""
    return {"status": "sent"}


root_agent = LlmAgent(
    name="closer_agent",
    instruction="Route approvals.",
    tools=[FunctionTool(func=send_email)],
)
'''

_MANIFEST = """version: "0.1"
project:
  name: declaration-authoring
agent:
  name: closer-agent
  declared_purpose:
    - route approval mail
environment:
  target: local
tool_sources:
  - id: adk_agent
    type: google_adk
    path: agent.py
"""


def _project(root: Path, *, manifest: str = _MANIFEST) -> Path:
    project = root / "project"
    project.mkdir(exist_ok=True)
    (project / "agent.py").write_text(_AGENT_SOURCE, encoding="utf-8")
    (project / "shipgate.yaml").write_text(manifest, encoding="utf-8")
    return project / "shipgate.yaml"


def _scan(config: Path, out: Path) -> ReadinessReport:
    report, _ = run_scan(
        config_path=config,
        output_dir=out,
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    assert report.release_decision is not None
    return report


def _coverage(report: ReadinessReport):
    assert report.release_decision is not None
    return report.release_decision.evidence_coverage


def _gap(report: ReadinessReport, kind: str):
    rows = [gap for gap in _coverage(report).evidence_gaps if gap.kind == kind]
    assert rows, f"no {kind} row in {[g.kind for g in _coverage(report).evidence_gaps]}"
    return rows[0]


def _apply(report_path: Path, *, kinds: str | None = None) -> object:
    args = ["apply-patches", "--from", str(report_path), "--apply"]
    if kinds is not None:
        args += ["--kinds", kinds]
    return runner.invoke(app, args)


# --- authorable_by ----------------------------------------------------------


def test_a_filled_effect_row_is_the_agents_to_draft(tmp_path: Path) -> None:
    """The scan read this action; the row says an agent may write that down."""

    config = _project(tmp_path)
    report = _scan(config, tmp_path / "out")

    row = _gap(report, "inferred_effect_only").next_action
    assert row.authorable_by == "coding_agent"
    assert row.declaration_template is not None
    assert row.declaration_template["effect"] == "external_communication"
    assert row.suggested_patch_kind == "declare_action"
    assert row.patch is not None
    assert row.patch.selector["tool"] == "send_email"
    # The two increments compose without either knowing about the other: §D
    # writes the answer, §E pins it to the evidence that justified it, and the
    # patch is the whole template rather than the half this feature added.
    assert set(row.patch.declaration) == {"effect", "basis"}
    assert row.patch.declaration["effect"] == "external_communication"
    assert row.patch.declaration["basis"].startswith("confirmed:")
    assert {**row.patch.selector, **row.patch.declaration} == row.declaration_template


def test_a_row_with_a_blank_is_never_the_agents_to_draft(tmp_path: Path) -> None:
    """Authority is a fact about a deployment, and no repository holds it."""

    config = _project(tmp_path)
    report = _scan(config, tmp_path / "out")

    row = _gap(report, "missing_authority_evidence").next_action
    assert row.authorable_by == "human"
    assert row.suggested_patch_kind == "manual"
    assert row.patch is None
    assert REVIEW_REQUIRED_SENTINEL in json.dumps(row.declaration_template)


def test_no_published_row_carries_a_patch_that_writes_a_blank(tmp_path: Path) -> None:
    """The property that makes the whole route safe, checked on every row.

    Not on the one row this fixture happens to raise: an agent-authorable tag
    is a licence to write into the trust root, so the invariant is that *no*
    published patch anywhere carries a value a human still owes.
    """

    config = _project(tmp_path)
    report = _scan(config, tmp_path / "out")

    for gap in _coverage(report).evidence_gaps:
        patch = gap.next_action.patch
        if patch is None:
            continue
        payload = json.dumps(patch.model_dump(mode="json"))
        assert REVIEW_REQUIRED_SENTINEL not in payload, gap.kind
        assert gap.next_action.authorable_by == "coding_agent"


@pytest.mark.parametrize(
    "template",
    [
        {"tool": "send_email", "effect": REVIEW_REQUIRED_SENTINEL},
        {"tool": "send_email", "authority": {"mode": REVIEW_REQUIRED_SENTINEL}},
        {"tool": "send_email", "scopes": [REVIEW_REQUIRED_SENTINEL]},
        None,
        {},
    ],
)
def test_the_row_model_rejects_an_agent_tag_on_an_unfilled_template(template) -> None:
    """Enforced on the type, not on the one builder that sets it today."""

    with pytest.raises(ValidationError):
        EvidenceGapAction(
            kind="declare_action_effect",
            why="w",
            expects="e",
            authorable_by="coding_agent",
            declaration_template=template,
        )


def test_the_row_model_rejects_a_template_that_only_names_the_action() -> None:
    """A selector-only template has no blank left, and is still not an answer.

    It declares nothing, so the patch built from it would write no field while
    the question counted itself as one an agent can close.
    """

    with pytest.raises(ValidationError):
        EvidenceGapAction(
            kind="declare_action_effect",
            why="w",
            expects="e",
            authorable_by="coding_agent",
            declaration_template={"tool": "send_email", "source_id": "adk_agent"},
        )


def test_a_stale_report_does_not_silently_write_nothing(tmp_path: Path) -> None:
    """A no-op exit 0 would loop the agent against an unchanged file."""

    config = _project(tmp_path)
    _scan(config, tmp_path / "out")
    config.write_text(
        config.read_text(encoding="utf-8") + "\n# edited after the scan\n",
        encoding="utf-8",
    )
    before = config.read_text(encoding="utf-8")

    result = _apply(tmp_path / "out" / "report.json", kinds="declare_action")

    assert result.exit_code == 5, result.output
    assert config.read_text(encoding="utf-8") == before


def test_the_row_model_rejects_an_agent_tag_on_a_kind_no_scan_can_draft() -> None:
    """A complete-looking authority template is still not a scan's to write."""

    with pytest.raises(ValidationError):
        EvidenceGapAction(
            kind="declare_action_authority",
            why="w",
            expects="e",
            authorable_by="coding_agent",
            declaration_template={"tool": "send_email", "authority": {"mode": "none"}},
        )


def _row(**overrides):
    """An agent-authorable effect row with a patch that matches its template."""

    from agents_shipgate.schemas.patches import DeclareActionPatch

    template = overrides.pop(
        "declaration_template",
        {"tool": "send_email", "effect": "external_communication"},
    )
    patch_fields = {
        "target_path": "shipgate.yaml",
        "selector": {"tool": "send_email"},
        "declaration": {"effect": "external_communication"},
        "confidence": "high",
        "rationale": "r",
        "target_sha256": "0" * 64,
    }
    patch_fields.update(overrides.pop("patch_fields", {}))
    return EvidenceGapAction(
        kind="declare_action_effect",
        why="w",
        expects="e",
        authorable_by="coding_agent",
        declaration_template=template,
        suggested_patch_kind="declare_action",
        patch=DeclareActionPatch(**patch_fields),
        **overrides,
    )


def test_a_matching_patch_and_template_validate() -> None:
    """The positive case, so the rejections below are about what they name."""

    assert _row().patch is not None


@pytest.mark.parametrize(
    ("label", "overrides"),
    [
        # The patch must be the template, split — not a superset, not a
        # substitute. Each of these validated before the binding existed.
        ("writes a field the template never mentioned",
         {"patch_fields": {"declaration": {"effect": "external_communication", "risk_tags": ["destructive"]}}}),
        ("writes a different effect",
         {"patch_fields": {"declaration": {"effect": "read"}}}),
        ("writes an authority block instead",
         {"patch_fields": {"declaration": {"authority": {"mode": "none"}}}}),
        ("names a different action",
         {"patch_fields": {"selector": {"tool": "other_tool"}}}),
        ("smuggles an identifying key into the written half",
         {"declaration_template": {"tool": "send_email", "tool_id": "t1", "effect": "external_communication"},
          "patch_fields": {"selector": {"tool": "send_email"},
                           "declaration": {"tool_id": "t1", "effect": "external_communication"}}}),
        ("writes an effect outside the vocabulary",
         {"declaration_template": {"tool": "send_email", "effect": "exfiltrate"},
          "patch_fields": {"declaration": {"effect": "exfiltrate"}}}),
        ("is published below high confidence",
         {"patch_fields": {"confidence": "medium"}}),
    ],
)
def test_the_row_model_binds_the_patch_to_the_template(label, overrides) -> None:
    """A tag that says "evidence-derived" cannot sit beside a patch that is not.

    ``authorable_by`` is what ``fix_task`` reads to authorize the route, and
    ``patch`` is what gets written. Validating them independently left the two
    free to disagree on a payload a consumer rehydrated.
    """

    with pytest.raises(ValidationError):
        _row(**overrides)


def test_a_drafted_declaration_is_never_weaker_than_an_observation() -> None:
    """``read`` is the one reading a scan may not assert for itself (#357).

    The full covering relation lives in ``core.semantic_assessment`` and cannot
    be imported by a schema module; this is the direction where accepting the
    weaker answer loses safety rather than over-declaring, so it is the one the
    model checks itself.
    """

    from agents_shipgate.schemas.report import EvidenceReading

    readings = [EvidenceReading(effect="external_communication", observed=True)]
    # Escalating past the observation is fine — that is the safe direction.
    _row(
        declaration_template={"tool": "send_email", "effect": "destructive"},
        patch_fields={"declaration": {"effect": "destructive"}},
        observed_readings=readings,
    )
    with pytest.raises(ValidationError):
        _row(
            declaration_template={"tool": "send_email", "effect": "read"},
            patch_fields={"declaration": {"effect": "read"}},
            observed_readings=readings,
        )
    # An unobserved reading — a protocol default standing in for the absence of
    # evidence — is not an observation, and must not block the answer.
    _row(
        declaration_template={"tool": "send_email", "effect": "read"},
        patch_fields={"declaration": {"effect": "read"}},
        observed_readings=[EvidenceReading(effect="write", observed=False)],
    )


_STALE_PIN = """action_surface:
  actions:
    - tool: send_email
      source_id: adk_agent
      effect: external_communication
      basis: confirmed:000000000000
"""


def test_a_real_drift_row_is_published_human_owned(tmp_path: Path) -> None:
    """The two increments composed, on a scan rather than a constructed row.

    A declaration pinned to evidence that has since moved re-opens as
    ``declaration_drift`` (#410 §E) with a *complete* template — it restates
    the answer beside the new pin — and a repair spelled
    ``declare_action_effect``. Everything the content rule reads says
    "drafted"; only the gap kind says otherwise, so this is the case that
    proves the builder consults it.
    """

    config = _project(tmp_path, manifest=_MANIFEST + _STALE_PIN)
    report = _scan(config, tmp_path / "out")

    drift = _gap(report, "declaration_drift").next_action
    assert drift.kind == "declare_action_effect"
    assert template_is_complete(drift.declaration_template)
    assert drift.authorable_by == "human"
    assert drift.patch is None
    assert drift.suggested_patch_kind == "manual"


def test_a_drift_row_is_never_drafted(tmp_path: Path) -> None:
    """A question asking a person to look again is not answerable by a scan.

    ``declaration_drift`` restates a confirmed answer beside the pin that
    moved, so its template is complete and its repair is spelled
    ``declare_action_effect`` — everything the content rule looks at says
    "drafted". Only the *gap* kind carries the distinction, and an agent
    re-stamping the pin would close the request the row exists to make (#410
    §E).
    """

    from agents_shipgate.schemas.report import EvidenceGap

    drifted = EvidenceGapAction(
        kind="declare_action_effect",
        why="w",
        expects="e",
        authorable_by="coding_agent",
        declaration_template={
            "tool": "send_email",
            "effect": "external_communication",
            "basis": "confirmed:0123456789ab",
        },
    )
    with pytest.raises(ValidationError):
        EvidenceGap(
            kind="declaration_drift",
            subject="send_email [adk_agent]",
            why="the evidence behind this answer moved",
            next_action=drifted,
        )
    # The same action on a question that *is* the scan's to answer is fine.
    EvidenceGap(
        kind="inferred_effect_only",
        subject="send_email [adk_agent]",
        why="w",
        next_action=drifted,
    )


def test_the_row_model_rejects_a_patch_on_a_human_owned_row() -> None:
    from agents_shipgate.schemas.patches import DeclareActionPatch

    patch = DeclareActionPatch(
        target_path="shipgate.yaml",
        selector={"tool": "send_email"},
        declaration={"effect": "read"},
        confidence="high",
        rationale="r",
        target_sha256="0" * 64,
    )
    with pytest.raises(ValidationError):
        EvidenceGapAction(
            kind="declare_action_authority",
            why="w",
            expects="e",
            authorable_by="human",
            patch=patch,
        )


def test_one_list_says_which_template_keys_name_the_action() -> None:
    """The selector split is a contract between two modules, so pin it.

    ``_action_selector`` builds the identifying half of every declaration
    template; ``ACTION_SELECTOR_KEYS`` is what the patch generator and the row
    model take back out of it. A key added to one and not the other would be
    written into ``declaration`` as if it were a claim about the action, or
    counted as a declaration that declares nothing.
    """

    from agents_shipgate.ci.release_decision import _action_selector
    from agents_shipgate.core.domain import Tool
    from agents_shipgate.schemas.patches import ACTION_SELECTOR_KEYS

    with_source_id = Tool(
        id="tool_1", name="send_email", source_type="google_adk", source_id="adk_agent"
    )
    without_source_id = Tool(id="tool_1", name="send_email", source_type="google_adk")
    produced = set(_action_selector(with_source_id)) | set(
        _action_selector(without_source_id)
    )
    assert produced == set(ACTION_SELECTOR_KEYS)


def test_template_completeness_looks_all_the_way_down() -> None:
    assert template_is_complete({"tool": "x", "effect": "write"})
    assert not template_is_complete({"tool": "x", "a": [{"b": REVIEW_REQUIRED_SENTINEL}]})
    assert not template_is_complete({REVIEW_REQUIRED_SENTINEL: "x"})
    assert not template_is_complete({})
    assert not template_is_complete(None)


# --- the questionnaire fold -------------------------------------------------


def test_a_question_carries_the_tag_of_the_rows_it_folds(tmp_path: Path) -> None:
    config = _project(tmp_path)
    report = _scan(config, tmp_path / "out")

    questions = {
        (row.subject_kind, row.dimension): row.authorable_by
        for row in _coverage(report).semantic_coverage.declaration_questions.open_questions
    }
    assert questions[("action", "effect")] == "coding_agent"
    assert questions[("tool_source", "authority")] == "human"


def test_open_wins_when_one_folded_row_is_human_owned() -> None:
    """A block an agent can half-write is not an agent's edit.

    Exercised directly on the fold rather than through a fixture, because the
    shape it guards — two rows of one dimension on one subject, disagreeing
    about authorship — is what a future gap kind would introduce, and it must
    be safe before such a kind exists.
    """

    from agents_shipgate.core.declaration_questions import question_authorship
    from agents_shipgate.schemas.report import EvidenceGap

    def _row(kind: str, authorable_by: str, template) -> EvidenceGap:
        return EvidenceGap(
            kind=kind,
            subject="send_email",
            subject_id="tool_1",
            why="w",
            next_action=EvidenceGapAction(
                kind=(
                    "declare_action_effect"
                    if authorable_by == "coding_agent"
                    else "resolve_semantic_conflict"
                ),
                why="w",
                expects="e",
                authorable_by=authorable_by,
                declaration_template=template,
            ),
        )

    drafted = _row("inferred_effect_only", "coding_agent", {"tool": "x", "effect": "write"})
    contested = _row("conflicting_effect_evidence", "human", None)

    assert question_authorship([drafted]) == {("action", "tool_1", "effect"): "coding_agent"}
    assert question_authorship([drafted, contested]) == {
        ("action", "tool_1", "effect"): "human"
    }
    assert question_authorship([contested, drafted]) == {
        ("action", "tool_1", "effect"): "human"
    }


# --- applying the patch -----------------------------------------------------


def test_declare_action_is_outside_the_default_kinds(tmp_path: Path) -> None:
    """No existing pipeline starts writing declarations because of this PR."""

    config = _project(tmp_path)
    _scan(config, tmp_path / "out")
    before = config.read_text(encoding="utf-8")

    result = _apply(tmp_path / "out" / "report.json")

    assert result.exit_code == 0, result.output
    assert config.read_text(encoding="utf-8") == before


def test_applying_the_declaration_answers_the_question(tmp_path: Path) -> None:
    """The loop, end to end: 0 of 2 answered, apply, 1 of 2 answered."""

    config = _project(tmp_path)
    first = _scan(config, tmp_path / "out")
    counter = _coverage(first).semantic_coverage.declaration_questions
    assert (counter.total, counter.answered) == (2, 0)

    result = _apply(tmp_path / "out" / "report.json", kinds="declare_action")
    assert result.exit_code == 0, result.output

    second = _scan(config, tmp_path / "out2")
    counter = _coverage(second).semantic_coverage.declaration_questions
    assert (counter.total, counter.answered) == (2, 1)
    assert [row.dimension for row in counter.open_questions] == ["authority"]
    # And the answer is the one the evidence supports, so #409 stays silent.
    assert not [
        gap
        for gap in _coverage(second).evidence_gaps
        if gap.kind == "declaration_below_inferred_evidence"
    ]


def test_the_written_row_is_the_only_change_to_the_manifest(tmp_path: Path) -> None:
    """A trust-root edit a reviewer cannot read is not a review surface.

    Round-tripping YAML re-emits every sequence at one indentation setting, so
    without pinning it a one-line declaration also reformatted every unrelated
    list in the file.
    """

    manifest = _MANIFEST + (
        "policies:\n"
        "  require_approval_for_tools:\n"
        "    - send_email\n"
    )
    config = _project(tmp_path, manifest=manifest)
    _scan(config, tmp_path / "out")
    before = config.read_text(encoding="utf-8").splitlines()

    result = _apply(tmp_path / "out" / "report.json", kinds="declare_action")
    assert result.exit_code == 0, result.output

    after = config.read_text(encoding="utf-8").splitlines()
    assert after[: len(before)] == before
    assert "action_surface:" in after[len(before) :][0]


def test_a_silent_field_on_an_existing_row_is_filled_in_place(tmp_path: Path) -> None:
    manifest = _MANIFEST + (
        "action_surface:\n"
        "  actions:\n"
        "    - tool: send_email\n"
        "      source_id: adk_agent\n"
        "      authority:\n"
        "        mode: none\n"
    )
    config = _project(tmp_path, manifest=manifest)
    _scan(config, tmp_path / "out")

    result = _apply(tmp_path / "out" / "report.json", kinds="declare_action")
    assert result.exit_code == 0, result.output

    text = config.read_text(encoding="utf-8")
    assert text.count("- tool: send_email") == 1
    assert "effect: external_communication" in text


# --- the refusals -----------------------------------------------------------


def test_a_reviewed_answer_is_never_replaced(tmp_path: Path) -> None:
    """Only a human may assert against the evidence — including by hand.

    Reached by pointing a *stale* report at a manifest that has since been
    answered differently. Nothing is written, and the exit code says so: an
    agent that re-ran verify on a silent no-op would get the identical route
    back forever.
    """

    config = _project(tmp_path)
    _scan(config, tmp_path / "out")
    report_path = tmp_path / "out" / "report.json"

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    answered = _MANIFEST + (
        "action_surface:\n"
        "  actions:\n"
        "    - tool: send_email\n"
        "      source_id: adk_agent\n"
        "      effect: destructive\n"
    )
    config.write_text(answered, encoding="utf-8")
    # The SHA pin would stop this first; re-pin it so the *content* rule is
    # what the test proves.
    import hashlib

    digest = hashlib.sha256(config.read_bytes()).hexdigest()
    for gap in payload["release_decision"]["evidence_coverage"]["evidence_gaps"]:
        patch = gap["next_action"].get("patch")
        if patch:
            patch["target_sha256"] = digest
    report_path.write_text(json.dumps(payload), encoding="utf-8")

    result = _apply(report_path, kinds="declare_action")

    assert result.exit_code == 5, result.output
    assert config.read_text(encoding="utf-8") == answered


def _declare(tool: str, tool_id: str, source_id: str, effect: str):
    from agents_shipgate.schemas.patches import DeclareActionPatch

    return DeclareActionPatch(
        target_path="shipgate.yaml",
        selector={"tool": tool, "tool_id": tool_id, "source_id": source_id},
        declaration={"effect": effect},
        confidence="high",
        rationale="r",
        target_sha256="0" * 64,
    )


def test_two_providers_exporting_one_name_get_two_rows() -> None:
    """A display name is not an identity, and the manifest already knows that.

    Two supported providers can both export ``send_email``; the scan emits a
    patch for each, distinguished by ``tool_id``/``source_id``. Matching on the
    display name alone made the *batch* unexecutable: the first patch appended
    a row, the second read that row as its own mismatched match, and the whole
    group was refused with nothing written.
    """

    from agents_shipgate.cli.apply_patches import _declare_action

    root: dict = {}
    _declare_action(root, _declare("send_email", "t_a", "gmail", "external_communication"))
    _declare_action(root, _declare("send_email", "t_b", "sendgrid", "external_communication"))

    rows = root["action_surface"]["actions"]
    assert [(row["tool_id"], row["source_id"]) for row in rows] == [
        ("t_a", "gmail"),
        ("t_b", "sendgrid"),
    ]
    assert all(row["effect"] == "external_communication" for row in rows)


def test_the_generator_and_the_applier_agree_about_two_same_named_actions(
    tmp_path: Path,
) -> None:
    """Generator to file, for the shape that broke: one name, two capabilities.

    ``test_two_providers_exporting_one_name_get_two_rows`` pins the applier on
    hand-built patches. This one starts a step earlier, at the rows a scan
    publishes, so the two halves cannot drift into disagreeing about what makes
    an action distinct.
    """

    from agents_shipgate.checks.patches import attach_declaration_patches
    from agents_shipgate.cli.apply_patches import _declare_action
    from agents_shipgate.schemas.report import EvidenceGap, EvidenceGapAction

    manifest = tmp_path / "shipgate.yaml"
    manifest.write_text('version: "0.1"\n', encoding="utf-8")

    gaps = [
        EvidenceGap(
            kind="inferred_effect_only",
            subject=f"send_email [{source}]",
            subject_id=tool_id,
            why="w",
            next_action=EvidenceGapAction(
                kind="declare_action_effect",
                why="w",
                expects="e",
                authorable_by="coding_agent",
                declaration_template={
                    "tool": "send_email",
                    "tool_id": tool_id,
                    "source_id": source,
                    "effect": "external_communication",
                },
            ),
        )
        for source, tool_id in (("gmail", "tool_a"), ("sendgrid", "tool_b"))
    ]

    assert attach_declaration_patches(gaps, manifest_path=manifest) == 2

    root: dict = {}
    for gap in gaps:
        assert gap.next_action.patch is not None
        _declare_action(root, gap.next_action.patch)

    rows = root["action_surface"]["actions"]
    assert [(row["tool_id"], row["source_id"]) for row in rows] == [
        ("tool_a", "gmail"),
        ("tool_b", "sendgrid"),
    ]


def test_a_row_that_names_the_same_action_less_precisely_is_written_into() -> None:
    """A human writes ``tool:``; the scan knows the id. That is one action."""

    from agents_shipgate.cli.apply_patches import _declare_action

    root = {"action_surface": {"actions": [{"tool": "send_email", "authority": {"mode": "none"}}]}}
    _declare_action(root, _declare("send_email", "t_a", "gmail", "external_communication"))

    rows = root["action_surface"]["actions"]
    assert len(rows) == 1
    assert rows[0]["effect"] == "external_communication"
    # The qualifiers are *not* written in: they are this scan's identity for
    # the action, not a correction to what the reviewer chose to spell.
    assert "tool_id" not in rows[0]


def test_two_equally_compatible_rows_are_never_guessed_between() -> None:
    """Refusal is reserved for a manifest that really is ambiguous."""

    from agents_shipgate.cli.apply_patches import DeclarationConflict, _declare_action

    root = {
        "action_surface": {
            "actions": [{"tool": "send_email"}, {"tool": "send_email"}],
        }
    }
    with pytest.raises(DeclarationConflict):
        _declare_action(root, _declare("send_email", "t_a", "gmail", "external_communication"))


def test_a_crlf_manifest_can_answer_its_own_question(tmp_path: Path) -> None:
    """The generator hashes bytes, so the applier must hash bytes too.

    Reading as text normalizes CRLF to LF, so an untouched CRLF manifest
    produced a digest no patch could match: every apply reported drift, and
    rescanning recreated the same failure — an unbreakable loop for the agent
    following the route. The newline style survives the round trip as well,
    because a declaration is reviewed as a diff.
    """

    import hashlib

    from agents_shipgate.cli.apply_patches import _apply_one_file
    from agents_shipgate.schemas.patches import DeclareActionPatch

    target = tmp_path / "shipgate.yaml"
    target.write_bytes(b'version: "0.1"\r\naction_surface:\r\n  actions: []\r\n')
    patch = DeclareActionPatch(
        target_path="shipgate.yaml",
        selector={"tool": "send_email"},
        declaration={"effect": "external_communication"},
        confidence="high",
        rationale="r",
        # Exactly what the generator computes: a digest of the bytes on disk.
        target_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
    )

    outcome = _apply_one_file(target, [patch], apply=True)

    assert outcome.status == "applied", outcome.error
    written = target.read_bytes()
    assert b"effect: external_communication" in written
    assert b"\r\n" in written
    assert b"\n" not in written.replace(b"\r\n", b"")


def test_a_forged_weakening_still_meets_the_gate(tmp_path: Path) -> None:
    """The content rule holds at the verdict, not only at the file.

    ``apply-patches`` reads a report the caller supplied, so a rewritten one
    can put ``effect: read`` into the manifest — exactly as a text editor can.
    That is not the boundary. The boundary is that the next scan reads the
    evidence again and refuses to call the result answered (#409).
    """

    config = _project(tmp_path)
    _scan(config, tmp_path / "out")
    report_path = tmp_path / "out" / "report.json"

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    for gap in payload["release_decision"]["evidence_coverage"]["evidence_gaps"]:
        patch = gap["next_action"].get("patch")
        if patch:
            patch["declaration"]["effect"] = "read"
    report_path.write_text(json.dumps(payload), encoding="utf-8")

    result = _apply(report_path, kinds="declare_action")
    assert result.exit_code == 0, result.output
    assert "effect: read" in config.read_text(encoding="utf-8")

    after = _scan(config, tmp_path / "out2")
    kinds = [gap.kind for gap in _coverage(after).evidence_gaps]
    assert "declaration_below_inferred_evidence" in kinds
    counter = _coverage(after).semantic_coverage.declaration_questions
    assert counter.answered == 0
    assert after.release_decision is not None
    assert after.release_decision.decision != "passed"
