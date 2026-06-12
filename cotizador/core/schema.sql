-- GT Cotizador — core schema
-- BASC/ISO 9001 compliance: immutable audit log, DB-layer state machine
-- SQLite 3.x

-- ──────────────────────────────────────────────
-- Sequential counter for reference codes (atomic)
-- ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ref_counters (
    year_month TEXT PRIMARY KEY,   -- e.g. '2605' = May 2026
    last_seq   INTEGER NOT NULL DEFAULT 0
);

-- ──────────────────────────────────────────────
-- Quotes
-- ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS quotes (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    reference_code    TEXT    UNIQUE NOT NULL,
    client_name       TEXT    NOT NULL,
    client_email      TEXT,             -- populated when known; required for auto-send
    incoterm          TEXT    NOT NULL,
    mode              TEXT    NOT NULL CHECK(mode IN ('aereo', 'lcl', 'fcl')),
    origin            TEXT    NOT NULL,
    destination       TEXT    NOT NULL,
    cargo_description TEXT,
    weight_kg         REAL,
    volume_cbm        REAL,
    dimensions_json   TEXT,          -- {l, w, h, qty} in cm
    costeo_json       TEXT,          -- INTERNAL — never sent to client
    venta_json        TEXT,          -- sell-side data → goes to client
    margin_pct        REAL,
    exchange_rate     REAL,          -- SBS USD→PEN rate at creation time
    status            TEXT    NOT NULL DEFAULT 'PENDING'
                              CHECK(status IN ('PENDING','APPROVED','REJECTED','SENT')),
    staff_code        TEXT    NOT NULL,
    language          TEXT    DEFAULT 'es',
    requester_type    TEXT    DEFAULT 'cliente',  -- 'cliente' | 'agente'
    operation         TEXT    DEFAULT 'exportacion', -- 'exportacion' | 'importacion'
    created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    approved_by       TEXT,
    approved_at       TEXT,
    sent_at           TEXT,
    notes             TEXT
);

-- ──────────────────────────────────────────────
-- Immutable audit log
-- Triggers below prevent UPDATE and DELETE.
-- ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type      TEXT    NOT NULL,
    quote_reference TEXT,
    actor           TEXT,
    detail_json     TEXT,
    ts              TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ──────────────────────────────────────────────
-- State machine: valid transitions only
--   PENDING  → APPROVED | REJECTED
--   APPROVED → SENT     | REJECTED
--   REJECTED, SENT are terminal
-- ──────────────────────────────────────────────
CREATE TRIGGER IF NOT EXISTS enforce_state_transition
BEFORE UPDATE OF status ON quotes
BEGIN
    SELECT CASE
        WHEN OLD.status = 'PENDING'  AND NEW.status NOT IN ('APPROVED','REJECTED')
            THEN RAISE(ABORT, 'Invalid transition: PENDING may only go to APPROVED or REJECTED')
        WHEN OLD.status = 'APPROVED' AND NEW.status NOT IN ('SENT','REJECTED')
            THEN RAISE(ABORT, 'Invalid transition: APPROVED may only go to SENT or REJECTED')
        WHEN OLD.status IN ('REJECTED','SENT')
            THEN RAISE(ABORT, 'Invalid transition: REJECTED and SENT are terminal states')
    END;
END;

-- ──────────────────────────────────────────────
-- Audit log immutability
-- ──────────────────────────────────────────────
CREATE TRIGGER IF NOT EXISTS protect_audit_log_update
BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is immutable — UPDATE not permitted');
END;

CREATE TRIGGER IF NOT EXISTS protect_audit_log_delete
BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is immutable — DELETE not permitted');
END;

-- ──────────────────────────────────────────────
-- Provider contact directory
-- Seeded from DATA COLOADERS.xlsx (SharePoint)
-- Used by provider_emails.py for real To: addresses
-- ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS providers (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    company      TEXT    NOT NULL,         -- MSL, CRAFT, SACO, VANGUARD, ECU WORLDWIDE
    contact_name TEXT,
    role         TEXT,
    email        TEXT,
    phone        TEXT,
    service_type TEXT,                     -- lcl_impo, lcl_expo, air_impo, air_expo, general
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ──────────────────────────────────────────────
-- ──────────────────────────────────────────────
-- Provider rate replies
-- One row per provider reply received for a quote.
-- Multiple providers can reply to the same quote_reference.
-- parse_status: 'parsed' | 'parse_failed' | 'manual_review'
-- ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS provider_replies (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_reference     TEXT    NOT NULL,
    provider_name       TEXT    NOT NULL,
    sender_email        TEXT,
    email_subject       TEXT,
    email_body          TEXT,
    flete_usd           REAL,
    visto_bueno_usd     REAL,
    transit_days        INTEGER,
    validity_days       INTEGER,
    currency            TEXT    DEFAULT 'USD',
    surcharges_json     TEXT,
    parse_status        TEXT    NOT NULL DEFAULT 'parsed',
    raw_extract_json    TEXT,
    needs_manual_review INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ──────────────────────────────────────────────
-- Approved credit registry
-- Seeded from LISTA CRÉDITOS.xlsx (SharePoint)
-- Tracks GT's approved counterparties and credit terms
-- ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS credit_registry (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    company     TEXT    NOT NULL,
    category    TEXT    NOT NULL,          -- local_client, international_agent, service_provider
    country     TEXT,
    credit_days INTEGER,
    condition   TEXT,
    credit_line INTEGER,                   -- optional credit limit (some agents)
    notes       TEXT,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
