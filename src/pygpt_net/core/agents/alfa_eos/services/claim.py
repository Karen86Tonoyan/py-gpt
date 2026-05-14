#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ================================================== #
# ALFA-EOS — Claim Service                           #
# RFC v0.1 §4 — CLAIM_SERVICE                        #
# ================================================== #

"""
CLAIM_SERVICE: authoritative store for Claim objects.
All mutations must go through this service; raw dict manipulation is forbidden.
Every mutation emits an event to the EventLog before applying the change.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from ..events import EventLog, EventType
from ..invariants import InvariantChecker
from ..normalizer import ClaimNormalizer
from ..primitives import Claim, ClaimStatus, ClaimType, Evidence, PolicyContext
from ..state_machine import EpistemicStateMachine, StateMachineError


class ClaimService:
    """
    Manages the lifecycle of Claim objects:
    - create (with deterministic claim_id from canonical_form)
    - transition state (validated by EpistemicStateMachine + InvariantChecker)
    - query by id, status, type
    - resolve dependency cascades
    """

    def __init__(
        self,
        event_log: EventLog,
        agent_id: str = "system",
        schema_version: str = "1.0",
    ) -> None:
        self._store: Dict[str, Claim] = {}
        self._log = event_log
        self._agent_id = agent_id
        self._schema_version = schema_version
        self._normalizer = ClaimNormalizer()
        self._state_machine = EpistemicStateMachine()
        self._invariant_checker = InvariantChecker()

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def create(
        self,
        raw_text: str,
        claim_type: ClaimType,
        content: str = "",
        depends_on: Optional[List[str]] = None,
        meta: Optional[Dict] = None,
    ) -> Claim:
        """
        Create a new Claim.  claim_id is derived deterministically from
        canonical_form (INVARIANT_05).  Duplicate claims (same canonical_form)
        are returned from the store without re-creation.
        """
        canonical_form, claim_id = self._normalizer.normalize(raw_text)

        # Idempotent: return existing claim if canonical form already known
        if claim_id in self._store:
            return self._store[claim_id]

        claim = Claim(
            claim_id=claim_id,
            canonical_form=canonical_form,
            raw_variants=[raw_text],
            type=claim_type,
            status=ClaimStatus.UNVERIFIED,
            content=content or raw_text,
            evidence_ref=[],
            confidence=0.0,
            verified_at=None,
            valid_until=None,
            revalidation_policy=None,
            depends_on=depends_on or [],
            meta=meta or {},
        )

        self._log.emit(
            EventType.CLAIM_CREATED,
            agent_id=self._agent_id,
            payload={
                "claim_id": claim_id,
                "canonical_form": canonical_form,
                "type": claim_type.value,
                "schema_version": self._schema_version,
            },
            claim_id=claim_id,
        )

        self._store[claim_id] = claim
        return claim

    # ------------------------------------------------------------------
    # Transition
    # ------------------------------------------------------------------

    def transition(
        self,
        claim_id: str,
        target_status: ClaimStatus,
        new_evidence: Optional[List[Evidence]] = None,
        policy: Optional[PolicyContext] = None,
        context: str = "",
    ) -> Claim:
        """
        Transition a claim to a new status.
        Validates state machine rules and invariants before applying.
        """
        claim = self._get_or_raise(claim_id)

        # State machine check (raises StateMachineError on illegal transition)
        self._state_machine.validate_transition(
            claim.status, target_status, context=context
        )

        # Invariant checks (INVARIANT_01, _02)
        violations = self._invariant_checker.check_all(
            claim=claim,
            target_status=target_status,
            new_evidence=new_evidence,
        )
        if violations:
            # CLASS A violations are hard failures
            class_a = [v for v in violations if v.failure_class == "A"]
            if class_a:
                raise StateMachineError(
                    f"Transition blocked by invariant violation: {class_a[0]}"
                )

        self._log.emit(
            EventType.STATE_TRANSITIONED,
            agent_id=self._agent_id,
            payload={
                "from_status": claim.status.value,
                "to_status": target_status.value,
                "context": context,
                "evidence_count": len(new_evidence) if new_evidence else 0,
            },
            claim_id=claim_id,
        )

        # Apply mutation
        claim.status = target_status
        if target_status == ClaimStatus.VERIFIED:
            claim.verified_at = datetime.now(timezone.utc)

        return claim

    # ------------------------------------------------------------------
    # Evidence attachment
    # ------------------------------------------------------------------

    def attach_evidence(
        self, claim_id: str, evidence: Evidence, new_confidence: float
    ) -> Claim:
        claim = self._get_or_raise(claim_id)
        if evidence.evidence_id not in claim.evidence_ref:
            claim.evidence_ref.append(evidence.evidence_id)
        claim.confidence = max(0.0, min(1.0, new_confidence))

        self._log.emit(
            EventType.EVIDENCE_ADDED,
            agent_id=self._agent_id,
            payload={
                "evidence_id": evidence.evidence_id,
                "new_confidence": new_confidence,
                "support_type": evidence.support_type.value,
            },
            claim_id=claim_id,
        )
        return claim

    # ------------------------------------------------------------------
    # Dependency cascade
    # ------------------------------------------------------------------

    def cascade_conflict(self, source_claim_id: str) -> List[str]:
        """
        When a claim transitions to CONFLICT, mark all dependents UNVERIFIED.
        Returns list of affected claim_ids.
        """
        affected = []
        for cid, claim in self._store.items():
            if source_claim_id in claim.depends_on and claim.status == ClaimStatus.VERIFIED:
                self.transition(
                    cid,
                    ClaimStatus.UNVERIFIED,
                    context=f"cascade from conflict on {source_claim_id}",
                )
                affected.append(cid)
        return affected

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get(self, claim_id: str) -> Optional[Claim]:
        return self._store.get(claim_id)

    def get_by_status(self, status: ClaimStatus) -> List[Claim]:
        return [c for c in self._store.values() if c.status == status]

    def all(self) -> List[Claim]:
        return list(self._store.values())

    def _get_or_raise(self, claim_id: str) -> Claim:
        claim = self._store.get(claim_id)
        if claim is None:
            raise KeyError(f"Claim '{claim_id}' not found in store.")
        return claim
