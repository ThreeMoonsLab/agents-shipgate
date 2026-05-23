from __future__ import annotations

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
FINGERPRINT_EXCLUDED_EVIDENCE_KEYS = {
    "default_severity",
    "observed",
    "source_provenance",
}
