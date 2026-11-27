"""#588: the identity reader types the entry and observation it refused on.

Every assertion reads the failure's attributes. None parses the message: the
message stays the prose existing callers match, and is not a public payload.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agents_shipgate.core import trust_roots
from agents_shipgate.core.trust_roots import (
    IdentityBoundReadSession,
    IdentityReadBudgetExceeded,
    IdentityValidationFailure,
)


def _session(root: Path) -> IdentityBoundReadSession:
    return IdentityBoundReadSession(root, max_entries=1_000, max_total_bytes=10_000)


def _facts(failure: IdentityValidationFailure) -> tuple[str, str, str | None]:
    return failure.phase, failure.observation, failure.entry


@pytest.fixture
def read_one(tmp_path: Path) -> tuple[IdentityBoundReadSession, Path]:
    (tmp_path / "pkg").mkdir()
    source = tmp_path / "pkg" / "settings.json"
    source.write_text("{}")
    session = _session(tmp_path)
    assert session.read_bytes(Path("pkg/settings.json"), max_bytes=100) == b"{}"
    return session, source


def test_a_real_change_after_the_read_names_the_entry_and_the_mismatch(read_one) -> None:
    session, source = read_one
    source.write_text('{"changed": true}')
    with pytest.raises(IdentityValidationFailure) as raised:
        session.finish()
    assert _facts(raised.value) == ("final_validation", "identity_mismatch", "pkg/settings.json")
    # Every caller that catches ValueError still refuses exactly as before.
    assert isinstance(raised.value, ValueError)
    assert not isinstance(raised.value, IdentityReadBudgetExceeded)
    assert str(raised.value) == "path changed identity while it was read"


def test_an_uninspectable_entry_is_named_without_claiming_a_change(
    read_one, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, _source = read_one
    original = Path.lstat

    def lstat(self):
        if self.name == "settings.json":
            raise PermissionError("private filesystem detail")
        return original(self)

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(IdentityValidationFailure) as raised:
        session.finish()
    assert _facts(raised.value) == (
        "final_validation", "inspection_unavailable", "pkg/settings.json",
    )
    assert "changed" not in str(raised.value)
    assert isinstance(raised.value.__cause__, PermissionError)
    # Nothing was cached as validated: only a complete later pass, which
    # inspects the entry again, validates the session.
    monkeypatch.setattr(Path, "lstat", original)
    session.finish()


def test_a_directory_that_cannot_be_listed_at_final_validation(
    read_one, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, source = read_one
    original = os.scandir

    def scandir(path):
        if Path(path) == source.parent:
            raise PermissionError("private filesystem detail")
        return original(path)

    monkeypatch.setattr(os, "scandir", scandir)
    with pytest.raises(IdentityValidationFailure) as raised:
        session.finish()
    assert _facts(raised.value) == ("final_validation", "inspection_unavailable", "pkg")
    assert "changed" not in str(raised.value)


def test_a_changed_root_has_no_entry(tmp_path: Path) -> None:
    session = _session(tmp_path)
    assert session.directory_entries() == ()
    (tmp_path / "added.json").write_text("{}")
    with pytest.raises(IdentityValidationFailure) as raised:
        session.finish()
    assert _facts(raised.value) == ("final_validation", "root_changed", None)


def test_changed_names_and_kinds_name_their_entry(
    read_one, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A parent's own identity usually changes first; these replace one
    # observation each, to show the remaining branches carry their entry.
    session, _source = read_one
    assert session.directory_entry_kind(Path("pkg/settings.json")) == "file"
    monkeypatch.setattr(trust_roots, "_directory_member_kind", lambda path: "directory")
    with pytest.raises(IdentityValidationFailure) as raised:
        session.finish()
    assert _facts(raised.value) == ("final_validation", "kind_changed", "pkg/settings.json")

    monkeypatch.undo()
    scan = session._scan_names

    def scan_names(directory, **kwargs):
        # Every read is done: only final validation lists directories now.
        names = scan(directory, **kwargs)
        return names | {"added.json"} if directory.name == "pkg" else names

    monkeypatch.setattr(session, "_scan_names", scan_names)
    with pytest.raises(IdentityValidationFailure) as raised:
        session.finish()
    assert _facts(raised.value) == ("final_validation", "entries_changed", "pkg")


@pytest.mark.parametrize("refusal", ["oversized", "hard_link"])
def test_read_refusals_are_typed_with_unchanged_messages(tmp_path: Path, refusal: str) -> None:
    (tmp_path / "pkg").mkdir()
    source = tmp_path / "pkg" / "hook.sh"
    source.write_text("echo hi\n")
    if refusal == "hard_link":
        os.link(source, tmp_path / "pkg" / "alias.sh")
    session = _session(tmp_path)
    with pytest.raises(IdentityValidationFailure) as raised:
        session.read_bytes(Path("pkg/hook.sh"), max_bytes=2 if refusal == "oversized" else 100)
    if refusal == "oversized":
        assert _facts(raised.value) == ("read", "size_limit_exceeded", "pkg/hook.sh")
        # The hook-script reader matches this exact text to say `oversized_input`.
        assert str(raised.value) == "file exceeds the 2-byte read limit"
    else:
        assert _facts(raised.value) == ("read", "not_singly_linked_regular_file", "pkg/hook.sh")
        assert str(raised.value) == "path is not one singly-linked regular file"
