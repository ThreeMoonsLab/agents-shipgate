from __future__ import annotations

from agents_shipgate.checks.base import agent_finding, tool_finding
from agents_shipgate.core.check_ids import projection_check_ids
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.findings.remediation import BINDING_NOT_ESTABLISHED
from agents_shipgate.core.heuristics import is_broad_scope
from agents_shipgate.core.manifest_protection import (
    CODEOWNERS_LOCATIONS,
    manifest_protection,
)
from agents_shipgate.core.risk_hints import is_policy_eligible_high_risk_tool, risk_tags
from agents_shipgate.core.tool_identity import ToolSelectorIndex
from agents_shipgate.schemas.manifest import PolicyToolEntry, SuppressionConfig


def run(context: ScanContext, *, known_check_ids: set[str]) -> list:
    findings = []
    catalog = context.tool_catalog or context.tools
    selectors = ToolSelectorIndex.build(catalog)
    reachable_ids = {tool.id for tool in context.tools}
    tool_names = {tool.name for tool in context.tools}
    # ``context.tools`` is the proven-reachable subset; ``tool_catalog`` is
    # every extracted declaration. A name in the catalog but not reachable is
    # an unestablished binding, not a missing tool: the tool exists, and
    # deleting its approval, confirmation or idempotency policy would remove a
    # control from a real tool the moment its binding is established (#1035).
    catalog_names = {tool.name for tool in catalog} - tool_names
    findings.extend(
        _stale_suppressions(context, selectors, reachable_ids, known_check_ids)
    )
    findings.extend(_stale_policies(context, selectors, reachable_ids))
    findings.extend(_stale_overrides(context, tool_names, catalog_names))
    findings.extend(_missing_high_risk_owners(context))
    findings.extend(_unused_manifest_scopes(context))
    findings.extend(_unprotected_manifest(context))
    return findings


def _unprotected_manifest(context: ScanContext) -> list:
    """Nothing in this checkout requires a human to approve a manifest change.

    Every verdict rests on the manifest, so who may change it is part of what
    the verdict is worth. Attestation is the PR review of a protected file —
    not a separate ceremony — and CODEOWNERS is the half of that a checkout
    can prove (#410 §G).

    Two things keep it proportionate, and both follow from what it can
    actually establish.

    **It asks only where the manifest is load-bearing.** Who may change the
    gate matters once the gate is enforcing something; a repository still on
    ``ci.mode: advisory`` has not asked CI to fail on this verdict yet, and
    ``doctor`` already names manifest protection as one of the two steps from
    rung 2 to rung 3. Repeating it on every advisory scan would be noise at
    the one moment there is nothing to act on. The *declared* block is what
    is read, never the invocation's ``--ci-mode``: the question is what this
    repository enforces, not how one run was launched (#298).

    **It never moves a verdict.** Branch protection is the other half and it
    lives in repository settings no file here can read, so a repository with
    CODEOWNERS may still merge without review and one without it may protect
    the manifest another way — an organization-wide approval rule covers the
    manifest without naming it anywhere in the checkout. Deciding a verdict on
    the half that is visible would be exactly the pretending this finding
    exists to avoid, so it carries ``requires_human_review: False`` and stays
    guidance a reviewer reads.
    """

    declared_ci = context.declared_ci or context.manifest.ci
    if declared_ci.mode != "strict":
        return []
    protection = manifest_protection(context.config_path)
    if protection.covered:
        return []
    if protection.codeowners_path is None:
        where = "this repository has no CODEOWNERS file GitHub would read"
    elif protection.owners and not protection.self_owned:
        # Both halves matter and they want different fixes. A rule set that
        # owns the manifest but not itself describes a protection one edit
        # deep: the same pull request can delete the rule.
        where = (
            f"{protection.codeowners_path} covers it but assigns no owner to "
            "itself, so the rule can be removed in the same change"
        )
    else:
        where = f"{protection.codeowners_path} has no rule covering it"
    return [
        agent_finding(
            check_id="SHIP-TRUST-MANIFEST-UNPROTECTED",
            title="No CODEOWNERS rule covers the Agents Shipgate manifest",
            severity="low",
            category="manifest",
            evidence={
                "ci_mode": declared_ci.mode,
                "manifest_path": protection.manifest_path,
                "codeowners_path": protection.codeowners_path,
                "codeowners_self_owned": protection.self_owned,
                "searched": list(CODEOWNERS_LOCATIONS),
                # Named so the finding says what it did *not* establish. A
                # repository can require review without CODEOWNERS, and can
                # have CODEOWNERS without requiring review.
                "branch_protection": "not statically verifiable",
            },
            confidence="high",
            recommendation=(
                f"Add a CODEOWNERS rule for {protection.manifest_path} and require "
                "review on the branch it merges to, so changing what this gate "
                f"enforces takes a named human's approval — {where}."
            ),
            context=context,
            provenance_kind="static_declaration",
        )
    ]


