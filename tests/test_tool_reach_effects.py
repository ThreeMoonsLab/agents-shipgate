"""#913: what a bound tool reaches beyond HTTP, named by import identity.

Each family has a positive case and a negative one: a name-alike from an
unrelated library or the repository itself, which stays a named limit and is
never read as the library's.
"""

import pytest
from test_tool_reach import _reach, _tool

from agents_shipgate.core.semantic_assessment import (
    REACH_EFFECT_CLAIM_SOURCE,
    assess_tool_semantics,
)
from agents_shipgate.inputs.tool_effects import file_mode, sql_operation
from agents_shipgate.inputs.tool_reach import MAX_CALLS


def _effects(reach):
    return [
        (item["family"], item["operation"], item["call"], item.get("target"))
        for item in reach.get("effects", [])
    ]


def _claims(reach):
    return [claim["effect"] for claim in reach["effect_claims"]]


def _whys(reach):
    return [item["why"] for item in reach["limits"]]


# -- SQL -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("head", "complete", "operation", "keyword", "table"),
    [
        ("SELECT name FROM users WHERE id = ?", True, "read", "SELECT", "users"),
        ("  -- the latest\n select * from public.orders", True, "read", "SELECT", "public.orders"),
        ("/* c */ (SELECT 1)", True, "read", "SELECT", None),
        ("INSERT INTO audit (a) VALUES (?)", True, "write", "INSERT", "audit"),
        ("update users set name = ?", True, "write", "UPDATE", "users"),
        ("DELETE FROM sessions", True, "write", "DELETE", "sessions"),
        ("CREATE TABLE IF NOT EXISTS t (a int)", True, "write", "CREATE", "t"),
        # A read keyword with something that may write after it is not a read.
        ("SELECT * INTO backup FROM users", True, "unknown", "SELECT", "backup"),
        ("WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x", True, "unknown", "WITH", "t"),
        ("SELECT 1; DROP TABLE users", True, "write", "SELECT", "users"),
        ("SELECT 1; SELECT 2", True, "read", "SELECT", None),
        ("BEGIN", True, None, "BEGIN", None),
        ("CALL refresh()", True, "unknown", "CALL", None),
        # The literal ends before the keyword or the table name does.
        ("SEL", False, "unknown", None, None),
        ("SELECT * FROM user", False, "read", "SELECT", None),
        ("SELECT * FROM ", False, "read", "SELECT", None),
        # A leading word that is no SQL keyword is never published.
        ("ghpAbCdEf0123456789", True, "unknown", None, None),
    ],
)
def test_a_sql_literal_says_whether_it_reads(head, complete, operation, keyword, table):
    statement = sql_operation(head, complete=complete)
    assert (statement.operation, statement.keyword, statement.table) == (operation, keyword, table)
    if head.startswith("SELECT 1;"):
        pass
    assert statement.open is (not complete)


@pytest.mark.parametrize(
    ("mode", "operation"), [("r", "read"), ("rb", "read"), ("w", "write"), ("a", "write"), ("x", "write"), ("r+", "write")]
)
def test_a_file_mode_writes_when_it_can(mode, operation):
    assert file_mode(mode) == operation


# -- databases -------------------------------------------------------------------


def test_a_literal_select_on_a_local_connection_reads(tmp_path):
    reach = _reach(
        tmp_path,
        "import sqlite3\n\n"
        "def act(user_id: str) -> list:\n"
        '    with sqlite3.connect("app.db") as conn:\n'
        "        conn.row_factory = sqlite3.Row\n"
        '        rows = conn.execute("SELECT name FROM users WHERE id = ?", (user_id,)).fetchall()\n'
        "    return [dict(row) for row in rows]\n",
    )
    [effect] = reach["effects"]
    assert _effects(reach) == [("database", "read", "connection.execute", "users")]
    assert (effect["library"], effect["service"], effect["statement"]) == ("sqlite3", "sqlite", "SELECT")
    assert effect["model_supplied"] == [{"param": "user_id", "into": "parameters"}]
    # The statement itself is digested, never printed.
    assert "value_sha256" in effect and "name FROM" not in str(reach)
    assert reach["limits"] == []
    assert reach["effect_claims"] == [{"effect": "read", "at": "agent.py:3", "calls": 0, "effects": 1}]


