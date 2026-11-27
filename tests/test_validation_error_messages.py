"""A Pydantic ``ValidationError`` never becomes a message as Pydantic's own text.

``str(ValidationError)`` carries type codes (``type=missing``), the invalid
input values (``input_value=…``) and an ``errors.pydantic.dev`` URL per row.
Interpolated into an error message it reaches the console, the agent-mode
``message`` and artifact prose, where it echoes whatever the input held and
names nothing a reader can act on (#1040). Every boundary describes the error
with :func:`agents_shipgate.core.errors.format_validation_error` instead, or
:func:`~agents_shipgate.core.errors.format_error_message` /
:func:`~agents_shipgate.core.errors.format_exception_text` where the handler
catches other exceptions too.

The source walk below enforces that with an empty allowlist. A handler is
checked when it names ``ValidationError`` (alone or in a tuple), or catches one
by subclass (``ValueError``, ``Exception``) around code that can raise one: a
``model_validate*`` call, a ``Model(**payload)`` construction, or a call to a
function in this package that does either without catching it, followed
through any depth of such functions. In a checked handler the exception, or a
plain alias of it, must not reach an f-string, ``%``, ``str``/``repr``/
``format``, ``.json()``, or any call; nor may an unfiltered ``.errors()``, one
of its entries, or an entry's ``input``. An in-package function it is passed to
is checked the same way; only a ``format_*`` helper or a type test receives it
unread, and a call or comparison that only decides an ``if`` reads it without
rendering it. A branch, or the rest of a block after an early exit, that has
established the exception is or is not a ``ValidationError`` is read
accordingly. Only the package walk builds the package index (a few seconds);
the synthetic cases index their own source and a small helper module.
"""

from __future__ import annotations

import ast
import functools
import json
from dataclasses import dataclass, field
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
#: A try whose handlers catch one of these keeps a ValidationError from
#: leaving the function; the handler is then checked where it stands.
_CATCHES_VALIDATION = _BROAD | {"ValidationError"}
#: Callees that receive the exception without rendering it.
_TYPE_TESTS = frozenset({"isinstance", "issubclass", "type", "id", "callable"})
_TERMINAL = (ast.Return, ast.Raise, ast.Continue, ast.Break)
_NESTED_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


# --- the package index: functions, imports, and which can raise --------------


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(SOURCE_ROOT.parent).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


@dataclass
class _Module:
    name: str
    is_package: bool
    tree: ast.Module
    imports: dict[str, tuple[str, str | None]] = field(default_factory=dict)


@dataclass
class _Index:
    modules: dict[str, _Module]
    functions: dict[tuple[str, str], ast.FunctionDef | ast.AsyncFunctionDef]
    raising: set[tuple[str, str]]


def _statements(statements: list[ast.stmt]):
    """Every statement, nested ones included, without walking expressions."""

    for statement in statements:
        yield statement
        for value in ast.iter_fields(statement):
            items = value[1]
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, ast.stmt):
                        yield from _statements([item])
                    elif isinstance(item, ast.excepthandler | ast.match_case):
                        yield from _statements(item.body)


