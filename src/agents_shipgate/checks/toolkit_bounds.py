"""Flag a dynamically-loaded agent toolkit mounted without a scope bound.

The static extractor cannot enumerate a runtime toolkit factory
(``*toolkit.get_tools()``); what it *can* parse is the configuration
allowlist passed to the toolkit constructor, captured as a
:class:`~agents_shipgate.core.domain.ToolkitScopeBound`. When that allowlist
is absent the *full* toolkit surface is mounted — e.g. ``stripe_agent_toolkit``
exposes refund / cancel / dispute — and today that passes silently on a plain
scan: the only existing signal, ``SHIP-VERIFY-CAPABILITY-SCOPE-BROADENED``, is
verify-tier and fires only on a base→head *weakening* of a known bound.

This check names the unbounded grant on any scan, so it routes to human review
instead of disappearing into ``insufficient_evidence`` (the real-world blind
spot the 2026-06-01 Stripe pilot and the W24/W25 mining both hit). It is the
base-scan companion to the verify-tier broadening check, not a duplicate: that
one needs a base bound to weaken from; this one fires on the head's unbounded
state directly.
"""

from __future__ import annotations

from agents_shipgate.checks.base import agent_finding
from agents_shipgate.core.context import ScanContext
from agents_shipgate.schemas.report import Finding

CHECK_ID = "SHIP-SCOPE-TOOLKIT-UNBOUNDED"


def run(context: ScanContext) -> list[Finding]:
    findings: list[Finding] = []
    for bound in context.toolkit_bounds:
        if bound.bounded:
            # An explicit resource:verb allowlist was declared — least
            # privilege, exactly what we want. Nothing to flag.
            continue
        location = bound.source_ref or ""
        if bound.source_line is not None:
            location = f"{location}:{bound.source_line}" if location else str(bound.source_line)
        findings.append(
            agent_finding(
                check_id=CHECK_ID,
                title=f"{bound.provider} toolkit mounted without a scope bound",
                severity="high",
                category="scope",
                evidence={
                    "provider": bound.provider,
                    "constructor": bound.constructor,
                    "binding": bound.binding or "",
                    "source_ref": location,
                },
                confidence="high",
                recommendation=(
                    f"Pass an explicit `configuration` allowlist (resource:verb "
                    f"actions) to the {bound.constructor} constructor so the agent "
                    "mounts only the tools it needs, not the full toolkit surface."
                ),
                context=context,
                provenance_kind="ast_extraction",
            )
        )
    return findings
