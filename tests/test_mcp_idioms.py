"""The built-in MCP registration-idiom registry and its reader (#431).

The reader turns source text into tool names, so every test here is really
asking one of two questions: *can it be made to invent a tool?* and *can it be
made to lose one silently?* The second is the reason this input exists — the
gap it closes is three vendor servers reported as "not an agent project" — so a
construct it cannot resolve has to become a recorded omission, never a gap in
the output nobody can see.

The adversarial sweep at the bottom is the probe list #393 requires of anything
that publishes an extraction claim. Eight of its cases were fail-open in the
first draft of this reader.
"""

from __future__ import annotations

import pytest

from agents_shipgate.inputs.mcp_idioms import (
    DIFF_TOKENS,
    IDIOMS,
    IDIOMS_BY_ID,
    LANGUAGE_EXTENSIONS,
    OMISSION_REASONS,
    PREFILTER_TOKEN,
    SourceScanResult,
    decode_literal,
    is_scannable_path,
    language_for_path,
    mask_source,
    scan_source,
)
from tests.mcp_idiom_corpus import (
    ADVERSARIAL,
    ESCAPE_CASES,
    MASKING_FAILURES,
    POSITIVE_SAMPLES,
    REGRESSIONS,
    SCANNABLE_PATHS,
)


def _names(text: str, language: str = "typescript") -> list[str]:
    return [site.name for site in scan_source(text, language).sites if site.name]


def _unresolved(text: str, language: str = "typescript") -> list[str]:
    return [
        site.unresolved_reason
        for site in scan_source(text, language).sites
        if site.name is None
    ]


# --- Registry shape ---------------------------------------------------------


def test_every_idiom_has_a_language_this_reader_can_read():
    for idiom in IDIOMS:
        assert idiom.language in LANGUAGE_EXTENSIONS
        assert idiom.id == idiom.id.lower()
        assert idiom.diff_tokens


def test_idiom_ids_are_unique():
    assert len(IDIOMS_BY_ID) == len(IDIOMS)


def test_the_prefilter_cannot_hide_an_idiom_this_reader_matches():
    """``scan_source`` skips a file without the prefilter token.

    That shortcut is only sound while every idiom's pattern requires the token.
    The first draft put the prefilter at the *call site* and keyed it off the
    trigger catalog's ``diff_tokens`` — which do not include the substring
    ``static readonly toolName`` carries — and four MongoDB tools were reported
    by ``scan`` and never by ``detect``.
    """

    for sample in POSITIVE_SAMPLES.values():
        assert PREFILTER_TOKEN in sample.text.lower(), sample.idiom


def test_jsx_is_out_of_scope_so_prose_never_opens_a_string():
    """JSX puts prose in code position; an apostrophe would read as a quote."""

    assert ".tsx" not in LANGUAGE_EXTENSIONS["typescript"]
    assert ".jsx" not in LANGUAGE_EXTENSIONS["typescript"]
    assert language_for_path("app/Panel.tsx") is None


@pytest.mark.parametrize(
    ("path", "scannable"), SCANNABLE_PATHS, ids=[case[0] for case in SCANNABLE_PATHS]
)
def test_scannable_paths(path: str, scannable: bool):
    assert is_scannable_path(path) is scannable


# --- Positive samples, one per idiom ----------------------------------------


def test_every_idiom_has_a_positive_sample():
    assert set(POSITIVE_SAMPLES) == set(IDIOMS_BY_ID)


@pytest.mark.parametrize("idiom_id", sorted(POSITIVE_SAMPLES))
def test_positive_sample_resolves_exactly_its_own_tool(idiom_id: str):
    sample = POSITIVE_SAMPLES[idiom_id]
    result = scan_source(sample.text, sample.language)
    resolved = [site for site in result.sites if site.name is not None]
    assert [site.name for site in resolved] == [sample.name]
    assert resolved[0].idiom == idiom_id
    assert result.anomalies == ()


@pytest.mark.parametrize("idiom_id", sorted(POSITIVE_SAMPLES))
def test_published_diff_tokens_appear_in_the_sample_they_route(idiom_id: str):
    """The trigger catalog routes on these tokens, so they must be real.

    A token nobody can produce routes nothing, and nothing in the catalog file
    would say so — the rule would read as coverage it does not have.
    """

    sample = POSITIVE_SAMPLES[idiom_id]
    for token in IDIOMS_BY_ID[idiom_id].diff_tokens:
        assert token in sample.text, (idiom_id, token)


def test_ts_static_field_reads_the_sibling_operation_class_and_description():
    sample = POSITIVE_SAMPLES["ts_static_tool_name"]
    site = scan_source(sample.text, "typescript").sites[0]
    assert site.operation_type == "delete"
    assert site.description == "Removes the specified database"


def test_a_go_struct_description_is_read_only_from_its_own_level():
    sample = POSITIVE_SAMPLES["go_tool_struct"]
    site = scan_source(sample.text, "go").sites[0]
    assert site.description == "Get information about an issue"


def test_a_static_field_outside_a_class_still_names_its_tool():
    """The enclosing block supplies siblings; its absence is not a failure."""

    case = REGRESSIONS["static_field_outside_a_class"]
    sites = scan_source(case.text, case.language).sites
    assert [site.name for site in sites] == ["loose"]
    assert sites[0].operation_type is None


def test_a_modifier_between_static_and_the_field_is_read():
    """``public static readonly toolName: string = "…"`` is MongoDB's spelling
    in four of its packages, and it is not ``static toolName``."""

    assert _names(REGRESSIONS["modifier_between_static_and_the_field"].text) == [
        "get_response"
    ]


