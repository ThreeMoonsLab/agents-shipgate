"""The existing cloud reader follows exact Memory Bank SDK calls, not names."""

import json

import pytest
from test_tool_reach import _reach

from tests.test_application_diff import commit, git, run

IMPORT = "from google.cloud.aiplatform_v1beta1 import MemoryBankServiceClient\n"
PARENT = "projects/p/locations/l/reasoningEngines/e"
MEMORY = PARENT + "/memories/m"
METHODS = [
    ("retrieve_memories", "read", "parent"), ("get_memory", "read", "name"),
    ("list_memories", "read", "parent"), ("generate_memories", "write", "parent"),
    ("create_memory", "write", "parent"), ("update_memory", "write", "memory"),
    ("delete_memory", "write", "name"),
]


def argument(word):
    if word == "memory":
        return "{'name': '" + MEMORY + "', 'fact': 'updated'}"
    return repr(MEMORY if word == "name" else PARENT)


@pytest.mark.parametrize("method,operation,word", METHODS)
@pytest.mark.parametrize("request_form", ["direct", "keyword", "positional"])
def test_documented_methods_and_request_resources(tmp_path, method, operation, word, request_form):
    value = argument(word)
    args = f"{word}={value}" if request_form == "direct" else "{'" + word + "': " + value + "}"
    if request_form == "keyword":
        args = "request=" + args
    reach = _reach(tmp_path, IMPORT + f"def act():\n    client = MemoryBankServiceClient()\n    return client.{method}({args})\n")
    [effect] = reach["effects"]
    assert (effect["family"], effect["operation"], effect["service"]) == ("cloud", operation, "memory_bank")
    assert effect["target"] == (MEMORY if word in {"name", "memory"} else PARENT)
    assert not reach["limits"] and reach["effect"] == operation


@pytest.mark.parametrize("imports,constructor", [
    ("from google.cloud import aiplatform_v1beta1 as api\n", "api.MemoryBankServiceClient"),
    ("from google.cloud.aiplatform_v1beta1.services.memory_bank_service import MemoryBankServiceClient as Client\n", "Client"),
])
def test_documented_exports_keep_import_identity(tmp_path, imports, constructor):
    reach = _reach(tmp_path, imports + f"def act():\n    return {constructor}().retrieve_memories(parent='{PARENT}')\n")
    assert reach["effect"] == "read" and reach["effects"][0]["target"] == PARENT


@pytest.mark.parametrize("method", ["purge_memories", "delete_all", "get_iam_policy"])
def test_methods_outside_the_selected_table_stay_unknown(tmp_path, method):
    reach = _reach(tmp_path, IMPORT + f"def act():\n    return MemoryBankServiceClient().{method}(parent='{PARENT}')\n")
    assert reach["effects"][0]["operation"] == "unknown"
    assert reach["limits"] and reach["effect"] is None


@pytest.mark.parametrize("prefix,files", [
    ("from unrelated import MemoryBankServiceClient\n", None),
    ("class MemoryBankServiceClient:\n    def retrieve_memories(self, **kwargs):\n        return []\n", None),
    (IMPORT + "MemoryBankServiceClient = provided_client\n", None),
    (IMPORT + "MemoryBankServiceClient.retrieve_memories = replacement\n", None),
    (IMPORT, {"google/__init__.py": "", "google/cloud/__init__.py": "", "google/cloud/aiplatform_v1beta1.py": "class MemoryBankServiceClient:\n    pass\n"}),
])
def test_name_alikes_and_mutations_are_not_the_sdk(tmp_path, prefix, files):
    reach = _reach(tmp_path, prefix + f"def act():\n    return MemoryBankServiceClient().retrieve_memories(parent='{PARENT}')\n", files=files)
    assert not reach.get("effects") and reach["limits"] and reach["effect"] != "read"


@pytest.mark.parametrize("source", [
    IMPORT + f"client = MemoryBankServiceClient()\ndef act():\n    return client.retrieve_memories(parent='{PARENT}')\n",
    IMPORT + f"def act():\n    client = MemoryBankServiceClient(transport=provided_transport)\n    return client.retrieve_memories(parent='{PARENT}')\n",
])
def test_unseen_client_configuration_does_not_establish_read(tmp_path, source):
    reach = _reach(tmp_path, source)
    assert reach["effects"][0]["operation"] == "read"
    assert reach["limits"] and reach["effect"] is None


