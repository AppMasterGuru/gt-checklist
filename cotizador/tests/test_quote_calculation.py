"""
Tests for quote calculation fixes from Abel demo 2026-06-09:
  - Margin applied to each venta line item
  - venta_total = sum of line items
  - 10% margin floor (GT-P-001)
  - LCL W/M factor columns
  - requester_type field
  - /acuses route
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()
os.environ.setdefault("DB_PATH", _tmp_db.name)

from core.db import get_connection, init_db  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    with get_connection() as conn:
        conn.executescript("""
            DROP TABLE IF EXISTS audit_log;
            DROP TABLE IF EXISTS quotes;
            DROP TABLE IF EXISTS ref_counters;
            DROP TABLE IF EXISTS providers;
            DROP TABLE IF EXISTS provider_replies;
            DROP TABLE IF EXISTS credit_registry;
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

_BASE_FORM = {
    "client_name": "Test Shipper SA",
    "client_email": "test@shipper.com",
    "mode": "lcl",
    "incoterm": "FOB",
    "origin": "Lima, Peru",
    "destination": "Hamburg, Germany",
    "cargo_description": "Test cargo",
    "weight": "500",
    "weight_unit": "kg",
    "volume_cbm": "2.0",
    "flete_lcl": "200.00",
    "consolidator": "MSL",
    "staff_code": "GT-PC",
    "language": "es",
    "requester_type": "cliente",
}


def _post_quote(client, overrides=None):
    from urllib.parse import unquote
    data = {**_BASE_FORM, **(overrides or {})}
    resp = client.post("/quote/new", data=data, follow_redirects=False)
    assert resp.status_code == 302
    ref = unquote(resp.headers["Location"].rstrip("/").split("/quote/")[-1])
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM quotes WHERE reference_code = ?", (ref,)
        ).fetchone()
    assert row is not None, f"Quote not found for ref={ref!r}"
    return dict(row)


# ── Margin math ───────────────────────────────────────────────────────────────

class TestVentaLineItemsScaleWithMargin:
    def test_25pct_margin_each_item_scaled(self, client):
        q = _post_quote(client, {"margin_pct": "25", "flete_lcl": "100"})
        venta = json.loads(q["venta_json"])
        flete_item = venta["line_items"][0]
        assert flete_item["total"] == pytest.approx(125.0, rel=0.01)

    def test_35pct_margin_each_item_scaled(self, client):
        q = _post_quote(client, {"margin_pct": "35", "flete_lcl": "100"})
        venta = json.loads(q["venta_json"])
        flete_item = venta["line_items"][0]
        assert flete_item["total"] == pytest.approx(135.0, rel=0.01)

    def test_50pct_margin_each_item_scaled(self, client):
        q = _post_quote(client, {"margin_pct": "50", "flete_lcl": "100"})
        venta = json.loads(q["venta_json"])
        flete_item = venta["line_items"][0]
        assert flete_item["total"] == pytest.approx(150.0, rel=0.01)

    def test_venta_total_equals_sum_of_items(self, client):
        q = _post_quote(client, {"margin_pct": "30", "flete_lcl": "300"})
        venta = json.loads(q["venta_json"])
        item_sum = round(sum(i["total"] for i in venta["line_items"]), 2)
        assert venta["total_usd"] == item_sum

    def test_line_items_not_raw_costeo_amounts(self, client):
        # MARGIN_FLOOR defaults to 20%; any quote must have items > costeo
        q = _post_quote(client, {"flete_lcl": "100"})
        venta = json.loads(q["venta_json"])
        flete_item = venta["line_items"][0]
        assert flete_item["total"] > 100.0


# ── Margin floor (GT-P-001) ───────────────────────────────────────────────────
# MARGIN_FLOOR = float(os.getenv("MIN_MARGIN_PCT", "20")) / 100 = 0.20 in prod

class TestMarginFloor:
    def test_margin_below_floor_clamped(self, client):
        # Request 5% — below the 20% floor — stored margin must be >= floor
        q = _post_quote(client, {"margin_pct": "5", "flete_lcl": "100"})
        assert float(q["margin_pct"]) >= 0.20

    def test_margin_above_floor_accepted(self, client):
        q = _post_quote(client, {"margin_pct": "30", "flete_lcl": "100"})
        assert abs(float(q["margin_pct"]) - 0.30) < 0.001

    def test_margin_0_clamped_to_floor(self, client):
        q = _post_quote(client, {"margin_pct": "0", "flete_lcl": "100"})
        assert float(q["margin_pct"]) >= 0.20


