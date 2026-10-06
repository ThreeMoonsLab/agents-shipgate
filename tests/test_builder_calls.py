from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from agents_shipgate.core.static_inputs import (
    StaticInputSnapshot,
    activate_static_input_snapshot,
    reset_static_input_snapshot,
)
from agents_shipgate.inputs.builder_calls import (
    BuilderCalls,
    CallLimit,
    CallSite,
    bind_call,
    single_return,
)
from agents_shipgate.inputs.python_imports import ImportResolver


def _caller_input_plan(root):
    from agents_shipgate.core.verification_identity import build_verification_plan

    source = (root / "builders.py").read_text()
    resolver = ImportResolver(root)
    module = resolver.entry(root / "builders.py", ast.parse(source), source)
    snapshot = StaticInputSnapshot(root)
    token = activate_static_input_snapshot(snapshot)
    try:
        census = BuilderCalls(resolver).callers(module, module.tree.body[0])
        plan = build_verification_plan(
            git_root=root, input_root=root, config_path=root / "shipgate.yaml",
            config_logical_path="shipgate.yaml", base_ref=None, head_ref="HEAD",
            archived_head=False, repository_id="https://example.test/caller-fixture.git",
            base_commit_sha=None, base_tree_sha=None, head_commit_sha=None,
            head_tree_sha=None, merge_base_sha=None, changed_files=[], diff_text="",
            baseline_path=None, diff_from_path=None, policy_pack_paths=[],
            evaluation_date="2026-10-05", options={}, plugins_enabled=False,
            captured_input_paths=snapshot.paths(),
        )
        snapshot.finish()
        return census, plan
    finally:
        reset_static_input_snapshot(token)


def test_repairing_an_unread_caller_file_requires_a_fresh_input_plan(tmp_path):
    from agents_shipgate.core.verification_identity import validate_dependency_inputs
    from agents_shipgate.inputs.common import MAX_INPUT_FILE_BYTES

    (tmp_path / "shipgate.yaml").write_text("agent:\n  name: caller-fixture\n")
    (tmp_path / "builders.py").write_text("def build(tools):\n    return None\n")
    (tmp_path / "app.py").write_text("from builders import build\na = build([read])\n")
    unread = tmp_path / "zz_unused.py"
    unread.write_bytes(b"#" * (MAX_INPUT_FILE_BYTES + 1))
    census, old_plan = _caller_input_plan(tmp_path)
    assert census.limits and "could not be read" in census.limits[0]
    unread.write_text("from builders import build\na = build([write])\n")
    with pytest.raises(ValueError, match="dependency could not be captured"):
        validate_dependency_inputs(old_plan, root=tmp_path)
    fresh_census, fresh_plan = _caller_input_plan(tmp_path)
    assert not fresh_census.limits and len(fresh_census.sites) == 2
    validate_dependency_inputs(fresh_plan, root=tmp_path)
    assert fresh_plan.inputs.input_set_id != old_plan.inputs.input_set_id


def test_a_new_operator_provider_invalidates_the_caller_input_plan(tmp_path):
    from agents_shipgate.core.input_directory_identity import validate_directory_inputs
    from agents_shipgate.core.verification_identity import validate_dependency_inputs

    (tmp_path / "shipgate.yaml").write_text("agent:\n  name: caller-fixture\n")
    (tmp_path / "builders.py").write_text("def build(tools):\n    return None\n")
    (tmp_path / "app.py").write_text("from builders import build\na = build([read])\n")
    _, old_plan = _caller_input_plan(tmp_path)
    validate_dependency_inputs(old_plan, root=tmp_path)
    validate_directory_inputs(old_plan, snapshot=StaticInputSnapshot(tmp_path))
    (tmp_path / "operator.py").write_text("def ior(owner, values):\n    return owner\n")
    with pytest.raises(ValueError, match="input directory membership changed"):
        validate_directory_inputs(old_plan, snapshot=StaticInputSnapshot(tmp_path))
    _, fresh_plan = _caller_input_plan(tmp_path)
    validate_dependency_inputs(fresh_plan, root=tmp_path)
    validate_directory_inputs(fresh_plan, snapshot=StaticInputSnapshot(tmp_path))
    assert fresh_plan.inputs.input_set_id != old_plan.inputs.input_set_id


