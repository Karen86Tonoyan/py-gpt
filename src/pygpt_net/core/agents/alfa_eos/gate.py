#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ================================================== #
# ALFA-EOS — Evidence Gate                           #
# RFC v0.1 §7 — EVIDENCE_GATE Pipeline               #
# ================================================== #

"""
EVIDENCE_GATE: 6-step pipeline that accepts or rejects evidence before it
is admitted to the truth_anchor.

Steps (in order):
  1. FRESHNESS — evidence is within policy freshness_window
  2. TRUST      — source.trust_profile >= domain minimum
  3. FORMAT     — required fields are present and valid
  4. CONSISTENCY — cross-check against existing evidence set
  5. CORROBORATION — independent source count meets minimum
  6. POLICY     — domain-specific override rules
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .primitives import Evidence, PolicyContext, Source, SupportType


DOMAIN_TRUST_MINIMUMS: Dict[str, float] = {
    "marketing": 0.3,
    "business":  0.5,
    "banking":   0.7,
    "medical":   0.8,
}

DOMAIN_CORROBORATION_MINIMUMS: Dict[str, int] = {
    "marketing": 1,
    "business":  2,
    "banking":   3,
    "medical":   4,
}


@dataclass
class GateResult:
    passed: bool
    step_failed: Optional[str]    # None if passed
    reason: str
    evidence_id: str
    adjusted_weight: Optional[float] = None   # set if gate adjusted weight


class EvidenceGate:
    """
    Runs evidence through the 6-step admission pipeline.
    Returns GateResult indicating pass/fail and the reason.
    """

    def admit(
        self,
        evidence: Evidence,
        source: Source,
        policy: PolicyContext,
        existing_evidence: Optional[List[Evidence]] = None,
    ) -> GateResult:
        """
        Run all 6 gate checks in order. Short-circuits on first failure.

        :param evidence: candidate evidence
        :param source: the source associated with this evidence
        :param policy: domain policy context
        :param existing_evidence: current evidence for this claim (for consistency)
        :return: GateResult
        """
        existing = existing_evidence or []

        for step_name, check_fn in [
            ("FRESHNESS",      lambda: self._check_freshness(evidence, source, policy)),
            ("TRUST",          lambda: self._check_trust(source, policy)),
            ("FORMAT",         lambda: self._check_format(evidence, source)),
            ("CONSISTENCY",    lambda: self._check_consistency(evidence, existing)),
            ("CORROBORATION",  lambda: self._check_corroboration(evidence, existing, policy)),
            ("POLICY",         lambda: self._check_policy(evidence, source, policy)),
        ]:
            ok, reason, adjusted_weight = check_fn()
            if not ok:
                return GateResult(
                    passed=False,
                    step_failed=step_name,
                    reason=reason,
                    evidence_id=evidence.evidence_id,
                )

        return GateResult(
            passed=True,
            step_failed=None,
            reason="All gate checks passed.",
            evidence_id=evidence.evidence_id,
        )

    # ------------------------------------------------------------------
    # Step implementations
    # ------------------------------------------------------------------

    def _check_freshness(
        self, evidence: Evidence, source: Source, policy: PolicyContext
    ) -> Tuple[bool, str, Optional[float]]:
        age = datetime.now(timezone.utc) - source.timestamp
        if age > policy.freshness_window:
            return (
                False,
                f"Evidence age {age} exceeds policy freshness_window {policy.freshness_window}.",
                None,
            )
        return True, "ok", None

    def _check_trust(
        self, source: Source, policy: PolicyContext
    ) -> Tuple[bool, str, Optional[float]]:
        minimum = DOMAIN_TRUST_MINIMUMS.get(policy.domain, 0.5)
        if source.trust_profile < minimum:
            return (
                False,
                f"Source trust_profile {source.trust_profile:.2f} below domain minimum {minimum:.2f}.",
                None,
            )
        return True, "ok", None

    def _check_format(
        self, evidence: Evidence, source: Source
    ) -> Tuple[bool, str, Optional[float]]:
        if not evidence.claim_id:
            return False, "Evidence missing claim_id.", None
        if not evidence.source_id:
            return False, "Evidence missing source_id.", None
        if not (0.0 <= evidence.weight <= 1.0):
            return False, f"Evidence weight {evidence.weight} out of range [0,1].", None
        if not (0.0 <= evidence.freshness <= 1.0):
            return False, f"Evidence freshness {evidence.freshness} out of range [0,1].", None
        if not (0.0 <= evidence.consistency <= 1.0):
            return False, f"Evidence consistency {evidence.consistency} out of range [0,1].", None
        return True, "ok", None

    def _check_consistency(
        self, candidate: Evidence, existing: List[Evidence]
    ) -> Tuple[bool, str, Optional[float]]:
        if not existing:
            return True, "ok", None
        # Hard conflict: if majority REFUTES and candidate SUPPORTS — flag
        support_count = sum(1 for e in existing if e.support_type == SupportType.SUPPORTS)
        refute_count  = sum(1 for e in existing if e.support_type == SupportType.REFUTES)
        total = len(existing)
        if total > 0 and candidate.support_type == SupportType.SUPPORTS:
            if refute_count / total > 0.75:
                return (
                    False,
                    f"Consistency gate: candidate SUPPORTS but {refute_count}/{total} "
                    "existing evidence REFUTES. Requires arbitration.",
                    None,
                )
        if total > 0 and candidate.support_type == SupportType.REFUTES:
            if support_count / total > 0.75:
                return (
                    False,
                    f"Consistency gate: candidate REFUTES but {support_count}/{total} "
                    "existing evidence SUPPORTS. Requires arbitration.",
                    None,
                )
        return True, "ok", None

    def _check_corroboration(
        self, evidence: Evidence, existing: List[Evidence], policy: PolicyContext
    ) -> Tuple[bool, str, Optional[float]]:
        # Corroboration gate only blocks evidence that would create a VERIFIED-grade
        # confidence signal while the source pool is still too thin.
        # Individual evidence pieces are always admitted; it's the status transition
        # (via EvidenceService.infer_target_status) that enforces the minimum.
        # We only hard-block when an incoming piece is a duplicate source ID that adds
        # no new independent corroboration and the claim is already well above threshold.
        minimum = DOMAIN_CORROBORATION_MINIMUMS.get(policy.domain, 1)
        if evidence.support_type == SupportType.SUPPORTS and existing:
            existing_support = [e for e in existing if e.support_type == SupportType.SUPPORTS]
            existing_source_ids = {e.source_id for e in existing_support}
            is_new_source = evidence.source_id not in existing_source_ids
            projected = len(existing_source_ids) + (1 if is_new_source else 0)
            # Only reject if we already have > minimum pieces and this is a duplicate
            # source — i.e., it adds zero new corroboration to an already-verified claim.
            if not is_new_source and projected >= minimum and len(existing_support) >= minimum:
                return (
                    False,
                    f"Corroboration gate: duplicate source_id adds no new independent "
                    f"corroboration ({projected} unique sources already meet minimum {minimum}).",
                    None,
                )
        return True, "ok", None

    def _check_policy(
        self, evidence: Evidence, source: Source, policy: PolicyContext
    ) -> Tuple[bool, str, Optional[float]]:
        # Medical domain: reject any evidence from non-peer-reviewed source
        if policy.domain == "medical" and source.type not in ("peer_reviewed", "clinical_study"):
            if evidence.weight > 0.5:
                return (
                    False,
                    f"Policy gate [medical]: source type '{source.type}' not peer_reviewed. "
                    "High-weight evidence rejected.",
                    None,
                )
        return True, "ok", None