def _unbound_recommendation(tool: str, entry: str) -> str:
    return (
        f"Keep this {entry}: {tool} is in the tool catalog, but the scan did not "
        "prove the agent reaches it, so the entry cannot be checked against it "
        "yet. Establish the binding (agent_bindings or tool_sources[].binding) "
        "instead of removing the entry."
    )


def _tool_selector_issue(
    entry: PolicyToolEntry | SuppressionConfig,
    selectors: ToolSelectorIndex,
    reachable_ids: set[str],
) -> str | None:
    # Resolve stable ids and source aliases the same way as policy controls.
    # A missed qualifier is not new grounds for deleting a legacy entry
    # whose name still exists. Other checks report unresolved selectors.
    matches = selectors.resolve(entry).matches or selectors.by_name.get(entry.tool or "", ())
    if any(tool.id in reachable_ids for tool in matches):
        return None
    return BINDING_NOT_ESTABLISHED if matches else "missing_tool"


def _stale_suppressions(
    context: ScanContext,
    selectors: ToolSelectorIndex,
    reachable_ids: set[str],
    known_check_ids: set[str],
) -> list:
    findings = []
    for suppression in context.manifest.checks.ignore:
        issues = []
        if suppression.check_id not in known_check_ids:
            issues.append("unknown_check_id")
        tool_label = suppression.tool or suppression.tool_id
        if tool_label:
            issue = _tool_selector_issue(suppression, selectors, reachable_ids)
            # ID-only suppressions never had an automatic missing-tool removal.
            if issue is not None and (suppression.tool or issue == BINDING_NOT_ESTABLISHED):
                issues.append(issue)
        if not issues:
            continue
        unbound_only = issues == [BINDING_NOT_ESTABLISHED]
        # A projection id is in the catalog but is never a finding, so it is
        # as unsuppressible as an unknown id; say which one it is (#1047).
        projection = (
            "unknown_check_id" in issues
            and suppression.check_id in projection_check_ids()
        )
        if unbound_only:
            title = (
                f"Suppression for {suppression.check_id} names {tool_label}, "
                "whose binding to the agent is not established"
            )
        elif projection:
            title = (
                f"Suppression for {suppression.check_id} names a projection ID, "
                "which labels a PR annotation or SARIF row and is never a finding"
            )
        else:
            title = f"Suppression for {suppression.check_id} no longer matches the manifest"
        findings.append(
            agent_finding(
                check_id="SHIP-MANIFEST-STALE-SUPPRESSION",
                title=title,
                severity="medium",
                category="manifest",
                evidence={
                    "check_id": suppression.check_id,
                    "tool": suppression.tool,
                    "issues": issues,
                    **({"tool_id": suppression.tool_id} if not suppression.tool and suppression.tool_id else {}),
                },
                confidence="high",
                recommendation=(
                    _unbound_recommendation(str(tool_label), "suppression")
                    if unbound_only
                    else "Remove stale suppressions or update them to match current check IDs and tool names."
                ),
                context=context,
                provenance_kind="static_declaration",
            )
        )
    return findings


