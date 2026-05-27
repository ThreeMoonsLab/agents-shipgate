"""Tests for ``init --write``'s gitignore side effect.

Covers:

* Pure :mod:`cli.discovery.gitignore_block` behaviors (parse, upsert,
  detect_existing_state across the seven file states).
* End-to-end ``init --write`` integration: JSON payload carries the
  ``gitignore`` block; on-disk ``.gitignore`` reflects the requested state;
  re-running is idempotent; existing variants are respected.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agents_shipgate.cli.discovery.gitignore_block import (
    GITIGNORE_BLOCK_VERSION,
    REPORTS_DIR_NAME,
    GitignoreBlockState,
    GitignoreOutcomeStatus,
    GitignoreUpsertStatus,
    detect_existing_state,
    ensure_reports_gitignore,
    parse,
    render_block,
    upsert,
)
from agents_shipgate.cli.main import app

# ---------- pure parsing / upsert -----------------------------------------


def test_parse_no_markers_on_empty_host() -> None:
    assert parse(b"").state is GitignoreBlockState.NO_MARKERS


def test_parse_no_markers_on_arbitrary_content() -> None:
    assert parse(b"node_modules/\nbuild/\n").state is GitignoreBlockState.NO_MARKERS


def test_parse_locates_present_block() -> None:
    host = render_block(1, b"\n")
    parsed = parse(host)
    assert parsed.state is GitignoreBlockState.PRESENT
    assert parsed.location is not None
    assert parsed.location.version == 1
    assert parsed.location.line_start == 0
    assert parsed.location.line_end == len(host)


def test_parse_ambiguous_on_duplicate_markers() -> None:
    host = render_block(1, b"\n") + render_block(1, b"\n")
    assert parse(host).state is GitignoreBlockState.AMBIGUOUS


def test_parse_ambiguous_on_orphan_start() -> None:
    assert parse(b"# agents-shipgate:start v=1\nfoo\n").state is GitignoreBlockState.AMBIGUOUS


def test_parse_ambiguous_on_orphan_end() -> None:
    assert parse(b"foo\n# agents-shipgate:end\n").state is GitignoreBlockState.AMBIGUOUS


def test_render_block_contains_canonical_path() -> None:
    block = render_block(1, b"\n").decode("utf-8")
    assert f"{REPORTS_DIR_NAME}/" in block
    assert block.startswith("# agents-shipgate:start v=1\n")
    assert block.endswith("# agents-shipgate:end\n")


def test_render_block_preserves_crlf_newlines() -> None:
    block = render_block(1, b"\r\n")
    assert block.startswith(b"# agents-shipgate:start v=1\r\n")
    assert block.endswith(b"# agents-shipgate:end\r\n")


def test_upsert_appends_to_empty_host() -> None:
    result = upsert(b"")
    assert result.status is GitignoreUpsertStatus.APPENDED
    assert b"agents-shipgate-reports/" in result.new_bytes


def test_upsert_appends_with_one_blank_line_separator() -> None:
    host = b"node_modules/\n"
    result = upsert(host)
    assert result.status is GitignoreUpsertStatus.APPENDED
    # Two newlines after the existing content (one to terminate the prior
    # line if missing, one to separate).
    assert result.new_bytes.startswith(b"node_modules/\n\n# agents-shipgate:start")


def test_upsert_appends_when_host_already_ends_in_blank_line() -> None:
    host = b"node_modules/\n\n"
    result = upsert(host)
    assert result.status is GitignoreUpsertStatus.APPENDED
    # Should not add a third newline.
    assert result.new_bytes.startswith(b"node_modules/\n\n# agents-shipgate:start")


def test_upsert_unchanged_when_block_already_present() -> None:
    host = b"node_modules/\n\n" + render_block(1, b"\n")
    result = upsert(host)
    assert result.status is GitignoreUpsertStatus.UNCHANGED


def test_upsert_refuses_newer_version() -> None:
    # Synthesize a fake v99 block by hand. Future-proofs the contract.
    future_block = (
        b"# agents-shipgate:start v=99\n"
        b"future-stuff/\n"
        b"# agents-shipgate:end\n"
    )
    result = upsert(future_block)
    assert result.status is GitignoreUpsertStatus.NEWER_VERSION
    assert result.block_version == 99
    # Host must not be modified.
    assert result.new_bytes == future_block


def test_upsert_refuses_ambiguous() -> None:
    host = render_block(1, b"\n") + render_block(1, b"\n")
    result = upsert(host)
    assert result.status is GitignoreUpsertStatus.AMBIGUOUS
    assert result.new_bytes == host  # unchanged


# ---------- variant + negation detection ----------------------------------


def test_detect_existing_state_finds_trailing_slash_variant() -> None:
    present, negated = detect_existing_state(b"foo\nagents-shipgate-reports/\nbar\n")
    assert present is True
    assert negated is False


def test_detect_existing_state_finds_no_slash_variant() -> None:
    present, _ = detect_existing_state(b"agents-shipgate-reports\n")
    assert present is True


def test_detect_existing_state_finds_anchored_variant() -> None:
    present, _ = detect_existing_state(b"/agents-shipgate-reports/\n")
    assert present is True


def test_detect_existing_state_ignores_inline_comment() -> None:
    present, _ = detect_existing_state(b"agents-shipgate-reports/  # legacy line\n")
    assert present is True


def test_detect_existing_state_respects_escaped_hash() -> None:
    # ``\#`` is a literal hash, not a comment introducer; the token therefore
    # isn't the canonical name and shouldn't match.
    present, _ = detect_existing_state(b"agents-shipgate-reports/\\#weird\n")
    assert present is False


def test_detect_existing_state_finds_negation() -> None:
    _, negated = detect_existing_state(b"!agents-shipgate-reports/\n")
    assert negated is True


def test_detect_existing_state_ignores_globstar_variant() -> None:
    # We intentionally don't normalize ``**/`` — it has different semantics
    # and we'd rather be redundant than wrong.
    present, _ = detect_existing_state(b"**/agents-shipgate-reports/\n")
    assert present is False


def test_detect_existing_state_ignores_lines_inside_managed_block() -> None:
    # A prior managed-block install must not be classified as a manual mention
    # (which would prevent upsert).
    block_host = render_block(1, b"\n")
    present, _ = detect_existing_state(block_host)
    assert present is False


# ---------- ensure_reports_gitignore (filesystem) -------------------------


def test_ensure_dry_run_does_not_touch_filesystem(tmp_path: Path) -> None:
    outcome = ensure_reports_gitignore(tmp_path, write=False)
    assert outcome.status is GitignoreOutcomeStatus.DRY_RUN
    assert not (tmp_path / ".gitignore").exists()


def test_ensure_creates_when_missing(tmp_path: Path) -> None:
    outcome = ensure_reports_gitignore(tmp_path, write=True)
    assert outcome.status is GitignoreOutcomeStatus.CREATED
    content = (tmp_path / ".gitignore").read_text()
    assert "# agents-shipgate:start v=1" in content
    assert "agents-shipgate-reports/" in content


def test_ensure_appends_when_no_markers_present(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("node_modules/\nbuild/\n")
    outcome = ensure_reports_gitignore(tmp_path, write=True)
    assert outcome.status is GitignoreOutcomeStatus.APPENDED
    content = (tmp_path / ".gitignore").read_text()
    assert content.startswith("node_modules/\nbuild/\n\n# agents-shipgate:start")
    assert "agents-shipgate-reports/" in content


def test_ensure_idempotent_on_second_run(tmp_path: Path) -> None:
    first = ensure_reports_gitignore(tmp_path, write=True)
    assert first.status is GitignoreOutcomeStatus.CREATED
    before = (tmp_path / ".gitignore").read_bytes()
    second = ensure_reports_gitignore(tmp_path, write=True)
    assert second.status is GitignoreOutcomeStatus.UNCHANGED
    after = (tmp_path / ".gitignore").read_bytes()
    assert before == after  # byte-identical


def test_ensure_respects_existing_canonical_line(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("node_modules/\nagents-shipgate-reports/\n")
    outcome = ensure_reports_gitignore(tmp_path, write=True)
    assert outcome.status is GitignoreOutcomeStatus.ALREADY_PRESENT
    # File must be untouched.
    assert (tmp_path / ".gitignore").read_text() == (
        "node_modules/\nagents-shipgate-reports/\n"
    )


def test_ensure_respects_existing_anchored_variant(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("/agents-shipgate-reports/\n")
    outcome = ensure_reports_gitignore(tmp_path, write=True)
    assert outcome.status is GitignoreOutcomeStatus.ALREADY_PRESENT


def test_ensure_respects_negation(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("!agents-shipgate-reports/\n")
    outcome = ensure_reports_gitignore(tmp_path, write=True)
    assert outcome.status is GitignoreOutcomeStatus.SKIPPED_NEGATED
    # File must be untouched.
    assert (tmp_path / ".gitignore").read_text() == "!agents-shipgate-reports/\n"


def test_ensure_skips_ambiguous_markers(tmp_path: Path) -> None:
    duplicate = render_block(1, b"\n") + render_block(1, b"\n")
    (tmp_path / ".gitignore").write_bytes(duplicate)
    outcome = ensure_reports_gitignore(tmp_path, write=True)
    assert outcome.status is GitignoreOutcomeStatus.SKIPPED_AMBIGUOUS
    assert (tmp_path / ".gitignore").read_bytes() == duplicate


def test_ensure_skips_newer_version(tmp_path: Path) -> None:
    future_block = (
        b"# agents-shipgate:start v=99\n"
        b"future-stuff/\n"
        b"# agents-shipgate:end\n"
    )
    (tmp_path / ".gitignore").write_bytes(future_block)
    outcome = ensure_reports_gitignore(tmp_path, write=True)
    assert outcome.status is GitignoreOutcomeStatus.SKIPPED_NEWER_VERSION
    assert outcome.block_version == 99
    assert (tmp_path / ".gitignore").read_bytes() == future_block


def test_ensure_skips_when_path_is_directory(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").mkdir()  # adversarial filesystem state
    outcome = ensure_reports_gitignore(tmp_path, write=True)
    assert outcome.status is GitignoreOutcomeStatus.SKIPPED_NOT_REGULAR_FILE


def test_ensure_refuses_symlink_in_path_chain(tmp_path: Path) -> None:
    # `.gitignore -> /elsewhere` would otherwise route the write out of the
    # workspace. Refuse without creating anything.
    elsewhere = tmp_path.parent / f"{tmp_path.name}_elsewhere"
    elsewhere.touch()
    try:
        (tmp_path / ".gitignore").symlink_to(elsewhere)
    except (OSError, NotImplementedError):
        # Some CI environments forbid symlink creation (Windows, restricted
        # macOS). Skip silently — the safer-default code path is still
        # exercised by the non-symlink tests.
        return
    outcome = ensure_reports_gitignore(tmp_path, write=True)
    assert outcome.status is GitignoreOutcomeStatus.SKIPPED_SYMLINK
    assert elsewhere.read_bytes() == b""  # unchanged


# ---------- end-to-end via `init --write` ---------------------------------


def test_init_write_creates_gitignore_in_empty_workspace(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["init", "--workspace", str(tmp_path), "--write"])
    assert result.exit_code == 0, result.stdout
    gitignore = (tmp_path / ".gitignore").read_text()
    assert "agents-shipgate-reports/" in gitignore
    assert f"v={GITIGNORE_BLOCK_VERSION}" in gitignore


def test_init_write_json_emits_gitignore_block(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app, ["init", "--workspace", str(tmp_path), "--write", "--json"]
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert "gitignore" in payload
    gi = payload["gitignore"]
    assert gi["status"] == "created"
    assert gi["path"].endswith(".gitignore")
    assert gi["block_version"] == GITIGNORE_BLOCK_VERSION


def test_init_write_dry_run_does_not_create_gitignore(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["init", "--workspace", str(tmp_path)])
    assert result.exit_code == 0, result.stdout
    # Without --write, we never touch the filesystem; the dry-run JSON path
    # would emit a DRY_RUN outcome but the bare console path doesn't.
    assert not (tmp_path / ".gitignore").exists()


def test_init_write_appends_when_user_has_existing_gitignore(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("node_modules/\n.venv/\n")
    runner = CliRunner()
    result = runner.invoke(app, ["init", "--workspace", str(tmp_path), "--write"])
    assert result.exit_code == 0, result.stdout
    content = (tmp_path / ".gitignore").read_text()
    # User content preserved verbatim, our block appended.
    assert content.startswith("node_modules/\n.venv/\n")
    assert "# agents-shipgate:start v=1" in content
    assert "agents-shipgate-reports/" in content


def test_init_write_already_present_is_no_op(tmp_path: Path) -> None:
    original = "node_modules/\nagents-shipgate-reports/\n"
    (tmp_path / ".gitignore").write_text(original)
    runner = CliRunner()
    result = runner.invoke(
        app, ["init", "--workspace", str(tmp_path), "--write", "--json"]
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["gitignore"]["status"] == "already_present"
    assert (tmp_path / ".gitignore").read_text() == original


def test_init_write_runs_again_even_when_manifest_exists(tmp_path: Path) -> None:
    # Pre-existing manifest. Without our addition the gitignore would never
    # be touched on subsequent invocations; with it, the line still lands.
    (tmp_path / "shipgate.yaml").write_text("version: '0.1'\nproject:\n  name: x\n")
    runner = CliRunner()
    result = runner.invoke(
        app, ["init", "--workspace", str(tmp_path), "--write", "--json"]
    )
    # Manifest skipped (exit 2), but the gitignore action still ran.
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["gitignore"]["status"] == "created"
    assert (tmp_path / ".gitignore").exists()
    assert "agents-shipgate-reports/" in (tmp_path / ".gitignore").read_text()