def test_a_write_statement_supports_write(tmp_path):
    reach = _reach(
        tmp_path,
        "import sqlite3\n\n"
        "def act(name: str) -> None:\n"
        '    conn = sqlite3.connect("app.db")\n'
        "    cur = conn.cursor()\n"
        '    cur.execute("SELECT 1 FROM users")\n'
        '    cur.execute("INSERT INTO users (name) VALUES (?)", (name,))\n'
        "    conn.commit()\n",
    )
    assert _effects(reach) == [
        ("database", "read", "cursor.execute", "users"),
        ("database", "write", "cursor.execute", "users"),
    ]
    assert _claims(reach) == ["write"]


def test_a_statement_that_is_not_a_literal_is_a_named_limit(tmp_path):
    reach = _reach(
        tmp_path,
        "import sqlite3\n\n"
        "def act(sql: str) -> list:\n"
        '    return sqlite3.connect("app.db").execute(sql).fetchall()\n',
    )
    assert _effects(reach) == [("database", "unknown", "connection.execute", None)]
    assert reach["effects"][0]["model_supplied"] == [{"param": "sql", "into": "statement"}]
    assert "the SQL statement is not a literal; whether it writes is not read" in _whys(reach)
    assert reach["effect_claims"] == []


def test_a_select_splicing_in_an_unread_value_is_a_read_but_not_a_read_claim(tmp_path):
    reach = _reach(
        tmp_path,
        "import sqlite3\nfrom tables import TABLES\n\n"
        "def act(key: str) -> list:\n"
        '    conn = sqlite3.connect("app.db")\n'
        '    return conn.execute(f"SELECT * FROM {TABLES[key]}").fetchall()\n',
        files={"tables.py": 'TABLES = {"a": "alpha"}\n'},
    )
    assert _effects(reach) == [("database", "read", "connection.execute", None)]
    assert any("splices in a value the read does not name" in why for why in _whys(reach))
    assert reach["effect_claims"] == []


def test_a_lazily_built_module_pool_is_followed_past_the_helper_bound(tmp_path):
    # MIS_TALENT#7's shape: the query runs three helpers from the tool, on a
    # pool a module global holds once a fourth helper built it.
    repository = (
        "import os\n"
        "from psycopg2 import pool\n"
        "from psycopg2.extras import RealDictCursor\n\n"
        "_db_pool = None\n\n"
        "def init_db_pool():\n"
        "    global _db_pool\n"
        "    if _db_pool is not None:\n"
        "        return _db_pool\n"
        "    password = os.getenv('DB_PASSWORD') or os.getenv('PGPASSWORD')\n"
        "    _db_pool = pool.ThreadedConnectionPool(\n"
        "        minconn=1, maxconn=4, password=password,\n"
        "        host=os.getenv('DB_HOST', 'db.example.com'),\n"
        "    )\n"
        "    return _db_pool\n\n"
        "def query_db(query, params=None):\n"
        "    db_pool = init_db_pool()\n"
        "    connection = db_pool.getconn()\n"
        "    try:\n"
        "        with connection.cursor(cursor_factory=RealDictCursor) as cursor:\n"
        "            cursor.execute(query, params)\n"
        "            return [dict(row) for row in cursor.fetchall()]\n"
        "    finally:\n"
        "        db_pool.putconn(connection)\n"
    )
    data = (
        "from repository import query_db\n\n"
        "def _fetch(table):\n"
        '    return query_db(f"SELECT * FROM {table}")\n\n'
        "def get_services():\n"
        '    return _fetch("service")\n'
    )
    reach = _reach(
        tmp_path,
        "from data import get_services\n\n"
        "def act() -> list:\n"
        "    return get_services()\n",
        files={"repository.py": repository, "data.py": data},
    )
    [effect] = reach["effects"]
    assert _effects(reach) == [("database", "read", "cursor.execute", "service")]
    assert (effect["library"], effect["service"], effect["at"]) == ("psycopg2", "postgresql", "repository.py:23")
    assert effect["host"] == ["db.example.com", "env DB_HOST"]
    assert effect["credential_sources"] == [{"keyword": "password", "env": ["DB_PASSWORD", "PGPASSWORD"]}]
    # The pool's builder is past the bound, and the pool is built outside the
    # function that reads through it: both stay named, so no read is claimed.
    assert any("calls init_db_pool, more than 3 helper calls" in why for why in _whys(reach))
    assert any(why.startswith("reads through cursor, built outside this function") for why in _whys(reach))
    assert reach["effect_claims"] == []


