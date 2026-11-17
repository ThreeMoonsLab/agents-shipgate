"""Scan output stays inside the workspace (#1031).

``shipgate.yaml`` is repository content, so a pull request can change it, and
so is anything committed in the reports directory. ``output.directory`` used to
be joined onto the manifest directory unchecked, and every report writer
followed a link at its destination. Now the manifest schema rejects absolute
and ``..`` values, ``scan`` refuses a reports directory reached through a link,
and every file it writes or clears goes through one contained writer that
refuses a linked, special or hard-linked destination before anything changes.
``--out`` remains the operator's way to write elsewhere.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.schemas.manifest.output import OutputConfig

# `tests/test_manifest_schema_parity.py` holds docs/manifest-v0.1.json to the
# same answers as the validator exercised here.
SAMPLES = Path(__file__).resolve().parent.parent / "samples"
runner = CliRunner()

REJECTED = [
    "/tmp/x",
    "../x",
    "..",
    "reports/../../x",
    "reports/..",
    "\\\\server\\share\\reports",
    "\\reports",
    "C:\\reports",
    "C:/reports",
    "c:reports",
    "reports\\..\\..\\x",
]
ACCEPTED = [
    "agents-shipgate-reports",
    "reports/nested",
    "./reports",
    "reports/./nested",
    "a..b",
    "..reports",
    "reports..",
    ".hidden-reports",
    "",
]


def _workspace(tmp_path: Path, directory: str) -> Path:
    workspace = tmp_path / "ws"
    shutil.copytree(SAMPLES / "support_refund_agent", workspace)
    manifest = workspace / "shipgate.yaml"
    data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    data.setdefault("output", {})["directory"] = directory
    manifest.write_text(yaml.safe_dump(data), encoding="utf-8")
    return workspace


def _clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "AGENTS_SHIPGATE_CLI",
        "AGENTS_SHIPGATE_ENABLE_PLUGINS",
        "CLAUDECODE",
        "CURSOR_TRACE_ID",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("value", REJECTED)
def test_the_manifest_rejects_an_output_directory_outside_its_directory(value: str) -> None:
    with pytest.raises(ValidationError, match="output.directory|inside the manifest"):
        OutputConfig(directory=value)


@pytest.mark.parametrize("value", ACCEPTED)
def test_the_manifest_accepts_a_relative_output_directory(value: str) -> None:
    assert OutputConfig(directory=value).directory == value


@pytest.mark.parametrize("spelling", ["absolute", "parent"])
def test_scan_refuses_an_output_directory_outside_the_workspace_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spelling: str
) -> None:
    _clean_environment(monkeypatch)
    outside = tmp_path / "elsewhere"
    value = str(outside) if spelling == "absolute" else "../elsewhere"
    workspace = _workspace(tmp_path, value)

    result = runner.invoke(
        app,
        ["scan", "-c", str(workspace / "shipgate.yaml"), "--ci-mode", "advisory"],
        env={"AGENTS_SHIPGATE_AGENT_MODE": "1"},
    )

    assert result.exit_code == 2, result.output
    assert "output.directory" in result.output
    assert not outside.exists()
    assert sorted(path.name for path in tmp_path.iterdir()) == ["ws"]


@pytest.mark.skipif(os.name != "posix", reason="creates a directory symlink")
def test_scan_refuses_an_output_directory_that_links_outside_the_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clean_environment(monkeypatch)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    workspace = _workspace(tmp_path, "reports")
    (workspace / "reports").symlink_to(outside, target_is_directory=True)

    result = runner.invoke(
        app, ["scan", "-c", str(workspace / "shipgate.yaml"), "--ci-mode", "advisory"]
    )

    assert result.exit_code == 2, result.output
    assert "symbolic link" in result.output
    assert list(outside.iterdir()) == []


def test_out_still_writes_wherever_the_operator_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clean_environment(monkeypatch)
    workspace = _workspace(tmp_path, "agents-shipgate-reports")
    out = tmp_path / "operator-chosen"

    result = runner.invoke(
        app,
        [
            "scan",
            "-c",
            str(workspace / "shipgate.yaml"),
            "--ci-mode",
            "advisory",
            "--format",
            "json",
            "--out",
            str(out),
        ],
    )

    assert result.exit_code == 0, result.output
    assert (out / "report.json").is_file()


def test_a_relative_output_directory_still_writes_inside_the_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clean_environment(monkeypatch)
    workspace = _workspace(tmp_path, "reports/nested")

    result = runner.invoke(
        app,
        [
            "scan",
            "-c",
            str(workspace / "shipgate.yaml"),
            "--ci-mode",
            "advisory",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (workspace / "reports" / "nested" / "report.json").is_file()


# --- Every file a scan writes or clears (#1031) -------------------------------

_ALL_OUTPUTS = [
    "--format",
    "markdown,json,sarif",
    "--packet",
    "--packet-format",
    "md,json,html",
]


@pytest.mark.skipif(os.name != "posix", reason="creates symbolic links")
@pytest.mark.parametrize(
    "name",
    [
        "report.json",
        "report.md",
        "report.sarif",
        "packet.md",
        "packet.json",
        "packet.html",
        "current-control.json",
        "suggested-inventory.json",
        "verifier.json",
    ],
    ids=[
        "json",
        "markdown",
        "sarif",
        "packet-md",
        "packet-json",
        "packet-html",
        "control-artifact",
        "scaffold",
        "cleanup",
    ],
)
def test_scan_refuses_a_linked_output_file_before_writing_or_clearing_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """A committed link under a name the scan writes or removes changes nothing."""

    _clean_environment(monkeypatch)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_text("untouched\n", encoding="utf-8")
    workspace = _workspace(tmp_path, "agents-shipgate-reports")
    reports = workspace / "agents-shipgate-reports"
    reports.mkdir()
    (reports / name).symlink_to(sentinel)

    result = runner.invoke(
        app,
        ["scan", "-c", str(workspace / "shipgate.yaml"), "--ci-mode", "advisory", *_ALL_OUTPUTS],
    )

    assert result.exit_code == 2, result.output
    assert "symbolic link" in result.output
    assert sentinel.read_text(encoding="utf-8") == "untouched\n"
    assert (reports / name).is_symlink()
    assert sorted(path.name for path in reports.iterdir()) == [name]


@pytest.mark.skipif(os.name != "posix", reason="creates symbolic links")
def test_scan_refuses_a_linked_output_file_inside_an_operator_out_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--out`` trusts the directory the operator typed, not links planted in it."""

    _clean_environment(monkeypatch)
    sentinel = tmp_path / "sentinel"
    sentinel.write_text("untouched\n", encoding="utf-8")
    workspace = _workspace(tmp_path, "agents-shipgate-reports")
    out = workspace / "operator-reports"
    out.mkdir()
    (out / "report.md").symlink_to(sentinel)

    result = runner.invoke(
        app,
        [
            "scan",
            "-c",
            str(workspace / "shipgate.yaml"),
            "--ci-mode",
            "advisory",
            "--out",
            str(out),
            *_ALL_OUTPUTS,
        ],
    )

    assert result.exit_code == 2, result.output
    assert sentinel.read_text(encoding="utf-8") == "untouched\n"


