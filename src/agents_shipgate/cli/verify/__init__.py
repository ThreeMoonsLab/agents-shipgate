"""Verify command orchestration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .command import verify
    from .orchestrator import run_verify

__all__ = ["run_verify", "verify"]


def __getattr__(name: str) -> Any:
    # Resolved on first use. `diff` reads `cli.verify.git`, and importing this
    # package must not import the verify command and orchestrator with it:
    # that cost the Stop hook's `diff` half a second (#661).
    if name == "verify":
        from .command import verify

        return verify
    if name == "run_verify":
        from .orchestrator import run_verify

        return run_verify
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
