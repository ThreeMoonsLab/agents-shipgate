"""Transcript redaction.

Routes raw artifacts through :mod:`agents_shipgate.core.privacy` plus two
harness-specific patterns: env-var values loaded from ``.env.harness``, and
absolute paths under ``$HOME``. Writes redacted copies to a sibling
``redacted/`` directory; the scorecard and public CSV consume the redacted
form exclusively.

The contract from commit #94 is that *no* file the harness publishes — CSV
rows, scorecard JSON, or anything under ``redacted/`` — contains a secret
that matches Shipgate's secret patterns. The redaction unit test
(``tests/harness/test_redaction.py``) enforces that contract.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from agents_shipgate.core.privacy import RedactionStats, redact_text


@dataclass(frozen=True)
class RedactionConfig:
    extra_literal_secrets: tuple[str, ...] = ()
    """Literal strings to redact verbatim (e.g. env-var values from ``.env.harness``).

    These are matched case-sensitively. Keep the list short — large literal
    sets slow text rewrites; for high-volume patterns add a regex to
    :mod:`agents_shipgate.core.privacy` instead.
    """
    home_dir: str = ""
    """Absolute path prefix to rewrite to ``$HOME``. Empty disables it."""


def load_env_harness(env_file: Path | None) -> tuple[str, ...]:
    """Extract values from a ``.env.harness`` file for literal redaction.

    Returns an empty tuple if the file does not exist; safe to call
    unconditionally.
    """
    if env_file is None or not env_file.is_file():
        return ()
    out: list[str] = []
    for line in env_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        _, _, value = stripped.partition("=")
        value = value.strip().strip("\"'")
        if value and len(value) >= 8:
            out.append(value)
    return tuple(out)


def redact_file(
    source: Path,
    destination: Path,
    *,
    config: RedactionConfig,
    stats: RedactionStats | None = None,
) -> None:
    """Redact one file from ``source`` into ``destination``.

    Treats every file as text; binary files are not expected in the artifact
    set this redactor consumes (transcripts, diffs, summaries).
    """
    text = source.read_text(encoding="utf-8", errors="replace")
    redacted = redact_string(text, config=config, stats=stats, path=str(source.name))
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(redacted, encoding="utf-8")


def redact_string(
    text: str,
    *,
    config: RedactionConfig,
    stats: RedactionStats | None = None,
    path: str = "$",
) -> str:
    """Apply core privacy redaction plus harness-specific extras."""
    out = redact_text(text, stats=stats, path=path) or ""
    for secret in config.extra_literal_secrets:
        if not secret:
            continue
        out = out.replace(secret, "[REDACTED:env_secret]")
    if config.home_dir:
        # Rewrite both the raw path and a Path-resolved variant.
        out = out.replace(config.home_dir, "$HOME")
    return out


def default_config() -> RedactionConfig:
    """Build the default redaction config from process environment."""
    env_file = Path(".env.harness")
    return RedactionConfig(
        extra_literal_secrets=load_env_harness(env_file),
        home_dir=os.path.expanduser("~"),
    )


def redact_tree(
    raw_dir: Path,
    redacted_dir: Path,
    *,
    config: RedactionConfig | None = None,
    stats: RedactionStats | None = None,
) -> RedactionStats:
    """Mirror ``raw_dir`` into ``redacted_dir``, redacting every file.

    Returns the stats so a caller can record total redaction count on the
    scorecard. Safe to call repeatedly — overwrites in place.
    """
    cfg = config or default_config()
    s = stats or RedactionStats()
    for path in sorted(raw_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(raw_dir)
        redact_file(path, redacted_dir / rel, config=cfg, stats=s)
    return s


__all__ = [
    "RedactionConfig",
    "default_config",
    "load_env_harness",
    "redact_file",
    "redact_string",
    "redact_tree",
]
