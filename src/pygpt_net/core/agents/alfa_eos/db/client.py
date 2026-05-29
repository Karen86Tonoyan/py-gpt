#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ALFA-EOS — PostgreSQL DB Client

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, List, Optional

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    _PSYCOPG2_AVAILABLE = True
except ImportError:
    _PSYCOPG2_AVAILABLE = False


class EpistemicDBClient:
    """
    Thin wrapper around a psycopg2 connection that enforces the
    alfa_eos.replay_context session variable before any projection mutation.

    All writes to claim_state / claim_edges / execution_permissions / snapshots
    MUST go through epistemic_mutation_context() — the PostgreSQL triggers will
    reject any attempt that bypasses it.
    """

    def __init__(self, dsn: str) -> None:
        if not _PSYCOPG2_AVAILABLE:
            raise ImportError(
                "psycopg2 is required for the DB persistence layer. "
                "Install it with: pip install psycopg2-binary"
            )
        self.dsn = dsn
        self._conn = psycopg2.connect(dsn, cursor_factory=RealDictCursor)
        self._conn.autocommit = False

    @contextmanager
    def epistemic_mutation_context(self) -> Iterator[Any]:
        """
        Context manager that sets alfa_eos.replay_context = 'true' for the
        duration of the transaction, commits on success, rolls back on failure.

        Usage:
            with client.epistemic_mutation_context() as cursor:
                cursor.execute(sql, params)
        """
        cursor = self._conn.cursor()
        try:
            cursor.execute("SET LOCAL alfa_eos.replay_context = 'true';")
            yield cursor
            self._conn.commit()
        except Exception as exc:
            self._conn.rollback()
            raise RuntimeError(
                f"Class A Integrity Failure during epistemic DB mutation: {exc}"
            ) from exc
        finally:
            cursor.close()

    def query(self, sql: str, params: Optional[tuple] = None) -> Optional[List[Any]]:
        """Read-only query helper. Does NOT set the replay_context flag."""
        with self._conn.cursor() as cursor:
            cursor.execute(sql, params)
            if cursor.description:
                return cursor.fetchall()
            return None

    def apply_schema(self, schema_sql_path: str) -> None:
        """
        Execute the DDL from schema.sql against the connected database.
        Safe to call multiple times — all CREATE statements use IF NOT EXISTS.
        """
        with open(schema_sql_path, "r", encoding="utf-8") as f:
            ddl = f.read()
        with self._conn.cursor() as cursor:
            cursor.execute("SET LOCAL alfa_eos.replay_context = 'true';")
            cursor.execute(ddl)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
