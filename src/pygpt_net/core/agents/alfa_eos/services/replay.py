#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ================================================== #
# ALFA-EOS — Replay Service                          #
# RFC v0.1 §12 — REPLAY_SERVICE                      #
# ================================================== #

"""
REPLAY_SERVICE: replays an event log to reconstruct epistemic state.

INVARIANT_07: replaying the same event log with the same schema_version MUST
produce an identical final state.  The service verifies this by hashing both
the replayed and current states and comparing them.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional

from ..events import Event, EventLog, EventType
from ..invariants import InvariantChecker
from ..primitives import Claim, ClaimStatus, ClaimType


class ReplayService:
    """
    Rebuilds ClaimService state from an EventLog.
    Used for audit, debugging, and INVARIANT_07 verification.
    """

    def __init__(self) -> None:
        self._invariant_checker = InvariantChecker()

    def replay(self, event_log: EventLog) -> Dict[str, Claim]:
        """
        Process all events in order and return the reconstructed claim store.
        """
        store: Dict[str, Claim] = {}

        for event in event_log.all():
            self._apply(event, store)

        return store

    def verify_determinism(
        self,
        event_log: EventLog,
        current_store: Dict[str, Claim],
        schema_version: str,
    ) -> Optional[str]:
        """
        Replay the event log and compare the result to current_store.
        Returns None if they match (INVARIANT_07 satisfied).
        Returns an error string describing the divergence if they don't.
        """
        replayed = self.replay(event_log)

        replay_hash = self._hash_store(replayed)
        current_hash = self._hash_store(current_store)

        violation = self._invariant_checker.invariant_07_replay_deterministic(
            replay_state=self._serialise_store(replayed),
            runtime_state=self._serialise_store(current_store),
            schema_version=schema_version,
        )

        if violation:
            return str(violation)
        return None

    # ------------------------------------------------------------------
    # Event application
    # ------------------------------------------------------------------

    def _apply(self, event: Event, store: Dict[str, Claim]) -> None:
        et = event.event_type
        p  = event.payload

        if et == EventType.CLAIM_CREATED:
            claim_id = p.get("claim_id", "")
            if claim_id and claim_id not in store:
                store[claim_id] = Claim(
                    claim_id=claim_id,
                    canonical_form=p.get("canonical_form", ""),
                    raw_variants=[],
                    type=ClaimType(p.get("type", "FACT")),
                    status=ClaimStatus.UNVERIFIED,
                    content=p.get("canonical_form", ""),
                    evidence_ref=[],
                    confidence=0.0,
                    verified_at=None,
                    valid_until=None,
                    revalidation_policy=None,
                    depends_on=[],
                )

        elif et == EventType.STATE_TRANSITIONED:
            claim_id = event.claim_id
            if claim_id and claim_id in store:
                store[claim_id].status = ClaimStatus(p["to_status"])

        elif et == EventType.EVIDENCE_ADDED:
            claim_id = event.claim_id
            if claim_id and claim_id in store:
                eid = p.get("evidence_id")
                if eid and eid not in store[claim_id].evidence_ref:
                    store[claim_id].evidence_ref.append(eid)
                if "new_confidence" in p:
                    store[claim_id].confidence = float(p["new_confidence"])

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _serialise_store(store: Dict[str, Claim]) -> Dict[str, Any]:
        return {
            cid: {
                "status": c.status.value,
                "confidence": round(c.confidence, 6),
                "evidence_ref": sorted(c.evidence_ref),
            }
            for cid, c in store.items()
        }

    @staticmethod
    def _hash_store(store: Dict[str, Claim]) -> str:
        serialised = {
            cid: {
                "status": c.status.value,
                "confidence": round(c.confidence, 6),
                "evidence_ref": sorted(c.evidence_ref),
            }
            for cid, c in store.items()
        }
        return hashlib.sha256(
            json.dumps(serialised, sort_keys=True).encode()
        ).hexdigest()
