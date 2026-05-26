"""Render the repo-scoped Codex skill bundle from packaged kit files."""

from __future__ import annotations

from agents_shipgate.cli.discovery.agent_instructions import adoption_kit as _adoption_kit

TARGET = "codex-skill"


def render_files(config: _adoption_kit.AdoptionKitConfig | None = None) -> dict[str, str]:
    """Return relative file path -> UTF-8 text for the Codex skill bundle."""

    return _adoption_kit.render_adoption_kit(TARGET, config).files


def render_bundle_text(config: _adoption_kit.AdoptionKitConfig | None = None) -> str:
    """Return a human-readable dry-run rendering of the full bundle."""

    return _adoption_kit.render_bundle_text(TARGET, config)


PRIOR_RENDER_SHA256: dict[str, tuple[str, ...]] = _adoption_kit.prior_render_hashes(TARGET)
