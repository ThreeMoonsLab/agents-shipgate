"""Library-level tests for the agent-instructions managed-block helper."""

from __future__ import annotations

import pytest

from agents_shipgate.cli.discovery.agent_instructions.managed_block import (
    BlockState,
    UpsertStatus,
    detect_newline,
    parse,
    render_block,
    upsert,
)

# --- detect_newline --------------------------------------------------------


def test_detect_newline_lf_default() -> None:
    assert detect_newline(b"") == b"\n"
    assert detect_newline(b"hello\nworld\n") == b"\n"


def test_detect_newline_crlf_when_any_present() -> None:
    assert detect_newline(b"hello\r\nworld\n") == b"\r\n"
    assert detect_newline(b"\r\n") == b"\r\n"


# --- parse -----------------------------------------------------------------


def test_parse_no_markers() -> None:
    assert parse(b"# heading\nhello\n").state is BlockState.NO_MARKERS
    assert parse(b"").state is BlockState.NO_MARKERS


def test_parse_present_returns_line_offsets() -> None:
    host = (
        b"# heading\n"
        b"prelude\n"
        b"<!-- agents-shipgate:start v=1 -->\n"
        b"body line 1\n"
        b"body line 2\n"
        b"<!-- agents-shipgate:end -->\n"
        b"trailer\n"
    )
    parsed = parse(host)
    assert parsed.state is BlockState.PRESENT
    assert parsed.location is not None
    assert parsed.location.version == 1
    # line_start should be at the start of the start-marker LINE (not mid-line).
    assert host[parsed.location.line_start :].startswith(b"<!-- agents-shipgate:start")
    # line_end should be just after the end marker's trailing newline.
    assert host[parsed.location.line_end :] == b"trailer\n"


def test_parse_present_handles_eof_without_trailing_newline() -> None:
    host = (
        b"<!-- agents-shipgate:start v=1 -->\n"
        b"body\n"
        b"<!-- agents-shipgate:end -->"
    )
    parsed = parse(host)
    assert parsed.state is BlockState.PRESENT
    assert parsed.location is not None
    assert parsed.location.line_end == len(host)


def test_parse_ambiguous_two_start_markers() -> None:
    host = (
        b"<!-- agents-shipgate:start v=1 -->\n"
        b"a\n"
        b"<!-- agents-shipgate:start v=1 -->\n"
        b"b\n"
        b"<!-- agents-shipgate:end -->\n"
    )
    assert parse(host).state is BlockState.AMBIGUOUS


def test_parse_ambiguous_end_before_start() -> None:
    host = (
        b"<!-- agents-shipgate:end -->\n"
        b"<!-- agents-shipgate:start v=1 -->\n"
    )
    assert parse(host).state is BlockState.AMBIGUOUS


def test_parse_ambiguous_unmatched_end() -> None:
    host = b"<!-- agents-shipgate:start v=1 -->\nbody\n"
    assert parse(host).state is BlockState.AMBIGUOUS


# --- render_block ----------------------------------------------------------


def test_render_block_lf_normalizes_newlines_in_inner() -> None:
    out = render_block("a\nb\n", version=1, newline=b"\n")
    assert out == (
        b"<!-- agents-shipgate:start v=1 -->\n"
        b"a\n"
        b"b\n"
        b"<!-- agents-shipgate:end -->\n"
    )


def test_render_block_crlf_propagates_to_inner() -> None:
    out = render_block("a\nb\n", version=1, newline=b"\r\n")
    assert out == (
        b"<!-- agents-shipgate:start v=1 -->\r\n"
        b"a\r\nb\r\n"
        b"<!-- agents-shipgate:end -->\r\n"
    )


def test_render_block_inner_without_trailing_newline_gets_one() -> None:
    out = render_block("body", version=1, newline=b"\n")
    assert out == (
        b"<!-- agents-shipgate:start v=1 -->\nbody\n<!-- agents-shipgate:end -->\n"
    )


def test_render_block_rejects_zero_or_negative_version() -> None:
    with pytest.raises(ValueError):
        render_block("body\n", version=0, newline=b"\n")
    with pytest.raises(ValueError):
        render_block("body\n", version=-1, newline=b"\n")


# --- upsert ----------------------------------------------------------------


def test_upsert_empty_host_writes_just_block() -> None:
    out = upsert(b"", inner="hello\n", version=1)
    assert out.status is UpsertStatus.APPENDED
    assert out.new_bytes == (
        b"<!-- agents-shipgate:start v=1 -->\nhello\n<!-- agents-shipgate:end -->\n"
    )


def test_upsert_appends_with_blank_line_separator() -> None:
    host = b"# Heading\nExisting line.\n"
    out = upsert(host, inner="block body\n", version=1)
    assert out.status is UpsertStatus.APPENDED
    # Original two lines preserved byte-for-byte.
    assert out.new_bytes.startswith(host)
    # Exactly one blank line between user content and block.
    assert out.new_bytes == (
        b"# Heading\n"
        b"Existing line.\n"
        b"\n"
        b"<!-- agents-shipgate:start v=1 -->\n"
        b"block body\n"
        b"<!-- agents-shipgate:end -->\n"
    )


