"""
PQX Hybrid Signing — stub implementation.

All functions raise NotImplementedError until the PQX library is wired in.
The public API matches what test_pqxhybrid.py expects.
"""

from __future__ import annotations

import json
import base64
from dataclasses import dataclass
from typing import Optional


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class InvalidSignatureError(Exception):
    """Raised when signature verification fails or signing input is corrupt."""


class UnsupportedSchemeError(Exception):
    """Raised when an unknown or unregistered scheme is requested."""


class FrameFormatError(Exception):
    """Raised when a signed frame is malformed (missing required fields)."""


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AlgorithmSpec:
    name: str
    secret_size: int
    public_size: int
    signature_size: int
    personalization: bytes


@dataclass(frozen=True)
class PQKeyPair:
    scheme: str
    public_key: bytes
    secret_key: bytes


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

_PROVIDERS: dict[str, object] = {}

_BUILTIN_SCHEMES = {"falcon", "sphincs", "dilithium"}


def available_schemes() -> list[str]:
    return sorted(_BUILTIN_SCHEMES | set(_PROVIDERS.keys()))


def register_provider(scheme: str, provider: object) -> None:
    if scheme in _PROVIDERS or scheme in _BUILTIN_SCHEMES:
        raise ValueError(f"Scheme {scheme!r} is already registered.")
    _PROVIDERS[scheme] = provider


def unregister_provider(scheme: str) -> None:
    _PROVIDERS.pop(scheme, None)


def _get_provider(scheme: str) -> object:
    if scheme in _PROVIDERS:
        return _PROVIDERS[scheme]
    if scheme in _BUILTIN_SCHEMES:
        raise NotImplementedError(
            f"Built-in PQX scheme {scheme!r} is not yet implemented. "
            "Wire in the PQX library (e.g. liboqs-python) to activate it."
        )
    raise UnsupportedSchemeError(f"Unknown scheme: {scheme!r}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_keypair(scheme: str, seed: Optional[bytes] = None) -> PQKeyPair:
    provider = _get_provider(scheme)
    return provider.generate_keypair(seed)  # type: ignore[union-attr]


def sign_message(message: bytes, keypair: PQKeyPair) -> bytes:
    provider = _get_provider(keypair.scheme)
    return provider.sign(message, keypair)  # type: ignore[union-attr]


def verify_message(message: bytes, signature: bytes, scheme: str, public_key: bytes) -> bool:
    provider = _get_provider(scheme)
    return provider.verify(message, signature, public_key)  # type: ignore[union-attr]


def sign_frame(payload: bytes, keypair: PQKeyPair) -> bytes:
    signature = sign_message(payload, keypair)
    frame = {
        "scheme": keypair.scheme,
        "payload": base64.b64encode(payload).decode("ascii"),
        "signature": base64.b64encode(signature).decode("ascii"),
    }
    return json.dumps(frame).encode("utf-8")


def verify_frame(frame: bytes, public_key: bytes) -> tuple[bytes, str]:
    try:
        document = json.loads(frame)
        scheme = document["scheme"]
        payload = base64.b64decode(document["payload"])
        signature = base64.b64decode(document["signature"])
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise FrameFormatError(f"Malformed signed frame: {exc}") from exc

    if not verify_message(payload, signature, scheme, public_key):
        raise InvalidSignatureError("Frame signature verification failed.")

    return payload, scheme
