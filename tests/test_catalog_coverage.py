"""Every active catalog check ID has a test that asserts it fires (#1048).

A check whose ID no test asserts can stop firing, or start firing on the wrong
evidence, and the whole suite still passes. This module reads the test sources
and requires every ``active`` catalog ID to appear, as the full ID string, at an
*assertion site*:

* inside an ``assert`` statement's condition (not its message);
* in a comprehension's filter, which selects findings for a test to assert on
  (``[f for f in findings if f.check_id == "SHIP-..."]``);
* bound by a plain assignment (``CHECK = "SHIP-..."``, or a tuple, list, set or
  dict of literals) to a name that an assertion site reads;
* in a ``pytest.mark.parametrize`` value whose parameter an assertion site in the
  decorated test reads (an ``id=`` label is not a value);
* in the value of a one-element unpacking (``[finding] = ...``), which fails
  unless exactly one item comes back;
* as a ``check_id`` in a committed golden that a test compares whole against
  what the engine emits.

An ID under ``not`` or on either side of ``not in``/``!=``/``is not`` is a
negative assertion and does not count. Comments, docstrings, assertion messages,
comparisons in an ``if`` statement and plain mentions never count.

The rule proves an ID is pinned where a test fails if it changes; it cannot see
whether the asserted value came from running a check, so an assertion about the
catalog entry itself (its metadata or its docs) also counts. Projection and deprecated
IDs are exempt through the catalog's ``lifecycle`` field. The rest of the gap is
the shrink-only allowlist in ``tests/fixtures/catalog_coverage_allowlist.yaml``.
"""

from __future__ import annotations

import ast
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

import pytest
import yaml

from agents_shipgate.checks import registry
from agents_shipgate.schemas.checks import CheckMetadata

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = REPO_ROOT / "tests"
ALLOWLIST = TESTS_DIR / "fixtures" / "catalog_coverage_allowlist.yaml"

#: Directories under ``tests/`` that hold inputs a test scans, not test code.
DATA_DIRS = frozenset({"corpus", "fixtures", "golden", "starter_repo"})

#: Committed goldens compared whole against engine output, and the module that
#: compares them. A ``check_id`` in one fails that module if the check stops
#: firing. The hand-written protocol examples in ``tests/golden/agent_protocol``
#: are only schema-validated, so they are not listed.
COMPARED_GOLDENS: dict[str, tuple[str, str]] = {
    "samples/*/expected/*.json": (
        "tests/test_regenerate_goldens.py",
        "regenerate_goldens.py",
    ),
    "tests/golden/codex_boundary_result/*.json": (
        "tests/test_codex_boundary_check.py",
        '"codex_boundary_result"',
    ),
}

#: Severities whose IDs must have a firing test; the allowlist cannot hold them
#: under ``uncovered``.
BLOCKING_SEVERITIES = frozenset({"critical", "high"})

_ID_TOKEN = re.compile(r"(?<![A-Za-z0-9_-])[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+(?![A-Za-z0-9_-])")
_NEGATIVE_OPS = (ast.NotIn, ast.NotEq, ast.IsNot)
_LITERAL_CALLS = frozenset({"dict", "frozenset", "list", "set", "sorted", "tuple"})


# --- The assertion-site rule -------------------------------------------------


def _ids_in(text: str) -> set[str]:
    return set(_ID_TOKEN.findall(text))


def _literal_ids(node: ast.AST | None) -> set[str] | None:
    """IDs in a literal expression, or ``None`` when it is not a literal.

    Only literals bind a name: ``findings = scan(fixture("SHIP-X"))`` does not
    make ``findings`` stand for ``SHIP-X``.
    """

    if isinstance(node, ast.Constant):
        return _ids_in(node.value) if isinstance(node.value, str) else set()
    if isinstance(node, ast.Starred):
        return _literal_ids(node.value)
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        parts: list[ast.AST | None] = list(node.elts)
    elif isinstance(node, ast.Dict):
        parts = [part for part in (*node.keys, *node.values) if part is not None]
    elif (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _LITERAL_CALLS
        and not node.keywords
    ):
        parts = list(node.args)
    else:
        return None
    found: set[str] = set()
    for part in parts:
        ids = _literal_ids(part)
        if ids is None:
            return None
        found |= ids
    return found


