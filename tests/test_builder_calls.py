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


@pytest.mark.parametrize("refined_first", [False, True])
@pytest.mark.parametrize("write", ["slots['Agent'] = Agent", "slots.update(Agent=Agent)",
                                   "slots.update({'Agent': Agent})", "slots |= {'Agent': Agent}"])
def test_confined_dictionary_data_does_not_change_the_ordinary_caller_census(tmp_path, refined_first, write):
    resolver = ImportResolver(tmp_path)
    module = _module(tmp_path, "builders.py", "def Agent(name, tools):\n    return None\n", resolver)
    _module(tmp_path, "app.py", "from builders import Agent\noriginal = {}\nslots = original\n" + write + "\ndel slots['Agent']\n", resolver)
    calls = BuilderCalls(resolver)
    answers = {}
    for refined in [refined_first, not refined_first, refined_first]:
        answers[refined] = calls.callers(module, module.tree.body[0], allow_empty=True,
                                        confined_dictionary_data=refined)
    assert not answers[True].sites and not answers[True].limits
    assert not answers[False].sites
    assert any("used as a value" in limit for limit in answers[False].limits)
    assert any("named in a string" in limit for limit in answers[False].limits)


@pytest.mark.parametrize("suffix,extra", [
    ("slots['Agent']('Used', [])\n", None),
    ("saved = slots['Agent']\n", None),
    ("slots.get('Agent')\n", None),
    ("consumer(slots)\n", None),
    ("slots.values()\n", None),
    ("slots.copy()\n", None),
    ("def capture():\n    return slots\n", None),
    ("__all__ = ['slots']\n", None),
    ("", "from app import slots as saved\n"),
    ("", "import app as saved\n"),
    ("slots = {}\n", None),
    ("saved = slots.update(Agent=Agent)\n", None),
    ("changes = {'Agent': Agent}\nslots |= changes\n", None),
    ("saved = slots.update\nsaved(Agent=Agent)\n", None),
    ("slots.update(**{'Agent': Agent})\n", None),
    ("slots.update(*[{'Agent': Agent}])\n", None),
    ("slots.update({key(): Agent})\n", None),
    ("slots |= {key(): Agent}\n", None),
    ("slots.update(Agent=produce())\n", None),
    ("slots |= {'Agent': produce()}\n", None),
    ("slots.__ior__({'Agent': Agent})\n", None),
])
def test_dictionary_data_edges_require_complete_confinement(tmp_path, suffix, extra):
    resolver = ImportResolver(tmp_path)
    module = _module(tmp_path, "builders.py", "def Agent(name, tools):\n    return None\n", resolver)
    _module(tmp_path, "app.py", "from builders import Agent\noriginal = {}\nslots = original\nslots['Agent'] = Agent\n" + suffix, resolver)
    if extra is not None:
        _module(tmp_path, "consumer.py", extra, resolver)
    census = BuilderCalls(resolver).callers(module, module.tree.body[0], allow_empty=True,
                                          confined_dictionary_data=True)
    assert census.limits


@pytest.mark.parametrize("refined_first", [False, True])
def test_literal_dictionary_initializer_keeps_each_function_data_edge(tmp_path, refined_first):
    resolver = ImportResolver(tmp_path)
    module = _module(tmp_path, "builders.py", "def Agent(name, tools):\n    return None\n", resolver)
    _module(tmp_path, "app.py", "from builders import Agent\nslots = {'Agent': Agent}\nslots['Agent'] = Agent\ndel slots['Agent']\n", resolver)
    calls = BuilderCalls(resolver)
    answers = {}
    for refined in [refined_first, not refined_first, refined_first]:
        answers[refined] = calls.callers(module, module.tree.body[0], allow_empty=True,
                                        confined_dictionary_data=refined)
    assert not answers[True].sites and not answers[True].limits
    assert not answers[False].sites
    assert any("used as a value" in limit for limit in answers[False].limits)
    assert any("named in a string" in limit for limit in answers[False].limits)


@pytest.mark.parametrize("initializer,suffix", [
    ("{'Agent': Agent}", "saved = slots['Agent']\n"),
    ("{'Agent': Agent}", "slots['Agent']('Used', [])\n"),
    ("{'Agent': Agent}", "def capture():\n    return slots\n"),
    ("{'Agent': Agent}", "__all__ = ['slots']\n"),
    ("{key(): Agent}", ""),
    ("{**opaque, 'Agent': Agent}", ""),
    ("{'Agent': produce(), 'stored': Agent}", ""),
])
def test_literal_dictionary_initializer_requires_complete_data_confinement(tmp_path, initializer, suffix):
    resolver = ImportResolver(tmp_path)
    module = _module(tmp_path, "builders.py", "def Agent(name, tools):\n    return None\n", resolver)
    _module(tmp_path, "app.py", "from builders import Agent\nslots = " + initializer + "\n" + suffix, resolver)
    census = BuilderCalls(resolver).callers(module, module.tree.body[0], allow_empty=True,
                                          confined_dictionary_data=True)
    assert census.limits


@pytest.mark.parametrize("suffix,has_site", [
    ("Agent('Used', [])\n", True),
    ("saved = Agent\n", False),
    ("print('Agent')\n", False),
    ("saved = Agent.__globals__\n", False),
    ("globals()\n", False),
])
def test_confined_dictionary_data_preserves_other_function_edges(tmp_path, suffix, has_site):
    resolver = ImportResolver(tmp_path)
    module = _module(tmp_path, "builders.py", "def Agent(name, tools):\n    return None\n", resolver)
    _module(tmp_path, "app.py", "from builders import Agent\nslots = {}\nslots['Agent'] = Agent\n" + suffix, resolver)
    census = BuilderCalls(resolver).callers(module, module.tree.body[0], allow_empty=True,
                                          confined_dictionary_data=True)
    if has_site:
        assert len(census.sites) == 1
    else:
        assert census.limits


@pytest.mark.parametrize("failure", [None, "candidate_bound", "unread_candidate"])
def test_dictionary_sink_census_retains_its_read_limit(tmp_path, failure):
    from agents_shipgate.inputs.list_expressions import ListExpressions
    from agents_shipgate.inputs.python_imports import ScopeIndex, _external_constructor_use

    resolver = ImportResolver(tmp_path)
    defining = _module(tmp_path, "builders.py", "from agents import Agent as Canonical\ndef Replacement(name, tools):\n    return None\n", resolver)
    module = _module(tmp_path, "helper.py", "import builders\nslots = {}\nslots['Replacement'] = builders.Replacement\n", resolver)
    if failure == "candidate_bound":
        # Exercise the shipped 32-candidate bound, rather than a changed test engine.
        for index in range(33):
            (tmp_path / f"candidate_{index}.py").write_text("# helper\n")
    elif failure == "unread_candidate":
        (tmp_path / "unread.py").write_bytes(b"# helper\n\xff")
    scopes = ScopeIndex(module.tree)
    assert _external_constructor_use(resolver, module, "agents", {defining.path}) is None
    assert resolver._constructor_dictionary_sinks
    calls = BuilderCalls(resolver)
    lists = ListExpressions(ref=module.ref, tree=module.tree, scopes=scopes,
                            bindings=module.bindings, module=module, resolver=resolver,
                            agent_reads=lambda call, keyword: False, builder_calls=calls)
    assert lists.constructor_namespace_changed() is (failure is not None)
    if failure == "candidate_bound":
        assert "exceeds 32 candidate modules" in lists.constructor_namespace_issue
    elif failure == "unread_candidate":
        assert "unread.py could not be read" in lists.constructor_namespace_issue
    else:
        assert lists.constructor_namespace_issue is None


@pytest.mark.parametrize("refined_first", [False, True])
def test_unused_namespace_data_preserves_ordinary_carrier_limits(tmp_path, refined_first):
    resolver = ImportResolver(tmp_path)
    module = _module(tmp_path, "builders.py", "def Agent():\n    return None\n", resolver)
    _module(tmp_path, "app.py", "import builders\nunused = builders\n", resolver)
    calls = BuilderCalls(resolver)
    answers = {}
    for refined in [refined_first, not refined_first, refined_first]:
        answers[refined] = calls.callers(module, module.tree.body[0], allow_empty=True,
                                        unused_namespace_data=refined)
    assert not answers[True].sites and not answers[True].limits
    assert not answers[False].sites and answers[False].limits
    assert any("module is used" in limit for limit in answers[False].limits)
    assert any("retained namespace is used" in limit for limit in answers[False].limits)


@pytest.mark.parametrize("suffix,extra", [
    ("consumer(unused)\n", None),
    ("saved = unused.Agent\n", None),
    ("alias = unused\n", None),
    ("def capture():\n    return unused\n", None),
    ("__all__ = ['unused']\n", None),
    ("unused = {}\n", None),
    ("del unused\n", None),
    ("globals()['unused']\n", None),
    ("", "from app import unused\n"),
    ("", "import app\n"),
])
def test_unused_namespace_data_requires_a_closed_destination(tmp_path, suffix, extra):
    resolver = ImportResolver(tmp_path)
    module = _module(tmp_path, "builders.py", "def Agent():\n    return None\n", resolver)
    _module(tmp_path, "app.py", "import builders\nunused = builders\n" + suffix, resolver)
    if extra is not None:
        _module(tmp_path, "consumer.py", extra, resolver)
    census = BuilderCalls(resolver).callers(module, module.tree.body[0], allow_empty=True,
                                          unused_namespace_data=True)
    assert census.limits


@pytest.mark.parametrize("source", [
    "from builders import Agent\nunused = Agent\n",
    "import builders\nunused = builders.Agent\n",
])
def test_unused_namespace_data_is_not_a_symbol_or_attribute_proof(tmp_path, source):
    resolver = ImportResolver(tmp_path)
    module = _module(tmp_path, "builders.py", "def Agent():\n    return None\n", resolver)
    _module(tmp_path, "app.py", source, resolver)
    census = BuilderCalls(resolver).callers(module, module.tree.body[0], allow_empty=True,
                                          unused_namespace_data=True)
    assert census.limits


@pytest.mark.parametrize("suffix,has_site", [
    ("builders.Agent()\n", True),
    ("print('Agent')\n", False),
    ("saved = builders.Agent\n", False),
    ("saved = builders.Agent.__globals__\n", False),
])
def test_unused_namespace_data_preserves_other_function_uses(tmp_path, suffix, has_site):
    resolver = ImportResolver(tmp_path)
    module = _module(tmp_path, "builders.py", "def Agent():\n    return None\n", resolver)
    _module(tmp_path, "app.py", "import builders\nunused = builders\n" + suffix, resolver)
    census = BuilderCalls(resolver).callers(module, module.tree.body[0], allow_empty=True,
                                          unused_namespace_data=True)
    if has_site:
        assert len(census.sites) == 1
    else:
        assert census.limits


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


def test_namespace_directory_absence_is_read_under_any_letter_case(namespace_workspace, monkeypatch):
    """A directory missing by exact name beside a differently cased one is not proven absent."""
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace)
    (namespace_workspace / "Vendor").mkdir()
    real_exists = Path.exists

    def calls_with_empty_namespace_state():
        calls = BuilderCalls(resolver)
        calls._namespace_entry_work = 0
        calls._namespace_directories = {}
        calls._namespace_directory_names = {}
        return calls

    # What a case-sensitive host answers for the spelling asked about.
    monkeypatch.setattr(Path, "exists", lambda self, **kw: False if self.name == "vendor" else real_exists(self, **kw))
    with pytest.raises(CallLimit, match="unread directory evidence"):
        calls_with_empty_namespace_state()._namespace_directory_entries(namespace_workspace / "vendor")
    # No listing entry matches this spelling under any case: a proven absence.
    monkeypatch.setattr(Path, "exists", lambda self, **kw: False if self.name == "other" else real_exists(self, **kw))
    assert calls_with_empty_namespace_state()._namespace_directory_entries(namespace_workspace / "other") is None


def test_a_differently_cased_virtualenv_marker_does_not_hide_a_directory_on_any_host(tmp_path):
    """Presence of ``pyvenv.cfg`` selects the code read, so it comes from the listing."""
    resolver = ImportResolver(tmp_path)
    _module(tmp_path, "builders.py", "def build(tools):\n    return None\n", resolver)
    (tmp_path / "environment").mkdir()
    (tmp_path / "environment/caller.py").write_text("from builders import build\nbuild([])\n")
    (tmp_path / "environment/PyVenv.cfg").write_text("home = elsewhere\n")
    (tmp_path / "real").mkdir()
    (tmp_path / "real/caller.py").write_text("from builders import build\nbuild([])\n")
    (tmp_path / "real/pyvenv.cfg").write_text("home = elsewhere\n")
    inventory = {path.relative_to(tmp_path).as_posix() for path in BuilderCalls(resolver)._inventory()}
    assert "environment/caller.py" in inventory  # Not a virtual environment: it is read.
    assert "real/caller.py" not in inventory  # The exact spelling is one: it is skipped.


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


@pytest.mark.parametrize('namespace_first', [False, True])
def test_function_namespace_census_does_not_replace_shared_list_census(tmp_path, namespace_first):
    resolver = ImportResolver(tmp_path)
    module = _module(tmp_path, 'tools.py', 'def read():\n    return None\n', resolver)
    _module(tmp_path, 'registry.py', 'from tools import read\ndef anchor():\n    return None\n', resolver)
    _module(tmp_path, 'bridge.py', 'from registry import anchor\ndef carrier():\n    return None\n', resolver)
    _module(tmp_path, 'sibling.py', 'from bridge import carrier\nconsume(carrier)\n', resolver)
    calls = BuilderCalls(resolver)
    results = {}
    for namespace in [namespace_first, not namespace_first]:
        results[namespace] = calls.retaining_modules(module, 'read', namespace_carriers=namespace)
    assert tmp_path / 'sibling.py' in results[True]
    assert tmp_path / 'sibling.py' not in results[False]
    assert calls.retaining_modules(module, 'read') == results[False]


@pytest.fixture
def namespace_workspace(tmp_path):
    from tests.test_constructor_dependency_ownership import _git

    _git(tmp_path, "init", "-q")
    return tmp_path


@pytest.mark.parametrize("family,package", [("agents", "agents"), ("agents", "openai_agents"),
                                           ("google.adk", "google.adk.agents")])
def test_namespace_source_context_reads_quiet_and_imported_excluded_code(namespace_workspace, family, package):
    (namespace_workspace / "entry.py").write_text(f"import {package} as framework\nfrom tests import hooks\n")
    (namespace_workspace / "quiet.py").write_text("VALUE = None\n")
    (namespace_workspace / "tests").mkdir()
    (namespace_workspace / "tests/__init__.py").write_text("VALUE = None\n")
    (namespace_workspace / "tests/hooks.py").write_text("VALUE = None\n")
    calls = BuilderCalls(ImportResolver(namespace_workspace))
    context = calls.namespace_source_context(family)
    assert {module.ref for module in context.modules} == {
        "entry.py", "quiet.py", "tests/__init__.py", "tests/hooks.py",
    }
    assert context.external_imports == (f"entry.py:1: {package}",)
    assert calls._read_bytes == sum(len(module.text.encode("utf-8")) for module in context.modules)
    assert not calls._censuses


@pytest.mark.parametrize("expanded", [False, True])
@pytest.mark.parametrize("count", [32, 33])
def test_namespace_source_context_bounds_the_complete_graph(namespace_workspace, expanded, count):
    if expanded:
        (namespace_workspace / "tests").mkdir()
        (namespace_workspace / "tests/__init__.py").write_text("")
        (namespace_workspace / "entry.py").write_text("from tests import chain\n")
        for index in range(count - 2):
            name = "chain" if index == 0 else f"step{index}"
            tail = f"from . import step{index + 1}\n" if index < count - 3 else "VALUE = None\n"
            (namespace_workspace / "tests" / f"{name}.py").write_text(tail)
    else:
        for index in range(count):
            (namespace_workspace / f"quiet{index}.py").write_text("VALUE = None\n")
    calls = BuilderCalls(ImportResolver(namespace_workspace))
    if count == 33:
        with pytest.raises(CallLimit, match="exceeds 32 total modules"):
            calls.namespace_source_context("agents")
    else:
        assert len(calls.namespace_source_context("agents").modules) == count


@pytest.mark.parametrize("problem,reason", [
    ("unknown", "module 'unknown'"),
    ("relative", "missing"),
    ("namespace", "no readable module"),
    ("wildcard", "wildcard"),
    ("utf8", "could not be read"),
    ("link", "symbolic link"),
    ("syntax", "could not be read or parsed"),
])
def test_namespace_source_context_names_unread_inputs(namespace_workspace, problem, reason):
    (namespace_workspace / "entry.py").write_text("VALUE = None\n")
    (namespace_workspace / "tests").mkdir()
    (namespace_workspace / "tests/__init__.py").write_text("")
    if problem == "unknown":
        (namespace_workspace / "entry.py").write_text("import unknown\n")
    elif problem == "relative":
        (namespace_workspace / "entry.py").write_text("from tests import hooks\n")
        (namespace_workspace / "tests/hooks.py").write_text("from .missing import value\n")
    elif problem == "namespace":
        (namespace_workspace / "portion").mkdir()
        (namespace_workspace / "entry.py").write_text("import portion\n")
    elif problem == "wildcard":
        (namespace_workspace / "entry.py").write_text("from tests import *\n")
    else:
        (namespace_workspace / "entry.py").write_text("from tests import hooks\n")
        if problem == "utf8":
            (namespace_workspace / "tests/hooks.py").write_bytes(b"\xff")
        elif problem == "link":
            (namespace_workspace / "tests/hooks.py").symlink_to("../entry.py")
        else:
            (namespace_workspace / "tests/hooks.py").write_text("def broken(:\n")
    with pytest.raises(CallLimit, match=reason):
        BuilderCalls(ImportResolver(namespace_workspace)).namespace_source_context("agents")


def test_namespace_source_context_keeps_an_aggregate_syntax_bound(namespace_workspace):
    (namespace_workspace / "quiet.py").write_text("VALUE = None\n" * 6000)
    with pytest.raises(CallLimit, match="exceeds 20000 syntax nodes"):
        BuilderCalls(ImportResolver(namespace_workspace)).namespace_source_context("agents")


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("extra", [{}, {"quiet.py": "import unavailable\n"}])
def test_namespace_source_context_never_grants_copy_or_function_authority(namespace_workspace, framework, extra):
    from tests.test_builder_bindings import _namespace_mutation_observations

    built, warnings = _namespace_mutation_observations(
        namespace_workspace, framework, "slots = dict(vars(framework))\nslots['Agent'] = fake.Agent\n", extra,
    )
    assert warnings
    assert all(not item.tools_complete and item.issues for item in built)


@pytest.mark.parametrize("family,package", [("agents", "agents"), ("google.adk", "google.adk.agents")])
@pytest.mark.parametrize("unread", [False, True])
def test_namespace_source_context_integration_keeps_reflection_refusal(namespace_workspace, family, package, unread):
    from agents_shipgate.inputs.python_imports import (
        _external_constructor_use,
        _namespace_copy_source_limit,
    )

    source = f"import {package} as framework\nslots = dict(vars(framework))\n"
    path = namespace_workspace / "entry.py"
    path.write_text(source)
    if unread:
        (namespace_workspace / "quiet.py").write_text("import unavailable\n")
    resolver = ImportResolver(namespace_workspace)
    module = resolver.entry(path, ast.parse(source), source)
    limit = _namespace_copy_source_limit(resolver, module, family)
    answer = _external_constructor_use(resolver, module, family, {path})
    if unread:
        assert limit and "quiet.py" in limit and "unavailable" in limit
        assert answer == limit
    else:
        assert limit is None
        assert answer and "reflective" in answer
    assert not resolver._constructor_namespace_owners
    assert not resolver._constructor_dictionary_sinks
    assert not resolver._constructor_dictionary_class_sinks


def test_namespace_source_context_keeps_an_aggregate_byte_bound(namespace_workspace):
    for index in range(9):
        (namespace_workspace / f"quiet{index}.py").write_text("#" + "x" * (4 * 1024 * 1024) + "\n")
    with pytest.raises(CallLimit, match="exceeds 33554432 source bytes"):
        BuilderCalls(ImportResolver(namespace_workspace)).namespace_source_context("agents")


def test_namespace_source_context_names_its_selected_scope_limit(namespace_workspace):
    (namespace_workspace / "__init__.py").write_text("VALUE = None\n")
    scoped = namespace_workspace / "selected"
    scoped.mkdir()
    (scoped / "entry.py").write_text("VALUE = None\n")
    with pytest.raises(CallLimit, match="selected.*above the read scope"):
        BuilderCalls(ImportResolver(scoped)).namespace_source_context("agents")


def test_namespace_source_context_does_not_pair_cached_syntax_with_new_bytes(namespace_workspace):
    path = namespace_workspace / "entry.py"
    old = "VALUE = None\n"
    path.write_text(old)
    resolver = ImportResolver(namespace_workspace)
    resolver.entry(path, ast.parse(old), old)
    path.write_text("import unavailable\n")
    with pytest.raises(CallLimit, match="entry.py changed after its parsed namespace source was read"):
        BuilderCalls(resolver).namespace_source_context("agents")


@pytest.mark.parametrize("source", [
    "__import__('unread_external')\n",
    "saved = __import__\n",
    "import importlib as loader\nloader.import_module('unread_external')\n",
    "from builtins import __import__ as loader\nloader('unread_external')\n",
])
def test_namespace_source_context_refuses_dynamic_importers(namespace_workspace, source):
    (namespace_workspace / "quiet.py").write_text(source)
    with pytest.raises(CallLimit, match="quiet.py:1.*dynamic import machinery"):
        BuilderCalls(ImportResolver(namespace_workspace)).namespace_source_context("agents")


@pytest.mark.parametrize("order", ["portion, local", "local, portion"])
def test_namespace_source_context_checks_each_grouped_import_alias(namespace_workspace, order):
    (namespace_workspace / "portion").mkdir()
    (namespace_workspace / "local.py").write_text("VALUE = None\n")
    (namespace_workspace / "entry.py").write_text(f"import {order}\n")
    with pytest.raises(CallLimit, match="imports 'portion'.*no readable module"):
        BuilderCalls(ImportResolver(namespace_workspace)).namespace_source_context("agents")


@pytest.mark.parametrize("family,package", [("agents", "agents"), ("agents", "openai_agents"),
                                           ("google.adk", "google.adk.agents")])
@pytest.mark.parametrize("order", ["canonical_first", "local_first"])
def test_namespace_source_context_attributes_grouped_external_boundaries(namespace_workspace, family, package, order):
    aliases = [f"{package} as framework", "local"]
    if order == "local_first":
        aliases.reverse()
    (namespace_workspace / "entry.py").write_text("import " + ", ".join(aliases) + "\n")
    (namespace_workspace / "local.py").write_text("VALUE = None\n")
    context = BuilderCalls(ImportResolver(namespace_workspace)).namespace_source_context(family)
    assert {module.ref for module in context.modules} == {"entry.py", "local.py"}
    assert context.external_imports == (f"entry.py:1: {package}",)


def test_namespace_source_context_reads_a_scope_with_proven_absent_initializers(namespace_workspace):
    selected = namespace_workspace / "selected"
    selected.mkdir()
    (selected / "entry.py").write_text("VALUE = None\n")
    context = BuilderCalls(ImportResolver(selected)).namespace_source_context("agents")
    assert [module.ref for module in context.modules] == ["entry.py"]
    assert not context.external_imports


@pytest.mark.parametrize("kind", ["module", "namespace", "src_module", "src_namespace", "case", "linked"])
def test_namespace_source_context_retains_above_scope_import_candidates(namespace_workspace, kind):
    selected = namespace_workspace / "selected"
    selected.mkdir()
    (selected / "entry.py").write_text("import common\n")
    root = namespace_workspace
    if kind.startswith("src_"):
        root = namespace_workspace / "src"
        root.mkdir()
    if kind.endswith("namespace"):
        (root / "common").mkdir()
    elif kind == "case":
        (root / "Common.py").write_text("VALUE = None\n")
    elif kind == "linked":
        (selected / "target.py").write_text("VALUE = None\n")
        (root / "common.py").symlink_to("selected/target.py")
    else:
        (root / "common.py").write_text("VALUE = None\n")
    with pytest.raises(CallLimit, match="common.*above the read scope"):
        BuilderCalls(ImportResolver(selected)).namespace_source_context("agents")


@pytest.mark.parametrize("kind", ["plain", "case", "linked"])
def test_namespace_source_context_refuses_ancestor_initializer_shapes(namespace_workspace, kind):
    selected = namespace_workspace / "selected"
    selected.mkdir()
    (selected / "entry.py").write_text("VALUE = None\n")
    name = "__INIT__.PY" if kind == "case" else "__init__.py"
    if kind == "linked":
        (namespace_workspace / name).symlink_to("selected/entry.py")
    else:
        (namespace_workspace / name).write_text("VALUE = None\n")
    with pytest.raises(CallLimit, match="initializer.*above.*read scope"):
        BuilderCalls(ImportResolver(selected)).namespace_source_context("agents")


def test_namespace_source_context_does_not_use_an_unbound_layout_as_disk_evidence(namespace_workspace):
    from agents_shipgate.inputs.python_imports import RepositoryLayout, repository_layout

    selected = namespace_workspace / "selected"
    selected.mkdir()
    (selected / "entry.py").write_text("VALUE = None\n")
    with repository_layout(RepositoryLayout("selected", lambda path: frozenset())):
        resolver = ImportResolver(selected)
    with pytest.raises(CallLimit, match="no bound disk ancestry"):
        BuilderCalls(resolver).namespace_source_context("agents")


def test_namespace_source_context_refuses_ancestry_outside_active_snapshot(namespace_workspace):
    selected = namespace_workspace / "selected"
    selected.mkdir()
    (selected / "entry.py").write_text("VALUE = None\n")
    snapshot = StaticInputSnapshot(selected)
    token = activate_static_input_snapshot(snapshot)
    try:
        with pytest.raises(CallLimit, match="outside the bound input snapshot"):
            BuilderCalls(ImportResolver(selected)).namespace_source_context("agents")
    finally:
        reset_static_input_snapshot(token)


def test_namespace_source_context_binds_ancestor_absence_to_the_repository_snapshot(namespace_workspace):
    from agents_shipgate.core.input_directory_identity import validate_directory_inputs
    from agents_shipgate.core.verification_identity import (
        build_verification_plan,
        validate_dependency_inputs,
    )
    from tests.test_constructor_dependency_ownership import _commit

    selected = namespace_workspace / "selected"
    selected.mkdir()
    (selected / "entry.py").write_text("VALUE = None\n")
    (namespace_workspace / "shipgate.yaml").write_text("agent:\n  name: namespace-fixture\n")
    _commit(namespace_workspace, {})
    snapshot = StaticInputSnapshot(namespace_workspace)
    token = activate_static_input_snapshot(snapshot)
    try:
        context = BuilderCalls(ImportResolver(selected)).namespace_source_context("agents")
        assert [module.ref for module in context.modules] == ["entry.py"]
        plan = build_verification_plan(
            git_root=namespace_workspace, input_root=namespace_workspace,
            config_path=namespace_workspace / "shipgate.yaml", config_logical_path="shipgate.yaml",
            base_ref=None, head_ref="HEAD", archived_head=False,
            repository_id="https://example.test/namespace-fixture.git",
            base_commit_sha=None, base_tree_sha=None, head_commit_sha=None,
            head_tree_sha=None, merge_base_sha=None, changed_files=[], diff_text="",
            baseline_path=None, diff_from_path=None, policy_pack_paths=[],
            evaluation_date="2026-10-05", options={}, plugins_enabled=False,
            captured_input_paths=snapshot.paths(),
        )
        snapshot.finish()
    finally:
        reset_static_input_snapshot(token)
    validate_directory_inputs(plan, snapshot=StaticInputSnapshot(namespace_workspace))
    validate_dependency_inputs(plan, root=namespace_workspace)
    (namespace_workspace / "__init__.py").write_text("VALUE = None\n")
    with pytest.raises(ValueError):
        validate_directory_inputs(plan, snapshot=StaticInputSnapshot(namespace_workspace))
    with pytest.raises(ValueError):
        validate_dependency_inputs(plan, root=namespace_workspace)


def _source_slot_fixture(root, extra="", definition="def Agent(tools):\n    return tools\n"):
    (root / "fake.py").write_text(definition)
    source = "import fake\nimport fake as other\nother.Agent = fake.Agent\n" + extra
    (root / "entry.py").write_text(source)
    resolver = ImportResolver(root)
    module = resolver.entry(root / "entry.py", ast.parse(source), source)
    statement = module.tree.body[2]
    return resolver, module, statement


@pytest.mark.parametrize("family", ["agents", "google.adk"])
def test_source_slot_proof_keeps_ordinary_caller_cache_separate(namespace_workspace, family):
    resolver, module, statement = _source_slot_fixture(namespace_workspace)
    calls = BuilderCalls(resolver)
    home, function, _ = calls._source_slot_candidate(module, statement)
    before = calls.callers(home, function, allow_empty=True)
    assert before.limits
    assert calls.idempotent_source_slot(module, statement, family)
    assert calls.uncalled_source_slot(home, function)
    assert calls.callers(home, function, allow_empty=True) is before
    assert before.limits and not before.sites
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("extra", [
    "fake.Agent([])\n",
    "saved = fake.Agent\n",
    "fake.Agent = replacement\n",
    "del fake.Agent\n",
    "unknown.Agent = replacement\n",
    "setattr(fake, 'Agent', replacement)\n",
    "saved = vars(fake)\n",
    "saved = fake.Agent.__globals__\n",
    "import sys\nsys.modules['fake'] = replacement\n",
    "import builtins\nbuiltins.dict = replacement\n",
    "def __getattr__(name):\n    return replacement\n",
])
def test_source_slot_proof_refuses_other_calls_escapes_and_mutations(namespace_workspace, extra):
    resolver, module, statement = _source_slot_fixture(namespace_workspace, extra)
    calls = BuilderCalls(resolver)
    home, function, _ = calls._source_slot_candidate(module, statement)
    with pytest.raises(CallLimit):
        calls.idempotent_source_slot(module, statement, "agents")
    assert not calls.uncalled_source_slot(home, function)
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("definition", [
    "@decorator\ndef Agent(tools):\n    return tools\n",
    "async def Agent(tools):\n    return tools\n",
    "class Agent:\n    pass\n",
])
def test_source_slot_proof_requires_the_exact_plain_source_function(namespace_workspace, definition):
    resolver, module, statement = _source_slot_fixture(namespace_workspace, definition=definition)
    assert not BuilderCalls(resolver).idempotent_source_slot(module, statement, "agents")
    assert not resolver._checking_source_module_slot


def test_source_slot_proof_checks_sibling_primitive_changes(namespace_workspace):
    resolver, module, statement = _source_slot_fixture(namespace_workspace)
    (namespace_workspace / "quiet.py").write_text("import builtins\nbuiltins.dict = replacement\n")
    with pytest.raises(CallLimit):
        BuilderCalls(resolver).idempotent_source_slot(module, statement, "agents")
    assert not resolver._checking_source_module_slot


def test_source_slot_proof_rechecks_source_after_a_previous_success(namespace_workspace):
    resolver, module, statement = _source_slot_fixture(namespace_workspace)
    calls = BuilderCalls(resolver)
    assert calls.idempotent_source_slot(module, statement, "agents")
    (namespace_workspace / "fake.py").write_text("def Agent(tools):\n    return replacement\n")
    with pytest.raises(CallLimit):
        calls.idempotent_source_slot(module, statement, "agents")
    assert not resolver._checking_source_module_slot


def test_source_slot_proof_does_not_clear_an_active_reentry_guard(namespace_workspace):
    resolver, module, statement = _source_slot_fixture(namespace_workspace)
    resolver._checking_source_module_slot = True
    with pytest.raises(CallLimit, match="reentered"):
        BuilderCalls(resolver).idempotent_source_slot(module, statement, "agents")
    assert resolver._checking_source_module_slot


def test_namespace_directory_capture_counts_unique_entries_and_rechecks_currency(namespace_workspace):
    selected = namespace_workspace / "selected"
    selected.mkdir()
    (selected / "entry.py").write_text("import agents\n" * 24)
    for number in range(4200):
        (namespace_workspace / f"data_{number}.txt").touch()
    calls = BuilderCalls(ImportResolver(selected))
    assert len(calls.namespace_source_context("agents").modules) == 1
    assert calls._namespace_entry_work < 10000
    (namespace_workspace / "agents.py").write_text("VALUE = None\n")
    with pytest.raises(CallLimit, match="changed after"):
        calls._namespace_directory_currency()


def _source_slot_alias_fixture(root, declaration="other = fake", *, local=False, extra="", prefix=""):
    (root / "fake.py").write_text("def Agent(tools):\n    return tools\n")
    body = "import fake\n" + declaration + "\nother.Agent = fake.Agent\n" + extra
    source = prefix + ("def touch():\n" + "".join("    " + line + "\n" for line in body.splitlines())
                       if local else body)
    (root / "entry.py").write_text(source)
    resolver = ImportResolver(root)
    module = resolver.entry(root / "entry.py", ast.parse(source), source)
    statements = module.tree.body[-1].body if local else module.tree.body[len(ast.parse(prefix).body):]
    return resolver, module, statements[1].value, statements[2]


@pytest.mark.parametrize("family", ["agents", "google.adk"])
@pytest.mark.parametrize("local", [False, True])
@pytest.mark.parametrize("declaration", ["other = fake", "other: object = fake"])
def test_source_slot_alias_proof_binds_initializer_and_slot_without_cache_grants(
    namespace_workspace, family, local, declaration,
):
    resolver, module, initializer, terminal = _source_slot_alias_fixture(
        namespace_workspace, declaration, local=local,
    )
    calls = BuilderCalls(resolver)
    home, function, _ = calls._source_slot_candidate(module, terminal)
    before = calls.callers(home, function, allow_empty=True)
    assert before.limits
    assert calls.idempotent_source_slot(module, initializer, family)
    assert calls.idempotent_source_slot(module, terminal, family)
    assert calls.uncalled_source_slot(home, function)
    assert calls.callers(home, function, allow_empty=True) is before
    assert before.limits and not before.sites
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("extra", [
    "other.Agent([])\n", "fake.Agent([])\n", "saved = other.Agent\n", "saved = fake.Agent\n",
    "saved = other\n", "other.unrelated = None\n", "other = fake\n", "del other\n",
    "def retain():\n    return other\n", "def retain(value=other):\n    pass\n",
    "def retain(value: other):\n    pass\n", "__all__ = ['other']\n",
    "other['Agent'] = replacement\n", "setattr(other, 'Agent', replacement)\n",
    "fake.Agent = replacement\n", "unknown.Agent = replacement\n",
])
def test_source_slot_alias_proof_refuses_calls_captures_rebinding_and_other_writes(namespace_workspace, extra):
    resolver, module, initializer, terminal = _source_slot_alias_fixture(namespace_workspace, extra=extra)
    calls = BuilderCalls(resolver)
    for node in (initializer, terminal):
        try:
            assert not calls.idempotent_source_slot(module, node, "agents")
        except CallLimit:
            pass
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("annotation", ["object()", "type", "object[int]", "builtins.object", "'object'", "Custom"])
def test_source_slot_alias_annotation_is_only_the_exact_builtin_name(namespace_workspace, annotation):
    resolver, module, initializer, terminal = _source_slot_alias_fixture(
        namespace_workspace, f"other: {annotation} = fake",
    )
    calls = BuilderCalls(resolver)
    assert not calls.idempotent_source_slot(module, initializer, "agents")
    assert not calls.idempotent_source_slot(module, terminal, "agents")


@pytest.mark.parametrize("local", [False, True])
def test_source_slot_alias_annotation_cannot_borrow_a_shadowed_object(namespace_workspace, local):
    resolver, module, initializer, terminal = _source_slot_alias_fixture(
        namespace_workspace, "other: object = fake", local=local, prefix="object = replacement\n",
    )
    assert not BuilderCalls(resolver).idempotent_source_slot(module, initializer, "agents")
    assert not BuilderCalls(resolver).idempotent_source_slot(module, terminal, "agents")


def test_source_slot_alias_annotation_rechecks_sibling_builtin_writes(namespace_workspace):
    resolver, module, initializer, terminal = _source_slot_alias_fixture(
        namespace_workspace, "other: object = fake",
    )
    (namespace_workspace / "quiet.py").write_text("import builtins\nbuiltins.object = replacement\n")
    for node in (initializer, terminal):
        with pytest.raises(CallLimit):
            BuilderCalls(resolver).idempotent_source_slot(module, node, "agents")


def test_source_slot_alias_proof_does_not_allow_importing_the_namespace_owner(namespace_workspace):
    resolver, module, initializer, terminal = _source_slot_alias_fixture(namespace_workspace)
    (namespace_workspace / "consumer.py").write_text("from entry import other\n")
    for node in (initializer, terminal):
        with pytest.raises(CallLimit, match="imported source-slot namespace alias"):
            BuilderCalls(resolver).idempotent_source_slot(module, node, "agents")


def test_source_slot_alias_proof_rechecks_new_alias_uses_after_success(namespace_workspace):
    resolver, module, initializer, terminal = _source_slot_alias_fixture(namespace_workspace)
    calls = BuilderCalls(resolver)
    assert calls.idempotent_source_slot(module, initializer, "agents")
    path = namespace_workspace / "entry.py"
    path.write_text(path.read_text() + "saved = other\n")
    for node in (initializer, terminal):
        with pytest.raises(CallLimit):
            calls.idempotent_source_slot(module, node, "agents")


@pytest.mark.parametrize("shape", ["distinct", "rhs_only", "shared"])
def test_source_slot_alias_route_requires_one_shared_or_target_initializer(namespace_workspace, shape):
    (namespace_workspace / "fake.py").write_text("def Agent(tools):\n    return tools\n")
    bodies = {
        "distinct": "left = fake\nright = fake\nleft.Agent = right.Agent\n",
        "rhs_only": "right = fake\nfake.Agent = right.Agent\n",
        "shared": "left = fake\nleft.Agent = left.Agent\n",
    }
    source = "import fake\n" + bodies[shape]
    path = namespace_workspace / "entry.py"
    path.write_text(source)
    resolver = ImportResolver(namespace_workspace)
    module = resolver.entry(path, ast.parse(source), source)
    calls = BuilderCalls(resolver)
    terminal = module.tree.body[-1]
    initializers = [statement.value for statement in module.tree.body[1:-1]]
    for node in [terminal, *initializers]:
        assert calls.idempotent_source_slot(module, node, "agents") is (shape == "shared")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("package", [False, True])
@pytest.mark.parametrize("where", ["home", "quiet"])
@pytest.mark.parametrize("alias", [False, True])
def test_source_slot_proof_refuses_raw_path_markers_in_every_source_module(
    namespace_workspace, package, where, alias,
):
    if package:
        (namespace_workspace / "fake").mkdir()
    home = namespace_workspace / ("fake/__init__.py" if package else "fake.py")
    home.write_text("def Agent(tools):\n    return tools\n")
    changed = home if where == "home" else namespace_workspace / "quiet.py"
    changed.write_text(changed.read_text() + "__path__ = []\n" if changed.exists() else "__path__ = []\n")
    source = "import fake\n" + ("other = fake\nother.Agent = fake.Agent\n" if alias
                                else "import fake as other\nother.Agent = fake.Agent\n")
    path = namespace_workspace / "entry.py"
    path.write_text(source)
    resolver = ImportResolver(namespace_workspace)
    module = resolver.entry(path, ast.parse(source), source)
    calls = BuilderCalls(resolver)
    nodes = [module.tree.body[-1]]
    if alias:
        nodes.append(module.tree.body[1].value)
    for node in nodes:
        with pytest.raises(CallLimit, match="raw package-path or module-namespace mutation markers"):
            calls.idempotent_source_slot(module, node, "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("write", [
    "slots.__init__(Agent=fake.Agent)",
    "slots.__init__({'Agent': fake.Agent})",
])
@pytest.mark.parametrize("alias", [False, True])
def test_bound_dictionary_initialization_keeps_exact_data_and_ordinary_census(
    namespace_workspace, write, alias,
):
    resolver = ImportResolver(namespace_workspace)
    home = _module(namespace_workspace, "fake.py", "def Agent(tools):\n    return tools\n", resolver)
    declaration = "original = {}\nslots = original\n" if alias else "slots = {}\n"
    module = _module(namespace_workspace, "entry.py", "import fake\n" + declaration + write + "\n", resolver)
    calls = BuilderCalls(resolver)
    payload = next(node for node in ast.walk(module.tree)
                   if isinstance(node, ast.Attribute) and node.attr == "Agent")
    before = calls.callers(home, home.tree.body[0], allow_empty=True)
    assert before.limits
    assert payload in calls._confined_dictionary_data(module).values
    refined = calls.callers(home, home.tree.body[0], allow_empty=True, confined_dictionary_data=True)
    assert not refined.sites and not refined.limits
    assert calls.callers(home, home.tree.body[0], allow_empty=True) is before
    assert before.limits


@pytest.mark.parametrize("write,suffix,consumer", [
    ("slots.__init__(Agent=fake.Agent)", "saved = slots['Agent']\n", None),
    ("slots.__init__(Agent=fake.Agent)", "slots['Agent']([])\n", None),
    ("slots.__init__(Agent=fake.Agent)", "saved = slots\nconsumer(saved)\n", None),
    ("slots.__init__(Agent=fake.Agent)", "def capture():\n    return slots\n", None),
    ("slots.__init__(Agent=fake.Agent)", "__all__ = ['slots']\n", None),
    ("slots.__init__(Agent=fake.Agent)", "slots = {}\n", None),
    ("slots.__init__(Agent=fake.Agent)", "del slots\n", None),
    ("slots.__init__(Agent=fake.Agent)", "", "from entry import slots\n"),
    ("slots.__init__(Agent=fake.Agent)", "", "import entry\n"),
    ("saved = slots.__init__(Agent=fake.Agent)", "", None),
    ("saved = slots.__init__\nsaved(Agent=fake.Agent)", "", None),
    ("slots.__init__(opaque, Agent=fake.Agent)", "", None),
    ("slots.__init__(*opaque, Agent=fake.Agent)", "", None),
    ("slots.__init__(**{'Agent': fake.Agent})", "", None),
    ("slots.__init__({'Agent': fake.Agent}, opaque)", "", None),
    ("slots.__init__({'Agent': fake.Agent}, other=fake.Agent)", "", None),
    ("slots.__init__({key(): fake.Agent})", "", None),
    ("slots.__init__({**opaque, 'Agent': fake.Agent})", "", None),
    ("slots.__init__(__class__=fake.Agent)", "", None),
    ("slots.__init__({'__class__': fake.Agent})", "", None),
    ("dict.__init__(slots, Agent=fake.Agent)", "", None),
    ("builtins.dict.__init__(slots, Agent=fake.Agent)", "", None),
])
def test_bound_dictionary_initialization_refuses_reads_escapes_and_opaque_protocols(
    namespace_workspace, write, suffix, consumer,
):
    resolver = ImportResolver(namespace_workspace)
    home = _module(namespace_workspace, "fake.py", "def Agent(tools):\n    return tools\n", resolver)
    module = _module(namespace_workspace, "entry.py", "import fake\nslots = {}\n" + write + "\n" + suffix, resolver)
    if consumer is not None:
        _module(namespace_workspace, "consumer.py", consumer, resolver)
    calls = BuilderCalls(resolver)
    payloads = [node for node in ast.walk(module.tree)
                if isinstance(node, ast.Attribute) and node.attr == "Agent"]
    assert payloads and all(node not in calls._confined_dictionary_data(module).values for node in payloads)
    census = calls.callers(home, home.tree.body[0], allow_empty=True, confined_dictionary_data=True)
    assert census.limits


@pytest.mark.parametrize("allocation", ["vars(fake)", "fake.__dict__", "dict(vars(fake))", "unknown()", "imported"])
def test_bound_dictionary_initialization_cannot_borrow_a_namespace_or_unknown_receiver(namespace_workspace, allocation):
    resolver = ImportResolver(namespace_workspace)
    _module(namespace_workspace, "fake.py", "def Agent(tools):\n    return tools\n", resolver)
    module = _module(namespace_workspace, "entry.py", "import fake\nslots = " + allocation + "\nslots.__init__(Agent=fake.Agent)\n", resolver)
    assert not BuilderCalls(resolver)._confined_dictionary_data(module).values


@pytest.mark.parametrize("suffix,has_site", [("fake.Agent([])\n", True), ("saved = fake.Agent\n", False)])
def test_bound_dictionary_initialization_preserves_neighboring_function_uses(namespace_workspace, suffix, has_site):
    resolver = ImportResolver(namespace_workspace)
    home = _module(namespace_workspace, "fake.py", "def Agent(tools):\n    return tools\n", resolver)
    _module(namespace_workspace, "entry.py", "import fake\nslots = {}\nslots.__init__(Agent=fake.Agent)\n" + suffix, resolver)
    census = BuilderCalls(resolver).callers(home, home.tree.body[0], allow_empty=True, confined_dictionary_data=True)
    assert bool(census.sites) if has_site else bool(census.limits)


def _source_dictionary_slot_fixture(root, *, write="fake.__dict__['Agent'] = fake.Agent", extra="", home="def Agent(tools):\n    return tools\n"):
    (root / "fake.py").write_text(home)
    source = "import fake\n" + write + "\n" + extra
    path = root / "entry.py"
    path.write_text(source)
    resolver = ImportResolver(root)
    module = resolver.entry(path, ast.parse(source), source)
    return resolver, module, module.tree.body[1]


@pytest.mark.parametrize("family", ["agents", "google.adk"])
def test_source_dictionary_slot_keeps_projection_key_and_function_roles_distinct(namespace_workspace, family):
    resolver, module, statement = _source_dictionary_slot_fixture(namespace_workspace)
    calls = BuilderCalls(resolver)
    home, function, actual = calls._source_slot_candidate(module, statement)
    assert actual is statement
    parts = calls._source_slot_parts(module, statement)
    assert parts[0] is statement.targets[0].value.value
    assert parts[3] is statement.targets[0].value
    assert parts[4] is statement.targets[0].slice
    before = calls.callers(home, function, allow_empty=True)
    assert before.limits and not before.sites
    for node in (statement, statement.value, parts[0], parts[3]):
        assert calls.idempotent_source_slot(module, node, family)
    assert calls.uncalled_source_slot(home, function)
    assert calls.callers(home, function, allow_empty=True) is before
    assert before.limits and not before.sites
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("extra", [
    "fake.Agent([])\n", "saved = fake.Agent\n", "saved = fake.__dict__\n",
    "fake.__dict__['Agent']([])\n", "saved = fake.__dict__['Agent']\n",
    "def capture():\n    return fake.__dict__\n", "consumer(fake)\n", "__all__ = ['fake']\n",
    "fake.Agent = replacement\n", "unknown.Agent = replacement\n",
    "fake.__dict__['Agent'] = replacement\n", "del fake.__dict__['Agent']\n",
    "fake.__dict__[key] = replacement\n", "fake.__class__ = replacement\n",
])
def test_source_dictionary_slot_keeps_calls_escapes_and_other_writes_unread(namespace_workspace, extra):
    resolver, module, statement = _source_dictionary_slot_fixture(namespace_workspace, extra=extra)
    calls = BuilderCalls(resolver)
    try:
        assert not calls.idempotent_source_slot(module, statement, "agents")
    except CallLimit:
        pass
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("write", [
    "fake.__dict__[key] = fake.Agent",
    "fake.__dict__['other'] = fake.Agent",
    "fake.__dict__['__class__'] = fake.Agent",
    "vars(fake, opaque)['Agent'] = fake.Agent",
    "getattr(fake, '__dict__')['Agent'] = fake.Agent",
    "first = fake\nother = first\nother.__dict__['Agent'] = fake.Agent",
    "slots = fake.__dict__\nother = slots\nother['Agent'] = fake.Agent",
    "def touch():\n    fake.__dict__['Agent'] = fake.Agent",
    "fake().__dict__['Agent'] = fake.Agent",
])
def test_source_dictionary_slot_does_not_grant_other_projection_or_alias_roles(namespace_workspace, write):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=write)
    calls = BuilderCalls(resolver)
    for statement in ast.walk(module.tree):
        if isinstance(statement, ast.Assign):
            assert calls._source_slot_candidate(module, statement) is None
            assert not calls.idempotent_source_slot(module, statement, "agents")


@pytest.mark.parametrize("home", [
    "class Agent:\n    pass\n", "@decorate\ndef Agent(tools):\n    return tools\n",
    "async def Agent(tools):\n    return tools\n", "Agent = unknown\n",
])
def test_source_dictionary_slot_requires_the_actual_original_plain_function(namespace_workspace, home):
    resolver, module, statement = _source_dictionary_slot_fixture(namespace_workspace, home=home)
    assert BuilderCalls(resolver)._source_slot_candidate(module, statement) is None
    assert not BuilderCalls(resolver).idempotent_source_slot(module, statement, "agents")


def test_source_dictionary_slot_does_not_pair_distinct_module_homes(namespace_workspace):
    resolver, module, statement = _source_dictionary_slot_fixture(
        namespace_workspace, write="import other\nother.__dict__['Agent'] = fake.Agent",
    )
    (namespace_workspace / "other.py").write_text("def Agent(tools):\n    return tools\n")
    statement = module.tree.body[-1]
    assert BuilderCalls(resolver)._source_slot_candidate(module, statement) is None


@pytest.mark.parametrize("mutation", ["__path__ = []\n", "__getattr__ = replacement\n", "import sys\nsys.modules.clear()\n"])
def test_source_dictionary_slot_retains_raw_hook_path_and_table_checks(namespace_workspace, mutation):
    resolver, module, statement = _source_dictionary_slot_fixture(namespace_workspace)
    (namespace_workspace / "quiet.py").write_text(mutation)
    with pytest.raises(CallLimit):
        BuilderCalls(resolver).idempotent_source_slot(module, statement, "agents")


def test_source_dictionary_slot_raw_proof_cannot_borrow_receiving_callbacks(namespace_workspace, monkeypatch):
    from agents_shipgate.inputs import list_expressions

    resolver, module, statement = _source_dictionary_slot_fixture(namespace_workspace)
    def forbidden(*args, **kwargs):
        raise AssertionError("a raw projection proof borrowed a receiving-owner callback")
    for name in ("_independent_foreign_class_slot_write", "_independent_plain_class_slot_write",
                 "_agent_instance", "_provisional_agent_list_clear"):
        monkeypatch.setattr(list_expressions, name, forbidden)
    assert BuilderCalls(resolver).idempotent_source_slot(module, statement, "agents")


def test_source_dictionary_slot_rechecks_the_actual_source_after_success(namespace_workspace):
    resolver, module, statement = _source_dictionary_slot_fixture(namespace_workspace)
    calls = BuilderCalls(resolver)
    assert calls.idempotent_source_slot(module, statement, "agents")
    (namespace_workspace / "fake.py").write_text("def Agent(tools):\n    return replacement\n")
    with pytest.raises(CallLimit):
        calls.idempotent_source_slot(module, statement, "agents")
    assert not resolver._checking_source_module_slot



@pytest.mark.parametrize("alias,consumer", [
    (False, "from entry import fake\n"),
    (True, "from entry import other\n"),
    (True, "from entry import fake\n"),
    (False, "import entry as exported\n"),
])
def test_source_dictionary_slot_refuses_importing_either_actual_namespace_receiver(namespace_workspace, alias, consumer):
    write = "import fake as other\nother.__dict__['Agent'] = fake.Agent" if alias else "fake.__dict__['Agent'] = fake.Agent"
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=write)
    statement = module.tree.body[-1]
    (namespace_workspace / "consumer.py").write_text(consumer)
    calls = BuilderCalls(resolver)
    for node in (statement, statement.value, statement.targets[0].value):
        with pytest.raises(CallLimit, match="imported intrinsic source mapping namespace"):
            calls.idempotent_source_slot(module, node, "agents")
    assert not resolver._checking_source_module_slot


def test_source_dictionary_slot_import_guard_preserves_the_source_function_export_contract(namespace_workspace):
    resolver, module, statement = _source_dictionary_slot_fixture(
        namespace_workspace, home="__all__ = ['Agent']\ndef Agent(tools):\n    return tools\n",
    )
    assert BuilderCalls(resolver).idempotent_source_slot(module, statement, "agents")


@pytest.mark.parametrize("family", ["agents", "google.adk"])
def test_direct_vars_slot_keeps_getter_projection_key_namespace_and_function_distinct(namespace_workspace, family):
    resolver, module, statement = _source_dictionary_slot_fixture(
        namespace_workspace, write="vars(fake)['Agent'] = fake.Agent",
    )
    calls = BuilderCalls(resolver)
    home, function, _ = calls._source_slot_candidate(module, statement)
    projection = statement.targets[0].value
    assert isinstance(projection, ast.Call) and isinstance(projection.func, ast.Name)
    before = calls.callers(home, function, allow_empty=True)
    assert before.limits and not before.sites
    for node in (statement, projection, projection.func, projection.args[0], statement.value):
        assert calls.idempotent_source_slot(module, node, family)
    assert calls.uncalled_source_slot(home, function)
    assert calls.callers(home, function, allow_empty=True) is before
    assert before.limits and not before.sites
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("write", [
    "vars()['Agent'] = fake.Agent", "vars(fake, opaque)['Agent'] = fake.Agent",
    "vars(fake, extra=opaque)['Agent'] = fake.Agent", "vars(*[fake])['Agent'] = fake.Agent",
    "vars(owner=fake)['Agent'] = fake.Agent", "builtins.vars(fake)['Agent'] = fake.Agent",
    "from builtins import vars\nvars(fake)['Agent'] = fake.Agent",
    "replace = vars\nreplace(fake)['Agent'] = fake.Agent",
    "vars = replacement\nvars(fake)['Agent'] = fake.Agent",
    "vars(fake())['Agent'] = fake.Agent", "vars(fake)[key] = fake.Agent",
    "vars(fake)['other'] = fake.Agent", "vars(fake)['__class__'] = fake.Agent",
    "first = fake\nother = first\nvars(other)['Agent'] = fake.Agent",
    "slots = vars(fake)\nother = slots\nother['Agent'] = fake.Agent",
    "def touch():\n    vars(fake)['Agent'] = fake.Agent",
])
def test_direct_vars_slot_refuses_noncanonical_getters_receivers_and_other_roles(namespace_workspace, write):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=write)
    calls = BuilderCalls(resolver)
    for statement in ast.walk(module.tree):
        if isinstance(statement, ast.Assign):
            assert calls._source_slot_candidate(module, statement) is None
            assert not calls.idempotent_source_slot(module, statement, "agents")


@pytest.mark.parametrize("extra", [
    "fake.Agent([])\n", "saved = fake.Agent\n", "saved = vars(fake)\n",
    "vars(fake)['Agent']([])\n", "consumer(fake)\n", "__all__ = ['fake']\n",
    "fake.Agent = replacement\n", "del fake.Agent\n",
])
def test_direct_vars_slot_preserves_other_uses_and_competing_writes(namespace_workspace, extra):
    resolver, module, statement = _source_dictionary_slot_fixture(
        namespace_workspace, write="vars(fake)['Agent'] = fake.Agent", extra=extra,
    )
    calls = BuilderCalls(resolver)
    try:
        assert not calls.idempotent_source_slot(module, statement, "agents")
    except CallLimit:
        pass
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("consumer", ["from entry import fake\n", "import entry as exported\n"])
def test_direct_vars_slot_requires_the_completed_namespace_import_guard(namespace_workspace, consumer):
    resolver, module, statement = _source_dictionary_slot_fixture(
        namespace_workspace, write="vars(fake)['Agent'] = fake.Agent",
    )
    (namespace_workspace / "consumer.py").write_text(consumer)
    with pytest.raises(CallLimit, match="imported intrinsic source mapping namespace"):
        BuilderCalls(resolver).idempotent_source_slot(module, statement.targets[0].value.func, "agents")


def test_direct_vars_slot_rechecks_foreign_builtin_replacement(namespace_workspace):
    resolver, module, statement = _source_dictionary_slot_fixture(
        namespace_workspace, write="vars(fake)['Agent'] = fake.Agent",
    )
    (namespace_workspace / "quiet.py").write_text("import builtins\nbuiltins.vars = replacement\n")
    with pytest.raises(CallLimit):
        BuilderCalls(resolver).idempotent_source_slot(module, statement.targets[0].value.func, "agents")


def test_direct_vars_slot_raw_proof_cannot_borrow_receiving_callbacks(namespace_workspace, monkeypatch):
    from agents_shipgate.inputs import list_expressions

    resolver, module, statement = _source_dictionary_slot_fixture(
        namespace_workspace, write="vars(fake)['Agent'] = fake.Agent",
    )
    def forbidden(*args, **kwargs):
        raise AssertionError("a raw vars proof borrowed a receiving-owner callback")
    for name in ("_independent_foreign_class_slot_write", "_independent_plain_class_slot_write",
                 "_agent_instance", "_provisional_agent_list_clear"):
        monkeypatch.setattr(list_expressions, name, forbidden)
    assert BuilderCalls(resolver).idempotent_source_slot(module, statement.targets[0].value.func, "agents")


def test_direct_vars_slot_does_not_clear_an_active_reentry_guard(namespace_workspace):
    resolver, module, statement = _source_dictionary_slot_fixture(
        namespace_workspace, write="vars(fake)['Agent'] = fake.Agent",
    )
    resolver._checking_source_module_slot = True
    with pytest.raises(CallLimit, match="reentered"):
        BuilderCalls(resolver).idempotent_source_slot(module, statement.targets[0].value.func, "agents")
    assert resolver._checking_source_module_slot


@pytest.mark.parametrize("projection", ["other.__dict__", "vars(other)"])
@pytest.mark.parametrize("declaration", ["other = fake", "other: object = fake"])
@pytest.mark.parametrize("rhs", ["fake", "other"])
@pytest.mark.parametrize("family", ["agents", "google.adk"])
def test_source_mapping_module_alias_preserves_actual_initializer_and_uncached_function_roles(
    namespace_workspace, projection, declaration, rhs, family,
):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write=f"{declaration}\n{projection}['Agent'] = {rhs}.Agent",
    )
    initializer = module.tree.body[1].value
    statement = module.tree.body[-1]
    calls = BuilderCalls(resolver)
    home, function, actual = calls._source_slot_candidate(module, statement)
    assert actual is statement
    parts = calls._source_slot_parts(module, statement)
    receiver = calls._source_slot_receiver(module, parts[0], statement)
    assert receiver[0] is initializer and receiver[1] is initializer
    before = calls.callers(home, function, allow_empty=True)
    assert before.limits and not before.sites
    assert not calls.idempotent_source_slot(module, parts[1], family)
    nodes = [initializer, statement, parts[0], parts[3], statement.value]
    if isinstance(parts[3], ast.Call):
        nodes.append(parts[3].func)
    for node in nodes:
        assert calls.idempotent_source_slot(module, node, family)
    assert calls.uncalled_source_slot(home, function)
    assert calls.callers(home, function, allow_empty=True) is before
    assert before.limits and not before.sites
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("projection", ["other.__dict__", "vars(other)"])
@pytest.mark.parametrize("extra", [
    "consumer(other)\n", "saved = other\n", "other.Agent([])\n",
    "saved = other.Agent\n", "saved = other.__dict__\n",
    "def capture():\n    return other\n", "def capture(value=other):\n    pass\n",
    "other = fake\n", "del other\n", "__all__ = ['other']\n",
    "other.Agent = replacement\n", "other.__dict__[key] = replacement\n",
])
def test_source_mapping_module_alias_refuses_other_alias_uses(namespace_workspace, projection, extra):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write=f"other = fake\n{projection}['Agent'] = fake.Agent", extra=extra,
    )
    statement = module.tree.body[2]
    try:
        assert not BuilderCalls(resolver).idempotent_source_slot(module, statement, "agents")
    except CallLimit:
        pass
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("write", [
    "other = fake\nfake.__dict__['Agent'] = other.Agent",
    "other = fake\nvars(fake)['Agent'] = other.Agent",
    "other = fake\nright = fake\nother.__dict__['Agent'] = right.Agent",
    "other = fake\nright = fake\nvars(other)['Agent'] = right.Agent",
    "other: 'object' = fake\nother.__dict__['Agent'] = fake.Agent",
    "other: custom = fake\nvars(other)['Agent'] = fake.Agent",
    "object = replacement\nother: object = fake\nvars(other)['Agent'] = fake.Agent",
    "other = fake\nslots = vars(other)\nslots['Agent'] = fake.Agent",
])
def test_source_mapping_module_alias_refuses_other_initializer_and_annotation_routes(namespace_workspace, write):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=write)
    calls = BuilderCalls(resolver)
    statement = module.tree.body[-1]
    assert calls._source_slot_candidate(module, statement) is None
    assert not calls.idempotent_source_slot(module, statement, "agents")


@pytest.mark.parametrize("projection", ["other.__dict__", "vars(other)"])
@pytest.mark.parametrize("rhs", ["fake", "other"])
@pytest.mark.parametrize("consumer", [
    "from entry import other\n", "from entry import fake\n", "import entry as exported\n",
])
def test_source_mapping_module_alias_refuses_importing_actual_receiver_or_initializer(
    namespace_workspace, projection, rhs, consumer,
):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write=f"other = fake\n{projection}['Agent'] = {rhs}.Agent",
    )
    (namespace_workspace / "consumer.py").write_text(consumer)
    with pytest.raises(CallLimit):
        BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[1].value, "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("projection", ["other.__dict__", "vars(other)"])
def test_source_mapping_module_alias_rechecks_initializer_source_currency(namespace_workspace, projection):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write=f"other = fake\n{projection}['Agent'] = fake.Agent",
    )
    calls = BuilderCalls(resolver)
    initializer = module.tree.body[1].value
    assert calls.idempotent_source_slot(module, initializer, "agents")
    module.path.write_text(f"import fake\nother = replacement\n{projection}['Agent'] = fake.Agent\n")
    with pytest.raises(CallLimit):
        calls.idempotent_source_slot(module, initializer, "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("projection", ["fake.__dict__", "vars(fake)"])
@pytest.mark.parametrize("family", ["agents", "google.adk"])
def test_saved_source_mapping_store_keeps_mapping_and_function_roles_distinct(namespace_workspace, projection, family):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write=f"slots = {projection}\nslots['Agent'] = fake.Agent",
    )
    declaration, statement = module.tree.body[1:]
    calls = BuilderCalls(resolver)
    parts = calls._source_slot_parts(module, statement)
    mapping = parts[5]
    assert mapping.declaration is declaration and mapping.target is declaration.targets[0]
    assert mapping.receiver is statement.targets[0].value
    assert mapping.projection is declaration.value and mapping.namespace is parts[0]
    home, function, actual = calls._source_slot_candidate(module, statement)
    assert actual is statement
    before = calls.callers(home, function, allow_empty=True)
    assert before.limits and not before.sites
    assert not calls.idempotent_source_slot(module, declaration, family)
    nodes = [statement, mapping.receiver, declaration.value, parts[0], statement.value]
    if isinstance(declaration.value, ast.Call):
        nodes.append(declaration.value.func)
    for node in nodes:
        assert calls.idempotent_source_slot(module, node, family)
    assert calls.uncalled_source_slot(home, function)
    assert calls.callers(home, function, allow_empty=True) is before
    assert not calls._confined_dictionary_data(module).values
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("projection", ["fake.__dict__", "vars(fake)"])
@pytest.mark.parametrize("extra", [
    "saved = slots\n", "saved = slots['Agent']\n", "slots['Agent']([])\n",
    "consumer(slots)\n", "def capture():\n    return slots\n",
    "def capture(value=slots):\n    pass\n", "slots = {}\n", "del slots\n",
    "slots.update(Agent=fake.Agent)\n", "slots |= {'Agent': fake.Agent}\n",
    "__all__ = ['slots']\n", "__all__ = ['fake']\n", "fake.Agent([])\n",
    "fake.Agent = replacement\n", "unknown.__dict__['Agent'] = replacement\n",
])
def test_saved_source_mapping_store_refuses_reads_captures_and_competing_uses(namespace_workspace, projection, extra):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write=f"slots = {projection}\nslots['Agent'] = fake.Agent", extra=extra,
    )
    try:
        assert not BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[2], "agents")
    except CallLimit:
        pass
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("write", [
    "slots: object = vars(fake)\nslots['Agent'] = fake.Agent",
    "other = fake\nslots = vars(other)\nslots['Agent'] = fake.Agent",
    "other = fake\nslots = fake.__dict__\nslots['Agent'] = other.Agent",
    "slots = fake.__dict__\nother = slots\nother['Agent'] = fake.Agent",
    "slots = vars(fake)\nslots[key] = fake.Agent",
    "slots = fake.__dict__\nslots['other'] = fake.Agent",
    "slots = getattr(fake, '__dict__', {})\nslots['Agent'] = fake.Agent",
    "slots = dict(vars(fake))\nslots['Agent'] = fake.Agent",
    "slots = builtins.vars(fake)\nslots['Agent'] = fake.Agent",
    "replace = vars\nother = replace\nslots = other(fake)\nslots['Agent'] = fake.Agent",
    "vars = replacement\nslots = vars(fake)\nslots['Agent'] = fake.Agent",
    "slots = other = fake.__dict__\nslots['Agent'] = fake.Agent",
    "def touch():\n    slots = vars(fake)\n    slots['Agent'] = fake.Agent",
])
def test_saved_source_mapping_store_refuses_other_projection_and_declaration_shapes(namespace_workspace, write):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=write)
    calls = BuilderCalls(resolver)
    for statement in ast.walk(module.tree):
        if isinstance(statement, ast.Assign):
            assert calls._source_slot_candidate(module, statement) is None
            assert not calls.idempotent_source_slot(module, statement, "agents")


@pytest.mark.parametrize("projection", ["fake.__dict__", "vars(fake)"])
@pytest.mark.parametrize("consumer", [
    "from entry import slots\n", "from entry import fake\n",
    "from entry import origin\n", "import entry\n",
])
def test_saved_source_mapping_store_checks_dictionary_and_both_imported_namespace_names(namespace_workspace, projection, consumer):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write=f"import fake as origin\nslots = {projection}\nslots['Agent'] = origin.Agent",
    )
    statement = module.tree.body[-1]
    calls = BuilderCalls(resolver)
    assert calls.idempotent_source_slot(module, statement, "agents")
    (namespace_workspace / "consumer.py").write_text(consumer)
    source = module.path.read_text()
    current_resolver = ImportResolver(namespace_workspace)
    current_module = current_resolver.entry(module.path, ast.parse(source), source)
    with pytest.raises(CallLimit, match="imported intrinsic source mapping namespace"):
        BuilderCalls(current_resolver).idempotent_source_slot(current_module, current_module.tree.body[-1], "agents")
    assert not current_resolver._checking_source_module_slot


@pytest.mark.parametrize("projection", ["fake.__dict__", "vars(fake)"])
def test_saved_source_mapping_store_preserves_function_home_exports(namespace_workspace, projection):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write=f"slots = {projection}\nslots['Agent'] = fake.Agent",
        home="__all__ = ['Agent']\ndef Agent(tools):\n    return tools\n",
    )
    assert BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[-1], "agents")


@pytest.mark.parametrize("projection", ["fake.__dict__", "vars(fake)"])
def test_saved_source_mapping_store_rechecks_declaration_and_reentry(namespace_workspace, projection):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write=f"slots = {projection}\nslots['Agent'] = fake.Agent",
    )
    calls = BuilderCalls(resolver)
    statement = module.tree.body[-1]
    assert calls.idempotent_source_slot(module, statement, "agents")
    resolver._checking_source_module_slot = True
    with pytest.raises(CallLimit, match="reentered"):
        calls.idempotent_source_slot(module, statement, "agents")
    assert resolver._checking_source_module_slot
    resolver._checking_source_module_slot = False
    module.path.write_text("import fake\nslots = replacement\nslots['Agent'] = fake.Agent\n")
    with pytest.raises(CallLimit):
        calls.idempotent_source_slot(module, statement, "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("projection", ["fake.__dict__", "vars(fake)"])
@pytest.mark.parametrize("write", ["slots.update(Agent=fake.Agent)", "slots.update({'Agent': fake.Agent})"])
@pytest.mark.parametrize("family", ["agents", "google.adk"])
def test_saved_source_mapping_update_separates_function_value_from_mutation_syntax(namespace_workspace, projection, write, family):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=f"slots = {projection}\n{write}")
    declaration, statement = module.tree.body[1:]
    calls = BuilderCalls(resolver)
    edge = calls._source_slot_edge(module, statement)
    assert edge.statement is statement and edge.call is statement.value
    assert edge.callee is statement.value.func
    assert edge.parts[5].receiver is edge.callee.value
    assert edge.parts[5].projection is declaration.value
    if isinstance(edge.payload, ast.keyword):
        assert edge.rhs is edge.payload.value
        assert edge.parts[4] is None
    else:
        assert edge.rhs is edge.payload.values[0]
        assert edge.parts[4] is edge.payload.keys[0]
    home, function, actual = calls._source_slot_candidate(module, statement)
    assert actual is statement
    before = calls.callers(home, function, allow_empty=True)
    assert before.limits and not before.sites
    assert not calls.idempotent_source_slot(module, edge.payload, family)
    assert not calls.idempotent_source_slot(module, declaration, family)
    nodes = [statement, edge.call, edge.callee, edge.parts[5].receiver, edge.rhs, declaration.value, edge.parts[0]]
    if isinstance(declaration.value, ast.Call):
        nodes.append(declaration.value.func)
    for node in nodes:
        assert calls.idempotent_source_slot(module, node, family)
    assert calls.uncalled_source_slot(home, function)
    assert calls.callers(home, function, allow_empty=True) is before
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("projection", ["fake.__dict__", "vars(fake)"])
@pytest.mark.parametrize("write", [
    "slots.update(**{'Agent': fake.Agent})", "slots.update(*[{'Agent': fake.Agent}])",
    "slots.update(Agent=fake.Agent, other=fake.Agent)",
    "slots.update({'Agent': fake.Agent}, {'Agent': fake.Agent})",
    "slots.update({'Agent': fake.Agent}, Agent=fake.Agent)",
    "slots.update({'Agent': fake.Agent, 'Agent': fake.Agent})",
    "slots.update({key: fake.Agent})", "slots.update({**opaque, 'Agent': fake.Agent})",
    "slots.update(mapping)", "slots.update(other=fake.Agent)",
    "slots.update({'other': fake.Agent})", "slots.update(Agent=other.Agent)",
    "saved = slots.update(Agent=fake.Agent)", "consume(slots.update(Agent=fake.Agent))",
    "replace = slots.update\nreplace(Agent=fake.Agent)",
    "dict.update(slots, Agent=fake.Agent)", "builtins.dict.update(slots, Agent=fake.Agent)",
    "if condition:\n    slots.update(Agent=fake.Agent)",
    "slots.update(Agent=fake.Agent)\nsaved = slots",
    "slots.update(Agent=fake.Agent)\nslots.update(Agent=fake.Agent)",
])
def test_saved_source_mapping_update_refuses_other_payloads_methods_and_uses(namespace_workspace, projection, write):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=f"slots = {projection}\n{write}")
    calls = BuilderCalls(resolver)
    for statement in ast.walk(module.tree):
        if isinstance(statement, ast.Assign | ast.Expr):
            assert calls._source_slot_candidate(module, statement) is None
            assert not calls.idempotent_source_slot(module, statement, "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("write", ["slots.update(Agent=fake.Agent)", "slots.update({'Agent': fake.Agent})"])
@pytest.mark.parametrize("consumer", ["from entry import slots\n", "from entry import fake\n", "import entry\n"])
def test_saved_source_mapping_update_checks_completed_mapping_and_source_imports(namespace_workspace, write, consumer):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=f"slots = vars(fake)\n{write}")
    (namespace_workspace / "consumer.py").write_text(consumer)
    with pytest.raises(CallLimit, match="imported intrinsic source mapping namespace"):
        BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[-1], "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("write", ["slots.update(Agent=fake.Agent)", "slots.update({'Agent': fake.Agent})"])
@pytest.mark.parametrize("extra", ["fake.Agent([])\n", "saved = fake.Agent\n", "__all__ = ['slots']\n"])
def test_saved_source_mapping_update_preserves_calls_values_and_exports(namespace_workspace, write, extra):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=f"slots = vars(fake)\n{write}", extra=extra)
    try:
        assert not BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[2], "agents")
    except CallLimit:
        pass
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("write", ["slots.update(Agent=fake.Agent)", "slots.update({'Agent': fake.Agent})"])
def test_saved_source_mapping_update_rechecks_source_and_preserves_reentry(namespace_workspace, write):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=f"slots = vars(fake)\n{write}")
    calls = BuilderCalls(resolver)
    statement = module.tree.body[-1]
    assert calls.idempotent_source_slot(module, statement, "agents")
    resolver._checking_source_module_slot = True
    with pytest.raises(CallLimit, match="reentered"):
        calls.idempotent_source_slot(module, statement, "agents")
    assert resolver._checking_source_module_slot
    resolver._checking_source_module_slot = False
    (namespace_workspace / "fake.py").write_text("def Agent(tools):\n    return replacement\n")
    with pytest.raises(CallLimit):
        calls.idempotent_source_slot(module, statement, "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("write", ["slots.update(Agent=fake.Agent)", "slots.update({'Agent': fake.Agent})"])
def test_saved_source_mapping_update_cannot_borrow_receiving_owner_callbacks(namespace_workspace, monkeypatch, write):
    from agents_shipgate.inputs import list_expressions

    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=f"slots = vars(fake)\n{write}")
    def forbidden(*args, **kwargs):
        raise AssertionError("a raw saved mapping update borrowed a receiving-owner callback")
    for name in ("_independent_foreign_class_slot_write", "_independent_plain_class_slot_write",
                 "_agent_instance", "_provisional_agent_list_clear"):
        monkeypatch.setattr(list_expressions, name, forbidden)
    assert BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[-1], "agents")


@pytest.mark.parametrize("write", ["slots.update(update=fake.update)", "slots.update({'update': fake.update})"])
@pytest.mark.parametrize("called", [False, True])
def test_saved_source_mapping_update_keeps_a_same_named_function_distinct_from_the_dictionary_method(namespace_workspace, write, called):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write=f"slots = vars(fake)\n{write}",
        extra="fake.update([])\n" if called else "", home="def update(tools):\n    return tools\n",
    )
    calls = BuilderCalls(resolver)
    statement = module.tree.body[2]
    home, function, _ = calls._source_slot_candidate(module, statement)
    before = calls.callers(home, function, allow_empty=True)
    assert before.limits
    assert calls.idempotent_source_slot(module, statement, "agents") is (not called)
    assert calls.uncalled_source_slot(home, function) is (not called)
    assert calls.callers(home, function, allow_empty=True) is before
    assert bool(before.sites) is called
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("projection", ["fake.__dict__", "vars(fake)"])
@pytest.mark.parametrize("family", ["agents", "google.adk"])
def test_saved_source_mapping_ior_proves_actual_augmented_binding_and_separate_function_role(namespace_workspace, projection, family):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write=f"slots = {projection}\nslots |= {'{'}'Agent': fake.Agent{'}'}",
    )
    declaration, statement = module.tree.body[1:]
    calls = BuilderCalls(resolver)
    edge = calls._source_slot_edge(module, statement)
    mapping = edge.parts[5]
    assert isinstance(statement.op, ast.BitOr) and isinstance(statement.target.ctx, ast.Store)
    assert edge.target is statement.target and mapping.receiver is statement.target
    assert edge.payload is statement.value and edge.rhs is statement.value.values[0]
    assert edge.parts[4] is statement.value.keys[0]
    assert edge.call is None and edge.callee is None
    assert mapping.declaration is declaration and mapping.target is declaration.targets[0]
    assert mapping.projection is declaration.value
    rows = module.bindings['slots']
    assert len(rows) == 2
    assert next(row for row in rows if row.statement is declaration).top_level
    assert not next(row for row in rows if row.statement is statement).top_level
    assert calls.scopes(module).parents[statement] is module.tree
    home, function, actual = calls._source_slot_candidate(module, statement)
    assert actual is statement
    before = calls.callers(home, function, allow_empty=True)
    assert before.limits and not before.sites
    assert not calls.idempotent_source_slot(module, declaration, family)
    assert not calls.idempotent_source_slot(module, edge.payload, family)
    nodes = [statement, edge.target, edge.rhs, declaration.value, mapping.namespace]
    if isinstance(declaration.value, ast.Call):
        nodes.append(declaration.value.func)
    for node in nodes:
        assert calls.idempotent_source_slot(module, node, family)
    assert calls.uncalled_source_slot(home, function)
    assert calls.callers(home, function, allow_empty=True) is before
    assert not calls._confined_dictionary_data(module).values
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("projection", ["fake.__dict__", "vars(fake)"])
@pytest.mark.parametrize("write", [
    "slots += {'Agent': fake.Agent}", "slots &= {'Agent': fake.Agent}",
    "slots |= {'Agent': fake.Agent, 'Agent': fake.Agent}",
    "slots |= {'Agent': fake.Agent, 'other': fake.Agent}",
    "slots |= {key: fake.Agent}", "slots |= {**opaque, 'Agent': fake.Agent}",
    "slots |= {'other': fake.Agent}", "slots |= {'Agent': other.Agent}",
    "slots |= mapping", "slots |= {'Agent': fake}",
    "slots |= {'Agent': fake.Agent}\nslots |= {'Agent': fake.Agent}",
    "slots |= {'Agent': fake.Agent}\nslots.update(Agent=fake.Agent)",
    "slots |= {'Agent': fake.Agent}\nslots['Agent'] = fake.Agent",
    "slots |= {'Agent': fake.Agent}\nsaved = slots",
    "slots |= {'Agent': fake.Agent}\ndel slots",
    "slots |= {'Agent': fake.Agent}\nslots = {}",
    "slots |= {'Agent': fake.Agent}\ndef capture():\n    return slots",
    "slots |= {'Agent': fake.Agent}\ndef capture(value=slots):\n    pass",
    "slots |= {'Agent': fake.Agent}\n__all__ = ['slots']",
    "if condition:\n    slots |= {'Agent': fake.Agent}",
    "def touch():\n    global slots\n    slots |= {'Agent': fake.Agent}",
])
def test_saved_source_mapping_ior_refuses_competing_uses_payloads_and_nested_targets(namespace_workspace, projection, write):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=f"slots = {projection}\n{write}")
    calls = BuilderCalls(resolver)
    for statement in ast.walk(module.tree):
        if isinstance(statement, ast.AugAssign):
            assert calls._source_slot_candidate(module, statement) is None
            assert not calls.idempotent_source_slot(module, statement, "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("write", [
    "slots: object = vars(fake)\nslots |= {'Agent': fake.Agent}",
    "other = fake\nslots = vars(other)\nslots |= {'Agent': fake.Agent}",
    "slots = vars(fake)\nother = slots\nother |= {'Agent': fake.Agent}",
    "slots = getattr(fake, '__dict__', {})\nslots |= {'Agent': fake.Agent}",
    "slots = dict(vars(fake))\nslots |= {'Agent': fake.Agent}",
    "slots = builtins.vars(fake)\nslots |= {'Agent': fake.Agent}",
    "vars = replacement\nslots = vars(fake)\nslots |= {'Agent': fake.Agent}",
    "slots = other = vars(fake)\nslots |= {'Agent': fake.Agent}",
    "def touch():\n    slots = vars(fake)\n    slots |= {'Agent': fake.Agent}",
])
def test_saved_source_mapping_ior_refuses_other_declarations_and_projection_routes(namespace_workspace, write):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=write)
    calls = BuilderCalls(resolver)
    for statement in ast.walk(module.tree):
        if isinstance(statement, ast.AugAssign):
            assert calls._source_slot_candidate(module, statement) is None
            assert not calls.idempotent_source_slot(module, statement, "agents")


@pytest.mark.parametrize("projection", ["fake.__dict__", "vars(fake)"])
@pytest.mark.parametrize("consumer", ["from entry import slots\n", "from entry import fake\n", "import entry\n"])
def test_saved_source_mapping_ior_checks_completed_mapping_and_namespace_imports(namespace_workspace, projection, consumer):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=f"slots = {projection}\nslots |= {'{'}'Agent': fake.Agent{'}'}")
    (namespace_workspace / "consumer.py").write_text(consumer)
    with pytest.raises(CallLimit, match="imported intrinsic source mapping namespace"):
        BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[-1], "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("called", [False, True])
def test_saved_source_mapping_ior_keeps_a_function_named_update_subject_to_real_calls(namespace_workspace, called):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="slots = vars(fake)\nslots |= {'update': fake.update}",
        extra="fake.update([])\n" if called else "", home="def update(tools):\n    return tools\n",
    )
    calls = BuilderCalls(resolver)
    statement = module.tree.body[2]
    home, function, _ = calls._source_slot_candidate(module, statement)
    before = calls.callers(home, function, allow_empty=True)
    assert calls.idempotent_source_slot(module, statement, "agents") is (not called)
    assert calls.uncalled_source_slot(home, function) is (not called)
    assert calls.callers(home, function, allow_empty=True) is before
    assert bool(before.sites) is called


def test_saved_source_mapping_ior_rejects_a_counterfeit_load_target(namespace_workspace):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write="slots = vars(fake)\nslots |= {'Agent': fake.Agent}")
    statement = module.tree.body[-1]
    statement.target.ctx = ast.Load()
    assert BuilderCalls(resolver)._source_slot_augmented_mapping(module, statement) is None


def test_saved_source_mapping_ior_rechecks_source_and_preserves_reentry(namespace_workspace):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write="slots = vars(fake)\nslots |= {'Agent': fake.Agent}")
    calls = BuilderCalls(resolver)
    statement = module.tree.body[-1]
    assert calls.idempotent_source_slot(module, statement, "agents")
    resolver._checking_source_module_slot = True
    with pytest.raises(CallLimit, match="reentered"):
        calls.idempotent_source_slot(module, statement, "agents")
    assert resolver._checking_source_module_slot
    resolver._checking_source_module_slot = False
    module.path.write_text("import fake\nslots = replacement\nslots |= {'Agent': fake.Agent}\n")
    with pytest.raises(CallLimit):
        calls.idempotent_source_slot(module, statement, "agents")
    assert not resolver._checking_source_module_slot


_SAVED_SOURCE_GETATTR_WRITES = (
    "slots['Agent'] = fake.Agent",
    "slots.update(Agent=fake.Agent)",
    "slots.update({'Agent': fake.Agent})",
    "slots |= {'Agent': fake.Agent}",
)


@pytest.mark.parametrize("write", _SAVED_SOURCE_GETATTR_WRITES)
@pytest.mark.parametrize("family", ["agents", "google.adk"])
def test_saved_source_getattr_mapping_keeps_metadata_distinct_from_slot_key_and_function(namespace_workspace, write, family):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write=f"slots = getattr(fake, '__dict__')\n{write}",
    )
    declaration, statement = module.tree.body[1:]
    calls = BuilderCalls(resolver)
    edge = calls._source_slot_edge(module, statement)
    projection = edge.parts[3]
    metadata = projection.args[1]
    assert calls._source_slot_projection_metadata(projection) is metadata
    assert metadata is not edge.parts[4] and metadata is not edge.rhs
    assert edge.parts[5].declaration is declaration
    assert calls._source_slot_projection_metadata(ast.Constant('__dict__')) is None
    assert calls._source_slot_candidate(module, ast.Constant('__dict__')) is None
    home, function, actual = calls._source_slot_candidate(module, statement)
    assert actual is statement
    before = calls.callers(home, function, allow_empty=True)
    assert before.limits and not before.sites
    assert not calls.idempotent_source_slot(module, declaration, family)
    for node in (statement, projection, projection.func, projection.args[0], metadata, edge.rhs, edge.parts[5].receiver):
        assert calls.idempotent_source_slot(module, node, family)
    assert calls.uncalled_source_slot(home, function)
    assert calls.callers(home, function, allow_empty=True) is before
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("write", _SAVED_SOURCE_GETATTR_WRITES)
@pytest.mark.parametrize("getter", [
    "getattr(fake, '__dict__', {})", "getattr(fake, name='__dict__')",
    "getattr(*[fake, '__dict__'])", "getattr(fake, field)", "getattr(fake, 'other')",
    "builtins.getattr(fake, '__dict__')", "replace(fake, '__dict__')",
    "getattr(fake, '__dict__').copy()", "getattr(fake.Agent, '__dict__')",
])
def test_saved_source_getattr_mapping_refuses_other_getter_shapes(namespace_workspace, write, getter):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=f"slots = {getter}\n{write}")
    calls = BuilderCalls(resolver)
    assert calls._source_slot_candidate(module, module.tree.body[-1]) is None
    assert not calls.idempotent_source_slot(module, module.tree.body[-1], "agents")


@pytest.mark.parametrize("write", _SAVED_SOURCE_GETATTR_WRITES)
@pytest.mark.parametrize("extra", [
    "getattr = replacement\n", "saved = slots\n", "slots = {}\n", "del slots\n",
    "def capture():\n    return slots\n", "def capture(value=slots):\n    pass\n",
    "__all__ = ['slots']\n", "slots['Agent'] = fake.Agent\n", "fake.Agent([])\n",
])
def test_saved_source_getattr_mapping_preserves_shadowing_capture_export_and_actual_call_checks(namespace_workspace, write, extra):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=f"slots = getattr(fake, '__dict__')\n{write}", extra=extra)
    try:
        assert not BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[2], "agents")
    except CallLimit:
        pass
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("write", _SAVED_SOURCE_GETATTR_WRITES)
@pytest.mark.parametrize("consumer", ["from entry import slots\n", "from entry import fake\n", "import entry\n"])
def test_saved_source_getattr_mapping_checks_complete_context_imports(namespace_workspace, write, consumer):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=f"slots = getattr(fake, '__dict__')\n{write}")
    (namespace_workspace / "consumer.py").write_text(consumer)
    with pytest.raises(CallLimit, match="imported intrinsic source mapping namespace"):
        BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[-1], "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("write", _SAVED_SOURCE_GETATTR_WRITES)
@pytest.mark.parametrize("mutation", [
    "import builtins\nbuiltins.getattr = replacement\n",
    "import builtins as b\nb.getattr = replacement\n",
    "import builtins\nsetattr(builtins, 'getattr', replacement)\n",
    "import sys\nsys.modules.clear()\n", "__getattr__ = replacement\n",
])
def test_saved_source_getattr_mapping_retains_raw_getter_hook_and_table_stability(namespace_workspace, write, mutation):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=f"slots = getattr(fake, '__dict__')\n{write}")
    (namespace_workspace / "quiet.py").write_text(mutation)
    with pytest.raises(CallLimit):
        BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[-1], "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("write", _SAVED_SOURCE_GETATTR_WRITES)
def test_saved_source_getattr_mapping_rechecks_source_and_preserves_reentry(namespace_workspace, write):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=f"slots = getattr(fake, '__dict__')\n{write}")
    calls = BuilderCalls(resolver)
    statement = module.tree.body[-1]
    assert calls.idempotent_source_slot(module, statement, "agents")
    resolver._checking_source_module_slot = True
    with pytest.raises(CallLimit, match="reentered"):
        calls.idempotent_source_slot(module, statement, "agents")
    assert resolver._checking_source_module_slot
    resolver._checking_source_module_slot = False
    module.path.write_text(f"import fake\nslots = replacement\n{write}\n")
    with pytest.raises(CallLimit):
        calls.idempotent_source_slot(module, statement, "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("write", _SAVED_SOURCE_GETATTR_WRITES)
@pytest.mark.parametrize("called", [False, True])
def test_saved_source_getattr_mapping_keeps_function_update_distinct_from_method_and_metadata(namespace_workspace, write, called):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="slots = getattr(fake, '__dict__')\n" + write.replace('Agent', 'update'),
        extra="fake.update([])\n" if called else "", home="def update(tools):\n    return tools\n",
    )
    calls = BuilderCalls(resolver)
    statement = module.tree.body[2]
    home, function, _ = calls._source_slot_candidate(module, statement)
    before = calls.callers(home, function, allow_empty=True)
    assert calls.idempotent_source_slot(module, statement, "agents") is (not called)
    assert calls.uncalled_source_slot(home, function) is (not called)
    assert calls.callers(home, function, allow_empty=True) is before
    assert bool(before.sites) is called


@pytest.mark.parametrize("write", _SAVED_SOURCE_GETATTR_WRITES)
def test_saved_source_getattr_mapping_cannot_borrow_receiving_owner_callbacks(namespace_workspace, monkeypatch, write):
    from agents_shipgate.inputs import list_expressions

    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=f"slots = getattr(fake, '__dict__')\n{write}")
    def forbidden(*args, **kwargs):
        raise AssertionError("a raw getattr mapping borrowed a receiving-owner callback")
    for name in ("_independent_foreign_class_slot_write", "_independent_plain_class_slot_write",
                 "_agent_instance", "_provisional_agent_list_clear"):
        monkeypatch.setattr(list_expressions, name, forbidden)
    assert BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[-1], "agents")


@pytest.mark.parametrize("field", ["vars", "dict", "getattr", "setattr"])
@pytest.mark.parametrize("family", ["agents", "google.adk"])
def test_operator_metadata_write_requires_exact_completed_target_and_uncached_source_proof(namespace_workspace, field, family):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write=f"import operator\noperator.{field} = None\nslots = vars(fake)\nslots['Agent'] = fake.Agent",
    )
    calls = BuilderCalls(resolver)
    metadata = calls._operator_metadata_write(module, module.tree.body[2])
    assert metadata.target is metadata.statement.targets[0]
    assert metadata.imported is module.tree.body[1].names[0]
    assert metadata.marker == f'operator.{field}'
    home, function, _ = calls._source_slot_candidate(module, module.tree.body[-1])
    before = calls.callers(home, function, allow_empty=True)
    assert before.limits and not before.sites
    with pytest.raises(CallLimit):
        BuilderCalls(resolver).namespace_source_context(family)
    assert calls.operator_metadata_write(module, metadata.target, family)
    assert calls.idempotent_source_slot(module, module.tree.body[-1], family,
                                        required_operator_metadata=(module, metadata.target))
    assert not calls.idempotent_source_slot(module, module.tree.body[-1], family,
                                            required_operator_metadata=(home, metadata.target))
    clone = ast.parse(f'operator.{field} = None').body[0].targets[0]
    assert not calls.operator_metadata_write(module, clone, family)
    assert not calls.idempotent_source_slot(module, module.tree.body[-1], family,
                                            required_operator_metadata=(module, clone))
    assert not calls.operator_metadata_write(module, metadata.statement, family)
    assert not calls.operator_metadata_write(module, metadata.receiver, family)
    assert calls.uncalled_source_slot(home, function)
    assert calls.callers(home, function, allow_empty=True) is before
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("write", [
    "import operator\noperator.vars = None\noperator.vars = None",
    "import operator\noperator.vars = None\nimport operator as op\nop.vars = None",
    "import operator\noperator.vars = None\nother = operator",
    "import operator as op\nop.vars = None", "from operator import vars\nvars = None",
    "import operator\noperator.vars = replacement", "import operator\noperator.vars = fake.Agent",
    "import operator\noperator.vars += None", "import operator\ndel operator.vars",
    "import operator\nif condition:\n    operator.vars = None",
    "import operator\ndef touch():\n    operator.vars = None",
    "import operator\noperator.vars = None\nconsumer(operator)",
    "import operator\noperator.vars = None\ndef capture():\n    return operator",
    "import operator\noperator.vars = None\ndef capture(value=operator):\n    pass",
    "import operator\noperator.vars = None\n__all__ = ['operator']",
    "import operator\noperator = fake\noperator.vars = None",
    "import operator\noperator.vars = None\nsetattr(operator, 'vars', None)",
    "import operator\noperator.vars = None\noperator.__dict__['vars'] = None",
])
def test_operator_metadata_write_refuses_other_shapes_and_namespace_uses(namespace_workspace, write):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write=write + "\nslots = vars(fake)\nslots['Agent'] = fake.Agent",
    )
    calls = BuilderCalls(resolver)
    for node in ast.walk(module.tree):
        if isinstance(node, ast.Attribute) and node.attr == 'vars':
            try:
                assert not calls.operator_metadata_write(module, node, 'agents')
            except CallLimit:
                pass
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("provider", ["module", "package", "case", "linked"])
def test_operator_metadata_write_requires_absence_of_repository_providers(namespace_workspace, provider):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import operator\noperator.vars = None\nslots = vars(fake)\nslots['Agent'] = fake.Agent",
    )
    if provider == 'package':
        (namespace_workspace / 'operator').mkdir()
        (namespace_workspace / 'operator/__init__.py').write_text('VALUE = None\n')
    elif provider == 'linked':
        (namespace_workspace / 'operator.py').symlink_to('fake.py')
    else:
        (namespace_workspace / ('Operator.py' if provider == 'case' else 'operator.py')).write_text('VALUE = None\n')
    source = module.path.read_text()
    current = ImportResolver(namespace_workspace)
    actual = current.entry(module.path, ast.parse(source), source)
    try:
        assert not BuilderCalls(current).operator_metadata_write(actual, actual.tree.body[2].targets[0], 'agents')
    except CallLimit:
        pass


def test_operator_metadata_write_case_variant_provider_blocks_on_a_case_sensitive_host(namespace_workspace, monkeypatch):
    """The absence proof reads the exact listing, not what this host's filesystem finds.

    ``Operator.py`` is found as ``operator.py`` by a case-insensitive filesystem
    and not by a case-sensitive one. The probe below makes any host answer as
    the second does; the verdict must be the same as on the first.
    """
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import operator\noperator.vars = None\nslots = vars(fake)\nslots['Agent'] = fake.Agent",
    )
    (namespace_workspace / 'Operator.py').write_text('VALUE = None\n')
    real_lstat = Path.lstat

    def case_sensitive_lstat(self, *args, **kwargs):
        if self.name in {'operator', 'operator.py'}:
            raise FileNotFoundError(self)
        return real_lstat(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'lstat', case_sensitive_lstat)
    source = module.path.read_text()
    current = ImportResolver(namespace_workspace)
    actual = current.entry(module.path, ast.parse(source), source)
    try:
        assert not BuilderCalls(current).operator_metadata_write(actual, actual.tree.body[2].targets[0], 'agents')
    except CallLimit:
        pass


@pytest.mark.parametrize("field", ["vars", "dict", "getattr", "setattr"])
@pytest.mark.parametrize("consumer", ["from entry import operator\n", "import entry\n"])
def test_operator_metadata_write_checks_even_unused_importers(namespace_workspace, field, consumer):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write=f"import operator\noperator.{field} = None\nfake.Agent = fake.Agent",
    )
    (namespace_workspace / 'consumer.py').write_text(consumer)
    with pytest.raises(CallLimit, match='imported operator metadata namespace'):
        BuilderCalls(resolver).operator_metadata_write(module, module.tree.body[2].targets[0], 'agents')
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("field", ["vars", "dict", "getattr", "setattr"])
@pytest.mark.parametrize("injected", ['unknown', 'copied', 'missing'])
def test_operator_metadata_write_refuses_incomplete_or_nonunique_raw_producers(namespace_workspace, monkeypatch, field, injected):
    from agents_shipgate.inputs import list_expressions

    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write=f"import operator\noperator.{field} = None\nslots = vars(fake)\nslots['Agent'] = fake.Agent",
    )
    target = module.tree.body[2].targets[0]
    original = list_expressions._qualified_attribute_patches
    def traced(view, **kwargs):
        patches = original(view, **kwargs)
        producers = kwargs.get('patch_producers')
        if view.module is module and producers is not None:
            marker = f'operator.{field}'
            if injected == 'missing':
                producers.pop(marker, None)
            else:
                extra = None if injected == 'unknown' else ast.parse(f'operator.{field} = None').body[0].targets[0]
                producers.setdefault(marker, set()).add(extra)
        return patches
    monkeypatch.setattr(list_expressions, '_qualified_attribute_patches', traced)
    try:
        assert not BuilderCalls(resolver).operator_metadata_write(module, target, 'agents')
    except CallLimit:
        pass
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("field", ["vars", "dict", "getattr", "setattr"])
def test_operator_metadata_write_rechecks_currency_and_reentry(namespace_workspace, field):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write=f"import operator\noperator.{field} = None\nslots = vars(fake)\nslots['Agent'] = fake.Agent",
    )
    calls = BuilderCalls(resolver)
    target = module.tree.body[2].targets[0]
    assert calls.operator_metadata_write(module, target, 'agents')
    resolver._checking_source_module_slot = True
    with pytest.raises(CallLimit, match='reentered'):
        calls.operator_metadata_write(module, target, 'agents')
    assert resolver._checking_source_module_slot
    resolver._checking_source_module_slot = False
    (namespace_workspace / 'operator.py').write_text('VALUE = None\n')
    with pytest.raises(CallLimit):
        calls.operator_metadata_write(module, target, 'agents')
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("write", [
    "import operator\noperator.vars = None", "import operator\ndel operator.vars",
    "import operator\noperator.__dict__['vars'] = None",
    "import operator\nslots = vars(operator)\nslots |= {'vars': None}",
    "import operator\nslots = vars(operator)\nslots.update(vars=None)",
    "import operator\nslots = vars(operator)\nsaved = slots.update",
    "import operator\ndict.update(vars(operator), {'vars': None})",
    "import operator\nsetattr(operator, 'vars', None)",
    "import operator\noperator.ior(vars(operator), {'vars': None})",
    "setattr(*opaque)", "import operator\noperator.ior(*opaque)",
    "import builtins\nbuiltins.vars = None\nslots = vars(fake)\nslots['vars'] = None",
])
def test_raw_patch_producer_evidence_preserves_default_sets_and_primitive_flags(namespace_workspace, write):
    from agents_shipgate.inputs.list_expressions import (
        _qualified_attribute_patches,
        _View,
        bindings_at,
    )

    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=write)
    def view():
        value = _View(module.ref, module.tree, BuilderCalls(resolver).scopes(module), module.bindings, module, set())
        value.lookup = bindings_at(value.scopes, value.bindings)
        value.resolver = resolver
        return value
    ordinary, observed = view(), view()
    expected = _qualified_attribute_patches(ordinary, raw_namespaces=True)
    producers = {}
    actual = _qualified_attribute_patches(observed, raw_namespaces=True, patch_producers=producers)
    assert actual == expected
    assert observed.namespace_builtins_changed == ordinary.namespace_builtins_changed
    assert all(producers.get(marker) for marker in actual)
    nodes = set(ast.walk(module.tree))
    assert all(node is None or node in nodes for values in producers.values() for node in values)
    assert all(node is None or isinstance(node, ast.Attribute | ast.Subscript | ast.AugAssign | ast.Call)
               for values in producers.values() for node in values)


def test_raw_patch_producer_evidence_marks_fixed_point_exhaustion_as_unknown(namespace_workspace):
    from agents_shipgate.inputs.list_expressions import (
        _NAMESPACE_PRIMITIVES,
        _UNREAD_NAMESPACE_ALIAS,
        _qualified_attribute_patches,
        _View,
        bindings_at,
    )
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write='import builtins\nbuiltins.vars = None')
    view = _View(module.ref, module.tree, BuilderCalls(resolver).scopes(module), module.bindings, module, set())
    view.lookup = bindings_at(view.scopes, view.bindings)
    view.resolver = resolver
    producers = {}
    patches = _qualified_attribute_patches(view, raw_namespaces=True,
                                           primitive_pass=len(_NAMESPACE_PRIMITIVES), patch_producers=producers)
    assert _UNREAD_NAMESPACE_ALIAS in patches
    assert producers[_UNREAD_NAMESPACE_ALIAS] == {None}


@pytest.mark.parametrize("field", ["vars", "dict", "getattr", "setattr"])
@pytest.mark.parametrize("operation", ["metadata_target", "source_slot"])
@pytest.mark.parametrize("tree_backed", [False, True])
def test_operator_metadata_requires_bound_disk_currency(namespace_workspace, monkeypatch, field, operation, tree_backed):
    from contextlib import nullcontext

    from agents_shipgate.inputs.python_imports import RepositoryLayout, repository_layout

    files = {
        "entry.py": f"import fake\nimport operator\noperator.{field} = None\nfake.Agent = fake.Agent\n",
        "fake.py": "def Agent(tools):\n    return tools\n",
    }
    for name, source in files.items():
        (namespace_workspace / name).write_text(source)
    layout = RepositoryLayout(
        "", lambda ref: frozenset(files) if ref == "" else None, read=lambda ref: files.get(ref),
    )
    with repository_layout(layout) if tree_backed else nullcontext():
        resolver = ImportResolver(namespace_workspace)
        source = files["entry.py"]
        module = resolver.entry(namespace_workspace / "entry.py", ast.parse(source), source)
        calls = BuilderCalls(resolver)
        target = module.tree.body[2].targets[0]
        terminal = module.tree.body[3]
        if tree_backed:
            assert resolver._layout.disk_root is None

            def forbidden_disk_capture(*args):
                pytest.fail("tree metadata proof must refuse before capturing live disk currency")

            monkeypatch.setattr(calls, "_namespace_directory_entries", forbidden_disk_capture)
        with pytest.raises(CallLimit):
            calls.namespace_source_context("agents")
        if tree_backed:
            with pytest.raises(CallLimit, match="no bound import-root currency for the operator metadata import"):
                if operation == "metadata_target":
                    calls.operator_metadata_write(module, target, "agents")
                else:
                    calls.idempotent_source_slot(module, terminal, "agents")
        elif operation == "metadata_target":
            assert calls.operator_metadata_write(module, target, "agents")
        else:
            assert calls.idempotent_source_slot(module, terminal, "agents")
        assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("projection", ["fake.__dict__", "vars(fake)", "getattr(fake, '__dict__')"])
@pytest.mark.parametrize("write", ["slots.__init__(Agent=fake.Agent)", "slots.__init__({'Agent': fake.Agent})"])
@pytest.mark.parametrize("family", ["agents", "google.adk"])
def test_saved_source_mapping_init_separates_function_value_from_mutation_syntax(namespace_workspace, projection, write, family):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=f"slots = {projection}\n{write}")
    declaration, statement = module.tree.body[1:]
    calls = BuilderCalls(resolver)
    edge = calls._source_slot_edge(module, statement)
    assert edge.statement is statement and edge.call is statement.value
    assert edge.callee is statement.value.func
    assert edge.parts[5].receiver is edge.callee.value
    assert edge.parts[5].projection is declaration.value
    if isinstance(edge.payload, ast.keyword):
        assert edge.rhs is edge.payload.value
        assert edge.parts[4] is None
    else:
        assert edge.rhs is edge.payload.values[0]
        assert edge.parts[4] is edge.payload.keys[0]
    home, function, actual = calls._source_slot_candidate(module, statement)
    assert actual is statement
    before = calls.callers(home, function, allow_empty=True)
    assert before.limits and not before.sites
    assert not calls.idempotent_source_slot(module, edge.payload, family)
    assert not calls.idempotent_source_slot(module, declaration, family)
    nodes = [statement, edge.call, edge.callee, edge.parts[5].receiver, edge.rhs, declaration.value, edge.parts[0]]
    if isinstance(declaration.value, ast.Call):
        nodes.append(declaration.value.func)
    for node in nodes:
        assert calls.idempotent_source_slot(module, node, family)
    assert calls.uncalled_source_slot(home, function)
    assert calls.callers(home, function, allow_empty=True) is before
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("projection", ["fake.__dict__", "vars(fake)", "getattr(fake, '__dict__')"])
@pytest.mark.parametrize("write", [
    "slots.__init__(**{'Agent': fake.Agent})", "slots.__init__(*[{'Agent': fake.Agent}])",
    "slots.__init__(Agent=fake.Agent, other=fake.Agent)",
    "slots.__init__({'Agent': fake.Agent}, {'Agent': fake.Agent})",
    "slots.__init__({'Agent': fake.Agent}, Agent=fake.Agent)",
    "slots.__init__({'Agent': fake.Agent, 'Agent': fake.Agent})",
    "slots.__init__({key: fake.Agent})", "slots.__init__({**opaque, 'Agent': fake.Agent})",
    "slots.__init__(mapping)", "slots.__init__(other=fake.Agent)",
    "slots.__init__({'other': fake.Agent})", "slots.__init__(Agent=other.Agent)",
    "saved = slots.__init__(Agent=fake.Agent)", "consume(slots.__init__(Agent=fake.Agent))",
    "replace = slots.__init__\nreplace(Agent=fake.Agent)",
    "dict.__init__(slots, Agent=fake.Agent, other=fake.Agent)", "builtins.dict.__init__(slots, Agent=fake.Agent)",
    "if condition:\n    slots.__init__(Agent=fake.Agent)",
    "slots.__init__(Agent=fake.Agent)\nsaved = slots",
    "slots.__init__(Agent=fake.Agent)\nslots.__init__(Agent=fake.Agent)",
])
def test_saved_source_mapping_init_refuses_other_payloads_methods_and_uses(namespace_workspace, projection, write):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=f"slots = {projection}\n{write}")
    calls = BuilderCalls(resolver)
    for statement in ast.walk(module.tree):
        if isinstance(statement, ast.Assign | ast.Expr):
            assert calls._source_slot_candidate(module, statement) is None
            assert not calls.idempotent_source_slot(module, statement, "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("write", ["slots.__init__(Agent=fake.Agent)", "slots.__init__({'Agent': fake.Agent})"])
@pytest.mark.parametrize("consumer", ["from entry import slots\n", "from entry import fake\n", "import entry\n"])
def test_saved_source_mapping_init_checks_completed_mapping_and_source_imports(namespace_workspace, write, consumer):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=f"slots = vars(fake)\n{write}")
    (namespace_workspace / "consumer.py").write_text(consumer)
    with pytest.raises(CallLimit, match="imported intrinsic source mapping namespace"):
        BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[-1], "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("write", ["slots.__init__(Agent=fake.Agent)", "slots.__init__({'Agent': fake.Agent})"])
@pytest.mark.parametrize("extra", ["fake.Agent([])\n", "saved = fake.Agent\n", "__all__ = ['slots']\n"])
def test_saved_source_mapping_init_preserves_calls_values_and_exports(namespace_workspace, write, extra):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=f"slots = vars(fake)\n{write}", extra=extra)
    try:
        assert not BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[2], "agents")
    except CallLimit:
        pass
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("write", ["slots.__init__(Agent=fake.Agent)", "slots.__init__({'Agent': fake.Agent})"])
def test_saved_source_mapping_init_rechecks_source_and_preserves_reentry(namespace_workspace, write):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=f"slots = vars(fake)\n{write}")
    calls = BuilderCalls(resolver)
    statement = module.tree.body[-1]
    assert calls.idempotent_source_slot(module, statement, "agents")
    resolver._checking_source_module_slot = True
    with pytest.raises(CallLimit, match="reentered"):
        calls.idempotent_source_slot(module, statement, "agents")
    assert resolver._checking_source_module_slot
    resolver._checking_source_module_slot = False
    (namespace_workspace / "fake.py").write_text("def Agent(tools):\n    return replacement\n")
    with pytest.raises(CallLimit):
        calls.idempotent_source_slot(module, statement, "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("write", ["slots.__init__(Agent=fake.Agent)", "slots.__init__({'Agent': fake.Agent})"])
def test_saved_source_mapping_init_cannot_borrow_receiving_owner_callbacks(namespace_workspace, monkeypatch, write):
    from agents_shipgate.inputs import list_expressions

    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=f"slots = vars(fake)\n{write}")
    def forbidden(*args, **kwargs):
        raise AssertionError("a raw saved mapping update borrowed a receiving-owner callback")
    for name in ("_independent_foreign_class_slot_write", "_independent_plain_class_slot_write",
                 "_agent_instance", "_provisional_agent_list_clear"):
        monkeypatch.setattr(list_expressions, name, forbidden)
    assert BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[-1], "agents")


@pytest.mark.parametrize("write", ["slots.__init__(update=fake.update)", "slots.__init__({'update': fake.update})"])
@pytest.mark.parametrize("called", [False, True])
def test_saved_source_mapping_init_keeps_a_same_named_function_distinct_from_the_dictionary_method(namespace_workspace, write, called):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write=f"slots = vars(fake)\n{write}",
        extra="fake.update([])\n" if called else "", home="def update(tools):\n    return tools\n",
    )
    calls = BuilderCalls(resolver)
    statement = module.tree.body[2]
    home, function, _ = calls._source_slot_candidate(module, statement)
    before = calls.callers(home, function, allow_empty=True)
    assert before.limits
    assert calls.idempotent_source_slot(module, statement, "agents") is (not called)
    assert calls.uncalled_source_slot(home, function) is (not called)
    assert calls.callers(home, function, allow_empty=True) is before
    assert bool(before.sites) is called
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("projection", ["fake.__dict__", "vars(fake)", "getattr(fake, '__dict__')"])
@pytest.mark.parametrize("write", ["dict.__init__(slots, Agent=fake.Agent)", "dict.__init__(slots, {'Agent': fake.Agent})"])
@pytest.mark.parametrize("family", ["agents", "google.adk"])
def test_source_unbound_mapping_init_separates_function_value_from_mutation_syntax(namespace_workspace, projection, write, family):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=f"slots = {projection}\n{write}")
    declaration, statement = module.tree.body[1:]
    calls = BuilderCalls(resolver)
    edge = calls._source_slot_edge(module, statement)
    assert edge.statement is statement and edge.call is statement.value
    assert edge.callee is statement.value.func
    assert edge.parts[5].receiver is edge.call.args[0]
    assert edge.primitive is edge.callee.value and edge.primitive.id == "dict"
    assert edge.target is edge.parts[5].receiver
    assert edge.parts[5].projection is declaration.value
    if isinstance(edge.payload, ast.keyword):
        assert edge.rhs is edge.payload.value
        assert edge.parts[4] is None
    else:
        assert edge.rhs is edge.payload.values[0]
        assert edge.parts[4] is edge.payload.keys[0]
    home, function, actual = calls._source_slot_candidate(module, statement)
    assert actual is statement
    before = calls.callers(home, function, allow_empty=True)
    assert before.limits and not before.sites
    assert not calls.idempotent_source_slot(module, edge.payload, family)
    assert not calls.idempotent_source_slot(module, declaration, family)
    nodes = [edge.primitive, statement, edge.call, edge.callee, edge.parts[5].receiver, edge.rhs, declaration.value, edge.parts[0]]
    if isinstance(declaration.value, ast.Call):
        nodes.append(declaration.value.func)
    for node in nodes:
        assert calls.idempotent_source_slot(module, node, family)
    assert calls.uncalled_source_slot(home, function)
    assert calls.callers(home, function, allow_empty=True) is before
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("projection", ["fake.__dict__", "vars(fake)", "getattr(fake, '__dict__')"])
@pytest.mark.parametrize("write", [
    "dict.__init__(slots, **{'Agent': fake.Agent})", "dict.__init__(slots, *[{'Agent': fake.Agent}])",
    "dict.__init__(slots, Agent=fake.Agent, other=fake.Agent)",
    "dict.__init__(slots, {'Agent': fake.Agent}, {'Agent': fake.Agent})",
    "dict.__init__(slots, {'Agent': fake.Agent}, Agent=fake.Agent)",
    "dict.__init__(slots, {'Agent': fake.Agent, 'Agent': fake.Agent})",
    "dict.__init__(slots, {key: fake.Agent})", "dict.__init__(slots, {**opaque, 'Agent': fake.Agent})",
    "dict.__init__(slots, mapping)", "dict.__init__(slots, other=fake.Agent)",
    "dict.__init__(slots, {'other': fake.Agent})", "dict.__init__(slots, Agent=other.Agent)",
    "saved = dict.__init__(slots, Agent=fake.Agent)", "consume(dict.__init__(slots, Agent=fake.Agent))",
    "replace = slots.__init__\nreplace(Agent=fake.Agent)",
    "dict.update(slots, Agent=fake.Agent)", "builtins.dict.__init__(slots, Agent=fake.Agent)",
    "if condition:\n    dict.__init__(slots, Agent=fake.Agent)",
    "dict.__init__(slots, Agent=fake.Agent)\nsaved = slots",
    "dict.__init__(slots, Agent=fake.Agent)\ndict.__init__(slots, Agent=fake.Agent)",
])
def test_source_unbound_mapping_init_refuses_other_payloads_methods_and_uses(namespace_workspace, projection, write):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=f"slots = {projection}\n{write}")
    calls = BuilderCalls(resolver)
    for statement in ast.walk(module.tree):
        if isinstance(statement, ast.Assign | ast.Expr):
            assert calls._source_slot_candidate(module, statement) is None
            assert not calls.idempotent_source_slot(module, statement, "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("write", ["dict.__init__(slots, Agent=fake.Agent)", "dict.__init__(slots, {'Agent': fake.Agent})"])
@pytest.mark.parametrize("consumer", ["from entry import slots\n", "from entry import fake\n", "import entry\n"])
def test_source_unbound_mapping_init_checks_completed_mapping_and_source_imports(namespace_workspace, write, consumer):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=f"slots = vars(fake)\n{write}")
    (namespace_workspace / "consumer.py").write_text(consumer)
    with pytest.raises(CallLimit, match="imported intrinsic source mapping namespace"):
        BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[-1], "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("write", ["dict.__init__(slots, Agent=fake.Agent)", "dict.__init__(slots, {'Agent': fake.Agent})"])
@pytest.mark.parametrize("extra", ["fake.Agent([])\n", "saved = fake.Agent\n", "__all__ = ['slots']\n"])
def test_source_unbound_mapping_init_preserves_calls_values_and_exports(namespace_workspace, write, extra):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=f"slots = vars(fake)\n{write}", extra=extra)
    try:
        assert not BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[2], "agents")
    except CallLimit:
        pass
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("write", ["dict.__init__(slots, Agent=fake.Agent)", "dict.__init__(slots, {'Agent': fake.Agent})"])
def test_source_unbound_mapping_init_rechecks_source_and_preserves_reentry(namespace_workspace, write):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=f"slots = vars(fake)\n{write}")
    calls = BuilderCalls(resolver)
    statement = module.tree.body[-1]
    assert calls.idempotent_source_slot(module, statement, "agents")
    resolver._checking_source_module_slot = True
    with pytest.raises(CallLimit, match="reentered"):
        calls.idempotent_source_slot(module, statement, "agents")
    assert resolver._checking_source_module_slot
    resolver._checking_source_module_slot = False
    (namespace_workspace / "fake.py").write_text("def Agent(tools):\n    return replacement\n")
    with pytest.raises(CallLimit):
        calls.idempotent_source_slot(module, statement, "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("write", ["dict.__init__(slots, Agent=fake.Agent)", "dict.__init__(slots, {'Agent': fake.Agent})"])
def test_source_unbound_mapping_init_cannot_borrow_receiving_owner_callbacks(namespace_workspace, monkeypatch, write):
    from agents_shipgate.inputs import list_expressions

    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=f"slots = vars(fake)\n{write}")
    def forbidden(*args, **kwargs):
        raise AssertionError("a raw saved mapping update borrowed a receiving-owner callback")
    for name in ("_independent_foreign_class_slot_write", "_independent_plain_class_slot_write",
                 "_agent_instance", "_provisional_agent_list_clear"):
        monkeypatch.setattr(list_expressions, name, forbidden)
    assert BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[-1], "agents")


@pytest.mark.parametrize("write", ["dict.__init__(slots, update=fake.update)", "dict.__init__(slots, {'update': fake.update})"])
@pytest.mark.parametrize("called", [False, True])
def test_source_unbound_mapping_init_keeps_a_same_named_function_distinct_from_the_dictionary_method(namespace_workspace, write, called):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write=f"slots = vars(fake)\n{write}",
        extra="fake.update([])\n" if called else "", home="def update(tools):\n    return tools\n",
    )
    calls = BuilderCalls(resolver)
    statement = module.tree.body[2]
    home, function, _ = calls._source_slot_candidate(module, statement)
    before = calls.callers(home, function, allow_empty=True)
    assert before.limits
    assert calls.idempotent_source_slot(module, statement, "agents") is (not called)
    assert calls.uncalled_source_slot(home, function) is (not called)
    assert calls.callers(home, function, allow_empty=True) is before
    assert bool(before.sites) is called
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("called", [False, True])
def test_source_unbound_mapping_init_keeps_builtin_callee_distinct_from_function_dict(namespace_workspace, called):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="slots = vars(fake)\ndict.__init__(slots, dict=fake.dict)",
        extra="fake.dict([])\n" if called else "", home="def dict(tools):\n    return tools\n",
    )
    calls = BuilderCalls(resolver)
    terminal = module.tree.body[2]
    edge = calls._source_slot_edge(module, terminal)
    assert edge.primitive is terminal.value.func.value
    assert edge.rhs is terminal.value.keywords[0].value
    assert edge.primitive is not edge.rhs.value
    assert calls.idempotent_source_slot(module, terminal, "agents") is (not called)
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("field", ["Agent", "dict"])
def test_source_unbound_mapping_init_requires_raw_canonical_primitive(namespace_workspace, monkeypatch, field):
    from agents_shipgate.inputs import list_expressions

    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write=f"slots = vars(fake)\ndict.__init__(slots, {field}=fake.{field})",
        home=f"def {field}(tools):\n    return tools\n",
    )
    calls = BuilderCalls(resolver)
    terminal = module.tree.body[2]
    assert calls.idempotent_source_slot(module, terminal, "agents")
    original = list_expressions._namespace_call_reference
    raw_original = list_expressions._qualified_attribute_patches
    raw_active = False

    def raw(view, **kwargs):
        nonlocal raw_active
        previous = raw_active
        raw_active = True
        try:
            return raw_original(view, **kwargs)
        finally:
            raw_active = previous

    def altered(view, call):
        if call is terminal.value and not raw_active:
            return "foreign.dict.__init__"
        return original(view, call)

    monkeypatch.setattr(list_expressions, "_qualified_attribute_patches", raw)
    monkeypatch.setattr(list_expressions, "_namespace_call_reference", altered)
    with pytest.raises(CallLimit, match="unread dictionary initialization primitive"):
        calls.idempotent_source_slot(module, terminal, "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("extra", [
    "dict = replacement\n", "from provider import dict\n", "import builtins\nbuiltins.dict = replacement\n",
    "import builtins\nbuiltins.dict.__init__ = replacement\n", "fake.__class__ = replacement\n",
    "import builtins\nsetattr(builtins, 'dict', replacement)\n",
])
def test_source_unbound_mapping_init_preserves_local_and_remote_primitive_stability(namespace_workspace, extra):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="slots = vars(fake)\ndict.__init__(slots, Agent=fake.Agent)", extra=extra,
    )
    calls = BuilderCalls(resolver)
    try:
        assert not calls.idempotent_source_slot(module, module.tree.body[2], "agents")
    except CallLimit:
        pass
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("operation", ["metadata_target", "source_slot"])
@pytest.mark.parametrize("disk_root_kind", ["disjoint", "nested"])
def test_operator_metadata_refuses_invalid_disk_root_before_currency_capture(namespace_workspace, monkeypatch, operation, disk_root_kind):
    from agents_shipgate.inputs.python_imports import RepositoryLayout, repository_layout

    files = {
        "entry.py": "import fake\nimport operator\noperator.dict = None\nfake.Agent = fake.Agent\n",
        "fake.py": "def Agent(tools):\n    return tools\n",
    }
    for name, source in files.items():
        (namespace_workspace / name).write_text(source)
    disk_root = (namespace_workspace.parent if disk_root_kind == "disjoint" else namespace_workspace) / "elsewhere"
    layout = RepositoryLayout(
        "", lambda ref: frozenset(files) if ref == "" else None,
        read=lambda ref: files.get(ref), disk_root=disk_root,
    )

    def forbidden_disk_capture(*args):
        pytest.fail("invalid disk ancestry must be refused before acquiring directory currency")

    monkeypatch.setattr(BuilderCalls, "_namespace_directory_entries", forbidden_disk_capture)
    with repository_layout(layout):
        resolver = ImportResolver(namespace_workspace)
        source = files["entry.py"]
        module = resolver.entry(namespace_workspace / "entry.py", ast.parse(source), source)
        calls = BuilderCalls(resolver)
        with pytest.raises(CallLimit, match="disk ancestry that does not match the read scope"):
            if operation == "metadata_target":
                calls.operator_metadata_write(module, module.tree.body[2].targets[0], "agents")
            else:
                calls.idempotent_source_slot(module, module.tree.body[3], "agents")
        assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("projection", ["fake.__dict__", "vars(fake)", "getattr(fake, '__dict__')"])
@pytest.mark.parametrize("write", ["builtins.dict.__init__(slots, Agent=fake.Agent)", "builtins.dict.__init__(slots, {'Agent': fake.Agent})"])
@pytest.mark.parametrize("family", ["agents", "google.adk"])
def test_source_qualified_mapping_init_separates_function_value_from_mutation_syntax(namespace_workspace, projection, write, family):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=f"import builtins\nslots = {projection}\n{write}")
    builtin_import, declaration, statement = module.tree.body[1:]
    calls = BuilderCalls(resolver)
    edge = calls._source_slot_edge(module, statement)
    assert edge.statement is statement and edge.call is statement.value
    assert edge.callee is statement.value.func
    assert edge.parts[5].receiver is edge.call.args[0]
    assert edge.primitive is edge.callee.value and edge.primitive.attr == "dict"
    assert edge.primitive_import.statement is builtin_import
    assert edge.primitive_import.root is edge.primitive.value
    assert edge.target is edge.parts[5].receiver
    assert edge.parts[5].projection is declaration.value
    if isinstance(edge.payload, ast.keyword):
        assert edge.rhs is edge.payload.value
        assert edge.parts[4] is None
    else:
        assert edge.rhs is edge.payload.values[0]
        assert edge.parts[4] is edge.payload.keys[0]
    home, function, actual = calls._source_slot_candidate(module, statement)
    assert actual is statement
    before = calls.callers(home, function, allow_empty=True)
    assert before.limits and not before.sites
    assert not calls.idempotent_source_slot(module, edge.payload, family)
    assert not calls.idempotent_source_slot(module, declaration, family)
    nodes = [edge.primitive_import.root, edge.primitive, statement, edge.call, edge.callee, edge.parts[5].receiver, edge.rhs, declaration.value, edge.parts[0]]
    if isinstance(declaration.value, ast.Call):
        nodes.append(declaration.value.func)
    for node in nodes:
        assert calls.idempotent_source_slot(module, node, family)
    assert calls.source_primitive_import(module, builtin_import, family)
    assert calls.uncalled_source_slot(home, function)
    assert calls.callers(home, function, allow_empty=True) is before
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("projection", ["fake.__dict__", "vars(fake)", "getattr(fake, '__dict__')"])
@pytest.mark.parametrize("write", [
    "builtins.dict.__init__(slots, **{'Agent': fake.Agent})", "builtins.dict.__init__(slots, *[{'Agent': fake.Agent}])",
    "builtins.dict.__init__(slots, Agent=fake.Agent, other=fake.Agent)",
    "builtins.dict.__init__(slots, {'Agent': fake.Agent}, {'Agent': fake.Agent})",
    "builtins.dict.__init__(slots, {'Agent': fake.Agent}, Agent=fake.Agent)",
    "builtins.dict.__init__(slots, {'Agent': fake.Agent, 'Agent': fake.Agent})",
    "builtins.dict.__init__(slots, {key: fake.Agent})", "builtins.dict.__init__(slots, {**opaque, 'Agent': fake.Agent})",
    "builtins.dict.__init__(slots, mapping)", "builtins.dict.__init__(slots, other=fake.Agent)",
    "builtins.dict.__init__(slots, {'other': fake.Agent})", "builtins.dict.__init__(slots, Agent=other.Agent)",
    "saved = builtins.dict.__init__(slots, Agent=fake.Agent)", "consume(builtins.dict.__init__(slots, Agent=fake.Agent))",
    "replace = slots.__init__\nreplace(Agent=fake.Agent)",
    "dict.update(slots, Agent=fake.Agent)", "builtins.dict.update(slots, Agent=fake.Agent, other=fake.Agent)",
    "if condition:\n    builtins.dict.__init__(slots, Agent=fake.Agent)",
    "builtins.dict.__init__(slots, Agent=fake.Agent)\nsaved = slots",
    "builtins.dict.__init__(slots, Agent=fake.Agent)\nbuiltins.dict.__init__(slots, Agent=fake.Agent)",
])
def test_source_qualified_mapping_init_refuses_other_payloads_methods_and_uses(namespace_workspace, projection, write):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=f"import builtins\nslots = {projection}\n{write}")
    calls = BuilderCalls(resolver)
    for statement in ast.walk(module.tree):
        if isinstance(statement, ast.Assign | ast.Expr):
            assert calls._source_slot_candidate(module, statement) is None
            assert not calls.idempotent_source_slot(module, statement, "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("write", ["builtins.dict.__init__(slots, Agent=fake.Agent)", "builtins.dict.__init__(slots, {'Agent': fake.Agent})"])
@pytest.mark.parametrize("consumer", ["from entry import slots\n", "from entry import fake\n", "import entry\n"])
def test_source_qualified_mapping_init_checks_completed_mapping_and_source_imports(namespace_workspace, write, consumer):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=f"import builtins\nslots = vars(fake)\n{write}")
    (namespace_workspace / "consumer.py").write_text(consumer)
    with pytest.raises(CallLimit, match="imported intrinsic source mapping namespace"):
        BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[-1], "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("write", ["builtins.dict.__init__(slots, Agent=fake.Agent)", "builtins.dict.__init__(slots, {'Agent': fake.Agent})"])
@pytest.mark.parametrize("extra", ["fake.Agent([])\n", "saved = fake.Agent\n", "__all__ = ['slots']\n"])
def test_source_qualified_mapping_init_preserves_calls_values_and_exports(namespace_workspace, write, extra):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=f"import builtins\nslots = vars(fake)\n{write}", extra=extra)
    try:
        assert not BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[3], "agents")
    except CallLimit:
        pass
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("write", ["builtins.dict.__init__(slots, Agent=fake.Agent)", "builtins.dict.__init__(slots, {'Agent': fake.Agent})"])
def test_source_qualified_mapping_init_rechecks_source_and_preserves_reentry(namespace_workspace, write):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=f"import builtins\nslots = vars(fake)\n{write}")
    calls = BuilderCalls(resolver)
    statement = module.tree.body[-1]
    assert calls.idempotent_source_slot(module, statement, "agents")
    resolver._checking_source_module_slot = True
    with pytest.raises(CallLimit, match="reentered"):
        calls.idempotent_source_slot(module, statement, "agents")
    assert resolver._checking_source_module_slot
    resolver._checking_source_module_slot = False
    (namespace_workspace / "fake.py").write_text("def Agent(tools):\n    return replacement\n")
    with pytest.raises(CallLimit):
        calls.idempotent_source_slot(module, statement, "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("write", ["builtins.dict.__init__(slots, Agent=fake.Agent)", "builtins.dict.__init__(slots, {'Agent': fake.Agent})"])
def test_source_qualified_mapping_init_cannot_borrow_receiving_owner_callbacks(namespace_workspace, monkeypatch, write):
    from agents_shipgate.inputs import list_expressions

    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=f"import builtins\nslots = vars(fake)\n{write}")
    def forbidden(*args, **kwargs):
        raise AssertionError("a raw saved mapping update borrowed a receiving-owner callback")
    for name in ("_independent_foreign_class_slot_write", "_independent_plain_class_slot_write",
                 "_agent_instance", "_provisional_agent_list_clear"):
        monkeypatch.setattr(list_expressions, name, forbidden)
    assert BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[-1], "agents")


@pytest.mark.parametrize("write", ["builtins.dict.__init__(slots, update=fake.update)", "builtins.dict.__init__(slots, {'update': fake.update})"])
@pytest.mark.parametrize("called", [False, True])
def test_source_qualified_mapping_init_keeps_a_same_named_function_distinct_from_the_dictionary_method(namespace_workspace, write, called):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write=f"import builtins\nslots = vars(fake)\n{write}",
        extra="fake.update([])\n" if called else "", home="def update(tools):\n    return tools\n",
    )
    calls = BuilderCalls(resolver)
    statement = module.tree.body[3]
    home, function, _ = calls._source_slot_candidate(module, statement)
    before = calls.callers(home, function, allow_empty=True)
    assert before.limits
    assert calls.idempotent_source_slot(module, statement, "agents") is (not called)
    assert calls.uncalled_source_slot(home, function) is (not called)
    assert calls.callers(home, function, allow_empty=True) is before
    assert bool(before.sites) is called
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("called", [False, True])
def test_source_qualified_mapping_init_keeps_builtin_callee_distinct_from_function_dict(namespace_workspace, called):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import builtins\nslots = vars(fake)\nbuiltins.dict.__init__(slots, dict=fake.dict)",
        extra="fake.dict([])\n" if called else "", home="def dict(tools):\n    return tools\n",
    )
    calls = BuilderCalls(resolver)
    terminal = module.tree.body[3]
    edge = calls._source_slot_edge(module, terminal)
    assert edge.primitive is terminal.value.func.value
    assert edge.rhs is terminal.value.keywords[0].value
    assert edge.primitive is not edge.rhs.value
    assert calls.idempotent_source_slot(module, terminal, "agents") is (not called)
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("field", ["Agent", "dict"])
def test_source_qualified_mapping_init_requires_raw_canonical_primitive(namespace_workspace, monkeypatch, field):
    from agents_shipgate.inputs import list_expressions

    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write=f"import builtins\nslots = vars(fake)\nbuiltins.dict.__init__(slots, {field}=fake.{field})",
        home=f"def {field}(tools):\n    return tools\n",
    )
    calls = BuilderCalls(resolver)
    terminal = module.tree.body[3]
    assert calls.idempotent_source_slot(module, terminal, "agents")
    original = list_expressions._namespace_call_reference
    raw_original = list_expressions._qualified_attribute_patches
    raw_active = False

    def raw(view, **kwargs):
        nonlocal raw_active
        previous = raw_active
        raw_active = True
        try:
            return raw_original(view, **kwargs)
        finally:
            raw_active = previous

    def altered(view, call):
        if call is terminal.value and not raw_active:
            return "foreign.dict.__init__"
        return original(view, call)

    monkeypatch.setattr(list_expressions, "_qualified_attribute_patches", raw)
    monkeypatch.setattr(list_expressions, "_namespace_call_reference", altered)
    with pytest.raises(CallLimit, match="unread dictionary initialization primitive"):
        calls.idempotent_source_slot(module, terminal, "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("extra", [
    "builtins = replacement\n", "from provider import dict\n", "import builtins\nbuiltins.dict = replacement\n",
    "import builtins\nbuiltins.dict.__init__ = replacement\n", "fake.__class__ = replacement\n",
    "import builtins\nsetattr(builtins, 'dict', replacement)\n",
])
def test_source_qualified_mapping_init_preserves_local_and_remote_primitive_stability(namespace_workspace, extra):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import builtins\nslots = vars(fake)\nbuiltins.dict.__init__(slots, Agent=fake.Agent)", extra=extra,
    )
    calls = BuilderCalls(resolver)
    try:
        assert not calls.idempotent_source_slot(module, module.tree.body[3], "agents")
    except CallLimit:
        pass
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("family", ["agents", "google.adk"])
def test_source_qualified_mapping_init_requires_actual_import_receipt(namespace_workspace, family):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import builtins\nslots = vars(fake)\nbuiltins.dict.__init__(slots, Agent=fake.Agent)",
    )
    calls = BuilderCalls(resolver)
    declaration, terminal = module.tree.body[1], module.tree.body[3]
    clone = ast.parse("import builtins").body[0]
    assert calls.source_primitive_import(module, declaration, family)
    assert calls.idempotent_source_slot(module, terminal, family, required_primitive_import=(module, declaration))
    assert not calls.source_primitive_import(module, clone, family)
    assert not calls.source_primitive_import(module, module.tree.body[0], family)
    assert not calls.idempotent_source_slot(module, terminal, family, required_primitive_import=(module, clone))
    assert not calls.idempotent_source_slot(module, declaration, family)
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("imported", [
    "import builtins as b\n", "import builtins, other\n", "from builtins import dict\n", "",
])
@pytest.mark.parametrize("extra", ["", "saved = builtins\n", "builtins.dict\n", "__all__ = ['builtins']\n"])
def test_source_qualified_mapping_init_refuses_other_import_and_root_uses(namespace_workspace, imported, extra):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write=imported + "slots = vars(fake)\nbuiltins.dict.__init__(slots, Agent=fake.Agent)", extra=extra,
    )
    calls = BuilderCalls(resolver)
    # Only a direct unaliased import with its sole actual callee use is eligible.
    for statement in module.tree.body:
        if isinstance(statement, ast.Import):
            assert not calls.source_primitive_import(module, statement, "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("provider", ["module", "package", "case", "link"])
def test_source_qualified_mapping_init_requires_absent_builtin_provider(namespace_workspace, provider):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import builtins\nslots = vars(fake)\nbuiltins.dict.__init__(slots, Agent=fake.Agent)",
    )
    if provider == "package":
        (namespace_workspace / "builtins").mkdir()
        (namespace_workspace / "builtins/__init__.py").write_text("VALUE = None\n")
    elif provider == "link":
        (namespace_workspace / "builtins.py").symlink_to("fake.py")
    else:
        (namespace_workspace / ("Builtins.py" if provider == "case" else "builtins.py")).write_text("VALUE = None\n")
    try:
        assert not BuilderCalls(resolver).source_primitive_import(module, module.tree.body[1], "agents")
    except CallLimit:
        pass
    assert not resolver._checking_source_module_slot


def test_source_qualified_mapping_init_raw_constructor_route_cannot_borrow_import_proof(namespace_workspace, monkeypatch):
    from agents_shipgate.inputs.python_imports import _external_constructor_use

    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import builtins\nslots = vars(fake)\nbuiltins.dict.__init__(slots, Agent=fake.Agent)",
    )

    def forbidden(*args, **kwargs):
        pytest.fail("raw constructor observation borrowed a completed primitive import proof")

    monkeypatch.setattr(BuilderCalls, "source_primitive_import", forbidden)
    assert _external_constructor_use(resolver, module, "agents", {module.path}, allow_owner_routes=False)
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("extra", [
    "saved = builtins\n", "builtins.dict\n", "__all__ = ['builtins']\n",
    "def capture(value=builtins):\n    pass\n", "del builtins\n", "builtins = fake\n",
])
def test_source_qualified_mapping_init_confines_actual_imported_root(namespace_workspace, extra):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import builtins\nslots = vars(fake)\nbuiltins.dict.__init__(slots, Agent=fake.Agent)",
        extra=extra,
    )
    calls = BuilderCalls(resolver)
    assert not calls.source_primitive_import(module, module.tree.body[1], "agents")
    assert calls._source_slot_candidate(module, module.tree.body[3]) is None
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("operation", ["import", "slot"])
def test_source_qualified_mapping_init_tree_context_cannot_borrow_live_disk_root(namespace_workspace, monkeypatch, operation):
    from agents_shipgate.inputs.python_imports import RepositoryLayout, repository_layout

    files = {
        "entry.py": "import fake\nimport builtins\nslots = vars(fake)\nbuiltins.dict.__init__(slots, Agent=fake.Agent)\n",
        "fake.py": "def Agent(tools):\n    return tools\n",
    }
    for name, source in files.items():
        (namespace_workspace / name).write_text(source)
    layout = RepositoryLayout("", lambda ref: frozenset(files) if ref == "" else None, read=lambda ref: files.get(ref))

    def forbidden(*args):
        pytest.fail("a tree primitive proof borrowed live directory currency")

    monkeypatch.setattr(BuilderCalls, "_namespace_directory_entries", forbidden)
    with repository_layout(layout):
        resolver = ImportResolver(namespace_workspace)
        source = files["entry.py"]
        module = resolver.entry(namespace_workspace / "entry.py", ast.parse(source), source)
        calls = BuilderCalls(resolver)
        with pytest.raises(CallLimit, match="no bound import-root currency for the dictionary initialization import"):
            if operation == "import":
                calls.source_primitive_import(module, module.tree.body[1], "agents")
            else:
                calls.idempotent_source_slot(module, module.tree.body[3], "agents")
        assert not resolver._checking_source_module_slot


def test_source_qualified_mapping_init_rechecks_provider_currency_and_import_reentry(namespace_workspace):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import builtins\nslots = vars(fake)\nbuiltins.dict.__init__(slots, Agent=fake.Agent)",
    )
    calls = BuilderCalls(resolver)
    declaration = module.tree.body[1]
    assert calls.source_primitive_import(module, declaration, "agents")
    resolver._checking_source_module_slot = True
    with pytest.raises(CallLimit, match="reentered"):
        calls.source_primitive_import(module, declaration, "agents")
    assert resolver._checking_source_module_slot
    resolver._checking_source_module_slot = False
    (namespace_workspace / "builtins.py").write_text("VALUE = None\n")
    with pytest.raises(CallLimit):
        calls.source_primitive_import(module, declaration, "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("projection", ["fake.__dict__", "vars(fake)", "getattr(fake, '__dict__')"])
@pytest.mark.parametrize("write", ["builtins.dict.update(slots, Agent=fake.Agent)", "builtins.dict.update(slots, {'Agent': fake.Agent})"])
@pytest.mark.parametrize("family", ["agents", "google.adk"])
def test_source_qualified_mapping_update_separates_function_value_from_mutation_syntax(namespace_workspace, projection, write, family):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=f"import builtins\nslots = {projection}\n{write}")
    builtin_import, declaration, statement = module.tree.body[1:]
    calls = BuilderCalls(resolver)
    edge = calls._source_slot_edge(module, statement)
    assert edge.statement is statement and edge.call is statement.value
    assert edge.callee is statement.value.func
    assert edge.parts[5].receiver is edge.call.args[0]
    assert edge.primitive is edge.callee.value and edge.primitive.attr == "dict"
    assert edge.primitive_import.statement is builtin_import
    assert edge.primitive_import.root is edge.primitive.value
    assert edge.target is edge.parts[5].receiver
    assert edge.parts[5].projection is declaration.value
    if isinstance(edge.payload, ast.keyword):
        assert edge.rhs is edge.payload.value
        assert edge.parts[4] is None
    else:
        assert edge.rhs is edge.payload.values[0]
        assert edge.parts[4] is edge.payload.keys[0]
    home, function, actual = calls._source_slot_candidate(module, statement)
    assert actual is statement
    before = calls.callers(home, function, allow_empty=True)
    assert before.limits and not before.sites
    assert not calls.idempotent_source_slot(module, edge.payload, family)
    assert not calls.idempotent_source_slot(module, declaration, family)
    nodes = [edge.primitive_import.root, edge.primitive, statement, edge.call, edge.callee, edge.parts[5].receiver, edge.rhs, declaration.value, edge.parts[0]]
    if isinstance(declaration.value, ast.Call):
        nodes.append(declaration.value.func)
    for node in nodes:
        assert calls.idempotent_source_slot(module, node, family)
    assert calls.source_primitive_import(module, builtin_import, family)
    assert calls.uncalled_source_slot(home, function)
    assert calls.callers(home, function, allow_empty=True) is before
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("projection", ["fake.__dict__", "vars(fake)", "getattr(fake, '__dict__')"])
@pytest.mark.parametrize("write", [
    "builtins.dict.update(slots, **{'Agent': fake.Agent})", "builtins.dict.update(slots, *[{'Agent': fake.Agent}])",
    "builtins.dict.update(slots, Agent=fake.Agent, other=fake.Agent)",
    "builtins.dict.update(slots, {'Agent': fake.Agent}, {'Agent': fake.Agent})",
    "builtins.dict.update(slots, {'Agent': fake.Agent}, Agent=fake.Agent)",
    "builtins.dict.update(slots, {'Agent': fake.Agent, 'Agent': fake.Agent})",
    "builtins.dict.update(slots, {key: fake.Agent})", "builtins.dict.update(slots, {**opaque, 'Agent': fake.Agent})",
    "builtins.dict.update(slots, mapping)", "builtins.dict.update(slots, other=fake.Agent)",
    "builtins.dict.update(slots, {'other': fake.Agent})", "builtins.dict.update(slots, Agent=other.Agent)",
    "saved = builtins.dict.update(slots, Agent=fake.Agent)", "consume(builtins.dict.update(slots, Agent=fake.Agent))",
    "replace = slots.update\nreplace(Agent=fake.Agent)",
    "dict.update(slots, Agent=fake.Agent)", "builtins.dict.update(slots, Agent=fake.Agent, other=fake.Agent)",
    "if condition:\n    builtins.dict.update(slots, Agent=fake.Agent)",
    "builtins.dict.update(slots, Agent=fake.Agent)\nsaved = slots",
    "builtins.dict.update(slots, Agent=fake.Agent)\nbuiltins.dict.update(slots, Agent=fake.Agent)",
])
def test_source_qualified_mapping_update_refuses_other_payloads_methods_and_uses(namespace_workspace, projection, write):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=f"import builtins\nslots = {projection}\n{write}")
    calls = BuilderCalls(resolver)
    for statement in ast.walk(module.tree):
        if isinstance(statement, ast.Assign | ast.Expr):
            assert calls._source_slot_candidate(module, statement) is None
            assert not calls.idempotent_source_slot(module, statement, "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("write", ["builtins.dict.update(slots, Agent=fake.Agent)", "builtins.dict.update(slots, {'Agent': fake.Agent})"])
@pytest.mark.parametrize("consumer", ["from entry import slots\n", "from entry import fake\n", "import entry\n"])
def test_source_qualified_mapping_update_checks_completed_mapping_and_source_imports(namespace_workspace, write, consumer):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=f"import builtins\nslots = vars(fake)\n{write}")
    (namespace_workspace / "consumer.py").write_text(consumer)
    with pytest.raises(CallLimit, match="imported intrinsic source mapping namespace"):
        BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[-1], "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("write", ["builtins.dict.update(slots, Agent=fake.Agent)", "builtins.dict.update(slots, {'Agent': fake.Agent})"])
@pytest.mark.parametrize("extra", ["fake.Agent([])\n", "saved = fake.Agent\n", "__all__ = ['slots']\n"])
def test_source_qualified_mapping_update_preserves_calls_values_and_exports(namespace_workspace, write, extra):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=f"import builtins\nslots = vars(fake)\n{write}", extra=extra)
    try:
        assert not BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[3], "agents")
    except CallLimit:
        pass
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("write", ["builtins.dict.update(slots, Agent=fake.Agent)", "builtins.dict.update(slots, {'Agent': fake.Agent})"])
def test_source_qualified_mapping_update_rechecks_source_and_preserves_reentry(namespace_workspace, write):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=f"import builtins\nslots = vars(fake)\n{write}")
    calls = BuilderCalls(resolver)
    statement = module.tree.body[-1]
    assert calls.idempotent_source_slot(module, statement, "agents")
    resolver._checking_source_module_slot = True
    with pytest.raises(CallLimit, match="reentered"):
        calls.idempotent_source_slot(module, statement, "agents")
    assert resolver._checking_source_module_slot
    resolver._checking_source_module_slot = False
    (namespace_workspace / "fake.py").write_text("def Agent(tools):\n    return replacement\n")
    with pytest.raises(CallLimit):
        calls.idempotent_source_slot(module, statement, "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("write", ["builtins.dict.update(slots, Agent=fake.Agent)", "builtins.dict.update(slots, {'Agent': fake.Agent})"])
def test_source_qualified_mapping_update_cannot_borrow_receiving_owner_callbacks(namespace_workspace, monkeypatch, write):
    from agents_shipgate.inputs import list_expressions

    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=f"import builtins\nslots = vars(fake)\n{write}")
    def forbidden(*args, **kwargs):
        raise AssertionError("a raw saved mapping update borrowed a receiving-owner callback")
    for name in ("_independent_foreign_class_slot_write", "_independent_plain_class_slot_write",
                 "_agent_instance", "_provisional_agent_list_clear"):
        monkeypatch.setattr(list_expressions, name, forbidden)
    assert BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[-1], "agents")


@pytest.mark.parametrize("write", ["builtins.dict.update(slots, update=fake.update)", "builtins.dict.update(slots, {'update': fake.update})"])
@pytest.mark.parametrize("called", [False, True])
def test_source_qualified_mapping_update_keeps_a_same_named_function_distinct_from_the_dictionary_method(namespace_workspace, write, called):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write=f"import builtins\nslots = vars(fake)\n{write}",
        extra="fake.update([])\n" if called else "", home="def update(tools):\n    return tools\n",
    )
    calls = BuilderCalls(resolver)
    statement = module.tree.body[3]
    home, function, _ = calls._source_slot_candidate(module, statement)
    before = calls.callers(home, function, allow_empty=True)
    assert before.limits
    assert calls.idempotent_source_slot(module, statement, "agents") is (not called)
    assert calls.uncalled_source_slot(home, function) is (not called)
    assert calls.callers(home, function, allow_empty=True) is before
    assert bool(before.sites) is called
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("called", [False, True])
def test_source_qualified_mapping_update_keeps_builtin_callee_distinct_from_function_dict(namespace_workspace, called):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import builtins\nslots = vars(fake)\nbuiltins.dict.update(slots, dict=fake.dict)",
        extra="fake.dict([])\n" if called else "", home="def dict(tools):\n    return tools\n",
    )
    calls = BuilderCalls(resolver)
    terminal = module.tree.body[3]
    edge = calls._source_slot_edge(module, terminal)
    assert edge.primitive is terminal.value.func.value
    assert edge.rhs is terminal.value.keywords[0].value
    assert edge.primitive is not edge.rhs.value
    assert calls.idempotent_source_slot(module, terminal, "agents") is (not called)
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("field", ["Agent", "dict"])
def test_source_qualified_mapping_update_requires_raw_canonical_primitive(namespace_workspace, monkeypatch, field):
    from agents_shipgate.inputs import list_expressions

    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write=f"import builtins\nslots = vars(fake)\nbuiltins.dict.update(slots, {field}=fake.{field})",
        home=f"def {field}(tools):\n    return tools\n",
    )
    calls = BuilderCalls(resolver)
    terminal = module.tree.body[3]
    assert calls.idempotent_source_slot(module, terminal, "agents")
    original = list_expressions._namespace_call_reference
    raw_original = list_expressions._qualified_attribute_patches
    raw_active = False

    def raw(view, **kwargs):
        nonlocal raw_active
        previous = raw_active
        raw_active = True
        try:
            return raw_original(view, **kwargs)
        finally:
            raw_active = previous

    def altered(view, call):
        if call is terminal.value and not raw_active:
            return "foreign.dict.update"
        return original(view, call)

    monkeypatch.setattr(list_expressions, "_qualified_attribute_patches", raw)
    monkeypatch.setattr(list_expressions, "_namespace_call_reference", altered)
    with pytest.raises(CallLimit, match="unread dictionary initialization primitive"):
        calls.idempotent_source_slot(module, terminal, "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("extra", [
    "builtins = replacement\n", "from provider import dict\n", "import builtins\nbuiltins.dict = replacement\n",
    "import builtins\nbuiltins.dict.update = replacement\n", "fake.__class__ = replacement\n",
    "import builtins\nsetattr(builtins, 'dict', replacement)\n",
])
def test_source_qualified_mapping_update_preserves_local_and_remote_primitive_stability(namespace_workspace, extra):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import builtins\nslots = vars(fake)\nbuiltins.dict.update(slots, Agent=fake.Agent)", extra=extra,
    )
    calls = BuilderCalls(resolver)
    try:
        assert not calls.idempotent_source_slot(module, module.tree.body[3], "agents")
    except CallLimit:
        pass
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("family", ["agents", "google.adk"])
def test_source_qualified_mapping_update_requires_actual_import_receipt(namespace_workspace, family):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import builtins\nslots = vars(fake)\nbuiltins.dict.update(slots, Agent=fake.Agent)",
    )
    calls = BuilderCalls(resolver)
    declaration, terminal = module.tree.body[1], module.tree.body[3]
    clone = ast.parse("import builtins").body[0]
    assert calls.source_primitive_import(module, declaration, family)
    assert calls.idempotent_source_slot(module, terminal, family, required_primitive_import=(module, declaration))
    assert not calls.source_primitive_import(module, clone, family)
    assert not calls.source_primitive_import(module, module.tree.body[0], family)
    assert not calls.idempotent_source_slot(module, terminal, family, required_primitive_import=(module, clone))
    assert not calls.idempotent_source_slot(module, declaration, family)
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("imported", [
    "import builtins as b\n", "import builtins, other\n", "from builtins import dict\n", "",
])
@pytest.mark.parametrize("extra", ["", "saved = builtins\n", "builtins.dict\n", "__all__ = ['builtins']\n"])
def test_source_qualified_mapping_update_refuses_other_import_and_root_uses(namespace_workspace, imported, extra):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write=imported + "slots = vars(fake)\nbuiltins.dict.update(slots, Agent=fake.Agent)", extra=extra,
    )
    calls = BuilderCalls(resolver)
    # Only a direct unaliased import with its sole actual callee use is eligible.
    for statement in module.tree.body:
        if isinstance(statement, ast.Import):
            assert not calls.source_primitive_import(module, statement, "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("provider", ["module", "package", "case", "link"])
def test_source_qualified_mapping_update_requires_absent_builtin_provider(namespace_workspace, provider):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import builtins\nslots = vars(fake)\nbuiltins.dict.update(slots, Agent=fake.Agent)",
    )
    if provider == "package":
        (namespace_workspace / "builtins").mkdir()
        (namespace_workspace / "builtins/update.py").write_text("VALUE = None\n")
    elif provider == "link":
        (namespace_workspace / "builtins.py").symlink_to("fake.py")
    else:
        (namespace_workspace / ("Builtins.py" if provider == "case" else "builtins.py")).write_text("VALUE = None\n")
    try:
        assert not BuilderCalls(resolver).source_primitive_import(module, module.tree.body[1], "agents")
    except CallLimit:
        pass
    assert not resolver._checking_source_module_slot


def test_source_qualified_mapping_update_raw_constructor_route_cannot_borrow_import_proof(namespace_workspace, monkeypatch):
    from agents_shipgate.inputs.python_imports import _external_constructor_use

    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import builtins\nslots = vars(fake)\nbuiltins.dict.update(slots, Agent=fake.Agent)",
    )

    def forbidden(*args, **kwargs):
        pytest.fail("raw constructor observation borrowed a completed primitive import proof")

    monkeypatch.setattr(BuilderCalls, "source_primitive_import", forbidden)
    assert _external_constructor_use(resolver, module, "agents", {module.path}, allow_owner_routes=False)
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("extra", [
    "saved = builtins\n", "builtins.dict\n", "__all__ = ['builtins']\n",
    "def capture(value=builtins):\n    pass\n", "del builtins\n", "builtins = fake\n",
])
def test_source_qualified_mapping_update_confines_actual_imported_root(namespace_workspace, extra):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import builtins\nslots = vars(fake)\nbuiltins.dict.update(slots, Agent=fake.Agent)",
        extra=extra,
    )
    calls = BuilderCalls(resolver)
    assert not calls.source_primitive_import(module, module.tree.body[1], "agents")
    assert calls._source_slot_candidate(module, module.tree.body[3]) is None
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("operation", ["import", "slot"])
def test_source_qualified_mapping_update_tree_context_cannot_borrow_live_disk_root(namespace_workspace, monkeypatch, operation):
    from agents_shipgate.inputs.python_imports import RepositoryLayout, repository_layout

    files = {
        "entry.py": "import fake\nimport builtins\nslots = vars(fake)\nbuiltins.dict.update(slots, Agent=fake.Agent)\n",
        "fake.py": "def Agent(tools):\n    return tools\n",
    }
    for name, source in files.items():
        (namespace_workspace / name).write_text(source)
    layout = RepositoryLayout("", lambda ref: frozenset(files) if ref == "" else None, read=lambda ref: files.get(ref))

    def forbidden(*args):
        pytest.fail("a tree primitive proof borrowed live directory currency")

    monkeypatch.setattr(BuilderCalls, "_namespace_directory_entries", forbidden)
    with repository_layout(layout):
        resolver = ImportResolver(namespace_workspace)
        source = files["entry.py"]
        module = resolver.entry(namespace_workspace / "entry.py", ast.parse(source), source)
        calls = BuilderCalls(resolver)
        with pytest.raises(CallLimit, match="no bound import-root currency for the dictionary initialization import"):
            if operation == "import":
                calls.source_primitive_import(module, module.tree.body[1], "agents")
            else:
                calls.idempotent_source_slot(module, module.tree.body[3], "agents")
        assert not resolver._checking_source_module_slot


def test_source_qualified_mapping_update_rechecks_provider_currency_and_import_reentry(namespace_workspace):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import builtins\nslots = vars(fake)\nbuiltins.dict.update(slots, Agent=fake.Agent)",
    )
    calls = BuilderCalls(resolver)
    declaration = module.tree.body[1]
    assert calls.source_primitive_import(module, declaration, "agents")
    resolver._checking_source_module_slot = True
    with pytest.raises(CallLimit, match="reentered"):
        calls.source_primitive_import(module, declaration, "agents")
    assert resolver._checking_source_module_slot
    resolver._checking_source_module_slot = False
    (namespace_workspace / "builtins.py").write_text("VALUE = None\n")
    with pytest.raises(CallLimit):
        calls.source_primitive_import(module, declaration, "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("projection", ["fake.__dict__", "vars(fake)", "getattr(fake, '__dict__')"])
@pytest.mark.parametrize("family", ["agents", "google.adk"])
def test_source_bare_setitem_keeps_positional_roles_and_uncached_function_proof(namespace_workspace, projection, family):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write=f"slots = {projection}\ndict.__setitem__(slots, 'Agent', fake.Agent)",
    )
    calls = BuilderCalls(resolver)
    statement = module.tree.body[-1]
    edge = calls._source_slot_edge(module, statement)
    assert edge.call is statement.value
    assert edge.callee is edge.call.func
    assert edge.primitive is edge.callee.value
    assert edge.target is edge.call.args[0]
    assert edge.parts[4] is edge.call.args[1]
    assert edge.rhs is edge.call.args[2]
    assert edge.payload is None and edge.primitive_import is None
    home, function, actual = calls._source_slot_candidate(module, statement)
    assert actual is statement
    before = calls.callers(home, function, allow_empty=True)
    assert before.limits and not before.sites
    for node in (statement, edge.call, edge.callee, edge.primitive, edge.target, edge.parts[4], edge.rhs):
        assert calls.idempotent_source_slot(module, node, family)
    assert not calls.idempotent_source_slot(module, module.tree.body[1], family)
    assert not calls.idempotent_source_slot(module, ast.parse("dict.__setitem__(slots, 'Agent', fake.Agent)").body[0], family)
    assert calls.uncalled_source_slot(home, function)
    assert calls.callers(home, function, allow_empty=True) is before
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("projection", ["fake.__dict__", "vars(fake)", "getattr(fake, '__dict__')"])
@pytest.mark.parametrize("write", [
    "slots.__setitem__('Agent', fake.Agent)", "builtins.dict.__setitem__(slots, 'Agent', fake.Agent)",
    "replace = dict.__setitem__\nreplace(slots, 'Agent', fake.Agent)",
    "dict.__setitem__(slots, 'Agent')", "dict.__setitem__(slots, 'Agent', fake.Agent, opaque)",
    "dict.__setitem__(slots, 'Agent', fake.Agent, other=opaque)",
    "dict.__setitem__(slots, key='Agent', value=fake.Agent)",
    "dict.__setitem__(*opaque)", "dict.__setitem__(slots, *opaque)",
    "dict.__setitem__(slots, key, fake.Agent)", "dict.__setitem__(slots, 'other', fake.Agent)",
    "dict.__setitem__(slots, '_Agent', fake._Agent)", "dict.__setitem__(slots, 'Agent', other.Agent)",
    "saved = dict.__setitem__(slots, 'Agent', fake.Agent)",
    "consume(dict.__setitem__(slots, 'Agent', fake.Agent))",
    "if condition:\n    dict.__setitem__(slots, 'Agent', fake.Agent)",
    "dict.__setitem__(slots, 'Agent', fake.Agent)\nsaved = slots",
    "dict.__setitem__(slots, 'Agent', fake.Agent)\ndict.__setitem__(slots, 'Agent', fake.Agent)",
    "dict = replacement\ndict.__setitem__(slots, 'Agent', fake.Agent)",
    "def apply(dict):\n    dict.__setitem__(slots, 'Agent', fake.Agent)",
])
def test_source_bare_setitem_refuses_other_shapes_and_mapping_uses(namespace_workspace, projection, write):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=f"slots = {projection}\n{write}")
    calls = BuilderCalls(resolver)
    for statement in ast.walk(module.tree):
        if isinstance(statement, ast.Assign | ast.Expr):
            assert calls._source_slot_candidate(module, statement) is None
            assert not calls.idempotent_source_slot(module, statement, "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("field", ["Agent", "dict", "update"])
@pytest.mark.parametrize("called", [False, True])
def test_source_bare_setitem_preserves_selected_function_calls_and_names(namespace_workspace, field, called):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write=f"slots = vars(fake)\ndict.__setitem__(slots, '{field}', fake.{field})",
        extra=f"fake.{field}([])\n" if called else "", home=f"def {field}(tools):\n    return tools\n",
    )
    calls = BuilderCalls(resolver)
    terminal = module.tree.body[2]
    edge = calls._source_slot_edge(module, terminal)
    assert edge.primitive is not edge.rhs.value
    home, function, _ = calls._source_slot_candidate(module, terminal)
    before = calls.callers(home, function, allow_empty=True)
    assert calls.idempotent_source_slot(module, terminal, "agents") is (not called)
    assert calls.uncalled_source_slot(home, function) is (not called)
    assert calls.callers(home, function, allow_empty=True) is before
    assert bool(before.sites) is called
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("consumer", ["from entry import slots\n", "from entry import fake\n", "import entry\n"])
def test_source_bare_setitem_checks_even_unused_mapping_importers(namespace_workspace, consumer):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="slots = vars(fake)\ndict.__setitem__(slots, 'Agent', fake.Agent)",
    )
    (namespace_workspace / "consumer.py").write_text(consumer)
    with pytest.raises(CallLimit, match="imported intrinsic source mapping namespace"):
        BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[2], "agents")
    assert not resolver._checking_source_module_slot


def test_source_bare_setitem_cannot_borrow_receiving_owner_callbacks(namespace_workspace, monkeypatch):
    from agents_shipgate.inputs import list_expressions

    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="slots = vars(fake)\ndict.__setitem__(slots, 'Agent', fake.Agent)",
    )
    def forbidden(*args, **kwargs):
        pytest.fail("a raw positional source write borrowed a receiving owner")
    for name in ("_independent_foreign_class_slot_write", "_independent_plain_class_slot_write",
                 "_agent_instance", "_provisional_agent_list_clear"):
        monkeypatch.setattr(list_expressions, name, forbidden)
    assert BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[2], "agents")


@pytest.mark.parametrize("extra", ["from provider import dict\n", "import builtins\nbuiltins.dict = replacement\n",
                                    "import builtins\nbuiltins.dict.__setitem__ = replacement\n", "fake.__class__ = replacement\n"])
def test_source_bare_setitem_requires_raw_primitive_stability(namespace_workspace, extra):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="slots = vars(fake)\ndict.__setitem__(slots, 'Agent', fake.Agent)", extra=extra,
    )
    try:
        assert not BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[2], "agents")
    except CallLimit:
        pass
    assert not resolver._checking_source_module_slot


def test_source_bare_setitem_rechecks_source_currency_and_reentry(namespace_workspace):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="slots = vars(fake)\ndict.__setitem__(slots, 'Agent', fake.Agent)",
    )
    calls = BuilderCalls(resolver)
    terminal = module.tree.body[2]
    assert calls.idempotent_source_slot(module, terminal, "agents")
    resolver._checking_source_module_slot = True
    with pytest.raises(CallLimit, match="reentered"):
        calls.idempotent_source_slot(module, terminal, "agents")
    assert resolver._checking_source_module_slot
    resolver._checking_source_module_slot = False
    (namespace_workspace / "fake.py").write_text("def Agent(tools):\n    return replacement\n")
    with pytest.raises(CallLimit):
        calls.idempotent_source_slot(module, terminal, "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("replacement", ["foreign.dict.__setitem__", "builtins.dict.update", "builtins.dict.__init__"])
def test_source_bare_setitem_requires_exact_actual_raw_call_identity(namespace_workspace, monkeypatch, replacement):
    from agents_shipgate.inputs import list_expressions

    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="slots = vars(fake)\ndict.__setitem__(slots, 'Agent', fake.Agent)",
    )
    calls = BuilderCalls(resolver)
    terminal = module.tree.body[2]
    assert calls.idempotent_source_slot(module, terminal, "agents")
    original = list_expressions._namespace_call_reference
    raw_original = list_expressions._qualified_attribute_patches
    raw_active = False
    def raw(view, **kwargs):
        nonlocal raw_active
        previous = raw_active
        raw_active = True
        try:
            return raw_original(view, **kwargs)
        finally:
            raw_active = previous
    def altered(view, call):
        if call is terminal.value and not raw_active:
            return replacement
        return original(view, call)
    monkeypatch.setattr(list_expressions, "_qualified_attribute_patches", raw)
    monkeypatch.setattr(list_expressions, "_namespace_call_reference", altered)
    with pytest.raises(CallLimit, match="unread dictionary initialization primitive"):
        calls.idempotent_source_slot(module, terminal, "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("family", ["agents", "google.adk"])
@pytest.mark.parametrize("field", ["Agent", "unrelated"])
def test_fresh_unbound_init_receipt_is_local_exact_data_not_a_caller_grant(namespace_workspace, family, field):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write=f"slots = {{}}\ndict.__init__(slots, {field}=fake.Agent)",
    )
    calls = BuilderCalls(resolver)
    value = module.tree.body[2].value.keywords[0].value
    resolution = calls.resolve(module, value)
    assert resolution.resolved and not resolution.caveats
    home, function = resolution.module, resolution.definition
    before = calls.callers(home, function, allow_empty=True)
    assert before.limits and not before.sites
    with calls.fresh_unbound_dictionary_receipt(module, value, family) as receipt:
        assert receipt is not None and receipt.values == frozenset({value})
        assert receipt.proof is not calls and receipt.proof.resolver is not resolver
        assert receipt.proof.resolver._modules is not resolver._modules
        ordinary = receipt.proof._census(home, function, allow_empty=True)
        assert ordinary.limits and not ordinary.sites
        confined = receipt.proof._census(
            home, function, allow_empty=True, confined_dictionary_data=True,
            unused_namespace_data=True, confined_primitive_dictionary_values=receipt.values,
        )
        assert not confined.limits and not confined.sites
        receipt.reconfirm()
        with pytest.raises(CallLimit, match="no longer active"):
            receipt.reconfirm()
    assert calls.callers(home, function, allow_empty=True) is before
    assert not resolver._checking_fresh_dictionary_primitive


@pytest.mark.parametrize("field", ["Agent", "dict", "update"])
def test_fresh_unbound_init_actual_function_calls_are_still_census_evidence(namespace_workspace, field):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write=f"slots = {{}}\ndict.__init__(slots, unrelated=fake.{field})",
        extra=f"fake.{field}([])\n", home=f"def {field}(tools):\n    return tools\n",
    )
    value = module.tree.body[2].value.keywords[0].value
    with BuilderCalls(resolver).fresh_unbound_dictionary_receipt(module, value, "agents") as receipt:
        assert receipt is not None
        resolution = receipt.proof.resolve(module, value)
        assert resolution.resolved
        census = receipt.proof._census(
            resolution.module, resolution.definition, allow_empty=True,
            confined_dictionary_data=True, unused_namespace_data=True,
            confined_primitive_dictionary_values=receipt.values,
        )
        assert census.sites
    assert not resolver._checking_fresh_dictionary_primitive
    with pytest.raises(CallLimit, match="no longer active"):
        receipt.reconfirm()


@pytest.mark.parametrize("write", [
    "slots = {}\nslots.__init__(Agent=fake.Agent)",
    "slots = {}\nbuiltins.dict.__init__(slots, Agent=fake.Agent)",
    "slots = dict()\ndict.__init__(slots, Agent=fake.Agent)",
    "slots = {'other': None}\ndict.__init__(slots, Agent=fake.Agent)",
    "slots = {}\ndict.__init__(slots, Agent=fake.Agent, other=None)",
    "slots = {}\ndict.__init__(slots, opaque, Agent=fake.Agent)",
    "slots = {}\ndict.__init__(*opaque, Agent=fake.Agent)",
    "slots = {}\ndict.__init__(slots, **opaque)",
    "slots = {}\nsaved = slots\ndict.__init__(slots, Agent=fake.Agent)",
    "slots = {}\ndict.__init__(slots, Agent=fake.Agent)\nconsume(slots)",
    "slots = {}\ndict.__init__(slots, Agent=fake.Agent)\nslots.clear()",
    "slots = {}\nif condition:\n    dict.__init__(slots, Agent=fake.Agent)",
    "slots = {}\ndict = replacement\ndict.__init__(slots, Agent=fake.Agent)",
])
def test_fresh_unbound_init_refuses_other_allocations_shapes_and_receiver_uses(namespace_workspace, write):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=write)
    calls = BuilderCalls(resolver)
    for node in ast.walk(module.tree):
        if isinstance(node, ast.Attribute) and node.attr == "Agent":
            with calls.fresh_unbound_dictionary_receipt(module, node, "agents") as receipt:
                assert receipt is None
    assert not resolver._checking_fresh_dictionary_primitive


@pytest.mark.parametrize("mutation", ["source", "new_file", "new_directory", "provider", "venv_selector"])
def test_fresh_unbound_init_final_identity_rejects_changes_after_zero_census(namespace_workspace, mutation):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="slots = {}\ndict.__init__(slots, Agent=fake.Agent)",
    )
    value = module.tree.body[2].value.keywords[0].value
    calls = BuilderCalls(resolver)
    with calls.fresh_unbound_dictionary_receipt(module, value, "agents") as receipt:
        assert receipt is not None
        resolution = receipt.proof.resolve(module, value)
        assert resolution.resolved and not resolution.caveats
        home, function = resolution.module, resolution.definition
        census = receipt.proof._census(
            home, function, allow_empty=True, confined_dictionary_data=True,
            unused_namespace_data=True, confined_primitive_dictionary_values=receipt.values,
        )
        assert not census.sites and not census.limits
        if mutation == "source":
            (namespace_workspace / "fake.py").write_text("def Agent(tools):\n    return changed\n")
        elif mutation == "new_file":
            (namespace_workspace / "new.py").write_text("VALUE = None\n")
        elif mutation == "new_directory":
            (namespace_workspace / "new").mkdir()
        elif mutation == "provider":
            (namespace_workspace / "agents.py").write_text("VALUE = None\n")
        else:
            (namespace_workspace / "pyvenv.cfg").write_text("home = changed\n")
        with pytest.raises((CallLimit, ValueError, OSError)):
            receipt.reconfirm()
    assert not resolver._checking_fresh_dictionary_primitive


@pytest.mark.parametrize("consumer", ["from entry import slots\n", "import entry\n"])
def test_fresh_unbound_init_refuses_even_unused_dictionary_importers(namespace_workspace, consumer):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="slots = {}\ndict.__init__(slots, Agent=fake.Agent)",
    )
    (namespace_workspace / "consumer.py").write_text(consumer)
    with pytest.raises(CallLimit, match="imported fresh dictionary allocation"):
        with BuilderCalls(resolver).fresh_unbound_dictionary_receipt(
            module, module.tree.body[2].value.keywords[0].value, "agents",
        ):
            pytest.fail("an imported dictionary received a confined-data receipt")
    assert not resolver._checking_fresh_dictionary_primitive


@pytest.mark.parametrize("change", ["source", "directory"])
def test_fresh_unbound_init_rejects_initial_stale_snapshot_evidence(namespace_workspace, change):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="slots = {}\ndict.__init__(slots, Agent=fake.Agent)",
    )
    snapshot = StaticInputSnapshot(namespace_workspace)
    token = activate_static_input_snapshot(snapshot)
    try:
        BuilderCalls(resolver).namespace_source_context("agents")
        if change == "source":
            (namespace_workspace / "fake.py").write_text("def Agent(tools):\n    return changed\n")
        else:
            (namespace_workspace / "new.py").write_text("VALUE = None\n")
        with pytest.raises(CallLimit):
            with BuilderCalls(resolver).fresh_unbound_dictionary_receipt(
                module, module.tree.body[2].value.keywords[0].value, "agents",
            ):
                pytest.fail("stale snapshot data received a fresh receipt")
    finally:
        reset_static_input_snapshot(token)
    assert not resolver._checking_fresh_dictionary_primitive


def test_fresh_unbound_init_preserves_unknown_family_and_both_reentry_guards(namespace_workspace):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="slots = {}\ndict.__init__(slots, Agent=fake.Agent)",
    )
    calls = BuilderCalls(resolver)
    value = module.tree.body[2].value.keywords[0].value
    with calls.fresh_unbound_dictionary_receipt(module, value, "unknown") as receipt:
        assert receipt is None
    with calls.fresh_unbound_dictionary_receipt(module, value, "agents") as receipt:
        assert receipt is not None
        for caller in (calls, receipt.proof):
            with pytest.raises(CallLimit, match="reentered"):
                with caller.fresh_unbound_dictionary_receipt(module, value, "agents"):
                    pytest.fail("a nested primitive receipt reentered")
        receipt.reconfirm()
    assert not resolver._checking_fresh_dictionary_primitive
    assert not receipt.proof.resolver._checking_fresh_dictionary_primitive


def test_fresh_member_sink_requires_actual_queued_edge_and_exact_function_identity(namespace_workspace):
    from agents_shipgate.inputs.list_expressions import ListExpressions

    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="slots = {}\ndict.__init__(slots, Agent=fake.Agent)",
        home="def Agent(tools):\n    tools.append(None)\ndef other(tools):\n    tools.append(None)\n",
    )
    calls = BuilderCalls(resolver)
    data = module.tree.body[2].value.keywords[0].value
    resolution = calls.resolve(module, data)
    home, function = resolution.module, resolution.definition
    expression = function.body[0].value.func.value
    reader = ListExpressions(
        ref=module.ref, tree=module.tree, scopes=calls.scopes(module), bindings=module.bindings,
        module=module, resolver=resolver, agent_reads=lambda *args: False, builder_calls=calls,
    )
    view = reader._foreign(home)
    ordinary = calls.callers(home, function, allow_empty=True)
    before = calls.callers(home, function, allow_empty=True, confined_dictionary_data=True,
                           unused_namespace_data=True)
    assert before.limits and not before.sites
    assert not reader._uncalled_member_sink(view, expression)
    resolver._constructor_dictionary_sinks[("agents", id(data))] = (module, data, "agents")
    assert not reader._uncalled_member_sink(view, expression)
    resolver._constructor_member_sinks[id(expression)] = (home, expression)
    assert not reader._uncalled_member_sink(view, expression)
    reader._checking_constructor_namespaces = True
    assert reader._uncalled_member_sink(view, expression)
    assert not reader._fresh_dictionary_function_sink_owned(
        reader.entry, data, "agents", expected_module=home, expected_function=home.tree.body[1],
    )
    assert calls.callers(home, function, allow_empty=True, confined_dictionary_data=True,
                         unused_namespace_data=True) == before
    assert calls.callers(home, function, allow_empty=True) is ordinary
    assert not resolver._checking_fresh_dictionary_primitive


@pytest.mark.parametrize("family", ["agents", "google.adk"])
@pytest.mark.parametrize("field", ["Agent", "unrelated"])
def test_fresh_qualified_init_admits_only_actual_import_and_exact_data(namespace_workspace, family, field):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write=f"import builtins\nslots = {{}}\nbuiltins.dict.__init__(slots, {field}=fake.Agent)",
    )
    calls = BuilderCalls(resolver)
    statement = module.tree.body[1]
    value = module.tree.body[3].value.keywords[0].value
    edge = calls._fresh_unbound_dictionary_edge(module, value)
    record = edge[4]
    assert record.statement is statement
    assert record.imported is statement.names[0]
    assert record.root is module.tree.body[3].value.func.value.value
    assert record.primitive is module.tree.body[3].value.func.value
    resolution = calls.resolve(module, value)
    before = calls.callers(resolution.module, resolution.definition, allow_empty=True)
    assert before.limits and not before.sites
    assert calls.fresh_dictionary_primitive_import(module, statement, family)
    assert not calls.fresh_dictionary_primitive_import(module, ast.parse("import builtins").body[0], family)
    assert not calls.fresh_dictionary_primitive_import(module, record.imported, family)
    with calls.fresh_unbound_dictionary_receipt(module, value, family) as receipt:
        assert receipt is not None and receipt.values == frozenset({value})
        receipt.reconfirm()
    assert calls.callers(resolution.module, resolution.definition, allow_empty=True) is before
    assert before.limits
    assert not resolver._checking_fresh_dictionary_primitive


@pytest.mark.parametrize("write", [
    "slots = {}\nbuiltins.dict.__init__(slots, Agent=fake.Agent)",
    "from builtins import dict\nslots = {}\ndict.__init__(slots, Agent=fake.Agent)",
    "import builtins as ordinary\nslots = {}\nordinary.dict.__init__(slots, Agent=fake.Agent)",
    "import builtins\nslots = {}\nbuiltins = replacement\nbuiltins.dict.__init__(slots, Agent=fake.Agent)",
    "import builtins\nsaved = builtins\nslots = {}\nbuiltins.dict.__init__(slots, Agent=fake.Agent)",
    "import builtins\nslots = {}\nbuiltins.dict.__init__(slots, Agent=fake.Agent)\nbuiltins.dict.__init__(slots, Agent=fake.Agent)",
])
def test_fresh_qualified_init_refuses_missing_aliased_rebound_or_shared_import(namespace_workspace, write):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=write)
    calls = BuilderCalls(resolver)
    for node in module.tree.body:
        if isinstance(node, ast.Import):
            assert not calls.fresh_dictionary_primitive_import(module, node, "agents")
    assert not resolver._checking_fresh_dictionary_primitive


@pytest.mark.parametrize("consumer", ["from entry import builtins\n", "from entry import slots\n", "import entry\n"])
def test_fresh_qualified_init_checks_unused_importers_of_both_roots(namespace_workspace, consumer):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import builtins\nslots = {}\nbuiltins.dict.__init__(slots, Agent=fake.Agent)",
    )
    (namespace_workspace / "consumer.py").write_text(consumer)
    with pytest.raises(CallLimit, match="imported fresh dictionary"):
        BuilderCalls(resolver).fresh_dictionary_primitive_import(module, module.tree.body[1], "agents")
    assert not resolver._checking_fresh_dictionary_primitive


def test_fresh_qualified_import_adapter_keeps_raw_refusal_and_does_not_borrow_owners(namespace_workspace, monkeypatch):
    from agents_shipgate.inputs import list_expressions, python_imports

    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import builtins\nslots = {}\nbuiltins.dict.__init__(slots, Agent=fake.Agent)",
    )
    def forbidden(*args, **kwargs):
        pytest.fail("a qualified fresh import borrowed a receiving owner")
    for name in ("_independent_foreign_class_slot_write", "_independent_plain_class_slot_write",
                 "_agent_instance", "_provisional_agent_list_clear"):
        monkeypatch.setattr(list_expressions, name, forbidden)
    assert BuilderCalls(resolver).fresh_dictionary_primitive_import(module, module.tree.body[1], "agents")
    result = python_imports._external_constructor_use(
        resolver, module, "agents", {module.path}, allow_owner_routes=False,
    )
    assert result and "builtin or dynamic import machinery" in result
    assert not resolver._checking_fresh_dictionary_primitive


def test_fresh_qualified_import_adapter_cannot_erase_actual_function_calls(namespace_workspace):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import builtins\nslots = {}\nbuiltins.dict.__init__(slots, Agent=fake.Agent)",
        extra="fake.Agent([])\n",
    )
    assert not BuilderCalls(resolver).fresh_dictionary_primitive_import(module, module.tree.body[1], "agents")
    assert not resolver._checking_fresh_dictionary_primitive


@pytest.mark.parametrize("change", ["builtin_provider", "source", "new_initializer"])
def test_fresh_qualified_init_reconfirms_sources_and_import_currency_after_zero_census(namespace_workspace, change):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import builtins\nslots = {}\nbuiltins.dict.__init__(slots, Agent=fake.Agent)",
    )
    calls = BuilderCalls(resolver)
    value = module.tree.body[3].value.keywords[0].value
    with calls.fresh_unbound_dictionary_receipt(module, value, "agents") as receipt:
        assert receipt is not None
        resolution = receipt.proof.resolve(module, value)
        census = receipt.proof._census(
            resolution.module, resolution.definition, allow_empty=True,
            confined_dictionary_data=True, unused_namespace_data=True,
            confined_primitive_dictionary_values=receipt.values,
        )
        assert not census.sites and not census.limits
        if change == "builtin_provider":
            (namespace_workspace / "builtins.py").write_text("dict = replacement\n")
        elif change == "source":
            (namespace_workspace / "fake.py").write_text("def Agent(tools):\n    return changed\n")
        else:
            (namespace_workspace / "__init__.py").write_text("VALUE = None\n")
        with pytest.raises((CallLimit, ValueError, OSError)):
            receipt.reconfirm()
    assert not resolver._checking_fresh_dictionary_primitive


@pytest.mark.parametrize("home", [
    "class Agent:\n    pass\n",
    "@decorate\ndef Agent(tools):\n    return tools\n",
    "Agent = opaque\n",
])
def test_fresh_qualified_init_import_admission_requires_read_undecorated_function(namespace_workspace, home):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import builtins\nslots = {}\nbuiltins.dict.__init__(slots, Agent=fake.Agent)",
        home=home,
    )
    try:
        assert not BuilderCalls(resolver).fresh_dictionary_primitive_import(module, module.tree.body[1], "agents")
    except CallLimit:
        pass
    assert not resolver._checking_fresh_dictionary_primitive


def test_fresh_qualified_init_adapter_preserves_unknown_family_and_reentry(namespace_workspace):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import builtins\nslots = {}\nbuiltins.dict.__init__(slots, Agent=fake.Agent)",
    )
    calls = BuilderCalls(resolver)
    statement = module.tree.body[1]
    assert not calls.fresh_dictionary_primitive_import(module, statement, "unknown")
    value = module.tree.body[3].value.keywords[0].value
    with calls.fresh_unbound_dictionary_receipt(module, value, "agents") as receipt:
        assert receipt is not None
        with pytest.raises(CallLimit, match="reentered"):
            calls.fresh_dictionary_primitive_import(module, statement, "agents")
        receipt.reconfirm()
    assert not resolver._checking_fresh_dictionary_primitive


@pytest.mark.parametrize("family", ["agents", "google.adk"])
@pytest.mark.parametrize("names", [("producer", "mapping", "Handler", "extra_field"),
                                  ("service", "table", "dispatch", "new_entry")])
def test_absent_field_bound_init_has_independent_actual_roles(namespace_workspace, family, names):
    source, mapping, function, field = names
    (namespace_workspace / f"{source}.py").write_text(f"def {function}(tools):\n    return tools\n")
    text = f"import {source}\n{mapping} = vars({source})\n{mapping}.__init__({field}={source}.{function})\n"
    path = namespace_workspace / "entry.py"
    path.write_text(text)
    resolver = ImportResolver(namespace_workspace)
    module = resolver.entry(path, ast.parse(text), text)
    calls = BuilderCalls(resolver)
    getter = module.tree.body[1].value.func
    value = module.tree.body[2].value.keywords[0].value
    resolution = calls.resolve(module, value)
    ordinary = calls.callers(resolution.module, resolution.definition, allow_empty=True)
    assert ordinary.limits and not ordinary.sites
    assert calls.absent_field_dictionary_getter(module, getter, family)
    assert not calls.absent_field_dictionary_getter(module, ast.Name(id="vars", ctx=ast.Load()), family)
    with calls.absent_field_dictionary_receipt(module, value, family) as receipt:
        assert receipt is not None
        assert receipt.roles.home is resolution.module
        assert receipt.roles.function is resolution.definition
        assert receipt.roles.value is value
        census = receipt.census()
        assert not census.sites and not census.limits
        receipt.reconfirm()
        with pytest.raises(CallLimit, match="active exact census"):
            receipt.census()
    assert calls.callers(resolution.module, resolution.definition, allow_empty=True) is ordinary
    assert not resolver._checking_fresh_dictionary_primitive


@pytest.mark.parametrize("home,extra", [
    ("unrelated = None\n", ""),
    ("unrelated: int\n", ""),
    ("", "result = fake.unrelated\n"),
    ("", "from fake import unrelated\n"),
    ("", "saved = fake\n"),
    ("", "saved = slots\n"),
    ("", "fake.Agent([])\n"),
    ("", "result = 'unrelated'\n"),
    ("", "slots.__init__(unrelated=fake.Agent)\n"),
])
def test_absent_field_bound_init_refuses_observers_existing_fields_escapes_and_calls(namespace_workspace, home, extra):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="slots = vars(fake)\nslots.__init__(unrelated=fake.Agent)", extra=extra,
        home=home + "def Agent(tools):\n    return tools\n",
    )
    calls = BuilderCalls(resolver)
    try:
        assert not calls.absent_field_dictionary_getter(module, module.tree.body[1].value.func, "agents")
    except CallLimit:
        pass
    assert not resolver._checking_fresh_dictionary_primitive


@pytest.mark.parametrize("write", [
    "slots = vars(fake)\nslots.__init__(__loader__=fake.Agent)",
    "slots = vars(fake)\nslots.__init__(vars=fake.Agent)",
    "slots = vars(fake)\ndict.__init__(slots, unrelated=fake.Agent, extra_field=fake.Agent)",
    "import builtins\nslots = vars(fake)\nbuiltins.dict.__init__(slots, unrelated=fake.Agent, extra_field=fake.Agent)",
    "saved = vars\nslots = saved(fake)\nslots.__init__(unrelated=fake.Agent)",
    "slots = fake.__dict__\nslots.__init__(unrelated=fake.Agent)",
])
def test_absent_field_bound_init_does_not_widen_getters_primitives_or_metadata(namespace_workspace, write):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=write)
    calls = BuilderCalls(resolver)
    value = module.tree.body[-1].value.keywords[0].value
    try:
        with calls.absent_field_dictionary_receipt(module, value, "agents") as receipt:
            assert receipt is None
    except CallLimit:
        pass
    assert not resolver._checking_fresh_dictionary_primitive


def test_absent_field_getter_preserves_raw_constructor_refusal(namespace_workspace):
    from agents_shipgate.inputs import python_imports

    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="slots = vars(fake)\nslots.__init__(unrelated=fake.Agent)",
    )
    result = python_imports._external_constructor_use(
        resolver, module, "agents", {module.path}, allow_owner_routes=False,
    )
    assert result and "reflective machinery" in result


@pytest.mark.parametrize("consumer", ["from fake import Agent\n", "import fake\n", "from entry import slots\n"])
def test_absent_field_bound_init_refuses_imported_namespace_and_mapping(namespace_workspace, consumer):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="slots = vars(fake)\nslots.__init__(unrelated=fake.Agent)",
    )
    (namespace_workspace / "consumer.py").write_text(consumer)
    try:
        assert not BuilderCalls(resolver).absent_field_dictionary_getter(module, module.tree.body[1].value.func, "agents")
    except CallLimit:
        pass


@pytest.mark.parametrize("change", ["source", "provider", "observer"])
def test_absent_field_bound_init_reconfirms_after_zero_census(namespace_workspace, change):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="slots = vars(fake)\nslots.__init__(unrelated=fake.Agent)",
    )
    value = module.tree.body[2].value.keywords[0].value
    with BuilderCalls(resolver).absent_field_dictionary_receipt(module, value, "agents") as receipt:
        assert receipt is not None
        census = receipt.census()
        assert not census.sites and not census.limits
        if change == "source":
            (namespace_workspace / "fake.py").write_text("unrelated = None\ndef Agent(tools):\n    return tools\n")
        elif change == "provider":
            (namespace_workspace / "builtins.py").write_text("vars = None\n")
        else:
            (namespace_workspace / "observer.py").write_text("from entry import slots\n")
        with pytest.raises((CallLimit, ValueError, OSError)):
            receipt.reconfirm()
    assert not resolver._checking_fresh_dictionary_primitive


def test_absent_field_roles_cannot_enter_another_census(namespace_workspace):
    from dataclasses import replace

    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="slots = vars(fake)\nslots.__init__(unrelated=fake.Agent)",
    )
    calls = BuilderCalls(resolver)
    value = module.tree.body[2].value.keywords[0].value
    with calls.absent_field_dictionary_receipt(module, value, "agents") as receipt:
        assert receipt is not None
        with pytest.raises(CallLimit, match="active exact census"):
            calls._census(receipt.roles.home, receipt.roles.function, allow_empty=True,
                          absent_field_roles=receipt.roles)
        with pytest.raises(CallLimit, match="active exact census"):
            receipt.proof._census(receipt.roles.home, receipt.roles.function, allow_empty=True,
                                 absent_field_roles=replace(receipt.roles, home=module))
        assert not receipt.census().limits
        receipt.reconfirm()


def test_absent_field_bound_init_never_borrows_same_slot_or_receiving_owners(namespace_workspace, monkeypatch):
    from agents_shipgate.inputs import list_expressions

    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="slots = vars(fake)\nslots.__init__(unrelated=fake.Agent)",
    )
    def forbidden(*args, **kwargs):
        pytest.fail("an unread-field proof borrowed another ownership result")
    monkeypatch.setattr(BuilderCalls, "idempotent_source_slot", forbidden)
    for name in ("_independent_foreign_class_slot_write", "_independent_plain_class_slot_write",
                 "_agent_instance", "_provisional_agent_list_clear"):
        monkeypatch.setattr(list_expressions, name, forbidden)
    assert BuilderCalls(resolver).absent_field_dictionary_getter(module, module.tree.body[1].value.func, "agents")


def test_absent_field_bound_init_keeps_explicit_family_and_reentry_boundary(namespace_workspace):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="slots = vars(fake)\nslots.__init__(unrelated=fake.Agent)",
    )
    calls = BuilderCalls(resolver)
    value = module.tree.body[2].value.keywords[0].value
    with calls.absent_field_dictionary_receipt(module, value, "unknown") as receipt:
        assert receipt is None
    with calls.absent_field_dictionary_receipt(module, value, "agents") as receipt:
        assert receipt is not None
        with pytest.raises(CallLimit, match="reentered"):
            with calls.absent_field_dictionary_receipt(module, value, "agents"):
                pass
        receipt.reconfirm()
    assert not resolver._checking_fresh_dictionary_primitive


@pytest.mark.parametrize("family", ["agents", "google.adk"])
@pytest.mark.parametrize("names", [("producer", "mapping", "Handler", "extra_field"),
                                  ("service", "table", "dispatch", "new_entry")])
def test_absent_field_unbound_init_proves_exact_primitive_and_distinct_roles(namespace_workspace, family, names):
    source, mapping, function, field = names
    (namespace_workspace / f"{source}.py").write_text(f"def {function}(tools):\n    return tools\n")
    text = f"import {source}\n{mapping} = vars({source})\ndict.__init__({mapping}, {field}={source}.{function})\n"
    path = namespace_workspace / "entry.py"
    path.write_text(text)
    resolver = ImportResolver(namespace_workspace)
    module = resolver.entry(path, ast.parse(text), text)
    calls = BuilderCalls(resolver)
    getter = module.tree.body[1].value.func
    call = module.tree.body[2].value
    value = call.keywords[0].value
    edge = calls._absent_field_dictionary_edge(module, value)
    assert edge[6] is call.func.value
    resolution = calls.resolve(module, value)
    ordinary = calls.callers(resolution.module, resolution.definition, allow_empty=True)
    assert ordinary.limits and not ordinary.sites
    assert calls.absent_field_dictionary_getter(module, getter, family)
    assert calls.absent_field_dictionary_namespace(module, module.tree.body[1].value.args[0], family)
    assert not calls.absent_field_dictionary_namespace(module, value.value, family)
    with calls.absent_field_dictionary_receipt(module, value, family) as receipt:
        assert receipt is not None
        assert call.func.value in receipt.roles.namespace_nodes
        census = receipt.census()
        assert not census.sites and not census.limits
        receipt.reconfirm()
    assert calls.callers(resolution.module, resolution.definition, allow_empty=True) is ordinary
    assert not resolver._checking_fresh_dictionary_primitive


@pytest.mark.parametrize("write", [
    "dict = replacement\nslots = vars(fake)\ndict.__init__(slots, unrelated=fake.Agent)",
    "slots = vars(fake)\ndict.__init__(slots, slots, unrelated=fake.Agent)",
    "slots = vars(fake)\ndict.__init__(slots, unrelated=fake.Agent, other=fake.Agent)",
    "slots = vars(fake)\ndict.__init__(slots, **payload)",
    "replace = dict.__init__\nslots = vars(fake)\nreplace(slots, unrelated=fake.Agent)",
    "import builtins\nslots = vars(fake)\nbuiltins.dict.__init__(slots, unrelated=fake.Agent, extra_field=fake.Agent)",
    "from builtins import dict\nslots = vars(fake)\ndict.__init__(slots, unrelated=fake.Agent)",
])
def test_absent_field_unbound_init_keeps_shadow_alias_payload_and_qualified_refusals(namespace_workspace, write):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=write)
    call = module.tree.body[-1].value
    value = call.keywords[0].value
    try:
        with BuilderCalls(resolver).absent_field_dictionary_receipt(module, value, "agents") as receipt:
            assert receipt is None
    except CallLimit:
        pass
    assert not resolver._checking_fresh_dictionary_primitive


@pytest.mark.parametrize("extra", ["fake.Agent([])\n", "saved = slots\n", "result = fake.unrelated\n"])
def test_absent_field_unbound_init_keeps_calls_escape_and_field_observer_visible(namespace_workspace, extra):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="slots = vars(fake)\ndict.__init__(slots, unrelated=fake.Agent)", extra=extra,
    )
    try:
        assert not BuilderCalls(resolver).absent_field_dictionary_getter(module, module.tree.body[1].value.func, "agents")
    except CallLimit:
        pass


@pytest.mark.parametrize("change", ["primitive", "source", "importer"])
def test_absent_field_unbound_init_final_census_currency_remains_bound(namespace_workspace, change):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="slots = vars(fake)\ndict.__init__(slots, unrelated=fake.Agent)",
    )
    value = module.tree.body[2].value.keywords[0].value
    with BuilderCalls(resolver).absent_field_dictionary_receipt(module, value, "agents") as receipt:
        assert receipt is not None
        assert not receipt.census().limits
        if change == "primitive":
            with (namespace_workspace / "entry.py").open("a") as out:
                out.write("dict = replacement\n")
        elif change == "source":
            (namespace_workspace / "fake.py").write_text("unrelated = None\ndef Agent(tools):\n    return tools\n")
        else:
            (namespace_workspace / "observer.py").write_text("from entry import slots\n")
        with pytest.raises((CallLimit, ValueError, OSError)):
            receipt.reconfirm()
    assert not resolver._checking_fresh_dictionary_primitive


@pytest.mark.parametrize("family", ["agents", "google.adk"])
@pytest.mark.parametrize("called", [False, True])
def test_absent_field_unbound_init_does_not_confuse_source_function_with_primitive_method(namespace_workspace, family, called):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="slots = vars(fake)\ndict.__init__(slots, unrelated=fake.__init__)",
        extra="fake.__init__([])\n" if called else "",
        home="def __init__(tools):\n    return tools\n",
    )
    assert BuilderCalls(resolver).absent_field_dictionary_getter(module, module.tree.body[1].value.func, family) is (not called)
    assert not resolver._checking_fresh_dictionary_primitive


@pytest.mark.parametrize("family", ["agents", "google.adk"])
@pytest.mark.parametrize("names", [("producer", "mapping", "Handler", "extra_field"),
                                  ("service", "table", "dispatch", "new_entry")])
def test_absent_field_qualified_init_binds_actual_import_and_independent_data(namespace_workspace, family, names):
    source, mapping, function, field = names
    (namespace_workspace / f"{source}.py").write_text(f"def {function}(tools):\n    return tools\n")
    text = f"import {source}\nimport builtins\n{mapping} = vars({source})\nbuiltins.dict.__init__({mapping}, {field}={source}.{function})\n"
    path = namespace_workspace / "entry.py"
    path.write_text(text)
    resolver = ImportResolver(namespace_workspace)
    module = resolver.entry(path, ast.parse(text), text)
    calls = BuilderCalls(resolver)
    statement = module.tree.body[1]
    value = module.tree.body[3].value.keywords[0].value
    record = calls._absent_field_dictionary_edge(module, value)[7]
    assert record.statement is statement and record.imported is statement.names[0]
    assert record.primitive is module.tree.body[3].value.func.value
    resolution = calls.resolve(module, value)
    ordinary = calls.callers(resolution.module, resolution.definition, allow_empty=True)
    assert ordinary.limits and not ordinary.sites
    assert calls.absent_field_dictionary_primitive_import(module, statement, family)
    assert not calls.absent_field_dictionary_primitive_import(module, ast.parse("import builtins").body[0], family)
    assert not calls.absent_field_dictionary_primitive_import(module, record.imported, family)
    with calls.absent_field_dictionary_receipt(module, value, family) as receipt:
        assert receipt is not None
        census = receipt.census()
        assert not census.sites and not census.limits
        receipt.reconfirm()
    assert calls.callers(resolution.module, resolution.definition, allow_empty=True) is ordinary
    assert not resolver._checking_fresh_dictionary_primitive


@pytest.mark.parametrize("write", [
    "slots = vars(fake)\nbuiltins.dict.__init__(slots, unrelated=fake.Agent)",
    "slots = vars(fake)\nbuiltins.dict.__init__(slots, unrelated=fake.Agent)\nimport builtins",
    "import builtins as ordinary\nslots = vars(fake)\nordinary.dict.__init__(slots, unrelated=fake.Agent)",
    "from builtins import dict\nslots = vars(fake)\ndict.__init__(slots, unrelated=fake.Agent)",
    "import builtins\nbuiltins = replacement\nslots = vars(fake)\nbuiltins.dict.__init__(slots, unrelated=fake.Agent)",
    "import builtins\nsaved = builtins\nslots = vars(fake)\nbuiltins.dict.__init__(slots, unrelated=fake.Agent)",
    "import builtins\nslots = vars(fake)\nbuiltins.dict.__init__(slots, unrelated=fake.Agent, other=fake.Agent)",
])
def test_absent_field_qualified_init_keeps_actual_import_and_payload_boundary(namespace_workspace, write):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=write)
    calls = BuilderCalls(resolver)
    for node in module.tree.body:
        if isinstance(node, ast.Import):
            assert not calls.absent_field_dictionary_primitive_import(module, node, "agents")
    assert not resolver._checking_fresh_dictionary_primitive


@pytest.mark.parametrize("consumer", ["from entry import builtins\n", "from entry import slots\n", "import entry\n"])
def test_absent_field_qualified_init_refuses_all_writer_namespace_imports(namespace_workspace, consumer):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import builtins\nslots = vars(fake)\nbuiltins.dict.__init__(slots, unrelated=fake.Agent)",
    )
    (namespace_workspace / "consumer.py").write_text(consumer)
    with pytest.raises(CallLimit, match="imports"):
        BuilderCalls(resolver).absent_field_dictionary_primitive_import(module, module.tree.body[1], "agents")
    assert not resolver._checking_fresh_dictionary_primitive


@pytest.mark.parametrize("extra", ["fake.Agent([])\n", "result = fake.unrelated\n", "saved = slots\n"])
def test_absent_field_qualified_import_requires_zero_calls_and_no_field_observer(namespace_workspace, extra):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import builtins\nslots = vars(fake)\nbuiltins.dict.__init__(slots, unrelated=fake.Agent)",
        extra=extra,
    )
    try:
        assert not BuilderCalls(resolver).absent_field_dictionary_primitive_import(module, module.tree.body[1], "agents")
    except CallLimit:
        pass
    assert not resolver._checking_fresh_dictionary_primitive


def test_absent_field_qualified_import_never_borrows_owners_and_preserves_raw_refusal(namespace_workspace, monkeypatch):
    from agents_shipgate.inputs import list_expressions, python_imports

    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import builtins\nslots = vars(fake)\nbuiltins.dict.__init__(slots, unrelated=fake.Agent)",
    )
    def forbidden(*args, **kwargs):
        pytest.fail("the qualified unread-field proof borrowed a receiving owner")
    monkeypatch.setattr(BuilderCalls, "idempotent_source_slot", forbidden)
    for name in ("_independent_foreign_class_slot_write", "_independent_plain_class_slot_write",
                 "_agent_instance", "_provisional_agent_list_clear"):
        monkeypatch.setattr(list_expressions, name, forbidden)
    assert BuilderCalls(resolver).absent_field_dictionary_primitive_import(module, module.tree.body[1], "agents")
    result = python_imports._external_constructor_use(resolver, module, "agents", {module.path}, allow_owner_routes=False)
    assert result and "builtin or dynamic import machinery" in result


@pytest.mark.parametrize("change", ["provider", "source", "importer"])
def test_absent_field_qualified_init_final_receipt_reconfirms_all_provenance(namespace_workspace, change):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import builtins\nslots = vars(fake)\nbuiltins.dict.__init__(slots, unrelated=fake.Agent)",
    )
    value = module.tree.body[3].value.keywords[0].value
    with BuilderCalls(resolver).absent_field_dictionary_receipt(module, value, "agents") as receipt:
        assert receipt is not None
        assert not receipt.census().limits
        if change == "provider":
            (namespace_workspace / "builtins.py").write_text("dict = None\n")
        elif change == "source":
            (namespace_workspace / "fake.py").write_text("unrelated = None\ndef Agent(tools):\n    return tools\n")
        else:
            (namespace_workspace / "observer.py").write_text("from entry import builtins\n")
        with pytest.raises((CallLimit, ValueError, OSError)):
            receipt.reconfirm()
    assert not resolver._checking_fresh_dictionary_primitive


def test_absent_field_qualified_import_keeps_explicit_family_and_reentry_refusal(namespace_workspace):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import builtins\nslots = vars(fake)\nbuiltins.dict.__init__(slots, unrelated=fake.Agent)",
    )
    calls = BuilderCalls(resolver)
    statement = module.tree.body[1]
    assert not calls.absent_field_dictionary_primitive_import(module, statement, "unknown")
    value = module.tree.body[3].value.keywords[0].value
    with calls.absent_field_dictionary_receipt(module, value, "agents") as receipt:
        assert receipt is not None
        with pytest.raises(CallLimit, match="reentered"):
            calls.absent_field_dictionary_primitive_import(module, statement, "agents")
        receipt.reconfirm()
    assert not resolver._checking_fresh_dictionary_primitive


@pytest.mark.parametrize("family", ["agents", "google.adk"])
@pytest.mark.parametrize("names", [("producer", "mapping", "Handler", "extra_field"),
                                  ("service", "table", "dispatch", "new_entry")])
def test_absent_field_bound_update_reuses_only_independent_source_data_roles(namespace_workspace, family, names):
    source, mapping, function, field = names
    (namespace_workspace / f"{source}.py").write_text(f"def {function}(tools):\n    return tools\n")
    text = f"import {source}\n{mapping} = vars({source})\n{mapping}.update({field}={source}.{function})\n"
    path = namespace_workspace / "entry.py"
    path.write_text(text)
    resolver = ImportResolver(namespace_workspace)
    module = resolver.entry(path, ast.parse(text), text)
    calls = BuilderCalls(resolver)
    getter = module.tree.body[1].value.func
    value = module.tree.body[2].value.keywords[0].value
    edge = calls._absent_field_dictionary_edge(module, value)
    assert edge[6] is None and edge[7] is None
    resolution = calls.resolve(module, value)
    ordinary = calls.callers(resolution.module, resolution.definition, allow_empty=True)
    assert ordinary.limits and not ordinary.sites
    assert calls.absent_field_dictionary_getter(module, getter, family)
    with calls.absent_field_dictionary_receipt(module, value, family) as receipt:
        assert receipt is not None
        assert receipt.roles.value is value
        census = receipt.census()
        assert not census.sites and not census.limits
        receipt.reconfirm()
    assert calls.callers(resolution.module, resolution.definition, allow_empty=True) is ordinary


@pytest.mark.parametrize("write", [
    "slots = vars(fake)\ndict.update(slots, unrelated=fake.Agent)",
    "import builtins\nslots = vars(fake)\nbuiltins.dict.update(slots, unrelated=fake.Agent)",
    "slots = vars(fake)\nslots.update(unrelated=fake.Agent, other=fake.Agent)",
    "slots = vars(fake)\nslots.update({'unrelated': fake.Agent})",
    "slots = vars(fake)\nslots.update(**payload)",
    "slots = fake.__dict__\nslots.update(unrelated=fake.Agent, extra_field=fake.Agent)",
    "slots = getattr(fake, '__dict__')\nslots.update(unrelated=fake.Agent, extra_field=fake.Agent)",
])
def test_absent_field_bound_update_keeps_receiver_getter_and_payload_bounds(namespace_workspace, write):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=write)
    calls = BuilderCalls(resolver)
    try:
        assert not calls.absent_field_dictionary_getter(module, module.tree.body[1].value.func if isinstance(module.tree.body[1], ast.Assign) and isinstance(module.tree.body[1].value, ast.Call) else ast.Name(id="vars", ctx=ast.Load()), "agents")
    except CallLimit:
        pass
    assert not resolver._checking_fresh_dictionary_primitive


@pytest.mark.parametrize("extra", ["fake.Agent([])\n", "result = fake.unrelated\n", "saved = slots\n",
                                  "__all__ = ['slots']\n", "slots.update(other=fake.Agent)\n"])
def test_absent_field_bound_update_keeps_calls_reads_exports_and_other_mutations_visible(namespace_workspace, extra):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="slots = vars(fake)\nslots.update(unrelated=fake.Agent)", extra=extra,
    )
    try:
        assert not BuilderCalls(resolver).absent_field_dictionary_getter(module, module.tree.body[1].value.func, "agents")
    except CallLimit:
        pass


@pytest.mark.parametrize("called", [False, True])
def test_absent_field_bound_update_keeps_function_and_dictionary_method_identity_separate(namespace_workspace, called):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="slots = vars(fake)\nslots.update(unrelated=fake.update)",
        extra="fake.update([])\n" if called else "", home="def update(tools):\n    return tools\n",
    )
    assert BuilderCalls(resolver).absent_field_dictionary_getter(module, module.tree.body[1].value.func, "agents") is (not called)


def test_absent_field_bound_update_keeps_raw_refusal_and_same_key_proof_distinct(namespace_workspace):
    from agents_shipgate.inputs import python_imports

    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="slots = vars(fake)\nslots.update(Agent=fake.Agent)",
    )
    calls = BuilderCalls(resolver)
    assert calls.idempotent_source_slot(module, module.tree.body[1].value.func, "agents")
    assert not calls.absent_field_dictionary_getter(module, module.tree.body[1].value.func, "agents")
    result = python_imports._external_constructor_use(resolver, module, "agents", {module.path}, allow_owner_routes=False)
    assert result and "reflective machinery" in result


@pytest.mark.parametrize("family", ["agents", "google.adk"])
@pytest.mark.parametrize("names", [("producer", "mapping", "Handler", "extra_field"),
                                  ("service", "table", "dispatch", "new_entry")])
def test_absent_field_getattr_update_keeps_metadata_and_function_roles_distinct(namespace_workspace, family, names):
    source, mapping, function, field = names
    (namespace_workspace / f"{source}.py").write_text(f"def {function}(tools):\n    return tools\n")
    text = f"import {source}\n{mapping} = getattr({source}, '__dict__')\n{mapping}.update({field}={source}.{function})\n"
    path = namespace_workspace / "entry.py"
    path.write_text(text)
    resolver = ImportResolver(namespace_workspace)
    module = resolver.entry(path, ast.parse(text), text)
    calls = BuilderCalls(resolver)
    projection = module.tree.body[1].value
    value = module.tree.body[2].value.keywords[0].value
    resolution = calls.resolve(module, value)
    ordinary = calls.callers(resolution.module, resolution.definition, allow_empty=True)
    assert ordinary.limits and not ordinary.sites
    assert calls.absent_field_dictionary_getter(module, projection.func, family)
    assert calls.absent_field_dictionary_namespace(module, projection.args[0], family)
    assert not calls.absent_field_dictionary_namespace(module, projection.args[1], family)
    with calls.absent_field_dictionary_receipt(module, value, family) as receipt:
        assert receipt is not None
        assert receipt.roles.getter_metadata is projection.args[1]
        assert receipt.roles.getter_metadata not in receipt.roles.namespace_nodes
        assert receipt.roles.value is value
        census = receipt.census()
        assert not census.sites and not census.limits
        receipt.reconfirm()
    assert calls.callers(resolution.module, resolution.definition, allow_empty=True) is ordinary


@pytest.mark.parametrize("write", [
    "slots = getattr(fake, '__dict__')\nslots.__init__(unrelated=fake.Agent)",
    "slots = getattr(fake, '__dict__')\ndict.__init__(slots, unrelated=fake.Agent)",
    "import builtins\nslots = getattr(fake, '__dict__')\nbuiltins.dict.__init__(slots, unrelated=fake.Agent)",
    "slots = getattr(fake, '__dict__', {})\nslots.update(unrelated=fake.Agent)",
    "key = '__dict__'\nslots = getattr(fake, key)\nslots.update(unrelated=fake.Agent)",
    "slots = getattr(fake, '__globals__')\nslots.update(unrelated=fake.Agent)",
    "saved = getattr\nslots = saved(fake, '__dict__')\nslots.update(unrelated=fake.Agent)",
    "getattr = replacement\nslots = getattr(fake, '__dict__')\nslots.update(unrelated=fake.Agent)",
    "slots = getattr(fake, '__dict__')\nslots.update(unrelated=fake.Agent, other=fake.Agent)",
])
def test_absent_field_getattr_stays_exact_and_update_only(namespace_workspace, write):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=write)
    calls = BuilderCalls(resolver)
    try:
        for node in ast.walk(module.tree):
            if isinstance(node, ast.Name) and node.id == "getattr":
                assert not calls.absent_field_dictionary_getter(module, node, "agents")
    except CallLimit:
        pass
    assert not resolver._checking_fresh_dictionary_primitive


@pytest.mark.parametrize("extra", ["other = '__dict__'\n", "saved = slots\n", "fake.Agent([])\n", "result = fake.unrelated\n"])
def test_absent_field_getattr_does_not_exempt_other_metadata_or_namespace_uses(namespace_workspace, extra):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="slots = getattr(fake, '__dict__')\nslots.update(unrelated=fake.Agent)", extra=extra,
    )
    try:
        assert not BuilderCalls(resolver).absent_field_dictionary_getter(module, module.tree.body[1].value.func, "agents")
    except CallLimit:
        pass
    assert not resolver._checking_fresh_dictionary_primitive


def test_absent_field_getattr_keeps_raw_refusal_and_receiving_owners_out(namespace_workspace, monkeypatch):
    from agents_shipgate.inputs import list_expressions, python_imports

    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="slots = getattr(fake, '__dict__')\nslots.update(unrelated=fake.Agent)",
    )
    def forbidden(*args, **kwargs):
        pytest.fail("the source metadata proof borrowed an ownership assertion")
    monkeypatch.setattr(BuilderCalls, "idempotent_source_slot", forbidden)
    for name in ("_independent_foreign_class_slot_write", "_independent_plain_class_slot_write",
                 "_agent_instance", "_provisional_agent_list_clear"):
        monkeypatch.setattr(list_expressions, name, forbidden)
    assert BuilderCalls(resolver).absent_field_dictionary_getter(module, module.tree.body[1].value.func, "agents")
    result = python_imports._external_constructor_use(resolver, module, "agents", {module.path}, allow_owner_routes=False)
    assert result and "reflective machinery" in result


@pytest.mark.parametrize("change", ["source", "importer", "getter"])
def test_absent_field_getattr_final_receipt_reconfirms_getter_and_home(namespace_workspace, change):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="slots = getattr(fake, '__dict__')\nslots.update(unrelated=fake.Agent)",
    )
    value = module.tree.body[2].value.keywords[0].value
    with BuilderCalls(resolver).absent_field_dictionary_receipt(module, value, "agents") as receipt:
        assert receipt is not None
        assert not receipt.census().limits
        if change == "source":
            (namespace_workspace / "fake.py").write_text("unrelated = None\ndef Agent(tools):\n    return tools\n")
        elif change == "importer":
            (namespace_workspace / "observer.py").write_text("from entry import slots\n")
        else:
            with (namespace_workspace / "entry.py").open("a") as out:
                out.write("getattr = replacement\n")
        with pytest.raises((CallLimit, ValueError, OSError)):
            receipt.reconfirm()
    assert not resolver._checking_fresh_dictionary_primitive


@pytest.mark.parametrize("family", ["agents", "google.adk"])
@pytest.mark.parametrize("names", [("producer", "mapping", "Handler", "extra_field"),
                                  ("service", "table", "dispatch", "new_entry")])
def test_absent_field_attribute_update_keeps_metadata_and_function_roles_distinct(namespace_workspace, family, names):
    source, mapping, function, field = names
    (namespace_workspace / f"{source}.py").write_text(f"def {function}(tools):\n    return tools\n")
    text = f"import {source}\n{mapping} = {source}.__dict__\n{mapping}.update({field}={source}.{function})\n"
    path = namespace_workspace / "entry.py"
    path.write_text(text)
    resolver = ImportResolver(namespace_workspace)
    module = resolver.entry(path, ast.parse(text), text)
    calls = BuilderCalls(resolver)
    projection = module.tree.body[1].value
    value = module.tree.body[2].value.keywords[0].value
    resolution = calls.resolve(module, value)
    ordinary = calls.callers(resolution.module, resolution.definition, allow_empty=True)
    assert ordinary.limits and not ordinary.sites
    assert calls.absent_field_dictionary_projection(module, projection, family)
    assert calls.absent_field_dictionary_namespace(module, projection.value, family)
    assert not calls.absent_field_dictionary_projection(module, projection.value, family)
    assert not calls.absent_field_dictionary_projection(module, value, family)
    assert not calls.absent_field_dictionary_getter(module, projection, family)
    with calls.absent_field_dictionary_receipt(module, value, family) as receipt:
        assert receipt is not None
        assert receipt.roles.getter_metadata is None
        assert receipt.roles.intrinsic_projection is projection
        assert projection not in receipt.roles.namespace_nodes
        assert receipt.roles.value is value
        census = receipt.census()
        assert not census.sites and not census.limits
        receipt.reconfirm()
    assert calls.callers(resolution.module, resolution.definition, allow_empty=True) is ordinary


@pytest.mark.parametrize("write", [
    "slots = fake.__dict__\nslots.__init__(unrelated=fake.Agent)",
    "slots = fake.__dict__\ndict.__init__(slots, unrelated=fake.Agent)",
    "import builtins\nslots = fake.__dict__\nbuiltins.dict.__init__(slots, unrelated=fake.Agent)",
    "slots = fake.__globals__\nslots.update(unrelated=fake.Agent)",
    "slots = fake.__dict__\nslots.update({'unrelated': fake.Agent})",
    "slots = fake.__dict__\ndict.update(slots, unrelated=fake.Agent)",
    "slots = fake.__dict__\nslots.update(unrelated=fake.Agent, other=fake.Agent)",
    "slots = fake.__dict__\nsaved = slots\nsaved.update(unrelated=fake.Agent)",
    "slots = fake.__dict__\nslots.update(unrelated=lambda: fake.Agent)",
])
def test_absent_field_attribute_stays_exact_and_update_only(namespace_workspace, write):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=write)
    calls = BuilderCalls(resolver)
    try:
        for node in ast.walk(module.tree):
            if isinstance(node, ast.Attribute) and node.attr == "__dict__":
                assert not calls.absent_field_dictionary_projection(module, node, "agents")
    except CallLimit:
        pass
    assert not resolver._checking_fresh_dictionary_primitive


@pytest.mark.parametrize("extra", ["other = '__dict__'\n", "saved = slots\n", "fake.Agent([])\n", "result = fake.unrelated\n"])
def test_absent_field_attribute_does_not_exempt_other_metadata_or_namespace_uses(namespace_workspace, extra):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="slots = fake.__dict__\nslots.update(unrelated=fake.Agent)", extra=extra,
    )
    try:
        assert not BuilderCalls(resolver).absent_field_dictionary_projection(module, module.tree.body[1].value, "agents")
    except CallLimit:
        pass
    assert not resolver._checking_fresh_dictionary_primitive


def test_absent_field_attribute_keeps_raw_refusal_and_receiving_owners_out(namespace_workspace, monkeypatch):
    from agents_shipgate.inputs import list_expressions, python_imports

    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="slots = fake.__dict__\nslots.update(unrelated=fake.Agent)",
    )
    def forbidden(*args, **kwargs):
        pytest.fail("the source metadata proof borrowed an ownership assertion")
    monkeypatch.setattr(BuilderCalls, "idempotent_source_slot", forbidden)
    for name in ("_independent_foreign_class_slot_write", "_independent_plain_class_slot_write",
                 "_agent_instance", "_provisional_agent_list_clear"):
        monkeypatch.setattr(list_expressions, name, forbidden)
    assert BuilderCalls(resolver).absent_field_dictionary_projection(module, module.tree.body[1].value, "agents")
    value = module.tree.body[2].value.keywords[0].value
    home = BuilderCalls(resolver).resolve(module, value).module
    assert home is not None
    result = python_imports._external_constructor_use(
        resolver, module, "agents", {module.path, home.path}, allow_owner_routes=False,
    )
    assert result is not None


@pytest.mark.parametrize("change", ["source", "importer", "getter"])
def test_absent_field_attribute_final_receipt_reconfirms_getter_and_home(namespace_workspace, change):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="slots = fake.__dict__\nslots.update(unrelated=fake.Agent)",
    )
    value = module.tree.body[2].value.keywords[0].value
    with BuilderCalls(resolver).absent_field_dictionary_receipt(module, value, "agents") as receipt:
        assert receipt is not None
        assert not receipt.census().limits
        if change == "source":
            (namespace_workspace / "fake.py").write_text("unrelated = None\ndef Agent(tools):\n    return tools\n")
        elif change == "importer":
            (namespace_workspace / "observer.py").write_text("from entry import slots\n")
        else:
            with (namespace_workspace / "entry.py").open("a") as out:
                out.write("fake.__dict__ = replacement\n")
        with pytest.raises((CallLimit, ValueError, OSError)):
            receipt.reconfirm()
    assert not resolver._checking_fresh_dictionary_primitive


@pytest.mark.parametrize("family", ["agents", "google.adk"])
@pytest.mark.parametrize("names", [("producer", "mapping", "Handler"), ("service", "table", "dispatch")])
def test_source_bound_ior_preserves_actual_same_slot_roles_and_ordinary_census(namespace_workspace, family, names):
    source, mapping, function = names
    (namespace_workspace / f"{source}.py").write_text(f"def {function}(tools):\n    return tools\n")
    text = f"import {source}\n{mapping} = {source}.__dict__\n{mapping}.__ior__({{'{function}': {source}.{function}}})\n"
    path = namespace_workspace / "entry.py"
    path.write_text(text)
    resolver = ImportResolver(namespace_workspace)
    module = resolver.entry(path, ast.parse(text), text)
    calls = BuilderCalls(resolver)
    terminal = module.tree.body[2]
    home, definition, statement = calls._source_slot_candidate(module, terminal)
    assert statement is terminal
    ordinary = calls.callers(home, definition, allow_empty=True)
    assert ordinary.limits and not ordinary.sites
    edge = calls._source_slot_edge(module, terminal)
    assert edge.primitive is None
    assert edge.call is terminal.value
    assert edge.callee is terminal.value.func
    assert edge.payload is terminal.value.args[0]
    assert edge.rhs is edge.payload.values[0]
    assert calls.idempotent_source_slot(module, terminal, family)
    assert calls.uncalled_source_slot(home, definition)
    assert calls.callers(home, definition, allow_empty=True) is ordinary
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("write", [
    "slots = fake.__dict__\nslots.__ior__(Agent=fake.Agent)",
    "slots = fake.__dict__\nslots.__ior__({'other': fake.Agent})",
    "slots = fake.__dict__\nslots.__ior__({'Agent': fake.Agent, 'extra': fake.Agent})",
    "slots = fake.__dict__\nslots.__ior__({**{'Agent': fake.Agent}})",
    "slots = fake.__dict__\nslots.__ior__(*({'Agent': fake.Agent},))",
    "slots = fake.__dict__\ndict.__ior__(slots, {'Agent': fake.Agent})",
    "import builtins\nslots = fake.__dict__\nbuiltins.dict.__ior__(slots, {'Agent': fake.Agent})",
    "slots = vars(fake)\nslots.__ior__({'Agent': fake.Agent})",
    "slots = getattr(fake, '__dict__')\nslots.__ior__({'Agent': fake.Agent})",
    "slots = fake.__dict__\nresult = slots.__ior__({'Agent': fake.Agent})",
])
def test_source_bound_ior_keeps_literal_bound_projection_and_discarded_result_limits(namespace_workspace, write):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=write)
    calls = BuilderCalls(resolver)
    for statement in module.tree.body:
        if (isinstance(statement, ast.Expr)
                or isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Call)):
            assert calls._source_slot_edge(module, statement) is None
            assert not calls.idempotent_source_slot(module, statement, "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("extra", ["saved = slots\n", "slots.update(Agent=fake.Agent)\n", "fake.Agent([])\n",
                                   "__all__ = ['slots']\n", "getattr(fake, 'Agent')\n"])
def test_source_bound_ior_keeps_calls_exports_and_competing_uses_visible(namespace_workspace, extra):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="slots = fake.__dict__\nslots.__ior__({'Agent': fake.Agent})", extra=extra,
    )
    calls = BuilderCalls(resolver)
    try:
        assert not calls.idempotent_source_slot(module, module.tree.body[2], "agents")
    except CallLimit:
        pass
    assert not resolver._checking_source_module_slot


def test_source_bound_ior_cannot_borrow_receiving_ownership_or_raw_false(namespace_workspace, monkeypatch):
    from agents_shipgate.inputs import list_expressions, python_imports

    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="slots = fake.__dict__\nslots.__ior__({'Agent': fake.Agent})",
    )
    calls = BuilderCalls(resolver)
    home, definition, _ = calls._source_slot_candidate(module, module.tree.body[2])
    def forbidden(*args, **kwargs):
        pytest.fail("a raw same-slot merge borrowed a receiving-owner callback")
    for name in ("_independent_foreign_class_slot_write", "_independent_plain_class_slot_write",
                 "_agent_instance", "_provisional_agent_list_clear"):
        monkeypatch.setattr(list_expressions, name, forbidden)
    assert calls.idempotent_source_slot(module, module.tree.body[2], "agents")
    assert calls.uncalled_source_slot(home, definition)
    monkeypatch.setattr(BuilderCalls, "idempotent_source_slot", forbidden)
    assert python_imports._external_constructor_use(
        resolver, module, "agents", {module.path, home.path}, allow_owner_routes=False,
    ) is not None


def test_source_bound_ior_rechecks_actual_source_and_reentry(namespace_workspace):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="slots = fake.__dict__\nslots.__ior__({'Agent': fake.Agent})",
    )
    calls = BuilderCalls(resolver)
    statement = module.tree.body[2]
    assert calls.idempotent_source_slot(module, statement, "agents")
    resolver._checking_source_module_slot = True
    with pytest.raises(CallLimit, match="reentered"):
        calls.idempotent_source_slot(module, statement, "agents")
    assert resolver._checking_source_module_slot
    resolver._checking_source_module_slot = False
    (namespace_workspace / "fake.py").write_text("def Agent(tools):\n    return replacement\n")
    with pytest.raises(CallLimit):
        calls.idempotent_source_slot(module, statement, "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("family", ["agents", "google.adk"])
@pytest.mark.parametrize("names", [("producer", "Handler"), ("service", "dispatch")])
def test_qualified_source_setter_keeps_source_key_function_and_builtin_roles_distinct(namespace_workspace, family, names):
    source, function = names
    (namespace_workspace / f"{source}.py").write_text(f"def {function}(tools):\n    return tools\n")
    text = f"import {source}\nimport builtins\nbuiltins.setattr({source}, '{function}', {source}.{function})\n"
    path = namespace_workspace / "entry.py"
    path.write_text(text)
    resolver = ImportResolver(namespace_workspace)
    module = resolver.entry(path, ast.parse(text), text)
    calls = BuilderCalls(resolver)
    statement = module.tree.body[2]
    home, definition, actual = calls._source_slot_candidate(module, statement)
    assert actual is statement
    ordinary = calls.callers(home, definition, allow_empty=True)
    assert ordinary.limits and not ordinary.sites
    edge = calls._source_slot_edge(module, statement)
    record = edge.setter_import
    assert record.statement is module.tree.body[1]
    assert record.callee is statement.value.func
    assert record.root is record.callee.value
    assert edge.primitive is None and edge.primitive_import is None
    assert edge.parts[3] is None and edge.parts[5] is None
    assert edge.parts[0] is statement.value.args[0]
    assert edge.parts[4] is statement.value.args[1]
    assert edge.rhs is statement.value.args[2]
    assert calls.idempotent_source_slot(module, record.callee, family)
    assert calls.idempotent_source_slot(module, record.root, family)
    assert calls.idempotent_source_slot(module, edge.parts[4], family)
    assert calls.source_setter_import(module, record.statement, family)
    assert not calls.source_primitive_import(module, record.statement, family)
    assert calls.uncalled_source_slot(home, definition)
    assert calls.callers(home, definition, allow_empty=True) is ordinary
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("write", [
    "import builtins\nbuiltins.setattr(fake, 'unrelated', fake.Agent)",
    "import builtins\nbuiltins.setattr(fake, key, fake.Agent)",
    "import builtins\nbuiltins.setattr(fake, 'Agent', fake.Replacement)",
    "import builtins\nbuiltins.setattr(fake, 'Agent', fake.Agent, None)",
    "import builtins\nbuiltins.setattr(fake, 'Agent', fake.Agent, extra=None)",
    "import builtins\nbuiltins.setattr(*(fake, 'Agent', fake.Agent))",
    "import builtins as runtime\nruntime.setattr(fake, 'Agent', fake.Agent)",
    "from builtins import setattr as replace\nreplace(fake, 'Agent', fake.Agent, extra=None)",
    "import builtins\nreplace = builtins.setattr\nreplace(fake, 'Agent', fake.Agent, extra=None)",
    "setattr(fake, 'Agent', fake.Agent, extra=None)",
    "import builtins\nresult = builtins.setattr(fake, 'Agent', fake.Agent)",
    "import builtins\nimport fake as alias\nbuiltins.setattr(alias, 'Agent', alias.Agent)",
    "import builtins\nbuiltins = replacement\nbuiltins.setattr(fake, 'Agent', fake.Agent)",
])
def test_qualified_source_setter_preserves_exact_import_call_and_same_slot_limits(namespace_workspace, write):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=write)
    calls = BuilderCalls(resolver)
    for statement in module.tree.body:
        if isinstance(statement, ast.Expr) or isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Call):
            assert calls._source_slot_edge(module, statement) is None
            assert not calls.idempotent_source_slot(module, statement, "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("extra", ["saved = fake\n", "fake.Agent([])\n", "saved = builtins\n",
                                   "__all__ = ['fake']\n", "getattr(fake, 'Agent')\n"])
def test_qualified_source_setter_keeps_calls_namespace_exports_and_other_uses_visible(namespace_workspace, extra):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import builtins\nbuiltins.setattr(fake, 'Agent', fake.Agent)", extra=extra,
    )
    try:
        assert not BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[2], "agents")
    except CallLimit:
        pass
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("home", [
    "def Agent(tools):\n    return tools\ndef __getattr__(name):\n    return Agent\n",
    "class Carrier:\n    pass\ndef Agent(tools):\n    return tools\n",
    "@decorate\ndef Agent(tools):\n    return tools\n",
])
def test_qualified_source_setter_does_not_establish_hook_or_decorator_purity(namespace_workspace, home):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import builtins\nbuiltins.setattr(fake, 'Agent', fake.Agent)", home=home,
    )
    try:
        assert not BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[2], "agents")
    except CallLimit:
        pass
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("phase", ["raw", "canonical"])
def test_qualified_source_setter_requires_raw_canonical_setter_before_token_admission(namespace_workspace, monkeypatch, phase):
    from agents_shipgate.inputs import list_expressions

    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import builtins\nbuiltins.setattr(fake, 'Agent', fake.Agent)",
    )
    calls = BuilderCalls(resolver)
    terminal = module.tree.body[2]
    assert calls.idempotent_source_slot(module, terminal, "agents")
    original = list_expressions._namespace_call_reference
    raw_original = list_expressions._qualified_attribute_patches
    raw_active = False
    raw_visits = 0
    changed_calls = 0
    def raw(view, **kwargs):
        nonlocal raw_active, raw_visits
        previous = raw_active
        raw_active = True
        raw_visits += 1
        try:
            return raw_original(view, **kwargs)
        finally:
            raw_active = previous
    def wrong(view, node):
        nonlocal changed_calls
        if node is terminal.value and (phase == "raw" or not raw_active):
            changed_calls += 1
            return "foreign.setattr"
        return original(view, node)
    monkeypatch.setattr(list_expressions, "_qualified_attribute_patches", raw)
    monkeypatch.setattr(list_expressions, "_namespace_call_reference", wrong)
    diagnostic = "unstable namespace primitives" if phase == "raw" else "setter primitive"
    with pytest.raises(CallLimit, match=diagnostic):
        calls.idempotent_source_slot(module, terminal, "agents")
    assert raw_visits and changed_calls
    assert not resolver._checking_source_module_slot


def test_qualified_source_setter_raw_false_and_receiving_owners_cannot_borrow_its_proof(namespace_workspace, monkeypatch):
    from agents_shipgate.inputs import list_expressions, python_imports

    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import builtins\nbuiltins.setattr(fake, 'Agent', fake.Agent)",
    )
    calls = BuilderCalls(resolver)
    home, definition, _ = calls._source_slot_candidate(module, module.tree.body[2])
    def forbidden(*args, **kwargs):
        pytest.fail("the source setter borrowed a receiving-owner proof")
    for name in ("_independent_foreign_class_slot_write", "_independent_plain_class_slot_write",
                 "_agent_instance", "_provisional_agent_list_clear"):
        monkeypatch.setattr(list_expressions, name, forbidden)
    assert calls.idempotent_source_slot(module, module.tree.body[2], "agents")
    assert calls.uncalled_source_slot(home, definition)
    monkeypatch.setattr(BuilderCalls, "source_setter_import", forbidden)
    monkeypatch.setattr(BuilderCalls, "idempotent_source_slot", forbidden)
    assert python_imports._external_constructor_use(
        resolver, module, "agents", {module.path, home.path}, allow_owner_routes=False,
    ) is not None


@pytest.mark.parametrize("change", ["source", "builtin_provider"])
def test_qualified_source_setter_reconfirms_source_and_primitive_provider_currency(namespace_workspace, change):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import builtins\nbuiltins.setattr(fake, 'Agent', fake.Agent)",
    )
    calls = BuilderCalls(resolver)
    statement = module.tree.body[2]
    assert calls.idempotent_source_slot(module, statement, "agents")
    if change == "source":
        (namespace_workspace / "fake.py").write_text("def Agent(tools):\n    return replacement\n")
    else:
        (namespace_workspace / "builtins.py").write_text("def setattr(*args):\n    return None\n")
    with pytest.raises(CallLimit):
        calls.idempotent_source_slot(module, statement, "agents")
    assert not resolver._checking_source_module_slot



@pytest.mark.parametrize("family", ["agents", "google.adk"])
@pytest.mark.parametrize("names", [("producer", "Handler", "apply"), ("service", "dispatch", "replace")])
def test_saved_source_setter_keeps_initializer_assignment_callee_and_function_roles_distinct(namespace_workspace, family, names):
    source, function, alias = names
    (namespace_workspace / f"{source}.py").write_text(f"def {function}(tools):\n    return tools\n")
    text = f"import {source}\nimport builtins\n{alias} = builtins.setattr\n{alias}({source}, '{function}', {source}.{function})\n"
    path = namespace_workspace / "entry.py"
    path.write_text(text)
    resolver = ImportResolver(namespace_workspace)
    module = resolver.entry(path, ast.parse(text), text)
    calls = BuilderCalls(resolver)
    terminal = module.tree.body[3]
    home, definition, actual = calls._source_slot_candidate(module, terminal)
    assert actual is terminal
    ordinary = calls.callers(home, definition, allow_empty=True)
    assert ordinary.limits and not ordinary.sites
    edge = calls._source_slot_edge(module, terminal)
    record = edge.setter_import
    assert record.statement is module.tree.body[1]
    assert record.assignment is module.tree.body[2]
    assert record.target is record.assignment.targets[0]
    assert record.initializer is record.assignment.value
    assert record.root is record.initializer.value
    assert record.callee is terminal.value.func
    assert record.callee is not record.target
    assert edge.rhs is terminal.value.args[2]
    assert edge.primitive is None and edge.primitive_import is None
    assert calls.idempotent_source_slot(module, record.callee, family)
    assert not calls.idempotent_source_slot(module, record.root, family)
    assert calls.source_setter_initializer(module, record.initializer, family)
    assert not calls.source_setter_initializer(module, edge.rhs, family)
    assert calls.source_setter_import(module, record.statement, family)
    assert not calls.source_primitive_import(module, record.statement, family)
    assert calls.uncalled_source_slot(home, definition)
    assert calls.callers(home, definition, allow_empty=True) is ordinary
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("write", [
    "import builtins\nreplace = builtins.setattr\nreplace(fake, 'unrelated', fake.Agent)",
    "import builtins\nreplace = builtins.setattr\nreplace(fake, key, fake.Agent)",
    "import builtins\nreplace = builtins.setattr\nreplace(fake, 'Agent', fake.Replacement)",
    "import builtins\nreplace = builtins.setattr\nreplace(fake, 'Agent', fake.Agent, extra=None)",
    "import builtins\nreplace = builtins.setattr\nresult = replace(fake, 'Agent', fake.Agent)",
    "import builtins\nreplace, other = builtins.setattr, None\nreplace(fake, 'Agent', fake.Agent)",
    "import builtins\nreplace: object = builtins.setattr\nreplace(fake, 'Agent', fake.Agent)",
    "import builtins\nreplace = builtins.setattr\nreplace = replacement\nreplace(fake, 'Agent', fake.Agent)",
    "import builtins as runtime\nreplace = runtime.setattr\nreplace(fake, 'Agent', fake.Agent)",
    "from builtins import setattr as replace\nreplace(fake, 'Agent', fake.Agent, extra=None)",
    "setattr(fake, 'Agent', fake.Agent, extra=None)",
])
def test_saved_source_setter_keeps_exact_alias_assignment_and_terminal_limits(namespace_workspace, write):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=write)
    calls = BuilderCalls(resolver)
    for statement in module.tree.body:
        if isinstance(statement, ast.Expr) or isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Call):
            assert calls._source_slot_edge(module, statement) is None
            assert not calls.idempotent_source_slot(module, statement, "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("extra", ["saved = replace\n", "replace(fake, 'Agent', fake.Agent)\n", "fake.Agent([])\n",
                                   "__all__ = ['replace']\n", "getattr(fake, 'Agent')\n"])
def test_saved_source_setter_keeps_alias_escapes_calls_and_namespace_uses_visible(namespace_workspace, extra):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import builtins\nreplace = builtins.setattr\nreplace(fake, 'Agent', fake.Agent)", extra=extra,
    )
    try:
        assert not BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[3], "agents")
    except CallLimit:
        pass
    assert not resolver._checking_source_module_slot


def test_saved_source_setter_cannot_borrow_raw_false_or_receiving_ownership(namespace_workspace, monkeypatch):
    from agents_shipgate.inputs import list_expressions, python_imports

    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import builtins\nreplace = builtins.setattr\nreplace(fake, 'Agent', fake.Agent)",
    )
    calls = BuilderCalls(resolver)
    home, definition, _ = calls._source_slot_candidate(module, module.tree.body[3])
    def forbidden(*args, **kwargs):
        pytest.fail("the saved source setter borrowed an ownership shortcut")
    for name in ("_independent_foreign_class_slot_write", "_independent_plain_class_slot_write",
                 "_agent_instance", "_provisional_agent_list_clear"):
        monkeypatch.setattr(list_expressions, name, forbidden)
    assert calls.idempotent_source_slot(module, module.tree.body[3], "agents")
    assert calls.uncalled_source_slot(home, definition)
    monkeypatch.setattr(BuilderCalls, "source_setter_initializer", forbidden)
    monkeypatch.setattr(BuilderCalls, "source_setter_import", forbidden)
    monkeypatch.setattr(BuilderCalls, "idempotent_source_slot", forbidden)
    assert python_imports._external_constructor_use(
        resolver, module, "agents", {module.path, home.path}, allow_owner_routes=False,
    ) is not None


def test_saved_source_setter_requires_terminal_canonical_identity_after_raw_proof(namespace_workspace, monkeypatch):
    from agents_shipgate.inputs import list_expressions

    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import builtins\nreplace = builtins.setattr\nreplace(fake, 'Agent', fake.Agent)",
    )
    calls = BuilderCalls(resolver)
    terminal = module.tree.body[3]
    assert calls.idempotent_source_slot(module, terminal, "agents")
    original = list_expressions._namespace_call_reference
    raw_original = list_expressions._qualified_attribute_patches
    raw_active = False
    def raw(view, **kwargs):
        nonlocal raw_active
        previous = raw_active
        raw_active = True
        try:
            return raw_original(view, **kwargs)
        finally:
            raw_active = previous
    def wrong(view, node):
        return "foreign.setattr" if node is terminal.value and not raw_active else original(view, node)
    monkeypatch.setattr(list_expressions, "_qualified_attribute_patches", raw)
    monkeypatch.setattr(list_expressions, "_namespace_call_reference", wrong)
    with pytest.raises(CallLimit, match="setter primitive"):
        calls.idempotent_source_slot(module, terminal, "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("change", ["source", "builtin_provider"])
def test_saved_source_setter_reconfirms_source_and_provider_currency(namespace_workspace, change):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import builtins\nreplace = builtins.setattr\nreplace(fake, 'Agent', fake.Agent)",
    )
    calls = BuilderCalls(resolver)
    terminal = module.tree.body[3]
    assert calls.idempotent_source_slot(module, terminal, "agents")
    if change == "source":
        (namespace_workspace / "fake.py").write_text("def Agent(tools):\n    return replacement\n")
    else:
        (namespace_workspace / "builtins.py").write_text("def setattr(*args):\n    return None\n")
    with pytest.raises(CallLimit):
        calls.idempotent_source_slot(module, terminal, "agents")
    assert not resolver._checking_source_module_slot



@pytest.mark.parametrize("family", ["agents", "google.adk"])
@pytest.mark.parametrize("names", [("producer", "Handler", "apply"), ("service", "dispatch", "replace")])
def test_from_source_setter_keeps_actual_import_alias_terminal_and_function_roles_distinct(namespace_workspace, family, names):
    source, function, alias = names
    (namespace_workspace / f"{source}.py").write_text(f"def {function}(tools):\n    return tools\n")
    text = f"import {source}\nfrom builtins import setattr as {alias}\n{alias}({source}, '{function}', {source}.{function})\n"
    path = namespace_workspace / "entry.py"
    path.write_text(text)
    resolver = ImportResolver(namespace_workspace)
    module = resolver.entry(path, ast.parse(text), text)
    calls = BuilderCalls(resolver)
    terminal = module.tree.body[2]
    home, definition, actual = calls._source_slot_candidate(module, terminal)
    assert actual is terminal
    ordinary = calls.callers(home, definition, allow_empty=True)
    assert ordinary.limits and not ordinary.sites
    edge = calls._source_slot_edge(module, terminal)
    record = edge.setter_import
    assert record.statement is module.tree.body[1]
    assert isinstance(record.statement, ast.ImportFrom)
    assert record.imported is record.statement.names[0]
    assert record.imported.asname == alias
    assert record.callee is terminal.value.func
    assert not hasattr(record, "root") and not hasattr(record, "initializer")
    assert not hasattr(record, "assignment") and not hasattr(record, "target")
    assert edge.rhs is terminal.value.args[2]
    assert edge.primitive is None and edge.primitive_import is None
    assert calls.idempotent_source_slot(module, record.callee, family)
    assert calls.source_from_setter_import(module, record.statement, family)
    assert not calls.source_from_setter_import(module, module.tree.body[0], family)
    assert not calls.source_setter_import(module, record.statement, family)
    assert not calls.source_primitive_import(module, record.statement, family)
    assert not calls.source_setter_initializer(module, record.callee, family)
    assert calls.uncalled_source_slot(home, definition)
    assert calls.callers(home, definition, allow_empty=True) is ordinary
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("write", [
    "from builtins import setattr as replace\nreplace(fake, 'unrelated', fake.Agent)",
    "from builtins import setattr as replace\nreplace(fake, key, fake.Agent)",
    "from builtins import setattr as replace\nreplace(fake, 'Agent', fake.Replacement)",
    "from builtins import setattr as replace\nreplace(fake, 'Agent', fake.Agent, extra=None)",
    "from builtins import setattr as replace\nresult = replace(fake, 'Agent', fake.Agent)",
    "from builtins import setattr as replace\nother = replace\nother(fake, 'Agent', fake.Agent)",
    "from builtins import setattr as replace\nreplace = replacement\nreplace(fake, 'Agent', fake.Agent)",
    "from .builtins import setattr as replace\nreplace(fake, 'Agent', fake.Agent)",
    "from foreign import setattr as replace\nreplace(fake, 'Agent', fake.Agent)",
    "from builtins import setattr\nsetattr(fake, 'Agent', fake.Agent)",
    "from builtins import setattr as replace, getattr as read\nreplace(fake, 'Agent', fake.Agent)",
    "from builtins import *\nreplace(fake, 'Agent', fake.Agent)",
    "from builtins import setattr as replace\nreplace(*(fake, 'Agent', fake.Agent))",
    "setattr(fake, 'Agent', fake.Agent, extra=None)",
])
def test_from_source_setter_keeps_exact_import_alias_and_terminal_limits(namespace_workspace, write):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=write)
    calls = BuilderCalls(resolver)
    for statement in module.tree.body:
        if isinstance(statement, ast.Expr) or isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Call):
            assert calls._source_slot_edge(module, statement) is None
            assert not calls.idempotent_source_slot(module, statement, "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("extra", ["saved = replace\n", "replace(fake, 'Agent', fake.Agent)\n", "fake.Agent([])\n",
                                   "__all__ = ['replace']\n", "getattr(fake, 'Agent')\n"])
def test_from_source_setter_keeps_alias_escapes_calls_and_namespace_uses_visible(namespace_workspace, extra):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="from builtins import setattr as replace\nreplace(fake, 'Agent', fake.Agent)", extra=extra,
    )
    try:
        assert not BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[2], "agents")
    except CallLimit:
        pass
    assert not resolver._checking_source_module_slot


def test_from_source_setter_cannot_borrow_raw_false_or_receiving_ownership(namespace_workspace, monkeypatch):
    from agents_shipgate.inputs import list_expressions, python_imports

    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="from builtins import setattr as replace\nreplace(fake, 'Agent', fake.Agent)",
    )
    calls = BuilderCalls(resolver)
    home, definition, _ = calls._source_slot_candidate(module, module.tree.body[2])
    def forbidden(*args, **kwargs):
        pytest.fail("the from source setter borrowed an ownership shortcut")
    for name in ("_independent_foreign_class_slot_write", "_independent_plain_class_slot_write",
                 "_agent_instance", "_provisional_agent_list_clear"):
        monkeypatch.setattr(list_expressions, name, forbidden)
    assert calls.idempotent_source_slot(module, module.tree.body[2], "agents")
    assert calls.uncalled_source_slot(home, definition)
    monkeypatch.setattr(BuilderCalls, "source_setter_initializer", forbidden)
    monkeypatch.setattr(BuilderCalls, "source_from_setter_import", forbidden)
    monkeypatch.setattr(BuilderCalls, "idempotent_source_slot", forbidden)
    assert python_imports._external_constructor_use(
        resolver, module, "agents", {module.path, home.path}, allow_owner_routes=False,
    ) is not None


def test_from_source_setter_requires_terminal_canonical_identity_after_raw_proof(namespace_workspace, monkeypatch):
    from agents_shipgate.inputs import list_expressions

    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="from builtins import setattr as replace\nreplace(fake, 'Agent', fake.Agent)",
    )
    calls = BuilderCalls(resolver)
    terminal = module.tree.body[2]
    assert calls.idempotent_source_slot(module, terminal, "agents")
    original = list_expressions._namespace_call_reference
    raw_original = list_expressions._qualified_attribute_patches
    raw_active = False
    def raw(view, **kwargs):
        nonlocal raw_active
        previous = raw_active
        raw_active = True
        try:
            return raw_original(view, **kwargs)
        finally:
            raw_active = previous
    def wrong(view, node):
        return "foreign.setattr" if node is terminal.value and not raw_active else original(view, node)
    monkeypatch.setattr(list_expressions, "_qualified_attribute_patches", raw)
    monkeypatch.setattr(list_expressions, "_namespace_call_reference", wrong)
    with pytest.raises(CallLimit, match="setter primitive"):
        calls.idempotent_source_slot(module, terminal, "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("change", ["source", "builtin_provider"])
def test_from_source_setter_reconfirms_source_and_provider_currency(namespace_workspace, change):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="from builtins import setattr as replace\nreplace(fake, 'Agent', fake.Agent)",
    )
    calls = BuilderCalls(resolver)
    terminal = module.tree.body[2]
    assert calls.idempotent_source_slot(module, terminal, "agents")
    if change == "source":
        (namespace_workspace / "fake.py").write_text("def Agent(tools):\n    return replacement\n")
    else:
        (namespace_workspace / "builtins.py").write_text("def setattr(*args):\n    return None\n")
    with pytest.raises(CallLimit):
        calls.idempotent_source_slot(module, terminal, "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("alias", ["eval", "exec", "compile", "globals", "type", "object"])
def test_from_source_setter_keeps_imported_alias_metadata_separate_from_unsafe_alias_spelling(namespace_workspace, alias):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write=f"from builtins import setattr as {alias}\n{alias}(fake, 'Agent', fake.Agent)",
    )
    calls = BuilderCalls(resolver)
    terminal = module.tree.body[2]
    assert calls._source_slot_edge(module, terminal) is not None
    with pytest.raises(CallLimit):
        calls.idempotent_source_slot(module, terminal, "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("family", ["agents", "google.adk"])
@pytest.mark.parametrize("names", [("producer", "Handler", "apply"), ("service", "dispatch", "replace")])
def test_bare_saved_setter_keeps_initializer_assignment_callee_and_function_roles_distinct(namespace_workspace, family, names):
    source, function, alias = names
    (namespace_workspace / f"{source}.py").write_text(f"def {function}(tools):\n    return tools\n")
    text = f"import {source}\n{alias} = setattr\n{alias}({source}, '{function}', {source}.{function})\n"
    path = namespace_workspace / "entry.py"
    path.write_text(text)
    resolver = ImportResolver(namespace_workspace)
    module = resolver.entry(path, ast.parse(text), text)
    calls = BuilderCalls(resolver)
    terminal = module.tree.body[2]
    home, definition, actual = calls._source_slot_candidate(module, terminal)
    assert actual is terminal
    ordinary = calls.callers(home, definition, allow_empty=True)
    assert ordinary.limits and not ordinary.sites
    edge = calls._source_slot_edge(module, terminal)
    record = edge.bare_setter
    assert edge.setter_import is None
    assert not hasattr(record, "statement") and not hasattr(record, "imported") and not hasattr(record, "root")
    assert record.assignment is module.tree.body[1]
    assert record.target is record.assignment.targets[0]
    assert record.initializer is record.assignment.value
    assert isinstance(record.initializer, ast.Name) and record.initializer.id == "setattr"
    assert record.callee is terminal.value.func
    assert record.callee is not record.target
    assert edge.rhs is terminal.value.args[2]
    assert edge.primitive is None and edge.primitive_import is None
    assert calls.idempotent_source_slot(module, record.callee, family)
    assert not calls.idempotent_source_slot(module, record.initializer, family)
    assert calls.source_bare_setter_initializer(module, record.initializer, family)
    assert not calls.source_bare_setter_initializer(module, edge.rhs, family)
    assert not calls.source_setter_import(module, module.tree.body[0], family)
    assert not calls.source_primitive_import(module, module.tree.body[0], family)
    assert calls.uncalled_source_slot(home, definition)
    assert calls.callers(home, definition, allow_empty=True) is ordinary
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("write", [
    "replace = setattr\nreplace(fake, 'unrelated', fake.Agent)",
    "replace = setattr\nreplace(fake, key, fake.Agent)",
    "replace = setattr\nreplace(fake, 'Agent', fake.Replacement)",
    "replace = setattr\nreplace(fake, 'Agent', fake.Agent, extra=None)",
    "replace = setattr\nresult = replace(fake, 'Agent', fake.Agent)",
    "replace, other = setattr, None\nreplace(fake, 'Agent', fake.Agent)",
    "replace: object = setattr\nreplace(fake, 'Agent', fake.Agent)",
    "replace = setattr\nreplace = replacement\nreplace(fake, 'Agent', fake.Agent)",
    "import builtins as runtime\nreplace = runtime.setattr\nreplace(fake, 'Agent', fake.Agent)",
    "from builtins import setattr as replace\nreplace(fake, 'Agent', fake.Agent, extra=None)",
    "setattr(fake, 'Agent', fake.Agent, extra=None)",
])
def test_bare_saved_setter_keeps_exact_alias_assignment_and_terminal_limits(namespace_workspace, write):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=write)
    calls = BuilderCalls(resolver)
    for statement in module.tree.body:
        if isinstance(statement, ast.Expr) or isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Call):
            assert calls._source_slot_edge(module, statement) is None
            assert not calls.idempotent_source_slot(module, statement, "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("extra", ["saved = replace\n", "replace(fake, 'Agent', fake.Agent)\n", "fake.Agent([])\n",
                                   "__all__ = ['replace']\n", "getattr(fake, 'Agent')\n"])
def test_bare_saved_setter_keeps_alias_escapes_calls_and_namespace_uses_visible(namespace_workspace, extra):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="replace = setattr\nreplace(fake, 'Agent', fake.Agent)", extra=extra,
    )
    try:
        assert not BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[2], "agents")
    except CallLimit:
        pass
    assert not resolver._checking_source_module_slot


def test_bare_saved_setter_cannot_borrow_raw_false_or_receiving_ownership(namespace_workspace, monkeypatch):
    from agents_shipgate.inputs import list_expressions, python_imports

    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="replace = setattr\nreplace(fake, 'Agent', fake.Agent)",
    )
    calls = BuilderCalls(resolver)
    home, definition, _ = calls._source_slot_candidate(module, module.tree.body[2])
    def forbidden(*args, **kwargs):
        pytest.fail("the bare saved setter borrowed an ownership shortcut")
    for name in ("_independent_foreign_class_slot_write", "_independent_plain_class_slot_write",
                 "_agent_instance", "_provisional_agent_list_clear"):
        monkeypatch.setattr(list_expressions, name, forbidden)
    assert calls.idempotent_source_slot(module, module.tree.body[2], "agents")
    assert calls.uncalled_source_slot(home, definition)
    monkeypatch.setattr(BuilderCalls, "source_bare_setter_initializer", forbidden)
    monkeypatch.setattr(BuilderCalls, "source_setter_import", forbidden)
    monkeypatch.setattr(BuilderCalls, "idempotent_source_slot", forbidden)
    assert python_imports._external_constructor_use(
        resolver, module, "agents", {module.path, home.path}, allow_owner_routes=False,
    ) is not None


def test_bare_saved_setter_requires_terminal_canonical_identity_after_raw_proof(namespace_workspace, monkeypatch):
    from agents_shipgate.inputs import list_expressions

    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="replace = setattr\nreplace(fake, 'Agent', fake.Agent)",
    )
    calls = BuilderCalls(resolver)
    terminal = module.tree.body[2]
    assert calls.idempotent_source_slot(module, terminal, "agents")
    original = list_expressions._namespace_call_reference
    raw_original = list_expressions._qualified_attribute_patches
    raw_active = False
    def raw(view, **kwargs):
        nonlocal raw_active
        previous = raw_active
        raw_active = True
        try:
            return raw_original(view, **kwargs)
        finally:
            raw_active = previous
    def wrong(view, node):
        return "foreign.setattr" if node is terminal.value and not raw_active else original(view, node)
    monkeypatch.setattr(list_expressions, "_qualified_attribute_patches", raw)
    monkeypatch.setattr(list_expressions, "_namespace_call_reference", wrong)
    with pytest.raises(CallLimit, match="setter primitive"):
        calls.idempotent_source_slot(module, terminal, "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("change", ["source", "builtin_provider"])
def test_bare_saved_setter_reconfirms_source_and_provider_currency(namespace_workspace, change):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="replace = setattr\nreplace(fake, 'Agent', fake.Agent)",
    )
    calls = BuilderCalls(resolver)
    terminal = module.tree.body[2]
    assert calls.idempotent_source_slot(module, terminal, "agents")
    if change == "source":
        (namespace_workspace / "fake.py").write_text("def Agent(tools):\n    return replacement\n")
    else:
        (namespace_workspace / "builtins.py").write_text("def setattr(*args):\n    return None\n")
    with pytest.raises(CallLimit):
        calls.idempotent_source_slot(module, terminal, "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("write", [
    "setattr = replacement\nreplace = setattr\nreplace(fake, 'Agent', fake.Agent)",
    "from foreign import setattr\nreplace = setattr\nreplace(fake, 'Agent', fake.Agent)",
    "replace = setattr\nsetattr = replacement\nreplace(fake, 'Agent', fake.Agent)",
])
def test_bare_saved_setter_refuses_shadowed_native_initializer(namespace_workspace, write):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=write)
    terminal = next(n for n in reversed(module.tree.body) if isinstance(n, ast.Expr))
    assert BuilderCalls(resolver)._source_slot_edge(module, terminal) is None
    assert not BuilderCalls(resolver).idempotent_source_slot(module, terminal, "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("alias", ["eval", "exec", "type", "object"])
def test_bare_saved_setter_keeps_unsafe_alias_binding_spelling_visible(namespace_workspace, alias):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write=f"{alias} = setattr\n{alias}(fake, 'Agent', fake.Agent)",
    )
    calls = BuilderCalls(resolver)
    with pytest.raises(CallLimit):
        calls.idempotent_source_slot(module, module.tree.body[2], "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("family", ["agents", "google.adk"])
@pytest.mark.parametrize("names", [("producer", "Handler", "extract", "slots"), ("service", "dispatch", "replace", "mapping")])
def test_saved_vars_store_keeps_getter_mapping_namespace_and_function_roles_distinct(namespace_workspace, family, names):
    source, function, alias, mapping_name = names
    (namespace_workspace / f"{source}.py").write_text(f"def {function}(tools):\n    return tools\n")
    text = f"import {source}\n{alias} = vars\n{mapping_name} = {alias}({source})\n{mapping_name}['{function}'] = {source}.{function}\n"
    path = namespace_workspace / "entry.py"
    path.write_text(text)
    resolver = ImportResolver(namespace_workspace)
    module = resolver.entry(path, ast.parse(text), text)
    calls = BuilderCalls(resolver)
    terminal = module.tree.body[3]
    home, definition, actual = calls._source_slot_candidate(module, terminal)
    assert actual is terminal
    ordinary = calls.callers(home, definition, allow_empty=True)
    assert ordinary.limits and not ordinary.sites
    edge = calls._source_slot_edge(module, terminal)
    mapping = edge.parts[5]
    getter = mapping.saved_getter
    assert mapping.declaration is module.tree.body[2]
    assert mapping.target is mapping.declaration.targets[0]
    assert mapping.receiver is terminal.targets[0].value
    assert mapping.projection is getter.projection is mapping.declaration.value
    assert getter.assignment is module.tree.body[1]
    assert getter.target is getter.assignment.targets[0]
    assert getter.initializer is getter.assignment.value
    assert isinstance(getter.initializer, ast.Name) and getter.initializer.id == "vars"
    assert getter.callee is getter.projection.func and getter.callee is not getter.target
    assert mapping.namespace is getter.projection.args[0]
    assert calls._source_slot_projection(module, getter.projection) is None
    assert edge.setter_import is None and edge.bare_setter is None and edge.primitive is None
    assert calls.idempotent_source_slot(module, terminal, family)
    assert calls.idempotent_source_slot(module, getter.callee, family)
    assert not calls.idempotent_source_slot(module, getter.initializer, family)
    assert calls.source_saved_vars_initializer(module, getter.initializer, family)
    assert not calls.source_saved_vars_initializer(module, getter.callee, family)
    assert not calls.source_saved_vars_initializer(module, edge.rhs, family)
    assert calls.callers(home, definition, allow_empty=True) is ordinary
    assert ordinary.limits and not ordinary.sites
    assert calls.uncalled_source_slot(home, definition)


@pytest.mark.parametrize("write", [
    "extract = vars\nextract(fake)['Agent'] = fake.Agent",
    "extract = vars\nslots = extract(fake)\nslots.update({'Agent': fake.Agent})",
    "extract = vars\nslots = extract(fake)\nslots.__init__({'Agent': fake.Agent})",
    "extract = vars\nslots = extract(fake)\nslots |= {'Agent': fake.Agent}",
    "extract = vars\nslots = extract(fake)\nslots.__ior__({'Agent': fake.Agent})",
    "extract: object = vars\nslots = extract(fake)\nslots['Agent'] = fake.Agent",
    "extract = other = vars\nslots = extract(fake)\nslots['Agent'] = fake.Agent",
    "extract = vars\nother = extract\nslots = other(fake)\nslots['Agent'] = fake.Agent",
    "extract = builtins.vars\nslots = extract(fake)\nslots['Agent'] = fake.Agent",
    "extract = vars\nslots = extract(fake, other)\nslots['Agent'] = fake.Agent",
    "extract = vars\nslots = extract(source=fake)\nslots['Agent'] = fake.Agent",
    "extract = vars\nslots = extract(*args)\nslots['Agent'] = fake.Agent",
    "extract = vars\nslots = extract(fake)\nslots[key] = fake.Agent",
    "extract = vars\nslots = extract(fake)\nslots['other'] = fake.Agent",
    "extract = vars\nslots = extract(fake)\nslots['Agent'] = other.Agent",
])
def test_saved_vars_store_keeps_other_projection_and_mutation_shapes_unproved(namespace_workspace, write):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=write)
    calls = BuilderCalls(resolver)
    for statement in ast.walk(module.tree):
        if isinstance(statement, ast.Assign | ast.Expr | ast.AugAssign):
            assert calls._source_slot_candidate(module, statement) is None
            assert not calls.idempotent_source_slot(module, statement, "agents")


@pytest.mark.parametrize("extra", [
    "other = extract\n", "other = slots\n", "other = extract(fake)\n", "getattr(fake, 'extract')\n",
    "fake.Agent([])\n", "__all__ = ['extract']\n",
])
def test_saved_vars_store_preserves_getter_mapping_escapes_exports_and_source_calls(namespace_workspace, extra):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="extract = vars\nslots = extract(fake)\nslots['Agent'] = fake.Agent", extra=extra,
    )
    try:
        assert not BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[3], "agents")
    except CallLimit:
        pass
    assert not resolver._checking_source_module_slot


def test_saved_vars_store_cannot_borrow_raw_false_or_receiving_ownership(namespace_workspace, monkeypatch):
    from agents_shipgate.inputs import list_expressions, python_imports

    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="extract = vars\nslots = extract(fake)\nslots['Agent'] = fake.Agent",
    )
    calls = BuilderCalls(resolver)
    home, definition, _ = calls._source_slot_candidate(module, module.tree.body[3])
    def forbidden(*args, **kwargs):
        pytest.fail("the saved getter borrowed an ownership shortcut")
    for name in ("_independent_foreign_class_slot_write", "_independent_plain_class_slot_write",
                 "_agent_instance", "_provisional_agent_list_clear"):
        monkeypatch.setattr(list_expressions, name, forbidden)
    assert calls.idempotent_source_slot(module, module.tree.body[3], "agents")
    assert calls.uncalled_source_slot(home, definition)
    monkeypatch.setattr(BuilderCalls, "source_saved_vars_initializer", forbidden)
    monkeypatch.setattr(BuilderCalls, "idempotent_source_slot", forbidden)
    assert python_imports._external_constructor_use(
        resolver, module, "agents", {module.path, home.path}, allow_owner_routes=False,
    ) is not None


def test_saved_vars_store_requires_actual_canonical_getter_after_raw_proof(namespace_workspace, monkeypatch):
    from agents_shipgate.inputs import list_expressions

    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="extract = vars\nslots = extract(fake)\nslots['Agent'] = fake.Agent",
    )
    calls = BuilderCalls(resolver)
    terminal = module.tree.body[3]
    projection = module.tree.body[2].value
    assert calls.idempotent_source_slot(module, terminal, "agents")
    original = list_expressions._namespace_call_reference
    raw_original = list_expressions._qualified_attribute_patches
    raw_active = False
    def raw(view, **kwargs):
        nonlocal raw_active
        previous = raw_active
        raw_active = True
        try:
            return raw_original(view, **kwargs)
        finally:
            raw_active = previous
    def wrong(view, node):
        return "foreign.vars" if node is projection and not raw_active else original(view, node)
    monkeypatch.setattr(list_expressions, "_qualified_attribute_patches", raw)
    monkeypatch.setattr(list_expressions, "_namespace_call_reference", wrong)
    with pytest.raises(CallLimit, match="saved getter primitive"):
        calls.idempotent_source_slot(module, terminal, "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("change", ["source", "builtin_provider", "getter_importer", "mapping_importer"])
def test_saved_vars_store_reconfirms_source_provider_and_handle_currency(namespace_workspace, change):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="extract = vars\nslots = extract(fake)\nslots['Agent'] = fake.Agent",
    )
    calls = BuilderCalls(resolver)
    terminal = module.tree.body[3]
    assert calls.idempotent_source_slot(module, terminal, "agents")
    if change == "source":
        (namespace_workspace / "fake.py").write_text("def Agent(tools):\n    return replacement\n")
    elif change == "builtin_provider":
        (namespace_workspace / "builtins.py").write_text("def vars(*args):\n    return replacement\n")
    else:
        name = "extract" if change == "getter_importer" else "slots"
        (namespace_workspace / "consumer.py").write_text(f"from entry import {name}\n")
    with pytest.raises(CallLimit):
        calls.idempotent_source_slot(module, terminal, "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("write", [
    "vars = replacement\nextract = vars\nslots = extract(fake)\nslots['Agent'] = fake.Agent",
    "from foreign import vars\nextract = vars\nslots = extract(fake)\nslots['Agent'] = fake.Agent",
    "extract = vars\nvars = replacement\nslots = extract(fake)\nslots['Agent'] = fake.Agent",
])
def test_saved_vars_store_refuses_shadowed_builtin_initializer(namespace_workspace, write):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=write)
    assert not BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[-1], "agents")


@pytest.mark.parametrize("alias", ["eval", "exec", "type", "object"])
def test_saved_vars_store_keeps_unsafe_getter_alias_spelling_visible(namespace_workspace, alias):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write=f"{alias} = vars\nslots = {alias}(fake)\nslots['Agent'] = fake.Agent",
    )
    with pytest.raises(CallLimit):
        BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[3], "agents")
    assert not resolver._checking_source_module_slot


def test_saved_vars_store_raw_producer_evidence_preserves_default_marker_and_first_line():
    from agents_shipgate.inputs.python_imports import MODULE_TABLE_COMPUTED, _attribute_patches

    tree = ast.parse("extract = vars; other = globals()\nmore = globals\n")
    producers = {}
    ordinary = _attribute_patches(tree)
    observed = _attribute_patches(tree, patch_producers=producers)
    assert ordinary == observed == {MODULE_TABLE_COMPUTED: 1}
    assert producers[MODULE_TABLE_COMPUTED] == {tree.body[0].value, tree.body[2].value, None}
    assert _attribute_patches(tree) == ordinary


@pytest.mark.parametrize("write,extra", [
    ("extract = vars; other = globals\nslots = extract(fake)\nslots['Agent'] = fake.Agent", ""),
    ("extract = vars; other = globals()\nslots = extract(fake)\nslots['Agent'] = fake.Agent", ""),
    ("extract = vars\nslots = extract(fake)\nslots['Agent'] = fake.Agent", "import sys\nsys.modules[key] = fake\n"),
    ("extract = vars\nslots = extract(fake)\nslots['Agent'] = fake.Agent", "import builtins\nother = builtins.vars\n"),
])
def test_saved_vars_store_requires_complete_raw_marker_origin_population(namespace_workspace, write, extra):
    from agents_shipgate.inputs.python_imports import MODULE_TABLE_COMPUTED, _attribute_patches

    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=write, extra=extra)
    calls = BuilderCalls(resolver)
    terminals = [node for node in module.tree.body if isinstance(node, ast.Assign) and calls._source_slot_candidate(module, node)]
    assert len(terminals) == 1
    terminal = terminals[0]
    getter = calls._source_slot_edge(module, terminal).parts[5].saved_getter
    producers = {}
    assert _attribute_patches(module.tree, patch_producers=producers) == module.attribute_patches
    assert producers[MODULE_TABLE_COMPUTED] != {getter.initializer}
    with pytest.raises(CallLimit, match="raw package-path or module-namespace mutation markers"):
        calls.idempotent_source_slot(module, terminal, "agents")
    assert not resolver._checking_source_module_slot


def test_saved_vars_store_replays_pending_raw_marker_after_zero_census(namespace_workspace, monkeypatch):
    import agents_shipgate.inputs.builder_calls as builder_calls

    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="extract = vars\nslots = extract(fake)\nslots['Agent'] = fake.Agent",
    )
    calls = BuilderCalls(resolver)
    terminal = module.tree.body[3]
    assert calls.idempotent_source_slot(module, terminal, "agents")
    original = builder_calls._attribute_patches
    replays = 0
    def unknown_on_reconfirm(tree, **kwargs):
        nonlocal replays
        answer = original(tree, **kwargs)
        if tree is module.tree and kwargs.get("patch_producers") is not None:
            replays += 1
            if replays == 2:
                kwargs["patch_producers"][builder_calls.MODULE_TABLE_COMPUTED].add(None)
        return answer
    monkeypatch.setattr(builder_calls, "_attribute_patches", unknown_on_reconfirm)
    with pytest.raises(CallLimit, match="undischarged saved getter namespace marker"):
        calls.idempotent_source_slot(module, terminal, "agents")
    assert replays == 2
    assert not resolver._checking_source_module_slot


def test_saved_vars_store_table_scan_keeps_actual_module_origin_and_ordinary_markers(namespace_workspace):
    from agents_shipgate.inputs.python_imports import MODULE_TABLE_COMPUTED

    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="extract = vars\nslots = extract(fake)\nslots['Agent'] = fake.Agent",
    )
    line = module.tree.body[1].lineno
    ordinary = dict(module.attribute_patches)
    runtime = resolver._runtime_attribute_patches(module)
    scan = resolver._patched_names(module.path)
    assert ordinary == runtime == {MODULE_TABLE_COMPUTED: line}
    assert scan.patched[MODULE_TABLE_COMPUTED] == [(module.ref, line, None)]
    assert scan.table_modules[(MODULE_TABLE_COMPUTED, module.ref, line)] == (module,)
    assert BuilderCalls(resolver).source_saved_vars_table_marker(module, MODULE_TABLE_COMPUTED, line)
    resolution = resolver.resolve(module, "fake.Agent")
    assert resolution.resolved and not resolution.caveats
    assert module.attribute_patches == ordinary
    assert resolver._runtime_attribute_patches(module) is runtime and runtime == ordinary
    assert resolver._patched_names(module.path) is scan
    assert scan.patched[MODULE_TABLE_COMPUTED] == [(module.ref, line, None)]


@pytest.mark.parametrize("provenance", ["missing", "ambiguous", "wrong_module", "copied_module", "duplicate_identity"])
def test_saved_vars_store_table_consumption_refuses_missing_or_ambiguous_module_origin(namespace_workspace, provenance):
    from dataclasses import replace

    from agents_shipgate.inputs.python_imports import MODULE_TABLE_COMPUTED

    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="extract = vars\nslots = extract(fake)\nslots['Agent'] = fake.Agent",
    )
    scan = resolver._patched_names(module.path)
    row = (MODULE_TABLE_COMPUTED, module.ref, module.tree.body[1].lineno)
    if provenance == "missing":
        scan.table_modules.clear()
    elif provenance == "ambiguous":
        scan.table_modules[row] = (module, replace(module))
    elif provenance == "wrong_module":
        scan.table_modules[row] = (resolver._patch_scan(namespace_workspace / "fake.py"),)
    elif provenance == "copied_module":
        scan.table_modules[row] = (replace(module),)
    else:
        scan.table_modules[row] = (module, module)
    resolution = resolver.resolve(module, "fake.Agent")
    assert resolution.caveats and any("computed" in caveat for caveat in resolution.caveats)
    assert resolver._runtime_attribute_patches(module) == module.attribute_patches


@pytest.mark.parametrize("flag", ["_checking_source_module_slot", "_checking_fresh_dictionary_primitive"])
def test_saved_vars_store_table_marker_refuses_reentry_before_any_model(namespace_workspace, monkeypatch, flag):
    from agents_shipgate.inputs.python_imports import MODULE_TABLE_COMPUTED

    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="extract = vars\nslots = extract(fake)\nslots['Agent'] = fake.Agent",
    )
    calls = BuilderCalls(resolver)
    def forbidden(*args, **kwargs):
        pytest.fail("a reentered table marker attempted another model")
    monkeypatch.setattr(calls, "source_saved_vars_initializer", forbidden)
    setattr(resolver, flag, True)
    try:
        assert not calls.source_saved_vars_table_marker(module, MODULE_TABLE_COMPUTED, module.tree.body[1].lineno)
    finally:
        setattr(resolver, flag, False)


def test_saved_vars_store_table_marker_keeps_unknown_same_line_origin_unread(namespace_workspace, monkeypatch):
    from agents_shipgate.inputs.python_imports import MODULE_TABLE_COMPUTED

    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="extract = vars; other = globals()\nslots = extract(fake)\nslots['Agent'] = fake.Agent",
    )
    calls = BuilderCalls(resolver)
    def forbidden(*args, **kwargs):
        pytest.fail("an unknown producer reached a source proof")
    monkeypatch.setattr(calls, "source_saved_vars_initializer", forbidden)
    assert not calls.source_saved_vars_table_marker(module, MODULE_TABLE_COMPUTED, module.tree.body[1].lineno)
    assert resolver.resolve(module, "fake.Agent").caveats


def test_saved_vars_store_table_admission_rechecks_source_after_a_completed_read(namespace_workspace):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="extract = vars\nslots = extract(fake)\nslots['Agent'] = fake.Agent",
    )
    assert not resolver.resolve(module, "fake.Agent").caveats
    (namespace_workspace / "fake.py").write_text("def Agent(tools):\n    return replacement\n")
    assert resolver.resolve(module, "fake.Agent").caveats
    assert not resolver._checking_source_module_slot


def test_saved_vars_store_table_marker_retains_nonzero_actual_function_census(namespace_workspace):
    from agents_shipgate.inputs.python_imports import MODULE_TABLE_COMPUTED

    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="extract = vars\nslots = extract(fake)\nslots['Agent'] = fake.Agent", extra="fake.Agent([])\n",
    )
    assert not BuilderCalls(resolver).source_saved_vars_table_marker(module, MODULE_TABLE_COMPUTED, module.tree.body[1].lineno)
    assert resolver.resolve(module, "fake.Agent").caveats
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("family", ["agents", "google.adk"])
@pytest.mark.parametrize("names", [("producer", "Handler", "slots", "combine"), ("service", "dispatch", "mapping", "apply")])
def test_saved_bound_ior_keeps_method_mapping_and_actual_function_roles_distinct(namespace_workspace, family, names):
    source, function, mapping_name, handle = names
    (namespace_workspace / f"{source}.py").write_text(f"def {function}(tools):\n    return tools\n")
    text = (f"import {source}\n{mapping_name} = {source}.__dict__\n{handle} = {mapping_name}.__ior__\n"
            f"{handle}({{'{function}': {source}.{function}}})\n")
    path = namespace_workspace / "entry.py"
    path.write_text(text)
    resolver = ImportResolver(namespace_workspace)
    module = resolver.entry(path, ast.parse(text), text)
    calls = BuilderCalls(resolver)
    terminal = module.tree.body[3]
    edge = calls._source_slot_edge(module, terminal)
    mapping, method = edge.parts[5], edge.saved_bound_ior
    assert mapping.declaration is module.tree.body[1]
    assert mapping.projection is mapping.declaration.value
    assert method.assignment is module.tree.body[2]
    assert method.target is method.assignment.targets[0]
    assert method.initializer is method.assignment.value
    assert mapping.receiver is method.receiver is method.initializer.value
    assert method.callee is edge.callee is terminal.value.func
    assert method.call is edge.call is terminal.value
    assert method.payload is edge.payload is terminal.value.args[0]
    assert method.callee is not method.target and method.initializer is not method.callee
    assert edge.rhs is method.payload.values[0] and edge.parts[4] is method.payload.keys[0]
    assert edge.primitive is None and edge.setter_import is None and edge.bare_setter is None
    home, definition, actual = calls._source_slot_candidate(module, terminal)
    assert actual is terminal
    ordinary = calls.callers(home, definition, allow_empty=True)
    assert ordinary.limits and not ordinary.sites
    assert calls.idempotent_source_slot(module, terminal, family)
    assert calls.idempotent_source_slot(module, method.callee, family)
    assert calls.idempotent_source_slot(module, mapping.receiver, family)
    assert not calls.idempotent_source_slot(module, method.initializer, family)
    assert calls.source_saved_bound_ior_initializer(module, method.initializer, family)
    assert not calls.source_saved_bound_ior_initializer(module, method.callee, family)
    assert calls.callers(home, definition, allow_empty=True) is ordinary
    assert ordinary.limits and not ordinary.sites
    assert calls.uncalled_source_slot(home, definition)


@pytest.mark.parametrize("write", [
    "slots = vars(fake)\ncombine = slots.__ior__\ncombine({'Agent': fake.Agent})",
    "slots = getattr(fake, '__dict__')\ncombine = slots.__ior__\ncombine({'Agent': fake.Agent})",
    "slots = fake.__dict__\ncombine = slots.update\ncombine({'Agent': fake.Agent})",
    "slots = fake.__dict__\ncombine = slots.__init__\ncombine({'Agent': fake.Agent})",
    "slots = fake.__dict__\ncombine: object = slots.__ior__\ncombine({'Agent': fake.Agent})",
    "slots = fake.__dict__\ncombine = other = slots.__ior__\ncombine({'Agent': fake.Agent})",
    "slots = fake.__dict__\ncombine = slots.__ior__\nother = combine\nother({'Agent': fake.Agent})",
    "slots = fake.__dict__\ncombine = slots.__ior__\nresult = combine({'Agent': fake.Agent})",
    "slots = fake.__dict__\ncombine = slots.__ior__\nconsume(combine({'Agent': fake.Agent}))",
    "slots = fake.__dict__\ncombine = slots.__ior__\ncombine({'other': fake.Agent})",
    "slots = fake.__dict__\ncombine = slots.__ior__\ncombine({key: fake.Agent})",
    "slots = fake.__dict__\ncombine = slots.__ior__\ncombine({'Agent': other.Agent})",
    "slots = fake.__dict__\ncombine = slots.__ior__\ncombine({'Agent': fake.Agent, 'Other': fake.Agent})",
    "slots = fake.__dict__\ncombine = slots.__ior__\ncombine(Agent=fake.Agent)",
    "slots = fake.__dict__\ncombine = slots.__ior__\ncombine(*args)",
    "slots = fake.__dict__\ncombine = slots.__ior__\ncombine({'Agent': fake.Agent})\ncombine({'Agent': fake.Agent})",
    "slots = fake.__dict__\ncombine = slots.__ior__\ncombine({'Agent': fake.Agent})\ncopy = combine",
    "slots = fake.__dict__\ncombine = slots.__ior__\ncombine({'Agent': fake.Agent})\ncopy = slots",
    "slots = fake.__dict__\ncombine = slots.__ior__\ncombine({'Agent': fake.Agent})\ndef capture():\n    return combine",
    "slots = fake.__dict__\ncombine = slots.__ior__\ncombine({'Agent': fake.Agent})\ndef capture():\n    return slots",
    "slots = fake.__dict__\ncombine = slots.__ior__\ncombine({'Agent': fake.Agent})\ndel combine",
    "slots = fake.__dict__\ncombine = slots.__ior__\ncombine({'Agent': fake.Agent})\ndel slots",
    "slots = fake.__dict__\ncombine = slots.__ior__\ncombine({'Agent': fake.Agent})\n__all__ = ['combine']",
])
def test_saved_bound_ior_keeps_other_shapes_and_either_lifetime_escape_unproved(namespace_workspace, write):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=write)
    calls = BuilderCalls(resolver)
    for statement in module.tree.body:
        if isinstance(statement, ast.Expr):
            assert calls._source_slot_candidate(module, statement) is None
            assert not calls.idempotent_source_slot(module, statement, "agents")


@pytest.mark.parametrize("extra", ["fake.Agent([])\n", "import builtins\nbuiltins.dict.__ior__ = other\n", "fake.__class__ = other\n"])
def test_saved_bound_ior_preserves_nonzero_calls_and_native_provider_changes(namespace_workspace, extra):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="slots = fake.__dict__\ncombine = slots.__ior__\ncombine({'Agent': fake.Agent})", extra=extra,
    )
    try:
        assert not BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[3], "agents")
    except CallLimit:
        pass
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("name", ["slots", "combine"])
def test_saved_bound_ior_preserves_mapping_and_method_importer_obligations(namespace_workspace, name):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="slots = fake.__dict__\ncombine = slots.__ior__\ncombine({'Agent': fake.Agent})",
    )
    (namespace_workspace / "consumer.py").write_text(f"from entry import {name}\n")
    with pytest.raises(CallLimit, match="imported intrinsic source mapping namespace"):
        BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[3], "agents")


def test_saved_bound_ior_requires_full_actual_wildcard_producer_bag(namespace_workspace, monkeypatch):
    from agents_shipgate.inputs import list_expressions

    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="slots = fake.__dict__\ncombine = slots.__ior__\ncombine({'Agent': fake.Agent})",
    )
    calls = BuilderCalls(resolver)
    terminal = module.tree.body[3]
    assert calls.idempotent_source_slot(module, terminal, "agents")
    raw = list_expressions._qualified_attribute_patches
    def ambiguous(view, **kwargs):
        patches = raw(view, **kwargs)
        if view.module is module and kwargs.get("patch_producers") is not None:
            kwargs["patch_producers"].setdefault("fake.*", set()).add(None)
        return patches
    monkeypatch.setattr(list_expressions, "_qualified_attribute_patches", ambiguous)
    with pytest.raises(CallLimit, match="wildcard provenance"):
        calls.idempotent_source_slot(module, terminal, "agents")
    assert not resolver._checking_source_module_slot


def test_saved_bound_ior_retains_default_raw_maps_and_cannot_borrow_owner_callbacks(namespace_workspace, monkeypatch):
    from agents_shipgate.inputs import list_expressions, python_imports

    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="slots = fake.__dict__\ncombine = slots.__ior__\ncombine({'Agent': fake.Agent})",
    )
    assert module.attribute_patches == {}
    cached = resolver._runtime_attribute_patches(module)
    calls = BuilderCalls(resolver)
    home, definition, _ = calls._source_slot_candidate(module, module.tree.body[3])
    def forbidden(*args, **kwargs):
        pytest.fail("the saved method borrowed a receiving-owner shortcut")
    for name in ("_independent_foreign_class_slot_write", "_independent_plain_class_slot_write",
                 "_agent_instance", "_provisional_agent_list_clear"):
        monkeypatch.setattr(list_expressions, name, forbidden)
    assert calls.idempotent_source_slot(module, module.tree.body[3], "agents")
    assert resolver._runtime_attribute_patches(module) is cached and cached == {}
    monkeypatch.setattr(BuilderCalls, "source_saved_bound_ior_initializer", forbidden)
    monkeypatch.setattr(BuilderCalls, "idempotent_source_slot", forbidden)
    assert python_imports._external_constructor_use(
        resolver, module, "agents", {module.path, home.path}, allow_owner_routes=False,
    ) is not None


def test_saved_bound_ior_replays_entire_origin_bag_after_zero_census(namespace_workspace, monkeypatch):
    from agents_shipgate.inputs import list_expressions

    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="slots = fake.__dict__\ncombine = slots.__ior__\ncombine({'Agent': fake.Agent})",
    )
    census = BuilderCalls._census
    raw = list_expressions._qualified_attribute_patches
    finished = False
    def complete(self, *args, **kwargs):
        nonlocal finished
        result = census(self, *args, **kwargs)
        finished = True
        return result
    def changed(view, **kwargs):
        patches = raw(view, **kwargs)
        if finished and view.module is module and kwargs.get("patch_producers") is not None:
            kwargs["patch_producers"].setdefault("fake.*", set()).add(None)
        return patches
    monkeypatch.setattr(BuilderCalls, "_census", complete)
    monkeypatch.setattr(list_expressions, "_qualified_attribute_patches", changed)
    with pytest.raises(CallLimit, match="undischarged saved method wildcard"):
        BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[3], "agents")
    assert finished and not resolver._checking_source_module_slot


@pytest.mark.parametrize("flag", ["_checking_source_module_slot", "_checking_fresh_dictionary_primitive"])
def test_saved_bound_ior_initializer_refuses_active_reentry_before_model(namespace_workspace, monkeypatch, flag):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="slots = fake.__dict__\ncombine = slots.__ior__\ncombine({'Agent': fake.Agent})",
    )
    setattr(resolver, flag, True)
    def forbidden(*args, **kwargs):
        pytest.fail("the saved initializer attempted a model under reentry")
    monkeypatch.setattr(BuilderCalls, "idempotent_source_slot", forbidden)
    assert not BuilderCalls(resolver).source_saved_bound_ior_initializer(module, module.tree.body[2].value, "agents")
    assert getattr(resolver, flag)


def test_saved_bound_ior_rechecks_source_currency_after_completed_proof(namespace_workspace):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="slots = fake.__dict__\ncombine = slots.__ior__\ncombine({'Agent': fake.Agent})",
    )
    calls = BuilderCalls(resolver)
    assert calls.idempotent_source_slot(module, module.tree.body[3], "agents")
    (namespace_workspace / "fake.py").write_text("def Agent(tools):\n    return replacement\n")
    with pytest.raises(CallLimit):
        calls.idempotent_source_slot(module, module.tree.body[3], "agents")
    assert not resolver._checking_source_module_slot


def test_saved_bound_ior_rejects_a_new_replayed_hook_with_selected_producer_intact(namespace_workspace, monkeypatch):
    from agents_shipgate.inputs import list_expressions

    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="slots = fake.__dict__\ncombine = slots.__ior__\ncombine({'Agent': fake.Agent})",
    )
    census = BuilderCalls._census
    raw = list_expressions._qualified_attribute_patches
    finished = False
    def complete(self, *args, **kwargs):
        nonlocal finished
        result = census(self, *args, **kwargs)
        finished = True
        return result
    def new_hook(view, **kwargs):
        patches = raw(view, **kwargs)
        if finished and view.module is module and kwargs.get("patch_producers") is not None:
            initializer = module.tree.body[2].value
            assert kwargs["patch_producers"]["fake.*"] == {initializer}
            patches.add("fake.__getattribute__")
            kwargs["patch_producers"]["fake.__getattribute__"] = {module.tree.body[0]}
        return patches
    monkeypatch.setattr(BuilderCalls, "_census", complete)
    monkeypatch.setattr(list_expressions, "_qualified_attribute_patches", new_hook)
    with pytest.raises(CallLimit, match="undischarged saved method wildcard"):
        BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[3], "agents")
    assert finished and not resolver._checking_source_module_slot


@pytest.mark.parametrize("family", ["agents", "google.adk"])
@pytest.mark.parametrize("function", ["update", "clear", "pop"])
def test_saved_bound_ior_closes_only_the_selected_actual_function_read_origin(namespace_workspace, family, function):
    from agents_shipgate.inputs import list_expressions

    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, home=f"def {function}(tools):\n    return tools\n",
        write=f"slots = fake.__dict__\ncombine = slots.__ior__\ncombine({{'{function}': fake.{function}}})",
    )
    calls = BuilderCalls(resolver)
    terminal = module.tree.body[3]
    edge = calls._source_slot_edge(module, terminal)
    home, definition, selected = calls._source_slot_candidate(module, terminal)
    assert definition.name == function and selected is terminal
    view = list_expressions._View(module.ref, module.tree, calls.scopes(module), module.bindings, module, set())
    view.lookup = list_expressions.bindings_at(view.scopes, view.bindings)
    view.resolver = resolver
    origins = {}
    patches = list_expressions._qualified_attribute_patches(view, raw_namespaces=True, patch_producers=origins)
    expected = {edge.saved_bound_ior.initializer, edge.rhs}
    assert origins["fake.*"] == expected and not view.namespace_builtins_changed
    assert calls.idempotent_source_slot(module, terminal, family)
    assert calls.source_saved_bound_ior_initializer(module, edge.saved_bound_ior.initializer, family)
    assert calls.uncalled_source_slot(home, definition)
    after = {}
    assert list_expressions._qualified_attribute_patches(view, raw_namespaces=True, patch_producers=after) == patches
    assert after == origins and module.attribute_patches == {}


@pytest.mark.parametrize("family", ["agents", "google.adk"])
def test_saved_bound_ior_function_read_origin_does_not_discharge_a_function_call(namespace_workspace, family):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, home="def update(tools):\n    return tools\n",
        write="slots = fake.__dict__\ncombine = slots.__ior__\ncombine({'update': fake.update})",
        extra="fake.update([])\n",
    )
    try:
        assert not BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[3], family)
    except CallLimit:
        pass
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("change", ["caller", "nested_caller", "empty_directory", "removed_file", "file_to_directory", "directory_to_file", "venv_added", "venv_removed", "venv_kind"])
def test_source_slot_reconfirms_consumed_inventory_after_zero_census(namespace_workspace, monkeypatch, change):
    from agents_shipgate.core.static_inputs import active_static_input_snapshot

    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace)
    assert active_static_input_snapshot() is None
    (namespace_workspace / "quiet.py").write_text("# unrelated source\n")
    nested = namespace_workspace / "nested"
    nested.mkdir()
    venv = namespace_workspace / "environment"
    venv.mkdir()
    marker = venv / "pyvenv.cfg"
    if change in {"venv_removed", "venv_kind"}:
        marker.write_text("home = unused\n")
        (venv / "caller.py").write_text("import fake\nfake.Agent([])\n")
    original = BuilderCalls._census
    changed = False
    def census(self, *args, **kwargs):
        nonlocal changed
        result = original(self, *args, **kwargs)
        if kwargs.get("source_slot_values") and not changed:
            assert not result.sites and not result.limits
            changed = True
            if change == "caller":
                (namespace_workspace / "late.py").write_text("import fake\nfake.Agent([])\n")
            elif change == "nested_caller":
                (nested / "late.py").write_text("import fake\nfake.Agent([])\n")
            elif change == "empty_directory":
                (namespace_workspace / "late").mkdir()
            elif change == "removed_file":
                (namespace_workspace / "quiet.py").unlink()
            elif change == "file_to_directory":
                (namespace_workspace / "quiet.py").unlink()
                (namespace_workspace / "quiet.py").mkdir()
            elif change == "directory_to_file":
                nested.rmdir()
                nested.write_text("# changed kind\n")
            elif change == "venv_added":
                marker.write_text("home = unused\n")
            elif change == "venv_removed":
                marker.unlink()
            else:
                marker.unlink()
                marker.mkdir()
        return result
    monkeypatch.setattr(BuilderCalls, "_census", census)
    with pytest.raises(CallLimit, match="caller inventory"):
        BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[1].targets[0].value, "agents")
    assert changed and not resolver._checking_source_module_slot


def test_cached_inventory_keeps_original_currency_obligations(namespace_workspace):
    resolver, _, _ = _source_dictionary_slot_fixture(namespace_workspace)
    calls = BuilderCalls(resolver)
    first = calls._inventory()
    assert calls._inventory() is first
    calls._namespace_directories = {}
    calls._namespace_directory_currency()
    (namespace_workspace / "late.py").write_text("import fake\nfake.Agent([])\n")
    assert calls._inventory() is first and all(path.name != "late.py" for path in first)
    with pytest.raises(CallLimit, match="caller inventory"):
        calls._namespace_directory_currency()



def test_inventory_currency_rejects_new_names_before_reading_their_kinds(namespace_workspace, monkeypatch):
    resolver, _, _ = _source_dictionary_slot_fixture(namespace_workspace)
    calls = BuilderCalls(resolver)
    calls._inventory()
    calls._namespace_directories = {}
    late = namespace_workspace / "late.py"
    late.write_text("import fake\nfake.Agent([])\n")
    original = Path.lstat
    def lstat(path, *args, **kwargs):
        if path == late:
            raise AssertionError("a new entry kind was read before name drift was refused")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(CallLimit, match="caller inventory"):
        calls._namespace_directory_currency()


@pytest.mark.parametrize("excluded", ["tests", "node_modules"])
def test_inventory_currency_does_not_expand_consumed_source_scope(namespace_workspace, excluded):
    resolver, _, _ = _source_dictionary_slot_fixture(namespace_workspace)
    ignored = namespace_workspace / excluded
    if excluded == "tests":
        ignored /= "fixtures"  # The listing of tests itself is consumed; this child directory is skipped.
    ignored.mkdir(parents=True)
    (ignored / "unread.py").write_text("import fake\nfake.Agent([])\n")
    calls = BuilderCalls(resolver)
    assert ignored / "unread.py" not in calls._inventory()
    calls._namespace_directories = {}
    (ignored / "late.py").write_text("unknown framework contents\n")
    calls._namespace_directory_currency()



def test_inventory_currency_keeps_consumed_test_directory_membership(namespace_workspace):
    resolver, _, _ = _source_dictionary_slot_fixture(namespace_workspace)
    directory = namespace_workspace / "tests"
    directory.mkdir()
    (directory / "unread.py").write_text("import fake\nfake.Agent([])\n")
    calls = BuilderCalls(resolver)
    assert directory / "unread.py" not in calls._inventory()
    calls._namespace_directories = {}
    calls._namespace_directory_currency()
    (directory / "late.py").write_text("import fake\nfake.Agent([])\n")
    with pytest.raises(CallLimit, match="caller inventory"):
        calls._namespace_directory_currency()


@pytest.mark.parametrize("family", ["agents", "google.adk"])
@pytest.mark.parametrize("function", ["Agent", "change", "update", "operator"])
def test_direct_operator_setitem_keeps_actual_primitive_projection_and_function_roles(namespace_workspace, family, function):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, home=f"def {function}(tools):\n    return tools\n",
        write=f"import operator\noperator.setitem(fake.__dict__, '{function}', fake.{function})",
    )
    calls = BuilderCalls(resolver)
    statement = module.tree.body[2]
    record = calls._source_operator_setitem(module, statement)
    assert record.statement is module.tree.body[1] and record.imported is record.statement.names[0]
    assert record.call is statement.value and record.callee is record.call.func and record.root is record.callee.value
    assert record.projection is record.call.args[0] and record.key is record.call.args[1] and record.rhs is record.call.args[2]
    edge = calls._source_slot_edge(module, statement)
    assert edge.operator_setitem is record or edge.operator_setitem == record
    assert edge.setter_import is None and edge.primitive_import is None and edge.saved_bound_ior is None
    for node in (statement, record.call, record.callee, record.root, record.projection, record.key, record.rhs):
        assert calls.idempotent_source_slot(module, node, family)
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("write", [
    "import operator as op\nop.setitem(fake.__dict__, 'Agent', fake.Agent)",
    "from operator import setitem\nsetitem(fake.__dict__, 'Agent', fake.Agent)",
    "import operator\nchange = operator.setitem\nchange(fake.__dict__, 'Agent', fake.Agent)",
    "import operator\nslots = fake.__dict__\noperator.setitem(slots, 'Agent', fake.Agent)",
    "import operator\noperator.ior(fake.__dict__, {'Agent': fake.Agent})",
    "import operator\nresult = operator.setitem(fake.__dict__, 'Agent', fake.Agent)",
    "import operator\noperator.setitem(fake.__dict__, key, fake.Agent)",
    "import operator\noperator.setitem(fake.__dict__, 'Other', fake.Agent)",
    "import operator\noperator.setitem(fake.__dict__, 'Agent', replacement)",
    "import operator\noperator.setitem(fake.__dict__, 'Agent', fake.Agent, **extra)",
    "import operator\noperator.setitem(fake.__dict__, 'Agent', fake.Agent)\nconsume(operator)",
    "import operator\noperator.setitem(fake.__dict__, 'Agent', fake.Agent)\ndef capture():\n    return operator",
    "import operator\noperator.setitem(fake.__dict__, 'Agent', fake.Agent)\noperator.setitem = replacement",
])
def test_direct_operator_setitem_leaves_aliases_other_primitives_and_escapes_unproved(namespace_workspace, write):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=write)
    calls = BuilderCalls(resolver)
    assert not any(calls._source_operator_setitem(module, statement) for statement in module.tree.body)


@pytest.mark.parametrize("extra", ["fake.Agent([])\n", "import operator\noperator.setitem = replacement\n"])
def test_direct_operator_setitem_preserves_calls_and_foreign_native_patches(namespace_workspace, extra):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write="import operator\noperator.setitem(fake.__dict__, 'Agent', fake.Agent)")
    (namespace_workspace / "other.py").write_text("import fake\n" + extra)
    calls = BuilderCalls(resolver)
    if extra == "fake.Agent([])\n":
        assert not calls.idempotent_source_slot(module, module.tree.body[2], "agents")
    else:
        with pytest.raises(CallLimit):
            calls.idempotent_source_slot(module, module.tree.body[2], "agents")
    assert not resolver._checking_source_module_slot


def test_direct_operator_setitem_keeps_generic_context_unsupported(namespace_workspace):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write="import operator\noperator.setitem(fake.__dict__, 'Agent', fake.Agent)")
    with pytest.raises(CallLimit):
        BuilderCalls(resolver).namespace_source_context("agents")
    assert BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[2], "agents")


@pytest.mark.parametrize("family", ["agents", "google.adk"])
@pytest.mark.parametrize("provider", ["file", "package", "link"])
def test_direct_operator_setitem_requires_absent_native_provider(namespace_workspace, family, provider):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write="import operator\noperator.setitem(fake.__dict__, 'Agent', fake.Agent)")
    if provider == "package":
        path = namespace_workspace / "operator"
        path.mkdir()
        (path / "__init__.py").write_text("def setitem(*args):\n    return None\n")
    elif provider == "file":
        (namespace_workspace / "operator.py").write_text("def setitem(*args):\n    return None\n")
    else:
        target = namespace_workspace / "counterfeit.txt"
        target.write_text("def setitem(*args):\n    return None\n")
        (namespace_workspace / "operator.py").symlink_to(target)
    with pytest.raises(CallLimit):
        BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[2], family)


@pytest.mark.parametrize("home", [
    "def Agent(tools):\n    return tools\ndef __getattr__(name):\n    return replacement\n",
    "def Agent(tools):\n    return tools\n__class__ = replacement\n",
    "class Agent:\n    pass\n",
])
def test_direct_operator_setitem_cannot_prove_a_custom_module_or_class_field(namespace_workspace, home):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, home=home, write="import operator\noperator.setitem(fake.__dict__, 'Agent', fake.Agent)")
    try:
        result = BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[2], "agents")
    except CallLimit:
        result = False
    assert not result


def test_direct_operator_setitem_cannot_borrow_receiving_callbacks(namespace_workspace, monkeypatch):
    from agents_shipgate.inputs import list_expressions

    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write="import operator\noperator.setitem(fake.__dict__, 'Agent', fake.Agent)")
    def forbidden(*args, **kwargs):
        raise AssertionError("the independent operator proof borrowed a receiving callback")
    for name in ("_independent_foreign_class_slot_write", "_independent_plain_class_slot_write", "_agent_instance", "_provisional_agent_list_clear"):
        monkeypatch.setattr(list_expressions, name, forbidden)
    assert BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[2], "agents")


@pytest.mark.parametrize("repetitions", [1, 200])
def test_direct_operator_candidate_extraction_uses_structural_positions(namespace_workspace, repetitions):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import operator\n" + "operator.setitem(fake.__dict__, 'Agent', fake.Agent)\n" * repetitions,
    )
    calls = BuilderCalls(resolver)
    calls.scopes(module)
    class Body(list):
        def __contains__(self, value):
            raise AssertionError("candidate extraction performed a linear body membership scan")
        def index(self, *args, **kwargs):
            raise AssertionError("candidate extraction performed a linear body index scan")
    module.tree.body = Body(module.tree.body)  # Same physical parsed nodes, instrument only the container.
    records = [calls._source_operator_setitem(module, statement) for statement in module.tree.body]
    assert sum(record is not None for record in records) == (1 if repetitions == 1 else 0)
    assert len(calls._source_operator_positions[module.tree]) == len(module.tree.body)


@pytest.mark.parametrize("family", ["agents", "google.adk"])
@pytest.mark.parametrize("change", ["caller", "same_size_caller", "home", "writer", "expanded", "same_size_expanded"])
def test_source_slot_reconfirms_consumed_text_after_zero_census(namespace_workspace, monkeypatch, family, change):
    from agents_shipgate.core.static_inputs import active_static_input_snapshot

    assert active_static_input_snapshot() is None
    quiet = namespace_workspace / ("test_helper.py" if "expanded" in change else "other.py")
    initial = "#" + " " * 199 + "\n"
    quiet.write_text(initial)
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import operator\noperator.setitem(fake.__dict__, 'Agent', fake.Agent)",
        extra="import test_helper\n" if "expanded" in change else "",
    )
    original = BuilderCalls._census
    changed = False
    def census(self, *args, **kwargs):
        nonlocal changed
        result = original(self, *args, **kwargs)
        if kwargs.get("source_slot_values") and not changed:
            assert not result.sites and not result.limits
            assert quiet in self._texts
            changed = True
            if change == "home":
                path = namespace_workspace / "fake.py"
                path.write_text(path.read_text() + "import fake\nfake.Agent([])\n")
            elif change == "writer":
                path = namespace_workspace / "entry.py"
                path.write_text(path.read_text() + "fake.Agent([])\n")
            else:
                replacement = "import fake\nfake.Agent([])\n"
                if change.startswith("same_size"):
                    replacement += "#" + " " * (len(initial.encode("utf-8")) - len(replacement.encode("utf-8")) - 2) + "\n"
                    assert len(replacement.encode("utf-8")) == len(initial.encode("utf-8"))
                quiet.write_text(replacement)
        return result
    monkeypatch.setattr(BuilderCalls, "_census", census)
    with pytest.raises(CallLimit, match="namespace source bytes"):
        BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[2], family)
    assert changed and not resolver._checking_source_module_slot


@pytest.mark.parametrize("change", ["invalid_utf8", "grown_before_loader"])
def test_consumed_text_currency_refuses_unread_or_grown_input(namespace_workspace, monkeypatch, change):
    from agents_shipgate.inputs import builder_calls

    quiet = namespace_workspace / "other.py"
    quiet.write_text("# quiet\n")
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace)
    original = BuilderCalls._census
    loader = builder_calls.load_text_file
    changed = False
    def load(path):
        if changed and path == quiet and change == "grown_before_loader":
            raise AssertionError("grown consumed bytes reached the loader before size refusal")
        return loader(path)
    def census(self, *args, **kwargs):
        nonlocal changed
        result = original(self, *args, **kwargs)
        if kwargs.get("source_slot_values") and not changed:
            assert not result.sites and not result.limits
            assert quiet in self._texts
            changed = True
            quiet.write_bytes(b"\xff" * 8 if change == "invalid_utf8" else b"#" * 100)
        return result
    monkeypatch.setattr(builder_calls, "load_text_file", load)
    monkeypatch.setattr(BuilderCalls, "_census", census)
    with pytest.raises(CallLimit):
        BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[1], "agents")
    assert changed and not resolver._checking_source_module_slot


def test_consumed_text_currency_reconfirms_cached_bytes_without_replacing_them(namespace_workspace, monkeypatch):
    from agents_shipgate.inputs import builder_calls

    quiet = namespace_workspace / "other.py"
    quiet.write_text("# unchanged µ\n")
    resolver, _, _ = _source_dictionary_slot_fixture(namespace_workspace)
    calls = BuilderCalls(resolver)
    calls.namespace_source_context("agents")
    captured = dict(calls._texts)
    accounted = calls._read_bytes
    loader = builder_calls.load_text_file
    reads = []
    def load(path):
        reads.append(path)
        return loader(path)
    monkeypatch.setattr(builder_calls, "load_text_file", load)
    for _ in range(2):
        calls._namespace_directory_currency()
        assert calls._texts == captured and calls._read_bytes == accounted
    assert reads.count(quiet) == 2


@pytest.mark.parametrize("excluded", ["tests/test_quiet.py", "node_modules/quiet.py"])
def test_consumed_text_currency_does_not_read_unconsumed_files(namespace_workspace, monkeypatch, excluded):
    path = namespace_workspace / excluded
    path.parent.mkdir()
    path.write_text("# quiet\n")
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace)
    original = BuilderCalls._census
    changed = False
    def census(self, *args, **kwargs):
        nonlocal changed
        result = original(self, *args, **kwargs)
        if kwargs.get("source_slot_values") and not changed:
            assert not result.sites and not result.limits
            assert path not in self._texts
            changed = True
            path.write_text("import fake\nfake.Agent([])\n")
        return result
    monkeypatch.setattr(BuilderCalls, "_census", census)
    assert BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[1], "agents")
    assert changed and not resolver._checking_source_module_slot


def test_consumed_text_currency_checks_aggregate_bound_before_loading(namespace_workspace, monkeypatch):
    from agents_shipgate.inputs import builder_calls

    resolver, _, _ = _source_dictionary_slot_fixture(namespace_workspace)
    calls = BuilderCalls(resolver)
    calls.namespace_source_context("agents")
    monkeypatch.setattr(builder_calls, "MAX_TOTAL_BYTES", calls._read_bytes - 1)
    def forbidden(path):
        raise AssertionError("an over-budget consumed population reached the loader")
    monkeypatch.setattr(builder_calls, "load_text_file", forbidden)
    with pytest.raises(CallLimit, match="source bytes"):
        calls._namespace_directory_currency()


@pytest.mark.parametrize("family", ["agents", "google.adk"])
@pytest.mark.parametrize("name", ["Agent", "update", "ior", "operator"])
def test_direct_operator_ior_keeps_actual_saved_mapping_and_function_roles(namespace_workspace, family, name):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, home=f"def {name}(tools):\n    return tools\n",
        write=f"import operator\nslots = fake.__dict__\noperator.ior(slots, {{'{name}': fake.{name}}})",
    )
    calls = BuilderCalls(resolver)
    statement = module.tree.body[3]
    edge = calls._source_slot_edge(module, statement)
    assert edge is not None and edge.operator_ior is not None
    record = edge.operator_ior
    assert record.call is statement.value and record.mapping.receiver is record.call.args[0]
    assert record.mapping.declaration is module.tree.body[2]
    assert record.payload is record.call.args[1] and record.rhs is record.payload.values[0]
    assert edge.operator_setitem is None and edge.saved_bound_ior is None and edge.primitive_import is None
    for node in (statement, record.call, record.callee, record.root, record.mapping.receiver,
                 record.mapping.projection, record.key, record.rhs):
        assert calls.idempotent_source_slot(module, node, family)
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("write", [
    "import operator as op\nslots = fake.__dict__\nop.ior(slots, {'Agent': fake.Agent})",
    "from operator import ior\nslots = fake.__dict__\nior(slots, {'Agent': fake.Agent})",
    "import operator\nslots = fake.__dict__\nchange = operator.ior\nchange(slots, {'Agent': fake.Agent})",
    "import operator\nslots = vars(fake)\noperator.ior(slots, {'Agent': fake.Agent})",
    "import operator\nslots = getattr(fake, '__dict__')\noperator.ior(slots, {'Agent': fake.Agent})",
    "import operator\nother = fake\nslots = other.__dict__\noperator.ior(slots, {'Agent': fake.Agent})",
    "import operator\nslots = fake.__dict__\nsecond = slots\noperator.ior(second, {'Agent': fake.Agent})",
    "import operator\nslots: dict = fake.__dict__\noperator.ior(slots, {'Agent': fake.Agent})",
    "import operator\nslots = second = fake.__dict__\noperator.ior(slots, {'Agent': fake.Agent})",
    "import operator\nslots = fake.__dict__\nslots = replacement\noperator.ior(slots, {'Agent': fake.Agent})",
    "import operator\nslots = fake.__dict__\nresult = operator.ior(slots, {'Agent': fake.Agent})",
    "import operator\nslots = fake.__dict__\noperator.ior(slots, {'Agent': fake.Agent}, extra)",
    "import operator\nslots = fake.__dict__\noperator.ior(slots, {'Agent': fake.Agent}, **extra)",
    "import operator\nslots = fake.__dict__\noperator.ior(*slots, {'Agent': fake.Agent})",
    "import operator\nslots = fake.__dict__\noperator.ior(slots, {**extra, 'Agent': fake.Agent})",
    "import operator\nslots = fake.__dict__\noperator.ior(slots, {key: fake.Agent})",
    "import operator\nslots = fake.__dict__\noperator.ior(slots, {'Other': fake.Agent})",
    "import operator\nslots = fake.__dict__\noperator.ior(slots, {'Agent': replacement})",
    "import operator\nslots = fake.__dict__\noperator.ior(slots, {'Agent': fake.Agent})\nconsume(slots)",
    "import operator\nslots = fake.__dict__\noperator.ior(slots, {'Agent': fake.Agent})\ndef capture():\n    return slots",
    "import operator\nslots = fake.__dict__\noperator.ior(slots, {'Agent': fake.Agent})\nconsume(operator)",
])
def test_direct_operator_ior_does_not_admit_other_grammar_or_escapes(namespace_workspace, write):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=write)
    calls = BuilderCalls(resolver)
    assert not any(calls._source_operator_ior(module, statement) for statement in module.tree.body)


@pytest.mark.parametrize("provider", ["file", "package", "link"])
@pytest.mark.parametrize("family", ["agents", "google.adk"])
def test_direct_operator_ior_requires_absent_native_provider(namespace_workspace, family, provider):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import operator\nslots = fake.__dict__\noperator.ior(slots, {'Agent': fake.Agent})",
    )
    if provider == "package":
        path = namespace_workspace / "operator"
        path.mkdir()
        (path / "__init__.py").write_text("def ior(*args):\n    return None\n")
    elif provider == "file":
        (namespace_workspace / "operator.py").write_text("def ior(*args):\n    return None\n")
    else:
        target = namespace_workspace / "counterfeit.txt"
        target.write_text("def ior(*args):\n    return None\n")
        (namespace_workspace / "operator.py").symlink_to(target)
    with pytest.raises(CallLimit):
        BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[3], family)


@pytest.mark.parametrize("extra", ["fake.Agent([])\n", "import operator\noperator.ior = replacement\n"])
def test_direct_operator_ior_preserves_real_calls_and_native_patches(namespace_workspace, extra):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import operator\nslots = fake.__dict__\noperator.ior(slots, {'Agent': fake.Agent})",
    )
    (namespace_workspace / "other.py").write_text("import fake\n" + extra)
    calls = BuilderCalls(resolver)
    if extra == "fake.Agent([])\n":
        assert not calls.idempotent_source_slot(module, module.tree.body[3], "agents")
    else:
        with pytest.raises(CallLimit):
            calls.idempotent_source_slot(module, module.tree.body[3], "agents")
    assert not resolver._checking_source_module_slot


def test_direct_operator_ior_keeps_generic_context_and_receiving_callbacks_separate(namespace_workspace, monkeypatch):
    from agents_shipgate.inputs import list_expressions

    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import operator\nslots = fake.__dict__\noperator.ior(slots, {'Agent': fake.Agent})",
    )
    with pytest.raises(CallLimit):
        BuilderCalls(resolver).namespace_source_context("agents")
    def forbidden(*args, **kwargs):
        raise AssertionError("the independent operator proof borrowed a receiving callback")
    for name in ("_independent_foreign_class_slot_write", "_independent_plain_class_slot_write", "_agent_instance", "_provisional_agent_list_clear"):
        monkeypatch.setattr(list_expressions, name, forbidden)
    assert BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[3], "agents")


@pytest.mark.parametrize("repetitions", [1, 200])
def test_direct_operator_ior_candidate_extraction_uses_structural_positions(namespace_workspace, repetitions):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import operator\nslots = fake.__dict__\n" + "operator.ior(slots, {'Agent': fake.Agent})\n" * repetitions,
    )
    calls = BuilderCalls(resolver)
    calls.scopes(module)
    class Body(list):
        def __contains__(self, value):
            raise AssertionError("candidate extraction performed a linear body membership scan")
        def index(self, *args, **kwargs):
            raise AssertionError("candidate extraction performed a linear body index scan")
    module.tree.body = Body(module.tree.body)
    records = [calls._source_operator_ior(module, statement) for statement in module.tree.body]
    assert sum(record is not None for record in records) == (1 if repetitions == 1 else 0)
    assert len(calls._source_operator_positions[module.tree]) == len(module.tree.body)


@pytest.mark.parametrize("family", ["agents", "google.adk"])
@pytest.mark.parametrize("name", ["Agent", "update", "ior", "operator"])
def test_from_operator_ior_keeps_actual_saved_mapping_and_function_roles(namespace_workspace, family, name):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, home=f"def {name}(tools):\n    return tools\n",
        write=f"from operator import ior as combine\nslots = fake.__dict__\ncombine(slots, {{'{name}': fake.{name}}})",
    )
    calls = BuilderCalls(resolver)
    statement = module.tree.body[3]
    edge = calls._source_slot_edge(module, statement)
    assert edge is not None and edge.from_operator_ior is not None
    record = edge.from_operator_ior
    assert record.call is statement.value and record.mapping.receiver is record.call.args[0]
    assert record.mapping.declaration is module.tree.body[2]
    assert record.payload is record.call.args[1] and record.rhs is record.payload.values[0]
    assert edge.operator_setitem is None and edge.saved_bound_ior is None and edge.primitive_import is None
    for node in (statement, record.call, record.callee, record.mapping.receiver,
                 record.mapping.projection, record.key, record.rhs):
        assert calls.idempotent_source_slot(module, node, family)
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("provider", ["file", "package", "link"])
@pytest.mark.parametrize("family", ["agents", "google.adk"])
def test_from_operator_ior_requires_absent_native_provider(namespace_workspace, family, provider):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="from operator import ior as combine\nslots = fake.__dict__\ncombine(slots, {'Agent': fake.Agent})",
    )
    if provider == "package":
        path = namespace_workspace / "operator"
        path.mkdir()
        (path / "__init__.py").write_text("def ior(*args):\n    return None\n")
    elif provider == "file":
        (namespace_workspace / "operator.py").write_text("def ior(*args):\n    return None\n")
    else:
        target = namespace_workspace / "counterfeit.txt"
        target.write_text("def ior(*args):\n    return None\n")
        (namespace_workspace / "operator.py").symlink_to(target)
    with pytest.raises(CallLimit):
        BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[3], family)


@pytest.mark.parametrize("extra", ["fake.Agent([])\n", "import operator\noperator.ior = replacement\n"])
def test_from_operator_ior_preserves_real_calls_and_native_patches(namespace_workspace, extra):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="from operator import ior as combine\nslots = fake.__dict__\ncombine(slots, {'Agent': fake.Agent})",
    )
    (namespace_workspace / "other.py").write_text("import fake\n" + extra)
    calls = BuilderCalls(resolver)
    if extra == "fake.Agent([])\n":
        assert not calls.idempotent_source_slot(module, module.tree.body[3], "agents")
    else:
        with pytest.raises(CallLimit):
            calls.idempotent_source_slot(module, module.tree.body[3], "agents")
    assert not resolver._checking_source_module_slot


def test_from_operator_ior_keeps_generic_context_and_receiving_callbacks_separate(namespace_workspace, monkeypatch):
    from agents_shipgate.inputs import list_expressions

    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="from operator import ior as combine\nslots = fake.__dict__\ncombine(slots, {'Agent': fake.Agent})",
    )
    with pytest.raises(CallLimit):
        BuilderCalls(resolver).namespace_source_context("agents")
    def forbidden(*args, **kwargs):
        raise AssertionError("the independent operator proof borrowed a receiving callback")
    for name in ("_independent_foreign_class_slot_write", "_independent_plain_class_slot_write", "_agent_instance", "_provisional_agent_list_clear"):
        monkeypatch.setattr(list_expressions, name, forbidden)
    assert BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[3], "agents")


@pytest.mark.parametrize("repetitions", [1, 200])
def test_from_operator_ior_candidate_extraction_uses_structural_positions(namespace_workspace, repetitions):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="from operator import ior as combine\nslots = fake.__dict__\n" + "combine(slots, {'Agent': fake.Agent})\n" * repetitions,
    )
    calls = BuilderCalls(resolver)
    calls.scopes(module)
    class Body(list):
        def __contains__(self, value):
            raise AssertionError("candidate extraction performed a linear body membership scan")
        def index(self, *args, **kwargs):
            raise AssertionError("candidate extraction performed a linear body index scan")
    module.tree.body = Body(module.tree.body)
    records = [calls._source_from_operator_ior(module, statement) for statement in module.tree.body]
    assert sum(record is not None for record in records) == (1 if repetitions == 1 else 0)
    assert len(calls._source_operator_positions[module.tree]) == len(module.tree.body)



@pytest.mark.parametrize("write", [
    "from operator import ior\nslots = fake.__dict__\nior(slots, {'Agent': fake.Agent})",
    "from operator import ior as vars\nslots = fake.__dict__\nvars(slots, {'Agent': fake.Agent})",
    "from operator import ior as Agent\nslots = fake.__dict__\nAgent(slots, {'Agent': fake.Agent})",
    "from operator import ior as fake\nslots = fake.__dict__\nfake(slots, {'Agent': fake.Agent})",
    "from operator import ior as slots\nslots = fake.__dict__\nslots(slots, {'Agent': fake.Agent})",
    "from operator import ior as combine, setitem\nslots = fake.__dict__\ncombine(slots, {'Agent': fake.Agent})",
    "from .operator import ior as combine\nslots = fake.__dict__\ncombine(slots, {'Agent': fake.Agent})",
    "from other import ior as combine\nslots = fake.__dict__\ncombine(slots, {'Agent': fake.Agent})",
    "from operator import setitem as combine\nslots = fake.__dict__\ncombine(slots, {'Agent': fake.Agent})",
    "from operator import ior as combine\nslots = fake.__dict__\ncombine = replacement\ncombine(slots, {'Agent': fake.Agent})",
    "from operator import ior as combine\nslots = fake.__dict__\nsecond = combine\nsecond(slots, {'Agent': fake.Agent})",
    "from operator import ior as combine\nslots = fake.__dict__\nresult = combine(slots, {'Agent': fake.Agent})",
    "from operator import ior as combine\nslots = vars(fake)\ncombine(slots, {'Agent': fake.Agent})",
    "from operator import ior as combine\nslots = fake.__dict__\ncombine(slots, {'Agent': fake.Agent}, **extra)",
    "from operator import ior as combine\nslots = fake.__dict__\ncombine(slots, {**extra, 'Agent': fake.Agent})",
    "from operator import ior as combine\nslots = fake.__dict__\ncombine(slots, {'Other': fake.Agent})",
    "from operator import ior as combine\nslots = fake.__dict__\ncombine(slots, {'Agent': replacement})",
    "from operator import ior as combine\nslots = fake.__dict__\ncombine(slots, {'Agent': fake.Agent})\nconsume(combine)",
    "from operator import ior as combine\nslots = fake.__dict__\ncombine(slots, {'Agent': fake.Agent})\nconsume(slots)",
])
def test_from_operator_ior_leaves_other_imports_and_escapes_unproved(namespace_workspace, write):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=write)
    calls = BuilderCalls(resolver)
    assert not any(calls._source_from_operator_ior(module, statement) for statement in module.tree.body)


@pytest.mark.parametrize("repetitions", [1, 200])
def test_from_operator_ior_distinct_candidates_share_only_structural_indexes(namespace_workspace, monkeypatch, repetitions):
    from agents_shipgate.inputs import list_expressions

    writes = "\n".join(
        f"from operator import ior as combine{i}\nslots{i} = fake.__dict__\ncombine{i}(slots{i}, {{'Agent': fake.Agent}})"
        for i in range(repetitions)
    )
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=writes)
    calls = BuilderCalls(resolver)
    calls.scopes(module)
    original_walk = ast.walk
    walks = 0
    def walk(node):
        nonlocal walks
        if node is module.tree:
            walks += 1
        return original_walk(node)
    monkeypatch.setattr(ast, "walk", walk)
    statements = list(module.tree.body)
    records = [calls._source_from_operator_ior(module, statement) for statement in statements]
    assert sum(record is not None for record in records) == repetitions
    assert walks == 1
    class Body(list):
        def __iter__(self):
            raise AssertionError("import-boundary selection rescanned every statement")
        def __contains__(self, value):
            raise AssertionError("import-boundary selection rescanned statement membership")
        def index(self, *args, **kwargs):
            raise AssertionError("import-boundary selection rescanned statement positions")
    module.tree.body = Body(module.tree.body)
    monkeypatch.setattr(list_expressions, "_standard_operator", lambda view: False)
    for record in records:
        if record is not None:
            assert not calls._namespace_from_operator_ior_import(module, record.statement, record.imported)
    assert walks == 1  # Failed native proof never grants a role; only syntax routing is instrumented.


@pytest.mark.parametrize("family", ["agents", "google.adk"])
@pytest.mark.parametrize("alias", [False, True])
@pytest.mark.parametrize("name", ["Agent", "update", "operator"])
def test_direct_bare_source_setter_keeps_actual_callee_namespace_and_function_roles(namespace_workspace, family, alias, name):
    write = ("other = fake\n" if alias else "") + f"setattr({'other' if alias else 'fake'}, '{name}', fake.{name})"
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write=write, home=f"def {name}(tools):\n    return tools\n",
    )
    calls = BuilderCalls(resolver)
    statement = module.tree.body[-1]
    edge = calls._source_slot_edge(module, statement)
    assert edge is not None and edge.direct_bare_setter is not None
    record = edge.direct_bare_setter
    assert record.callee is statement.value.func
    assert not any(hasattr(record, name) for name in ("assignment", "statement", "root", "initializer"))
    assert edge.bare_setter is None and edge.setter_import is None and edge.primitive_import is None
    nodes = [statement, record.callee, edge.call, edge.target, edge.rhs, edge.parts[4]]
    if alias:
        initializer = module.tree.body[1].value
        assert calls._source_slot_receiver(module, edge.target, statement)[1] is initializer
        nodes.append(initializer)
    for node in nodes:
        assert calls.idempotent_source_slot(module, node, family)
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("write", [
    "setattr = replacement\nsetattr(fake, 'Agent', fake.Agent)",
    "from builtins import setattr\nsetattr(fake, 'Agent', fake.Agent)",
    "setattr(fake, 'Agent', fake.Agent, extra=None)",
    "setattr(fake, 'Agent', fake.Agent, None)",
    "setattr(*(fake, 'Agent', fake.Agent))",
    "setattr(fake, key, fake.Agent)",
    "setattr(fake, 'Other', fake.Agent)",
    "setattr(fake, 'Agent', replacement)",
    "result = setattr(fake, 'Agent', fake.Agent)",
    "other: object = fake\nsetattr(other, 'Agent', fake.Agent)",
    "other = fake\nsecond = other\nsetattr(second, 'Agent', fake.Agent)",
    "other = fake\nsetattr(fake, 'Agent', other.Agent)",
    "first = fake\nsecond = fake\nsetattr(first, 'Agent', second.Agent)",
    "import fake as other\nsetattr(other, 'Agent', other.Agent)",
    "other = fake\nother = replacement\nsetattr(other, 'Agent', fake.Agent)",
    "other = fake\nsetattr(other, 'Agent', fake.Agent)\nconsume(other)",
    "other = fake\nsetattr(other, 'Agent', fake.Agent)\ndef capture():\n    return other",
    "setattr(fake, 'Agent', fake.Agent)\nconsume(fake)",
    "setattr(fake, 'Agent', fake.Agent)\n__all__ = ['fake']",
])
def test_direct_bare_source_setter_keeps_other_grammar_and_namespace_escapes_unproved(namespace_workspace, write):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=write)
    calls = BuilderCalls(resolver)
    assert not any((edge := calls._source_slot_edge(module, statement)) is not None
                   and edge.direct_bare_setter is not None
                   for statement in module.tree.body if isinstance(statement, ast.Assign | ast.Expr | ast.AugAssign))


@pytest.mark.parametrize("extra", [
    "import builtins\nbuiltins.setattr = replacement\n",
    "import sys\nsys.modules['builtins'] = replacement\n",
    "import fake\nfake.__class__ = replacement\n",
    "import fake\ndef __getattr__(name):\n    return fake\n",
])
def test_direct_bare_source_setter_preserves_raw_native_and_module_machinery(namespace_workspace, extra):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="other = fake\nsetattr(other, 'Agent', fake.Agent)",
    )
    (namespace_workspace / "other.py").write_text(extra)
    with pytest.raises(CallLimit):
        BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[-1], "agents")
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("alias", [False, True])
def test_direct_bare_source_setter_does_not_discharge_an_actual_function_call(namespace_workspace, alias):
    write = ("other = fake\n" if alias else "") + f"setattr({'other' if alias else 'fake'}, 'Agent', fake.Agent)"
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=write)
    (namespace_workspace / "caller.py").write_text("import fake\nfake.Agent([])\n")
    assert not BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[-1], "agents")
    assert not resolver._checking_source_module_slot


def test_direct_bare_source_setter_cannot_borrow_receiving_callbacks(namespace_workspace, monkeypatch):
    from agents_shipgate.inputs import list_expressions

    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="other = fake\nsetattr(other, 'Agent', fake.Agent)",
    )
    def forbidden(*args, **kwargs):
        raise AssertionError("the independent bare setter proof borrowed a receiving callback")
    for name in ("_independent_foreign_class_slot_write", "_independent_plain_class_slot_write", "_agent_instance", "_provisional_agent_list_clear"):
        monkeypatch.setattr(list_expressions, name, forbidden)
    assert BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[-1], "agents")


@pytest.mark.parametrize("alias", [False, True])
def test_direct_bare_source_setter_reconfirms_consumed_text_after_zero_census(namespace_workspace, monkeypatch, alias):
    quiet = namespace_workspace / "quiet.py"
    original_text = "#" + " " * 99 + "\n"
    quiet.write_text(original_text)
    write = ("other = fake\n" if alias else "") + f"setattr({'other' if alias else 'fake'}, 'Agent', fake.Agent)"
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=write)
    original = BuilderCalls._census
    changed = False
    def census(self, *args, **kwargs):
        nonlocal changed
        result = original(self, *args, **kwargs)
        if kwargs.get("source_slot_values") and not changed:
            assert not result.sites and not result.limits and quiet in self._texts
            changed = True
            replacement = "import fake\nfake.Agent([])\n"
            replacement += "#" + " " * (len(original_text) - len(replacement) - 2) + "\n"
            assert len(replacement.encode()) == len(original_text.encode())
            quiet.write_text(replacement)
        return result
    monkeypatch.setattr(BuilderCalls, "_census", census)
    with pytest.raises(CallLimit, match="namespace source bytes"):
        BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[-1], "agents")
    assert changed and not resolver._checking_source_module_slot


@pytest.mark.parametrize("family", ["agents", "google.adk"])
@pytest.mark.parametrize("name", ["Agent", "update", "operator"])
def test_imported_bare_setter_receiver_keeps_two_actual_imports_and_original_function(namespace_workspace, family, name):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write=f"import fake as other\nsetattr(other, '{name}', fake.{name})",
        home=f"def {name}(tools):\n    return tools\n",
    )
    calls = BuilderCalls(resolver)
    statement = module.tree.body[-1]
    left, right = statement.value.args[0], statement.value.args[2].value
    assert calls._source_direct_bare_receivers(module, statement) == ((left, None, None), (right, None, None))
    assert module.bindings[left.id][0].statement is module.tree.body[1]
    assert module.bindings[right.id][0].statement is module.tree.body[0]
    assert module.bindings[left.id][0].node is module.tree.body[1].names[0]
    candidate = calls._source_slot_candidate(module, statement)
    assert candidate is not None and candidate[0].path == namespace_workspace / 'fake.py'
    assert candidate[1] is candidate[0].tree.body[0]
    edge = calls._source_slot_edge(module, statement)
    assert edge is not None and edge.direct_bare_setter is not None
    for node in (statement, edge.call, edge.callee, edge.target, edge.rhs, edge.parts[4]):
        assert calls.idempotent_source_slot(module, node, family)
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("write", [
    "import foreign as other\nsetattr(other, 'Agent', fake.Agent)",
    "import fake.child as other\nsetattr(other, 'Agent', fake.Agent)",
    "import fake as other, foreign\nsetattr(other, 'Agent', fake.Agent)",
    "from fake import other\nsetattr(other, 'Agent', fake.Agent)",
    "import fake as other\nsetattr(other, 'Agent', other.Agent)",
    "import fake as other\nsetattr(fake, 'Agent', other.Agent)",
    "import fake as other\nimport fake as other\nsetattr(other, 'Agent', fake.Agent)",
    "import fake as other\nother = fake\nsetattr(other, 'Agent', fake.Agent)",
    "import fake as other\nconsume(other)\nsetattr(other, 'Agent', fake.Agent)",
    "import fake as other\nsetattr(other, 'Agent', fake.Agent)\nconsume(fake)",
    "import fake as other\nsetattr(other, 'Agent', fake.Agent)\n__all__ = ['other']",
    "import fake as other\nsetattr(other, 'Agent', fake.Agent)\ndef capture():\n    return other",
    "import fake as setattr\nsetattr(setattr, 'Agent', fake.Agent)",
])
def test_imported_bare_setter_receiver_keeps_unproved_import_and_escape_shapes(namespace_workspace, write):
    resolver, module, _ = _source_dictionary_slot_fixture(namespace_workspace, write=write)
    calls = BuilderCalls(resolver)
    assert not any(calls._source_slot_edge(module, statement) is not None for statement in module.tree.body
                   if isinstance(statement, ast.Expr))


@pytest.mark.parametrize("importer", [
    "from entry import other\n",
    "from entry import fake\n",
    "from entry import fake as retained\n",
    "import entry\nconsume(entry.other)\n",
])
def test_imported_bare_setter_receiver_refuses_both_writer_namespace_exports(namespace_workspace, importer):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import fake as other\nsetattr(other, 'Agent', fake.Agent)",
    )
    (namespace_workspace / 'importer.py').write_text(importer)
    with pytest.raises(CallLimit):
        BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[-1], 'agents')
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("family", ["agents", "google.adk"])
def test_imported_bare_setter_receiver_preserves_actual_function_calls(namespace_workspace, family):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import fake as other\nsetattr(other, 'Agent', fake.Agent)",
    )
    (namespace_workspace / 'caller.py').write_text("import fake\nfake.Agent([])\n")
    assert not BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[-1], family)
    assert not resolver._checking_source_module_slot


def test_imported_bare_setter_receiver_rechecks_caller_bytes_after_real_zero(namespace_workspace, monkeypatch):
    quiet = namespace_workspace / 'quiet.py'
    original_text = '#' + ' ' * 99 + '\n'
    quiet.write_text(original_text)
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import fake as other\nsetattr(other, 'Agent', fake.Agent)",
    )
    original = BuilderCalls._census
    changed = False
    def census(self, *args, **kwargs):
        nonlocal changed
        result = original(self, *args, **kwargs)
        if kwargs.get('source_slot_values') and not changed:
            assert not result.sites and not result.limits and quiet in self._texts
            changed = True
            replacement = "import fake\nfake.Agent([])\n"
            replacement += '#' + ' ' * (len(original_text) - len(replacement) - 2) + '\n'
            assert len(replacement.encode()) == len(original_text.encode())
            quiet.write_text(replacement)
        return result
    monkeypatch.setattr(BuilderCalls, '_census', census)
    with pytest.raises(CallLimit, match='namespace source bytes'):
        BuilderCalls(resolver).idempotent_source_slot(module, module.tree.body[-1], 'agents')
    assert changed and not resolver._checking_source_module_slot


@pytest.mark.parametrize("family", ['agents', 'google.adk'])
def test_same_text_entry_preserves_retained_source_slot_function_proof(namespace_workspace, family):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import fake as other\nsetattr(other, 'Agent', fake.Agent)",
    )
    calls = BuilderCalls(resolver)
    statement = module.tree.body[-1]
    home, function, _ = calls._source_slot_candidate(module, statement)
    assert resolver._scanned[home.path] is home and home.path not in resolver._modules
    retained_scopes = calls.scopes(home)
    assert home.path not in resolver._modules
    fresh_tree = ast.parse(home.text)
    registered = resolver.entry(home.path, fresh_tree, home.text)
    assert registered is home and registered.tree.body[0] is function
    assert calls.scopes(registered) is retained_scopes
    assert calls._source_slot_candidate(module, statement)[:2] == (home, function)
    assert BuilderCalls(resolver).uncalled_source_slot(home, function)
    assert calls.idempotent_source_slot(module, statement, family)
    assert not resolver._checking_source_module_slot


@pytest.mark.parametrize("family", ['agents', 'google.adk'])
def test_same_text_entry_cannot_drop_an_actual_source_function_caller(namespace_workspace, family):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import fake as other\nsetattr(other, 'Agent', fake.Agent)",
    )
    (namespace_workspace / 'caller.py').write_text('import fake\nfake.Agent([])\n')
    calls = BuilderCalls(resolver)
    statement = module.tree.body[-1]
    home, function, _ = calls._source_slot_candidate(module, statement)
    assert resolver.entry(home.path, ast.parse(home.text), home.text) is home
    assert not BuilderCalls(resolver).uncalled_source_slot(home, function)
    assert not calls.idempotent_source_slot(module, statement, family)
    assert not resolver._checking_source_module_slot


def test_same_text_entry_keeps_captured_source_currency_obligation(namespace_workspace):
    resolver, module, _ = _source_dictionary_slot_fixture(
        namespace_workspace, write="import fake as other\nsetattr(other, 'Agent', fake.Agent)",
    )
    snapshot = StaticInputSnapshot(namespace_workspace)
    token = activate_static_input_snapshot(snapshot)
    try:
        calls = BuilderCalls(resolver)
        statement = module.tree.body[-1]
        home, function, _ = calls._source_slot_candidate(module, statement)
        assert resolver.entry(home.path, ast.parse(home.text), home.text) is home
        assert calls.idempotent_source_slot(module, statement, 'agents')
        assert home.tree.body[0] is function
        assert home.path in snapshot.dependency_paths()
        home.path.write_text(home.text.replace('return tools', 'return None '))
        with pytest.raises((ValueError, OSError)):
            snapshot.finish()
    finally:
        reset_static_input_snapshot(token)


@pytest.mark.parametrize("imported", [
    "from producer import made", "from producer import made as retained",
    "import producer", "from producer import *",
])
def test_constructor_export_refusal_retains_actual_import_location(tmp_path, imported):
    source = "made = object()\n"
    (tmp_path / "producer.py").write_text(source)
    (tmp_path / "consumer.py").write_text("# actual importer\n" + imported + "\n")
    resolver = ImportResolver(tmp_path)
    module = resolver.entry(tmp_path / "producer.py", ast.parse(source), source)
    call = module.tree.body[0].value
    proof = BuilderCalls(resolver)
    assert proof.exported_elsewhere(module, call)
    reason = proof.export_limit(call)
    assert reason is not None and "consumer.py:2" in reason and "producer.py:1" in reason
    assert proof.exported_elsewhere(module, call)  # Existing Boolean cache retains its route.
    assert proof.export_limit(call) == reason


def test_an_unrelated_same_named_import_cannot_create_an_export_route(tmp_path):
    source = "made = object()\n"
    (tmp_path / "producer.py").write_text(source)
    (tmp_path / "another.py").write_text(source)
    (tmp_path / "consumer.py").write_text("# producer candidate\nfrom another import made\n")
    resolver = ImportResolver(tmp_path)
    module = resolver.entry(tmp_path / "producer.py", ast.parse(source), source)
    call = module.tree.body[0].value
    proof = BuilderCalls(resolver)
    assert not proof.exported_elsewhere(module, call)
    assert proof.export_limit(call) is None


def test_constructor_scope_cache_preserves_actual_tree_roles_without_verdicts(tmp_path):
    source = "from agents import Agent\nagent = Agent(name='app', tools=[])\n"
    path = tmp_path / "agent.py"
    path.write_text(source)
    resolver = ImportResolver(tmp_path)
    module = resolver.entry(path, ast.parse(source), source)
    first = resolver._constructor_scope(module)
    assert resolver._constructor_scope(module) is first
    call = module.tree.body[1].value
    assert first.parents[call] is module.tree.body[1]
    # A different actual tree cannot borrow the old tree's roles, even with identical text.
    from agents_shipgate.inputs.python_imports import _module
    other = _module(path, "agent.py", ast.parse(source), source)
    second = resolver._constructor_scope(other)
    assert second is not first and second.parents[other.tree.body[1].value] is other.tree.body[1]
    assert len(resolver._constructor_scopes) == 2


@pytest.mark.parametrize("depth", [40, 80])
def test_constructor_reference_work_does_not_resolve_each_attribute_prefix(tmp_path, monkeypatch, depth):
    from agents_shipgate.inputs.python_imports import _external_constructor_use

    source = "from agents import Agent\nAgent" + ".metadata" * depth + "\n"
    path = tmp_path / "agent.py"
    path.write_text(source)
    resolver = ImportResolver(tmp_path)
    module = resolver.entry(path, ast.parse(source), source)
    original = ImportResolver._constructor_reference
    visits = []

    def count(self, current, node, scopes):
        visits.append(node)
        return original(self, current, node, scopes)

    monkeypatch.setattr(ImportResolver, "_constructor_reference", count)
    assert _external_constructor_use(resolver, module, "agents", {path}, allow_owner_routes=False) is None
    assert len(visits) <= depth + 5
    assert resolver._constructor_scope(module) is resolver._constructor_scopes[module.tree]
