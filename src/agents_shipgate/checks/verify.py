"""Verify category — trust-root protection (the cheap reward-hacking guard).

``SHIP-VERIFY-TRUST-ROOT-TOUCHED`` is Tier A of trust-root protection
(docs/engineering/ai-coding-workflow-verifier.md §5.1): pure path/glob
classification of the PR's changed files against the release gate's
trust spine. It is fully deterministic, needs no base scan, and fires
only when a :class:`VerificationContext` is present — plain ``scan``
(``context.verification is None``) emits nothing.

Reward hacking is the coding-agent threat model: an optimizer told to
"make CI green" may edit the gate instead of fixing the readiness issue.
Touching a trust root requires at least human review, so the finding is
emitted at ``medium`` severity and routes to ``release_decision``'s
review tier by default. Strict CI / severity overrides can escalate it
through the existing decision machinery — it stays one ordinary
``Finding`` through the one decision engine; it is never a second
verdict.
"""

from __future__ import annotations

from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.globbing import glob_match

# The trust-root table lives in ``core.trust_roots`` so the local boundary
# evaluator can classify paths from the identical data without an import cycle
# (``checks.verify`` -> ``core.context`` -> ``core.agent_boundary``).  These
# re-exports keep every historical ``checks.verify`` import site working.
from agents_shipgate.core.trust_roots import (  # noqa: F401
    _FORBIDDEN_EDIT_CLASSES,
    _LEGACY_TRUST_ROOT_SURFACES,
    PROTECTED_FILE_EDITS,
    TRUST_ROOT_SURFACES,
    is_configured_manifest,
)
from agents_shipgate.schemas.common import (
    SourceReference,
    parse_confidence,
    parse_severity,
)
from agents_shipgate.schemas.report import Finding

CHECK_ID = "SHIP-VERIFY-TRUST-ROOT-TOUCHED"


def run(context: ScanContext) -> list[Finding]:
    verification = context.verification
    if verification is None:
        return []
    findings: list[Finding] = []
    seen: set[str] = set()
    for raw in verification.changed_files:
        path = raw.replace("\\", "/").strip()
        if not path or path in seen:
            continue
        seen.add(path)
        classification = _classify(path) or _configured_manifest(context, path)
        if classification is None:
            continue
        trust_root_class, matched_glob = classification
        findings.append(
            _finding(context, path, trust_root_class, matched_glob)
        )
    return findings


def _classify(path: str) -> tuple[str, str] | None:
    for trust_root_class, pattern in TRUST_ROOT_SURFACES:
        if glob_match(pattern, path):
            return trust_root_class, pattern
    return None


def _configured_manifest(context: ScanContext, path: str) -> tuple[str, str] | None:
    """Classify the manifest this run was actually pointed at.

    Whatever a run loaded as its gate is a manifest trust root, even when it is
    not called ``shipgate.yaml``.
    """

    config = getattr(context, "config_path", None)
    if not is_configured_manifest(config, path):
        return None
    # Deliberately the changed path, not ``config_path``: a committed-head run
    # loads its manifest from a freshly named ``agents-shipgate-verify-head-*``
    # archive, and this value lands in finding evidence, which is hashed into
    # the fingerprint. Returning the resolved config path made two identical
    # runs produce different fingerprints and report run ids.
    return "manifest", path


def _finding(
    context: ScanContext,
    path: str,
    trust_root_class: str,
    matched_glob: str,
) -> Finding:
    return Finding(
        check_id=CHECK_ID,
        title=f"Release trust root touched: {path}",
        severity=parse_severity("medium"),
        category="verify",
        agent_id=context.agent.id,
        evidence={
            "changed_file": path,
            "trust_root_class": trust_root_class,
            "matched_glob": matched_glob,
        },
        confidence=parse_confidence("high"),
        provenance_kind="static_declaration",
        source=SourceReference(type="changed_file", path=path),
        recommendation=(
            "This PR changes a file that defines the release gate's trust "
            "spine. A human must review the change before merge; do not "
            "weaken the gate to make CI pass."
        ),
    )
