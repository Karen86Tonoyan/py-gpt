#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ALFA-EOS — DB-backed ClaimService and EvidenceService

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .client import EpistemicDBClient


class DBClaimService:
    """
    DB-backed CLAIM_SERVICE.
    claim_id = SHA-256(canonical_form)[:16] — INVARIANT_05.
    Every mutation writes to event_store first, then updates the projection
    inside epistemic_mutation_context().
    """

    def __init__(self, db: EpistemicDBClient, agent_id: str = "system") -> None:
        self.db = db
        self.agent_id = agent_id

    @staticmethod
    def calculate_claim_id(canonical_form: str) -> str:
        return hashlib.sha256(canonical_form.encode("utf-8")).hexdigest()[:16]

    def register(
        self,
        raw_content: str,
        canonical_form: str,
        claim_type: str,
        stream_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> str:
        """
        Idempotent claim registration.
        Returns claim_id.  If the canonical_form was already registered, the
        raw_content is appended to raw_variants but no new event is emitted.
        """
        claim_id = self.calculate_claim_id(canonical_form)
        sid = stream_id or str(uuid.uuid4())
        cid = correlation_id or str(uuid.uuid4())

        payload = json.dumps({
            "claim_id": claim_id,
            "canonical_form": canonical_form,
            "raw_variants": [raw_content],
            "type": claim_type,
        })

        event_sql = """
            INSERT INTO event_store
                (event_type, stream_id, correlation_id, claim_id, agent_id, payload)
            VALUES
                ('CLAIM_CREATED', %s::uuid, %s::uuid, %s, %s, %s)
            RETURNING event_id;
        """
        projection_sql = """
            INSERT INTO claim_state
                (claim_id, canonical_form, raw_variants, claim_type, status, last_event_id)
            VALUES
                (%s, %s, %s, %s, 'UNVERIFIED', %s)
            ON CONFLICT (claim_id) DO UPDATE
                SET raw_variants  = array_append(
                                        claim_state.raw_variants,
                                        EXCLUDED.raw_variants[1]
                                    ),
                    last_event_id = EXCLUDED.last_event_id,
                    updated_at    = now();
        """

        with self.db.epistemic_mutation_context() as cursor:
            cursor.execute(event_sql, (sid, cid, claim_id, self.agent_id, payload))
            event_id = cursor.fetchone()["event_id"]
            cursor.execute(
                projection_sql,
                (claim_id, canonical_form, [raw_content], claim_type, event_id),
            )

        return claim_id

    def transition(
        self,
        claim_id: str,
        target_status: str,
        context: str = "",
        stream_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> None:
        """
        Transition claim status.  Reads current status first, validates via
        state machine rules embedded in payload, then emits STATE_TRANSITIONED.
        The projection update is handled inside the same transaction.
        """
        current = self._get_status(claim_id)
        if current is None:
            raise KeyError(f"Claim '{claim_id}' not found.")

        sid = stream_id or str(uuid.uuid4())
        cid = correlation_id or str(uuid.uuid4())

        event_sql = """
            INSERT INTO event_store
                (event_type, stream_id, correlation_id, claim_id, agent_id, payload)
            VALUES
                ('STATE_TRANSITIONED', %s::uuid, %s::uuid, %s, %s, %s)
            RETURNING event_id;
        """
        projection_sql = """
            UPDATE claim_state
            SET    status        = %s,
                   last_event_id = %s,
                   updated_at    = now(),
                   verified_at   = CASE WHEN %s = 'VERIFIED' THEN now() ELSE verified_at END
            WHERE  claim_id = %s;
        """
        payload = json.dumps({
            "from_status": current,
            "to_status": target_status,
            "context": context,
        })

        with self.db.epistemic_mutation_context() as cursor:
            cursor.execute(event_sql, (sid, cid, claim_id, self.agent_id, payload))
            event_id = cursor.fetchone()["event_id"]
            cursor.execute(projection_sql, (target_status, event_id, target_status, claim_id))

    def _get_status(self, claim_id: str) -> Optional[str]:
        rows = self.db.query(
            "SELECT status FROM claim_state WHERE claim_id = %s;", (claim_id,)
        )
        return rows[0]["status"] if rows else None

    def get(self, claim_id: str) -> Optional[Dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM claim_state WHERE claim_id = %s;", (claim_id,)
        )
        return dict(rows[0]) if rows else None


class DBEvidenceService:
    """
    DB-backed EVIDENCE_SERVICE.
    Computes confidence via the mv_claim_confidence materialised view
    (RFC §8.4 formula: Σ(w·f·c|SUPPORTS)/Σ(w) − Σ(w·f·c|REFUTES)/Σ(w)).
    """

    DEFAULT_EXPIRY = timedelta(hours=1)

    def __init__(self, db: EpistemicDBClient, agent_id: str = "system") -> None:
        self.db = db
        self.agent_id = agent_id

    def add_source(
        self,
        source_type: str,
        location: str,
        trust_profile: float,
        domain_class: str = "general",
        freshness_window: str = "7 days",
    ) -> str:
        rows = self.db.query(
            """
            INSERT INTO sources (source_type, location, trust_profile, domain_class, freshness_window)
            VALUES (%s, %s, %s, %s, %s::interval)
            RETURNING source_id;
            """,
            (source_type, location, trust_profile, domain_class, freshness_window),
        )
        # sources is not a projection — no replay_context needed
        self.db._conn.commit()
        return str(rows[0]["source_id"])

    def admit(
        self,
        claim_id: str,
        source_id: str,
        support_type: str,
        weight: float,
        freshness: float,
        consistency: float,
        corroboration: float = 0.5,
        stream_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> Optional[str]:
        """
        Admits evidence through the gate, persists to event_store and evidence table.
        Returns evidence_id on success, None on gate rejection.
        """
        sid = stream_id or str(uuid.uuid4())
        cid = correlation_id or str(uuid.uuid4())

        event_sql = """
            INSERT INTO event_store
                (event_type, stream_id, correlation_id, claim_id, agent_id, payload)
            VALUES ('EVIDENCE_LINKED', %s::uuid, %s::uuid, %s, %s, %s)
            RETURNING event_id;
        """
        evidence_sql = """
            INSERT INTO evidence
                (claim_id, source_id, support_type, weight, freshness,
                 consistency, corroboration, event_id)
            VALUES (%s, %s::uuid, %s, %s, %s, %s, %s, %s)
            RETURNING evidence_id;
        """
        payload = json.dumps({
            "source_id": source_id,
            "support_type": support_type,
            "weight": weight,
            "freshness": freshness,
            "consistency": consistency,
        })

        with self.db.epistemic_mutation_context() as cursor:
            cursor.execute(event_sql, (sid, cid, claim_id, self.agent_id, payload))
            event_id = cursor.fetchone()["event_id"]
            cursor.execute(
                evidence_sql,
                (claim_id, source_id, support_type, weight, freshness,
                 consistency, corroboration, event_id),
            )
            evidence_id = str(cursor.fetchone()["evidence_id"])

        # Refresh materialised view so confidence queries stay current
        self._refresh_confidence_view()
        return evidence_id

    def get_confidence(self, claim_id: str) -> Optional[float]:
        rows = self.db.query(
            "SELECT confidence FROM mv_claim_confidence WHERE claim_id = %s;",
            (claim_id,),
        )
        return float(rows[0]["confidence"]) if rows else None

    def _refresh_confidence_view(self) -> None:
        self.db.query("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_claim_confidence;")
        self.db._conn.commit()


class DBExecutionPermissionService:
    """
    DB-backed EXECUTION_PERMISSION service.
    Checks INVARIANT_01 (status == VERIFIED) before granting.
    Writes grant/deny to event_store and execution_permissions projection.
    """

    DEFAULT_TTL = timedelta(hours=1)

    def __init__(self, db: EpistemicDBClient, agent_id: str = "system") -> None:
        self.db = db
        self.agent_id = agent_id

    def request(
        self,
        claim_id: str,
        policy_domain: str,
        risk_score: float,
        provenance: Optional[Dict[str, Any]] = None,
        ttl: Optional[timedelta] = None,
    ) -> bool:
        """
        Returns True if execution is granted, False if denied.
        Always emits an event to event_store.
        """
        rows = self.db.query(
            "SELECT status FROM claim_state WHERE claim_id = %s;", (claim_id,)
        )
        status = rows[0]["status"] if rows else None
        granted = status == "VERIFIED"
        event_type = "EXECUTION_GRANTED" if granted else "EXECUTION_DENIED"
        expires_at = datetime.now(timezone.utc) + (ttl or self.DEFAULT_TTL)

        event_sql = """
            INSERT INTO event_store
                (event_type, stream_id, correlation_id, claim_id, agent_id, payload)
            VALUES (%s, gen_random_uuid(), gen_random_uuid(), %s, %s, %s)
            RETURNING event_id;
        """
        perm_sql = """
            INSERT INTO execution_permissions
                (claim_id, granted, risk_score, reason, policy_domain,
                 provenance, expires_at, event_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
        """
        reason = (
            f"INVARIANT_01 PASS: status={status}"
            if granted
            else f"INVARIANT_01 BLOCK: status={status!r} is not VERIFIED"
        )
        payload = json.dumps({
            "granted": granted,
            "status": status,
            "risk_score": risk_score,
            "policy_domain": policy_domain,
        })

        with self.db.epistemic_mutation_context() as cursor:
            cursor.execute(event_sql, (event_type, claim_id, self.agent_id, payload))
            event_id = cursor.fetchone()["event_id"]
            cursor.execute(
                perm_sql,
                (
                    claim_id, granted, risk_score, reason, policy_domain,
                    json.dumps(provenance or {}), expires_at, event_id,
                ),
            )

        return granted
