"""What a bound tool reaches: its outbound HTTP calls, read statically (#872).

A comparison row that names only a signature tells the reviewer nothing they
would not see on the first screen of the file. This reader follows one tool
function, and the repository helpers it calls up to :data:`MAX_DEPTH` hops,
and records each outbound HTTP call made through ``requests``, ``httpx``,
``aiohttp`` or ``urllib.request``:

* the method, and the URL as a template whose parts are labelled by source: a
  literal, a tool parameter (``{pr_number}``) or an environment variable
  (``{env OWNER}``);
* request fields: a literal value, the literals a field is chosen from and
  what decides between them, or the parameters it is made from;
* the environment variables a request sends as credentials, by name only;
* for GraphQL, whether the document is a query or a mutation. Transport is not
  effect: a GraphQL query sent over POST reads.

What the code reaches beyond HTTP — a database, a process, a file, a cloud SDK
or a message — is read the same way, through the objects a recognised library
builds (:class:`Handle`) and the tables in
:mod:`agents_shipgate.inputs.tool_effects` (#913).

Nothing is imported or run. A value the read cannot name is labelled as such,
and a call it cannot follow is a named limit with its location, never a guess.
A tool is said to read only when every call it makes was followed and every
outbound call reads.
"""

from __future__ import annotations

import ast
import functools
import hashlib
import json
import os
import re
import string
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from agents_shipgate.core.privacy import (
    is_credential_key,
    looks_like_secret_value,
    redact_text,
    redact_url_credentials,
)
from agents_shipgate.inputs import tool_effects as effect_tables
from agents_shipgate.inputs.python_imports import (
    MODULE_NOT_FOUND,
    ImportResolver,
    PythonModule,
    Resolution,
    reference_spelling,
)
from agents_shipgate.schemas.action_effects import EFFECT_RISK_RANK

#: Helper hops followed from the tool function.
MAX_DEPTH = 3
#: Function bodies read for one tool.
MAX_FRAMES = 64
#: Outbound calls recorded for one tool.
MAX_CALLS = 24
#: Limits recorded for one tool; the rest are counted.
MAX_LIMITS = 12
#: Hops through module constants when evaluating one value.
MAX_CONSTANT_DEPTH = 8
#: Longest literal request-field value printed.
MAX_LITERAL = 64
#: Parts one string template keeps.
MAX_PARTS = 64
#: Recursive calls followed with their own arguments before the body is read
#: once with every parameter unnamed.
MAX_RECURSION = 2

_METHOD_EFFECT = {
    "GET": "read",
    "HEAD": "read",
    "OPTIONS": "read",
    "POST": "write",
    "PUT": "write",
    "PATCH": "write",
    "DELETE": "destructive",
}
_VERBS = {verb.lower(): verb for verb in _METHOD_EFFECT}
_HTTP_LIBRARIES = ("requests", "httpx")
#: Positional parameters after ``url`` for each verb function.
_VERB_POSITIONALS = {
    "get": ("params",),
    "post": ("data", "json"),
    "put": ("data",),
    "patch": ("data",),
}
_CLIENTS = {
    "requests.Session": "requests",
    "requests.session": "requests",
    "requests.sessions.Session": "requests",
    "httpx.Client": "httpx",
    "httpx.AsyncClient": "httpx",
    "aiohttp.ClientSession": "aiohttp",
}
_URLOPEN = frozenset({"urllib.request.urlopen"})
_REQUEST_CLASSES = frozenset({"urllib.request.Request"})
_BASIC_AUTH = frozenset(
    {"requests.auth.HTTPBasicAuth", "httpx.BasicAuth", "aiohttp.BasicAuth"}
)
_ENV_READERS = frozenset({"os.getenv", "os.environ.get", "os.environ.setdefault"})

#: Calls with no effect outside the process. A call is followed, recorded or
#: named as a limit; these are the only ones passed over.
_PURE_BUILTINS = frozenset(
    {
        "abs", "all", "any", "bool", "callable", "dict", "divmod", "enumerate",
        "filter", "float", "format", "frozenset", "getattr", "hasattr", "hash",
        "id", "int", "isinstance", "issubclass", "iter", "len", "list", "map",
        "max", "min", "next", "print", "range", "repr", "reversed", "round",
        "set", "sorted", "str", "sum", "tuple", "type", "zip",
    }
)
_PURE_FUNCTIONS = frozenset(
    {
        "asyncio.sleep", "base64.b64decode", "base64.b64encode",
        "base64.urlsafe_b64decode", "base64.urlsafe_b64encode",
        "datetime.datetime.now", "datetime.datetime.utcnow", "datetime.date.today",
        "datetime.datetime.fromisoformat", "datetime.timedelta",
        "json.dumps", "json.loads", "logging.getLogger", "math.ceil", "math.floor",
        *(
            f"{module}.{name}"
            for module in ("os.path", "posixpath", "ntpath")
            for name in (
                "abspath", "basename", "dirname", "exists", "expanduser", "getsize",
                "isdir", "isfile", "join", "normpath", "realpath", "relpath", "splitext",
            )
        ),
        "pathlib.Path", "pathlib.PurePath", "pathlib.PurePosixPath",
        "re.compile", "re.escape", "re.findall", "re.finditer", "re.fullmatch",
        "re.match", "re.search", "re.split", "re.sub", "re.subn",
        "textwrap.dedent", "time.monotonic", "time.sleep", "time.time",
        "operator.attrgetter", "operator.itemgetter", "typing.cast",
        "urllib.parse.quote", "urllib.parse.quote_plus", "warnings.warn",
        "urllib.parse.unquote", "urllib.parse.urlencode", "urllib.parse.urljoin",
        "urllib.parse.urlparse", "urllib.parse.urlsplit", "uuid.uuid4",
        *_ENV_READERS,
    }
)
#: Methods passed over on any receiver. None of them writes on the types that
#: define them (``dict.get``, ``str.strip``, ``Response.json``,
#: ``Match.group``); on another object the same name at worst reads.
_PURE_METHODS = frozenset(
    {
        "capitalize", "count", "decode", "encode", "endswith", "find", "findall",
        "format", "fullmatch", "get", "group", "groupdict", "groups", "isalnum",
        "isalpha", "isdigit", "isoformat", "items", "join", "json", "keys", "lower",
        "lstrip", "match", "raise_for_status", "rfind", "rsplit", "rstrip", "search",
        "split", "splitlines", "startswith", "strftime", "strip", "sub", "title",
        "total_seconds", "upper", "values",
        # Dates and paths: at worst they read.
        "absolute", "as_posix", "astimezone", "date", "exists", "expanduser",
        "is_dir", "is_file", "isoweekday", "joinpath", "read_bytes", "read_text",
        "relative_to", "resolve", "timestamp", "weekday", "with_name", "with_suffix",
        # A response's own readers.
        "geturl", "getcode", "info", "read", "readlines", "text",
    }
)
#: Pure methods whose result is another object, not plain data.
_OBJECT_METHODS = frozenset(
    {
        "absolute", "astimezone", "date", "expanduser", "finditer", "fullmatch",
        "joinpath", "match", "relative_to", "resolve", "search", "with_name",
        "with_suffix",
    }
)
#: Attributes of a library object that are plain data. A finished process's
#: output and exit status are (#913).
_DATA_ATTRIBUTES = frozenset(
    {
        "content", "headers", "name", "ok", "reason", "returncode", "status", "status_code", "stderr",
        "stdout", "stem", "suffix", "text", "url",
    }
)
#: Pure library calls whose result is an object rather than plain data.
_OBJECT_FUNCTIONS = frozenset(
    {
        "datetime.date.today", "datetime.datetime.fromisoformat", "datetime.datetime.now",
        "datetime.datetime.utcnow", "datetime.timedelta", "logging.getLogger",
        "pathlib.Path", "pathlib.PurePath", "pathlib.PurePosixPath", "re.compile",
        "re.finditer", "re.fullmatch", "re.match", "re.search", "uuid.uuid4",
    }
)
#: Decorators that leave a function doing what its body says.
_INERT_DECORATORS = frozenset(
    {
        "abc.abstractmethod", "agents.function_tool", "functools.cache",
        "functools.lru_cache", "functools.wraps", "typing.overload",
    }
)
_REDACTED = "[REDACTED:sensitive_field]"
#: A bot API's method name (`sendMessage`, `getUpdates`): lower camelCase.
_METHOD_NAME = re.compile(r"[a-z]{2,12}[A-Z][A-Za-z]{1,20}")
#: A query value short and plain enough to print: a lowercase word or a number.
_PLAIN_QUERY_VALUE = re.compile(
    r"(?:[A-Za-z][A-Za-z_-]{0,15}|\d{1,10}|\d{4}-\d{2}-\d{2}(?:-[a-z]{1,12})?"
    r"|[a-z_]{1,16}(?:,[a-z_]{1,16}){1,7})"
)
#: A request-field value plain enough to print: a word without digits.
_PLAIN_FIELD_VALUE = re.compile(r"[A-Za-z][A-Za-z_.-]{0,31}")
#: Words of a field, header or query name that mark it as carrying a secret.
#: Whole words, never substrings: `max_tokens`, `author` and `assignees` are not
#: credentials (#872 review 2).
_SECRET_WORDS = frozenset(
    {
        "accesskey", "apikey", "auth", "authorization", "bearer", "contrasena", "cookie",
        "credential", "credentials", "haslo", "jwt", "kennwort", "motdepasse", "otp",
        "parola", "pass", "passcode", "passphrase", "passwd", "password", "passwort", "pin",
        "privatekey", "pw", "pwd", "secret", "secretkey", "senha", "session", "sessionid",
        "sid", "sig", "signature", "token", "wachtwoord",
    }
)
#: Words that make a following `key` a secret: `X-Api-Key`, `subscription_key`.
#: `Idempotency-Key` and `sort_key` are not.
_KEY_QUALIFIERS = frozenset(
    {
        "access", "account", "api", "app", "application", "auth", "client", "consumer", "developer",
        "encryption", "function", "functions", "license", "master", "private", "secret",
        "service", "signing", "subscription", "x",
    }
)
#: Callees that run a function passed to them, and where it is passed.
_CALLABLE_POSITIONS = {
    "filter": (0,),
    "functools.partial": (0,),
    "functools.reduce": (0,),
    "itertools.dropwhile": (0,),
    "itertools.filterfalse": (0,),
    "itertools.starmap": (0,),
    "itertools.takewhile": (0,),
    "map": (0,),
    "re.sub": (1,),
    "re.subn": (1,),
    "sub": (0,),
    "subn": (0,),
}
_CALLABLE_KEYWORDS = frozenset(
    {
        "auth", "callback", "default", "event_hooks", "func", "function", "hook", "hooks",
        "key", "mounts", "object_hook", "object_pairs_hook", "repl", "target", "trace_configs",
        "transport",
    }
)
#: Marks an outbound call or a client or request built here: its keywords that
#: take a function (`hooks=`, `auth=`) are read as strictly as a passed-over
#: call's (#872 review 3).
_SENDS = "<sends>"
#: A client's settings that only tune transport. Any other attribute set on a
#: client (`s.request = fn`) replaces what it does.
_CLIENT_SETTINGS = frozenset(
    {"cert", "cookies", "headers", "max_redirects", "params", "proxies", "stream", "timeout", "trust_env", "verify"}
)
#: Transport constructors that only retry or pool, given plain values.
_TRANSPORTS = frozenset(
    {
        "httpx.AsyncHTTPTransport", "httpx.HTTPTransport", "requests.adapters.HTTPAdapter",
        "urllib3.Retry", "urllib3.util.Retry", "urllib3.util.retry.Retry",
    }
)
#: Pure library calls whose result is text or JSON whatever they are given.
_TEXT_FUNCTION_PREFIXES = ("base64.", "ntpath.", "os.path.", "posixpath.", "urllib.parse.")
_TEXT_FUNCTIONS = frozenset(
    {"json.dumps", "json.loads", "math.ceil", "math.floor", "re.escape", "textwrap.dedent", "time.monotonic", "time.time"}
)
#: Hosts whose URL path is itself the credential: a webhook or bot URL.
_CAPABILITY_HOSTS = (
    "api.telegram.org", "discord.com", "discordapp.com", "hooks.slack.com",
    "hooks.zapier.com", "outlook.office.com",
)
_CAPABILITY_HOST_SUFFIXES = (".m.pipedream.net", ".webhook.office.com")
#: Fixed path words kept on a capability URL.
_CAPABILITY_WORDS = frozenset(
    {"api", "catch", "hooks", "IncomingWebhook", "services", "webhook", "webhookb2", "webhooks"}
)
#: Methods passed over only on a value read as a string.
_STRING_METHODS = frozenset({"replace", "zfill", "ljust", "rjust", "center"})
#: Methods that change a container, passed over only on a list, dict or set
#: the function itself built: on another object ``append`` or ``update`` may
#: store something (#872 review).
_CONTAINER_METHODS = frozenset(
    {"add", "append", "clear", "copy", "extend", "insert", "pop", "remove", "setdefault", "sort", "update"}
)
_CONTAINER_BUILDERS = frozenset({"dict", "list", "set", "sorted", "tuple"})
#: The builtins module's names (Python 3.12 and later), written out: the
#: scanner imports no `builtins` (tests/test_adapter_static_only.py).
_BUILTIN_NAMES = frozenset(
    {
        "ArithmeticError", "AssertionError", "AttributeError", "BaseException", "BaseExceptionGroup",
        "BlockingIOError", "BrokenPipeError", "BufferError", "BytesWarning", "ChildProcessError",
        "ConnectionAbortedError", "ConnectionError", "ConnectionRefusedError", "ConnectionResetError",
        "DeprecationWarning", "EOFError", "Ellipsis", "EncodingWarning", "EnvironmentError", "Exception",
        "ExceptionGroup", "False", "FileExistsError", "FileNotFoundError", "FloatingPointError",
        "FutureWarning", "GeneratorExit", "IOError", "ImportError", "ImportWarning", "IndentationError",
        "IndexError", "InterruptedError", "IsADirectoryError", "KeyError", "KeyboardInterrupt",
        "LookupError", "MemoryError", "ModuleNotFoundError", "NameError", "None", "NotADirectoryError",
        "NotImplemented", "NotImplementedError", "OSError", "OverflowError", "PendingDeprecationWarning",
        "PermissionError", "ProcessLookupError", "PythonFinalizationError", "RecursionError",
        "ReferenceError", "ResourceWarning", "RuntimeError", "RuntimeWarning", "StopAsyncIteration",
        "StopIteration", "SyntaxError", "SyntaxWarning", "SystemError", "SystemExit", "TabError",
        "TimeoutError", "True", "TypeError", "UnboundLocalError", "UnicodeDecodeError",
        "UnicodeEncodeError", "UnicodeError", "UnicodeTranslateError", "UnicodeWarning", "UserWarning",
        "ValueError", "Warning", "ZeroDivisionError", "_IncompleteInputError", "__build_class__",
        "__debug__", "__doc__", "__import__", "__loader__", "__name__", "__package__", "__spec__", "abs",
        "aiter", "all", "anext", "any", "ascii", "bin", "bool", "breakpoint", "bytearray", "bytes",
        "callable", "chr", "classmethod", "compile", "complex", "copyright", "credits", "delattr", "dict",
        "dir", "divmod", "enumerate", "eval", "exec", "exit", "filter", "float", "format", "frozenset",
        "getattr", "globals", "hasattr", "hash", "help", "hex", "id", "input", "int", "isinstance",
        "issubclass", "iter", "len", "license", "list", "locals", "map", "max", "memoryview", "min", "next",
        "object", "oct", "open", "ord", "pow", "print", "property", "quit", "range", "repr", "reversed",
        "round", "set", "setattr", "slice", "sorted", "staticmethod", "str", "sum", "super", "tuple",
        "type", "vars", "zip"
    }
)


# -- values --------------------------------------------------------------------


@dataclass(frozen=True)
class Lit:
    """A literal: str, int, float, bool or None."""

    value: Any


@dataclass(frozen=True)
class Tpl:
    """A string built from parts, each a :class:`Lit` or an unnamed value."""

    parts: tuple[Any, ...]


@dataclass(frozen=True)
class Rec:
    """A dict with literal keys. ``open``: other keys may be added.

    ``shared``: a module-level dict. Any code in the process may change it, by
    routes a static read cannot bound (a helper two calls away, `*args`, an
    accessor, a loop), so a value read out of it is never taken as written
    (#872 review 9).
    """

    fields: tuple[tuple[str, Any], ...]
    open: bool = False
    shared: bool = False

    def get(self, key: str) -> Any | None:
        for name, value in self.fields:
            if name == key:
                return value
        return None


@dataclass(frozen=True)
class Seq:
    """A list or tuple display."""

    items: tuple[Any, ...]


@dataclass(frozen=True)
class Alt:
    """One of several values; ``deciders`` are the parameters that choose."""

    options: tuple[Any, ...]
    deciders: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Op:
    """A value the read does not name exactly, and the inputs it is made from.

    ``exact``: the value is exactly its one parameter or environment variable.
    ``literal``: it is made from literals alone. ``data``: it is plain data —
    an environment value, a JSON value the model supplies, or made only from
    such values — whose methods cannot reach outside the process. ``what``
    names a value the tool cannot see into, such as a parameter of an
    enclosing factory.
    """

    params: frozenset[str] = frozenset()
    envs: frozenset[str] = frozenset()
    exact: bool = False
    literal: bool = False
    what: str | None = None
    data: bool = False
    #: An object a known library returned (a response, a regex match, a
    #: date): its read-only methods reach nothing outside the process.
    inert: bool = False


@dataclass(frozen=True)
class Lib:
    """An object a library outside the repository provides, by dotted name."""

    dotted: str


@dataclass(frozen=True)
class Client:
    """An HTTP client or session, with its base URL and default headers."""

    library: str
    base_url: Any = None
    headers: Any = None
    auth: Any = None
    params: Any = None


@dataclass(frozen=True)
class Request:
    """``urllib.request.Request(url, data=..., headers=..., method=...)``."""

    url: Any
    data: Any
    headers: Any
    method: Any


@dataclass(frozen=True)
class Handle:
    """An object a recognised library built, followed by its methods (#913).

    A database connection, a cursor, a cloud client, a path, an open file. The
    library is its import identity, never a spelling. ``target`` is the value
    naming what it reaches where one is known (a path, a bucket, a
    collection); ``extra`` carries a role's own detail (a boto3 paginator's
    operation, a SQLAlchemy statement's keyword). ``built``: the function
    that constructed it, ``0`` for module level, so a call through one built
    elsewhere can be told apart. ``credentials`` and ``host`` are read off the
    construction and published by name only.
    """

    family: str
    library: str
    role: str
    service: str | None = None
    target: Any = None
    extra: Any = None
    built: int = 0
    credentials: tuple[tuple[tuple[str, Any], ...], ...] = ()
    host: tuple[str, ...] = ()


class Func:
    """A repository function, and the frame of the function that encloses it."""

    __slots__ = ("module", "node", "enclosing")

    def __init__(
        self,
        module: PythonModule,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        enclosing: _Frame | None = None,
    ) -> None:
        self.module = module
        self.node = node
        self.enclosing = enclosing

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, Func)
            and other.node is self.node
            and other.enclosing is self.enclosing
        )

    def __hash__(self) -> int:
        return hash((id(self.node), id(self.enclosing)))


class _Class:
    """A plain class the module defines, which an instance can be read from."""

    __slots__ = ("module", "node")

    def __init__(self, module: PythonModule, node: ast.ClassDef) -> None:
        self.module = module
        self.node = node

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Class) and other.node is self.node

    def __hash__(self) -> int:
        return hash(("class", id(self.node)))


class _Instance(_Class):
    """An instance of a :class:`_Class` built with no arguments."""

    __slots__ = ()

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Instance) and other.node is self.node

    def __hash__(self) -> int:
        return hash(("instance", id(self.node)))


_UNKNOWN = Op()


def _sources(value: Any) -> tuple[frozenset[str], frozenset[str]]:
    """The parameters and environment variables a value is made from."""

    params: set[str] = set()
    envs: set[str] = set()
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, Op):
            params.update(item.params)
            envs.update(item.envs)
        elif isinstance(item, Tpl):
            stack.extend(item.parts)
        elif isinstance(item, Rec):
            stack.extend(value for _, value in item.fields)
        elif isinstance(item, Seq):
            stack.extend(item.items)
        elif isinstance(item, Alt):
            params.update(item.deciders)
            stack.extend(item.options)
        elif isinstance(item, Request):
            stack.extend((item.url, item.data, item.headers, item.method))
        elif isinstance(item, Client):
            stack.extend((item.base_url, item.headers))
        elif isinstance(item, Handle):
            # A path made from a model-supplied name is made from it (#913).
            stack.append(item.target)
    return frozenset(params), frozenset(envs)


def _literal_only(value: Any) -> bool:
    if isinstance(value, Lit):
        return True
    if isinstance(value, Op):
        return value.literal
    if isinstance(value, Tpl):
        return all(_literal_only(part) for part in value.parts)
    if isinstance(value, Rec):
        return not value.open and all(_literal_only(item) for _, item in value.fields)
    if isinstance(value, Seq):
        return all(_literal_only(item) for item in value.items)
    if isinstance(value, Alt):
        return not value.deciders and all(_literal_only(item) for item in value.options)
    return False


def _derived(
    *values: Any,
    what: str | None = None,
    result: bool = False,
    data: bool | None = None,
    inert: bool | None = None,
    literal: bool | None = None,
) -> Op:
    """An unnamed value made from ``values``.

    ``result``: what a call returned. It is never a literal, however literal
    its arguments (a secret a client fetched is not written in the source),
    and it is plain data or an inert object only when said so.
    """

    params: frozenset[str] = frozenset()
    envs: frozenset[str] = frozenset()
    for value in values:
        more_params, more_envs = _sources(value)
        params |= more_params
        envs |= more_envs
    if result:
        data = bool(data)
        inert = bool(inert)
    if literal is None:
        literal = not result and bool(values) and all(_literal_only(value) for value in values)
    return Op(
        params=params,
        envs=envs,
        literal=literal,
        what=what,
        data=(bool(values) and all(_is_data(value) for value in values)) if data is None else data,
        inert=(bool(values) and all(_is_quiet(value) for value in values)) if inert is None else inert,
    )


def _is_quiet(value: Any) -> bool:
    """Plain data, or an object whose read-only methods reach nothing outside."""

    if isinstance(value, Op) and value.inert:
        return True
    if isinstance(value, Handle) and value.role == effect_tables.RESULTS:
        # What an effect handed back: its methods only read it (#913).
        return True
    if isinstance(value, Alt):
        return all(_is_quiet(option) for option in value.options)
    return _is_data(value)


def _is_data(value: Any) -> bool:
    """Plain data: strings, numbers, and lists or dicts of them."""

    if isinstance(value, Lit | Tpl):
        return True
    if isinstance(value, Op):
        return value.data or value.literal
    if isinstance(value, Alt):
        return all(_is_data(option) for option in value.options)
    if isinstance(value, Seq):
        return all(_is_data(item) for item in value.items)
    if isinstance(value, Rec):
        return not value.open and all(_is_data(item) for _, item in value.fields)
    return False


#: Annotation names a model-supplied argument can have and still be the plain
#: JSON value the framework decoded.
_JSON_ANNOTATIONS = frozenset(
    {
        "Any", "Dict", "List", "Literal", "Mapping", "Optional", "Sequence", "Tuple",
        "Union", "bool", "dict", "float", "int", "list", "str", "tuple",
    }
)


def _json_annotation(node: ast.AST | None, *, literal: bool = False) -> bool:
    if node is None:
        return True
    if isinstance(node, ast.Constant):
        # A string outside `Literal[...]` is a forward reference to a class.
        return node.value is None or (literal and isinstance(node.value, str | int | bool))
    if isinstance(node, ast.Name):
        return node.id in _JSON_ANNOTATIONS
    if isinstance(node, ast.Attribute):
        return node.attr in _JSON_ANNOTATIONS
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _json_annotation(node.left, literal=literal) and _json_annotation(node.right, literal=literal)
    if isinstance(node, ast.Subscript):
        inside = literal or (
            isinstance(node.value, ast.Name | ast.Attribute)
            and (node.value.id if isinstance(node.value, ast.Name) else node.value.attr) == "Literal"
        )
        return _json_annotation(node.value) and _json_annotation(node.slice, literal=inside)
    if isinstance(node, ast.Tuple):
        return all(_json_annotation(item, literal=literal) for item in node.elts)
    return False


def _text(value: Any) -> Any:
    """``value`` as a string part: a template stays one, anything else is a part."""

    if isinstance(value, Lit) and not isinstance(value.value, str):
        return Lit(str(value.value)) if value.value is not None else _derived(value)
    return value


def _join(parts: list[Any]) -> Any:
    flat: list[Any] = []
    for part in parts:
        part = _text(part)
        flat.extend(part.parts if isinstance(part, Tpl) else [part])
    merged: list[Any] = []
    for part in flat:
        if merged and isinstance(part, Lit) and isinstance(merged[-1], Lit):
            merged[-1] = Lit(merged[-1].value + part.value)
        else:
            merged.append(part)
    if len(merged) == 1 and isinstance(merged[0], Lit):
        return merged[0]
    if not merged:
        return Lit("")
    if len(merged) > MAX_PARTS:
        # A string built by doubling grows exponentially; past the bound it
        # is only what it is made from.
        return _derived(*merged)
    return Tpl(tuple(merged))


def _is_text(value: Any) -> bool:
    return (isinstance(value, Lit) and isinstance(value.value, str)) or isinstance(value, Tpl)


def _alt(options: list[Any], deciders: frozenset[str] = frozenset()) -> Any:
    unique: list[Any] = []
    for option in options:
        if isinstance(option, Alt):
            deciders |= option.deciders
            items: tuple[Any, ...] = option.options
        else:
            items = (option,)
        for item in items:
            if item not in unique:
                unique.append(item)
    if len(unique) == 1 and not deciders:
        return unique[0]
    return Alt(tuple(unique), deciders)


# -- frames --------------------------------------------------------------------


@dataclass(eq=False)
class _Frame:
    """One function body being read, with the values its parameters hold."""

    module: PythonModule
    node: ast.FunctionDef | ast.AsyncFunctionDef | None
    args: dict[str, Any]
    enclosing: _Frame | None = None
    via: tuple[str, ...] = ()
    depth: int = 0
    local: dict[str, list[tuple[str, ast.AST, ast.AST]]] = field(default_factory=dict)
    mutations: dict[str, list[ast.AST]] = field(default_factory=dict)
    conditions: dict[int, list[ast.expr]] = field(default_factory=dict)
    raised: set[int] = field(default_factory=set)
    calls: list[ast.Call] = field(default_factory=list)
    nested: list[ast.FunctionDef | ast.AsyncFunctionDef] = field(default_factory=list)
    referenced: set[str] = field(default_factory=set)
    evaluating: set[str] = field(default_factory=set)
    partial: dict[str, list[Any]] = field(default_factory=dict)
    cache: dict[str, Any] = field(default_factory=dict)
    #: ``id(Name) -> iterable``: a name a comprehension binds, read inside it.
    comprehension_names: dict[int, ast.expr] = field(default_factory=dict)
    #: Item stores and deletes: ``(statement, the object stored into)``.
    stores: list[tuple[ast.AST, ast.expr]] = field(default_factory=list)
    #: Loads of each name, by (line, column), for flow checks.
    loads: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    #: Attribute stores: ``(statement, target)``.
    attribute_stores: list[tuple[ast.AST, ast.Attribute]] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.node.name if self.node is not None else "<module>"

    def binds(self, name: str) -> bool:
        return name in self.args or name in self.local


def _scan(frame: _Frame) -> None:
    """Record the frame's own bindings, mutations, calls and conditions.

    Nested functions and classes are their own scopes; a comprehension or
    lambda body is read as part of this one, since it runs here.
    """

    node = frame.node
    assert node is not None

    def bind(name: str, kind: str, value: ast.AST, statement: ast.AST) -> None:
        frame.local.setdefault(name, []).append((kind, value, statement))

    def visit(item: ast.AST, tests: list[ast.expr], comprehension: dict[str, ast.expr]) -> None:
        if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
            # Decorators and defaults run where the function is defined.
            for part in [*item.decorator_list, *item.args.defaults, *item.args.kw_defaults]:
                if part is not None:
                    visit(part, tests, comprehension)
            bind(item.name, "def", item, item)
            frame.nested.append(item)
            return
        if isinstance(item, ast.ClassDef):
            # A class body runs where the class is defined; its methods do not.
            bind(item.name, "opaque", item, item)
            for part in [*item.decorator_list, *item.bases, *(k.value for k in item.keywords)]:
                visit(part, tests, comprehension)
            for child in item.body:
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                    for part in [*child.decorator_list, *child.args.defaults, *child.args.kw_defaults]:
                        if part is not None:
                            visit(part, tests, comprehension)
                else:
                    visit(child, tests, comprehension)
            return
        if isinstance(item, ast.ListComp | ast.SetComp | ast.GeneratorExp | ast.DictComp):
            inner = dict(comprehension)
            for generator in item.generators:
                visit(generator.iter, tests, inner)
                for name in _names(generator.target):
                    inner[name] = generator.iter
                for condition in generator.ifs:
                    visit(condition, tests, inner)
            for part in (
                [item.key, item.value] if isinstance(item, ast.DictComp) else [item.elt]
            ):
                visit(part, tests, inner)
            return
        if isinstance(item, ast.Raise):
            for part in (item.exc, item.cause):
                if isinstance(part, ast.Call):
                    frame.raised.add(id(part))
        if isinstance(item, ast.Assign):
            for target in item.targets:
                _target(target, item.value, item, bind, frame)
        elif isinstance(item, ast.AnnAssign) and item.value is not None:
            _target(item.target, item.value, item, bind, frame)
        elif isinstance(item, ast.AugAssign):
            if isinstance(item.target, ast.Name):
                bind(item.target.id, "aug", item, item)
            else:
                _target(item.target, item.value, item, bind, frame)
        elif isinstance(item, ast.For | ast.AsyncFor):
            for name in _names(item.target):
                bind(name, "iter", item.iter, item)
        elif isinstance(item, ast.With | ast.AsyncWith):
            for with_item in item.items:
                if isinstance(with_item.optional_vars, ast.Name):
                    bind(with_item.optional_vars.id, "value", with_item.context_expr, item)
                elif with_item.optional_vars is not None:
                    for name in _names(with_item.optional_vars):
                        bind(name, "opaque", with_item.context_expr, item)
        elif isinstance(item, ast.ExceptHandler) and item.name:
            bind(item.name, "opaque", item, item)
        elif isinstance(item, ast.NamedExpr) and isinstance(item.target, ast.Name):
            bind(item.target.id, "value", item.value, item)
        elif isinstance(item, ast.Import | ast.ImportFrom):
            for alias in item.names:
                if alias.name != "*":
                    bind(alias.asname or alias.name.split(".", 1)[0], "import", alias, item)
        elif isinstance(item, ast.Global | ast.Nonlocal):
            kind = "global" if isinstance(item, ast.Global) else "nonlocal"
            for name in item.names:
                bind(name, kind, item, item)
        elif isinstance(item, ast.Delete):
            for target in item.targets:
                if isinstance(target, ast.Subscript):
                    frame.stores.append((item, target.value))
                    if isinstance(target.value, ast.Name):
                        frame.mutations.setdefault(target.value.id, []).append(item)
        elif isinstance(item, ast.MatchAs | ast.MatchStar) and item.name:
            bind(item.name, "opaque", item, item)
        elif isinstance(item, ast.MatchMapping) and item.rest:
            bind(item.rest, "opaque", item, item)
        elif isinstance(item, ast.Call):
            if tests:
                frame.conditions[id(item)] = list(tests)
            root = _root_name(item.func.value) if isinstance(item.func, ast.Attribute) else None
            if root is not None:
                frame.mutations.setdefault(root, []).append(item)
        elif isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load):
            frame.referenced.add(item.id)
            frame.loads.setdefault(item.id, []).append((item.lineno, item.col_offset))
            if item.id in comprehension:
                frame.comprehension_names[id(item)] = comprehension[item.id]
        if isinstance(item, ast.stmt | ast.NamedExpr) and tests:
            frame.conditions[id(item)] = list(tests)
        if isinstance(item, ast.If | ast.While):
            visit(item.test, tests, comprehension)
            for child in item.body:
                visit(child, [*tests, item.test], comprehension)
            for child in item.orelse:
                visit(child, [*tests, item.test], comprehension)
            return
        if isinstance(item, ast.IfExp):
            visit(item.test, tests, comprehension)
            visit(item.body, [*tests, item.test], comprehension)
            visit(item.orelse, [*tests, item.test], comprehension)
            return
        if isinstance(item, ast.Match):
            visit(item.subject, tests, comprehension)
            for case in item.cases:
                visit(case.pattern, [*tests, item.subject], comprehension)
                if case.guard is not None:
                    visit(case.guard, [*tests, item.subject], comprehension)
                for child in case.body:
                    visit(child, [*tests, item.subject], comprehension)
            return
        for child in ast.iter_child_nodes(item):
            visit(child, tests, comprehension)
        if isinstance(item, ast.Call):
            # After its arguments and receiver: an inner call is classified
            # before the call made on what it returns.
            frame.calls.append(item)

    for statement in node.body:
        visit(statement, [], {})


def _target(
    target: ast.AST,
    value: ast.expr,
    statement: ast.AST,
    bind: Any,
    frame: _Frame,
) -> None:
    if isinstance(target, ast.Name):
        bind(target.id, "value", value, statement)
    elif isinstance(target, ast.Tuple | ast.List):
        for name in _names(target):
            bind(name, "unpacked", value, statement)
    elif isinstance(target, ast.Subscript):
        frame.stores.append((statement, target.value))
        root = _root_name(target.value)
        if root is not None:
            frame.mutations.setdefault(root, []).append(statement)
    elif isinstance(target, ast.Attribute):
        frame.attribute_stores.append((statement, target))
        root = _root_name(target.value)
        if root is not None:
            frame.mutations.setdefault(root, []).append(statement)
    elif isinstance(target, ast.Starred):
        _target(target.value, value, statement, bind, frame)


