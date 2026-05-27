"""Scan command — public surface.

This package implements ``agents-shipgate scan`` as nine sequential
phase helpers under ``cli/scan/`` (see :mod:`orchestrator` for the
composition). Each phase helper lives in its own module and is
intentionally private — the underscore prefix is the contract.

External callers (CLI registration, tests, downstream tooling) should
import only the three names re-exported here:

- :data:`PACKET_FORMAT_NAMES` — packet output format set.
- :func:`run_scan` — entry-point orchestrator. Public signature,
  exit-code contract, and ``_run_id`` hash inputs are stable.
- :func:`inspect_sources` — read-only adapter inspection used by
  ``init --workspace . --json`` and the per-framework smoke tests.

Phase helpers (``_prepare_scan``, ``_load_inputs``,
``_run_checks_and_decide``, …) and per-phase utilities
(``_run_id``, ``_load_sources``, ``_flatten_and_deduplicate_tools``,
``_build_agent``, ``_resolve_audit_log_path``, …) are not part of the
public surface. Test code that exercises them directly should import
from the owning submodule, e.g. ``agents_shipgate.cli.scan.run_identity``
or ``agents_shipgate.cli.scan.source_loading``.
"""

from __future__ import annotations

from .inspect import inspect_sources
from .orchestrator import run_scan
from .output_helpers import PACKET_FORMAT_NAMES

__all__ = [
    "PACKET_FORMAT_NAMES",
    "inspect_sources",
    "run_scan",
]
