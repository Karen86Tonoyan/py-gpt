#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ================================================== #
# ALFA-EOS — Evidence Service                        #
# RFC v0.1 §8 — EVIDENCE_SERVICE                     #
# ================================================== #

"""
EVIDENCE_SERVICE: manages the evidence store, runs the EVIDENCE_GATE pipeline,
and computes the cross-consistency score for the full evidence set of a claim.

Confidence formula (RFC §8.4):
    confidence = Σ(w_i × f_i × c_i) / Σ(w_i)   for SUPPORTS evidence
               − Σ(w_j × f_j × c_j) / Σ(w_j)   for REFUTES evidence
    clamped to [0.0, 1.0]
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from ..events import EventLog, EventType
from ..gate import EvidenceGate, GateResult, DOMAIN_CORROBORATION_MINIMUMS
from ..primitives import ClaimStatus, Evidence, PolicyContext, Source, SupportType


class EvidenceService:
    """
    Accepts evidence through the EVIDENCE_GATE pipeline and maintains
    the evidence store.  Computes cross-consistency and confidence scores.
    """

    def __init__(self, event_log: EventLog, agent_id: str = "system") -> None:
        self._store: Dict[str, Evidence] = {}       # evidence_id → Evidence
        self._by_claim: Dict[str, List[str]] = {}   # claim_id → [evidence_id]
        self._sources: Dict[str, Source] = {}       # source_id → Source
        self._log = event_log
        self._agent_id = agent_id
        self._gate = EvidenceGate()

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------

    def add_source(self, source: Source) -> None:
        self._sources[source.source_id] = source

    def admit(
        self,
        evidence: Evidence,
        source: Source,
        policy: PolicyContext,
    ) -> Tuple[GateResult, Optional[float]]:
        """
        Run evidence through EVIDENCE_GATE.  If it passes, store it,
        recompute cross-consistency, and return updated confidence.

        Returns: (GateResult, new_confidence | None)
        """
        self.add_source(source)
        existing = self._evidence_for_claim(evidence.claim_id)

        gate_result = self._gate.admit(evidence, source, policy, existing)
        if not gate_result.passed:
            self._log.emit(
                EventType.EVIDENCE_INVALIDATED,
                agent_id=self._agent_id,
                payload={
                    "evidence_id": evidence.evidence_id,
                    "gate_step": gate_result.step_failed,
                    "reason": gate_result.reason,
                },
                claim_id=evidence.claim_id,
            )
            return gate_result, None

        # Recompute consistency for the full set including new evidence
        evidence = self._recompute_consistency(evidence, existing)
        self._store[evidence.evidence_id] = evidence
        self._by_claim.setdefault(evidence.claim_id, []).append(evidence.evidence_id)

        new_confidence = self.compute_confidence(evidence.claim_id)

        self._log.emit(
            EventType.EVIDENCE_ADDED,
            agent_id=self._agent_id,
            payload={
                "evidence_id": evidence.evidence_id,
                "support_type": evidence.support_type.value,
                "weight": evidence.weight,
                "freshness": evidence.freshness,
                "consistency": evidence.consistency,
                "new_confidence": new_confidence,
            },
            claim_id=evidence.claim_id,
        )

        return gate_result, new_confidence

    # ------------------------------------------------------------------
    # Consistency scoring
    # ------------------------------------------------------------------

    def _recompute_consistency(
        self, candidate: Evidence, existing: List[Evidence]
    ) -> Evidence:
        """
        Consistency = agreement fraction among all evidence with same support_type.
        For a homogeneous set c=1.0; for mixed support c drops proportionally.
        """
        if not existing:
            candidate.consistency = 1.0
            return candidate

        same_type = [e for e in existing if e.support_type == candidate.support_type]
        opposite   = [e for e in existing if e.support_type != candidate.support_type
                      and e.support_type != SupportType.NEUTRAL]

        total = len(same_type) + len(opposite) + 1   # +1 for candidate
        agree  = len(same_type) + 1
        candidate.consistency = agree / total

        # Also retroactively update consistency of existing same-type evidence
        for ev in existing:
            if ev.support_type == candidate.support_type:
                ev.consistency = agree / total

        return candidate

    # ------------------------------------------------------------------
    # Confidence computation
    # ------------------------------------------------------------------

    def compute_confidence(self, claim_id: str) -> float:
        """
        RFC §8.4 formula:
            confidence = Σ(w_i × f_i × c_i|SUPPORTS) / Σ(w_i|SUPPORTS)
                       − Σ(w_j × f_j × c_j|REFUTES) / Σ(w_j|REFUTES)
        Clamped to [0.0, 1.0].
        """
        evs = self._evidence_for_claim(claim_id)
        if not evs:
            return 0.0

        supports = [e for e in evs if e.support_type == SupportType.SUPPORTS]
        refutes  = [e for e in evs if e.support_type == SupportType.REFUTES]

        def _weighted_avg(evidence_list: List[Evidence]) -> float:
            if not evidence_list:
                return 0.0
            numerator   = sum(e.weight * e.freshness * e.consistency for e in evidence_list)
            denominator = sum(e.weight for e in evidence_list) or 1.0
            return numerator / denominator

        confidence = _weighted_avg(supports) - _weighted_avg(refutes)
        return max(0.0, min(1.0, confidence))

    def infer_target_status(
        self, claim_id: str, policy: PolicyContext
    ) -> ClaimStatus:
        """
        Infer what ClaimStatus the confidence score warrants given policy thresholds.
        VERIFIED requires both confidence threshold AND minimum corroboration sources.
        """
        confidence = self.compute_confidence(claim_id)
        evs = self._evidence_for_claim(claim_id)
        has_refuting = any(e.support_type == SupportType.REFUTES for e in evs)
        has_supporting = any(e.support_type == SupportType.SUPPORTS for e in evs)

        if has_refuting and has_supporting:
            return ClaimStatus.CONFLICT

        if confidence >= policy.verify_threshold:
            # Also enforce corroboration minimum before granting VERIFIED
            supporting = [e for e in evs if e.support_type == SupportType.SUPPORTS]
            unique_sources = len({e.source_id for e in supporting})
            min_corroboration = DOMAIN_CORROBORATION_MINIMUMS.get(policy.domain, 1)
            if unique_sources >= min_corroboration:
                return ClaimStatus.VERIFIED
            return ClaimStatus.PARTIAL

        if confidence >= policy.partial_threshold:
            return ClaimStatus.PARTIAL
        if evs:
            return ClaimStatus.EVIDENCE_REQUIRED
        return ClaimStatus.UNVERIFIED

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get(self, evidence_id: str) -> Optional[Evidence]:
        return self._store.get(evidence_id)

    def _evidence_for_claim(self, claim_id: str) -> List[Evidence]:
        ids = self._by_claim.get(claim_id, [])
        return [self._store[eid] for eid in ids if eid in self._store]

    def evidence_for_claim(self, claim_id: str) -> List[Evidence]:
        return self._evidence_for_claim(claim_id)
