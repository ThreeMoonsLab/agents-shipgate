"""What a bound tool reaches beyond HTTP: the tables of library effects (#913).

A tool's reach (:mod:`agents_shipgate.inputs.tool_reach`) names the outbound
HTTP calls its own code makes. Most tools that change in real pull requests do
their work somewhere else: a database, a process, a file, a cloud SDK or a
message. This module holds what the reader knows about those libraries, by
import identity and method, never by a name alone:

* :data:`CONSTRUCTORS`: a library call that builds an object the reader
  follows (a connection, a client, a path), and what it is;
* :data:`FUNCTIONS`: a library function that is itself an effect
  (``subprocess.run``, ``os.remove``, ``shutil.rmtree``);
* :func:`method_rule`: what a method on such an object does — an effect, read
  or write or execution; another object (``conn.cursor()``); or nothing
  outside the process (``cursor.fetchall()``). A method outside the tables
  has no rule, and the reader names it as a limit rather than guess;
* :func:`sql_operation`: whether a literal SQL statement reads or writes.

Everything here is data about libraries and pure string reading. Nothing is
imported or run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: The families an effect belongs to, in the order a row lists them.
FAMILIES = ("database", "process", "filesystem", "cloud", "messaging")
#: An effect's operation. ``unknown``: the family is established, the
#: direction is not.
OPERATIONS = ("read", "write", "execute", "unknown")


@dataclass(frozen=True)
class Kind:
    """What a constructor builds: its family, library, role and service."""

    family: str
    library: str
    role: str
    service: str | None = None


def _kinds(family: str, library: str, role: str, service: str | None, *dotted: str) -> dict[str, Kind]:
    return {name: Kind(family, library, role, service) for name in dotted}


#: Library calls that build an object whose methods the reader follows.
CONSTRUCTORS: dict[str, Kind] = {
    # Databases.
    **_kinds("database", "sqlite3", "connection", "sqlite", "sqlite3.connect", "sqlite3.Connection"),
    **_kinds("database", "psycopg2", "connection", "postgresql", "psycopg2.connect"),
    **_kinds(
        "database", "psycopg2", "pool", "postgresql",
        "psycopg2.pool.SimpleConnectionPool", "psycopg2.pool.ThreadedConnectionPool",
    ),
    **_kinds(
        "database", "psycopg", "connection", "postgresql",
        "psycopg.connect", "psycopg.Connection.connect", "psycopg.AsyncConnection.connect",
    ),
    **_kinds(
        "database", "psycopg", "pool", "postgresql",
        "psycopg_pool.ConnectionPool", "psycopg_pool.AsyncConnectionPool",
    ),
    **_kinds("database", "asyncpg", "connection", "postgresql", "asyncpg.connect"),
    **_kinds("database", "asyncpg", "pool", "postgresql", "asyncpg.create_pool"),
    **_kinds(
        "database", "pymysql", "connection", "mysql",
        "pymysql.connect", "pymysql.Connect", "pymysql.connections.Connection",
    ),
    **_kinds(
        "database", "sqlalchemy", "engine", None,
        "sqlalchemy.create_engine", "sqlalchemy.ext.asyncio.create_async_engine",
    ),
    **_kinds(
        "database", "sqlalchemy", "session_factory", None,
        "sqlalchemy.orm.sessionmaker", "sqlalchemy.ext.asyncio.async_sessionmaker",
    ),
    # A scoped session is called for its session and proxies one's methods.
    **_kinds(
        "database", "sqlalchemy", "session", None,
        "sqlalchemy.orm.Session", "sqlalchemy.ext.asyncio.AsyncSession",
        "sqlalchemy.orm.scoped_session", "sqlalchemy.ext.asyncio.async_scoped_session",
    ),
    **_kinds("database", "pymongo", "client", "mongodb", "pymongo.MongoClient", "pymongo.mongo_client.MongoClient"),
    **_kinds(
        "database", "redis", "client", "redis",
        "redis.Redis", "redis.StrictRedis", "redis.from_url", "redis.Redis.from_url",
        "redis.asyncio.Redis", "redis.asyncio.from_url", "redis.asyncio.Redis.from_url",
        "redis.client.Redis",
    ),
    # Processes started and kept as an object.
    **_kinds("process", "subprocess", "process", None, "subprocess.Popen"),
    # Files.
    **_kinds(
        "filesystem", "pathlib", "path", None,
        "pathlib.Path", "pathlib.PosixPath", "pathlib.WindowsPath",
        "pathlib.Path.cwd", "pathlib.Path.home",
    ),
    # Cloud SDKs.
    **_kinds("cloud", "boto3", "session", None, "boto3.Session", "boto3.session.Session"),
    **_kinds("cloud", "boto3", "client", None, "boto3.client"),
    **_kinds("cloud", "boto3", "resource", None, "boto3.resource"),
    **_kinds(
        "cloud", "google.cloud.storage", "client", "storage",
        "google.cloud.storage.Client", "google.cloud.storage.Client.from_service_account_json",
        "google.cloud.storage.Client.from_service_account_info",
    ),
    **_kinds(
        "cloud", "google.cloud.firestore", "client", "firestore",
        "google.cloud.firestore.Client", "google.cloud.firestore.AsyncClient",
        "google.cloud.firestore_v1.Client", "google.cloud.firestore_v1.AsyncClient",
    ),
    **_kinds(
        "cloud", "google.cloud.bigquery", "client", "bigquery",
        "google.cloud.bigquery.Client",
    ),
    **_kinds("cloud", "vertexai", "client", "memory_bank", "vertexai.Client"),
    **_kinds(
        "cloud", "google.adk", "memory_bank", "memory_bank",
        "google.adk.memory.VertexAiMemoryBankService",
        "google.adk.memory.vertex_ai_memory_bank_service.VertexAiMemoryBankService",
    ),
    # Messaging.
    **_kinds("messaging", "smtplib", "client", "smtp", "smtplib.SMTP", "smtplib.SMTP_SSL", "smtplib.LMTP"),
    **_kinds(
        "messaging", "slack_sdk", "client", "slack",
        "slack_sdk.WebClient", "slack_sdk.web.WebClient", "slack_sdk.web.client.WebClient",
        "slack_sdk.AsyncWebClient", "slack_sdk.web.async_client.AsyncWebClient",
    ),
    **_kinds(
        "messaging", "slack_sdk", "webhook", "slack",
        "slack_sdk.webhook.WebhookClient", "slack_sdk.webhook.async_client.AsyncWebhookClient",
    ),
    **_kinds("messaging", "twilio", "client", "twilio", "twilio.rest.Client"),
}

#: Where a constructor's argument names what it reaches: the boto3 service, an
#: SMTP host, a path. ``(position, keyword)``.
CONSTRUCTOR_TARGETS: dict[str, tuple[int | None, str]] = {
    "boto3.client": (0, "service_name"),
    "boto3.resource": (0, "service_name"),
    "pathlib.Path": (0, "*"),
    "pathlib.PosixPath": (0, "*"),
    "pathlib.WindowsPath": (0, "*"),
    "smtplib.SMTP": (0, "host"),
    "smtplib.SMTP_SSL": (0, "host"),
    "smtplib.LMTP": (0, "host"),
}

#: Constructor arguments that carry a credential by position, by name.
POSITIONAL_CREDENTIALS: dict[str, tuple[str, ...]] = {
    "twilio.rest.Client": ("username", "password"),
}

#: SQLAlchemy statement constructors, by the statement they build.
STATEMENTS: dict[str, str] = {
    f"{module}.{name}": name.upper()
    for module in (
        "sqlalchemy", "sqlalchemy.sql", "sqlalchemy.sql.expression", "sqlalchemy.future",
        "sqlalchemy.dialects.postgresql", "sqlalchemy.dialects.sqlite", "sqlalchemy.dialects.mysql",
    )
    for name in ("select", "insert", "update", "delete")
}
#: Calls that hand back the SQL text they are given.
SQL_TEXT = frozenset({"sqlalchemy.text", "sqlalchemy.sql.text", "sqlalchemy.sql.expression.text"})


@dataclass(frozen=True)
class Function:
    """A library function that is an effect, and where its target is.

    ``operation``: ``read``, ``write``, ``execute`` or ``mode`` (the file mode
    argument decides). ``target``: ``(position, keyword)`` of the argument
    naming what it reaches. ``shell``: the command is a shell string.
    """

    family: str
    operation: str
    target: tuple[int | None, str] = (0, "")
    shell: bool = False


_PROCESS_EXEC = Function("process", "execute", (0, "args"))
_PROCESS_SHELL = Function("process", "execute", (0, "cmd"), shell=True)

#: Library functions that are themselves an effect.
FUNCTIONS: dict[str, Function] = {
    **{
        f"subprocess.{name}": _PROCESS_EXEC
        for name in ("run", "call", "check_call", "check_output", "Popen")
    },
    "subprocess.getoutput": _PROCESS_SHELL,
    "subprocess.getstatusoutput": _PROCESS_SHELL,
    "os.system": Function("process", "execute", (0, "command"), shell=True),
    "os.popen": _PROCESS_SHELL,
    **{
        f"os.{name}": Function("process", "execute", (0, "path"))
        for name in (
            "execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp", "execvpe",
            "posix_spawn", "posix_spawnp", "startfile",
        )
    },
    **{
        f"os.{name}": Function("process", "execute", (1, "file"))
        for name in ("spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe")
    },
    "asyncio.create_subprocess_exec": Function("process", "execute", (0, "program")),
    "asyncio.create_subprocess_shell": Function("process", "execute", (0, "cmd"), shell=True),
    "open": Function("filesystem", "mode", (0, "file")),
    "io.open": Function("filesystem", "mode", (0, "file")),
    **{
        f"os.{name}": Function("filesystem", "write", (0, "path"))
        for name in (
            "remove", "unlink", "rmdir", "removedirs", "makedirs", "mkdir", "chmod", "chown",
            "lchown", "truncate", "utime", "mkfifo",
        )
    },
    **{f"os.{name}": Function("filesystem", "write", (1, "dst")) for name in ("rename", "renames", "replace", "symlink", "link")},
    **{f"os.{name}": Function("filesystem", "read", (0, "path")) for name in ("listdir", "scandir", "stat", "lstat")},
    "os.walk": Function("filesystem", "read", (0, "top")),
    **{
        f"shutil.{name}": Function("filesystem", "write", (1, "dst"))
        for name in ("copy", "copy2", "copyfile", "copytree", "copymode", "copystat", "move")
    },
    "shutil.rmtree": Function("filesystem", "write", (0, "path")),
    "shutil.chown": Function("filesystem", "write", (0, "path")),
    "shutil.make_archive": Function("filesystem", "write", (0, "base_name")),
    "shutil.unpack_archive": Function("filesystem", "write", (1, "extract_dir")),
}

#: Library functions passed over when they read or write a file the tool
#: opened: the effect was recorded where the file was opened.
FILE_CODECS = frozenset({"json.load", "json.dump", "yaml.safe_load", "yaml.safe_dump"})
#: Classes a database call is handed that only shape the rows it returns.
ROW_FACTORIES = frozenset(
    {
        "sqlite3.Row", "psycopg2.extras.RealDictCursor", "psycopg2.extras.DictCursor",
        "psycopg2.extras.NamedTupleCursor", "psycopg.rows.dict_row", "psycopg.rows.namedtuple_row",
        "psycopg.rows.tuple_row", "pymysql.cursors.DictCursor", "pymysql.cursors.SSCursor",
        "pymysql.cursors.SSDictCursor",
    }
)
#: A file object's reads and writes. On a file the tool opened they are part of
#: the effect named where it was opened; on one opened elsewhere (a module's
#: log file) they are the effect.
FILE_IO = {
    "read": "read", "readline": "read", "readlines": "read", "__next__": "read",
    "write": "write", "writelines": "write", "truncate": "write",
}
#: Cloud operations that also write a local file, and where its path is.
LOCAL_WRITES: dict[tuple[str, str, str], tuple[int, str]] = {
    ("boto3", "client", "download_file"): (2, "Filename"),
    ("boto3", "bucket", "download_file"): (1, "Filename"),
    ("boto3", "object", "download_file"): (0, "Filename"),
    ("google.cloud.storage", "blob", "download_to_filename"): (0, "filename"),
}
#: boto3's S3 transfer methods take their bucket by position.
BOTO3_BUCKET_POSITIONS = {
    "upload_file": 1, "upload_fileobj": 1, "download_file": 0, "download_fileobj": 0, "copy": 1,
}
#: Settings of a database connection that only shape how it answers.
HANDLE_SETTINGS = frozenset({"row_factory", "text_factory", "isolation_level", "autocommit", "cursor_factory"})

# -- methods -------------------------------------------------------------------

#: A method's rule: ``read``, ``write`` or ``execute``: an effect; ``sql``: the
#: SQL argument decides; ``statement``: a SQL text or SQLAlchemy statement
#: decides; ``script``: several SQL statements decide; ``aggregate``: a MongoDB
#: pipeline decides; ``mode``: the file mode argument decides; ``pass``: no
#: effect outside the process; ``same``: the same object, refined;
#: ``->role``: another object; ``read->role``: a read that hands back another
#: object. No rule: not in the tables.
Rule = str

_DBAPI_CONNECTION: dict[str, Rule] = {
    "cursor": "->cursor", "execute": "sql", "executemany": "sql", "executescript": "script",
    "commit": "pass", "rollback": "pass", "close": "pass", "transaction": "pass",
    "__enter__": "same",
}
_DBAPI_CURSOR: dict[str, Rule] = {
    "execute": "sql", "executemany": "sql", "executescript": "script",
    "fetchone": "pass", "fetchall": "pass", "fetchmany": "pass", "close": "pass",
    "scroll": "pass", "nextset": "pass", "mogrify": "pass", "__enter__": "same",
}
_ASYNCPG_CONNECTION: dict[str, Rule] = {
    "execute": "sql", "executemany": "sql", "fetch": "sql", "fetchrow": "sql", "fetchval": "sql",
    "fetchmany": "sql", "prepare": "sql",
    "copy_to_table": "write", "copy_records_to_table": "write",
    "copy_from_table": "read", "copy_from_query": "read",
    "transaction": "pass", "close": "pass",
}
_SQLALCHEMY_CONNECTION: dict[str, Rule] = {
    "execute": "statement", "scalar": "statement", "scalars": "statement", "exec_driver_sql": "sql",
    "commit": "pass", "rollback": "pass", "close": "pass", "begin": "pass", "begin_nested": "pass",
    "__enter__": "same",
}
_SQLALCHEMY_SESSION: dict[str, Rule] = {
    "execute": "statement", "scalar": "statement", "scalars": "statement",
    "get": "read", "refresh": "read", "query": "->query",
    "add": "write", "add_all": "write", "delete": "write", "merge": "write",
    "bulk_save_objects": "write", "bulk_insert_mappings": "write", "bulk_update_mappings": "write",
    "commit": "pass", "flush": "pass", "rollback": "pass", "close": "pass", "begin": "pass",
    "begin_nested": "pass", "expunge": "pass", "expunge_all": "pass", "expire": "pass",
    "expire_all": "pass", "__enter__": "same",
}
_SQLALCHEMY_QUERY: dict[str, Rule] = {
    **{
        name: "same"
        for name in (
            "filter", "filter_by", "order_by", "limit", "offset", "join", "outerjoin", "options",
            "group_by", "having", "distinct", "with_entities", "where", "select_from",
        )
    },
    **{name: "read" for name in ("all", "first", "one", "one_or_none", "scalar", "count", "get")},
    "delete": "write", "update": "write",
}
_PYMONGO_COLLECTION: dict[str, Rule] = {
    **{
        name: "read"
        for name in (
            "find", "find_one", "count_documents", "estimated_document_count", "distinct",
            "list_indexes", "index_information", "watch", "find_raw_batches",
        )
    },
    **{
        name: "write"
        for name in (
            "insert_one", "insert_many", "update_one", "update_many", "replace_one", "delete_one",
            "delete_many", "find_one_and_update", "find_one_and_replace", "find_one_and_delete",
            "bulk_write", "create_index", "create_indexes", "drop", "drop_index", "drop_indexes",
            "rename",
        )
    },
    "aggregate": "aggregate",
}
_REDIS_READS = frozenset(
    {
        "get", "mget", "getrange", "strlen", "exists", "keys", "scan", "scan_iter", "type", "ttl",
        "pttl", "hget", "hgetall", "hmget", "hkeys", "hvals", "hlen", "hexists", "hscan",
        "hscan_iter", "lrange", "llen", "lindex", "smembers", "sismember", "smismember", "scard",
        "srandmember", "sscan", "sscan_iter", "zrange", "zrangebyscore", "zrevrange",
        "zrevrangebyscore", "zscore", "zrank", "zrevrank", "zcard", "zcount", "zscan",
        "xrange", "xrevrange", "xread", "xlen", "dbsize",
    }
)
_REDIS_WRITES = frozenset(
    {
        "set", "setex", "psetex", "setnx", "mset", "msetnx", "getset", "getdel", "getex", "append",
        "incr", "incrby", "incrbyfloat", "decr", "decrby", "delete", "unlink", "expire", "expireat",
        "pexpire", "pexpireat", "persist", "rename", "renamenx", "hset", "hsetnx", "hmset", "hdel",
        "hincrby", "hincrbyfloat", "lpush", "rpush", "lpushx", "rpushx", "lpop", "rpop", "blpop",
        "brpop", "lrem", "lset", "ltrim", "linsert", "lmove", "sadd", "srem", "spop", "smove",
        "zadd", "zrem", "zincrby", "zpopmin", "zpopmax", "zremrangebyscore", "zremrangebyrank",
        "xadd", "xdel", "xtrim", "publish", "flushdb", "flushall",
    }
)
_REDIS_CLIENT: dict[str, Rule] = {
    **{name: "read" for name in _REDIS_READS},
    **{name: "write" for name in _REDIS_WRITES},
    "close": "pass", "aclose": "pass", "ping": "pass", "pipeline": "->pipeline", "__enter__": "same",
}
_REDIS_PIPELINE: dict[str, Rule] = {**_REDIS_CLIENT, "execute": "pass", "multi": "pass", "reset": "pass"}

_PATH: dict[str, Rule] = {
    **{name: "read" for name in ("read_text", "read_bytes", "iterdir", "glob", "rglob", "walk", "readlink")},
    **{
        name: "write"
        for name in (
            "write_text", "write_bytes", "touch", "mkdir", "unlink", "rmdir", "rename", "replace",
            "chmod", "lchmod", "symlink_to", "hardlink_to", "link_to",
        )
    },
    "open": "mode",
    **{
        name: "same"
        for name in (
            "resolve", "absolute", "expanduser", "joinpath", "with_name", "with_suffix", "with_stem",
            "relative_to", "with_segments",
        )
    },
    **{
        name: "pass"
        for name in (
            "exists", "is_file", "is_dir", "is_symlink", "is_absolute", "is_relative_to", "stat",
            "lstat", "as_posix", "as_uri", "match", "full_match", "samefile", "owner", "group",
            "is_mount", "is_socket", "is_fifo", "is_block_device", "is_char_device",
        )
    },
}
#: A path's attributes that are another path; any other is plain data.
PATH_ATTRIBUTES = frozenset({"parent", "parents"})
_FILE: dict[str, Rule] = {
    name: "pass"
    for name in (
        "read", "readline", "readlines", "write", "writelines", "seek", "tell", "flush", "close",
        "truncate", "fileno", "readable", "writable", "seekable", "__enter__", "__iter__", "__next__",
    )
}
_PROCESS: dict[str, Rule] = {
    name: "pass"
    for name in ("communicate", "wait", "poll", "kill", "terminate", "send_signal", "__enter__")
}

#: boto3 operation names, by the verb they start with.
_BOTO3_READ_PREFIXES = ("get_", "list_", "describe_", "head_", "batch_get_", "download_", "select_")
_BOTO3_READS = frozenset({"query", "scan"})
_BOTO3_WRITE_PREFIXES = (
    "put_", "create_", "delete_", "update_", "upload_", "batch_write_", "copy_", "send_",
    "publish", "tag_", "untag_", "start_", "stop_", "terminate_", "attach_", "detach_",
    "modify_", "restore_",
)
_BOTO3_PASS = frozenset({"generate_presigned_url", "generate_presigned_post", "get_waiter", "close", "can_paginate"})
#: A boto3 resource object's own methods: `s3.Object(b, k).get()`.
_BOTO3_RESOURCE_READS = frozenset({"get", "load", "reload"})
_BOTO3_RESOURCE_WRITES = frozenset({"put", "delete", "copy_from", "upload_file", "upload_fileobj", "update"})
#: A boto3 resource collection (`bucket.objects`).
_BOTO3_COLLECTION: dict[str, Rule] = {
    "all": "read", "filter": "read", "limit": "read", "page_size": "read", "delete": "write",
}

_GCS_CLIENT: dict[str, Rule] = {
    "bucket": "->bucket", "get_bucket": "read->bucket", "lookup_bucket": "read->bucket",
    "list_buckets": "read", "list_blobs": "read", "create_bucket": "write", "close": "pass",
}
_GCS_BUCKET: dict[str, Rule] = {
    "blob": "->blob", "get_blob": "read->blob", "list_blobs": "read", "exists": "read",
    "reload": "read", "delete": "write", "delete_blob": "write", "delete_blobs": "write",
    "copy_blob": "write", "rename_blob": "write", "patch": "write", "update": "write",
    "make_public": "write", "make_private": "write",
}
_GCS_BLOB: dict[str, Rule] = {
    **{
        name: "write"
        for name in (
            "upload_from_string", "upload_from_filename", "upload_from_file", "delete", "patch",
            "update", "compose", "rewrite", "make_public", "make_private",
        )
    },
    **{
        name: "read"
        for name in (
            "download_as_text", "download_as_bytes", "download_as_string", "download_to_filename",
            "download_to_file", "exists", "reload",
        )
    },
    "open": "mode", "generate_signed_url": "pass",
}
_FIRESTORE_QUERY_BUILDERS = (
    "where", "order_by", "limit", "limit_to_last", "offset", "select", "start_at", "start_after",
    "end_at", "end_before",
)
_FIRESTORE_CLIENT: dict[str, Rule] = {
    "collection": "->collection", "document": "->document", "batch": "->batch",
    "collections": "read", "get_all": "read", "close": "pass",
}
_FIRESTORE_COLLECTION: dict[str, Rule] = {
    "document": "->document", "add": "write", "stream": "read", "get": "read",
    "list_documents": "read", **{name: "->query" for name in _FIRESTORE_QUERY_BUILDERS},
}
_FIRESTORE_QUERY: dict[str, Rule] = {
    "stream": "read", "get": "read", **{name: "same" for name in _FIRESTORE_QUERY_BUILDERS},
}
_FIRESTORE_DOCUMENT: dict[str, Rule] = {
    "get": "read", "set": "write", "update": "write", "delete": "write", "create": "write",
    "collection": "->collection", "collections": "read",
}
_FIRESTORE_BATCH: dict[str, Rule] = {
    "set": "write", "update": "write", "delete": "write", "create": "write", "commit": "pass",
}
_BIGQUERY_CLIENT: dict[str, Rule] = {
    "query": "sql", "query_and_wait": "sql",
    **{
        name: "read"
        for name in ("get_table", "get_dataset", "list_tables", "list_datasets", "list_rows", "get_job", "list_jobs")
    },
    **{
        name: "write"
        for name in (
            "insert_rows", "insert_rows_json", "insert_rows_from_dataframe", "load_table_from_dataframe",
            "load_table_from_file", "load_table_from_json", "load_table_from_uri", "create_table",
            "create_dataset", "delete_table", "delete_dataset", "update_table", "update_dataset",
            "copy_table", "extract_table",
        )
    },
    "close": "pass",
}
_MEMORY_WRITES = ("generate", "create", "delete", "update", "purge")
_MEMORY_READS = ("retrieve", "get", "list")
_VERTEX_AGENT_ENGINES: dict[str, Rule] = {
    "generate_memories": "write", "create_memory": "write", "delete_memory": "write",
    "update_memory": "write", "purge_memories": "write",
    "retrieve_memories": "read", "get_memory": "read", "list_memories": "read",
}
_VERTEX_MEMORIES: dict[str, Rule] = {
    **{name: "write" for name in _MEMORY_WRITES}, **{name: "read" for name in _MEMORY_READS},
}
_ADK_MEMORY_BANK: dict[str, Rule] = {
    "add_session_to_memory": "write", "add_events_to_memory": "write", "search_memory": "read",
}
_SMTP: dict[str, Rule] = {
    "sendmail": "write", "send_message": "write",
    **{
        name: "pass"
        for name in (
            "login", "starttls", "ehlo", "helo", "ehlo_or_helo_if_needed", "quit", "close",
            "connect", "set_debuglevel", "noop", "has_extn", "__enter__",
        )
    },
}
_SLACK_WRITES = frozenset(
    {
        "chat_postMessage", "chat_postEphemeral", "chat_scheduleMessage", "chat_update", "chat_delete",
        "chat_meMessage", "files_upload", "files_upload_v2", "files_delete", "reactions_add",
        "reactions_remove", "conversations_create", "conversations_invite", "conversations_kick",
        "conversations_archive", "conversations_setTopic", "conversations_setPurpose", "pins_add",
        "pins_remove", "views_open", "views_publish", "views_push", "views_update",
    }
)
_SLACK_READS = frozenset(
    {
        "conversations_history", "conversations_replies", "conversations_list", "conversations_info",
        "conversations_members", "users_list", "users_info", "users_lookupByEmail", "auth_test",
        "chat_getPermalink", "files_list", "files_info", "reactions_get", "team_info",
    }
)
_SLACK_CLIENT: dict[str, Rule] = {
    **{name: "write" for name in _SLACK_WRITES}, **{name: "read" for name in _SLACK_READS},
}
_SLACK_WEBHOOK: dict[str, Rule] = {"send": "write", "send_dict": "write"}
_TWILIO_RESOURCES: dict[str, Rule] = {"create": "write", "list": "read", "stream": "read"}

#: ``(library, role) -> {method: rule}``.
_METHODS: dict[tuple[str, str], dict[str, Rule]] = {
    **{(library, "connection"): _DBAPI_CONNECTION for library in ("sqlite3", "psycopg2", "psycopg", "pymysql")},
    **{(library, "cursor"): _DBAPI_CURSOR for library in ("sqlite3", "psycopg2", "psycopg", "pymysql")},
    ("psycopg2", "pool"): {"getconn": "->connection", "putconn": "pass", "closeall": "pass"},
    ("psycopg", "pool"): {
        "connection": "->connection", "getconn": "->connection", "putconn": "pass",
        "close": "pass", "open": "pass", "wait": "pass",
    },
    ("asyncpg", "connection"): _ASYNCPG_CONNECTION,
    ("asyncpg", "pool"): {**_ASYNCPG_CONNECTION, "acquire": "->connection", "release": "pass"},
    ("sqlalchemy", "engine"): {
        "connect": "->connection", "begin": "->connection", "raw_connection": "->raw_connection",
        "dispose": "pass",
    },
    ("sqlalchemy", "raw_connection"): _DBAPI_CONNECTION,
    ("sqlalchemy", "cursor"): _DBAPI_CURSOR,
    ("sqlalchemy", "connection"): _SQLALCHEMY_CONNECTION,
    ("sqlalchemy", "session_factory"): {"begin": "->session"},
    ("sqlalchemy", "session"): _SQLALCHEMY_SESSION,
    ("sqlalchemy", "query"): _SQLALCHEMY_QUERY,
    ("pymongo", "client"): {
        "get_database": "->database", "get_default_database": "->database",
        "list_database_names": "read", "drop_database": "write", "close": "pass",
    },
    ("pymongo", "database"): {
        "get_collection": "->collection", "list_collection_names": "read",
        "create_collection": "write", "drop_collection": "write",
    },
    ("pymongo", "collection"): _PYMONGO_COLLECTION,
    ("redis", "client"): _REDIS_CLIENT,
    ("redis", "pipeline"): _REDIS_PIPELINE,
    ("subprocess", "process"): _PROCESS,
    ("pathlib", "path"): _PATH,
    ("builtins", "file"): _FILE,
    ("boto3", "session"): {"client": "->client", "resource": "->resource"},
    ("boto3", "collection"): _BOTO3_COLLECTION,
    ("google.cloud.storage", "client"): _GCS_CLIENT,
    ("google.cloud.storage", "bucket"): _GCS_BUCKET,
    ("google.cloud.storage", "blob"): _GCS_BLOB,
    ("google.cloud.storage", "stream"): _FILE,
    ("google.cloud.firestore", "client"): _FIRESTORE_CLIENT,
    ("google.cloud.firestore", "collection"): _FIRESTORE_COLLECTION,
    ("google.cloud.firestore", "query"): _FIRESTORE_QUERY,
    ("google.cloud.firestore", "document"): _FIRESTORE_DOCUMENT,
    ("google.cloud.firestore", "batch"): _FIRESTORE_BATCH,
    ("google.cloud.bigquery", "client"): _BIGQUERY_CLIENT,
    ("vertexai", "agent_engines"): _VERTEX_AGENT_ENGINES,
    ("vertexai", "memories"): _VERTEX_MEMORIES,
    ("google.adk", "memory_bank"): _ADK_MEMORY_BANK,
    ("smtplib", "client"): _SMTP,
    ("slack_sdk", "client"): _SLACK_CLIENT,
    ("slack_sdk", "webhook"): _SLACK_WEBHOOK,
    ("twilio", "messages"): _TWILIO_RESOURCES,
    ("twilio", "calls"): _TWILIO_RESOURCES,
}

#: ``(library, role) -> {attribute: role}``: an attribute that is another
#: object the reader follows (`client.agent_engines.memories`).
ATTRIBUTES: dict[tuple[str, str], dict[str, str]] = {
    ("vertexai", "client"): {"agent_engines": "agent_engines"},
    ("vertexai", "agent_engines"): {"memories": "memories"},
    ("twilio", "client"): {"messages": "messages", "calls": "calls"},
}
#: Roles whose every attribute (and item) names what it holds: a MongoDB
#: client's databases, a database's collections.
NAMED_CHILDREN: dict[tuple[str, str], str] = {
    ("pymongo", "client"): "database",
    ("pymongo", "database"): "collection",
}
#: Roles whose every method only reads what an effect handed back: a query's
#: rows, a MongoDB cursor, a BigQuery job.
RESULTS = "results"
#: Roles that, called, build another object: a `sessionmaker` builds a session.
FACTORIES: dict[tuple[str, str], str] = {
    ("sqlalchemy", "session_factory"): "session",
    ("sqlalchemy", "session"): "session",
}


def method_rule(library: str, role: str, method: str) -> Rule | None:
    """What ``method`` on a ``role`` object of ``library`` does, or None."""

    if role == RESULTS:
        return "pass"
    if role == "statement":
        return "statement" if method in {"execute", "scalar", "scalars"} else "same"
    rules = _METHODS.get((library, role))
    if rules is not None and method in rules:
        return rules[method]
    if library == "boto3":
        return _boto3_rule(role, method)
    return None


def _boto3_rule(role: str, method: str) -> Rule | None:
    if role == "client":
        if method == "get_paginator":
            return "->paginator"
        if method in _BOTO3_PASS:
            return "pass"
        return boto3_operation(method)
    if role == "paginator":
        return "paginate" if method == "paginate" else None
    if role == "resource":
        if method[:1].isupper():
            return f"->{method.lower()}"
        return None
    if role in {"session", "collection"}:
        return None
    # A resource object: `s3.Bucket(name)`, `dynamodb.Table(name)`.
    if method in _BOTO3_RESOURCE_READS:
        return "read"
    if method in _BOTO3_RESOURCE_WRITES:
        return "write"
    return boto3_operation(method)


def boto3_operation(name: str) -> Rule | None:
    """A boto3 operation's direction, read off the verb it starts with."""

    if name in _BOTO3_READS or name.startswith(_BOTO3_READ_PREFIXES):
        return "read"
    if name.startswith(_BOTO3_WRITE_PREFIXES):
        return "write"
    return None


