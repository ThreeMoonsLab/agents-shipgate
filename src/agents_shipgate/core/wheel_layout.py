"""The repository files a wheel copies into the package, and where they are here.

A built wheel carries ``agents_shipgate/_meta/``, ``_fixtures/`` and
``_adoption_kits/``, which Hatch copies from the repository at build time
(``[tool.hatch.build.targets.wheel]`` in ``pyproject.toml``). A source
checkout, an editable install and the ``./shipgate`` launcher import
``src/agents_shipgate`` in place and have none of them, so each such file is
still at its repository path.

:data:`WHEEL_LAYOUT` is that mapping, kept once. ``verification_identity``
projects it so a checkout and the wheel built from it have one engine-content
identity, and :func:`packaged_path` reads through it so a checkout loads the
packaged files a wheel loads. ``tests/test_packaged_default_policies.py``
checks it against ``pyproject.toml``.
"""

from __future__ import annotations

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]

#: ``(repository path, path inside the agents_shipgate package)`` for the
#: ``samples`` source and every ``force-include`` entry of the wheel target.
WHEEL_LAYOUT: tuple[tuple[str, str], ...] = (
    ("samples", "_fixtures"),
    ("AGENTS.md", "_meta/AGENTS.md"),
    ("STABILITY.md", "_meta/STABILITY.md"),
    ("docs/triggers.json", "_meta/triggers.json"),
    (".claude/commands/shipgate.md", "_meta/claude-command/shipgate.md"),
    ("docs/checks", "_meta/checks"),
    (
        "policies/codex-boundary.shipgate.yaml",
        "_meta/policies/codex-boundary.shipgate.yaml",
    ),
    (
        "policies/mcp-permissions.shipgate.yaml",
        "_meta/policies/mcp-permissions.shipgate.yaml",
    ),
    (
        "policies/host-boundary.shipgate.yaml",
        "_meta/policies/host-boundary.shipgate.yaml",
    ),
    (
        "policies/templates/agent-boundary-default.shipgate.yaml",
        "_meta/policies/agent-boundary.shipgate.yaml",
    ),
    ("policies/templates", "_meta/policies/templates"),
    ("adoption-kits", "_adoption_kits"),
)


def is_source_checkout(package_root: Path = PACKAGE_ROOT) -> bool:
    """Whether the package runs in place from a repository, not from a wheel.

    Every wheel carries ``_meta/``; a checkout never does.
    """

    return not (package_root / "_meta").is_dir()


def packaged_path(packaged: str, *, package_root: Path = PACKAGE_ROOT) -> Path:
    """Where the file a wheel ships at ``agents_shipgate/<packaged>`` is here.

    An installed wheel answers with that path. A source checkout answers with
    the repository file the wheel copies there, so both read the same bytes. A
    path the layout does not map stays under the package either way. Whether
    the file exists is the caller's question.
    """

    if not is_source_checkout(package_root):
        return package_root / packaged
    repository = package_root.parents[1]
    for source, target in WHEEL_LAYOUT:
        if packaged == target:
            return repository / source
        if packaged.startswith(f"{target}/"):
            return repository / source / packaged.removeprefix(f"{target}/")
    return package_root / packaged


__all__ = ["PACKAGE_ROOT", "WHEEL_LAYOUT", "is_source_checkout", "packaged_path"]
