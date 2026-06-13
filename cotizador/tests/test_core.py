"""
42 tests covering all core modules.
Run from cotizador/ directory: python -m pytest tests/ -v

No external network calls — exchange rate tests use an injected rate.
DB tests use a temp SQLite file.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from datetime import date, datetime
from pathlib import Path

import pytest

# ─── patch DB_PATH before importing core.db ───────────────────────────────────
_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()
os.environ["DB_PATH"] = _tmp_db.name
os.environ["CACHE_DIR"] = tempfile.mkdtemp()

import core.db as db_mod
from core.db import audit, get_connection, init_db, transition_status
from core.units import (
    CargoMeasurements,
    add_igv,
    cbm_from_cm,
    cbm_from_inches,
    inches_to_cm,
    lbs_to_kg,
    m_to_cm,
    parse_length,
    parse_weight,
    strip_igv,
)
from core.incoterms import (
    IncotermError,
    VALID_INCOTERMS,
    active_components,
    classify_incoterm,
    get_cost_components,
    validate,
)
from core.transport import (
    CONSOLIDATORS,
    _CONSOLIDATOR_ALIASES,
    calculate_transport,
    customs_total_usd,
    get_cbm_rate,
    get_consolidator,
    get_customs_agent,
    get_weight_rate,
    vb_rate_missing,
    visto_bueno_net_usd,
    visto_bueno_total_usd,
)
from core.exchange_rate import soles_to_usd, usd_to_soles
from core.parser import (
    detect_language,
    detect_mode,
    parse_cbm,
    parse_dimensions,
    parse_incoterm,
    parse_request,
    parse_weight_kg,
)
from core.reference import generate_reference, parse_reference
from core.acknowledgment import (
    SUPPORTED_LANGUAGES,
    generate_acknowledgment,
    supported_languages,
)
from core.pdf_generator import generate_html_preview, render_html, VALIDITY_DAYS


# ════════════════════════════════════════════════════════════════════
#  Fixtures
# ════════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def fresh_db():
    """Re-initialise (wipe and recreate) the temp DB before each test."""
    # Wipe
    with sqlite3.connect(_tmp_db.name) as conn:
        conn.executescript("""
            DROP TABLE IF EXISTS quotes;
            DROP TABLE IF EXISTS audit_log;
            DROP TABLE IF EXISTS ref_counters;
        """)
    init_db()
    yield


@pytest.fixture
def conn():
    c = get_connection()
    yield c
    c.close()


# ════════════════════════════════════════════════════════════════════
#  1–8 · core/units.py
# ════════════════════════════════════════════════════════════════════

class TestUnits:
    def test_lbs_to_kg(self):
        assert abs(lbs_to_kg(1) - 0.453592) < 0.0001

    def test_inches_to_cm(self):
        assert abs(inches_to_cm(1) - 2.54) < 0.0001

    def test_m_to_cm(self):
        assert m_to_cm(1.5) == 150.0

    def test_cbm_from_cm(self):
        # 100cm × 100cm × 100cm × 1 = 1 CBM
        assert abs(cbm_from_cm(100, 100, 100, 1) - 1.0) < 1e-9

    def test_cbm_from_inches(self):
        # 1 inch³ = (2.54cm)³ / 1_000_000 — just check it's > 0
        val = cbm_from_inches(10, 10, 10, 1)
        assert val > 0

    def test_parse_weight_kg(self):
        assert parse_weight(100, "kg") == 100.0

    def test_parse_weight_lbs(self):
        assert abs(parse_weight(100, "lbs") - 45.3592) < 0.001

    def test_parse_weight_unknown_unit_raises(self):
        with pytest.raises(ValueError, match="Unknown weight unit"):
            parse_weight(10, "tons")

    def test_parse_length_cm(self):
        assert parse_length(50, "cm") == 50.0

    def test_parse_length_inches(self):
        assert abs(parse_length(10, "in") - 25.4) < 0.001

    def test_add_igv(self):
        assert abs(add_igv(100) - 118.0) < 0.001

    def test_strip_igv(self):
        assert abs(strip_igv(118) - 100.0) < 0.001

    def test_cargo_measurements_cbm(self):
        cargo = CargoMeasurements(weight_kg=100, length_cm=100, width_cm=100, height_cm=100)
        assert abs(cargo.volume_cbm - 1.0) < 1e-9


# ════════════════════════════════════════════════════════════════════
#  9–14 · core/incoterms.py
# ════════════════════════════════════════════════════════════════════

class TestIncoterms:
    def test_validate_fob(self):
        assert validate("FOB") == "FOB"

    def test_validate_lowercase(self):
        assert validate("fob") == "FOB"

    def test_validate_invalid_raises(self):
        with pytest.raises(IncotermError):
            validate("XYZ")

    def test_fob_has_flete(self):
        c = get_cost_components("FOB")
        assert c.flete_internacional is True

    def test_ddp_has_import_duties(self):
        c = get_cost_components("DDP")
        assert c.derechos_importacion is True

    def test_cif_has_seguro(self):
        c = get_cost_components("CIF")
        assert c.seguro is True

    def test_classify_returns_dict(self):
        result = classify_incoterm("FOB")
        assert result["incoterm"] == "FOB"
        assert isinstance(result["components"], dict)

    def test_active_components_fob(self):
        comps = active_components("FOB")
        assert "flete_internacional" in comps
        assert "derechos_importacion" not in comps


# ════════════════════════════════════════════════════════════════════
#  15–22 · core/transport.py
# ════════════════════════════════════════════════════════════════════

class TestTransport:
    def test_cbm_rate_small(self):
        # 0.3 CBM → first band
        rate = get_cbm_rate(0.3)
        assert rate > 0

    def test_weight_rate_medium(self):
        # 295 kg → Abel's example band (500 kg band = S/200)
        rate = get_weight_rate(295)
        assert rate == 200.0

    def test_transport_weight_wins(self):
        # Abel's demo: 0.58 CBM, 295 kg → weight wins (S/200 > S/180)
        result = calculate_transport(weight_kg=295, cbm=0.58)
        assert result["basis"] == "weight"
        assert result["charge_soles"] == 200.0

    def test_transport_volume_wins(self):
        # Small weight, large CBM
        result = calculate_transport(weight_kg=10, cbm=2.5)
        assert result["basis"] == "volume"

    def test_get_consolidator_msl(self):
        c = get_consolidator("MSL")
        # VB rates split by operation (Bug 1 fix 2026-06-12)
        assert c["visto_bueno_export_usd"] == 160.0
        assert c["visto_bueno_import_usd"] == 90.0

    def test_get_consolidator_craft(self):
        c = get_consolidator("CRAFT")
        assert c["visto_bueno_export_usd"] == 160.0

    def test_get_consolidator_saco(self):
        c = get_consolidator("SACO")
        assert c["visto_bueno_export_usd"] == 190.0

    def test_get_consolidator_unknown_raises(self):
        with pytest.raises(ValueError):
            get_consolidator("UNKNOWN_CO")

    # ── Regression: missing-rate consolidator must never crash ────────────────

    def test_get_consolidator_vanguard_no_crash(self):
        """VANGUARD is approved but has no confirmed rates — must return dict, never raise."""
        cons = get_consolidator("VANGUARD")  # must not raise
        assert cons["name"] == "Vanguard"
        assert cons["visto_bueno_export_usd"] is None
        assert cons["visto_bueno_import_usd"] is None
        assert vb_rate_missing(cons, "exportacion") is True
        assert vb_rate_missing(cons, "importacion") is True
        assert visto_bueno_net_usd(cons, "exportacion") == 0.0
        assert visto_bueno_net_usd(cons, "importacion") == 0.0

    def test_get_consolidator_ecu_aliases(self):
        """ECU and ECU WORLDWIDE are aliases for the EQ canonical entry."""
        assert "ECU WORLDWIDE" in _CONSOLIDATOR_ALIASES
        assert "ECU" in _CONSOLIDATOR_ALIASES
        eq   = get_consolidator("EQ")
        ecu  = get_consolidator("ECU")
        ecuw = get_consolidator("ECU WORLDWIDE")
        assert eq is ecu
        assert eq is ecuw
        assert eq["name"] == "ECU Worldwide"

    def test_visto_bueno_none_rate_returns_zero(self):
        """visto_bueno_net_usd must handle None rates without crashing."""
        dummy = {"visto_bueno_export_usd": None, "visto_bueno_import_usd": None}
        assert visto_bueno_net_usd(dummy, "exportacion") == 0.0
        assert visto_bueno_net_usd(dummy, "importacion") == 0.0

    def test_visto_bueno_returns_pre_igv_net(self):
        # Bug 1 fix: visto_bueno_total_usd now returns NET pre-IGV (alias for export).
        # IGV is applied once by the PDF layer, not here.
        msl = get_consolidator("MSL")
        net = visto_bueno_total_usd(msl)
        assert net == 160.0  # export net confirmed by Abel 2026-06-12

    def test_customs_default_is_alefero(self):
        agent = get_customs_agent(False)
        assert agent["name"] == "Alefero"

    def test_customs_oea_basc_for_farmex(self):
        agent = get_customs_agent(True)
        assert agent["requires_oea_basc"] is True

    def test_customs_total_returns_pre_igv_net(self):
        # Bug 1 fix: customs_total_usd now returns NET pre-IGV.
        # IGV is applied once by the PDF layer.
        agent = get_customs_agent(False)
        net = customs_total_usd(agent)
        expected_net = agent["commission_usd"] + agent["gastos_usd"]
        assert abs(net - expected_net) < 0.01


# ════════════════════════════════════════════════════════════════════
#  23–26 · core/exchange_rate.py  (no network — injected rate)
# ════════════════════════════════════════════════════════════════════

class TestExchangeRate:
    def test_soles_to_usd(self):
        assert abs(soles_to_usd(372, rate=3.72) - 100.0) < 0.001

    def test_usd_to_soles(self):
        assert abs(usd_to_soles(100, rate=3.72) - 372.0) < 0.001

    def test_roundtrip(self):
        rate = 3.72
        original = 500.0
        assert abs(soles_to_usd(usd_to_soles(original, rate), rate) - original) < 0.001

    def test_fallback_rate_env(self):
        """FALLBACK_EXCHANGE_RATE env var is read at import time."""
        from core.exchange_rate import FALLBACK_RATE
        assert FALLBACK_RATE > 0


# ════════════════════════════════════════════════════════════════════
#  27–32 · core/parser.py
# ════════════════════════════════════════════════════════════════════

class TestParser:
    def test_detect_language_spanish(self):
        text = "Estimado equipo, necesito una cotización para flete marítimo."
        assert detect_language(text) == "es"

    def test_detect_language_english(self):
        text = "Dear team, please provide a quotation for air freight cargo."
        assert detect_language(text) == "en"

    def test_detect_language_german(self):
        text = "Sehr geehrte Damen und Herren, bitte schicken Sie uns ein Angebot für Fracht."
        assert detect_language(text) == "de"

    def test_parse_incoterm(self):
        assert parse_incoterm("We need a FOB quote from Lima") == "FOB"

    def test_parse_incoterm_none(self):
        assert parse_incoterm("no incoterm here") is None

    def test_parse_weight_kg_from_text(self):
        val = parse_weight_kg("Total weight: 295 kg")
        assert abs(val - 295.0) < 0.01

    def test_parse_weight_lbs_from_text(self):
        val = parse_weight_kg("Weight: 100 lbs")
        assert abs(val - lbs_to_kg(100)) < 0.01

    def test_detect_mode_lcl(self):
        assert detect_mode("We have an LCL shipment to Miami") == "lcl"

    def test_detect_mode_aereo(self):
        assert detect_mode("air freight from Lima to Frankfurt") == "aereo"

    def test_parse_request_full(self):
        text = "Dear team, please quote FOB for LCL shipment, 295 kg, 0.58 cbm"
        result = parse_request(text)
        assert result.incoterm == "FOB"
        assert result.mode == "lcl"
        assert result.weight_kg is not None
        assert result.confidence == 1.0


# ════════════════════════════════════════════════════════════════════
#  33–37 · core/reference.py
# ════════════════════════════════════════════════════════════════════

class TestReference:
    def test_generate_reference_format(self, conn):
        now = datetime(2026, 5, 7)
        ref = generate_reference(conn, "Universal Cargo", "FOB", "GT-PC", now)
        assert ref.startswith("26-05-001")
        assert "Universal Cargo" in ref
        assert "FOB" in ref
        assert "GT-PC" in ref

    def test_sequential_increments(self, conn):
        now = datetime(2026, 5, 7)
        ref1 = generate_reference(conn, "Client A", "FOB", "GT-PC", now)
        ref2 = generate_reference(conn, "Client B", "CIF", "GT-PC", now)
        seq1 = int(ref1.split("-")[2].split(" ")[0])
        seq2 = int(ref2.split("-")[2].split(" ")[0])
        assert seq2 == seq1 + 1

    def test_different_months_reset(self, conn):
        may = datetime(2026, 5, 1)
        jun = datetime(2026, 6, 1)
        ref_may = generate_reference(conn, "Client", "FOB", "GT-PC", may)
        ref_jun = generate_reference(conn, "Client", "FOB", "GT-PC", jun)
        # Both should be seq 001 (different year_month counters)
        assert "26-05-001" in ref_may
        assert "26-06-001" in ref_jun

    def test_parse_reference(self, conn):
        now = datetime(2026, 5, 7)
        ref = generate_reference(conn, "Universal Cargo", "FOB", "GT-PC", now)
        parsed = parse_reference(ref)
        assert parsed["incoterm"] == "FOB"
        assert parsed["staff_code"] == "GT-PC"
        assert parsed["client_name"] == "Universal Cargo"

    def test_staff_code_wca(self, conn):
        ref = generate_reference(conn, "Freight Co", "CIF", "GT-WCA")
        assert "GT-WCA" in ref


# ════════════════════════════════════════════════════════════════════
#  38–42 · core/acknowledgment.py + state machine
# ════════════════════════════════════════════════════════════════════

class TestAcknowledgment:
    def test_spanish_acknowledgment(self):
        text = generate_acknowledgment("es", "Empresa XYZ", "carga general LCL", "26-05-001 ABC FOB GT-PC")
        assert "Empresa XYZ" in text
        assert "carga general LCL" in text
        assert "26-05-001" in text

    def test_english_acknowledgment(self):
        text = generate_acknowledgment("en", "Cargo Corp", "air freight", "REF-001")
        assert "Dear" in text
        assert "Cargo Corp" in text

    def test_german_acknowledgment(self):
        text = generate_acknowledgment("de", "Müller GmbH", "Textilien LCL", "REF-002")
        assert "Sehr geehrte" in text
        assert "Müller GmbH" in text

    def test_fallback_to_spanish(self):
        # 'sw' (Swahili) not supported → falls back to 'es'
        text = generate_acknowledgment("sw", "Client", "cargo", "REF")
        assert "Estimado" in text

    def test_all_six_languages_supported(self):
        langs = supported_languages()
        assert set(langs) >= {"es", "en", "de", "zh", "fr", "pt"}


class TestStateMachine:
    """State machine is enforced at the DB layer by SQLite triggers."""

    def _insert_quote(self, conn, ref="TEST-REF-001"):
        conn.execute(
            """INSERT INTO quotes
               (reference_code, client_name, incoterm, mode, origin, destination,
                staff_code, status)
               VALUES (?,?,?,?,?,?,?,?)""",
            (ref, "Test Client", "FOB", "lcl", "Lima", "Miami", "GT-PC", "PENDING"),
        )
        conn.commit()
        row = conn.execute("SELECT id FROM quotes WHERE reference_code=?", (ref,)).fetchone()
        return row[0]

    def test_pending_to_approved(self, conn):
        qid = self._insert_quote(conn)
        transition_status(qid, "APPROVED", "jp")
        row = conn.execute("SELECT status FROM quotes WHERE id=?", (qid,)).fetchone()
        assert row[0] == "APPROVED"

    def test_pending_to_rejected(self, conn):
        qid = self._insert_quote(conn)
        transition_status(qid, "REJECTED", "jp", "Client cancelled")
        row = conn.execute("SELECT status FROM quotes WHERE id=?", (qid,)).fetchone()
        assert row[0] == "REJECTED"

    def test_approved_to_sent(self, conn):
        qid = self._insert_quote(conn)
        transition_status(qid, "APPROVED", "jp")
        transition_status(qid, "SENT", "abel")
        row = conn.execute("SELECT status FROM quotes WHERE id=?", (qid,)).fetchone()
        assert row[0] == "SENT"

    def test_illegal_pending_to_sent_blocked(self, conn):
        """Cannot jump from PENDING directly to SENT — trigger must abort."""
        qid = self._insert_quote(conn)
        with pytest.raises(Exception):   # sqlite3.OperationalError or IntegrityError
            transition_status(qid, "SENT", "abel")

    def test_illegal_terminal_transition_blocked(self, conn):
        """SENT is terminal — cannot move to APPROVED."""
        qid = self._insert_quote(conn)
        transition_status(qid, "APPROVED", "jp")
        transition_status(qid, "SENT", "abel")
        with pytest.raises(Exception):
            transition_status(qid, "APPROVED", "jp")

    def test_audit_log_immutable_update(self, conn):
        """UPDATE on audit_log must be blocked by trigger."""
        conn.execute(
            "INSERT INTO audit_log (event_type, quote_reference, actor, detail_json)"
            " VALUES ('TEST','REF','actor','{}')"
        )
        conn.commit()
        with pytest.raises(Exception):
            conn.execute("UPDATE audit_log SET event_type='HACKED' WHERE id=1")
            conn.commit()

    def test_audit_log_immutable_delete(self, conn):
        """DELETE on audit_log must be blocked by trigger."""
        conn.execute(
            "INSERT INTO audit_log (event_type, quote_reference, actor, detail_json)"
            " VALUES ('TEST','REF','actor','{}')"
        )
        conn.commit()
        with pytest.raises(Exception):
            conn.execute("DELETE FROM audit_log WHERE id=1")
            conn.commit()