def _attribute_name(node: ast.AST) -> str | None:
    return node.attr if isinstance(node, ast.Attribute) else None


def _root_name(node: ast.AST) -> str | None:
    while isinstance(node, ast.Attribute | ast.Subscript):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _names(target: ast.AST) -> list[str]:
    return [
        node.id
        for node in ast.walk(target)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    ]


def _module_library(module: PythonModule, name: str) -> str | None:
    """The dotted library name a module-level import binds to ``name``.

    Only one unconditional import binding counts. A relative import is the
    repository's own code.
    """

    bindings = module.bindings.get(name, [])
    if len(bindings) != 1 or not bindings[0].top_level:
        return None
    alias, statement = bindings[0].node, bindings[0].statement
    return _import_dotted(alias, statement)


#: Methods that change what an instance's attribute holds, or how it is read.
_INSTANCE_HOOKS = frozenset(
    {
        "__init__", "__new__", "__post_init__", "__getattr__", "__getattribute__",
        "__setattr__", "__init_subclass__", "__set_name__", "__class_getitem__",
    }
)


def _module_class(module: PythonModule, name: str) -> ast.ClassDef | None:
    """The plain class ``name`` is, when the module binds it once to one (#910).

    Plain: no base but ``object``, no metaclass or other class keyword, no
    decorator but the standard library's ``dataclass``, no method that builds,
    reads or sets an instance's attributes, and no method storing on ``self``.
    An instance of such a class built with no arguments holds the class body's
    defaults. Anything else — a pydantic ``BaseSettings`` reads the environment
    by field name — is not read.
    """

    bindings = module.bindings.get(name, [])
    if len(bindings) != 1 or not bindings[0].top_level or module.star_import:
        return None
    node = bindings[0].node
    if not isinstance(node, ast.ClassDef) or node.keywords:
        return None
    if any(not (isinstance(base, ast.Name) and base.id == "object") for base in node.bases):
        return None
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        spelling = reference_spelling(target)
        head = spelling.split(".", 1)[0] if spelling else None
        library = _module_library(module, head) if head else None
        if library is None or f"{library}{spelling[len(head):]}" != "dataclasses.dataclass":
            return None
    for statement in node.body:
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
            if statement.name in _INSTANCE_HOOKS:
                return None
            for item in ast.walk(statement):
                if (
                    isinstance(item, ast.Attribute)
                    and isinstance(item.ctx, ast.Store | ast.Del)
                    and isinstance(item.value, ast.Name)
                    and item.value.id == "self"
                ):
                    return None
    return node


def _import_dotted(alias: ast.AST, statement: ast.AST) -> str | None:
    if not isinstance(alias, ast.alias):
        return None
    if isinstance(statement, ast.Import):
        return alias.name if alias.asname else alias.name.split(".", 1)[0]
    if isinstance(statement, ast.ImportFrom) and statement.module and not statement.level:
        return f"{statement.module}.{alias.name}"
    return None


# -- the reader ----------------------------------------------------------------


