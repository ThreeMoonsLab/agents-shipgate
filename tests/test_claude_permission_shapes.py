"""Known permission shape failures remain visible without imposing a vendor schema (#768)."""

import json
from pathlib import Path

import pytest

from agents_shipgate.cli.host_audit import host_audit_inventory
from agents_shipgate.core.host_grants import inventory_is_complete


@pytest.mark.parametrize('document', [
    None, [], {'permissions': None}, {'permissions': []},
    *({'permissions': {key: value}} for key in ('allow', 'deny', 'ask')
      for value in ('Bash(synthetic-private-token)', None, {}, ['Read(*)', 3], [' '])),
])
def test_invalid_known_permission_shapes_are_incomplete(tmp_path: Path, document):
    path = tmp_path / '.claude/settings.json'
    path.parent.mkdir()
    path.write_text(json.dumps(document))
    inventory = host_audit_inventory(tmp_path)
    assert not inventory_is_complete(inventory)
    issues = [i for i in inventory['issues'] if i['source'] == '.claude/settings.json']
    assert issues and all(i['blocking'] and i['kind'] == 'unsupported' for i in issues)
    assert 'synthetic-private-token' not in json.dumps(inventory)
    assert not inventory['grants']
    assert inventory['artifacts'][0]['parse_status'] == 'unsupported'


@pytest.mark.parametrize('document', [
    {}, {'permissions': {}}, {'permissions': {'allow': [], 'deny': [], 'ask': []}},
    {'permissions': {'allow': ['Read(*)'], 'futureExtension': {'anything': True}}, 'futureSetting': 7},
])
def test_valid_and_unknown_extension_shapes_remain_supported(tmp_path: Path, document):
    path = tmp_path / '.claude/settings.json'
    path.parent.mkdir()
    path.write_text(json.dumps(document))
    assert inventory_is_complete(host_audit_inventory(tmp_path))
