"""The support page names the host surfaces no adapter reads (#701, #702).

A composite action or a hook-run script edit produces no row. Until a reader
exists, the published boundary has to say so, or advertised coverage would
exceed measured behavior.
"""

from __future__ import annotations

from pathlib import Path

PAGE = Path(__file__).resolve().parents[1] / "docs" / "host-boundary-support.md"


def _section() -> str:
    text = PAGE.read_text(encoding="utf-8")
    start = text.index("### Known unread surfaces")
    end = text.find("\n#", start + 1)
    return text[start : end if end != -1 else len(text)]


def test_composite_actions_are_named_as_unread() -> None:
    section = _section()
    assert ".github/actions/<name>/action.yml" in section
    assert "(#701)" in section


def test_hook_run_scripts_are_named_as_unread() -> None:
    section = _section()
    assert "script a hook command runs" in section
    assert "(#702)" in section
