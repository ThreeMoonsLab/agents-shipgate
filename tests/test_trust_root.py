"""SHIP-VERIFY-TRUST-ROOT-TOUCHED — Tier A trust-root protection.

Covers the path classifier, the "only fires with a verification context"
contract, and the end-to-end scan path (findings flow through the
existing release gate, not a second verdict).
"""

import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from agents_shipgate.checks.verify import CHECK_ID
from agents_shipgate.checks.verify import run as verify_run
from agents_shipgate.cli.main import app
from agents_shipgate.config.loader import load_manifest
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.domain import Agent
from agents_shipgate.core.findings.mutations import apply_suppressions
from agents_shipgate.core.trust_roots import (
    inspect_lexical_path_identity,
    is_configured_manifest,
)
from agents_shipgate.schemas.manifest import SuppressionConfig
from agents_shipgate.schemas.verification import VerificationContext

runner = CliRunner()


def _context(changed_files: list[str] | None = None, *, verification: bool = True) -> ScanContext:
    manifest = load_manifest(Path("samples/support_refund_agent/shipgate.yaml"))
    vc = (
        VerificationContext(changed_files=changed_files or [])
        if verification
        else None
    )
    return ScanContext(
        manifest=manifest,
        agent=Agent(id="agent:test/test", name="test"),
        tools=[],
        config_path=Path("shipgate.yaml"),
        verification=vc,
    )


def test_plain_scan_without_verification_emits_nothing():
    """Absence of a verification context == plain scan behavior."""
    assert verify_run(_context(verification=False)) == []


def test_empty_changed_files_emits_nothing():
    assert verify_run(_context(changed_files=[])) == []


def test_non_trust_root_files_are_not_flagged():
    findings = verify_run(
        _context(
            changed_files=[
                "src/agent.py",
                "README.md",
                "docs/guide.md",
                "tests/test_agent.py",
                "package.json",
            ]
        )
    )
    assert findings == []


@pytest.mark.parametrize(
    "path,expected_class",
    [
        ("shipgate.yaml", "manifest"),
        ("services/billing/shipgate.yaml", "manifest"),
        (".agents-shipgate/baseline.json", "shipgate_state"),
        ("policies/refund.yaml", "policy"),
        ("prompts/system.md", "prompts"),
        (".github/workflows/agents-shipgate.yml", "ci_gate"),
        (".github/workflows/agents-shipgate.yaml", "ci_gate"),
        ("AGENTS.md", "agent_instructions"),
        ("agents.md", "agent_instructions"),
        ("CLAUDE.md", "agent_instructions"),
        (".claude/commands/shipgate.md", "agent_instructions"),
        (".cursor/rules/agents-shipgate.mdc", "agent_instructions"),
        (".agents/skills/agents-shipgate/reference.md", "agent_instructions"),
        (".codex/config.toml", "agent_instructions"),
        (".codex/hooks.json", "agent_instructions"),
        (".codex/hooks/preflight.sh", "agent_instructions"),
        (".codex-plugin/plugin.json", "codex_plugin"),
        ("plugins/browser/.app.json", "tool_surface_decl"),
        ("servers/search/.mcp.json", "tool_surface_decl"),
        (".cursor/mcp.json", "tool_surface_decl"),
        ("apps/web/.cursor/mcp.json", "tool_surface_decl"),
        (".vscode/mcp.json", "tool_surface_decl"),
        ("apps/web/.vscode/mcp.json", "tool_surface_decl"),
        (".claude/settings.json", "agent_instructions"),
        (".claude/settings.local.json", "agent_instructions"),
        ("packages/refund/SKILL.md", "tool_surface_decl"),
    ],
)
def test_trust_root_classification(path, expected_class):
    findings = verify_run(_context(changed_files=[path]))
    assert len(findings) == 1
    finding = findings[0]
    assert finding.check_id == CHECK_ID
    assert finding.severity == "medium"
    assert finding.evidence["changed_file"] == path
    assert finding.evidence["trust_root_class"] == expected_class
    assert finding.source.type == "changed_file"
    assert finding.provenance_kind == "static_declaration"


def test_generic_workflows_are_shared_host_boundary_trust_roots():
    """Workflow permissions and pull_request_target affect the host boundary."""
    findings = verify_run(
        _context(changed_files=[".github/workflows/test.yml", ".github/workflows/deploy.yaml"])
    )
    assert len(findings) == 2
    assert {
        finding.evidence["trust_root_class"] for finding in findings
    } == {"host_boundary"}