class _Reach:
    def __init__(
        self,
        resolver: ImportResolver,
        mutated: dict[str, str] | None = None,
        whole: dict[str, str] | None = None,
        library: dict[str, str] | None = None,
        *,
        instances: bool = False,
    ) -> None:
        self.resolver = resolver
        #: Read an attribute of a plain class instance built with no arguments
        #: as the class body's default (#910): ``settings.url`` after
        #: ``settings = Settings()``. Only an object binding's identity reads
        #: it; a tool's reach does not.
        self.instances = instances
        #: Follow the objects a recognised library builds and name their
        #: effects (#913). An object binding's identity reads values the way
        #: it did before: it names no effect.
        self.effects_enabled = not instances
        #: Effects beyond HTTP the tool's code reaches, and those past
        #: :data:`MAX_CALLS`, which still count for the claims.
        self.effects: list[dict[str, Any]] = []
        self.dropped_effects: list[dict[str, Any]] = []
        #: ``(id(module), name) -> value``: module globals read for identity.
        self.globals: dict[tuple[int, str], Any] = {}
        #: ``"json.dumps" -> "file:line"``: library attributes stored into.
        self.library = library or {}
        #: The attribute stored last through an object the scan does not
        #: follow, by name (`http.get` -> `get`).
        self.unseen_segments: dict[str, str] = {}
        holders = _holders(self.library)
        for key, where in self.library.items():
            if key.startswith("unseen:") and (not key.startswith("unseen:^") or _held_store(key[7:], holders)):
                self.unseen_segments.setdefault(key[7:].rsplit(".", 1)[-1], where)
        #: Method names stored into on a library object (`Path.read_text`).
        self.replaced_methods = {
            key.rsplit(".", 1)[-1]: where
            for key, where in self.library.items()
            if not key.endswith("*")
            and not key.startswith(("kept:", "unseen:", "class:", "via:"))
            # A method replaced on a class (`Path.read_text`), not a
            # function stored on a module (`config.get = cached`).
            and len(parts := key.split(".")) >= 2
            and parts[-2][:1].isupper()
        }
        #: ``name -> "file:line"``: names stored under somewhere in the scope.
        self.mutated = mutated or {}
        #: ``name -> "file:line"``: containers or modules changed under names
        #: the read cannot see.
        self.whole = whole or {}
        self.calls: list[dict[str, Any]] = []
        self.limits: list[dict[str, str]] = []
        self.limit_count = 0
        self.frames = 0
        self.walked: set[tuple[Any, ...]] = set()
        self.stack: list[int] = []
        self.returns: dict[tuple[Any, ...], Any] = {}
        self.constants = 0
        self.module_frames: dict[int, _Frame] = {}
        self.names: dict[tuple[int, str], Any] = {}
        self.truncated = False
        #: Effects of outbound calls past :data:`MAX_CALLS`, never dropped.
        self.dropped: list[dict[str, Any]] = []
        self.recursions: dict[int, int] = {}
        #: Calls already named as limits: a method called on what one returns
        #: is part of the same unread chain.
        self.limited: set[int] = set()
        #: Module-level constructions and modules whose configuration was read.
        self.configured: set[tuple[int, int]] = set()
        self.patched: set[int] = set()

    # -- limits ------------------------------------------------------------

    def limit(self, frame: _Frame, node: ast.AST, why: str) -> None:
        self.limited.add(id(node))
        self.limit_count += 1
        entry = {"at": f"{frame.module.ref}:{getattr(node, 'lineno', 0)}", "why": why}
        if entry not in self.limits and len(self.limits) < MAX_LIMITS:
            self.limits.append(entry)

    # -- frames ------------------------------------------------------------

    def frame(
        self,
        func: Func,
        args: dict[str, Any],
        *,
        via: tuple[str, ...],
        depth: int,
    ) -> _Frame:
        frame = _Frame(
            module=func.module,
            node=func.node,
            args=args,
            enclosing=func.enclosing,
            via=via,
            depth=depth,
        )
        _scan(frame)
        return frame

    def module_frame(self, module: PythonModule) -> _Frame:
        frame = self.module_frames.get(id(module))
        if frame is None:
            frame = _Frame(module=module, node=None, args={})
            self.module_frames[id(module)] = frame
        return frame

    def bind(
        self,
        func: Func,
        call: ast.Call | None,
        caller: _Frame | None,
        *,
        defaults: bool = True,
    ) -> dict[str, Any]:
        """Parameter values for one call of ``func``; unmatched ones are unnamed."""

        arguments = func.node.args
        positional = [*arguments.posonlyargs, *arguments.args]
        values: dict[str, Any] = {}
        default_values = dict(
            zip(
                [param.arg for param in positional[len(positional) - len(arguments.defaults):]],
                arguments.defaults,
                strict=True,
            )
        )
        default_values.update(
            {
                param.arg: default
                for param, default in zip(arguments.kwonlyargs, arguments.kw_defaults, strict=True)
                if default is not None
            }
        )
        spread = False
        if call is not None and caller is not None:
            for index, arg in enumerate(call.args):
                if isinstance(arg, ast.Starred):
                    spread = True
                    break
                if index < len(positional):
                    values[positional[index].arg] = self.value(arg, caller)
            for keyword in call.keywords:
                if keyword.arg is None:
                    spread = True
                elif keyword.arg not in values:
                    values[keyword.arg] = self.value(keyword.value, caller)
        module = self.module_frame(func.module)
        for param in [*positional, *arguments.kwonlyargs]:
            if param.arg in values:
                continue
            if spread:
                values[param.arg] = Op(what=f"parameter {param.arg} of {func.node.name}")
            elif param.arg in default_values and defaults:
                values[param.arg] = self.value(default_values[param.arg], module)
            else:
                values[param.arg] = Op(what=f"parameter {param.arg} of {func.node.name}")
        for extra in (arguments.vararg, arguments.kwarg):
            if extra is not None:
                values[extra.arg] = Op(what=f"parameter {extra.arg} of {func.node.name}")
        return values

    # -- walking -----------------------------------------------------------

    def walk(self, frame: _Frame) -> None:
        key = (id(frame.node), id(frame.enclosing), tuple(sorted(frame.args.items(), key=repr)))
        if key in self.walked:
            return
        if id(frame.node) in self.stack:
            # Recursion with other arguments: follow it twice as called, then
            # read the body once more with every parameter unnamed (defaults
            # too), which covers whatever else it recurses with.
            entries = self.recursions.get(id(frame.node), 0)
            self.recursions[id(frame.node)] = entries + 1
            if entries >= MAX_RECURSION:
                widened = (id(frame.node), id(frame.enclosing), "*")
                if widened in self.walked or frame.node is None:
                    return
                self.walked.add(widened)
                func = Func(frame.module, frame.node, frame.enclosing)
                frame = self.frame(
                    func, self.bind(func, None, None, defaults=False), via=frame.via, depth=frame.depth
                )
                key = widened
        if self.frames >= MAX_FRAMES:
            if not self.truncated:
                self.truncated = True
                self.limit(frame, frame.node or frame.module.tree, (
                    f"reading {frame.name} would read more than {MAX_FRAMES} function bodies"
                ))
            return
        self.frames += 1
        self.walked.add(key)
        self.stack.append(id(frame.node))
        try:
            self.patches(frame.module)
            self.decorators(frame)
            for call in frame.calls:
                self.classify(call, frame)
            for statement, attribute in frame.attribute_stores:
                self.attribute_store(statement, attribute, frame)
            for statement, target in frame.stores:
                stored = self.value(target, frame)
                if not (
                    isinstance(stored, Rec | Seq)
                    or _is_data(stored)
                    or self._container(target, stored, frame)
                ):
                    self.limit(
                        frame,
                        statement,
                        f"stores into {_spelling(target) or 'an object'}[…], which is not read",
                    )
            for nested in frame.nested:
                if nested.name in frame.referenced and not self._only_called(frame, nested.name):
                    # Handed on rather than called here: it may run with
                    # arguments this read does not see.
                    func = Func(frame.module, nested, frame)
                    self.walk(
                        self.frame(func, self.bind(func, None, None), via=frame.via, depth=frame.depth)
                    )
        finally:
            self.stack.pop()

    def attribute_store(self, statement: ast.AST, target: ast.Attribute, frame: _Frame) -> None:
        """``x.attr = v`` on something this function did not build is a limit.

        A setter may send, and a request or client handed in (`r.method =
        "DELETE"` in a helper) changes what its caller's call does (#872 review
        2). A local request or client is read by its own name instead.
        """

        root = _root_name(target.value)
        if self.effects_enabled and target.attr in effect_tables.HANDLE_SETTINGS:
            owner = _handle_of(self.value(target.value, frame))
            if owner is not None and owner.family == "database":
                # `conn.row_factory = sqlite3.Row`: how rows come back.
                return
        if (
            root is not None
            and root not in frame.args
            and root in frame.local
            and all(self._built_here(kind, value, frame) for kind, value, _ in frame.local[root])
        ):
            owner = self.value(target.value, frame)
            if not isinstance(owner, Client):
                if isinstance(owner, Request | Rec | Seq) or _is_data(owner):
                    return
                # `box[0].request = send`: something held in a local container.
                self.limit(frame, statement, f"sets {_spelling(target) or 'an attribute'}, which is not read")
                return
            value = getattr(statement, "value", None)
            if target.attr in {"auth", "hooks"} and isinstance(value, ast.expr):
                # A function set as the client's auth or hook runs on every
                # request (#872 review 3).
                self._handed(statement, value, frame, strict=True)
                return
            if target.attr in _CLIENT_SETTINGS:
                return
            self.limit(
                frame, statement, f"sets {_spelling(target) or 'an attribute'} on a client, which is not read"
            )
            return
        if _is_data(self.value(target.value, frame)):
            return
        self.limit(frame, statement, f"sets {_spelling(target) or 'an attribute'}, which is not read")

    def _client_statement(self, change: ast.AST, frame: _Frame) -> None:
        """One module-level statement changing a client: read it or name it."""

        if isinstance(change, ast.Call):
            func = change.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "update"
                and _attribute_name(func.value) == "headers"
            ):
                return
            self.limit(frame, change, f"calls {_spelling(func)} on a client, which is not read")
            return
        if not isinstance(change, ast.Assign | ast.AnnAssign | ast.AugAssign):
            return
        targets = change.targets if isinstance(change, ast.Assign) else [change.target]
        for target in targets:
            if isinstance(target, ast.Subscript) and _attribute_name(target.value) == "headers":
                continue
            attribute = target if isinstance(target, ast.Attribute) else None
            if attribute is None and isinstance(target, ast.Subscript) and isinstance(target.value, ast.Attribute):
                attribute = target.value
            if attribute is None:
                continue
            if attribute.attr in {"auth", "hooks"} and change.value is not None:
                self._handed(change, change.value, frame, strict=True)
            elif attribute.attr not in _CLIENT_SETTINGS or attribute is not target:
                self.limit(
                    frame, change, f"sets {_spelling(target) or 'an attribute'} on a client, which is not read"
                )

    def patches(self, module: PythonModule) -> None:
        """A module that patches an HTTP library at import changes every request.

        `requests.get = logged_get`, `urllib.request.install_opener(...)` or a
        `monkey.patch_all()` in a module this read follows is named, once
        (#872 review 4).
        """

        if id(module) in self.patched:
            return
        self.patched.add(id(module))
        frame = self.module_frame(module)
        for statement in module.tree.body:
            if isinstance(statement, ast.Assign | ast.AugAssign | ast.AnnAssign):
                targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
                for target in targets:
                    root = _root_name(target) if isinstance(target, ast.Attribute) else None
                    if root is not None and isinstance(self.module_name(module, root), Lib):
                        self.limit(frame, statement, f"patches {_spelling(target)} at import, which is not read")
            elif isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
                callee = self.dotted_callee(statement.value.func, frame)
                if callee is None and isinstance(statement.value.func, ast.Name):
                    callee = self.module_name(module, statement.value.func.id)
                if isinstance(callee, Lib) and any(
                    word in callee.dotted.lower() for word in ("install", "monkey", "patch")
                ):
                    self.limit(
                        frame,
                        statement,
                        f"calls {callee.dotted} at import, which may change every request; not read",
                    )

    def _local_client(self, node: ast.AST, frame: _Frame) -> bool:
        """Whether the client a call is made on was constructed in this function."""

        if isinstance(node, ast.Call):
            return self._built_here("value", node, frame)
        if not isinstance(node, ast.Name) or node.id in frame.args:
            return False
        bindings = frame.local.get(node.id, [])
        return bool(bindings) and all(
            self._built_here(kind, value, frame) for kind, value, _ in bindings
        )

    def _built_here(self, kind: str, value: ast.AST, frame: _Frame) -> bool:
        """A binding to a request, client or container this function constructs."""

        if kind != "value":
            return False
        if isinstance(value, ast.List | ast.Dict | ast.Set | ast.ListComp | ast.DictComp | ast.SetComp):
            return True
        if not isinstance(value, ast.Call):
            return False
        # Only the library's own constructor: a factory's client may carry
        # hooks set where this read does not look (#872 review 5).
        callee = self.dotted_callee(value.func, frame) or self.value(value.func, frame)
        return isinstance(callee, Lib) and (callee.dotted in _CLIENTS or callee.dotted in _REQUEST_CLASSES)

    def decorators(self, frame: _Frame) -> None:
        """A decorator may replace the function: name any this read cannot see into."""

        if frame.node is None:
            return
        outer = frame.enclosing or self.module_frame(frame.module)
        for decorator in frame.node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            value = self.value(target, outer)
            if isinstance(value, Lib) and value.dotted in _INERT_DECORATORS:
                continue
            if isinstance(value, _Builtin) and value.name in {"classmethod", "property", "staticmethod"}:
                continue
            self.limit(
                frame,
                decorator,
                f"{frame.node.name} is decorated with {_spelling(target) or 'a computed decorator'}, "
                "which is not read",
            )

    def _only_called(self, frame: _Frame, name: str) -> bool:
        called = {
            id(call.func)
            for call in frame.calls
            if isinstance(call.func, ast.Name) and call.func.id == name
        }
        assert frame.node is not None
        return all(
            id(node) in called
            for node in ast.walk(frame.node)
            if isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Load)
        )

    def classify(self, call: ast.Call, frame: _Frame) -> None:
        callee = self._classify(call, frame)
        self.handed_on(call, frame, callee)

    def _classify(self, call: ast.Call, frame: _Frame) -> str | None:
        """Follow, record or name one call; return the name of a callee passed over."""

        func = call.func
        spelling = _spelling(func)
        dotted = self.dotted_callee(func, frame)
        if dotted is not None:
            callee: Any = dotted
        elif isinstance(func, ast.Attribute):
            receiver = self.value(func.value, frame)
            method = func.attr
            if isinstance(receiver, Lib):
                return self.library_call(call, frame, f"{receiver.dotted}.{method}")
            handle = _handle_of(receiver) if self.effects_enabled else None
            if handle is not None:
                self.handle_call(call, frame, handle, method)
                return None
            if isinstance(receiver, Client):
                if method in _VERBS or method in {"request", "stream"}:
                    self.http(call, frame, receiver.library, method, receiver)
                    if not self._local_client(func.value, frame):
                        # A module-level, imported or factory-made client may be
                        # configured anywhere (#872 review 5).
                        self.limit(
                            frame,
                            call,
                            f"sends through {_spelling(func.value) or 'a client'}, built outside this "
                            "function; its configuration is not read",
                        )
                    return _SENDS
                if method == "mount" and all(_is_quiet(self.value(arg, frame)) for arg in call.args):
                    # A retry adapter: `s.mount("https://", HTTPAdapter(max_retries=3))`.
                    return None
                if method not in {"close", "aclose"}:
                    self.limit(frame, call, f"calls {spelling}, which is not read")
                return None
            if isinstance(receiver, Func):
                self.limit(frame, call, f"calls {spelling}, which is not read")
                return None
            local = isinstance(receiver, Rec | Seq) or self._container(func.value, receiver, frame)
            replaced = None if local else (
                self.replaced_methods.get(method) or self.library.get(f"*.{method}") or self.library.get("*")
            )
            if method in _PURE_METHODS and _is_quiet(receiver) and replaced is not None:
                # `pathlib.Path.read_text = hook` elsewhere (#872 review 13).
                if id(call) not in frame.raised:
                    self.limit(frame, call, f"calls {spelling}, which {replaced} replaces; not read")
                return None
            if (
                (method in _PURE_METHODS and (local or _is_quiet(receiver)))
                or (method in _STRING_METHODS and _is_data(receiver))
                or (method in _CONTAINER_METHODS and (local or _is_data(receiver)))
            ):
                return method
            if id(call) in frame.raised:
                return None
            if isinstance(func.value, ast.Call) and id(func.value) in self.limited:
                return None
            described = _described(receiver)
            head = spelling.split(".", 1)[0]
            self.limit(
                frame,
                call,
                f"calls {spelling}"
                + (f" ({described})" if described and described != head else "")
                + ", which is not read",
            )
            return None
        else:
            callee = self.value(func, frame)
        if isinstance(callee, Lib):
            return self.library_call(call, frame, callee.dotted)
        if isinstance(callee, Func):
            name = callee.node.name
            if frame.depth >= MAX_DEPTH:
                self.limit(
                    frame,
                    call,
                    f"calls {name}, more than {MAX_DEPTH} helper calls from the tool; not read",
                )
                return None
            args = self.bind(callee, call, frame)
            self.walk(
                self.frame(
                    callee,
                    args,
                    via=(*frame.via, f"{frame.module.ref}:{call.lineno} {name}"),
                    depth=frame.depth + 1,
                )
            )
            return None
        if isinstance(callee, _Builtin):
            if callee.name == "print" and self.effects_enabled and self._print_to_file(call, frame):
                return None
            if callee.name in _PURE_BUILTINS:
                return callee.name
            if callee.name == "open" and self.effects_enabled:
                self.function_effect(call, frame, "open", effect_tables.FUNCTIONS["open"])
                return None
            if id(call) not in frame.raised:
                self.limit(frame, call, f"calls {callee.name}, which is not read")
            return None
        if id(call) in frame.raised:
            return None
        if (
            isinstance(callee, Handle)
            and self.effects_enabled
            and (callee.library, callee.role) in effect_tables.FACTORIES
        ):
            # `Session()` from a `sessionmaker`: it builds a session.
            return None
        if isinstance(callee, Op) and callee.what:
            self.limit(frame, call, f"calls {spelling} ({callee.what}), which is not read")
        else:
            self.limit(frame, call, f"calls {spelling or 'a computed callee'}, which is not read")
        return None

    def _container(self, node: ast.AST, receiver: Any, frame: _Frame) -> bool:
        """Whether ``node`` is a list, dict or set this function built itself."""

        if isinstance(receiver, Rec | Seq):
            return True
        if not isinstance(node, ast.Name) or node.id in frame.args:
            return False
        bindings = frame.local.get(node.id, [])
        return bool(bindings) and all(
            kind == "value"
            and (
                isinstance(
                    value,
                    ast.List | ast.Dict | ast.Set | ast.ListComp | ast.DictComp | ast.SetComp,
                )
                or (
                    isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Name)
                    and value.func.id in _CONTAINER_BUILDERS
                    and not frame.binds(value.func.id)
                )
            )
            for kind, value, _ in bindings
        )

    def handed_on(self, call: ast.Call, frame: _Frame, passed: str | None) -> None:
        """A function passed to a call may run there: read it, or name it.

        ``sorted(items, key=helper)`` runs ``helper``; ``map(es.delete, ids)``
        runs a method this read never sees called (#872 review). Where a call
        this read passed over takes a function — ``map``'s first argument, a
        ``key=`` — whatever is passed must be read or named. Anywhere else a
        repository function passed on is read as called.
        """

        positions = _CALLABLE_POSITIONS.get(passed or "", ())
        if passed == "iter" and len(call.args) == 2:
            positions = (0,)  # `iter(callable, sentinel)` calls it until the sentinel
        for index, node in enumerate(call.args):
            if isinstance(node, ast.Starred):
                node = node.value
            self._handed(call, node, frame, strict=passed is not None and index in positions)
        for keyword in call.keywords:
            if keyword.arg is None and passed is not None:
                self._spread(call, keyword.value, frame)
                continue
            strict = passed is not None and keyword.arg in _CALLABLE_KEYWORDS
            self._handed(call, keyword.value, frame, strict=strict)

    def _spread(self, call: ast.Call, node: ast.expr, frame: _Frame) -> None:
        """`requests.get(url, **opts)`: each option that takes a function is read.

        A `hooks` entry in a spread dict runs as surely as one written out
        (#872 review 4); a spread the read cannot see into is a limit.
        """

        value = self.value(node, frame)
        records = _records(value)
        if records is None:
            self.limit(frame, call, f"passes **{_spelling(node) or 'a computed value'}, which is not read")
            return
        for record in records:
            for key, item in record.fields:
                if key == "**":
                    self.limit(frame, call, f"passes **{_spelling(node) or 'a computed value'}, which is not read")
                elif key in _CALLABLE_KEYWORDS:
                    self._handed_value(call, item, frame, strict=True, label=f"{_spelling(node)}[{key!r}]")

    def _handed(self, call: ast.Call | ast.AST, node: ast.expr, frame: _Frame, *, strict: bool) -> None:
        if isinstance(node, ast.Lambda):
            return  # its body is read as part of this function
        if isinstance(node, ast.Dict | ast.List | ast.Tuple | ast.Set):
            # `hooks={"response": [_audit]}`: each element may be a function.
            elements = [*node.values] if isinstance(node, ast.Dict) else list(node.elts)
            for element in elements:
                if isinstance(element, ast.Starred):
                    element = element.value
                self._handed(call, element, frame, strict=strict)
            return
        if not strict and not isinstance(node, ast.Name | ast.Attribute):
            return
        value = self.value(node, frame)
        if isinstance(node, ast.Attribute) and isinstance(self.value(node.value, frame), Client):
            self.limit(frame, call, f"hands on {_spelling(node)}, which is not read")
            return
        self._handed_value(call, value, frame, strict=strict, label=_spelling(node))

    def _handed_value(
        self, call: ast.Call | ast.AST, value: Any, frame: _Frame, *, strict: bool, label: str
    ) -> None:
        for option in value.options if isinstance(value, Alt) else (value,):
            if strict and isinstance(option, Rec | Seq):
                # `hooks=HOOKS`: a dict or list of functions held elsewhere.
                items = [item for _, item in option.fields] if isinstance(option, Rec) else option.items
                for item in items:
                    self._handed_value(call, item, frame, strict=True, label=label)
                if isinstance(option, Rec) and option.open:
                    self.limit(frame, call, f"hands on {label or 'a computed value'}, which is not read")
                continue
            if isinstance(option, Func):
                if frame.depth >= MAX_DEPTH:
                    self.limit(
                        frame,
                        call,
                        f"hands on {option.node.name}, more than {MAX_DEPTH} helper calls "
                        "from the tool; not read",
                    )
                    continue
                self.walk(
                    self.frame(
                        option,
                        self.bind(option, None, None),
                        via=(*frame.via, f"{frame.module.ref}:{call.lineno} {option.node.name}"),
                        depth=frame.depth + 1,
                    )
                )
            elif isinstance(option, Lib):
                if "." in option.dotted and not _inert_library(option.dotted):
                    self.limit(frame, call, f"hands on {option.dotted}, which is not read")
            elif isinstance(option, _Builtin):
                if strict and option.name not in _PURE_BUILTINS:
                    self.limit(frame, call, f"hands on {option.name}, which is not read")
            elif strict and not _is_quiet(option):
                # Plain data (a replacement string, `None`) or an inert object
                # (a retrying transport) is not a function.
                self.limit(frame, call, f"hands on {label or 'a computed value'}, which is not read")

    def replaced(self, dotted: str) -> str | None:
        """Where a library function the read takes as pure was replaced, if anywhere.

        Matched by any part of its path, from any module on it: `json.dumps`,
        `sys.modules["json"].dumps = …` (recorded as `json.dumps`),
        `datetime.date = …` for `datetime.date.today`, or `json.*` when the
        module's namespace changed under names the scan cannot see.
        """
        parts = dotted.split(".")
        found = self.library.get("*") or self.library.get(f"*.{parts[-1]}") or self.whole.get("*")
        for start in range(len(parts) - 1):
            for end in range(start + 2, len(parts) + 1):
                path = ".".join(parts[start:end])
                found = found or self.library.get(path)
                if end < len(parts):
                    found = found or self.library.get(f"{path}.*")
            found = found or self.library.get(f"{parts[start]}.*")
            # A module kept where the scan does not follow it, and this
            # attribute stored on an object the scan does not follow.
            if found is None and f"kept:{parts[start]}" in self.library:
                found = self.unseen_segments.get(parts[start + 1])
        return found

    def library_call(self, call: ast.Call, frame: _Frame, dotted: str) -> str | None:
        where = self.replaced(dotted)
        if where is not None and _inert_library(dotted):
            # `json.dumps = audited_dumps` elsewhere in the scope.
            if id(call) not in frame.raised:
                self.limit(frame, call, f"calls {dotted}, which {where} replaces; not read")
            return None
        if self.effects_enabled:
            spec = effect_tables.FUNCTIONS.get(dotted)
            if spec is not None or dotted in effect_tables.CONSTRUCTORS:
                if where is not None:
                    # `subprocess.run = fake_run` elsewhere: not the library's.
                    self.limit(frame, call, f"calls {dotted}, which {where} replaces; not read")
                elif spec is not None:
                    self.function_effect(call, frame, dotted, spec)
                return None
            if dotted in effect_tables.FILE_CODECS and self._codec_on_file(call, frame, dotted):
                # `json.load(f)` on a file this read opened: the read is where
                # it was opened.
                return dotted
            if dotted in effect_tables.STATEMENTS or dotted in effect_tables.SQL_TEXT:
                return dotted
        library, _, verb = dotted.rpartition(".")
        if library in _HTTP_LIBRARIES and (verb in _VERBS or verb in {"request", "stream"}):
            self.http(call, frame, library, verb, None)
            return _SENDS
        if dotted in _URLOPEN:
            self.urlopen(call, frame)
            return _SENDS
        if dotted in _CLIENTS or dotted in _REQUEST_CLASSES:
            return _SENDS
        if _inert_library(dotted):
            return dotted
        elif id(call) not in frame.raised:
            self.limit(frame, call, f"calls {dotted}, which is not read")
        return None

    # -- effects beyond HTTP (#913) ----------------------------------------

    def function_effect(
        self, call: ast.Call, frame: _Frame, name: str, spec: effect_tables.Function
    ) -> None:
        """A library function that is itself an effect: a process or a file."""

        arguments = _Arguments(call)
        position, keyword = spec.target
        node = arguments.take(position, keyword)
        value = self.value(node, frame) if node is not None else None
        others = [
            self.value(item, frame)
            for item in [*arguments.positional, *arguments.keywords.values()]
            if item is not node
        ]
        library = name.split(".", 1)[0] if "." in name else "builtins"
        if spec.family == "process":
            shell_node = arguments.take(None, "shell")
            shell = spec.shell or (
                shell_node is not None and self.value(shell_node, frame) == Lit(True)
            )
            executable = arguments.take(None, "executable")
            exec_form = name.startswith(("os.exec", "os.spawn", "os.posix_spawn", "os.startfile")) or (
                name == "asyncio.create_subprocess_exec"
            )
            program = (
                self.value(executable, frame)
                if executable is not None
                else value
                if exec_form
                else _command_program(value, shell)
            )
            names, unread = _program_names(program)
            self.record_effect(
                call,
                frame,
                family="process",
                library=library,
                operation="execute",
                name=name,
                target="|".join([*names, *(["{…}"] if unread or not names else [])]),
                shell=shell,
                supplied=[("command", value), ("command", program), ("arguments", _derived(*others))],
                digest=(value, program),
            )
            return
        operation = spec.operation
        if operation == "mode":
            operation = self._file_mode(arguments.take(1, "mode"), frame)
        if value is None and name in {"os.listdir", "os.scandir", "os.walk"}:
            value = Lit(".")
        self.path_effect(call, frame, library, name, operation, value, others)
        if operation == "unknown":
            self.limit(frame, call, "the file mode is not a literal; whether it writes is not read")

    def path_effect(
        self,
        call: ast.Call,
        frame: _Frame,
        library: str,
        name: str,
        operation: str,
        path: Any,
        others: list[Any],
    ) -> None:
        if isinstance(path, Handle):
            path = path.target
        shown = _path_text(path)
        self.record_effect(
            call,
            frame,
            family="filesystem",
            library=library,
            operation=operation,
            name=name,
            target=shown,
            supplied=[("path", path), ("arguments", _derived(*others))],
            digest=(path,) if shown is None and path is not None else None,
        )

    def _file_mode(self, node: ast.AST | None, frame: _Frame) -> str:
        """``read`` or ``write`` from a literal mode, ``unknown`` otherwise."""

        if node is None:
            return "read"
        operations: set[str] = set()
        for option in _options(self.value(node, frame)):
            if isinstance(option, Lit) and option.value is None:
                operations.add("read")
            elif isinstance(option, Lit) and isinstance(option.value, str):
                operations.add(effect_tables.file_mode(option.value))
            else:
                return "unknown"
        return "write" if "write" in operations else "read"

    def _codec_on_file(self, call: ast.Call, frame: _Frame, dotted: str) -> bool:
        """`json.dump(data, handle)` on a file object: its read or write is the
        file's. On one opened elsewhere it is named here."""

        arguments = _Arguments(call)
        reads = dotted.endswith("load")
        node = arguments.take(0 if reads else 1, "fp" if dotted.startswith("json") else "stream")
        handle = _handle_of(self.value(node, frame)) if node is not None else None
        if handle is None or handle.role != "file":
            return False
        if handle.built != _frame_key(frame):
            self.path_effect(call, frame, handle.library, dotted, "read" if reads else "write", handle, [])
        return True

    def _print_to_file(self, call: ast.Call, frame: _Frame) -> bool:
        """`print(..., file=f)` writes to ``f``: a file object, or a limit."""

        node = _Arguments(call).take(None, "file")
        if node is None:
            return False
        value = self.value(node, frame)
        handle = _handle_of(value)
        if handle is not None and handle.role == "file":
            if handle.built != _frame_key(frame):
                self.path_effect(call, frame, handle.library, "print", "write", handle, [])
            return True
        if is_absent(value) or (isinstance(value, Lib) and value.dotted in {"sys.stdout", "sys.stderr"}):
            return False
        self.limit(frame, call, f"prints to {_spelling(node) or 'an object'}, which is not read")
        return True

    def handle_call(self, call: ast.Call, frame: _Frame, handle: Handle, method: str) -> None:
        """A method on an object a recognised library built: name its effect."""

        spelling = _spelling(call.func) or method
        replaced = self.replaced_methods.get(method) or self.library.get(f"*.{method}") or self.library.get("*")
        if replaced is not None:
            if id(call) not in frame.raised:
                self.limit(frame, call, f"calls {spelling}, which {replaced} replaces; not read")
            return
        if handle.built != _frame_key(frame) and self._foreign_handle_call(call, frame, handle, method, spelling):
            return
        rule = effect_tables.method_rule(handle.library, handle.role, method)
        if rule is not None and handle.role == effect_tables.RESULTS and method.startswith(_WRITING_VERBS):
            rule = None
        name = f"{handle.role}.{method}"
        if rule is None:
            if id(call) in frame.raised:
                return
            if handle.role != effect_tables.RESULTS:
                given = self._effect_target(handle, _Arguments(call), frame, method)
                self.record_effect(
                    call,
                    frame,
                    operation="unknown",
                    name=name,
                    handle=handle,
                    target=_name_text(given if given is not None else handle.target),
                )
            self.limit(
                frame,
                call,
                f"calls {spelling} ({handle.library} {name}), which the {handle.family} table "
                "does not name; what it does is not read",
            )
            return
        if rule in {"pass", "same"} or rule.startswith("->"):
            return
        arguments = _Arguments(call)
        statement: str | None = None
        target: Any = None
        operation = rule.split("->", 1)[0]
        supplied: list[tuple[str, Any]] = []
        digest: tuple[Any, ...] | None = None
        if rule in {"sql", "statement", "script"}:
            node = next(
                (
                    found
                    for found in (
                        arguments.take(0, word) for word in ("sql", "query", "operation", "statement")
                    )
                    if found is not None
                ),
                None,
            )
            if handle.role == "statement" and node is None:
                # `select(User).execute()`: the statement is the object.
                text: Any = handle
            else:
                text = self.value(node, frame) if node is not None else _UNKNOWN
            read = _sql_statement(text)
            if read.operation is None:
                return  # transaction control: `BEGIN`, `COMMIT`
            operation = read.operation
            statement, target = read.keyword, read.table
            supplied.append(("statement", text))
            digest = (text,)
            if read.operation == "unknown":
                self.limit(
                    frame,
                    call,
                    f"what the {read.keyword} statement does is not read"
                    if read.keyword
                    else "the SQL statement is not a literal; whether it writes is not read",
                )
            elif read.open and read.operation == "read":
                self.limit(
                    frame,
                    call,
                    f"the {read.keyword} statement splices in a value the read does not name; "
                    "a further statement in it is not read",
                )
        elif rule == "aggregate":
            operation = _pipeline_operation(self.value(arguments.take(0, "pipeline"), frame))
            if operation == "unknown":
                self.limit(frame, call, "the aggregation pipeline is not a literal; whether it writes is not read")
        elif rule == "mode":
            operation = self._file_mode(arguments.take(0, "mode"), frame)
            if operation == "unknown":
                self.limit(frame, call, "the file mode is not a literal; whether it writes is not read")
        elif rule == "paginate":
            name_value = handle.extra
            found = (
                effect_tables.boto3_operation(name_value.value)
                if isinstance(name_value, Lit) and isinstance(name_value.value, str)
                else None
            )
            operation = found or "unknown"
            if operation == "unknown":
                self.limit(frame, call, f"pages an operation the boto3 table does not name ({spelling}); not read")
        if handle.family == "filesystem":
            # `path.write_text(...)`: the path is the target.
            self.path_effect(
                call,
                frame,
                handle.library,
                name,
                operation,
                handle,
                [self.value(item, frame) for item in [*arguments.positional, *arguments.keywords.values()]],
            )
            return
        given = self._effect_target(handle, arguments, frame, method)
        if target is None:
            target_value = given if given is not None else handle.target
            target = _name_text(target_value) if target_value is not None else None
            if target is None and target_value is not None:
                digest = (*(digest or ()), target_value)
        supplied.append(("target", handle.target))
        sql = rule in {"sql", "statement", "script"}
        for index, item in enumerate(arguments.positional):
            if not (sql and index == 0):
                supplied.append(("parameters" if sql else "arguments", self.value(item, frame)))
        for word, item in arguments.keywords.items():
            if not (sql and word in {"sql", "query", "operation", "statement"}):
                supplied.append((f"argument {word}", self.value(item, frame)))
        self.record_effect(
            call,
            frame,
            operation=operation,
            name=name,
            handle=handle,
            target=target,
            statement=statement,
            supplied=supplied,
            digest=digest,
        )
        local = effect_tables.LOCAL_WRITES.get((handle.library, handle.role, method))
        if local is not None:
            # `s3.download_file(bucket, key, path)` also writes a local file.
            node = arguments.take(*local)
            if node is not None:
                self.path_effect(call, frame, handle.library, name, "write", self.value(node, frame), [])
        if (
            operation == "read"
            and handle.family in {"database", "cloud", "messaging"}
            and handle.built != _frame_key(frame)
        ):
            # A client built elsewhere may be configured anywhere (#872's rule
            # for HTTP clients): a hook or event listener can do more.
            receiver = _spelling(call.func.value) if isinstance(call.func, ast.Attribute) else None
            self.limit(
                frame,
                call,
                f"reads through {receiver or 'an object'}, "
                + (
                    "built with an argument this read does not see into"
                    if handle.built == _CONFIGURED
                    else "built outside this function"
                )
                + "; its configuration is not read",
            )

    def _foreign_handle_call(
        self, call: ast.Call, frame: _Frame, handle: Handle, method: str, spelling: str
    ) -> bool:
        """A file or process another function opened or started.

        Its effect was not recorded where this tool can see it: a write to a
        module-level log file is a write here (#913).
        """

        if handle.role == "file":
            operation = effect_tables.FILE_IO.get(method)
            if operation is None:
                return False
            self.path_effect(call, frame, handle.library, f"file.{method}", operation, handle, [])
            return True
        if handle.role == "process" and method not in {"__enter__", "poll", "wait"}:
            self.record_effect(call, frame, operation="unknown", name=f"process.{method}", handle=handle)
            self.limit(frame, call, f"calls {spelling} on a process started outside this function, which is not read")
            return True
        return False

    def _effect_target(
        self, handle: Handle, arguments: _Arguments, frame: _Frame, method: str = ""
    ) -> Any:
        """The argument naming what one call reaches, where the library has one."""

        words: tuple[str, ...] = ()
        position: int | None = None
        if handle.library == "boto3" and handle.role == "client":
            position = effect_tables.BOTO3_BUCKET_POSITIONS.get(method)
            words = ("Bucket", "TableName", "FunctionName", "StreamName", "QueueName")
        elif handle.library == "redis":
            words, position = ("name", "key", "channel"), 0
        elif handle.library == "slack_sdk" and handle.role == "client":
            words = ("channel",)
        for word in words:
            node = arguments.take(position, word)
            if node is not None:
                return self.value(node, frame)
        return None

    def record_effect(
        self,
        call: ast.Call,
        frame: _Frame,
        *,
        operation: str,
        name: str,
        handle: Handle | None = None,
        family: str | None = None,
        library: str | None = None,
        target: str | None = None,
        statement: str | None = None,
        shell: bool = False,
        supplied: list[tuple[str, Any]] | None = None,
        digest: tuple[Any, ...] | None = None,
    ) -> None:
        family = family or (handle.family if handle else "unknown")
        library = library or (handle.library if handle else "unknown")
        at = f"{frame.module.ref}:{call.lineno}"
        if len(self.effects) >= MAX_CALLS:
            if not self.truncated:
                self.truncated = True
                self.limit(frame, call, f"the tool reaches more than {MAX_CALLS} library effects")
            self.dropped_effects.append({"family": family, "operation": operation, "call": name, "at": at})
            return
        entry: dict[str, Any] = {
            "family": family,
            "operation": operation,
            "library": library,
            "call": name,
        }
        if handle is not None and handle.service:
            entry["service"] = handle.service
        if target:
            entry["target"] = target
        if statement:
            entry["statement"] = statement
        if shell:
            entry["shell"] = True
        if handle is not None and handle.host:
            entry["host"] = list(handle.host)
        entry["at"] = at
        entry["via"] = list(frame.via)
        if any(
            (item["at"], item["via"], item["call"], item.get("target"), item["operation"])
            == (at, entry["via"], name, entry.get("target"), operation)
            for item in self.effects
        ):
            return
        if handle is not None and handle.credentials:
            entry["credential_sources"] = [_thawed(item) for item in handle.credentials]
        into: dict[str, list[str]] = {}
        for place, value in supplied or []:
            if value is None:
                continue
            for param in sorted(_sources(value)[0]):
                places = into.setdefault(param, [])
                if place not in places:
                    places.append(place)
        if into:
            entry["model_supplied"] = [
                {"param": param, "into": place} for param, places in sorted(into.items()) for place in places
            ]
        if digest:
            entry["value_sha256"] = object_digest(*digest)
        self.effects.append(entry)

    def library_value(
        self, call: ast.Call, frame: _Frame, dotted: str, arguments: list[Any], keywords: dict[str, Any]
    ) -> Any | None:
        """What a recognised library call hands back, or None when not one."""

        built = _frame_key(frame)
        kind = effect_tables.CONSTRUCTORS.get(dotted)
        if kind is not None:
            if kind.family in {"database", "cloud", "messaging"} and any(
                _unseen_argument(value) for value in [*arguments, *keywords.values()]
            ):
                # `sqlite3.connect(db, factory=Audited)`: built with an object
                # whose behaviour this read does not see, like one built
                # elsewhere.
                built = _CONFIGURED
            return self.construct(dotted, kind, arguments, keywords, built)
        if dotted in effect_tables.STATEMENTS:
            return Handle("database", "sqlalchemy", "statement", extra=effect_tables.STATEMENTS[dotted], built=built)
        if dotted in effect_tables.SQL_TEXT:
            return arguments[0] if arguments else keywords.get("text", _UNKNOWN)
        spec = effect_tables.FUNCTIONS.get(dotted)
        if spec is not None:
            if dotted in {"asyncio.create_subprocess_exec", "asyncio.create_subprocess_shell"}:
                return Handle("process", "subprocess", "process", built=built)
            if dotted == "io.open":
                path = arguments[0] if arguments else keywords.get("file", _UNKNOWN)
                return Handle("filesystem", "builtins", "file", target=path, built=built)
            plain = dotted in {
                "subprocess.check_output", "subprocess.getoutput", "subprocess.getstatusoutput",
                "os.system", "os.listdir", "os.walk",
            }
            return Op(what=f"what {dotted} returns", inert=True, data=plain)
        if dotted in effect_tables.FILE_CODECS and dotted.endswith("load"):
            return Op(what=f"what {dotted} returns", inert=True, data=True)
        return None

    def construct(
        self,
        dotted: str,
        kind: effect_tables.Kind,
        arguments: list[Any],
        keywords: dict[str, Any],
        built: int,
    ) -> Handle:
        service = kind.service
        target: Any = None
        spec = effect_tables.CONSTRUCTOR_TARGETS.get(dotted)
        given: Any = None
        if spec is not None:
            position, word = spec
            given = keywords.get(word)
            if given is None and position is not None and position < len(arguments):
                given = arguments[position]
        if kind.library == "boto3":
            if isinstance(given, Lit) and isinstance(given.value, str):
                service = given.value
        elif kind.library == "sqlalchemy" and kind.role == "engine":
            # `create_engine("postgresql+psycopg2://…")`: the dialect.
            url = arguments[0] if arguments else keywords.get("url")
            if isinstance(url, Lit | Tpl):
                scheme = _leading_literal(url).partition("://")
                if scheme[1] and re.fullmatch(r"[a-z0-9]+(?:\+[a-z0-9_]+)?", scheme[0]):
                    service = scheme[0].split("+", 1)[0]
        elif kind.role == "path":
            if dotted in {"pathlib.Path.cwd", "pathlib.Path.home"}:
                target = _UNKNOWN
            elif len(arguments) > 1:
                target = _join([part for item in arguments for part in (item, Lit("/"))][:-1])
            else:
                target = given if given is not None else Lit(".")
        hosts = _hosts(dotted, kind, arguments, keywords)
        if kind.family == "messaging" and kind.library == "smtplib" and given is not None:
            hosts = _hosts_of(given)
        credentials = _construction_credentials(dotted, arguments, keywords)
        if kind.library == "sqlalchemy" and kind.role in {"session", "session_factory"}:
            # `sessionmaker(bind=engine)`: the engine's dialect, host and
            # credentials.
            engine = _handle_of(keywords.get("bind") or (arguments[0] if arguments else None))
            if engine is not None and engine.library == "sqlalchemy":
                service, hosts, credentials = engine.service, list(engine.host), engine.credentials
        return Handle(
            kind.family,
            kind.library,
            kind.role,
            service,
            target,
            None,
            built,
            credentials,
            tuple(hosts),
        )

    def handle_value(self, handle: Handle, method: str, call: ast.Call, frame: _Frame) -> Any:
        """What a method on a recognised library's object hands back."""

        rule = effect_tables.method_rule(handle.library, handle.role, method)
        if rule is None or (handle.role == effect_tables.RESULTS and method.startswith(_WRITING_VERBS)):
            return _derived(handle, result=True)
        arguments = _Arguments(call)
        if rule == "same":
            if handle.role != "path":
                return handle
            if method == "joinpath" and not arguments.spread:
                parts = [self.value(item, frame) for item in arguments.positional]
                return replace(
                    handle, target=_join([handle.target or _UNKNOWN, *(p for item in parts for p in (Lit("/"), item))])
                )
            return replace(handle, target=_UNKNOWN)
        if "->" in rule:
            role = rule.split("->", 1)[1]
            first_node = arguments.positional[0] if arguments.positional else None
            first = self.value(first_node, frame) if first_node is not None else None
            if role == "paginator":
                return replace(handle, role=role, extra=first)
            if handle.library == "boto3" and handle.role == "session":
                service = first.value if isinstance(first, Lit) and isinstance(first.value, str) else None
                return replace(handle, role=role, service=service)
            return replace(handle, role=role, target=_child_target(handle, role, first))
        if rule == "pass":
            if handle.role == effect_tables.RESULTS:
                return handle
            return Op(what=f"what {handle.role}.{method} returns", inert=True, data=True)
        if rule == "mode":
            # `path.open("w")`, `blob.open("wb")`: a file object whose reads
            # and writes are the effect named where it was opened.
            if handle.role == "path":
                return Handle("filesystem", "builtins", "file", target=handle.target, built=_frame_key(frame))
            return replace(handle, role="stream", built=_frame_key(frame))
        if handle.library in {"sqlite3", "psycopg2", "psycopg", "pymysql"} or handle.role in {"raw_connection", "cursor"}:
            if rule in {"sql", "script"}:
                return replace(handle, role="cursor")
        if handle.library in _PLAIN_RESULTS:
            return Op(what=f"what {handle.role}.{method} returns", inert=True, data=True)
        return replace(handle, role=effect_tables.RESULTS, target=None)

    def handle_attribute(self, handle: Handle, attribute: str) -> Any:
        role = effect_tables.attribute_role(handle.library, handle.role, attribute)
        if role is not None:
            return replace(handle, role=role)
        child = effect_tables.NAMED_CHILDREN.get((handle.library, handle.role))
        if child is not None and not attribute.startswith("_"):
            return replace(handle, role=child, target=_child_target(handle, child, Lit(attribute)))
        if handle.role == "path":
            if attribute in effect_tables.PATH_ATTRIBUTES:
                return replace(handle, target=_UNKNOWN)
            return Op(what=f"the path's {attribute}", inert=True, data=True)
        if handle.role in {"process", effect_tables.RESULTS, "file"}:
            return Op(what=f"the {handle.role}'s {attribute}", inert=True)
        return _derived(handle)

    def global_value(self, module: PythonModule, name: str) -> Any | None:
        """A module global the module's own functions set: the one library object it holds.

        ``_pool = None`` at module level and ``global _pool; _pool =
        psycopg2.pool.ThreadedConnectionPool(...)`` in a function is the lazy
        singleton pattern. When every assignment in the module is ``None`` or
        the same kind of recognised object, and nothing anywhere stores into
        that name, the global holds that object. Its configuration is not
        read: it is built outside the tool's function.
        """

        key = (id(module), name)
        if key in self.globals:
            return self.globals[key]
        self.globals[key] = None
        if self.mutated.get(name) or self.whole.get("*") or self.whole.get(_module_stem(module.ref)):
            return None
        sites: list[tuple[ast.expr, ast.FunctionDef | ast.AsyncFunctionDef | None]] = []
        for statement in _module_level(module.tree.body):
            for target, value in _bound_names(statement):
                if target == name:
                    if value is None:
                        return None
                    sites.append((value, None))
        for function in ast.walk(module.tree):
            if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            declared = any(
                isinstance(node, ast.Global) and name in node.names for node in _own_nodes(function)
            )
            if not declared:
                continue
            for node in _own_nodes(function):
                for target, value in _bound_names(node):
                    if target == name:
                        if value is None:
                            return None
                        sites.append((value, function))
        values: list[Any] = []
        for value, function in sites:
            if function is None:
                frame = self.module_frame(module)
            else:
                func = Func(module, function)
                frame = self.frame(func, self.bind(func, None, None), via=(), depth=MAX_DEPTH + 1)
            values.append(self.value(value, frame))
        handles = [value for value in values if value != Lit(None)]
        if not handles or not all(isinstance(value, Handle) for value in handles):
            return None
        first = handles[0]
        if any((item.family, item.library, item.role) != (first.family, first.library, first.role) for item in handles):
            return None
        found = _handle_of(_alt([replace(item, built=0) for item in handles]))
        self.globals[key] = found
        return found

    # -- outbound calls ----------------------------------------------------

    def http(
        self,
        call: ast.Call,
        frame: _Frame,
        library: str,
        verb: str,
        client: Client | None,
    ) -> None:
        arguments = _Arguments(call)
        if verb in {"request", "stream"}:
            method = self.value(arguments.take(0, "method"), frame)
            url = self.value(arguments.take(1, "url"), frame)
            positionals: tuple[str, ...] = ()
        else:
            method = Lit(_VERBS[verb])
            url = self.value(arguments.take(0, "url"), frame)
            positionals = _VERB_POSITIONALS.get(verb, ())
        named: dict[str, Any] = {}
        for index, name in enumerate(positionals):
            node = arguments.take(index + 1, name)
            if node is not None:
                named[name] = self.value(node, frame)
        for name in ("params", "data", "json", "content", "headers", "auth", "files"):
            if name not in named:
                node = arguments.take(None, name)
                if node is not None:
                    named[name] = self.value(node, frame)
        if client is not None:
            if client.base_url is not None and not _absolute(url):
                url = _join([client.base_url, url])
            if client.headers is not None:
                named["headers"] = _merge_headers(client.headers, named.get("headers"))
            if client.auth is not None and named.get("auth") is None:
                named["auth"] = client.auth
            if client.params is not None:
                named["params"] = _merge_headers(client.params, named.get("params"))
        if arguments.spread:
            named.setdefault("headers", _UNKNOWN)
        self.record(call, frame, library, method, url, named)

    def urlopen(self, call: ast.Call, frame: _Frame) -> None:
        arguments = _Arguments(call)
        target_node = arguments.take(0, "url")
        target = self.value(target_node, frame)
        if isinstance(target, Request) and target_node is not None and not self._local_client(target_node, frame):
            # A module-level request may be changed anywhere (#872 review 6).
            self.limit(
                frame,
                call,
                f"sends {_spelling(target_node) or 'a request'}, built outside this function; "
                "its configuration is not read",
            )
        data_node = arguments.take(1, "data")
        data = self.value(data_node, frame) if data_node is not None else None
        headers: Any = None
        if isinstance(target, Request):
            url, headers = target.url, target.headers
            data = target.data if data is None else data
            method = target.method
        elif _is_data(target):
            url, method = target, None
        else:
            # Not a URL and not a request this read built: its method is
            # whatever the object says.
            url, method = _UNKNOWN, _UNKNOWN
        if method is None:
            method = Lit("GET") if data is None or data == Lit(None) else Lit("POST")
        named = {"data": data} if data is not None else {}
        if headers is not None:
            named["headers"] = headers
        self.record(call, frame, "urllib", method, url, named)

    def record(
        self,
        call: ast.Call,
        frame: _Frame,
        library: str,
        method: Any,
        url: Any,
        named: dict[str, Any],
    ) -> None:
        if len(self.calls) >= MAX_CALLS:
            if not self.truncated:
                self.truncated = True
                self.limit(frame, call, f"the tool makes more than {MAX_CALLS} outbound calls")
            methods = _methods(method)
            # Not listed, but its effect still counts: a later DELETE must
            # not leave the claim at write.
            self.dropped.append(
                {
                    "effect": _call_effect(methods, None),
                    "method": "|".join(methods) if methods else None,
                    "url": _render_url(url, named.get("params")),
                    "at": f"{frame.module.ref}:{call.lineno}",
                }
            )
            return
        entry: dict[str, Any] = {"library": library}
        methods = _methods(method)
        entry["method"] = "|".join(methods) if methods else None
        entry["url"] = _render_url(url, named.get("params"))
        payload = next(
            (
                named[name]
                for name in ("json", "data", "content")
                if named.get(name) is not None and named[name] != Lit(None)
            ),
            None,
        )
        graphql = None
        document = (
            payload.get("query") if isinstance(payload, Rec) and not payload.shared else None
        )
        kinds = (
            _graphql_operations(document.value)
            if isinstance(document, Lit) and isinstance(document.value, str)
            else None
        )
        # Only a GraphQL endpoint: a LogQL or PromQL `query` parses as an
        # anonymous GraphQL query, and a POST to a delete endpoint is not a read
        # (#872 review 2).
        if _graphql_endpoint(url):
            graphql = (
                "mutation"
                if kinds and "mutation" in kinds
                else "query"
                if kinds and kinds <= {"query", "subscription"}
                else "unknown"
            )
            entry["graphql"] = graphql
        entry["effect"] = _call_effect(methods, graphql)
        entry["at"] = f"{frame.module.ref}:{call.lineno}"
        entry["via"] = list(frame.via)
        if any(
            (item["at"], item["via"], item["method"], item["url"])
            == (entry["at"], entry["via"], entry["method"], entry["url"])
            for item in self.calls
        ):
            # The same site reached again, as a function handed on as well as
            # called: one call.
            return
        fields = _fields(payload)
        if fields:
            entry["fields"] = fields
        credentials = _credentials(
            named.get("headers"), named.get("auth"), named.get("params"), url, payload
        )
        if credentials:
            entry["credential_sources"] = credentials
        supplied = _model_supplied(method, url, named, payload)
        if supplied:
            entry["model_supplied"] = supplied
        self.calls.append(entry)
        if not methods:
            self.limit(frame, call, "the request method is not a literal")
        elif graphql == "unknown":
            self.limit(frame, call, "the GraphQL document is not a literal; its operation is not read")

    # -- values ------------------------------------------------------------

    def value(self, node: ast.AST | None, frame: _Frame) -> Any:
        if node is None:
            return Lit(None)
        try:
            return self._value(node, frame)
        except RecursionError:
            return _UNKNOWN

    def _value(self, node: ast.AST, frame: _Frame) -> Any:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bytes):
                return Op(literal=True)
            return Lit(node.value)
        if isinstance(node, ast.JoinedStr):
            parts: list[Any] = []
            for part in node.values:
                if isinstance(part, ast.FormattedValue):
                    inner = self.value(part.value, frame)
                    if part.format_spec is not None or part.conversion not in (-1, ord("s")):
                        inner = _derived(inner)
                    parts.append(inner)
                else:
                    parts.append(self.value(part, frame))
            return _join(parts)
        if isinstance(node, ast.BinOp):
            left, right = self.value(node.left, frame), self.value(node.right, frame)
            if (
                isinstance(node.op, ast.Div)
                and self.effects_enabled
                and isinstance(left, Handle)
                and left.role == "path"
            ):
                # `Path(root) / "out.json"` is a path.
                base = left.target if left.target is not None else _UNKNOWN
                return replace(left, target=_join([base, Lit("/"), right]))
            if isinstance(node.op, ast.Add) and (_is_text(left) or _is_text(right)):
                return _join([left, right])
            return _derived(left, right)
        if isinstance(node, ast.Name):
            iterable = frame.comprehension_names.get(id(node))
            if iterable is not None:
                return _derived(self.value(iterable, frame))
            return self.name(node.id, frame, node)
        if isinstance(node, ast.Attribute):
            return self.attribute(node, frame)
        if isinstance(node, ast.Subscript):
            base = self.value(node.value, frame)
            key = self.value(node.slice, frame)
            if isinstance(base, Lib) and base.dotted == "os.environ":
                if isinstance(key, Lit) and isinstance(key.value, str):
                    return Op(envs=frozenset({key.value}), exact=True, data=True)
                return _derived(key)
            if self.effects_enabled and (handle := _handle_of(base)) is not None:
                child = effect_tables.NAMED_CHILDREN.get((handle.library, handle.role))
                if child is not None:
                    # `client["shop"]["orders"]`: a database, a collection.
                    return replace(handle, role=child, target=_child_target(handle, child, key))
            if isinstance(base, Rec) and base.shared:
                return Op(what=f"{_spelling(node.value) or 'a module-level dict'}[…], a module-level dict")
            if isinstance(base, Rec) and isinstance(key, Lit) and isinstance(key.value, str):
                found = base.get(key.value)
                if found is not None:
                    return found
            if _is_data(base):
                # An item or slice of plain data is plain data.
                return _derived(base, key, data=True)
            return _derived(base, key)
        if isinstance(node, ast.Slice):
            bounds = [self.value(part, frame) for part in (node.lower, node.upper, node.step) if part]
            return _derived(*bounds) if bounds else Lit(None)
        if isinstance(node, ast.Call):
            return self.call_value(node, frame)
        if isinstance(node, ast.Dict):
            fields: dict[str, Any] = {}
            open_ = False
            shared = False
            extra: list[Any] = []
            for key_node, value_node in zip(node.keys, node.values, strict=True):
                value = self.value(value_node, frame)
                key = self.value(key_node, frame) if key_node is not None else None
                if isinstance(key, Lit) and isinstance(key.value, str):
                    fields.pop(key.value, None)
                    fields[key.value] = value
                elif key_node is None and isinstance(value, Rec):
                    fields.update(value.fields)
                    open_ = open_ or value.open
                    # `{**DEFAULTS}` copies a module-level dict (#872 review 10).
                    shared = shared or value.shared
                else:
                    open_ = True
                    extra.append(value)
            if extra:
                fields["**"] = _derived(*extra)
            return Rec(tuple(fields.items()), open_, shared)
        if isinstance(node, ast.List | ast.Tuple | ast.Set):
            return Seq(tuple(self.value(item, frame) for item in node.elts))
        if isinstance(node, ast.IfExp):
            test = self.value(node.test, frame)
            return _alt(
                [self.value(node.body, frame), self.value(node.orelse, frame)],
                _sources(test)[0],
            )
        if isinstance(node, ast.BoolOp):
            return _alt([self.value(item, frame) for item in node.values])
        if isinstance(node, ast.Await):
            return self.value(node.value, frame)
        if isinstance(node, ast.NamedExpr):
            return self.value(node.value, frame)
        if isinstance(node, ast.Starred):
            return _derived(self.value(node.value, frame))
        if isinstance(node, ast.ListComp | ast.SetComp | ast.GeneratorExp | ast.DictComp):
            # What the comprehension holds is its elements: `[Request(...) for
            # i in ids]` holds requests, not the ids.
            elements = [node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]
            return _derived(
                *(self.value(gen.iter, frame) for gen in node.generators),
                *(self.value(element, frame) for element in elements),
            )
        if isinstance(node, ast.Compare | ast.UnaryOp):
            return _derived(*(self.value(child, frame) for child in ast.iter_child_nodes(node) if isinstance(child, ast.expr)))
        return _UNKNOWN

    def dotted_callee(self, func: ast.AST, frame: _Frame) -> Func | Lib | None:
        """``module.function`` or ``library.name`` spelled through a module-level name."""

        if not isinstance(func, ast.Attribute):
            return None
        head: ast.AST = func
        while isinstance(head, ast.Attribute):
            head = head.value
        if not isinstance(head, ast.Name) or self._local(head.id, frame):
            return None
        if head.id not in frame.module.bindings:
            return None
        value = self.attribute(func, frame)
        return value if isinstance(value, Func | Lib) else None

    def attribute(self, node: ast.Attribute, frame: _Frame) -> Any:
        head: ast.AST = node
        while isinstance(head, ast.Attribute):
            head = head.value
        if isinstance(head, ast.Name) and not self._local(head.id, frame):
            spelling = reference_spelling(node)
            if spelling is not None and head.id in frame.module.bindings:
                named = self.module_name(frame.module, head.id)
                if isinstance(named, Lib):
                    return Lib(f"{named.dotted}{spelling[len(head.id):]}")
                if isinstance(named, _Instance) and node.value is head:
                    return self.instance_attribute(named, node.attr)
                if not (self.effects_enabled and _handle_of(named) is not None):
                    resolved = self._resolved(frame.module, self.resolver.resolve(frame.module, spelling))
                    if resolved is not None:
                        return resolved
        base = self.value(node.value, frame)
        if isinstance(base, Lib):
            return Lib(f"{base.dotted}.{node.attr}")
        if self.effects_enabled and (handle := _handle_of(base)) is not None:
            return self.handle_attribute(handle, node.attr)
        if isinstance(base, _Instance):
            return self.instance_attribute(base, node.attr)
        if isinstance(base, Client) and node.attr == "headers":
            return base.headers if base.headers is not None else Rec(())
        if isinstance(base, Op) and base.inert and not base.data:
            return _derived(base, data=node.attr in _DATA_ATTRIBUTES, inert=True)
        return _derived(base)

    def _local(self, name: str, frame: _Frame | None) -> bool:
        while frame is not None and frame.node is not None:
            if frame.binds(name):
                return not all(kind == "global" for kind, _, _ in frame.local.get(name, [])) or name in frame.args
            frame = frame.enclosing
        return False

    def name(self, name: str, frame: _Frame, node: ast.AST) -> Any:
        scope: _Frame | None = frame
        while scope is not None and scope.node is not None:
            if scope.binds(name):
                kinds = {kind for kind, _, _ in scope.local.get(name, [])}
                if "global" in kinds:
                    held = self.global_value(frame.module, name) if self.effects_enabled else None
                    return held if held is not None else Op(what=f"module global {name}")
                if "nonlocal" in kinds:
                    scope = scope.enclosing
                    continue
                # A name a nested function rebinds (`nonlocal`) or stores
                # into is shared state, like a module global (#872 review 11).
                where = _closure_changes(scope.node).get(name)
                if where is not None:
                    return Op(what=f"{name}, which {frame.module.ref}:{where} changes")
                value = self._frame_name(name, scope)
                if scope is not frame and isinstance(value, Rec):
                    # A dict an enclosing function holds outlives this call:
                    # any closure over it may change it.
                    value = replace(value, shared=True)
                return value
            scope = scope.enclosing
        return self.module_name(frame.module, name)

    def _frame_name(self, name: str, frame: _Frame) -> Any:
        if name in frame.cache:
            return frame.cache[name]
        if name in frame.evaluating:
            # ``x = x.strip()``: the name read inside its own rebinding holds
            # what the bindings read so far give.
            earlier = frame.partial.get(name) or []
            return _derived(*earlier) if earlier else _UNKNOWN
        frame.evaluating.add(name)
        options: list[Any] = []
        frame.partial[name] = options
        try:
            bindings = frame.local.get(name, [])
            replaced = self._replaced(name, bindings, frame)
            if replaced is not None:
                bindings = bindings[replaced:]
            elif name in frame.args:
                options.append(frame.args[name])
            for kind, value, statement in bindings:
                options.append(self._binding(kind, value, statement, name, frame))
            deciders: set[str] = set()
            if len(options) > 1:
                for _, _, statement in bindings:
                    for test in frame.conditions.get(id(statement), []):
                        deciders.update(_sources(self.value(test, frame))[0])
            value = options[0] if len(options) == 1 else _alt(options, frozenset(deciders))
            value = self._mutated(name, value, frame)
        finally:
            frame.evaluating.discard(name)
            frame.partial.pop(name, None)
        frame.cache[name] = value
        return value

    def _replaced(
        self, name: str, bindings: list[tuple[str, ast.AST, ast.AST]], frame: _Frame
    ) -> int | None:
        """The binding that replaces ``name`` before anything reads it, if any.

        ``pr_number = int(os.environ["PR_NUMBER"])`` as the body's first use of
        ``pr_number`` means the argument never reaches the request (#872
        review): flow order matters where a parameter is overwritten outright.
        """

        if frame.node is None:
            return None
        if any(
            isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Load)
            for nested in frame.nested
            for node in ast.walk(nested)
        ):
            # A closure reads the name when it runs, which may be before the
            # rebinding (#872 review 2).
            return None
        body = {id(statement) for statement in frame.node.body}
        for index, (kind, value, statement) in enumerate(bindings):
            if kind != "value" or id(statement) not in body:
                continue
            reads_itself = any(
                isinstance(node, ast.Name) and node.id == name
                for node in ast.walk(value)
            )
            position = (getattr(statement, "lineno", 0), getattr(statement, "col_offset", 0))
            earlier = [read for read in frame.loads.get(name, []) if read < position]
            if reads_itself or earlier:
                return None
            return index
        return None

    def _binding(
        self, kind: str, value: ast.AST, statement: ast.AST, name: str, frame: _Frame
    ) -> Any:
        if kind == "value":
            assert isinstance(value, ast.expr)
            return self.value(value, frame)
        if kind == "aug":
            assert isinstance(statement, ast.AugAssign)
            increment = self.value(statement.value, frame)
            previous = self._previous(name, frame)
            if isinstance(statement.op, ast.Add) and (_is_text(increment) or _is_text(previous)):
                return _join([previous, increment])
            return _derived(previous, increment)
        if kind in {"unpacked", "iter"}:
            assert isinstance(value, ast.expr)
            return _derived(self.value(value, frame))
        if kind == "def":
            assert isinstance(value, ast.FunctionDef | ast.AsyncFunctionDef)
            return Func(frame.module, value, frame)
        if kind == "import":
            assert isinstance(value, ast.alias) and isinstance(statement, ast.Import | ast.ImportFrom)
            resolution = self.resolver.resolve_local_import(frame.module, statement, value, name)
            dotted = _import_dotted(value, statement)
            if dotted is not None and resolution.reason == MODULE_NOT_FOUND:
                return Lib(dotted)
            return self._resolved(frame.module, resolution) or Op(what=name)
        return Op(what=name)

    def _previous(self, name: str, frame: _Frame) -> Any:
        options = [frame.args[name]] if name in frame.args else []
        options.extend(
            self._binding(kind, value, statement, name, frame)
            for kind, value, statement in frame.local.get(name, [])
            if kind != "aug"
        )
        return _alt(options) if options else _UNKNOWN

    def _mutated(self, name: str, value: Any, frame: _Frame) -> Any:
        """Apply the frame's item stores and in-place calls on ``name``."""

        changes = frame.mutations.get(name, [])
        if changes and isinstance(value, Request | Client):
            return self._reconfigured(value, changes, frame)
        options = value.options if isinstance(value, Alt) else (value,)
        if not changes or not any(isinstance(option, Rec | Seq | Op) for option in options):
            # A client, library object, function or string is not a container
            # a call on it can add to.
            return value
        added: list[tuple[str, Any]] = []
        extra: list[Any] = []
        open_ = False
        shared = False
        for change in changes:
            if isinstance(change, ast.Call):
                method = change.func.attr if isinstance(change.func, ast.Attribute) else ""
                arguments = [self.value(arg, frame) for arg in change.args]
                arguments += [self.value(keyword.value, frame) for keyword in change.keywords]
                if method in {"append", "extend", "insert", "add"}:
                    extra.extend(arguments)
                elif method == "update" and len(arguments) == 1 and isinstance(arguments[0], Rec):
                    added.extend(arguments[0].fields)
                    open_ = open_ or arguments[0].open
                    shared = shared or arguments[0].shared
                elif method == "setdefault" and arguments and isinstance(arguments[0], Lit):
                    added.append((str(arguments[0].value), _alt(arguments[1:] or [Lit(None)])))
                elif method in _PURE_METHODS or method in {"copy", "sort"}:
                    continue
                else:
                    open_ = True
                    extra.extend(arguments)
            elif isinstance(change, ast.Assign | ast.AugAssign | ast.AnnAssign):
                targets = change.targets if isinstance(change, ast.Assign) else [change.target]
                stored = self.value(change.value, frame) if change.value is not None else _UNKNOWN
                for target in targets:
                    if not (isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name) and target.value.id == name):
                        continue
                    key = self.value(target.slice, frame)
                    if isinstance(key, Lit) and isinstance(key.value, str) and not isinstance(change, ast.AugAssign):
                        added.append((key.value, stored))
                    else:
                        open_ = True
                        extra.append(stored)
            else:
                open_ = True
        if not added and not extra and not open_:
            return value
        if isinstance(value, Rec):
            value = replace(value, shared=value.shared or shared)
            fields = list(value.fields)
            for key, item in added:
                previous = next((old for name_, old in fields if name_ == key), None)
                fields = [pair for pair in fields if pair[0] != key]
                fields.append((key, _alt([previous, item]) if previous is not None else item))
            if extra:
                fields.append(("**", _derived(*extra)))
            return Rec(tuple(fields), value.open or open_, value.shared)
        if isinstance(value, Alt) and any(isinstance(option, Rec) for option in value.options):
            return Alt(
                tuple(
                    self._apply_to(option, added, extra, open_) for option in value.options
                ),
                value.deciders,
            )
        return _derived(value, *extra, *(item for _, item in added))

    def _reconfigured(self, value: Request | Client, changes: list[ast.AST], frame: _Frame) -> Any:
        """A request or client changed after it was built.

        Only a change to what it sends counts: its headers (`s.headers.update`,
        `s.headers["X"] = v`, `req.add_header`), its auth, a request's data or
        URL. Setting a request's method, or any attribute this read does not
        know, leaves the method unread. A client's own calls (`s.post(...)`)
        change nothing about it (#872 review 2).
        """

        headers = value.headers
        is_request = isinstance(value, Request)
        method = value.method if is_request else None
        data = value.data if is_request else None
        url = value.url if is_request else None
        auth = None if is_request else value.auth
        params = None if is_request else value.params
        for change in changes:
            if isinstance(change, ast.Call):
                func = change.func
                if not isinstance(func, ast.Attribute):
                    continue
                arguments = [self.value(arg, frame) for arg in change.args]
                if func.attr == "update" and _attribute_name(func.value) == "headers" and len(arguments) == 1:
                    update = arguments[0]
                    headers = (
                        _merge_headers(headers or Rec(()), update)
                        if isinstance(update, Rec)
                        else _derived(headers, update)
                    )
                elif is_request and func.attr in {"add_header", "add_unredirected_header"} and len(arguments) == 2:
                    key = arguments[0]
                    headers = (
                        _merge_headers(headers or Rec(()), Rec(((key.value, arguments[1]),)))
                        if isinstance(key, Lit) and isinstance(key.value, str)
                        else _derived(headers, *arguments)
                    )
                continue
            if not isinstance(change, ast.Assign | ast.AnnAssign | ast.AugAssign):
                continue
            stored = self.value(change.value, frame) if change.value is not None else _UNKNOWN
            targets = change.targets if isinstance(change, ast.Assign) else [change.target]
            for target in targets:
                if isinstance(target, ast.Subscript) and _attribute_name(target.value) == "headers":
                    key = self.value(target.slice, frame)
                    headers = (
                        _merge_headers(headers or Rec(()), Rec(((key.value, stored),)))
                        if isinstance(key, Lit) and isinstance(key.value, str)
                        else _derived(headers, key, stored)
                    )
                    continue
                if not isinstance(target, ast.Attribute):
                    continue
                if target.attr == "headers":
                    headers = stored
                elif target.attr == "auth":
                    auth = stored
                elif not is_request and target.attr == "params":
                    params = stored
                elif is_request and target.attr == "data":
                    data = stored
                elif is_request and target.attr in {"full_url", "selector", "host", "type"}:
                    url = _UNKNOWN
                elif is_request:
                    method = _UNKNOWN
        if is_request:
            return Request(url, data, headers, method)
        assert isinstance(value, Client)
        return Client(value.library, value.base_url, headers, auth, params)

    def _apply_to(self, value: Any, added: list[tuple[str, Any]], extra: list[Any], open_: bool) -> Any:
        if not isinstance(value, Rec):
            return _derived(value, *extra, *(item for _, item in added))
        fields = [pair for pair in value.fields if pair[0] not in {key for key, _ in added}]
        fields.extend(added)
        if extra:
            fields.append(("**", _derived(*extra)))
        return Rec(tuple(fields), value.open or open_, value.shared)

    def module_name(self, module: PythonModule, name: str) -> Any:
        key = (id(module), name)
        if key in self.names:
            return self.names[key]
        if name not in module.bindings:
            patched = (
                self.mutated.get(f"builtins.{name}")
                or self.library.get(f"builtins.{name}")
                or self.library.get("builtins.*")
                or self.whole.get("builtins")
                or self.whole.get("*")
            )
            value: Any = (
                Op(what=f"builtin {name}, which {patched} changes")
                if patched is not None and name in _BUILTIN_NAMES
                else _Builtin(name)
                if name in _BUILTIN_NAMES and not module.star_import
                else Op(what=name)
            )
        elif self.instances and (plain := _module_class(module, name)) is not None:
            value = _Class(module, plain)
        else:
            resolution = self.resolver.resolve(module, name)
            library = _module_library(module, name)
            if library is not None and resolution.reason == MODULE_NOT_FOUND:
                # An import no file in the read scope provides: a library.
                value = Lib(library)
            else:
                value = self._resolved(module, resolution) or Op(what=name)
        self.names[key] = value
        return value

    def _resolved(self, module: PythonModule, resolution: Resolution) -> Any | None:
        if resolution.module is not None and (
            resolution.definition is not None or resolution.value is not None
        ):
            name = resolution.steps[-1].get("name") if resolution.steps else None
            where = self.mutated.get(str(name))
            for step in resolution.steps:
                # `agent_config.METHOD` after `setattr(agent_config, key, …)`:
                # every module the chain reads may have been changed under
                # unseen names. A namespace change is keyed by the module it
                # changes, so it is matched by module, never by a bound name
                # (`from .settings import OWNER` binds a value).
                where = where or self.whole.get(_module_stem(str(step.get("path", ""))))
            # A namespace changed through a name the scan could not tie to one
            # module: every module-scope value is unknown.
            where = where or self.whole.get("*")
            if where is not None:
                # Changed from outside the tool's path: what it holds when the
                # tool runs is not what its definition says (#872 review 6).
                return Op(what=f"{name}, which {where} changes")
        if resolution.definition is not None and resolution.module is not None:
            return Func(resolution.module, resolution.definition)
        if resolution.value is not None and resolution.module is not None:
            return self.constant(resolution.module, resolution.value)
        if resolution.reason is not None:
            return Op(what=resolution.detail or resolution.reason)
        return None

    def constant(self, module: PythonModule, value: ast.expr) -> Any:
        if self.constants >= MAX_CONSTANT_DEPTH:
            return Op(what=f"a value more than {MAX_CONSTANT_DEPTH} constants deep")
        self.constants += 1
        try:
            frame = self.module_frame(module)
            result = self.value(value, frame)
            if isinstance(result, Client | Request) and isinstance(value, ast.Call):
                # A module-level client's hooks, auth and transport run on
                # every request it sends (#872 review 4).
                key = (id(module), id(value))
                if key not in self.configured:
                    self.configured.add(key)
                    self.handed_on(value, frame, _SENDS)
            name = _assigned_name(module, value)
            if name is not None:
                result = self._module_changes(module, name, result)
            if isinstance(result, Rec):
                result = replace(result, shared=True)
        finally:
            self.constants -= 1
        return result

    def _module_changes(self, module: PythonModule, name: str, value: Any) -> Any:
        """A module-level value changed by the module's own later statements.

        ``session.headers.update({...})`` or ``CONFIG["key"] = ...`` at the top
        level changes what every function reads.
        """

        changes: list[ast.AST] = []
        for statement in module.tree.body:
            if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
                func = statement.value.func
                if isinstance(func, ast.Attribute) and _root_name(func.value) == name:
                    changes.append(statement.value)
            elif isinstance(statement, ast.Assign | ast.AugAssign | ast.AnnAssign):
                targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
                if any(
                    isinstance(target, ast.Attribute | ast.Subscript) and _root_name(target) == name
                    for target in targets
                ):
                    changes.append(statement)
        if not changes:
            return value
        frame = self.module_frame(module)
        if isinstance(value, Request | Client):
            key = (id(module), hash(name))
            if isinstance(value, Client) and key not in self.configured:
                self.configured.add(key)
                for change in changes:
                    self._client_statement(change, frame)
            return self._reconfigured(value, changes, frame)
        stored = [
            self.value(change.value, frame)
            for change in changes
            if isinstance(change, ast.Assign | ast.AugAssign | ast.AnnAssign) and change.value is not None
        ]
        if isinstance(value, Rec):
            return Rec(value.fields + (("**", _derived(*stored)),), True)
        return _derived(value, *stored)

    def call_value(self, call: ast.Call, frame: _Frame) -> Any:
        """What a call returns, read without recording anything it does."""

        func = call.func
        receiver: Any = None
        dotted = self.dotted_callee(func, frame)
        if dotted is not None:
            callee: Any = dotted
        elif isinstance(func, ast.Attribute):
            receiver = self.value(func.value, frame)
            if isinstance(receiver, Lib):
                callee = Lib(f"{receiver.dotted}.{func.attr}")
            elif isinstance(receiver, Client):
                return Op(what="an HTTP response", inert=True)
            elif self.effects_enabled and (handle := _handle_of(receiver)) is not None:
                return self.handle_value(handle, func.attr, call, frame)
            else:
                return self._method_value(receiver, func.attr, call, frame)
        else:
            callee = self.value(func, frame)
        arguments = [self.value(arg, frame) for arg in call.args]
        keywords = {
            keyword.arg: self.value(keyword.value, frame)
            for keyword in call.keywords
            if keyword.arg is not None
        }
        if isinstance(callee, Lib):
            dotted = callee.dotted
            library, _, verb = dotted.rpartition(".")
            if dotted in _URLOPEN or (
                library in _HTTP_LIBRARIES and (verb in _VERBS or verb in {"request", "stream"})
            ):
                # What the service answers is not made from the request.
                return Op(what="an HTTP response", inert=True)
            if self.effects_enabled and self.replaced(dotted) is None:
                found = self.library_value(call, frame, dotted, arguments, keywords)
                if found is not None:
                    return found
            if dotted in _ENV_READERS:
                name = arguments[0] if arguments else keywords.get("key")
                default = arguments[1] if len(arguments) > 1 else keywords.get("default")
                if isinstance(name, Lit) and isinstance(name.value, str):
                    read = Op(envs=frozenset({name.value}), exact=True, data=True)
                    if default is not None and default != Lit(None):
                        return _alt([read, default])
                    return read
                return _derived(*arguments, result=True, data=True)
            if dotted in _CLIENTS:
                return Client(
                    _CLIENTS[dotted],
                    keywords.get("base_url"),
                    keywords.get("headers"),
                    keywords.get("auth"),
                    keywords.get("params"),
                )
            if dotted in _REQUEST_CLASSES:
                return Request(
                    arguments[0] if arguments else keywords.get("url", _UNKNOWN),
                    arguments[1] if len(arguments) > 1 else keywords.get("data"),
                    arguments[2] if len(arguments) > 2 else keywords.get("headers"),
                    arguments[5] if len(arguments) > 5 else keywords.get("method"),
                )
            if dotted == "json.dumps" and arguments:
                return arguments[0]
            if dotted == "logging.getLogger":
                return Lib("logging.Logger")
            if dotted == "typing.cast" and len(arguments) == 2:
                return arguments[1]
            if dotted in {"urllib.parse.urljoin", "os.path.join"} and len(arguments) == 2:
                return _join([arguments[0], Lit("/") if dotted == "os.path.join" else Lit(""), arguments[1]])
            if dotted in _BASIC_AUTH:
                return _derived(*arguments, *keywords.values())
            if dotted in _TRANSPORTS and all(_is_data(value) for value in [*arguments, *keywords.values()]):
                # A retrying transport or adapter configured with plain values.
                return Op(what=dotted, inert=True)
            if dotted in _PURE_FUNCTIONS:
                inputs = [*arguments, *keywords.values()]
                text = (
                    dotted in _TEXT_FUNCTIONS or dotted.startswith(_TEXT_FUNCTION_PREFIXES)
                ) and not (set(keywords) & {"cls", "default", "object_hook", "object_pairs_hook"})
                object_ = dotted in _OBJECT_FUNCTIONS
                return _derived(
                    *inputs,
                    result=True,
                    # `asyncio.sleep(0, result=client)` hands back the client.
                    data=text or (not object_ and all(_is_data(value) for value in inputs)),
                    inert=True,
                    # `base64.b64encode(b"user:pass")` is still written in the
                    # source; `Path("audit.log")` is an object, not text, so its
                    # `replace` moves a file (#872 review 3).
                    literal=not object_
                    and bool(inputs)
                    and all(_literal_only(value) for value in inputs),
                )
            return _derived(*arguments, *keywords.values(), result=True)
        if isinstance(callee, _Builtin):
            if callee.name == "open" and self.effects_enabled:
                path = arguments[0] if arguments else keywords.get("file", _UNKNOWN)
                return Handle("filesystem", "builtins", "file", target=path, built=_frame_key(frame))
            if (
                callee.name == "str"
                and len(arguments) == 1
                and isinstance(arguments[0], Handle)
                and arguments[0].role == "path"
            ):
                # `str(path)` is the path's text.
                return arguments[0].target if arguments[0].target is not None else _UNKNOWN
            if callee.name in {"str", "int", "float"} and len(arguments) == 1:
                inner = arguments[0]
                if isinstance(inner, Op | Lit | Tpl):
                    return inner if callee.name == "str" or not isinstance(inner, Tpl) else _derived(inner)
            if callee.name == "dict" and not arguments:
                return Rec(tuple(keywords.items()))
            if callee.name in _PURE_BUILTINS:
                return _derived(*arguments, *keywords.values())
            return _derived(*arguments, *keywords.values(), result=True)
        if isinstance(callee, Func):
            return self.returned(callee, call, frame)
        if type(callee) is _Class and not call.args and not call.keywords:
            return _Instance(callee.module, callee.node)
        if self.effects_enabled and isinstance(callee, Handle):
            role = effect_tables.FACTORIES.get((callee.library, callee.role))
            if role is not None:
                return replace(callee, role=role)
        return _derived(*arguments, *keywords.values(), result=True)

    def instance_attribute(self, instance: _Instance, attribute: str) -> Any:
        """``attribute`` of a plain class instance built with no arguments (#910).

        The class body's one assignment of it, read in the defining module,
        unless anything in the scope stores under that name: an instance's
        attribute can be changed by any code holding it.
        """

        where = self.mutated.get(attribute) or self.whole.get("*")
        if where is not None:
            return Op(what=f"{instance.node.name}.{attribute}, which {where} changes")
        values = [
            statement.value
            for statement in instance.node.body
            if isinstance(statement, ast.Assign | ast.AnnAssign)
            and statement.value is not None
            for target in (statement.targets if isinstance(statement, ast.Assign) else [statement.target])
            if isinstance(target, ast.Name) and target.id == attribute
        ]
        named = [
            statement
            for statement in instance.node.body
            if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
            and statement.name == attribute
        ]
        if len(values) != 1 or named:
            return Op(what=f"{instance.node.name}.{attribute}")
        return self.value(values[0], self.module_frame(instance.module))

    def _method_value(self, receiver: Any, method: str, call: ast.Call, frame: _Frame) -> Any:
        arguments = [self.value(arg, frame) for arg in call.args]
        if method in {"get", "pop", "setdefault"} and isinstance(receiver, Rec) and receiver.shared:
            return Op(what=f"{_spelling(call.func) or 'a module-level dict'}(…), a module-level dict")
        if method == "get" and isinstance(receiver, Rec) and arguments:
            key = arguments[0]
            if isinstance(key, Lit) and isinstance(key.value, str):
                found = receiver.get(key.value)
                default = arguments[1] if len(arguments) > 1 else Lit(None)
                if found is None:
                    return default if not receiver.open else _derived(receiver, default)
                return _alt([found, default])
        if method == "copy" and isinstance(receiver, Rec):
            return receiver
        if method == "format" and isinstance(receiver, Lit) and isinstance(receiver.value, str):
            keywords = {
                keyword.arg: self.value(keyword.value, frame)
                for keyword in call.keywords
                if keyword.arg is not None
            }
            formatted = _format(receiver.value, arguments, keywords)
            if formatted is not None:
                return formatted
        if method == "join" and isinstance(receiver, Lit) and isinstance(receiver.value, str):
            if len(arguments) == 1 and isinstance(arguments[0], Seq):
                parts: list[Any] = []
                for index, item in enumerate(arguments[0].items):
                    if index:
                        parts.append(receiver)
                    parts.append(item)
                return _join(parts)
        if method in {"strip", "rstrip", "lstrip"} and isinstance(receiver, Op) and receiver.exact:
            return receiver
        # What a method returns is plain data only when what it reads is: a dict
        # of clients hands back clients, and a default argument can be anything
        # (#872 review 2).
        arguments_data = all(_is_data(argument) for argument in arguments)
        if (method in _PURE_METHODS or method in _STRING_METHODS) and _is_data(receiver):
            return _derived(
                receiver, *arguments, result=True, data=arguments_data, inert=arguments_data
            )
        if method in _PURE_METHODS and _is_quiet(receiver):
            object_ = method in _OBJECT_METHODS
            return _derived(
                receiver,
                *arguments,
                result=True,
                data=arguments_data and not object_,
                inert=arguments_data,
            )
        return _derived(receiver, *arguments, result=True)

    def returned(self, func: Func, call: ast.Call, frame: _Frame) -> Any:
        # What a helper returns is read one call past the helper bound: reading
        # a value records nothing, and the call itself stays a limit there
        # (#913: `db = init_db_pool()` names the pool a query runs on).
        bound = MAX_DEPTH + 1 if self.effects_enabled else MAX_DEPTH
        if frame.depth >= bound or id(func.node) in self.stack:
            return Op(what=f"the value {func.node.name} returns")
        args = self.bind(func, call, frame)
        key = (func, tuple(sorted(args.items(), key=repr)))
        if key in self.returns:
            return self.returns[key]
        self.returns[key] = Op(what=f"the value {func.node.name} returns")
        inner = self.frame(func, args, via=frame.via, depth=frame.depth + 1)
        self.stack.append(id(func.node))
        try:
            values = [
                self.value(node.value, inner)
                for node in _returns(func.node)
                if node.value is not None
            ]
        finally:
            self.stack.pop()
        result = _alt(values) if values else Lit(None)
        self.returns[key] = result
        return result


