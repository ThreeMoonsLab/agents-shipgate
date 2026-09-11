"""Associate private observations and compare source predicates, never findings."""

from __future__ import annotations

from collections import defaultdict

from agents_shipgate.core.domain import LoadedToolSource, Tool
from agents_shipgate.schemas.guard_dependencies import (
    BooleanSourceComparison,
    GuardDependencyComparison,
    GuardDependencyEvidence,
)


def associate_guard_dependencies(
    sources: list[LoadedToolSource], tools: list[Tool]
) -> list[GuardDependencyEvidence]:
    members: dict[str, list[Tool]] = defaultdict(list)
    for tool in tools:
        for observation_id in tool.observation_ids:
            members[observation_id].append(tool)
    result = []
    for source in sources:
        for evidence in source.guard_dependencies:
            candidates = members[evidence.observation_id]
            if len(candidates) == 1:
                result.append(
                    evidence.model_copy(
                        update={"tool_id": candidates[0].id, "tool_name": candidates[0].name}
                    )
                )
            else:
                result.append(
                    evidence.model_copy(
                        update={
                            "tool_id": None,
                            "status": "ambiguous",
                            "reason": "capability_observation_not_unique",
                        }
                    )
                )
    return sorted(result, key=lambda row: (row.observation_id, row.tool_path, row.tool_line))


def _valid_predicate(row: GuardDependencyEvidence) -> bool:
    count = len(row.parameters)
    return (
        row.status == "observed"
        and row.tool_id is not None
        and count <= 8
        and row.parameters == sorted(set(row.parameters))
        and row.allowed_inputs == sorted(set(row.allowed_inputs))
        and all(type(mask) is int and 0 <= mask < 1 << count for mask in row.allowed_inputs)
        and row.guard_path is not None
        and row.guard_line is not None
        and row.call_line is not None
        and bool(row.inputs)
        and len({item.path for item in row.inputs}) == len(row.inputs)
        and any(item.path == row.tool_path and item.role == "tool_module" for item in row.inputs)
        and any(item.path == row.guard_path and item.role == "guard_module" for item in row.inputs)
    )


def _valid_source(row: GuardDependencyEvidence) -> bool:
    source = row.source_behavior
    return bool(
        _valid_predicate(row)
        and source is not None
        and source.status == "observed"
        and source.configuration_reads == "none"
        and source.binding is not None
        and type(source.binding.tool_bound) is bool
        and len(source.parameters) <= 8
        and source.parameters == sorted(set(source.parameters))
        and set(row.parameters) <= set(source.parameters)
        and len(source.returns) == 1 << len(source.parameters)
        and all(value in {"true", "false", "none"} for value in source.returns)
    )


def _domain_direction(before: set[int], after: set[int]) -> str:
    return (
        "unchanged"
        if before == after
        else "widened"
        if before < after
        else "narrowed"
        if after < before
        else "changed"
    )


def _compare_source(
    old: GuardDependencyEvidence | None, new: GuardDependencyEvidence | None
) -> BooleanSourceComparison:
    result = BooleanSourceComparison(reason="base_or_head_source_model_unavailable")
    if old is None or new is None or not _valid_source(old) or not _valid_source(new):
        return result
    before, after = old.source_behavior, new.source_behavior
    if (
        old.tool_id,
        old.source_id,
        old.tool_path,
        old.tool_symbol,
        old.guard_path,
        old.guard_symbol,
        before.parameters,
    ) != (
        new.tool_id,
        new.source_id,
        new.tool_path,
        new.tool_symbol,
        new.guard_path,
        new.guard_symbol,
        after.parameters,
    ):
        result.reason = "source_subject_or_parameter_domain_changed"
        return result
    result.returns = "unchanged" if before.returns == after.returns else "changed"
    previous = {i for i, value in enumerate(before.returns) if value == "true"}
    following = {i for i, value in enumerate(after.returns) if value == "true"}
    result.true_domain = _domain_direction(previous, following)
    if (before.binding.agent_symbol, before.binding.agent_name) != (
        after.binding.agent_symbol,
        after.binding.agent_name,
    ):
        result.binding = "changed"
        result.reason = "agent_source_identity_changed; bound relation is unresolved"
        return result
    result.binding = (
        "unchanged"
        if before.binding.tool_bound == after.binding.tool_bound
        else "added"
        if after.binding.tool_bound
        else "removed"
    )
    result.bound_true_domain = _domain_direction(
        previous if before.binding.tool_bound else set(),
        following if after.binding.tool_bound else set(),
    )
    result.reason = (
        "Compare the closed Boolean source function and its literal Agent tool membership only. "
        "A true return is not approval, authority or an action effect; deployed wiring and "
        "finding-predicate attribution remain unproved. No finding is excluded."
    )
    return result


def compare_guard_dependencies(
    current: list[GuardDependencyEvidence], base: list[GuardDependencyEvidence]
) -> list[GuardDependencyComparison]:
    before: dict[str, list[GuardDependencyEvidence]] = defaultdict(list)
    after: dict[str, list[GuardDependencyEvidence]] = defaultdict(list)
    for row in base:
        before[row.observation_id].append(row)
    for row in current:
        after[row.observation_id].append(row)
    comparisons = []
    for key in sorted(before.keys() | after.keys()):
        old_rows, new_rows = before[key], after[key]
        old = old_rows[0] if len(old_rows) == 1 else None
        new = new_rows[0] if len(new_rows) == 1 else None
        subject = new or old or (new_rows or old_rows)[0]
        direction = "unresolved"
        reason = "base_or_head_guard_evidence_unavailable"
        if len(old_rows) > 1 or len(new_rows) > 1:
            reason = "duplicate_guard_observation"
        elif old is not None and new is not None:
            if not _valid_predicate(old) or not _valid_predicate(new):
                reason = "predicate_or_dependency_evidence_unresolved"
            elif (
                old.tool_id,
                old.source_id,
                old.tool_path,
                old.tool_symbol,
                old.guard_path,
                old.guard_symbol,
                old.parameters,
            ) != (
                new.tool_id,
                new.source_id,
                new.tool_path,
                new.tool_symbol,
                new.guard_path,
                new.guard_symbol,
                new.parameters,
            ):
                reason = "guard_subject_or_parameter_domain_changed"
            else:
                previous, following = set(old.allowed_inputs), set(new.allowed_inputs)
                direction = (
                    "predicate_unchanged"
                    if previous == following
                    else "predicate_widened"
                    if previous < following
                    else "predicate_narrowed"
                    if following < previous
                    else "predicate_changed"
                )
                reason = "Compare the bounded Boolean source predicate only; downstream effects, runtime wiring and whole-capability dependency coverage remain unproved. No finding is excluded."
        comparisons.append(
            GuardDependencyComparison(
                observation_id=key,
                tool_id=subject.tool_id,
                tool_name=subject.tool_name,
                direction=direction,
                reason=reason,
                before=old,
                after=new,
                source_behavior=(
                    _compare_source(old, new)
                    if any(row.source_behavior is not None for row in old_rows + new_rows)
                    else None
                ),
            )
        )
    return comparisons