@pytest.mark.parametrize(
    ("case", "language", "text", "expected_names", "expected_unresolved"),
    ADVERSARIAL,
    ids=[case[0] for case in ADVERSARIAL],
)
def test_adversarial_constructs(
    case: str,
    language: str,
    text: str,
    expected_names: list[str],
    expected_unresolved: list[str],
):
    result = scan_source(text, language)
    assert sorted(site.name for site in result.sites if site.name) == sorted(
        expected_names
    ), case
    assert sorted(
        site.unresolved_reason for site in result.sites if site.name is None
    ) == sorted(expected_unresolved), case
    for site in result.sites:
        if site.unresolved_reason is not None:
            assert site.unresolved_reason in OMISSION_REASONS


# --- Regressions from review ------------------------------------------------


def test_a_class_that_registers_elsewhere_keeps_its_own_omission():
    """A lookup scope is not a wrapper.

    The unresolved field site needs the class body to find its sibling
    `operationType` and `description` literals. Carrying that body as the
    site's *span* made the containment rule read any registration written
    inside the class as "the same registration", so a class whose `toolName`
    is built at runtime lost its omission the moment its body also called
    `.registerTool(` — neither enumerated nor recorded, which is the silent
    miss this input exists to end.
    """

    case = REGRESSIONS["class_that_registers_elsewhere"]
    result = scan_source(case.text, case.language)

    assert sorted(site.name for site in result.sites if site.name) == ["inner"]
    assert [
        site.unresolved_reason for site in result.sites if site.name is None
    ] == ["name_not_literal"]


def test_an_attribute_assignment_is_not_the_description_field():
    """`this.description = …` is not the class's description.

    The lookbehind admitted a leading dot, and `_first_literal_in` returns the
    first match in the body, so an assignment in a constructor won over the
    real field and published a description that is not the tool's — into the
    report and into the declaration questionnaire a reviewer answers from.
    """

    case = REGRESSIONS["attribute_assignment_is_not_the_description"]
    site = scan_source(case.text, case.language).sites[0]

    assert site.name == "t"
    assert site.description == "Runs an aggregation"


@pytest.mark.parametrize(
    ("body", "language", "expected"),
    ESCAPE_CASES,
    ids=[f"{language}:{body}" for body, language, _expected in ESCAPE_CASES],
)
def test_each_language_decodes_with_its_own_grammar(
    body: str, language: str, expected: str | None
):
    """One decoder shared between two grammars is a silent mistranslation.

    Go writes an octal escape as three digits and JavaScript does not, so a
    JavaScript-shaped decoder turned `delete\\137all` — the name Go registers as
    `delete_all` — into `delete137all`: the real action missing from the
    catalog and an action id nobody serves standing in for it. And what a
    grammar does not define is refused rather than guessed, because a refusal
    becomes a recorded omission and a guess becomes a wrong tool name.
    """

    assert decode_literal(body, language) == expected


# --- Masking failures -------------------------------------------------------


def test_an_unterminated_block_comment_is_an_anomaly_not_silence():
    """Past an unterminated comment nothing is known, so say so.

    Reporting no sites would be indistinguishable from a file that registers
    nothing — the exact ambiguity this input exists to remove.
    """

    case = MASKING_FAILURES["unterminated_block_comment"]
    result = scan_source(case.text, case.language)
    assert result.sites == ()
    assert result.anomalies == ("unterminated_block_comment",)


def test_an_unterminated_string_is_an_anomaly_and_resyncs_at_the_line():
    case = MASKING_FAILURES["unterminated_string_resyncs_at_the_line"]
    result = scan_source(case.text, case.language)
    assert "unterminated_string" in result.anomalies
    assert [site.name for site in result.sites] == ["real"]


def test_an_unterminated_go_raw_string_is_an_anomaly():
    case = MASKING_FAILURES["unterminated_go_raw_string"]
    result = scan_source(case.text, case.language)
    assert result.anomalies == ("unterminated_string",)
    # And the registration past it is not reported, because past the unclosed
    # raw string this reader cannot tell code from content.
    assert result.sites == ()


def test_masking_preserves_offsets_so_line_numbers_are_the_file_s():
    case = REGRESSIONS["masking_preserves_offsets"]
    masked = mask_source(case.text, case.language)
    assert len(masked.masked) == len(case.text)
    assert scan_source(case.text, case.language).sites[0].line == 3


def test_a_file_without_the_prefilter_token_is_answered_without_masking(monkeypatch):
    """The shortcut has to be observable, or the test is about nothing.

    Comparing the result to an empty scan passes whether or not the prefilter
    exists — both are empty either way — so a perturbation that deleted the
    prefilter entirely went uncaught. The claim is that the file is answered
    *without masking it*, so that is what is asserted: `mask_source` is never
    reached.
    """

    import agents_shipgate.inputs.mcp_idioms as module

    def _fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("a file with no registration token was masked")

    monkeypatch.setattr(module, "mask_source", _fail)
    case = REGRESSIONS["no_registration_token"]
    assert scan_source(case.text, case.language) == SourceScanResult()

    # And the shortcut is only sound because it can never hide a real
    # registration: every idiom's own sample carries the token, which
    # `test_the_prefilter_cannot_hide_an_idiom_this_reader_matches` pins.
    monkeypatch.undo()
    assert scan_source(
        POSITIVE_SAMPLES["go_must_tool"].text, "go"
    ).sites


# --- The published token list ----------------------------------------------


def test_published_diff_tokens_are_the_union_of_the_idioms():
    assert DIFF_TOKENS == tuple(
        sorted({token for idiom in IDIOMS for token in idiom.diff_tokens})
    )
