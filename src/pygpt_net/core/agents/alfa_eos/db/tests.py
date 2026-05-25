#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ALFA-EOS — DB persistence integration tests
#
# Requires a live PostgreSQL instance.
# Set env var ALFA_EOS_TEST_DSN to run:
#   ALFA_EOS_TEST_DSN="postgresql://user:pass@localhost/alfa_eos_test" pytest db/tests.py -v

import os
import unittest

DSN = os.environ.get("ALFA_EOS_TEST_DSN", "")


@unittest.skipUnless(DSN, "Set ALFA_EOS_TEST_DSN to run DB integration tests")
class TestEventStoreImmutability(unittest.TestCase):
    """INVARIANT_06: event_store is append-only."""

    def setUp(self) -> None:
        from . import AlfaEpistemicOS
        self.eos = AlfaEpistemicOS(DSN)
        self.eos.apply_schema()

    def tearDown(self) -> None:
        self.eos.close()

    def test_delete_blocked(self) -> None:
        with self.assertRaises(Exception) as ctx:
            self.eos.db.query("DELETE FROM event_store;")
        self.assertIn("append-only", str(ctx.exception).lower())

    def test_update_blocked(self) -> None:
        with self.assertRaises(Exception) as ctx:
            self.eos.db.query(
                "UPDATE event_store SET agent_id = 'hacked' WHERE TRUE;"
            )
        self.assertIn("append-only", str(ctx.exception).lower())


@unittest.skipUnless(DSN, "Set ALFA_EOS_TEST_DSN to run DB integration tests")
class TestDAGCyclePrevention(unittest.TestCase):
    """Fix #2: CLAIM_DEPENDENCY_GRAPH must reject cycles."""

    def setUp(self) -> None:
        from . import AlfaEpistemicOS
        self.eos = AlfaEpistemicOS(DSN)
        self.eos.apply_schema()
        # Pre-create three dummy claims needed by FK constraints
        for raw in ("claim_a", "claim_b", "claim_c"):
            self.eos.claims.register(raw, raw, "FACT")
        self._a = self.eos.claims.calculate_claim_id("claim_a")
        self._b = self.eos.claims.calculate_claim_id("claim_b")
        self._c = self.eos.claims.calculate_claim_id("claim_c")

    def tearDown(self) -> None:
        self.eos.close()

    def test_cycle_rejected(self) -> None:
        with self.eos.db.epistemic_mutation_context() as cur:
            cur.execute(
                "INSERT INTO claim_edges (parent_claim_id, child_claim_id) VALUES (%s, %s);",
                (self._a, self._b),
            )
            cur.execute(
                "INSERT INTO claim_edges (parent_claim_id, child_claim_id) VALUES (%s, %s);",
                (self._b, self._c),
            )
            with self.assertRaises(Exception) as ctx:
                cur.execute(
                    "INSERT INTO claim_edges (parent_claim_id, child_claim_id) VALUES (%s, %s);",
                    (self._c, self._a),
                )
            self.assertIn("cycle", str(ctx.exception).lower())

    def test_direct_self_loop_rejected(self) -> None:
        with self.assertRaises(Exception):
            with self.eos.db.epistemic_mutation_context() as cur:
                cur.execute(
                    "INSERT INTO claim_edges (parent_claim_id, child_claim_id) VALUES (%s, %s);",
                    (self._a, self._a),
                )


@unittest.skipUnless(DSN, "Set ALFA_EOS_TEST_DSN to run DB integration tests")
class TestExecutionGate(unittest.TestCase):
    """INVARIANT_01: execution denied for non-VERIFIED claims."""

    def setUp(self) -> None:
        from . import AlfaEpistemicOS
        self.eos = AlfaEpistemicOS(DSN)
        self.eos.apply_schema()

    def tearDown(self) -> None:
        self.eos.close()

    def test_unverified_denied(self) -> None:
        claim_id = self.eos.claims.register(
            "server is down", "server is down", "FACT"
        )
        granted = self.eos.request_execution_permission(claim_id, "business", 0.5)
        self.assertFalse(granted)

    def test_verified_granted(self) -> None:
        claim_id = self.eos.claims.register(
            "payment confirmed", "payment confirmed", "FACT"
        )
        self.eos.claims.transition(claim_id, "VERIFIED")
        granted = self.eos.request_execution_permission(claim_id, "banking", 0.2)
        self.assertTrue(granted)


if __name__ == "__main__":
    unittest.main()
