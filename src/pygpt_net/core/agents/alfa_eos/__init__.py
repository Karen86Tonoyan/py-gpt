#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ================================================== #
# ALFA-EOS — Epistemic Operating System              #
# RFC v0.1                                           #
# ================================================== #

"""
Main facade for the ALFA-EOS epistemic runtime.

Usage:
    from pygpt_net.core.agents.alfa_eos import AlfaEOS

    eos = AlfaEOS(domain="banking", agent_id="planner")
    claim = eos.assert_claim("The payment gateway is unavailable", claim_type="FACT")
    eos.add_evidence(claim.claim_id, source_type="tool_output", ...)
    status = eos.get_claim(claim.claim_id).status
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List, Optional

from .events import EventLog
from .normalizer import ClaimNormalizer
from .primitives import (
    Claim,
    ClaimStatus,
    ClaimType,
    Evidence,
    ExecutionPermission,
    PolicyContext,
    Source,
    SupportType,
)
from .services import (
    ArbitrationService,
    ClaimService,
    DriftService,
    EvidenceService,
    ReplayService,
    SnapshotService,
)
from .services.arbitration import AgentOpinion, ArbitrationRecord
from .state_machine import EpistemicStateMachine
from .invariants import InvariantChecker, InvariantViolation


SCHEMA_VERSION = "1.0"


class AlfaEOS:
    """
    Top-level epistemic runtime facade.

    Composes all services and exposes a clean API for:
    - asserting and querying claims
    - admitting evidence
    - triggering state transitions
    - arbitration
    - snapshots and drift detection
    - replay verification
    """

    def __init__(
        self,
        domain: str = "business",
        agent_id: str = "system",
        schema_version: str = SCHEMA_VERSION,
    ) -> None:
        self.domain = domain
        self.agent_id = agent_id
        self.schema_version = schema_version

        self.policy = PolicyContext.for_domain(domain)
        self.event_log = EventLog(schema_version=schema_version)

        self.claims       = ClaimService(self.event_log, agent_id, schema_version)
        self.evidence     = EvidenceService(self.event_log, agent_id)
        self.arbitration  = ArbitrationService(self.event_log, agent_id)
        self.snapshots    = SnapshotService(self.event_log, schema_version)
        self.drift        = DriftService(self.event_log)
        self.replay       = ReplayService()

        self._state_machine     = EpistemicStateMachine()
        self._invariant_checker = InvariantChecker()
        self._normalizer        = ClaimNormalizer()

    # ------------------------------------------------------------------
    # Claims
    # ------------------------------------------------------------------

    def assert_claim(
        self,
        raw_text: str,
        claim_type: str = "FACT",
        content: str = "",
        depends_on: Optional[List[str]] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Claim:
        """Assert a new claim (idempotent — duplicate canonical forms return the existing claim)."""
        return self.claims.create(
            raw_text=raw_text,
            claim_type=ClaimType(claim_type),
            content=content,
            depends_on=depends_on,
            meta=meta,
        )

    def get_claim(self, claim_id: str) -> Optional[Claim]:
        return self.claims.get(claim_id)

    def transition_claim(
        self,
        claim_id: str,
        target_status: str,
        new_evidence: Optional[List[Evidence]] = None,
        context: str = "",
    ) -> Claim:
        return self.claims.transition(
            claim_id=claim_id,
            target_status=ClaimStatus(target_status),
            new_evidence=new_evidence,
            policy=self.policy,
            context=context,
        )

    # ------------------------------------------------------------------
    # Evidence
    # ------------------------------------------------------------------

    def add_evidence(
        self,
        claim_id: str,
        source_type: str,
        source_location: str,
        support_type: str = "SUPPORTS",
        weight: float = 0.8,
        trust_profile: float = 0.8,
    ) -> Optional[Evidence]:
        """
        Create a Source + Evidence, run them through EVIDENCE_GATE, and if
        admitted, attach to the claim and update its status.

        Returns the Evidence object if admitted, None if rejected.
        """
        source = Source.new(
            type=source_type,
            location=source_location,
            trust_profile=trust_profile,
            domain_class=self.domain,
        )
        ev = Evidence.new(
            claim_id=claim_id,
            source_id=source.source_id,
            support_type=SupportType(support_type),
            weight=weight,
        )

        gate_result, new_confidence = self.evidence.admit(ev, source, self.policy)
        if not gate_result.passed:
            return None

        self.claims.attach_evidence(claim_id, ev, new_confidence or 0.0)

        # Auto-transition based on new confidence
        inferred = self.evidence.infer_target_status(claim_id, self.policy)
        claim = self.claims.get(claim_id)
        if claim and self._state_machine.is_transition_allowed(claim.status, inferred):
            self.claims.transition(claim_id, inferred, new_evidence=[ev])

        return ev

    # ------------------------------------------------------------------
    # Execution gate
    # ------------------------------------------------------------------

    def request_execution(self, claim_id: str) -> ExecutionPermission:
        """
        Request execution permission for a claim.
        Checks INVARIANT_01 and INVARIANT_08.
        Returns ExecutionPermission (granted or denied with reason).
        """
        claim = self.claims.get(claim_id)
        if not claim:
            return ExecutionPermission.deny(claim_id, "Claim not found.")

        if not self._state_machine.can_execute(claim.status):
            return ExecutionPermission.deny(
                claim_id,
                f"INVARIANT_01: execution blocked for status '{claim.status.value}'.",
                risk_score=1.0,
            )

        risk_score = 1.0 - claim.confidence
        perm = ExecutionPermission.grant(
            claim_id=claim_id,
            reason=f"Status={claim.status.value}, confidence={claim.confidence:.2f}",
            risk_score=risk_score,
            provenance_chain={
                "evidence_ids": claim.evidence_ref,
                "policy_domain": self.domain,
            },
        )

        # INVARIANT_08 check
        violation = self._invariant_checker.invariant_08_execution_requires_policy_permission(
            perm, self.policy
        )
        if violation:
            return ExecutionPermission.deny(
                claim_id,
                str(violation),
                risk_score=risk_score,
            )

        return perm

    # ------------------------------------------------------------------
    # Arbitration
    # ------------------------------------------------------------------

    def start_arbitration(self, claim_id: str) -> ArbitrationRecord:
        return self.arbitration.open(claim_id, self.policy)

    def submit_opinion(self, claim_id: str, opinion: AgentOpinion) -> Optional[ClaimStatus]:
        return self.arbitration.submit_opinion(claim_id, opinion)

    def check_arbitration_timeout(self, claim_id: str) -> Optional[str]:
        return self.arbitration.check_timeout(claim_id)

    # ------------------------------------------------------------------
    # Snapshot / drift
    # ------------------------------------------------------------------

    def snapshot(self, agents: Optional[List[Dict]] = None) -> Any:
        baseline = self.snapshots.latest()
        truth_anchor = {c.claim_id: c for c in self.claims.all()}
        snap = self.snapshots.take(
            truth_anchor=truth_anchor,
            agents=agents or [],
            policy_context=self.policy,
        )
        if baseline:
            self.drift.detect(truth_anchor, baseline)
        return snap

    # ------------------------------------------------------------------
    # Replay / audit
    # ------------------------------------------------------------------

    def verify_replay(self) -> Optional[str]:
        """Return None if INVARIANT_07 holds, else error string."""
        current_store = {c.claim_id: c for c in self.claims.all()}
        return self.replay.verify_determinism(
            self.event_log, current_store, self.schema_version
        )

    def export_event_log(self) -> str:
        return self.event_log.to_jsonl()

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def status_summary(self) -> Dict[str, int]:
        """Count of claims per status."""
        summary: Dict[str, int] = {}
        for claim in self.claims.all():
            key = claim.status.value
            summary[key] = summary.get(key, 0) + 1
        return summary
