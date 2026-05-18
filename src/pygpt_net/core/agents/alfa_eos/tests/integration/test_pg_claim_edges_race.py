#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ALFA-EOS — integration tests: DAG race condition (PostgreSQL)
#
# Requires a live PostgreSQL instance.
# Set ALFA_EOS_TEST_DSN to run:
#   ALFA_EOS_TEST_DSN="postgresql://user:pass@localhost/alfa_eos_test" pytest -v
#
# Documents: in-memory ArbitrationService is single-threaded (threading.Lock).
# PostgreSQL layer relies on SERIALIZABLE isolation for concurrent DAG inserts.
# This test verifies that A→B and B→A inserted concurrently fail atomically.

import os
import threading
import unittest

DSN = os.environ.get("ALFA_EOS_TEST_DSN", "")


@unittest.skipUnless(DSN, "Set ALFA_EOS_TEST_DSN to run DB integration tests")
class TestConcurrentDAGInsertRace(unittest.TestCase):
    """Two threads simultaneously inserting A→B and B→A: at least one must fail."""

    def setUp(self):
        from pygpt_net.core.agents.alfa_eos.db import AlfaEpistemicOS
        self.eos = AlfaEpistemicOS(DSN)
        self.eos.apply_schema()
        for raw in ("race_claim_a", "race_claim_b"):
            self.eos.claims.register(raw, raw, "FACT")
        self._a = self.eos.claims.calculate_claim_id("race_claim_a")
        self._b = self.eos.claims.calculate_claim_id("race_claim_b")

    def tearDown(self):
        self.eos.close()

    def test_concurrent_cycle_insert_atomic_failure(self):
        errors = []

        def insert_ab():
            try:
                with self.eos.db.epistemic_mutation_context() as cur:
                    cur.execute(
                        "INSERT INTO claim_edges (parent_claim_id, child_claim_id) VALUES (%s, %s);",
                        (self._a, self._b),
                    )
            except Exception as e:
                errors.append(e)

        def insert_ba():
            try:
                with self.eos.db.epistemic_mutation_context() as cur:
                    cur.execute(
                        "INSERT INTO claim_edges (parent_claim_id, child_claim_id) VALUES (%s, %s);",
                        (self._b, self._a),
                    )
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=insert_ab)
        t2 = threading.Thread(target=insert_ba)
        t1.start(); t2.start()
        t1.join(); t2.join()

        # At most one insertion can succeed without creating a cycle
        self.assertGreaterEqual(len(errors), 1, "Expected at least one failure (cycle prevention)")


if __name__ == "__main__":
    unittest.main()
