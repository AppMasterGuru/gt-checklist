"""
Database connection, audit logging, and state transitions.
All state changes are logged to the immutable audit_log table.
DB-layer triggers (schema.sql) enforce the state machine independently.
"""

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).parent
SCHEMA_PATH = _HERE / "schema.sql"
DB_PATH = os.getenv("DB_PATH", str(_HERE.parent / "cotizador.db"))


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Create tables and triggers from schema.sql. Safe to call repeatedly."""
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    with get_connection() as conn:
        conn.executescript(schema)
        # Migrate: add client_email if it doesn't exist yet (added 2026-05-14)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(quotes)").fetchall()}
        if "client_email" not in cols:
            conn.execute("ALTER TABLE quotes ADD COLUMN client_email TEXT")
            conn.commit()
        if "requester_type" not in cols:
            conn.execute(
                "ALTER TABLE quotes ADD COLUMN requester_type TEXT DEFAULT 'cliente'"
            )
            conn.commit()
        if "operation" not in cols:
            conn.execute(
                "ALTER TABLE quotes ADD COLUMN operation TEXT DEFAULT 'exportacion'"
            )
            conn.commit()
        # New tables: providers + credit_registry (added 2026-05-15)
        # schema.sql handles CREATE TABLE IF NOT EXISTS; migrations only needed for
        # columns added to existing tables — these are brand-new tables, so no ALTER needed.


def audit(
    event_type: str,
    quote_reference: str | None,
    actor: str | None,
    detail: dict,
) -> None:
    """Append one record to the immutable audit_log. Never modifies existing rows."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO audit_log (event_type, quote_reference, actor, detail_json)"
            " VALUES (?, ?, ?, ?)",
            (event_type, quote_reference, actor, json.dumps(detail, ensure_ascii=False)),
        )
        conn.commit()


def transition_status(
    quote_id: int,
    new_status: str,
    actor: str,
    notes: str = "",
) -> None:
    """
    Attempt a status transition. The DB trigger in schema.sql will ABORT the
    transaction if the transition is invalid — so this never silently accepts
    illegal moves.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT reference_code, status FROM quotes WHERE id = ?", (quote_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Quote id={quote_id} not found")

        old_status = row["status"]
        ref = row["reference_code"]
        now_iso = datetime.now(timezone.utc).isoformat()

        # Build the SET clause dynamically to only touch what changes
        fields: dict[str, object] = {"status": new_status, "updated_at": now_iso}
        if new_status == "APPROVED":
            fields["approved_by"] = actor
            fields["approved_at"] = now_iso
        elif new_status == "SENT":
            fields["sent_at"] = now_iso
        if notes:
            fields["notes"] = notes

        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [quote_id]
        conn.execute(f"UPDATE quotes SET {set_clause} WHERE id = ?", values)
        conn.commit()

    # Log after successful commit
    audit(
        "STATUS_TRANSITION",
        ref,
        actor,
        {"from": old_status, "to": new_status, "notes": notes},
    )


def get_quote_by_ref(ref: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM quotes WHERE reference_code = ?", (ref,)
        ).fetchone()
    return dict(row) if row else None


def seed_credit_registry(entries: list[dict]) -> int:
    """
    Bulk-insert approved credit registry entries.
    Each dict: {company, category, country, credit_days, condition, credit_line, notes}.
    category: 'local_client' | 'international_agent' | 'service_provider'
    Skips duplicates (same company + category).
    Returns count inserted.
    """
    inserted = 0
    with get_connection() as conn:
        for e in entries:
            company  = (e.get("company") or "").strip()
            category = (e.get("category") or "").strip()
            if not company or not category:
                continue
            exists = conn.execute(
                "SELECT 1 FROM credit_registry WHERE company=? AND category=?",
                (company, category),
            ).fetchone()
            if exists:
                continue
            conn.execute(
                """INSERT INTO credit_registry
                   (company, category, country, credit_days, condition, credit_line, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    company,
                    category,
                    (e.get("country") or ""),
                    e.get("credit_days"),
                    (e.get("condition") or ""),
                    e.get("credit_line"),
                    (e.get("notes") or ""),
                ),
            )
            inserted += 1
        conn.commit()
    return inserted