# ── LCL W/M factor columns ────────────────────────────────────────────────────

class TestLclFleteRateFactorColumns:
    def test_flete_rate_lcl_volume_greater(self, client):
        # volume 2.0 CBM, weight 500kg → W/M = max(2.0, 0.5) = 2.0
        q = _post_quote(client, {
            "flete_rate_lcl": "100",
            "flete_lcl": "",
            "volume_cbm": "2.0",
            "weight": "500",
            "weight_unit": "kg",
        })
        venta = json.loads(q["venta_json"])
        flete_item = venta["line_items"][0]
        assert flete_item.get("factor_value") == pytest.approx(2.0, rel=0.001)
        assert flete_item.get("factor_unit") == "m³"

    def test_flete_rate_lcl_weight_greater(self, client):
        # weight 3000kg = 3.0 MT; volume 1.0 CBM → W/M = max(1.0, 3.0) = 3.0
        q = _post_quote(client, {
            "flete_rate_lcl": "100",
            "flete_lcl": "",
            "volume_cbm": "1.0",
            "weight": "3000",
            "weight_unit": "kg",
        })
        venta = json.loads(q["venta_json"])
        flete_item = venta["line_items"][0]
        assert flete_item.get("factor_value") == pytest.approx(3.0, rel=0.001)

    def test_factor_column_total_equals_rate_times_factor_times_margin(self, client):
        # rate=100, W/M=2.0, margin=20% → total = 100 * 2.0 * 1.2 = 240
        q = _post_quote(client, {
            "flete_rate_lcl": "100",
            "flete_lcl": "",
            "volume_cbm": "2.0",
            "weight": "500",
            "weight_unit": "kg",
            "margin_pct": "20",
        })
        venta = json.loads(q["venta_json"])
        flete_item = venta["line_items"][0]
        assert flete_item["total"] == pytest.approx(240.0, rel=0.01)

    def test_flat_flete_does_not_render_factor_columns(self, client):
        q = _post_quote(client, {"flete_lcl": "500", "flete_rate_lcl": ""})
        venta = json.loads(q["venta_json"])
        flete_item = venta["line_items"][0]
        assert flete_item.get("factor_value") is None

    def test_fcl_does_not_render_factor_columns(self, client):
        q = _post_quote(client, {
            "mode": "fcl",
            "flete_lcl": "2000",
            "flete_rate_lcl": "",
        })
        venta = json.loads(q["venta_json"])
        for item in venta["line_items"]:
            assert item.get("factor_value") is None

    def test_aereo_does_not_render_factor_columns(self, client):
        q = _post_quote(client, {
            "mode": "aereo",
            "flete_usd": "1500",
            "flete_lcl": "",
            "flete_rate_lcl": "",
            "consolidator": "",
        })
        venta = json.loads(q["venta_json"])
        for item in venta["line_items"]:
            assert item.get("factor_value") is None


# ── requester_type ─────────────────────────────────────────────────────────────

class TestRequesterType:
    def test_default_is_cliente(self, client):
        q = _post_quote(client, {"requester_type": ""})
        assert q["requester_type"] == "cliente"

    def test_agente_stored_correctly(self, client):
        q = _post_quote(client, {"requester_type": "agente"})
        assert q["requester_type"] == "agente"

    def test_invalid_value_defaults_to_cliente(self, client):
        q = _post_quote(client, {"requester_type": "unknown"})
        assert q["requester_type"] == "cliente"

    def test_cliente_stored_correctly(self, client):
        q = _post_quote(client, {"requester_type": "cliente"})
        assert q["requester_type"] == "cliente"


# ── /acuses route ─────────────────────────────────────────────────────────────