def _positive_reads(node: ast.AST, positive: bool = True) -> Iterator[tuple[str, str]]:
    """Yield ``("literal", text)`` and ``("name", id)`` reached positively."""

    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        yield from _positive_reads(node.operand, not positive)
        return
    if isinstance(node, ast.Compare):
        if all(isinstance(op, _NEGATIVE_OPS) for op in node.ops):
            positive = not positive
        for part in (node.left, *node.comparators):
            yield from _positive_reads(part, positive)
        return
    if isinstance(node, ast.Constant):
        if positive and isinstance(node.value, str):
            yield "literal", node.value
        return
    if isinstance(node, ast.Name):
        if positive:
            yield "name", node.id
        return
    for child in ast.iter_child_nodes(node):
        yield from _positive_reads(child, positive)


def _is_single_unpack(target: ast.AST) -> bool:
    return (
        isinstance(target, (ast.List, ast.Tuple))
        and len(target.elts) == 1
        and not isinstance(target.elts[0], ast.Starred)
    )


def _assertion_sites(tree: ast.AST) -> Iterator[ast.AST]:
    """An assert's condition, a comprehension's filter, a one-element unpacking.

    A comparison in an ``if`` statement is not a site: the block it guards may
    never run, and ``if check_id in ids: raise AssertionError`` asserts the
    opposite of firing.
    """

    if isinstance(tree, ast.Assert):
        yield tree.test
        return
    if (
        isinstance(tree, ast.Assign)
        and len(tree.targets) == 1
        and _is_single_unpack(tree.targets[0])
    ):
        yield tree.value
        return
    if isinstance(tree, ast.comprehension):
        yield from tree.ifs
    for child in ast.iter_child_nodes(tree):
        yield from _assertion_sites(child)


def _name_bindings(tree: ast.AST) -> dict[str, set[str]]:
    bound: dict[str, set[str]] = defaultdict(set)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            targets, value = [node.target], node.iter
        else:
            continue
        ids = _literal_ids(value)
        if not ids:
            continue
        for target in targets:
            for name in ast.walk(target):
                if isinstance(name, ast.Name):
                    bound[name.id] |= ids
    return bound


def _module_values(tree: ast.Module) -> dict[str, ast.AST]:
    return {
        node.targets[0].id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    }


def _parametrize_rows(
    decorator: ast.AST, module_values: Mapping[str, ast.AST]
) -> tuple[list[str], list[list[ast.AST]]] | None:
    if not (
        isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr == "parametrize"
        and len(decorator.args) >= 2
    ):
        return None
    names_node, values_node = decorator.args[0], decorator.args[1]
    if isinstance(names_node, ast.Constant) and isinstance(names_node.value, str):
        names = [name.strip() for name in names_node.value.split(",") if name.strip()]
    elif isinstance(names_node, (ast.Tuple, ast.List)):
        names = [
            item.value
            for item in names_node.elts
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        ]
    else:
        return None
    if isinstance(values_node, ast.Name):
        values_node = module_values.get(values_node.id)
    if not isinstance(values_node, (ast.List, ast.Tuple)):
        return None
    rows: list[list[ast.AST]] = []
    for row in values_node.elts:
        if isinstance(row, ast.Call) and getattr(row.func, "attr", None) == "param":
            rows.append(list(row.args))  # ``id=`` is a label, not a value
        elif len(names) == 1:
            rows.append([row])
        elif isinstance(row, (ast.Tuple, ast.List)):
            rows.append(list(row.elts))
    return names, rows


def asserted_ids(source: str) -> set[str]:
    """Every ID-shaped token the module's source pins at an assertion site."""

    tree = ast.parse(source)
    bound = _name_bindings(tree)
    found: set[str] = set()
    for site in _assertion_sites(tree):
        for kind, value in _positive_reads(site):
            found |= _ids_in(value) if kind == "literal" else bound.get(value, set())
    module_values = _module_values(tree)
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        read = {
            value
            for site in _assertion_sites(ast.Module(body=function.body, type_ignores=[]))
            for kind, value in _positive_reads(site)
            if kind == "name"
        }
        for decorator in function.decorator_list:
            parsed = _parametrize_rows(decorator, module_values)
            if parsed is None:
                continue
            names, rows = parsed
            for index, name in enumerate(names):
                if name not in read:
                    continue
                for row in rows:
                    if index < len(row):
                        found |= _literal_ids(row[index]) or set()
    return found


