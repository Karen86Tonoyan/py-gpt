"""
ALFA_BRAIN Chat Extension — alfa_chat stub.

Implement handle() to call your LLM provider (Ollama, Claude, OpenAI).
The Guardian Loop already handles Cerber scan and ALFA-EOS gate before
this extension is invoked — do not re-scan inside handle().
"""

from __future__ import annotations
from typing import Any, Dict, Optional


class ChatExtension:
    """
    Conversational AI extension for ALFABrain.

    Registered as 'chat' in ALFABrain extension registry.
    """

    def __init__(self, provider: Optional[Any] = None) -> None:
        """
        Args:
            provider: Any object with a .complete(prompt) method.
                      If None, returns a placeholder response.
        """
        self._provider = provider

    async def handle(self, text: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        """
        Call the LLM provider and return its response.

        The brain has already applied Cerber + ALFA-EOS gating.
        This method should focus purely on generation.
        """
        if self._provider is None:
            return f"[ChatExtension] Provider not configured. Input was: {text[:100]}"

        if hasattr(self._provider, "complete"):
            return await self._provider.complete(text)

        raise TypeError(f"Provider {type(self._provider)} has no .complete() method")