def test_duplicate_changed_files_are_deduplicated():
    findings = verify_run(_context(changed_files=["shipgate.yaml", "shipgate.yaml"]))
    assert len(findings) == 1


def test_configured_manifest_identity_is_canonical_within_workspace():
    assert is_configured_manifest(
        "/repo/docs/engineering/../gate.yml",
        "docs/gate.yml",
        workspace="/repo",
    )
    assert is_configured_manifest(
        "services/api/gate.yml",
        "services/api/config/../gate.yml",
        workspace="/repo",
    )


def test_configured_manifest_identity_has_no_same_basename_fallback():
    assert not is_configured_manifest(
        "/repo/config/release.gate",
        "release.gate",
        exact=True,
    )


def test_verify_uses_the_logical_configured_manifest_identity():
    context = _context(changed_files=["release.gate"])
    context.config_path = Path("/tmp/archive/config/release.gate")
    context.verification.configured_manifest_path = "config/release.gate"

    assert verify_run(context) == []

    context.verification.changed_files = ["config/release.gate"]
    findings = verify_run(context)
    assert len(findings) == 1
    assert findings[0].evidence["changed_file"] == "config/release.gate"


@pytest.mark.parametrize(
    ("actual_name", "alias_name"),
    [
        ("new-gate.yml", "NEW-GATE.yml"),
        ("caf\u00e9.yml", "cafe\u0301.yml"),
    ],
)
def test_lexical_path_identity_rejects_filesystem_resolved_aliases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    actual_name: str,
    alias_name: str,
):
    actual = tmp_path / actual_name
    actual.write_text("manifest\n", encoding="utf-8")
    alias = tmp_path / alias_name
    real_lstat = Path.lstat

    def aliased_lstat(path: Path, *args, **kwargs):
        if path == alias:
            return real_lstat(actual, *args, **kwargs)
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", aliased_lstat)

    issue = inspect_lexical_path_identity(tmp_path, Path(alias_name))
    assert issue is not None
    assert issue.kind == "alias"
    assert issue.requested == alias
    assert issue.actual == actual


def test_lexical_path_identity_rejects_windows_reparse_components(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = tmp_path / "bridge"
    bridge.mkdir()
    (bridge / "gate.yml").write_text("manifest\n", encoding="utf-8")
    real_lstat = Path.lstat

    def reparse_lstat(path: Path, *args, **kwargs):
        metadata = real_lstat(path, *args, **kwargs)
        if path == bridge:
            return SimpleNamespace(
                st_mode=metadata.st_mode,
                st_file_attributes=0x0400,
            )
        return metadata

    monkeypatch.setattr(Path, "lstat", reparse_lstat)

    issue = inspect_lexical_path_identity(
        tmp_path,
        Path("bridge/gate.yml"),
    )
    assert issue is not None
    assert issue.kind == "reparse_point"
    assert issue.requested == bridge


@pytest.mark.parametrize(
    ("config", "changed"),
    [
        ("../outside/gate.yml", "../outside/gate.yml"),
        ("/outside/gate.yml", "/outside/gate.yml"),
        ("/repo-sibling/gate.yml", "/repo-sibling/gate.yml"),
        ("/repo/services/api/gate.yml", "services/other/gate.yml"),
    ],
)
def test_configured_manifest_identity_rejects_workspace_escapes_and_aliases(
    config: str,
    changed: str,
):
    assert not is_configured_manifest(config, changed, workspace="/repo")


def test_first_match_wins_classification_order():
    """A SKILL.md under .agents/skills/ classifies as agent_instructions
    (ordered earlier) rather than tool_surface_decl — one finding, the
    earlier class."""
    findings = verify_run(
        _context(changed_files=[".agents/skills/agents-shipgate/SKILL.md"])
    )
    assert len(findings) == 1
    assert findings[0].evidence["trust_root_class"] == "agent_instructions"


def test_scan_changed_files_emits_finding_through_the_gate(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    shutil.copytree("samples/clean_read_only_agent", tmp_path / "wk")
    monkeypatch.chdir(tmp_path / "wk")
    changed = tmp_path / "changed.txt"
    changed.write_text("shipgate.yaml\nsrc/agent.py\nREADME.md\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "scan",
            "-c",
            str(tmp_path / "wk" / "shipgate.yaml"),
            "--changed-files",
            str(changed),
        ],
    )
    assert result.exit_code == 0, result.stdout

    report = json.loads(
        (tmp_path / "wk" / "agents-shipgate-reports" / "report.json").read_text()
    )
    verify_findings = [
        f for f in report["findings"] if f["check_id"] == CHECK_ID
    ]
    assert len(verify_findings) == 1
    assert verify_findings[0]["evidence"]["changed_file"] == "shipgate.yaml"
    # Routed through the one decision engine to the review tier.
    assert report["release_decision"]["decision"] == "review_required"
    assert verify_findings[0]["requires_human_review"] is True


def test_scan_changed_files_does_not_suffix_match_a_custom_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "wk"
    manifest_dir = workspace / "config"
    shutil.copytree("samples/clean_read_only_agent", manifest_dir)
    (manifest_dir / "shipgate.yaml").rename(manifest_dir / "release.gate")
    changed = tmp_path / "changed.txt"
    changed.write_text("release.gate\n", encoding="utf-8")
    monkeypatch.chdir(workspace)

    result = runner.invoke(
        app,
        [
            "scan",
            "-c",
            str(manifest_dir / "release.gate"),
            "--changed-files",
            str(changed),
        ],
    )

    assert result.exit_code == 0, result.output
    report = json.loads(
        (manifest_dir / "agents-shipgate-reports" / "report.json").read_text()
    )
    assert all(
        finding["evidence"].get("changed_file") != "release.gate"
        for finding in report["findings"]
        if finding["check_id"].startswith("SHIP-VERIFY-")
    )

    changed.write_text("config/release.gate\n", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "scan",
            "-c",
            str(manifest_dir / "release.gate"),
            "--changed-files",
            str(changed),
        ],
    )
    assert result.exit_code == 0, result.output
    report = json.loads(
        (manifest_dir / "agents-shipgate-reports" / "report.json").read_text()
    )
    assert any(
        finding["check_id"] == CHECK_ID
        and finding["evidence"].get("changed_file") == "config/release.gate"
        for finding in report["findings"]
    )