def attribute_role(library: str, role: str, attribute: str) -> str | None:
    """The role of ``attribute`` on a ``role`` object, when it is one."""

    found = ATTRIBUTES.get((library, role), {}).get(attribute)
    if found is not None:
        return found
    if library == "boto3" and role not in {"client", "session", "paginator", "collection"}:
        # `bucket.objects`, `s3.buckets`: a resource collection.
        if attribute.islower() and attribute.endswith("s") and not attribute.startswith("_"):
            return "collection"
    return None


#: The write a successful effect of each family supports, as an effect value.
CLAIM_EFFECTS = {
    ("database", "write"): "write",
    ("filesystem", "write"): "write",
    ("cloud", "write"): "write",
    ("messaging", "write"): "external_communication",
    ("process", "execute"): "code_execution",
}

# -- SQL -----------------------------------------------------------------------

#: Leading keywords of a statement that reads.
SQL_READS = frozenset({"SELECT", "SHOW", "DESCRIBE", "DESC", "EXPLAIN", "VALUES", "TABLE"})
#: Leading keywords of a statement that writes.
SQL_WRITES = frozenset(
    {
        "INSERT", "UPDATE", "DELETE", "REPLACE", "MERGE", "UPSERT", "CREATE", "DROP", "ALTER",
        "TRUNCATE", "GRANT", "REVOKE", "RENAME", "COMMENT",
    }
)
#: Transaction control: no effect of its own.
SQL_CONTROL = frozenset({"BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE", "END", "START"})
#: Every leading keyword a row may print. Any other leading word of a literal
#: is not published: it could be the start of something that is not SQL.
SQL_KEYWORDS = SQL_READS | SQL_WRITES | SQL_CONTROL | frozenset(
    {
        "WITH", "CALL", "EXEC", "EXECUTE", "DO", "SET", "PRAGMA", "COPY", "VACUUM", "ANALYZE",
        "LOCK", "USE", "LOAD", "ATTACH", "DETACH", "REINDEX", "CLUSTER", "LISTEN", "NOTIFY",
        "UNLISTEN", "PREPARE", "DEALLOCATE", "DECLARE", "FETCH", "MOVE", "CLOSE", "REFRESH",
        "DISCARD", "RESET", "CHECKPOINT",
    }
)
#: Functions a statement that reads may call and still only read. Any other
#: call in it (`SELECT setval(...)`, `SELECT pg_terminate_backend(...)`, a
#: user-defined function) may write, so the statement is not read as a read.
SQL_READ_FUNCTIONS = frozenset(
    {
        "abs", "age", "array_agg", "avg", "bool_and", "bool_or", "cast", "ceil", "ceiling",
        "char_length", "character_length", "coalesce", "concat", "concat_ws", "convert", "count",
        "cume_dist", "current_date", "current_timestamp", "date", "date_part", "date_trunc",
        "datetime", "dense_rank", "every", "extract", "first_value", "floor", "format",
        "generate_series", "greatest", "group_concat", "hex", "ifnull", "iif", "instr", "isnull",
        "json_agg", "json_array", "json_build_object", "json_extract", "json_object", "jsonb_agg",
        "jsonb_build_object", "julianday", "lag", "last_value", "lead", "least", "left", "length",
        "lower", "lpad", "ltrim", "max", "md5", "min", "now", "ntile", "nullif", "octet_length",
        "percent_rank", "plainto_tsquery", "position", "printf", "quote", "rank", "regexp_matches",
        "regexp_replace", "repeat", "replace", "reverse", "right", "round", "row_number", "rpad",
        "rtrim", "similarity", "split_part", "stddev", "strftime", "string_agg", "strpos", "substr",
        "substring", "sum", "time", "to_char", "to_date", "to_timestamp", "to_tsquery",
        "to_tsvector", "total", "trim", "ts_rank", "typeof", "unnest", "upper", "variance",
    }
)
#: Words that may stand before a parenthesis in a statement without calling.
_SQL_PARENTHESIZED = frozenset(
    {
        "all", "and", "any", "as", "between", "by", "case", "distinct", "else", "end", "except",
        "exists", "filter", "from", "having", "ilike", "in", "intersect", "is", "join", "lateral",
        "like", "limit", "not", "offset", "on", "or", "over", "select", "then", "union", "using",
        "values", "when", "where", "with", "within",
    }
)
_SQL_CALL = re.compile(r"([A-Za-z_][\w.$]*)\s*\(")
#: A quoted name followed by a parenthesis: `SELECT "setval"('s', 1)` calls.
_SQL_QUOTED_CALL = re.compile(r"[\"`\]]\s*\(")
#: Words that make a `WITH` or `SELECT` statement more than a read.
_SQL_WRITING_WORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|REPLACE|MERGE|UPSERT|CREATE|DROP|ALTER|TRUNCATE|GRANT|REVOKE|INTO)\b",
    re.IGNORECASE,
)
_SQL_COMMENT = re.compile(r"^(?:\s+|--[^\n]*(?:\n|$)|/\*.*?\*/|\()*", re.DOTALL)
_SQL_KEYWORD = re.compile(r"[A-Za-z]+")
_SQL_TABLE = re.compile(
    r"\b(?:FROM|INTO|UPDATE|JOIN|TABLE)\s+(?:IF\s+(?:NOT\s+)?EXISTS\s+)?"
    r"([`\"\[]?[A-Za-z_][\w$]*[`\"\]]?(?:\.[`\"\[]?[A-Za-z_][\w$]*[`\"\]]?)?)(?=[\s,;()]|$)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Statement:
    """What a SQL text establishes.

    ``operation``: ``read``, ``write``, ``unknown``, or None for transaction
    control. ``keyword``: its leading keyword. ``table``: the first table it
    names in literal text. ``open``: text after the keyword is not literal,
    so a further statement could be spliced in.
    """

    operation: str | None
    keyword: str | None
    table: str | None
    open: bool


def sql_operation(head: str, *, complete: bool) -> Statement:
    """Read a SQL statement from its literal ``head``.

    ``complete``: ``head`` is the whole statement. A statement whose leading
    keyword reads is a read only when nothing after the keyword could make it
    write: a complete text with no writing word in it (``SELECT … INTO``
    creates a table) and one statement only. Text the read does not see is
    reported as ``open``.
    """

    text = _SQL_COMMENT.sub("", head, count=1)
    match = _SQL_KEYWORD.match(text)
    if match is None or (not complete and match.end() == len(text)):
        # No keyword, or a keyword the literal may not finish (`SEL` + …).
        return Statement("unknown", None, None, not complete)
    keyword = match.group(0).upper()
    if keyword not in SQL_KEYWORDS:
        return Statement("unknown", None, None, not complete)
    table_match = _SQL_TABLE.search(text)
    table = table_match.group(1).strip("`\"[]") if table_match else None
    if table is not None and table_match is not None and not complete and table_match.end() == len(text):
        # `FROM user` + … may continue the name.
        table = None
    statements = [part for part in text.split(";") if part.strip()]
    if keyword in SQL_WRITES:
        return Statement("write", keyword, table, not complete)
    if keyword in SQL_CONTROL and complete and len(statements) == 1:
        return Statement(None, keyword, None, False)
    if keyword in SQL_READS or keyword == "WITH":
        if complete and len(statements) > 1:
            operations = [sql_operation(part, complete=True) for part in statements]
            written = next((item for item in operations if item.operation == "write"), None)
            if written is not None:
                # Name the statement that writes, not the one that leads.
                return Statement("write", written.keyword, written.table, False)
            if all(item.operation in {"read", None} for item in operations):
                return Statement("read", keyword, table, False)
            return Statement("unknown", keyword, table, False)
        rest = text[match.end():]
        writing = _SQL_WRITING_WORDS.search(rest)
        names = {name.lower() for name in _SQL_CALL.findall(rest)}
        # A qualified name is a user's function unless the catalog's own:
        # `app.count(...)` need not be SQL's `count`.
        called = {
            name
            for name in names
            if (name.rsplit(".", 1)[-1] not in SQL_READ_FUNCTIONS or ("." in name and not name.startswith("pg_catalog.")))
            and name not in _SQL_PARENTHESIZED
            and name != keyword.lower()
        }
        if writing is not None or called or _SQL_QUOTED_CALL.search(rest):
            return Statement("unknown", keyword, table, not complete)
        return Statement("read", keyword, table, not complete)
    return Statement("unknown", keyword, table, not complete)


def file_mode(mode: str) -> str:
    """A file mode string's direction: any of ``w``, ``a``, ``x``, ``+`` writes."""

    return "write" if set(mode) & {"w", "a", "x", "+"} else "read"
