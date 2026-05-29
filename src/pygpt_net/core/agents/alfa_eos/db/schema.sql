-- ===================================================================
-- ALFA-EOS PERSISTENCE LAYER — v0.3.1
-- PostgreSQL Event Store + JSONB Projection Schema
-- ===================================================================
-- Ordering: event_store → claim_state → sources → evidence
--           → claim_edges → snapshots → execution_permissions
-- ===================================================================

-- ---------------------------------------------------------------------------
-- 0. Extensions
-- ---------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS "pgcrypto";   -- gen_random_uuid()

-- ---------------------------------------------------------------------------
-- 1. Shared guard functions
-- ---------------------------------------------------------------------------

-- Blocks direct mutation of projection tables outside of replay context
CREATE OR REPLACE FUNCTION fn_check_epistemic_write_authorization()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF current_setting('alfa_eos.replay_context', true) IS DISTINCT FROM 'true' THEN
        RAISE EXCEPTION
            'Epistemic Mutation Error [%]: Write operations on projection table "%" '
            'are restricted. Mutate via Event Store Replay only.',
            TG_OP, TG_TABLE_NAME;
    END IF;
    RETURN NEW;
END;
$$;

-- Blocks UPDATE/DELETE on append-only tables (event_store, snapshots)
CREATE OR REPLACE FUNCTION fn_event_store_immutable_gate()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION
        'Class A Integrity Failure [%]: Table "%" is append-only. '
        'UPDATE or DELETE operations are structural sabotage.',
        TG_OP, TG_TABLE_NAME;
END;
$$;

