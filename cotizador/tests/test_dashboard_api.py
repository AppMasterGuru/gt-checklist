"""
Tests for GET /api/dashboard/quotes — the toast-polling JSON endpoint.
"""

from __future__ import annotations

import os
import tempfile

import pytest

# ── DB isolation (must patch before importing app modules) ────────────────────
_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()
os.environ["DB_PATH"] = _tmp_db.name

from core.db import get_connection, init_db  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    with get_connection() as conn:
        conn.executescript("""
            DROP TABLE IF EXISTS audit_log;
            DROP TABLE IF EXISTS quotes;
            DROP TABLE IF EXISTS ref_counters;
        """)
    init_db()
    yield


@pytest.fixture
def client():
    from api.app import create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _insert_quote(ref, client_name, status="PENDING"):
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO quotes
              (reference_code, client_name, incoterm, mode, origin, destination,
               cargo_description, weight_kg, volume_cbm, dimensions_json,
               costeo_json, venta_json, margin_pct, exchange_rate,
               status, staff_code, language)
            VALUES (?,?,'FOB','lcl','Lima','Miami','test cargo',100,1,'{}','{}','{}',
                    0.20,3.72,?,'GT-PC','es')
            """,
            (ref, client_name, status),
        )
        conn.commit()


def test_api_dashboard_quotes_empty(client):
    rv = client.get("/api/dashboard/quotes")
    assert rv.status_code == 200
    assert rv.get_json() == []


def test_api_dashboard_quotes_returns_quotes(client):
    _insert_quote("26-06-001 Acme EXW GT-PC", "Acme Corp")
    rv = client.get("/api/dashboard/quotes")
    assert rv.status_code == 200
    data = rv.get_json()
    assert len(data) == 1
    q = data[0]
    assert q["reference_code"] == "26-06-001 Acme EXW GT-PC"
    assert q["client_name"] == "Acme Corp"
    assert q["status"] == "PENDING"
    assert "id" in q
    assert "created_at" in q


def test_api_dashboard_quotes_multiple_statuses(client):
    _insert_quote("26-06-001 A EXW GT-PC", "Client A", "PENDING")
    _insert_quote("26-06-002 B EXW GT-PC", "Client B", "APPROVED")
    _insert_quote("26-06-003 C EXW GT-PC", "Client C", "SENT")
    rv = client.get("/api/dashboard/quotes")
    assert rv.status_code == 200
    data = rv.get_json()
    assert len(data) == 3
    statuses = {q["status"] for q in data}
    assert statuses == {"PENDING", "APPROVED", "SENT"}


def test_api_dashboard_quotes_content_type_json(client):
    rv = client.get("/api/dashboard/quotes")
    assert "application/json" in rv.content_type
