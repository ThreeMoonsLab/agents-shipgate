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
runtime closure, then inventories it by **reading ``.dist-info`` metadata**.

That last part is a security property, not a style choice. ``cyclonedx-py
environment`` inventories by *launching* the target interpreter, and starting a
Python process runs ``site`` processing — which executes any ``.pth`` file
beginning with ``import``. Those files come from the wheel's runtime dependency
closure, resolved unpinned from the index, so the previous implementation ran
third-party code inside the job that seals the release, before the handoff
digests were computed. Parsing metadata files cannot execute anything.

Installation is wheels-only for the same reason: an sdist would run its build
backend during resolution.

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
import re
import subprocess
import sys
import tempfile
import tomllib
import venv
from email.parser import BytesParser
from email.policy import default as email_policy
from pathlib import Path
from typing import Any

if __package__:
    from scripts._release_support import ReleaseError as ConfigError
    from scripts._release_support import canonicalize_name, inspect_wheel
else:  # ``python scripts/release_sbom.py``
    from _release_support import ReleaseError as ConfigError
    from _release_support import canonicalize_name, inspect_wheel

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
    dev_names = {_requirement_name(item) for item in dev}
    runtime_names = {_requirement_name(item) for item in runtime}
    return dev_names - runtime_names


def _requirement_name(requirement: str) -> str:
    """Canonical distribution name from a PEP 508 requirement string.

    Only the leading name is needed, so this avoids a ``packaging`` import and
    keeps the module usable from the publication jobs, which install no
    project dependencies.
    """

    match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)
    if not match:
        raise ConfigError(f"Unparsable requirement string: {requirement!r}")
    return canonicalize_name(match.group(1))


def _component_names(document: dict[str, Any]) -> set[str]:
    return {
        canonicalize_name(str(component.get("name", "")))
        for component in document.get("components", [])
        if component.get("name")
    }


def _assert_runtime_only(document: dict[str, Any], *, sbom_path: Path) -> None:
    forbidden = dev_only_distributions(REPO_ROOT / "pyproject.toml")
    present = sorted(forbidden & _component_names(document))
    if present:
        raise ConfigError(
            f"SBOM {sbom_path} inventories dev-only distributions ({', '.join(present)}); "
            "it describes a development environment rather than the shipped wheel."
        )


def _site_packages(env_dir: Path) -> Path:
    candidates = sorted(env_dir.glob("lib/python*/site-packages")) + [env_dir / "Lib/site-packages"]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    raise ConfigError(f"No site-packages directory under {env_dir}")


def _read_metadata(dist_info: Path) -> dict[str, Any]:
    """Parse one ``.dist-info/METADATA`` without importing anything from it."""

    path = dist_info / "METADATA"
    if not path.is_file():
        raise ConfigError(f"Installed distribution has no METADATA: {dist_info}")
    message = BytesParser(policy=email_policy).parsebytes(path.read_bytes())
    name = str(message.get("Name", "")).strip()
    version = str(message.get("Version", "")).strip()
    if not name or not version:
        raise ConfigError(f"Installed distribution has no Name/Version: {dist_info}")
    licenses = [
        str(value).strip()
        for value in message.get_all("License-Expression", [])
        or message.get_all("License", [])
        or []
        if str(value).strip()
    ]
    requires = [
        str(value).strip() for value in message.get_all("Requires-Dist", []) or [] if str(value)
    ]
    return {"name": name, "version": version, "licenses": licenses, "requires": requires}


def _component(entry: dict[str, Any]) -> dict[str, Any]:
    canonical = canonicalize_name(entry["name"])
    component: dict[str, Any] = {
        "type": "library",
        "bom-ref": f"{canonical}=={entry['version']}",
        "name": entry["name"],
        "version": entry["version"],
        "purl": f"pkg:pypi/{canonical}@{entry['version']}",
    }
    if entry["licenses"]:
        component["licenses"] = [{"license": {"name": value}} for value in entry["licenses"]]
    return component


