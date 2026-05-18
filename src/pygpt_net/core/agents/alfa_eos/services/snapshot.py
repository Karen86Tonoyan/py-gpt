#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ================================================== #
# ALFA-EOS — Snapshot Service                        #
# RFC v0.1 §10 — SNAPSHOT_SERVICE                    #
# ================================================== #

"""
SNAPSHOT_SERVICE: creates immutable, chained snapshots of the truth_anchor.

INVARIANT_03: each snapshot MUST have schema_version and a previous_snapshot
reference (sha256 of the previous snapshot payload) to guarantee the
immutability chain.  The very first snapshot uses previous_snapshot=None.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..events import EventLog, EventType
from ..primitives import Claim, PolicyContext, Snapshot


class SnapshotService:
    """
    Creates and verifies immutable Snapshot objects.
    All snapshots are kept in memory (replace with durable store for production).
    """

    def __init__(self, event_log: EventLog, schema_version: str = "1.0") -> None:
        self._snapshots: List[Snapshot] = []
        self._log = event_log
        self._schema_version = schema_version

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def take(
        self,
        truth_anchor: Dict[str, Claim],
        agents: List[Dict[str, Any]],
        policy_context: PolicyContext,
        drift_report: Optional[Dict[str, Any]] = None,
        next_action: Optional[str] = None,
        arbitration_history: Optional[List[Dict[str, Any]]] = None,
    ) -> Snapshot:
        """
        Create a new snapshot, chaining it to the previous one (INVARIANT_03).
        """
        previous_hash = self._snapshots[-1].compute_hash() if self._snapshots else None

        snapshot = Snapshot(
            snapshot_id=str(uuid.uuid4()),
            schema_version=self._schema_version,
            timestamp=datetime.now(timezone.utc),
            truth_anchor=dict(truth_anchor),
            gaps=[
                cid for cid, c in truth_anchor.items()
                if c.type.value == "GAP"
            ],
            agents=agents,
            drift_report=drift_report,
            next_action=next_action,
            arbitration_history=arbitration_history or [],
            policy_context={
                "domain": policy_context.domain,
                "risk_profile": policy_context.risk_profile,
                "verify_threshold": policy_context.verify_threshold,
            },
            previous_snapshot=previous_hash,
        )

        self._snapshots.append(snapshot)

        self._log.emit(
            EventType.SNAPSHOT_CREATED,
            agent_id="system",
            payload={
                "snapshot_id": snapshot.snapshot_id,
                "schema_version": snapshot.schema_version,
                "claim_count": len(truth_anchor),
                "previous_snapshot": previous_hash,
                "hash": snapshot.compute_hash()[:16],
            },
        )
        return snapshot

    # ------------------------------------------------------------------
    # Verify chain
    # ------------------------------------------------------------------

    def verify_chain(self) -> bool:
        """
        Walk the snapshot chain and verify each snapshot's previous_snapshot
        reference matches the hash of the prior snapshot.
        Returns True if the chain is intact, False otherwise.
        """
        for i in range(1, len(self._snapshots)):
            expected_prev = self._snapshots[i - 1].compute_hash()
            if self._snapshots[i].previous_snapshot != expected_prev:
                return False
        return True

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def latest(self) -> Optional[Snapshot]:
        return self._snapshots[-1] if self._snapshots else None

    def all(self) -> List[Snapshot]:
        return list(self._snapshots)

    def count(self) -> int:
        return len(self._snapshots)
