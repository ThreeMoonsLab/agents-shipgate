"""JSON-Schema export for the adoption-harness ``ScorecardV1`` model.

The runtime model lives at :mod:`harness.adoption.scorer.schema`; this module
re-exports its JSON Schema so downstream consumers can validate scorecard
artifacts without importing the harness package (which is local-only and not
shipped in the agents-shipgate wheel).

This is import-free at module load time and tolerant of the harness package
being absent (it is excluded from the wheel). The schema is rebuilt lazily on
first call.
"""
from __future__ import annotations

from typing import Any


def adoption_scorecard_json_schema() -> dict[str, Any]:
    """Return the JSON Schema for ``ScorecardV1``.

    Imports the harness model lazily so this module can be loaded even when
    the harness package is not on the path (e.g. in a wheel install).
    """
    from harness.adoption.scorer.schema import ScorecardV1

    schema = ScorecardV1.model_json_schema()
    schema.setdefault("title", "AdoptionHarnessScorecardV1")
    return schema


__all__ = ["adoption_scorecard_json_schema"]
