"""
Ed25519 digital signatures -- non-repudiation.

Separate concern from ledger.py's hash chain:
  - hash chain  -> proves a record was not altered AFTER creation
  - signature   -> proves WHO created/approved a record, and that they
                    can't later deny it

For the POC, each synthetic contractor and official gets a keypair
generated at seed time. Private keys are held server-side here purely
so the demo flow stays clickable end-to-end; in production these would
be held client-side (contractor's device / HSM for officials), never
by the platform itself.
"""

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives import serialization
import json


def generate_keypair() -> tuple[str, str]:
    """Returns (private_key_hex, public_key_hex)."""
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    priv_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return priv_bytes.hex(), pub_bytes.hex()


def _canonical_bytes(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_payload(private_key_hex: str, payload: dict) -> str:
    """Signs the canonical JSON encoding of payload. Returns hex signature."""
    private_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
    signature = private_key.sign(_canonical_bytes(payload))
    return signature.hex()


def verify_signature(public_key_hex: str, payload: dict, signature_hex: str) -> bool:
    """Returns True iff signature_hex is a valid Ed25519 signature of payload
    under public_key_hex. Never raises on a bad signature -- returns False.
    """
    try:
        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        public_key.verify(bytes.fromhex(signature_hex), _canonical_bytes(payload))
        return True
    except Exception:
        return False


if __name__ == "__main__":
    priv, pub = generate_keypair()
    claim = {"contract_id": "C-1001", "milestone_id": "M-3", "claimed_pct_complete": 60}

    signature = sign_payload(priv, claim)
    print("signature valid:", verify_signature(pub, claim, signature))

    tampered_claim = {**claim, "claimed_pct_complete": 95}
    print("tampered payload valid:", verify_signature(pub, tampered_claim, signature))

    _, wrong_pub = generate_keypair()
    print("wrong public key valid:", verify_signature(wrong_pub, claim, signature))
