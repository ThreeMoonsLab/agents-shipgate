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


@pytest.mark.parametrize("gone", ["file", "directory"])
def test_a_captured_entry_reported_absent_is_an_observed_change(
    read_one, monkeypatch: pytest.MonkeyPatch, gone: str,
) -> None:
    # Absent is an answer the filesystem gave, not a failure to look. Only the
    # one lookup reports it, so the parent's own identity cannot answer first.
    session, source = read_one
    if gone == "file":
        original_lstat = Path.lstat

        def lstat(self):
            if self.name == source.name:
                raise FileNotFoundError("private filesystem detail")
            return original_lstat(self)

        monkeypatch.setattr(Path, "lstat", lstat)
        expected = ("final_validation", "identity_mismatch", "pkg/settings.json")
    else:
        original_scandir = os.scandir

        def scandir(path):
            if Path(path) == source.parent:
                raise FileNotFoundError("private filesystem detail")
            return original_scandir(path)

        monkeypatch.setattr(os, "scandir", scandir)
        expected = ("final_validation", "identity_mismatch", "pkg")
    with pytest.raises(IdentityValidationFailure) as raised:
        session.finish()
    assert _facts(raised.value) == expected
    assert "changed while it was read" in str(raised.value)
    assert isinstance(raised.value.__cause__, FileNotFoundError)


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


@pytest.fixture(params=["dir_fd", "portable"])
def standalone_read(request, monkeypatch: pytest.MonkeyPatch):
    """The standalone reader, on its descriptor-relative and its portable path."""

    if request.param == "portable":
        monkeypatch.setattr(os, "supports_dir_fd", set())
    elif os.open not in os.supports_dir_fd:
        pytest.skip("descriptor-relative open unavailable")
    return trust_roots.read_identity_bound_bytes


def test_standalone_read_names_no_change_when_it_cannot_look_after_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The descriptor-relative path's post-read inspection; the portable path
    # lets the same OSError propagate unchanged, making no claim either.
    if os.open not in os.supports_dir_fd:
        pytest.skip("descriptor-relative open unavailable")
    standalone_read = trust_roots.read_identity_bound_bytes
    (tmp_path / "f.txt").write_text("x")
    original = Path.lstat
    calls = 0

    def lstat(self):
        nonlocal calls
        if self.name == "f.txt":
            calls += 1
            if calls > 1:  # every inspection after the read
                raise PermissionError("private filesystem detail")
        return original(self)

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(ValueError) as raised:
        standalone_read(tmp_path, Path("f.txt"), max_bytes=10)
    assert "changed" not in str(raised.value)
    assert "could not be inspected" in str(raised.value)


def test_standalone_read_of_a_file_removed_after_reading_says_it_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.open not in os.supports_dir_fd:
        pytest.skip("descriptor-relative open unavailable")
    target = tmp_path / "f.txt"
    target.write_text("x")
    original = trust_roots.inspect_lexical_path_identity
    calls = 0

    def inspect(root, relative):
        nonlocal calls
        calls += 1
        if calls == 2:  # the read is done: really remove the file
            target.unlink()
        return original(root, relative)

    monkeypatch.setattr(trust_roots, "inspect_lexical_path_identity", inspect)
    with pytest.raises(ValueError) as raised:
        trust_roots.read_identity_bound_bytes(tmp_path, Path("f.txt"), max_bytes=10)
    assert str(raised.value) == "path changed while it was read"
    assert isinstance(raised.value.__cause__, FileNotFoundError)


@pytest.mark.parametrize(("kind", "claims_change"), [
    ("inspection_error", False), ("symlink", True), ("alias", True),
])
def test_standalone_read_claims_a_change_only_when_it_saw_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, standalone_read,
    kind: str, claims_change: bool,
) -> None:
    (tmp_path / "f.txt").write_text("x")
    original = trust_roots.inspect_lexical_path_identity
    calls = 0

    def inspect(root, relative):
        nonlocal calls
        calls += 1
        if calls == 1:  # the inspection before the read is clean
            return original(root, relative)
        return trust_roots.PathIdentityIssue(kind=kind, requested=root / relative)

    monkeypatch.setattr(trust_roots, "inspect_lexical_path_identity", inspect)
    with pytest.raises(ValueError) as raised:
        standalone_read(tmp_path, Path("f.txt"), max_bytes=10)
    assert ("changed" in str(raised.value)) is claims_change


