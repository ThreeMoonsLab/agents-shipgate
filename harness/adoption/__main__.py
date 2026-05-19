"""``python -m harness.adoption`` entry point.

Bootstraps ``sys.path`` so the colocated ``src/`` (agents_shipgate) wins over
any editable install from a sibling worktree before importing the CLI.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / "src"):
    _s = str(_path)
    if _s not in sys.path:
        sys.path.insert(0, _s)

from harness.adoption.cli import app  # noqa: E402

if __name__ == "__main__":
    app()
