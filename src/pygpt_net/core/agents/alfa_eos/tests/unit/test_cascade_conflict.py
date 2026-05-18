#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ALFA-EOS — unit tests: cascade conflict (RFC §6.3)
#
# When a claim becomes CONFLICT, all VERIFIED dependents are downgraded.
#
# Thread-safety note: in-memory ArbitrationService is single-threaded by
# design (protected by threading.Lock in _finalise/_apply_timeout).
# The PostgreSQL layer relies on SERIALIZABLE isolation for concurrent
# sessions — see tests/integration/test_pg_claim_edges_race.py.

import unittest

from pygpt_net.core.agents.alfa_eos import AlfaEOS
from pygpt_net.core.agents.alfa_eos.events import EventType
from pygpt_net.core.agents.alfa_eos.primitives import ClaimStatus


class TestCascadeDowngradesVerifiedDependents(unittest.TestCase):
    """CONFLICT on a parent claim downgrades VERIFIED dependent claims."""

    def setUp(self):
        self.eos = AlfaEOS(domain="business")
        self.parent = self.eos.assert_claim("parent claim")
        self.child  = self.eos.assert_claim(
            "child claim", depends_on=[self.parent.claim_id]
        )
        # Directly set VERIFIED (transition requires evidence — bypassing for test setup)
        self.child.status = ClaimStatus.VERIFIED
        self.child.confidence = 0.8

    def test_cascade_downgrades_verified_child(self):
        affected = self.eos.claims.cascade_conflict(self.parent.claim_id)
        self.assertIn(self.child.claim_id, affected)
        updated = self.eos.get_claim(self.child.claim_id)
        self.assertEqual(updated.status, ClaimStatus.CONFLICT)

    def test_cascade_emits_state_transitioned_events(self):
        before = self.eos.event_log.count()
        self.eos.claims.cascade_conflict(self.parent.claim_id)
        transitions = self.eos.event_log.get_events_by_type(EventType.STATE_TRANSITIONED)
        # At least one STATE_TRANSITIONED event for the child
        child_events = [e for e in transitions if e.claim_id == self.child.claim_id]
        self.assertGreater(len(child_events), 0)


class TestCascadeIgnoresNonVerifiedDependents(unittest.TestCase):
    """cascade_conflict only downgrades VERIFIED claims, ignores others."""

    def setUp(self):
        self.eos = AlfaEOS(domain="business")
        self.parent   = self.eos.assert_claim("parent cascade-ignore")
        self.unver    = self.eos.assert_claim(
            "unverified dependent", depends_on=[self.parent.claim_id]
        )
        # Leave unver in UNVERIFIED state

    def test_unverified_dependent_not_in_affected(self):
        affected = self.eos.claims.cascade_conflict(self.parent.claim_id)
        self.assertNotIn(self.unver.claim_id, affected)

    def test_unverified_dependent_status_unchanged(self):
        self.eos.claims.cascade_conflict(self.parent.claim_id)
        claim = self.eos.get_claim(self.unver.claim_id)
        self.assertEqual(claim.status, ClaimStatus.UNVERIFIED)


class TestArbitrationFinaliseIdempotency(unittest.TestCase):
    """_finalise() under threading.Lock emits exactly one ARBITRATION_RESOLVED."""

    def test_double_finalise_emits_one_event(self):
        import threading
        from pygpt_net.core.agents.alfa_eos.services.arbitration import (
            AgentOpinion,
        )

        eos = AlfaEOS(domain="business")
        claim = eos.assert_claim("arbitration idempotency test")
        # Directly set status to CONFLICT (UNVERIFIED → CONFLICT is not a valid transition)
        claim.status = ClaimStatus.CONFLICT
        record = eos.start_arbitration(claim.claim_id)

        arb_svc = eos.arbitration
        results = []

        def finalise():
            r = arb_svc._finalise(record, ClaimStatus.VERIFIED)
            results.append(r)

        t1 = threading.Thread(target=finalise)
        t2 = threading.Thread(target=finalise)
        t1.start(); t2.start()
        t1.join(); t2.join()

        resolved_events = eos.event_log.get_events_by_type(EventType.ARBITRATION_RESOLVED)
        claim_events = [e for e in resolved_events if e.claim_id == claim.claim_id]
        self.assertEqual(len(claim_events), 1, "Expected exactly one ARBITRATION_RESOLVED event")


if __name__ == "__main__":
    unittest.main()
