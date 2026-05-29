#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ================================================== #
# ALFA-EOS — Drift Service                           #
# RFC v0.1 §11 — DRIFT_SERVICE                       #
# ================================================== #

"""
DRIFT_SERVICE: detects semantic and operational drift in the epistemic state.

Two detection modes:
  - LOCAL: compares current claim state to the most recent snapshot
  - LONGITUDINAL: compares trend across a window of snapshots

Six drift types (RFC §11.2):
  OBJECTIVE_DRIFT, EVIDENCE_DRIFT, TERMINOLOGY_DRIFT,
  POLICY_DRIFT, CONFIDENCE_DRIFT, IDENTITY_DRIFT
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..events import EventLog, EventType
from ..primitives import Claim, DriftType, Snapshot


@dataclass
class DriftSignal:
    drift_type: DriftType
    severity: float          # 0.0 – 1.0
    affected_claim_ids: List[str]
    description: str
    detected_at: datetime


class DriftService:
    """
    Detects drift between consecutive snapshots and emits DRIFT_DETECTED events.
    """

    CONFIDENCE_DRIFT_THRESHOLD = 0.15   # absolute change that counts as drift
    EVIDENCE_DRIFT_THRESHOLD   = 0.20   # proportion of evidence changed

    def __init__(self, event_log: EventLog) -> None:
        self._log = event_log

    def detect(
        self,
        current_claims: Dict[str, Claim],
        baseline_snapshot: Optional[Snapshot],
        policy_changed: bool = False,
    ) -> List[DriftSignal]:
        """
        Compare current state to baseline snapshot and return drift signals.
        """
        if baseline_snapshot is None:
            return []

        signals: List[DriftSignal] = []

        signals.extend(self._check_confidence_drift(current_claims, baseline_snapshot))
        signals.extend(self._check_evidence_drift(current_claims, baseline_snapshot))
        signals.extend(self._check_objective_drift(current_claims, baseline_snapshot))
        if policy_changed:
            signals.extend(self._check_policy_drift(current_claims))

        for sig in signals:
            self._log.emit(
                EventType.DRIFT_DETECTED,
                agent_id="drift_service",
                payload={
                    "drift_type": sig.drift_type.value,
                    "severity": sig.severity,
                    "affected_count": len(sig.affected_claim_ids),
                    "description": sig.description,
                },
            )

        return signals

    # ------------------------------------------------------------------
    # Individual checkers
    # ------------------------------------------------------------------

    def _check_confidence_drift(
        self, current: Dict[str, Claim], baseline: Snapshot
    ) -> List[DriftSignal]:
        affected = []
        for cid, claim in current.items():
            base_claim = baseline.truth_anchor.get(cid)
            if base_claim is None:
                continue
            delta = abs(claim.confidence - base_claim.confidence)
            if delta >= self.CONFIDENCE_DRIFT_THRESHOLD:
                affected.append(cid)

        if not affected:
            return []

        avg_delta = sum(
            abs(current[cid].confidence - baseline.truth_anchor[cid].confidence)
            for cid in affected
        ) / len(affected)

        return [DriftSignal(
            drift_type=DriftType.CONFIDENCE_DRIFT,
            severity=min(1.0, avg_delta / 0.5),
            affected_claim_ids=affected,
            description=(
                f"{len(affected)} claims show confidence drift ≥ "
                f"{self.CONFIDENCE_DRIFT_THRESHOLD:.0%}."
            ),
            detected_at=datetime.now(timezone.utc),
        )]

    def _check_evidence_drift(
        self, current: Dict[str, Claim], baseline: Snapshot
    ) -> List[DriftSignal]:
        affected = []
        for cid, claim in current.items():
            base_claim = baseline.truth_anchor.get(cid)
            if base_claim is None:
                continue
            base_refs = set(base_claim.evidence_ref)
            curr_refs = set(claim.evidence_ref)
            if not base_refs:
                continue
            changed_fraction = len(base_refs.symmetric_difference(curr_refs)) / len(base_refs)
            if changed_fraction >= self.EVIDENCE_DRIFT_THRESHOLD:
                affected.append(cid)

        if not affected:
            return []

        return [DriftSignal(
            drift_type=DriftType.EVIDENCE_DRIFT,
            severity=min(1.0, len(affected) / max(1, len(current))),
            affected_claim_ids=affected,
            description=f"{len(affected)} claims have significant evidence set changes.",
            detected_at=datetime.now(timezone.utc),
        )]

    def _check_objective_drift(
        self, current: Dict[str, Claim], baseline: Snapshot
    ) -> List[DriftSignal]:
        new_claims = set(current.keys()) - set(baseline.truth_anchor.keys())
        dropped = set(baseline.truth_anchor.keys()) - set(current.keys())
        if not new_claims and not dropped:
            return []

        change_count = len(new_claims) + len(dropped)
        total = max(1, len(baseline.truth_anchor))

        return [DriftSignal(
            drift_type=DriftType.OBJECTIVE_DRIFT,
            severity=min(1.0, change_count / total),
            affected_claim_ids=list(new_claims | dropped),
            description=(
                f"Claim set changed: {len(new_claims)} added, {len(dropped)} removed."
            ),
            detected_at=datetime.now(timezone.utc),
        )]

    def _check_policy_drift(
        self, current: Dict[str, Claim]
    ) -> List[DriftSignal]:
        return [DriftSignal(
            drift_type=DriftType.POLICY_DRIFT,
            severity=0.6,
            affected_claim_ids=list(current.keys()),
            description="Policy context has changed — all claims require revalidation.",
            detected_at=datetime.now(timezone.utc),
        )]
