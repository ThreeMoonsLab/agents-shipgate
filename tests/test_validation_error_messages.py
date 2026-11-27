"""A Pydantic ``ValidationError`` never becomes a message as Pydantic's own text.

``str(ValidationError)`` carries type codes (``type=missing``), the invalid
input values (``input_value=…``) and an ``errors.pydantic.dev`` URL per row.
Interpolated into an error message it reaches the console, the agent-mode
``message`` and artifact prose, where it echoes whatever the input held and
names nothing a reader can act on (#1040). Every boundary describes the error
with :func:`agents_shipgate.core.errors.format_validation_error` instead, or
:func:`~agents_shipgate.core.errors.format_error_message` where the handler
catches other exceptions too.

The source walk below enforces that with an empty allowlist. It sees a handler
that names ``ValidationError`` (alone or in a tuple), and a ``ValueError`` /
``Exception`` handler around a direct ``model_validate*`` call, which catches
one by subclass. It does not follow the exception into a helper function; a
helper that formats it is the fix, and is read in review.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_ROOT = REPO_ROOT / "src" / "agents_shipgate"

#: Handlers allowed to interpolate a ValidationError. Keep it empty: a new
#: entry needs a reason a person will read, not a convenience.
ALLOWLIST: frozenset[str] = frozenset()

#: What Pydantic's own rendering puts in a message, and no formatted one does.
PYDANTIC_TEXT = ("errors.pydantic.dev", "type=", "input_value", "input_type", "validation error for")

_VALIDATION_CALLS = frozenset(
    {
        "model_validate",
        "model_validate_json",
        "model_validate_strings",
        "validate_python",
        "validate_json",
        "validate_strings",
    }
)
#: Handler types that catch a ValidationError by subclass without naming it.
_BROAD = frozenset({"Exception", "BaseException", "ValueError"})


def _type_names(node: ast.expr | None) -> set[str]:
    if node is None:
        return set()
    if isinstance(node, ast.Tuple):
        return {name for element in node.elts for name in _type_names(element)}
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.Attribute):
        return {node.attr}
    return set()


def _validates(statements: list[ast.stmt]) -> bool:
    for node in ast.walk(ast.Module(body=statements, type_ignores=[])):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in _VALIDATION_CALLS:
                return True
    return False


def _narrowing(test: ast.expr, name: str) -> bool | None:
    """True when ``test`` holds only for a ValidationError bound to ``name``,
    False when it holds only for something else, None when it says nothing."""

    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        inner = _narrowing(test.operand, name)
        return None if inner is None else not inner
    if (
        isinstance(test, ast.Call)
        and isinstance(test.func, ast.Name)
        and test.func.id == "isinstance"
        and len(test.args) == 2
        and isinstance(test.args[0], ast.Name)
        and test.args[0].id == name
        and "ValidationError" in _type_names(test.args[1])
    ):
        return True
    return None


def _is_name(node: ast.AST, name: str) -> bool:
    return isinstance(node, ast.Name) and node.id == name


def _interpolations(node: ast.AST, name: str) -> list[int]:
    """Lines where ``name`` is turned into text, outside branches that have
    established it is not a ValidationError."""

    if isinstance(node, (ast.If, ast.IfExp)):
        narrowed = _narrowing(node.test, name)
        branches: list[ast.AST | list[ast.stmt]] = [node.test]
        if narrowed is not False:
            branches.append(node.body)
        if narrowed is not True:
            branches.append(node.orelse)
        hits: list[int] = []
        for branch in branches:
            for child in branch if isinstance(branch, list) else [branch]:
                hits.extend(_interpolations(child, name))
        return hits
    hits = []
    if isinstance(node, ast.FormattedValue) and _is_name(node.value, name):
        hits.append(node.lineno)
    elif isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name) and func.id in {"str", "repr", "format"}:
            if node.args and _is_name(node.args[0], name):
                hits.append(node.lineno)
        elif isinstance(func, ast.Attribute) and func.attr == "format":
            if any(_is_name(arg, name) for arg in node.args):
                hits.append(node.lineno)
        # `raise SomeError(exc)`: the new exception's text is the old one's.
        callee = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if callee.endswith(("Error", "Exception")) and any(
            _is_name(arg, name) for arg in node.args
        ):
            hits.append(node.lineno)
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        right = node.right.elts if isinstance(node.right, ast.Tuple) else [node.right]
        if isinstance(node.left, ast.Constant) and any(_is_name(item, name) for item in right):
            hits.append(node.lineno)
    for child in ast.iter_child_nodes(node):
        hits.extend(_interpolations(child, name))
    return hits


def _leaks(source: str) -> list[int]:
    """Handler lines whose body turns a caught ValidationError into text."""

    leaks: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Try | ast.TryStar):
            continue
        validated_earlier = False
        for handler in node.handlers:
            caught = _type_names(handler.type)
            names_it = "ValidationError" in caught
            by_subclass = (
                not validated_earlier and bool(caught & _BROAD) and _validates(node.body)
            )
            validated_earlier = validated_earlier or names_it
            if not handler.name or not (names_it or by_subclass):
                continue
            if any(
                _interpolations(statement, handler.name) for statement in handler.body
            ):
                leaks.append(handler.lineno)
    return leaks


@pytest.mark.parametrize(
    ("source", "leaks"),
    [
        ("try:\n x()\nexcept ValidationError as exc:\n raise E(f'bad: {exc}')\n", [3]),
        ("try:\n x()\nexcept (OSError, ValidationError) as exc:\n raise E(str(exc))\n", [3]),
        ("try:\n x()\nexcept pydantic.ValidationError as e:\n m = '{}'.format(e)\n", [3]),
        ("try:\n x()\nexcept ValidationError as e:\n m = 'bad %s' % e\n", [3]),
        ("try:\n x()\nexcept ValidationError as e:\n raise ConfigError(e)\n", [3]),
        ("try:\n M.model_validate(d)\nexcept ValueError as e:\n raise E(f'{e}')\n", [3]),
        ("try:\n M.model_validate(d)\nexcept Exception as e:\n raise E(str(e))\n", [3]),
        # Narrowed away from ValidationError: the other exception's text is fine.
        (
            "try:\n x()\nexcept (OSError, ValidationError) as e:\n"
            " m = fmt(e) if isinstance(e, ValidationError) else f'{e}'\n",
            [],
        ),
        (
            "try:\n x()\nexcept (OSError, ValidationError) as e:\n"
            " if isinstance(e, ValidationError):\n  m = fmt(e)\n else:\n  m = str(e)\n",
            [],
        ),
        (
            "try:\n x()\nexcept (OSError, ValidationError) as e:\n"
            " if not isinstance(e, ValidationError):\n  m = str(e)\n",
            [],
        ),
        # The ValidationError went to an earlier handler.
        (
            "try:\n M.model_validate(d)\nexcept ValidationError as e:\n m = fmt(e)\n"
            "except ValueError as e:\n m = str(e)\n",
            [],
        ),
        # A broad handler around code that validates nothing directly.
        ("try:\n x()\nexcept ValueError as e:\n raise E(f'{e}')\n", []),
        # Structured access is not Pydantic's rendering.
        ("try:\n x()\nexcept ValidationError as e:\n m = f\"{e.errors()[0]['msg']}\"\n", []),
        ("try:\n x()\nexcept ValidationError as e:\n raise E(fmt(e, label='x')) from e\n", []),
    ],
)
def test_the_source_walk_sees_the_shapes_it_is_for(source: str, leaks: list[int]) -> None:
    assert _leaks(source) == leaks


def test_no_handler_turns_a_validation_error_into_text() -> None:
    found = sorted(
        f"{path.relative_to(SOURCE_ROOT).as_posix()}:{line}"
        for path in SOURCE_ROOT.rglob("*.py")
        for line in _leaks(path.read_text(encoding="utf-8"))
    )
    # The walk must find the handlers it is about, or it proves nothing.
    handlers = sum(
        1
        for path in SOURCE_ROOT.rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ExceptHandler) and "ValidationError" in _type_names(node.type)
    )
    assert handlers > 30
    assert [entry for entry in found if entry not in ALLOWLIST] == [], (
        "Describe the error with core.errors.format_validation_error, or "
        "format_error_message when the handler catches other exceptions too"
    )


# --- representative ValidationErrors through the boundaries that leaked ------

CANARY = "private-validation-canary"


def _preflight_plan(tmp_path: Path) -> None:
    from agents_shipgate.core.preflight import _coerce_plan

    _coerce_plan({"planned_files": [CANARY]})


def _capability_request(tmp_path: Path) -> None:
    from agents_shipgate.core.preflight import _coerce_capability_request

    _coerce_capability_request({"unexpected": CANARY})


def _base_preflight(tmp_path: Path) -> None:
    from agents_shipgate.core.preflight import _coerce_base_preflight

    _coerce_base_preflight({"unexpected": CANARY})


def _packet(tmp_path: Path) -> None:
    from agents_shipgate.packet.json_packet import load_packet_json

    load_packet_json({"packet_schema_version": "0.18", "unexpected": CANARY})


def _baseline(tmp_path: Path) -> None:
    from agents_shipgate.core.baseline import load_baseline

    path = tmp_path / "baseline.json"
    path.write_text(json.dumps({"unexpected": CANARY}), encoding="utf-8")
    load_baseline(path)


def _baseline_audit_log(tmp_path: Path) -> None:
    from agents_shipgate.core.baseline_audit import read_audit_log

    path = tmp_path / "baseline-audit.log"
    path.write_text(json.dumps({"unexpected": CANARY}) + "\n", encoding="utf-8")
    read_audit_log(path)


def _skill_review_config(tmp_path: Path) -> None:
    from agents_shipgate.skill.config import load_skill_review_config

    path = tmp_path / "skill-review.yml"
    path.write_text(f"suppressions: {CANARY}\n", encoding="utf-8")
    load_skill_review_config(path)


def _diff_baseline(tmp_path: Path) -> None:
    from agents_shipgate.core.lenses.tool_surface import _reference_from_baseline_payload

    _reference_from_baseline_payload({"unexpected": CANARY}, "baseline.json")


def _next_action(tmp_path: Path) -> None:
    from agents_shipgate.core.agent_control import _validate_action

    _validate_action({"kind": "review", "unexpected": CANARY})


def _control_pointer(tmp_path: Path) -> None:
    from agents_shipgate.core.current_control import (
        CURRENT_CONTROL_ARTIFACT_NAME,
        _load_pointer,
    )

    path = tmp_path / CURRENT_CONTROL_ARTIFACT_NAME
    path.write_text(json.dumps({"unexpected": CANARY}), encoding="utf-8")
    _load_pointer(tmp_path, path)


@pytest.mark.parametrize(
    "boundary",
    [
        _preflight_plan,
        _capability_request,
        _base_preflight,
        _packet,
        _baseline,
        _baseline_audit_log,
        _skill_review_config,
        _diff_baseline,
        _next_action,
        _control_pointer,
    ],
    ids=lambda boundary: boundary.__name__.lstrip("_"),
)
def test_a_validation_error_is_described_by_field_without_its_input(
    tmp_path: Path, boundary
) -> None:
    from pydantic import ValidationError

    with pytest.raises(Exception) as caught:
        boundary(tmp_path)
    assert not isinstance(caught.value, ValidationError)
    assert isinstance(caught.value.__cause__, ValidationError), caught.value
    message = str(caught.value)
    for text in (*PYDANTIC_TEXT, CANARY):
        assert text not in message, message
    assert "\n- " in message, message


def test_evidence_packet_reports_an_invalid_packet_by_field(tmp_path: Path) -> None:
    packet = tmp_path / "packet.json"
    packet.write_text(
        json.dumps({"packet_schema_version": "0.18", "unexpected": CANARY}), encoding="utf-8"
    )
    result = CliRunner().invoke(
        app,
        ["evidence-packet", "--from", str(packet), "--out", str(tmp_path / "out")],
        env={"AGENTS_SHIPGATE_AGENT_MODE": "1"},
    )
    assert result.exit_code == 2, result.output
    assert "- run_id: Field required" in result.stderr
    assert "- unexpected: Extra inputs are not permitted" in result.stderr
    for text in (*PYDANTIC_TEXT, CANARY):
        assert text not in result.stderr, result.stderr
    [line] = [line for line in result.stderr.splitlines() if line.startswith("{")]
    assert json.loads(line)["exit_code"] == 2
