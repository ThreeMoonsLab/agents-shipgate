"""Paired caller/factory acceptance prepared for the next #874 increment."""
import pytest

from tests.test_builder_bindings import _files, _read
from tests.test_imported_tool_bindings import _write


@pytest.mark.parametrize('framework', ['sdk', 'adk'])
@pytest.mark.parametrize('returned,selected', [
    ('[read, write]', 'make_tools(runtime_db())'),
    ('(read, write)', 'make_tools(runtime_db())'),
    ("{'calendar': [read], 'email': [write]}", "groups['calendar'] + groups['email']"),
    ("{'calendar': [read], 'email': [write]}", "groups.get('calendar') + groups.get('email')"),
])
def test_returned_lists_and_literal_dictionary_projection(tmp_path, framework, returned, selected):
    files = _files(framework, 'groups = make_tools(runtime_db())\na = build(' + selected + ')\n')
    files['app.py'] = files['app.py'].replace('from tools import read, write', 'from tools import read, write\nfrom factory import make_tools')
    files['factory.py'] = 'from tools import read, write\ndef make_tools(db):\n    return ' + returned + '\n'
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert warnings == []
    (observed,) = observations
    assert observed.tools_complete and observed.tool_names == ['read', 'write']
    assert any('app.py:' in site for sites in observed.tool_sites.values() for site in sites)


@pytest.mark.parametrize('framework', ['sdk', 'adk'])
@pytest.mark.parametrize('mutation', [
    "groups['calendar'].append(write)",
    "groups.get('calendar').append(write)",
    "alias = groups['calendar']\nalias.clear()",
    "consume(groups)",
    "consume(groups['calendar'])",
    "groups['calendar'] = [write]",
])
def test_factory_projection_retains_mutation_and_escape_limits(tmp_path, framework, mutation):
    files = _files(framework, "groups = make_tools(runtime_db())\na = build(groups['calendar'])\n" + mutation + '\n')
    files['app.py'] = files['app.py'].replace('from tools import read, write', 'from tools import read, write\nfrom factory import make_tools')
    files['factory.py'] = "from tools import read\ndef make_tools(db):\n    return {'calendar': [read]}\n"
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert warnings and observations
    assert all(not item.tools_complete for item in observations)


@pytest.mark.parametrize('framework', ['sdk', 'adk'])
@pytest.mark.parametrize('expression', ["groups['missing']", "groups[runtime_key()]", "groups.get(runtime_key())"])
def test_missing_or_dynamic_projection_is_not_a_complete_empty_list(tmp_path, framework, expression):
    files = _files(framework, "groups = make_tools(runtime_db())\na = build(" + expression + ')\n')
    files['app.py'] = files['app.py'].replace('from tools import read, write', 'from tools import read, write\nfrom factory import make_tools')
    files['factory.py'] = "from tools import read\ndef make_tools(db):\n    return {'calendar': [read]}\n"
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert warnings and observations
    assert all(not item.tools_complete for item in observations)


@pytest.mark.parametrize('framework', ['sdk', 'adk'])
@pytest.mark.parametrize('escape', [
    'make_tools.__code__ = replacement.__code__',
    'consume(make_tools)',
    'callback = make_tools\nconsume(callback)',
])
def test_changed_or_escaped_factory_callable_is_not_established(tmp_path, framework, escape):
    files = _files(framework, "a = build(make_tools())\n")
    files['app.py'] = files['app.py'].replace('from tools import read, write', 'from tools import read, write\nfrom factory import make_tools')
    files['factory.py'] = "from tools import read, write\ndef make_tools():\n    return [read]\ndef replacement():\n    return [write]\n" + escape + '\n'
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize('framework', ['sdk', 'adk'])
@pytest.mark.parametrize('other', [
    'consume(make_tools())',
    'other = make_tools()\nconsume(other)',
    "other = make_tools()\nother[0].__name__ = 'renamed'",
])
def test_every_factory_result_retains_shared_callable_ownership(tmp_path, framework, other):
    files = _files(framework, other + '\na = build(make_tools())\n')
    files['app.py'] = files['app.py'].replace('from tools import read, write', 'from tools import read, write\nfrom factory import make_tools')
    files['factory.py'] = 'from tools import read\ndef make_tools():\n    return [read]\n'
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize('framework', ['sdk', 'adk'])
def test_unrelated_clean_factory_calls_keep_their_own_sites(tmp_path, framework):
    files = _files(framework, 'a = build(make_tools())\nb = build(make_tools())\n')
    files['app.py'] = files['app.py'].replace('from tools import read, write', 'from tools import read, write\nfrom factory import make_tools')
    files['factory.py'] = 'from tools import read\ndef make_tools():\n    return [read]\n'
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert warnings == []
    (observation,) = observations
    assert observation.tools_complete and observation.tool_names == ['read']
    assert {'app.py:5', 'app.py:6'} <= set(observation.tool_sites['read'])