@dataclass(frozen=True)
class _Builtin:
    name: str


class _Arguments:
    """A call's arguments, taken by position or keyword at most once each."""

    def __init__(self, call: ast.Call) -> None:
        self.positional = [arg for arg in call.args if not isinstance(arg, ast.Starred)]
        self.keywords = {keyword.arg: keyword.value for keyword in call.keywords if keyword.arg}
        self.spread = len(self.positional) != len(call.args) or any(
            keyword.arg is None for keyword in call.keywords
        )

    def take(self, index: int | None, name: str) -> ast.expr | None:
        if name in self.keywords:
            return self.keywords[name]
        if index is not None and index < len(self.positional):
            return self.positional[index]
        return None


# -- effects beyond HTTP: values (#913) ------------------------------------------

#: Libraries whose effects hand back plain data (a reply, a value).
_PLAIN_RESULTS = frozenset({"redis", "smtplib", "slack_sdk", "twilio"})
#: Verbs no method on what an effect handed back is taken to only read.
_WRITING_VERBS = (
    "add", "append", "create", "delete", "drop", "insert", "post", "publish", "put", "remove",
    "save", "send", "set", "update", "upload", "write",
)


def _frame_key(frame: _Frame) -> int:
    """Which function built a library object: ``0`` for module level."""

    return id(frame.node) if frame.node is not None else 0


#: ``Handle.built`` of an object built with an argument this read does not see
#: into: never local to any function.
_CONFIGURED = -1


def _unseen_argument(value: Any) -> bool:
    """A constructor argument that may carry behaviour: not plain data, not a
    recognised library object, not an inert library value."""

    if value is None:
        return False
    for option in _options(value):
        if isinstance(option, Lit | Handle) or _is_quiet(option):
            continue
        if isinstance(option, Lib) and (
            _inert_library(option.dotted) or option.dotted in effect_tables.CONSTRUCTORS
        ):
            continue
        return True
    return False


def _handle_of(value: Any) -> Handle | None:
    """The one kind of library object a value is, ignoring ``None``, or None."""

    if isinstance(value, Handle):
        return value
    if not isinstance(value, Alt):
        return None
    options = [option for option in value.options if not (isinstance(option, Lit) and option.value is None)]
    if not options or not all(isinstance(option, Handle) for option in options):
        return None
    first = options[0]
    if any((item.family, item.library, item.role) != (first.family, first.library, first.role) for item in options):
        return None
    if len(options) == 1:
        return first

    def agreed(attribute: str, default: Any) -> Any:
        values = {repr(getattr(item, attribute)) for item in options}
        return getattr(first, attribute) if len(values) == 1 else default

    targets = [item.target for item in options]
    return replace(
        first,
        service=agreed("service", None),
        target=None if any(target is None for target in targets) else _alt(targets),
        extra=agreed("extra", None),
        built=agreed("built", -1),
        credentials=tuple(dict.fromkeys(item for option in options for item in option.credentials)),
        host=tuple(sorted({item for option in options for item in option.host})),
    )


def _child_target(handle: Handle, role: str, first: Any) -> Any:
    """What a child object names: a MongoDB ``db.collection``, a bucket, a table."""

    if handle.library == "pymongo":
        if first is None:
            return None
        if role == "collection" and handle.target is not None:
            return _join([handle.target, Lit("."), first])
        return first
    if role in {"bucket", "collection"} or (handle.library == "boto3" and handle.role == "resource"):
        return first
    return handle.target


def _command_program(command: Any, shell: bool) -> Any:
    """The program a command runs: an argument list's first item, or the string."""

    options: list[Any] = []
    for option in _options(command):
        if isinstance(option, Seq):
            options.append(option.items[0] if option.items else _UNKNOWN)
        else:
            options.append(option)
    return options[0] if len(options) == 1 else Alt(tuple(options))


def _program_names(program: Any) -> tuple[list[str], bool]:
    """A command's program by file name only, never its arguments."""

    names: set[str] = set()
    unread = False
    for option in _options(program):
        if isinstance(option, Lib) and option.dotted == "sys.executable":
            names.add("sys.executable")
            continue
        if isinstance(option, Tpl):
            head = _leading_literal(option).lstrip()
            tokens = head.split(None, 1)
            # `f"git {verb}"`: the program is read only when its name ends
            # inside the literal.
            if tokens and (len(tokens) > 1 or head != head.rstrip()):
                option = Lit(tokens[0])
            else:
                unread = True
                continue
        found, missing = object_command(option)
        names.update(found)
        unread = unread or missing
    return sorted(names), unread


def _path_text(value: Any) -> str | None:
    """A path as a template, or None when it may name a place outside the repository.

    An absolute or home-relative literal, or one that climbs (``..``), is
    withheld (its digest is published instead); a piece shaped like a key is
    withheld as a URL's is. A parameter, an environment variable or an unread
    root is a placeholder.
    """

    if isinstance(value, Handle):
        value = value.target
    if not isinstance(value, Lit | Tpl | Op | Alt):
        return None
    rendered = _part(value)
    if not rendered or rendered == "{…}" or len(rendered) > 160:
        return None
    if rendered.startswith(("/", "~", "\\")) or re.match(r"[A-Za-z]:", rendered):
        return None
    pieces = re.split(r"[\\/]", rendered)
    if ".." in pieces:
        return None
    return "/".join(_REDACTED if _withheld_piece(piece) else piece for piece in pieces)


def _withheld_piece(piece: str) -> bool:
    """A path or name piece with a run shaped like a key, outside its
    placeholders. Words joined by ``_`` are runs of their own, so
    ``portfolio_demo_state.json`` is a name and ``ghp_<36 characters>`` is not."""

    literal = re.sub(r"\{[^{}]*\}", " ", piece)
    return any(_secret_piece(run) for run in re.split(r"[_\s]+", literal) if run)


def _name_text(value: Any) -> str | None:
    """A table, bucket, collection, key or channel name, when it is plainly a name."""

    if isinstance(value, Handle):
        value = value.target
    if not isinstance(value, Lit | Tpl | Op | Alt):
        return None
    rendered = _part(value)
    if not rendered or rendered == "{…}" or len(rendered) > 128:
        return None
    literal = re.sub(r"\{[^{}]*\}", "", rendered)
    for piece in re.split(r"[/:.@#|]", literal):
        if piece and (not re.fullmatch(r"[A-Za-z0-9_+-]+", piece) or _withheld_piece(piece)):
            return None
    return rendered


def _sql_statement(value: Any) -> effect_tables.Statement:
    """What a SQL value establishes, over every way it may be written."""

    results: list[effect_tables.Statement] = []
    for option in _options(value):
        if isinstance(option, Lit) and isinstance(option.value, str):
            results.append(effect_tables.sql_operation(option.value, complete=True))
        elif (
            isinstance(option, Tpl)
            and option.parts
            and isinstance(option.parts[0], Lit)
            and isinstance(option.parts[0].value, str)
        ):
            results.append(effect_tables.sql_operation(option.parts[0].value, complete=False))
        elif isinstance(option, Handle) and option.role == "statement" and isinstance(option.extra, str):
            # A SQLAlchemy `select(...)`, `insert(...)`: the construct says.
            operation = "read" if option.extra == "SELECT" else "write"
            results.append(effect_tables.Statement(operation, option.extra, None, False))
        else:
            results.append(effect_tables.Statement("unknown", None, None, True))
    operations = {item.operation for item in results}
    operation = next((name for name in ("write", "unknown", "read") if name in operations), None)
    keywords = sorted({item.keyword for item in results if item.keyword})
    tables = sorted({item.table for item in results if item.table and not _secret_piece(item.table)})
    return effect_tables.Statement(
        operation,
        "|".join(keywords) or None,
        "|".join(tables) or None,
        any(item.open for item in results),
    )


def _pipeline_operation(value: Any) -> str:
    """A MongoDB aggregation's direction: ``$out`` and ``$merge`` write."""

    if not isinstance(value, Seq):
        return "unknown"
    found = "read"
    for stage in value.items:
        if not isinstance(stage, Rec) or stage.open or stage.shared:
            found = "unknown"
            continue
        keys = {key for key, _ in stage.fields}
        if keys & {"$out", "$merge"}:
            return "write"
        if "**" in keys:
            found = "unknown"
    return found


def _hosts_of(value: Any) -> list[str]:
    """The hosts a host, URL or DSN value names: a literal, or an environment
    variable by name. A host the read cannot name is left out."""

    hosts: list[str] = []
    for option in _options(value):
        if isinstance(option, Lit) and isinstance(option.value, str):
            text = option.value.strip()
            host: str | None = None
            if "://" in text:
                try:
                    host = urlsplit(text).hostname
                except ValueError:
                    host = None
            elif re.search(r"(?:^|\s)host=", text):
                match = re.search(r"(?:^|\s)host=(\S+)", text)
                host = match.group(1) if match else None
            elif re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", text):
                host = text
            if host:
                if host.endswith(_CAPABILITY_HOST_SUFFIXES):
                    _label, dot, domain = host.partition(".")
                    host = f"{_REDACTED}{dot}{domain}"
                hosts.append(host)
        elif isinstance(option, Op) and option.exact and len(option.envs) == 1 and not option.params:
            hosts.append("env " + next(iter(option.envs)))
    return hosts


_HOST_LIBRARIES = frozenset({"psycopg2", "psycopg", "asyncpg", "pymysql", "sqlalchemy", "pymongo", "redis"})


def _hosts(dotted: str, kind: effect_tables.Kind, arguments: list[Any], keywords: dict[str, Any]) -> list[str]:
    if kind.library not in _HOST_LIBRARIES:
        return []
    candidates = [keywords[word] for word in ("host", "hostname", "dsn", "url", "conninfo") if word in keywords]
    if arguments and not dotted.startswith("psycopg2.pool."):
        candidates.append(arguments[0])
    return sorted({host for candidate in candidates for host in _hosts_of(candidate)})


def _construction_credentials(
    dotted: str, arguments: list[Any], keywords: dict[str, Any]
) -> tuple[tuple[tuple[str, Any], ...], ...]:
    """What a client is built with as a credential, by name only (#872's rules)."""

    found: list[dict[str, Any]] = []

    def add(entry: dict[str, Any], value: Any) -> None:
        params, envs = _sources(value)
        if envs:
            entry["env"] = sorted(envs)
        if params:
            entry["from"] = sorted(params)
        if _literal_fallback(value) or (not envs and not params and _literal_only(value)):
            entry["literal"] = True
        elif not envs and not params:
            entry["computed"] = True
        if entry not in found:
            found.append(entry)

    for word, value in keywords.items():
        if _secret_name(word) and not is_absent(value):
            add({"keyword": word}, value)
    for index, word in enumerate(effect_tables.POSITIONAL_CREDENTIALS.get(dotted, ())):
        if index < len(arguments) and _secret_name(word):
            add({"keyword": word}, arguments[index])
    sources = [keywords[word] for word in ("dsn", "url", "conninfo", "host") if word in keywords]
    if arguments:
        sources.append(arguments[0])
    for source in sources:
        for option in _options(source):
            if isinstance(option, Lit) and isinstance(option.value, str):
                text = option.value
                try:
                    secret = "://" in text and bool(urlsplit(text).password)
                except ValueError:
                    secret = False
                entry: dict[str, Any] | None = None
                if secret:
                    entry = {"userinfo": None, "literal": True}
                elif re.search(r"(?:^|\s)password=", text):
                    # A libpq `key=value` string: `"dbname=x password=…"`.
                    entry = {"keyword": "password", "literal": True}
                if entry is not None and entry not in found:
                    found.append(entry)
            elif isinstance(option, Tpl):
                for entry in _credentials(None, None, None, option):
                    if "userinfo" in entry and entry not in found:
                        found.append(entry)
    return tuple(_frozen(entry) for entry in found)


def _frozen(entry: dict[str, Any]) -> tuple[tuple[str, Any], ...]:
    return tuple((key, tuple(value) if isinstance(value, list) else value) for key, value in entry.items())


def _thawed(entry: tuple[tuple[str, Any], ...]) -> dict[str, Any]:
    return {key: list(value) if isinstance(value, tuple) else value for key, value in entry}


def _own_nodes(function: ast.FunctionDef | ast.AsyncFunctionDef) -> Iterator[ast.AST]:
    """A function's own nodes, not those of the functions and classes it defines."""

    stack: list[ast.AST] = list(function.body)
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda):
            continue
        stack.extend(ast.iter_child_nodes(node))


def _bound_names(node: ast.AST) -> Iterator[tuple[str, ast.expr | None]]:
    """The names a statement binds, with the value when it is one plain assignment."""

    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name):
                yield target.id, node.value
            else:
                for name in _names(target):
                    yield name, None
    elif isinstance(node, ast.AnnAssign):
        if isinstance(node.target, ast.Name) and node.value is not None:
            yield node.target.id, node.value
    elif isinstance(node, ast.AugAssign | ast.For | ast.AsyncFor | ast.Delete | ast.NamedExpr):
        targets = node.targets if isinstance(node, ast.Delete) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name):
                yield target.id, None
            for name in _names(target):
                yield name, None
    elif isinstance(node, ast.With | ast.AsyncWith):
        for item in node.items:
            if item.optional_vars is not None:
                for name in _names(item.optional_vars):
                    yield name, None
    elif isinstance(node, ast.Import | ast.ImportFrom):
        for alias in node.names:
            yield alias.asname or alias.name.split(".", 1)[0], None
    elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        yield node.name, None


def _described(value: Any) -> str | None:
    """What an unread receiver is, when the read knows."""

    if isinstance(value, Op):
        return value.what
    if isinstance(value, Alt):
        for option in value.options:
            if not _is_quiet(option):
                return _described(option)
    return None