class TestSintadTnM3:
    def test_sintad_export_tn_m3_populated_for_lcl(self, client):
        q = _post_quote(client, {"volume_cbm": "3.2", "weight": "850", "weight_unit": "kg", "flete_lcl": "275"})
        costeo = json.loads(q["costeo_json"])
        assert costeo.get("flete_factor") == pytest.approx(3.2, rel=0.001)
        assert costeo.get("flete_factor_unit") == "m³"

    def test_sintad_export_tn_m3_blank_for_fcl(self, client):
        q = _post_quote(client, {"mode": "fcl", "flete_lcl": "2000", "flete_rate_lcl": "", "volume_cbm": "30"})
        costeo = json.loads(q["costeo_json"])
        assert costeo.get("flete_factor") is None

    def test_sintad_export_tn_m3_blank_for_aereo(self, client):
        q = _post_quote(client, {"mode": "aereo", "flete_lcl": "", "flete_usd": "1500",
                                  "flete_rate_lcl": "", "consolidator": ""})
        costeo = json.loads(q["costeo_json"])
        assert costeo.get("flete_factor") is None

    def test_lcl_flete_factor_volume_wins(self, client):
        q = _post_quote(client, {"volume_cbm": "3.2", "weight": "850", "weight_unit": "kg", "flete_lcl": "100"})
        costeo = json.loads(q["costeo_json"])
        assert costeo["flete_factor"] == pytest.approx(3.2, rel=0.001)
        assert costeo["flete_factor_unit"] == "m³"

    def test_lcl_flete_factor_weight_wins(self, client):
        q = _post_quote(client, {"volume_cbm": "1.0", "weight": "3000", "weight_unit": "kg", "flete_lcl": "100"})
        costeo = json.loads(q["costeo_json"])
        assert costeo["flete_factor"] == pytest.approx(3.0, rel=0.001)
        assert costeo["flete_factor_unit"] == "ton"

    def test_thc_minimum_floor_applied(self, client):
        # thc_rate=12, W/M=2.0 → 24 < min 33 → thc_usd=33
        q = _post_quote(client, {
            "volume_cbm": "2.0", "weight": "500", "weight_unit": "kg",
            "flete_lcl": "100", "thc_rate": "12", "thc_min": "33",
        })
        costeo = json.loads(q["costeo_json"])
        assert costeo["thc_usd"] == pytest.approx(33.0, rel=0.01)

    def test_thc_above_minimum_uses_computed(self, client):
        # thc_rate=12, W/M=4.0 → 48 > min 33 → thc_usd=48
        q = _post_quote(client, {
            "volume_cbm": "4.0", "weight": "500", "weight_unit": "kg",
            "flete_lcl": "100", "thc_rate": "12", "thc_min": "33",
        })
        costeo = json.loads(q["costeo_json"])
        assert costeo["thc_usd"] == pytest.approx(48.0, rel=0.01)

    def test_pdf_shows_tn_m3_column_for_lcl_rate(self, client, monkeypatch):
        import api.routes as routes_mod
        monkeypatch.setattr(routes_mod, "WEASYPRINT_AVAILABLE", False)
        q = _post_quote(client, {
            "flete_rate_lcl": "35", "flete_lcl": "",
            "volume_cbm": "3.2", "weight": "850", "weight_unit": "kg",
        })
        resp = client.get(f"/quote/{q['reference_code']}/preview.pdf")
        assert resp.status_code == 200
        assert b"TN/M3" in resp.data

    def test_pdf_no_tn_m3_column_for_flat_lcl(self, client, monkeypatch):
        import api.routes as routes_mod
        monkeypatch.setattr(routes_mod, "WEASYPRINT_AVAILABLE", False)
        q = _post_quote(client, {"flete_lcl": "275", "flete_rate_lcl": ""})
        resp = client.get(f"/quote/{q['reference_code']}/preview.pdf")
        assert resp.status_code == 200
        assert b"TN/M3" not in resp.data


class TestAcusesRoute:
    def test_acuses_returns_200(self, client):
        resp = client.get("/acuses")
        assert resp.status_code == 200

    def test_acuses_returns_html(self, client):
        resp = client.get("/acuses")
        assert b"Acuses" in resp.data

    def test_acuses_empty_state_when_no_log(self, client):
        resp = client.get("/acuses")
        assert resp.status_code == 200
        assert b"registrado" in resp.data
