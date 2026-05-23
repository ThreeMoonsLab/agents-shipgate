"""Render the repo-scoped Claude Code skill bundle from packaged kit files."""

from __future__ import annotations

from agents_shipgate.cli.discovery.agent_instructions.adoption_kit import (
    AdoptionKitConfig,
    prior_render_hashes,
    render_adoption_kit,
)
from agents_shipgate.cli.discovery.agent_instructions.adoption_kit import (
    render_bundle_text as _render_bundle_text,
)

TARGET = "claude-code-skill"


def render_files(config: AdoptionKitConfig | None = None) -> dict[str, str]:
    """Return relative file path -> UTF-8 text for the Claude Code skill bundle."""

    return render_adoption_kit(TARGET, config).files


def render_bundle_text(config: AdoptionKitConfig | None = None) -> str:
    """Return a human-readable dry-run rendering of the full bundle."""

    return _render_bundle_text(TARGET, config)


PRIOR_RENDER_SHA256: dict[str, tuple[str, ...]] = prior_render_hashes(TARGET)