def golden_check_ids(payload: object) -> set[str]:
    """Every ``check_id`` value in a golden JSON document."""

    found: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key == "check_id" and isinstance(value, str):
                found.add(value)
            else:
                found |= golden_check_ids(value)
    elif isinstance(payload, list):
        for item in payload:
            found |= golden_check_ids(item)
    return found


# --- The coverage gate -------------------------------------------------------


@dataclass(frozen=True)
class Allowlist:
    uncovered: dict[str, str] = field(default_factory=dict)
    unreachable: dict[str, str] = field(default_factory=dict)

    @property
    def ids(self) -> set[str]:
        return {*self.uncovered, *self.unreachable}


def load_allowlist(path: Path = ALLOWLIST) -> Allowlist:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    assert set(raw) <= {"uncovered", "unreachable"}, sorted(raw)
    return Allowlist(
        uncovered=dict(raw.get("uncovered") or {}),
        unreachable=dict(raw.get("unreachable") or {}),
    )


def coverage_problems(
    catalog: Iterable[CheckMetadata], covered: set[str], allowlist: Allowlist
) -> list[str]:
    """What the catalog, the assertion sites and the allowlist disagree on."""

    by_id = {check.id: check for check in catalog}
    active = {check_id for check_id, check in by_id.items() if check.lifecycle == "active"}
    problems: list[str] = []
    for check_id in sorted(active - covered - allowlist.ids):
        problems.append(
            f"{check_id} ({by_id[check_id].default_severity}) has no test that asserts "
            "it fires. Add one that runs the check and asserts this exact ID."
        )
    for check_id in sorted(allowlist.ids & covered):
        problems.append(
            f"{check_id} now has an assertion site; remove it from {ALLOWLIST.name}."
        )
    for check_id in sorted(allowlist.ids - active):
        lifecycle = by_id[check_id].lifecycle if check_id in by_id else "not in the catalog"
        problems.append(
            f"{check_id} is allowlisted but is {lifecycle}; remove it from {ALLOWLIST.name}."
        )
    for check_id in sorted(set(allowlist.uncovered) & set(allowlist.unreachable)):
        problems.append(f"{check_id} is listed both as uncovered and as unreachable.")
    for check_id in sorted(allowlist.uncovered):
        check = by_id.get(check_id)
        if check is not None and check.default_severity in BLOCKING_SEVERITIES:
            problems.append(
                f"{check_id} is {check.default_severity}: it needs a firing test, "
                "not an allowlist entry."
            )
    for section in (allowlist.uncovered, allowlist.unreachable):
        for check_id, reason in sorted(section.items()):
            if not isinstance(reason, str) or not reason.strip():
                problems.append(f"{check_id} is allowlisted without a reason.")
    return problems


def _test_sources() -> Iterator[Path]:
    for path in sorted(TESTS_DIR.rglob("*.py")):
        if DATA_DIRS.isdisjoint(path.relative_to(TESTS_DIR).parts[:-1]):
            yield path


@cache
def _covered_by_tests() -> frozenset[str]:
    found: set[str] = set()
    for path in _test_sources():
        found |= asserted_ids(path.read_text(encoding="utf-8"))
    return frozenset(found)


@cache
def _covered_by_goldens() -> frozenset[str]:
    found: set[str] = set()
    for pattern in COMPARED_GOLDENS:
        for path in sorted(REPO_ROOT.glob(pattern)):
            found |= golden_check_ids(json.loads(path.read_text(encoding="utf-8")))
    return frozenset(found)


def _catalog() -> list[CheckMetadata]:
    return registry.check_catalog(plugins_enabled=False)


def test_every_active_catalog_id_is_asserted_to_fire_or_allowlisted() -> None:
    problems = coverage_problems(
        _catalog(), set(_covered_by_tests() | _covered_by_goldens()), load_allowlist()
    )

    assert not problems, "\n".join(problems)


def test_the_compared_goldens_are_still_compared_by_their_tests() -> None:
    for pattern, (owner, reference) in COMPARED_GOLDENS.items():
        assert sorted(REPO_ROOT.glob(pattern)), pattern
        assert reference in (REPO_ROOT / owner).read_text(encoding="utf-8"), (pattern, owner)


