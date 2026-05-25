"""
ALFA_BRAIN Security Extension — thin wrapper around Cerber.

Provides deeper scanning on-demand (beyond the Guardian Loop's automatic
input/output scans) and exposes Cerber statistics to the brain dashboard.
"""

from __future__ import annotations
from typing import Any, Dict, Optional


class SecurityExtension:
    """
    On-demand security analysis extension.

    Registered as 'security' in ALFABrain extension registry.
    """

    def __init__(self) -> None:
        self._guardian: Optional[Any] = None
        try:
            from ...cerber.auto_guardian import AutoGuardian
            self._guardian = AutoGuardian(
                enable_ollama_mixing=False,
                log_file="alfa_brain_security_ext.jsonl",
            )
        except ImportError:
            pass

    def handle(self, text: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        """Return a security analysis report for the given text."""
        if not self._guardian:
            return "[Security] Cerber not available."

        user_id = (metadata or {}).get("user_id", "anonymous")
        result = self._guardian.scan_and_decide(prompt=text, user_id=user_id)
        scan = result["scan_result"]

        lines = [
            f"Action: {result['action'].upper()}",
            f"Severity: {scan.get('max_severity', 'none')}",
            f"Triggers: {scan.get('trigger_count', 0)}",
            f"Categories: {', '.join(scan.get('categories', [])) or 'none'}",
        ]
        if result.get("lockdown"):
            lines.append("LOCKDOWN: True")

        return "\n".join(lines)

    def stats(self) -> Dict[str, Any]:
        if self._guardian:
            return self._guardian.get_statistics()
        return {}
