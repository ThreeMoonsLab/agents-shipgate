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

from agents_shipgate.core.context import ScanContext
from agents_shipgate.schemas.common import SourceReference, parse_confidence, parse_severity
from agents_shipgate.schemas.report import Finding

CHECK_ID = "SHIP-SCOPE-TOOLKIT-UNBOUNDED"


def run(context: ScanContext) -> list[Finding]:
    findings: list[Finding] = []
    for bound in context.toolkit_bounds:
        if bound.bounded:
            # An explicit resource:verb allowlist was declared — least
            # privilege, exactly what we want. Nothing to flag.
            continue
        findings.append(
            Finding(
                check_id=CHECK_ID,
                title=f"{bound.provider} toolkit mounted without a scope bound",
                severity=parse_severity("high"),
                category="scope",
                agent_id=context.agent.id,
                # Evidence is fingerprinted (core/findings/identity), so it
                # stays stable across harmless line moves: the file is here,
                # the LINE is not. The precise constructor location lives in
                # `source` (not fingerprinted) so SARIF / report point at the
                # constructor itself, not the manifest.
                evidence={
                    "provider": bound.provider,
                    "constructor": bound.constructor,
                    "binding": bound.binding or "",
                    "source_ref": bound.source_ref or "",
                },
                confidence=parse_confidence("high"),
                provenance_kind="ast_extraction",
                source=SourceReference(
                    type="agent_toolkit",
                    ref=bound.source_ref,
                    path=bound.source_ref,
                    start_line=bound.source_line,
                ),
                recommendation=(
                    f"Pass an explicit `configuration` allowlist (resource:verb "
                    f"actions) to the {bound.constructor} constructor so the agent "
                    "mounts only the tools it needs, not the full toolkit surface."
                ),
            )
        )
    return findings
