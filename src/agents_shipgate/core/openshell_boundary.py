"""Project the shared policy comparator through existing host review rules."""
from __future__ import annotations

from dataclasses import dataclass

from agents_shipgate.core.boundary_diff import BoundaryInputIssue
from agents_shipgate.core.boundary_rules import GENERIC_BOUNDARY_RULES
from agents_shipgate.core.host_boundary import DEFAULT_RULES, HostBoundaryPolicy
from agents_shipgate.core.openshell_compare import compare_openshell_grants
from agents_shipgate.schemas.agent_result_v1 import AgentResultDiagnostic, AgentResultViolatedRule


@dataclass(frozen=True)
class OpenShellBoundaryEvidence:
    paths: frozenset[str] = frozenset()
    before: dict | None = None
    after: dict | None = None
    changed_links: frozenset[str] = frozenset()


def assess_openshell_boundary(evidence: OpenShellBoundaryEvidence, changed: set[str],
                              policy: HostBoundaryPolicy):
    touched = evidence.paths & changed
    if not touched:
        return [], [], [], set()
    violations, diagnostics, issues = [], [], []
    for inventory in (evidence.before, evidence.after):
        for issue in (inventory or {}).get("issues", []):
            if issue.get("host") == "openshell" and issue.get("blocking"):
                issues.append(BoundaryInputIssue(code="openshell_input_unresolved",
                    path=issue.get("source"), message=issue["message"]))
    if touched & evidence.changed_links:
        changes = [(None, None, path) for path in sorted(touched & evidence.changed_links)]
    elif evidence.before is None or evidence.after is None:
        changes = [(None, None, path) for path in sorted(touched)]
    else:
        def grants(inventory):
            return {row["grant_id"]: row for row in inventory.get("grants", [])
                    if row.get("kind") == "openshell_policy"}
        before, after = grants(evidence.before), grants(evidence.after)
        changes = [(before.get(key), after.get(key),
                    (after.get(key) or before[key])["source"])
                   for key in sorted(before.keys() | after.keys())]
    safe = set(touched) if not issues else set()
    for before, after, path in changes:
        result = compare_openshell_grants(before, after)
        if result.direction in {"equivalent", "narrowed"}:
            diagnostics.append(AgentResultDiagnostic(level="info", code="openshell_policy_narrowed",
                path=path, message=result.explanation))
            continue
        safe.clear()
        rule = (policy.rules.get("HOST-PERMISSION-ALLOW-EXPANDED")
                or DEFAULT_RULES["HOST-PERMISSION-ALLOW-EXPANDED"]) if result.widened else (
                    GENERIC_BOUNDARY_RULES["PROTECTED-SURFACE-UNCLASSIFIED"])
        violations.append(AgentResultViolatedRule(id=rule.id, check_id=rule.check_id,
            action=rule.action, risk_level=rule.risk_level,
            title="OpenShell declared policy requires review", path=path,
            evidence={"kind": "openshell_policy_changed", "direction": result.direction,
                      "widened": list(result.widened), "narrowed": list(result.narrowed),
                      "limits": list(result.limits), "explanation": result.explanation},
            recommendation=rule.recommendation))
    # Selection files define which document is reviewed. Changing that trust
    # root is a separate obligation even if every individual grant is neutral.
    safe = {path for path in safe if not path.endswith("/.shipgate/openshell.json")
            and path != ".shipgate/openshell.json"}
    return violations, diagnostics, issues, safe