def test_scan_changed_files_maps_relative_config_from_a_git_subdirectory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "wk"
    manifest_dir = workspace / "config"
    shutil.copytree("samples/clean_read_only_agent", manifest_dir)
    (manifest_dir / "shipgate.yaml").rename(manifest_dir / "release.gate")
    (workspace / ".git").mkdir()
    changed = tmp_path / "changed.txt"
    changed.write_text("config/release.gate\n", encoding="utf-8")
    monkeypatch.chdir(manifest_dir)

    result = runner.invoke(
        app,
        [
            "scan",
            "-c",
            "release.gate",
            "--changed-files",
            str(changed),
        ],
    )

    assert result.exit_code == 0, result.output
    report = json.loads(
        (manifest_dir / "agents-shipgate-reports" / "report.json").read_text()
    )
    assert any(
        finding["check_id"] == CHECK_ID
        and finding["evidence"].get("changed_file") == "config/release.gate"
        for finding in report["findings"]
    )


def test_scan_changed_files_maps_a_nested_workspace_to_the_git_root(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repo"
    service = repository / "services" / "api"
    shutil.copytree("samples/clean_read_only_agent", service)
    (repository / ".git").mkdir()
    changed = tmp_path / "changed.txt"
    changed.write_text("services/api/shipgate.yaml\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "scan",
            "--workspace",
            str(service),
            "--changed-files",
            str(changed),
        ],
    )

    assert result.exit_code == 0, result.output
    report = json.loads(
        (service / "agents-shipgate-reports" / "report.json").read_text()
    )
    assert any(
        finding["check_id"] == CHECK_ID
        and finding["evidence"].get("changed_file")
        == "services/api/shipgate.yaml"
        for finding in report["findings"]
    )


@pytest.mark.parametrize("with_git_root", [False, True])
def test_scan_changed_files_maps_config_through_repository_symlink(
    tmp_path: Path,
    with_git_root: bool,
) -> None:
    repository = tmp_path / "repo"
    shutil.copytree("samples/clean_read_only_agent", repository)
    (repository / "shipgate.yaml").rename(repository / "release.gate")
    if with_git_root:
        (repository / ".git").mkdir()
    alias = tmp_path / "repo-alias"
    alias.symlink_to(repository, target_is_directory=True)
    changed = tmp_path / "changed.txt"
    changed.write_text("release.gate\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "scan",
            "-c",
            str(alias / "release.gate"),
            "--changed-files",
            str(changed),
        ],
    )

    if not with_git_root:
        assert result.exit_code == 2, result.output
        assert "no repository root could be proven" in result.output
        return
    assert result.exit_code == 0, result.output
    report = json.loads(
        (repository / "agents-shipgate-reports" / "report.json").read_text()
    )
    assert any(
        finding["check_id"] == CHECK_ID
        and finding["evidence"].get("changed_file") == "release.gate"
        for finding in report["findings"]
    )


