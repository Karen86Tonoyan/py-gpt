"""
ALFA_BRAIN Coding Extension — secure code execution stub.

Implement handle() to route to your code executor.
The Guardian Loop applies Cerber before this extension is reached —
malicious code injection attempts are blocked upstream.
"""

from __future__ import annotations
from typing import Any, Dict, Optional


class CodingExtension:
    """
    Code execution extension for ALFABrain.

    Registered as 'coding' in ALFABrain extension registry.
    """

    def __init__(self, executor: Optional[Any] = None) -> None:
        self._executor = executor

    async def handle(self, text: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        if self._executor is None:
            return "[CodingExtension] Executor not configured."

        if hasattr(self._executor, "run"):
            return await self._executor.run(text)

        raise TypeError(f"Executor {type(self._executor)} has no .run() method")