def test_a_global_set_to_two_kinds_of_object_is_not_read(tmp_path):
    reach = _reach(
        tmp_path,
        "import sqlite3\nimport redis\n\n"
        "_conn = None\n\n"
        "def use_redis():\n"
        "    global _conn\n"
        "    _conn = redis.Redis()\n\n"
        "def act() -> list:\n"
        "    global _conn\n"
        '    _conn = _conn or sqlite3.connect("x.db")\n'
        '    return _conn.execute("DELETE FROM t")\n',
    )
    assert "effects" not in reach
    assert reach["limits"]


def test_sqlalchemy_sessions_queries_and_statements(tmp_path):
    reach = _reach(
        tmp_path,
        "import os\n"
        "from sqlalchemy import create_engine, delete, select, text\n"
        "from sqlalchemy.orm import sessionmaker\n"
        "from models import User\n\n"
        'engine = create_engine("postgresql+psycopg2://app@db.internal/app")\n'
        "Session = sessionmaker(bind=engine)\n\n"
        "def act(name: str) -> None:\n"
        "    with Session() as session:\n"
        '        session.execute(text("SELECT 1 FROM users"))\n'
        "        session.query(User).filter(User.name == name).all()\n"
        "        session.execute(select(User).where(User.name == name))\n"
        "        session.add(User(name=name))\n"
        "        session.execute(delete(User).where(User.name == name))\n"
        "        session.commit()\n",
        files={"models.py": "class User:\n    name = None\n"},
    )
    assert _effects(reach) == [
        ("database", "read", "session.execute", "users"),
        ("database", "read", "query.all", None),
        ("database", "read", "session.execute", None),
        ("database", "write", "session.add", None),
        ("database", "write", "session.execute", None),
    ]
    assert {item["service"] for item in reach["effects"]} == {"postgresql"}
    assert reach["effects"][0]["host"] == ["db.internal"]
    assert _claims(reach) == ["write", "write"]


def test_mongodb_collections_by_name_and_an_aggregation_that_writes(tmp_path):
    reach = _reach(
        tmp_path,
        "from pymongo import MongoClient\n\n"
        "def act(sku: str) -> list:\n"
        '    client = MongoClient("mongodb://mongo:27017")\n'
        '    orders = client.shop["orders"]\n'
        '    found = list(orders.find({"sku": sku}).sort("at", -1).limit(5))\n'
        '    orders.aggregate([{"$match": {"sku": sku}}, {"$out": "archive"}])\n'
        "    return found\n",
    )
    assert _effects(reach) == [
        ("database", "read", "collection.find", "shop.orders"),
        ("database", "write", "collection.aggregate", "shop.orders"),
    ]
    assert reach["effects"][0]["host"] == ["mongo"]
    assert reach["limits"] == []
    assert _claims(reach) == ["write"]


def test_redis_reads_writes_and_a_command_outside_the_table(tmp_path):
    reach = _reach(
        tmp_path,
        "import redis\n\n"
        "def act(user: str) -> str:\n"
        '    r = redis.Redis(host="cache")\n'
        '    r.set(f"seen:{user}", 1)\n'
        '    r.eval("return 1", 0)\n'
        '    return r.get(f"name:{user}")\n',
    )
    assert _effects(reach) == [
        ("database", "write", "client.set", "seen:{user}"),
        ("database", "unknown", "client.eval", None),
        ("database", "read", "client.get", "name:{user}"),
    ]
    assert any(why.startswith("calls r.eval (redis client.eval), which the database table does not name") for why in _whys(reach))
    assert _claims(reach) == ["write"]


def test_asyncpg_fetches_by_their_statement(tmp_path):
    reach = _reach(
        tmp_path,
        "import os\nimport asyncpg\n\n"
        "async def act(order_id: int) -> list:\n"
        '    conn = await asyncpg.connect(os.environ["DATABASE_URL"])\n'
        '    rows = await conn.fetch("SELECT * FROM orders WHERE id = $1", order_id)\n'
        "    await conn.close()\n"
        "    return rows\n",
    )
    assert _effects(reach) == [("database", "read", "connection.fetch", "orders")]
    assert reach["effects"][0]["host"] == ["env DATABASE_URL"]
    assert _claims(reach) == ["read"]


