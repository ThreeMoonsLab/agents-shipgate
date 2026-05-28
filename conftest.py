"""Root pytest conftest.

Ensures the local repo root (for ``harness.*``) and ``src/`` (for
``agents_shipgate.*``) are first on ``sys.path`` so tests resolve modules
from THIS worktree rather than any editable install from another worktree.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
SRC_DIR = REPO_ROOT / "src"

for path in (REPO_ROOT, SRC_DIR):
    string = str(path)
    if string not in sys.path:
        sys.path.insert(0, string)

existing_pythonpath = os.environ.get("PYTHONPATH", "")
parts = [str(REPO_ROOT), str(SRC_DIR)]
if existing_pythonpath:
    parts.append(existing_pythonpath)
# Subprocess smoke tests must import this worktree, not another editable install.
os.environ["PYTHONPATH"] = os.pathsep.join(parts)