def _function(signature: str):
    return ast.parse(f"def build({signature}):\n    return tools\n").body[0]


def _call(source: str):
    return ast.parse(source).body[0].value


@pytest.mark.parametrize(
    "call, expected",
    [
        ("build([read], model)", {"tools": "[read]", "model": "model", "extra": "None"}),
        (
            "build(tools=[read], model=runtime(), extra=[write])",
            {"tools": "[read]", "model": "runtime()", "extra": "[write]"},
        ),
        (
            "build([read], model=runtime())",
            {"tools": "[read]", "model": "runtime()", "extra": "None"},
        ),
    ],
)
def test_binds_all_arguments_without_evaluating_ordinary_values(call, expected):
    values = bind_call(_function("tools, model, *, extra=None"), _call(call))
    assert {value.parameter.arg: ast.unparse(value.expr) for value in values} == expected


@pytest.mark.parametrize(
    "signature, call, reason",
    [
        ("tools, model", "build([read])", "required argument 'model'"),
        ("tools, model", "build([read], model, tools=[write])", "more than once"),
        ("tools, model", "build([read], model, model=other)", "more than once"),
        ("tools, model", "build(tools=[read], tools=[write], model=model)", "more than once"),
        ("tools, model", "build([read], model, typo=1)", "unknown"),
        ("tools, model", "build([read], model, 1)", "too many"),
        ("tools, /, model", "build(tools=[read], model=model)", "positional-only"),
        ("tools, *, model", "build([read], model)", "too many"),
        ("tools, *, model", "build([read])", "required argument 'model'"),
        ("tools, model", "build([read], **options)", "through * or **"),
        ("tools, model", "build(*options, model=model)", "through * or **"),
        ("tools, *rest", "build([read])", "variadic"),
        ("tools, **options", "build([read])", "variadic"),
    ],
)
def test_invalid_or_opaque_whole_call_is_not_a_binding(signature, call, reason):
    with pytest.raises(CallLimit, match=re.escape(reason)):
        bind_call(_function(signature), _call(call))


def test_supplied_and_default_arguments_keep_original_ast_nodes():
    function = _function("tools=DEFAULT, model=None")
    call = _call("build(model=runtime())")
    tools, model = bind_call(function, call)
    assert tools.default and tools.expr is function.args.defaults[0]
    assert not model.default and model.expr is call.keywords[0].value


def _module(root: Path, relative: str, source: str, resolver: ImportResolver):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)
    return resolver.entry(path, ast.parse(source), source)


def test_each_caller_retains_its_paired_arguments(tmp_path):
    resolver = ImportResolver(tmp_path)
    module = _module(
        tmp_path, "builders.py", "def build(tools, handoffs):\n    return None\n", resolver
    )
    caller = _module(
        tmp_path,
        "app.py",
        "from builders import build\na = build([read], [reader])\nb = build([write], [writer])\n",
        resolver,
    )
    calls = BuilderCalls(resolver)
    census = calls.callers(module, module.tree.body[0])
    assert not census.limits
    contexts = [calls.invoke(module, module.tree.body[0], site) for site in census.sites]
    assert [context.locations for context in contexts] == [("app.py:2",), ("app.py:3",)]
    assert [[ast.unparse(arg.expr) for arg in context.arguments] for context in contexts] == [
        ["[read]", "[reader]"],
        ["[write]", "[writer]"],
    ]
    assert contexts[0].key != contexts[1].key
    assert contexts[0].site.module.tree is caller.tree


def test_unrelated_text_matches_do_not_expand_the_caller_census(tmp_path, monkeypatch):
    from agents_shipgate.inputs import builder_calls

    monkeypatch.setattr(builder_calls, "MAX_CANDIDATES", 3)
    resolver = ImportResolver(tmp_path)
    module = _module(tmp_path, "builders.py", "def build(tools):\n    return None\n", resolver)
    _module(tmp_path, "app.py", "from builders import build\na = build([read])\n", resolver)
    _module(tmp_path, "utility.py", "# build\nimport json\n", resolver)
    for index in range(40):
        _module(tmp_path, f"noise{index}.py", "# utility\n", resolver)
    census = BuilderCalls(resolver).callers(module, module.tree.body[0])
    assert not census.limits
    assert [site.location for site in census.sites] == ["app.py:2"]


