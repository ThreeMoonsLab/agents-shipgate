from __future__ import annotations

import re
from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from agents_shipgate.schemas.manifest._common import STRICT_MODEL_CONFIG

#: ``output.directory`` as the published schema states it (#1031): no leading
#: ``/`` or ``\`` (a POSIX root, a Windows rooted path or UNC share), no drive
#: letter, and no ``..`` component. ECMA-262 and Python read it alike. The
#: validator below owns the message; ``tests/test_output_directory_containment.py``
#: holds the two to the same answers.
OUTPUT_DIRECTORY_PATTERN = r"^(?![/\\])(?![A-Za-z]:)(?!(?:.*[/\\])?\.\.(?:[/\\]|$))"


def output_directory_escape(value: str) -> str | None:
    """Why ``value`` could place reports outside the manifest's directory, or ``None``."""

    if (
        value.startswith(("/", "\\"))
        or PurePosixPath(value).is_absolute()
        or PureWindowsPath(value).drive
    ):
        return "an absolute path"
    if ".." in re.split(r"[/\\]", value):
        return "a '..' component"
    return None


class PacketOutputConfig(BaseModel):
    """Optional ``output.packet`` block for ``shipgate.yaml``.

    Controls whether ``scan`` emits the Release Evidence Packet
    alongside ``report.{md,json}``. Independent of ``output.formats``
    so the existing ``--format`` contract is unchanged. ``pdf`` is
    accepted but only written when the optional ``[pdf]`` extras
    (``weasyprint``) are installed.
    """

    model_config = STRICT_MODEL_CONFIG

    enabled: bool = True
    formats: list[Literal["md", "json", "html", "pdf"]] = Field(
        default_factory=lambda: ["md", "json", "html"]
    )


class OutputConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    # Repository content names this directory, and a scan writes and clears
    # report files in it, so it is held inside the manifest's directory
    # (#1031). ``scan --out`` is the operator's way to write anywhere else.
    directory: str = Field(
        default="agents-shipgate-reports",
        json_schema_extra={"pattern": OUTPUT_DIRECTORY_PATTERN},
    )
    formats: list[Literal["markdown", "json", "sarif"]] = Field(
        default_factory=lambda: ["markdown", "json"]
    )
    packet: PacketOutputConfig = Field(default_factory=PacketOutputConfig)

    @field_validator("directory")
    @classmethod
    def _directory_stays_inside_the_manifest_directory(cls, value: str) -> str:
        escape = output_directory_escape(value)
        if escape is not None:
            # The value is refused when the manifest loads, before any flag is
            # read, so the recovery is an edit; ``--out`` is how to write
            # elsewhere once the manifest loads again.
            raise ValueError(
                f"output.directory must be a relative path inside the manifest's "
                f"directory; {value!r} is {escape}. Remove output.directory or "
                "make it a relative path inside that directory; to write reports "
                "elsewhere, pass `scan --out <dir>`."
            )
        return value