def _import_table(module: _Module, known: set[str]) -> dict[str, tuple[str, str | None]]:
    table: dict[str, tuple[str, str | None]] = {}
    for node in _statements(module.tree.body):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                base = module.name.split(".")
                if not module.is_package:
                    base = base[:-1]
                base = base[: len(base) - (node.level - 1)]
                source = ".".join([*base, *([node.module] if node.module else [])])
            else:
                source = node.module or ""
            for alias in node.names:
                local = alias.asname or alias.name
                submodule = f"{source}.{alias.name}"
                table[local] = (submodule, None) if submodule in known else (source, alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in known:
                    table[alias.asname or alias.name] = (alias.name, None)
    return table


def _function_table(module: _Module) -> dict[tuple[str, str], ast.FunctionDef | ast.AsyncFunctionDef]:
    table: dict[tuple[str, str], ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for node in module.tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            table[(module.name, node.name)] = node
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                    table[(module.name, f"{node.name}.{item.name}")] = item
    return table


def _callee_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else ""


def _is_validation(call: ast.Call) -> bool:
    name = _callee_name(call)
    if name in _VALIDATION_CALLS:
        return True
    # `Model(**payload)` validates exactly as `Model.model_validate(payload)`.
    return name[:1].isupper() and any(keyword.arg is None for keyword in call.keywords)


def _resolve(index: _Index, module: str, call: ast.Call) -> tuple[str, str] | None:
    func = call.func
    imports = index.modules[module].imports
    if isinstance(func, ast.Name):
        if (module, func.id) in index.functions:
            return (module, func.id)
        target = imports.get(func.id)
        if target and target[1] and target in index.functions:
            return (target[0], target[1])
    elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        target = imports.get(func.value.id)
        if target and target[1] is None and (target[0], func.attr) in index.functions:
            return (target[0], func.attr)
    return None


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


def _escaping_calls(statements: list[ast.stmt]) -> list[ast.Call]:
    """Calls whose ValidationError would leave these statements uncaught."""

    calls: list[ast.Call] = []

    def visit(node: ast.AST, caught: bool) -> None:
        if isinstance(node, _NESTED_SCOPES):
            return
        if isinstance(node, ast.Try | ast.TryStar):
            catches = set().union(*(_type_names(h.type) for h in node.handlers)) if node.handlers else set()
            for statement in node.body:
                visit(statement, caught or bool(catches & _CATCHES_VALIDATION))
            for handler in node.handlers:
                for statement in handler.body:
                    visit(statement, caught)
            for statement in [*node.orelse, *node.finalbody]:
                visit(statement, caught)
            return
        if isinstance(node, ast.Call) and not caught:
            calls.append(node)
        for child in ast.iter_child_nodes(node):
            visit(child, caught)

    for statement in statements:
        visit(statement, False)
    return calls


def _build_index(sources: dict[str, tuple[bool, str]]) -> _Index:
    modules = {
        name: _Module(name, is_package, ast.parse(text))
        for name, (is_package, text) in sources.items()
    }
    for module in modules.values():
        module.imports = _import_table(module, set(modules))
    functions = {
        key: node for module in modules.values() for key, node in _function_table(module).items()
    }
    index = _Index(modules, functions, set())
    # Read each function once: does it validate, and whom does it call,
    # outside a try that catches the error? Then spread "can raise" from
    # the validators to their callers.
    callers: dict[tuple[str, str], set[tuple[str, str]]] = {}
    pending: list[tuple[str, str]] = []
    for key, node in functions.items():
        for call in _escaping_calls(node.body):
            if _is_validation(call):
                pending.append(key)
            elif (target := _resolve(index, key[0], call)) is not None:
                callers.setdefault(target, set()).add(key)
    while pending:
        key = pending.pop()
        if key not in index.raising:
            index.raising.add(key)
            pending.extend(callers.get(key, ()))
    return index


@functools.cache
def _package_index() -> _Index:
    return _build_index(
        {
            _module_name(path): (path.name == "__init__.py", path.read_text(encoding="utf-8"))
            for path in sorted(SOURCE_ROOT.rglob("*.py"))
        }
    )


# --- what a checked handler may do with the exception ------------------------


def _narrowing(test: ast.expr, names: frozenset[str]) -> bool | None:
    """True when ``test`` holds only for a ValidationError bound to ``names``,
    False when it holds only for something else, None when it says nothing."""

    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        inner = _narrowing(test.operand, names)
        return None if inner is None else not inner
    if (
        isinstance(test, ast.Call)
        and isinstance(test.func, ast.Name)
        and test.func.id == "isinstance"
        and len(test.args) == 2
        and isinstance(test.args[0], ast.Name)
        and test.args[0].id in names
        and "ValidationError" in _type_names(test.args[1])
    ):
        return True
    return None


def _unfiltered_errors(node: ast.AST, names: frozenset[str]) -> bool:
    """``exc.errors()`` with the input values or URLs still in it."""

    if not (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "errors"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in names
    ):
        return False
    filtered = {
        keyword.arg
        for keyword in node.keywords
        if isinstance(keyword.value, ast.Constant) and keyword.value.value is False
    }
    return not {"include_input", "include_url"} <= filtered


#: Keys of one ``errors()`` entry that carry what the input held.
_INPUT_KEYS = frozenset({"input", "url", "ctx"})


def _is_exception(node: ast.AST, names: frozenset[str]) -> bool:
    """The exception, its unfiltered ``errors()``, one entry of that list, or
    an entry's ``input``/``url``/``ctx`` field: all of them carry the input."""

    if isinstance(node, ast.Name) and node.id in names:
        return True
    if _unfiltered_errors(node, names):
        return True
    if isinstance(node, ast.Subscript) and _unfiltered_errors(node.value, names):
        return True  # `exc.errors()[0]`: a whole entry
    return (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Subscript)
        and _unfiltered_errors(node.value.value, names)
        and isinstance(node.slice, ast.Constant)
        and node.slice.value in _INPUT_KEYS
    )


@dataclass
class _Reader:
    index: _Index
    module: str
    seen: set[tuple[tuple[str, str], str]] = field(default_factory=set)

    def statements(self, statements: list[ast.stmt], names: frozenset[str]) -> list[int]:
        hits: list[int] = []
        for statement in statements:
            if isinstance(statement, ast.If):
                narrowed = _narrowing(statement.test, names)
                hits += self.condition(statement.test, names)
                if narrowed is not False:
                    hits += self.statements(statement.body, names)
                if narrowed is not True:
                    hits += self.statements(statement.orelse, names)
                ends = bool(statement.body) and isinstance(statement.body[-1], _TERMINAL)
                if narrowed is True and ends and not statement.orelse:
                    # Only a ValidationError entered the branch, and it left:
                    # what follows holds something else.
                    return hits
                continue
            if (
                isinstance(statement, ast.Assign)
                and isinstance(statement.value, ast.Name)
                and statement.value.id in names
            ):
                names = names | {
                    target.id for target in statement.targets if isinstance(target, ast.Name)
                }
                continue
            if isinstance(statement, _NESTED_SCOPES):
                continue
            for name, value in ast.iter_fields(statement):
                if isinstance(value, list) and value and isinstance(value[0], ast.stmt):
                    hits += self.statements(value, names)
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, ast.ExceptHandler):
                            hits += self.statements(item.body, names)
                        elif isinstance(item, ast.AST):
                            hits += self.expression(item, names)
                elif isinstance(value, ast.AST) and name != "body":
                    hits += self.expression(value, names)
        return hits

    def expression(self, node: ast.AST, names: frozenset[str]) -> list[int]:
        if isinstance(node, _NESTED_SCOPES):
            return []
        if isinstance(node, ast.IfExp):
            narrowed = _narrowing(node.test, names)
            hits = self.condition(node.test, names)
            if narrowed is not False:
                hits += self.expression(node.body, names)
            if narrowed is not True:
                hits += self.expression(node.orelse, names)
            return hits
        hits: list[int] = []
        if isinstance(node, ast.FormattedValue) and _is_exception(node.value, names):
            hits.append(node.lineno)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
            operands = node.right.elts if isinstance(node.right, ast.Tuple) else [node.right]
            if any(_is_exception(operand, names) for operand in operands):
                hits.append(node.lineno)
        elif isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "json"
                and isinstance(func.value, ast.Name)
                and func.value.id in names
            ):
                hits.append(node.lineno)
            if self.call_renders(node, names):
                hits.append(node.lineno)
        for child in ast.iter_child_nodes(node):
            hits += self.expression(child, names)
        return hits

    def condition(self, node: ast.expr, names: frozenset[str]) -> list[int]:
        """A test only reads the exception: a predicate call, or a comparison
        of its text, decides a branch and renders nothing. Its arguments are
        still read as expressions, so an f-string built for it is caught."""

        if isinstance(node, ast.BoolOp):
            return [line for value in node.values for line in self.condition(value, names)]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return self.condition(node.operand, names)
        if isinstance(node, ast.Compare):
            return [
                line
                for operand in (node.left, *node.comparators)
                for line in self.condition(operand, names)
            ]
        if isinstance(node, ast.Call):
            return [
                line
                for argument in (*node.args, *(keyword.value for keyword in node.keywords))
                for line in self.expression(argument, names)
            ]
        return self.expression(node, names)

    def call_renders(self, call: ast.Call, names: frozenset[str]) -> bool:
        passed = [
            (position, None, argument)
            for position, argument in enumerate(call.args)
            if _is_exception(argument, names)
        ] + [
            (None, keyword.arg, keyword.value)
            for keyword in call.keywords
            if _is_exception(keyword.value, names)
        ]
        if not passed:
            return False
        callee = _callee_name(call)
        if callee in _TYPE_TESTS or callee.lstrip("_").startswith("format_"):
            return False
        target = _resolve(self.index, self.module, call)
        if target is None:
            return True
        node = self.index.functions[target]
        parameters = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        offset = 1 if "." in target[1] else 0  # bound method: `self` is not passed
        for position, keyword, argument in passed:
            if not isinstance(argument, ast.Name):
                return True  # an unfiltered `.errors()` handed on
            if keyword is not None:
                parameter = keyword
            elif position + offset < len(node.args.posonlyargs) + len(node.args.args):
                parameter = parameters[position + offset].arg
            else:
                return True  # into *args: cannot follow
            if parameter not in {item.arg for item in parameters}:
                return True  # into **kwargs: cannot follow
            if (target, parameter) in self.seen:
                continue
            self.seen.add((target, parameter))
            reader = _Reader(self.index, target[0], self.seen)
            if reader.statements(node.body, frozenset({parameter})):
                return True
        return False


