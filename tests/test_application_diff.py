"""Application comparisons must work before adoption and retain uncertainty."""
import json
import subprocess

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app

SDK = '''from agents import Agent, function_tool
@function_tool
def lookup(query: str) -> str:
    return query
@function_tool
def execute(code: str) -> str:
    return code
agent = Agent(name="assistant", tools=TOOLS)
'''


def git(root, *args):
    result = subprocess.run(['git', '-c', 'core.excludesFile=/dev/null', *args],
                            cwd=root, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def commit(root, files):
    for name, content in files.items():
        path = root / name
        if content is None:
            path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
    git(root, 'add', '-A')
    git(root, '-c', 'user.name=Test', '-c', 'user.email=test@example.com',
        '-c', 'commit.gpgsign=false', 'commit', '--allow-empty', '-qm', 'fixture')
    return git(root, 'rev-parse', 'HEAD')


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, 'init', '-q', '-b', 'main')
    return tmp_path


def run(repo, base, head, *args):
    result = CliRunner().invoke(app, ['diff', '--application', '--workspace', str(repo),
                                     '--base', base, '--head', head, '--json', *args])
    assert result.exit_code == 0, result.output + repr(result.exception)
    return json.loads(result.output)


def test_fresh_repository_reports_actual_wiring_not_catalog(repo):
    base = commit(repo, {'agent.py': SDK.replace('TOOLS', '[lookup]')})
    head = commit(repo, {'agent.py': SDK.replace('TOOLS', '[lookup, execute]')})
    result = run(repo, base, head)
    assert result['comparison_status'] == 'compared'
    assert [(r['agent'], r['tool'], r['change']) for r in result['rows']] == [('agent', 'execute', 'added')]
    row = result['rows'][0]
    assert row['before'] is None
    assert row['after']['definition']['line'] == 6
    assert row['after']['signature'] == 'execute(code) -> str'
    assert result['input_origin'] == 'independent_tree_discovery'
    assert result['base']['compared_commit'] == base
    assert result['head']['compared_commit'] == head
    assert not (repo / 'shipgate.yaml').exists()
    assert not (repo / '.agents-shipgate-local-review.yaml').exists()
    assert not (repo / 'agents-shipgate-reports').exists()
    assert git(repo, 'status', '--porcelain') == ''
    assert not any(k in result for k in ['control', 'decision', 'release_decision', 'merge_verdict'])
    assert result == run(repo, base, head)


def test_definition_without_wiring_does_not_become_capability(repo):
    base = commit(repo, {'agent.py': SDK.replace('TOOLS', '[lookup]')})
    head = commit(repo, {'agent.py': SDK.replace('TOOLS', '[lookup]').replace('return code', 'return code.upper()')})
    assert run(repo, base, head)['rows'] == []


def test_two_agents_with_no_deployment_root_keep_their_own_edges(repo):
    initial = SDK.replace('TOOLS', '[lookup]') + '\nworker = Agent(name="worker", tools=[])\n'
    base = commit(repo, {'agent.py': initial})
    head = commit(repo, {'agent.py': initial.replace('tools=[]', 'tools=[execute]')})
    rows = run(repo, base, head)['rows']
    assert [(r['agent'], r['tool']) for r in rows] == [('worker', 'execute')]


@pytest.mark.parametrize('change', ['signature', 'body', 'remove'])
def test_bound_tool_changes_and_removals(repo, change):
    source = SDK.replace('TOOLS', '[lookup, execute]')
    base = commit(repo, {'agent.py': source})
    after = {'signature': source.replace('code: str', 'code: int'),
             'body': source.replace('return code', 'return code.upper()'),
             'remove': source.replace('[lookup, execute]', '[lookup]')}[change]
    head = commit(repo, {'agent.py': after})
    rows = run(repo, base, head)['rows']
    assert len(rows) == 1
    assert rows[0]['change'] == ('removed' if change == 'remove' else 'changed')


def test_comments_and_line_movement_are_not_capability_changes(repo):
    source = SDK.replace('TOOLS', '[lookup]')
    base = commit(repo, {'agent.py': source})
    head = commit(repo, {'agent.py': '# Comment\n\n' + source})
    assert run(repo, base, head)['rows'] == []


def test_scope_added_absence_is_tree_bound(repo):
    base = commit(repo, {'README.md': 'repository'})
    head = commit(repo, {'new/agent.py': SDK.replace('TOOLS', '[lookup]')})
    result = run(repo, base, head, '--scope', 'new')
    assert result['base']['status'] == 'absent'
    assert len(result['rows']) == 1


