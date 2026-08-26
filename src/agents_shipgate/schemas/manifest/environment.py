from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from agents_shipgate.schemas.manifest._common import STRICT_MODEL_CONFIG


class EnvironmentConfig(BaseModel):
    """Where the agent this manifest describes actually runs.

    ``template`` is not a deployment (#410 §G). It is the honest answer for a
    sample, an example, or a scaffold that ships to be copied: there is no
    environment, so there are no credentials, and asking every action which
    credential it runs with asks a question the repository cannot answer in
    principle. Both repositories the adoption walks used are of exactly this
    kind, and ``authority: {mode: none}`` written twelve times is the same
    claim spelled at cost.

    Declaring it answers the authority dimension once, for every action that
    does not say otherwise, and never quietly: every action it answers for is a
    review concern, so a ``template`` repository can reach ``review_required``
    and never ``passed``. That is the property that keeps it from being the
    cheap way out — an adopter must state their real authority before a green
    gate is available at all.
    """

    model_config = STRICT_MODEL_CONFIG

    target: Literal["local", "staging", "production_like", "production", "template"]
    promotion_from: str | None = None
    promotion_to: str | None = None