def test_upsert_appends_when_host_lacks_trailing_newline() -> None:
    host = b"# Heading\nNo newline at EOF"
    out = upsert(host, inner="body\n", version=1)
    assert out.status is UpsertStatus.APPENDED
    assert out.new_bytes == (
        b"# Heading\n"
        b"No newline at EOF\n"
        b"\n"
        b"<!-- agents-shipgate:start v=1 -->\n"
        b"body\n"
        b"<!-- agents-shipgate:end -->\n"
    )


def test_upsert_unchanged_when_block_matches_exactly() -> None:
    host = (
        b"prelude\n"
        b"<!-- agents-shipgate:start v=1 -->\n"
        b"body\n"
        b"<!-- agents-shipgate:end -->\n"
        b"trailer\n"
    )
    out = upsert(host, inner="body\n", version=1)
    assert out.status is UpsertStatus.UNCHANGED
    assert out.new_bytes == host


def test_upsert_updated_replaces_only_block_region() -> None:
    host = (
        b"prelude\n"
        b"<!-- agents-shipgate:start v=1 -->\n"
        b"old body\n"
        b"<!-- agents-shipgate:end -->\n"
        b"trailer\n"
    )
    out = upsert(host, inner="new body\n", version=1)
    assert out.status is UpsertStatus.UPDATED
    assert out.new_bytes == (
        b"prelude\n"
        b"<!-- agents-shipgate:start v=1 -->\n"
        b"new body\n"
        b"<!-- agents-shipgate:end -->\n"
        b"trailer\n"
    )


def test_upsert_migrated_bumps_version_and_replaces_block() -> None:
    host = (
        b"<!-- agents-shipgate:start v=1 -->\n"
        b"old\n"
        b"<!-- agents-shipgate:end -->\n"
    )
    out = upsert(host, inner="new\n", version=2)
    assert out.status is UpsertStatus.MIGRATED
    assert out.block_version == 2
    assert b"v=2" in out.new_bytes
    assert b"new\n" in out.new_bytes


def test_upsert_newer_version_refuses() -> None:
    host = (
        b"<!-- agents-shipgate:start v=99 -->\n"
        b"future\n"
        b"<!-- agents-shipgate:end -->\n"
    )
    out = upsert(host, inner="body\n", version=1)
    assert out.status is UpsertStatus.NEWER_VERSION
    assert out.new_bytes == host


def test_upsert_ambiguous_returns_unchanged_bytes() -> None:
    host = (
        b"<!-- agents-shipgate:start v=1 -->\n"
        b"a\n"
        b"<!-- agents-shipgate:start v=1 -->\n"
        b"b\n"
        b"<!-- agents-shipgate:end -->\n"
    )
    out = upsert(host, inner="body\n", version=1)
    assert out.status is UpsertStatus.AMBIGUOUS
    assert out.new_bytes == host


# --- CRLF preservation -----------------------------------------------------


@pytest.mark.parametrize(
    ("nl_label", "nl"),
    [("lf", b"\n"), ("crlf", b"\r\n")],
)
def test_upsert_preserves_host_newline_style(nl_label: str, nl: bytes) -> None:
    host = b"# Heading" + nl + b"User line." + nl
    out = upsert(host, inner="block body\n", version=1)
    assert out.status is UpsertStatus.APPENDED
    # Host prefix must be byte-equal to the original.
    assert out.new_bytes.startswith(host)
    # Only the host newline byte sequence appears in the inserted block.
    inserted = out.new_bytes[len(host) :]
    if nl == b"\r\n":
        assert b"\r\n" in inserted
        # Stray bare LFs are not allowed when host is CRLF (they would mix line endings).
        assert b"\n" not in inserted.replace(b"\r\n", b"")
    else:
        assert b"\r\n" not in inserted


def test_upsert_round_trip_preserves_user_bytes_outside_block(
) -> None:
    """Repeated upsert with the same content must be a no-op (UNCHANGED)
    and must preserve user bytes outside the block exactly."""
    user_prefix = b"# My Doc\n\nPara 1.\nPara 2.\n\n"
    user_suffix = b"\nMore prose after.\n"
    inner = "managed body line 1\nmanaged body line 2\n"
    first = upsert(user_prefix.rstrip(b"\n") + b"\n", inner=inner, version=1)
    # Insert block, then add user suffix back to simulate user editing AFTER us.
    composed = first.new_bytes + user_suffix
    second = upsert(composed, inner=inner, version=1)
    assert second.status is UpsertStatus.UNCHANGED
    assert second.new_bytes == composed
    # User suffix is intact.
    assert second.new_bytes.endswith(user_suffix)
