"""Rebuild the shipped sample artifacts with the current source-tree writers.

    python scripts/regenerate_goldens.py
    python scripts/regenerate_goldens.py --check
    python scripts/regenerate_goldens.py conductor_agent

Only synthetic samples are copied and committed in disposable repositories.
No source manifest, declaration, release receipt or qualification input is edited.
"""

from __future__ import annotations

import argparse
import contextlib
import html
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path, PurePosixPath, PureWindowsPath

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agents_shipgate.cli.scan import run_scan  # noqa: E402
from agents_shipgate.core.current_control import publish_current_control  # noqa: E402
from agents_shipgate.report.markdown import _safe_markdown_text  # noqa: E402
from agents_shipgate.schemas.current_control import CurrentControlPointer  # noqa: E402

GENERATED_AT = "2026-01-01T00:00:00+00:00"
# Explicit ownership: dropping a committed artifact must not silently shrink
# the check, and a new fixture needs a reviewed recipe, not guessed options.
REPORTS = ("report.json", "report.md")
RECIPES = {
    "conductor_agent": (
        *REPORTS,
        "current-control.json",
        "suggested-inventory.json",
        "summary.json",
    ),
    "declaration_repair_agent": (*REPORTS, "suggested-declarations.yaml"),
    "google_adk_cold_start_agent": (*REPORTS, "suggested-declarations.yaml", "cold-report.md"),
    "simple_crewai_agent": REPORTS,
    "simple_langchain_agent": REPORTS,
    "simple_openai_api_agent": REPORTS,
    "support_refund_agent": (*REPORTS, "packet.json", "packet.md", "packet.html", "summary.json"),
}


@contextlib.contextmanager
def _scan_environment() -> Iterator[None]:
    # This output sink changes privacy_audit and can write to the caller's CI
    # summary. Preserve the environment around the entirely local generation.
    keys = {key for key in os.environ if key.startswith("GIT_")} | {"GITHUB_STEP_SUMMARY"}
    saved = {key: os.environ[key] for key in keys if key in os.environ}
    for key in keys:
        os.environ.pop(key, None)
    os.environ.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    try:
        yield
    finally:
        for key in keys | {"GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM"}:
            os.environ.pop(key, None)
        os.environ.update(saved)


def _git(repo: Path, *args: str) -> None:
    # Ignore inherited repository handles; never execute contributor hooks or
    # signing helpers while creating the disposable fixture's committed state.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(
        GIT_AUTHOR_DATE=GENERATED_AT,
        GIT_COMMITTER_DATE=GENERATED_AT,
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "core.hooksPath=.git/no-golden-hooks",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.autocrlf=false",
            "-c",
            "user.name=Shipgate Golden",
            "-c",
            "user.email=shipgate@example.invalid",
            *args,
        ],
        check=True,
        capture_output=True,
        env=env,
    )


def _copy_sample(source: Path, repo: Path, *, cold: bool = False) -> None:
    # All recipe inputs are committed repository fixtures, not arbitrary paths.
    if source.is_symlink() or any(path.is_symlink() for path in source.rglob("*")):
        raise ValueError(f"{source.name}: sample inputs must not be symlinks")
    shutil.copytree(
        source,
        repo,
        ignore=shutil.ignore_patterns("expected", "__pycache__", ".git", "agents-shipgate-reports"),
    )
    # Git may check out these text inputs as CRLF on Windows. Scan the same LF
    # fixture bytes on every host, including the manifest that the pointer hashes.
    for path in repo.rglob("*"):
        if path.is_file():
            _lf(path)
    _git(repo, "init", "-q")
    if cold:
        _git(repo, "add", "--", "agent.py", "inventories", "specs")
    else:
        _git(repo, "add", "--", ".")
    _git(repo, "commit", "-qm", "synthetic golden inputs")


def _fixture_path(root: Path, relative: Path) -> Path:
    path = root
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError(f"{relative.as_posix()}: symlinked fixture path")
    if not path.resolve().is_relative_to(root):
        raise ValueError(f"{relative.as_posix()}: fixture path escapes repository")
    return path


def _lf(path: Path) -> None:
    path.write_bytes(path.read_text(encoding="utf-8").encode("utf-8"))


def _normalize_report(path: Path, sample: str) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if Path(payload["manifest_dir"]).resolve() != path.parent.parent.resolve():
        raise ValueError(f"{sample}/expected/report.json: unexpected manifest_dir")
    payload["manifest_dir"] = f"<REPO>/samples/{sample}"
    for key, value in payload["generated_reports"].items():
        if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute():
            raise ValueError(f"{sample}/expected/report.json: absolute generated_reports.{key}")
        # These are host-generated filesystem paths, not arbitrary report text.
        normalized = Path(value).as_posix()
        if PurePosixPath(normalized).parts != ("expected", Path(normalized).name):
            raise ValueError(f"{sample}/expected/report.json: unexpected output path {value!r}")
        payload["generated_reports"][key] = normalized
    # Match the product JSON writer, including its missing final newline.
    path.write_bytes(json.dumps(payload, indent=2).encode("utf-8"))