def _assigned_name(module: PythonModule, value: ast.expr) -> str | None:
    """The name a module-level ``name = value`` statement binds ``value`` to."""

    for statement in module.tree.body:
        if (
            isinstance(statement, ast.Assign | ast.AnnAssign)
            and statement.value is value
        ):
            targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
            if len(targets) == 1 and isinstance(targets[0], ast.Name):
                return targets[0].id
    return None


def _name_words(name: str) -> list[str]:
    return [word.lower() for word in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", name)]


def _secret_name(name: str) -> bool:
    """A field, header or query name that carries a secret, by whole word."""

    if is_credential_key(name):
        return True
    words = _name_words(name)
    joined = "".join(words)
    if joined in _SECRET_WORDS or any(word in _SECRET_WORDS for word in words):
        return True
    return bool(words) and words[-1] == "key" and (len(words) == 1 or words[-2] in _KEY_QUALIFIERS)


def _inert_library(dotted: str) -> bool:
    """A library call with no effect outside the process, or a constructor."""

    return (
        dotted in _PURE_FUNCTIONS
        or dotted in _TRANSPORTS
        or dotted in _CLIENTS
        or dotted in _REQUEST_CLASSES
        or dotted in _BASIC_AUTH
        or dotted in effect_tables.ROW_FACTORIES
        or dotted.startswith("logging.")
    )


def _returns(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.Return]:
    found: list[ast.Return] = []
    stack: list[ast.AST] = list(node.body)
    while stack:
        item = stack.pop()
        if isinstance(item, ast.Return):
            found.append(item)
        if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda):
            continue
        stack.extend(ast.iter_child_nodes(item))
    return sorted(found, key=lambda item: (item.lineno, item.col_offset))


def _spelling(node: ast.AST) -> str:
    spelling = reference_spelling(node)
    if spelling is not None:
        return spelling
    if isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Call):
            inner = _spelling(node.value.func)
            return f"{inner or '…'}(…).{node.attr}"
        return f"….{node.attr}"
    return ""


def _format(template: str, arguments: list[Any], keywords: dict[str, Any]) -> Any | None:
    """``"{}/{name}".format(...)`` as a template; None when it cannot be read."""

    try:
        parsed = list(string.Formatter().parse(template))
    except ValueError:
        return None
    parts: list[Any] = []
    automatic = 0
    for literal, field_name, spec, conversion in parsed:
        if literal:
            parts.append(Lit(literal))
        if field_name is None:
            continue
        if spec or conversion:
            return None
        if field_name == "":
            if automatic >= len(arguments):
                return None
            parts.append(arguments[automatic])
            automatic += 1
        elif field_name.isdigit() and int(field_name) < len(arguments):
            parts.append(arguments[int(field_name)])
        elif field_name in keywords:
            parts.append(keywords[field_name])
        else:
            return None
    return _join(parts)


def _absolute(url: Any) -> bool:
    text = _leading_literal(url)
    return "://" in text


def _leading_literal(value: Any) -> str:
    if isinstance(value, Lit) and isinstance(value.value, str):
        return value.value
    if isinstance(value, Tpl) and value.parts and isinstance(value.parts[0], Lit):
        return str(value.parts[0].value)
    return ""


def _graphql_endpoint(url: Any) -> bool:
    """Whether a URL names a GraphQL endpoint: in its literal text or its variable."""

    names = _literal_text(url)
    params, envs = _sources(url)
    return "graphql" in f"{names} {' '.join(envs)}".lower()


def _literal_text(value: Any) -> str:
    if isinstance(value, Lit) and isinstance(value.value, str):
        return value.value
    if isinstance(value, Tpl):
        return "".join(str(part.value) for part in value.parts if isinstance(part, Lit))
    if isinstance(value, Alt):
        return " ".join(_literal_text(option) for option in value.options)
    return ""


def _merge_headers(defaults: Any, given: Any) -> Any:
    if given is None:
        return defaults
    if isinstance(defaults, Rec) and isinstance(given, Rec):
        keys = {key for key, _ in given.fields}
        return Rec(
            tuple([pair for pair in defaults.fields if pair[0] not in keys] + list(given.fields)),
            defaults.open or given.open,
        )
    return _derived(defaults, given)


def _methods(value: Any) -> list[str]:
    options = value.options if isinstance(value, Alt) else (value,)
    methods: list[str] = []
    for option in options:
        if not (isinstance(option, Lit) and isinstance(option.value, str)):
            return []
        method = option.value.upper()
        if method not in _METHOD_EFFECT:
            return []
        if method not in methods:
            methods.append(method)
    return sorted(methods)


def _call_effect(methods: list[str], graphql: str | None) -> str | None:
    if not methods:
        return None
    if graphql == "query":
        effects = ["read" if method == "POST" else _METHOD_EFFECT[method] for method in methods]
    elif graphql == "mutation":
        effects = ["write" if method in {"POST", "GET"} else _METHOD_EFFECT[method] for method in methods]
    elif graphql == "unknown" and "POST" in methods:
        return None
    else:
        effects = [_METHOD_EFFECT[method] for method in methods]
    for effect in ("destructive", "write", "read"):
        if effect in effects:
            return effect
    return None


def _graphql_operations(document: str) -> set[str] | None:
    """The operation kinds a GraphQL document defines; None when unreadable."""

    kinds: set[str] = set()
    depth = 0
    pending: str | None = None
    index, size = 0, len(document)
    while index < size:
        char = document[index]
        if char == "#":
            newline = document.find("\n", index)
            index = size if newline == -1 else newline + 1
            continue
        if char == '"':
            if document.startswith('"""', index):
                end = index + 3
                while True:
                    end = document.find('"""', end)
                    if end == -1:
                        return None
                    if document[end - 1] == "\\":
                        # `\"""` is an escaped quote inside a block string.
                        end += 3
                        continue
                    break
                index = end + 3
                continue
            index += 1
            while index < size and document[index] != '"':
                index += 2 if document[index] == "\\" else 1
            index += 1
            continue
        if char == "{":
            if depth == 0:
                if pending is None:
                    kinds.add("query")
                elif pending != "fragment":
                    kinds.add(pending)
                pending = None
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                return None
        elif (char.isalpha() or char == "_") and depth == 0:
            end = index
            while end < size and (document[end].isalnum() or document[end] == "_"):
                end += 1
            word = document[index:end]
            if pending is None:
                if word not in {"query", "mutation", "subscription", "fragment"}:
                    return None
                pending = word
            index = end
            continue
        index += 1
    if depth != 0 or pending is not None or not kinds:
        return None
    return kinds


# -- rendering -----------------------------------------------------------------


def _part(value: Any) -> str:
    if isinstance(value, Lit):
        return str(value.value)
    if isinstance(value, Op):
        if value.exact and len(value.params) == 1 and not value.envs:
            return "{" + next(iter(value.params)) + "}"
        if value.exact and len(value.envs) == 1 and not value.params:
            return "{env " + next(iter(value.envs)) + "}"
        return "{…}"
    if isinstance(value, Alt):
        labels = [_label(option) or "…" for option in value.options]
        if any(label != "…" for label in labels) and len(labels) <= 4:
            return "{" + "|".join(sorted(set(labels))) + "}"
        return "{…}"
    if isinstance(value, Tpl):
        return "".join(_part(part) for part in value.parts)
    return "{…}"


def _label(value: Any) -> str | None:
    """One option of a choice, as a URL placeholder shows it, or None."""

    if isinstance(value, Lit):
        shown = _shown(value)
        return None if shown is None else str(shown)
    if isinstance(value, Op) and value.exact:
        if len(value.params) == 1 and not value.envs:
            return next(iter(value.params))
        if len(value.envs) == 1 and not value.params:
            return "env " + next(iter(value.envs))
    return None


def _redact_url(rendered: str) -> str:
    """Withhold what in a URL could be a secret: a query value, a token in the path.

    A query value prints only when it is a short lowercase word or a number;
    a path piece is withheld when it looks like a key (``hooks.slack.com/
    services/T…/B…/<token>``, a bot token after ``:``). Over-withholding costs
    an ID a reviewer could have read; under-withholding publishes a secret.
    """

    rendered, _ = redact_url_credentials(rendered)
    head, question, query = rendered.partition("?")
    scheme, separator, rest = head.partition("://")
    # The host is a name, not a secret; only what follows it is checked.
    if separator:
        host, slash, path = rest.partition("/")
    else:
        host, slash, path = "", "", head
    capability = host in _CAPABILITY_HOSTS or host.endswith(_CAPABILITY_HOST_SUFFIXES)
    if host.endswith(_CAPABILITY_HOST_SUFFIXES):
        label, dot, domain = host.partition(".")
        host = f"{_REDACTED}{dot}{domain}"
    pieces: list[str] = []
    segments = path.split("/")
    for position, segment in enumerate(segments):
        parts = []
        for piece in segment.split(":"):
            plain = not piece or piece.startswith("{") or piece.startswith("[REDACTED")
            # On a webhook or bot URL every piece is the credential, except its
            # fixed words and a final method name (`sendMessage`).
            method_name = position == len(segments) - 1 and bool(_METHOD_NAME.fullmatch(piece))
            if not plain and (
                (capability and piece not in _CAPABILITY_WORDS and not method_name)
                or _secret_piece(piece)
            ):
                piece = _REDACTED
            parts.append(piece)
        pieces.append(":".join(parts))
    rendered = "/".join(pieces)
    if separator:
        rendered = f"{scheme}{separator}{host}{slash}{rendered}"
    if question:
        pairs = []
        for pair in query.split("&"):
            key, equals, value = pair.partition("=")
            if not equals:
                # A bare token: `?0123abcd…`.
                if not (key.startswith("{") or _PLAIN_QUERY_VALUE.fullmatch(key)):
                    key = _REDACTED
            elif (
                not value.startswith("{")
                and not value.startswith("[REDACTED")
                and (_secret_name(key) or not _PLAIN_QUERY_VALUE.fullmatch(value))
            ):
                value = _REDACTED
            pairs.append(f"{key}{equals}{value}")
        rendered += "?" + "&".join(pairs)
    return redact_text(rendered) or rendered


def _secret_piece(piece: str) -> bool:
    """A path piece shaped like a key: a long run, or letters and digits mixed.

    Checked per run between `-`, `.` and `~`, so `gemini-1.5-flash` stays a
    name while `hunter2`, a commit SHA or a UUID's runs are withheld.
    """

    for run in re.split(r"[-.~]", piece):
        letters = any(char.isalpha() for char in run)
        digits = sum(char.isdigit() for char in run)
        # Two digits or more: `us-central1` and `v1beta` are names, `a1b2c3d4` is not.
        if len(run) >= 20 or (len(run) >= 7 and letters and digits >= 2):
            return True
    return False


def _records(value: Any) -> list[Rec] | None:
    """The dicts a value may be, without the absent ones; None when not all dicts."""

    records: list[Rec] = []
    for option in value.options if isinstance(value, Alt) else (value,):
        if option is None or (isinstance(option, Lit) and option.value is None):
            continue
        if not isinstance(option, Rec):
            return None
        if option.fields or option.open:
            records.append(option)
    return records


def _render_url(url: Any, params: Any) -> str:
    rendered = _part(url) if isinstance(url, Lit | Tpl | Op | Alt) else "{…}"
    records = _records(params)
    if records is None or len(records) > 1 or (records and records[0].open):
        rendered += ("&" if "?" in rendered else "?") + "{…}"
    elif records:
        query = "&".join(
            f"{key}={_part(value) if isinstance(value, Lit | Tpl | Op | Alt) else '{…}'}"
            for key, value in records[0].fields
            if key != "**"
        )
        rendered += ("&" if "?" in rendered else "?") + query
    return _redact_url(rendered)


def _fields(payload: Any) -> list[dict[str, Any]]:
    if payload is None or (isinstance(payload, Lit) and payload.value is None):
        return []
    if not isinstance(payload, Rec):
        params, envs = _sources(payload)
        entry: dict[str, Any] = {"field": None}
        _describe(entry, payload, params, envs, secret=False)
        return [entry]
    fields: list[dict[str, Any]] = []
    for key, value in payload.fields:
        entry = {"field": None if key == "**" else key}
        params, envs = _sources(value)
        _describe(entry, value, params, envs, secret=key != "**" and _secret_name(key))
        fields.append(entry)
    if payload.open and not any(item["field"] is None for item in fields):
        fields.append({"field": None, "computed": True})
    return fields


def _describe(
    entry: dict[str, Any],
    value: Any,
    params: frozenset[str],
    envs: frozenset[str],
    *,
    secret: bool,
) -> None:
    if isinstance(value, Lit):
        if secret:
            entry["literal"] = True
        else:
            shown = _shown(value)
            if shown is None:
                entry["literal"] = True
            else:
                entry["value"] = shown
        return
    if isinstance(value, Alt) and all(isinstance(option, Lit) for option in value.options):
        shown_values = [_shown(option) for option in value.options]
        if secret or any(item is None for item in shown_values):
            entry["literal"] = True
        else:
            entry["values"] = sorted({str(item) for item in shown_values})
        if value.deciders:
            entry["decided_by"] = sorted(value.deciders)
        return
    if params:
        entry["from"] = sorted(params)
    if envs:
        entry["env"] = sorted(envs)
    if not params and not envs:
        if _literal_only(value):
            entry["literal"] = True
        else:
            entry["computed"] = True


def _shown(value: Lit) -> Any | None:
    """A literal plain enough to print: a number, a boolean, or a word without digits."""

    item = value.value
    if isinstance(item, bool) or item is None:
        return item
    if isinstance(item, int | float):
        return item if abs(item) < 10**6 else None
    if not isinstance(item, str) or not _PLAIN_FIELD_VALUE.fullmatch(item):
        return None
    if looks_like_secret_value(item) or redact_text(item) != item:
        return None
    return item


def _credentials(
    headers: Any, auth: Any, params: Any, url: Any, payload: Any = None
) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def add(kind: str, name: str | None, value: Any) -> None:
        params_, envs = _sources(value)
        entry: dict[str, Any] = {kind: name}
        if envs:
            entry["env"] = sorted(envs)
        if params_:
            entry["from"] = sorted(params_)
        if _literal_fallback(value) or (not envs and not params_ and _literal_only(value)):
            entry["literal"] = True
        elif not envs and not params_:
            entry["computed"] = True
        if entry not in found:
            found.append(entry)

    header_records = _records(headers)
    if header_records is None:
        if _sources(headers)[1]:
            add("header", None, headers)
    else:
        for record in header_records:
            for key, value in record.fields:
                if key != "**" and _credential_header(key):
                    add("header", key, value)
            if record.open:
                add("header", None, Rec(tuple(pair for pair in record.fields if pair[0] == "**")))
    if auth is not None and not (isinstance(auth, Lit) and auth.value is None):
        add("auth", None, auth)
    for record in _records(params) or []:
        for key, value in record.fields:
            if key != "**" and _secret_name(key):
                add("query", key, value)
    for record in _records(payload) or []:
        for key, value in record.fields:
            if key != "**" and _secret_name(key) and value != Lit(None):
                add("field", key, value)
    if isinstance(url, Tpl):
        text = ""
        for part in url.parts:
            if isinstance(part, Lit):
                text += str(part.value)
                continue
            key = text.rsplit("?", 1)[-1].rsplit("&", 1)[-1]
            if "?" in text and key.endswith("=") and _secret_name(key[:-1]):
                add("query", key[:-1], part)
            text += "{}"
        # A value spliced into the URL's userinfo: `https://user:{env PW}@host`.
        rendered = "".join(
            str(part.value) if isinstance(part, Lit) else "\0" for part in url.parts
        )
        scheme, separator, rest = rendered.partition("://")
        authority = rest.split("/", 1)[0].split("?", 1)[0]
        if separator and "@" in authority and "\0" in authority.rpartition("@")[0]:
            count = authority.rpartition("@")[0].count("\0") + scheme.count("\0")
            spliced = [part for part in url.parts if not isinstance(part, Lit)][:count]
            add("userinfo", None, _derived(*spliced))
    return found


def _credential_header(name: str) -> bool:
    return _secret_name(name)


def _literal_fallback(value: Any) -> bool:
    """Whether a literal is one of the values: ``os.getenv("KEY", "sk-test")``."""

    if isinstance(value, Alt):
        return any(
            (isinstance(option, Lit) and option.value not in (None, ""))
            or _literal_fallback(option)
            for option in value.options
        )
    if isinstance(value, Tpl):
        return any(_literal_fallback(part) for part in value.parts if not isinstance(part, Lit))
    return False


def _model_supplied(method: Any, url: Any, named: dict[str, Any], payload: Any) -> list[dict[str, str]]:
    into: dict[str, list[str]] = {}

    def note(value: Any, place: str) -> None:
        for param in sorted(_sources(value)[0]):
            places = into.setdefault(param, [])
            if place not in places:
                places.append(place)

    note(method, "method")
    note(url, "url")
    params = _records(named.get("params"))
    if params is None:
        note(named.get("params"), "query")
    for record in params or []:
        for key, value in record.fields:
            note(value, f"query {key}" if key != "**" else "query")
    if isinstance(payload, Rec):
        for key, value in payload.fields:
            note(value, f"field {key}" if key != "**" else "body")
    elif payload is not None:
        note(payload, "body")
    if named.get("headers") is not None:
        note(named["headers"], "headers")
    return [
        {"param": param, "into": place}
        for param, places in sorted(into.items())
        for place in places
    ]


# -- entry point ---------------------------------------------------------------


def read_tool_reach(
    resolver: ImportResolver,
    module: PythonModule,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    model_params: frozenset[str],
    enclosing: ast.FunctionDef | ast.AsyncFunctionDef | None = None,
    is_test: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    """What one tool function reaches, as JSON-safe evidence.

    Every patch to the HTTP libraries anywhere in the resolver's scope, outside
    the files ``is_test`` names, is a limit on a tool that sends
    (:func:`scope_patches`): it changes what every request does.

    ``model_params`` are the parameters the model supplies; any other
    parameter (a framework context, ``self``) is named but not model input.
    ``enclosing`` is the function that defines ``function``, when it is
    nested: its parameters are values this read cannot see.
    """

    test_rule = is_test or _looks_like_test
    names, whole = scope_mutations(resolver.scope_root, test_rule)
    reach = _Reach(resolver, names, whole, scope_library_patches(resolver.scope_root, test_rule))
    outer: _Frame | None = None
    if enclosing is not None:
        outer_func = Func(module, enclosing)
        outer = reach.frame(
            outer_func,
            {
                param.arg: Op(what=f"parameter {param.arg} of {enclosing.name}")
                for param in [
                    *enclosing.args.posonlyargs,
                    *enclosing.args.args,
                    *enclosing.args.kwonlyargs,
                    *(item for item in (enclosing.args.vararg, enclosing.args.kwarg) if item),
                ]
            },
            via=(),
            depth=0,
        )
    func = Func(module, function, outer)
    args: dict[str, Any] = {}
    arguments = function.args
    for param in [
        *arguments.posonlyargs,
        *arguments.args,
        *arguments.kwonlyargs,
        *(item for item in (arguments.vararg, arguments.kwarg) if item),
    ]:
        if param.arg in model_params:
            args[param.arg] = Op(
                params=frozenset({param.arg}),
                exact=True,
                data=_json_annotation(param.annotation),
            )
        else:
            args[param.arg] = Op(what=f"parameter {param.arg}, not supplied by the model")
    reach.walk(reach.frame(func, args, via=(), depth=0))
    if reach.calls or reach.dropped:
        patches = scope_patches(resolver.scope_root, test_rule)
        named = {patch["at"] for patch in patches}
        kept_stack = sorted(
            key[5:] for key in reach.library if key.startswith("kept:") and key[5:].split(".")[0] in _HTTP_STACK
        )
        holders = _holders(reach.library)
        for key, where in sorted(reach.library.items()):
            if not kept_stack or not key.startswith("unseen:") or where in named:
                continue
            if _held_store(key[7:], holders):
                # `self.http = requests` … `self.http.get = …`, or
                # `h.module.Session.prepare_request = …`: the HTTP stack kept on
                # an object, and a store through it (#872 reviews 18-20).
                named.add(where)
                patches.append(
                    {
                        "at": where,
                        "why": f"stores {key[7:].lstrip('^')} on an object that may hold {kept_stack[0]}, "
                        "which may change every request; not read",
                    }
                )
        for key, where in reach.library.items():
            # `sys.modules["requests"].get = send`, `r = requests;
            # r.Session.request = …`: a patch to the HTTP stack however the
            # module was reached (#872 review 14).
            head = key.split(".")[0]
            if key.startswith("class:") and where not in named:
                # `request.__class__.method = "DELETE"`: a class reached
                # through its instance or its bases may be the stack's.
                named.add(where)
                patches.append(
                    {
                        "at": where,
                        "why": f"replaces {key[6:]} on a class reached through an object or its bases, "
                        "which may change every request; not read",
                    }
                )
            elif (head in _HTTP_STACK or (head == "*" and key.rsplit(".", 1)[-1] in _STACK_NAMES)) and where not in named:
                # `M = import_module(name); M.get = …`: a module chosen at run
                # time may be the stack.
                named.add(where)
                patches.append({"at": where, "why": f"replaces {key}, which changes every request; not read"})
        for patch in patches:
            reach.limit_count += 1
            if patch not in reach.limits and len(reach.limits) < MAX_LIMITS:
                reach.limits.append(dict(patch))
    return summarize(
        reach.calls + reach.dropped,
        reach.limits,
        reach.limit_count,
        reach.truncated,
        module,
        function,
        listed=len(reach.calls),
        effects=reach.effects + reach.dropped_effects,
        listed_effects=len(reach.effects),
    )


# -- object bindings (#910) ----------------------------------------------------


@dataclass(frozen=True, eq=False)
class ValueSite:
    """Where an expression of an object construction is written.

    ``function`` is the function it is written in (None: module level);
    ``call`` and ``caller`` say how that function was entered, so its
    parameters hold what the caller passes. Without them a parameter is a
    value this read does not name.
    """

    module: PythonModule
    function: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    call: ast.Call | None = None
    caller: ValueSite | None = None


def read_object_values(
    resolver: ImportResolver,
    items: list[tuple[ValueSite, ast.AST | None]],
    *,
    is_test: Callable[[str], bool] | None = None,
) -> list[Any]:
    """The values of an object construction's arguments, read as a tool's are.

    One read for the whole construction: a URL, its headers, a command and its
    arguments, a filter. Values follow the same rules as :func:`read_tool_reach`
    — module constants through imports, ``os.getenv`` with its default, a
    repository helper's return — and also read an attribute of a plain class
    instance built with no arguments (``settings.url``), which is how an
    application commonly configures a server. Nothing is imported or run.
    """

    test_rule = is_test or _looks_like_test
    names, whole = scope_mutations(resolver.scope_root, test_rule)
    reach = _Reach(
        resolver, names, whole, scope_library_patches(resolver.scope_root, test_rule), instances=True
    )
    frames: dict[int, _Frame] = {}

    def frame_of(site: ValueSite) -> _Frame:
        found = frames.get(id(site))
        if found is not None:
            return found
        if site.function is None:
            frame = reach.module_frame(site.module)
        else:
            outer: _Frame | None = None
            for enclosing in _enclosing_functions(site.module.tree, site.function):
                outer = reach.frame(
                    Func(site.module, enclosing, outer), _unnamed_parameters(enclosing), via=(), depth=0
                )
            func = Func(site.module, site.function, outer)
            if site.call is not None and site.caller is not None:
                args = reach.bind(func, site.call, frame_of(site.caller))
            else:
                args = _unnamed_parameters(site.function)
            frame = reach.frame(func, args, via=(), depth=0)
        frames[id(site)] = frame
        return frame

    return [None if node is None else reach.value(node, frame_of(site)) for site, node in items]


def _unnamed_parameters(function: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, Any]:
    arguments = function.args
    return {
        param.arg: Op(what=f"parameter {param.arg} of {function.name}")
        for param in [
            *arguments.posonlyargs,
            *arguments.args,
            *arguments.kwonlyargs,
            *(item for item in (arguments.vararg, arguments.kwarg) if item),
        ]
    }


def _enclosing_functions(
    tree: ast.Module, function: ast.FunctionDef | ast.AsyncFunctionDef
) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """The functions that enclose ``function``, outermost first."""

    stack: list[tuple[ast.AST, tuple[ast.AST, ...]]] = [(tree, ())]
    while stack:
        node, path = stack.pop()
        for child in ast.iter_child_nodes(node):
            if child is function:
                return [item for item in path if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)]
            stack.append((child, (*path, child)))
    return []


def is_absent(value: Any) -> bool:
    """Not given, or ``None``."""

    return value is None or value == Lit(None)


def _options(value: Any) -> tuple[Any, ...]:
    return value.options if isinstance(value, Alt) else (value,)


def _url_host(text: str) -> str | None:
    try:
        parts = urlsplit(text)
        host = parts.hostname
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() not in {"http", "https", "ws", "wss"} or not host:
        return None
    if host.endswith(_CAPABILITY_HOST_SUFFIXES):
        _label, dot, domain = host.partition(".")
        host = f"{_REDACTED}{dot}{domain}"
    return f"{host}:{port}" if port else host


def object_hosts(url: Any) -> tuple[list[str], bool]:
    """The hosts a URL value may name, never its path or query, and whether
    some option's host is not read.

    A literal (or a template whose host is written out) gives its host; a URL
    taken whole from an environment variable, or built on one, gives
    ``env NAME``. Anything else — a builder's parameter, a computed value, a
    host spliced in — is not read.
    """

    hosts: set[str] = set()
    unread = False
    options: list[Any] = []
    for option in _options(url):
        if isinstance(option, Tpl) and isinstance(option.parts[0], Alt):
            # ``f"{os.getenv('BASE', 'http://host:8080')}/sse"``: each way the
            # base may be set, with the rest of the URL.
            options.extend(_join([choice, *option.parts[1:]]) for choice in option.parts[0].options)
        else:
            options.append(option)
    for option in options:
        host: str | None = None
        if isinstance(option, Lit) and isinstance(option.value, str):
            host = _url_host(option.value)
        elif isinstance(option, Tpl):
            leading = _leading_literal(option)
            _scheme, separator, rest = leading.partition("://")
            # The host is read only when the literal runs past it.
            if separator and ("/" in rest or "?" in rest):
                host = _url_host(leading)
            first = option.parts[0]
            if host is None and isinstance(first, Op) and first.exact and len(first.envs) == 1 and not first.params:
                host = "env " + next(iter(first.envs))
        elif isinstance(option, Op) and option.exact and len(option.envs) == 1 and not option.params:
            host = "env " + next(iter(option.envs))
        if host is None:
            unread = True
        else:
            hosts.add(host)
    return sorted(hosts), unread


def object_command(command: Any) -> tuple[list[str], bool]:
    """The program a stdio server runs, by its file name only."""

    names: set[str] = set()
    unread = False
    for option in _options(command):
        if isinstance(option, Lit) and isinstance(option.value, str) and option.value.strip():
            program = PurePosixPath(option.value.strip().split()[0].replace(os.sep, "/")).name
            # A file name shaped like a key is withheld, as a URL's path piece is.
            names.add(_REDACTED if _secret_piece(program) else program)
        elif isinstance(option, Op) and option.exact and len(option.envs) == 1 and not option.params:
            names.add("env " + next(iter(option.envs)))
        else:
            unread = True
    return sorted(names), unread


def object_credentials(
    *, headers: Any = None, url: Any = None, env: Any = None
) -> tuple[list[dict[str, Any]], bool]:
    """Credential sources of a server connection, by name only (#872's rules).

    Headers and a URL's query or userinfo are read as a request's are; a stdio
    server's ``env`` entries whose names say they carry a secret are
    ``server_env``. The second value is True when headers or an environment
    are given but are not a dict this read can enumerate: a credential could
    be in what is not read.
    """

    found = _credentials(None if is_absent(headers) else headers, None, None, url)
    unread = False
    for given in (headers, env):
        if is_absent(given):
            continue
        records = _records(given)
        # A module-level dict can be changed by any code (#872 review 9): its
        # entries are not taken as written.
        if records is None or any(record.open or record.shared for record in records):
            unread = True
    for option in _options(url):
        if not (isinstance(option, Lit) and isinstance(option.value, str)):
            continue
        # A credential written into a literal URL: named, never printed.
        try:
            parts = urlsplit(option.value)
        except ValueError:
            continue
        if parts.username or parts.password:
            entry: dict[str, Any] = {"userinfo": None, "literal": True}
            if entry not in found:
                found.append(entry)
        for pair in parts.query.split("&") if parts.query else ():
            key = pair.partition("=")[0]
            if key and _secret_name(key):
                entry = {"query": key, "literal": True}
                if entry not in found:
                    found.append(entry)
    for record in ([] if is_absent(env) else _records(env) or []):
        for key, value in record.fields:
            if key == "**" or not _secret_name(key):
                continue
            params, envs = _sources(value)
            entry = {"server_env": key}
            if envs:
                entry["env"] = sorted(envs)
            if params:
                entry["from"] = sorted(params)
            if _literal_fallback(value) or (not envs and not params and _literal_only(value)):
                entry["literal"] = True
            elif not envs and not params:
                entry["computed"] = True
            if entry not in found:
                found.append(entry)
    return found, unread


def object_strings(value: Any) -> tuple[list[str] | None, bool]:
    """A literal list of names (a tool filter), None when absent, and whether
    it is not read."""

    if is_absent(value):
        return None, False
    if isinstance(value, Seq) and all(
        isinstance(item, Lit) and isinstance(item.value, str) for item in value.items
    ):
        return sorted({item.value for item in value.items}), False
    return None, True


def object_named(value: Any) -> bool:
    """Whether a value is written out: literals, or an environment variable."""

    if is_absent(value) or isinstance(value, Lit):
        return True
    if isinstance(value, Tpl):
        return all(object_named(part) for part in value.parts)
    if isinstance(value, Seq):
        return all(object_named(item) for item in value.items)
    if isinstance(value, Rec):
        return not value.open and not value.shared and all(object_named(item) for _, item in value.fields)
    if isinstance(value, Alt):
        return all(object_named(option) for option in value.options)
    if isinstance(value, Op):
        return value.literal or (value.exact and bool(value.envs) and not value.params)
    return False


def object_record(value: Any) -> Rec | None:
    """The one dict a value is (an SDK server's ``params``), or None."""

    if not isinstance(value, Rec) or value.open or value.shared:
        return None
    return value


def object_digest(*values: Any) -> str:
    """A digest of values that names none of them: a literal secret is hashed,
    never printed. Deterministic across runs and processes."""

    return hashlib.sha256(
        json.dumps([_canonical(value) for value in values], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _canonical(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Lit):
        item = value.value
        return ["lit", item if item is None or isinstance(item, str | int | float | bool) else repr(item)]
    if isinstance(value, Tpl):
        return ["tpl", [_canonical(part) for part in value.parts]]
    if isinstance(value, Rec):
        return ["rec", [[key, _canonical(item)] for key, item in value.fields], value.open]
    if isinstance(value, Seq):
        return ["seq", [_canonical(item) for item in value.items]]
    if isinstance(value, Alt):
        return [
            "alt",
            sorted(json.dumps(_canonical(option), sort_keys=True) for option in value.options),
            sorted(value.deciders),
        ]
    if isinstance(value, Op):
        return ["op", sorted(value.params), sorted(value.envs), value.exact, value.literal]
    if isinstance(value, Lib):
        return ["lib", value.dotted]
    if isinstance(value, Func):
        return ["func", value.node.name]
    if isinstance(value, Handle):
        return ["handle", value.library, value.role, value.service, _canonical(value.target)]
    if isinstance(value, _Class):
        return ["class", value.node.name]
    return ["value", type(value).__name__]


#: Libraries whose behaviour a patch can change for every request.
_HTTP_STACK = ("aiohttp", "http", "httpx", "requests", "socket", "ssl", "urllib", "urllib3")
#: Names an HTTP stack module or class sends through.
_STACK_NAMES = frozenset(
    {
        "AsyncClient", "Client", "ClientSession", "HTTPAdapter", "OpenerDirector", "PoolManager", "Request",
        "Session", "delete", "get", "head", "method", "options", "patch", "post", "put", "request", "send",
        "stream", "urlopen",
        # What a request passes through on its way (#872 review 19). An
        # instance's everyday attributes (`headers`, `data`) are not: a path
        # through a holder the scan saw is matched by the holder's name.
        "HTTPConnection", "HTTPSConnection", "HTTPTransport", "AsyncHTTPTransport", "_request",
        "_send_single_request", "build_request", "endheaders", "full_url", "get_method",
        "handle_async_request", "handle_request", "merge_environment_settings", "prepare_request",
        "putheader", "putrequest", "rebuild_auth", "rebuild_method", "request_encode_body",
        "request_encode_url", "resolve_redirects", "send_request",
    }
)
#: Classes a request passes through, matched on any attribute a store goes
#: through (#872 review 20).
_STACK_CLASSES = frozenset(
    {name for name in _STACK_NAMES if name[:1].isupper()}
    | {
        "AbstractHTTPHandler", "BaseHandler", "ClientRequest", "HTTPConnectionPool", "HTTPHandler",
        "HTTPSConnectionPool", "HTTPSHandler", "OpenerDirector", "PreparedRequest", "TCPConnector",
    }
)
#: The stack's exceptions not named like one (`httpx.StreamClosed`,
#: `http.client.RemoteDisconnected`).
_STACK_EXCEPTIONS = frozenset(
    {
        "BadStatusLine", "CannotSendHeader", "CannotSendRequest", "CookieConflict", "ImproperConnectionState",
        "IncompleteRead", "InvalidURL", "LineTooLong", "NotConnected", "RemoteDisconnected", "RequestNotRead",
        "ResponseNotRead", "ResponseNotReady", "StreamClosed", "StreamConsumed", "TooManyRedirects",
        "UnimplementedFileMode", "UnknownProtocol", "UnknownTransferEncoding", "UnsupportedProtocol",
    }
)
#: The stack's modules a request passes through, besides the top-level ones.
_STACK_MODULES = frozenset(
    {
        "aiohttp.client", "aiohttp.client_reqrep", "aiohttp.connector", "http.client", "http.cookiejar",
        "httpx._api", "httpx._client", "httpx._transports", "httpx._transports.default", "requests.adapters",
        "requests.api", "requests.models", "requests.sessions", "urllib.request", "urllib3.connection",
        "urllib3.connectionpool", "urllib3.poolmanager", "urllib3.util", "urllib3.util.connection",
        "urllib3.util.ssl_",
    }
)
#: Calls that install or instrument something in the HTTP stack.
_STACK_INSTALLERS = ("install_cache", "install_opener", "instrument", "monkey", "patch_all")


def http_patches(tree: ast.Module, ref: str) -> list[dict[str, str]]:
    """Places in one module that patch an HTTP library, anywhere in the file.

    A store into an attribute of `requests`, `httpx`, `urllib3`, `http`,
    `socket` and the like, a `setattr` on one, or a call that installs or
    instruments the stack (`install_opener`, `patch_all`,
    `RequestsInstrumentor().instrument()`) changes what every request in the
    process does. It is read wherever it is in the scope, not only on the path
    a tool takes (#872 review 5).
    """

    stack: dict[str, str] = {}
    #: Other imported names: `ddtrace.patch(...)` patches, `requests.patch(...)`
    #: and `session.patch(...)` send.
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".", 1)[0]
                if alias.name.split(".", 1)[0] in _HTTP_STACK:
                    stack[local] = alias.name
                else:
                    modules.add(local)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            if node.module.split(".", 1)[0] in _HTTP_STACK:
                for alias in node.names:
                    stack[alias.asname or alias.name] = f"{node.module}.{alias.name}"
            else:
                modules.update(alias.asname or alias.name for alias in node.names)
    found: list[dict[str, str]] = []

    def add(node: ast.AST, why: str) -> None:
        entry = {"at": f"{ref}:{getattr(node, 'lineno', 0)}", "why": why}
        if entry not in found:
            found.append(entry)

    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AugAssign | ast.AnnAssign):
            targets = [node.target]
        stored = getattr(node, "value", None)
        for target in targets:
            # `requests.adapters.DEFAULT_RETRIES = 3` tunes the stack; a
            # function or object stored there, or a class's default method,
            # replaces what it does.
            if (
                isinstance(target, ast.Attribute)
                and _root_name(target) in stack
                and not _setting(_spelling(target).split("."), stored)
            ):
                add(node, f"patches {_spelling(target)}, which changes every request; not read")
        if isinstance(node, ast.Call):
            spelling = _spelling(node.func)
            if (
                spelling == "setattr"
                and node.args
                and _root_name(node.args[0]) in stack
            ):
                add(node, f"patches {_spelling(node.args[0])} with setattr, which changes every request; not read")
            elif isinstance(node.func, ast.Attribute | ast.Name) and (
                any(word in spelling for word in _STACK_INSTALLERS)
                or (
                    isinstance(node.func, ast.Attribute)
                    and (node.func.attr == "patch" or node.func.attr.startswith("patch_"))
                    and _root_name(node.func) in modules
                )
            ):
                add(node, f"calls {spelling or 'an installer'}, which may change every request; not read")
    return found