@pytest.mark.parametrize('framework', ['sdk', 'adk'])
@pytest.mark.parametrize('body', [
    "    read.__name__ = 'renamed'\n    return {'calendar': [read]}",
    "    consume(read)\n    return {'calendar': [read]}",
    "    return {'calendar': [read], 'other': consume(read)}",
])
def test_factory_cannot_change_or_escape_a_projected_callable(tmp_path, framework, body):
    files = _files(framework, "groups = make_tools()\na = build(groups['calendar'])\n")
    files['app.py'] = files['app.py'].replace('from tools import read, write', 'from tools import read, write\nfrom factory import make_tools')
    files['factory.py'] = 'from tools import read\ndef make_tools():\n' + body + '\n'
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize('framework', ['sdk', 'adk'])
@pytest.mark.parametrize('mutation', [
    "for bucket in groups.values():\n    bucket.clear()",
    "for key, bucket in groups.items():\n    bucket.clear()",
    "groups.copy()['calendar'].clear()",
    "alias = groups.copy()\nalias['calendar'].clear()",
    "alias = groups | {}\nalias['calendar'].clear()",
    "consume(groups.values())",
    "consume(groups.items())",
])
def test_dictionary_views_and_shallow_copies_retain_mutable_values(tmp_path, framework, mutation):
    files = _files(framework, "groups = make_tools()\na = build(groups['calendar'])\n" + mutation + '\n')
    files['app.py'] = files['app.py'].replace('from tools import read, write', 'from tools import read, write\nfrom factory import make_tools')
    files['factory.py'] = "from tools import read\ndef make_tools():\n    return {'calendar': [read]}\n"
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize('framework', ['sdk', 'adk'])
@pytest.mark.parametrize('mutation', ["GROUPS['calendar'].clear()", "alias = GROUPS['calendar']\nalias.clear()"])
def test_imported_module_dictionary_projection_remains_explicitly_unread(tmp_path, framework, mutation):
    files = _files(framework, "from registry import GROUPS\na = build(GROUPS['calendar'])\n")
    files['registry.py'] = "from tools import read\nGROUPS = {'calendar': [read]}\n" + mutation + '\n'
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize('framework', ['sdk', 'adk'])
def test_equivalent_factory_refactor_does_not_repeat_the_caller_condition(tmp_path, framework):
    files = _files(framework, "if chosen:\n    a = build(make_tools())\n")
    files['app.py'] = files['app.py'].replace('from tools import read, write', 'from tools import read, write\nfrom factory import make_tools')
    files['factory.py'] = 'from tools import read\ndef make_tools():\n    return [read]\n'
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert warnings == []
    (observation,) = observations
    assert observation.tool_conditions['read'] == ["the caller's condition `chosen` holds"]


@pytest.mark.parametrize('framework', ['sdk', 'adk'])
@pytest.mark.parametrize('selected,expected', [
    ("groups.get('calendar', [write])", ['read']),
    ("groups.get('missing', [write])", ['write']),
    ("groups.get('missing')", []),
])
def test_dictionary_get_default_belongs_to_its_caller(tmp_path, framework, selected, expected):
    files = _files(framework, 'groups = make_tools()\na = build(' + selected + ')\n')
    files['app.py'] = files['app.py'].replace('from tools import read, write', 'from tools import read, write\nfrom factory import make_tools')
    files['factory.py'] = "from tools import read\ndef make_tools():\n    return {'calendar': [read]}\n"
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert warnings == []
    if framework == 'adk' and not expected:
        assert observations == []  # Complete empty ADK bindings are omitted by convention.
    else:
        (observation,) = observations
        assert observation.tools_complete and observation.tool_names == expected