def test_re_export_and_local_import_are_followed_but_shadow_is_not(tmp_path):
    resolver = ImportResolver(tmp_path)
    module = _module(tmp_path, "builders.py", "def build(tools):\n    return None\n", resolver)
    _module(tmp_path, "exports.py", "from builders import build as make\n", resolver)
    _module(
        tmp_path,
        "app.py",
        "from exports import make as construct\na = construct([read])\ndef start():\n    from builders import build\n    return build([write])\ndef shadow(build):\n    return build([other])\n",
        resolver,
    )
    census = BuilderCalls(resolver).callers(module, module.tree.body[0])
    assert not census.limits
    assert [site.location for site in census.sites] == ["app.py:2", "app.py:5"]


@pytest.mark.parametrize("escape", ["callback = build", 'callback = getattr(builders, "build")'])
def test_an_escape_does_not_remove_the_visible_caller(tmp_path, escape):
    resolver = ImportResolver(tmp_path)
    module = _module(tmp_path, "builders.py", "def build(tools):\n    return None\n", resolver)
    _module(
        tmp_path,
        "app.py",
        "import builders\nfrom builders import build\na = build([read])\n" + escape + "\n",
        resolver,
    )
    census = BuilderCalls(resolver).callers(module, module.tree.body[0])
    assert [site.location for site in census.sites] == ["app.py:3"]
    assert census.limits


def test_no_caller_and_test_only_caller_are_named_limits(tmp_path):
    resolver = ImportResolver(tmp_path)
    module = _module(tmp_path, "builders.py", "def build(tools):\n    return None\n", resolver)
    _module(
        tmp_path, "tests/test_app.py", "from builders import build\na = build([read])\n", resolver
    )
    census = BuilderCalls(resolver).callers(module, module.tree.body[0])
    assert not census.sites and "no direct caller" in census.limits[0]


@pytest.mark.parametrize("rebind", ["build = other", "from other import build"])
def test_ambiguous_local_import_preserves_caller_and_names_limit(tmp_path, rebind):
    resolver = ImportResolver(tmp_path)
    module = _module(tmp_path, "builders.py", "def build(tools):\n    return None\n", resolver)
    _module(
        tmp_path,
        "app.py",
        "from builders import build\na = build([read])\ndef start(flag):\n    from builders import build\n    if flag:\n        "
        + rebind
        + "\n    return build([write])\n",
        resolver,
    )
    census = BuilderCalls(resolver).callers(module, module.tree.body[0])
    assert [site.location for site in census.sites] == ["app.py:2"]
    assert any("unresolved reference at app.py:7" in limit for limit in census.limits)


@pytest.mark.parametrize(
    "access", ["getattr(b, selected)([write])", "vars(b)[selected]([write])", "consume(b)"]
)
def test_computed_module_access_preserves_visible_caller(tmp_path, access):
    resolver = ImportResolver(tmp_path)
    module = _module(tmp_path, "builders.py", "def build(tools):\n    return None\n", resolver)
    _module(tmp_path, "app.py", "from builders import build\na = build([read])\n", resolver)
    _module(
        tmp_path,
        "other.py",
        "import builders as b\nselected = 'bu' + 'ild'\nx = " + access + "\n",
        resolver,
    )
    census = BuilderCalls(resolver).callers(module, module.tree.body[0])
    assert [site.location for site in census.sites] == ["app.py:2"]
    assert any("computed access or as a value at other.py:3" in limit for limit in census.limits)