#: Constructors whose result is a new object the function owns.
_FRESH_CALLS = frozenset(
    {"AsyncClient", "Client", "ClientSession", "Request", "Session", "copy", "deepcopy", "dict", "list", "set"}
)


def _fresh(value: ast.AST | None) -> bool:
    """A value that is a new object: a display, a comprehension, a constructor."""

    if isinstance(
        value, ast.Dict | ast.List | ast.Set | ast.Tuple | ast.ListComp | ast.DictComp | ast.SetComp
    ):
        return True
    if isinstance(value, ast.Call):
        func = value.func
        name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
        return name in _FRESH_CALLS
    return False


def _stored_name(target: ast.AST) -> str | None:
    """The name a store writes under: `x.METHOD`, `x["method"]`, else the container's."""

    if isinstance(target, ast.Attribute):
        return target.attr
    if isinstance(target, ast.Subscript):
        key = target.slice
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            return key.value
        return _stored_name(target.value) if isinstance(target.value, ast.Attribute | ast.Subscript) else (
            target.value.id if isinstance(target.value, ast.Name) else None
        )
    if isinstance(target, ast.Name):
        return target.id
    return None


@functools.lru_cache(maxsize=256)
def _closure_changes_cached(node: ast.AST) -> tuple[tuple[str, int], ...]:
    found: dict[str, int] = {}
    for inner in ast.walk(node):
        if inner is node or not isinstance(inner, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            continue
        arguments = inner.args
        own = {
            argument.arg
            for argument in [
                *arguments.posonlyargs,
                *arguments.args,
                *arguments.kwonlyargs,
                *(item for item in (arguments.vararg, arguments.kwarg) if item),
            ]
        }
        declared: set[str] = set()
        for child in ast.walk(inner):
            if isinstance(child, ast.Nonlocal):
                declared.update(child.names)
                for name in child.names:
                    found.setdefault(name, child.lineno)
            elif isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                own.add(child.id)
        own -= declared
        for child in ast.walk(inner):
            changed: ast.AST | None = None
            if isinstance(child, ast.Assign | ast.AugAssign | ast.AnnAssign | ast.Delete):
                targets = child.targets if isinstance(child, ast.Assign | ast.Delete) else [child.target]
                for target in targets:
                    if isinstance(target, ast.Attribute | ast.Subscript):
                        changed = target.value
            elif isinstance(child, ast.Call):
                func = child.func
                if isinstance(func, ast.Attribute) and func.attr in (
                    _CONTAINER_METHODS | _DICT_CHANGES | _STORING_METHODS
                ):
                    changed = func.value
                elif _spelling(func) in {"setattr", "delattr", "setitem", "operator.setitem"} and child.args:
                    changed = child.args[0]
            root = None if changed is None else _root_name(changed)
            if root is not None and root not in own:
                found.setdefault(root, child.lineno)  # type: ignore[attr-defined]
    return tuple(sorted(found.items()))


def _closure_changes(node: ast.AST | None) -> dict[str, int]:
    """Names of `node`'s scope a function nested in it changes, with the line.

    A `nonlocal` rebinding, or a store into what the name holds (an item, an
    attribute, a container method), made by a function nested in `node`
    changes state every closure over it shares (#872 review 11).
    """
    return {} if node is None else dict(_closure_changes_cached(node))


def _module_stem(ref: str) -> str:
    parts = ref.split("/")
    stem = parts[-1].removesuffix(".py")
    return parts[-2] if stem == "__init__" and len(parts) > 1 else stem


#: Calls that return a module object (#872 review 10).
_MODULE_LOOKUPS = frozenset({"__import__", "getmodule", "import_module", "reload"})
#: Attributes that are some module's namespace the scan cannot name.
_FOREIGN_NAMESPACES = frozenset({"__builtins__", "__globals__", "f_globals", "f_locals"})
#: Calls whose result holds the elements of their (last) argument.
_ELEMENT_CALLS = frozenset({"filter", "frozenset", "iter", "list", "next", "reversed", "set", "sorted", "tuple"})
#: Methods whose result holds what their container holds.
_ELEMENT_METHODS = frozenset({"get", "items", "pop", "setdefault", "values"})
#: Methods that store their arguments into a container.
_STORING_METHODS = frozenset({"__setitem__", "add", "append", "extend", "insert", "setdefault", "update"})
#: Dict methods that change the dict under keys they are given.
_DICT_CHANGES = frozenset({"__setitem__", "clear", "pop", "popitem", "setdefault", "update"})
#: Attributes whose change changes every attribute a module reads as.
_MODULE_HOOKS = frozenset({"__class__", "__dict__", "__getattr__", "__getattribute__"})
#: Calls that only read a namespace passed to them; any other may change it.
_NAMESPACE_READERS = frozenset(
    {
        "_eval_type", "_evaluate", "all", "any", "critical", "debug", "deepcopy", "dict", "dir", "dump",
        "dumps", "enumerate", "error", "evaluate", "exception", "filter", "format", "frozenset", "get",
        "get_type_hints", "getattr", "hasattr", "hash",
        "id", "info", "isinstance", "items", "iter", "keys", "len", "list", "log", "map", "pformat",
        "pprint", "print", "repr", "set", "sorted", "str", "sum", "tuple", "values", "warning", "zip",
    }
)
#: Calls a module object handed to is only read by, or followed through.
_MODULE_READERS = _ELEMENT_CALLS | frozenset(
    {
        "callable", "critical", "debug", "delattr", "dir", "error", "exception", "format", "getattr",
        "getdoc", "getfile", "getmembers", "getmodule", "getsource", "getsourcefile", "hasattr", "hash",
        "id", "import_module", "info", "isclass", "isfunction", "isinstance", "ismodule", "issubclass",
        "len", "log", "print", "reload", "repr", "setattr", "signature", "str", "type", "vars", "warning",
    }
)
#: Methods the import system calls with a module.
_IMPORT_HOOK_METHODS = frozenset({"create_module", "exec_module", "load_module", "module_repr"})
#: Calls that register a function the import system calls with a module.
_IMPORT_HOOK_REGISTRARS = frozenset(
    {"add_import_hook", "post_import_hook", "register_import_hook", "register_post_import_hook", "when_imported"}
)
#: How many steps a module object is followed through names and calls.
MAX_OBJECT_DEPTH = 64
#: Bounds on following an imported name through the modules that bind it;
#: past either, it may be any module.
MAX_EXPANSIONS = 1024
MAX_EXPANSION_HOPS = 16

#: A parameter a change or a store reaches: ``("param", function, position,
#: parameter, is_method)``. ``function`` is None for a lambda; ``position`` is
#: -1 for keyword-only, and ``parameter`` is spelled ``*args``/``**kwargs``
#: for the rest.
_Parameter = tuple[str, str | None, int, str, bool]
#: What an expression may be, as a module (#872 review 10):
#: - a module stem or name, or ``*`` for any module;
#: - ``=name``: a name only read, a module-level object or one the file does
#:   not bind;
#: - ``~name``: a name imported ``from`` a module, or an attribute of a
#:   module (a submodule or a value in it);
#: - a :data:`_Parameter`;
#: - ``("call", function, positional, keywords)``: what a call returns;
#: - ``("ns", key)``: the namespace dict of what ``key`` is (`vars(m)`).
_Key = Any
_OWN = "<own>"
_ANY = "*"
#: An object the scan does not follow: a change to it counts as unclassified.
_UNSEEN = "?"
#: A class reached by introspection (`type(x)`, `Sub.__mro__[1]`), which may
#: be any class, an HTTP stack's among them (#872 review 18); and an
#: instance's own class (`type(self)`), which is not.
_CLASS = "@class"
_OWN_CLASS = "@own"


def _is_parameter(key: _Key) -> bool:
    return isinstance(key, tuple) and key[0] == "param"


def _is_namespace(key: _Key) -> bool:
    return isinstance(key, tuple) and key[0] == "ns"


def _is_call(key: _Key) -> bool:
    return isinstance(key, tuple) and key[0] == "call"


def _unwrap(key: _Key) -> _Key:
    return key[1] if _is_namespace(key) else key


def _strong(key: _Key) -> bool:
    """A key the scan knows is a module: imported with `import`, looked up."""
    key = _unwrap(key)
    return isinstance(key, str) and not key.startswith(("=", "~", "@")) and key != _UNSEEN


def _named_module(key: _Key) -> str | None:
    """The module file name (or `*`) a key names, imported either way; else None.

    A key keeps the dotted path it was reached by (`urllib.request`,
    `~pkg.agent_config`); a module of the scope is matched by its last part.
    """
    path = _module_path(key)
    return None if path is None else path.rsplit(".", 1)[-1] if path != _ANY else path


def _module_path(key: _Key) -> str | None:
    """The dotted path a key names (`urllib.request.Request`); else None."""
    key = _unwrap(key)
    if not isinstance(key, str) or key.startswith(("=", "@")) or key == _UNSEEN:
        return None
    return key.lstrip("~") or key


def _module_ish(key: _Key) -> bool:
    """Anything but a name only read: it may be a module."""
    return not (isinstance(key, str) and (key.startswith("=") or key in {_UNSEEN, _OWN_CLASS}))


def _module_level(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    """The statements that run at module level: inside `if`, `try` and
    `with` blocks too, not inside a function or class."""
    for statement in body:
        yield statement
        if isinstance(statement, ast.If | ast.While | ast.For | ast.AsyncFor | ast.With | ast.AsyncWith):
            yield from _module_level([*statement.body, *getattr(statement, "orelse", [])])
        elif isinstance(statement, ast.Try | ast.TryStar):
            yield from _module_level(
                [
                    *statement.body,
                    *(item for handler in statement.handlers for item in handler.body),
                    *statement.orelse,
                    *statement.finalbody,
                ]
            )


def _unpacked(target: ast.AST, value: ast.AST | None) -> Iterator[tuple[ast.AST, ast.AST | None]]:
    """Each target an assignment stores into, with what it stores there:
    `self.http, self.x = requests, 1` pairs them; an unpacked call gives each
    target the whole value."""
    if isinstance(target, ast.Starred):
        yield from _unpacked(target.value, value)
    elif isinstance(target, ast.Tuple | ast.List):
        values = (
            value.elts
            if isinstance(value, ast.Tuple | ast.List)
            and len(value.elts) == len(target.elts)
            and not any(isinstance(item, ast.Starred) for item in [*target.elts, *value.elts])
            else [value] * len(target.elts)
        )
        for item, item_value in zip(target.elts, values, strict=True):
            yield from _unpacked(item, item_value)
    else:
        yield target, value


def _trail(owner: ast.AST) -> tuple[list[str], ast.AST]:
    """The names an attribute is stored through, and where they start:
    `self.libs["http"]` is `libs.http` from `self`; `self.__dict__["http"]`
    and `getattr(self, "http")` are `http`; any other item is `[]`."""
    trail: list[str] = []
    while True:
        if isinstance(owner, ast.Attribute):
            if owner.attr != "__dict__":
                trail.insert(0, owner.attr)
            owner = owner.value
        elif isinstance(owner, ast.Subscript):
            key = _literal(owner.slice)
            trail.insert(0, key if isinstance(key, str) and key.isidentifier() else "[]")
            owner = owner.value
        elif (
            isinstance(owner, ast.Call)
            and _spelling(owner.func) in {"getattr", "vars"}
            and owner.args
            and (len(owner.args) == 1 or _literal(owner.args[1]) is not None)
        ):
            if len(owner.args) > 1:
                trail.insert(0, _literal(owner.args[1]))  # type: ignore[arg-type]
            owner = owner.args[0]
        else:
            return trail, owner


def _holder_of(target: ast.AST) -> str | None:
    """The attribute a stored value is kept under: `self.http = …`,
    `self.__dict__["http"] = …`, or the container's for `self.libs["x"] = …`."""
    if isinstance(target, ast.Attribute):
        return target.attr
    if isinstance(target, ast.Subscript):
        key = _literal(target.slice)
        if key is not None and _namespace_of(target.value) is not None:
            return key
        if isinstance(target.value, ast.Attribute):
            return target.value.attr
    return None


def _holders(library: dict[str, str]) -> set[str]:
    """The attributes seen holding part of the HTTP stack (`self.http`)."""
    return {key[4:] for key in library if key.startswith("via:")}


def _held_store(path: str, holders: set[str]) -> bool:
    """An attribute path stored through an object the scan does not follow
    that may patch a kept HTTP stack: one through an attribute seen holding it
    (`self.http.get = …`) or through one of the stack's classes; or, but
    through the method's own object (`^`), one ending in a name the stack
    sends through (`box.module.get = …`, `cast(type, Session).request`)."""
    own = path.startswith("^")
    segments = path.lstrip("^").split(".")
    if segments[-1] in _TUNING_ATTRIBUTES:
        return False
    return (
        bool(set(segments[:-1]) & holders)
        # A stack class stored into however it is reached
        # (`c.models.PreparedRequest.prepare_method = …`, #872 review 20).
        or bool(set(segments[1:] if own else segments) & _STACK_CLASSES)
        # A module or class kept somewhere, and a name it sends through
        # stored on an object that may be it (`box.module.get = …`).
        or (not own and segments[-1] in _STACK_NAMES)
    )


def _stack_object(path: str) -> bool:
    """A part of the HTTP stack a request passes through that can be kept and
    patched later: a module or a class, not a function
    (`asyncio.to_thread(requests.get, url)`), a constant or an exception."""
    parts = path.split(".")
    if parts[0] not in _HTTP_STACK or _inert(path):
        return False
    return len(parts) == 1 or path in _STACK_MODULES or parts[-1][:1].isupper() or path == "socket.socket"


def _inert(path: str) -> bool:
    """A part of the HTTP stack no request passes through: a constant
    (`socket.AF_INET`) or an exception (`requests.exceptions.Timeout`)."""
    parts = path.split(".")
    last = parts[-1]
    return (
        last.isupper()
        or last.endswith(("Error", "Exception", "Warning", "Timeout"))
        or last in {"timeout", "error", "herror", "gaierror"}
        or last in _STACK_EXCEPTIONS
        or any(part in {"exceptions", "error"} for part in parts[1:])
        # The server side (`aiohttp.web.HTTPBadGateway`).
        or parts[:2] in (["aiohttp", "web"], ["aiohttp", "web_exceptions"])
    )


def _is_self(key: _Key) -> bool:
    """A method's own first parameter (`self`, `cls`)."""
    return _is_parameter(key) and key[4] and key[2] == -1


def _namespaces(keys: frozenset[_Key] | set[_Key]) -> frozenset[_Key]:
    return frozenset(key if _is_namespace(key) else ("ns", key) for key in keys)


def _elements(keys: frozenset[_Key] | set[_Key]) -> frozenset[_Key]:
    """What an element of a container with these keys may be."""
    found: set[_Key] = set()
    for key in keys:
        if _is_namespace(key):
            # A value in a module may be a module imported there; a value in
            # an object's `__dict__` is an object the scan does not follow.
            if _named_module(key[1]) is not None:
                found.add(_ANY)
            elif _module_ish(key[1]):
                found.add(_UNSEEN)
        elif _module_ish(key):
            found.add(key)
            if isinstance(key, str) and key.startswith("~"):
                # An imported name read as a container (`from registry
                # import MODULES; MODULES[0]`): its items are objects the
                # scan does not follow (#872 review 12).
                found.add(_UNSEEN)
    return frozenset(found)


@dataclass
class FileMutations:
    """What one file changes in shared state (#872 reviews 6-10).

    - ``names``: names stored under: an attribute, a literal dict key, a
      ``setattr`` name.
    - ``whole``: namespaces changed under names the read cannot see
      (``setattr(config, name, v)``, ``vars(config).update(...)``,
      ``globals().update(...)``), keyed as :data:`_Key`.
    - ``dicts``: dicts changed under unseen keys that may be a namespace
      (``ns = vars(config); ns[k] = v``).
    - ``through``: a change, dict change or store into a container or
      attribute made to a function's parameter. What its callers pass there
      is then changed or stored.
    - ``calls``: every call's callee and what its arguments may be.
    - ``returns``: what a function may return or yield.
    - ``referenced``: names used as a value rather than called.
    - ``escapes``: module objects stored into a container or attribute.
    - ``unclassified``: a namespace changed on an object the scan takes as not
      a module.
    - ``passed``: what each call is handed; ``defined``: the functions and
      constructors the file defines.
    """

    names: list[tuple[str, str]] = field(default_factory=list)
    whole: list[tuple[_Key, str]] = field(default_factory=list)
    dicts: list[tuple[_Key, str]] = field(default_factory=list)
    through: list[tuple[_Parameter, str, str]] = field(default_factory=list)
    calls: list[
        tuple[str, tuple[tuple[int | None, frozenset[_Key]], ...], tuple[tuple[str | None, frozenset[_Key]], ...]]
    ] = field(default_factory=list)
    #: ``((module, function), keys, where, in_class)``.
    returns: list[tuple[tuple[str, str], frozenset[_Key], str, bool]] = field(default_factory=list)
    referenced: set[str] = field(default_factory=set)
    escapes: list[tuple[frozenset[_Key], str]] = field(default_factory=list)
    unclassified: list[str] = field(default_factory=list)
    #: ``(callee, argument keys, where)``: what a call is handed. A module
    #: handed to a call the scope does not define is kept where it is not
    #: followed (`SimpleNamespace(m=config)`, `partial(apply, config)`).
    passed: list[tuple[str | None, frozenset[_Key], str]] = field(default_factory=list)
    #: Functions and constructors this file defines, by name.
    defined: set[str] = field(default_factory=set)
    #: Classes whose own `__init__`/`__new__` this file defines.
    constructors: set[str] = field(default_factory=set)
    #: ``(name, function)``: `return name` inside `function` (a decorator
    #: factory handing on its wrapper).
    returned: list[tuple[str, str]] = field(default_factory=list)
    #: Names called anywhere but as a decorator (`@name(...)`).
    called: set[str] = field(default_factory=set)
    #: Functions registered as import hooks, which get any module.
    hooks: set[str] = field(default_factory=set)
    #: ``(dotted, where)``: an attribute of an imported module stored into
    #: (`json.dumps = audited`, `builtins.print = send`); ``module.*`` when
    #: under a name the scan cannot see (#872 review 12).
    library: list[tuple[str, str]] = field(default_factory=list)
    #: ``(owner keys, attribute, where)``: an attribute stored into on what
    #: may be a module however it was reached (#872 review 13).
    stores: list[tuple[frozenset[_Key], str, str]] = field(default_factory=list)
    #: ``(attribute path, where)``: stored on an object the scan does not follow.
    unseen: list[tuple[str, str]] = field(default_factory=list)
    #: ``(keys, attribute, where)``: what is kept under an attribute
    #: (`self.http = requests`).
    holders: list[tuple[frozenset[_Key], str, str]] = field(default_factory=list)
    #: ``(attribute, attribute)``: the first holds what the second holds
    #: (`self.client = self.http`, a property returning `self._http`).
    holder_aliases: list[tuple[str, str]] = field(default_factory=list)
    #: ``(name, keys)``: a module-level name and the modules it may hold, as
    #: another file importing it gets them (`import config as settings`).
    exports: list[tuple[str, frozenset[_Key]]] = field(default_factory=list)
    #: The module's dotted path under the scope (`pkg.tools`), the scope's
    #: own name for its `__init__.py`.
    module: str = ""
    #: ``~pkg.mod.name -> 2``: how much of an export an import bound is a
    #: module the import system found (`pkg.mod`), not attributes.
    export_locks: dict[str, int] = field(default_factory=dict)


def _namespace_of(node: ast.AST) -> ast.AST | str | None:
    """The object `node` is the namespace of: `x.__dict__`, `vars(x)`, `globals()`."""

    if isinstance(node, ast.Attribute):
        if node.attr == "__dict__":
            return node.value
        if node.attr in _FOREIGN_NAMESPACES:
            return _ANY
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id == "vars" and node.args:
            return node.args[0]
        if node.func.id in {"globals", "locals", "vars"} and not node.args:
            return _OWN
    return None


def _is_module_table(node: ast.AST, imported_from: dict[str, str] | None = None) -> bool:
    """`sys.modules`, or `modules` imported `from sys`."""
    if isinstance(node, ast.Name):
        return node.id == "modules" and (imported_from or {}).get("modules") == "sys"
    return _spelling(node) == "sys.modules"


def _answers_by_name(value: ast.AST, function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """A lazy export that answers the name asked for, not a module in its place.

    `getattr(import_module(path), name)` hands on an attribute, and
    `import_module(f".{name}", __name__)` the submodule of that name, which
    an importer reaches by its own file.
    """
    arguments = function.args
    asked = {argument.arg for argument in [*arguments.posonlyargs, *arguments.args]}
    if not isinstance(value, ast.Call) or not asked:
        return False
    callee = _spelling(value.func).rsplit(".", 1)[-1]
    if callee == "getattr" and len(value.args) >= 2:
        return any(isinstance(node, ast.Name) and node.id in asked for node in ast.walk(value.args[1]))
    if callee in {"import_module", "__import__"} and value.args:
        return any(isinstance(node, ast.Name) and node.id in asked for node in ast.walk(value.args[0]))
    return False


#: Attributes that tune the HTTP stack without changing what a request sends.
_TUNING_ATTRIBUTES = frozenset(
    {
        "cert", "debuglevel", "http_version", "keep_alive", "max_redirects", "max_retries", "pool_block",
        "pool_connections", "pool_maxsize", "retries", "timeout", "timeouts", "trust_env", "verify",
    }
)


def _setting(path: list[str], value: ast.AST | None) -> bool:
    """A plain value that only tunes: a module's constant
    (`requests.adapters.DEFAULT_RETRIES = 3`) or a class's tuning attribute
    (`HTTPConnection.debuglevel = 1`). A class's other attributes, its
    default `method` among them, change what a request sends (#872 review 15).
    """
    return value is not None and _plain(value) and _tuning(path)


def _tuning(path: list[str]) -> bool:
    """Whether a plain value stored at `path` only tunes (see :func:`_setting`)."""
    if not path:
        return False
    on_class = any(part[:1].isupper() for part in path[:-1])
    return not on_class or path[-1] in _TUNING_ATTRIBUTES


def _plain(node: ast.AST) -> bool:
    """A value written out in the source: a constant, or a display of them."""
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.UnaryOp):
        return _plain(node.operand)
    if isinstance(node, ast.Tuple | ast.List | ast.Set):
        return all(_plain(item) for item in node.elts)
    if isinstance(node, ast.Dict):
        return all(key is not None and _plain(key) and _plain(item) for key, item in zip(node.keys, node.values, strict=True))
    return False


def _literal(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


class _Fixpoint:
    """Sets computed over a graph with cycles, each node once (#872 reviews 10-11).

    Tarjan's components: a node finished while its component is still open
    keeps its partial set and is not computed again. When the component's
    root finishes, every member holds the root's set, since each reaches the
    same nodes. Past ``limit`` nested nodes the answer is any module.
    """

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.memo: dict[Any, frozenset[_Key]] = {}
        self._index: dict[Any, int] = {}
        self._partial: dict[Any, frozenset[_Key]] = {}
        self._open: list[Any] = []
        self._low: list[int] = []
        self._visited = 0

    def get(self, node: Any, compute: Callable[[], set[_Key] | frozenset[_Key]]) -> frozenset[_Key]:
        if node in self.memo:
            return self.memo[node]
        index = self._index.get(node)
        if index is not None:
            # In an open component: its set is gathered at the component's root.
            self._low[-1] = min(self._low[-1], index)
            return self._partial.get(node, frozenset())
        if len(self._low) >= self.limit:
            return frozenset({_ANY})
        index = self._visited
        self._visited += 1
        self._index[node] = index
        self._open.append(node)
        self._low.append(index)
        try:
            found = frozenset(compute())
        finally:
            low = self._low.pop()
        if low < index:
            self._partial[node] = found
            self._low[-1] = min(self._low[-1], low)
            return found
        while True:
            member = self._open.pop()
            del self._index[member]
            self._partial.pop(member, None)
            self.memo[member] = found
            if member is node or member == node:
                return found


class _Scopes:
    """The names each function of one file binds, to follow a module object.

    A module object is followed through names (closures, `global`,
    `nonlocal`), parameters and their defaults, what a function returns or
    yields, displays, loops and element methods (#872 review 10). Any other
    object, such as the result of a call the scope's own functions do not return a
    module from or an instance attribute, is taken as not a module.
    """

    def __init__(self, tree: ast.Module, own: str) -> None:
        self.own = own
        self.owner: dict[int, ast.AST | None] = {}
        self.parent: dict[int, ast.AST | None] = {}
        self.parameters: dict[int, dict[str, _Parameter]] = {}
        self.defaults: dict[tuple[int, str], ast.AST] = {}
        self.bindings: dict[int | None, dict[str, list[tuple[str, Any]]]] = {None: {}}
        #: `global X` / `nonlocal X`: the scopes that declare each.
        self.declared: dict[int, set[str]] = {}
        self.nonlocals: dict[int, set[str]] = {}
        self.writers: dict[tuple[str, str], list[int]] = {}
        #: `from helpers import set_all as apply`: apply -> set_all.
        self.aliases: dict[str, str] = {}
        #: `from pkg.helpers import fetch`: fetch -> helpers, and -> pkg.helpers.
        self.imported_from: dict[str, str] = {}
        self.imported_path: dict[str, str] = {}
        #: Whether a star import may bind names the file does not.
        self.star = False
        #: `(value, statement)` bound in a class body: a class attribute.
        self.class_values: list[tuple[ast.AST, ast.AST]] = []
        self.methods: dict[int, bool] = {}
        #: Functions (and classes with their own `__init__`) the file defines.
        self.defined: set[str] = set()
        self.constructors: set[str] = set()
        #: Functions defined directly in a class body.
        self.in_class: set[int] = set()
        self._owned: dict[int, frozenset[str]] = {}
        self._copies: dict[int, frozenset[str]] = {}
        self._fixpoint = _Fixpoint(MAX_OBJECT_DEPTH)
        stack: list[tuple[ast.AST, ast.AST | None]] = [(tree, None)]
        while stack:
            node, scope = stack.pop()
            for child in ast.iter_child_nodes(node):
                self._child(child, scope, node, stack)

    def _child(
        self,
        child: ast.AST,
        scope: ast.AST | None,
        node: ast.AST | None,
        stack: list[tuple[ast.AST, ast.AST | None]],
    ) -> None:
        self.owner[id(child)] = scope
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            if not isinstance(child, ast.Lambda):
                self._bind(scope, child.name, ("def", None))
            outer = [
                *getattr(child, "decorator_list", []),
                *child.args.defaults,
                *[default for default in child.args.kw_defaults if default is not None],
            ]
            for item in outer:
                self._child(item, scope, None, stack)
            self.parent[id(child)] = scope
            self.bindings[id(child)] = {}
            # A method's first parameter is its own instance, a staticmethod's is not.
            is_method = isinstance(node, ast.ClassDef) and not any(
                _spelling(decorator) == "staticmethod" for decorator in getattr(child, "decorator_list", [])
            )
            name = None if isinstance(child, ast.Lambda) else child.name
            if isinstance(node, ast.ClassDef):
                self.in_class.add(id(child))
            if is_method and name in {"__init__", "__new__"}:
                # `Holder(module)` calls `Holder.__init__`: keyed by the class.
                name = node.name  # type: ignore[union-attr]
                self.constructors.add(name)
            if name is not None:
                self.defined.add(name)
            self._parameters(child, is_method, name)
            for item in child.body if isinstance(child.body, list) else [child.body]:
                self._child(item, child, None, stack)
            return
        self._record(child, scope)
        if isinstance(node, ast.ClassDef) and isinstance(child, ast.Assign | ast.AnnAssign) and child.value is not None:
            self.class_values.append((child.value, child))
        stack.append((child, scope))

    def single_call(self, expr: ast.AST | None) -> ast.Call | None:
        """The one call `expr` is, or a name is only ever bound to."""
        if isinstance(expr, ast.Call):
            return expr
        if not isinstance(expr, ast.Name):
            return None
        scope = self.owner.get(id(expr))
        while scope is not None and expr.id not in self.bindings.get(id(scope), {}):
            scope = self.parent.get(id(scope))
        markers = self.bindings.get(None if scope is None else id(scope), {}).get(expr.id, [])
        if len(markers) == 1 and markers[0][0] == "value" and isinstance(markers[0][1], ast.Call):
            return markers[0][1]
        return None

    def self_name(self, function: ast.AST | None) -> str | None:
        """A method's own `self` (or `cls`): stores through it change an instance."""
        if function is None or not self.methods.get(id(function)):
            return None
        arguments = function.args  # type: ignore[attr-defined]
        positional = [*arguments.posonlyargs, *arguments.args]
        return positional[0].arg if positional else None

    def _copies_itself(self, value: ast.AST) -> bool:
        """`copy.copy(requests.Session)`: a class or module copies as itself,
        so the copy is not a new object (#872 review 22)."""
        if not (isinstance(value, ast.Call) and value.args):
            return False
        func = value.func
        name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
        return name in {"copy", "deepcopy"} and any(
            _module_ish(key) and not _is_parameter(key) for key in self.keys(value.args[0])
        )

    def copies(self, function: ast.AST | None) -> frozenset[str]:
        """Names `function` binds to a copy of what it does not follow (a
        parameter, `self.session_class`): a class copies as itself, so an
        attribute stored through one may patch it (#872 review 23)."""
        if function is None:
            return frozenset()
        if id(function) in self._copies:
            return self._copies[id(function)]
        self._copies[id(function)] = found = frozenset(
            name
            for name, markers in self.bindings.get(id(function), {}).items()
            if any(
                kind == "value"
                and isinstance(value, ast.Call)
                and value.args
                and (value.func.attr if isinstance(value.func, ast.Attribute) else getattr(value.func, "id", ""))
                in {"copy", "deepcopy"}
                and any(_is_parameter(key) or key == _UNSEEN for key in self.keys(value.args[0]))
                for kind, value in markers
            )
        )
        return found

    def owned(self, function: ast.AST | None) -> frozenset[str]:
        """Names `function` only ever binds to an object it builds (`x = {}`)."""
        if function is None:
            return frozenset()
        found = self._owned.get(id(function))
        if found is None:
            excluded = (
                set(self.parameters.get(id(function), {}))
                | self.declared.get(id(function), set())
                | self.nonlocals.get(id(function), set())
            )
            found = frozenset(
                name
                for name, markers in self.bindings.get(id(function), {}).items()
                if name not in excluded
                and markers
                and all(kind == "value" and _fresh(value) and not self._copies_itself(value) for kind, value in markers)
            )
            self._owned[id(function)] = found
        return found

    def _parameters(self, function: ast.AST, is_method: bool, name: str | None) -> None:
        self.methods[id(function)] = is_method
        arguments = function.args  # type: ignore[attr-defined]
        offset = 1 if is_method else 0
        positional = [*arguments.posonlyargs, *arguments.args]
        found: dict[str, _Parameter] = {}
        for index, argument in enumerate(positional):
            found[argument.arg] = ("param", name, index - offset, argument.arg, is_method)
        if arguments.vararg is not None:
            found[arguments.vararg.arg] = (
                "param", name, len(positional) - offset, f"*{arguments.vararg.arg}", is_method
            )
        for argument in arguments.kwonlyargs:
            found[argument.arg] = ("param", name, -1, argument.arg, is_method)
        if arguments.kwarg is not None:
            found[arguments.kwarg.arg] = ("param", name, -1, f"**{arguments.kwarg.arg}", is_method)
        self.parameters[id(function)] = found
        defaulted = positional[len(positional) - len(arguments.defaults):]
        for argument, default in zip(defaulted, arguments.defaults, strict=True):
            self.defaults[(id(function), argument.arg)] = default
        for argument, default in zip(arguments.kwonlyargs, arguments.kw_defaults, strict=True):
            if default is not None:
                self.defaults[(id(function), argument.arg)] = default

    def _bind(self, scope: ast.AST | None, name: str, marker: tuple[str, Any]) -> None:
        self.bindings[None if scope is None else id(scope)].setdefault(name, []).append(marker)

    def _target(self, scope: ast.AST | None, target: ast.AST, marker: tuple[str, Any]) -> None:
        if isinstance(target, ast.Name):
            self._bind(scope, target.id, marker)
        elif isinstance(target, ast.Starred):
            self._target(scope, target.value, ("in", marker[1]) if marker[0] == "value" else marker)
        elif isinstance(target, ast.Tuple | ast.List):
            value = marker[1] if marker[0] == "value" else None
            if (
                isinstance(value, ast.Tuple | ast.List)
                and len(value.elts) == len(target.elts)
                and not any(isinstance(item, ast.Starred) for item in (*value.elts, *target.elts))
            ):
                for item, part in zip(target.elts, value.elts, strict=True):
                    self._target(scope, item, ("value", part))
            else:
                for item in target.elts:
                    self._target(scope, item, ("in", marker[1]) if marker[0] != "none" else marker)

    def _record(self, node: ast.AST, scope: ast.AST | None) -> None:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                self._target(scope, target, ("value", node.value))
        elif isinstance(node, ast.AnnAssign | ast.AugAssign) and node.value is not None:
            self._target(scope, node.target, ("value", node.value))
        elif isinstance(node, ast.NamedExpr):
            self._target(scope, node.target, ("value", node.value))
        elif isinstance(node, ast.For | ast.AsyncFor | ast.comprehension):
            self._target(scope, node.target, ("in", node.iter))
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            self._target(scope, node.optional_vars, ("none", None))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    self._bind(scope, alias.asname, ("import", frozenset({alias.name})))
                else:
                    first = alias.name.split(".")[0]
                    self._bind(scope, first, ("import", frozenset({first})))
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    self.star = True
                    continue
                origin = f"{node.module}.{alias.name}" if node.module and not node.level else alias.name
                self._bind(scope, alias.asname or alias.name, ("from", frozenset({f"~{origin}"})))
                if node.module:
                    self.imported_from[alias.asname or alias.name] = node.module.rsplit(".", 1)[-1]
                    if not node.level:
                        self.imported_path[alias.asname or alias.name] = node.module
                if alias.asname:
                    self.aliases[alias.asname] = alias.name
        elif isinstance(node, ast.Global | ast.Nonlocal) and scope is not None:
            kind = "global" if isinstance(node, ast.Global) else "nonlocal"
            (self.declared if kind == "global" else self.nonlocals).setdefault(id(scope), set()).update(node.names)
            for name in node.names:
                self.writers.setdefault((kind, name), []).append(id(scope))
        elif isinstance(node, ast.ExceptHandler) and node.name:
            self._bind(scope, node.name, ("none", None))
        elif isinstance(node, ast.ClassDef):
            self._bind(scope, node.name, ("class", None))

    # -- what an expression may be, as a module ---------------------------------

    def keys(self, expr: ast.AST) -> frozenset[_Key]:
        return self._fixpoint.get(id(expr), lambda: self._keys(expr))

    def _keys(self, expr: ast.AST) -> set[_Key]:
        inner = self.keys
        if isinstance(expr, ast.Name):
            found = set(self.name(expr.id, self.owner.get(id(expr))))
            if expr.id == "__builtins__" and found == {"=__builtins__"}:
                # The builtins module, or its dict.
                return {"builtins", ("ns", "builtins")}
            return found
        if isinstance(expr, ast.Attribute):
            namespace = _namespace_of(expr)
            if namespace == _ANY:
                return {("ns", _ANY)}
            if namespace is not None:
                return set(_namespaces(inner(namespace)))  # type: ignore[arg-type]
            if expr.attr == "__class__":
                return self._class_of(inner(expr.value))
            if expr.attr in {"__base__", "__bases__", "__mro__"}:
                return {_CLASS}
            return self._member(inner(expr.value), expr.attr)
        if isinstance(expr, ast.Subscript):
            if _is_module_table(expr.value, self.imported_from):
                return self.lookup(expr.slice)
            namespace = _namespace_of(expr.value)
            if isinstance(namespace, ast.AST) and _literal(expr.slice) is not None:
                # `vars(urllib.request)["Request"]` is `urllib.request.Request`.
                return self._member(inner(namespace), _literal(expr.slice))  # type: ignore[arg-type]
            item = self._tuple_item(expr)
            if item is not None:
                # `(type(exc), exc, tb)[1]` is `exc`, not a class.
                return set(inner(item))
            return set(_elements(inner(expr.value)))
        if isinstance(expr, ast.Call):
            return self._call(expr, inner)
        if isinstance(expr, ast.Tuple | ast.List | ast.Set):
            return {key for item in expr.elts for key in inner(item)}
        if isinstance(expr, ast.Dict):
            return {key for item in expr.values for key in inner(item)}
        if isinstance(expr, ast.ListComp | ast.SetComp | ast.GeneratorExp):
            return set(inner(expr.elt))
        if isinstance(expr, ast.DictComp):
            return set(inner(expr.value))
        if isinstance(expr, ast.IfExp):
            return {*inner(expr.body), *inner(expr.orelse)}
        if isinstance(expr, ast.BoolOp):
            return {key for item in expr.values for key in inner(item)}
        if isinstance(expr, ast.NamedExpr | ast.Await | ast.Starred):
            return set(inner(expr.value))
        return set()

    def _tuple_item(self, expr: ast.Subscript) -> ast.AST | None:
        """The item a constant index reads from a tuple written out here, or
        from a name bound once to one."""
        index = expr.slice.value if isinstance(expr.slice, ast.Constant) else None
        if type(index) is not int:
            return None
        literal: ast.AST | None = expr.value
        if isinstance(literal, ast.Name):
            scope = self.binder(literal.id, self.owner.get(id(expr)))
            markers = self.bindings.get(None if scope is None else id(scope), {}).get(literal.id, [])
            parameter = scope is not None and literal.id in self.parameters.get(id(scope), {})
            # `global PAIR; PAIR = …` in a function, or `nonlocal`, writes it
            # from elsewhere (#872 review 21).
            written = ("global" if scope is None else "nonlocal", literal.id) in self.writers
            once = len(markers) == 1 and markers[0][0] == "value" and not parameter and not written
            literal = markers[0][1] if once else None
        if not isinstance(literal, ast.Tuple) or any(isinstance(item, ast.Starred) for item in literal.elts):
            return None
        return literal.elts[index] if -len(literal.elts) <= index < len(literal.elts) else None

    def binder(self, name: str, scope: ast.AST | None) -> ast.AST | None:
        """The function whose scope binds `name` read in `scope`; None for the module."""
        while scope is not None:
            if name in self.declared.get(id(scope), set()):
                return None
            if name not in self.nonlocals.get(id(scope), set()) and (
                name in self.parameters.get(id(scope), {}) or name in self.bindings.get(id(scope), {})
            ):
                return scope
            scope = self.parent.get(id(scope))
        return None

    def kinds(self, name: str, scope: ast.AST | None) -> tuple[ast.AST | None, set[str]]:
        """Where `name` is bound, and how: `param`, `def`, `class`, `import`, `value`, …"""
        binder = self.binder(name, scope)
        kinds = {kind for kind, _ in self.bindings.get(None if binder is None else id(binder), {}).get(name, [])}
        if binder is not None and name in self.parameters.get(id(binder), {}):
            kinds.add("param")
        return binder, kinds

    def name(self, name: str, scope: ast.AST | None) -> frozenset[_Key]:
        """What `name`, read in `scope`, may be: from the scope that binds it."""
        scope = self.binder(name, scope)
        key = ("name", None if scope is None else id(scope), name)
        return self._fixpoint.get(key, lambda: self._name(name, scope))

    def _name(self, name: str, scope: ast.AST | None) -> set[_Key]:
        found: set[_Key] = set()
        if scope is None:
            found.add(f"={name}")
            markers = list(self.bindings[None].get(name, []))
            if self.star and not markers and name not in _BUILTIN_NAMES:
                # `from registry import *` may bind it: what `registry`
                # exports under that name (#872 review 14).
                found.add(f"~{name}")
            writers = self.writers.get(("global", name), [])
        else:
            parameter = self.parameters.get(id(scope), {}).get(name)
            if parameter is not None:
                found.add(parameter)
            default = self.defaults.get((id(scope), name))
            if default is not None:
                found |= self.keys(default)
            markers = list(self.bindings.get(id(scope), {}).get(name, []))
            writers = self.writers.get(("nonlocal", name), [])
        for writer in writers:
            # `global X; X = ...` in a function binds the module's X.
            markers.extend(self.bindings.get(writer, {}).get(name, []))
        for marker in markers:
            found |= self._marker(marker)
        return found

    def _marker(self, marker: tuple[str, Any]) -> frozenset[_Key]:
        kind, value = marker
        if kind in {"import", "from"}:
            return value
        if kind == "value":
            return self.keys(value)
        if kind == "in":
            keys = self.keys(value)
            return keys if isinstance(value, ast.Tuple | ast.List | ast.Set | ast.Dict) else _elements(keys)
        return frozenset()

    def _class_of(self, instance: frozenset[_Key]) -> set[_Key]:
        """`x.__class__`, `type(x)`: a method's own class, or any class."""
        return {_OWN_CLASS} if instance and all(_is_self(key) for key in instance) else {_CLASS}

    def _member(self, base: frozenset[_Key], attr: str) -> set[_Key]:
        """`base.attr`: a submodule or a value of a module, else an object's attribute.

        An attribute of what may not be a module (a call's result, a
        parameter) may be an object the scan does not follow: ``?``. A
        dunder (`__version__`, `__mro__`) is never a submodule.
        """
        if attr.startswith("__") and attr.endswith("__"):
            return {f"={attr}"}
        if _ANY in base:
            return {_ANY}
        if _CLASS in base:
            # What a class reached by introspection holds: `type(req).headers`.
            return {_CLASS}
        modules = [
            key
            for key in base
            if _module_ish(key) and not (_is_call(key) or _is_parameter(key) or key == _CLASS)
        ]
        if modules:
            # Kept with the path it was reached by: `urllib.request.Request`.
            return {f"~{_module_path(key) or attr}.{attr}" if _module_path(key) else f"~{attr}" for key in modules}
        if _elements(base):
            # An attribute of an object the scan does not follow
            # (`usage.requests`) is not the module of that name.
            return {f"={attr}", _UNSEEN}
        return {f"={attr}"}

    def lookup(self, key: ast.AST | None) -> set[_Key]:
        """`sys.modules[key]`, `import_module(key)`.

        A name built with a literal last part (`f"pkg.{provider}.chat"`) names
        that module, whatever the package.
        """
        literal = _literal(key)
        if literal is None and isinstance(key, ast.JoinedStr) and key.values:
            literal = _literal(key.values[-1])
            literal = literal if literal is not None and "." in literal else None
        elif literal is None and isinstance(key, ast.BinOp) and isinstance(key.op, ast.Add):
            literal = _literal(key.right)
            literal = literal if literal is not None and "." in literal else None
        if literal is not None and literal.strip("."):
            # The whole path when it is written out (`"urllib.request"`); the
            # last part when only that is (`f"pkg.{provider}.chat"`).
            exact = _literal(key) is not None
            return {literal.strip(".") if exact else literal.strip(".").rsplit(".", 1)[-1]}
        if isinstance(key, ast.Name) and key.id == "__name__":
            return {self.own}
        return {_ANY}

    def _call(self, call: ast.Call, inner: Callable[[ast.AST], frozenset[_Key]]) -> set[_Key]:
        func = call.func
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
        arguments = call.args
        namespace = _namespace_of(call)
        if namespace == _OWN:
            # `locals()` or `vars()` in a function is that function's own.
            if name != "globals" and self.owner.get(id(call)) is not None:
                return set()
            return {("ns", self.own)}
        if namespace is not None:
            return set(_namespaces(inner(namespace)))  # type: ignore[arg-type]
        if isinstance(func, ast.Attribute) and _is_module_table(func.value, self.imported_from):
            return self.lookup(arguments[0]) if arguments else {_ANY}
        if name in _MODULE_LOOKUPS:
            if name == "reload" and arguments:
                return set(inner(arguments[0]))
            if name == "__import__" and _literal(arguments[0] if arguments else None) is not None:
                return {_literal(arguments[0]).split(".")[0], *self.lookup(arguments[0])}  # type: ignore[union-attr]
            return self.lookup(arguments[0]) if arguments and name != "getmodule" else {_ANY}
        if isinstance(func, ast.Name) and name == "getattr" and len(arguments) >= 2:
            base = inner(arguments[0])
            attr = _literal(arguments[1])
            if attr is not None:
                return self._member(base, attr)
            # An attribute read under a computed name is taken as not a
            # module, unless what it is read from may be any module.
            return {_ANY} if _ANY in base else set()
        if (
            isinstance(func, ast.Call)
            and _spelling(func.func).rsplit(".", 1)[-1] == "attrgetter"
            and len(func.args) == 1
            and isinstance(_literal(func.args[0]), str)
            and arguments
        ):
            # `operator.attrgetter("transport.adapters")(net)` is
            # `net.transport.adapters` (#872 review 24).
            found: set[_Key] = set(inner(arguments[0]))
            for attr in _literal(func.args[0]).split("."):  # type: ignore[union-attr]
                found = self._member(frozenset(found), attr)
            return found
        if isinstance(func, ast.Name) and name == "type" and len(arguments) == 1:
            return self._class_of(inner(arguments[0]))
        if name in {"__subclasses__", "getmro", "mro"}:
            return {_CLASS}
        if isinstance(func, ast.Name) and name in _ELEMENT_CALLS and arguments:
            return set(inner(arguments[-1]))
        if name in {"copy", "deepcopy"} and arguments and (
            (isinstance(func, ast.Attribute) and "copy" in inner(func.value))
            or (isinstance(func, ast.Name) and self.imported_path.get(func.id) == "copy")
        ):
            # `copy.copy(requests.Session)` is that class: a class or module
            # copies as itself (#872 review 21).
            return set(inner(arguments[0]))
        if isinstance(func, ast.Attribute) and name == "copy":
            # A copy of a namespace is a plain dict; of a list, the same items.
            return {key for key in inner(func.value) if not _is_namespace(key)}
        if isinstance(func, ast.Attribute) and name in _ELEMENT_METHODS:
            base = inner(func.value)
            # `session.get(id)` on an imported name is that module's (or
            # object's) function, not an element of a container.
            if not any(_named_module(key) is not None for key in base if not _is_namespace(key)):
                return set(_elements(base))
        if name is None:
            return set()
        # What a call returns is followed into the scope's own functions
        # (#872 review 11): one the file defines or imports by name, a
        # module's function, or the class's own method. What a function held
        # in a variable, or another object's method, returns is an object the
        # scan does not follow.
        home: str | None
        if isinstance(func, ast.Name):
            _, kinds = self.kinds(func.id, self.owner.get(id(call)))
            if kinds - {"def", "class", "from"}:
                return {_UNSEEN}
            home = self.own if kinds & {"def", "class"} else self.imported_from.get(func.id)
        elif isinstance(func.value, ast.Name) and func.value.id in {"self", "cls"}:
            home = self.own
        else:
            modules = {_named_module(key) for key in inner(func.value) if not _is_namespace(key)} - {None}
            if not modules:
                return {_UNSEEN}
            home = next(iter(modules)) if len(modules) == 1 and _ANY not in modules else None
        return {("call", self.aliases.get(name, name), *self.arguments(call), home)}

    def arguments(
        self, call: ast.Call
    ) -> tuple[tuple[tuple[int | None, frozenset[_Key]], ...], tuple[tuple[str | None, frozenset[_Key]], ...]]:
        """What each argument of `call` may be; a starred one at no fixed index."""
        positional: list[tuple[int | None, frozenset[_Key]]] = []
        starred = False
        for index, argument in enumerate(call.args):
            starred = starred or isinstance(argument, ast.Starred)
            keys = self.keys(argument)
            if keys:
                positional.append((None if starred else index, keys))
        keywords = tuple(
            (keyword.arg, keys) for keyword in call.keywords if (keys := self.keys(keyword.value))
        )
        return tuple(positional), keywords


def module_mutations(tree: ast.Module, ref: str) -> FileMutations:
    """What this file stores into, on anything but an object it just built.

    A store is keyed by the name it writes (#872 review 7), whatever it goes
    through: `agent_config.METHOD = ...`, `helpers.fetch = purge`,
    `cfg["method"] = ...` through an accessor or a parameter. A store under
    a name the read cannot see changes the whole namespace. That covers a
    computed `setattr`, `vars(x)`/`x.__dict__` (#872 review 8), and
    `globals()`, `sys.modules[...]`, `exec` and a namespace passed to a call
    (#872 review 10). :class:`_Scopes` follows what that namespace may be.
    Two cases are skipped:
    - stores into a dict, list, client or request the same function just built;
    - stores through a method's own `self`, which change an instance, not a
      module.
    """

    found = FileMutations()
    own_module = _module_stem(ref)
    scopes = _Scopes(tree, own_module)

    def where(node: ast.AST) -> str:
        return f"{ref}:{getattr(node, 'lineno', 0)}"

    def skipped(root: str | None, node: ast.AST) -> bool:
        if root is None:
            return False
        function = scopes.owner.get(id(node))
        return root == scopes.self_name(function) or root in scopes.owned(function)

    def named(name: str | None, root: str | None, node: ast.AST, *, key: bool = False) -> None:
        # A dict key needs no record: a value read out of a module-level dict
        # is never taken as written (#872 review 9).
        if name is not None and not key and not skipped(root, node):
            found.names.append((name, where(node)))

    def record(keys: frozenset[_Key] | set[_Key], node: ast.AST, kind: str = "change") -> None:
        """A namespace changed (``change``), or a dict that may be one (``dict``)."""
        if kind == "change" and not keys:
            found.unclassified.append(where(node))
        for key in keys:
            key = _unwrap(key) if kind == "change" else key
            if _is_parameter(key):
                found.through.append((key, kind, where(node)))
            elif kind == "change":
                found.whole.append((key, where(node)))
            elif _is_namespace(key) or _is_call(key) or key == _UNSEEN:
                found.dicts.append((key, where(node)))

    def changed(container: ast.AST, node: ast.AST, *, namespace: bool = False) -> None:
        """What `node` changes under names the read cannot see.

        A namespace: `setattr(m, name, v)`, `vars(m)`, `m.__dict__`,
        `globals()`. Otherwise a dict: one read out of a module is never taken
        as written (#872 review 9), so only a namespace reached another way
        counts (`ns = vars(m); ns[k] = v`).
        """
        target = _namespace_of(container)
        if target == _OWN:
            if scopes.keys(container):
                record({own_module}, node)
            return
        if target == _ANY:
            record({_ANY}, node)
            return
        if target is not None:
            container = target  # type: ignore[assignment]
            namespace = True
        root = _root_name(container)
        if skipped(root, node):
            if namespace and not isinstance(container, ast.Name):
                # `setattr(self.module, k, v)`: an attribute of an instance
                # the scan does not follow.
                found.unclassified.append(where(node))
            return
        if namespace:
            patched(container, None, node)
        record(scopes.keys(container), node, "change" if namespace else "dict")

    #: Each name an import binds, as the dotted path it imports.
    imported: dict[str, str] = {"builtins": "builtins", "__builtins__": "builtins"}
    for statement in ast.walk(tree):
        if isinstance(statement, ast.Import):
            for alias in statement.names:
                if alias.asname:
                    imported[alias.asname] = alias.name
                else:
                    imported.setdefault(alias.name.split(".")[0], alias.name.split(".")[0])
        elif isinstance(statement, ast.ImportFrom) and statement.module and not statement.level:
            for alias in statement.names:
                if alias.name != "*":
                    imported[alias.asname or alias.name] = f"{statement.module}.{alias.name}"

    def patched(owner: ast.AST | None, attr: str | None, node: ast.AST) -> None:
        """A store into what an import binds: `json.dumps = f`, `setattr(time, k, v)`."""
        spelling = reference_spelling(owner) if owner is not None else None
        if spelling is None:
            return
        root, _, rest = spelling.partition(".")
        _, kinds = scopes.kinds(root, scopes.owner.get(id(node)))
        if root in imported and (kinds & {"import", "from"} or (not kinds and root in {"builtins", "__builtins__"})):
            dotted = imported[root] + (f".{rest}" if rest else "")
            found.library.append((f"{dotted}.{attr or '*'}", where(node)))

    def stored_into(owner: ast.AST | None, attr: str | None, node: ast.AST, value: ast.AST | None = None) -> None:
        """An attribute stored on what may be a module, however reached:
        `sys.modules["json"].dumps = f`, `j = json; j.dumps = f`, a parameter.

        Recorded under the path from the module it starts at
        (`requests.Session.request`, `urllib.request.Request.method`). A
        setting stored as a plain value (`adapters.DEFAULT_RETRIES = 3`)
        replaces no code.
        """
        if owner is None:
            return
        base, chain = owner, []
        #: The class of an object, or a class's base, reached by
        #: introspection: `x.__class__`, `type(x)`, `Sub.__mro__[1]`.
        introspected = False
        while True:
            if isinstance(base, ast.Attribute) and base.attr in {"__class__", "__base__"}:
                introspected = True
                base = base.value
            elif isinstance(base, ast.Subscript) and (
                (isinstance(base.value, ast.Attribute) and base.value.attr in {"__bases__", "__mro__"})
                or (
                    isinstance(base.value, ast.Call)
                    and _spelling(base.value.func).rsplit(".", 1)[-1] in {"__subclasses__", "getmro", "mro"}
                )
            ):
                introspected = True
                inner = base.value
                base = (
                    inner.value
                    if isinstance(inner, ast.Attribute)
                    else inner.func.value
                    if isinstance(inner.func, ast.Attribute) and inner.func.attr in {"__subclasses__", "mro"}  # type: ignore[union-attr]
                    else (inner.args[0] if inner.args else inner)  # type: ignore[union-attr]
                )
            elif isinstance(base, ast.Call) and _spelling(base.func) == "type" and len(base.args) == 1:
                introspected = True
                base = base.args[0]
            elif isinstance(base, ast.Attribute):
                chain.insert(0, base.attr)
                base = base.value
            elif (
                isinstance(base, ast.Call)
                and _spelling(base.func) == "getattr"
                and len(base.args) >= 2
                and _literal(base.args[1]) is not None
            ):
                # `getattr(urllib.request, "Request").method = …`
                chain.insert(0, _literal(base.args[1]))  # type: ignore[arg-type]
                base = base.args[0]
            else:
                break
        path = ".".join([*chain, attr or "*"])
        owner_keys = scopes.keys(owner)

        def own_object(root: str | None) -> bool:
            """The method's own object, or one the function built: not an
            item of a container it built (`pair = (json, 1)`, `pair[0].get`),
            which may be anything put there (#872 review 21)."""
            function = scopes.owner.get(id(node))
            if isinstance(base, ast.Subscript) and root != scopes.self_name(function):
                return False
            if root in scopes.copies(function):
                return False
            return skipped(root, node)

        if (attr or "*") not in _TUNING_ATTRIBUTES and (
            _CLASS in owner_keys or (introspected and not skipped(_root_name(base), node))
        ):
            # Any class, an HTTP stack's among them (#872 reviews 17-18).
            found.library.append((f"class:{attr or '*'}", where(node)))
        plain = value is not None and _plain(value)
        if plain and _setting([*([base.id] if isinstance(base, ast.Name) else []), *path.split(".")], value):
            if isinstance(base, ast.Name) and base.id in imported:
                # A known module's setting (`adapters.DEFAULT_RETRIES = 3`).
                return
        else:
            patched(owner, attr, node)
        direct = frozenset(key for key in owner_keys if _module_ish(key) and key != _CLASS)
        if (not direct or _UNSEEN in owner_keys) and not (isinstance(owner, ast.Name) and skipped(owner.id, node)):
            # An object the scan does not follow, `self.http` included, may
            # hold a module kept there: record the whole path it is stored
            # through (`http.get`, `libs.http.get` for `self.libs["http"]`,
            # `module.Session.prepare_request`), `^` marking one through the
            # method's own object.
            trail, root = _trail(owner)
            own = "^" if isinstance(root, ast.Name) and own_object(root.id) else ""
            found.unseen.append((f"{own}{'.'.join([*trail, attr or '*'])}", where(node)))
        if own_object(_root_name(base)):
            return
        keys = frozenset(key for key in scopes.keys(base) if not (isinstance(key, str) and key.startswith("=")))
        if keys:
            # A plain value's owner is decided once it is resolved: a
            # class's `method` is a patch, a module's constant a setting.
            found.stores.append((keys, f"={path}" if plain else path, where(node)))
        # The owner itself, however it was reached: another module's name for
        # a library (`agent.requests.get = …`) (#872 review 18).
        if direct:
            found.stores.append((direct, f"={attr or '*'}" if plain else attr or "*", where(node)))

    def builtins_of(expr: ast.AST) -> bool:
        """`builtins`, however imported, or `__builtins__`."""
        return _root_name(expr) == "__builtins__" or any(
            _named_module(key) == "builtins" for key in scopes.keys(expr)
        )

    def reloads_itself(value: ast.AST | None, key: ast.AST) -> bool:
        """The module itself, loaded lazily, not another in its place.

        `sys.modules[name] = module_from_spec(find_spec(name))`, or a lazy
        proxy built from that name and its spec (`_LazyModule(name, spec)`).
        """
        def own_spec(argument: ast.AST) -> bool:
            spec = scopes.single_call(argument)
            return (
                spec is not None
                and _spelling(spec.func).endswith("find_spec")
                and bool(spec.args)
                and ast.dump(spec.args[0]) == ast.dump(key)
            )

        module = scopes.single_call(value)
        if module is None or not module.args:
            return False
        if _spelling(module.func).endswith("module_from_spec"):
            return own_spec(module.args[0])
        return ast.dump(module.args[0]) == ast.dump(key) and any(own_spec(arg) for arg in module.args[1:])

    def stored(value: ast.AST, node: ast.AST, holder: str | None = None) -> None:
        """`value` kept in a container or an attribute, where it is not
        followed; ``holder`` is the attribute it is kept under (`self.http`)."""
        keys = scopes.keys(value)
        kind = f"escape:{holder}" if holder else "escape"
        for key in keys:
            if _is_parameter(key):
                found.through.append((key, kind, where(node)))
        if any(not _is_parameter(key) and _module_ish(key) for key in keys):
            found.escapes.append((keys, where(node)))
            if holder:
                found.holders.append((keys, holder, where(node)))

    for value, statement in scopes.class_values:
        stored(
            value,
            statement,
            next(
                (target.id for target in getattr(statement, "targets", [getattr(statement, "target", None)])
                 if isinstance(target, ast.Name)),
                None,
            ),
        )

    called: set[int] = set()
    #: Decorator expressions, and `return` values with their function.
    decorating: set[int] = set()
    returning: dict[int, str] = {}
    #: What a name may be bound from (an assignment's or `:=`'s value, a
    #: `for` iterable, a `with` item), and classes, read again once
    #: everything is recorded.
    bound_from: list[ast.AST] = []
    classes: list[ast.ClassDef] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign | ast.NamedExpr) and node.value is not None:
            bound_from.append(node.value)
        elif isinstance(node, ast.For | ast.AsyncFor | ast.comprehension):
            bound_from.append(node.iter)
        elif isinstance(node, ast.withitem):
            bound_from.append(node.context_expr)
        if isinstance(node, ast.ClassDef):
            classes.append(node)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            decorating.update(id(decorator) for decorator in node.decorator_list)
        elif isinstance(node, ast.Return) and node.value is not None:
            function = scopes.owner.get(id(node))
            if isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
                returning[id(node.value)] = function.name
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        elif isinstance(node, ast.AugAssign):
            targets = [node.target]
            if isinstance(node.target, ast.Name):
                changed(node.target, node)  # `cfg |= overrides`
        elif isinstance(node, ast.Delete):
            targets = node.targets
        elif isinstance(node, ast.Lambda):
            # What a lambda returns is not followed: a module it returns
            # (`http = lambda: requests`) is kept there (#872 review 18).
            stored(node.body, node)
        elif isinstance(node, ast.Return | ast.Yield | ast.YieldFrom) and node.value is not None:
            function = scopes.owner.get(id(node))
            if (
                isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef)
                and isinstance(node.value, ast.Attribute)
                and isinstance(node.value.value, ast.Name)
                and node.value.value.id == scopes.self_name(function)
            ):
                # `@property def http(self): return self._http`.
                found.holder_aliases.append((function.name, node.value.attr))
            if isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
                keys = scopes.keys(node.value)
                if keys:
                    found.returns.append(
                        ((own_module, function.name), keys, where(node), id(function) in scopes.in_class)
                    )
                if (
                    function.name == "__getattr__"
                    and scopes.parent.get(id(function)) is None
                    and not _answers_by_name(node.value, function)
                ):
                    # A module's `__getattr__` (PEP 562) answers any name
                    # another file imports from it (#872 review 14).
                    stored(node.value, node)
        value = getattr(node, "value", None) if isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign) else None
        if (
            value is not None
            and scopes.owner.get(id(node)) is None
            and any(isinstance(target, ast.Name | ast.Tuple | ast.List) for target in targets)
        ):
            # A module-level name another file can import. An alias of a
            # module (`CFG = config`, `a, b = x, y`) is an import by another
            # name, followed through ``exports``. A module held in a
            # container or built value (`SETTINGS_MODULES = [config]`) is
            # kept there too, for routes that are not by name.
            aliases = all(
                isinstance(part, ast.Name | ast.Attribute)
                # `requests = importlib.import_module("requests")`: an import
                # by another spelling.
                or (isinstance(part, ast.Subscript) and _is_module_table(part.value, scopes.imported_from))
                or (
                    isinstance(part, ast.Call)
                    and _spelling(part.func).rsplit(".", 1)[-1] in {"__import__", "import_module"}
                )
                for part in (
                    value.elts
                    if isinstance(value, ast.Tuple | ast.List)
                    and all(isinstance(target, ast.Tuple | ast.List) for target in targets)
                    else [value]
                )
            )
            if not aliases:
                stored(value, node)
        unpacked = [pair for whole_target in targets for pair in _unpacked(whole_target, value)]
        for target, value in unpacked:
            if isinstance(target, ast.Attribute | ast.Subscript) and value is not None:
                stored(value, node, _holder_of(target))
                if isinstance(target, ast.Attribute) and isinstance(value, ast.Attribute):
                    # `self.client = self.http`: whatever `http` holds.
                    found.holder_aliases.append((target.attr, value.attr))
            if isinstance(target, ast.Attribute):
                named(target.attr, _root_name(target), node)
                stored_into(target.value, target.attr, node, value if isinstance(node, ast.Assign | ast.AnnAssign) else None)
                if builtins_of(target.value):
                    # `builtins.print = send_log`: every module's `print`.
                    found.names.append((f"builtins.{target.attr}", where(node)))
                if target.attr in _MODULE_HOOKS:
                    # `sys.modules[__name__].__class__ = Lazy` changes every read.
                    changed(target.value, node, namespace=True)
            elif isinstance(target, ast.Subscript):
                key = _literal(target.slice)
                if _is_module_table(target.value, scopes.imported_from):
                    # `sys.modules["agent_config"] = stub` replaces a module;
                    # `del sys.modules[name]` makes the next import read it again.
                    if not isinstance(node, ast.Delete) and not reloads_itself(value, target.slice):
                        record(scopes.lookup(target.slice), node)
                elif key is not None and _namespace_of(target.value) is not None:
                    # `vars(m)["X"]`, `m.__dict__["X"]`, `globals()["X"]` name
                    # a module attribute.
                    named(key, _root_name(target), node)
                    owner = _namespace_of(target.value)
                    stored_into(owner if isinstance(owner, ast.AST) else None, key, node, value)
                else:
                    changed(target.value, node)
        if isinstance(node, ast.Name | ast.Attribute) and isinstance(node.ctx, ast.Load) and id(node) not in called:
            # A function used as a value. `@deco` hands it a definition;
            # `return wrap` hands it to whoever calls the factory.
            name = node.id if isinstance(node, ast.Name) else node.attr
            if id(node) in returning:
                found.returned.append((name, returning[id(node)]))
            elif id(node) not in decorating:
                if isinstance(node, ast.Name):
                    # A function the file defines or imports, or one it
                    # does not bind (a star import): not a variable or a module.
                    _, kinds = scopes.kinds(node.id, scopes.owner.get(id(node)))
                    if not kinds or kinds & {"def", "from"}:
                        found.referenced.add(name)
                elif (isinstance(node.value, ast.Name) and node.value.id in {"self", "cls"}) or any(
                    _named_module(key) is not None for key in scopes.keys(node.value) if not _is_namespace(key)
                ):
                    # A bound method, or a module's function.
                    found.referenced.add(name)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            # A parameter a nested function captures is kept in its closure.
            owner = scopes.owner.get(id(node))
            binder, kinds = scopes.kinds(node.id, owner)
            if "param" in kinds and binder is not None and binder is not owner:
                found.through.append((scopes.parameters[id(binder)][node.id], "escape", where(node)))
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        called.add(id(func))
        callee = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
        if callee is not None and id(node) not in decorating:
            found.called.add(scopes.aliases.get(callee, callee))
        if callee in _IMPORT_HOOK_REGISTRARS:
            for argument in [*node.args, *(keyword.value for keyword in node.keywords)]:
                if isinstance(argument, ast.Name | ast.Attribute):
                    found.hooks.add(argument.id if isinstance(argument, ast.Name) else argument.attr)
        positional, keywords = scopes.arguments(node)
        if callee is None or callee not in _MODULE_READERS:
            # A namespace handed to a reader (`get_type_hints(globalns=…)`)
            # is only read.
            handed_keys = frozenset(
                key
                for _, keys in (*positional, *keywords)
                for key in keys
                if _module_ish(key) and not (_is_namespace(key) and callee in _NAMESPACE_READERS)
            )
            if handed_keys:
                found.passed.append((scopes.aliases.get(callee, callee) if callee else None, handed_keys, where(node)))
        if callee is not None:
            if positional or keywords:
                found.calls.append((scopes.aliases.get(callee, callee), positional, keywords))
            if callee not in _NAMESPACE_READERS or _spelling(func).endswith("patch.dict"):
                # `code.interact(local=globals())`: a namespace handed to a call
                # may be changed there. A `*`/`**` spread passes a copy;
                # `mock.patch.dict(ns, …)` changes it in place.
                handed = [
                    *(keys for index, keys in positional if index is not None),
                    *(keys for keyword, keys in keywords if keyword is not None),
                ]
                record({key for keys in handed for key in keys if _is_namespace(key)}, node, "dict")
        spelling = _spelling(func)
        root, _, rest = spelling.partition(".")
        if root in imported:
            # `from unittest.mock import patch as p; p.object(…)`.
            spelling = imported[root] + (f".{rest}" if rest else "")
        builtin = spelling.removeprefix("builtins.").removeprefix("__builtins__.")
        if spelling.endswith("patch.multiple") and node.args:
            # `mock.patch.multiple(requests, get=h)`: each keyword is set.
            for keyword in node.keywords:
                if keyword.arg is not None:
                    named(keyword.arg, _root_name(node.args[0]), node)
                    stored_into(node.args[0], keyword.arg, node, keyword.value)
        if spelling.rsplit(".", 1)[-1] in {"patch_function_wrapper", "wrap_function_wrapper", "wrap_object"} and len(
            node.args
        ) >= 2:
            # `wrapt.wrap_function_wrapper("requests", "get", h)`: a patch
            # named by strings.
            module, name = _literal(node.args[0]), _literal(node.args[1])
            if module is not None and name is not None:
                found.library.append((f"{module}.{name}", where(node)))
        receiver: ast.AST | None = None
        arguments = node.args
        if builtin in {"setattr", "delattr"} or spelling.endswith(("patch.object", "monkeypatch.setattr")):
            # `mock.patch.object(requests, "get", h).start()` stores like setattr.
            receiver, arguments = (node.args[0], node.args[1:]) if node.args else (None, [])
        elif isinstance(func, ast.Attribute) and func.attr in {"__setattr__", "__delattr__"}:
            # `object.__setattr__(m, k, v)` names its object; `m.__setattr__(k, v)` is bound.
            if len(node.args) >= (3 if func.attr == "__setattr__" else 2):
                receiver, arguments = node.args[0], node.args[1:]
            else:
                receiver = func.value
        elif spelling in {"setitem", "operator.setitem"} and node.args:
            key = _literal(node.args[1]) if len(node.args) > 1 else None
            if key is not None and _namespace_of(node.args[0]) is not None:
                named(key, _root_name(node.args[0]), node)
            else:
                changed(node.args[0], node)
            continue
        elif builtin in {"exec", "eval"}:
            # `exec(source, globals())`. Without a namespace, `exec` may still
            # run `global X; X = ...` in this module.
            spaces = [
                *node.args[1:3],
                *(keyword.value for keyword in node.keywords if keyword.arg in {"globals", "locals"}),
            ]
            if not spaces:
                record({own_module}, node)
            for space in spaces:
                changed(space, node)
            continue
        if receiver is not None:
            key = _literal(arguments[0] if arguments else None)
            if key is not None:
                named(key, _root_name(receiver), node)
                stored_into(receiver, key, node, arguments[1] if len(arguments) > 1 else None)
                if len(arguments) > 1:
                    # `setattr(self, "http", requests)` keeps it under `http`.
                    stored(arguments[1], node, key)
                if builtins_of(receiver):
                    found.names.append((f"builtins.{key}", where(node)))
            if key is None or key in _MODULE_HOOKS:
                changed(receiver, node, namespace=True)
            continue
        if isinstance(func, ast.Attribute) and _is_module_table(func.value, scopes.imported_from):
            if func.attr in _DICT_CHANGES | {"__delitem__"}:
                key = node.args[0] if node.args and func.attr != "update" else None
                record(scopes.lookup(key) if key is not None else {_ANY}, node)
            continue
        if not (isinstance(func, ast.Attribute) and func.attr in _STORING_METHODS | _CONTAINER_METHODS | _DICT_CHANGES):
            continue
        direct = _namespace_of(func.value) is not None
        if func.attr in _STORING_METHODS:
            for argument in node.args[1:] if func.attr in {"insert", "setdefault", "__setitem__"} else node.args:
                if direct and isinstance(argument, ast.Dict):
                    # `vars(self).update({"http": requests})`: kept under `http`.
                    for item_key, item in zip(argument.keys, argument.values, strict=True):
                        stored(item, node, _literal(item_key) if item_key is not None else None)
                else:
                    stored(argument, node)
            for keyword in node.keywords:
                stored(keyword.value, node, keyword.arg if direct else None)
        if not direct:
            if func.attr in _CONTAINER_METHODS and not (
                func.attr == "update" and not (node.args and isinstance(node.args[0], ast.Dict))
            ):
                named(_stored_name(func.value), _root_name(func.value), node)
            if func.attr in _DICT_CHANGES:
                changed(func.value, node)
            continue
        if func.attr not in _DICT_CHANGES:
            continue
        # A namespace's own method: names it is given, else the whole of it.
        names: list[str] = []
        unseen = False
        if func.attr == "update":
            for argument in node.args:
                keys = [_literal(key) for key in argument.keys] if isinstance(argument, ast.Dict) else [None]
                names.extend(key for key in keys if key is not None)
                unseen = unseen or None in keys
            for keyword in node.keywords:
                if keyword.arg is None:
                    unseen = True
                else:
                    names.append(keyword.arg)
        elif func.attr in {"setdefault", "pop", "__setitem__"} and _literal(node.args[0] if node.args else None):
            names.append(_literal(node.args[0]))  # type: ignore[arg-type]
        else:
            unseen = True
        owner = _namespace_of(func.value)
        for name in names:
            named(name, _root_name(func.value), node)
            stored_into(owner if isinstance(owner, ast.AST) else None, name, node)
        if unseen:
            changed(func.value, node)
    found.defined = set(scopes.defined)
    found.constructors = set(scopes.constructors)
    for name in scopes.bindings[None]:
        # What another file gets with `from this import name` (#872 review 13).
        keys = frozenset(key for key in scopes.name(name, None) if _named_module(key) is not None)
        if keys and keys != {f"~{name}"}:
            found.exports.append((name, keys))
    for value in bound_from:
        for part in ast.walk(value):
            if (
                isinstance(part, ast.Attribute)
                or (isinstance(part, ast.Call) and _spelling(part.func) == "getattr" and len(part.args) >= 2)
                # `operator.attrgetter("transport.adapters")(net)`.
                or (
                    isinstance(part, ast.Call)
                    and isinstance(part.func, ast.Call)
                    and _spelling(part.func.func).rsplit(".", 1)[-1] == "attrgetter"
                )
            ):
                # `ADAPTERS = net.transport.adapters`, `getattr(net.transport,
                # "adapters")`, in a function writing a global too: a key
                # reached by attributes, where a package's binding is read, has
                # no lock (#872 reviews 22-23). `ALL = [reviewer]` keeps the
                # import's.
                for key in scopes.keys(part):
                    if isinstance(key, str) and key.startswith("~"):
                        found.export_locks[key] = 0
    for statement in _module_level(tree.body):
        # A relative import, and a star import, re-export what they bind under
        # the module's own package: `from .transport import http` in
        # `net/__init__.py` makes `net.http` `net.transport.http` (#872
        # review 20).
        if not isinstance(statement, ast.ImportFrom):
            continue
        package = ref.split("/")[:-1]
        if statement.level > len(package) + 1:
            continue
        origin = [
            *(package[: len(package) - statement.level + 1] if statement.level else []),
            *(statement.module.split(".") if statement.module else []),
        ]
        for alias in statement.names:
            if alias.name == "*" and origin:
                found.exports.append(("*", frozenset({f"~{'.'.join(origin)}"})))
            elif alias.name != "*":
                key = f"~{'.'.join([*origin, alias.name])}"
                found.export_locks[key] = min(len(origin), found.export_locks.get(key, len(origin)))
                if statement.level:
                    found.exports.append((alias.asname or alias.name, frozenset({key})))
    for statement in classes:
        # `class Transport: from requests import Session`: the class holds it
        # under that name, kept where it is not followed (#872 review 22).
        for item in _module_level(statement.body):
            if isinstance(item, ast.Import | ast.ImportFrom) and not getattr(item, "level", 0):
                for alias in item.names:
                    if alias.name == "*":
                        continue
                    held = (
                        alias.name if isinstance(item, ast.Import) else f"~{item.module}.{alias.name}"
                    )
                    attribute = alias.asname or alias.name.split(".")[0]
                    if isinstance(item, ast.Import) and not alias.asname:
                        held = attribute
                    found.escapes.append((frozenset({held}), where(item)))
                    found.holders.append((frozenset({held}), attribute, where(item)))
                    if statement in tree.body:
                        found.exports.append((f"{statement.name}.{attribute}", frozenset({held})))
    for statement in tree.body:
        # A module-level class's attributes: `clients.Http.lib` (#872 review 19).
        if isinstance(statement, ast.ClassDef):
            for item in statement.body:
                if isinstance(item, ast.Assign | ast.AnnAssign) and item.value is not None:
                    keys = frozenset(key for key in scopes.keys(item.value) if _named_module(key) is not None)
                    for target in item.targets if isinstance(item, ast.Assign) else [item.target]:
                        if keys and isinstance(target, ast.Name):
                            found.exports.append((f"{statement.name}.{target.id}", keys))
    return found


