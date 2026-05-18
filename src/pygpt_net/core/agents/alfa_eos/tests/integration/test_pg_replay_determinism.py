#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ALFA-EOS — integration tests: replay determinism (INVARIANT_07, PostgreSQL)
#
# Requires a live PostgreSQL instance.
# Set ALFA_EOS_TEST_DSN to run:
#   ALFA_EOS_TEST_DSN="postgresql://user:pass@localhost/alfa_eos_test" pytest -v
#
# Verifies: replaying the event_store reproduces claim projections exactly.

import os
import unittest

DSN = os.environ.get("ALFA_EOS_TEST_DSN", "")


@unittest.skipUnless(DSN, "Set ALFA_EOS_TEST_DSN to run DB integration tests")
class TestPGReplayDeterminism(unittest.TestCase):
    """INVARIANT_07: replaying event_store reproduces final claim state."""

    def setUp(self):
        from pygpt_net.core.agents.alfa_eos.db import AlfaEpistemicOS
        self.eos = AlfaEpistemicOS(DSN)
        self.eos.apply_schema()

    def tearDown(self):
        self.eos.close()

    def test_replay_produces_same_claim_state(self):
        claim_id = self.eos.claims.register(
            "replay determinism test", "replay determinism test", "FACT"
        )
        self.eos.claims.transition(claim_id, "VERIFIED")

        # Replay must not report any violation
        report = self.eos.replay.verify(self.eos.db)
        self.assertIsNone(report, f"Replay violation detected: {report}")

    def test_event_store_count_matches_state_mutations(self):
        before_count = self.eos.db.query(
            "SELECT COUNT(*) FROM event_store;"
        )[0][0]

        claim_id = self.eos.claims.register("count test", "count test", "FACT")
        self.eos.claims.transition(claim_id, "VERIFIED")

        after_count = self.eos.db.query(
            "SELECT COUNT(*) FROM event_store;"
        )[0][0]

        # At minimum: CLAIM_CREATED + STATE_TRANSITIONED = 2 new events
        self.assertGreaterEqual(after_count - before_count, 2)


if __name__ == "__main__":
    unittest.main()
