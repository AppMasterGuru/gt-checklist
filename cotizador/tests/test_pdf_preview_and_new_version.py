"""
Tests for:
  - GET /quote/<ref>/preview.pdf  — inline proforma preview route
  - POST /quote/<ref>/new-version — clone SENT/REJECTED quote into new PENDING
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

# DB isolation — 'p' > 'c' so test_core.py is imported first and captures DB_PATH.
# Setting here in case this file runs standalone.
_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()
os.environ.setdefault("DB_PATH", _tmp_db.name)

from core.db import get_connection, init_db, transition_status  # noqa: E402


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def fresh_db():
    with get_connection() as conn:
        conn.executescript("""
            DROP TABLE IF EXISTS audit_log;
            DROP TABLE IF EXISTS quotes;
            DROP TABLE IF EXISTS ref_counters;
            DROP TABLE IF EXISTS providers;
            DROP TABLE IF EXISTS provider_replies;
        """)
    init_db()
    yield


@pytest.fixture
def app():
    from api.app import create_app
    a = create_app()
    a.config["TESTING"] = True
    return a


@pytest.fixture
def client(app):
    with app.test_client() as c:
        yield c


# ── Helpers ───────────────────────────────────────────────────────────────────

_VENTA = json.dumps({
    "line_items": [{"description": "Flete", "quantity": 1, "unit_price": 500.0, "total": 500.0}],
    "total_usd": 600.0,
    "margin_pct": 0.20,
    "validity_days": 15,
})
_COSTEO = json.dumps({
    "flete_internacional_usd": 300.0,
    "visto_bueno_usd": 80.0,
    "handling_aereo_usd": 0.0,
    "handling_aereo_detail": {},
    "customs_agent_usd": 70.0,
    "transport_usd": 50.0,
    "transport_soles": 187.5,
    "transport_detail": {},
    "total_usd": 500.0,
    "exchange_rate": 3.75,
    "consolidator": "MSL",
    "airline": None,
    "customs_agent": "Test Agent",
})


def _insert_quote(ref: str, status: str = "PENDING") -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO quotes
              (reference_code, client_name, client_email, incoterm, mode, origin, destination,
               cargo_description, weight_kg, volume_cbm, dimensions_json,
               costeo_json, venta_json, margin_pct, exchange_rate, status, staff_code, language)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'PENDING',?,?)
            """,
            (
                ref, "Test Client", "test@client.com", "FOB", "lcl",
                "Lima, Peru", "Hamburgo, Alemania", "Uvas", 500.0, 2.0,
                json.dumps({"l": 40, "w": 30, "h": 20, "qty": 1}),
                _COSTEO, _VENTA, 0.20, 3.75, "GT-PC", "es",
            ),
        )
        conn.commit()
    if status in ("APPROVED", "SENT", "REJECTED"):
        with get_connection() as conn:
            row = conn.execute(
                "SELECT id FROM quotes WHERE reference_code = ?", (ref,)
            ).fetchone()
        quote_id = row["id"]
        if status == "REJECTED":
            transition_status(quote_id, "REJECTED", "test")
        else:
            transition_status(quote_id, "APPROVED", "test")
            if status == "SENT":
                transition_status(quote_id, "SENT", "test")


# ── PDF preview route ─────────────────────────────────────────────────────────

class TestPdfPreviewRoute:
    def test_returns_200_for_pending_quote(self, client):
        _insert_quote("26-06-PRV-001", "PENDING")
        resp = client.get("/quote/26-06-PRV-001/preview.pdf")
        assert resp.status_code == 200

    def test_returns_content_for_pending(self, client):
        _insert_quote("26-06-PRV-002", "PENDING")
        resp = client.get("/quote/26-06-PRV-002/preview.pdf")
        assert len(resp.data) > 0

    def test_returns_html_fallback_when_no_weasyprint(self, client, monkeypatch):
        import api.routes as routes
        monkeypatch.setattr(routes, "WEASYPRINT_AVAILABLE", False)
        _insert_quote("26-06-PRV-003", "PENDING")
        resp = client.get("/quote/26-06-PRV-003/preview.pdf")
        assert resp.status_code == 200
        assert b"html" in resp.data.lower()

    def test_returns_404_for_unknown_ref(self, client):
        resp = client.get("/quote/26-06-NOEXIST/preview.pdf")
        assert resp.status_code == 404

    def test_margin_override_accepted(self, client):
        _insert_quote("26-06-PRV-004", "PENDING")
        resp = client.get("/quote/26-06-PRV-004/preview.pdf?override_margin_pct=30")
        assert resp.status_code == 200

    def test_route_works_for_sent_quote(self, client):
        _insert_quote("26-06-PRV-005", "SENT")
        resp = client.get("/quote/26-06-PRV-005/preview.pdf")
        assert resp.status_code == 200