#: Files and bytes the patch scan reads in one scope.
MAX_PATCH_FILES = 2000
MAX_PATCH_BYTES = 2_000_000


def _looks_like_test(relative: str) -> bool:
    parts = relative.split("/")
    name = parts[-1]
    return (
        any(part in {"test", "tests"} for part in parts[:-1])
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name in {"conftest.py", "test.py", "tests.py"}
    )


def scope_patches(root: Path, is_test: Callable[[str], bool]) -> list[dict[str, str]]:
    """Every place in the scope, outside tests, that patches an HTTP library.

    A patch anywhere in the application changes what each request does, so it
    is a limit on every tool that sends (#872 review 5). Past the scan's bound
    that is named too. Read once per scope and test rule.
    """

    return [dict(item) for item in _scope_scan(root.resolve(), is_test)[0]]


def scope_mutations(
    root: Path, is_test: Callable[[str], bool]
) -> tuple[dict[str, str], dict[str, str]]:
    """Names and whole namespaces changed in the scope (:func:`module_mutations`).

    A change made through a function's parameter changes each argument its
    callers pass there, through any number of helpers (`apply_overrides(CONFIG,
    …)` changes `CONFIG`). ``"*"`` in the second: a namespace changed that the
    scan cannot tie to one module, so any module may have changed.
    """

    _, names, whole, _ = _scope_scan(root.resolve(), is_test)
    return dict(names), dict(whole)


