"""
ALFA_BRAIN v4.0 — Central decision and event system.

Roles:
  - Event routing and Guardian Loop
  - Epistemic gating via ALFA-EOS
  - Security enforcement via Cerber (precedence.py)
  - Extension dispatch (chat, coding, security, ...)

ALFA_BRAIN does NOT modify payloads — it delegates filtering and
enforces decisions. See cerber/precedence.py for authority order.

Usage::

    brain = ALFABrain()
    result = await brain.process("user-123", "explain quantum computing")
"""

from .core_manager import ALFABrain, BrainResult  # noqa: F401

__all__ = ["ALFABrain", "BrainResult"]
