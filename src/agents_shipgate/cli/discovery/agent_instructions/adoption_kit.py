"""File-backed adoption-kit rendering for repo-scoped skill bundles."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

import yaml

from agents_shipgate import __version__

DEFAULT_CONFIG_RELATIVE_PATH = ".agents-shipgate/adoption-kit.yaml"
KIT_METADATA_FILENAME = ".agents-shipgate-kit-metadata.json"
SIDECAR_FILENAME = ".agents-shipgate-kit.json"
KIT_SCHEMA_VERSION = 1

KitSource = Literal["bundled", "local_override", "bundled_plus_local_override"]

_TEMPLATE_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


class AdoptionKitError(ValueError):
    """Raised when an adoption-kit config or override tree is invalid."""

    def __init__(self, message: str, *, path: Path | None = None) -> None:
        super().__init__(message)
        self.path = path


@dataclass(frozen=True)
class KitTarget:
    name: str
    target_root: str
    bundled_dir: str


KIT_TARGETS: dict[str, KitTarget] = {
    "codex-skill": KitTarget(
        name="codex-skill",
        target_root=".agents/skills/agents-shipgate",
        bundled_dir="codex-skill",
    ),
    "claude-code-skill": KitTarget(
        name="claude-code-skill",
        target_root=".claude/skills/agents-shipgate",
        bundled_dir="claude-code-skill",
    ),
}


@dataclass(frozen=True)
class AdoptionKitConfig:
    """Validated downstream override configuration."""

    path: Path
    source_id: str
    target_overrides: dict[str, Path]


@dataclass(frozen=True)
class RenderedAdoptionKit:
    """Rendered target tree plus provenance for JSON output and sidecars."""

    target: str
    files: dict[str, str]
    root_files: dict[str, str]
    kit_source: KitSource
    kit_source_id: str


@dataclass(frozen=True)
class KitSidecar:
    target: str
    kit_source: str
    kit_source_id: str
    writer_version: str
    file_hashes: dict[str, str]


def load_adoption_kit_config(
    workspace: Path, config_path: Path | None = None
) -> AdoptionKitConfig | None:
    """Load the optional repo-local adoption-kit override config.

    Relative ``config_path`` values are resolved against ``workspace`` so
    ``--workspace other-repo --agent-instructions-kit .agents-shipgate/...``
    stays repo-local.
    """

    workspace = workspace.resolve()
    if config_path is None:
        raw_path = workspace / DEFAULT_CONFIG_RELATIVE_PATH
        if not raw_path.exists():
            return None
    else:
        raw_path = config_path if config_path.is_absolute() else workspace / config_path
    symlink = first_symlink_in_chain(raw_path, workspace)
    if symlink is not None:
        raise AdoptionKitError(
            f"{symlink} is a symlink; refusing to read adoption-kit config.",
            path=raw_path,
        )
    path = raw_path.resolve()
    _ensure_under_workspace(path, workspace)
    if not path.is_file():
        raise AdoptionKitError(f"Adoption-kit config not found: {path}", path=path)

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise AdoptionKitError(
            f"Could not read adoption-kit config {path}: {exc}",
            path=path,
        ) from exc
    if not isinstance(raw, dict):
        raise AdoptionKitError(
            f"Adoption-kit config {path} must be a YAML mapping.",
            path=path,
        )
    if raw.get("schema_version") != KIT_SCHEMA_VERSION:
        raise AdoptionKitError(
            f"Adoption-kit config {path} must set schema_version: {KIT_SCHEMA_VERSION}.",
            path=path,
        )
    targets_raw = raw.get("targets") or {}
    if not isinstance(targets_raw, dict):
        raise AdoptionKitError(
            f"Adoption-kit config {path} field 'targets' must be a mapping.",
            path=path,
        )

    target_overrides: dict[str, Path] = {}
    for target, target_raw in targets_raw.items():
        if target not in KIT_TARGETS:
            raise AdoptionKitError(
                f"Adoption-kit config {path} has unknown target {target!r}.",
                path=path,
            )
        if not isinstance(target_raw, dict):
            raise AdoptionKitError(
                f"Adoption-kit config {path} target {target!r} must be a mapping.",
                path=path,
            )
        overrides_value = target_raw.get("overrides_dir")
        if not isinstance(overrides_value, str) or not overrides_value.strip():
            raise AdoptionKitError(
                f"Adoption-kit config {path} target {target!r} must set overrides_dir.",
                path=path,
            )
        overrides_path = workspace / overrides_value
        symlink = first_symlink_in_chain(overrides_path, workspace)
        if symlink is not None:
            raise AdoptionKitError(
                f"{symlink} is a symlink; refusing to read adoption-kit overrides.",
                path=path,
            )
        resolved_overrides = overrides_path.resolve()
        _ensure_under_workspace(resolved_overrides, workspace)
        if not resolved_overrides.is_dir():
            raise AdoptionKitError(
                f"Adoption-kit overrides_dir not found for {target!r}: "
                f"{resolved_overrides}",
                path=path,
            )
        target_overrides[target] = resolved_overrides

    return AdoptionKitConfig(
        path=path,
        source_id=path.relative_to(workspace).as_posix(),
        target_overrides=target_overrides,
    )


def render_adoption_kit(
    target: str, config: AdoptionKitConfig | None = None
) -> RenderedAdoptionKit:
    """Render bundled content plus any validated local overrides."""

    spec = _target(target)
    bundled_files = _read_bundled_files(spec)
    override_root = config.target_overrides.get(target) if config else None
    override_files = _read_override_files(override_root) if override_root else {}
    root_files = {**bundled_files, **override_files}
    root_files = {
        rel: _render_template(text)
        for rel, text in sorted(root_files.items(), key=lambda item: item[0])
    }
    files = {
        f"{spec.target_root}/{rel}": text
        for rel, text in root_files.items()
    }
    if not override_files:
        kit_source: KitSource = "bundled"
        kit_source_id = f"bundled:{spec.bundled_dir}"
    elif set(bundled_files).issubset(override_files):
        kit_source = "local_override"
        kit_source_id = f"local:{config.source_id}:{target}" if config else f"local:{target}"
    else:
        kit_source = "bundled_plus_local_override"
        kit_source_id = (
            f"bundled:{spec.bundled_dir}+local:{config.source_id}:{target}"
            if config
            else f"bundled:{spec.bundled_dir}+local:{target}"
        )
    return RenderedAdoptionKit(
        target=target,
        files=files,
        root_files=root_files,
        kit_source=kit_source,
        kit_source_id=kit_source_id,
    )


def render_bundle_text(
    target: str, config: AdoptionKitConfig | None = None
) -> str:
    """Return a human-readable dry-run rendering of a full kit target."""

    chunks: list[str] = []
    for path, text in render_adoption_kit(target, config).files.items():
        chunks.append(f"--- {path} ---\n{text.rstrip()}\n")
    return "\n".join(chunks)


def prior_render_hashes(target: str) -> dict[str, tuple[str, ...]]:
    """Return import-compatible prior hashes keyed by workspace-relative path."""

    return _metadata_hashes(target, "prior_render_sha256")


def bootstrap_legacy_hashes(target: str) -> dict[str, tuple[str, ...]]:
    """Return pre-sidecar bootstrap hashes keyed by workspace-relative path."""

    return _metadata_hashes(target, "bootstrap_legacy_sha256")


def build_sidecar(rendered: RenderedAdoptionKit) -> dict[str, object]:
    """Build the sidecar JSON payload for a rendered kit target."""

    return {
        "schema_version": KIT_SCHEMA_VERSION,
        "target": rendered.target,
        "kit_source": rendered.kit_source,
        "kit_source_id": rendered.kit_source_id,
        "writer_version": __version__,
        "file_hashes": _root_file_hashes(rendered.root_files),
    }


def parse_sidecar(path: Path) -> KitSidecar | None:
    """Parse a sidecar if present; return ``None`` for missing/invalid."""

    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    if raw.get("schema_version") != KIT_SCHEMA_VERSION:
        return None
    target = raw.get("target")
    kit_source = raw.get("kit_source")
    kit_source_id = raw.get("kit_source_id")
    writer_version = raw.get("writer_version")
    file_hashes = raw.get("file_hashes")
    if not all(
        isinstance(value, str)
        for value in (target, kit_source, kit_source_id, writer_version)
    ):
        return None
    if not isinstance(file_hashes, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in file_hashes.items()
    ):
        return None
    return KitSidecar(
        target=str(target),
        kit_source=str(kit_source),
        kit_source_id=str(kit_source_id),
        writer_version=str(writer_version),
        file_hashes=dict(file_hashes),
    )


def sidecar_text(rendered: RenderedAdoptionKit) -> str:
    return json.dumps(build_sidecar(rendered), indent=2, sort_keys=True) + "\n"


def root_relative_path(target: str, workspace_relative_path: str) -> str:
    """Convert a rendered workspace-relative path to a kit-root path."""

    spec = _target(target)
    prefix = f"{spec.target_root}/"
    if not workspace_relative_path.startswith(prefix):
        raise ValueError(
            f"{workspace_relative_path!r} is not under target root {spec.target_root!r}"
        )
    return workspace_relative_path.removeprefix(prefix)


def first_symlink_in_chain(path: Path, workspace: Path) -> Path | None:
    """Return the first existing symlink between ``workspace`` and ``path``."""

    workspace_real = workspace.resolve()
    try:
        relative_parts = path.relative_to(workspace_real).parts
    except ValueError:
        try:
            relative_parts = path.resolve().relative_to(workspace_real).parts
        except ValueError:
            return path
    cur = workspace_real
    for part in relative_parts:
        cur = cur / part
        if cur.is_symlink():
            return cur
        if not cur.exists():
            return None
    return None


def _target(target: str) -> KitTarget:
    try:
        return KIT_TARGETS[target]
    except KeyError as exc:
        raise AdoptionKitError(f"Unknown adoption-kit target {target!r}.") from exc


def _read_bundled_files(spec: KitTarget) -> dict[str, str]:
    root = _bundled_target_root(spec)
    if root is None:
        raise AdoptionKitError(
            f"Bundled adoption kit {spec.bundled_dir!r} is not available."
        )
    return _read_tree(root)


def _bundled_target_root(spec: KitTarget) -> Any | None:
    try:
        bundled = files("agents_shipgate") / "_adoption_kits" / spec.bundled_dir
        if bundled.is_dir():
            return bundled
    except (ModuleNotFoundError, FileNotFoundError):
        pass

    here = Path(__file__).resolve().parent
    for parent in [here, *here.parents]:
        candidate = parent / "adoption-kits" / spec.bundled_dir
        if candidate.is_dir():
            return candidate
    return None


def _read_tree(root: Any) -> dict[str, str]:
    output: dict[str, str] = {}

    def walk(node: Any, prefix: str = "") -> None:
        for child in sorted(node.iterdir(), key=lambda item: item.name):
            rel = f"{prefix}{child.name}"
            if child.name == KIT_METADATA_FILENAME:
                continue
            if child.is_dir():
                walk(child, f"{rel}/")
            elif child.is_file():
                output[rel] = child.read_text(encoding="utf-8")

    walk(root)
    return output


def _read_override_files(root: Path) -> dict[str, str]:
    output: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        if path.name == SIDECAR_FILENAME:
            raise AdoptionKitError(
                f"Adoption-kit overrides may not define {SIDECAR_FILENAME}: {path}",
                path=path,
            )
        if first_symlink_in_chain(path, root) is not None or path.is_symlink():
            raise AdoptionKitError(
                f"{path} is a symlink; refusing to read adoption-kit override.",
                path=path,
            )
        resolved = path.resolve()
        _ensure_under_workspace(resolved, root.resolve())
        try:
            rel = resolved.relative_to(root.resolve()).as_posix()
        except ValueError as exc:  # pragma: no cover - guarded above
            raise AdoptionKitError(
                f"Adoption-kit override escapes its override root: {path}",
                path=path,
            ) from exc
        try:
            output[rel] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise AdoptionKitError(
                f"Could not read adoption-kit override {path}: {exc}",
                path=path,
            ) from exc
    return output


def _metadata_hashes(target: str, field: str) -> dict[str, tuple[str, ...]]:
    spec = _target(target)
    raw = _read_metadata(spec).get(field) or {}
    if not isinstance(raw, dict):
        return {}
    output: dict[str, tuple[str, ...]] = {}
    for root_rel, hashes in raw.items():
        if not isinstance(root_rel, str) or not isinstance(hashes, list):
            continue
        safe_hashes = tuple(value for value in hashes if isinstance(value, str))
        if safe_hashes:
            output[f"{spec.target_root}/{root_rel}"] = safe_hashes
    return output


def _read_metadata(spec: KitTarget) -> dict[str, Any]:
    root = _bundled_target_root(spec)
    if root is None:
        return {}
    metadata = root / KIT_METADATA_FILENAME
    if not metadata.is_file():
        return {}
    try:
        raw = json.loads(metadata.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _render_template(text: str) -> str:
    context = {"shipgate_version": __version__}

    def replace(match: re.Match[str]) -> str:
        return context.get(match.group(1), match.group(0))

    return _TEMPLATE_RE.sub(replace, text)


def _root_file_hashes(files: dict[str, str]) -> dict[str, str]:
    return {
        rel: hashlib.sha256(content.encode("utf-8")).hexdigest()
        for rel, content in sorted(files.items())
    }


def _ensure_under_workspace(path: Path, workspace: Path) -> None:
    try:
        path.relative_to(workspace)
    except ValueError as exc:
        raise AdoptionKitError(
            f"Adoption-kit path {path} resolves outside workspace {workspace}.",
            path=path,
        ) from exc


__all__ = [
    "AdoptionKitConfig",
    "AdoptionKitError",
    "DEFAULT_CONFIG_RELATIVE_PATH",
    "KIT_TARGETS",
    "SIDECAR_FILENAME",
    "RenderedAdoptionKit",
    "bootstrap_legacy_hashes",
    "build_sidecar",
    "first_symlink_in_chain",
    "load_adoption_kit_config",
    "parse_sidecar",
    "prior_render_hashes",
    "render_adoption_kit",
    "render_bundle_text",
    "root_relative_path",
    "sidecar_text",
]
