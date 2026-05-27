from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from agents_shipgate.schemas.manifest._common import STRICT_MODEL_CONFIG


class EnvironmentConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    target: Literal["local", "staging", "production_like", "production"]
    promotion_from: str | None = None
    promotion_to: str | None = None