def test_computed_defining_module_access_is_a_limit(tmp_path):
    resolver = ImportResolver(tmp_path)
    module = _module(
        tmp_path,
        "builders.py",
        "def build(tools):\n    return None\nselected = 'bu' + 'ild'\nx = globals()[selected]([write])\n",
        resolver,
    )
    _module(tmp_path, "app.py", "from builders import build\na = build([read])\n", resolver)
    census = BuilderCalls(resolver).callers(module, module.tree.body[0])
    assert [site.location for site in census.sites] == ["app.py:2"]
    assert any("reflection at builders.py:4" in limit for limit in census.limits)


@pytest.mark.parametrize(
    "package_import", ["import pkg", "import pkg.unrelated", "from outer import pkg"]
)
@pytest.mark.parametrize(
    "escape", ["consume(bridge)", "getattr(bridge, selected)", "globals()[selected]"]
)
def test_retained_parent_namespace_escape_cannot_hide_callers(tmp_path, package_import, escape):
    resolver = ImportResolver(tmp_path)
    prefix = "outer/" if package_import.startswith("from outer") else ""
    if prefix:
        _module(tmp_path, "outer/__init__.py", "", resolver)
    _module(tmp_path, prefix + "pkg/__init__.py", "", resolver)
    _module(tmp_path, prefix + "pkg/unrelated.py", "", resolver)
    module = _module(
        tmp_path, prefix + "pkg/registry.py", "def build(tools):\n    return None\n", resolver
    )
    qualified = ("outer." if prefix else "") + "pkg.registry"
    _module(tmp_path, "app.py", f"from {qualified} import build\na = build([read])\n", resolver)
    _module(tmp_path, "bridge.py", package_import + "\n", resolver)
    _module(
        tmp_path, "escape.py", "import bridge\nselected = runtime()\n" + escape + "\n", resolver
    )
    census = BuilderCalls(resolver).callers(module, module.tree.body[0])
    assert [site.location for site in census.sites] == ["app.py:2"]
    assert any("escape.py:3" in limit for limit in census.limits)


def test_direct_call_through_a_retained_namespace_stays_readable(tmp_path):
    resolver = ImportResolver(tmp_path)
    module = _module(tmp_path, "registry.py", "def build(tools):\n    return None\n", resolver)
    _module(tmp_path, "bridge.py", "import registry as lib\n", resolver)
    _module(tmp_path, "app.py", "import bridge\na = bridge.lib.build([read])\n", resolver)
    census = BuilderCalls(resolver).callers(module, module.tree.body[0])
    assert [site.location for site in census.sites] == ["app.py:2"]
    assert census.limits == ()


def test_ambiguous_parent_namespace_is_not_excluded_from_callers(tmp_path):
    resolver = ImportResolver(tmp_path)
    _module(tmp_path, "pkg/__init__.py", "", resolver)
    module = _module(tmp_path, "pkg/registry.py", "def build(tools):\n    return None\n", resolver)
    _module(tmp_path, "app.py", "from pkg.registry import build\na = build([read])\n", resolver)
    _module(tmp_path, "sub/pkg/__init__.py", "", resolver)
    _module(tmp_path, "sub/escape.py", "import pkg\nconsume(pkg)\n", resolver)
    census = BuilderCalls(resolver).callers(module, module.tree.body[0])
    assert [site.location for site in census.sites] == ["app.py:2"]
    assert any("sub/escape.py:2" in limit for limit in census.limits)


def test_forwarded_contexts_keep_tools_and_handoffs_paired(tmp_path):
    resolver = ImportResolver(tmp_path)
    module = _module(
        tmp_path,
        "builders.py",
        "def build(tools, handoffs):\n    return Agent(tools=tools, handoffs=handoffs)\n",
        resolver,
    )
    _module(
        tmp_path,
        "app.py",
        "from builders import build\ndef forward(tools, handoffs):\n    return build(tools, handoffs)\na = forward([read], [reader])\nb = forward([write], [writer])\n",
        resolver,
    )
    calls = BuilderCalls(resolver)
    contexts = calls.contexts(module, module.tree.body[0].body[0].value)
    assert len(contexts) == 2 and not any(context.limits for context in contexts)
    assert [context.invocation.locations for context in contexts] == [
        ("app.py:3", "app.py:4"),
        ("app.py:3", "app.py:5"),
    ]
    parameter = module.tree.body[0].args.args[0]
    assert [context.invocation.argument(parameter)[2].site.location for context in contexts] == [
        "app.py:4",
        "app.py:5",
    ]