class TestPdfPreviewAbsentInTemplate:
    """The pdf-preview iframe must appear only in the PENDING gate."""

    def test_preview_present_on_pending_detail_page(self, client):
        _insert_quote("26-06-PRV-010", "PENDING")
        resp = client.get("/quote/26-06-PRV-010")
        assert b"pdf-preview-wrapper" in resp.data

    def test_preview_absent_on_sent_detail_page(self, client):
        _insert_quote("26-06-PRV-011", "SENT")
        resp = client.get("/quote/26-06-PRV-011")
        assert b"pdf-preview-wrapper" not in resp.data

    def test_preview_absent_on_rejected_detail_page(self, client):
        _insert_quote("26-06-PRV-012", "REJECTED")
        resp = client.get("/quote/26-06-PRV-012")
        assert b"pdf-preview-wrapper" not in resp.data


# ── New version route ─────────────────────────────────────────────────────────

class TestNewVersion:
    def test_new_version_from_sent_redirects(self, client):
        _insert_quote("26-06-NV-001", "SENT")
        resp = client.post(
            "/quote/26-06-NV-001/new-version",
            data={"actor": "abel"},
            follow_redirects=False,
        )
        assert resp.status_code == 302

    def test_new_version_from_rejected_redirects(self, client):
        _insert_quote("26-06-NV-002", "REJECTED")
        resp = client.post(
            "/quote/26-06-NV-002/new-version",
            data={"actor": "abel"},
            follow_redirects=False,
        )
        assert resp.status_code == 302

    def test_new_version_creates_pending_record(self, client):
        _insert_quote("26-06-NV-003", "SENT")
        client.post("/quote/26-06-NV-003/new-version", data={"actor": "abel"},
                    follow_redirects=False)
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT status FROM quotes WHERE reference_code != '26-06-NV-003' "
                "AND client_name = 'Test Client' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        assert rows is not None
        assert rows["status"] == "PENDING"

    def test_new_version_reference_differs_from_original(self, client):
        _insert_quote("26-06-NV-004", "SENT")
        resp = client.post("/quote/26-06-NV-004/new-version", data={"actor": "abel"},
                           follow_redirects=False)
        location = resp.headers.get("Location", "")
        new_ref = location.rstrip("/").split("/quote/")[-1]
        assert new_ref != "26-06-NV-004"

    def test_new_version_logs_version_created(self, client):
        _insert_quote("26-06-NV-005", "SENT")
        client.post("/quote/26-06-NV-005/new-version", data={"actor": "abel"},
                    follow_redirects=False)
        with get_connection() as conn:
            row = conn.execute(
                "SELECT detail_json FROM audit_log WHERE event_type = 'QUOTE_VERSION_CREATED'"
            ).fetchone()
        assert row is not None
        detail = json.loads(row["detail_json"] or "{}")
        assert detail["original_ref"] == "26-06-NV-005"

    def test_new_version_audit_has_new_ref(self, client):
        _insert_quote("26-06-NV-006", "SENT")
        client.post("/quote/26-06-NV-006/new-version", data={"actor": "abel"},
                    follow_redirects=False)
        with get_connection() as conn:
            row = conn.execute(
                "SELECT detail_json FROM audit_log WHERE event_type = 'QUOTE_VERSION_CREATED'"
            ).fetchone()
        detail = json.loads(row["detail_json"] or "{}")
        assert "new_ref" in detail
        assert detail["new_ref"] != "26-06-NV-006"

    def test_new_version_blocked_on_pending_quote(self, client):
        _insert_quote("26-06-NV-007", "PENDING")
        resp = client.post("/quote/26-06-NV-007/new-version", data={"actor": "abel"},
                           follow_redirects=False)
        assert resp.status_code == 302
        assert "new-version" not in resp.headers.get("Location", "")

    def test_new_version_button_present_on_sent_page(self, client):
        _insert_quote("26-06-NV-008", "SENT")
        resp = client.get("/quote/26-06-NV-008")
        assert b"nueva versi" in resp.data

    def test_new_version_button_present_on_rejected_page(self, client):
        _insert_quote("26-06-NV-009", "REJECTED")
        resp = client.get("/quote/26-06-NV-009")
        assert b"nueva versi" in resp.data