def test_scan_changed_files_rejects_ambiguous_absolute_custom_config_root(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repo"
    manifest_dir = repository / "config"
    shutil.copytree("samples/clean_read_only_agent", manifest_dir)
    (manifest_dir / "shipgate.yaml").rename(manifest_dir / "release.gate")
    changed = tmp_path / "changed.txt"
    changed.write_text("config/release.gate\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "scan",
            "-c",
            str(manifest_dir / "release.gate"),
            "--changed-files",
            str(changed),
        ],
    )

    assert result.exit_code == 2, result.output
    assert "no repository root could be proven" in result.output


def test_scan_changed_files_rejects_repository_internal_config_symlink(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repo"
    shutil.copytree("samples/clean_read_only_agent", repository)
    (repository / "shipgate.yaml").rename(repository / "release.gate")
    (repository / ".git").mkdir()
    (repository / "gate-dir").symlink_to(".", target_is_directory=True)
    changed = tmp_path / "changed.txt"
    changed.write_text("gate-dir\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "scan",
            "-c",
            str(repository / "gate-dir" / "release.gate"),
            "--changed-files",
            str(changed),
        ],
    )

    assert result.exit_code == 2, result.output
    assert "--config must not contain repository symlink components" in result.output


@pytest.mark.parametrize("target_has_git", [False, True])
def test_scan_changed_files_rejects_cross_repository_config_symlink(
    tmp_path: Path,
    target_has_git: bool,
) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    (source / ".git").mkdir()
    shutil.copytree("samples/clean_read_only_agent", target)
    (target / "shipgate.yaml").rename(target / "release.gate")
    if target_has_git:
        (target / ".git").mkdir()
    (source / "gate-dir").symlink_to(target, target_is_directory=True)
    changed = tmp_path / "changed.txt"
    changed.write_text("gate-dir\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "scan",
            "-c",
            str(source / "gate-dir" / "release.gate"),
            "--changed-files",
            str(changed),
        ],
    )

    assert result.exit_code == 2, result.output
    assert "--config must not contain repository symlink components" in result.output


def test_scan_changed_files_prefers_non_git_cwd_over_symlink_target_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    shutil.copytree("samples/clean_read_only_agent", target)
    (target / "shipgate.yaml").rename(target / "release.gate")
    (target / ".git").mkdir()
    (source / "gate-dir").symlink_to(target, target_is_directory=True)
    changed = tmp_path / "changed.txt"
    changed.write_text("gate-dir\n", encoding="utf-8")
    monkeypatch.chdir(source)

    result = runner.invoke(
        app,
        [
            "scan",
            "-c",
            "gate-dir/release.gate",
            "--changed-files",
            str(changed),
        ],
    )

    assert result.exit_code == 2, result.output
    assert "--config must not contain repository symlink components" in result.output


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="exercises the /var to /private/var filesystem alias on macOS",
)
def test_scan_changed_files_maps_config_through_var_alias(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repo"
    shutil.copytree("samples/clean_read_only_agent", repository)
    (repository / "shipgate.yaml").rename(repository / "release.gate")
    (repository / ".git").mkdir()
    manifest = repository / "release.gate"
    alias_text = str(manifest).replace("/private/var/", "/var/", 1)
    if alias_text == str(manifest):
        pytest.skip("temporary directory is not below /private/var")
    alias = Path(alias_text)
    try:
        if not alias.is_file() or not alias.samefile(manifest):
            pytest.skip("/var does not resolve to the same manifest")
    except OSError:
        pytest.skip("/var alias is unavailable")
    changed = tmp_path / "changed.txt"
    changed.write_text("release.gate\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "scan",
            "-c",
            str(alias),
            "--changed-files",
            str(changed),
        ],
    )

    assert result.exit_code == 0, result.output
    report = json.loads(
        (repository / "agents-shipgate-reports" / "report.json").read_text()
    )
    assert any(
        finding["check_id"] == CHECK_ID
        and finding["evidence"].get("changed_file") == "release.gate"
        for finding in report["findings"]
    )


def test_scan_without_changed_files_is_unchanged(tmp_path):
    shutil.copytree("samples/clean_read_only_agent", tmp_path / "wk")
    result = runner.invoke(
        app, ["scan", "-c", str(tmp_path / "wk" / "shipgate.yaml")]
    )
    assert result.exit_code == 0, result.stdout
    report = json.loads(
        (tmp_path / "wk" / "agents-shipgate-reports" / "report.json").read_text()
    )
    assert [f for f in report["findings"] if f["check_id"] == CHECK_ID] == []