def test_invalid_known_call_is_a_separate_context_limit(tmp_path):
    resolver = ImportResolver(tmp_path)
    module = _module(
        tmp_path,
        "builders.py",
        "def build(tools, model):\n    return Agent(tools=tools)\n",
        resolver,
    )
    _module(
        tmp_path,
        "app.py",
        "from builders import build\na = build([read], runtime())\nb = build([write])\n",
        resolver,
    )
    contexts = BuilderCalls(resolver).contexts(module, module.tree.body[0].body[0].value)
    assert any(
        context.invocation and context.invocation.site.location == "app.py:2"
        for context in contexts
    )
    assert any(
        context.limits and "app.py:3: required argument 'model'" in context.limits[0]
        for context in contexts
    )


@pytest.mark.parametrize("signature", ["tools, /, model=None", "tools=DEFAULT, *, model=None"])
def test_default_expression_belongs_to_defining_module_and_actual_to_caller(tmp_path, signature):
    resolver = ImportResolver(tmp_path)
    module = _module(
        tmp_path, "builders.py", f"def build({signature}):\n    return None\n", resolver
    )
    caller = _module(tmp_path, "app.py", "pass\n", resolver)
    function = module.tree.body[0]
    site = CallSite(
        caller,
        _call("build([read], model=runtime())")
        if "/" in signature
        else _call("build(model=runtime())"),
    )
    context = BuilderCalls(resolver).invoke(module, function, site)
    tools = context.argument(
        function.args.posonlyargs[0] if function.args.posonlyargs else function.args.args[0]
    )
    model = context.argument(
        function.args.args[-1] if function.args.posonlyargs else function.args.kwonlyargs[0]
    )
    assert tools[0] is (caller if "/" in signature else module)
    assert model[0] is caller


def test_census_binds_nonmatching_files_and_exclusion_marker_absence(tmp_path):
    resolver = ImportResolver(tmp_path)
    module = _module(tmp_path, "builders.py", "def build(tools):\n    return None\n", resolver)
    _module(tmp_path, "app.py", "from builders import build\na = build([read])\n", resolver)
    _module(tmp_path, "other/unused.py", "value = 1\n", resolver)
    snapshot = StaticInputSnapshot(tmp_path)
    token = activate_static_input_snapshot(snapshot)
    try:
        census = BuilderCalls(resolver).callers(module, module.tree.body[0])
        assert not census.limits
        assert {path.relative_to(tmp_path).as_posix() for path in snapshot.dependency_paths()} >= {
            "builders.py",
            "app.py",
            "other/unused.py",
        }
        assert tmp_path / "other/pyvenv.cfg" in snapshot.absent_dependency_paths()
        (tmp_path / "other/unused.py").write_text(
            "from builders import build\na = build([write])\n"
        )
        with pytest.raises((ValueError, OSError)):
            snapshot.finish()
    finally:
        reset_static_input_snapshot(token)


@pytest.mark.parametrize(
    "returned",
    ["return [read]", "def nested():\n        return None\n    return {'tools': [nested]}"],
)
def test_only_final_return_excludes_nested_function_returns(returned):
    function = ast.parse("def factory():\n    " + returned + "\n").body[0]
    assert single_return(function) is not None


@pytest.mark.parametrize(
    "body",
    [
        "if enabled:\n        return [read]\n    return [write]",
        "yield read",
        "return [read]\n    unreachable()",
    ],
)
def test_multiple_generator_or_nonfinal_returns_are_not_a_factory(body):
    assert single_return(ast.parse("def factory():\n    " + body + "\n").body[0]) is None


