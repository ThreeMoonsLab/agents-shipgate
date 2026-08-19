"""An absent input and a malformed input never share a message (#384).

Shipgate reads six kinds of input: the manifest, the workspace, a tool source,
a policy pack, a baseline, and a pair of diff refs. Each has a not-found
branch and a shape-check branch, and two of them used to route *absent*
through the shape check:

- the manifest, where a failed read collapsed to ``b""`` and then parsed as an
  empty YAML document, so a file that had never been created was reported as
  "Config file must contain a YAML object";
- the workspace, where ``git rev-parse`` fails identically in a directory that
  is not a repository and one that is not there, so both were reported as
  "Workspace is not inside a git checkout".

Both cost real diagnosis time in one session, in unrelated subsystems, which
is why this is a table over every input rather than two regression tests. The
other four already told the states apart and are asserted here so a future
refactor cannot quietly re-collapse them.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app

runner = CliRunner()

MANIFEST = """version: "0.1"
project:
  name: absent-input-probe
agent:
  name: probe-agent
  declared_purpose:
    - look things up
environment:
  target: local
tool_sources:
  - id: tools
    type: mcp
    path: tools.json
"""

TOOLS_JSON = (
    '{"tools": [{"name": "lookup", "description": '
    '"look an order up by its identifier for a support agent"}]}\n'
)

# Text that asserts the input exists and has the wrong shape. An absent input
# must never produce any of it.
SHAPE_CLAIMS = (
    "must contain a YAML object",
    "not inside a git checkout",
    "Unable to parse",
    "Invalid baseline file",
)


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "shipgate.yaml").write_text(MANIFEST, encoding="utf-8")
    (workspace / "tools.json").write_text(TOOLS_JSON, encoding="utf-8")
    return workspace


def _run(argv: list[str]) -> str:
    return runner.invoke(app, argv).output


def _manifest_cases(tmp_path: Path) -> tuple[str, str]:
    workspace = _workspace(tmp_path)
    malformed = workspace / "malformed.yaml"
    malformed.write_text("- a\n- b\n", encoding="utf-8")
    absent = _run(["doctor", "--config", str(workspace / "gone.yaml")])
    return absent, _run(["doctor", "--config", str(malformed)])


def _workspace_cases(tmp_path: Path) -> tuple[str, str]:
    workspace = _workspace(tmp_path)
    # "Exists but is not a git checkout" is the shape complaint here: the
    # workspace fixture is a plain directory, never `git init`ed.
    absent = _run(["verify", "--workspace", str(tmp_path / "gone")])
    return absent, _run(["verify", "--workspace", str(workspace)])


def _tool_source_cases(tmp_path: Path) -> tuple[str, str]:
    workspace = _workspace(tmp_path)
    absent_manifest = workspace / "absent-source.yaml"
    absent_manifest.write_text(
        MANIFEST.replace("path: tools.json", "path: gone.json"), encoding="utf-8"
    )
    (workspace / "broken.json").write_text("{ not json\n", encoding="utf-8")
    malformed_manifest = workspace / "malformed-source.yaml"
    malformed_manifest.write_text(
        MANIFEST.replace("path: tools.json", "path: broken.json"), encoding="utf-8"
    )
    return (
        _run(["scan", "--config", str(absent_manifest), "--format", "json"]),
        _run(["scan", "--config", str(malformed_manifest), "--format", "json"]),
    )


def _policy_pack_cases(tmp_path: Path) -> tuple[str, str]:
    workspace = _workspace(tmp_path)
    (workspace / "broken-pack.yaml").write_text("- a\n- b\n", encoding="utf-8")

    def manifest_with(pack: str, name: str) -> Path:
        path = workspace / name
        path.write_text(
            MANIFEST + f"checks:\n  policy_packs:\n    - {pack}\n", encoding="utf-8"
        )
        return path

    absent = manifest_with("gone-pack.yaml", "absent-pack.yaml")
    malformed = manifest_with("broken-pack.yaml", "malformed-pack.yaml")
    return (
        _run(["scan", "--config", str(absent), "--format", "json"]),
        _run(["scan", "--config", str(malformed), "--format", "json"]),
    )


def _baseline_cases(tmp_path: Path) -> tuple[str, str]:
    workspace = _workspace(tmp_path)
    manifest = workspace / "shipgate.yaml"
    broken = workspace / "broken-baseline.json"
    broken.write_text("not json", encoding="utf-8")
    common = ["scan", "--config", str(manifest), "--format", "json", "--baseline"]
    return (
        _run([*common, str(workspace / "gone-baseline.json")]),
        _run([*common, str(broken)]),
    )


def _diff_ref_cases(tmp_path: Path) -> tuple[str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    for argv in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "test@example.com"],
        ["config", "user.name", "Test User"],
    ):
        subprocess.run(["git", *argv], cwd=repo, check=True)
    (repo / "shipgate.yaml").write_text(MANIFEST, encoding="utf-8")
    (repo / "tools.json").write_text(TOOLS_JSON, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "one"], cwd=repo, check=True)

    common = ["verify", "--preview", "--json", "--workspace", str(repo), "--head", "HEAD"]
    # The "malformed" counterpart for a ref pair is a readable diff that
    # matched nothing — the state an unreadable one must never be reported as.
    return (
        _run([*common, "--base", "no-such-ref"]),
        _run([*common, "--base", "HEAD"]),
    )


INPUT_CASES = {
    "manifest": (_manifest_cases, ("not found",)),
    "workspace": (_workspace_cases, ("does not exist",)),
    "tool_source": (_tool_source_cases, ("not found",)),
    "policy_pack": (_policy_pack_cases, ("not found",)),
    "baseline": (_baseline_cases, ("not found",)),
    "diff_refs": (_diff_ref_cases, ("not available locally",)),
}


@pytest.mark.parametrize("name", sorted(INPUT_CASES))
def test_absent_input_is_not_reported_as_a_malformed_one(
    name: str, tmp_path: Path
) -> None:
    build, absent_markers = INPUT_CASES[name]
    absent_output, malformed_output = build(tmp_path)

    assert absent_output != malformed_output, name
    assert any(marker in absent_output for marker in absent_markers), absent_output
    for claim in SHAPE_CLAIMS:
        assert claim not in absent_output, (name, claim, absent_output)
