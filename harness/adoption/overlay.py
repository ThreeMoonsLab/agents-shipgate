"""Variant overlay renderer.

Each ``benchmark/setup-variants/<variant>/`` directory carries an
``overlay.yaml`` declaring:

* a ``files`` list of ``{source: <template name>, destination: <path>}`` pairs
  describing what each ``.template`` file becomes inside a target workspace;
* a ``required_placeholders`` list naming the placeholders the renderer must
  resolve before writing;
* optionally, a ``renderers`` list for generated overlays whose canonical
  content lives in package renderers rather than duplicated benchmark templates.

The renderer reads each template, substitutes ``{{PLACEHOLDER}}`` tokens from
the per-cell context, writes the destination file, then scans template-rendered
files for leftover ``{{`` or ``CHANGE_ME`` literals — if any are found the cell
fails before the driver runs, so a misconfigured overlay can never reach an
agent. Generated overlays are canonical package output and may contain those
tokens in explanatory prose.

The negative overlay (``60-docs-only-negative``) composes by adding a small
docs-only diff on top of the primary variant. Overlays compose via repeated
calls; they do not need to be aware of each other.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Z][A-Z0-9_]*)\s*\}\}")
LEFTOVER_MARKERS = ("{{", "CHANGE_ME")


class OverlayError(RuntimeError):
    """Raised when overlay rendering or validation fails.

    The cell that triggered the error must fail before the driver runs — a
    workspace with unresolved placeholders or missing destination files is
    not a valid input for any agent.
    """


@dataclass(frozen=True)
class OverlayFile:
    source: str
    destination: str
    append: bool = False
    """When True, append the rendered template to the destination instead of
    overwriting it. Used by the docs-only-negative overlay."""


@dataclass(frozen=True)
class OverlaySpec:
    variant: str
    files: list[OverlayFile]
    renderers: list[str] = field(default_factory=list)
    required_placeholders: list[str] = field(default_factory=list)


def load_overlay_spec(variant_dir: Path) -> OverlaySpec:
    """Load and validate ``overlay.yaml`` for one variant."""
    overlay_yaml = variant_dir / "overlay.yaml"
    if not overlay_yaml.is_file():
        raise OverlayError(
            f"Missing overlay.yaml in {variant_dir}. "
            "Every variant directory must declare its file map."
        )
    raw = yaml.safe_load(overlay_yaml.read_text(encoding="utf-8")) or {}
    files_raw = raw.get("files") or []
    files = [
        OverlayFile(
            source=entry["source"],
            destination=entry["destination"],
            append=bool(entry.get("append", False)),
        )
        for entry in files_raw
    ]
    renderers = list(raw.get("renderers") or [])
    required = list(raw.get("required_placeholders") or [])
    return OverlaySpec(
        variant=variant_dir.name,
        files=files,
        renderers=renderers,
        required_placeholders=required,
    )


def apply_overlay(
    *,
    variant_dir: Path,
    workspace_root: Path,
    placeholders: dict[str, str],
) -> None:
    """Apply one overlay onto a workspace.

    ``placeholders`` must contain at least every name in
    ``required_placeholders``. Anything extra is ignored, so the same context
    can drive multiple overlays without filtering.
    """
    spec = load_overlay_spec(variant_dir)
    missing = [p for p in spec.required_placeholders if p not in placeholders]
    if missing:
        raise OverlayError(
            f"Variant {spec.variant!r} requires placeholders {sorted(missing)!r} "
            "but they were not provided. Check ArchetypeContext."
        )

    template_written: list[Path] = []
    for renderer_name in spec.renderers:
        for rel_path, rendered in _render_generated_files(renderer_name).items():
            dest = workspace_root / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(rendered, encoding="utf-8")

    for entry in spec.files:
        src = variant_dir / entry.source
        if not src.is_file():
            raise OverlayError(
                f"Variant {spec.variant!r}: template source {src} does not exist."
            )
        rendered = _substitute(src.read_text(encoding="utf-8"), placeholders)
        dest = workspace_root / entry.destination
        dest.parent.mkdir(parents=True, exist_ok=True)
        if entry.append and dest.is_file():
            existing = dest.read_text(encoding="utf-8")
            dest.write_text(existing.rstrip() + "\n\n" + rendered, encoding="utf-8")
        else:
            dest.write_text(rendered, encoding="utf-8")
        template_written.append(dest)

    _validate_written(spec, template_written)


def apply_overlays(
    *,
    variant_dirs: Iterable[Path],
    workspace_root: Path,
    placeholders: dict[str, str],
) -> None:
    """Apply a sequence of overlays in order. Used to layer the negative
    overlay on top of a primary variant."""
    for vd in variant_dirs:
        apply_overlay(
            variant_dir=vd,
            workspace_root=workspace_root,
            placeholders=placeholders,
        )


def _substitute(text: str, placeholders: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in placeholders:
            return placeholders[name]
        return match.group(0)

    return PLACEHOLDER_RE.sub(replace, text)


def _render_generated_files(renderer_name: str) -> dict[str, str]:
    if renderer_name == "codex-skill":
        from agents_shipgate.cli.discovery.agent_instructions.renderers import (
            render_codex_skill_files,
        )

        return render_codex_skill_files()
    raise OverlayError(
        f"Unknown overlay renderer {renderer_name!r}. "
        "Supported renderers: codex-skill."
    )


def _validate_written(spec: OverlaySpec, paths: list[Path]) -> None:
    failures: list[str] = []
    for path in paths:
        content = path.read_text(encoding="utf-8")
        for marker in LEFTOVER_MARKERS:
            if marker in content:
                failures.append(f"{path} contains leftover marker {marker!r}")
    if failures:
        raise OverlayError(
            f"Variant {spec.variant!r}: post-render validation failed:\n  "
            + "\n  ".join(failures)
            + "\nA workspace with unresolved placeholders is not a valid agent input."
        )


__all__ = [
    "OverlayError",
    "OverlayFile",
    "OverlaySpec",
    "apply_overlay",
    "apply_overlays",
    "load_overlay_spec",
]