def test_explicit_scope_move_does_not_invent_added_tools(repo):
    source = SDK.replace('TOOLS', '[lookup]')
    base = commit(repo, {'old/agent.py': source})
    head = commit(repo, {'old/agent.py': None, 'new/agent.py': source})
    result = run(repo, base, head, '--scope', 'new', '--base-scope', 'old')
    assert result['rows'] == []
    assert result['base']['scope'] == 'old'
    assert result['head']['scope'] == 'new'


def test_bad_base_cannot_become_empty_base(repo):
    base = commit(repo, {'agent.py': 'from agents import Agent\nAgent(\n'})
    head = commit(repo, {'agent.py': SDK.replace('TOOLS', '[lookup]')})
    result = run(repo, base, head)
    assert result['comparison_status'] == 'partial'
    assert result['rows'] == []
    assert any('parsed' in x for x in result['base']['limits'])


def test_bad_head_cannot_become_removed_tools(repo):
    base = commit(repo, {'agent.py': SDK.replace('TOOLS', '[lookup]')})
    head = commit(repo, {'agent.py': 'from agents import Agent\nAgent(\n'})
    result = run(repo, base, head)
    assert result['comparison_status'] == 'partial'
    assert result['rows'] == []


def test_truncation_cannot_become_no_change(repo):
    base = commit(repo, {'a.py': '# ignored', 'agent.py': SDK.replace('TOOLS', '[lookup]')})
    head = commit(repo, {'agent.py': SDK.replace('TOOLS', '[lookup, execute]')})
    result = run(repo, base, head, '--max-python-files', '1')
    assert result['comparison_status'] == 'partial'
    assert any('truncat' in x or 'census' in x for x in result['base']['limits'])


def test_exact_head_does_not_read_dirty_worktree(repo):
    base = commit(repo, {'agent.py': SDK.replace('TOOLS', '[lookup]')})
    head = commit(repo, {'agent.py': SDK.replace('TOOLS', '[lookup, execute]')})
    (repo / 'agent.py').write_text('raise RuntimeError("never execute")')
    assert len(run(repo, base, head)['rows']) == 1


def test_adk_direct_binding_without_manifest(repo):
    source = '''from google.adk.agents import Agent

def lookup(query: str) -> str:
    return query
root_agent = Agent(name="helper", tools=TOOLS)
'''
    base = commit(repo, {'agent.py': source.replace('TOOLS', '[]')})
    head = commit(repo, {'agent.py': source.replace('TOOLS', '[lookup]')})
    result = run(repo, base, head)
    assert len(result['rows']) == 1
    assert result['rows'][0]['tool'] == 'lookup'


@pytest.mark.parametrize('scope', ['../outside', '/tmp', 'x/../../y'])
def test_scope_escape_refused(repo, scope):
    ref = commit(repo, {'README.md': 'x'})
    result = CliRunner().invoke(app, ['diff', '--application', '--workspace', str(repo),
                                     '--base', ref, '--scope', scope])
    assert result.exit_code == 2


def test_identical_git_rename_preserves_binding_identity(repo):
    source = SDK.replace('TOOLS', '[lookup]')
    base = commit(repo, {'before/agent.py': source})
    head = commit(repo, {'before/agent.py': None, 'after/agent.py': source})
    result = run(repo, base, head)
    assert result['rows'] == []
    assert result['source_correspondence'] == [{'base_source': 'before/agent.py',
        'head_source': 'after/agent.py', 'basis': 'git_rename_identical_blob'}]


def test_non_agent_repository_is_not_a_successful_no_change(repo):
    base = commit(repo, {'README.md': 'Hello'})
    head = commit(repo, {'README.md': 'Hello again'})
    assert run(repo, base, head)['comparison_status'] == 'not_established'


def test_changed_function_default_is_not_silently_unchanged(repo):
    source = SDK.replace('TOOLS', '[lookup]').replace('query: str', 'query: str = "first"')
    base = commit(repo, {'agent.py': source})
    head = commit(repo, {'agent.py': source.replace('"first"', '"second"')})
    assert run(repo, base, head)['rows'][0]['change'] == 'changed'


def test_refs_record_requested_base_separately_from_merge_base(repo):
    ancestor = commit(repo, {'agent.py': SDK.replace('TOOLS', '[lookup]')})
    git(repo, 'checkout', '-qb', 'feature')
    head = commit(repo, {'agent.py': SDK.replace('TOOLS', '[lookup, execute]')})
    git(repo, 'checkout', 'main')
    base_tip = commit(repo, {'README.md': 'base moved'})
    result = run(repo, 'main', head)
    assert result['base']['requested_commit'] == base_tip
    assert result['base']['compared_commit'] == ancestor
    assert result['head']['compared_commit'] == head
    assert result['rows'][0]['tool'] == 'execute'