@pytest.mark.skipif(os.name != "posix", reason="creates directory symbolic links")
def test_scan_refuses_a_reports_directory_linked_inside_the_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A link is refused even when its target is in the workspace: nothing about it is trusted."""

    _clean_environment(monkeypatch)
    workspace = _workspace(tmp_path, "agents-shipgate-reports")
    real = workspace / "real-reports"
    real.mkdir()
    (workspace / "agents-shipgate-reports").symlink_to(real, target_is_directory=True)

    result = runner.invoke(
        app, ["scan", "-c", str(workspace / "shipgate.yaml"), "--ci-mode", "advisory"]
    )

    assert result.exit_code == 2, result.output
    assert list(real.iterdir()) == []


@pytest.mark.skipif(os.name != "posix", reason="hard links")
def test_scan_refuses_a_hard_linked_output_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clean_environment(monkeypatch)
    sentinel = tmp_path / "sentinel"
    sentinel.write_text("untouched\n", encoding="utf-8")
    workspace = _workspace(tmp_path, "agents-shipgate-reports")
    reports = workspace / "agents-shipgate-reports"
    reports.mkdir()
    os.link(sentinel, reports / "report.json")

    result = runner.invoke(
        app, ["scan", "-c", str(workspace / "shipgate.yaml"), "--ci-mode", "advisory"]
    )

    assert result.exit_code == 2, result.output
    assert "hard link" in result.output
    assert sentinel.read_text(encoding="utf-8") == "untouched\n"


def test_scan_still_overwrites_and_clears_its_own_plain_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ordinary output keeps working: stale plain files are replaced or removed."""

    _clean_environment(monkeypatch)
    workspace = _workspace(tmp_path, "agents-shipgate-reports")
    reports = workspace / "agents-shipgate-reports"
    reports.mkdir()
    (reports / "report.json").write_text("stale\n", encoding="utf-8")
    (reports / "verifier.json").write_text("{}\n", encoding="utf-8")

    result = runner.invoke(
        app,
        ["scan", "-c", str(workspace / "shipgate.yaml"), "--ci-mode", "advisory", *_ALL_OUTPUTS],
    )

    assert result.exit_code == 0, result.output
    assert (reports / "report.json").read_text(encoding="utf-8").startswith("{")
    assert not (reports / "verifier.json").exists()
    for name in ("report.md", "report.sarif", "packet.md", "packet.json", "packet.html"):
        assert (reports / name).is_file(), name


# --- The contained writer itself ----------------------------------------------


@pytest.mark.skipif(os.name != "posix", reason="creates symbolic links")
def test_the_contained_writer_refuses_every_unsafe_destination(tmp_path: Path) -> None:
    from agents_shipgate.core.contained_write import (
        UnsafeOutputPathError,
        remove_contained,
        write_contained,
    )

    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_text("untouched\n", encoding="utf-8")
    (root / "leaf").symlink_to(sentinel)
    (root / "parent").symlink_to(outside, target_is_directory=True)

    for destination in (
        root / "leaf",
        root / "parent" / "sentinel",
        root / "sub" / ".." / ".." / "outside" / "sentinel",
        outside / "sentinel",
    ):
        with pytest.raises(UnsafeOutputPathError):
            write_contained(destination, "x", root=root)
        with pytest.raises(UnsafeOutputPathError):
            write_contained(destination, "x", root=root, atomic=True)
        with pytest.raises(UnsafeOutputPathError):
            remove_contained(destination, root=root)
    assert sentinel.read_text(encoding="utf-8") == "untouched\n"
    assert (root / "leaf").is_symlink()

    write_contained(root / "new" / "nested" / "file.txt", "a\n", root=root)
    write_contained(root / "new" / "nested" / "file.txt", "b\n", root=root, atomic=True)
    assert (root / "new" / "nested" / "file.txt").read_text(encoding="utf-8") == "b\n"
    assert remove_contained(root / "new" / "nested" / "file.txt", root=root) is True
    assert remove_contained(root / "new" / "nested" / "file.txt", root=root) is False
