#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ALFA-EOS — unit tests: PermissionExpiryService (RFC §7)
#
# The expiry service is scheduler-invoked and deterministic:
# the caller supplies `now`, so tests need no time mocking.

import unittest
from datetime import datetime, timedelta, timezone

from pygpt_net.core.agents.alfa_eos import AlfaEOS
from pygpt_net.core.agents.alfa_eos.events import EventLog, EventType
from pygpt_net.core.agents.alfa_eos.primitives import ExecutionPermission
from pygpt_net.core.agents.alfa_eos.services.expiry import PermissionExpiryService


_PAST   = datetime(2000, 1, 1, tzinfo=timezone.utc)
_FUTURE = datetime(2999, 1, 1, tzinfo=timezone.utc)
_NOW    = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)


def _grant(claim_id="claim-1", expires_at=None):
    return ExecutionPermission.grant(
        claim_id=claim_id,
        reason="test grant",
        risk_score=0.2,
        provenance_chain={},
        expires_at=expires_at,
    )


class TestSweepEmitsExpiredEvent(unittest.TestCase):
    """sweep() emits EXECUTION_PERMISSION_EXPIRED for each expired grant."""

    def test_expired_grant_emits_event(self):
        log = EventLog()
        svc = PermissionExpiryService(log, agent_id="test")
        grant = _grant("claim-expired", expires_at=_PAST)

        expired = svc.sweep([grant], now=_NOW)

        self.assertEqual(expired, ["claim-expired"])
        events = log.get_events_by_type(EventType.EXECUTION_PERMISSION_EXPIRED)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].claim_id, "claim-expired")

    def test_multiple_expired_grants(self):
        log = EventLog()
        svc = PermissionExpiryService(log)
        grants = [_grant(f"claim-{i}", expires_at=_PAST) for i in range(3)]

        expired = svc.sweep(grants, now=_NOW)

        self.assertEqual(len(expired), 3)
        events = log.get_events_by_type(EventType.EXECUTION_PERMISSION_EXPIRED)
        self.assertEqual(len(events), 3)


class TestSweepNoFalsePositives(unittest.TestCase):
    """sweep() does not expire grants with future expires_at or no expiry."""

    def test_future_grant_not_expired(self):
        log = EventLog()
        svc = PermissionExpiryService(log)
        grant = _grant("claim-future", expires_at=_FUTURE)

        expired = svc.sweep([grant], now=_NOW)

        self.assertEqual(expired, [])
        events = log.get_events_by_type(EventType.EXECUTION_PERMISSION_EXPIRED)
        self.assertEqual(len(events), 0)

    def test_no_expiry_field_not_expired(self):
        log = EventLog()
        svc = PermissionExpiryService(log)
        grant = _grant("claim-no-expiry", expires_at=None)

        expired = svc.sweep([grant], now=_NOW)

        self.assertEqual(expired, [])

    def test_denied_grant_ignored(self):
        log = EventLog()
        svc = PermissionExpiryService(log)
        deny = ExecutionPermission.deny("claim-denied", "test deny")
        deny.expires_at = _PAST  # type: ignore[attr-defined]

        expired = svc.sweep([deny], now=_NOW)

        self.assertEqual(expired, [])


class TestSweepViaFacade(unittest.TestCase):
    """AlfaEOS.sweep_expired_permissions() integrates PermissionExpiryService."""

    def test_facade_sweep_removes_expired_from_active_grants(self):
        eos = AlfaEOS(domain="business")
        claim = eos.assert_claim("sweep integration test claim")
        # Directly mark VERIFIED + set confidence (transition would require evidence)
        from pygpt_net.core.agents.alfa_eos.primitives import ClaimStatus
        claim.status = ClaimStatus.VERIFIED
        claim.confidence = 0.8

        past = _PAST
        perm = eos.request_execution(claim.claim_id, expires_at=past)
        self.assertTrue(perm.granted)
        self.assertEqual(len(eos._active_grants), 1)

        expired = eos.sweep_expired_permissions(now=_NOW)
        self.assertIn(claim.claim_id, expired)
        self.assertEqual(len(eos._active_grants), 0)


if __name__ == "__main__":
    unittest.main()
