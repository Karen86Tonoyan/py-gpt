"""
ALFA_BRAIN CoreManager — central dispatcher with Guardian Loop.

Pipeline per request:
  1. Cerber scan (RULE-064: crisis cut runs first, then full pipeline)
  2. If BLOCK → return lockdown response, stop.
  3. ALFA-EOS: assert_claim + request_execution epistemic gate
  4. If execution denied → return INSUFFICIENT_EVIDENCE response, stop.
  5. Route to extension (chat / coding / security / ...)
  6. On response: Cerber output scan (optional, WARN level)
  7. Emit ALFA-EOS evidence based on extension result
  8. Return BrainResult

GuardianAdapter modes (default: SHADOW):
  OFF     — no Cerber enforcement
  SHADOW  — evaluate but do not block (calibration)
  PARTIAL — block HIGH/CRITICAL only
  FULL    — full matrix
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from ..alfa_eos import AlfaEOS
from ..alfa_eos.primitives import ClaimStatus, PolicyContext, Source, SourceType


class GuardianMode(str, Enum):
    OFF = "OFF"
    SHADOW = "SHADOW"
    PARTIAL = "PARTIAL"
    FULL = "FULL"


@dataclass
class BrainResult:
    user_id: str
    input_text: str
    output_text: str
    was_blocked: bool = False
    block_reason: Optional[str] = None
    guardian_mode: str = "SHADOW"
    latency_ms: float = 0.0
    claim_id: Optional[str] = None
    epistemic_status: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ALFABrain:
    """
    Central orchestrator for the ALFA system.

    Args:
        guardian_mode: Enforcement level (default SHADOW for safe rollout).
        cerber_enabled: Whether to activate Cerber pipeline.
        eos_enabled: Whether to activate ALFA-EOS epistemic gate.
    """

    def __init__(
        self,
        guardian_mode: GuardianMode = GuardianMode.SHADOW,
        cerber_enabled: bool = True,
        eos_enabled: bool = True,
    ) -> None:
        self.guardian_mode = guardian_mode
        self.cerber_enabled = cerber_enabled
        self.eos_enabled = eos_enabled

        self._eos = AlfaEOS() if eos_enabled else None
        self._extensions: Dict[str, Any] = {}
        self._lock = asyncio.Lock()

        # Lazy Cerber import — optional dependency
        self._cerber: Optional[Any] = None
        if cerber_enabled:
            self._init_cerber()

    def _init_cerber(self) -> None:
        try:
            from ..cerber.auto_guardian import AutoGuardian
            self._cerber = AutoGuardian(
                enable_ollama_mixing=False,
                log_file="alfa_brain_cerber.jsonl",
            )
        except ImportError:
            self._cerber = None

    # ------------------------------------------------------------------
    # Extension registry
    # ------------------------------------------------------------------

    def register_extension(self, name: str, extension: Any) -> None:
        """Register a named extension (chat, coding, security, ...)."""
        self._extensions[name] = extension

    def get_extension(self, name: str) -> Optional[Any]:
        return self._extensions.get(name)

    # ------------------------------------------------------------------
    # Main pipeline
    # ------------------------------------------------------------------

    async def process(
        self,
        user_id: str,
        text: str,
        extension: str = "chat",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> BrainResult:
        """
        Run the full Guardian Loop for a user request.

        Args:
            user_id: Caller identifier (hashed internally for logs).
            text: Raw user input.
            extension: Target extension name.
            metadata: Optional context (session_id, domain, ...).

        Returns:
            BrainResult with output_text and full decision trace.
        """
        start = time.perf_counter()
        session_id = (metadata or {}).get("session_id", user_id)

        # --- Step 1: Cerber input scan ---
        cerber_result = self._cerber_scan(text, user_id=user_id)
        if cerber_result["blocked"]:
            return BrainResult(
                user_id=user_id,
                input_text=text,
                output_text=cerber_result["response"],
                was_blocked=True,
                block_reason=cerber_result["reason"],
                guardian_mode=self.guardian_mode.value,
                latency_ms=_ms(start),
            )

        # --- Step 2: ALFA-EOS epistemic gate ---
        claim_id: Optional[str] = None
        if self._eos:
            claim_id, eos_denied = self._eos_gate(text, user_id, metadata or {})
            if eos_denied:
                return BrainResult(
                    user_id=user_id,
                    input_text=text,
                    output_text="[ALFA-EOS] Insufficient epistemic basis for execution.",
                    was_blocked=True,
                    block_reason="INSUFFICIENT_EVIDENCE",
                    guardian_mode=self.guardian_mode.value,
                    latency_ms=_ms(start),
                    claim_id=claim_id,
                    epistemic_status="EVIDENCE_REQUIRED",
                )

        # --- Step 3: Route to extension ---
        output = await self._dispatch(extension, text, metadata)

        # --- Step 4: Cerber output scan (WARN only, SHADOW-safe) ---
        self._cerber_output_scan(output, user_id=user_id)

        # --- Step 5: Emit ALFA-EOS evidence from result ---
        if self._eos and claim_id:
            self._eos_emit_evidence(claim_id, output, user_id)

        return BrainResult(
            user_id=user_id,
            input_text=text,
            output_text=output,
            was_blocked=False,
            guardian_mode=self.guardian_mode.value,
            latency_ms=_ms(start),
            claim_id=claim_id,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _cerber_scan(self, text: str, user_id: str) -> Dict[str, Any]:
        """Run Cerber input scan. Returns {blocked, response, reason}."""
        if not self._cerber or self.guardian_mode == GuardianMode.OFF:
            return {"blocked": False, "response": None, "reason": None}

        result = self._cerber.scan_and_decide(prompt=text, user_id=user_id)
        action = result.get("action", "allow")

        if action == "block" and self.guardian_mode == GuardianMode.FULL:
            return {
                "blocked": True,
                "response": result.get("response", "[CERBER] Request blocked."),
                "reason": f"CERBER:{result['scan_result'].get('max_severity', 'UNKNOWN')}",
            }

        if action == "block" and self.guardian_mode == GuardianMode.PARTIAL:
            severity = result["scan_result"].get("max_severity", "none")
            if severity in ("critical", "high"):
                return {
                    "blocked": True,
                    "response": result.get("response", "[CERBER] Request blocked."),
                    "reason": f"CERBER:{severity}",
                }

        # SHADOW / PARTIAL low-medium / OFF — log only, do not block
        return {"blocked": False, "response": None, "reason": None}

    def _cerber_output_scan(self, output: str, user_id: str) -> None:
        """Scan LLM output (advisory only — does not block in SHADOW)."""
        if not self._cerber or self.guardian_mode in (GuardianMode.OFF, GuardianMode.SHADOW):
            return
        self._cerber.scan_and_decide(prompt=output, user_id=f"{user_id}:output")

    def _eos_gate(
        self, text: str, user_id: str, metadata: Dict[str, Any]
    ) -> tuple[str, bool]:
        """
        Assert claim and request execution via ALFA-EOS.

        Returns (claim_id, denied).
        Denied only when confidence is None (no admitted evidence).
        Already-VERIFIED claims are never denied here.
        """
        domain = metadata.get("domain", "general")
        policy = PolicyContext(
            domain=domain,
            min_confidence=0.6,
            min_corroborating_sources=1,
            timeout_seconds=30,
            timeout_action="DENY",
            max_risk_score=0.8,
        )
        claim_id = self._eos.assert_claim(text, domain=domain)
        perm = self._eos.request_execution(claim_id, policy=policy, agent_id=user_id)
        return claim_id, not perm.granted

    def _eos_emit_evidence(self, claim_id: str, output: str, agent_id: str) -> None:
        """Feed extension output back into ALFA-EOS as supporting evidence."""
        try:
            src = Source(
                agent_id=agent_id,
                source_type=SourceType.AGENT,
                trust_score=0.7,
                domain="general",
            )
            self._eos.add_evidence(
                claim_id=claim_id,
                fact=output[:500],
                supports=True,
                source=src,
                weight=0.5,
                freshness=1.0,
            )
        except Exception:
            pass  # Evidence admission is advisory; never block on failure

    async def _dispatch(
        self, extension: str, text: str, metadata: Optional[Dict[str, Any]]
    ) -> str:
        """Route to registered extension or return stub response."""
        ext = self._extensions.get(extension)
        if ext is None:
            return f"[ALFA_BRAIN] Extension '{extension}' not registered."

        if asyncio.iscoroutinefunction(getattr(ext, "handle", None)):
            return await ext.handle(text, metadata=metadata)
        if callable(getattr(ext, "handle", None)):
            return ext.handle(text, metadata=metadata)
        return str(ext)

    # ------------------------------------------------------------------
    # Mode control
    # ------------------------------------------------------------------

    def set_mode(self, mode: GuardianMode) -> None:
        self.guardian_mode = mode


def _ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000
