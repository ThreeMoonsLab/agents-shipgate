"""The declaration scaffold assembles what the engine already generates.

It must never assert a value a human owns, and it must never be mistaken for
something that closes a gap on its own.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from agents_shipgate.cli.scan.declarations import build_declaration_scaffold
from agents_shipgate.schemas.report import EvidenceGap, EvidenceGapAction


def _report(gaps: list[EvidenceGap]) -> list[EvidenceGap]:
    return gaps


def _gap(kind: str, path: str, template: dict | None) -> EvidenceGap:
    return EvidenceGap(
        kind=kind,  # type: ignore[arg-type]
        subject="lookup_order [openai_sdk_agent]",
        source_type="sdk_function",
        source_ref="risk_hint",
        why="test",
        next_action=EvidenceGapAction(
            kind="declare_action_effect",  # type: ignore[arg-type]
            path=path,
            why="test",
            expects="test",
            declaration_template=template,
        ),
    )


def test_scaffold_is_none_when_nothing_is_owed() -> None:
    assert build_declaration_scaffold(_report([])) is None
    without_template = _report([_gap("incomplete_surface", "shipgate.yaml", None)])
    assert build_declaration_scaffold(without_template) is None


def test_two_gaps_on_one_tool_merge_into_one_pasteable_row() -> None:
    """Two blocks for one tool would be invalid to paste into the manifest."""

    path = "shipgate.yaml#action_surface.actions[tool='lookup_order']"
    report = _report(
        [
            _gap("inferred_effect_only", path, {"tool": "lookup_order", "effect": "<REVIEW_REQUIRED>"}),
            _gap(
                "missing_authority_evidence",
                path,
                {"tool": "lookup_order", "authority": {"mode": "<REVIEW_REQUIRED>"}},
            ),
        ]
    )
    scaffold = build_declaration_scaffold(report)
    assert scaffold is not None
    assert scaffold.count("tool: lookup_order") == 1
    assert "closes: inferred_effect_only, missing_authority_evidence" in scaffold

    body = yaml.safe_load(scaffold)
    assert body == {
        "tool": "lookup_order",
        "effect": "<REVIEW_REQUIRED>",
        "authority": {"mode": "<REVIEW_REQUIRED>"},
    }


def test_scaffold_asserts_nothing_and_says_so() -> None:
    report = _report(
        [
            _gap(
                "inferred_effect_only",
                "shipgate.yaml#action_surface.actions[tool='lookup_order']",
                {"tool": "lookup_order", "effect": "<REVIEW_REQUIRED>"},
            )
        ]
    )
    scaffold = build_declaration_scaffold(report)
    assert scaffold is not None
    # The value a human owns is never guessed...
    assert "<REVIEW_REQUIRED>" in scaffold
    # ...and the file says a sentinel closes nothing, so a reader cannot
    # mistake pasting it verbatim for satisfying the gap.
    assert "closes nothing" in scaffold


def test_distinct_tools_stay_separate_blocks() -> None:
    report = _report(
        [
            _gap(
                "inferred_effect_only",
                "shipgate.yaml#action_surface.actions[tool='a']",
                {"tool": "a", "effect": "<REVIEW_REQUIRED>"},
            ),
            _gap(
                "inferred_effect_only",
                "shipgate.yaml#action_surface.actions[tool='b']",
                {"tool": "b", "effect": "<REVIEW_REQUIRED>"},
            ),
        ]
    )
    scaffold = build_declaration_scaffold(report)
    assert scaffold is not None
    assert "tool: a" in scaffold
    assert "tool: b" in scaffold
    assert scaffold.count("# merge into:") == 2


def test_gap_provenance_distinguishes_inherited_from_introduced(tmp_path) -> None:
    """An abstention a repository already owed must not read as an accusation
    about the current diff — and a genuinely new gap must still say so."""

    import json as _json

    from agents_shipgate.cli.verify.orchestrator import (
        _evidence_gap_identities,
        _gap_provenance_note,
    )
    from agents_shipgate.schemas.report import (
        EvidenceCoverageDecision,
        ReleaseDecision,
    )

    def _report_with(gaps: list[EvidenceGap]):
        class _R:
            release_decision = ReleaseDecision.model_construct(
                decision="insufficient_evidence",
                reason="test",
                evidence_coverage=EvidenceCoverageDecision.model_construct(
                    level="mixed", evidence_gaps=gaps
                ),
            )

        return _R()

    effect = _gap(
        "inferred_effect_only",
        "shipgate.yaml#action_surface.actions[tool='a']",
        {"tool": "a", "effect": "<REVIEW_REQUIRED>"},
    )
    authority = _gap(
        "missing_authority_evidence",
        "shipgate.yaml#action_surface.actions[tool='b']",
        {"tool": "b", "authority": {"mode": "<REVIEW_REQUIRED>"}},
    )

    def _base_file(gaps: list[EvidenceGap]) -> Path:
        path = tmp_path / f"base-{len(gaps)}.json"
        path.write_text(
            _json.dumps(
                {
                    "release_decision": {
                        "evidence_coverage": {
                            "evidence_gaps": [
                                {"kind": g.kind, "subject": g.subject} for g in gaps
                            ]
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        return path

    # Same gap set on both sides: inherited.
    inherited = _gap_provenance_note(
        report=_report_with([effect]), base_report=_base_file([effect])
    )
    assert inherited is not None
    assert "no new evidence gap" in inherited
    assert "suggested-declarations.yaml" in inherited

    # A gap absent from the base is introduced by this diff.
    introduced = _gap_provenance_note(
        report=_report_with([effect, authority]), base_report=_base_file([effect])
    )
    assert introduced is not None
    assert "1 of 2 evidence gap(s) are new" in introduced

    # Without a readable base there is no basis to claim anything.
    assert (
        _gap_provenance_note(report=_report_with([effect]), base_report=None) is None
    )
    missing = tmp_path / "nope.json"
    assert (
        _gap_provenance_note(report=_report_with([effect]), base_report=missing) is None
    )
    unreadable = tmp_path / "bad.json"
    unreadable.write_text("not json{", encoding="utf-8")
    assert (
        _gap_provenance_note(report=_report_with([effect]), base_report=unreadable)
        is None
    )
    assert _evidence_gap_identities("nonsense") is None


def test_authority_template_is_fillable_against_the_manifest_schema() -> None:
    """A template a human cannot fill is worse than no template.

    The manifest requires `auth_type` for every authority mode except `none`,
    and non-empty `scopes` for `scoped`. A template offering `mode` alone
    produced a config error for the most common answer, which nobody noticed
    while the templates were only reachable by walking report.json.
    """

    from agents_shipgate.schemas.manifest.action_surface import ActionDeclarationConfig

    # Shape of the shipped template, with a reviewer's answers filled in.
    filled = {
        "tool": "process_order",
        "effect": "write",
        "scopes": ["orders:write"],
        "authority": {"mode": "scoped", "auth_type": "api_key"},
    }
    declaration = ActionDeclarationConfig.model_validate(filled)
    assert declaration.authority is not None
    assert declaration.authority.mode == "scoped"

    # `none` takes neither co-required field, which is why the scaffold tells
    # the reviewer to delete what their answer does not take.
    minimal = ActionDeclarationConfig.model_validate(
        {"tool": "process_order", "effect": "read", "authority": {"mode": "none"}}
    )
    assert minimal.authority is not None


def test_no_shipped_template_asserts_on_a_humans_behalf() -> None:
    """A template must ask, never answer.

    The binding template once shipped `complete: true`, `tools: []` and
    `handoffs: []` — a claim that the agent definitively reaches no tools —
    which a reviewer could paste while sentinels were still present. Every
    scalar a template offers must therefore be a sentinel, and every list must
    be empty or sentinel-filled, so a verbatim paste cannot state a fact.
    """

    from agents_shipgate.ci.release_decision import REVIEW_REQUIRED_SENTINEL

    def assert_no_assertion(node, path: str = "") -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                assert_no_assertion(value, f"{path}.{key}")
            return
        if isinstance(node, list):
            for index, value in enumerate(node):
                assert_no_assertion(value, f"{path}[{index}]")
            return
        # A tool NAME is the subject the gap is about, not a claim about it.
        if path.endswith(".tool"):
            return
        assert node == REVIEW_REQUIRED_SENTINEL, (
            f"{path} = {node!r} asserts a value the human owns"
        )

    for template in _shipped_templates():
        assert_no_assertion(template)


def _shipped_templates() -> list[dict]:
    """Every declaration_template the decision engine can emit."""

    import agents_shipgate.ci.release_decision as rd
    from agents_shipgate.core.domain import Tool

    tool = Tool(
        id="t1",
        name="process_order",
        source_type="sdk_function",
        source_id="openai_sdk_agent",
    )
    # The binding root template is emitted from _binding_coverage, not
    # _semantic_gap, so enumerate it explicitly — a guard that misses the
    # template which actually carried an assertion is false confidence.
    templates: list[dict] = [dict(rd.AGENT_BINDINGS_ROOT_TEMPLATE)]
    for kind in (
        "inferred_effect_only",
        "missing_authority_evidence",
        "partial_authority_evidence",
        "unresolved_tool_selector",
        "incomplete_surface",
    ):
        gap = rd._semantic_gap(tool, kind=kind, why="test")
        template = gap.next_action.declaration_template
        if template:
            templates.append(template)
    assert templates, "expected at least one shipped template"
    return templates