@pytest.mark.parametrize(
    ("imports", "files"),
    [
        # The repository's own `connect`.
        ("from mydb import connect\n", {"mydb.py": "def connect(path):\n    return path\n"}),
        # Another library spelled like one in the table.
        ("from aiosqlite import connect\n", None),
        # A repository module that shadows the library's name.
        ("from sqlite3 import connect\n", {"sqlite3.py": "def connect(path):\n    return path\n"}),
    ],
)
def test_a_database_name_alike_is_not_the_library(tmp_path, imports, files):
    reach = _reach(
        tmp_path,
        f"{imports}\n"
        "def act(name: str) -> None:\n"
        '    connect("app.db").execute("DELETE FROM users")\n',
        files=files,
    )
    assert "effects" not in reach
    assert reach["limits"] and reach["effect_claims"] == []


# -- processes -------------------------------------------------------------------


def test_a_process_names_its_literal_program_through_a_helper(tmp_path):
    # hepagent#72's shape: the tool sends keys to tmux through `_run`.
    reach = _reach(
        tmp_path,
        "import subprocess\n\n"
        "def _run(args):\n"
        "    result = subprocess.run(args, text=True, capture_output=True)\n"
        "    return result.stdout.strip(), result.returncode\n\n"
        "def act(session_name: str, command: str) -> str:\n"
        '    out, rc = _run(["tmux", "send-keys", "-t", session_name, command, "Enter"])\n'
        "    return out.rfind('x')\n",
    )
    [effect] = reach["effects"]
    assert _effects(reach) == [("process", "execute", "subprocess.run", "tmux")]
    assert effect["via"] == ["agent.py:8 _run"]
    assert effect["model_supplied"] == [
        {"param": "command", "into": "command"},
        {"param": "session_name", "into": "command"},
    ]
    # The arguments are digested, never printed.
    assert "send-keys" not in str(reach) and "value_sha256" in effect
    assert reach["limits"] == []
    assert reach["effect_claims"] == [
        {
            "effect": "code_execution",
            "at": "agent.py:4",
            "family": "process",
            "operation": "execute",
            "call": "subprocess.run",
            "target": "tmux",
        }
    ]


@pytest.mark.parametrize(
    ("line", "target", "shell"),
    [
        ('os.system(f"git pull {branch}")', "git", True),
        ('subprocess.run("ls -la /srv", shell=True)', "ls", True),
        ('subprocess.Popen(["/usr/bin/rsync", "-a", branch]).communicate()', "rsync", False),
        ("subprocess.check_output([sys.executable, '-m', 'pip', 'install', branch])", "sys.executable", False),
        ('os.execvp(branch, [branch])', "{…}", False),
        ('subprocess.run(f"{branch} --help", shell=True)', "{…}", True),
    ],
)
def test_a_process_names_its_program_or_says_it_is_not_read(tmp_path, line, target, shell):
    reach = _reach(
        tmp_path,
        "import os\nimport subprocess\nimport sys\n\n"
        "def act(branch: str) -> None:\n"
        f"    {line}\n",
    )
    [effect] = reach["effects"]
    assert (effect["family"], effect["operation"], effect["target"]) == ("process", "execute", target)
    assert effect.get("shell", False) is shell
    assert _claims(reach) == ["code_execution"]


@pytest.mark.parametrize(
    ("source", "files"),
    [
        # The repository's own `subprocess` module.
        ("import subprocess\n", {"subprocess.py": "def run(args):\n    return args\n"}),
        # Another library's `run`.
        ("from invoke import run\nimport invoke as subprocess\n", None),
        # The library's function, replaced elsewhere in the scope.
        ("import subprocess\n", {"hooks.py": "import subprocess\n\nsubprocess.run = print\n"}),
    ],
)
def test_a_process_name_alike_is_not_an_execution(tmp_path, source, files):
    reach = _reach(
        tmp_path,
        f"{source}\n"
        "def act(name: str) -> None:\n"
        '    subprocess.run(["rm", "-rf", name])\n',
        files=files,
    )
    assert "effects" not in reach
    assert reach["effect_claims"] == []


# -- files -----------------------------------------------------------------------


