"""Shared pieces of the manifest schema.

**Validators in this package raise ``ValueError``, never ``TypeError``.**
Pydantic converts ``ValueError`` and ``AssertionError`` raised inside a
validator into a ``ValidationError`` carrying the field's location, which
the config loader renders as a ``config_error`` naming the manifest key. A
``TypeError`` propagates instead: it escapes the config-loading boundary,
lands in the generic internal-error handler, and tells someone who wrote a
mapping where a list belongs that they hit a bug and should file an issue
(#387). ``tests/test_config.py`` fails the build if a ``raise TypeError``
reappears anywhere under ``schemas/``.
"""

from __future__ import annotations

from pydantic import ConfigDict

#: The sentinel ``init`` writes for a value it could not read out of the
#: repository. It lives here rather than beside the renderer because two
#: layers below the CLI have to recognize it: the placeholder collector that
#: reports what a manifest still owes, and the adapter registry, whose
#: "no adapter registered for source type" message is addressed to someone
#: holding a template rather than to someone who mistyped an adapter name.
MANIFEST_PLACEHOLDER_VALUE = "CHANGE_ME"

# Every manifest section uses ``extra="forbid"`` so typos at any level
# raise a Pydantic validation error rather than silently no-op'ing. The
# config loader translates those errors into ``ConfigError`` (exit 2)
# with a close-match suggestion for the offending field name.
STRICT_MODEL_CONFIG = ConfigDict(extra="forbid")


def describe_yaml_shape(value: object) -> str:
    """Name the YAML shape a manifest value arrived as.

    Manifest type mismatches are edits, not defects, so the message has to
    say what was written as well as what was expected. YAML names are used
    rather than Python ones: someone reading the error is looking at
    ``shipgate.yaml``, not at a traceback.
    """

    if value is None:
        return "null"
    if isinstance(value, bool):
        return "a boolean"
    if isinstance(value, dict):
        return "a mapping"
    if isinstance(value, (list, tuple)):
        return "a list"
    if isinstance(value, str):
        return "a string"
    if isinstance(value, (int, float)):
        return "a number"
    return f"a {type(value).__name__}"