def test_the_scan_reads_the_test_tree_and_skips_its_data() -> None:
    scanned = {path.relative_to(REPO_ROOT).as_posix() for path in _test_sources()}

    assert "tests/test_catalog_coverage.py" in scanned
    assert "tests/agent_tasks/test_agent_tasks.py" in scanned
    assert not any(
        part in DATA_DIRS for path in scanned for part in Path(path).parts[1:-1]
    )


# --- The gate itself ---------------------------------------------------------


def _check(check_id: str, severity: str = "medium", lifecycle: str = "active") -> CheckMetadata:
    return CheckMetadata(
        id=check_id,
        category="test",
        default_severity=severity,
        lifecycle=lifecycle,
        description="A planted catalog entry.",
    )


def test_a_new_active_id_without_a_firing_test_fails_the_gate() -> None:
    catalog = [_check("SHIP-OLD-COVERED"), _check("SHIP-NEW-UNTESTED", "low")]

    problems = coverage_problems(catalog, {"SHIP-OLD-COVERED"}, Allowlist())

    assert len(problems) == 1
    assert problems[0].startswith("SHIP-NEW-UNTESTED (low) has no test")


def test_projection_and_deprecated_ids_are_exempt_through_their_lifecycle() -> None:
    catalog = [
        _check("SHIP-ROW-LABEL", lifecycle="projection"),
        _check("SHIP-RETIRED", lifecycle="deprecated"),
    ]

    assert coverage_problems(catalog, set(), Allowlist()) == []


def test_the_allowlist_only_shrinks() -> None:
    catalog = [_check("SHIP-GAINED-A-TEST"), _check("SHIP-STILL-UNTESTED")]
    allowlist = Allowlist(
        uncovered={"SHIP-GAINED-A-TEST": "no test yet", "SHIP-STILL-UNTESTED": "no test yet"}
    )

    problems = coverage_problems(catalog, {"SHIP-GAINED-A-TEST"}, allowlist)

    assert problems == [
        "SHIP-GAINED-A-TEST now has an assertion site; remove it from "
        "catalog_coverage_allowlist.yaml."
    ]


@pytest.mark.parametrize("severity", ["critical", "high"])
def test_a_blocking_id_cannot_be_allowlisted_as_uncovered(severity: str) -> None:
    catalog = [_check("SHIP-BLOCKER", severity)]

    problems = coverage_problems(
        catalog, set(), Allowlist(uncovered={"SHIP-BLOCKER": "no test yet"})
    )

    assert problems == [
        f"SHIP-BLOCKER is {severity}: it needs a firing test, not an allowlist entry."
    ]


def test_an_unreachable_id_is_listed_with_its_reason_at_any_severity() -> None:
    catalog = [_check("SHIP-NO-EMITTER", "high")]

    assert coverage_problems(
        catalog, set(), Allowlist(unreachable={"SHIP-NO-EMITTER": "no code path emits it"})
    ) == []
    assert coverage_problems(
        catalog, set(), Allowlist(unreachable={"SHIP-NO-EMITTER": " "})
    ) == ["SHIP-NO-EMITTER is allowlisted without a reason."]


def test_an_allowlisted_id_the_catalog_no_longer_treats_as_active_is_removed() -> None:
    catalog = [_check("SHIP-RETIRED", lifecycle="deprecated")]

    problems = coverage_problems(
        catalog, set(), Allowlist(uncovered={"SHIP-RETIRED": "no test yet"})
    )

    assert problems == [
        "SHIP-RETIRED is allowlisted but is deprecated; remove it from "
        "catalog_coverage_allowlist.yaml."
    ]


def test_the_committed_allowlist_has_no_blocking_uncovered_id() -> None:
    severities = {check.id: check.default_severity for check in _catalog()}

    assert not {
        check_id
        for check_id in load_allowlist().uncovered
        if severities.get(check_id) in BLOCKING_SEVERITIES
    }


# --- The assertion-site rule itself ------------------------------------------