def test_a_file_opened_for_writing_and_its_codec(tmp_path):
    reach = _reach(
        tmp_path,
        "import json\n\n"
        "def act(name: str, data: dict) -> dict:\n"
        '    with open(f"out/{name}.json", "w") as handle:\n'
        "        json.dump(data, handle)\n"
        '    with open("config.json") as handle:\n'
        "        return json.load(handle)\n",
    )
    assert _effects(reach) == [
        ("filesystem", "write", "open", "out/{name}.json"),
        ("filesystem", "read", "open", "config.json"),
    ]
    assert reach["effects"][0]["model_supplied"] == [{"param": "name", "into": "path"}]
    assert reach["limits"] == []
    assert _claims(reach) == ["write"]


def test_paths_shutil_and_os(tmp_path):
    reach = _reach(
        tmp_path,
        "import os\nimport shutil\nfrom pathlib import Path\n\n"
        'ROOT = Path(__file__).parent / "data"\n\n'
        "def act(name: str) -> str:\n"
        '    text = (ROOT / f"{name}.txt").read_text()\n'
        '    ROOT.joinpath("cache", name).write_text(text)\n'
        '    shutil.rmtree(ROOT / "tmp")\n'
        '    os.remove(f"logs/{name}.log")\n'
        "    return text\n",
    )
    assert _effects(reach) == [
        ("filesystem", "read", "path.read_text", "{…}/data/{name}.txt"),
        ("filesystem", "write", "path.write_text", "{…}/data/cache/{name}"),
        ("filesystem", "write", "shutil.rmtree", "{…}/data/tmp"),
        ("filesystem", "write", "os.remove", "logs/{name}.log"),
    ]
    assert set(_claims(reach)) == {"write"}


def test_a_path_opened_by_its_own_method_and_its_text(tmp_path):
    reach = _reach(
        tmp_path,
        "import os\nfrom pathlib import Path\n\n"
        "def act(name: str) -> None:\n"
        '    target = Path("exports") / name\n'
        '    with target.open("w") as handle:\n'
        '        handle.write("x")\n'
        '    os.chmod(str(target), 0o600)\n',
    )
    assert _effects(reach) == [
        ("filesystem", "write", "path.open", "exports/{name}"),
        ("filesystem", "write", "os.chmod", "exports/{name}"),
    ]
    assert all(item["model_supplied"] == [{"param": "name", "into": "path"}] for item in reach["effects"])
    assert reach["limits"] == []


def test_a_path_outside_the_repository_is_digested_not_printed(tmp_path):
    reach = _reach(
        tmp_path,
        "def act() -> str:\n"
        '    open("/home/alice/.netrc", "a").write("x")\n'
        '    return open("../secrets/token.txt").read()\n',
    )
    assert [item.get("target") for item in reach["effects"]] == [None, None]
    assert all("value_sha256" in item for item in reach["effects"])
    assert "alice" not in str(reach) and "secrets" not in str(reach)


def test_a_mode_that_is_not_a_literal_is_a_named_limit(tmp_path):
    reach = _reach(
        tmp_path,
        "def act(path: str, mode: str) -> None:\n"
        "    open(path, mode).close()\n",
    )
    assert _effects(reach) == [("filesystem", "unknown", "open", "{path}")]
    assert "the file mode is not a literal; whether it writes is not read" in _whys(reach)
    assert reach["effect_claims"] == []


@pytest.mark.parametrize(
    ("source", "line", "files", "limited"),
    [
        # The module defines its own `open`: it is read as a helper.
        ("def open(path, mode='r'):\n    return path\n", "open(name, 'w')", None, False),
        # Another library's `open`.
        ("from fsspec import open\n", "open(name, 'w')", None, True),
        # The repository's own `shutil`.
        ("import shutil\n", "shutil.rmtree(name)", {"shutil.py": "def rmtree(path):\n    return path\n"}, False),
    ],
)
def test_a_file_name_alike_is_not_the_library(tmp_path, source, line, files, limited):
    reach = _reach(
        tmp_path,
        f"import requests\n{source}\n"
        "def act(name: str) -> dict:\n"
        f"    {line}\n"
        '    return requests.get(f"https://x.test/{name}").json()\n',
        files=files,
    )
    assert "effects" not in reach
    assert bool(reach["limits"]) is limited


# -- cloud SDKs -----------------------------------------------------------------


