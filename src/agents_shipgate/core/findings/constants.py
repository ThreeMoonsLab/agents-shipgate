from __future__ import annotations

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
FINGERPRINT_EXCLUDED_EVIDENCE_KEYS = {
    # Which control pack asked for the missing control (#410 §F). The
    # finding is "this action lacks an audit log" whichever pack obliged
    # it, and a baseline recorded under one pack has to keep matching the
    # same finding under another — a fingerprint is identity, not context.
    "control_pack",
    # …and which effects it asked about (#410 §F review). Same rule: the
    # finding is "this action lacks an audit log" whichever effects obliged
    # it, and stamping the reason must not move the identity.
    "control_effects",
    "default_severity",
    "observed",
    "source_provenance",
}
