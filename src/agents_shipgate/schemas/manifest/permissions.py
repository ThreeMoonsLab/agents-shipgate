from __future__ import annotations

from pydantic import BaseModel, Field

from agents_shipgate.schemas.manifest._common import STRICT_MODEL_CONFIG


class PermissionsConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    scopes: list[str] = Field(default_factory=list)
    credential_mode: str | None = None
    notes: str | None = None