def test_boto3_operations_by_their_verb(tmp_path):
    reach = _reach(
        tmp_path,
        "import boto3\n\n"
        "def act(key: str, body: str) -> bytes:\n"
        '    s3 = boto3.client("s3")\n'
        '    s3.put_object(Bucket="reports", Key=key, Body=body)\n'
        '    boto3.client("lambda").invoke(FunctionName="rebuild")\n'
        '    return s3.get_object(Bucket="reports", Key=key)["Body"].read()\n',
    )
    assert _effects(reach) == [
        ("cloud", "write", "client.put_object", "reports"),
        ("cloud", "unknown", "client.invoke", "rebuild"),
        ("cloud", "read", "client.get_object", "reports"),
    ]
    assert [item["service"] for item in reach["effects"]] == ["s3", "lambda", "s3"]
    assert any("which the cloud table does not name" in why for why in _whys(reach))
    assert _claims(reach) == ["write"]


def test_boto3_resources_and_google_cloud_clients(tmp_path):
    reach = _reach(
        tmp_path,
        "import boto3\n"
        "from google.cloud import bigquery, firestore, storage\n\n"
        "def act(uid: str, path: str) -> list:\n"
        '    boto3.resource("dynamodb").Table("users").put_item(Item={"id": uid})\n'
        '    storage.Client().bucket("uploads").blob(uid).upload_from_filename(path)\n'
        '    firestore.Client().collection("profiles").document(uid).set({"seen": True})\n'
        '    return list(bigquery.Client().query("SELECT * FROM ds.events").result())\n',
    )
    assert _effects(reach) == [
        ("cloud", "write", "table.put_item", "users"),
        ("cloud", "write", "blob.upload_from_filename", "uploads"),
        ("cloud", "write", "document.set", "profiles"),
        ("cloud", "read", "client.query", "ds.events"),
    ]
    assert [item.get("service") for item in reach["effects"]] == ["dynamodb", "storage", "firestore", "bigquery"]


def test_vertex_ai_memory_bank_writes_and_retrieves(tmp_path):
    reach = _reach(
        tmp_path,
        "import vertexai\n"
        "from google.adk.memory import VertexAiMemoryBankService\n\n"
        'client = vertexai.Client(project="p", location="us-central1")\n\n'
        "def act(fact: str, name: str) -> list:\n"
        "    client.agent_engines.memories.generate(name=name, direct_contents_source={'events': [fact]})\n"
        "    VertexAiMemoryBankService(agent_engine_id='1').search_memory(app_name='a', user_id='u', query=fact)\n"
        "    return client.agent_engines.memories.retrieve(name=name)\n",
    )
    assert _effects(reach) == [
        ("cloud", "write", "memories.generate", None),
        ("cloud", "read", "memory_bank.search_memory", None),
        ("cloud", "read", "memories.retrieve", None),
    ]
    assert {item["service"] for item in reach["effects"]} == {"memory_bank"}
    assert _claims(reach) == ["write"]


@pytest.mark.parametrize(
    ("source", "files"),
    [
        ("from storage import client\n", {"storage.py": "class C:\n    def put_object(self, **k):\n        pass\n\nclient = C()\n"}),
        ("import aioboto3 as boto3\nclient = boto3.client('s3')\n", None),
    ],
)
def test_a_cloud_name_alike_is_not_the_sdk(tmp_path, source, files):
    reach = _reach(
        tmp_path,
        f"{source}\n"
        "def act(key: str) -> None:\n"
        '    client.put_object(Bucket="b", Key=key)\n',
        files=files,
    )
    assert "effects" not in reach
    assert reach["limits"]


# -- messaging -------------------------------------------------------------------


def test_messages_sent_by_smtp_slack_and_twilio(tmp_path):
    reach = _reach(
        tmp_path,
        "import os\nimport smtplib\nfrom slack_sdk import WebClient\nfrom twilio.rest import Client\n\n"
        "def act(to: str, body: str) -> None:\n"
        '    with smtplib.SMTP("smtp.example.com", 587) as smtp:\n'
        "        smtp.starttls()\n"
        '        smtp.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])\n'
        '        smtp.sendmail("bot@example.com", [to], body)\n'
        '    WebClient(token=os.environ["SLACK_BOT_TOKEN"]).chat_postMessage(channel="#ops", text=body)\n'
        '    Client(os.environ["TWILIO_SID"], os.environ["TWILIO_TOKEN"]).messages.create(to=to, body=body)\n',
    )
    assert _effects(reach) == [
        ("messaging", "write", "client.sendmail", None),
        ("messaging", "write", "client.chat_postMessage", "#ops"),
        ("messaging", "write", "messages.create", None),
    ]
    smtp, slack, twilio = reach["effects"]
    assert smtp["host"] == ["smtp.example.com"]
    assert slack["credential_sources"] == [{"keyword": "token", "env": ["SLACK_BOT_TOKEN"]}]
    assert twilio["credential_sources"] == [{"keyword": "password", "env": ["TWILIO_TOKEN"]}]
    assert set(_claims(reach)) == {"external_communication"}
    assert reach["limits"] == []


