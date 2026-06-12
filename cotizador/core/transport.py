"""
Local transport cost calculation.

Abel's rule: charge = max(weight_band_rate, cbm_band_rate)

From the demo (36:06 – 47:17):
  Example: 0.58 CBM, 295 kg
  → CBM puts it in 0.5–1 CBM band (S/180)
  → Weight at 295 kg exceeds 250 kg max for that band → S/200
  → Weight wins. Charge = S/200.

Costs are in soles (PEN). Converted to USD in the final quote via SBS rate.

LCL uses consolidators (MSL, Kraft, Saco, EQ) — NOT direct navieras.
Abel: "Para un LCL no cotizamos con la naviera de manera directa."
"""

from __future__ import annotations

import warnings

IGV = 0.18

# ── Transport rate bands ─────────────────────────────────────────────────────
# (upper_bound, rate_soles)
# These are representative starting values; actual values come from Vania's
# rate card Excel (Tarifas folder → transportista sheet).

CBM_BANDS: list[tuple[float, float]] = [
    (0.5,   150.0),
    (1.0,   180.0),
    (2.0,   250.0),
    (3.0,   320.0),
    (5.0,   450.0),
    (10.0,  700.0),
]

WEIGHT_BANDS: list[tuple[float, float]] = [
    (100.0,   120.0),
    (250.0,   180.0),
    (500.0,   200.0),   # Abel's example: 295 kg → S/200
    (1000.0,  320.0),
    (2000.0,  500.0),
]

# ── Consolidators (LCL only) ─────────────────────────────────────────────────
# NET visto bueno rates (pre-IGV). IGV applied once by the PDF/display layer.
# MSL import=90/export=160 confirmed by Abel 2026-06-12.
# KRAFT/SACO/EQ import rates are TODO placeholders — confirm with Abel/Vania.

CONSOLIDATORS: dict[str, dict] = {
    "MSL": {
        "name": "MSL",
        "visto_bueno_export_usd": 160.0,  # confirmed by Abel 2026-06-12
        "visto_bueno_import_usd": 90.0,   # confirmed by Abel 2026-06-12
    },
    "KRAFT": {
        "name": "Kraft",
        "visto_bueno_export_usd": 160.0,
        # TODO: confirm KRAFT import VB with Abel/Vania — using export rate as placeholder
        "visto_bueno_import_usd": 160.0,
    },
    "SACO": {
        "name": "Saco",
        "visto_bueno_export_usd": 190.0,
        # TODO: confirm SACO import VB with Abel/Vania — using export rate as placeholder
        "visto_bueno_import_usd": 190.0,
    },
    "EQ": {
        "name": "EQ",
        "visto_bueno_export_usd": 170.0,
        # TODO: confirm EQ import VB with Abel/Vania (ASK VANIA) — using export rate as placeholder
        "visto_bueno_import_usd": 170.0,
    },
}

# Warn at import time for consolidators where import VB is unconfirmed placeholder
_UNCONFIRMED_IMPORT_VB = [
    k for k, v in CONSOLIDATORS.items()
    if v.get("visto_bueno_import_usd") == v.get("visto_bueno_export_usd") and k != "MSL"
]
if _UNCONFIRMED_IMPORT_VB:
    warnings.warn(
        "CONSOLIDATOR WARNING — visto_bueno_import_usd is a placeholder (= export rate) for: "
        + ", ".join(_UNCONFIRMED_IMPORT_VB)
        + ". Confirm with Abel/Vania before using these for import quotes.",
        UserWarning,
        stacklevel=1,
    )

# ── Customs agents ────────────────────────────────────────────────────────────
# Abel: Alefero is default. OEA+BASC required for clients like Farmex.
# commission_usd and gastos_usd are NET (pre-IGV). IGV applied once by PDF layer.

CUSTOMS_AGENTS: dict[str, dict] = {
    "ALEFERO": {
        "name": "Alefero",
        "commission_usd": 50.0,   # net pre-IGV commission
        "gastos_usd": 0.19,       # gastos operativos (pre-IGV)
        "requires_oea_basc": False,
        "default": True,
    },
    "OEA_BASC": {
        "name": "OEA+BASC Certified Agent",
        "commission_usd": 80.0,
        "gastos_usd": 0.0,
        "requires_oea_basc": True,
        "default": False,
    },
}


def _band_rate(value: float, bands: list[tuple[float, float]]) -> float:
    """Return the rate for the band that value falls into."""
    for upper, rate in bands:
        if value <= upper:
            return rate
    # Beyond last band: extrapolate at last rate × 1.2
    return bands[-1][1] * 1.2


def get_cbm_rate(cbm: float) -> float:
    return _band_rate(cbm, CBM_BANDS)


def get_weight_rate(weight_kg: float) -> float:
    return _band_rate(weight_kg, WEIGHT_BANDS)


def calculate_transport(weight_kg: float, cbm: float) -> dict:
    """
    Return the transport charge breakdown.
    Charge = max(weight_band_rate, cbm_band_rate) — Abel's rule.
    """
    cbm_rate = get_cbm_rate(cbm)
    weight_rate = get_weight_rate(weight_kg)
    charge_soles = max(cbm_rate, weight_rate)
    basis = "weight" if weight_rate >= cbm_rate else "volume"

    return {
        "weight_kg": weight_kg,
        "cbm": cbm,
        "cbm_rate_soles": cbm_rate,
        "weight_rate_soles": weight_rate,
        "charge_soles": charge_soles,
        "basis": basis,
    }


def get_consolidator(name: str) -> dict:
    key = name.upper().strip()
    if key not in CONSOLIDATORS:
        raise ValueError(
            f"Unknown consolidator: {name!r}. Valid: {sorted(CONSOLIDATORS)}"
        )
    return CONSOLIDATORS[key]


def get_customs_agent(client_requires_oea_basc: bool = False) -> dict:
    """Select customs agent based on client requirements."""
    if client_requires_oea_basc:
        return CUSTOMS_AGENTS["OEA_BASC"]
    return CUSTOMS_AGENTS["ALEFERO"]


def visto_bueno_net_usd(consolidator: dict, operation: str = "exportacion") -> float:
    """
    Net visto bueno cost (pre-IGV). IGV is applied once by the PDF/display layer.

    BUG FIX (2026-06-12): Correct local-item composition:
      venta_neto = net × (1 + margin)
      igv        = venta_neto × 0.18
      total      = venta_neto + igv
    IGV must NEVER be applied to an already-IGV-inclusive base.
    """
    if operation == "importacion":
        return float(consolidator.get("visto_bueno_import_usd", 0.0))
    return float(consolidator.get("visto_bueno_export_usd", 0.0))


def customs_net_usd(agent: dict) -> float:
    """
    Net customs agent cost (pre-IGV). IGV applied once by PDF/display layer.

    BUG FIX (2026-06-12): Previously customs_total_usd() returned IGV-inclusive
    total, causing double-IGV (margin applied to IGV-inclusive base, then PDF
    applied IGV again). Now returns net pre-IGV amount only.
    """
    return round(agent["commission_usd"] + agent["gastos_usd"], 4)


# ── Legacy aliases (backward compat with any external callers) ────────────────

def visto_bueno_total_usd(consolidator: dict) -> float:
    """DEPRECATED: returns export net (pre-IGV). Use visto_bueno_net_usd() instead."""
    return visto_bueno_net_usd(consolidator, operation="exportacion")


def customs_total_usd(agent: dict) -> float:
    """DEPRECATED: returns net (pre-IGV). Use customs_net_usd() instead."""
    return customs_net_usd(agent)
