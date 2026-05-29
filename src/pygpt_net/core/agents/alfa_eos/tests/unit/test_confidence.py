#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ALFA-EOS — unit tests: confidence semantics (RFC §8.4)
#
# Critical invariant: None ≠ 0.0
#   None  → no admitted evidence (INSUFFICIENT_EVIDENCE)
#   0.0   → both SUPPORTS and REFUTES balance exactly (CONFLICT)
#   >0.0  → net positive support

import unittest

from pygpt_net.core.agents.alfa_eos import AlfaEOS
from pygpt_net.core.agents.alfa_eos.events import EventLog
from pygpt_net.core.agents.alfa_eos.primitives import (
    ClaimStatus,
    Evidence,
    PolicyContext,
    Source,
    SupportType,
)
from pygpt_net.core.agents.alfa_eos.services.evidence import EvidenceService


def _make_svc():
    log = EventLog()
    return EvidenceService(log, agent_id="test")


def _policy(domain="business"):
    return PolicyContext.for_domain(domain)


def _add(svc, claim_id, support_type, weight=0.8):
    source = Source.new("agent", "loc://test", trust_profile=0.9)
    ev = Evidence.new(claim_id, source.source_id, support_type, weight=weight)
    svc.add_source(source)
    gate, conf = svc.admit(ev, source, _policy())
    return gate, conf, ev


class TestNoEvidenceIsNone(unittest.TestCase):
    """Given no admitted evidence → confidence is None (not 0.0)."""

    def test_compute_confidence_unknown_claim(self):
        svc = _make_svc()
        result = svc.compute_confidence("nonexistent-claim-id")
        self.assertIsNone(result)

    def test_compute_confidence_after_rejected_evidence(self):
        """Even if evidence was rejected, confidence stays None."""
        svc = _make_svc()
        # Admit with a source that has trust_profile=0.0 (should be rejected by gate)
        source = Source.new("agent", "loc://test", trust_profile=0.0)
        ev = Evidence.new("claim-x", source.source_id, SupportType.SUPPORTS, weight=0.8)
        svc.add_source(source)
        gate, conf = svc.admit(ev, source, _policy())
        # Whether passed or not, if no evidence admitted for claim: None
        if not gate.passed:
            self.assertIsNone(svc.compute_confidence("claim-x"))


class TestNoEvidenceStatusIsEvidenceRequired(unittest.TestCase):
    """Given no admitted evidence → infer_target_status → EVIDENCE_REQUIRED."""

    def test_infer_target_status_no_evidence(self):
        svc = _make_svc()
        status = svc.infer_target_status("nonexistent", _policy())
        self.assertEqual(status, ClaimStatus.EVIDENCE_REQUIRED)


class TestNoEvidenceExecutionDenied(unittest.TestCase):
    """Given no admitted evidence → request_execution → denied with INSUFFICIENT_EVIDENCE."""

    def test_execution_denied_when_no_evidence(self):
        eos = AlfaEOS(domain="business")
        claim = eos.assert_claim("test claim with no evidence")
        perm = eos.request_execution(claim.claim_id)
        self.assertFalse(perm.granted)
        self.assertIn("INSUFFICIENT_EVIDENCE", perm.reason)


class TestBalancedConflictIsZeroNotNone(unittest.TestCase):
    """Given both SUPPORTS + REFUTES evidence → confidence=0.0, status=CONFLICT."""

    def setUp(self):
        self.svc = _make_svc()
        self.claim_id = "claim-balanced"
        # EvidenceGate blocks REFUTES when SUPPORTS already dominates (>75% threshold).
        # Insert both evidence types directly to test pure confidence arithmetic.
        source = Source.new("agent", "loc://test", trust_profile=0.9)
        self.svc.add_source(source)
        ev_sup = Evidence.new(self.claim_id, source.source_id, SupportType.SUPPORTS, weight=0.8)
        ev_ref = Evidence.new(self.claim_id, source.source_id, SupportType.REFUTES, weight=0.8)
        self.svc._store[ev_sup.evidence_id] = ev_sup
        self.svc._by_claim.setdefault(self.claim_id, []).append(ev_sup.evidence_id)
        self.svc._store[ev_ref.evidence_id] = ev_ref
        self.svc._by_claim.setdefault(self.claim_id, []).append(ev_ref.evidence_id)

    def test_confidence_is_zero_not_none(self):
        result = self.svc.compute_confidence(self.claim_id)
        self.assertIsNotNone(result)
        self.assertEqual(result, 0.0)

    def test_status_is_conflict(self):
        status = self.svc.infer_target_status(self.claim_id, _policy())
        self.assertEqual(status, ClaimStatus.CONFLICT)


class TestPositiveConfidence(unittest.TestCase):
    """Given only SUPPORTS evidence → confidence > 0.0."""

    def test_supports_only_positive_confidence(self):
        svc = _make_svc()
        _add(svc, "claim-pos", SupportType.SUPPORTS, weight=0.8)
        result = svc.compute_confidence("claim-pos")
        self.assertIsNotNone(result)
        self.assertGreater(result, 0.0)


if __name__ == "__main__":
    unittest.main()