@pytest.mark.parametrize('framework', ['sdk', 'adk'])
@pytest.mark.parametrize('call', ["mutate(groups)", "groups.get('calendar', mutate(groups))"])
def test_a_helpers_legacy_read_proof_cannot_hide_projected_dictionary_mutation(tmp_path, framework, call):
    files = _files(framework, "def mutate(groups):\n    groups['calendar'].clear()\ngroups = make_tools()\n" + call + "\na = build(groups['calendar'])\n")
    files['app.py'] = files['app.py'].replace('from tools import read, write', 'from tools import read, write\nfrom factory import make_tools')
    files['factory.py'] = "from tools import read\ndef make_tools():\n    return {'calendar': [read]}\n"
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize('framework', ['sdk', 'adk'])
def test_a_list_helper_cannot_rename_a_retained_callable_through_indexing(tmp_path, framework):
    files = _files(framework, "def mutate(tools):\n    tools[0].__name__ = 'renamed'\ngroup = make_tools()\nmutate(group)\na = build(group)\n")
    files['app.py'] = files['app.py'].replace('from tools import read, write', 'from tools import read, write\nfrom factory import make_tools')
    files['factory.py'] = 'from tools import read\ndef make_tools():\n    return [read]\n'
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize('framework', ['sdk', 'adk'])
def test_invoking_a_returned_callable_cannot_prove_its_identity_unchanged(tmp_path, framework):
    files = _files(framework, 'a = build(make_tools())\n')
    files['app.py'] = files['app.py'].replace('from tools import read, write', 'from tools import read, write\nfrom factory import make_tools')
    files['factory.py'] = 'from tools import read\ndef make_tools():\n    read()\n    return [read]\n'
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize('framework', ['sdk', 'adk'])
@pytest.mark.parametrize('argument', ["groups or {}", "groups if wanted else {}"])
def test_wrappers_preserve_dictionary_retention_uncertainty(tmp_path, framework, argument):
    files = _files(framework, "def mutate(groups):\n    groups['calendar'].clear()\ngroups = make_tools()\nmutate(" + argument + ")\na = build(groups['calendar'])\n")
    files['app.py'] = files['app.py'].replace('from tools import read, write', 'from tools import read, write\nfrom factory import make_tools')
    files['factory.py'] = "from tools import read\ndef make_tools():\n    return {'calendar': [read]}\n"
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize('framework', ['sdk', 'adk'])
@pytest.mark.parametrize('escape', [
    'mutate(group or [])',
    'mutate(group if wanted else [])',
    'mutate(group + [])',
    'mutate(group.copy())',
    'mutate(*group)',
    "alias = group.copy()\nalias[0].__name__ = 'renamed'",
    "alias = [*group]\nalias[0].__name__ = 'renamed'",
    'leave_alone.__code__ = mutate.__code__\nleave_alone(group)',
    "a.tools[0].__name__ = 'renamed'",
])
def test_retained_callable_identity_survives_no_copy_or_helper_escape(tmp_path, framework, escape):
    files = _files(framework, "def mutate(tools):\n    tools[0].__name__ = 'renamed'\ndef leave_alone(tools):\n    pass\ngroup = make_tools()\na = build(group)\n" + escape + '\n')
    files['app.py'] = files['app.py'].replace('from tools import read, write', 'from tools import read, write\nfrom factory import make_tools')
    files['factory.py'] = 'from tools import read\ndef make_tools():\n    return [read]\n'
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize('framework', ['sdk', 'adk'])
def test_present_dictionary_keys_do_not_skip_an_executable_default(tmp_path, framework):
    files = _files(framework, "def rename(tool):\n    tool.__name__ = 'renamed'\n    return []\ngroups = make_tools()\na = build(groups.get('calendar', rename(read)))\n")
    files['app.py'] = files['app.py'].replace('from tools import read, write', 'from tools import read, write\nfrom factory import make_tools')
    files['factory.py'] = "from tools import read\ndef make_tools():\n    return {'calendar': [read]}\n"
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize('framework', ['sdk', 'adk'])
@pytest.mark.parametrize('mutation', [
    "    write.__name__ = 'renamed'\n",
    "    alias.__name__ = 'renamed'\n",
    "    consume(write)\n",
])
def test_a_factory_cannot_change_the_callable_in_a_selected_caller_default(tmp_path, framework, mutation):
    files = _files(framework, "groups = make_tools()\na = build(groups.get('missing', [write]))\n")
    files['app.py'] = files['app.py'].replace('from tools import read, write', 'from tools import read, write\nfrom factory import make_tools')
    files['factory.py'] = "from tools import read, write\nfrom tools import write as alias\ndef make_tools():\n" + mutation + "    return {'calendar': [read]}\n"
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize('framework', ['sdk', 'adk'])
@pytest.mark.parametrize('mutation', [
    "    alias.__name__ = 'renamed'\n",
    "    consume(alias)\n",
    "    alias()\n",
    "    namespace.read.__name__ = 'renamed'\n",
    "    consume(namespace)\n",
])
def test_factory_callable_ownership_uses_import_identity_not_spelling(tmp_path, framework, mutation):
    files = _files(framework, 'a = build(make_tools())\n')
    files['app.py'] = files['app.py'].replace('from tools import read, write', 'from tools import read, write\nfrom factory import make_tools')
    files['factory.py'] = 'from tools import read\nfrom tools import read as alias\nimport tools as namespace\ndef make_tools():\n' + mutation + '    return [read]\n'
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize('framework', ['sdk', 'adk'])
@pytest.mark.parametrize('effect', [
    "rename()\n    return [read]",
    "return {'calendar': [read], 'other': rename()}",
])
def test_executable_factory_preludes_and_unselected_values_remain_unread(tmp_path, framework, effect):
    selected = "groups['calendar']" if 'calendar' in effect else 'groups'
    files = _files(framework, f'groups = make_tools()\na = build({selected})\n')
    files['app.py'] = files['app.py'].replace('from tools import read, write', 'from tools import read, write\nfrom factory import make_tools')
    field = 'name' if framework == 'sdk' else '__name__'
    files['factory.py'] = f"from tools import read\ndef rename():\n    read.{field} = 'renamed'\ndef make_tools():\n    " + effect + '\n'
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize('framework', ['sdk', 'adk'])
@pytest.mark.parametrize('expression', ["getattr(bridge, 'lib').read", "getattr(bridge.lib, 'read')", 'consume(bridge)'])
def test_computed_or_opaque_bridge_namespace_cannot_hide_returned_callable_change(tmp_path, framework, expression):
    files = _files(framework, 'a = build(make_tools())\n')
    files['app.py'] = files['app.py'].replace('from tools import read, write', 'from tools import read, write\nfrom factory import make_tools')
    files['bridge.py'] = 'import tools as lib\n'
    field = 'name' if framework == 'sdk' else '__name__'
    action = expression if expression.startswith('consume') else f"{expression}.{field} = 'renamed'"
    files['factory.py'] = 'from tools import read\nimport bridge\ndef make_tools():\n    ' + action + '\n    return [read]\n'
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize('framework', ['sdk', 'adk'])
@pytest.mark.parametrize('site', ['app.py', 'sibling.py'])
@pytest.mark.parametrize('action', ["alias.name = 'renamed'", 'rename(alias)', 'group.count(rename(alias))', 'group.index(rename(alias))'])
def test_caller_and_sibling_aliases_share_the_returned_callable(tmp_path, framework, site, action):
    files = _files(framework, 'group = make_tools()\na = build(group)\n')
    files['app.py'] = files['app.py'].replace('from tools import read, write', 'from tools import read, write\nfrom factory import make_tools')
    files['factory.py'] = 'from tools import read\ndef make_tools():\n    return [read]\n'
    field = 'name' if framework == 'sdk' else '__name__'
    source = f"from tools import read as alias\ndef rename(tool):\n    tool.{field} = 'renamed'\n" + action.replace('alias.name', f'alias.{field}') + '\n'
    files[site] = files.get(site, '') + source
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize('framework', ['sdk', 'adk'])
@pytest.mark.parametrize('method', ['count', 'index'])
def test_opaque_comparison_can_receive_each_returned_callable(tmp_path, framework, method):
    field = 'name' if framework == 'sdk' else '__name__'
    files = _files(framework, f"class Mutator:\n    def __eq__(self, tool):\n        tool.{field} = 'renamed'\n        return True\ngroup = make_tools()\ngroup.{method}(Mutator())\na = build(group)\n")
    files['app.py'] = files['app.py'].replace('from tools import read, write', 'from tools import read, write\nfrom factory import make_tools')
    files['factory.py'] = 'from tools import read\ndef make_tools():\n    return [read]\n'
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize('framework', ['sdk', 'adk'])
@pytest.mark.parametrize('operation', ['group == Mutator()', 'group in Mutator()', 'Mutator() in group', 'group + Mutator()', 'isinstance(group, Mutator)'])
def test_opaque_protocols_cannot_receive_returned_lists_or_members(tmp_path, framework, operation):
    field = 'name' if framework == 'sdk' else '__name__'
    equality = f"value.{field} = 'renamed'" if operation == 'Mutator() in group' else 'value.clear()'
    source = f"class Meta(type):\n    def __instancecheck__(cls, group):\n        group.clear()\n        return False\nclass Mutator(metaclass=Meta):\n    def __eq__(self, value):\n        {equality}\n        return True\n    def __contains__(self, group):\n        group.clear()\n        return False\n    def __radd__(self, group):\n        group.clear()\n        return []\ngroup = make_tools()\n{operation}\na = build(group)\n"
    files = _files(framework, source)
    files['app.py'] = files['app.py'].replace('from tools import read, write', 'from tools import read, write\nfrom factory import make_tools')
    files['factory.py'] = 'from tools import read\ndef make_tools():\n    return [read]\n'
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize('framework', ['sdk', 'adk'])
@pytest.mark.parametrize('replacement', ['namespace.read = write', 'del namespace.read', 'bridge.lib.read = write'])
def test_replacing_a_callable_before_the_factory_import_is_not_established(tmp_path, framework, replacement):
    files = _files(framework, 'a = build(make_tools())\n')
    files['app.py'] = files['app.py'].replace('from tools import read, write', 'from tools import read, write\nimport tools as namespace\nimport bridge\n' + replacement + '\nfrom factory import make_tools')
    files['bridge.py'] = 'import tools as lib\n'
    files['factory.py'] = 'from tools import read\ndef make_tools():\n    return [read]\n'
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize('framework', ['sdk', 'adk'])
@pytest.mark.parametrize('prelude', ['raise RuntimeError', 'assert False', 'while True:\n        pass', 'db + 1'])
def test_inert_factory_allowlist_does_not_equate_absent_call_with_nonexecution(tmp_path, framework, prelude):
    files = _files(framework, 'a = build(make_tools(runtime_db()))\n')
    files['app.py'] = files['app.py'].replace('from tools import read, write', 'from tools import read, write\nfrom factory import make_tools')
    files['factory.py'] = 'from tools import read\ndef make_tools(db):\n    ' + prelude + '\n    return [read]\n'
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize('framework', ['sdk', 'adk'])
def test_conditional_rebinding_cannot_hide_an_imported_callable_alias(tmp_path, framework):
    files = _files(framework, 'a = build(make_tools())\n')
    files['app.py'] = files['app.py'].replace('from tools import read, write', 'from tools import read, write\nfrom factory import make_tools')
    field = 'name' if framework == 'sdk' else '__name__'
    files['factory.py'] = 'from tools import read\ndef make_tools():\n    return [read]\n'
    files['sibling.py'] = f"from tools import read as alias\nif chosen:\n    alias.{field} = 'renamed'\nelse:\n    alias = 3\n"
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize('framework', ['sdk', 'adk'])
def test_identity_tests_literal_copies_and_inert_preludes_are_readable(tmp_path, framework):
    files = _files(framework, 'group = make_tools()\ngroup is None\ngroup is not None\nlen(group)\na = build(group + [write])\n')
    files['app.py'] = files['app.py'].replace('from tools import read, write', 'from tools import read, write\nfrom factory import make_tools')
    files['factory.py'] = 'def make_tools():\n    """Build tools."""\n    from tools import read\n    pass\n    return [read]\n'
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert warnings == []
    (observation,) = observations
    assert observation.tools_complete and observation.tool_names == ['read', 'write']


