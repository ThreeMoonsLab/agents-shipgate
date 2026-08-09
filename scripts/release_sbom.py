#!/usr/bin/env python3
"""Generate and verify an SBOM scoped to the shipped wheel.

The release workflow used to install ``.[dev]`` and run ``cyclonedx-py
environment`` against that, which inventoried the CI environment — pytest,
ruff, twine, Sigstore, and the CycloneDX tooling itself — rather than the
runtime dependency surface of the wheel being published. A signed SBOM that
describes the wrong artifact is worse than no SBOM: it is an attested claim
about software the user never receives.

``build`` installs *only* the wheel into an isolated interpreter created with
``--without-pip``, so the resulting environment is exactly the wheel plus its
runtime closure, then inventories that. Two post-processing steps matter:

1. CycloneDX records the wheel it installed from as an ``externalReferences``
   entry of type ``distribution``, including a ``file://`` URL pointing at the
   build machine's temporary directory. That path varies per run, so it would
   make the signed SBOM non-deterministic, and it leaks runner filesystem
   layout into a published artifact. The URL is reduced to the wheel basename;
   the SHA-256 alongside it is kept.
2. The inventory has no ``metadata.component``, so nothing in the document
   says which artifact it describes. The wheel is promoted to that slot with
   its digest, which is what ``verify`` later binds against.

``verify`` re-derives the wheel digest and refuses any SBOM that describes
different bytes, a different version, or an environment containing a dev-only
distribution. It runs before publication so a mismatch cannot ship.

Run from the repo root:

    python scripts/release_sbom.py build --wheel dist/agents_shipgate-*.whl \\
        --output dist/agents-shipgate-sbom.json
    python scripts/release_sbom.py verify --wheel dist/agents_shipgate-*.whl \\
        --sbom dist/agents-shipgate-sbom.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import venv
from pathlib import Path
from typing import Any

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from agents_shipgate.core.errors import ConfigError

if __package__:
    from scripts.run_safety_qualification import inspect_wheel
else:  # ``python scripts/release_sbom.py``
    from run_safety_qualification import inspect_wheel

REPO_ROOT = Path(__file__).resolve().parent.parent
WHEEL_FILENAME_PROPERTY = "agents-shipgate:wheel-filename"


def dev_only_distributions(pyproject_path: Path) -> set[str]:
    """Return canonical names that must never appear in a runtime-only SBOM.

    Derived from the ``dev`` extra rather than hardcoded, so adding a new dev
    tool extends the guard automatically. Declared runtime dependencies are
    subtracted: a distribution that is legitimately needed at runtime is not
    dev-only even if a dev tool also depends on it.
    """

    try:
        data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"Unable to read {pyproject_path}: {exc}") from exc
    project = data.get("project", {})
    dev = project.get("optional-dependencies", {}).get("dev", [])
    runtime = project.get("dependencies", [])
    if not dev:
        raise ConfigError(f"{pyproject_path} declares no [project.optional-dependencies].dev")
    dev_names = {canonicalize_name(Requirement(item).name) for item in dev}
    runtime_names = {canonicalize_name(Requirement(item).name) for item in runtime}
    return dev_names - runtime_names


def _component_names(document: dict[str, Any]) -> set[str]:
    return {
        canonicalize_name(str(component.get("name", "")))
        for component in document.get("components", [])
        if component.get("name")
    }


def _normalise_external_references(document: dict[str, Any], wheel_name: str) -> None:
    """Replace build-machine ``file://`` URLs with the wheel basename."""

    for component in [*document.get("components", []), document.get("metadata", {})]:
        for reference in component.get("externalReferences", []) or []:
            url = str(reference.get("url", ""))
            if url.startswith("file://"):
                reference["url"] = wheel_name


def _assert_runtime_only(document: dict[str, Any], *, sbom_path: Path) -> None:
    forbidden = dev_only_distributions(REPO_ROOT / "pyproject.toml")
    present = sorted(forbidden & _component_names(document))
    if present:
        raise ConfigError(
            f"SBOM {sbom_path} inventories dev-only distributions ({', '.join(present)}); "
            "it describes a development environment rather than the shipped wheel."
        )