def test_a_messaging_name_alike_is_not_a_send(tmp_path):
    reach = _reach(
        tmp_path,
        "from chat import WebClient\n\n"
        "def act(body: str) -> None:\n"
        '    WebClient().chat_postMessage(channel="#ops", text=body)\n',
        files={"chat.py": "class WebClient:\n    def chat_postMessage(self, **k):\n        return k\n"},
    )
    assert "effects" not in reach
    assert reach["limits"]


# -- privacy -------------------------------------------------------------------


def test_connection_strings_and_keys_never_print_a_secret(tmp_path):
    reach = _reach(
        tmp_path,
        "import psycopg2\nimport redis\n\n"
        "def act(q: str) -> None:\n"
        '    conn = psycopg2.connect("postgresql://app:hunter22@db.example.com/app")\n'
        '    conn.cursor().execute("SELECT 1 FROM users")\n'
        '    r = redis.Redis.from_url("redis://:s3cr3tpassw0rd@cache:6379/0")\n'
        '    r.set("token:ghp_0123456789abcdefABCDEF0123", q)\n',
    )
    text = str(reach)
    for secret in ("hunter22", "s3cr3tpassw0rd", "ghp_0123456789"):
        assert secret not in text
    database, cache = reach["effects"]
    assert database["host"] == ["db.example.com"]
    assert database["credential_sources"] == [{"userinfo": None, "literal": True}]
    assert cache["host"] == ["cache"] and "target" not in cache and "value_sha256" in cache


# -- what the effects support -----------------------------------------------------


def test_a_read_through_a_client_built_elsewhere_is_not_a_read_claim(tmp_path):
    reach = _reach(
        tmp_path,
        "import sqlite3\n\n"
        'CONN = sqlite3.connect("app.db")\n\n'
        "def act() -> list:\n"
        '    return CONN.execute("SELECT * FROM users").fetchall()\n',
    )
    assert _effects(reach) == [("database", "read", "connection.execute", "users")]
    assert _whys(reach) == ["reads through CONN, built outside this function; its configuration is not read"]
    assert reach["effect_claims"] == []


def test_a_read_effect_beside_an_http_read_is_still_a_read(tmp_path):
    reach = _reach(
        tmp_path,
        "import requests\nfrom pathlib import Path\n\n"
        "def act(q: str) -> dict:\n"
        '    template = Path("templates/q.txt").read_text()\n'
        '    return requests.get(f"https://x.test/{q}", params={"t": template}).json()\n',
    )
    assert reach["effect_claims"] == [{"effect": "read", "at": "agent.py:4", "calls": 1, "effects": 1}]


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT setval('orders_id_seq', 1)",
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity",
        "SELECT audit.log_access(?) FROM users",
    ],
)
def test_a_select_that_calls_a_function_not_known_to_read_is_not_a_read(tmp_path, statement):
    reach = _reach(
        tmp_path,
        "import sqlite3\n\n"
        "def act() -> list:\n"
        f'    return sqlite3.connect("a.db").execute("{statement}").fetchall()\n',
    )
    assert [item["operation"] for item in reach["effects"]] == ["unknown"]
    assert reach["effect_claims"] == []


def test_a_select_calling_only_read_functions_reads():
    assert sql_operation("SELECT count(*), max(at), lower(name) FROM t WHERE id IN (1, 2)", complete=True).operation == "read"