-- ---------------------------------------------------------------------------
-- 2. event_store — single source of truth (append-only)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS event_store (
    event_id        UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type      TEXT        NOT NULL
                    CHECK (event_type IN (
                        'CLAIM_CREATED', 'CLAIM_NORMALIZED',
                        'EVIDENCE_LINKED', 'EVIDENCE_REJECTED',
                        'STATE_TRANSITIONED',
                        'ARBITRATION_STARTED', 'ARBITRATION_RESOLVED', 'ARBITRATION_TIMEOUT',
                        'DRIFT_DETECTED',
                        'SNAPSHOT_CREATED',
                        'EXECUTION_GRANTED', 'EXECUTION_DENIED', 'EXECUTION_PERMISSION_EXPIRED',
                        'DEPENDENCY_ADDED',
                        'POLICY_CHANGED',
                        'INVARIANT_VIOLATED'
                    )),
    stream_id       UUID        NOT NULL,
    causation_id    UUID        REFERENCES event_store(event_id),
    correlation_id  UUID        NOT NULL,
    claim_id        TEXT,                        -- SHA-256[:16] from CLAIM_SERVICE
    agent_id        TEXT        NOT NULL,
    schema_version  TEXT        NOT NULL DEFAULT '1.0',
    payload         JSONB       NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TRIGGER trg_event_store_no_update
    BEFORE UPDATE ON event_store
    FOR EACH ROW EXECUTE FUNCTION fn_event_store_immutable_gate();

CREATE TRIGGER trg_event_store_no_delete
    BEFORE DELETE ON event_store
    FOR EACH ROW EXECUTE FUNCTION fn_event_store_immutable_gate();

-- Optimised indexes: jsonb_path_ops reduces GIN size ~60% for path queries
CREATE INDEX IF NOT EXISTS ix_es_claim_id
    ON event_store (claim_id) WHERE claim_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_es_event_type   ON event_store (event_type);
CREATE INDEX IF NOT EXISTS ix_es_stream_id    ON event_store (stream_id);
CREATE INDEX IF NOT EXISTS ix_es_created_at   ON event_store (created_at);
CREATE INDEX IF NOT EXISTS ix_es_payload_path
    ON event_store USING GIN (payload jsonb_path_ops);

-- ---------------------------------------------------------------------------
-- 3. claim_state — deterministic projection of event_store
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS claim_state (
    claim_id            TEXT         PRIMARY KEY,    -- SHA-256(canonical_form)[:16]
    canonical_form      TEXT         NOT NULL,
    raw_variants        TEXT[]       NOT NULL DEFAULT '{}',
    claim_type          TEXT         NOT NULL
                        CHECK (claim_type IN (
                            'FACT', 'HYPOTHESIS', 'INFERENCE', 'CONFLICT', 'GAP', 'DECISION'
                        )),
    status              TEXT         NOT NULL DEFAULT 'UNVERIFIED'
                        CHECK (status IN (
                            'UNVERIFIED', 'EVIDENCE_REQUIRED', 'PARTIAL',
                            'VERIFIED', 'CONFLICT', 'REFUTED'
                        )),
    confidence          NUMERIC(5,4) NOT NULL DEFAULT 0.0
                        CHECK (confidence BETWEEN 0.0 AND 1.0),
    verified_at         TIMESTAMPTZ,
    valid_until         TIMESTAMPTZ,
    revalidation_policy TEXT,
    meta                JSONB        NOT NULL DEFAULT '{}',
    last_event_id       UUID         REFERENCES event_store(event_id),
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TRIGGER trg_cs_event_only
    BEFORE INSERT OR UPDATE OR DELETE ON claim_state
    FOR EACH ROW EXECUTE FUNCTION fn_check_epistemic_write_authorization();

CREATE INDEX IF NOT EXISTS ix_cs_status     ON claim_state (status);
CREATE INDEX IF NOT EXISTS ix_cs_confidence ON claim_state (confidence);
CREATE INDEX IF NOT EXISTS ix_cs_meta_gin   ON claim_state USING GIN (meta);

-- ---------------------------------------------------------------------------
-- 4. sources & evidence
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sources (
    source_id        UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type      TEXT         NOT NULL,
    location         TEXT         NOT NULL,
    trust_profile    NUMERIC(4,3) NOT NULL CHECK (trust_profile BETWEEN 0.0 AND 1.0),
    domain_class     TEXT         NOT NULL DEFAULT 'general',
    freshness_window INTERVAL     NOT NULL DEFAULT '7 days',
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS evidence (
    evidence_id  UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    claim_id     TEXT         NOT NULL REFERENCES claim_state(claim_id) ON DELETE CASCADE,
    source_id    UUID         NOT NULL REFERENCES sources(source_id),
    support_type TEXT         NOT NULL
                 CHECK (support_type IN ('SUPPORTS', 'REFUTES', 'NEUTRAL')),
    weight       NUMERIC(4,3) NOT NULL CHECK (weight BETWEEN 0.0 AND 1.0),
    freshness    NUMERIC(4,3) NOT NULL CHECK (freshness BETWEEN 0.0 AND 1.0),
    consistency  NUMERIC(4,3) NOT NULL CHECK (consistency BETWEEN 0.0 AND 1.0),
    corroboration NUMERIC(4,3) NOT NULL DEFAULT 0.5,
    event_id     UUID         REFERENCES event_store(event_id),
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_ev_claim_id    ON evidence (claim_id);
CREATE INDEX IF NOT EXISTS ix_ev_source_id   ON evidence (source_id);
CREATE INDEX IF NOT EXISTS ix_ev_support     ON evidence (support_type);

-- Materialised confidence view (Fix #1 — avoids linear scan on large sets)
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_claim_confidence AS
SELECT
    claim_id,
    ROUND(
        COALESCE(
            SUM(weight * freshness * consistency) FILTER (WHERE support_type = 'SUPPORTS')
            / NULLIF(SUM(weight) FILTER (WHERE support_type = 'SUPPORTS'), 0),
            0
        )
        -
        COALESCE(
            SUM(weight * freshness * consistency) FILTER (WHERE support_type = 'REFUTES')
            / NULLIF(SUM(weight) FILTER (WHERE support_type = 'REFUTES'), 0),
            0
        ),
        4
    ) AS confidence,
    COUNT(*) FILTER (WHERE support_type = 'SUPPORTS') AS support_count,
    COUNT(*) FILTER (WHERE support_type = 'REFUTES')  AS refute_count,
    COUNT(DISTINCT source_id) FILTER (WHERE support_type = 'SUPPORTS') AS unique_support_sources
FROM evidence
GROUP BY claim_id
WITH DATA;

CREATE UNIQUE INDEX IF NOT EXISTS ix_mv_cc_claim_id ON mv_claim_confidence (claim_id);

-- ---------------------------------------------------------------------------
-- 5. claim_edges — DAG with cycle prevention (Fix #2)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS claim_edges (
    parent_claim_id TEXT        NOT NULL REFERENCES claim_state(claim_id),
    child_claim_id  TEXT        NOT NULL REFERENCES claim_state(claim_id),
    edge_type       TEXT        NOT NULL DEFAULT 'DEPENDS_ON',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (parent_claim_id, child_claim_id),
    CONSTRAINT chk_no_self_loop CHECK (parent_claim_id <> child_claim_id)
);

CREATE TRIGGER trg_ce_event_only
    BEFORE INSERT OR UPDATE OR DELETE ON claim_edges
    FOR EACH ROW EXECUTE FUNCTION fn_check_epistemic_write_authorization();

CREATE INDEX IF NOT EXISTS ix_ce_child ON claim_edges (child_claim_id);

-- Recursive cycle detection trigger (Fix #2 — guards against N-length cycles)
CREATE OR REPLACE FUNCTION fn_prevent_epistemic_graph_cycles()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    -- Walk backwards from NEW.parent_claim_id; if we can reach NEW.child_claim_id
    -- it means adding this edge would close a cycle.
    IF EXISTS (
        WITH RECURSIVE graph_trace AS (
            SELECT parent_claim_id AS node
            FROM   claim_edges
            WHERE  child_claim_id = NEW.parent_claim_id

            UNION

            SELECT ce.parent_claim_id
            FROM   claim_edges ce
            JOIN   graph_trace  gt ON ce.child_claim_id = gt.node
        )
        SELECT 1 FROM graph_trace WHERE node = NEW.child_claim_id
    ) THEN
        RAISE EXCEPTION
            'Class B Epistemic Failure: Cycle detected in CLAIM_DEPENDENCY_GRAPH '
            '(% → %). Aborted to prevent cascading stack overflow.',
            NEW.parent_claim_id, NEW.child_claim_id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_ce_prevent_cycle
    BEFORE INSERT ON claim_edges
    FOR EACH ROW EXECUTE FUNCTION fn_prevent_epistemic_graph_cycles();

-- ---------------------------------------------------------------------------
-- 6. snapshots — immutable, hash-chained checkpoints
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    schema_version      TEXT        NOT NULL,
    previous_hash       TEXT,                   -- sha256 of previous snapshot payload
    snapshot_hash       TEXT        NOT NULL UNIQUE,
    claim_ids           TEXT[]      NOT NULL,
    gap_claim_ids       TEXT[]      NOT NULL DEFAULT '{}',
    policy_context      JSONB       NOT NULL,
    drift_report        JSONB,
    arbitration_history JSONB       NOT NULL DEFAULT '[]',
    event_id            UUID        REFERENCES event_store(event_id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TRIGGER trg_snapshots_no_update
    BEFORE UPDATE ON snapshots
    FOR EACH ROW EXECUTE FUNCTION fn_event_store_immutable_gate();

CREATE TRIGGER trg_snapshots_no_delete
    BEFORE DELETE ON snapshots
    FOR EACH ROW EXECUTE FUNCTION fn_event_store_immutable_gate();

-- ---------------------------------------------------------------------------
-- 7. execution_permissions — derived table, never manually edited (Fix #3)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS execution_permissions (
    permission_id UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    claim_id      TEXT         NOT NULL REFERENCES claim_state(claim_id),
    granted       BOOLEAN      NOT NULL,
    risk_score    NUMERIC(4,3) NOT NULL CHECK (risk_score BETWEEN 0.0 AND 1.0),
    reason        TEXT         NOT NULL,
    policy_domain TEXT         NOT NULL,
    provenance    JSONB        NOT NULL DEFAULT '{}',
    expires_at    TIMESTAMPTZ  NOT NULL,        -- Fix #3: mandatory expiry
    revoked_at    TIMESTAMPTZ,
    revoked_reason TEXT,
    event_id      UUID         REFERENCES event_store(event_id),
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TRIGGER trg_ep_event_only
    BEFORE INSERT OR UPDATE OR DELETE ON execution_permissions
    FOR EACH ROW EXECUTE FUNCTION fn_check_epistemic_write_authorization();

-- Partial index — fast gate check for valid active grants
CREATE INDEX IF NOT EXISTS ix_ep_active_grants
    ON execution_permissions (claim_id, granted)
    WHERE granted = true AND revoked_at IS NULL;

-- Expired grants auto-revocation (run periodically by a maintenance job)
CREATE OR REPLACE FUNCTION fn_revoke_expired_permissions()
RETURNS void LANGUAGE plpgsql AS $$
DECLARE r RECORD;
BEGIN
    PERFORM set_config('alfa_eos.replay_context', 'true', true);
    FOR r IN
        SELECT permission_id, claim_id
        FROM   execution_permissions
        WHERE  granted = true AND revoked_at IS NULL AND expires_at < now()
    LOOP
        -- Emit event FIRST (event-sourcing purity: log before projection mutation)
        INSERT INTO event_store (event_type, stream_id, correlation_id, claim_id, agent_id, payload)
        VALUES (
            'EXECUTION_PERMISSION_EXPIRED',
            gen_random_uuid(),
            gen_random_uuid(),
            r.claim_id,
            'EXPIRY_SWEEP',
            jsonb_build_object('permission_id', r.permission_id)
        );

        -- Then update the projection row
        UPDATE execution_permissions
        SET    granted        = false,
               revoked_at     = now(),
               revoked_reason = 'TIMEOUT_FALLBACK: Permission expired.'
        WHERE  permission_id = r.permission_id;
    END LOOP;
END;
$$;

-- DAG cascading revocation: degraded parent → revoke all downstream grants
CREATE OR REPLACE FUNCTION fn_cascade_revocation_on_parent_degrade()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status IN ('CONFLICT', 'REFUTED')
       AND OLD.status NOT IN ('CONFLICT', 'REFUTED') THEN

        PERFORM set_config('alfa_eos.replay_context', 'true', true);

        UPDATE execution_permissions ep
        SET    granted        = false,
               revoked_at     = now(),
               revoked_reason = 'CASCADE_REVOCATION: parent claim [' || NEW.claim_id
                                || '] degraded to ' || NEW.status
        WHERE  ep.claim_id IN (
            WITH RECURSIVE downstream AS (
                SELECT child_claim_id AS node
                FROM   claim_edges
                WHERE  parent_claim_id = NEW.claim_id

                UNION

                SELECT ce.child_claim_id
                FROM   claim_edges ce
                JOIN   downstream dn ON ce.parent_claim_id = dn.node
            )
            SELECT node FROM downstream
        )
        AND ep.granted = true
        AND ep.revoked_at IS NULL;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_cascade_revocation
    AFTER UPDATE ON claim_state
    FOR EACH ROW EXECUTE FUNCTION fn_cascade_revocation_on_parent_degrade();

-- ---------------------------------------------------------------------------
-- 8. Replay procedure
-- ---------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE proc_replay_from_snapshot(
    p_snapshot_id      UUID,
    p_schema_version   TEXT DEFAULT '1.0'
)
LANGUAGE plpgsql AS $$
DECLARE
    v_snapshot_ts TIMESTAMPTZ;
BEGIN
    PERFORM set_config('alfa_eos.replay_context', 'true', true);

    SELECT created_at INTO v_snapshot_ts
    FROM snapshots WHERE snapshot_id = p_snapshot_id;

    IF v_snapshot_ts IS NULL THEN
        RAISE EXCEPTION 'Snapshot % not found.', p_snapshot_id;
    END IF;

    -- Apply CLAIM_CREATED events
    INSERT INTO claim_state (
        claim_id, canonical_form, raw_variants, claim_type, status,
        last_event_id, created_at, updated_at
    )
    SELECT
        payload->>'claim_id',
        payload->>'canonical_form',
        ARRAY[payload->>'raw_variants'],
        COALESCE(payload->>'type', 'FACT'),
        'UNVERIFIED',
        event_id,
        created_at,
        created_at
    FROM event_store
    WHERE event_type = 'CLAIM_CREATED'
      AND created_at >= v_snapshot_ts
      AND schema_version = p_schema_version
    ON CONFLICT (claim_id) DO NOTHING;

    -- Apply STATE_TRANSITIONED events (in order)
    UPDATE claim_state cs
    SET    status        = es.payload->>'to_status',
           last_event_id = es.event_id,
           updated_at    = es.created_at
    FROM (
        SELECT DISTINCT ON (claim_id)
               claim_id, payload, event_id, created_at
        FROM   event_store
        WHERE  event_type = 'STATE_TRANSITIONED'
          AND  created_at >= v_snapshot_ts
          AND  schema_version = p_schema_version
        ORDER BY claim_id, created_at DESC
    ) es
    WHERE cs.claim_id = es.claim_id;

    -- Apply latest confidence from materialised view
    UPDATE claim_state cs
    SET    confidence  = mv.confidence,
           updated_at  = now()
    FROM   mv_claim_confidence mv
    WHERE  cs.claim_id = mv.claim_id;
END;
$$;

-- ---------------------------------------------------------------------------
-- 9. Integrity verification function (INVARIANT_07)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_verify_replay_integrity(p_snapshot_id UUID)
RETURNS BOOLEAN LANGUAGE plpgsql AS $$
DECLARE
    v_current_hash TEXT;
    v_snapshot_hash TEXT;
BEGIN
    SELECT md5(string_agg(
        claim_id || ':' || status || ':' || confidence::TEXT,
        ',' ORDER BY claim_id
    ))
    INTO v_current_hash
    FROM claim_state;

    SELECT snapshot_hash INTO v_snapshot_hash
    FROM snapshots WHERE snapshot_id = p_snapshot_id;

    RETURN v_current_hash IS NOT DISTINCT FROM v_snapshot_hash;
END;
$$;