@pytest.mark.parametrize('framework', ['sdk', 'adk'])
@pytest.mark.parametrize('change', ['empty.__code__ = changed.__code__', 'consume(empty)'])
def test_add_shape_proof_requires_the_same_callable_census(tmp_path, framework, change):
    files = _files(framework, "def empty():\n    return []\nclass Mutator:\n    def __radd__(self, group):\n        group.clear()\n        return []\ndef changed():\n    return Mutator()\n" + change + '\ngroup = make_tools()\ngroup + empty()\na = build(group)\n')
    files['app.py'] = files['app.py'].replace('from tools import read, write', 'from tools import read, write\nfrom factory import make_tools')
    files['factory.py'] = 'from tools import read\ndef make_tools():\n    return [read]\n'
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize('framework', ['sdk', 'adk'])
@pytest.mark.parametrize('machine', ['eval', 'exec', 'alias', 'builtins_dict'])
def test_executable_text_in_a_borrower_cannot_establish_callable_ownership(tmp_path, framework, machine):
    files = _files(framework, 'a = build(make_tools())\n')
    files['app.py'] = files['app.py'].replace('from tools import read, write', 'from tools import read, write\nfrom factory import make_tools')
    files['factory.py'] = 'from tools import read\ndef make_tools():\n    return [read]\n'
    field = 'name' if framework == 'sdk' else '__name__'
    statement = f"eval('read').{field} = 'renamed'" if machine == 'eval' else f'exec("read.{field}=\'renamed\'")'
    if machine == 'builtins_dict':
        statement = statement.replace('exec(', "__builtins__['exec'](")
    if machine == 'alias':
        statement = 'from builtins import exec as execute\n' + statement.replace('exec(', 'execute(')
    files['sibling.py'] = 'from tools import read\n' + statement + '\n'
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize('framework', ['sdk', 'adk'])
@pytest.mark.parametrize('other', ['not_defined', 'write'])
def test_every_eager_dictionary_member_needs_an_established_binding(tmp_path, framework, other):
    files = _files(framework, "groups = make_tools()\na = build(groups['calendar'])\n")
    files['app.py'] = files['app.py'].replace('from tools import read, write', 'from tools import read, write\nfrom factory import make_tools')
    prefix = 'from tools import read, write\ndel write\n' if other == 'write' else 'from tools import read\n'
    files['factory.py'] = prefix + "def make_tools():\n    return {'calendar': [read], 'other': [" + other + ']}\n'
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize('framework', ['sdk', 'adk'])
@pytest.mark.parametrize('source', ["others = {'calendar': []}", "def other():\n    return {'calendar': []}\nothers = other()"])
def test_add_shape_cannot_borrow_an_unowned_retained_dictionary_projection(tmp_path, framework, source):
    files = _files(framework, "class Mutator:\n    def __radd__(self, group):\n        group.clear()\n        return []\n" + source + "\nothers['calendar'] = Mutator()\ngroup = make_tools()\ngroup + others['calendar']\na = build(group)\n")
    files['app.py'] = files['app.py'].replace('from tools import read, write', 'from tools import read, write\nfrom factory import make_tools')
    files['factory.py'] = 'from tools import read\ndef make_tools():\n    return [read]\n'
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize('framework', ['sdk', 'adk'])
@pytest.mark.parametrize('machine', ['eval', 'exec'])
@pytest.mark.parametrize('imported', ['from machine import execute', 'import machine as carrier', 'from machine import *'])
def test_reexported_execution_machinery_is_read_through_existing_import_evidence(tmp_path, framework, machine, imported):
    files = _files(framework, 'a = build(make_tools())\n')
    files['app.py'] = files['app.py'].replace('from tools import read, write', 'from tools import read, write\nfrom factory import make_tools')
    files['factory.py'] = 'from tools import read\ndef make_tools():\n    return [read]\n'
    files['machine.py'] = f'from builtins import {machine} as execute\n'
    field = 'name' if framework == 'sdk' else '__name__'
    execute = 'carrier.execute' if 'carrier' in imported else 'execute'
    statement = f"{execute}('read').{field} = 'renamed'" if machine == 'eval' else f'{execute}("read.{field}=\'renamed\'")'
    files['sibling.py'] = 'from tools import read\n' + imported + '\n' + statement + '\n'
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize('framework', ['sdk', 'adk'])
@pytest.mark.parametrize('route', [
    # ``__all__`` lists the alias, and a star import takes exactly that list.
    ({'machine.py': "from builtins import eval as execute\n__all__ = ['execute']\n"}, 'from machine import *'),
    # A third module re-exports the unused-here alias; its importer reaches it.
    ({'machine.py': 'from builtins import eval as execute\n', 'bridge.py': 'from machine import execute\n'},
     'from bridge import execute'),
    ({'machine.py': 'from builtins import eval as execute\n', 'bridge.py': 'import machine\nexecute = machine.execute\n'},
     'from bridge import execute'),
    ({'machine.py': 'from builtins import eval as execute\n', 'bridge.py': 'from machine import *\n'},
     'from bridge import execute'),
])
def test_unused_here_execution_alias_is_not_discharged_while_any_module_imports_it(tmp_path, framework, route):
    extra, imported = route
    files = _files(framework, 'a = build(make_tools())\n')
    files['app.py'] = files['app.py'].replace('from tools import read, write', 'from tools import read, write\nfrom factory import make_tools')
    files['factory.py'] = 'from tools import read\ndef make_tools():\n    return [read]\n'
    files.update(extra)
    field = 'name' if framework == 'sdk' else '__name__'
    files['sibling.py'] = 'from tools import read\n' + imported + f"\nexecute('read').{field} = 'renamed'\n"
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize('framework', ['sdk', 'adk'])
@pytest.mark.parametrize('anchor_module', ['tools', 'registry'])
@pytest.mark.parametrize('expression', ['anchor.__globals__', "object.__getattribute__(anchor, '__globals__')", 'rename(anchor)'])
def test_another_function_can_retain_the_returned_callable_namespace(tmp_path, framework, anchor_module, expression):
    files = _files(framework, 'a = build(make_tools())\n')
    files['app.py'] = files['app.py'].replace('from tools import read, write', 'from tools import read, write\nfrom factory import make_tools')
    files['factory.py'] = 'from tools import read\ndef make_tools():\n    return [read]\n'
    field = 'name' if framework == 'sdk' else '__name__'
    files[anchor_module + '.py'] = files.get(anchor_module + '.py', 'from tools import read\n') + 'def anchor():\n    return None\n'
    files['consumer.py'] = f"def rename(holder):\n    holder.__globals__['r'+'ead'].{field} = 'renamed'\n"
    action = expression if expression.startswith('rename') else f"{expression}['r'+'ead'].{field} = 'renamed'"
    files['sibling.py'] = f'from {anchor_module} import anchor\nfrom consumer import rename\n' + action + '\n'
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize('framework', ['sdk', 'adk'])
def test_function_namespaces_retain_subject_through_more_than_one_module(tmp_path, framework):
    files = _files(framework, 'a = build(make_tools())\n')
    files['app.py'] = files['app.py'].replace('from tools import read, write', 'from tools import read, write\nfrom factory import make_tools')
    files['factory.py'] = 'from tools import read\ndef make_tools():\n    return [read]\n'
    field = 'name' if framework == 'sdk' else '__name__'
    files['registry.py'] = 'from tools import read\ndef anchor():\n    return None\n'
    files['bridge.py'] = 'from registry import anchor\ndef carrier():\n    return None\n'
    files['sibling.py'] = 'from bridge import carrier\nfrom consumer import rename\nrename(carrier)\n'
    files['consumer.py'] = f"def rename(holder):\n    holder.__globals__['anchor'].__globals__['r'+'ead'].{field} = 'renamed'\n"
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize('framework', ['sdk', 'adk'])
@pytest.mark.parametrize('dictionary', [False, True])
def test_application_diff_factory_addition_retains_construction_and_definition(tmp_path, framework, dictionary):
    from tests.test_imported_tool_bindings import _commit, _compare, _git

    selected = "groups['calendar']" if dictionary else 'groups'
    files = _files(framework, f'groups = make_tools()\na = build({selected})\n')
    files['app.py'] = files['app.py'].replace('from tools import read, write', 'from tools import read, write\nfrom factory import make_tools')
    returned = "{'calendar': [read]}" if dictionary else '[read]'
    files['factory.py'] = 'from tools import read, write\ndef make_tools():\n    return ' + returned + '\n'
    _git(tmp_path, 'init', '-q', '-b', 'main')
    base = _commit(tmp_path, files)
    head = _commit(tmp_path, {'factory.py': files['factory.py'].replace('[read]', '[read, write]')})
    result = _compare(tmp_path, base, head, '--scope', '.')
    row = next(row for row in result['rows'] if row['agent'] == 'Built' and row['tool'] == 'write')
    assert row['change'] == 'added'
    assert {'app.py:5', 'app.py:6', 'builders.py:3'} <= set(row['after']['construction_sites'])
    assert row['after']['definition']['source'] == 'tools.py'