@pytest.mark.parametrize(
    "body, reason",
    [
        (
            "from builders import build\ndef forward(tools):\n    return build(tools)\nx = forward(runtime_tools)\n",
            "not bound",
        ),
    ],
)
def test_contexts_keep_dynamic_actuals_for_the_membership_reader(tmp_path, body, reason):
    # Caller indexing does not pretend to evaluate a dynamic value.
    resolver = ImportResolver(tmp_path)
    module = _module(
        tmp_path, "builders.py", "def build(tools):\n    return Agent(tools=tools)\n", resolver
    )
    _module(tmp_path, "app.py", body, resolver)
    contexts = BuilderCalls(resolver).contexts(module, module.tree.body[0].body[0].value)
    assert len(contexts) == 1 and contexts[0].invocation is not None
    assert not contexts[0].limits


def test_recursive_parameter_forwarding_is_a_limit(tmp_path):
    resolver = ImportResolver(tmp_path)
    module = _module(
        tmp_path,
        "builders.py",
        "def build(tools):\n    result = Agent(tools=tools)\n    build(tools)\n    return result\n",
        resolver,
    )
    contexts = BuilderCalls(resolver).contexts(module, module.tree.body[0].body[0].value)
    assert contexts and any(
        "recursive argument flow" in limit for context in contexts for limit in context.limits
    )


def test_context_bound_is_named(tmp_path, monkeypatch):
    from agents_shipgate.inputs import builder_calls

    monkeypatch.setattr(builder_calls, "MAX_CONTEXTS", 1)
    resolver = ImportResolver(tmp_path)
    module = _module(
        tmp_path, "builders.py", "def build(tools):\n    return Agent(tools=tools)\n", resolver
    )
    _module(
        tmp_path,
        "app.py",
        "from builders import build\na = build([read])\nb = build([write])\n",
        resolver,
    )
    contexts = BuilderCalls(resolver).contexts(module, module.tree.body[0].body[0].value)
    assert any(
        "more than 1 caller contexts" in limit for context in contexts for limit in context.limits
    )


def test_new_caller_invalidates_directory_census(tmp_path):
    resolver = ImportResolver(tmp_path)
    module = _module(tmp_path, "builders.py", "def build(tools):\n    return None\n", resolver)
    _module(tmp_path, "app.py", "from builders import build\na = build([read])\n", resolver)
    snapshot = StaticInputSnapshot(tmp_path)
    token = activate_static_input_snapshot(snapshot)
    try:
        assert not BuilderCalls(resolver).callers(module, module.tree.body[0]).limits
        (tmp_path / "new.py").write_text("from builders import build\na = build([write])\n")
        with pytest.raises((ValueError, OSError)):
            snapshot.finish()
    finally:
        reset_static_input_snapshot(token)


def test_total_source_byte_budget_is_a_persistent_named_limit(tmp_path, monkeypatch):
    from agents_shipgate.inputs import builder_calls

    monkeypatch.setattr(builder_calls, "MAX_TOTAL_BYTES", 32)
    resolver = ImportResolver(tmp_path)
    module = _module(
        tmp_path,
        "builders.py",
        "def build(tools):\n    return None\ndef other(tools):\n    return None\n",
        resolver,
    )
    _module(tmp_path, "app.py", "from builders import build\na = build([read])\n", resolver)
    calls = BuilderCalls(resolver)
    for function in module.tree.body:
        census = calls.callers(module, function)
        assert census.limits and "32 source bytes" in census.limits[0]


@pytest.mark.parametrize("kind", ["async", "generator"])
def test_coroutine_or_generator_builders_are_named_limits(tmp_path, kind):
    resolver = ImportResolver(tmp_path)
    source = (
        "async def build(tools):\n    return Agent(tools=tools)\n"
        if kind == "async"
        else "def build(tools):\n    result = Agent(tools=tools)\n    yield result\n"
    )
    module = _module(tmp_path, "builders.py", source, resolver)
    _module(tmp_path, "app.py", "from builders import build\na = build([read])\n", resolver)
    construction = next(node for node in ast.walk(module.tree) if isinstance(node, ast.Call))
    contexts = BuilderCalls(resolver).contexts(module, construction)
    assert contexts and all(context.limits for context in contexts)
    assert any(
        "coroutine or generator builder" in limit
        for context in contexts
        for limit in context.limits
    )