def get_credit_entry(company: str) -> dict | None:
    """
    Look up a company in the credit registry (case-insensitive, partial match).
    Returns the first matching row or None.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM credit_registry WHERE UPPER(company) LIKE UPPER(?) AND active=1",
            (f"%{company}%",),
        ).fetchone()
    return dict(row) if row else None


def get_credit_registry(category: str | None = None) -> list[dict]:
    """Return all active credit registry entries, optionally filtered by category."""
    with get_connection() as conn:
        if category:
            rows = conn.execute(
                "SELECT * FROM credit_registry WHERE active=1 AND category=? ORDER BY company",
                (category,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM credit_registry WHERE active=1 ORDER BY category, company"
            ).fetchall()
    return [dict(r) for r in rows]


def store_provider_reply(data: dict) -> int:
    """
    Insert one provider reply row. Returns the new row id.
    data keys: quote_reference, provider_name, sender_email, email_subject,
               email_body, flete_usd, visto_bueno_usd, transit_days,
               validity_days, currency, surcharges_json, parse_status,
               raw_extract_json, needs_manual_review.
    """
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO provider_replies
               (quote_reference, provider_name, sender_email, email_subject,
                email_body, flete_usd, visto_bueno_usd, transit_days,
                validity_days, currency, surcharges_json, parse_status,
                raw_extract_json, needs_manual_review)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                data.get("quote_reference"),
                data.get("provider_name") or "Unknown",
                data.get("sender_email"),
                data.get("email_subject"),
                data.get("email_body"),
                data.get("flete_usd"),
                data.get("visto_bueno_usd"),
                data.get("transit_days"),
                data.get("validity_days"),
                data.get("currency") or "USD",
                data.get("surcharges_json"),
                data.get("parse_status") or "parsed",
                data.get("raw_extract_json"),
                1 if data.get("needs_manual_review") else 0,
            ),
        )
        conn.commit()
        return cur.lastrowid


def get_provider_replies(quote_reference: str) -> list[dict]:
    """All provider replies for a quote, ordered by flete_usd ascending (cheapest first)."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM provider_replies
               WHERE quote_reference = ?
               ORDER BY CASE WHEN flete_usd IS NULL THEN 1 ELSE 0 END,
                        flete_usd ASC, created_at ASC""",
            (quote_reference,),
        ).fetchall()
    return [dict(r) for r in rows]


def update_quote_flete(quote_ref: str, flete_usd: float, vb_usd: float | None) -> bool:
    """
    Patch flete_internacional_usd (and optionally visto_bueno_usd) in
    quote's costeo_json and recalculate the cost total.
    Returns True if the quote was found and updated.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT costeo_json FROM quotes WHERE reference_code = ?",
            (quote_ref,),
        ).fetchone()
        if not row:
            return False
        costeo = json.loads(row["costeo_json"] or "{}")
        costeo["flete_internacional_usd"] = round(flete_usd, 2)
        if vb_usd is not None:
            costeo["visto_bueno_usd"] = round(vb_usd, 2)
        costeo["total_usd"] = round(
            costeo.get("flete_internacional_usd", 0)
            + costeo.get("visto_bueno_usd", 0)
            + costeo.get("handling_aereo_usd", 0)
            + costeo.get("customs_agent_usd", 0)
            + costeo.get("transport_usd", 0),
            2,
        )
        conn.execute(
            "UPDATE quotes SET costeo_json = ?, updated_at = ? WHERE reference_code = ?",
            (json.dumps(costeo), datetime.now(timezone.utc).isoformat(), quote_ref),
        )
        conn.commit()
    return True


def get_audit_trail(quote_reference: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE quote_reference = ? ORDER BY ts ASC",
            (quote_reference,),
        ).fetchall()
    return [dict(r) for r in rows]