def _stale_policies(
    context: ScanContext, selectors: ToolSelectorIndex, reachable_ids: set[str]
) -> list:
    findings = []
    policy_sets: list[tuple[str, list[PolicyToolEntry]]] = [
        ("approval", context.manifest.policies.require_approval_for_tools),
        ("confirmation", context.manifest.policies.require_confirmation_for_tools),
        ("idempotency", context.manifest.policies.require_idempotency_for_tools),
    ]
    for policy_name, entries in policy_sets:
        for entry in entries:
            issue = _tool_selector_issue(entry, selectors, reachable_ids)
            if issue is None:
                continue
            if issue == BINDING_NOT_ESTABLISHED:
                # Not stale, and never a removal: the tool exists. Deleting
                # this entry is what a high-confidence patch used to propose
                # for create_refund and terminate_account (#1035).
                findings.append(
                    agent_finding(
                        check_id="SHIP-MANIFEST-STALE-POLICY",
                        title=(
                            f"{policy_name} policy references {entry.tool}, whose "
                            "binding to the agent is not established"
                        ),
                        severity="medium",
                        category="manifest",
                        evidence={
                            "policy": policy_name,
                            "tool": entry.tool,
                            "issues": [BINDING_NOT_ESTABLISHED],
                        },
                        confidence="high",
                        recommendation=_unbound_recommendation(
                            entry.tool, f"{policy_name} policy entry"
                        ),
                        context=context,
                        provenance_kind="static_declaration",
                    )
                )
                continue
            findings.append(
                agent_finding(
                    check_id="SHIP-MANIFEST-STALE-POLICY",
                    title=f"{policy_name} policy references missing tool {entry.tool}",
                    severity="medium",
                    category="manifest",
                    evidence={"policy": policy_name, "tool": entry.tool},
                    confidence="high",
                    recommendation="Remove stale policy entries or update them to current tool names.",
                    context=context,
                    provenance_kind="static_declaration",
                )
            )
    return findings


def _stale_overrides(
    context: ScanContext, tool_names: set[str], catalog_names: set[str]
) -> list:
    findings = []
    for tool_name in context.manifest.risk_overrides.tools:
        if tool_name in tool_names:
            continue
        if tool_name in catalog_names:
            findings.append(
                agent_finding(
                    check_id="SHIP-MANIFEST-STALE-RISK-OVERRIDE",
                    title=(
                        f"Risk override references {tool_name}, whose binding to "
                        "the agent is not established"
                    ),
                    severity="medium",
                    category="manifest",
                    evidence={"tool": tool_name, "issues": [BINDING_NOT_ESTABLISHED]},
                    confidence="high",
                    recommendation=_unbound_recommendation(tool_name, "risk override"),
                    context=context,
                    provenance_kind="static_declaration",
                )
            )
            continue
        findings.append(
            agent_finding(
                check_id="SHIP-MANIFEST-STALE-RISK-OVERRIDE",
                title=f"Risk override references missing tool {tool_name}",
                severity="medium",
                category="manifest",
                evidence={"tool": tool_name},
                confidence="high",
                recommendation="Remove stale risk overrides or update them to current tool names.",
                context=context,
                provenance_kind="static_declaration",
            )
        )
    return findings


def _missing_high_risk_owners(context: ScanContext) -> list:
    if context.manifest.environment.target not in {"production_like", "production"}:
        return []
    findings = []
    for tool in context.tools:
        if not is_policy_eligible_high_risk_tool(tool) or tool.owner:
            continue
        findings.append(
            tool_finding(
                tool=tool,
                check_id="SHIP-MANIFEST-HIGH-RISK-OWNER-MISSING",
                title=f"{tool.name} is high-risk but has no owner",
                severity="high",
                category="manifest",
                evidence={
                    "environment": context.manifest.environment.target,
                    "risk_tags": risk_tags(tool, min_confidence="medium"),
                },
                confidence="high",
                recommendation="Declare an owner for each high-risk production tool in risk_overrides.tools.",
                context=context,
                provenance_kind="static_declaration",
            )
        )
    return findings


def _unused_manifest_scopes(context: ScanContext) -> list:
    manifest_scopes = context.manifest.permissions.scopes
    if not manifest_scopes:
        return []
    tool_scopes = [scope for tool in context.tools for scope in tool.auth.scopes]
    findings = []
    for manifest_scope in manifest_scopes:
        if any(_scope_covers_tool_scope(manifest_scope, tool_scope) for tool_scope in tool_scopes):
            continue
        severity = "high" if is_broad_scope(manifest_scope) else "medium"
        findings.append(
            agent_finding(
                check_id="SHIP-MANIFEST-UNUSED-SCOPE",
                title=f"Manifest declares unused permission scope {manifest_scope}",
                severity=severity,
                category="manifest",
                evidence={
                    "scope": manifest_scope,
                    "tool_scopes": sorted(tool_scopes),
                },
                confidence="medium",
                recommendation="Remove unused manifest scopes or add tool metadata showing why they are required.",
                context=context,
                provenance_kind="static_declaration",
            )
        )
    return findings


def _scope_covers_tool_scope(manifest_scope: str, tool_scope: str) -> bool:
    declared = manifest_scope.lower()
    required = tool_scope.lower()
    if declared in {"*", required}:
        return True
    return declared.endswith(":*") and required.startswith(declared[:-1])
