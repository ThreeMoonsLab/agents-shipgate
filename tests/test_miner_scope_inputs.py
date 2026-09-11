"""Original Git scope evidence must survive unsupported miner comparisons."""
from __future__ import annotations

import csv
import json
import subprocess
from pathlib import Path

import pytest
from test_miner import _commit_all, _init_repo

from benchmark.miner import evaluate
from benchmark.miner.rows import MinedRow, read_jsonl, write_csv, write_jsonl


def _scope_case(
    tmp_path: Path, kind: str, *, scope: str = 'agents/new scope',
) -> tuple[Path, MinedRow, str]:
    repo = _init_repo(tmp_path)
    (repo / 'README.md').write_text('Root remains outside the selected project.\n')
    if kind in {'rename', 'partial_rename', 'ambiguous_rename'}:
        for directory, name in [('old scope', 'agent.py'), ('other scope', 'tool.py')]:
            path = repo / 'agents' / directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f'# unique {name}\n' * 30)
    elif kind == 'unrelated_removed':
        old = repo / 'unrelated'
        old.mkdir()
        (old / 'different.py').write_text('# unrelated retired project\n')
    base = _commit_all(repo, 'base')
    target = repo / scope
    target.mkdir(parents=True)
    if kind in {'rename', 'partial_rename', 'ambiguous_rename'}:
        (repo / 'agents/old scope/agent.py').rename(target / 'agent.py')
        if kind == 'ambiguous_rename':
            (repo / 'agents/other scope/tool.py').rename(target / 'tool.py')
        elif kind == 'partial_rename':
            (target / 'new.py').write_text('# newly introduced entrypoint\n')
    else:
        (target / 'agent.py').write_text('# new project implementation\n')
    if kind == 'unrelated_removed':
        (repo / 'unrelated/different.py').unlink()
    head = _commit_all(repo, 'head')
    row = MinedRow(
        repo='local/scope', pr_number=1, pr_url='local://1', title=kind,
        merged_at='', base_sha=base, head_sha=head,
    )
    return repo, row, scope


@pytest.mark.parametrize('kind', ['new', 'rename', 'partial_rename', 'ambiguous_rename', 'unrelated_removed'])
def test_missing_scoped_base_keeps_original_git_input_obligation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    repo, row, scope = _scope_case(tmp_path, kind)
    before = subprocess.check_output(['git', '-C', str(repo), 'status', '--porcelain'])
    monkeypatch.setattr(evaluate, '_commit_manifest', lambda *a: pytest.fail('unsupported input committed'))
    evaluate._verify_pr(
        row, repo, tmp_path / 'base', repo, repo / scope / 'shipgate.yaml', tmp_path,
        subdir=scope, manifest_injected=True,
    )
    obligation = row.verify_input_obligation
    assert obligation is not None
    assert obligation['kind'] == 'unsupported_input'
    assert obligation['base_sha'] == row.base_sha
    assert obligation['head_sha'] == row.head_sha
    assert obligation['head_scope'] == scope
    assert obligation['next_action'] == 'provide_supported_scoped_base_comparison'
    if 'rename' in kind:
        assert obligation['reason'] == 'scoped_base_rename_candidates'
        pairs = obligation['observed_renames']
        assert len(pairs) == (2 if kind == 'ambiguous_rename' else 1)
        assert pairs[0]['base_path'] == 'agents/old scope/agent.py'
        assert pairs[0]['head_path'] == scope + '/agent.py'
        assert pairs[0]['git_status'] == 'R100'
    else:
        assert obligation['reason'] == 'scoped_base_absent'
        assert obligation['observed_renames'] == []
    assert row.verify_verdict == ''
    assert row.verify_can_merge is None
    assert row.verify_cap_added is None
    assert not (repo / scope / 'shipgate.yaml').exists()
    assert subprocess.check_output(['git', '-C', str(repo), 'status', '--porcelain']) == before


def test_missing_base_scope_prevents_a_capability_export_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, row, scope = _scope_case(tmp_path, 'new')
    monkeypatch.setattr(evaluate, '_capability_export', lambda *a: pytest.fail('unbound export'))
    evaluate._capability_delta(
        row, repo, tmp_path / 'base', repo, repo / scope / 'shipgate.yaml', tmp_path,
        subdir=scope,
    )
    assert row.verify_input_obligation['reason'] == 'scoped_base_absent'
    assert row.cap_added is None
    assert row.cap_removed is None


