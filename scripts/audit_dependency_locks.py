#!/usr/bin/env python3
"""Audit the reviewed pins selected by this interpreter without resolving packages."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

from packaging.markers import Marker, default_environment
from packaging.utils import canonicalize_name
from packaging.version import Version

if __package__:
    from scripts._release_support import ReleaseError
    from scripts.verify_dependency_lock import BACKEND_LOCK, CO_INSTALLED, REPO_ROOT, parse_lock
else:  # python scripts/audit_dependency_locks.py
    from _release_support import ReleaseError
    from verify_dependency_lock import BACKEND_LOCK, CO_INSTALLED, REPO_ROOT, parse_lock


SUBJECTS = {
    "test-build": next(group for group in CO_INSTALLED if BACKEND_LOCK in group),
    "release-publishing": ("constraints/release-publish.txt",),
}


def _collect_pins(root: Path, out: Path, locks: tuple[str, ...], evidence: dict) -> dict[str, Version]:
    expected: dict[str, Version] = {}
    for name in locks:
        raw = (root / name).read_bytes()
        snapshot = out / name
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(raw)
        evidence["locks"].append({"path": name, "sha256": hashlib.sha256(raw).hexdigest()})
        for package, pins in parse_lock(snapshot).items():
            for pin in pins:
                if pin.marker and not Marker(pin.marker).evaluate(evidence["environment"]):
                    continue
                # A URL does not identify a package version for an advisory lookup.
                if pin.version is None or not pin.hashes:
                    evidence["unsupported"].append({
                        "name": package, "lock": name, "line": pin.line,
                        "reason": "direct URL" if pin.version is None else "pin has no hash",
                    })
                    continue
                version = Version(pin.version)
                if expected.setdefault(package, version) != version:
                    raise ReleaseError(f"Conflicting active pins for {package} in {name}.")
    if not expected:
        raise ReleaseError("The selected locks have no auditable pins for this environment.")
    return expected


def _installed_mismatches(expected: dict[str, Version]) -> list[dict]:
    mismatches = []
    for name, pinned in sorted(expected.items()):
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            installed = None
        if installed is None or Version(installed) != pinned:
            mismatches.append({"name": name, "pinned": str(pinned), "installed": installed})
    return mismatches


def _read_results(path: Path, expected: dict[str, Version], evidence: dict) -> None:
    result = json.loads(path.read_bytes())
    if not isinstance(result, dict) or not isinstance(result.get("dependencies"), list):
        raise ReleaseError("pip-audit did not return a dependency list.")
    seen = set()
    audited: dict[str, Version] = {}
    findings = 0
    for item in result["dependencies"]:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ReleaseError("pip-audit returned a dependency without a name.")
        name = canonicalize_name(item["name"])
        if name in seen:
            raise ReleaseError(f"pip-audit returned duplicate results for {name}.")
        seen.add(name)
        if isinstance(item.get("skip_reason"), str):
            evidence["skipped"].append({"name": name, "reason": item["skip_reason"]})
            continue
        if not isinstance(item.get("version"), str) or not isinstance(item.get("vulns"), list):
            raise ReleaseError(f"pip-audit returned incomplete audit data for {name}.")
        audited[name] = Version(item["version"])
        findings += len(item["vulns"])
    evidence["finding_count"] = findings
    evidence["missing_or_mismatched"] = sorted(name for name in expected if audited.get(name) != expected[name])
    evidence["unexpected"] = sorted(seen - expected.keys())


def audit(root: Path, out: Path, subject: str, *, check_installed: bool = False) -> int:
    out.mkdir(parents=True, exist_ok=False)
    evidence: dict = {
        "subject": subject,
        "environment": default_environment(),
        "started_at_utc": datetime.now(UTC).isoformat(),
        "advisory_source": "pypi",
        "locks": [],
        "unsupported": [],
        "skipped": [],
        "status": "incomplete",
    }
    try:
        evidence["pip_audit_version"] = metadata.version("pip-audit")
        pins = _collect_pins(root, out, SUBJECTS[subject], evidence)
        inventory = "".join(f"{name}=={version}\n" for name, version in sorted(pins.items())).encode()
        requirements = out / "requirements.txt"
        requirements.write_bytes(inventory)
        evidence["inventory_sha256"] = hashlib.sha256(inventory).hexdigest()
        evidence["inventory_size"] = len(pins)
        evidence["installed_mismatches"] = _installed_mismatches(pins) if check_installed else None
        report = out / "pip-audit.json"
        run = subprocess.run([
            sys.executable, "-m", "pip_audit", "--requirement", str(requirements),
            "--no-deps", "--disable-pip", "--vulnerability-service", evidence["advisory_source"],
            "--format", "json", "--desc", "off", "--progress-spinner", "off", "--output", str(report),
        ], check=False)
        evidence["pip_audit_exit_code"] = run.returncode
        _read_results(report, pins, evidence)
        if run.returncode not in (0, 1) or any(evidence[key] for key in (
            "unsupported", "skipped", "missing_or_mismatched", "unexpected", "installed_mismatches",
        )):
            return 2
        if bool(run.returncode) != bool(evidence["finding_count"]):
            raise ReleaseError("pip-audit's exit code disagrees with its findings.")
        evidence["status"] = "findings" if run.returncode else "passed"
        return run.returncode
    except (OSError, ValueError, ReleaseError, metadata.PackageNotFoundError) as exc:
        evidence["error"] = str(exc)
        return 2
    finally:
        evidence["finished_at_utc"] = datetime.now(UTC).isoformat()
        (out / "evidence.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subject", required=True, choices=SUBJECTS)
    parser.add_argument("--out", required=True, type=Path, help="A new directory for the audit evidence")
    parser.add_argument("--check-installed", action="store_true", help="Compare every active pin with the installed version")
    args = parser.parse_args()
    try:
        status = audit(REPO_ROOT, args.out, args.subject, check_installed=args.check_installed)
    except OSError as exc:
        print(f"Dependency audit could not write its evidence: {exc}", file=sys.stderr)
        return 2
    outcome = {0: "passed", 1: "found advisories", 2: "incomplete"}[status]
    print(f"{args.subject} audit {outcome}. Evidence is in {args.out / 'evidence.json'}.")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
