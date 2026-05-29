#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ALFA-EOS — Replay Engine + InvariantGuard

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from .client import EpistemicDBClient


class InvariantGuard:
    """
    Stateless guard that verifies runtime invariants against the live DB state.
    All checks are read-only — violations are recorded to event_store by the caller.
    """

    @staticmethod
    def verify_execution_safety(cursor: Any, claim_id: str) -> bool:
        """INVARIANT_01: execution only for VERIFIED claims."""
        cursor.execute(
            "SELECT status FROM claim_state WHERE claim_id = %s;", (claim_id,)
        )
        row = cursor.fetchone()
        return bool(row and row["status"] == "VERIFIED")

    @staticmethod
    def verify_claim_id_canonical(cursor: Any, claim_id: str) -> bool:
        """INVARIANT_05: claim_id == sha256(canonical_form)[:16]."""
        cursor.execute(
            "SELECT canonical_form FROM claim_state WHERE claim_id = %s;", (claim_id,)
        )
        row = cursor.fetchone()
        if not row:
            return False
        expected = hashlib.sha256(
            row["canonical_form"].encode("utf-8")
        ).hexdigest()[:16]
        return claim_id == expected

    @staticmethod
    def verify_event_store_count(cursor: Any) -> int:
        """Returns current event count — used to detect M_silent > 0 anomalies."""
        cursor.execute("SELECT COUNT(*) AS n FROM event_store;")
        return cursor.fetchone()["n"]


class EpistemicReplayEngine:
    """
    Calls proc_replay_from_snapshot() and verifies INVARIANT_07 determinism.
    """

    def __init__(self, db: EpistemicDBClient) -> None:
        self.db = db

    def replay_from_snapshot(
        self, snapshot_id: str, schema_version: str = "1.0"
    ) -> bool:
        """
        Execute replay and verify hash equality (INVARIANT_07).
        Emits INVARIANT_VIOLATED to event_store on divergence, then raises.
        Returns True on success.
        """
        with self.db.epistemic_mutation_context() as cursor:
            cursor.execute(
                "CALL proc_replay_from_snapshot(%s::uuid, %s);",
                (snapshot_id, schema_version),
            )

            # Compute current projection hash
            cursor.execute(
                """
                SELECT string_agg(
                    claim_id || ':' || status || ':' || confidence::TEXT,
                    ',' ORDER BY claim_id
                ) AS state_string
                FROM claim_state;
                """
            )
            state_str = cursor.fetchone()["state_string"] or ""
            current_hash = hashlib.sha256(state_str.encode()).hexdigest()[:32]

            # Compare against stored snapshot_hash
            cursor.execute(
                "SELECT snapshot_hash FROM snapshots WHERE snapshot_id = %s::uuid;",
                (snapshot_id,),
            )
            row = cursor.fetchone()
            if row and row["snapshot_hash"] != current_hash:
                cursor.execute(
                    """
                    INSERT INTO event_store
                        (event_type, stream_id, correlation_id, agent_id, payload)
                    VALUES (
                        'INVARIANT_VIOLATED',
                        gen_random_uuid(), gen_random_uuid(),
                        'REPLAY_ENGINE',
                        %s
                    );
                    """,
                    (json.dumps({
                        "invariant": "INVARIANT_07",
                        "snapshot_id": str(snapshot_id),
                        "expected_hash": row["snapshot_hash"][:16],
                        "computed_hash": current_hash[:16],
                        "error": "Replay divergence detected",
                    }),),
                )
                raise RuntimeError(
                    "Class A Critical Integrity Failure: INVARIANT_07 violated — "
                    f"replay hash {current_hash[:16]} ≠ snapshot hash "
                    f"{row['snapshot_hash'][:16]}. System state is unverified."
                )

        return True

    def verify_db_integrity(self, snapshot_id: str) -> Optional[str]:
        """
        Calls fn_verify_replay_integrity() and returns error string on failure,
        None on success.
        """
        rows = self.db.query(
            "SELECT fn_verify_replay_integrity(%s::uuid) AS ok;", (snapshot_id,)
        )
        if rows and not rows[0]["ok"]:
            return f"INVARIANT_07: replay integrity check failed for snapshot {snapshot_id}"
        return None