def scope_library_patches(root: Path, is_test: Callable[[str], bool]) -> dict[str, str]:
    """Imported modules' attributes stored into in the scope, with where.

    ``json.dumps`` for `json.dumps = audited`, ``json.*`` for a store under a
    name the scan cannot see, ``*`` when a file cannot be read (#872 review 12).
    """

    return dict(_scope_scan(root.resolve(), is_test)[3])


def _arguments_at(
    positional: tuple[tuple[int | None, frozenset[_Key]], ...],
    keywords: tuple[tuple[str | None, frozenset[_Key]], ...],
    parameter: _Parameter,
) -> list[frozenset[_Key]]:
    """What a call passes to `parameter`: by position, keyword, or unpacking."""

    _, _, position, name, is_method = parameter
    found: list[frozenset[_Key]] = []
    if name.startswith("**"):
        return [keys for _, keys in keywords]
    rest = name.startswith("*")
    for index, keys in positional:
        # `Class.method(obj, x)` passes `self` too.
        if index is None or (
            position >= 0
            and (index == position or (is_method and index == position + 1) or (rest and index >= position))
        ):
            found.append(keys)
    if not rest:
        found.extend(keys for keyword, keys in keywords if keyword is None or keyword == name)
    return found


def _called(key: _Key, found: set[str], depth: int = 0) -> None:
    """Every function a key's calls name, arguments included."""
    if depth > MAX_OBJECT_DEPTH or not isinstance(key, tuple):
        return
    if _is_namespace(key):
        _called(key[1], found, depth + 1)
    elif _is_call(key):
        found.add(key[1])
        for _, keys in (*key[2], *key[3]):
            for item in keys:
                _called(item, found, depth + 1)


class _Resolver:
    """What a key from any file of the scope may be, once every file is read.

    Each function's summary, what it may return besides its own parameters
    and which of those it returns, is solved on a worklist (#872 review 11).
    Nothing recurses through the call graph, so a large cycle of functions
    returning each other's results is solved in linear time. A call is read
    through the functions of the module it names, or of every module when
    it names none.
    """

    def __init__(self, returns: dict[tuple[str, str], set[_Key]]) -> None:
        self.returns = returns
        self.summary: dict[tuple[str, str], tuple[frozenset[_Key], frozenset[_Key]]] = {}
        self.named: dict[str, list[tuple[str, str]]] = {}
        dependents: dict[str, set[tuple[str, str]]] = {}
        for function, items in returns.items():
            self.named.setdefault(function[1], []).append(function)
            called: set[str] = set()
            for item in items:
                _called(item, called)
            for callee in called:
                dependents.setdefault(callee, set()).add(function)
        pending = list(returns)
        queued = set(pending)
        while pending:
            function = pending.pop()
            queued.discard(function)
            keys: set[_Key] = set()
            parameters: set[_Key] = set()
            for item in returns[function]:
                for value in self.resolve(item):
                    if not _is_parameter(value):
                        keys.add(value)
                    elif value[1] == function[1]:
                        parameters.add(value)
                    else:
                        # An enclosing function's parameter, returned by a
                        # closure: kept there (an escape where it is passed).
                        keys.add(_UNSEEN)
            summary = (frozenset(keys), frozenset(parameters))
            if summary != self.summary.get(function):
                self.summary[function] = summary
                for dependent in dependents.get(function[1], ()):
                    if dependent not in queued:
                        queued.add(dependent)
                        pending.append(dependent)

    def resolve(self, key: _Key, depth: int = 0) -> frozenset[_Key]:
        """What `key` may be, each call read through the summaries so far."""
        if depth > MAX_OBJECT_DEPTH:
            return frozenset({_ANY})
        if _is_namespace(key):
            return _namespaces(self.resolve(key[1], depth + 1))
        if not _is_call(key):
            return frozenset({key})
        _, function, positional, keywords, home = key
        found: set[_Key] = set()
        for definition in [(home, function)] if home is not None else self.named.get(function, []):
            keys, parameters = self.summary.get(definition, (frozenset(), frozenset()))
            found |= keys
            for parameter in parameters:
                for argument in _arguments_at(positional, keywords, parameter):
                    for item in argument:
                        found |= self.resolve(item, depth + 1)
        return frozenset(found)


@functools.lru_cache(maxsize=4)
def _scope_scan(
    root: Path, is_test: Callable[[str], bool]
) -> tuple[
    tuple[dict[str, str], ...],
    tuple[tuple[str, str], ...],
    tuple[tuple[str, str], ...],
    tuple[tuple[str, str], ...],
]:
    found: list[dict[str, str]] = []
    mutated: dict[str, str] = {}
    library: dict[str, str] = {}
    stems: set[str] = set()
    files: list[FileMutations] = []
    count = 0
    for directory, subdirectories, names in os.walk(root, followlinks=False):
        subdirectories.sort()
        for name in sorted(names):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if not name.endswith(".py") or path.is_symlink() or is_test(relative):
                continue
            count += 1
            stems.add(_module_stem(relative))
            if count > MAX_PATCH_FILES:
                found.append(
                    {
                        "at": ".",
                        "why": f"the scope holds more than {MAX_PATCH_FILES} Python files; "
                        "patches to HTTP libraries past them are not read",
                    }
                )
                return tuple(found), tuple(mutated.items()), ((_ANY, "."),), (("*", "."),)
            try:
                raw = path.read_bytes()
                if len(raw) > MAX_PATCH_BYTES:
                    raise ValueError("too large")
                tree = ast.parse(raw)
                found.extend(http_patches(tree, relative))
                changes = module_mutations(tree, relative)
                dotted = relative.removesuffix(".py").replace("/", ".").removesuffix(".__init__")
                changes.module = root.name if dotted == "__init__" else dotted
            except (SyntaxError, ValueError, RecursionError, OSError):
                # A module the scan cannot read may patch or change anything.
                found.append(
                    {"at": relative, "why": "could not be read for patches to HTTP libraries; not read"}
                )
                files.append(FileMutations(whole=[(_ANY, relative)]))
                library.setdefault("*", relative)
                continue
            for key, where in changes.names:
                mutated.setdefault(key, where)
            for key, where in changes.library:
                library.setdefault(key, where)
            files.append(changes)
    whole, replaced = _changed_namespaces(files, stems)
    for key, where in replaced.items():
        library.setdefault(key, where)
    return (tuple(found), tuple(mutated.items()), tuple(whole.items()), tuple(library.items()))


def _changed_namespaces(
    files: list[FileMutations], stems: set[str] | frozenset[str] = frozenset()
) -> tuple[dict[str, str], dict[str, str]]:
    """Each namespace changed under unseen names, and each module attribute
    stored into, with where (#872 reviews 10-13).

    ``stems`` are the scope's module names: an imported name that is none of
    them (`from registry import settings_module`) is an object the scan does
    not follow.
    """

    returns: dict[tuple[str, str], set[_Key]] = {}
    in_class: list[tuple[tuple[str, str], str]] = []
    #: ``(module name, name) -> [(module path, keys)]``: what each module
    #: binds, read through that module only: `from pkg.tool import tool`
    #: elsewhere does not make `pkg.tool` hold its own function, nor does
    #: `pkg/web` binding a name make `pkg/internal/web` bind it (#872 review 19).
    exported: dict[tuple[str, str], list[tuple[list[str], frozenset[_Key]]]] = {}
    by_name: dict[str, set[_Key]] = {}
    #: ``~pkg.mod.name -> 2``: a path's first parts the import system found as
    #: modules, where no name a package binds is read (#872 review 21).
    locks: dict[str, int] = {}
    for changes in files:
        for key, lock in changes.export_locks.items():
            locks[key] = min(lock, locks.get(key, lock))
        for name, keys in changes.exports:
            module = changes.module.split(".")
            exported.setdefault((module[-1], name), []).append((module, keys))
            by_name.setdefault(name, set()).update(keys)

    def bound_through(prefix: list[str], name: str) -> set[_Key]:
        """What `prefix` binds as `name`, `prefix` read as a module of the
        scope: one module path the other's dotted suffix, the scope's root
        and the package root not being the same directory."""
        found: set[_Key] = set()
        for module, keys in exported.get((prefix[-1], name), ()):
            if same_module(module, prefix):
                found |= keys
        for module, keys in exported.get((prefix[-1], "*"), ()):
            # `from net.transport import *` in `net`: `net.http` may be
            # `net.transport.http`.
            if same_module(module, prefix):
                for key in keys:
                    if (path := _module_path(key)) is not None:
                        found.add(f"~{path}.{name}")
                        locks.setdefault(f"~{path}.{name}", path.count(".") + 1)
        return found

    def same_module(module: list[str], prefix: list[str]) -> bool:
        shorter, longer = sorted((module, prefix), key=len)
        return longer[len(longer) - len(shorter):] == shorter

    expansions: dict[str, frozenset[_Key]] = {}

    def expand_one(item: str) -> frozenset[_Key]:
        """What one imported name may also be (see :func:`expand`), once."""
        if item in expansions:
            return expansions[item]
        found: set[_Key] = {item}
        # What a name the code reads is may be any binding along it: no lock.
        frontier = [(item, 0, 0)]
        while frontier:
            current, hops, lock = frontier.pop()
            if hops >= MAX_EXPANSION_HOPS or len(found) >= MAX_EXPANSIONS:
                # Past the bound: any module, not nothing (#872 review 19).
                found.add(_ANY)
                break
            parts = current[1:].split(".")
            for index, part in enumerate(parts):
                # A name a module of the scope binds, read through that
                # module (`mods.urllib`), or imported by itself; or a
                # module-level class's attribute (`clients.Http.lib`). Not
                # inside what an import found as modules: `from .agent import
                # reviewer` in `reviewer/__init__.py` is `reviewer.agent`'s
                # name, whatever the package `reviewer` is bound to.
                if index < lock:
                    continue
                if index:
                    matches = [(1, bound_through(parts[:index], part))]
                    if index + 1 < len(parts):
                        matches.append((2, bound_through(parts[:index], f"{part}.{parts[index + 1]}")))
                else:
                    matches = [(len(parts), by_name.get(current[1:], set()) if len(parts) <= 2 else set())]
                for width, bound in matches:
                    replaced, after = parts[:index + width], parts[index + width:]
                    for extra in sorted(bound, key=str):
                        path = _module_path(extra)
                        within = path.split(".") if path is not None else []
                        tail = within[len(replaced):]
                        if within[:len(replaced)] == replaced and tail and after[:len(tail)] == tail:
                            # `pkg.init` binds `pkg.init.init` (a package
                            # re-exporting its module's function), and this path
                            # already goes on through it: once more adds nothing.
                            continue
                        new = extra if not after else (f"~{path}.{'.'.join(after)}" if path else None)
                        if new is None or new in found:
                            continue
                        found.add(new)
                        if isinstance(new, str) and new.startswith("~"):
                            frontier.append((new, hops + 1, locks.get(extra, 0) if isinstance(extra, str) else 0))
        expansions[item] = frozenset(found)
        return expansions[item]

    def expand(items: set[_Key]) -> set[_Key]:
        """`~settings_module` imported from where it binds `config`: `config`
        too; and `~mods.urllib.request.Request`, where `mods` binds `urllib`,
        is `urllib.request.Request` (#872 review 18)."""
        found = set(items)
        for item in items:
            if isinstance(item, str) and item.startswith("~"):
                found |= expand_one(item)
        return found

    defined: set[str] = set()
    constructors: set[str] = set()
    for changes in files:
        for function, keys, where, method in changes.returns:
            returns.setdefault(function, set()).update(keys)
            if method:
                in_class.append((function, where))
        defined |= changes.defined
        constructors |= changes.constructors
    resolver = _Resolver(returns)
    # A method's result is read as an object the scan does not follow
    # (`registry.get(name)`), so a module a method returns is kept there.
    method_escapes = [
        (module, where)
        for function, where in in_class
        for key in resolver.summary.get(function, (frozenset(), frozenset()))[0]
        if (module := _named_module(key)) is not None
    ]

    whole: dict[str, str] = {}
    replaced: dict[str, str] = {}
    unseen_stores: list[tuple[str, str]] = []
    unclassified: list[str] = []
    escaped: list[tuple[str, str]] = list(method_escapes)
    pending: list[tuple[_Parameter, str, str]] = [item for changes in files for item in changes.through]

    def reach(keys: frozenset[_Key] | set[_Key], kind: str, where: str) -> None:
        """What `keys` may be, changed (`change`, `dict`) or kept (`escape`)."""
        resolved = expand({item for key in keys for item in resolver.resolve(key)})
        if kind == "change" or kind.startswith("store:"):
            resolved = {_unwrap(item) for item in resolved}
        if kind == "change" and (
            _UNSEEN in resolved
            or not any(_module_ish(item) for item in resolved)
            or any(
                isinstance(item, str)
                and item.startswith("~")
                and stems
                and _named_module(item) not in stems
                and not expand_one(item) - {item}
                for item in resolved
            )
        ):
            # Possibly an object the scan does not follow.
            unclassified.append(where)
        if kind.startswith("store:"):
            attribute = kind.partition(":")[2]
            plain = attribute.startswith("=")
            attribute = attribute.lstrip("=")
            if not any(_module_ish(item) for item in resolved):
                # What the call returns is not followed: an object the scan
                # does not follow.
                unseen_stores.append((attribute, where))
            for item in resolved:
                if _is_parameter(item):
                    pending.append((item, kind, where))
                elif item == _CLASS:
                    # A class a helper is handed (`force(type(req))`).
                    if attribute.rsplit(".", 1)[-1] not in _TUNING_ATTRIBUTES:
                        replaced.setdefault(f"class:{attribute}", where)
                elif item == _UNSEEN:
                    unseen_stores.append((attribute, where))
                elif (module := _named_module(item)) is not None:
                    # `r = requests; r.Session.request = …` records
                    # `requests.Session.request`; a name that is no module of
                    # the scope may also be an object the scan does not follow.
                    full = f"{_module_path(item)}.{attribute}"
                    if not (plain and _tuning(full.split("."))):
                        replaced.setdefault(full, where)
                    if item.startswith("~") and stems and module not in stems and not expand_one(item) - {item}:
                        unseen_stores.append((attribute, where))
            return
        for item in resolved:
            if _is_parameter(item):
                pending.append((item, kind, where))
            elif kind == "escape" or kind.startswith("escape:"):
                module = _named_module(item)
                if module is not None:
                    escaped.append((module, where))
                    path = _module_path(item) or module
                    if _stack_object(path) or (_strong(item) and path.split(".")[0] not in _HTTP_STACK):
                        # A module an import binds, or anything of the HTTP
                        # stack but a constant or an exception
                        # (`urllib.request`, `requests.Session`), kept where
                        # it is not followed: an attribute stored on an object
                        # the scan does not follow may be stored on it.
                        replaced.setdefault(f"kept:{path}", where)
                        if kind.startswith("escape:") and _stack_object(path):
                            # The attribute it is kept under (`self.http`).
                            replaced.setdefault(f"via:{kind[7:]}", where)
            elif kind == "dict":
                # Only a namespace dict is a module changed; a dict the scan
                # does not follow may be one kept elsewhere.
                if _is_namespace(item):
                    reach({item[1]}, "change", where)
                elif item == _UNSEEN:
                    unclassified.append(where)
            elif item == _CLASS:
                # `setattr(type(req), name, value)`: any attribute of a class.
                replaced.setdefault("class:*", where)
            elif isinstance(item, str) and item not in {_UNSEEN, _OWN_CLASS}:
                whole.setdefault(_named_module(item) or item.lstrip("=") or item, where)
                if _strong(item):
                    # A library module's namespace changed: any of its functions.
                    replaced.setdefault(f"{item}.*", where)

    for changes in files:
        for key, where in changes.whole:
            reach({key}, "change", where)
        for key, where in changes.dicts:
            reach({key}, "dict", where)
        for keys, where in changes.escapes:
            reach(keys, "escape", where)
        for keys, attribute, where in changes.stores:
            reach(keys, f"store:{attribute}", where)
        for attribute, where in changes.unseen:
            unseen_stores.append((attribute, where))
        for callee, keys, where in changes.passed:
            if callee is None or callee not in defined:
                # Handed to a call the scope does not define: kept where it
                # is not followed.
                reach(keys, "escape", where)
        unclassified.extend(changes.unclassified)

    # A parameter passed on to a function that changes (or keeps) its
    # parameter is itself changed (or kept), to a fixed point: `boot()` →
    # `load_env(mod)` → `_set_all(ns)` → `setattr(ns, k, v)`.
    calls: dict[str, list[tuple[Any, Any]]] = {}
    referenced: set[str] = set()
    returned: dict[str, set[str]] = {}
    called: set[str] = set()
    hooks: set[str] = set(_IMPORT_HOOK_METHODS)
    for changes in files:
        for callee, positional, keywords in changes.calls:
            calls.setdefault(callee, []).append((positional, keywords))
        referenced |= changes.referenced
        called |= changes.called
        hooks |= changes.hooks
        for name, function in changes.returned:
            returned.setdefault(name, set()).add(function)

    def as_value(function: str) -> bool:
        """Whether `function` reaches callers the scan cannot name.

        Used only as `@function`, or returned by a factory used only as
        `@factory(...)`, it is handed decorated definitions, never a module.
        A class named as a value (a base, `isinstance`) is a type; one
        called through another name is handed what that call passes.
        """
        if function in constructors:
            return False
        return function in referenced or any(
            factory in referenced or factory in called for factory in returned.get(function, ())
        )

    reaching: set[tuple[_Parameter, str]] = set()
    while pending:
        parameter, kind, where = pending.pop()
        if (parameter, kind) in reaching:
            continue
        reaching.add((parameter, kind))
        function = parameter[1]
        if kind == "change" and function in hooks:
            # The import system hands an import hook any module.
            whole.setdefault(_ANY, where)
        elif kind.startswith("store:") and function in hooks:
            replaced.setdefault(f"*.{kind.partition(':')[2].lstrip('=').rsplit('.', 1)[-1]}", where)
        elif function is None or as_value(function):
            # A lambda, or a function used as a value (a callback,
            # `functools.partial`): what it is called with cannot all be
            # named. It changes an object the scan does not follow; a module
            # reaches it only by being handed somewhere, which is kept.
            if kind == "change":
                unclassified.append(where)
            elif kind.startswith("store:"):
                unseen_stores.append((kind.partition(":")[2].lstrip("="), where))
            if function is None:
                continue
        for positional, keywords in calls.get(function, ()):  # type: ignore[arg-type]
            for keys in _arguments_at(positional, keywords, parameter):
                reach(keys, kind, where)
    if unclassified:
        # A module kept where the scan does not follow it, and a namespace
        # changed on an object the scan does not follow: it may be that
        # module.
        for module, where in escaped:
            whole.setdefault(module, where)
            if f"kept:{module}" in replaced:
                replaced.setdefault(f"{module}.*", where)
    for attribute, where in unseen_stores:
        # Matched against the modules kept above when a call is read, not
        # multiplied out here.
        replaced.setdefault(f"unseen:{attribute}", where)
    for changes in files:
        for keys, holder, where in changes.holders:
            for item in expand(set(keys)):
                path = _module_path(item)
                if path is not None and _stack_object(path):
                    replaced.setdefault(f"via:{holder}", where)
    # `self.client = self.http`, or a property returning `self._http`: the
    # alias holds what its source holds.
    aliases = [pair for changes in files for pair in changes.holder_aliases]
    grew = True
    while grew:
        grew = False
        for alias, source in aliases:
            if f"via:{source}" in replaced and f"via:{alias}" not in replaced:
                replaced[f"via:{alias}"] = replaced[f"via:{source}"]
                grew = True
    return whole, replaced


def summarize(
    calls: list[dict[str, Any]],
    limits: list[dict[str, str]],
    limit_count: int,
    truncated: bool,
    module: PythonModule,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    listed: int | None = None,
    effects: list[dict[str, Any]] | None = None,
    listed_effects: int | None = None,
) -> dict[str, Any]:
    """The evidence, and the effect claims it supports.

    ``listed``: how many of ``calls`` are published; the rest are past the
    call bound and count only for the effect. ``effects`` and
    ``listed_effects`` are the same for what the tool reaches beyond HTTP
    (#913).

    A call that writes supports ``write`` (or ``destructive``) wherever the
    rest of the tool leads; so does a library effect that writes, and one
    that runs a process supports ``code_execution``, one that sends a message
    ``external_communication``. ``read`` needs more: at least one outbound
    call or effect, every one of them a read, and nothing the read could not
    follow.
    """

    effects = effects or []
    claims: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for item in calls:
        if item["effect"] in {"write", "destructive"}:
            key = (item["effect"], item["method"], item["url"], item.get("graphql"))
            if key in seen:
                continue
            seen.add(key)
            claims.append(
                {
                    "effect": item["effect"],
                    "at": item["at"],
                    "method": item["method"],
                    "url": item["url"],
                    **({"graphql": item["graphql"]} if "graphql" in item else {}),
                }
            )
    for item in effects:
        effect = effect_tables.CLAIM_EFFECTS.get((item["family"], item["operation"]))
        if effect is None:
            continue
        key = (effect, item["family"], item["call"], item.get("target"))
        if key in seen:
            continue
        seen.add(key)
        claims.append(
            {
                "effect": effect,
                "at": item["at"],
                "family": item["family"],
                "operation": item["operation"],
                "call": item["call"],
                **({"target": item["target"]} if "target" in item else {}),
            }
        )
    if (
        (calls or effects)
        and not claims
        and not limits
        and not truncated
        and all(item["effect"] == "read" for item in calls)
        and all(item["operation"] == "read" for item in effects)
    ):
        claims.append(
            {
                "effect": "read",
                "at": f"{module.ref}:{function.lineno}",
                "calls": len(calls),
                **({"effects": len(effects)} if effects else {}),
            }
        )
    result: dict[str, Any] = {
        "calls": calls[:listed] if listed is not None else calls,
        **({"effects": effects[:listed_effects] if listed_effects is not None else effects} if effects else {}),
        "limits": limits,
        "effect": claims[0]["effect"] if len(claims) == 1 else _strongest(claims),
        "effect_claims": claims,
    }
    if limit_count > len(limits):
        result["more_limits"] = limit_count - len(limits)
    return result


def _strongest(claims: list[dict[str, Any]]) -> str | None:
    effects = [claim["effect"] for claim in claims if claim["effect"] in EFFECT_RISK_RANK]
    return max(effects, key=lambda effect: EFFECT_RISK_RANK[effect]) if effects else None
