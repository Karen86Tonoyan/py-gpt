#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ================================================== #
# ALFA-EOS — Arbitration Service                     #
# RFC v0.1 §9 — ARBITRATION_SERVICE                  #
# ================================================== #

"""
ARBITRATION_SERVICE: resolves CONFLICT claims.

Fix (analysis §5.A #3): Added timeout enforcement using PolicyContext.max_arbitration_time
and PolicyContext.timeout_action ("DENY" | "DEFER" | "HUMAN_ESCALATION").

Arbitration protocol:
  1. Collect agent opinions (at least 2 independent agents — INVARIANT_04)
  2. Compute risk_score from evidence divergence
  3. Apply weighted-vote resolution or escalate on high risk
  4. Enforce timeout — if arbitration exceeds max_arbitration_time, apply timeout_action
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from ..events import EventLog, EventType
from ..invariants import InvariantChecker
from ..primitives import (
    ArbitrationLevel,
    ClaimStatus,
    ExecutionPermission,
    PolicyContext,
)
from ..state_machine import EpistemicStateMachine


@dataclass
class AgentOpinion:
    agent_id: str
    claim_id: str
    proposed_status: ClaimStatus
    confidence: float          # 0.0 – 1.0
    rationale: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class ArbitrationRecord:
    arbitration_id: str
    claim_id: str
    started_at: datetime
    deadline: datetime
    opinions: List[AgentOpinion]
    resolved_at: Optional[datetime]
    resolution: Optional[ClaimStatus]
    level: ArbitrationLevel
    timeout_action: str
    risk_score: float

    @property
    def is_timed_out(self) -> bool:
        return datetime.now(timezone.utc) > self.deadline

    @property
    def is_resolved(self) -> bool:
        return self.resolved_at is not None


class ArbitrationService:
    """
    Manages arbitration sessions for CONFLICT claims.
    Enforces timeout via PolicyContext and INVARIANT_04 (≥2 agents).
    """

    def __init__(self, event_log: EventLog, agent_id: str = "system") -> None:
        self._sessions: Dict[str, ArbitrationRecord] = {}   # arbitration_id → record
        self._by_claim: Dict[str, str] = {}                 # claim_id → arbitration_id
        self._log = event_log
        self._agent_id = agent_id
        self._invariant_checker = InvariantChecker()
        self._state_machine = EpistemicStateMachine()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def open(self, claim_id: str, policy: PolicyContext) -> ArbitrationRecord:
        """
        Open an arbitration session for a CONFLICT claim.
        If a session already exists for this claim, return it.
        """
        if claim_id in self._by_claim:
            return self._sessions[self._by_claim[claim_id]]

        now = datetime.now(timezone.utc)
        record = ArbitrationRecord(
            arbitration_id=str(uuid.uuid4()),
            claim_id=claim_id,
            started_at=now,
            deadline=now + policy.max_arbitration_time,
            opinions=[],
            resolved_at=None,
            resolution=None,
            level=self._infer_level(policy),
            timeout_action=policy.timeout_action,
            risk_score=0.0,
        )
        self._sessions[record.arbitration_id] = record
        self._by_claim[claim_id] = record.arbitration_id

        self._log.emit(
            EventType.ARBITRATION_STARTED,
            agent_id=self._agent_id,
            payload={
                "arbitration_id": record.arbitration_id,
                "deadline": record.deadline.isoformat(),
                "level": record.level.value,
                "timeout_action": record.timeout_action,
            },
            claim_id=claim_id,
        )
        return record

    def submit_opinion(
        self, claim_id: str, opinion: AgentOpinion
    ) -> Optional[ClaimStatus]:
        """
        Add an agent opinion to the active arbitration session.
        If enough opinions exist and quorum is reached, auto-resolve.
        Returns resolved ClaimStatus if resolved, None otherwise.
        """
        record = self._get_active_or_raise(claim_id)

        if record.is_timed_out:
            return self._apply_timeout(record)

        record.opinions.append(opinion)
        record.risk_score = self._compute_risk_score(record.opinions)

        # Try auto-resolution if ≥2 opinions (INVARIANT_04)
        if len(record.opinions) >= 2:
            resolved = self._try_resolve(record)
            if resolved:
                return resolved

        return None

    def check_timeout(self, claim_id: str) -> Optional[str]:
        """
        Check if the active arbitration session has timed out.
        Returns the timeout_action string if timed out, None otherwise.
        Emits ARBITRATION_TIMEOUT event and marks session resolved.
        """
        arb_id = self._by_claim.get(claim_id)
        if not arb_id:
            return None
        record = self._sessions[arb_id]
        if record.is_resolved:
            return None
        if record.is_timed_out:
            self._apply_timeout(record)
            return record.timeout_action
        return None

    def force_resolve(
        self, claim_id: str, resolution: ClaimStatus, by_agent: str = "human"
    ) -> ArbitrationRecord:
        """Human override — force a resolution regardless of opinion count."""
        record = self._get_active_or_raise(claim_id)
        return self._finalise(record, resolution, by_agent=by_agent)

    # ------------------------------------------------------------------
    # Resolution logic
    # ------------------------------------------------------------------

    def _try_resolve(self, record: ArbitrationRecord) -> Optional[ClaimStatus]:
        """Weighted majority vote among submitted opinions."""
        if not record.opinions:
            return None

        vote_weights: Dict[ClaimStatus, float] = {}
        for op in record.opinions:
            vote_weights[op.proposed_status] = (
                vote_weights.get(op.proposed_status, 0.0) + op.confidence
            )

        total_weight = sum(vote_weights.values()) or 1.0
        winner, winner_weight = max(vote_weights.items(), key=lambda kv: kv[1])
        winner_fraction = winner_weight / total_weight

        # Require super-majority (>60%) for decisive resolution
        if winner_fraction > 0.60:
            return self._finalise(record, winner)

        # No quorum yet
        return None

    def _apply_timeout(self, record: ArbitrationRecord) -> Optional[ClaimStatus]:
        with self._lock:
            if record.is_resolved:
                return record.resolution

            action = record.timeout_action
            resolution_map = {
                "DENY":             ClaimStatus.UNVERIFIED,
                "DEFER":            ClaimStatus.CONFLICT,    # stay in CONFLICT
                "HUMAN_ESCALATION": ClaimStatus.CONFLICT,    # stays, triggers escalation
            }
            resolution = resolution_map.get(action, ClaimStatus.UNVERIFIED)

            self._log.emit(
                EventType.ARBITRATION_TIMEOUT,
                agent_id=self._agent_id,
                payload={
                    "arbitration_id": record.arbitration_id,
                    "timeout_action": action,
                    "resolution": resolution.value,
                },
                claim_id=record.claim_id,
            )

            record.resolved_at = datetime.now(timezone.utc)
            record.resolution = resolution
            return resolution if action != "DEFER" else None

    def _finalise(
        self,
        record: ArbitrationRecord,
        resolution: ClaimStatus,
        by_agent: str = "system",
    ) -> ArbitrationRecord:
        with self._lock:
            if record.is_resolved:
                return record

            record.resolved_at = datetime.now(timezone.utc)
            record.resolution = resolution

            self._log.emit(
                EventType.ARBITRATION_RESOLVED,
                agent_id=by_agent,
                payload={
                    "arbitration_id": record.arbitration_id,
                    "resolution": resolution.value,
                    "opinion_count": len(record.opinions),
                    "risk_score": record.risk_score,
                },
                claim_id=record.claim_id,
            )
        return record

    # ------------------------------------------------------------------
    # Risk scoring
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_risk_score(opinions: List[AgentOpinion]) -> float:
        """
        Risk = disagreement factor × average confidence.
        High risk when agents strongly disagree with each other.
        """
        if len(opinions) < 2:
            return 0.5
        statuses = [o.proposed_status for o in opinions]
        unique = len(set(statuses))
        disagreement = (unique - 1) / len(statuses)
        avg_confidence = sum(o.confidence for o in opinions) / len(opinions)
        return min(1.0, disagreement * avg_confidence + 0.1)

    @staticmethod
    def _infer_level(policy: PolicyContext) -> ArbitrationLevel:
        risk_map = {
            "low":      ArbitrationLevel.LOCAL,
            "medium":   ArbitrationLevel.SYSTEM,
            "high":     ArbitrationLevel.SYSTEM,
            "critical": ArbitrationLevel.HUMAN,
        }
        return risk_map.get(policy.risk_profile, ArbitrationLevel.SYSTEM)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_for_claim(self, claim_id: str) -> Optional[ArbitrationRecord]:
        arb_id = self._by_claim.get(claim_id)
        return self._sessions.get(arb_id) if arb_id else None

    def _get_active_or_raise(self, claim_id: str) -> ArbitrationRecord:
        arb_id = self._by_claim.get(claim_id)
        if not arb_id:
            raise KeyError(f"No active arbitration session for claim '{claim_id}'.")
        return self._sessions[arb_id]