def inventory_environment(env_dir: Path) -> list[dict[str, Any]]:
    """Inventory installed distributions by reading ``.dist-info`` directories.

    Deliberately does *not* launch the target interpreter. `cyclonedx-py
    environment` does, and starting a Python process runs `site` processing —
    which executes any ``.pth`` file beginning with ``import``. Those files come
    from the wheel's runtime dependency closure, resolved from the index, so
    launching that interpreter would run third-party code inside the job that
    seals the release, before the handoff digests are computed. Reading
    metadata files cannot execute anything.
    """

    site_packages = _site_packages(env_dir)
    entries = [_read_metadata(path) for path in sorted(site_packages.glob("*.dist-info"))]
    if not entries:
        raise ConfigError(f"No installed distributions found under {site_packages}")
    return entries


def _dependency_graph(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Edges between installed distributions, from ``Requires-Dist`` names.

    Environment markers and extras are not evaluated: the *component set* is
    exact because it is what was installed, while the edge set is a documented
    over-approximation of the closure.
    """

    by_canonical = {canonicalize_name(entry["name"]): entry for entry in entries}
    graph = []
    for entry in entries:
        canonical = canonicalize_name(entry["name"])
        depends = sorted(
            {
                f"{name}=={by_canonical[name]['version']}"
                for name in (_requirement_name(item) for item in entry["requires"])
                if name in by_canonical and name != canonical
            }
        )
        graph.append({"ref": f"{canonical}=={entry['version']}", "dependsOn": depends})
    return graph


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
                # Wheels only: an sdist would execute its build backend during
                # resolution, which is exactly the code execution this module
                # is structured to avoid.
                "--only-binary",
                ":all:",
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
        entries = inventory_environment(env_dir)

    subject = next(
        (
            entry
            for entry in entries
            if canonicalize_name(entry["name"]) == wheel_name and entry["version"] == wheel_version
        ),
        None,
    )
    if subject is None:
        raise ConfigError(
            f"SBOM does not inventory {wheel_name} {wheel_version}; the isolated "
            "environment did not contain the wheel under test."
        )

    subject_component = _component(subject)
    subject_component["hashes"] = [{"alg": "SHA-256", "content": wheel_sha256}]
    subject_component["properties"] = [{"name": WHEEL_FILENAME_PROPERTY, "value": wheel_path.name}]

    document = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": {"component": subject_component},
        # The subject appears once, as the metadata component; listing it again
        # here would leave consumers unable to tell which node the document is
        # about.
        "components": [_component(entry) for entry in entries if entry["name"] != subject["name"]],
        "dependencies": _dependency_graph(entries),
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

    _assert_subject_is_singular(document, sbom_path=sbom_path, wheel_name=wheel_name)
    _assert_runtime_only(document, sbom_path=sbom_path)
    return document


def _assert_subject_is_singular(
    document: dict[str, Any], *, sbom_path: Path, wheel_name: str
) -> None:
    """The subject appears once, and the dependency graph still refers to it.

    Guards the failure mode of describing the wheel both as the metadata
    subject and as an ordinary installed package, which leaves consumers
    unable to tell which node the document is actually about.
    """

    duplicates = sorted(
        str(component.get("bom-ref", component.get("name")))
        for component in document.get("components", [])
        if canonicalize_name(str(component.get("name", ""))) == wheel_name
    )
    if duplicates:
        raise ConfigError(
            f"SBOM {sbom_path} lists {wheel_name} in components as well as "
            f"metadata.component ({', '.join(duplicates)}); the subject is described twice."
        )

    subject_ref = str(document["metadata"]["component"].get("bom-ref", ""))
    dependencies = document.get("dependencies")
    if dependencies is None:
        # cyclonedx-py emits a graph, but an SBOM without one is still bound by
        # the digest checks above; only assert consistency when it is present.
        return
    matching = [
        node for node in dependencies if str(node.get("ref", "")) == subject_ref and subject_ref
    ]
    if len(matching) != 1:
        raise ConfigError(
            f"SBOM {sbom_path} has {len(matching)} dependency nodes for the declared "
            f"subject {subject_ref!r}; expected exactly one."
        )


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
