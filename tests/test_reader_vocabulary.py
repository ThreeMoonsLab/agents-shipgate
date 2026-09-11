"""#652: the sentence a person reads must be in their language.

Every shipped fixture is run and its human-facing strings — the headline,
`control.reason`, and the `why` on the next action — are checked for engine
vocabulary. These are the fields a reader meets first, and they were
carrying field paths and internal nouns: `control.state`, `input_set_id`,
`binding graph`, `Route H`.

The enum values themselves are untouched. `release_decision.decision` is
still `insufficient_evidence`, because a machine consumer gates on it; what
must not appear is the *identifier* in a sentence written for a person.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app

runner = CliRunner()
REPO_ROOT = Path(__file__).resolve().parents[1]

#: Terms that name how the engine is built rather than what happened. Each
#: is matched as it would be *written*, so the guard does not fire on an
#: enum value in a JSON field.
ENGINE_VOCABULARY = (
    "control.state",
    "input_set_id",
    "binding graph",
    "Route H",
    "exclusion ledger",
    "insufficient_evidence",
    "agent_boundary_result",
    "tool_surface_diff",
    "release_decision.decision",
)

#: The strings a person reads before anything else.
READER_FIELDS = ("headline", "summary", "reason", "why")


def _fixture_names() -> list[str]:
    result = subprocess.run(
        ["python", "-m", "agents_shipgate", "fixture", "list"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [
        line.split("\t", 1)[0].strip()
        for line in result.stdout.splitlines()
        if line.strip() and not line.startswith(" ")
    ]


def _reader_strings(payload: object, path: str = "") -> list[tuple[str, str]]:
    """Every human-facing string in a payload, with where it came from."""

    found: list[tuple[str, str]] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            here = f"{path}.{key}" if path else str(key)
            if isinstance(value, str) and key in READER_FIELDS:
                found.append((here, value))
            else:
                found.extend(_reader_strings(value, here))
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            found.extend(_reader_strings(item, f"{path}[{index}]"))
    return found


def _offenders(payload: object) -> list[str]:
    hits: list[str] = []
    for where, text in _reader_strings(payload):
        for term in ENGINE_VOCABULARY:
            if term in text:
                hits.append(f"{where}: {term!r} in {text[:120]!r}")
    return hits


@pytest.mark.parametrize("fixture", _fixture_names())
def test_no_shipped_fixture_speaks_engine_to_a_reader(
    fixture: str, tmp_path: Path
) -> None:
    result = runner.invoke(
        app, ["fixture", "run", fixture, "--out", str(tmp_path)]
    )
    assert result.exit_code in {0, 20}, result.output

    for artifact in ("verifier.json", "report.json"):
        path = tmp_path / artifact
        if not path.exists():
            continue
        offenders = _offenders(json.loads(path.read_text(encoding="utf-8")))
        assert not offenders, f"{fixture}/{artifact}: " + "; ".join(offenders)


def test_the_guard_fires_on_engine_vocabulary() -> None:
    """A negative control: a guard that never fires is not a guard."""

    assert _offenders({"control": {"reason": "read control.state first"}})
    assert _offenders({"headline": "verdict is insufficient_evidence"})
    assert _offenders({"next_action": {"why": "the binding graph is ambiguous"}})
    # An enum in a machine field is not a reader string and must not fire.
    assert not _offenders({"release_decision": {"decision": "insufficient_evidence"}})
    assert not _offenders({"merge_verdict": "insufficient_evidence"})


def test_reader_fields_are_actually_found() -> None:
    """The walker must reach nested reader strings, or the parametrized
    cases above pass by looking at nothing."""

    payload = {
        "control": {"reason": "a", "next_action": {"why": "b"}},
        "headline": "c",
        "rows": [{"why": "d"}],
    }

    assert {value for _, value in _reader_strings(payload)} == {"a", "b", "c", "d"}


def test_the_six_prominent_commands_are_the_ones_help_shows() -> None:
    """`--help` is where a stranger looks. Three of 53 was a keyhole."""

    result = runner.invoke(app, ["--help"])

    listed = re.findall(r"^│ ([a-z][a-z-]*)", result.output, re.M)
    assert listed == ["diff", "check", "verify", "audit", "init", "doctor"]


def test_help_all_lists_the_supporting_commands_too() -> None:
    """Prominence is a reading aid, not a claim about what exists."""

    result = runner.invoke(app, ["--help-all"])

    listed = set(re.findall(r"^│ ([a-z][a-z-]*)", result.output, re.M))
    assert {"diff", "check", "verify", "audit", "init", "doctor"} <= listed
    assert {"scan", "detect", "contract", "explain", "trigger"} <= listed
    assert len(listed) > 25
