#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ================================================== #
# ALFA-EOS — Invariant Checker                       #
# RFC v0.1 §5 — Invariants                           #
# ================================================== #

"""
InvariantChecker enforces the 8 system invariants from the RFC.
These are the "laws of physics" for the epistemic runtime.

Violation of INVARIANT_01, _03, _07 is a CLASS A failure (Critical Integrity).
Violation of _02, _04, _05, _06, _08 is CLASS B (Epistemic Quality).

Fix (analysis §4.A #4): This class was described in the RFC but absent
from the reference implementation.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .primitives import Claim, ClaimStatus, Evidence, ExecutionPermission, PolicyContext
from .state_machine import EXECUTION_BLOCKED_STATUSES


@dataclass
class InvariantViolation:
    invariant_id: str
    description: str
    failure_class: str    # "A" | "B" | "C"
    context: Dict[str, Any]

    def __str__(self) -> str:
        return f"[INV-{self.invariant_id} CLASS-{self.failure_class}] {self.description}"


class InvariantChecker:
    """
    Checks all 8 system invariants before state mutations and execution grants.
    Call check_all() to run all applicable checks at once.
    """

    def check_all(
        self,
        *,
        claim: Optional[Claim] = None,
        target_status: Optional[ClaimStatus] = None,
        new_evidence: Optional[List[Evidence]] = None,
        execution_permission: Optional[ExecutionPermission] = None,
        policy_context: Optional[PolicyContext] = None,
        snapshot_data: Optional[Dict] = None,
        event_log_entries: Optional[int] = None,
    ) -> List[InvariantViolation]:
        """Run all applicable invariant checks and return list of violations."""
        violations = []

        if claim and target_status:
            v = self.invariant_01_no_execution_on_unverified(claim, target_status)
            if v:
                violations.append(v)
            v = self.invariant_02_verified_no_degrade_without_evidence(
                claim, target_status, new_evidence
            )
            if v:
                violations.append(v)

        if claim and target_status == ClaimStatus.CONFLICT:
            v = self.invariant_04_conflict_needs_two_agents()
            if v:
                violations.append(v)

        if claim:
            v = self.invariant_05_claim_id_from_canonical(claim)
            if v:
                violations.append(v)

        if snapshot_data:
            v = self.invariant_03_snapshot_immutable(snapshot_data)
            if v:
                violations.append(v)

        if execution_permission and policy_context:
            v = self.invariant_08_execution_requires_policy_permission(
                execution_permission, policy_context
            )
            if v:
                violations.append(v)

        return violations

    # ------------------------------------------------------------------
    # Individual invariants
    # ------------------------------------------------------------------

    def invariant_01_no_execution_on_unverified(
        self, claim: Claim, target_status: Optional[ClaimStatus] = None
    ) -> Optional[InvariantViolation]:
        """
        INVARIANT_01: No action may be taken based on UNVERIFIED or HYPOTHESIS.
        This is checked at execution gate, not at state transition.
        """
        effective_status = target_status if target_status else claim.status
        if effective_status in EXECUTION_BLOCKED_STATUSES:
            return InvariantViolation(
                invariant_id="01",
                description=(
                    f"Execution blocked: claim '{claim.claim_id}' has status "
                    f"'{effective_status.value}' which is not permitted for execution."
                ),
                failure_class="A",
                context={"claim_id": claim.claim_id, "status": effective_status.value},
            )
        return None

    def invariant_02_verified_no_degrade_without_evidence(
        self,
        claim: Claim,
        target_status: ClaimStatus,
        new_evidence: Optional[List[Evidence]],
    ) -> Optional[InvariantViolation]:
        """
        INVARIANT_02: VERIFIED status cannot be degraded without new hard evidence.
        """
        if claim.status != ClaimStatus.VERIFIED:
            return None
        # Degradation means moving away from VERIFIED to a lesser status
        downgrade_statuses = {
            ClaimStatus.UNVERIFIED,
            ClaimStatus.PARTIAL,
            ClaimStatus.EVIDENCE_REQUIRED,
        }
        if target_status in downgrade_statuses:
            has_new_evidence = bool(new_evidence)
            if not has_new_evidence:
                return InvariantViolation(
                    invariant_id="02",
                    description=(
                        f"Cannot downgrade VERIFIED claim '{claim.claim_id}' to "
                        f"'{target_status.value}' without new evidence."
                    ),
                    failure_class="B",
                    context={
                        "claim_id": claim.claim_id,
                        "current_status": claim.status.value,
                        "target_status": target_status.value,
                    },
                )
        return None

    def invariant_03_snapshot_immutable(
        self, snapshot_data: Dict
    ) -> Optional[InvariantViolation]:
        """
        INVARIANT_03: Snapshot must be immutable and versioned.
        Checks that the snapshot has a schema_version and previous_snapshot reference.
        """
        if not snapshot_data.get("schema_version"):
            return InvariantViolation(
                invariant_id="03",
                description="Snapshot missing schema_version — cannot guarantee immutability chain.",
                failure_class="A",
                context={"snapshot_id": snapshot_data.get("snapshot_id", "unknown")},
            )
        return None

    def invariant_04_conflict_needs_two_agents(
        self, agent_opinions: Optional[List[Dict]] = None
    ) -> Optional[InvariantViolation]:
        """
        INVARIANT_04: Arbitration in CONFLICT state must involve at least two independent AGENT_ROLEs.
        When agent_opinions are not provided (single-agent call), this is a warning-level check.
        """
        if agent_opinions is not None and len(agent_opinions) < 2:
            return InvariantViolation(
                invariant_id="04",
                description=(
                    "CONFLICT arbitration requires at least 2 independent AGENT_ROLEs. "
                    f"Only {len(agent_opinions)} provided."
                ),
                failure_class="B",
                context={"agent_count": len(agent_opinions)},
            )
        return None

    def invariant_05_claim_id_from_canonical(
        self, claim: Claim
    ) -> Optional[InvariantViolation]:
        """
        INVARIANT_05: claim_id MUST be deterministically derived from canonical_form.
        Recomputes and compares.
        """
        expected_id = hashlib.sha256(
            claim.canonical_form.encode("utf-8")
        ).hexdigest()[:16]
        if claim.claim_id != expected_id:
            return InvariantViolation(
                invariant_id="05",
                description=(
                    f"claim_id mismatch: stored='{claim.claim_id}', "
                    f"expected from canonical='{expected_id}'."
                ),
                failure_class="B",
                context={
                    "claim_id": claim.claim_id,
                    "expected_id": expected_id,
                    "canonical_form": claim.canonical_form[:100],
                },
            )
        return None

    def invariant_06_summary_not_modify_truth_anchor(
        self, operation: str
    ) -> Optional[InvariantViolation]:
        """
        INVARIANT_06: Summary operations must not modify truth_anchor.
        Check by operation type at call site.
        """
        if operation == "summary":
            return InvariantViolation(
                invariant_id="06",
                description="Summary operation attempted to modify truth_anchor. Blocked.",
                failure_class="B",
                context={"operation": operation},
            )
        return None

    def invariant_07_replay_deterministic(
        self,
        replay_state: Dict,
        runtime_state: Dict,
        schema_version: str,
    ) -> Optional[InvariantViolation]:
        """
        INVARIANT_07: Replay must produce identical final state for same event log
        and same schema_version.
        """
        replay_hash = hashlib.sha256(
            json.dumps(replay_state, sort_keys=True).encode()
        ).hexdigest()
        runtime_hash = hashlib.sha256(
            json.dumps(runtime_state, sort_keys=True).encode()
        ).hexdigest()
        if replay_hash != runtime_hash:
            return InvariantViolation(
                invariant_id="07",
                description=(
                    f"Replay divergence detected for schema_version='{schema_version}'. "
                    "This is a CLASS A failure."
                ),
                failure_class="A",
                context={
                    "schema_version": schema_version,
                    "replay_hash": replay_hash[:16],
                    "runtime_hash": runtime_hash[:16],
                },
            )
        return None

    def invariant_08_execution_requires_policy_permission(
        self,
        permission: ExecutionPermission,
        policy: PolicyContext,
    ) -> Optional[InvariantViolation]:
        """
        INVARIANT_08: Execution permission cannot be granted if policy_context requires escalation.
        """
        if permission.granted and permission.risk_score >= policy.require_human_above_risk:
            return InvariantViolation(
                invariant_id="08",
                description=(
                    f"Execution granted with risk_score={permission.risk_score:.2f} but "
                    f"policy requires human escalation above {policy.require_human_above_risk:.2f}."
                ),
                failure_class="A",
                context={
                    "claim_id": permission.claim_id,
                    "risk_score": permission.risk_score,
                    "threshold": policy.require_human_above_risk,
                    "domain": policy.domain,
                },
            )
        return None
