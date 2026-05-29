#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ALFA-EOS — unit tests: epistemic invariants (RFC §5)
#
# One class per invariant.  All 8 invariants are covered.

import unittest

from pygpt_net.core.agents.alfa_eos import AlfaEOS
from pygpt_net.core.agents.alfa_eos.primitives import (
    ClaimStatus,
    ClaimType,
    ExecutionPermission,
    PolicyContext,
)
from pygpt_net.core.agents.alfa_eos.invariants import InvariantChecker
from pygpt_net.core.agents.alfa_eos.state_machine import StateMachineError


class TestInvariant01ExecutionRequiresVerified(unittest.TestCase):
    """INVARIANT_01: execution is only permitted for VERIFIED claims."""

    def setUp(self):
        self.eos = AlfaEOS(domain="business")

    def test_unverified_claim_denied(self):
        claim = self.eos.assert_claim("Invariant01 test — unverified")
        # claim starts UNVERIFIED with no evidence → denied
        perm = self.eos.request_execution(claim.claim_id)
        self.assertFalse(perm.granted)

    def test_verified_claim_granted(self):
        claim = self.eos.assert_claim("Invariant01 test — verified")
        # Directly set VERIFIED + confidence to bypass transition evidence requirement
        claim.status = ClaimStatus.VERIFIED
        claim.confidence = 0.8
        perm = self.eos.request_execution(claim.claim_id)
        self.assertTrue(perm.granted)


class TestInvariant02NoEvidenceNoVerification(unittest.TestCase):
    """INVARIANT_02: a claim cannot be VERIFIED without any evidence."""

    def test_transition_to_verified_without_evidence_raises(self):
        eos = AlfaEOS(domain="business")
        claim = eos.assert_claim("Invariant02 test")
        with self.assertRaises(Exception):
            # State machine should reject UNVERIFIED → VERIFIED without evidence
            eos.claims.transition(claim.claim_id, ClaimStatus.VERIFIED)


class TestInvariant03ClaimIdDeterminism(unittest.TestCase):
    """INVARIANT_03: claim_id is deterministic from canonical_form."""

    def test_same_text_same_id(self):
        eos = AlfaEOS()
        c1 = eos.assert_claim("The server is down")
        c2 = eos.assert_claim("the server is down")  # normalised
        self.assertEqual(c1.claim_id, c2.claim_id)

    def test_different_text_different_id(self):
        eos = AlfaEOS()
        c1 = eos.assert_claim("server is down")
        c2 = eos.assert_claim("server is available")
        self.assertNotEqual(c1.claim_id, c2.claim_id)


class TestInvariant04MinimumTwoAgentsForArbitration(unittest.TestCase):
    """INVARIANT_04: arbitration requires at least 2 independent agent opinions."""

    def test_single_opinion_does_not_resolve(self):
        from pygpt_net.core.agents.alfa_eos.services.arbitration import AgentOpinion
        eos = AlfaEOS(domain="business")
        claim = eos.assert_claim("Invariant04 test")
        # Directly set CONFLICT (UNVERIFIED → CONFLICT is not a valid transition)
        claim.status = ClaimStatus.CONFLICT
        eos.start_arbitration(claim.claim_id)

        opinion = AgentOpinion(
            agent_id="agent-a",
            claim_id=claim.claim_id,
            proposed_status=ClaimStatus.VERIFIED,
            confidence=0.9,
            rationale="looks good",
        )
        result = eos.submit_opinion(claim.claim_id, opinion)
        self.assertIsNone(result, "Should not resolve with a single opinion")


class TestInvariant05CanonicalFormSource(unittest.TestCase):
    """INVARIANT_05: claim_id is derived from canonical_form, never raw_text."""

    def test_raw_text_variant_does_not_change_id(self):
        eos = AlfaEOS()
        c1 = eos.assert_claim("Payment   confirmed")
        c2 = eos.assert_claim("payment confirmed")
        self.assertEqual(c1.claim_id, c2.claim_id)


class TestInvariant06EventLogAppendOnly(unittest.TestCase):
    """INVARIANT_06: event log is append-only (in-memory: length only grows)."""

    def test_log_only_grows(self):
        eos = AlfaEOS()
        before = eos.event_log.count()
        eos.assert_claim("event log test")
        after = eos.event_log.count()
        self.assertGreater(after, before)

    def test_events_are_not_deleted(self):
        eos = AlfaEOS()
        eos.assert_claim("append-only test")
        count = eos.event_log.count()
        # Verify all() returns same count
        self.assertEqual(len(eos.event_log.all()), count)


class TestInvariant07ReplayDeterminism(unittest.TestCase):
    """INVARIANT_07: replaying event log reproduces exact state."""

    def test_verify_replay_returns_none_on_clean_state(self):
        eos = AlfaEOS()
        eos.assert_claim("replay test claim")
        result = eos.verify_replay()
        # None means no violation detected
        self.assertIsNone(result)


class TestInvariant08ExecutionRequiresPolicyPermission(unittest.TestCase):
    """INVARIANT_08: execution must comply with PolicyContext risk thresholds."""

    def test_high_risk_score_may_be_denied(self):
        checker = InvariantChecker()
        policy = PolicyContext.for_domain("banking")  # require_human_above_risk=0.5
        perm = ExecutionPermission(
            granted=True,
            claim_id="test",
            reason="test",
            risk_score=0.9,   # above banking threshold
            policy_context="banking",
            provenance_chain={},
        )
        violation = checker.invariant_08_execution_requires_policy_permission(perm, policy)
        self.assertIsNotNone(violation)

    def test_low_risk_score_passes(self):
        checker = InvariantChecker()
        policy = PolicyContext.for_domain("marketing")  # require_human_above_risk=0.9
        perm = ExecutionPermission(
            granted=True,
            claim_id="test",
            reason="test",
            risk_score=0.1,
            policy_context="marketing",
            provenance_chain={},
        )
        violation = checker.invariant_08_execution_requires_policy_permission(perm, policy)
        self.assertIsNone(violation)


if __name__ == "__main__":
    unittest.main()
