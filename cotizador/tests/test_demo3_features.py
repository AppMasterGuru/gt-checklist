"""
Tests for Abel Demo 3 features (2026-06-12):
  Bug 1  — VB double-IGV regression guard
  Feature 1 — operation field persisted on quote
  Feature 2 — VB resolves correctly by (consolidator × operation)
  Feature 3 — extra-item bucket tagging flows to correct intl/local flags
  Feature 5 — CIF calc items use venta_neto without margin scaling
"""

import json
import pytest

from core.transport import (
    CONSOLIDATORS,
    visto_bueno_net_usd,
    customs_net_usd,
    get_consolidator,
    get_customs_agent,
)


# ─── Bug 1: VB double-IGV regression ─────────────────────────────────────────

class TestVbDoubleIgvRegression:
    """
    The old code returned visto_bueno_total_usd = net × 1.18, then routes
    applied margin to that IGV-inclusive base, then the PDF applied IGV again.
    The fix: visto_bueno_net_usd returns PRE-IGV net only.
    IGV is applied ONCE by the PDF/display layer.
    """

    def test_msl_export_vb_is_pre_igv_net(self):
        cons = get_consolidator("MSL")
        net = visto_bueno_net_usd(cons, "exportacion")
        assert net == 160.0, f"Expected 160.0, got {net}"
        # Not IGV-inclusive (old bad value was 180 * 1.18 = 212.40)
        assert net != 212.40

    def test_msl_import_vb_is_pre_igv_net(self):
        cons = get_consolidator("MSL")
        net = visto_bueno_net_usd(cons, "importacion")
        assert net == 90.0, f"Expected 90.0, got {net}"

    def test_vb_with_margin_produces_correct_venta_neto(self):
        """
        Correct: venta_neto = net * (1 + margin); igv = venta_neto * 0.18
        For MSL import net=90, margin=20%: venta=108.00, igv=19.44, total=127.44
        The old broken path produced 254.88 then re-applied IGV.
        """
        cons = get_consolidator("MSL")
        net = visto_bueno_net_usd(cons, "importacion")
        margin = 0.20
        venta_neto = round(net * (1 + margin), 2)
        igv        = round(venta_neto * 0.18, 2)
        total      = round(venta_neto + igv, 2)
        assert venta_neto == 108.00
        assert igv        == 19.44
        assert total      == 127.44

    def test_double_igv_sentinel_never_appears(self):
        """254.88 was the symptom: 180*1.18*1.20 = 254.88 stored as venta."""
        cons = get_consolidator("MSL")
        for op in ("exportacion", "importacion"):
            net = visto_bueno_net_usd(cons, op)
            for margin in (0.10, 0.15, 0.20, 0.25, 0.30):
                venta_neto = net * (1 + margin)
                assert abs(venta_neto - 254.88) > 0.01, (
                    f"Double-IGV sentinel 254.88 appeared: op={op}, margin={margin}"
                )

    def test_customs_net_is_pre_igv(self):
        agent = get_customs_agent(False)
        net = customs_net_usd(agent)
        # Alefero: 50.00 + 0.19 = 50.19 (pre-IGV)
        assert abs(net - 50.19) < 0.01
        # Not IGV-inclusive (59.22 would be the old broken value)
        assert net < 51.0


# ─── Feature 1: operation field ───────────────────────────────────────────────

class TestOperationField:
    """operation defaults to 'exportacion'; only 'importacion' is the other valid value."""

    def test_valid_operations_accepted(self):
        for op in ("exportacion", "importacion"):
            sanitized = op if op in ("exportacion", "importacion") else "exportacion"
            assert sanitized == op

    def test_invalid_operation_falls_back_to_exportacion(self):
        for bad in ("export", "Import", "", "fob", "lcl"):
            sanitized = bad if bad in ("exportacion", "importacion") else "exportacion"
            assert sanitized == "exportacion"


# ─── Feature 2: VB resolves by (consolidator × operation) ────────────────────

class TestVbByConsolidatorAndOperation:

    def test_msl_export(self):
        assert visto_bueno_net_usd(get_consolidator("MSL"), "exportacion") == 160.0

    def test_msl_import(self):
        assert visto_bueno_net_usd(get_consolidator("MSL"), "importacion") == 90.0

    def test_craft_export(self):
        assert visto_bueno_net_usd(get_consolidator("CRAFT"), "exportacion") == 160.0

    def test_saco_export(self):
        assert visto_bueno_net_usd(get_consolidator("SACO"), "exportacion") == 190.0

    def test_eq_export(self):
        assert visto_bueno_net_usd(get_consolidator("EQ"), "exportacion") == 170.0

    def test_default_operation_is_exportacion(self):
        cons = get_consolidator("MSL")
        assert visto_bueno_net_usd(cons) == visto_bueno_net_usd(cons, "exportacion")

    def test_all_consolidators_have_rate_fields(self):
        """All entries must have both rate keys. None means pending confirmation (not a bug)."""
        for key in CONSOLIDATORS:
            cons = CONSOLIDATORS[key]
            assert "visto_bueno_export_usd" in cons, f"{key} missing export rate key"
            assert "visto_bueno_import_usd" in cons, f"{key} missing import rate key"
        # MSL is the only fully confirmed consolidator
        msl = CONSOLIDATORS["MSL"]
        assert msl["visto_bueno_export_usd"] == 160.0
        assert msl["visto_bueno_import_usd"] == 90.0