def test_a_connection_built_with_an_unseen_object_is_not_read_as_plain(tmp_path):
    reach = _reach(
        tmp_path,
        "import sqlite3\nfrom audited import AuditedConnection\n\n"
        "def act() -> list:\n"
        '    conn = sqlite3.connect("a.db", factory=AuditedConnection)\n'
        '    return conn.execute("SELECT * FROM users").fetchall()\n',
        files={"audited.py": "import sqlite3\n\nclass AuditedConnection(sqlite3.Connection):\n    pass\n"},
    )
    assert _effects(reach) == [("database", "read", "connection.execute", "users")]
    assert any("built with an argument this read does not see into" in why for why in _whys(reach))
    assert reach["effect_claims"] == []


@pytest.mark.parametrize(
    "line",
    [
        "AUDIT.write(q)",
        "print(q, file=AUDIT)",
        "json.dump({'q': q}, AUDIT)",
    ],
)
def test_a_write_to_a_file_opened_elsewhere_is_a_write_here(tmp_path, line):
    reach = _reach(
        tmp_path,
        "import json\nimport requests\n\n"
        'AUDIT = open("audit.log", "a")\n\n'
        "def act(q: str) -> dict:\n"
        f"    {line}\n"
        '    return requests.get(f"https://x.test/{q}").json()\n',
    )
    [effect] = reach["effects"]
    assert (effect["family"], effect["operation"], effect["target"]) == ("filesystem", "write", "audit.log")
    assert _claims(reach) == ["write"]


def test_printing_to_an_object_the_read_cannot_name_is_a_limit(tmp_path):
    reach = _reach(
        tmp_path,
        "import requests\nfrom sinks import SINK\n\n"
        "def act(q: str) -> dict:\n"
        "    print(q, file=SINK)\n"
        '    return requests.get(f"https://x.test/{q}").json()\n',
    )
    assert "prints to SINK, which is not read" in _whys(reach)
    assert reach["effect_claims"] == []


def test_a_download_also_writes_the_local_file(tmp_path):
    reach = _reach(
        tmp_path,
        "import boto3\n\n"
        "def act(key: str) -> None:\n"
        '    boto3.client("s3").download_file("reports", key, f"cache/{key}")\n',
    )
    assert _effects(reach) == [
        ("cloud", "read", "client.download_file", "reports"),
        ("filesystem", "write", "client.download_file", "cache/{key}"),
    ]
    assert _claims(reach) == ["write"]


def test_a_process_started_elsewhere_is_not_read(tmp_path):
    reach = _reach(
        tmp_path,
        "import subprocess\n\n"
        'WORKER = subprocess.Popen(["worker"], stdin=subprocess.PIPE)\n\n'
        "def act(q: str) -> None:\n"
        "    WORKER.communicate(q.encode())\n",
    )
    assert _effects(reach) == [("process", "unknown", "process.communicate", None)]
    assert any("on a process started outside this function" in why for why in _whys(reach))
    assert reach["effect_claims"] == []


def test_effects_past_the_bound_still_count(tmp_path):
    lines = "".join(f'    open("f{index}.txt").read()\n' for index in range(MAX_CALLS))
    reach = _reach(
        tmp_path,
        f"def act() -> None:\n{lines}"
        '    open("last.txt", "w").write("x")\n',
    )
    assert len(reach["effects"]) == MAX_CALLS
    assert f"the tool reaches more than {MAX_CALLS} library effects" in _whys(reach)
    assert _claims(reach) == ["write"]


def test_the_assessment_reads_a_library_effect_as_structural_evidence():
    reach = {
        "calls": [],
        "effects": [],
        "limits": [],
        "effect_claims": [
            {
                "effect": "code_execution",
                "at": "agent.py:4",
                "family": "process",
                "operation": "execute",
                "call": "subprocess.run",
                "target": "tmux",
            }
        ],
    }
    assessed = assess_tool_semantics(_tool(reach))
    assert assessed.conservative_effect == "code_execution"
    assert assessed.effect.status == "structural"
    [claim] = [claim for claim in assessed.effect.claims if claim.source == REACH_EFFECT_CLAIM_SOURCE]
    assert claim.evidence == {"family": "process", "operation": "execute", "call": "subprocess.run", "target": "tmux"}


def test_effects_are_deterministic(tmp_path):
    source = (
        "import subprocess\nimport sqlite3\n\n"
        "def act(name: str) -> None:\n"
        '    subprocess.run(["git", "tag", name])\n'
        '    sqlite3.connect("a.db").execute("UPDATE t SET a = ?", (name,))\n'
    )
    assert _reach(tmp_path, source) == _reach(tmp_path, source)
