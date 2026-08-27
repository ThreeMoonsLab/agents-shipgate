"""Control packs: the rules layer, chosen once (#410 §F).

The properties worth pinning are not "the table has the rows I typed". They
are the two that make selecting a pack safe:

* a pack can only *add* obligations, proven by comparing whole finding sets
  across a real scan rather than by reading the tables;
* a pack decides which control findings fire and never what a declaration
  means, so a pack that obliged every effect identically cannot collapse the
  obligation lattice the #413 coverage rule depends on.

Everything else here exists because it was a way to get one of those wrong.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.action_semantics import (
    ACTION_EFFECT_RANK,
    BUILTIN_EFFECT_OBLIGATIONS,
)
from agents_shipgate.core.control_packs import (
    BUILTIN_CONTROL_PACKS,
    CONTROL_PACK_IDS,
    DEFAULT_CONTROL_PACK,
    DEFAULT_CONTROL_PACK_ID,
    ControlPack,
    _assert_packs_extend_default,
    control_rule_summaries,
    manifest_control_pack_block,
    resolve_control_pack,
)
from agents_shipgate.core.findings.identity import finding_fingerprint
from agents_shipgate.schemas.manifest import AgentsShipgateManifest
from agents_shipgate.schemas.report import Finding

runner = CliRunner()

_NON_DEFAULT_PACKS = [
    pack_id for pack_id in CONTROL_PACK_IDS if pack_id != DEFAULT_CONTROL_PACK_ID
]


# --------------------------------------------------------------------------
# The safety property: a pack may add, never subtract
# --------------------------------------------------------------------------


@pytest.mark.parametrize("pack_id", _NON_DEFAULT_PACKS)
@pytest.mark.parametrize("effect", sorted(BUILTIN_EFFECT_OBLIGATIONS))
def test_every_pack_requires_at_least_what_default_requires(
    pack_id: str, effect: str
) -> None:
    """Selecting a pack is a one-word answer; it must not be able to cost coverage."""

    assert DEFAULT_CONTROL_PACK.obligations_for(effect) <= BUILTIN_CONTROL_PACKS[
        pack_id
    ].obligations_for(effect)


def test_the_extends_default_guard_actually_bites() -> None:
    """A guard nobody has seen fail is a guard nobody knows works.

    ``_assert_packs_extend_default`` runs at import, so every real run proves
    only the passing case. Feed it a pack that drops an obligation and require
    the message to name the effect and the control, because that message is
    the whole value of failing at import rather than in a scan.
    """

    weakened = ControlPack(
        id="weakened",
        name="Weakened",
        version="1",
        summary="drops an obligation",
        obligations={"financial_write": frozenset({"approval.required"})},
    )
    BUILTIN_CONTROL_PACKS["weakened"] = weakened
    try:
        with pytest.raises(AssertionError) as excinfo:
            _assert_packs_extend_default()
    finally:
        del BUILTIN_CONTROL_PACKS["weakened"]
    message = str(excinfo.value)
    assert "weakened" in message
    assert "financial_write" in message
    assert "safeguards.audit_log" in message


def test_the_default_pack_obliges_nothing_outside_the_dedicated_checks() -> None:
    """The claim that makes ``policies.control_pack`` additive.

    The pack-only route exists for effects with no control check of their own.
    If ``default`` ever obliged one of those, every manifest that never
    answered the question would start emitting a finding it did not before —
    so the sentence in ``_pack_only_control_findings`` is a guarded claim, not
    a comment.
    """

    from agents_shipgate.core.control_packs import (
        EFFECTS_WITH_DEDICATED_CONTROL_CHECK,
    )

    assert (
        set(DEFAULT_CONTROL_PACK.obligations) <= EFFECTS_WITH_DEDICATED_CONTROL_CHECK
    )


def test_one_rule_is_one_finding_however_many_effects_share_it(
    tmp_path: Path,
) -> None:
    """An action that writes *and* reads privileged data owes one audit log.

    ``read-only-agent`` obliges the same pair of controls for both, so it is
    one rule; three findings repeating one sentence about one action is the
    shape #410 exists to remove.
    """

    workspace = tmp_path / "grouped"
    report = _scan_effect(
        workspace,
        "write",
        pack_id="read-only-agent",
        declared_risk_tags=["privileged_data_access"],
    )
    rows = [
        finding
        for finding in report.findings
        if finding.check_id == "SHIP-ACTION-POLICY-VIOLATION"
    ]
    assert len(rows) == 1, [row.evidence.get("policy_id") for row in rows]
    policy_id = rows[0].evidence["policy_id"]
    assert policy_id == "control-pack:privileged_data_access+write"
    summaries = control_rule_summaries(report.findings)
    assert [row.effects for row in summaries] == [
        ("privileged_data_access", "write")
    ]


def test_the_default_pack_is_the_builtin_table_itself() -> None:
    """Not a copy of it. A copy is a second place for the rules to be wrong."""

    assert dict(DEFAULT_CONTROL_PACK.obligations) == dict(BUILTIN_EFFECT_OBLIGATIONS)


def test_an_absent_control_pack_resolves_to_default() -> None:
    manifest = AgentsShipgateManifest.model_validate(_manifest_dict())
    assert resolve_control_pack(manifest) is DEFAULT_CONTROL_PACK


def test_an_unknown_pack_id_is_rejected_by_the_schema() -> None:
    """A typo must not fall back to the loosest pack."""

    data = _manifest_dict()
    data["policies"] = {"control_pack": "financial_strict"}
    with pytest.raises(Exception) as excinfo:
        AgentsShipgateManifest.model_validate(data)
    assert "control_pack" in str(excinfo.value)


# --------------------------------------------------------------------------
# The tables, pinned against the controls that actually fire
# --------------------------------------------------------------------------


@pytest.mark.parametrize("pack_id", CONTROL_PACK_IDS)
@pytest.mark.parametrize("effect", sorted(ACTION_EFFECT_RANK))
def test_a_pack_table_matches_the_controls_that_fire(
    tmp_path: Path, pack_id: str, effect: str
) -> None:
    """One real scan per (pack, effect), comparing the exact set both ways.

    Modelled on ``test_the_builtin_obligation_table_matches_the_controls_that_fire``
    and for the same reason: a table that merely *contains* what fires still
    lets a route oblige something the table never claimed, and this table is
    now what the console line, the report section, and the tool-level
    ``SHIP-POLICY-*`` checks all read.
    """

    pack = BUILTIN_CONTROL_PACKS[pack_id]
    report = _scan_effect(tmp_path / f"{pack_id}-{effect}", effect, pack_id=pack_id)
    assert _missing_control_paths(report) == set(pack.obligations_for(effect)), (
        f"{pack_id}/{effect}: the pack says "
        f"{sorted(pack.obligations_for(effect))} but the scan reported "
        f"{sorted(_missing_control_paths(report))} missing on an action "
        "declaring none of them."
    )


@pytest.mark.parametrize("pack_id", _NON_DEFAULT_PACKS)
@pytest.mark.parametrize("effect", sorted(ACTION_EFFECT_RANK))
def test_a_stricter_pack_never_drops_a_finding(
    tmp_path: Path, pack_id: str, effect: str
) -> None:
    """Compare the whole finding set, not one named check.

    Asserting on a named check would survive a pack that silently stopped a
    *different* family — which is exactly the shape the increment-4 template
    fail-open took.

    The comparison is ``(check_id, tool_id)`` plus the missing set, not the
    fingerprint. A fingerprint hashes ``evidence.missing``, so a rule that
    grows from one required control to two legitimately re-fingerprints the
    same concern — a stricter pack re-opens those baseline entries, which is
    the fail-closed direction and is documented rather than worked around.
    """

    base = _scan_effect(tmp_path / f"base-{effect}", effect)
    strict = _scan_effect(tmp_path / f"{pack_id}-{effect}", effect, pack_id=pack_id)
    base_rows = _control_rows(base)
    strict_rows = _control_rows(strict)
    dropped = set(base_rows) - set(strict_rows)
    assert not dropped, (
        f"{pack_id} stopped reporting {sorted(dropped)} for {effect}; "
        "a pack may only add"
    )
    for key, paths in base_rows.items():
        assert paths <= strict_rows[key], (
            f"{pack_id} narrowed {key} for {effect}: {sorted(paths)} → "
            f"{sorted(strict_rows[key])}"
        )
    # Nothing outside the control families quietly went away either.
    assert _non_control_fingerprints(base) <= _non_control_fingerprints(strict)


@pytest.mark.parametrize("pack_id", CONTROL_PACK_IDS)
@pytest.mark.parametrize("effect", sorted(ACTION_EFFECT_RANK))
def test_declaring_exactly_what_a_pack_asks_for_closes_every_control_finding(
    tmp_path: Path, pack_id: str, effect: str
) -> None:
    """A rule nobody can satisfy is a trap, not a gate.

    Every control finding tells the reader to declare a specific list. This
    walks that instruction for all 27 (pack × effect) pairs: declare exactly
    the pack's list, and every control family — the four action ones *and*
    the two tool-level ``SHIP-POLICY-*`` ones — has to go quiet. A published
    next step that cannot change the answer is the #399 defect, and a
    ``confirmation.required`` that is not writable on the action row is
    exactly where it would hide.
    """

    controls = BUILTIN_CONTROL_PACKS[pack_id].obligations_for(effect)
    report = _scan_effect(
        tmp_path / f"{pack_id}-{effect}",
        effect,
        pack_id=pack_id,
        declared_controls=controls,
    )
    remaining = sorted(
        {
            finding.check_id
            for finding in report.findings
            if finding.check_id in _CONTROL_CHECKS
            or finding.check_id.startswith("SHIP-POLICY-")
        }
    )
    assert remaining == [], (
        f"{pack_id}/{effect}: declaring {sorted(controls)} left {remaining}"
    )


def test_writing_default_explicitly_scans_exactly_like_omitting_it(
    tmp_path: Path,
) -> None:
    """Silence and ``default`` are the same answer, all the way to the findings."""

    omitted = _scan_effect(tmp_path / "omitted", "financial_write")
    explicit = _scan_effect(
        tmp_path / "explicit", "financial_write", pack_id=DEFAULT_CONTROL_PACK_ID
    )
    assert _fingerprints(omitted) == _fingerprints(explicit)
    assert _missing_control_paths(omitted) == _missing_control_paths(explicit)


# --------------------------------------------------------------------------
# A pack decides what fires, never what a declaration means
# --------------------------------------------------------------------------


@pytest.mark.parametrize("pack_id", CONTROL_PACK_IDS)
def test_a_pack_cannot_make_one_effect_discharge_another(
    tmp_path: Path, pack_id: str
) -> None:
    """The #413 rule survives every pack.

    ``declaration_covers`` compares *built-in* obligations, so a pack that
    obliges the same controls for ``write`` and ``external_communication``
    must not thereby let a declared ``write`` discharge an observed
    ``external_communication``. ``read-only-agent`` obliges approval and an
    audit log for both, which is exactly the shape that would collapse the
    lattice if the comparator read the pack.
    """

    from agents_shipgate.core.semantic_assessment import declaration_covers

    assert not declaration_covers("write", "external_communication")
    assert not declaration_covers("write", "destructive")
    # And through a scan: the pack is in force, and the confirmation the
    # external-communication rule wants is still demanded.
    report = _scan_effect(
        tmp_path / pack_id, "external_communication", pack_id=pack_id
    )
    assert "confirmation.required" in _missing_control_paths(report)


# --------------------------------------------------------------------------
# Identity, suppression, and the report the adopter reads
# --------------------------------------------------------------------------


def test_the_pack_annotation_does_not_move_a_fingerprint() -> None:
    """A baseline recorded before this field has to keep matching."""

    without = Finding(
        check_id="SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING",
        title="t",
        severity="critical",
        category="action_surface",
        evidence={"action_id": "a1", "missing": ["approval.required"]},
        recommendation="r",
        tool_id="tool-1",
    )
    with_pack = without.model_copy(
        update={
            "evidence": {
                **without.evidence,
                "control_pack": "read-only-agent",
            }
        }
    )
    assert finding_fingerprint(without) == finding_fingerprint(with_pack)


def test_a_pack_obligation_cannot_be_waived_by_a_suppression(tmp_path: Path) -> None:
    """Routing through the generic id must not make it the waivable one.

    The four dedicated control families are already non-waivable. An
    obligation that exists because the repository chose a stricter pack is
    just as built-in, and it reaches ``checks.ignore`` through
    ``SHIP-ACTION-POLICY-VIOLATION`` — the one id a user policy also uses.
    """

    workspace = tmp_path / "suppressed"
    report = _scan_effect(
        workspace,
        "write",
        pack_id="read-only-agent",
        extra="""
