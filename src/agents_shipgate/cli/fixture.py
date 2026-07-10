"""``agents-shipgate fixture`` subcommand: list, run, copy, and verify the
bundled fixtures so an agent can validate install + report shape with one
command, without authoring a manifest.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path

import typer

from agents_shipgate.cli.scan.orchestrator import run_scan
from agents_shipgate.cli.verify.orchestrator import run_verify
from agents_shipgate.core.disclaimers import STATIC_VERDICT_DISCLAIMER
from agents_shipgate.core.errors import AgentsShipgateError, ConfigError, InputParseError
from agents_shipgate.fixtures import (
    FixtureNotFoundError,
    FixturesUnavailableError,
    fixture_path,
    list_fixtures,
)

fixture_app = typer.Typer(
    help="Run, copy, list, or verify bundled sample fixtures.",
    no_args_is_help=True,
)


@fixture_app.command("list")
def fixture_list(
    json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of text."),
) -> None:
    """List the bundled fixtures."""
    try:
        fixtures = list_fixtures()
    except FixturesUnavailableError as exc:
        typer.echo(f"Fixtures unavailable: {exc}", err=True)
        raise typer.Exit(4) from exc

    if json_output:
        typer.echo(json.dumps(fixtures, indent=2))
        return

    if not fixtures:
        typer.echo("No bundled fixtures available.")
        return
    for fixture in fixtures:
        line = f"{fixture['name']}"
        if fixture.get("description"):
            line += f"\t{fixture['description']}"
        typer.echo(line)


@fixture_app.command("run")
def fixture_run(
    name: str = typer.Argument(..., help="Fixture name; see `fixture list`."),
    out: Path | None = typer.Option(
        None,
        "--out",
        help="Output directory for the report. Defaults to a temp location next to the fixture copy.",
    ),
    ci_mode: str | None = typer.Option(
        None,
        "--ci-mode",
        help="advisory or strict; defaults to advisory for fixture runs.",
    ),
    keep: bool = typer.Option(
        False,
        "--keep",
        help=(
            "Keep the fixture copy in a tempdir when --out writes reports outside it. "
            "Default report output inside the copy is always left accessible."
        ),
    ),
) -> None:
    """Copy a fixture to a tempdir and scan it."""
    src = _resolve_fixture(name)

    if name == "ai_generated_refund_pr":
        _run_ai_generated_refund_pr_fixture(
            name=name,
            src=src,
            out=out,
            ci_mode=ci_mode,
            keep=keep,
        )
        return

    if name == "agent_weakens_gate":
        _run_agent_weakens_gate_fixture(
            name=name,
            src=src,
            out=out,
            ci_mode=ci_mode,
            keep=keep,
        )
        return

    import tempfile

    workdir = Path(tempfile.mkdtemp(prefix=f"shipgate-fixture-{name}-"))
    target = workdir / name
    shutil.copytree(src, target)

    out_dir = out or (target / "reports")

    try:
        report, exit_code = run_scan(
            config_path=target / "shipgate.yaml",
            output_dir=out_dir,
            formats=["markdown", "json"],
            ci_mode=ci_mode or "advisory",
        )
    except (ConfigError, InputParseError, AgentsShipgateError) as exc:
        typer.echo(f"Fixture {name!r} scan failed: {exc}", err=True)
        raise typer.Exit(4) from exc

    typer.echo(f"Fixture: {name}")
    decision = report.release_decision
    if decision is not None:
        typer.echo(f"Decision: {decision.decision}")
        typer.echo(
            f"Blockers: {len(decision.blockers)}  "
            f"Review items: {len(decision.review_items)}"
        )
    typer.echo(
        f"Counts:  critical={report.summary.critical_count} "
        f"high={report.summary.high_count} medium={report.summary.medium_count}"
    )
    typer.echo(f"Reports: {out_dir}")
    typer.echo(f"Static-verdict boundary: {STATIC_VERDICT_DISCLAIMER}")
    _finish_fixture_copy(
        workdir=workdir,
        target=target,
        out_was_explicit=out is not None,
        keep=keep,
    )
    raise typer.Exit(exit_code)


@fixture_app.command("copy")
def fixture_copy(
    name: str = typer.Argument(..., help="Fixture name."),
    to: Path = typer.Option(..., "--to", help="Destination directory (created if missing)."),
) -> None:
    """Copy a fixture into a user-provided directory.

    The destination is always ``<to>/<fixture-name>``; ``<to>`` is created if
    it does not exist. The fixture is copied as a self-contained subdirectory
    so multiple fixtures can be staged side-by-side.
    """
    src = _resolve_fixture(name)

    to.mkdir(parents=True, exist_ok=True)
    target = to / name
    if target.exists():
        typer.echo(f"Destination already exists: {target}", err=True)
        raise typer.Exit(2)

    shutil.copytree(src, target)
    typer.echo(f"Copied fixture {name!r} to {target}")


@fixture_app.command("verify")
def fixture_verify(
    name: str = typer.Argument(..., help="Fixture name."),
) -> None:
    """Scan a fixture and (when ``expected/`` is present) confirm the JSON
    summary matches the golden snapshot."""
    src = _resolve_fixture(name)

    import tempfile

    workdir = Path(tempfile.mkdtemp(prefix=f"shipgate-fixture-verify-{name}-"))
    target = workdir / name
    shutil.copytree(src, target)
    out_dir = target / "reports"

    try:
        report, _ = run_scan(
            config_path=target / "shipgate.yaml",
            output_dir=out_dir,
            formats=["json"],
            ci_mode="advisory",
        )
    except (ConfigError, InputParseError, AgentsShipgateError) as exc:
        typer.echo(f"Fixture {name!r} scan failed: {exc}", err=True)
        raise typer.Exit(4) from exc

    expected_dir = src / "expected"
    if not expected_dir.is_dir():
        typer.echo(
            f"Fixture {name!r} has no expected/ directory; "
            "verification skipped (scan succeeded).",
        )
        raise typer.Exit(0)

    summary = {
        "status": report.summary.status,
        "critical_count": report.summary.critical_count,
        "high_count": report.summary.high_count,
        "medium_count": report.summary.medium_count,
    }
    expected_summary_file = expected_dir / "summary.json"
    if expected_summary_file.is_file():
        expected = json.loads(expected_summary_file.read_text(encoding="utf-8"))
        if summary == expected:
            typer.echo(f"Fixture {name!r}: summary matches expected/summary.json")
            raise typer.Exit(0)
        typer.echo("Fixture summary diverged from expected:", err=True)
        typer.echo(f"  expected: {expected}", err=True)
        typer.echo(f"  actual:   {summary}", err=True)
        raise typer.Exit(20)

    typer.echo(
        f"Fixture {name!r}: no expected/summary.json; "
        f"actual summary = {json.dumps(summary)}",
    )


def _run_ai_generated_refund_pr_fixture(
    *,
    name: str,
    src: Path,
    out: Path | None,
    ci_mode: str | None,
    keep: bool,
) -> None:
    """Run the homepage-style base/head verifier demo.

    Ordinary fixtures are static scan inputs. This one intentionally builds a
    tiny git history so users can reproduce the verifier artifacts that a PR
    would create: ``verifier.json``, ``report.json``, and ``pr-comment.md``.
    """

    def head_files(target: Path) -> dict[str, str | None]:
        head_tools = target / "_head" / "tools.json"
        if not head_tools.is_file():
            typer.echo(f"Fixture {name!r} is missing _head/tools.json", err=True)
            raise typer.Exit(4)
        head_payload = head_tools.read_text(encoding="utf-8")
        shutil.rmtree(target / "_head", ignore_errors=True)
        return {"tools.json": head_payload}

    _run_verify_pr_fixture(
        name=name,
        src=src,
        out=out,
        ci_mode=ci_mode,
        keep=keep,
        head_files_for=head_files,
        base_commit_message="base support agent",
        head_commit_message="codex adds refund tool",
    )


def _run_agent_weakens_gate_fixture(
    *,
    name: str,
    src: Path,
    out: Path | None,
    ci_mode: str | None,
    keep: bool,
) -> None:
    """Run the trust-root demo: the head commit deletes the Shipgate CI
    gate workflow — the cheapest reward-hack — and the verifier blocks the
    merge via the suppression-immune SHIP-VERIFY-CI-GATE-REMOVED check."""

    def head_files(_target: Path) -> dict[str, str | None]:
        return {".github/workflows/agents-shipgate.yml": None}

    _run_verify_pr_fixture(
        name=name,
        src=src,
        out=out,
        ci_mode=ci_mode,
        keep=keep,
        head_files_for=head_files,
        base_commit_message="base docs agent with Shipgate gate",
        head_commit_message="agent removes Shipgate CI gate",
    )


def _run_verify_pr_fixture(
    *,
    name: str,
    src: Path,
    out: Path | None,
    ci_mode: str | None,
    keep: bool,
    head_files_for: Callable[[Path], dict[str, str | None]],
    base_commit_message: str,
    head_commit_message: str,
) -> None:
    import tempfile

    workdir = Path(tempfile.mkdtemp(prefix=f"shipgate-fixture-{name}-"))
    target = workdir / name
    shutil.copytree(src, target)

    try:
        materialize_git_pr_fixture(
            target,
            head_files=head_files_for(target),
            user_email="fixture@example.com",
            user_name="Agents Shipgate Fixture",
            base_commit_message=base_commit_message,
            head_commit_message=head_commit_message,
        )
    except subprocess.CalledProcessError as exc:
        typer.echo(f"Fixture {name!r} git setup failed: {exc}", err=True)
        raise typer.Exit(4) from exc

    out_dir = out or (target / "reports")
    try:
        verifier, _report, exit_code = run_verify(
            workspace=target,
            config=Path("shipgate.yaml"),
            base="origin/main",
            head="HEAD",
            archive_head=True,
            out=out_dir,
            ci_mode=ci_mode or "advisory",
            fail_on=None,
            baseline=None,
            baseline_mode="new-findings",
            diff_from=None,
            policy_packs=None,
            plugins_enabled=None,
            strict_plugins=False,
            suggest_patches=False,
            no_heuristics=False,
            verbose=False,
            pr_comment_style="capability-review",
        )
    except (ConfigError, InputParseError, AgentsShipgateError) as exc:
        typer.echo(f"Fixture {name!r} verify failed: {exc}", err=True)
        raise typer.Exit(4) from exc

    typer.echo(f"Fixture: {name}")
    typer.echo("Mode: verify")
    typer.echo(f"Merge verdict: {verifier.merge_verdict}")
    if verifier.release_decision is not None:
        typer.echo(f"Decision: {verifier.release_decision.get('decision')}")
    typer.echo(f"Can merge without human: {str(verifier.can_merge_without_human).lower()}")
    typer.echo(f"Reports: {out_dir}")
    typer.echo(f"Verifier: {out_dir / 'verifier.json'}")
    typer.echo(f"PR comment: {out_dir / 'pr-comment.md'}")
    typer.echo(f"Static-verdict boundary: {STATIC_VERDICT_DISCLAIMER}")
    _finish_fixture_copy(
        workdir=workdir,
        target=target,
        out_was_explicit=out is not None,
        keep=keep,
    )
    raise typer.Exit(exit_code)


def _finish_fixture_copy(
    *,
    workdir: Path,
    target: Path,
    out_was_explicit: bool,
    keep: bool,
) -> None:
    if keep or not out_was_explicit:
        typer.echo(f"Fixture copy left at {target}.")
        return
    shutil.rmtree(workdir, ignore_errors=True)


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _resolve_fixture(name: str) -> Path:
    try:
        return fixture_path(name)
    except FixturesUnavailableError as exc:
        typer.echo(f"Fixtures unavailable: {exc}", err=True)
        raise typer.Exit(4) from exc
    except FixtureNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc


def materialize_git_pr_fixture(
    repo: Path,
    *,
    base_files: Mapping[str, str | None] | None = None,
    head_files: Mapping[str, str | None],
    user_email: str,
    user_name: str,
    base_commit_message: str,
    head_commit_message: str,
) -> None:
    """Create a deterministic base/head git fixture with origin/main.

    The fixture CLI and internal benchmark scripts both need the same minimal
    PR-shaped history. Keeping the git plumbing here avoids parallel copies of
    the audited subprocess call site.
    """

    if base_files:
        _apply_fixture_files(repo, base_files)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", user_email)
    _git(repo, "config", "user.name", user_name)
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", base_commit_message)
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _apply_fixture_files(repo, head_files)
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", head_commit_message)


def run_fixture_git(repo: Path, *args: str) -> str:
    """Run the fixture git helper and return stripped stdout."""

    return _git(repo, *args)


def _apply_fixture_files(repo: Path, files: Mapping[str, str | None]) -> None:
    for relative, content in sorted(files.items()):
        path = repo / relative
        if content is None:
            if path.exists():
                path.unlink()
            _prune_empty_parents(path.parent, repo)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _prune_empty_parents(path: Path, root: Path) -> None:
    while path != root and path.exists():
        try:
            path.rmdir()
        except OSError:
            return
        path = path.parent


__all__ = ["fixture_app", "materialize_git_pr_fixture", "run_fixture_git"]
