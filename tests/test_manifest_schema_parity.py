"""The published manifest schema must reject what the CLI rejects.

``docs/manifest-v0.1.json`` is advertised for live editor validation. A schema
that accepts a manifest the runtime refuses is worse than no schema: it tells a
user their file is valid and then the CLI stops them, which is the failure this
file exists to prevent (PR #411 review 4).

Parity is asserted in the direction that matters. Every payload here is run
through *both* validators and they must agree. The runtime is allowed to be
stricter than a portable ECMA-262 character class can express (surrogate,
private-use, and unassigned code points cannot be enumerated in one), so the
cases below stay inside what both can decide.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from agents_shipgate.schemas.manifest import AgentsShipgateManifest

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_SCHEMA = json.loads(
    (REPO_ROOT / "docs" / "manifest-v0.1.json").read_text(encoding="utf-8")
)
VALIDATOR = Draft202012Validator(MANIFEST_SCHEMA)


def _manifest(action: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": "0.1",
        "project": {"name": "parity"},
        "agent": {"name": "a", "declared_purpose": ["do one thing"]},
        "environment": {"target": "local"},
        "tool_sources": [{"id": "s", "type": "mcp", "path": "tools.json"}],
        "action_surface": {"actions": [action]},
    }


def _schema_accepts(payload: dict[str, Any]) -> bool:
    return not list(VALIDATOR.iter_errors(payload))


def _runtime_accepts(payload: dict[str, Any]) -> bool:
    try:
        AgentsShipgateManifest.model_validate(payload)
    except ValidationError:
        return False
    return True


_VALID_OVERRIDE = {
    "evidence": "agent.py returns a cached row",
    "reason": "no outbound client is constructed",
}

_CASES: list[tuple[str, dict[str, Any], bool]] = [
    (
        "override with a declared effect",
        {"tool": "t", "effect": "read", "override": dict(_VALID_OVERRIDE)},
        True,
    ),
    (
        "override with no declared effect",
        {"tool": "t", "override": dict(_VALID_OVERRIDE)},
        False,
    ),
    (
        "override beside an explicitly null effect",
        {"tool": "t", "effect": None, "override": dict(_VALID_OVERRIDE)},
        False,
    ),
    (
        "empty evidence",
        {"tool": "t", "effect": "read", "override": {**_VALID_OVERRIDE, "evidence": ""}},
        False,
    ),
    (
        "whitespace-only reason",
        {"tool": "t", "effect": "read", "override": {**_VALID_OVERRIDE, "reason": "   "}},
        False,
    ),
    (
        "zero-width evidence",
        {
            "tool": "t",
            "effect": "read",
            "override": {**_VALID_OVERRIDE, "evidence": "​⁠"},
        },
        False,
    ),
    (
        "bidi-control reason",
        {
            "tool": "t",
            "effect": "read",
            "override": {**_VALID_OVERRIDE, "reason": "‪‬"},
        },
        False,
    ),
    (
        "control-character evidence",
        {
            "tool": "t",
            "effect": "read",
            "override": {**_VALID_OVERRIDE, "evidence": "\x01\x02"},
        },
        False,
    ),
    (
        "variation-selector reason",
        {
            "tool": "t",
            "effect": "read",
            "override": {**_VALID_OVERRIDE, "reason": "️"},
        },
        False,
    ),
    (
        "padded but visible reason",
        {
            "tool": "t",
            "effect": "read",
            "override": {**_VALID_OVERRIDE, "reason": "  it renders a draft  "},
        },
        True,
    ),
    (
        "no override at all",
        {"tool": "t", "effect": "read"},
        True,
    ),
]


@pytest.mark.parametrize(
    ("label", "action", "expected"),
    _CASES,
    ids=[case[0].replace(" ", "-") for case in _CASES],
)
def test_published_schema_and_runtime_agree(
    label: str, action: dict[str, Any], expected: bool
) -> None:
    payload = _manifest(action)
    schema_ok = _schema_accepts(payload)
    runtime_ok = _runtime_accepts(payload)

    assert runtime_ok is expected, f"runtime disagreed on {label}"
    assert schema_ok is expected, (
        f"docs/manifest-v0.1.json disagreed on {label}: schema accepts="
        f"{schema_ok}, runtime accepts={runtime_ok}. The published schema is "
        "advertised for live editor validation; encode the invariant there too."
    )


def test_the_published_pattern_is_usable_by_a_json_schema_validator() -> None:
    """Negative control for the pattern's own portability.

    ``\\u{...}`` is ECMA-262-with-``u``-flag only and Python's ``re`` rejects it
    outright, so a pattern written that way would make every astral range
    unusable by the validators that actually consume this file.
    """

    override = MANIFEST_SCHEMA["$defs"]["ActionEffectOverrideConfig"]
    pattern = override["properties"]["evidence"]["pattern"]

    import re

    compiled = re.compile(pattern)
    assert compiled.search("visible")
    assert not compiled.search("​⁠")
    assert not compiled.search("\U000e0001")
