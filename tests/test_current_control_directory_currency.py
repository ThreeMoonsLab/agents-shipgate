"""A recorded file set cannot stand in for a directory input's membership."""

from __future__ import annotations

import json

import pytest
import yaml
from test_current_control import _git, _live, _verify
from test_current_control import repo as repo  # noqa: F401
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.current_control import CurrentControlUnavailable, read_current_control

LOOKUP = '''export class LookupTool extends MongoDBToolBase {
  static toolName = "docs.lookup";
  public description = "Look up existing documentation metadata";
  static operationType: OperationType = "read";
}
'''
ADDED = '''export class DropDatabaseTool extends MongoDBToolBase {
  static toolName = "drop-database";
  public description = "Removes the specified database";
  static operationType: OperationType = "delete";
}
'''


def directory_source(repo):
    path = repo / "shipgate.yaml"
    manifest = yaml.safe_load(path.read_text())
    manifest["tool_sources"][0].update(type="mcp_server_source", path="server")
    path.write_text(yaml.safe_dump(manifest, sort_keys=False))
    source = repo / "server"
    source.mkdir()
    (source / "lookup.ts").write_text(LOOKUP)
    with (repo / ".gitignore").open("a") as handle:
        handle.write("/server/added.ts\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "Synthetic directory source")
    return source


@pytest.mark.parametrize("committed", [False, True])
@pytest.mark.parametrize("observation", [0, 1, 2])
def test_ignored_directory_member_invalidates_both_current_observations(repo, committed, observation):
    source = directory_source(repo)
    _verify(repo, archive_head=committed)
    reports = repo / "agents-shipgate-reports"
    before = read_current_control(reports, live=lambda: _live(repo)).pointer
    assert before.control.permissions.update_pr
    calls = []

    def insert():
        (source / "added.ts").write_text(ADDED)

    if observation == 0:
        insert()

    def live():
        calls.append(1)
        state = _live(repo)
        if len(calls) == observation:
            insert()
        return state

    with pytest.raises(CurrentControlUnavailable):
        read_current_control(reports, live=live)
    assert _live(repo).changed_paths == ()
    result = CliRunner().invoke(app, [
        "agent", "control", "--workspace", str(repo), "--reports-dir", str(reports),
    ], env={"AGENTS_SHIPGATE_AGENT_MODE": "1"})
    assert result.exit_code == 4, result.output
    assert result.stdout == ""
    assert "verify" in json.loads(result.stderr.splitlines()[-1])["next_action"]


@pytest.mark.parametrize('mutation', ['remove', 'rename', 'file_to_directory', 'empty_directory', 'nested_file'])
def test_directory_membership_changes_are_bound_even_without_a_new_blob(repo, mutation):
    source = directory_source(repo)
    empty = source / 'empty'
    empty.mkdir()
    ignored = source / 'unread.txt'
    ignored.write_text('unparsed')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-m', 'Synthetic non-tool member')
    _verify(repo, archive_head=False)
    reports = repo / 'agents-shipgate-reports'
    read_current_control(reports, live=lambda: _live(repo))
    if mutation == 'remove':
        ignored.unlink()
    elif mutation == 'rename':
        ignored.rename(source / 'renamed.txt')
    elif mutation == 'file_to_directory':
        ignored.unlink()
        ignored.mkdir()
    elif mutation == 'empty_directory':
        (source / 'another').mkdir()
    else:
        (empty / 'added.ts').write_text(ADDED)
    from test_current_control_input_origins import plan_at

    from agents_shipgate.core.verification_input_currency import validate_current_plan_inputs
    # Bypass Git drift detection to isolate the input membership obligation.
    with pytest.raises(ValueError, match='directory membership'):
        validate_current_plan_inputs(plan_at(reports), root=repo, artifacts_root=reports)


@pytest.mark.parametrize('committed', [False, True])
@pytest.mark.parametrize('nested', [False, True])
def test_root_source_has_current_authority_with_generated_output_scaffolding(repo, committed, nested):
    directory_source(repo)
    path = repo / 'shipgate.yaml'
    manifest = yaml.safe_load(path.read_text())
    manifest['tool_sources'][0]['path'] = '.'
    path.write_text(yaml.safe_dump(manifest, sort_keys=False))
    with (repo / '.gitignore').open('a') as handle:
        handle.write('/build/\n')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-m', 'Synthetic root source')
    reports = repo / ('build/artifacts/reports' if nested else 'agents-shipgate-reports')
    _verify(repo, archive_head=committed, out=reports)
    before = read_current_control(reports, live=lambda: _live(repo)).pointer
    assert before.control.permissions.update_pr
    if nested:
        (repo / 'build' / 'added.ts').write_text(ADDED)
        with pytest.raises(CurrentControlUnavailable):
            read_current_control(reports, live=lambda: _live(repo))


@pytest.mark.parametrize('committed', [False, True])
def test_prepare_and_worker_bind_the_directory_census(repo, committed):
    from test_current_control_input_origins import plan_at

    from agents_shipgate.core.verification_identity import validate_plan_inputs

    source = directory_source(repo)
    reports = repo / 'agents-shipgate-reports'
    result = CliRunner().invoke(app, [
        'verification', 'prepare', '--workspace', str(repo), '--no-plugins',
        '--out', str(reports / 'verification-plan.json'),
        *(['--base', 'HEAD', '--head', 'HEAD'] if committed else []),
    ])
    assert result.exit_code == 0, result.output
    plan = plan_at(reports)
    kwargs = dict(root=repo, bundle_root=reports, diff_path=reports / 'verification-input.diff')
    validate_plan_inputs(plan, **kwargs)
    (source / 'added.ts').write_text(ADDED)
    with pytest.raises(ValueError, match='directory membership'):
        validate_plan_inputs(plan, **kwargs)
    result = CliRunner().invoke(app, [
        'verification', 'worker', '--plan', str(reports / 'verification-plan.json'),
        '--workspace', str(repo), '--diff', str(reports / 'verification-input.diff'),
        '--out', str(reports / 'replayed-unit.json'),
    ])
    assert result.exit_code != 0
    assert not (reports / 'replayed-unit.json').exists()
    assert 'directory membership' in str(result.exception)


def test_file_source_ignores_an_unrelated_ignored_sibling(repo):
    with (repo / '.gitignore').open('a') as handle:
        handle.write('/unrelated.txt\n')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-m', 'Synthetic ignored sibling')
    _verify(repo, archive_head=True)
    reports = repo / 'agents-shipgate-reports'
    first = read_current_control(reports, live=lambda: _live(repo)).pointer
    (repo / 'unrelated.txt').write_text('not a selected input')
    second = read_current_control(reports, live=lambda: _live(repo)).pointer
    assert first.current_control_id == second.current_control_id


def test_legacy_census_is_unknown_instead_of_a_complete_empty_list(repo):
    from test_current_control_input_origins import plan_at

    from agents_shipgate.core.verification_input_currency import validate_current_plan_inputs

    _verify(repo, archive_head=False)
    reports = repo / 'agents-shipgate-reports'
    plan = plan_at(reports)
    assert plan.inputs.options['input_directories']['directories'] == []
    del plan.inputs.options['input_directories']
    with pytest.raises(ValueError, match='capture is unavailable; re-run'):
        validate_current_plan_inputs(plan, root=repo, artifacts_root=reports)


def test_worker_accepts_relative_workspace_and_bundle_paths(repo, monkeypatch):
    directory_source(repo)
    _verify(repo, archive_head=True)
    monkeypatch.chdir(repo.parent)
    reports = 'repo/agents-shipgate-reports'
    result = CliRunner().invoke(app, [
        'verification', 'worker', '--plan', f'{reports}/verification-plan.json',
        '--workspace', 'repo', '--diff', f'{reports}/verification-input.diff',
        '--out', f'{reports}/relative-unit.json',
    ])
    assert result.exit_code == 0, str(result.exception)
    assert (repo / 'agents-shipgate-reports/relative-unit.json').is_file()


@pytest.mark.parametrize('mutation', ['version', 'source', 'absolute', 'traversal', 'duplicate', 'kind', 'name', 'unconfirmable', 'missing_exclusion'])
def test_malformed_census_refuses_before_filesystem_access(repo, monkeypatch, mutation):
    from test_current_control_input_origins import plan_at

    from agents_shipgate.core import verification_input_currency as currency

    directory_source(repo)
    _verify(repo, archive_head=False)
    reports = repo / 'agents-shipgate-reports'
    plan = plan_at(reports)
    raw = plan.inputs.options['input_directories']
    if mutation == 'version':
        raw['version'] = True
    elif mutation == 'source':
        raw['source'] = 'git_blob'
    elif mutation == 'absolute':
        raw['directories'][0]['path'] = str(repo / 'server')
    elif mutation == 'traversal':
        raw['excluded_paths'] = ['../server']
    elif mutation == 'duplicate':
        raw['directories'].append(raw['directories'][0])
    elif mutation == 'kind':
        raw['directories'][0]['members'][0]['kind'] = 'trusted'
    elif mutation == 'name':
        raw['directories'][0]['members'][0]['name'] = 'another/path.ts'
    elif mutation == 'unconfirmable':
        raw['unconfirmable_paths'] = ['server']
    else:
        raw['excluded_paths'] = []

    def forbidden(*args, **kwargs):
        pytest.fail('malformed metadata must refuse before input filesystem I/O')

    monkeypatch.setattr(currency, 'StaticInputSnapshot', forbidden)
    with pytest.raises(ValueError):
        currency.validate_current_plan_inputs(plan, root=repo, artifacts_root=reports)


def test_empty_new_directory_moves_input_identity_without_changing_file_blobs(repo):
    from test_current_control_input_origins import plan_at

    source = directory_source(repo)
    reports = repo / 'agents-shipgate-reports'
    _verify(repo, archive_head=False)
    before = plan_at(reports)
    (source / 'empty-new-input-directory').mkdir()
    assert _live(repo).changed_paths == ()
    _verify(repo, archive_head=False)
    after = plan_at(reports)
    assert before.inputs.tool_sources == after.inputs.tool_sources
    assert before.inputs.changed_files == after.inputs.changed_files
    assert before.inputs.input_set_id != after.inputs.input_set_id


def test_assembled_pointer_rechecks_directory_membership(repo):
    from agents_shipgate.cli.verification import assemble, worker

    source = directory_source(repo)
    _verify(repo, archive_head=True)
    reports = repo / 'agents-shipgate-reports'
    unit = reports / 'replayed-unit.json'
    worker(plan_path=reports / 'verification-plan.json', workspace=repo,
           diff_path=reports / 'verification-input.diff', out=unit)
    assemble(plan_path=reports / 'verification-plan.json', unit_paths=[unit],
             verifier_path=reports / 'verifier.json', artifacts_root=reports,
             out=reports / 'verification-receipt.json')
    read_current_control(reports, live=lambda: _live(repo))
    (source / 'added.ts').write_text(ADDED)
    with pytest.raises(CurrentControlUnavailable):
        read_current_control(reports, live=lambda: _live(repo))


@pytest.mark.parametrize('committed', [False, True])
def test_prepare_root_source_maps_nested_output_exclusions(repo, committed):
    from test_current_control_input_origins import plan_at

    from agents_shipgate.core.verification_identity import validate_plan_inputs

    directory_source(repo)
    path = repo / 'shipgate.yaml'
    manifest = yaml.safe_load(path.read_text())
    manifest['tool_sources'][0]['path'] = '.'
    path.write_text(yaml.safe_dump(manifest, sort_keys=False))
    with (repo / '.gitignore').open('a') as handle:
        handle.write('/build/\n')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-m', 'Synthetic root preparation')
    reports = repo / 'build/artifacts/reports'
    result = CliRunner().invoke(app, [
        'verification', 'prepare', '--workspace', str(repo), '--no-plugins',
        '--out', str(reports / 'verification-plan.json'),
        *(['--base', 'HEAD', '--head', 'HEAD'] if committed else []),
    ])
    assert result.exit_code == 0, str(result.exception)
    plan = plan_at(reports)
    validate_plan_inputs(plan, root=repo, bundle_root=reports,
                         diff_path=reports / 'verification-input.diff')
    (repo / 'build' / 'unrelated-empty-directory').mkdir()
    with pytest.raises(ValueError, match='directory membership'):
        validate_plan_inputs(plan, root=repo, bundle_root=reports,
                             diff_path=reports / 'verification-input.diff')
