"""Which published findings are lexical advisories (#1055, Option A; #1035).

A check marked ``advisory_when_policy_ineligible`` in the catalog publishes an
instance whose only evidence is a keyword or pattern as a visible,
non-blocking finding instead of an evidence gap (``cli/scan/decision.py``).
Such a finding never gates, so a review surface that counts coverage — the
packet's evidence matrix, ``capability_facts[].control_status``, the
misalignments and the scenarios they suggest — shows it as advisory and does
not let it change a coverage status. The release decision already excludes
it through ``support.policy_eligible: false``.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from agents_shipgate.checks._metadata_loader import load_check_metadata

#: How a review surface labels a lexical advisory.
ADVISORY_LABEL = "Advisory (lexical evidence, does not gate)"


@lru_cache(maxsize=1)
def advisory_check_ids() -> frozenset[str]:
    """Built-in check ids whose lexical instances are published as advisories."""

    return frozenset(
        metadata.id
        for metadata in load_check_metadata()
        if metadata.advisory_when_policy_ineligible
    )


def is_lexical_advisory(check_id: str | None, policy_eligible: Any) -> bool:
    """Whether a published finding is a lexical advisory.

    ``policy_eligible`` is the finding's ``support.policy_eligible`` (``None``
    when it has no support, which is never an advisory). Only an ineligible
    instance of an advisory check was retained as one; a policy-eligible
    instance of the same check gates as before.
    """

    return policy_eligible is False and check_id in advisory_check_ids()
