"""Private, invocation-local evidence for recovering an unresolved agent name."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from agents_shipgate.cli.discovery.signals import (
    _non_product_origin,
    detect_workspace,
    select_agent_name,
)
from agents_shipgate.core.errors import DiscoveryError
from agents_shipgate.schemas.detect import DetectResult


@dataclass(frozen=True)
class AgentNameRecovery:
    state: Literal["product", "non_product_only", "unresolved"]
    facts: dict[str, object]
    product_path: str | None = None

    @property
    def identity_facts(self) -> dict[str, object]:
        return {"state": self.state, "facts": self.facts, "product_path": self.product_path}

    @property
    def human_reason(self) -> str | None:
        if self.state != "non_product_only":
            return None
        return (
            "Readable names are from test/template code. A person must choose "
            "agent.name or supply product code; an arbitrary nonempty value is not evidence."
        )


def needs_agent_name(placeholders: Sequence[Mapping[str, object]]) -> bool:
    return any(entry.get("path") == "agent.name" for entry in placeholders)


def classify_agent_name(result: DetectResult) -> AgentNameRecovery:
    """Classify only the names this discovery read, never an absence of agents.

    Coverage and scope outrank selection. A capped or partly unreadable census
    cannot establish that its test names are the only readable candidates.
    No warning/rationale prose or saved discovery artifact is a routing input.
    """
    candidates = [
        candidate for candidate in result.agent_name_candidates
        if candidate.role != "workspace_dir"
    ]
    signals = result.workspace_signals
    facts = {
        "candidates": [candidate.model_dump(mode="json", exclude={"rationale", "rank_score"})
                       for candidate in candidates],
        "agent_scope": result.agent_scope,
        "agent_scope_truncated": result.agent_scope_truncated,
        "python_parse_truncated": result.python_parse_truncated,
        "python_file_count": signals.python_file_count,
        "python_file_total": signals.python_file_total,
    }
    if (
        result.agent_scope != "single"
        or result.agent_scope_truncated
        or result.python_parse_truncated
        or signals.python_file_count != signals.python_file_total
    ):
        return AgentNameRecovery("unresolved", facts)
    selected = select_agent_name(candidates)
    if selected is not None:
        return AgentNameRecovery("product", facts, selected.path)
    if candidates and all(
        candidate.path is not None and _non_product_origin(candidate.path) is not None
        for candidate in candidates
    ):
        return AgentNameRecovery("non_product_only", facts)
    return AgentNameRecovery("unresolved", facts)


def discover_agent_name(
    manifest_path: Path, placeholders: Sequence[Mapping[str, object]],
) -> AgentNameRecovery | None:
    """Fresh doctor evidence, scoped to this manifest rather than its siblings.

    Discovery failure does not replace the existing manifest/source failure.
    Doctor has no parse-cap override, so offer no higher-cap retry it cannot
    consume. Broader unresolved-name recovery remains outside this slice.
    """
    if not needs_agent_name(placeholders):
        return None
    try:
        # Source inspection resolves the manifest itself before choosing its
        # directory. A link's parent can contain an unrelated product or tests.
        root = manifest_path.resolve().parent
        recovery = classify_agent_name(detect_workspace(root))
        return replace(
            recovery,
            facts={**recovery.facts, "workspace": str(root)},
            product_path=str(root / recovery.product_path) if recovery.product_path else None,
        )
    except DiscoveryError:
        return AgentNameRecovery("unresolved", {"discovery_failed": True})