@pytest.mark.parametrize("failure", ["uninspectable", "changed"])
def test_preflight_claims_a_graph_change_only_when_the_reader_saw_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    from agents_shipgate.core.errors import ConfigError
    from agents_shipgate.core.preflight import build_trust_root_graph

    instructions = tmp_path / "AGENTS.md"
    instructions.write_text("# agents\n")
    original_finish = IdentityBoundReadSession.finish
    original_lstat = Path.lstat

    def lstat(self):
        if self.name == "AGENTS.md":
            raise PermissionError("private filesystem detail")
        return original_lstat(self)

    def finish(self):
        if failure == "changed":
            instructions.write_text("# agents, edited after the read\n")
            return original_finish(self)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(Path, "lstat", lstat)
            return original_finish(self)

    monkeypatch.setattr(IdentityBoundReadSession, "finish", finish)
    with pytest.raises(ConfigError) as raised:
        build_trust_root_graph(tmp_path)
    cause = raised.value.__cause__
    assert isinstance(cause, IdentityValidationFailure)
    if failure == "changed":
        assert cause.observation == "identity_mismatch"
        assert "changed identity" in str(raised.value)
    else:
        assert cause.observation == "inspection_unavailable"
        assert "changed" not in str(raised.value)
        assert "could not be inspected during final validation" in str(raised.value)


def test_preflight_says_the_graph_changed_when_its_root_is_reported_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agents_shipgate.core.errors import ConfigError
    from agents_shipgate.core.preflight import build_trust_root_graph

    (tmp_path / "AGENTS.md").write_text("# agents\n")
    root = tmp_path.resolve()
    original_finish = IdentityBoundReadSession.finish
    original_lstat = Path.lstat

    def lstat(self):
        if Path(self) == root:
            raise FileNotFoundError("private filesystem detail")
        return original_lstat(self)

    def finish(self):
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(Path, "lstat", lstat)
            return original_finish(self)

    monkeypatch.setattr(IdentityBoundReadSession, "finish", finish)
    with pytest.raises(ConfigError) as raised:
        build_trust_root_graph(tmp_path)
    # The reader lets this OSError through untyped, as before; it is still
    # an observed absence, so the graph is reported changed.
    assert isinstance(raised.value.__cause__, FileNotFoundError)
    assert "changed identity" in str(raised.value)
    assert "no change was established" not in str(raised.value)


@pytest.mark.parametrize(("failure", "says"), [
    ("lstat_denied", "could not be inspected after it was read"),
    ("lstat_absent", "changed while it was read"),
    ("inspection_error", "could not be inspected after it was read"),
    ("symlink", "changed identity while it was read"),
])
def test_host_grants_baseline_claims_a_change_only_when_it_saw_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str, says: str,
) -> None:
    from agents_shipgate.core import host_grants

    baseline = (tmp_path / "baseline.json").resolve()
    baseline.write_text("{}")
    if failure.startswith("lstat_"):
        error = PermissionError if failure == "lstat_denied" else FileNotFoundError
        original_lstat = Path.lstat

        def lstat(self):
            # The read opens the baseline by descriptor, so every lstat of it
            # here is the inspection after the read.
            if self.name == baseline.name:
                raise error("private filesystem detail")
            return original_lstat(self)

        monkeypatch.setattr(Path, "lstat", lstat)
    else:
        def inspect(root, relative):
            return trust_roots.PathIdentityIssue(kind=failure, requested=root / relative)

        monkeypatch.setattr(host_grants, "inspect_lexical_path_identity", inspect)
    # The exact read itself: the path checks before it are their own refusals.
    with pytest.raises(ValueError) as raised:
        host_grants._read_exact_baseline_text(baseline, display_path=baseline)
    assert says in str(raised.value)
    if "could not be inspected" in says:
        assert "changed" not in str(raised.value)


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
