"""Shared harness-test fixtures.

The CLI helper ``_relative_artifacts_path`` deliberately raises when an
artifacts_dir lives outside the repo so the published CSV never leaks an
absolute host path. Tests that exercise scorecard creation therefore need a
tmp directory UNDER the repo's gitignored ``.agents-private/`` tree. This
fixture provides one and cleans up.
"""
from __future__ import annotations

import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from harness.adoption import cli as cli_mod


@pytest.fixture()
def repo_tmp_path() -> Iterator[Path]:
    """Yield a unique tmp directory under ``<repo>/.agents-private/test-runs/``.

    ``.agents-private/`` is already gitignored, so leftovers from a crashed
    test run don't pollute git. We clean up at teardown regardless.
    """
    root = cli_mod._repo_root() / ".agents-private" / "test-runs" / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)