def build_release_sbom(*, wheel_path: Path, output_path: Path) -> dict[str, Any]:
    """Inventory an isolated runtime-only installation of ``wheel_path``."""

    wheel_name, wheel_version, wheel_sha256 = inspect_wheel(wheel_path)

    with tempfile.TemporaryDirectory(prefix="shipgate-sbom-") as workdir:
        env_dir = Path(workdir) / "runtime"
        # ``with_pip=False`` keeps pip, setuptools, and wheel out of the
        # inventory: they are installer plumbing, not part of what ships.
        #
        # ``symlinks`` mirrors what ``python -m venv`` does on this platform.
        # EnvBuilder's constructor defaults to False where the CLI defaults to
        # True on POSIX, and a *copied* interpreter cannot resolve
        # ``@rpath/libpython3.12.dylib`` on macOS, so the environment is built
        # but every subsequent call into it dies in dyld.
        venv.EnvBuilder(with_pip=False, clear=True, symlinks=os.name != "nt").create(env_dir)
        env_python = env_dir / ("Scripts" if sys.platform == "win32" else "bin") / "python"
        install = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "--python",
                str(env_python),
                "install",
                "--quiet",
                "--no-input",
                str(wheel_path),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if install.returncode != 0:
            raise ConfigError(
                f"Unable to install {wheel_path} into an isolated environment: {install.stderr}"
            )

        raw_path = Path(workdir) / "raw-sbom.json"
        generate = subprocess.run(
            [
                sys.executable,
                "-m",
                "cyclonedx_py",
                "environment",
                "--output-reproducible",
                "--of",
                "JSON",
                "-o",
                str(raw_path),
                str(env_python),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if generate.returncode != 0:
            raise ConfigError(f"cyclonedx-py failed for {wheel_path}: {generate.stderr}")
        document = json.loads(raw_path.read_text(encoding="utf-8"))

    _normalise_external_references(document, wheel_path.name)
    metadata = document.setdefault("metadata", {})
    metadata["component"] = {
        "type": "library",
        "bom-ref": f"{wheel_name}-wheel",
        "name": wheel_name,
        "version": wheel_version,
        "hashes": [{"alg": "SHA-256", "content": wheel_sha256}],
        "properties": [{"name": WHEEL_FILENAME_PROPERTY, "value": wheel_path.name}],
    }
    _assert_runtime_only(document, sbom_path=output_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return document


def verify_release_sbom(*, wheel_path: Path, sbom_path: Path) -> dict[str, Any]:
    """Fail closed unless the SBOM describes exactly this wheel's runtime surface."""

    if not sbom_path.is_file():
        raise ConfigError(f"SBOM not found: {sbom_path}")
    try:
        document = json.loads(sbom_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Invalid SBOM {sbom_path}: {exc}") from exc
    if not isinstance(document, dict):
        raise ConfigError(f"SBOM {sbom_path} must contain a JSON object")

    wheel_name, wheel_version, wheel_sha256 = inspect_wheel(wheel_path)
    component = document.get("metadata", {}).get("component")
    if not isinstance(component, dict):
        raise ConfigError(f"SBOM {sbom_path} has no metadata.component to bind against")

    errors: list[str] = []
    if canonicalize_name(str(component.get("name", ""))) != wheel_name:
        errors.append(f"SBOM component name {component.get('name')!r} is not {wheel_name!r}")
    if str(component.get("version", "")) != wheel_version:
        errors.append(f"SBOM component version {component.get('version')!r} is not {wheel_version}")
    digests = {
        str(entry.get("content", ""))
        for entry in component.get("hashes", []) or []
        if str(entry.get("alg", "")).upper() == "SHA-256"
    }
    if wheel_sha256 not in digests:
        errors.append(
            f"SBOM records no SHA-256 matching the wheel ({wheel_sha256}); found {sorted(digests)}"
        )
    if errors:
        raise ConfigError(
            f"SBOM {sbom_path} is not bound to {wheel_path.name}: " + "; ".join(errors)
        )

    _assert_runtime_only(document, sbom_path=sbom_path)
    return document


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build or verify an SBOM scoped to the published wheel."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="generate a wheel-scoped SBOM")
    build.add_argument("--wheel", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)

    verify = subparsers.add_parser("verify", help="verify an SBOM is bound to the wheel")
    verify.add_argument("--wheel", type=Path, required=True)
    verify.add_argument("--sbom", type=Path, required=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "build":
            build_release_sbom(wheel_path=args.wheel, output_path=args.output)
            target = args.output
        else:
            verify_release_sbom(wheel_path=args.wheel, sbom_path=args.sbom)
            target = args.sbom
    except (ConfigError, OSError, ValueError) as exc:
        sys.stderr.write(f"Release SBOM error: {exc}\n")
        return 1
    sys.stdout.write(f"OK: {target} describes the runtime surface of {args.wheel.name}.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
