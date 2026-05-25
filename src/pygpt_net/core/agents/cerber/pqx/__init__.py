"""
Cerber PQX — Post-Quantum Hybrid Signing layer.

Provides: generate_keypair, sign_message, verify_message,
          sign_frame, verify_frame, available_schemes,
          register_provider, unregister_provider.

Schemes (stubs): falcon, sphincs, dilithium.
Full implementation required before enabling ALFA KeyVault PQX snapshots.

Test suite: cerber/tests/unit/test_pqxhybrid.py (imports from here).
"""

from .hybrid import (  # noqa: F401
    AlgorithmSpec,
    FrameFormatError,
    InvalidSignatureError,
    PQKeyPair,
    UnsupportedSchemeError,
    available_schemes,
    generate_keypair,
    register_provider,
    sign_frame,
    sign_message,
    unregister_provider,
    verify_frame,
    verify_message,
)
