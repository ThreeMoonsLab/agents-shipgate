from __future__ import annotations

from pathlib import Path

from agents_shipgate.config.loader import load_manifest_with_positions
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.schemas.common import parse_severity

from .models import _ResolvedManifest
from .output_helpers import PACKET_FORMAT_NAMES


def _prepare_scan(
    *,
    config_path: Path,
    ci_mode: str | None,
    fail_on: list[str] | None,
    output_dir: Path | None,
    formats: list[str] | None,
    packet_enabled: bool | None,
    packet_formats: list[str] | None,
    baseline_mode: str,
) -> _ResolvedManifest:
    """Phase 1: load manifest with positions; apply CLI overrides.

    CLI overrides take precedence over manifest values. Raises
    ``ConfigError`` (exit 2) for invalid packet formats or unsupported
    baseline modes — both fail before any source loading happens.
    """
    raw_manifest, manifest_positions = load_manifest_with_positions(config_path)
    manifest = raw_manifest.model_copy(deep=True)
    if ci_mode:
        manifest.ci.mode = ci_mode
    if fail_on is not None:
        manifest.ci.fail_on = [parse_severity(item) for item in fail_on]
    if output_dir:
        manifest.output.directory = str(output_dir)
    if formats:
        manifest.output.formats = formats
    if packet_enabled is not None:
        manifest.output.packet.enabled = packet_enabled
    if packet_formats is not None:
        invalid = [f for f in packet_formats if f not in PACKET_FORMAT_NAMES]
        if invalid:
            raise ConfigError(
                "--packet-format values must be one of "
                f"{sorted(PACKET_FORMAT_NAMES)}; got {invalid}"
            )
        manifest.output.packet.formats = packet_formats
    if baseline_mode != "new-findings":
        raise ConfigError("--baseline-mode supports only new-findings")
    return _ResolvedManifest(
        manifest=manifest,
        manifest_positions=manifest_positions,
        base_dir=config_path.resolve().parent,
    )