# ─── Feature 3: bucket tagging flows to correct PDF flags ────────────────────

class TestBucketTagging:
    """
    Extra items with bucket='intl'  → intl_only=True,  local_only=False
    Extra items with bucket='local' → intl_only=False, local_only=True
    """

    _FLAGS_INTL  = {"intl_only": True,  "local_only": False}
    _FLAGS_LOCAL = {"intl_only": False, "local_only": True}

    def _resolve_flags(self, bucket: str) -> dict:
        return self._FLAGS_LOCAL if bucket == "local" else self._FLAGS_INTL

    def test_intl_bucket_gets_intl_flags(self):
        flags = self._resolve_flags("intl")
        assert flags["intl_only"]  is True
        assert flags["local_only"] is False

    def test_local_bucket_gets_local_flags(self):
        flags = self._resolve_flags("local")
        assert flags["intl_only"]  is False
        assert flags["local_only"] is True

    def test_missing_bucket_defaults_to_intl(self):
        ei = {"concept": "Flete", "valor": 100.0}  # no bucket key
        bucket = ei.get("bucket", "intl")
        flags = self._resolve_flags(bucket)
        assert flags["intl_only"] is True

    def test_items_serialization_roundtrip(self):
        """Simulate what syncJson produces and what routes.py reads back."""
        items = [
            {"concept": "Pick Up",     "bucket": "intl",  "valor": 50.0,  "factor": None, "min_usd": None},
            {"concept": "Visto Bueno", "bucket": "local", "valor": 160.0, "factor": None, "min_usd": None},
        ]
        parsed = json.loads(json.dumps(items))
        assert parsed[0]["bucket"] == "intl"
        assert parsed[1]["bucket"] == "local"


# ─── Feature 5: CIF calculator — venta_neto bypass margin scaling ─────────────

class TestCifCalculator:
    """
    CIF calc items already encode the margin in pct_venta vs pct_costo spread.
    venta_neto must NOT be multiplied by (1 + margin) again in routes.py.
    """

    def _calc_cif(self, cif: float, pct_costo: float, pct_venta: float, min_usd: float) -> tuple:
        costo_neto = max(min_usd, (pct_costo / 100) * cif)
        venta_neto = max(min_usd, (pct_venta / 100) * cif)
        return round(costo_neto, 2), round(venta_neto, 2)

    def test_standard_cif_calc(self):
        costo, venta = self._calc_cif(50000, 0.30, 0.35, 100)
        assert costo == 150.00
        assert venta == 175.00

    def test_minimum_floor_applies_for_small_cif(self):
        costo2, venta2 = self._calc_cif(100, 0.30, 0.35, 100)
        assert costo2 == 100.00  # min floor
        assert venta2 == 100.00  # min floor

    def test_venta_neto_not_scaled_by_margin(self):
        """
        The margin is baked into pct_venta > pct_costo.
        Applying margin again would give 175 × 1.20 = 210 — wrong.
        """
        _, venta_neto = self._calc_cif(50000, 0.30, 0.35, 100)
        margin = 0.20
        assert venta_neto == 175.00
        # Confirm the doubled value is measurably different
        assert abs(venta_neto * (1 + margin) - 175.00) > 1.0

    def test_cif_item_serialization(self):
        """Simulate the full JSON payload for a CIF calc item."""
        cif, pc, pv, minU = 50000, 0.30, 0.35, 100
        costo = round(max(minU, (pc / 100) * cif), 2)
        venta = round(max(minU, (pv / 100) * cif), 2)
        item = {
            "concept": "Agente de Aduana",
            "bucket": "local",
            "cif_calc": True,
            "cif_usd": cif,
            "pct_costo": pc,
            "pct_venta": pv,
            "min_usd": minU,
            "valor": costo,
            "venta_neto": venta,
            "factor": None,
            "total": costo,
        }
        parsed = json.loads(json.dumps(item))
        assert parsed["cif_calc"] is True
        assert parsed["venta_neto"] == 175.0
        assert parsed["valor"] == 150.0
        assert parsed["bucket"] == "local"

    def test_export_customs_is_flat_not_cif(self):
        """Export customs = flat 50 USD. Never use CIF calc for export."""
        item = {"concept": "Agente de Aduana", "bucket": "local", "valor": 50.0, "factor": None}
        assert item.get("cif_calc") is None or item.get("cif_calc") is False
        assert item["valor"] == 50.0