checks:
  ignore:
    - check_id: SHIP-ACTION-POLICY-VIOLATION
      tool: act
      reason: accepted for this test
""",
    )
    pack_findings = [
        finding
        for finding in report.findings
        if finding.check_id == "SHIP-ACTION-POLICY-VIOLATION"
    ]
    assert pack_findings, "expected the pack-only route to fire on a plain write"
    assert all(finding.suppressed for finding in pack_findings)
    decision = report.release_decision
    assert decision is not None
    blocked = {item.check_id for item in decision.blockers}
    assert "SHIP-ACTION-POLICY-VIOLATION" in blocked


def _policy_violation(**evidence) -> Finding:
    return Finding(
        check_id="SHIP-ACTION-POLICY-VIOLATION",
        title="t",
        severity="high",
        category="action_surface",
        evidence=evidence,
        recommendation="r",
        tool_id="tool-1",
    )


@pytest.mark.parametrize(
    "evidence, recognised",
    [
        # Engine-minted: the pack is stamped and the id is in the grammar.
        ({"policy_id": "control-pack:write", "control_pack": "read-only-agent"}, True),
        (
            {
                "policy_id": "control-pack:privileged_data_access+write",
                "control_pack": "read-only-agent",
            },
            True,
        ),
        ({"policy_id": "builtin-high-impact-approval", "control_pack": "default"}, True),
        # A *user* policy that took the grammar. Without engine provenance it
        # is their rule, and making it non-waivable would turn their own
        # suppression of it into a blocker.
        ({"policy_id": "control-pack:write"}, False),
        # Provenance without the grammar is not this route either.
        ({"policy_id": "org-approval", "control_pack": "default"}, False),
        ({"policy_id": "control-pack:not_an_effect", "control_pack": "default"}, False),
        ({"policy_id": "control-pack:", "control_pack": "default"}, False),
        ({"policy_id": "control-pack:write", "control_pack": "made-up"}, False),
        ({"control_pack": "default"}, False),
        ({}, False),
    ],
)
def test_only_findings_this_engine_raised_are_read_as_pack_rules(
    evidence: dict, recognised: bool
) -> None:
    """Provenance is the stamp, not the string.

    Deciding this from ``policy_id`` alone read a user-authored
    ``action_surface.policies[].id`` in the same grammar as engine-minted —
    silently non-waivable, and their suppression of it promoted to a blocker.
    """

    from agents_shipgate.core.control_packs import is_control_pack_finding

    assert is_control_pack_finding(_policy_violation(**evidence)) is recognised


def test_the_manifest_refuses_the_reserved_policy_id_prefix() -> None:
    """The second layer: the collision is refused before it can be raised."""

    data = _manifest_dict()
    data["action_surface"] = {
        "policies": [
            {
                "id": "control-pack:write",
                "severity": "high",
                "require": {"approval.required": True},
            }
        ]
    }
    with pytest.raises(Exception) as excinfo:
        AgentsShipgateManifest.model_validate(data)
    message = str(excinfo.value)
    assert "reserved" in message
    assert "policies.control_pack" in message
    # …and an ordinary organization id is untouched.
    data["action_surface"]["policies"][0]["id"] = "ORG-REQUIRE-APPROVAL"
    AgentsShipgateManifest.model_validate(data)


@pytest.mark.parametrize(
    "tool_level_evidence",
    [
        # As emitted today.
        {"control_pack": "default", "policy_match": None},
        # And with a policy_id, because excluding this finding by naming its
        # check id would be unreachable *today* and vacuous tomorrow: the
        # reason it is not a rule row is that it is not about an action.
        {"control_pack": "default", "policy_id": "control-pack:financial_write"},
    ],
)
def test_a_finding_about_a_tool_is_not_a_rule_row(tool_level_evidence: dict) -> None:
    """``SHIP-POLICY-APPROVAL-MISSING`` carries the pack but is not a rule row.

    It is the same missing approval the effect rule already counts, said about
    the whole tool. Counting it too would tell a reader two actions are short
    where one is.
    """

    findings = [
        Finding(
            check_id="SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING",
            title="t",
            severity="critical",
            category="action_surface",
            evidence={
                "action_id": "a1",
                "missing": ["approval.required"],
                "control_pack": "default",
                "control_effects": ["financial_write"],
            },
            recommendation="r",
            tool_id="tool-1",
        ),
        Finding(
            check_id="SHIP-POLICY-APPROVAL-MISSING",
            title="t",
            severity="critical",
            category="policy",
            evidence=tool_level_evidence,
            recommendation="r",
            tool_id="tool-1",
        ),
    ]
    summaries = control_rule_summaries(findings)
    assert [row.effects for row in summaries] == [("financial_write",)]
    assert summaries[0].action_count == 1


@pytest.mark.parametrize("pack_id", CONTROL_PACK_IDS)
@pytest.mark.parametrize("effect", sorted(ACTION_EFFECT_RANK))
def test_the_tool_level_policy_checks_read_the_same_pack(
    tmp_path: Path, pack_id: str, effect: str
) -> None:
    """`SHIP-POLICY-APPROVAL-MISSING` / `-CONFIRMATION-MISSING`, per pack.

    These two carried their own effect sets — ``{financial_write, destructive,
    production_operation, code_execution}`` and ``{destructive,
    external_communication}`` — which were exactly the projections
    ``effects_obliging`` computes, maintained by hand beside the table. A
    perturbation reverting them to the literals broke **nothing**, so the
    mechanism was working and completely unguarded.
    """

    pack = BUILTIN_CONTROL_PACKS[pack_id]
    report = _scan_effect(tmp_path / f"{pack_id}-{effect}", effect, pack_id=pack_id)
    fired = {finding.check_id for finding in report.findings}
    assert ("SHIP-POLICY-APPROVAL-MISSING" in fired) is (
        effect in pack.effects_obliging("approval.required")
    )
    assert ("SHIP-POLICY-CONFIRMATION-MISSING" in fired) is (
        effect in pack.effects_obliging("confirmation.required")
    )


def test_the_report_and_the_console_name_the_same_rules(tmp_path: Path) -> None:
    """One projection, two surfaces — the #403 value-join rule.

    ``report.md`` and the console line both render
    ``control_rule_summaries``; a scan whose report names a rule the console
    does not would mean two readings of one report. The console half is
    asserted through the real command, because removing that line broke no
    test at all when it was only asserted through the projection.
    """

    workspace = tmp_path / "render"
    report = _scan_effect(workspace, "financial_write")
    summaries = control_rule_summaries(report.findings)
    assert summaries, "expected a financial-write rule to be short"
    text = (workspace / "out" / "report.md").read_text(encoding="utf-8")
    assert "## Control Pack" in text
    assert "`default`" in text
    assert "financial write requires" in text
    assert "1 action short" in text

    result = runner.invoke(
        app,
        [
            "scan",
            "-c",
            str(workspace / "shipgate.yaml"),
            "--out",
            str(workspace / "console-out"),
        ],
    )
    assert result.exit_code == 0, result.output
    console = [
        line for line in result.output.splitlines() if line.startswith("Control pack:")
    ]
    assert console == ["Control pack: default — actions short of: financial write (1)"]


def test_the_console_line_names_every_rule_it_can_and_counts_the_rest(
    tmp_path: Path,
) -> None:
    """A truncated console line states how much it is not showing (#364)."""

    from agents_shipgate.cli._helpers import _CLI_CONTROL_RULE_LIMIT

    workspace = tmp_path / "many"
    _scan_effect(
        workspace,
        "destructive",
        pack_id="read-only-agent",
        declared_risk_tags=[
            "financial_write",
            "external_communication",
            "production_operation",
            "privileged_data_access",
        ],
    )
    result = runner.invoke(
        app,
        [
            "scan",
            "-c",
            str(workspace / "shipgate.yaml"),
            "--out",
            str(workspace / "console-out"),
        ],
    )
    assert result.exit_code == 0, result.output
    line = next(
        line for line in result.output.splitlines() if line.startswith("Control pack:")
    )
    assert line.count("(") == _CLI_CONTROL_RULE_LIMIT
    assert "more rule" in line


def test_a_clean_control_surface_renders_no_control_pack_section(
    tmp_path: Path,
) -> None:
    """The section is about what is missing; with nothing missing it says nothing."""

    report = _scan_effect(tmp_path / "clean", "read")
    assert control_rule_summaries(report.findings) == []
    text = (tmp_path / "clean" / "out" / "report.md").read_text(encoding="utf-8")
    assert "## Control Pack" not in text


def test_the_documented_pack_matrix_matches_the_packs() -> None:
    """The reference table is the table, not a description of it.

    A pack is chosen by reading the docs, so a doc row that drifts from the
    engine is a wrong answer given confidently. Rebuilt here from the pack
    objects and compared to the committed rows — the same shape as the schema
    doc-parity rules, and the reason the doc says which test holds it.
    """

    from agents_shipgate.core.action_semantics import (
        control_phrase,
        ordered_controls,
    )

    doc = (
        Path(__file__).resolve().parent.parent / "docs" / "manifest-v0.1.md"
    ).read_text(encoding="utf-8")
    header = "| Effect | " + " | ".join(f"`{p}`" for p in CONTROL_PACK_IDS) + " |"
    assert header in doc, "the pack matrix header does not name today's packs"
    for effect in sorted(ACTION_EFFECT_RANK):
        cells = []
        for pack_id in CONTROL_PACK_IDS:
            obligations = BUILTIN_CONTROL_PACKS[pack_id].obligations_for(effect)
            cells.append(
                ", ".join(
                    f"`{control_phrase(path)}`"
                    for path in ordered_controls(obligations)
                )
                if obligations
                else "\u2014"
            )
        row = f"| `{effect}` | " + " | ".join(cells) + " |"
        assert row in doc, f"docs/manifest-v0.1.md is missing or wrong for:\n{row}"


# --------------------------------------------------------------------------
# Moving to a weaker pack is a weakening, and the gate has to say so
# --------------------------------------------------------------------------


def _policy_context(head_pack: str | None, base_pack: str | None):
    """A verify-active context whose base snapshot names ``base_pack``."""

    from agents_shipgate.core.context import ScanContext
    from agents_shipgate.core.domain import Agent
    from agents_shipgate.core.lenses.tool_surface import ToolSurfaceDiffReference
    from agents_shipgate.schemas.capability_change import EffectivePolicy
    from agents_shipgate.schemas.verification import VerificationContext

    data = _manifest_dict()
    if head_pack is not None:
        data["policies"] = {"control_pack": head_pack}
    manifest = AgentsShipgateManifest.model_validate(data)
    reference = ToolSurfaceDiffReference(
        kind="report",
        facts=None,
        effective_policy=EffectivePolicy(control_pack=base_pack),
    )
    return ScanContext(
        manifest=manifest,
        agent=Agent(id="agent:test/test", name="test"),
        tools=[],
        config_path=Path("shipgate.yaml"),
        verification=VerificationContext(
            changed_files=["shipgate.yaml"],
            configured_manifest_path="shipgate.yaml",
            manifest_introduced=False,
        ),
        diff_reference=reference,
    )


def _run_policy_check(head_pack: str | None, base_pack: str | None) -> list:
    from agents_shipgate.checks import verify_policy

    return verify_policy.run(_policy_context(head_pack, base_pack))


def _weakenings(head_pack: str | None, base_pack: str | None) -> list:
    return [
        finding
        for finding in _run_policy_check(head_pack, base_pack)
        if finding.evidence.get("kind") == "control_pack_weakened"
    ]


def _removed_rows(head_pack: str | None, base_pack: str | None) -> set:
    rows = set()
    for finding in _weakenings(head_pack, base_pack):
        for row in finding.evidence["removed_controls"]:
            rows.add((row["effect"], tuple(sorted(row["controls"]))))
    return rows


@pytest.mark.parametrize("base_pack", _NON_DEFAULT_PACKS)
def test_moving_to_a_pack_that_requires_less_is_a_weakening(base_pack: str) -> None:
    """The gate can be weakened by a *rule* change, not only a severity change.

    Every other field in the effective-policy snapshot answers "does the same
    finding still block?". The control pack answers "does the same action
    still produce the finding?", which is the other way a gate gets weaker —
    and, before this, the one a base-vs-head comparison could not see.
    """

    findings = _weakenings(DEFAULT_CONTROL_PACK_ID, base_pack)
    # One changed line is one finding, the shape `fail_on_loosened` uses. A
    # finding per effect would repeat one sentence eight times about one edit,
    # which is the shape #410 exists to remove.
    assert len(findings) == 1, [f.title for f in findings]
    evidence = findings[0].evidence
    assert evidence["base_control_pack"] == base_pack
    assert evidence["head_control_pack"] == DEFAULT_CONTROL_PACK_ID
    assert evidence["removed_controls"]
    assert findings[0].severity == "high"
    # The title says how much the evidence carries, and the sentence names a
    # bounded prefix of it plus how many it is not naming (#364).
    assert f"{len(evidence['removed_controls'])} effects require less" in (
        findings[0].title
    )
    assert "more effects" in findings[0].recommendation


@pytest.mark.parametrize(
    "removed, expected_title_fragment, expected_verb",
    [
        ([("write", ["approval.required"])], "1 effect requires less", "requires"),
        (
            [("write", ["approval.required"]), ("destructive", ["safeguards.rollback"])],
            "2 effects require less",
            "require",
        ),
    ],
)
def test_the_weakening_sentence_agrees_at_every_length(
    removed: list, expected_title_fragment: str, expected_verb: str
) -> None:
    """No two built-in packs differ on exactly one effect, so the singular
    reading is unreachable through a real scan and would ship unread."""

    from agents_shipgate.checks.verify_policy import (
        _weakening_sentence,
        _weakening_title,
    )

    assert expected_title_fragment in _weakening_title("a", "b", removed)
    sentence = _weakening_sentence("a", "b", removed)
    assert f"no longer {expected_verb} controls" in sentence
    for effect, controls in removed:
        assert effect.replace("_", " ") in sentence
        for path in controls:
            # `confirmation.required` is renamed; the rest are the raw path.
            assert path in sentence or "confirmation policy" in sentence


@pytest.mark.parametrize("head_pack", CONTROL_PACK_IDS)
@pytest.mark.parametrize("base_pack", CONTROL_PACK_IDS)
def test_a_pack_move_is_reported_exactly_where_something_was_dropped(
    head_pack: str, base_pack: str
) -> None:
    """Both directions, all nine pairs — including the two incomparable ones.

    ``financial-strict`` and ``read-only-agent`` are not ordered: each
    requires something the other does not, so *either* direction between them
    is a weakening for some effect. Asserting only on the obvious
    "strict -> default" pair would leave the sideways move silent.
    """

    base = BUILTIN_CONTROL_PACKS[base_pack]
    head = BUILTIN_CONTROL_PACKS[head_pack]
    expected = {
        (
            effect,
            tuple(sorted(base.obligations_for(effect) - head.obligations_for(effect))),
        )
        for effect in base.obligations
        if base.obligations_for(effect) - head.obligations_for(effect)
    }
    assert _removed_rows(head_pack, base_pack) == expected


def test_a_base_that_predates_the_field_is_compared_as_default() -> None:
    """``None`` is the ``default`` rule set, not "cannot compare".

    A build without control packs could not have loaded a manifest naming
    one, so a base snapshot with no ``control_pack`` ran ``default``'s rules.
    Resolving it to ``default`` and comparing keeps the "no pack is weaker
    than default" invariant enforced here rather than assumed: nothing is
    reported today, and a weaker pack added later would be.
    """

    assert _weakenings(DEFAULT_CONTROL_PACK_ID, None) == []
    assert _weakenings("read-only-agent", None) == []
    # …and the reverse is a weakening, because the base did run `default`.
    assert _weakenings(DEFAULT_CONTROL_PACK_ID, "read-only-agent")


def test_comparing_a_missing_base_as_default_is_not_the_same_as_skipping_it() -> None:
    """The three assertions above cannot tell the two apart, and neither can
    any pair of today's packs: every one of them requires at least what
    ``default`` does, so a ``None`` base yields nothing either way. A
    perturbation replacing the resolution with an early ``return []`` broke
    nothing.

    What the branch is *for* is the case ``_assert_packs_extend_default``
    forbids from existing. Register a weaker pack, keep the base ``None``, and
    the comparison has to report it — that is the difference between an
    invariant enforced twice and an invariant assumed once.
    """

    from agents_shipgate.checks.verify_policy import _weakened_pack_rules

    weaker = ControlPack(
        id="weaker-than-default",
        name="Weaker",
        version="1",
        summary="requires less than default",
        obligations={"financial_write": frozenset({"approval.required"})},
    )
    BUILTIN_CONTROL_PACKS[weaker.id] = weaker
    try:
        # Base is `None` — a snapshot from a build that predates the field,
        # which ran `default`. Head requires less, and the comparison says so.
        removed = dict(_weakened_pack_rules(None, weaker.id))
    finally:
        del BUILTIN_CONTROL_PACKS[weaker.id]
    assert "financial_write" in removed
    assert set(removed["financial_write"]) == {
        "safeguards.audit_log",
        "safeguards.idempotency",
    }
    assert "destructive" in removed


def test_the_snapshot_publishes_the_pack_in_force(tmp_path: Path) -> None:
    """A comparison needs the base side to have been recorded at all."""

    report = _scan_effect(tmp_path / "snapshot", "write", pack_id="read-only-agent")
    assert report.effective_policy is not None
    assert report.effective_policy.control_pack == "read-only-agent"


def test_an_unrecognized_base_pack_is_not_read_as_no_weakening() -> None:
    """"Cannot compare" and "nothing changed" are different claims.

    A base report written by a build that knows a pack this one does not
    resolves to no delta at all, so ``future-strict -> default`` read as a
    clean comparison. It routes to the fail-safe id — the one whose whole job
    is saying the comparison could not be made — rather than to a weakening
    claim nothing can support.
    """

    from agents_shipgate.checks.verify_policy import BASE_ABSENT_CHECK_ID

    findings = _run_policy_check(DEFAULT_CONTROL_PACK_ID, "future-strict")
    assert [f.check_id for f in findings] == [BASE_ABSENT_CHECK_ID]
    evidence = findings[0].evidence
    assert evidence["kind"] == "control_pack_unrecognized"
    assert evidence["unrecognized"] == ["future-strict"]
    assert evidence["base_control_pack"] == "future-strict"
    assert evidence["head_control_pack"] == DEFAULT_CONTROL_PACK_ID
    # And no weakening finding is invented beside it.
    assert not _weakenings(DEFAULT_CONTROL_PACK_ID, "future-strict")


def test_the_high_impact_rule_reports_only_the_effects_the_action_has(
    tmp_path: Path,
) -> None:
    """One id serves two effects; the finding must not inherit both.

    Recovering the effects from ``builtin-high-impact-approval`` reported a
    code-execution action as also operating on production — and the rule row
    then unioned the obligations of both and demanded ``safeguards.rollback``,
    which is `production_operation`'s requirement under ``financial-strict``
    and nothing this action was ever asked for.
    """

    report = _scan_effect(
        tmp_path / "high-impact", "code_execution", pack_id="financial-strict"
    )
    rows = [
        finding
        for finding in report.findings
        if finding.check_id == "SHIP-ACTION-POLICY-VIOLATION"
    ]
    assert len(rows) == 1
    assert rows[0].evidence["control_effects"] == ["code_execution"]
    summaries = control_rule_summaries(report.findings)
    assert [row.effects for row in summaries] == [("code_execution",)]
    assert set(summaries[0].controls) == set(
        BUILTIN_CONTROL_PACKS["financial-strict"].obligations_for("code_execution")
    )
    assert "safeguards.rollback" not in summaries[0].controls


def test_a_suppressed_mandatory_control_still_explains_the_blocker(
    tmp_path: Path,
) -> None:
    """A report cannot say BLOCKED and name nothing that blocks it.

    The release decision keeps a mandatory current-surface control blocking
    through a ``checks.ignore`` entry. The summary dropped every suppressed
    finding, so that report rendered no Control Pack section and no console
    line — the blocker was real and unexplained. One predicate now decides
    both.
    """

    workspace = tmp_path / "suppressed-visible"
    report = _scan_effect(
        workspace,
        "financial_write",
        extra="""
checks:
  ignore:
    - check_id: SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING
      tool: act
      reason: accepted for this test
""",
    )
    decision = report.release_decision
    assert decision is not None
    assert "SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING" in {
        item.check_id for item in decision.blockers
    }
    summaries = control_rule_summaries(report.findings)
    assert [row.effects for row in summaries] == [("financial_write",)]
    text = (workspace / "out" / "report.md").read_text(encoding="utf-8")
    assert "## Control Pack" in text
    assert "financial write requires" in text


@pytest.mark.parametrize(
    "effect, expected_check",
    [
        # The pack-only route, which is where the pack name lived in the id.
        ("identity_access", "SHIP-ACTION-POLICY-VIOLATION"),
        # …and a dedicated route, whose id never carried it. Named explicitly
        # so this case cannot quietly become a second copy of the first.
        (
            "external_communication",
            "SHIP-ACTION-EXTERNAL-COMMUNICATION-AUDIT-MISSING",
        ),
    ],
)
def test_equivalent_rules_keep_one_fingerprint_across_packs(
    tmp_path: Path, effect: str, expected_check: str
) -> None:
    """A baseline entry survives a move between packs that ask the same thing.

    ``policy_id`` used to embed the pack name, so the same missing control on
    the same action re-fingerprinted on a switch between packs whose rule for
    that effect is identical — silently dropping the accepted-debt entry. The
    pack is context (``evidence.control_pack``, excluded from the
    fingerprint); the rule is the effects and what they require.
    """

    pack_a, pack_b = "financial-strict", "read-only-agent"
    assert BUILTIN_CONTROL_PACKS[pack_a].obligations_for(effect) == (
        BUILTIN_CONTROL_PACKS[pack_b].obligations_for(effect)
    ), "fixture assumes these two packs state one rule for this effect"
    a = _scan_effect(tmp_path / f"{pack_a}-{effect}", effect, pack_id=pack_a)
    b = _scan_effect(tmp_path / f"{pack_b}-{effect}", effect, pack_id=pack_b)
    assert expected_check in {
        finding.check_id for finding in a.findings
    }, "fixture must exercise the route it names"
    assert _control_fingerprints(a) == _control_fingerprints(b)


def test_a_pack_that_asks_for_more_does_move_the_fingerprint(
    tmp_path: Path,
) -> None:
    """The other half: a rule that grew is a different row, and must re-open.

    Stability across equivalent packs would be a fail-open if it also held
    where the requirement changed.
    """

    base = _scan_effect(tmp_path / "write-fs", "write", pack_id="financial-strict")
    grown = _scan_effect(tmp_path / "write-ro", "write", pack_id="read-only-agent")
    assert _control_fingerprints(base) != _control_fingerprints(grown)


# --------------------------------------------------------------------------
# init: the one question, and every answer it takes
# --------------------------------------------------------------------------


def test_the_manifest_glossary_names_every_pack() -> None:
    """A pack added without a line here is a choice nobody is told about."""

    lines = manifest_control_pack_block(DEFAULT_CONTROL_PACK_ID)
    # The block wraps prose across comment lines, so compare against the
    # unwrapped text rather than against a rendering artefact.
    flat = " ".join(" ".join(lines).replace("#", " ").split())
    for pack_id in CONTROL_PACK_IDS:
        pack = BUILTIN_CONTROL_PACKS[pack_id]
        assert pack_id in flat
        assert " ".join(pack.summary.split()) in flat


@pytest.mark.parametrize("pack_id", CONTROL_PACK_IDS)
def test_init_writes_the_selected_pack(tmp_path: Path, pack_id: str) -> None:
    workspace = tmp_path / pack_id
    workspace.mkdir()
    (workspace / "tools.json").write_text(
        json.dumps({"tools": [{"name": "act", "description": "An action."}]}),
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "init",
            "--workspace",
            str(workspace),
            "--control-pack",
            pack_id,
            "--write",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["control_pack"]["selected"] == pack_id
    assert payload["control_pack"]["requested"] == pack_id
    assert [entry["id"] for entry in payload["control_pack"]["available"]] == list(
        CONTROL_PACK_IDS
    )
    assert payload["control_pack"]["manifest_path"] == "policies.control_pack"
    manifest_text = (workspace / "shipgate.yaml").read_text(encoding="utf-8")
    assert f"control_pack: {pack_id}" in manifest_text
    # The written manifest is the one the schema has to accept, including the
    # pack line — a glossary comment that renders invalid YAML would only be
    # found on the next command.
    import yaml

    manifest = AgentsShipgateManifest.model_validate(
        yaml.safe_load(manifest_text)
    )
    assert resolve_control_pack(manifest).id == pack_id


def test_init_reports_the_choice_even_when_it_writes_nothing(tmp_path: Path) -> None:
    """A caller about to re-run `init` needs to know what it may pass.

    `skipped_existing` is the common second run: the manifest is already
    there, so nothing is written, and the answer list is the only way the
    caller learns the flag exists.
    """

    workspace = tmp_path / "existing"
    workspace.mkdir()
    (workspace / "tools.json").write_text(
        json.dumps({"tools": [{"name": "act", "description": "An action."}]}),
        encoding="utf-8",
    )
    (workspace / "shipgate.yaml").write_text(
        """
version: "0.1"
project: {name: existing}
agent:
  name: agent
  declared_purpose: [act]
environment: {target: local}
tool_sources:
  - id: src
    type: mcp
    path: tools.json
policies:
  control_pack: financial-strict
""",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "init",
            "--workspace",
            str(workspace),
            "--write",
            "--json",
            "--control-pack",
            "read-only-agent",
        ],
    )
    payload = json.loads(result.stdout)
    assert payload["manifest_status"] == "skipped_existing"
    # The manifest on disk is the authority. Reporting the *request* here
    # would describe a file this run did not write (#399, one field over).
    assert payload["control_pack"]["selected"] == "financial-strict"
    assert payload["control_pack"]["requested"] == "read-only-agent"
    assert [entry["id"] for entry in payload["control_pack"]["available"]] == list(
        CONTROL_PACK_IDS
    )


def test_init_says_nothing_it_cannot_know_about_a_manifest_that_is_not_there(
    tmp_path: Path,
) -> None:
    """A dry run wrote no manifest, so no pack governs anything yet."""

    workspace = tmp_path / "dry"
    workspace.mkdir()
    (workspace / "tools.json").write_text(
        json.dumps({"tools": [{"name": "act", "description": "An action."}]}),
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "init",
            "--workspace",
            str(workspace),
            "--json",
            "--control-pack",
            "read-only-agent",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["control_pack"]["selected"] is None
    assert payload["control_pack"]["requested"] == "read-only-agent"
    # …and the template it *would* write carries the request.
    assert "control_pack: read-only-agent" in payload["template"]


def _init_workspace(tmp_path: Path, name: str) -> Path:
    workspace = tmp_path / name
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "tools.json").write_text(
        json.dumps({"tools": [{"name": "act", "description": "An action."}]}),
        encoding="utf-8",
    )
    return workspace


@pytest.mark.parametrize("pack_id", _NON_DEFAULT_PACKS)
def test_the_route_a_dry_run_emits_writes_the_pack_the_dry_run_was_asked_for(
    tmp_path: Path, pack_id: str
) -> None:
    """Run the emitted argv, not a reconstruction of it (#410 §F review).

    ``_requested_setup_flags`` did not carry ``--control-pack``, so a dry run
    with a non-default pack handed back a machine-readable command that wrote
    ``default`` — the agent-mode promise is that following the route completes
    the setup that was asked for.
    """

    workspace = _init_workspace(tmp_path, f"route-{pack_id}")
    dry = runner.invoke(
        app,
        ["init", "--workspace", str(workspace), "--control-pack", pack_id, "--json"],
    )
    assert dry.exit_code == 0, dry.output
    payload = json.loads(dry.stdout)
    argv = payload["next_actions"][0]["args"]
    assert f"--control-pack={pack_id}" in argv

    executed = runner.invoke(app, argv)
    assert executed.exit_code == 0, executed.output
    manifest = (workspace / "shipgate.yaml").read_text(encoding="utf-8")
    assert f"control_pack: {pack_id}" in manifest


def test_two_packs_do_not_share_one_route_identity(tmp_path: Path) -> None:
    """A cache keyed by the documented identity must not reuse another answer."""

    seen = {}
    for pack_id in CONTROL_PACK_IDS:
        workspace = _init_workspace(tmp_path, f"identity-{pack_id}")
        result = runner.invoke(
            app,
            [
                "init",
                "--workspace",
                str(workspace),
                "--control-pack",
                pack_id,
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        seen[pack_id] = json.loads(result.stdout)["control"]["input_id"]
    assert len(set(seen.values())) == len(CONTROL_PACK_IDS), seen


def test_the_unresolved_scope_route_carries_the_pack(tmp_path: Path) -> None:
    """The refusal route is a rerun too, and reruns repeat what was asked."""

    workspace = tmp_path / "multi"
    for name in ("alpha", "beta"):
        project = workspace / name
        project.mkdir(parents=True)
        (project / "pyproject.toml").write_text(
            f'[project]\nname = "{name}"\n', encoding="utf-8"
        )
        (project / "agent.py").write_text(
            "from google.adk.agents import Agent\n"
            "def act() -> str:\n    return 'ok'\n"
            f"root_agent = Agent(name='{name}', tools=[act])\n",
            encoding="utf-8",
        )
    result = runner.invoke(
        app,
        [
            "init",
            "--workspace",
            str(workspace),
            "--write",
            "--control-pack",
            "read-only-agent",
            "--json",
        ],
    )
    payload = json.loads(result.stdout)
    if payload["manifest_status"] != "refused_unresolved_scope":
        pytest.skip("workspace did not produce an unresolved scope")
    commands = [action.get("command") or "" for action in payload["next_actions"]]
    assert commands, payload
    assert any("--control-pack=read-only-agent" in command for command in commands), (
        commands
    )


def test_the_invalid_pack_recovery_repeats_the_run_it_corrects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A recovery that drops --workspace runs somewhere else entirely."""

    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    workspace = _init_workspace(tmp_path, "bad-pack")
    result = runner.invoke(
        app,
        [
            "init",
            "--workspace",
            str(workspace),
            "--write",
            "--json",
            "--ci",
            "--control-pack",
            "strict",
        ],
    )
    assert result.exit_code == 2
    # The structured route rides the error stream, beside the human line.
    payload = json.loads(
        next(
            line
            for line in reversed((result.stderr or result.output).splitlines())
            if line.startswith("{")
        )
    )
    argv = payload["next_actions"][0]["args"]
    assert "--workspace" in argv
    assert str(workspace.resolve()) in argv
    assert "--write" in argv
    assert "--json" in argv
    assert "--ci" in argv
    # The corrected value is the one the command assumes when the flag is
    # absent, so it is not repeated.
    assert not any(arg.startswith("--control-pack") for arg in argv)


def test_init_refuses_an_unknown_pack_before_writing_anything(tmp_path: Path) -> None:
    workspace = tmp_path / "bad"
    workspace.mkdir()
    result = runner.invoke(
        app,
        [
            "init",
            "--workspace",
            str(workspace),
            "--control-pack",
            "strict",
            "--write",
        ],
    )
    assert result.exit_code == 2
    assert "Unknown control pack" in result.output
    assert not (workspace / "shipgate.yaml").exists()


@pytest.mark.parametrize("declared", [True, False])
def test_doctor_reads_the_answer_back(tmp_path: Path, declared: bool) -> None:
    """An answer nothing repeats is an answer nobody can check.

    ``declared`` distinguishes "this repository chose default" from "this
    repository said nothing", which is the difference between an answered
    question and an unasked one.
    """

    workspace = tmp_path / f"doctor-{declared}"
    workspace.mkdir()
    (workspace / "tools.json").write_text(
        json.dumps({"tools": [{"name": "act", "description": "An action."}]}),
        encoding="utf-8",
    )
    policies = "policies:\n  control_pack: financial-strict\n" if declared else ""
    (workspace / "shipgate.yaml").write_text(
        f"""
version: "0.1"
project: {{name: packs}}
agent:
  name: agent
  declared_purpose: [act]
environment: {{target: local}}
tool_sources:
  - id: src
    type: mcp
    path: tools.json
{policies}""",
        encoding="utf-8",
    )
    result = runner.invoke(
        app, ["doctor", "-c", str(workspace / "shipgate.yaml"), "--json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)[0]["control_pack"]
    assert payload["declared"] is declared
    assert payload["id"] == ("financial-strict" if declared else "default")

    human = runner.invoke(app, ["doctor", "-c", str(workspace / "shipgate.yaml")])
    assert human.exit_code == 0, human.output
    assert f"Control pack: {payload['id']}" in human.output


def test_the_minimal_template_offers_the_same_choice(tmp_path: Path) -> None:
    """``--minimal`` is a different renderer; it must not describe packs differently."""

    workspace = tmp_path / "minimal"
    workspace.mkdir()
    result = runner.invoke(
        app,
        [
            "init",
            "--workspace",
            str(workspace),
            "--minimal",
            "--control-pack",
            "financial-strict",
            "--write",
        ],
    )
    assert result.exit_code == 0, result.output
    text = (workspace / "shipgate.yaml").read_text(encoding="utf-8")
    assert "control_pack: financial-strict" in text
    for pack_id in CONTROL_PACK_IDS:
        assert pack_id in text


# --------------------------------------------------------------------------
# Run identity
# --------------------------------------------------------------------------


def test_a_clean_surface_still_tells_two_packs_apart(tmp_path: Path) -> None:
    """Two manifests enforcing different policy are two different runs.

    ``run_id`` hashed the findings but not the rules that produced them, so a
    workspace clean under every pack — no control finding to differ — hashed
    identically under all three while enforcing three different policies
    (#410 §F review). The pack's obligations are hashed, not only its name,
    so a release that changes what ``default`` requires moves it too.
    """

    run_ids = {}
    for pack_id in CONTROL_PACK_IDS:
        report = _scan_effect(tmp_path / f"identity-{pack_id}", "read", pack_id=pack_id)
        assert report.findings == [] or all(
            finding.check_id not in _CONTROL_CHECKS for finding in report.findings
        ), "fixture must be clean of control findings for this to mean anything"
        run_ids[pack_id] = report.run_id
    assert len(set(run_ids.values())) == len(CONTROL_PACK_IDS), run_ids


def test_the_run_identity_covers_what_a_pack_requires_not_only_its_name() -> None:
    from agents_shipgate.core.control_packs import DEFAULT_CONTROL_PACK

    identity = DEFAULT_CONTROL_PACK.run_identity()
    assert identity["id"] == DEFAULT_CONTROL_PACK_ID
    assert identity["version"] == DEFAULT_CONTROL_PACK.version
    assert identity["obligations"]["financial_write"] == sorted(
        DEFAULT_CONTROL_PACK.obligations_for("financial_write")
    )


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _manifest_dict() -> dict:
    return {
        "version": "0.1",
        "project": {"name": "packs"},
        "agent": {"name": "agent", "declared_purpose": ["act"]},
        "environment": {"target": "local"},
        "tool_sources": [{"id": "src", "type": "mcp", "path": "tools.json"}],
    }


def _scan_effect(
    workspace: Path,
    effect: str,
    *,
    pack_id: str | None = None,
    extra: str = "",
    declared_risk_tags: list[str] | None = None,
    declared_controls: frozenset[str] | None = None,
):
    """Scan a one-action workspace declaring ``effect`` and no controls."""

    from agents_shipgate.cli.scan import run_scan

    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "tools.json").write_text(
        json.dumps({"tools": [{"name": "act", "description": "An action."}]}),
        encoding="utf-8",
    )
    policies = (
        f"policies:\n  control_pack: {pack_id}\n" if pack_id is not None else ""
    )
    risk_tags = ""
    if declared_risk_tags:
        rendered = ", ".join(declared_risk_tags)
        risk_tags = f"      risk_tags: [{rendered}]\n"
    # The three places a control can be declared, which is the point: five of
    # the six are fields on the action row and ``confirmation.required`` is
    # not writable there at all.
    controls = declared_controls or frozenset()
    approval = "      approval:\n        required: true\n" if (
        "approval.required" in controls
    ) else ""
    safeguards = sorted(
        path.split(".", 1)[1] for path in controls if path.startswith("safeguards.")
    )
    safeguard_block = ""
    if safeguards:
        safeguard_block = "      safeguards:\n" + "".join(
            f"        {name}: true\n" for name in safeguards
        )
    if "confirmation.required" in controls:
        policies = policies or "policies:\n"
        policies += "  require_confirmation_for_tools:\n    - tool: act\n"
    (workspace / "shipgate.yaml").write_text(
        f"""
version: "0.1"
project: {{name: packs}}
agent:
  name: agent
  declared_purpose: [act]
environment: {{target: local}}
tool_sources:
  - id: src
    type: mcp
    path: tools.json
agent_bindings:
  declarations:
    - agent: root
      complete: true
      tools: [{{tool: act, source_id: src}}]
      handoffs: []
      reason: reviewed test binding
action_surface:
  actions:
    - tool: act
      source_id: src
      effect: {effect}
{risk_tags}      authority:
        mode: none
{approval}{safeguard_block}{policies}{extra}
""",
        encoding="utf-8",
    )
    report, _ = run_scan(
        config_path=workspace / "shipgate.yaml",
        output_dir=workspace / "out",
        formats=["json", "markdown"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    return report


_CONTROL_CHECKS = frozenset(
    {
        "SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING",
        "SHIP-ACTION-EXTERNAL-COMMUNICATION-AUDIT-MISSING",
        "SHIP-ACTION-DESTRUCTIVE-ROLLBACK-MISSING",
        "SHIP-ACTION-POLICY-VIOLATION",
    }
)


def _missing_control_paths(report) -> set[str]:
    paths: set[str] = set()
    for finding in report.findings:
        if finding.check_id not in _CONTROL_CHECKS:
            continue
        raw = finding.evidence.get("missing")
        if not isinstance(raw, list):
            continue
        for item in raw:
            if isinstance(item, str):
                paths.add(item)
            elif isinstance(item, dict) and isinstance(item.get("path"), str):
                paths.add(item["path"])
    return paths


def _fingerprints(report) -> set[str]:
    return {
        finding.fingerprint
        for finding in report.findings
        if finding.fingerprint is not None
    }


def _control_fingerprints(report) -> set[str]:
    return {
        finding.fingerprint
        for finding in report.findings
        if finding.fingerprint is not None and finding.check_id in _CONTROL_CHECKS
    }


def _non_control_fingerprints(report) -> set[str]:
    return {
        finding.fingerprint
        for finding in report.findings
        if finding.fingerprint is not None and finding.check_id not in _CONTROL_CHECKS
    }


def _control_rows(report) -> dict[tuple[str, str], set[str]]:
    """``(check_id, tool_id) -> missing control paths`` for the control families."""

    rows: dict[tuple[str, str], set[str]] = {}
    for finding in report.findings:
        if finding.check_id not in _CONTROL_CHECKS:
            continue
        key = (finding.check_id, finding.tool_id or finding.tool_name or "")
        raw = finding.evidence.get("missing")
        paths = rows.setdefault(key, set())
        if not isinstance(raw, list):
            continue
        for item in raw:
            if isinstance(item, str):
                paths.add(item)
            elif isinstance(item, dict) and isinstance(item.get("path"), str):
                paths.add(item["path"])
    return rows