@pytest.mark.parametrize('framework', ['sdk', 'adk'])
@pytest.mark.parametrize('dictionary', [False, True])
@pytest.mark.parametrize('retention', ['comparison', 'different_call'])
def test_application_diff_cannot_publish_removal_through_opaque_factory_retention(tmp_path, framework, dictionary, retention):
    from tests.test_imported_tool_bindings import _commit, _compare, _git

    selected = "groups['calendar']" if dictionary else 'groups'
    files = _files(framework, f'groups = make_tools()\na = build({selected})\n')
    files['app.py'] = files['app.py'].replace('from tools import read, write', 'from tools import read, write\nfrom factory import make_tools')
    returned = "{'calendar': [read, write]}" if dictionary else '[read, write]'
    files['factory.py'] = 'from tools import read, write\ndef make_tools():\n    return ' + returned + '\n'
    _git(tmp_path, 'init', '-q', '-b', 'main')
    base = _commit(tmp_path, files)
    field = 'name' if framework == 'sdk' else '__name__'
    opaque = 'consume(make_tools())\n' if retention == 'different_call' else f"class Comparator:\n    def __eq__(self, tool):\n        tool.{field} = 'renamed'\n        return True\n{selected}.count(Comparator())\n"
    head = _commit(tmp_path, {'factory.py': files['factory.py'].replace('[read, write]', '[read]'), 'app.py': files['app.py'] + opaque})
    result = _compare(tmp_path, base, head, '--scope', '.')
    rows = [row for row in result['rows'] if row['agent'] == 'Built' and row['tool'] == 'write']
    assert rows and all(row['change'] == 'not_established' for row in rows)


