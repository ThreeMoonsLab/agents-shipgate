from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from agents_shipgate.schemas.manifest._common import STRICT_MODEL_CONFIG

BaselineIntegrityMode = Literal["off", "warn", "strict"]


class BaselineConfig(BaseModel):
    """Manifest knob governing v0.5 baseline integrity checks.

    ``integrity_mode`` decides what happens when ``scan`` (with
    ``--baseline``) detects an integrity issue:

    - ``off``: no integrity checks run (back-compat escape hatch for
      repos that have not migrated to v0.5 baselines yet).
    - ``warn`` (default in v0.17): integrity findings are emitted but
      ``blocks_release`` is false; release decision is unaffected.
    - ``strict``: ``SHIP-BASELINE-INTEGRITY-MISMATCH`` findings get
      ``blocks_release=true`` and ``agents-shipgate baseline verify``
      exits with code 6 on the same condition. Recommended target for
      v0.18.

    ``audit_log`` overrides the default audit log path. Relative paths
    resolve against the baseline file's directory so ``baseline save``
    and ``scan --baseline`` use the same file. Usually left at its default.
    """

    model_config = STRICT_MODEL_CONFIG

    integrity_mode: BaselineIntegrityMode = "warn"
    audit_log: str | None = None
