#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ================================================== #
# ALFA-EOS — Epistemic State Machine                 #
# RFC v0.1 §4 — State Machine                        #
# ================================================== #

"""
Deterministic state machine for claim epistemic status.
All transitions are validated against VALID_TRANSITIONS.
State mutation without a corresponding event is a CLASS A failure (INVARIANT violation).
"""

from typing import Dict, List, Optional, Set

from .primitives import ClaimStatus


# ---------------------------------------------------------------------------
# Transition table
# ---------------------------------------------------------------------------

VALID_TRANSITIONS: Dict[ClaimStatus, List[ClaimStatus]] = {
    ClaimStatus.UNVERIFIED: [
        ClaimStatus.EVIDENCE_REQUIRED,
        ClaimStatus.PARTIAL,
        ClaimStatus.VERIFIED,
    ],
    ClaimStatus.EVIDENCE_REQUIRED: [
        ClaimStatus.UNVERIFIED,
        ClaimStatus.PARTIAL,
        ClaimStatus.VERIFIED,
    ],
    ClaimStatus.PARTIAL: [
        ClaimStatus.VERIFIED,
        ClaimStatus.CONFLICT,
        ClaimStatus.EVIDENCE_REQUIRED,
        ClaimStatus.UNVERIFIED,
    ],
    ClaimStatus.VERIFIED: [
        ClaimStatus.CONFLICT,
        ClaimStatus.REFUTED,
        # VERIFIED cannot degrade without new evidence — INVARIANT_02
    ],
    ClaimStatus.CONFLICT: [
        ClaimStatus.VERIFIED,
        ClaimStatus.UNVERIFIED,
        ClaimStatus.REFUTED,
    ],
    ClaimStatus.REFUTED: [
        ClaimStatus.CONFLICT,
        # REFUTED → VERIFIED requires arbitration first (gated by ArbitrationService)
        ClaimStatus.VERIFIED,
    ],
}

# Statuses that BLOCK execution (INVARIANT_01)
EXECUTION_BLOCKED_STATUSES: Set[ClaimStatus] = {
    ClaimStatus.UNVERIFIED,
    ClaimStatus.EVIDENCE_REQUIRED,
    ClaimStatus.CONFLICT,
    ClaimStatus.REFUTED,
}

# Statuses that permit execution
EXECUTION_PERMITTED_STATUSES: Set[ClaimStatus] = {
    ClaimStatus.VERIFIED,
    ClaimStatus.PARTIAL,  # allowed with risk annotation
}

# Statuses that require human review before execution in high-risk domains
HIGH_RISK_REVIEW_STATUSES: Set[ClaimStatus] = {
    ClaimStatus.PARTIAL,
}


class StateMachineError(Exception):
    """Raised when an illegal state transition is attempted."""


class EpistemicStateMachine:
    """
    Validates and enforces epistemic state transitions.
    Does NOT mutate Claim objects — mutation is the responsibility of
    CLAIM_SERVICE after verifying the transition is legal and emitting
    a STATE_TRANSITIONED event.
    """

    def validate_transition(
        self,
        current: ClaimStatus,
        target: ClaimStatus,
        context: Optional[str] = None,
    ) -> bool:
        """
        Returns True if the transition is legal.
        Raises StateMachineError if not.

        :param current: current claim status
        :param target: desired target status
        :param context: optional context string for error messages
        :raises StateMachineError: if transition is not in VALID_TRANSITIONS
        :return: True
        """
        allowed = VALID_TRANSITIONS.get(current, [])
        if target not in allowed:
            ctx = f" (context: {context})" if context else ""
            raise StateMachineError(
                f"Illegal epistemic transition: {current.value} → {target.value}{ctx}. "
                f"Allowed: {[s.value for s in allowed]}"
            )
        return True

    def is_transition_allowed(self, current: ClaimStatus, target: ClaimStatus) -> bool:
        """Non-raising version of validate_transition."""
        return target in VALID_TRANSITIONS.get(current, [])

    def can_execute(self, status: ClaimStatus) -> bool:
        """
        INVARIANT_01: UNVERIFIED and HYPOTHESIS must not initiate execution.
        Returns True only for statuses that permit execution.
        """
        return status in EXECUTION_PERMITTED_STATUSES

    def requires_arbitration(self, status: ClaimStatus) -> bool:
        """Returns True if the status requires arbitration before proceeding."""
        return status == ClaimStatus.CONFLICT

    def get_allowed_transitions(self, current: ClaimStatus) -> List[ClaimStatus]:
        return list(VALID_TRANSITIONS.get(current, []))