def _reaches_validation(index: _Index, module: str, statements: list[ast.stmt]) -> bool:
    for statement in statements:
        for node in ast.walk(statement):
            if isinstance(node, ast.Call) and (
                _is_validation(node) or _resolve(index, module, node) in index.raising
            ):
                return True
    return False


def _leaks_in(index: _Index, module: str) -> list[int]:
    """Handler lines whose body turns a caught ValidationError into text."""

    leaks: list[int] = []
    for node in ast.walk(index.modules[module].tree):
        if not isinstance(node, ast.Try | ast.TryStar):
            continue
        reaches = None
        taken_earlier = False
        for handler in node.handlers:
            caught = _type_names(handler.type)
            names_it = "ValidationError" in caught
            checked = names_it
            if not checked and not taken_earlier and caught & _BROAD:
                if reaches is None:
                    reaches = _reaches_validation(index, module, node.body)
                checked = reaches
            # A ValidationError never reaches a handler after one that takes it.
            taken_earlier = taken_earlier or bool(caught & _CATCHES_VALIDATION)
            if handler.name and checked and _Reader(index, module).statements(
                handler.body, frozenset({handler.name})
            ):
                leaks.append(handler.lineno)
    return leaks


#: A module the synthetic cases import, so they exercise resolution across
#: modules without paying for the package index.
_HELPERS = """\
from pydantic import ValidationError

def load(d):
    return M.model_validate(d)

def load_quietly(d):
    try:
        return M.model_validate(d)
    except ValidationError as e:
        raise ValueError(format_it(e)) from e
"""


