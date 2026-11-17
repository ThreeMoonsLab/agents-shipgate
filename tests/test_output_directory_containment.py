"""``output.directory`` stays inside the manifest's directory (#1031).

``shipgate.yaml`` is repository content, so a pull request can change it. Its
``output.directory`` used to be joined onto the manifest directory unchecked,
and an absolute or ``..`` value made ``scan`` write — and clear — report files
anywhere. The manifest schema now rejects both spellings, ``scan`` refuses an
output directory that resolves outside the manifest directory (a link
included), and ``--out`` remains the operator's way to write elsewhere.
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
    assert "outside the manifest directory" in result.output
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