def test_unreadable_base_is_not_absence(tmp_path: Path) -> None:
    repo, row, scope = _scope_case(tmp_path, 'new')
    row.base_sha = 'f' * 40
    obligation = evaluate._scoped_base_obligation(row, repo, scope)
    assert obligation['reason'] == 'scoped_comparison_unreadable'
    assert obligation['next_action'] == 'restore_readable_comparison_trees'


def test_existing_scope_is_read_from_git_not_injected_files(tmp_path: Path) -> None:
    repo, row, scope = _scope_case(tmp_path, 'new')
    row.base_sha = row.head_sha
    # The scope exists in Git even if its local materialization disappears.
    (repo / scope / 'agent.py').unlink()
    (repo / scope).rmdir()
    assert evaluate._scoped_base_obligation(row, repo, scope) is None


@pytest.mark.parametrize('output', ['R100\0old\0', 'A\0unterminated'])
def test_incomplete_rename_observation_does_not_choose_a_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: str,
) -> None:
    repo, row, scope = _scope_case(tmp_path, 'new')
    original = evaluate._git

    def truncated(repo: Path, args: list[str], **kwargs: object):
        if args[0] == 'diff':
            return subprocess.CompletedProcess(args, 0, output, '')
        return original(repo, args, **kwargs)

    monkeypatch.setattr(evaluate, '_git', truncated)
    obligation = evaluate._scoped_base_obligation(row, repo, scope)
    assert obligation['reason'] == 'scoped_comparison_unreadable'
    assert obligation['observed_renames'] == []


def test_scope_obligation_round_trips_and_legacy_rows_keep_no_invented_obligation(tmp_path: Path) -> None:
    repo, row, scope = _scope_case(tmp_path, 'rename')
    row.verify_input_obligation = evaluate._scoped_base_obligation(row, repo, scope)
    out = tmp_path / 'rows.jsonl'
    write_jsonl([row], out)
    assert read_jsonl(out)[0] == row
    write_csv([row], tmp_path / 'rows.csv')
    with (tmp_path / 'rows.csv').open() as handle:
        recorded = next(csv.DictReader(handle))
    assert json.loads(recorded['verify_input_obligation']) == row.verify_input_obligation
    legacy = row.to_json()
    legacy.pop('verify_input_obligation')
    legacy['schema_version'] = '0.2'
    out.write_text(json.dumps(legacy) + '\n')
    assert read_jsonl(out)[0].verify_input_obligation is None
    assert read_jsonl(out)[0].schema_version == '0.2'


def test_real_pr_added_scoped_manifest_retains_terminal_trust_root_review(tmp_path: Path) -> None:
    repo, row, scope = _scope_case(tmp_path, 'new', scope='agents/new-scope')
    (repo / scope / 'mcp-tools.json').write_text('{"tools": []}\n')
    manifest = repo / scope / 'shipgate.yaml'
    manifest.write_text(
        'version: "0.1"\n'
        'project: {name: new-project}\n'
        'agent: {name: new-agent, declared_purpose: [serve]}\n'
        'environment: {target: production_like}\n'
        'tool_sources: [{id: mcp, type: mcp, path: mcp-tools.json}]\n'
    )
    row.head_sha = _commit_all(repo, 'real PR adds scoped manifest')
    base_wt = tmp_path / 'base'
    # Capability comparison still has no evaluated base. The real manifest's
    # terminal verifier route must remain available despite that limitation.
    evaluate._capability_delta(row, repo, base_wt, repo, manifest, tmp_path, subdir=scope)
    assert row.verify_input_obligation is not None
    try:
        evaluate._verify_pr(
            row, repo, base_wt, repo, manifest, tmp_path,
            subdir=scope, manifest_injected=False,
        )
        assert row.verify_verdict
        assert row.verify_trust_root_touched is True
        assert row.verify_can_merge is False
        assert row.verify_input_obligation is None
        assert row.cap_added is None
        result = json.loads((tmp_path / 'verify-reports/verifier.json').read_text())
        assert result['base_status'] == 'missing_manifest'
    finally:
        evaluate._worktree_remove(repo, base_wt)


def test_a_file_at_the_old_scope_is_not_an_empty_directory(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / 'agent').write_text('previously a regular file')
    base = _commit_all(repo, 'base file')
    (repo / 'agent').unlink()
    (repo / 'agent').mkdir()
    (repo / 'agent/new.py').write_text('# new directory')
    head = _commit_all(repo, 'head directory')
    row = MinedRow(
        repo='local/scope', pr_number=1, pr_url='local://1', title='file to directory',
        merged_at='', base_sha=base, head_sha=head,
    )
    assert evaluate._scoped_base_obligation(row, repo, 'agent')['reason'] == 'scoped_base_not_directory'
