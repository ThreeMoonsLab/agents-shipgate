from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from agents_shipgate.schemas.human_authorization import (
    HUMAN_AUTHORIZATION_SIGNATURE_DOMAIN,
    HumanAuthorizationStatementV1,
    ed25519_key_id,
)
from agents_shipgate.schemas.verification_identity import canonical_json

ROOT = Path(__file__).resolve().parent.parent
VECTOR = ROOT / "docs" / "human-authorization-signature-v1.json"


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def test_public_human_authorization_signature_vector_is_canonical() -> None:
    vector = json.loads(VECTOR.read_text(encoding="utf-8"))
    statement = HumanAuthorizationStatementV1.model_validate(vector["statement"])
    canonical = canonical_json(statement.model_dump(mode="json"))
    payload = HUMAN_AUTHORIZATION_SIGNATURE_DOMAIN + canonical
    public_key = _decode(vector["public_key"])

    assert vector["signature_domain_hex"] == HUMAN_AUTHORIZATION_SIGNATURE_DOMAIN.hex()
    assert vector["canonical_statement_json"].encode("utf-8") == canonical
    assert vector["signature_payload_sha256"] == (
        "sha256:" + hashlib.sha256(payload).hexdigest()
    )
    assert vector["key_id"] == ed25519_key_id(public_key)
    Ed25519PublicKey.from_public_bytes(public_key).verify(
        _decode(vector["signature"]),
        payload,
    )