def test_application_code_is_never_executed(repo):
    marker = repo / 'EXECUTED'
    source = f'from pathlib import Path\nPath({str(marker)!r}).write_text("bad")\n' + SDK
    base = commit(repo, {'agent.py': source.replace('TOOLS', '[]')})
    head = commit(repo, {'agent.py': source.replace('TOOLS', '[lookup]')})
    result = run(repo, base, head)
    assert len(result['rows']) == 1, result
    assert not marker.exists()


def test_gitlink_refusal_is_not_a_successful_comparison(repo):
    base = commit(repo, {'agent.py': SDK.replace('TOOLS', '[]')})
    git(repo, 'update-index', '--add', '--cacheinfo', f'160000,{base},external')
    git(repo, '-c', 'user.name=Test', '-c', 'user.email=test@example.com',
        '-c', 'commit.gpgsign=false', 'commit', '-qm', 'gitlink')
    result = CliRunner().invoke(app, ['diff', '--application', '--workspace', str(repo),
                                     '--base', base, '--head', 'HEAD', '--json'])
    assert result.exit_code == 2
    assert '160000' in result.output


def test_python_size_bound_precedes_discovery(repo, monkeypatch):
    import agents_shipgate.cli.application_diff as module
    ref = commit(repo, {'agent.py': SDK.replace('TOOLS', '[lookup]')})
    monkeypatch.setattr(module, 'MAX_PYTHON_BYTES', 8)
    monkeypatch.setattr(module, 'detect_workspace', lambda *a, **kw: pytest.fail('oversized input parsed'))
    result = run(repo, ref, ref)
    assert result['comparison_status'] == 'partial'
    assert result['rows'] == []


def test_adk_handoff_line_numbers_are_not_binding_identity(repo):
    source = '''from google.adk.agents import Agent
worker = Agent(name="worker", tools=[])
root_agent = Agent(name="root", tools=[], sub_agents=[worker])
'''
    base = commit(repo, {'agent.py': source})
    head = commit(repo, {'agent.py': '# shifted evidence locations\n\n' + source})
    assert run(repo, base, head)['rows'] == []


def test_explicit_empty_tools_is_known_empty_wiring_not_missing_manifest(repo):
    base = commit(repo, {'agent.py': SDK.replace('TOOLS', '[]')})
    head = commit(repo, {'agent.py': SDK.replace('TOOLS', '[lookup]')})
    result = run(repo, base, head)
    assert result['comparison_status'] == 'compared'
    assert result['base']['binding_count'] == 0
    assert result['rows'][0]['tool'] == 'lookup'


def test_unresolved_tools_are_not_an_empty_surface(repo):
    base = commit(repo, {'agent.py': SDK.replace('TOOLS', 'make_tools()')})
    head = commit(repo, {'agent.py': SDK.replace('TOOLS', '[lookup]')})
    result = run(repo, base, head)
    assert result['comparison_status'] == 'partial'
    assert result['rows'] == []


def test_nonidentical_unpaired_move_does_not_invent_new_capabilities(repo):
    source = SDK.replace('TOOLS', '[lookup]')
    base = commit(repo, {'old/agent.py': source})
    head = commit(repo, {'old/agent.py': None, 'new/agent.py': '# changed comment\n' + source})
    result = run(repo, base, head)
    assert result['comparison_status'] == 'partial'
    assert result['rows'] == []
    assert any('unpaired' in x for x in result['head']['limits'])


def test_exact_file_move_preserves_handoff_identity(repo):
    source = '''from google.adk.agents import Agent
worker = Agent(name="worker", tools=[])
root_agent = Agent(name="root", tools=[], sub_agents=[worker])
'''
    base = commit(repo, {'old/agent.py': source})
    head = commit(repo, {'old/agent.py': None, 'new/agent.py': source})
    result = run(repo, base, head)
    assert result['rows'] == []
    assert result['comparison_status'] == 'compared'


@pytest.mark.parametrize('json_output', [False, True])
def test_diagnostic_limits_use_shared_redaction(repo, monkeypatch, json_output):
    import agents_shipgate.cli.application_diff as module
    ref = commit(repo, {'README.md': 'x'})
    secret = 'sk-privacyaaaaaaaaaaaaaaaa'
    monkeypatch.setattr(module, 'observe', lambda *a, **kw:
                        module.Observations('.', status='partial', limits=[f'unresolved {secret}']))
    args = ['diff', '--application', '--workspace', str(repo), '--base', ref]
    if json_output:
        args.append('--json')
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    assert secret not in result.output
    assert '[REDACTED:' in result.output