@pytest.mark.parametrize("args", [
    "request=provided_request",
    "request={'parent': 'first', **provided_request}",
    "request={'parent': 'first'}, parent='second'",
    "{'parent': 'first'}, request={'parent': 'second'}",
    "{'parent': 'first'}, {'parent': 'second'}",
    "parent='first', parent='second'",
    "request={'parent': 'first'}, request={'parent': 'second'}",
])
def test_unread_or_conflicting_resource_arguments_are_not_named(tmp_path, args):
    reach = _reach(tmp_path, IMPORT + f"def act():\n    return MemoryBankServiceClient().retrieve_memories({args})\n")
    assert "target" not in reach["effects"][0]
    assert reach["limits"] and reach["effect"] is None


def test_literal_dictionary_keys_follow_python_last_value(tmp_path):
    reach = _reach(tmp_path, IMPORT + "def act():\n    return MemoryBankServiceClient().retrieve_memories(request={'parent': 'first', 'parent': 'second'})\n")
    assert reach["effects"][0]["target"] == "second"


@pytest.mark.parametrize("mutation", ["other['parent'] = 'second'", "other.update({'parent': 'second'})"])
def test_mutable_request_alias_does_not_supply_a_stale_target(tmp_path, mutation):
    reach = _reach(tmp_path, IMPORT + f"def act():\n    req = {{'parent': 'first'}}\n    other = req\n    {mutation}\n    return MemoryBankServiceClient().retrieve_memories(request=req)\n")
    assert "target" not in reach["effects"][0]
    assert reach["limits"] and reach["effect"] is None


@pytest.mark.parametrize("request_form", ["memory=mem", "request={'memory': mem}", "request=req"])
def test_nested_memory_alias_does_not_supply_a_stale_target(tmp_path, request_form):
    reach = _reach(tmp_path, IMPORT + f"def act():\n    mem = {{'name': 'first'}}\n    other = mem\n    req = {{'memory': mem}}\n    other['name'] = 'second'\n    return MemoryBankServiceClient().update_memory({request_form})\n")
    assert "target" not in reach["effects"][0] and reach["limits"]


@pytest.mark.parametrize("resource", ["req['parent']", "parent", "f'{parent}'", "provided_parent"])
@pytest.mark.parametrize("request_form", ["direct", "inline"])
def test_derived_resource_values_do_not_hide_mutable_aliases(tmp_path, resource, request_form):
    args = "parent=" + resource if request_form == "direct" else "request={'parent': " + resource + "}"
    reach = _reach(tmp_path, IMPORT + f"def act():\n    req = {{'parent': 'first'}}\n    other = req\n    other['parent'] = 'second'\n    parent = req['parent']\n    return MemoryBankServiceClient().retrieve_memories({args})\n")
    assert "target" not in reach["effects"][0]
    assert reach["limits"] and reach["effect"] is None


def test_credentials_and_secret_shaped_resource_pieces_are_withheld(tmp_path):
    secret = "ghp_0123456789abcdefABCDEF0123456789"
    reach = _reach(tmp_path, "import os\n" + IMPORT + f"def act():\n    client = MemoryBankServiceClient(credentials=os.environ['CLOUD_TOKEN'])\n    return client.generate_memories(parent='projects/{secret}/locations/l/reasoningEngines/e')\n")
    [effect] = reach["effects"]
    assert effect["credential_sources"] == [{"keyword": "credentials", "env": ["CLOUD_TOKEN"]}]
    assert "target" not in effect and effect["value_sha256"]
    assert secret not in json.dumps(reach)


def test_application_rows_carry_selected_memory_effects(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    source = "from agents import Agent, function_tool\n" + IMPORT + f"""
@function_tool
def recall() -> str:
    return MemoryBankServiceClient().retrieve_memories(parent='{PARENT}')
@function_tool
def remember() -> str:
    return MemoryBankServiceClient().generate_memories(parent='{PARENT}')
agent = Agent(name='memory-agent', tools=[])
"""
    base = commit(tmp_path, {"agent.py": source})
    head = commit(tmp_path, {"agent.py": source.replace("tools=[]", "tools=[recall, remember]")})
    result = run(tmp_path, base, head)
    assert result["comparison_status"] == "compared"
    assert {(row["tool"], row["change"]) for row in result["rows"]} == {("recall", "added"), ("remember", "added")}
    assert {(row["tool"], row["after"]["reach"]["effect"]) for row in result["rows"]} == {("recall", "read"), ("remember", "write")}