def _leaks(source: str) -> list[int]:
    """The walk over one synthetic module, beside :data:`_HELPERS`."""

    index = _build_index({"synthetic": (False, source), "helpers": (False, _HELPERS)})
    return _leaks_in(index, "synthetic")


_PRELUDE = "from pydantic import ValidationError\n"


@pytest.mark.parametrize(
    ("source", "leaks"),
    [
        # Each way of turning the exception into text.
        ("try:\n x()\nexcept ValidationError as exc:\n raise E(f'bad: {exc}')\n", [3]),
        ("try:\n x()\nexcept (OSError, ValidationError) as exc:\n raise E(str(exc))\n", [3]),
        ("try:\n x()\nexcept pydantic.ValidationError as e:\n m = '{}'.format(e)\n", [3]),
        ("try:\n x()\nexcept ValidationError as e:\n m = 'bad %s' % e\n", [3]),
        ("try:\n x()\nexcept ValidationError as e:\n raise ConfigError(e)\n", [3]),
        ("try:\n x()\nexcept ValidationError as e:\n typer.echo(e, err=True)\n", [3]),
        ("try:\n x()\nexcept ValidationError as e:\n raise typer.BadParameter(e)\n", [3]),
        ("try:\n x()\nexcept ValidationError as e:\n log.warning('%s', e)\n", [3]),
        ("try:\n x()\nexcept ValidationError as e:\n m = e.json()\n", [3]),
        ("try:\n x()\nexcept ValidationError as e:\n m = f'{e.errors()}'\n", [3]),
        ("try:\n x()\nexcept ValidationError as e:\n m = str(e.errors(include_input=False))\n", [3]),
        ("try:\n x()\nexcept ValidationError as e:\n err = e\n m = f'{err}'\n", [3]),
        # Each way of catching one by subclass.
        ("try:\n M.model_validate(d)\nexcept ValueError as e:\n raise E(f'{e}')\n", [3]),
        ("try:\n M.model_validate(d)\nexcept Exception as e:\n raise E(str(e))\n", [3]),
        ("try:\n M(**d)\nexcept ValueError as e:\n raise E(f'{e}')\n", [3]),
        (
            "def load(d):\n return M.model_validate(d)\n"
            "def outer(d):\n return load(d)\n"
            "try:\n outer(d)\nexcept ValueError as e:\n raise E(str(e))\n",
            [7],
        ),
        ("from helpers import load\ntry:\n load(d)\nexcept ValueError as e:\n raise E(f'{e}')\n", [4]),
        ("import helpers\ntry:\n helpers.load(d)\nexcept Exception as e:\n raise E(str(e))\n", [4]),
        ("from helpers import load_quietly\ntry:\n load_quietly(d)\nexcept ValueError as e:\n raise E(str(e))\n", []),
        # A helper the exception is handed to is read the same way.
        (
            "def say(error):\n return 'bad: ' + str(error)\n"
            "try:\n x()\nexcept ValidationError as e:\n raise E(say(e))\n",
            [5],
        ),
        (
            "def say(kind, error):\n"
            " return format_it(error) if isinstance(error, ValidationError) else str(error)\n"
            "try:\n x()\nexcept ValidationError as e:\n raise E(say('x', e))\n",
            [],
        ),
        # Narrowed away from ValidationError: the other exception's text is fine.
        (
            "try:\n x()\nexcept (OSError, ValidationError) as e:\n"
            " m = format_it(e) if isinstance(e, ValidationError) else f'{e}'\n",
            [],
        ),
        (
            "try:\n x()\nexcept (OSError, ValidationError) as e:\n"
            " if isinstance(e, ValidationError):\n  m = format_it(e)\n else:\n  m = str(e)\n",
            [],
        ),
        (
            "try:\n x()\nexcept (OSError, ValidationError) as e:\n"
            " if not isinstance(e, ValidationError):\n  m = str(e)\n",
            [],
        ),
        (
            "def say(e):\n if isinstance(e, ValidationError):\n  return format_it(e)\n return f'{e}'\n"
            "try:\n x()\nexcept (OSError, ValidationError) as e:\n raise E(say(e))\n",
            [],
        ),
        # The ValidationError went to an earlier handler.
        (
            "try:\n M.model_validate(d)\nexcept ValidationError as e:\n m = format_it(e)\n"
            "except ValueError as e:\n m = str(e)\n",
            [],
        ),
        # A broad handler around code that cannot raise one, or that catches its own.
        ("try:\n x()\nexcept ValueError as e:\n raise E(f'{e}')\n", []),
        (
            "def load(d):\n try:\n  return M.model_validate(d)\n"
            " except ValidationError as e:\n  raise ValueError(format_it(e)) from e\n"
            "try:\n load(d)\nexcept ValueError as e:\n raise E(str(e))\n",
            [],
        ),
        # Structured access is not Pydantic's rendering, nor is a type test.
        ("try:\n x()\nexcept ValidationError as e:\n m = f\"{e.errors()[0]['msg']}\"\n", []),
        ("try:\n x()\nexcept ValidationError as e:\n m = str(e.errors()[0])\n", [3]),
        ("try:\n x()\nexcept ValidationError as e:\n m = f\"{e.errors()[0]['input']}\"\n", [3]),
        ("try:\n x()\nexcept ValidationError as e:\n raise E(format_validation_error(e, label='x')) from e\n", []),
        ("try:\n x()\nexcept ValidationError as e:\n m = type(e).__name__\n", []),
        # A test decides a branch; it renders nothing.
        (
            "def is_floor(error):\n return 'floor' in str(error)\n"
            "try:\n x()\nexcept ValidationError as e:\n if is_floor(e):\n  raise E('floor')\n",
            [],
        ),
        ("try:\n x()\nexcept ValidationError as e:\n if 'floor' in str(e):\n  raise E('floor')\n", []),
        ("try:\n x()\nexcept ValidationError as e:\n if check(f'{e}'):\n  raise E('floor')\n", [3]),
        # Only one handler sees a ValidationError: the first that can take it.
        (
            "try:\n M.model_validate(d)\nexcept ValueError as e:\n raise E(format_it(e))\n"
            "except Exception as e:\n raise E(f'{e}')\n",
            [],
        ),
        # A helper the walk cannot read is assumed to render what it is given.
        ("try:\n x()\nexcept ValidationError as e:\n raise E(describe(e))\n", [3]),
    ],
)
def test_the_source_walk_sees_the_shapes_it_is_for(source: str, leaks: list[int]) -> None:
    offset = _PRELUDE.count("\n")
    assert _leaks(_PRELUDE + source) == [line + offset for line in leaks]


