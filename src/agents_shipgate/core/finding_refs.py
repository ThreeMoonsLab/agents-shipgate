from __future__ import annotations

from agents_shipgate.core.models import Finding


def finding_tool_names(
    finding: Finding,
    known_tool_names: set[str] | list[str] | tuple[str, ...],
) -> list[str]:
    """Return tool names referenced by a finding and present in known tools."""
    known = set(known_tool_names)
    names: set[str] = set()
    if finding.tool_name:
        names.add(finding.tool_name)
    for key in ("tool_name", "tool"):
        value = finding.evidence.get(key)
        if isinstance(value, str):
            names.add(value)
    for key in ("tools", "high_risk_tools"):
        value = finding.evidence.get(key)
        if isinstance(value, list):
            names.update(item for item in value if isinstance(item, str))
    return sorted(name for name in names if name in known)

