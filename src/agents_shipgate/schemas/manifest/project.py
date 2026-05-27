from __future__ import annotations

from pydantic import BaseModel

from agents_shipgate.schemas.manifest._common import STRICT_MODEL_CONFIG


class ProjectConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    name: str
    owner: str | None = None
    repo: str | None = None
