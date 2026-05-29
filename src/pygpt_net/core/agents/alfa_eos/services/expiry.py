#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ================================================== #
# ALFA-EOS — Permission Expiry Service               #
# RFC v0.1 §7 — EXECUTION_PERMISSION_EXPIRED         #
# ================================================== #

"""
PermissionExpiryService: scheduler-invoked sweep that detects expired
ExecutionPermission grants and emits EXECUTION_PERMISSION_EXPIRED events.

Design intent: the domain stays deterministic — the service never calls
datetime.now() internally; instead the caller supplies `now` so the sweep
is fully testable without mocking.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from ..events import EventLog, EventType
from ..primitives import ExecutionPermission


class PermissionExpiryService:
    """
    Checks active grants against their expires_at field.
    Emits EXECUTION_PERMISSION_EXPIRED for each expired grant.
    Called by an external scheduler — not by domain logic.
    """

    def __init__(self, event_log: EventLog, agent_id: str = "system") -> None:
        self._log = event_log
        self._agent_id = agent_id

    def sweep(
        self, grants: List[ExecutionPermission], now: datetime
    ) -> List[str]:
        """
        Inspect each grant in `grants`.  For every grant where
        `expires_at` is set and `expires_at < now`, emit
        EXECUTION_PERMISSION_EXPIRED and collect the claim_id.

        Returns list of claim_ids whose grants have expired.
        """
        expired: List[str] = []
        for grant in grants:
            if not grant.granted:
                continue
            if grant.expires_at is None:
                continue
            # Normalise both sides to UTC-aware for safe comparison
            expires = grant.expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            reference = now
            if reference.tzinfo is None:
                reference = reference.replace(tzinfo=timezone.utc)

            if expires < reference:
                self._log.emit(
                    EventType.EXECUTION_PERMISSION_EXPIRED,
                    agent_id=self._agent_id,
                    payload={
                        "claim_id": grant.claim_id,
                        "expires_at": grant.expires_at.isoformat(),
                        "swept_at": reference.isoformat(),
                    },
                    claim_id=grant.claim_id,
                )
                expired.append(grant.claim_id)

        return expired
