"""Regenerate the JSON-Schema and check-catalog files under docs/.

Run from the repo root:

    python scripts/generate_schemas.py

Writes:
- docs/manifest-v0.1.json       (from agents_shipgate.config.schema)
- docs/checks.json              (from agents-shipgate list-checks --json)
- docs/report-schema.v0.6.json  (from agents_shipgate.core.models.ReadinessReport)

CI calls this script and asserts the working tree is clean afterward, so
out-of-date generated files fail the build — drift protection for any
field changes on Finding (e.g., patches) or ReadinessReport
(e.g., manifest_dir).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS = REPO_ROOT / "docs"
SRC = REPO_ROOT / "src"

# Allow `python scripts/generate_schemas.py` from a checkout without install.
sys.path.insert(0, str(SRC))


def write_manifest_schema() -> None:
    from agents_shipgate.config.schema import AgentsShipgateManifest

    schema = AgentsShipgateManifest.model_json_schema()
    schema["$id"] = (
        "https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/"
        "main/docs/manifest-v0.1.json"
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "Agents Shipgate Manifest v0.1"
    schema["description"] = (
        "JSON Schema for shipgate.yaml. Generated from "
        "agents_shipgate.config.schema.AgentsShipgateManifest. Do not edit by hand."
    )
    target = DOCS / "manifest-v0.1.json"
    target.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {target.relative_to(REPO_ROOT)}")


def write_report_schema() -> None:
    """Generate docs/report-schema.v0.<minor>.json from the Pydantic
    ReadinessReport model.

    The minor version is derived from ``ReadinessReport.report_schema_version``
    so a schema bump is one-step: change the default in models.py and rerun
    this script. CI's clean-tree assertion catches any field drift.
    """
    from agents_shipgate.core.models import ReadinessReport

    schema = ReadinessReport.model_json_schema()
    minor = ReadinessReport.model_fields["report_schema_version"].default
    title = f"Agents Shipgate Readiness Report v{minor}"
    schema_id = (
        "https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/"
        f"main/docs/report-schema.v{minor}.json"
    )
    schema["$id"] = schema_id
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = title
    schema["description"] = (
        "JSON Schema for the Agents Shipgate Tool-Use Readiness Report. "
        "Generated from agents_shipgate.core.models.ReadinessReport. "
        "Do not edit by hand."
    )
    target = DOCS / f"report-schema.v{minor}.json"
    target.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {target.relative_to(REPO_ROOT)}")


def write_checks_catalog() -> None:
    from agents_shipgate.checks.registry import check_catalog

    payload = {
        "$id": (
            "https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/"
            "main/docs/checks.json"
        ),
        "title": "Agents Shipgate Check Catalog",
        "description": (
            "Machine-readable catalog of built-in checks. Generated from "
            "agents_shipgate.checks.registry.check_catalog(). Do not edit by hand."
        ),
        "checks": [check.model_dump(mode="json") for check in check_catalog()],
    }
    target = DOCS / "checks.json"
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {target.relative_to(REPO_ROOT)}")


def main() -> int:
    DOCS.mkdir(parents=True, exist_ok=True)
    write_manifest_schema()
    write_checks_catalog()
    write_report_schema()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
