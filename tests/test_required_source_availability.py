"""One availability contract for required tool sources (#585).

`docs/diagnostics.md` publishes it: a required `tool_sources[].path` that
does not resolve is `InputParseError(3)` from `scan`. Readers used to
decide for themselves — the shared loaders and `mcp_server_source` raised,
`openai_agents_sdk` returned a warning and let the scan finish advisory
exit 0 — so the answer depended on which reader you happened to declare.

The precondition now runs once, before any adapter, off the same resolver
`doctor` uses. Optional sources are untouched.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.cli.scan.source_loading import _load_sources
from agents_shipgate.config.loader import load_manifest
from agents_shipgate.core.errors import InputParseError

runner = CliRunner()

# Every per-source type that takes a path. The point of the parametrization
# is that the answer no longer depends on which reader would have run.
PATH_SOURCE_TYPES = ["openai_agents_sdk", "mcp", "openapi", "mcp_server_source"]


def _manifest(
    project: Path,
    *,
    source_type: str = "openai_agents_sdk",
    path: str = "agent.py",
    optional: bool = False,
) -> Path:
    project.mkdir(parents=True, exist_ok=True)
    config = project / "shipgate.yaml"
    config.write_text(
        "version: '0.1'\n"
        "project: {name: availability}\n"
        "agent: {name: availability, declared_purpose: [read local data]}\n"
        "environment: {target: local}\n"
        "tool_sources:\n"
        f"  - {{id: src, type: {source_type}, path: {path}, "
        f"optional: {str(optional).lower()}}}\n",
        encoding="utf-8",
    )
    return config


def _scan_cli(config: Path, out: Path, *extra: str):
    return runner.invoke(
        app,
        ["scan", "--config", str(config), "--out", str(out), *extra],
    )


# --- the contract ----------------------------------------------------------


@pytest.mark.parametrize("source_type", PATH_SOURCE_TYPES)
def test_a_required_missing_path_is_an_input_error_for_every_reader(
    tmp_path: Path, source_type: str
) -> None:
    config = _manifest(tmp_path / "p", source_type=source_type)

    result = _scan_cli(config, tmp_path / "out")

    assert result.exit_code == 3
    assert "Required tool source unavailable" in result.output
    # The exact declared path, so the reader knows which line to fix.
    assert "'src'" in result.output
    assert "'agent.py'" in result.output


@pytest.mark.parametrize("source_type", PATH_SOURCE_TYPES)
def test_an_optional_missing_path_still_warns_and_completes(
    tmp_path: Path, source_type: str
) -> None:
    config = _manifest(tmp_path / "p", source_type=source_type, optional=True)

    result = _scan_cli(config, tmp_path / "out")

    assert result.exit_code == 0
    assert "Required tool source unavailable" not in result.output
    report = json.loads((tmp_path / "out" / "report.json").read_text())
    assert report["source_warnings"], "an optional miss must still be reported"


def test_the_sdk_reader_no_longer_disagrees_with_the_published_contract(
    tmp_path: Path,
) -> None:
    """#585's exact reproduction: this combination completed advisory
    exit 0 with only a source warning."""

    config = _manifest(tmp_path / "p", source_type="openai_agents_sdk")

    result = _scan_cli(config, tmp_path / "out")

    assert result.exit_code == 3
    assert not (tmp_path / "out" / "report.json").exists()


def test_a_required_path_outside_the_manifest_directory_is_refused(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    config = _manifest(tmp_path / "p", source_type="mcp", path="../outside.json")

    result = _scan_cli(config, tmp_path / "out")

    assert result.exit_code == 3
    assert "resolves outside the manifest directory" in result.output


def test_the_refusal_precedes_adapter_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The point of #585 is one precondition, not another reader that
    happens to raise. No adapter may run at all."""

    from agents_shipgate.inputs import protocol

    def explode(*args: object, **kwargs: object):  # pragma: no cover - must not run
        raise AssertionError("an adapter ran before the availability check")

    monkeypatch.setattr(protocol.REGISTRY, "require", explode)
    config = _manifest(tmp_path / "p")
    manifest = load_manifest(config)

    with pytest.raises(InputParseError, match="Required tool source unavailable"):
        _load_sources(manifest, config.parent, verbose=False)


def test_strict_mode_reports_the_same_input_error(tmp_path: Path) -> None:
    """Exit 3 is an input failure, not the strict gate's exit 20."""

    config = _manifest(tmp_path / "p")

    result = _scan_cli(config, tmp_path / "out", "--ci-mode", "strict")

    assert result.exit_code == 3


def test_module_invocation_agrees_with_the_cli(tmp_path: Path) -> None:
    config = _manifest(tmp_path / "p")

    completed = subprocess.run(
        [
            "python",
            "-m",
            "agents_shipgate",
            "scan",
            "--config",
            str(config),
            "--out",
            str(tmp_path / "out"),
        ],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
    )

    assert completed.returncode == 3
    assert "Required tool source unavailable" in completed.stdout + completed.stderr


# --- the other two surfaces must say the same thing ------------------------


def test_doctor_json_reports_the_same_source_without_raising(tmp_path: Path) -> None:
    """doctor's documented behaviour is a diagnostic, not an exception —
    but it must be about the same source, from the same resolver."""

    config = _manifest(tmp_path / "p")

    result = runner.invoke(app, ["doctor", "--config", str(config), "--json"])

    payload = json.loads(result.output)
    payload = payload[0] if isinstance(payload, list) else payload
    assert payload["unresolved_sources"] == [
        {"declared_path": "agent.py", "id": "src", "line": 6, "reason": "missing"}
    ]
    assert any(
        entry.get("id") == "SHIP-DIAG-MISSING-SOURCE-FILE"
        for entry in payload["diagnostics"]
    )


def test_verify_reports_a_typed_base_failure_and_leaves_the_head_gate(
    tmp_path: Path,
) -> None:
    """A base commit whose manifest declares a file that tree does not
    contain: the base scan is refused, named, and explicitly does not move
    the head gate."""

    repo = tmp_path / "repo"
    config = _manifest(repo)
    (repo / ".gitignore").write_text("agents-shipgate-reports/\n", encoding="utf-8")

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Test")
    git("add", "-A")
    git("commit", "-qm", "manifest without its entrypoint")
    git("checkout", "-q", "-b", "feat")
    (repo / "agent.py").write_text(
        "from agents import Agent, function_tool\n"
        "@function_tool\n"
        "def read_tool() -> str:\n"
        '    return "a"\n'
        'agent = Agent(name="availability", tools=[read_tool])\n',
        encoding="utf-8",
    )
    git("add", "-A")
    git("commit", "-qm", "add the entrypoint")

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            str(config),
            "--base",
            "main",
            "--head",
            "HEAD",
            "--ci-mode",
            "advisory",
        ],
    )

    assert result.exit_code == 0
    verifier = json.loads(
        (repo / "agents-shipgate-reports" / "verifier.json").read_text()
    )
    assert verifier["base_status"] == "scan_failed"
    notes = " ".join(verifier["base_notes"])
    assert "Required tool source unavailable" in notes
    assert "without changing the head gate" in notes
    # The head scan itself is unaffected: it read its own tree fine.
    assert verifier["merge_verdict"] != "unknown"
