"""STABILITY.md documents exactly the keys ``agents-shipgate contract --json`` emits.

The "Stable JSON fields" list under ``### Runtime contract JSON`` is hand-written
prose, but the key set it covers is ``ContractPayload`` as the command
serializes it. These tests hold the two sets equal in both directions, so an
integrator never meets an emitted key the contract does not name (#1054), and
keep the ``[]`` / ``{}`` suffix honest about the JSON type.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app

STABILITY = Path(__file__).resolve().parents[1] / "STABILITY.md"
SECTION_HEADING = "### Runtime contract JSON"
LIST_INTRO = "Stable JSON fields:"
#: The separator between a bullet's key part and its description.
DESCRIPTION_DASH = " — "
KEY_PATTERN = re.compile(r"`([a-z][a-z0-9_]*)(\[\]|\{\})?`")


def _emitted_payload() -> dict[str, Any]:
    """The JSON the installed command prints, not a model the test re-dumps."""

    result = CliRunner().invoke(app, ["contract", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert isinstance(payload, dict) and payload
    return payload


def _list_bullets(text: str) -> list[str]:
    """Each top-level bullet of the list, with its wrapped lines joined."""

    start = text.index(SECTION_HEADING)
    end = text.find("\n### ", start + len(SECTION_HEADING))
    section = text[start : end if end != -1 else len(text)]
    assert section.count(LIST_INTRO) == 1, (
        f"{SECTION_HEADING} must introduce its key list with one {LIST_INTRO!r} line."
    )
    bullets: list[str] = []
    for line in section.split(LIST_INTRO, 1)[1].splitlines():
        if line.startswith("- "):
            bullets.append(line[2:])
        elif line.startswith("  ") and line.strip() and bullets:
            bullets[-1] += " " + line.strip()
        elif line.strip():
            break  # the first paragraph after the list ends it
    return bullets


def _documented_fields() -> list[tuple[str, str]]:
    """``(key, suffix)`` for every key a bullet names before its dash."""

    fields: list[tuple[str, str]] = []
    for bullet in _list_bullets(STABILITY.read_text(encoding="utf-8")):
        assert DESCRIPTION_DASH in bullet, (
            f"Stable JSON fields bullet has no {DESCRIPTION_DASH.strip()!r} "
            f"between its keys and their description: {bullet[:120]!r}"
        )
        names = KEY_PATTERN.findall(bullet.split(DESCRIPTION_DASH, 1)[0])
        assert names, f"Stable JSON fields bullet names no key: {bullet[:120]!r}"
        fields.extend(names)
    return fields


def test_every_emitted_contract_key_is_documented() -> None:
    documented = {key for key, _ in _documented_fields()}
    undocumented = sorted(set(_emitted_payload()) - documented)
    assert not undocumented, (
        f"`contract --json` emits {len(undocumented)} key(s) that STABILITY.md "
        f"§ Runtime contract JSON does not list: {undocumented}. Document each "
        "under 'Stable JSON fields', marking any whose surface is provisional."
    )


def test_every_documented_contract_key_is_emitted() -> None:
    emitted = set(_emitted_payload())
    phantom = sorted({key for key, _ in _documented_fields()} - emitted)
    assert not phantom, (
        f"STABILITY.md § Runtime contract JSON lists {phantom}, which "
        "`contract --json` does not emit. Removing a stable key needs the "
        "deprecation cycle in STABILITY.md; otherwise fix the spelling."
    )


def test_each_contract_key_is_documented_once() -> None:
    counts = Counter(key for key, _ in _documented_fields())
    repeated = sorted(key for key, count in counts.items() if count > 1)
    assert not repeated, f"Stable JSON fields lists these keys more than once: {repeated}"


@pytest.mark.parametrize(
    ("json_type", "suffix"),
    [(list, "[]"), (dict, "{}")],
    ids=["array", "object"],
)
def test_the_documented_suffix_states_the_json_type(json_type: type, suffix: str) -> None:
    """STABILITY.md writes an array key as ``key[]`` and an object key as ``key{}``."""

    payload = _emitted_payload()
    documented = dict(_documented_fields())
    wrong = sorted(
        f"{key}{documented[key]}"
        for key, value in payload.items()
        if key in documented and isinstance(value, json_type) != (documented[key] == suffix)
    )
    assert not wrong, (
        f"These keys' suffixes misstate whether the value is a JSON "
        f"{json_type.__name__}; it is spelled `key{suffix}` exactly when it is: {wrong}"
    )
