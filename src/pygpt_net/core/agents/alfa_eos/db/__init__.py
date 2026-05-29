#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ALFA-EOS — PostgreSQL-backed facade (AlfaEpistemicOS)

"""
AlfaEpistemicOS: production facade backed by PostgreSQL.

Use when a DSN is available (staging / production).
The in-memory AlfaEOS in the parent package remains the default for
lightweight / embedded usage that does not require a database.

Usage:
    from pygpt_net.core.agents.alfa_eos.db import AlfaEpistemicOS

    eos = AlfaEpistemicOS("postgresql://user:pass@localhost/alfa_eos")
    eos.apply_schema()   # idempotent DDL bootstrap

    claim_id = eos.claims.register(
        raw_content="server down",
        canonical_form="server is down",
        claim_type="FACT",
    )
    eos.permissions.request(claim_id, "banking", risk_score=0.3)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from .client import EpistemicDBClient
from .engine import EpistemicReplayEngine, InvariantGuard
from .services import DBClaimService, DBEvidenceService, DBExecutionPermissionService

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


class AlfaEpistemicOS:
    """
    PostgreSQL-backed epistemic microkernel facade.
    Composes all DB-backed services under a single entry point.
    """

    def __init__(self, dsn: str, agent_id: str = "system") -> None:
        self.db          = EpistemicDBClient(dsn)
        self.claims      = DBClaimService(self.db, agent_id)
        self.evidence    = DBEvidenceService(self.db, agent_id)
        self.permissions = DBExecutionPermissionService(self.db, agent_id)
        self.replay      = EpistemicReplayEngine(self.db)
        self.guard       = InvariantGuard()

    def apply_schema(self, schema_path: Optional[str] = None) -> None:
        """Bootstrap the DB schema (idempotent — uses IF NOT EXISTS)."""
        path = schema_path or str(_SCHEMA_PATH)
        self.db.apply_schema(path)

    def request_execution_permission(
        self, claim_id: str, policy_domain: str, risk_score: float = 0.5
    ) -> bool:
        """Main orchestration gate — returns True only for VERIFIED claims."""
        return self.permissions.request(claim_id, policy_domain, risk_score)

    def close(self) -> None:
        self.db.close()


__all__ = [
    "AlfaEpistemicOS",
    "EpistemicDBClient",
    "DBClaimService",
    "DBEvidenceService",
    "DBExecutionPermissionService",
    "EpistemicReplayEngine",
    "InvariantGuard",
]
