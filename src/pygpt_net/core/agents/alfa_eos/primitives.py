#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ================================================== #
# ALFA-EOS — Epistemic Operating System              #
# RFC v0.1 — Core Data Primitives                    #
# ================================================== #

"""
Core data types for ALFA-EOS.  All dataclasses are immutable by default;
mutation happens only through explicit STATE_TRANSITIONED events.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class ClaimType(str, Enum):
    FACT = "FACT"
    HYPOTHESIS = "HYPOTHESIS"
    INFERENCE = "INFERENCE"
    CONFLICT = "CONFLICT"
    GAP = "GAP"
    DECISION = "DECISION"


class ClaimStatus(str, Enum):
    UNVERIFIED = "UNVERIFIED"
    EVIDENCE_REQUIRED = "EVIDENCE_REQUIRED"
    PARTIAL = "PARTIAL"
    VERIFIED = "VERIFIED"
    CONFLICT = "CONFLICT"
    REFUTED = "REFUTED"


class SupportType(str, Enum):
    SUPPORTS = "SUPPORTS"
    REFUTES = "REFUTES"
    NEUTRAL = "NEUTRAL"


class DriftType(str, Enum):
    OBJECTIVE_DRIFT = "OBJECTIVE_DRIFT"
    EVIDENCE_DRIFT = "EVIDENCE_DRIFT"
    TERMINOLOGY_DRIFT = "TERMINOLOGY_DRIFT"
    POLICY_DRIFT = "POLICY_DRIFT"
    CONFIDENCE_DRIFT = "CONFIDENCE_DRIFT"
    IDENTITY_DRIFT = "IDENTITY_DRIFT"


class ArbitrationLevel(str, Enum):
    LOCAL = "LOCAL"       # resolved within service
    SYSTEM = "SYSTEM"     # requires multi-agent vote
    HUMAN = "HUMAN"       # escalate to human operator


class FailureClass(str, Enum):
    A = "A"  # Critical Integrity Failure — stop execution
    B = "B"  # Epistemic Quality Failure — revalidate
    C = "C"  # Operational Degradation — optimize


# ---------------------------------------------------------------------------
# Source
# ---------------------------------------------------------------------------

@dataclass
class Source:
    source_id: str
    type: str                        # "document" | "agent" | "tool_output" | "user_input" | "external_api"
    location: str
    timestamp: datetime
    trust_profile: float             # 0.0 – 1.0
    domain_class: str
    freshness_window: timedelta

    @staticmethod
    def new(type: str, location: str, trust_profile: float = 0.8,
            domain_class: str = "general",
            freshness_window: timedelta = timedelta(days=7)) -> "Source":
        return Source(
            source_id=str(uuid.uuid4()),
            type=type,
            location=location,
            timestamp=datetime.now(timezone.utc),
            trust_profile=trust_profile,
            domain_class=domain_class,
            freshness_window=freshness_window,
        )


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

@dataclass
class Evidence:
    """
    Evidence is the relation between a Source and a Claim.
    It is not the source itself.

    Fix (analysis §3, sekcja 8): Added `consistency` field that was missing
    from the original reference implementation.
    """
    evidence_id: str
    claim_id: str
    source_id: str
    support_type: SupportType
    weight: float            # 0.0 – 1.0: how strongly source supports/refutes
    freshness: float         # 0.0 – 1.0: decay from creation time
    consistency: float       # 0.0 – 1.0: agreement with other evidence in set
    corroboration: float     # 0.0 – 1.0: independent confirmation count, normalised
    timestamp: datetime

    @property
    def aggregate_score(self) -> float:
        """
        Weighted aggregate score as per §8.4 of the RFC.
        aggregate = weight × freshness × consistency
        Consistent with the formula: confidence = Σ(w_i × f_i × c_i) / Σ(w_i)
        """
        return self.weight * self.freshness * self.consistency

    @staticmethod
    def new(
        claim_id: str,
        source_id: str,
        support_type: SupportType,
        weight: float = 0.8,
        freshness: float = 1.0,
        consistency: float = 1.0,
        corroboration: float = 0.5,
    ) -> "Evidence":
        return Evidence(
            evidence_id=str(uuid.uuid4()),
            claim_id=claim_id,
            source_id=source_id,
            support_type=support_type,
            weight=weight,
            freshness=freshness,
            consistency=consistency,
            corroboration=corroboration,
            timestamp=datetime.now(timezone.utc),
        )


# ---------------------------------------------------------------------------
# Claim
# ---------------------------------------------------------------------------

@dataclass
class Claim:
    """
    Central epistemic unit.  claim_id MUST be derived from canonical_form
    (never from raw content).  Includes depends_on for CLAIM_DEPENDENCY_GRAPH.
    """
    claim_id: str                        # sha256(canonical_form)[:16]
    canonical_form: str
    raw_variants: List[str]
    type: ClaimType
    status: ClaimStatus
    content: str
    evidence_ref: List[str]              # evidence_ids
    confidence: float                    # 0.0 – 1.0
    verified_at: Optional[datetime]
    valid_until: Optional[datetime]
    revalidation_policy: Optional[str]
    depends_on: List[str]               # CLAIM_DEPENDENCY_GRAPH: list of claim_ids
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    meta: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------

@dataclass
class Snapshot:
    snapshot_id: str
    schema_version: str
    timestamp: datetime
    truth_anchor: Dict[str, Claim]       # claim_id -> Claim
    gaps: List[str]                      # claim_ids of GAP type
    agents: List[Dict[str, Any]]
    drift_report: Optional[Dict[str, Any]]
    next_action: Optional[str]
    arbitration_history: List[Dict[str, Any]]
    policy_context: Dict[str, Any]
    previous_snapshot: Optional[str]    # sha256 of previous snapshot payload

    def compute_hash(self) -> str:
        """Deterministic hash of this snapshot for chain-of-custody."""
        import json
        payload = {
            "snapshot_id": self.snapshot_id,
            "schema_version": self.schema_version,
            "timestamp": self.timestamp.isoformat(),
            "claim_ids": sorted(self.truth_anchor.keys()),
            "previous_snapshot": self.previous_snapshot,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()


# ---------------------------------------------------------------------------
# Policy Context
# ---------------------------------------------------------------------------

@dataclass
class PolicyContext:
    domain: str
    verify_threshold: float              # min confidence to reach VERIFIED
    partial_threshold: float             # min confidence for PARTIAL
    risk_profile: str                    # "low" | "medium" | "high" | "critical"
    max_arbitration_time: timedelta
    timeout_action: str                  # "DENY" | "DEFER" | "HUMAN_ESCALATION"
    require_human_above_risk: float      # risk_score threshold for human escalation
    freshness_window: timedelta          # max age for valid evidence

    @staticmethod
    def for_domain(domain: str) -> "PolicyContext":
        """Pre-built domain profiles."""
        profiles = {
            "marketing": PolicyContext(
                domain="marketing",
                verify_threshold=0.5,
                partial_threshold=0.3,
                risk_profile="low",
                max_arbitration_time=timedelta(minutes=30),
                timeout_action="DEFER",
                require_human_above_risk=0.9,
                freshness_window=timedelta(days=30),
            ),
            "business": PolicyContext(
                domain="business",
                verify_threshold=0.65,
                partial_threshold=0.4,
                risk_profile="medium",
                max_arbitration_time=timedelta(minutes=10),
                timeout_action="DENY",
                require_human_above_risk=0.7,
                freshness_window=timedelta(days=14),
            ),
            "banking": PolicyContext(
                domain="banking",
                verify_threshold=0.85,
                partial_threshold=0.6,
                risk_profile="high",
                max_arbitration_time=timedelta(minutes=5),
                timeout_action="DENY",
                require_human_above_risk=0.5,
                freshness_window=timedelta(hours=24),
            ),
            "medical": PolicyContext(
                domain="medical",
                verify_threshold=0.95,
                partial_threshold=0.75,
                risk_profile="critical",
                max_arbitration_time=timedelta(minutes=2),
                timeout_action="HUMAN_ESCALATION",
                require_human_above_risk=0.3,
                freshness_window=timedelta(hours=6),
            ),
        }
        return profiles.get(domain, profiles["business"])


# ---------------------------------------------------------------------------
# Execution Permission
# ---------------------------------------------------------------------------

@dataclass
class ExecutionPermission:
    granted: bool
    claim_id: str
    reason: str
    risk_score: float
    policy_context: str
    provenance_chain: Dict[str, Any]    # audit trail

    @staticmethod
    def deny(claim_id: str, reason: str, risk_score: float = 1.0) -> "ExecutionPermission":
        return ExecutionPermission(
            granted=False,
            claim_id=claim_id,
            reason=reason,
            risk_score=risk_score,
            policy_context="",
            provenance_chain={},
        )

    @staticmethod
    def grant(claim_id: str, reason: str, risk_score: float,
              provenance_chain: Dict[str, Any]) -> "ExecutionPermission":
        return ExecutionPermission(
            granted=True,
            claim_id=claim_id,
            reason=reason,
            risk_score=risk_score,
            policy_context="",
            provenance_chain=provenance_chain,
        )
