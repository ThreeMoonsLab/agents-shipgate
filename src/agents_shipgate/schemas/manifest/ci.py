from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from agents_shipgate.schemas.common import Severity
from agents_shipgate.schemas.manifest._common import STRICT_MODEL_CONFIG


class CiConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    mode: Literal["advisory", "strict"] = "advisory"
    fail_on: list[Severity] | None = None
    pr_comment: bool = True
    annotations: bool = False
    upload_artifact: bool = True
