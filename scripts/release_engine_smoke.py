#!/usr/bin/env python3
"""Exercise the installed candidate and Action on one disposable refund PR.

`prepare` commits synthetic fixture history into the checkout it is given.
Use only in the disposable release-engine-smoke job or a temporary clone.
The output is distribution evidence, not human labels or release qualification.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args], text=True, stderr=subprocess.PIPE,
    ).strip()


def _cli(root: Path, *args: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME"}}
    result = subprocess.run(
        [sys.executable, "-I", "-m", "agents_shipgate", *args],
        cwd=root, env=env, text=True, capture_output=True, check=False,
    )
    if result.returncode:
        raise ValueError(f"Installed CLI failed ({result.returncode}): {result.stderr}")
    return json.loads(result.stdout)


def prepare(root: Path, source_commit: str, wheel: Path) -> dict:
    if _git(root, "rev-parse", "HEAD") != source_commit:
        raise ValueError("Disposable checkout must still be at the candidate source commit")
    fixture = root / ".shipgate-smoke/fixture"
    if fixture.exists():
        raise ValueError("Smoke fixture must not pre-exist")
    sample = root / "samples/ai_generated_refund_pr"
    fixture.mkdir(parents=True)
    for name in ("shipgate.yaml", "tools.json"):
        shutil.copyfile(sample / name, fixture / name)
    _git(root, "config", "user.name", "Agents Shipgate distribution smoke")
    _git(root, "config", "user.email", "distribution-smoke@example.invalid")
    exclude = Path(_git(root, "rev-parse", "--git-path", "info/exclude"))
    if not exclude.is_absolute():
        exclude = root / exclude
    with exclude.open("a", encoding="utf-8") as handle:
        handle.write("\n/.shipgate-smoke/\n")
    _git(root, "add", "-f", ".shipgate-smoke/fixture/shipgate.yaml",
         ".shipgate-smoke/fixture/tools.json")
    _git(root, "-c", "commit.gpgsign=false", "commit", "-qm", "smoke: base support tools")
    base = _git(root, "rev-parse", "HEAD")
    shutil.copyfile(sample / "_head/tools.json", fixture / "tools.json")
    _git(root, "add", "-f", ".shipgate-smoke/fixture/tools.json")
    _git(root, "-c", "commit.gpgsign=false", "commit", "-qm", "smoke: add refund capability")
    contract = _cli(root, "contract", "--json")
    # Init runs in a separate disposable adopter directory so its generated
    # workflow is not a trust-root change in the reviewed fixture.
    with tempfile.TemporaryDirectory(prefix="shipgate-distribution-adopter-") as directory:
        adopter = Path(directory)
        shutil.copyfile(fixture / "shipgate.yaml", adopter / "shipgate.yaml")
        shutil.copyfile(fixture / "tools.json", adopter / "tools.json")
        _cli(adopter, "init", "--workspace", str(adopter), "--ci", "--json")
        generated_text = (adopter / ".github/workflows/agents-shipgate.yml").read_text()
    if f"ThreeMoonsLab/agents-shipgate@{source_commit}" not in generated_text:
        raise ValueError("Installed candidate generated a different Action source")
    if f'shipgate_version: "{contract["cli_version"]}"' not in generated_text:
        raise ValueError("Installed candidate did not pin its package version")
    _cli(root, "verify", "--workspace", str(root), "--config", str(fixture / "shipgate.yaml"),
         "--base", base, "--head", "HEAD", "--ci-mode", "advisory",
         "--out", str(root / ".shipgate-smoke/local"), "--format", "json")
    result = {
        "source_commit": source_commit, "action_ref": source_commit,
        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "contract": contract, "base_ref": base, "head_ref": _git(root, "rev-parse", "HEAD"),
        "generated_workflow": generated_text, "qualified": False,
    }
    (root / ".shipgate-smoke/prepared.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def compare(root: Path) -> dict:
    prepared = json.loads((root / ".shipgate-smoke/prepared.json").read_text())
    results = []
    for name in ("local", "ci"):
        directory = root / ".shipgate-smoke" / name
        report = json.loads((directory / "report.json").read_text())
        verifier = json.loads((directory / "verifier.json").read_text())
        plan = json.loads((directory / "verification-plan.json").read_text())
        results.append({
            "decision": report["release_decision"]["decision"],
            "merge_verdict": verifier["merge_verdict"],
            "findings": sorted((f["check_id"], f["severity"]) for f in report["findings"]),
            "engine": plan["engine"],
        })
    if results[0] != results[1] or results[0]["decision"] != "blocked":
        raise ValueError("Installed CLI and Action disagree, or the unsafe refund was not blocked")
    current_contract = _cli(root, "contract", "--json")
    if current_contract != prepared["contract"]:
        raise ValueError("Installed runtime contract changed between local and Action execution")
    return {**prepared, "local_and_action_agree": True, "result": results[0],
            "qualification_claim": "none: synthetic distribution smoke only"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "compare"))
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--source-commit")
    parser.add_argument("--wheel", type=Path)
    args = parser.parse_args()
    try:
        if args.operation == "prepare":
            if not args.source_commit or args.wheel is None:
                raise ValueError("prepare requires --source-commit and --wheel")
            result = prepare(args.workspace.resolve(), args.source_commit, args.wheel)
            if os.environ.get("GITHUB_OUTPUT"):
                with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
                    handle.write(f"base_ref={result['base_ref']}\nhead_ref={result['head_ref']}\n")
        else:
            result = compare(args.workspace.resolve())
        print(json.dumps(result, indent=2, sort_keys=True))
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"Release engine smoke failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