@pytest.mark.parametrize('framework', ['sdk', 'adk'])
@pytest.mark.parametrize('site', ['tools', 'registry', 'bridge'])
def test_opaque_class_carriers_cannot_escape_through_method_globals(tmp_path, framework, site):
    files = _files(framework, 'a = build(make_tools())\n')
    files['app.py'] = files['app.py'].replace('from tools import read, write', 'from tools import read, write\nfrom factory import make_tools')
    files['factory.py'] = 'from tools import read\ndef make_tools():\n    return [read]\n'
    field = 'name' if framework == 'sdk' else '__name__'
    files['registry.py'] = 'from tools import read\nclass Anchor:\n    def method(self):\n        return None\n'
    files['bridge.py'] = 'from registry import Anchor\n'
    if site == 'tools':
        files['tools.py'] += 'class Anchor:\n    def method(self):\n        return None\n'
    files['consumer.py'] = f"def consume(holder):\n    holder.method.__globals__['r'+'ead'].{field} = 'renamed'\n"
    files['sibling.py'] = f'from {site} import Anchor\nfrom consumer import consume\nconsume(Anchor)\n'
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize('framework', ['sdk', 'adk'])
def test_a_locally_defined_class_is_also_a_namespace_carrier(tmp_path, framework):
    files = _files(framework, 'a = build(make_tools())\n')
    files['app.py'] = files['app.py'].replace('from tools import read, write', 'from tools import read, write\nfrom factory import make_tools')
    files['factory.py'] = 'from tools import read\ndef make_tools():\n    return [read]\n'
    files['tools.py'] += 'class Anchor:\n    def method(self):\n        return None\nconsume(Anchor)\n'
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize('framework', ['sdk', 'adk'])
def test_class_construction_does_not_supply_a_read_only_namespace_proof(tmp_path, framework):
    files = _files(framework, 'a = build(make_tools())\n')
    files['app.py'] = files['app.py'].replace('from tools import read, write', 'from tools import read, write\nfrom factory import make_tools')
    files['factory.py'] = 'from tools import read\ndef make_tools():\n    return [read]\n'
    files['tools.py'] += 'class Anchor:\n    def method(self):\n        return None\n'
    files['sibling.py'] = 'from tools import Anchor\nconsume(Anchor())\n'
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize('framework', ['sdk', 'adk'])
@pytest.mark.parametrize('carrier', ['helper', 'Helper', 'namespace', 'bridge'])
def test_empty_factory_projection_cannot_establish_removal_with_escaped_namespace(tmp_path, framework, carrier):
    from tests.test_imported_tool_bindings import _commit, _compare, _git

    files = _files(framework, "groups = make_tools()\na = build(groups['selected'])\n")
    files['app.py'] = files['app.py'].replace('from tools import read, write', 'from tools import read, write\nfrom factory import make_tools')
    files['factory.py'] = (
        "from tools import read, write\ndef make_tools():\n    return {'selected': [read], 'unused': [write]}\n"
        + "def helper():\n    return None\nclass Helper:\n    def run(self):\n        return None\n"
    )
    files['bridge.py'] = 'from factory import helper\ndef carrier():\n    return None\n'
    _git(tmp_path, 'init', '-q', '-b', 'main')
    base = _commit(tmp_path, files)
    escape = {
        'helper': 'from factory import helper\nconsume(helper)\n',
        'Helper': 'from factory import Helper\nconsume(Helper)\n',
        'namespace': 'import factory\nconsume(factory)\n',
        'bridge': 'from bridge import carrier\nconsume(carrier)\n',
    }[carrier]
    head = _commit(tmp_path, {'factory.py': files['factory.py'].replace("'selected': [read]", "'selected': []"), 'borrower.py': escape})
    result = _compare(tmp_path, base, head, '--scope', '.')
    assert not any(row['change'] == 'removed' for row in result['rows'])
    assert result['head']['limits'] and result['head']['coverage_gaps']


@pytest.mark.parametrize('framework', ['sdk', 'adk'])
def test_clean_empty_factory_projection_still_establishes_removal(tmp_path, framework):
    from tests.test_imported_tool_bindings import _commit, _compare, _git

    files = _files(framework, "groups = make_tools()\na = build(groups['selected'])\n")
    files['app.py'] = files['app.py'].replace('from tools import read, write', 'from tools import read, write\nfrom factory import make_tools')
    files['factory.py'] = "from tools import read, write\ndef make_tools():\n    return {'selected': [read], 'unused': [write]}\n"
    _git(tmp_path, 'init', '-q', '-b', 'main')
    base = _commit(tmp_path, files)
    head = _commit(tmp_path, {'factory.py': files['factory.py'].replace("'selected': [read]", "'selected': []")})
    result = _compare(tmp_path, base, head, '--scope', '.')
    assert [(row['tool'], row['change']) for row in result['rows']] == [('read', 'removed')]
    assert result['comparison_status'] == 'compared'
