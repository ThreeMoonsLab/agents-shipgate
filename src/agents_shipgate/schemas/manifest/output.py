from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from agents_shipgate.schemas.manifest._common import STRICT_MODEL_CONFIG


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

    directory: str = "agents-shipgate-reports"
    formats: list[Literal["markdown", "json", "sarif"]] = Field(
        default_factory=lambda: ["markdown", "json"]
    )
    packet: PacketOutputConfig = Field(default_factory=PacketOutputConfig)