@pytest.mark.parametrize("manifest_name", [" gate.yml", "gate\u2028prod.yml"])
def test_scan_changed_files_preserves_exact_custom_manifest_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manifest_name: str,
) -> None:
    shutil.copytree("samples/clean_read_only_agent", tmp_path / "wk")
    workspace = tmp_path / "wk"
    manifest = workspace / manifest_name
    (workspace / "shipgate.yaml").rename(manifest)
    changed = tmp_path / "changed.txt"
    changed.write_text(f"{manifest_name}\n", encoding="utf-8")
    monkeypatch.chdir(workspace)

    result = runner.invoke(
        app,
        ["scan", "-c", str(manifest), "--changed-files", str(changed)],
    )

    assert result.exit_code == 0, result.output
    report = json.loads(
        (workspace / "agents-shipgate-reports" / "report.json").read_text(
            encoding="utf-8"
        )
    )
    findings = [
        finding
        for finding in report["findings"]
        if finding["check_id"] == CHECK_ID
    ]
    assert [finding["evidence"]["changed_file"] for finding in findings] == [
        manifest_name
    ]


# --- Reward-hacking guard: the trust-root finding cannot be silenced -------


def test_apply_suppressions_cannot_suppress_trust_root_finding():
    """Unit: a checks.ignore entry targeting the verify check must NOT
    flip ``suppressed`` — otherwise a PR could edit shipgate.yaml to
    silence the very check that flags the edit."""
    findings = verify_run(_context(changed_files=["shipgate.yaml"]))
    assert len(findings) == 1
    apply_suppressions(
        findings,
        [SuppressionConfig(check_id=CHECK_ID, reason="make CI green")],
    )
    assert findings[0].suppressed is False


def test_scan_ignore_entry_does_not_silence_trust_root(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    """End-to-end: shipgate.yaml adding `checks.ignore` for the trust-root
    check does not suppress it; the finding stays active and the gate
    still routes to review_required."""
    shutil.copytree("samples/clean_read_only_agent", tmp_path / "wk")
    manifest = tmp_path / "wk" / "shipgate.yaml"
    manifest.write_text(
        manifest.read_text()
        + "\nchecks:\n  ignore:\n    - check_id: SHIP-VERIFY-TRUST-ROOT-TOUCHED\n"
        + "      reason: make CI green\n",
        encoding="utf-8",
    )
    changed = tmp_path / "changed.txt"
    changed.write_text("shipgate.yaml\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path / "wk")

    result = runner.invoke(
        app, ["scan", "-c", str(manifest), "--changed-files", str(changed)]
    )
    assert result.exit_code == 0, result.stdout
    report = json.loads(
        (tmp_path / "wk" / "agents-shipgate-reports" / "report.json").read_text()
    )
    verify_findings = [f for f in report["findings"] if f["check_id"] == CHECK_ID]
    assert len(verify_findings) == 1
    assert verify_findings[0]["suppressed"] is False
    assert report["release_decision"]["decision"] == "review_required"


def test_scan_severity_override_below_floor_is_rejected(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Weakening the trust-root check below its medium floor via
    checks.severity_overrides is a hard ConfigError (exit 2), not a
    silent downgrade."""
    shutil.copytree("samples/clean_read_only_agent", tmp_path / "wk")
    manifest = tmp_path / "wk" / "shipgate.yaml"
    manifest.write_text(
        manifest.read_text()
        + "\nchecks:\n  severity_overrides:\n"
        + "    SHIP-VERIFY-TRUST-ROOT-TOUCHED: info\n",
        encoding="utf-8",
    )
    changed = tmp_path / "changed.txt"
    changed.write_text("shipgate.yaml\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path / "wk")

    result = runner.invoke(
        app, ["scan", "-c", str(manifest), "--changed-files", str(changed)]
    )
    assert result.exit_code == 2, result.stdout


def test_scan_changed_files_rejected_for_multi_config(tmp_path):
    """--changed-files is single-config only: a workspace that resolves
    more than one manifest must reject it (exit 2) rather than fan the
    same changed-files list across every manifest."""
    shutil.copytree("samples/multi_agent_workspace", tmp_path / "wk")
    manifests = list((tmp_path / "wk").rglob("shipgate.yaml"))
    assert len(manifests) > 1, "fixture must resolve multiple manifests"
    changed = tmp_path / "changed.txt"
    changed.write_text("shipgate.yaml\n", encoding="utf-8")

    result = runner.invoke(
        app,
        ["scan", "--workspace", str(tmp_path / "wk"), "--changed-files", str(changed)],
    )
    assert result.exit_code == 2, result.stdout