PLANTED = "SHIP-PLANTED-ID"


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(f'def test_x(ids):\n    assert "{PLANTED}" in ids\n', id="assert-in"),
        pytest.param(
            f'def test_x(findings):\n    assert [f.check_id for f in findings] == ["{PLANTED}"]\n',
            id="assert-equal",
        ),
        pytest.param(
            f'def test_x(findings):\n    assert any(f.check_id == "{PLANTED}" for f in findings)\n',
            id="assert-any",
        ),
        pytest.param(
            f'def _only(findings):\n    return [f for f in findings if f.check_id == "{PLANTED}"]\n',
            id="comprehension-filter",
        ),
        pytest.param(
            f'CHECK = "{PLANTED}"\n\ndef test_x(ids):\n    assert CHECK in ids\n',
            id="literal-bound-name",
        ),
        pytest.param(
            f'EXPECTED = {{"{PLANTED}", "SHIP-OTHER"}}\n\ndef test_x(ids):\n    assert ids >= EXPECTED\n',
            id="literal-bound-set",
        ),
        pytest.param(
            f'def test_x(ids):\n    for check_id in ("{PLANTED}",):\n        assert check_id in ids\n',
            id="loop-over-literals",
        ),
        pytest.param(
            f'def test_x(findings):\n    [finding] = only(findings, "{PLANTED}")\n',
            id="one-element-unpacking",
        ),
        pytest.param(
            "import pytest\n\n"
            f'@pytest.mark.parametrize("path,check_id", [("a.json", "{PLANTED}")])\n'
            "def test_x(path, check_id, ids):\n    assert check_id in ids\n",
            id="parametrize-compared",
        ),
        pytest.param(
            "import pytest\n\n"
            f'CASES = [pytest.param("{PLANTED}", id="case")]\n\n'
            '@pytest.mark.parametrize("check_id", CASES)\n'
            "def test_x(check_id, ids):\n    assert check_id in ids\n",
            id="parametrize-module-table",
        ),
        pytest.param(
            f'def test_x(text):\n    assert "fires {PLANTED} once" in text\n',
            id="inside-a-longer-literal",
        ),
    ],
)
def test_an_assertion_site_counts(source: str) -> None:
    assert PLANTED in asserted_ids(source)


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(f"# {PLANTED}\ndef test_x():\n    assert True\n", id="comment"),
        pytest.param(f'def test_x():\n    """{PLANTED} fires."""\n    assert True\n', id="docstring"),
        pytest.param(f'def test_x(ids):\n    assert ids, "{PLANTED}"\n', id="assert-message"),
        pytest.param(f'def test_x(ids):\n    assert "{PLANTED}" not in ids\n', id="not-in"),
        pytest.param(
            f'def test_x(findings):\n    assert not any(f.check_id == "{PLANTED}" for f in findings)\n',
            id="not-any",
        ),
        pytest.param(
            f'def test_x(findings):\n    assert all(f.check_id != "{PLANTED}" for f in findings)\n',
            id="all-not-equal",
        ),
        pytest.param(
            f'def _rest(findings):\n    return [f for f in findings if f.check_id != "{PLANTED}"]\n',
            id="filtered-out",
        ),
        pytest.param(
            "def test_x(findings):\n    for f in findings:\n"
            f'        if f.check_id == "{PLANTED}":\n            raise AssertionError(f)\n',
            id="if-statement-comparison",
        ),
        pytest.param(
            f'def test_x(render):\n    finding = Finding(check_id="{PLANTED}")\n    assert render(finding)\n',
            id="hand-built-finding",
        ),
        pytest.param(
            f'def test_x(ids):\n    findings = scan(fixture("{PLANTED}"))\n    assert findings\n',
            id="non-literal-binding",
        ),
        pytest.param(
            "import pytest\n\n"
            f'@pytest.mark.parametrize("check_id", [pytest.param("x", id="{PLANTED}")])\n'
            "def test_x(check_id, ids):\n    assert check_id in ids\n",
            id="parametrize-id-label",
        ),
        pytest.param(
            "import pytest\n\n"
            f'@pytest.mark.parametrize("check_id", ["{PLANTED}"])\n'
            "def test_x(check_id, ids):\n    run(check_id)\n    assert ids\n",
            id="parametrize-value-not-compared",
        ),
        pytest.param(
            f'def test_x(ids):\n    assert "{PLANTED}-LONGER" in ids\n',
            id="longer-id",
        ),
    ],
)
def test_a_mention_that_is_not_an_assertion_does_not_count(source: str) -> None:
    assert PLANTED not in asserted_ids(source)


def test_a_golden_counts_only_its_check_id_values() -> None:
    golden = {
        "findings": [{"check_id": PLANTED, "title": "SHIP-ONLY-IN-A-TITLE"}],
        "violated_rules": [{"id": "RULE-1", "check_id": "SHIP-SECOND"}],
    }

    assert golden_check_ids(golden) == {PLANTED, "SHIP-SECOND"}