def _assert_no_machine_paths(path: Path, *roots: Path) -> None:
    text = path.read_text(encoding="utf-8")
    for root in roots:
        for spelling in (
            str(root),
            root.as_posix(),
            str(root.resolve()),
            root.resolve().as_posix(),
        ):
            if any(
                rendered in text
                for rendered in (
                    spelling,
                    json.dumps(spelling)[1:-1],
                    _safe_markdown_text(spelling),
                    html.escape(spelling),
                )
            ):
                raise ValueError(f"{path.name}: generating-machine path leaked into an artifact")


def _build_sample(root: Path, sample: str, temp: Path) -> dict[Path, bytes]:
    source = root / "samples" / sample
    repo = temp / sample
    _copy_sample(source, repo)
    out = repo / "expected"
    report, _ = run_scan(
        config_path=repo / "shipgate.yaml",
        output_dir=Path("expected"),
        formats=["markdown", "json"],
        ci_mode="advisory",
        packet_enabled=False,
        plugins_enabled=False,
    )
    _normalize_report(out / "report.json", sample)
    _lf(out / "report.md")
    if "summary.json" in RECIPES[sample]:
        # This existing compact fixture predates the full ReportSummary model.
        summary = report.summary.model_dump(mode="json")
        summary = {
            key: summary[key] for key in ("status", "critical_count", "high_count", "medium_count")
        }
        (out / "summary.json").write_bytes((json.dumps(summary, indent=2) + "\n").encode("utf-8"))
    if "packet.json" in RECIPES[sample]:
        # Report goldens deliberately have packet disabled. The packet golden
        # is a separate real scan with the same fixed date as its existing test.
        packet_out = repo / "packet-output"
        run_scan(
            config_path=repo / "shipgate.yaml",
            output_dir=packet_out,
            formats=["markdown", "json"],
            ci_mode="advisory",
            packet_generated_at=GENERATED_AT,
            plugins_enabled=False,
        )
        for name in ("packet.json", "packet.md", "packet.html"):
            shutil.copyfile(packet_out / name, out / name)
    if "cold-report.md" in RECIPES[sample]:
        cold = temp / "cold"
        _copy_sample(source, cold, cold=True)
        cold_out = cold / "reports"
        run_scan(
            config_path=cold / "shipgate.yaml",
            output_dir=cold_out,
            formats=["markdown", "json"],
            ci_mode="advisory",
            packet_generated_at=GENERATED_AT,
            plugins_enabled=False,
        )
        shutil.copyfile(cold_out / "report.md", out / "cold-report.md")
    for name in RECIPES[sample]:
        if name != "current-control.json":
            _lf(out / name)
    if "current-control.json" in RECIPES[sample]:
        pointer_path = out / "current-control.json"
        pointer = CurrentControlPointer.model_validate_json(pointer_path.read_bytes())
        # Fresh synthetic fixture, not a continuation of the last generating
        # machine's in-progress pointer. Rebind only AFTER normalization/LF.
        pointer_path.unlink()
        publish_current_control(
            out,
            operation=pointer.operation,
            control=pointer.control,
            workspace_identity=pointer.workspace_identity,
            artifact_keys={"report", "report_markdown"},
        )
        _lf(pointer_path)
    result = {}
    for name in RECIPES[sample]:
        path = out / name
        _assert_no_machine_paths(path, temp, root)
        result[Path("samples") / sample / "expected" / name] = path.read_bytes()
    return result


def build_goldens(root: Path = ROOT, samples: list[str] | None = None) -> dict[Path, bytes]:
    root = root.resolve()
    selected = sorted(RECIPES if samples is None else set(samples))
    unknown = sorted(set(selected) - RECIPES.keys())
    if unknown:
        raise ValueError(f"No golden recipe for: {', '.join(unknown)}")
    for sample in selected:
        _fixture_path(root, Path("samples") / sample)
    managed = {Path("samples") / s / "expected" / name for s in selected for name in RECIPES[s]}
    actual = {
        path.relative_to(root)
        for path in (root / "samples").glob("*/expected/*")
        if samples is None or path.parent.parent.name in selected
    }
    extras = sorted(actual - managed)
    if extras:
        raise ValueError(f"No golden recipe for: {', '.join(p.as_posix() for p in extras)}")
    result = {}
    with tempfile.TemporaryDirectory(prefix="shipgate-goldens-") as temp, _scan_environment():
        for sample in selected:
            result.update(_build_sample(root, sample, Path(temp)))
    return result


def main(argv: list[str] | None = None, *, root: Path = ROOT) -> int:
    root = root.resolve()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "samples", nargs="*", choices=None, help="sample directory names (default: all)"
    )
    parser.add_argument("--check", action="store_true", help="report drift without writing")
    args = parser.parse_args(argv)
    try:
        artifacts = build_goldens(root, args.samples or None)
        # Build and validate the entire selected set before any repository write.
        for path in artifacts:
            _fixture_path(root, path)
        changed = [
            path
            for path, data in artifacts.items()
            if not (root / path).is_file() or (root / path).read_bytes() != data
        ]
        for path in changed:
            if not args.check:
                target = _fixture_path(root, path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(artifacts[path])
            print(f"{'DRIFT' if args.check else 'WROTE'} {path.as_posix()}")
        print(
            f"{'Checked' if args.check else 'Generated'} {len(artifacts)} sample artifacts; {len(changed)} changed."
        )
        return int(args.check and bool(changed))
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Golden generation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