def test_no_handler_turns_a_validation_error_into_text() -> None:
    index = _package_index()
    found = sorted(
        f"{module.replace('.', '/')}.py:{line}"
        for module in index.modules
        for line in _leaks_in(index, module)
    )
    # The walk must find the handlers it is about, or it proves nothing.
    handlers = sum(
        1
        for module in index.modules.values()
        for node in ast.walk(module.tree)
        if isinstance(node, ast.ExceptHandler) and "ValidationError" in _type_names(node.type)
    )
    assert handlers > 30
    assert len(index.raising) > 100
    assert [entry for entry in found if entry not in ALLOWLIST] == [], (
        "Describe the error with core.errors.format_validation_error, or "
        "format_error_message / format_exception_text when the handler catches "
        "other exceptions too"
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


def test_agent_handoff_reports_an_invalid_verifier_by_field(tmp_path: Path) -> None:
    verifier = tmp_path / "verifier.json"
    verifier.write_text(
        json.dumps({"verifier_schema_version": "0.1", "unexpected": CANARY}), encoding="utf-8"
    )
    result = CliRunner().invoke(
        app,
        ["agent", "handoff", "--from", str(verifier), "--json"],
        env={"AGENTS_SHIPGATE_AGENT_MODE": "1"},
    )
    assert result.exit_code == 3, result.output
    assert "the verifier artifacts failed validation:" in result.stderr
    assert "- unexpected: Extra inputs are not permitted" in result.stderr
    for text in (*PYDANTIC_TEXT, CANARY):
        assert text not in result.output, result.output
    [line] = [line for line in result.stderr.splitlines() if line.startswith("{")]
    assert json.loads(line)["exit_code"] == 3


def _mcp_handoff(tmp_path: Path) -> None:
    from agents_shipgate.mcp_server.server import shipgate_handoff

    verifier = tmp_path / "verifier.json"
    verifier.write_text(
        json.dumps({"verifier_schema_version": "0.1", "unexpected": CANARY}), encoding="utf-8"
    )
    shipgate_handoff(verifier_path=str(verifier))


def _mcp_preflight(tmp_path: Path) -> None:
    from agents_shipgate.mcp_server.server import shipgate_preflight

    shipgate_preflight(workspace=str(tmp_path), capability_request={"unexpected": CANARY})


@pytest.mark.parametrize("tool", [_mcp_handoff, _mcp_preflight], ids=["handoff", "preflight"])
def test_an_mcp_tool_reports_invalid_input_by_field(tmp_path: Path, tool) -> None:
    """The SDK hands a tool's exception text to the client as the error."""

    from pydantic import ValidationError

    with pytest.raises(Exception) as caught:
        tool(tmp_path)
    assert not isinstance(caught.value, ValidationError)
    assert isinstance(caught.value.__cause__, ValidationError), caught.value
    message = str(caught.value)
    assert "- unexpected: Extra inputs are not permitted" in message
    for text in (*PYDANTIC_TEXT, CANARY):
        assert text not in message, message


def test_an_internal_error_names_an_internal_model_not_the_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ValidationError at an internal-error boundary is a product defect, so
    its text must not tell the user their input was invalid (#328)."""

    from pydantic import TypeAdapter

    from agents_shipgate.cli import skill

    def fail(**_: object) -> None:
        TypeAdapter(int).validate_python(CANARY)

    monkeypatch.setattr(skill, "run_skill_review", fail)
    result = CliRunner().invoke(
        app, ["skill", "lint", str(tmp_path)], env={"AGENTS_SHIPGATE_AGENT_MODE": "1"}
    )
    assert result.exit_code == 4, result.output
    assert "Internal error: an internal model failed validation:\n- <root>:" in result.stderr
    assert "Invalid input" not in result.stderr
    for text in (*PYDANTIC_TEXT, CANARY):
        assert text not in result.stderr, result.stderr
